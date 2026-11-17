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
