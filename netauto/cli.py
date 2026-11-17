"""Command-line entry point.

Every command that consumes the SoT loads it and validates it first. Nothing
is rendered or deployed from data that has open findings.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import render
from .model import Sot, SotError, load_sot
from .validate import RULES, validate

DEFAULT_SOT = render.ROOT / "sot" / "lab.yml"


def _load_validated(args) -> Sot:
    if args.source == "netbox":
        from . import netbox

        sot = netbox.load(netbox.connect(), args.site)
    else:
        sot = load_sot(args.sot)
    findings = validate(sot)
    if findings:
        for finding in findings:
            print(finding, file=sys.stderr)
        raise SystemExit(f"{len(findings)} validation finding(s) - refusing to continue")
    return sot


def cmd_validate(args) -> int:
    sot = _load_validated(args)
    print(f"OK: {len(sot.devices)} devices, {len(sot.links)} links, {len(RULES)} rules passed")
    return 0


def cmd_rules(args) -> int:
    for rule_id, (summary, _) in RULES.items():
        print(f"{rule_id}  {summary}")
    return 0


def cmd_render(args) -> int:
    sot = _load_validated(args)
    if args.check:
        stale = render.stale_files(sot)
        for path in stale:
            print(f"STALE {path}", file=sys.stderr)
        if stale:
            print("run `netauto render` and commit the result", file=sys.stderr)
        return 1 if stale else 0
    for path in render.write_all(sot):
        print(f"wrote {path}")
    return 0


def cmd_netbox_seed(args) -> int:
    from . import netbox

    args.source = "yaml"  # seeding always starts from the file
    netbox.seed(netbox.connect(), _load_validated(args))
    print("NetBox seeded")
    return 0


def _routers(sot: Sot) -> list[str]:
    return [name for name, dev in sot.devices.items() if dev.is_router]


def _print_reports(reports) -> int:
    for report in reports.values():
        print(f"== {report.device}: {report.status}")
        if report.diff:
            print(report.diff)
        for check in report.checks:
            print(f"   {check}")
    failed = [r.device for r in reports.values() if not r.ok]
    if failed:
        print(f"FAILED: {', '.join(failed)}", file=sys.stderr)
    return 1 if failed else 0


def _run_live(args, action) -> int:
    from . import deploy
    from .intent import intent_for

    sot = _load_validated(args)
    drivers = {}
    try:
        for name in _routers(sot):
            drivers[name] = deploy.open_driver(str(sot.devices[name].mgmt_ip))
        intents = {name: intent_for(sot, name) for name in drivers}
        reports = action(deploy, sot, drivers, intents)
    finally:
        for driver in drivers.values():
            driver.close()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path("artifacts") / f"{stamp}-{args.command}"
    deploy.save_reports(reports, out)
    print(f"evidence saved to {out}/")
    return _print_reports(reports)


def cmd_check(args) -> int:
    return _run_live(
        args, lambda deploy, sot, drivers, intents: deploy.check_fabric(drivers, intents)
    )


def cmd_deploy(args) -> int:
    def action(deploy, sot, drivers, intents):
        return deploy.deploy_fabric(
            drivers,
            render.render_configs(sot),
            intents,
            commit=args.commit,
            revert_in=args.revert_in,
            settle_timeout=args.settle_timeout,
        )

    return _run_live(args, action)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="netauto", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sot", type=Path, default=DEFAULT_SOT, help="SoT file (default: sot/lab.yml)"
    )
    parser.add_argument("--source", choices=("yaml", "netbox"), default="yaml")
    parser.add_argument("--site", default="lab", help="NetBox site when --source netbox")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="load and validate the SoT").set_defaults(fn=cmd_validate)
    sub.add_parser("rules", help="list validation rules").set_defaults(fn=cmd_rules)

    p = sub.add_parser("render", help="write configs/ and clab/ from the SoT")
    p.add_argument("--check", action="store_true", help="fail if committed files are stale")
    p.set_defaults(fn=cmd_render)

    sub.add_parser("netbox-seed", help="create missing NetBox objects").set_defaults(
        fn=cmd_netbox_seed
    )
    sub.add_parser("check", help="compare live state with intent (read-only)").set_defaults(
        fn=cmd_check
    )

    p = sub.add_parser("deploy", help="push configs; dry-run unless --commit")
    p.add_argument(
        "--commit", action="store_true", help="apply the change (default: show diff only)"
    )
    p.add_argument("--revert-in", type=int, default=300, help="device auto-revert timer, seconds")
    p.add_argument("--settle-timeout", type=int, default=120, help="max wait for checks to pass")
    p.set_defaults(fn=cmd_deploy)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except SotError as exc:
        print(f"SoT error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
