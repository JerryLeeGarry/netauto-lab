"""The deployment sequence, driven by a fake NAPALM driver.

FakeDriver records every call and replays a scripted series of state
snapshots, so the tests can assert both the outcome and the order of
operations on the device.
"""

import pytest
from helpers import healthy_state, unconfigured_state

from netauto.deploy import check_fabric, deploy_fabric
from netauto.intent import intent_for
from netauto.render import render_configs

ROUTERS = ("spine1", "spine2", "leaf1", "leaf2")


class FakeDriver:
    def __init__(self, states, diff="+ router bgp", fail_on=None):
        self._states = list(states)  # one per state collection; the last one repeats
        self._current = None
        self.diff = diff
        self.fail_on = fail_on
        self.calls = []

    def _call(self, name):
        self.calls.append(name)
        if name == self.fail_on:
            raise ConnectionError(f"{name} failed")

    def get_interfaces(self):
        self._call("collect")
        self._current = self._states.pop(0) if len(self._states) > 1 else self._states[0]
        return self._current["interfaces"]

    def get_lldp_neighbors(self):
        return self._current["lldp"]

    def get_bgp_neighbors(self):
        return self._current["bgp"]

    def load_merge_candidate(self, config):
        self._call("load_merge")
        self.loaded = config

    def compare_config(self):
        return self.diff

    def discard_config(self):
        self._call("discard")

    def commit_config(self, revert_in=None):
        self._call(f"commit(revert_in={revert_in})")

    def confirm_commit(self):
        self._call("confirm")

    def rollback(self):
        self._call("rollback")


class FakeClock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fabric(sot):
    intents = {name: intent_for(sot, name) for name in ROUTERS}
    return render_configs(sot), intents


def _run(drivers, fabric, **kwargs):
    configs, intents = fabric
    clock = FakeClock()
    reports = deploy_fabric(drivers, configs, intents, sleep=clock.sleep, clock=clock, **kwargs)
    return reports, clock


def test_dry_run_is_the_default_and_changes_nothing(fabric):
    drivers = {name: FakeDriver([unconfigured_state()]) for name in ROUTERS}
    reports, _ = _run(drivers, fabric)
    assert {r.status for r in reports.values()} == {"dry-run"}
    assert all(r.diff and r.ok for r in reports.values())
    for driver in drivers.values():
        assert driver.calls == ["collect", "load_merge", "discard"]


def test_each_device_gets_its_own_rendered_config(fabric):
    drivers = {name: FakeDriver([unconfigured_state()]) for name in ROUTERS}
    _run(drivers, fabric)
    assert "hostname leaf2" in drivers["leaf2"].loaded
    assert "router bgp 65000" in drivers["spine1"].loaded


def test_in_sync_fabric_is_checked_but_not_touched(fabric):
    _, intents = fabric
    drivers = {name: FakeDriver([healthy_state(intents[name])], diff="") for name in ROUTERS}
    reports, _ = _run(drivers, fabric, commit=True)
    assert {r.status for r in reports.values()} == {"in-sync"}
    assert all(r.ok and r.checks for r in reports.values())
    for driver in drivers.values():
        assert driver.calls == ["collect", "load_merge", "discard"]


def test_commit_is_confirmed_once_the_fabric_converges(fabric):
    """BGP is not up on the first poll; the deploy waits, then confirms."""
    _, intents = fabric
    drivers = {
        name: FakeDriver([unconfigured_state(), unconfigured_state(), healthy_state(intents[name])])
        for name in ROUTERS
    }
    reports, clock = _run(drivers, fabric, commit=True, revert_in=300, settle_timeout=60)
    assert {r.status for r in reports.values()} == {"committed"}
    assert clock.sleeps == [5]
    for driver in drivers.values():
        assert driver.calls == [
            "collect", "load_merge", "commit(revert_in=300)", "collect", "collect", "confirm",
        ]  # fmt: skip


