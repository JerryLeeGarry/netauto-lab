# netauto-lab

Source-of-Truth driven network automation lab with validation-first design —
applying safety-critical (SIL-4) data validation discipline from railway
signalling engineering to network configuration pipelines.

## Architecture

NetBox (SoT) → config rendering (Python / Ansible) → deployment (NAPALM)
→ containerlab topology → pre/post-change validation

## Stack

- **Source of Truth:** NetBox
- **Automation:** Python (NAPALM), Ansible
- **Lab topology:** containerlab — Arista cEOS-lab (primary), Nokia SR Linux (Day-0 bootstrap), FRR (fallback)
- **Infrastructure-as-Code:** Terraform
- **Runtime:** WSL2 + Docker

## Roadmap

- [ ] Phase 1 — containerlab base topology + Day-0 bootstrap
- [ ] Phase 2 — NetBox SoT schema + config rendering
- [ ] Phase 3 — NAPALM deployment + pre/post-change validation
- [ ] Phase 4 — CI: lint, dry-run, config diff

**v1 target: mid-September 2026**
