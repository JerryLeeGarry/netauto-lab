"""Rendering: derived content is right, output is deterministic, golden files are current."""

import random

import yaml
from helpers import mutated

from netauto import render


def test_committed_files_match_a_fresh_render(sot):
    """The golden-file gate CI enforces: SoT changed -> re-render and commit."""
    assert render.stale_files(sot) == []


def test_bgp_neighbours_are_derived_from_links(sot):
    ctx = render.device_context(sot, "leaf1")
    assert ctx["neighbors"] == [
        {"ip": "10.1.0.0", "remote_as": 65000, "description": "spine1"},
        {"ip": "10.1.0.4", "remote_as": 65000, "description": "spine2"},
    ]
    assert ctx["max_paths"] == 2  # one path per spine uplink


def test_both_ends_of_a_session_agree(sot):
    """Each side must point at the other side's address and AS."""
    for link in sot.links:
        a, b = sot.devices[link.a.device], sot.devices[link.b.device]
        if not (a.is_router and b.is_router):
            continue
        seen_by_a = {
            n["ip"]: n["remote_as"] for n in render.device_context(sot, link.a.device)["neighbors"]
        }
        seen_by_b = {
            n["ip"]: n["remote_as"] for n in render.device_context(sot, link.b.device)["neighbors"]
        }
        assert seen_by_a[str(link.b.ip.ip)] == b.asn
        assert seen_by_b[str(link.a.ip.ip)] == a.asn


def test_leaf_originates_loopback_and_host_subnet_only(sot):
    assert render.device_context(sot, "leaf1")["networks"] == ["10.0.0.11/32", "10.10.1.0/24"]
    assert render.device_context(sot, "spine1")["networks"] == ["10.0.0.1/32"]


def test_hosts_get_no_device_config(sot):
    assert set(render.render_configs(sot)) == {"spine1", "spine2", "leaf1", "leaf2"}


def test_output_does_not_depend_on_input_order(sot):
    def shuffle(raw):
        rng = random.Random(7)
        items = list(raw["devices"].items())
        rng.shuffle(items)
        raw["devices"] = dict(items)
        rng.shuffle(raw["links"])

    assert render.render_all(mutated(shuffle)) == render.render_all(sot)


def test_clab_topology_matches_the_sot(sot):
    topo = yaml.safe_load(render.render_clab(sot))["topology"]
    assert set(topo["nodes"]) == set(sot.devices)
    assert topo["nodes"]["leaf1"] == {"kind": "arista_ceos", "mgmt-ipv4": "172.20.20.21"}
    assert topo["nodes"]["host1"]["exec"] == [
        "ip addr add 10.10.1.10/24 dev eth1",
        "ip route add 10.0.0.0/8 via 10.10.1.1",
    ]
    assert {"endpoints": ["spine1:eth1", "leaf1:eth1"]} in topo["links"]
    assert len(topo["links"]) == len(sot.links)


def test_config_for_a_removed_device_is_reported_stale(sot, tmp_path):
    render.write_all(sot, tmp_path)
    (tmp_path / "configs" / "leaf9.cfg").write_text("left behind\n")
    assert [str(p) for p in render.stale_files(sot, tmp_path)] == ["configs/leaf9.cfg"]
    render.write_all(sot, tmp_path)
    assert render.stale_files(sot, tmp_path) == []
