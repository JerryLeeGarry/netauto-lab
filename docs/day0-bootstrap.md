# Day-0 bootstrap

Bring the lab from nothing to "every router answers eAPI and is ready for
`netauto deploy`". Day-0 state is whatever containerlab puts on a cEOS node by
default: hostname, the `admin` user, a management address and eAPI. Everything
after that (interfaces, addressing, BGP) is pushed by the pipeline.

> **Executed once, not yet on WSL2.** All nine steps passed on 2026-11-17 on a
> cloud Ubuntu 24.04 box (Docker 29.1.3, containerlab 0.83.0, cEOS 4.32.0F).
> Run it on WSL2, correct it where reality differs, and record the run in
> `docs/runlog/`.

## Prerequisites

- WSL2 with Docker running inside it.
- containerlab: `bash -c "$(curl -sL https://get.containerlab.dev)"`
- The cEOS-lab image. It is not on a public registry: download
  `cEOS-lab-4.32.0F.tar.xz` (or the `cEOS64-lab-` build) from arista.com
  (free account), then

  ```bash
  docker import cEOS-lab-4.32.0F.tar.xz ceos:4.32.0F
  ```

  A different version works if `CLAB_KIND` in `netauto/render.py` is changed
  to match and the topology is re-rendered.
- Memory: four cEOS nodes. WSL2 caps how much RAM it may use (`.wslconfig`).
  Check with `docker stats` once the lab is up. If the laptop cannot hold four,
  delete `spine2` and its two links from `sot/lab.yml` and run `netauto render`
  — the single-spine fabric passes validation (there is a test for it).

## Procedure

| # | Step | Expected result |
|---|---|---|
| 1 | `netauto validate && netauto render --check` | `OK: 6 devices, 6 links, 12 rules passed`, exit 0 |
| 2 | `sudo containerlab deploy -t clab/lab.clab.yml` | Table of 6 nodes, all `running`, management addresses matching `sot/lab.yml` |
| 3 | `docker exec -it clab-netauto-leaf1 Cli -c "show running-config"` | Hostname `leaf1`, user `admin`, `management api http-commands` enabled, no BGP |
| 4 | eAPI answers on every router (command below) | JSON containing `"modelName": "cEOSLab"` |
| 5 | `netauto check` | Fails: C03 for every BGP peer and C01 for `Loopback0`, on every router. This is correct — nothing is configured yet. |
| 6 | `netauto deploy` | `dry-run` for every router, each with a diff. Nothing changed. |
| 7 | `netauto deploy --commit` | `committed` for every router, all checks PASS |
| 8 | `netauto check` | All PASS |
| 9 | `docker exec clab-netauto-host1 ping -c 3 10.10.2.10` | Replies from host2 |

Step 4, for each of 172.20.20.11, .12, .21, .22:

```bash
curl -sk -u admin:admin https://172.20.20.21/command-api \
  -d '{"jsonrpc":"2.0","method":"runCmds","params":{"version":1,"cmds":["show version"]},"id":1}'
```

Steps 5–8 write diff, pre/post state and check results to `artifacts/` (not
committed). That directory is the evidence for the run log.

## What the first run answered

| Question | Answer on cEOS 4.32.0F / containerlab 0.83.0 |
|---|---|
| Management in a VRF? | No. `Management0` is in the default VRF. |
| eAPI over HTTPS by default? | Yes, on all four routers. |
| LLDP neighbour name format | Plain hostname (`spine1`), no domain suffix. |
| Rendered configs accepted? | Yes, every line, on all four routers. |
| Step 5 result | Exactly as predicted: C01 `Loopback0` missing, C03 for every peer. |
| BGP convergence time | Not measured. Record it on WSL2. |

Still open: how the WSL2 memory cap copes with four cEOS nodes.

## Tear down

```bash
sudo containerlab destroy -t clab/lab.clab.yml --cleanup
```
