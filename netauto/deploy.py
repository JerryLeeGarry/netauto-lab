"""Deploy rendered configs with NAPALM, gated on pre/post-change validation.

The fabric is changed as one transaction:
  1. snapshot state on every device (pre)
  2. load the candidate on every device (merge) and read the diffs
  3. dry-run (the default): discard everything and report the diffs
  4. commit every changed device with a revert timer - a device rolls itself
     back if it is never confirmed, even if we lose contact with it
  5. poll the whole fabric until intent and regression checks pass, or time out
  6. all pass -> confirm every commit; any failure -> revert every change and
     verify each device is back to its exact pre-change running and startup config

It has to be fabric-wide: a BGP session only comes up once both ends are
configured, so judging one device at a time would fail every first deployment.

`deploy_fabric` only needs objects with the NAPALM driver methods it calls,
so the whole sequence is tested with a fake driver (tests/test_deploy.py).

Why the revert does not use NAPALM's rollback(): on EOS, with a commit-confirm
pending, rollback() sends `configure session ... abort` and `write memory` in one
batch. On cEOS 4.32.0F the save can run before the abort has reverted running
config, so the rolled-back change ends up in startup-config and returns on the
next reboot (reproduced in the lab, see docs/runlog/). The commit with a revert
timer never writes startup, so aborting the session is enough - and the result
is checked (C07) instead of assumed.

Known limitation: this uses config *merge*. Lines removed from the SoT stay
on the device. C05 in checks.py detects the BGP side of that drift; moving to
full config replace needs the Day-0 baseline in the template first.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .checks import CheckResult, check_intent, check_regression, failures
from .intent import Intent


@dataclass
class DeviceReport:
    device: str
    # checked | in-sync | dry-run | committed | rolled-back | rollback-unverified
    status: str = "checked"
    diff: str = ""
    checks: list[CheckResult] = field(default_factory=list)
    pre: dict = field(default_factory=dict)
    post: dict = field(default_factory=dict)
    pre_config: dict = field(default_factory=dict)  # running/startup before the change

    @property
    def ok(self) -> bool:
        return self.status not in ("rolled-back", "rollback-unverified") and not failures(
            self.checks
        )


def collect_state(driver) -> dict:
    state = {"interfaces": driver.get_interfaces(), "lldp": driver.get_lldp_neighbors()}
    try:
        state["bgp"] = driver.get_bgp_neighbors()
    except Exception as exc:
        # A router with no BGP configured may answer the BGP show commands with
        # an error instead of an empty table. Fail closed: no BGP data means
        # every BGP check fails. The reason is kept as evidence.
        state["bgp"] = {}
        state["bgp_error"] = str(exc)
    return state


def config_body(text: str) -> list[str]:
    """Config lines without comments, so running and startup compare equal when
    they hold the same configuration (they differ only in '!' header lines)."""
    return [line.rstrip() for line in text.splitlines() if line.strip() and line.strip()[0] != "!"]


def read_configs(driver) -> dict:
    config = driver.get_config(retrieve="all")
    return {"running": config["running"], "startup": config["startup"]}


def revert(
    driver,
    device: str,
    pre_config: dict,
    *,
    timeout: int = 30,
    poll_interval: int = 2,
    sleep=time.sleep,
    clock=time.monotonic,
) -> CheckResult:
    """Abort the pending commit-confirm and verify the device is back where it was.

    discard_config() aborts the pending session without saving. Running config
    reverts asynchronously, so poll until both running and startup match the
    pre-change text exactly.
    """
    driver.discard_config()
    expected = {kind: config_body(text) for kind, text in pre_config.items()}
    deadline = clock() + timeout
    while True:
        now = read_configs(driver)
        wrong = [
            kind for kind in ("running", "startup") if config_body(now[kind]) != expected[kind]
        ]
        if not wrong or clock() >= deadline:
            break
        sleep(poll_interval)
    detail = "" if not wrong else f"{' and '.join(wrong)} differ from pre-change after {timeout}s"
    return CheckResult("C07-rollback-verified", device, "config", not wrong, detail)


def check_fabric(drivers: dict, intents: dict[str, Intent]) -> dict[str, DeviceReport]:
    """Read-only: compare live state with intent. Changes nothing."""
    reports = {}
    for name, driver in drivers.items():
        state = collect_state(driver)
        reports[name] = DeviceReport(name, checks=check_intent(intents[name], state), pre=state)
    return reports


def deploy_fabric(
    drivers: dict,
    configs: dict[str, str],
    intents: dict[str, Intent],
    *,
    commit: bool = False,
    revert_in: int = 300,
    settle_timeout: int = 120,
    poll_interval: int = 5,
    revert_timeout: int = 30,
    sleep=time.sleep,
    clock=time.monotonic,
) -> dict[str, DeviceReport]:
    if settle_timeout + revert_timeout >= revert_in:
        # Otherwise a device could auto-revert while we are still deciding or
        # still verifying, and the verification would be measuring the timer.
        raise ValueError("settle_timeout + revert_timeout must be shorter than revert_in")

    def undo(name: str) -> CheckResult:
        return revert(
            drivers[name],
            name,
            reports[name].pre_config,
            timeout=revert_timeout,
            sleep=sleep,
            clock=clock,
        )

    reports = {}
    for name, driver in drivers.items():
        report = DeviceReport(name, status="in-sync", pre=collect_state(driver))
        report.pre_config = read_configs(driver)
        driver.load_merge_candidate(config=configs[name])
        report.diff = driver.compare_config()
        reports[name] = report
    changed = [name for name, report in reports.items() if report.diff]

    if not commit or not changed:
        for name, driver in drivers.items():
            driver.discard_config()
            if reports[name].diff:
                reports[name].status = "dry-run"
            else:
                reports[name].checks = check_intent(intents[name], reports[name].pre)
        return reports

    for name in drivers:
        if name not in changed:
            drivers[name].discard_config()

    committed = []
    try:
        for name in changed:
            drivers[name].commit_config(revert_in=revert_in)
            committed.append(name)

        deadline = clock() + settle_timeout
        while True:
            for name, driver in drivers.items():
                report = reports[name]
                report.post = collect_state(driver)
                report.checks = check_intent(intents[name], report.post) + check_regression(
                    name, report.pre, report.post
                )
            passed = all(not failures(r.checks) for r in reports.values())
            if passed or clock() >= deadline:
                break
            sleep(poll_interval)  # BGP needs time to converge; keep polling
    except Exception:
        # Lost a device or hit an API error mid-change: undo what we can reach.
        # Anything we cannot reach reverts by itself when its timer expires.
        for name in committed:
            # Best effort only - the revert timer is the backstop.
            with contextlib.suppress(Exception):
                undo(name)
        raise

    for name in changed:
        if passed:
            drivers[name].confirm_commit()
            reports[name].status = "committed"
        else:
            result = undo(name)
            reports[name].checks.append(result)
            reports[name].status = "rolled-back" if result.ok else "rollback-unverified"
    return reports


def open_driver(host: str):
    """Open a NAPALM EOS session. Credentials come from the environment and
    default to containerlab's cEOS defaults (admin/admin) - lab only."""
    from napalm import get_network_driver  # imported here: optional dependency

    driver = get_network_driver("eos")(
        hostname=host,
        username=os.environ.get("NETAUTO_USERNAME", "admin"),
        password=os.environ.get("NETAUTO_PASSWORD", "admin"),
        optional_args={"transport": os.environ.get("NETAUTO_EOS_TRANSPORT", "https")},
    )
    driver.open()
    return driver


def save_reports(reports: dict[str, DeviceReport], directory: Path) -> None:
    """Keep the evidence: diff, pre/post state and check results per device."""
    for report in reports.values():
        out = directory / report.device
        out.mkdir(parents=True, exist_ok=True)
        (out / "status.txt").write_text(report.status + "\n")
        (out / "diff.txt").write_text(report.diff + "\n" if report.diff else "")
        (out / "pre.json").write_text(json.dumps(report.pre, indent=2, default=str))
        (out / "post.json").write_text(json.dumps(report.post, indent=2, default=str))
        for kind, text in report.pre_config.items():
            (out / f"pre-{kind}.cfg").write_text(text)
        (out / "checks.txt").write_text("".join(f"{c}\n" for c in report.checks))
