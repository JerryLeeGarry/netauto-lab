"""Round trip through a real NetBox. Skipped unless NETBOX_URL/NETBOX_TOKEN are set.

NETBOX_URL=http://localhost:8000 NETBOX_TOKEN=... pytest -m netbox
"""

import os

import pytest

from netauto import render
from netauto.validate import validate

pytestmark = [
    pytest.mark.netbox,
    pytest.mark.skipif(
        not (os.environ.get("NETBOX_URL") and os.environ.get("NETBOX_TOKEN")),
        reason="NETBOX_URL and NETBOX_TOKEN not set",
    ),
]


@pytest.fixture(scope="module")
def nb():
    from netauto import netbox

    return netbox.connect()


def test_seed_is_idempotent(nb, sot):
    from netauto import netbox

    netbox.seed(nb, sot)
    before = (
        nb.dcim.devices.count(),
        nb.dcim.interfaces.count(),
        nb.ipam.ip_addresses.count(),
        nb.dcim.cables.count(),
    )
    netbox.seed(nb, sot)
    after = (
        nb.dcim.devices.count(),
        nb.dcim.interfaces.count(),
        nb.ipam.ip_addresses.count(),
        nb.dcim.cables.count(),
    )
    assert before == after


def test_round_trip_renders_identically(nb, sot):
    """YAML -> NetBox -> model must render byte-for-byte like YAML -> model."""
    from netauto import netbox

    netbox.seed(nb, sot)
    from_netbox = netbox.load(nb, sot.site)
    assert validate(from_netbox) == []
    assert render.render_all(from_netbox) == render.render_all(sot)
