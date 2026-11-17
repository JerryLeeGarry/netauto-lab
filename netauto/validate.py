"""Semantic validation of the SoT.

Each rule is a small function with an ID, registered in `RULES`. Rules are
independent of the rendering code on purpose: the validator must not share
logic with the generator it is guarding, otherwise one bug hides in both.

Every rule has a negative test in tests/test_validate.py that breaks a valid
SoT in exactly one way and asserts that exactly this rule fires.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from itertools import combinations

from .model import Sot

PRIVATE_ASN = range(64512, 65535)  # 2-byte private range, 64512-65534
IFACE_PATTERN = {
    "eos": re.compile(r"Ethernet[1-9]\d*"),
    "linux": re.compile(r"eth[1-9]\d*"),
}


@dataclass(frozen=True)
class Finding:
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.message}"


RuleFn = Callable[[Sot], Iterator[str]]
RULES: dict[str, tuple[str, RuleFn]] = {}


def rule(rule_id: str, summary: str) -> Callable[[RuleFn], RuleFn]:
    def register(fn: RuleFn) -> RuleFn:
        RULES[rule_id] = (summary, fn)
        return fn

    return register


def validate(sot: Sot) -> list[Finding]:
    findings = []
    for rule_id, (_, fn) in RULES.items():
        findings.extend(Finding(rule_id, msg) for msg in fn(sot))
    return findings


def _known_ends(sot: Sot):
    """Link ends whose device exists (R01 reports the ones that do not)."""
    for link in sot.links:
        for end in link.ends:
            if end.device in sot.devices:
                yield end, sot.devices[end.device]


def _known_links(sot: Sot):
    for link in sot.links:
        if link.a.device in sot.devices and link.b.device in sot.devices:
            yield link, sot.devices[link.a.device], sot.devices[link.b.device]


@rule("R01", "link endpoints reference defined devices")
def _r01(sot):
    for link in sot.links:
        for end in link.ends:
            if end.device not in sot.devices:
                yield f"link end {end.device}:{end.interface} references undefined device"


@rule("R02", "interface names are valid for the device platform")
def _r02(sot):
    for end, dev in _known_ends(sot):
        if not IFACE_PATTERN[dev.platform].fullmatch(end.interface):
            yield f"{end.device}:{end.interface} is not a valid {dev.platform} interface name"


@rule("R03", "an interface terminates at most one link")
def _r03(sot):
    used = Counter((end.device, end.interface) for link in sot.links for end in link.ends)
    for (device, interface), count in used.items():
        if count > 1:
            yield f"{device}:{interface} is used by {count} links"


@rule("R04", "both ends of a link are distinct usable addresses in one subnet")
def _r04(sot):
    for link in sot.links:
        a, b = link.a, link.b
        name = f"{a.device}:{a.interface} <-> {b.device}:{b.interface}"
        if a.ip.network != b.ip.network:
            yield f"{name}: {a.ip} and {b.ip} are not in the same subnet"
            continue
        if a.ip.ip == b.ip.ip:
            yield f"{name}: both ends use {a.ip.ip}"
        net = a.ip.network
        if net.prefixlen < 31:
            for end in (a, b):
                if end.ip.ip in (net.network_address, net.broadcast_address):
                    yield f"{name}: {end.ip} is the network or broadcast address"


@rule("R05", "no IP address is assigned twice")
def _r05(sot):
    owners: dict[object, list[str]] = {}
    for name, dev in sot.devices.items():
        owners.setdefault(dev.mgmt_ip, []).append(f"{name}:mgmt")
        if dev.loopback0 is not None:
            owners.setdefault(dev.loopback0.ip, []).append(f"{name}:Loopback0")
    for link in sot.links:
        a, b = link.a, link.b
        owners.setdefault(a.ip.ip, []).append(f"{a.device}:{a.interface}")
        # Two ends of one link sharing an address is R04's finding, not R05's.
        if b.ip.ip != a.ip.ip:
            owners.setdefault(b.ip.ip, []).append(f"{b.device}:{b.interface}")
    for ip, where in owners.items():
        if len(where) > 1:
            yield f"{ip} is assigned to {', '.join(where)}"


@rule("R06", "link subnets and loopbacks do not overlap")
def _r06(sot):
    nets = [(link.a.ip.network, f"{link.a.device}:{link.a.interface}") for link in sot.links]
    for (net1, name1), (net2, name2) in combinations(nets, 2):
        if net1.overlaps(net2):
            yield f"subnet {net1} ({name1}) overlaps {net2} ({name2})"
    for dev_name, dev in sot.devices.items():
        if dev.loopback0 is None:
            continue
        for net, name in nets:
            if dev.loopback0.ip in net:
                yield f"{dev_name} loopback {dev.loopback0.ip} is inside link subnet {net} ({name})"


@rule("R07", "attributes match the device role")
def _r07(sot):
    for name, dev in sot.devices.items():
        if dev.is_router:
            if dev.platform != "eos":
                yield f"{name}: {dev.role} must run eos, not {dev.platform}"
            if dev.asn is None:
                yield f"{name}: {dev.role} needs an asn"
            if dev.loopback0 is None:
                yield f"{name}: {dev.role} needs loopback0"
            elif dev.loopback0.network.prefixlen != 32:
                yield f"{name}: loopback0 {dev.loopback0} must be a /32"
        else:
            if dev.platform != "linux":
                yield f"{name}: host must run linux, not {dev.platform}"
            if dev.asn is not None or dev.loopback0 is not None:
                yield f"{name}: host must not define asn or loopback0"


@rule("R08", "ASN plan: private range, spines share one ASN, leaves are unique")
def _r08(sot):
    routers = {n: d for n, d in sot.devices.items() if d.is_router and d.asn is not None}
    for name, dev in routers.items():
        if dev.asn not in PRIVATE_ASN:
            yield f"{name}: asn {dev.asn} is outside the private range 64512-65534"
    spine_asns = {d.asn for d in routers.values() if d.role == "spine"}
    if len(spine_asns) > 1:
        yield f"spines use different ASNs: {sorted(spine_asns)}"
    leaf_asns = Counter(d.asn for d in routers.values() if d.role == "leaf")
    for asn, count in leaf_asns.items():
        if count > 1:
            yield f"asn {asn} is used by {count} leaves"
        if asn in spine_asns:
            yield f"leaf asn {asn} collides with the spine asn"


@rule("R09", "fabric shape: every leaf has exactly one link to every spine, nothing else")
def _r09(sot):
    pairs = Counter()
    for link, dev_a, dev_b in _known_links(sot):
        if not (dev_a.is_router and dev_b.is_router):
            continue
        if dev_a.role == dev_b.role:
            yield f"{link.a.device} <-> {link.b.device}: {dev_a.role}-{dev_b.role} link not allowed"
            continue
        pairs[frozenset((link.a.device, link.b.device))] += 1
    spines = [n for n, d in sot.devices.items() if d.role == "spine"]
    leaves = [n for n, d in sot.devices.items() if d.role == "leaf"]
    for leaf in leaves:
        for spine in spines:
            count = pairs[frozenset((leaf, spine))]
            if count != 1:
                yield f"{leaf} has {count} links to {spine}, expected 1"


@rule("R10", "a host has exactly one link, and it goes to a leaf")
def _r10(sot):
    for name, dev in sot.devices.items():
        if dev.role != "host":
            continue
        peers = [remote.device for _, remote in sot.links_of(name)]
        if len(peers) != 1:
            yield f"{name} has {len(peers)} links, expected 1"
        for peer in peers:
            if peer in sot.devices and sot.devices[peer].role != "leaf":
                yield f"{name} is attached to {peer}, which is not a leaf"


@rule("R11", "management addresses are usable hosts inside mgmt_subnet")
def _r11(sot):
    net = sot.mgmt_subnet
    reserved = {net.network_address, net.broadcast_address, net.network_address + 1}
    for name, dev in sot.devices.items():
        if dev.mgmt_ip not in net:
            yield f"{name}: mgmt_ip {dev.mgmt_ip} is outside {net}"
        elif dev.mgmt_ip in reserved:
            yield f"{name}: mgmt_ip {dev.mgmt_ip} is reserved (network, gateway or broadcast)"


@rule("R12", "fabric addresses sit inside the supernet, management does not")
def _r12(sot):
    for name, dev in sot.devices.items():
        if dev.loopback0 is not None and dev.loopback0.ip not in sot.supernet:
            yield f"{name}: loopback0 {dev.loopback0} is outside supernet {sot.supernet}"
    for link in sot.links:
        for end in link.ends:
            if not end.ip.network.subnet_of(sot.supernet):
                yield f"{end.device}:{end.interface} {end.ip} is outside supernet {sot.supernet}"
    if sot.mgmt_subnet.overlaps(sot.supernet):
        yield f"mgmt_subnet {sot.mgmt_subnet} overlaps supernet {sot.supernet}"
