"""NetBox as the Source of Truth: seed it from the SoT file, load the model back.

How the SoT model maps onto NetBox objects:

    site                -> dcim.Site
    supernet            -> ipam.Prefix with role "fabric-supernet", scoped to the site
    mgmt_subnet         -> ipam.Prefix with role "management", scoped to the site
    device              -> dcim.Device (role, platform, custom field bgp_asn)
    device.mgmt_ip      -> IP on a mgmt-only interface, set as primary_ip4
    device.loopback0    -> IP on interface Loopback0
    link end            -> dcim.Interface + ipam.IPAddress
    link                -> dcim.Cable between the two interfaces

`load` returns the same `Sot` model the YAML loader returns, so validation and
rendering do not know or care where the data came from. The round trip
(YAML -> NetBox -> model -> render) is tested to be byte-identical to
rendering straight from YAML (tests/test_netbox_live.py).

`seed` only creates what is missing. It never updates or deletes, so it is
safe to re-run and it will not overwrite edits made in NetBox.
"""

from __future__ import annotations

import os

from .model import Sot

ASN_FIELD = "bgp_asn"
ROLE_SUPERNET = "fabric-supernet"
ROLE_MGMT = "management"
MGMT_IFACE = {"eos": "Management0", "linux": "eth0"}
DEVICE_TYPE = {"eos": ("Arista", "cEOS-lab"), "linux": ("Generic", "linux-host")}


def connect(url: str | None = None, token: str | None = None):
    import pynetbox  # imported here: optional dependency

    url = url or os.environ.get("NETBOX_URL")
    token = token or os.environ.get("NETBOX_TOKEN")
    if not url or not token:
        raise RuntimeError("set NETBOX_URL and NETBOX_TOKEN")
    return pynetbox.api(url, token=token)


def _slug(text: str) -> str:
    return text.lower().replace(" ", "-")


def _ensure(endpoint, lookup: dict, **fields):
    """Return the object matching `lookup`, creating it if it does not exist."""
    return endpoint.get(**lookup) or endpoint.create(**lookup, **fields)


def _ensure_named(endpoint, name: str, **fields):
    return _ensure(endpoint, {"slug": _slug(name)}, name=name, **fields)


def _ensure_ip(nb, address: str, interface_id: int):
    return _ensure(
        nb.ipam.ip_addresses,
        {"address": address, "interface_id": interface_id},
        assigned_object_type="dcim.interface",
        assigned_object_id=interface_id,
    )


def _ensure_interface(nb, device_id: int, name: str, **fields):
    kind = "virtual" if name.startswith("Loopback") else "1000base-t"
    return _ensure(
        nb.dcim.interfaces,
        {"device_id": device_id, "name": name},
        device=device_id,
        type=kind,
        **fields,
    )


