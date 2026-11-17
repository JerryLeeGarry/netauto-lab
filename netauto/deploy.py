"""Deploy rendered configs with NAPALM, gated on pre/post-change validation.

The fabric is changed as one transaction:
  1. snapshot state on every device (pre)
  2. load the candidate on every device (merge) and read the diffs
  3. dry-run (the default): discard everything and report the diffs
  4. commit every changed device with a revert timer - a device rolls itself
     back if it is never confirmed, even if we lose contact with it
  5. poll the whole fabric until intent and regression checks pass, or time out
  6. all pass -> confirm every commit; any failure -> roll every change back

It has to be fabric-wide: a BGP session only comes up once both ends are
configured, so judging one device at a time would fail every first deployment.

`deploy_fabric` only needs objects with the NAPALM driver methods it calls,
so the whole sequence is tested with a fake driver (tests/test_deploy.py).

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
    status: str = "in-sync"  # in-sync | dry-run | committed | rolled-back
    diff: str = ""
    checks: list[CheckResult] = field(default_factory=list)
    pre: dict = field(default_factory=dict)
    post: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status != "rolled-back" and not failures(self.checks)


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
    sleep=time.sleep,
    clock=time.monotonic,
) -> dict[str, DeviceReport]:
    if settle_timeout >= revert_in:
        # Otherwise a device could auto-revert while we are still deciding.
        raise ValueError("settle_timeout must be shorter than revert_in")

    reports = {}
    for name, driver in drivers.items():
        report = DeviceReport(name, pre=collect_state(driver))
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
                drivers[name].rollback()
        raise

    for name in changed:
        if passed:
            drivers[name].confirm_commit()
            reports[name].status = "committed"
        else:
            drivers[name].rollback()
            reports[name].status = "rolled-back"
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
        (out / "checks.txt").write_text("".join(f"{c}\n" for c in report.checks))
