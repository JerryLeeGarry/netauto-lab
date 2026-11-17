"""Source-of-Truth data model and strict loader.

Loading is the first validation layer: a file that parses into `Sot` is
structurally sound (types, required fields, no unknown or duplicate keys).
Whether it is *semantically* sound is decided separately in `validate.py`.
"""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Interface, IPv4Network
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

ROUTER_ROLES = ("spine", "leaf")


class SotError(Exception):
    """The SoT file could not be loaded into the data model."""


class _Strict(BaseModel):
    # Unknown keys are almost always typos ("loopback" for "loopback0").
    # Silently ignoring them would drop intent, so they are an error.
    model_config = ConfigDict(extra="forbid", frozen=True)


class Device(_Strict):
    role: Literal["spine", "leaf", "host"]
    platform: Literal["eos", "linux"]
    mgmt_ip: IPv4Address
    asn: int | None = None
    loopback0: IPv4Interface | None = None

    @property
    def is_router(self) -> bool:
        return self.role in ROUTER_ROLES


class Endpoint(_Strict):
    device: str
    interface: str
    ip: IPv4Interface


class Link(_Strict):
    a: Endpoint
    b: Endpoint

    @property
    def ends(self) -> tuple[Endpoint, Endpoint]:
        return (self.a, self.b)


class Sot(_Strict):
    site: str
    supernet: IPv4Network
    mgmt_subnet: IPv4Network
    devices: dict[str, Device]
    links: list[Link]

    def links_of(self, device: str) -> list[tuple[Endpoint, Endpoint]]:
        """(local end, remote end) for every link touching `device`."""
        out = []
        for link in self.links:
            if link.a.device == device:
                out.append((link.a, link.b))
            if link.b.device == device:
                out.append((link.b, link.a))
        return out


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys.

    Plain PyYAML keeps the last duplicate and discards the earlier one without
    a word. In a SoT that means a device definition can vanish silently.
    """

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise SotError(f"duplicate key {key!r} at line {key_node.start_mark.line + 1}")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def parse_sot(text: str) -> Sot:
    try:
        raw = yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass
    except yaml.YAMLError as exc:
        raise SotError(f"invalid YAML: {exc}") from exc
    try:
        return Sot.model_validate(raw)
    except ValidationError as exc:
        lines = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()]
        raise SotError("schema errors:\n  " + "\n  ".join(lines)) from exc


def load_sot(path: str | Path) -> Sot:
    return parse_sot(Path(path).read_text())