def test_every_device_is_committed_before_any_is_judged(fabric):
    """A session needs both ends configured, so checks must not start early."""
    _, intents = fabric
    order = []

    class Ordered(FakeDriver):
        def __init__(self, name, states):
            super().__init__(states)
            self.name = name

        def _call(self, call):
            super()._call(call)
            order.append((self.name, call.split("(")[0]))

    drivers = {
        name: Ordered(name, [unconfigured_state(), healthy_state(intents[name])])
        for name in ROUTERS
    }
    _run(drivers, fabric, commit=True)
    assert [call for _, call in order] == (
        ["collect", "load_merge"] * 4 + ["commit"] * 4 + ["collect"] * 4 + ["confirm"] * 4
    )


def test_one_failing_device_rolls_the_whole_fabric_back(fabric):
    _, intents = fabric
    drivers = {
        name: FakeDriver([unconfigured_state(), healthy_state(intents[name])]) for name in ROUTERS
    }
    drivers["leaf2"] = FakeDriver([unconfigured_state()])  # never converges
    reports, clock = _run(drivers, fabric, commit=True, revert_in=300, settle_timeout=20)
    assert {r.status for r in reports.values()} == {"rolled-back"}
    assert not reports["leaf2"].ok
    assert sum(clock.sleeps) == 20  # waited the full settle timeout, no longer
    for driver in drivers.values():
        assert driver.calls[-1] == "rollback"
        assert "confirm" not in driver.calls


def test_regression_on_an_unchanged_device_blocks_the_change(fabric):
    """spine1 has no diff of its own and still matches the SoT, but an interface
    the SoT does not know about went down during the change."""
    _, intents = fabric
    drivers = {
        name: FakeDriver([unconfigured_state(), healthy_state(intents[name])]) for name in ROUTERS
    }
    before, after = healthy_state(intents["spine1"]), healthy_state(intents["spine1"])
    before["interfaces"]["Ethernet9"] = {"is_up": True}
    after["interfaces"]["Ethernet9"] = {"is_up": False}
    drivers["spine1"] = FakeDriver([before, after], diff="")
    reports, _ = _run(drivers, fabric, commit=True, settle_timeout=10)
    assert reports["spine1"].status == "in-sync" and not reports["spine1"].ok
    assert [c.check for c in reports["spine1"].checks if not c.ok] == ["C06-no-regression"]
    assert "commit(revert_in=300)" not in drivers["spine1"].calls
    for name in ("spine2", "leaf1", "leaf2"):
        assert reports[name].status == "rolled-back"


def test_losing_a_device_mid_change_rolls_back_what_was_committed(fabric):
    drivers = {name: FakeDriver([unconfigured_state()]) for name in ROUTERS}
    drivers["leaf1"] = FakeDriver([unconfigured_state()], fail_on="commit(revert_in=300)")
    with pytest.raises(ConnectionError):
        _run(drivers, fabric, commit=True)
    # spine1 and spine2 were committed before leaf1 failed; leaf2 was never reached.
    assert drivers["spine1"].calls[-1] == "rollback"
    assert drivers["spine2"].calls[-1] == "rollback"
    assert "rollback" not in drivers["leaf2"].calls


def test_settle_timeout_must_be_shorter_than_the_revert_timer(fabric):
    drivers = {name: FakeDriver([unconfigured_state()]) for name in ROUTERS}
    with pytest.raises(ValueError, match="shorter than revert_in"):
        _run(drivers, fabric, commit=True, revert_in=60, settle_timeout=60)
    assert all(driver.calls == [] for driver in drivers.values())


def test_bgp_getter_error_fails_closed(fabric):
    """An unconfigured router may reject the BGP show commands outright."""
    _, intents = fabric

    class NoBgp(FakeDriver):
        def get_bgp_neighbors(self):
            raise RuntimeError("BGP inactive")

    drivers = {"leaf1": NoBgp([healthy_state(intents["leaf1"])])}
    report = check_fabric(drivers, {"leaf1": intents["leaf1"]})["leaf1"]
    assert report.pre["bgp_error"] == "BGP inactive"
    assert {c.check for c in report.checks if not c.ok} == {"C03-bgp-established"}


def test_check_fabric_is_read_only(fabric):
    _, intents = fabric
    drivers = {name: FakeDriver([healthy_state(intents[name])]) for name in ROUTERS}
    reports = check_fabric(drivers, intents)
    assert all(r.ok for r in reports.values())
    assert all(driver.calls == ["collect"] for driver in drivers.values())
