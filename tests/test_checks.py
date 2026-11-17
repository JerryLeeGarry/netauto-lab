"""State checks against synthetic NAPALM getter output."""

import copy

from helpers import healthy_state, unconfigured_state

from netauto.checks import check_intent, check_regression, failures
from netauto.intent import intent_for


def _failed(results):
    return {(r.check, r.subject) for r in failures(results)}


def test_intent_is_derived_from_the_sot(sot):
    intent = intent_for(sot, "leaf1")
    assert intent.interfaces == ["Ethernet1", "Ethernet2", "Ethernet3", "Loopback0"]
    assert intent.bgp == {"10.1.0.0": 65000, "10.1.0.4": 65000}
    # host1 is a Linux box without LLDP, so Ethernet3 has no LLDP expectation
    assert intent.lldp == {
        "Ethernet1": ("spine1", "Ethernet1"),
        "Ethernet2": ("spine2", "Ethernet1"),
    }


def test_healthy_device_passes_every_check(sot):
    intent = intent_for(sot, "leaf1")
    results = check_intent(intent, healthy_state(intent))
    assert failures(results) == []
    assert {r.check for r in results} == {
        "C01-interface-up",
        "C02-lldp-cabling",
        "C03-bgp-established",
        "C04-bgp-prefixes-received",
    }


def test_unconfigured_device_fails(sot):
    intent = intent_for(sot, "leaf1")
    failed = _failed(check_intent(intent, unconfigured_state()))
    assert ("C01-interface-up", "Ethernet1") in failed
    assert ("C03-bgp-established", "10.1.0.0") in failed


def test_interface_down(sot):
    intent = intent_for(sot, "leaf1")
    state = healthy_state(intent)
    state["interfaces"]["Ethernet2"]["is_up"] = False
    assert _failed(check_intent(intent, state)) == {("C01-interface-up", "Ethernet2")}


def test_miscabled_link_is_caught_by_lldp(sot):
    intent = intent_for(sot, "leaf1")
    state = healthy_state(intent)
    state["lldp"]["Ethernet1"] = [{"hostname": "spine2", "port": "Ethernet1"}]
    assert _failed(check_intent(intent, state)) == {("C02-lldp-cabling", "Ethernet1")}


def test_lldp_hostname_with_domain_suffix_still_matches(sot):
    intent = intent_for(sot, "leaf1")
    state = healthy_state(intent)
    state["lldp"]["Ethernet1"] = [{"hostname": "spine1.lab.example", "port": "Ethernet1"}]
    assert failures(check_intent(intent, state)) == []


def test_bgp_session_down(sot):
    intent = intent_for(sot, "leaf1")
    state = healthy_state(intent)
    peer = state["bgp"]["global"]["peers"]["10.1.0.4"]
    peer["is_up"] = False
    peer["address_family"]["ipv4"]["received_prefixes"] = -1
    assert _failed(check_intent(intent, state)) == {
        ("C03-bgp-established", "10.1.0.4"),
        ("C04-bgp-prefixes-received", "10.1.0.4"),
    }


def test_bgp_peer_in_wrong_as(sot):
    intent = intent_for(sot, "leaf1")
    state = healthy_state(intent)
    state["bgp"]["global"]["peers"]["10.1.0.0"]["remote_as"] = 65001
    assert _failed(check_intent(intent, state)) == {("C03-bgp-established", "10.1.0.0")}


def test_session_up_but_no_routes(sot):
    intent = intent_for(sot, "leaf1")
    state = healthy_state(intent)
    state["bgp"]["global"]["peers"]["10.1.0.0"]["address_family"]["ipv4"]["received_prefixes"] = 0
    assert _failed(check_intent(intent, state)) == {("C04-bgp-prefixes-received", "10.1.0.0")}


def test_peer_unknown_to_the_sot_is_drift(sot):
    intent = intent_for(sot, "leaf1")
    state = healthy_state(intent)
    state["bgp"]["global"]["peers"]["10.99.0.1"] = {"is_up": True, "remote_as": 65099}
    assert _failed(check_intent(intent, state)) == {("C05-no-unexpected-bgp-peer", "10.99.0.1")}


def test_regression_is_caught_even_outside_the_intent(sot):
    """Something that worked before the change and not after is a failure,
    whether or not the SoT mentions it."""
    intent = intent_for(sot, "leaf1")
    pre = healthy_state(intent)
    pre["interfaces"]["Ethernet9"] = {"is_up": True}
    post = copy.deepcopy(pre)
    post["interfaces"]["Ethernet9"]["is_up"] = False
    post["bgp"]["global"]["peers"]["10.1.0.0"]["is_up"] = False
    assert _failed(check_regression("leaf1", pre, post)) == {
        ("C06-no-regression", "Ethernet9"),
        ("C06-no-regression", "10.1.0.0"),
    }


def test_no_regression_when_nothing_was_up_before(sot):
    intent = intent_for(sot, "leaf1")
    assert check_regression("leaf1", unconfigured_state(), healthy_state(intent)) == []
