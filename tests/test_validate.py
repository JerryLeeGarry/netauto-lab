"""Every rule must pass on the valid SoT and fire on a SoT broken in one way.

Each case breaks the valid lab SoT in exactly one place and asserts that the
named rule - and only that rule - reports it. A rule without a case here is a
rule nobody has ever seen fail, so `test_every_rule_has_a_negative_case`
fails when one is added without one.
"""

import pytest
from helpers import mutated

from netauto.validate import RULES, validate


def _dev(name, **changes):
    def apply(raw):
        for key, value in changes.items():
            if value is None:
                raw["devices"][name].pop(key)
            else:
                raw["devices"][name][key] = value

    return apply


def _end(index, side, **changes):
    def apply(raw):
        raw["links"][index][side].update(changes)

    return apply


def _add_link(a, b):
    def apply(raw):
        raw["links"].append(
            {
                "a": dict(zip(("device", "interface", "ip"), a, strict=True)),
                "b": dict(zip(("device", "interface", "ip"), b, strict=True)),
            }
        )

    return apply


def _drop_link(index):
    def apply(raw):
        del raw["links"][index]

    return apply


def _host_subnet(leaf_ip, host_ip):
    """Readdress the leaf1 <-> host1 link."""

    def apply(raw):
        raw["links"][4]["a"]["ip"] = leaf_ip
        raw["links"][4]["b"]["ip"] = host_ip

    return apply


CASES = [
    ("R01", "undefined device", _add_link(("ghost", "Ethernet1", "10.9.0.0/31"), ("leaf1", "Ethernet9", "10.9.0.1/31"))),
    ("R02", "bad interface name", _end(0, "a", interface="Eth1")),
    ("R03", "interface used twice", _end(1, "a", interface="Ethernet1")),
    ("R04", "ends in different subnets", _end(0, "b", ip="10.1.9.1/31")),
    ("R04", "both ends same address", _end(0, "b", ip="10.1.0.0/31")),
    ("R04", "network address on a /24", _end(4, "a", ip="10.10.1.0/24")),
    ("R05", "duplicate loopback", _dev("leaf2", loopback0="10.0.0.11/32")),
    ("R05", "mgmt address reused", _dev("host2", mgmt_ip="172.20.20.31")),
    ("R06", "overlapping link subnets", _host_subnet("10.10.0.1/16", "10.10.0.10/16")),
    ("R06", "loopback inside a link subnet", _dev("leaf1", loopback0="10.10.2.99/32")),
    ("R07", "router without asn", _dev("leaf1", asn=None)),
    ("R07", "router without loopback", _dev("leaf1", loopback0=None)),
    ("R07", "loopback not a /32", _dev("leaf1", loopback0="10.0.0.11/31")),
    ("R07", "host with asn", _dev("host1", asn=65099)),
    ("R08", "public asn", _dev("leaf1", asn=100)),
    ("R08", "spines disagree", _dev("spine2", asn=65001)),
    ("R08", "leaf asn reused", _dev("leaf2", asn=65011)),
    ("R08", "leaf uses spine asn", _dev("leaf1", asn=65000)),
    ("R09", "missing spine-leaf link", _drop_link(3)),
    ("R09", "spine-spine link", _add_link(("spine1", "Ethernet9", "10.9.0.0/31"), ("spine2", "Ethernet9", "10.9.0.1/31"))),
    ("R10", "host attached to a spine", _end(4, "a", device="spine1", interface="Ethernet3")),
    ("R10", "host without a link", _drop_link(5)),
    ("R11", "mgmt outside subnet", _dev("host1", mgmt_ip="192.168.0.5")),
    ("R11", "mgmt on the gateway address", _dev("host1", mgmt_ip="172.20.20.1")),
    ("R12", "loopback outside supernet", _dev("leaf1", loopback0="192.168.1.1/32")),
    ("R12", "link outside supernet", _host_subnet("192.168.9.1/24", "192.168.9.10/24")),
]  # fmt: skip


def test_valid_sot_has_no_findings(sot):
    assert validate(sot) == []


@pytest.mark.parametrize(
    ("rule", "mutation"), [(r, m) for r, _, m in CASES], ids=[f"{r}-{d}" for r, d, _ in CASES]
)
def test_rule_fires(rule, mutation):
    fired = {finding.rule for finding in validate(mutated(mutation))}
    assert fired == {rule}


def test_every_rule_has_a_negative_case():
    assert {rule for rule, _, _ in CASES} == set(RULES)


def test_platform_mismatch_is_reported_by_both_affected_rules():
    """A router on linux breaks the role rule and makes its interface names invalid."""
    fired = {finding.rule for finding in validate(mutated(_dev("spine1", platform="linux")))}
    assert fired == {"R02", "R07"}


def test_smaller_fabric_is_still_valid():
    """Dropping spine2 and its links leaves a consistent single-spine fabric."""

    def single_spine(raw):
        del raw["devices"]["spine2"]
        raw["links"] = [
            link
            for link in raw["links"]
            if "spine2" not in (link["a"]["device"], link["b"]["device"])
        ]

    assert validate(mutated(single_spine)) == []
