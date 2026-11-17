# Postmortems

One file per incident or drill, named `YYYY-MM-DD-<short-title>.md`.
Blameless: describe what the system did and why, not who got it wrong.

```markdown
# <title>

## Summary
Two sentences: what broke, how it was noticed.

## Timeline
Timestamps, commands, observations.

## Detection
Which check caught it (C01–C06, R01–R12)? If none did, that is the finding.

## Root cause

## What went well / what did not

## Actions
Concrete changes to rules, checks, templates or docs. Link the commits.
```

## Already observed, waiting for a postmortem

**A rolled-back change survived in startup-config** (2026-11-17, cloud box).
A commit-confirm on leaf2 was rolled back with NAPALM's `rollback()`; running
config reverted, startup-config kept the change. Reproduced 3/3 on spine1,
fixed by aborting and verifying instead (C07), fix verified 3/3. Raw evidence
is on the box under `artifacts/evidence-2026-11-17/` — the logs, not a
write-up. Questions a postmortem should answer: why did no check C01–C06
notice? What would have happened on the next reboot? What else in the
pipeline trusts a call's return value instead of observing the result?

## Drills

Three failures to cause on purpose. Each one exercises a different part of
the pipeline; each deserves a postmortem written from the real output.

1. **Miscabling.** In `clab/lab.clab.yml` only (not the SoT), swap leaf1's two
   uplinks, then destroy and redeploy the lab. Run `netauto check`.
   Question to answer: which checks fire, and is the message enough to find
   the fault without looking at the topology file?

2. **Out-of-band change.** On spine1, shut Ethernet1 by hand. Run
   `netauto check`, then `netauto deploy`.
   Question to answer: the checks fail, but does the deploy produce a diff?
   What does that say about merge versus replace?

3. **Change that does not converge.** With a clean, unconfigured lab, run
   `netauto deploy --commit --settle-timeout 5 --revert-in 60`.
   Question to answer: is every device back in its pre-change state
   afterwards? Compare `pre.json` with a fresh `netauto check`.
