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
    """Replays state snapshots and models running/startup config the way cEOS
    behaved in the lab: an aborted commit-confirm reverts running config only
    after a delay (`revert_lag` config reads), and NAPALM's rollback() saves
    startup before that revert lands."""

    def __init__(self, states, diff="+ router bgp", fail_on=None, revert_lag=1):
        self._states = list(states)  # one per state collection; the last one repeats
        self._current = None
        self.diff = diff
        self.fail_on = fail_on
        self.calls = []
        self.running = self.startup = "! Command: show running-config\nhostname x\n"
        self.candidate = None
        self.pending_from = None  # running config before a commit-confirm
        self.revert_lag = revert_lag
        self._reverting = None

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

    def get_config(self, retrieve="all"):
        if self._reverting is not None:
            self._reverting -= 1
            if self._reverting < 0:
                self.running, self.pending_from, self._reverting = self.pending_from, None, None
        return {"running": self.running, "startup": self.startup, "candidate": ""}

    def load_merge_candidate(self, config):
        self._call("load_merge")
        self.loaded = self.candidate = config

    def compare_config(self):
        return self.diff

    def discard_config(self):
        self._call("discard")
        if self.pending_from is not None:
            self._reverting = self.revert_lag  # aborting a pending commit: revert later
        self.candidate = None

    def commit_config(self, revert_in=None):
        self._call(f"commit(revert_in={revert_in})")
        self.pending_from = self.running
        self.running = self.running + self.candidate

    def confirm_commit(self):
        self._call("confirm")
        self.pending_from = None
        self.startup = self.running  # write memory

    def rollback(self):
        """NAPALM EOS: abort + write memory in one batch; the save wins the race."""
        self._call("rollback")
        self.startup = self.running
        self._reverting = self.revert_lag


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
    assert sum(clock.sleeps[:4]) == 20  # waited the full settle timeout, no longer
    assert {r.status for r in reports.values()} == {"rolled-back"}
    assert not reports["leaf2"].ok
    for driver in drivers.values():
        assert driver.calls[-1] == "discard"
        assert "confirm" not in driver.calls and "rollback" not in driver.calls
        assert driver.running == driver.startup  # back to pre-change, nothing saved
    for report in reports.values():
        assert [c.ok for c in report.checks if c.check == "C07-rollback-verified"] == [True]


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
    for name in ("spine1", "spine2"):
        assert drivers[name].calls[-1] == "discard"
        assert drivers[name].running == drivers[name].startup
    assert drivers["leaf2"].calls == ["collect", "load_merge"]


def test_settle_timeout_must_be_shorter_than_the_revert_timer(fabric):
    drivers = {name: FakeDriver([unconfigured_state()]) for name in ROUTERS}
    with pytest.raises(ValueError, match="must be shorter than revert_in"):
        _run(drivers, fabric, commit=True, revert_in=60, settle_timeout=40)
    assert all(driver.calls == [] for driver in drivers.values())


def test_revert_that_never_lands_is_reported_unverified(fabric):
    """If running config never returns to its pre-change text, say so loudly."""
    _, intents = fabric
    drivers = {name: FakeDriver([unconfigured_state()], revert_lag=1000) for name in ROUTERS}
    reports, _ = _run(drivers, fabric, commit=True, settle_timeout=10, revert_timeout=6)
    assert {r.status for r in reports.values()} == {"rollback-unverified"}
    for report in reports.values():
        (c07,) = [c for c in report.checks if c.check == "C07-rollback-verified"]
        assert not c07.ok and "running differ" in c07.detail


def test_dirty_startup_after_revert_is_caught(fabric):
    """Running config comes back but startup kept the change - the leaf2 case
    from the lab. Verification must look at startup too."""

    class SavesOnAbort(FakeDriver):
        def discard_config(self):
            super().discard_config()
            if self._reverting is not None:
                self.startup = self.running  # saved before the revert landed

    drivers = {name: SavesOnAbort([unconfigured_state()]) for name in ROUTERS}
    reports, _ = _run(drivers, fabric, commit=True, settle_timeout=10, revert_timeout=6)
    assert {r.status for r in reports.values()} == {"rollback-unverified"}
    for name, report in reports.items():
        assert drivers[name].running != drivers[name].startup
        (c07,) = [c for c in report.checks if c.check == "C07-rollback-verified"]
        assert c07.detail.startswith("startup differ")


def test_napalm_rollback_would_have_saved_the_change(fabric):
    """Documents the cEOS behaviour the revert works around: with NAPALM's
    rollback(), running config reverts but startup keeps the change."""
    configs, _ = fabric
    driver = FakeDriver([unconfigured_state()])
    before = driver.get_config()
    driver.load_merge_candidate(config=configs["leaf1"])
    driver.commit_config(revert_in=300)
    driver.rollback()
    for _ in range(3):
        after = driver.get_config()
    assert after["running"] == before["running"]
    assert after["startup"] != before["startup"]


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
    assert all(r.ok and r.status == "checked" for r in reports.values())
    assert all(driver.calls == ["collect"] for driver in drivers.values())
