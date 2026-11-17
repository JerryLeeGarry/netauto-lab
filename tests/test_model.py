"""Load-time checks: the file must parse into the model, strictly."""

import pytest
import yaml
from helpers import mutated, raw_sot

from netauto.model import SotError, parse_sot


def test_lab_sot_loads(sot):
    assert set(sot.devices) == {"spine1", "spine2", "leaf1", "leaf2", "host1", "host2"}
    assert len(sot.links) == 6


def test_duplicate_key_is_rejected():
    # Plain PyYAML would keep the second leaf1 and silently drop the first.
    text = yaml.safe_dump(raw_sot()).replace("  leaf2:", "  leaf1:", 1)
    with pytest.raises(SotError, match="duplicate key 'leaf1'"):
        parse_sot(text)


def test_unknown_field_is_rejected():
    def typo(raw):
        raw["devices"]["leaf1"]["loopback"] = raw["devices"]["leaf1"].pop("loopback0")

    with pytest.raises(SotError, match="devices.leaf1.loopback"):
        mutated(typo)


def test_malformed_address_is_rejected():
    def bad_ip(raw):
        raw["links"][0]["a"]["ip"] = "10.1.0.300/31"

    with pytest.raises(SotError, match="links.0.a.ip"):
        mutated(bad_ip)


def test_missing_required_field_is_rejected():
    def no_mgmt(raw):
        del raw["devices"]["spine1"]["mgmt_ip"]

    with pytest.raises(SotError, match="devices.spine1.mgmt_ip"):
        mutated(no_mgmt)
