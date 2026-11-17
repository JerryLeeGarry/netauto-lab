"""Shared test helpers: SoT mutation and synthetic device state."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from netauto.intent import Intent
from netauto.model import Sot, parse_sot

SOT_FILE = Path(__file__).resolve().parent.parent / "sot" / "lab.yml"


def raw_sot() -> dict:
    return yaml.safe_load(SOT_FILE.read_text())


def mutated(mutation) -> Sot:
    """The valid lab SoT with one change applied to the raw data."""
    raw = copy.deepcopy(raw_sot())
    mutation(raw)
    return parse_sot(yaml.safe_dump(raw))


def healthy_state(intent: Intent) -> dict:
    """A NAPALM-shaped state snapshot in which every intent check passes."""
    return {
        "interfaces": {name: {"is_up": True, "is_enabled": True} for name in intent.interfaces},
        "lldp": {
            local: [{"hostname": host, "port": port}] for local, (host, port) in intent.lldp.items()
        },
        "bgp": {
            "global": {
                "router_id": "0.0.0.0",
                "peers": {
                    address: {
                        "is_up": True,
                        "remote_as": remote_as,
                        "address_family": {"ipv4": {"received_prefixes": 3}},
                    }
                    for address, remote_as in intent.bgp.items()
                },
            }
        },
    }


def unconfigured_state() -> dict:
    """A freshly booted device: nothing configured yet."""
    return {"interfaces": {}, "lldp": {}, "bgp": {}}
