# Run logs

One file per real run against the lab, named `YYYY-MM-DD-<what>.md`.
Written by hand, from what was observed. Copy the headings below.

```markdown
# <date> — <what was run>

## Environment
Laptop / WSL2 distro / Docker / containerlab / cEOS / NetBox versions.

## What I ran
Commands, in order.

## What happened
Outputs that matter (trimmed). Name the artifacts/ directory.

## Where reality differed from the docs or the code
Each difference: what I expected, what I saw, what I changed.

## Open questions
```

## First runs to log

1. Day-0 bootstrap, steps 1–9 of `docs/day0-bootstrap.md`.
2. NetBox: bring it up, `netauto netbox-seed`, then `pytest -m netbox` and
   `netauto --source netbox deploy`.
3. A change: add a third leaf to `sot/lab.yml`, render, review the diff in a
   pull request, deploy.
