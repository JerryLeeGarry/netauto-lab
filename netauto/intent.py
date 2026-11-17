"""Expected operational state of a router, derived from the SoT.

This is what "the change worked" means, written down before the change is
made. The deploy step compares the live device against it (checks.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Sot


@dataclass(frozen=True)
class Intent:
    device: str
    interfaces: list[str] = field(default_factory=list)  # must be up
    # local interface -> (neighbour hostname, neighbour interface); routers only,
    # because the Linux hosts do not speak LLDP.
    lldp: dict[str, tuple[str, str]] = field(default_factory=dict)
    bgp: dict[str, int] = field(default_factory=dict)  # peer address -> remote AS


def intent_for(sot: Sot, name: str) -> Intent:
    interfaces, lldp, bgp = ["Loopback0"], {}, {}
    for local, remote in sot.links_of(name):
        interfaces.append(local.interface)
        peer = sot.devices[remote.device]
        if peer.is_router:
            lldp[local.interface] = (remote.device, remote.interface)
            bgp[str(remote.ip.ip)] = peer.asn
    return Intent(device=name, interfaces=sorted(interfaces), lldp=lldp, bgp=bgp)