def seed(nb, sot: Sot) -> None:
    site = _ensure_named(nb.dcim.sites, sot.site, status="active")

    if not nb.extras.custom_fields.get(name=ASN_FIELD):
        nb.extras.custom_fields.create(
            name=ASN_FIELD, label="BGP ASN", type="integer", object_types=["dcim.device"]
        )

    for role_name, prefix in ((ROLE_SUPERNET, sot.supernet), (ROLE_MGMT, sot.mgmt_subnet)):
        role = _ensure_named(nb.ipam.roles, role_name)
        _ensure(
            nb.ipam.prefixes,
            {"prefix": str(prefix), "role_id": role.id},
            role=role.id,
            scope_type="dcim.site",
            scope_id=site.id,
            status="container" if role_name == ROLE_SUPERNET else "active",
        )

    interface_ids: dict[tuple[str, str], int] = {}
    for name, dev in sot.devices.items():
        vendor, model = DEVICE_TYPE[dev.platform]
        manufacturer = _ensure_named(nb.dcim.manufacturers, vendor)
        device = _ensure(
            nb.dcim.devices,
            {"name": name, "site_id": site.id},
            site=site.id,
            status="active",
            role=_ensure_named(nb.dcim.device_roles, dev.role, color="9e9e9e").id,
            platform=_ensure_named(nb.dcim.platforms, dev.platform).id,
            device_type=_ensure(
                nb.dcim.device_types,
                {"slug": _slug(model)},
                model=model,
                manufacturer=manufacturer.id,
            ).id,
            custom_fields={ASN_FIELD: dev.asn},
        )

        mgmt = _ensure_interface(nb, device.id, MGMT_IFACE[dev.platform], mgmt_only=True)
        mgmt_ip = _ensure_ip(nb, f"{dev.mgmt_ip}/{sot.mgmt_subnet.prefixlen}", mgmt.id)
        if device.primary_ip4 is None:
            device.update({"primary_ip4": mgmt_ip.id})

        if dev.loopback0 is not None:
            loopback = _ensure_interface(nb, device.id, "Loopback0")
            _ensure_ip(nb, str(dev.loopback0), loopback.id)

        for local, _ in sot.links_of(name):
            iface = _ensure_interface(nb, device.id, local.interface)
            _ensure_ip(nb, str(local.ip), iface.id)
            interface_ids[(name, local.interface)] = iface.id

    for link in sot.links:
        a_id = interface_ids[(link.a.device, link.a.interface)]
        b_id = interface_ids[(link.b.device, link.b.interface)]
        if nb.dcim.interfaces.get(a_id).cable is None:
            nb.dcim.cables.create(
                a_terminations=[{"object_type": "dcim.interface", "object_id": a_id}],
                b_terminations=[{"object_type": "dcim.interface", "object_id": b_id}],
                status="connected",
            )


def _prefix_with_role(nb, site_id: int, role: str) -> str:
    matches = [
        p
        for p in nb.ipam.prefixes.filter(role=role)
        if p.scope_type == "dcim.site" and p.scope_id == site_id
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one {role!r} prefix for the site, found {len(matches)}"
        )
    return matches[0].prefix


def load(nb, site_name: str) -> Sot:
    """Build the SoT model from NetBox. The result still goes through
    `validate` - data in NetBox is not trusted more than data in a file."""
    site = nb.dcim.sites.get(slug=_slug(site_name))
    if site is None:
        raise RuntimeError(f"site {site_name!r} not found in NetBox")

    addresses: dict[tuple[str, str], str] = {}
    for ip in nb.ipam.ip_addresses.filter(site_id=site.id, assigned_to_interface=True):
        iface = ip.assigned_object
        addresses[(iface.device.name, iface.name)] = ip.address

    devices = {}
    for dev in nb.dcim.devices.filter(site_id=site.id):
        fields = {
            "role": dev.role.slug,
            "platform": dev.platform.slug,
            "mgmt_ip": dev.primary_ip4.address.split("/")[0],
        }
        if dev.custom_fields.get(ASN_FIELD) is not None:
            fields["asn"] = dev.custom_fields[ASN_FIELD]
        if (dev.name, "Loopback0") in addresses:
            fields["loopback0"] = addresses[(dev.name, "Loopback0")]
        devices[dev.name] = fields

    links = []
    for cable in sorted(nb.dcim.cables.filter(site_id=site.id), key=lambda c: c.id):
        ends = []
        for side in (cable.a_terminations, cable.b_terminations):
            (iface,) = side
            key = (iface.device.name, iface.name)
            ends.append({"device": key[0], "interface": key[1], "ip": addresses[key]})
        links.append({"a": ends[0], "b": ends[1]})

    return Sot.model_validate(
        {
            "site": site_name,
            "supernet": _prefix_with_role(nb, site.id, ROLE_SUPERNET),
            "mgmt_subnet": _prefix_with_role(nb, site.id, ROLE_MGMT),
            "devices": devices,
            "links": links,
        }
    )
