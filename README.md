# netauto-lab

Source-of-Truth driven network automation lab with validation-first design —
applying safety-critical (SIL-4) data validation discipline from railway
signalling engineering to network configuration pipelines.

The idea carried over from signalling: configuration data is validated
independently of the tool that consumes it, every validation rule is proven to
fire, and a change is only accepted once the system is observed in the
intended state.

## Pipeline

```
sot/lab.yml ──seed──▶ NetBox
     │                  │
     └────────┬─────────┘
              ▼
        load into model      strict: unknown or duplicate keys are errors
              ▼
          validate           12 semantic rules, each with a negative test
              ▼
           render            configs/*.cfg + clab/lab.clab.yml (golden files)
              ▼
           deploy            NAPALM merge, dry-run by default
              ▼
     commit with revert timer on every changed device
              ▼
   poll fabric: intent checks + regression checks
        │                         │
      pass                      fail
        ▼                         ▼
   confirm commit           abort every commit, then verify running
                            and startup match their pre-change text
```

The lab is a 2-spine / 2-leaf eBGP fabric of Arista cEOS with two Linux hosts.
BGP neighbours, interface descriptions, host routes and the containerlab
topology are all derived from the SoT; none of them is entered by hand.

## Quick start (no devices needed)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

netauto validate          # load + validate sot/lab.yml
netauto rules             # list the validation rules
netauto render            # regenerate configs/ and clab/
netauto render --check    # fail if committed files are stale (CI gate)
pytest -q
```

With the lab running (see [docs/day0-bootstrap.md](docs/day0-bootstrap.md)):

```bash
pip install -e ".[deploy,netbox]"

netauto check             # live state vs intent, read-only
netauto deploy            # dry-run: show per-device diff, change nothing
netauto deploy --commit   # apply, verify, then confirm or roll back
```

With NetBox as the source instead of the file:

```bash
export NETBOX_URL=http://localhost:8000 NETBOX_TOKEN=...
netauto netbox-seed                     # create missing objects from sot/lab.yml
netauto --source netbox validate
netauto --source netbox deploy
```

## Layout

| Path | What it is |
|---|---|
| `sot/lab.yml` | Intent: devices, links, addressing. Seed for NetBox. |
| `netauto/model.py` | Data model and strict loader. |
| `netauto/validate.py` | Semantic rules R01–R12. |
| `netauto/render.py`, `templates/` | Config and topology rendering. |
| `netauto/netbox.py` | NetBox seed and loader. |
| `netauto/intent.py`, `netauto/checks.py` | Expected state and checks C01–C06 (C07, revert verification, lives in `deploy.py`). |
| `netauto/deploy.py` | Fabric-wide deploy with commit-confirm. |
| `configs/`, `clab/lab.clab.yml` | Generated. Committed so config changes show in PR diffs. |
| `tests/` | Offline tests; `test_netbox_live.py` needs a NetBox. |
| `docs/` | Day-0 procedure, run logs, postmortems. |

## Status

| Phase | Code | Verified |
|---|---|---|
| 1 — containerlab topology + Day-0 bootstrap | done | [x] all 9 steps on cEOS 4.32.0F, cloud Ubuntu 24.04 box — [ ] on my WSL2 |
| 2 — SoT model, validation, rendering | done | [x] offline tests in CI — [x] rendered configs accepted by cEOS |
| 2 — NetBox seed / load | done | [x] round trip against NetBox 4.7.5 — [ ] on my own NetBox |
| 3 — NAPALM deploy + pre/post-change validation | done | [x] commit, confirm, and a rejected change reverted on cEOS (C07 verified) |
| 4 — CI: lint, validate, render check, tests | done | [x] green on GitHub |

Unticked boxes are things that have not been observed yet. Results go in
[docs/runlog/](docs/runlog/).

## Known limitations

- **cEOS: NAPALM's `rollback()` can save a rolled-back change to startup.**
  Found in the lab. The deploy no longer calls it; it aborts the pending
  commit and verifies running and startup (C07). Anything else that calls
  `rollback()` on EOS with a commit-confirm pending is exposed.
- **Renumbering cannot be deployed.** Changing a link's addresses leaves the
  old BGP neighbour configured (merge), C05 flags it, and the change is
  reverted. Seen in the lab. Needs config replace, or explicit removal lines.

- **Config merge, not replace.** Lines removed from the SoT stay on the device.
  Check C05 detects stray BGP peers; anything else drifts silently. Replace
  needs the Day-0 baseline (management, users, eAPI) in the template first.
- **One vendor.** Templates and interface naming are EOS only.
- **Control plane only.** Checks look at interfaces, LLDP and BGP. Nothing
  tests that host1 can actually reach host2.
- **Seed never updates.** `netbox-seed` creates missing objects and leaves
  existing ones alone.

## Not built yet

Ansible, Terraform, SR Linux / FRR, streaming telemetry (gNMI, Prometheus,
Grafana), Batfish analysis of rendered configs.
