"""Compare observed device state with intent, and post-change with pre-change.

Pure functions over the dictionaries NAPALM getters return. No I/O here, so
every check is unit-tested against recorded state without a device.

State snapshot shape (see deploy.collect_state):
    {"interfaces": get_interfaces(), "lldp": get_lldp_neighbors(),
     "bgp": get_bgp_neighbors()}
"""

from __future__ import annotations

from dataclasses import dataclass

from .intent import Intent


@dataclass(frozen=True)
class CheckResult:
    check: str
    device: str
    subject: str
    ok: bool
    detail: str = ""

    def __str__(self) -> str:
        status = "PASS" if self.ok else "FAIL"
        tail = f" - {self.detail}" if self.detail else ""
        return f"{status} {self.check} {self.device} {self.subject}{tail}"


def _peers(state: dict) -> dict:
    return state.get("bgp", {}).get("global", {}).get("peers", {})


def check_intent(intent: Intent, state: dict) -> list[CheckResult]:
    """Does the device do what the SoT says it should?"""
    dev = intent.device
    results = []

    for name in intent.interfaces:
        iface = state.get("interfaces", {}).get(name)
        ok = bool(iface and iface.get("is_up"))
        detail = "" if ok else ("missing" if iface is None else "down")
        results.append(CheckResult("C01-interface-up", dev, name, ok, detail))

    for local, (host, port) in intent.lldp.items():
        seen = [
            (n.get("hostname", "").split(".")[0], n.get("port", ""))
            for n in state.get("lldp", {}).get(local, [])
        ]
        ok = (host, port) in seen
        detail = "" if ok else f"expected {host}:{port}, saw {seen or 'nothing'}"
        results.append(CheckResult("C02-lldp-cabling", dev, local, ok, detail))

    peers = _peers(state)
    for address, remote_as in intent.bgp.items():
        peer = peers.get(address)
        if peer is None:
            results.append(
                CheckResult("C03-bgp-established", dev, address, False, "not configured")
            )
            continue
        ok = bool(peer.get("is_up")) and peer.get("remote_as") == remote_as
        detail = "" if ok else f"is_up={peer.get('is_up')} remote_as={peer.get('remote_as')}"
        results.append(CheckResult("C03-bgp-established", dev, address, ok, detail))

        received = peer.get("address_family", {}).get("ipv4", {}).get("received_prefixes", -1)
        ok = received > 0
        detail = "" if ok else f"received_prefixes={received}"
        results.append(CheckResult("C04-bgp-prefixes-received", dev, address, ok, detail))

    # Drift: a session the SoT knows nothing about. Config merge cannot remove
    # it, so it has to be at least detected.
    for address in sorted(set(peers) - set(intent.bgp)):
        results.append(CheckResult("C05-no-unexpected-bgp-peer", dev, address, False, "not in SoT"))

    return results


def check_regression(device: str, pre: dict, post: dict) -> list[CheckResult]:
    """Did the change break anything that worked before, intended or not?"""
    results = []
    for name, iface in pre.get("interfaces", {}).items():
        if iface.get("is_up"):
            ok = bool(post.get("interfaces", {}).get(name, {}).get("is_up"))
            results.append(
                CheckResult("C06-no-regression", device, name, ok, "" if ok else "was up, now down")
            )
    post_peers = _peers(post)
    for address, peer in _peers(pre).items():
        if peer.get("is_up"):
            ok = bool(post_peers.get(address, {}).get("is_up"))
            results.append(
                CheckResult(
                    "C06-no-regression", device, address, ok, "" if ok else "was up, now down"
                )
            )
    return results


def failures(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if not r.ok]
