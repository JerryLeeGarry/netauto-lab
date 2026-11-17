# Day-0 bootstrap

Bring the lab from nothing to "every router answers eAPI and is ready for
`netauto deploy`". Day-0 state is whatever containerlab puts on a cEOS node by
default: hostname, the `admin` user, a management address and eAPI. Everything
after that (interfaces, addressing, BGP) is pushed by the pipeline.

> **Not yet executed.** This procedure was written without access to cEOS or
> containerlab. Run it, correct it where reality differs, and record the run
> in `docs/runlog/`.

## Prerequisites

- WSL2 with Docker running inside it.
- containerlab: `bash -c "$(curl -sL https://get.containerlab.dev)"`
- The cEOS-lab image. It is not on a public registry: download
  `cEOS64-lab-4.32.0F.tar.xz` from arista.com (free account), then

  ```bash
  docker import cEOS64-lab-4.32.0F.tar.xz ceos:4.32.0F
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

## Things most likely to differ from this document

- Whether containerlab's default cEOS config puts management in a VRF. If it
  does, NAPALM still connects, but note it: it matters for config replace.
- Whether eAPI listens on HTTPS by default. If only HTTP, set
  `NETAUTO_EOS_TRANSPORT=http`.
- How long BGP takes to converge on cEOS. The default `--settle-timeout` is
  120 s; record the real number.
- Whether LLDP reports the neighbour as `spine1` or with a domain suffix (the
  check strips a suffix).
- What an unconfigured router answers to the BGP show commands: an empty
  table or an error. Either is handled; an error shows up as `bgp_error` in
  `pre.json`.
- Whether EOS accepts every line of the rendered configs. They have been
  reviewed but never loaded on a device.

## Tear down

```bash
sudo containerlab destroy -t clab/lab.clab.yml --cleanup
```
