from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import Conflict, ValidationFailed
from app.models import NetworkInterface, NetworkSettings, User
from app.models.enums import InterfaceRole, ServiceStatus
from app.services.audit import write_audit
from app.services.host import HostError, host

log = logging.getLogger(__name__)


@dataclass
class NetworkPlan:
    netplan_yaml: str
    sysctl_content: str
    hash: str
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    validations: dict = field(default_factory=dict)
    host_available: bool = True

    @property
    def valid(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "netplan_yaml": self.netplan_yaml,
            "sysctl_content": self.sysctl_content,
            "hash": self.hash,
            "warnings": self.warnings,
            "errors": self.errors,
            "validations": self.validations,
            "host_available": self.host_available,
        }


def validate_interface_name(name: str | None) -> str | None:
    if not name:
        return None
    if not name.replace(".", "").replace("-", "").replace("_", "").replace(":", "").replace("@", "").isalnum():
        raise ValidationFailed(f"Invalid interface name: {name}")
    if len(name) > 64:
        raise ValidationFailed("Interface name is too long")
    return name


def validate_ipv6(value: str | None, field: str) -> str | None:
    if not value:
        return None
    try:
        network = ipaddress.IPv6Network(value, strict=False)
    except ValueError as exc:
        raise ValidationFailed(f"{field} must be a valid IPv6 prefix/address") from exc
    return str(network)


def validate_ipv4(value: str | None, field: str) -> str | None:
    if not value:
        return None
    try:
        return str(ipaddress.IPv4Address(value))
    except ValueError as exc:
        raise ValidationFailed(f"{field} must be a valid IPv4 address") from exc


def validate_settings(record: NetworkSettings) -> list[str]:
    warnings: list[str] = []
    for name in (record.wan4_interface, record.wan6_interface, record.lan6_interface):
        validate_interface_name(name)
    if record.client_prefix:
        network = ipaddress.IPv6Network(record.client_prefix, strict=False)
        if network.prefixlen > 64:
            warnings.append("Client prefix is longer than /64; SLAAC will not work for typical clients")
    if record.nat64_prefix:
        net = ipaddress.IPv6Network(record.nat64_prefix, strict=True)
        if net.prefixlen not in {32, 40, 48, 56, 64, 96}:
            raise ValidationFailed("NAT64 prefix must be /32, /40, /48, /56, /64 or /96")
    validate_ipv4(record.default_gateway4, "default_gateway4")
    validate_ipv6(record.default_gateway6, "default_gateway6")
    if not (576 <= record.mtu <= 9216):
        raise ValidationFailed("MTU must be between 576 and 9216")
    if record.manage_routes and not (record.default_gateway4 or record.default_gateway6):
        warnings.append("manage_routes is enabled but no default gateway is configured")
    return warnings


def render_netplan(record: NetworkSettings) -> str:
    config: dict[str, Any] = {"network": {"version": 2, "renderer": "networkd", "ethernets": {}}}
    interfaces = {
        name
        for name in (record.wan4_interface, record.wan6_interface, record.lan6_interface)
        if name
    }
    for name in sorted(interfaces):
        entry: dict[str, Any] = {}
        if record.manage_mtu:
            entry["mtu"] = record.mtu
        if entry:
            config["network"]["ethernets"][name] = entry
    if record.manage_routes:
        # Netplan applies routes per-interface. Attach each default route to the
        # WAN interface that owns that address family; if none is selected, omit
        # the route rather than guessing an interface.
        if record.default_gateway4 and record.wan4_interface:
            ethernet = config["network"]["ethernets"].setdefault(record.wan4_interface, {})
            ethernet.setdefault("routes", []).append(
                {"to": "0.0.0.0/0", "via": record.default_gateway4}
            )
        if record.default_gateway6 and record.wan6_interface:
            ethernet = config["network"]["ethernets"].setdefault(record.wan6_interface, {})
            ethernet.setdefault("routes", []).append(
                {"to": "::/0", "via": record.default_gateway6}
            )
    lines = [
        "# Managed by NAT64 ISP Manager. Changes are applied only through an approved change request.",
        "network:",
        "  version: 2",
        "  renderer: networkd",
    ]
    ethernets = config["network"]["ethernets"]
    if not ethernets:
        lines.append("  ethernets: {}")
    else:
        lines.append("  ethernets:")
        for name, entry in ethernets.items():
            lines.append(f"    {name}:")
            for key, value in entry.items():
                if key == "routes":
                    lines.append("      routes:")
                    for route in value:
                        lines.append(f"        - to: {route['to']}")
                        lines.append(f"          via: {route['via']}")
                else:
                    lines.append(f"      {key}: {value}")
    lines.append("")
    return "\n".join(lines)


def render_sysctl(record: NetworkSettings) -> str:
    lines = ["# Managed by NAT64 ISP Manager. Applied with sysctl --system."]
    if record.ipv4_forwarding:
        lines.append("net.ipv4.ip_forward=1")
    if record.ipv6_forwarding:
        lines.extend(
            [
                "net.ipv6.conf.all.forwarding=1",
                "net.ipv6.conf.default.forwarding=1",
            ]
        )
    if record.wan4_interface:
        # Reverse path filtering can drop asymmetric NAT64 return traffic on
        # multi-homed gateways. Keep it permissive and document the trade-off.
        lines.append(f"net.ipv4.conf.{record.wan4_interface}.rp_filter=2")
    lines.append("")
    return "\n".join(lines)


def content_hash(netplan: str, sysctl: str) -> str:
    return hashlib.sha256((netplan + "\n---\n" + sysctl).encode()).hexdigest()


def build_plan(db: Session, record: NetworkSettings) -> NetworkPlan:
    warnings = validate_settings(record)
    netplan = render_netplan(record)
    sysctl = render_sysctl(record)
    digest = content_hash(netplan, sysctl)
    errors: list[str] = []
    host_available = host.available
    if not host_available:
        errors.append(host.unavailable_reason() or "Host integration unavailable")
    if not record.manage_mtu and not record.manage_routes and record.ipv4_forwarding and record.ipv6_forwarding:
        warnings.append(
            "Only forwarding sysctls will change; interface MTU/route management is disabled "
            "(manage_mtu/manage_routes)."
        )
    if record.default_gateway4 or record.default_gateway6:
        warnings.append(
            "Default gateway changes can interrupt management connectivity. A rollback timer is started after apply; "
            "confirm the change from the UI."
        )
    validations: dict = {"syntax": None, "netplan": None, "host": host_available}
    if host_available:
        try:
            result = host.network("validate", netplan_yaml=netplan, sysctl_content=sysctl)
            validations["syntax"] = "ok" if result.ok else "failed"
            validations["netplan"] = result.message or "ok"
            if not result.ok:
                errors.append(f"Network validation failed: {result.message}")
        except HostError as exc:
            validations["syntax"] = "error"
            validations["netplan"] = exc.message
            errors.append(f"Network validation failed: {exc.message}")
    return NetworkPlan(
        netplan_yaml=netplan,
        sysctl_content=sysctl,
        hash=digest,
        warnings=warnings,
        errors=errors,
        validations=validations,
        host_available=host_available,
    )


def apply_plan(db: Session, record: NetworkSettings, user: User | None = None) -> dict:
    plan = build_plan(db, record)
    if not plan.valid:
        raise ValidationFailed("Network change is not valid", plan.as_dict())
    result = host.network(
        "apply",
        netplan_yaml=plan.netplan_yaml,
        sysctl_content=plan.sysctl_content,
        rollback_seconds=settings.network_rollback_seconds,
        timeout=90,
    )
    if not result.ok:
        raise ValidationFailed(f"Network apply failed: {result.message}", result.as_dict())
    record.last_render_hash = plan.hash
    record.config_version = (record.config_version or 0) + 1
    record.last_applied_at = datetime.now(UTC)
    record.last_snapshot = result.data.get("snapshot")
    record.pending_confirmation = bool(result.data.get("rollback_timer"))
    write_audit(
        db,
        action="network.apply",
        user=user,
        resource_type="network_settings",
        resource_id=record.id,
        detail={"hash": plan.hash, "snapshot": record.last_snapshot, "rollback_timer": record.pending_confirmation},
    )
    return {"plan": plan.as_dict(), "result": result.as_dict()}


def confirm(db: Session, record: NetworkSettings, user: User | None = None) -> dict:
    if not record.pending_confirmation:
        return {"confirmed": True, "message": "No pending network rollback timer"}
    if not host.available:
        raise Conflict("Host integration unavailable; cannot cancel rollback timer")
    result = host.network("confirm")
    if not result.ok:
        raise Conflict(f"Could not confirm network change: {result.message}")
    record.pending_confirmation = False
    record.rollout_confirmed_at = datetime.now(UTC)
    write_audit(
        db,
        action="network.confirm",
        user=user,
        resource_type="network_settings",
        resource_id=record.id,
        detail={"snapshot": record.last_snapshot},
    )
    return {"confirmed": True, "result": result.as_dict()}


def rollback(db: Session, record: NetworkSettings, user: User | None = None) -> dict:
    if not record.last_snapshot:
        raise Conflict("No network snapshot is recorded")
    if not host.available:
        raise Conflict("Host integration unavailable; cannot roll back")
    result = host.network("rollback", snapshot=record.last_snapshot, timeout=90)
    if not result.ok:
        raise Conflict(f"Network rollback failed: {result.message}")
    record.pending_confirmation = False
    write_audit(
        db,
        action="network.rollback",
        user=user,
        resource_type="network_settings",
        resource_id=record.id,
        detail={"snapshot": record.last_snapshot},
    )
    return {"rolled_back": True, "result": result.as_dict()}


def status(record: NetworkSettings) -> dict:
    if not host.available:
        return {
            "status": ServiceStatus.UNSUPPORTED.value,
            "message": host.unavailable_reason(),
            "pending_confirmation": record.pending_confirmation,
        }
    try:
        result = host.network("status")
        return {
            "status": ServiceStatus.ONLINE.value if result.ok else ServiceStatus.ERROR.value,
            "pending_confirmation": record.pending_confirmation,
            "rollback_timer_active": result.data.get("rollback_timer_active"),
            "snapshots": result.data.get("snapshots", []),
            "message": result.message or None,
        }
    except HostError as exc:
        return {"status": ServiceStatus.ERROR.value, "message": exc.message}


def parse_ip_json(payload: str) -> list[dict]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValidationFailed("Could not parse `ip -j` output") from exc
    if not isinstance(data, list):
        return []
    interfaces = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("ifname")
        if not name:
            continue
        addresses = []
        for addr in item.get("addr_info") or []:
            local = addr.get("local")
            if not local:
                continue
            prefix = addr.get("prefixlen")
            addresses.append(
                {
                    "family": addr.get("family"),
                    "address": f"{local}/{prefix}" if prefix is not None else local,
                    "scope": addr.get("scope"),
                    "dynamic": bool(addr.get("dynamic")),
                }
            )
        linkinfo = item.get("linkinfo") or {}
        vlan_id = None
        if "." in name and name.rsplit(".", 1)[-1].isdigit():
            vlan_id = int(name.rsplit(".", 1)[-1])
        interfaces.append(
            {
                "name": name,
                "mac": item.get("address"),
                "mtu": item.get("mtu"),
                "is_up": item.get("operstate") == "UP" or item.get("flags", []).count("UP") > 0,
                "operstate": item.get("operstate"),
                "addresses": addresses,
                "vlan_id": vlan_id,
                "kind": linkinfo.get("info_kind") or "physical",
            }
        )
    return interfaces


def _infer_role(item: dict, default4: str | None, default6: str | None) -> str:
    name = item["name"]
    if name == "lo":
        return InterfaceRole.OTHER.value
    if name == default4:
        return InterfaceRole.WAN4.value
    if name == default6:
        return InterfaceRole.WAN6.value
    v6_global = [a for a in item.get("addresses", []) if a.get("family") == "inet6" and a.get("scope") == "global"]
    if v6_global:
        return InterfaceRole.LAN6.value
    return InterfaceRole.OTHER.value


def detect_interfaces(db: Session, user: User | None = None) -> dict:
    if not host.available:
        return {
            "status": ServiceStatus.UNSUPPORTED.value,
            "message": host.unavailable_reason(),
            "interfaces": [],
        }
    try:
        result = host.ip("interfaces")
        if not result.ok:
            return {"status": "error", "message": result.message, "interfaces": []}
        interfaces = parse_ip_json(result.stdout)
        default4 = default6 = None
        try:
            routes4 = host.ip("routes4")
            routes6 = host.ip("routes6")
            for route in json.loads(routes4.stdout or "[]"):
                if route.get("dst") == "default" and route.get("dev"):
                    default4 = route["dev"]
                    break
            for route in json.loads(routes6.stdout or "[]"):
                if route.get("dst") == "default" and route.get("dev"):
                    default6 = route["dev"]
                    break
        except Exception:
            pass
        now = datetime.now(UTC)
        seen = set()
        for item in interfaces:
            seen.add(item["name"])
            record = db.scalar(select(NetworkInterface).where(NetworkInterface.name == item["name"]))
            if not record:
                record = NetworkInterface(
                    name=item["name"],
                    role=_infer_role(item, default4, default6),
                    mac_address=item.get("mac"),
                    mtu=item.get("mtu"),
                    is_up=item.get("is_up", False),
                    vlan_id=item.get("vlan_id"),
                    addresses=item.get("addresses", []),
                    last_seen_at=now,
                )
                db.add(record)
            else:
                record.mac_address = item.get("mac") or record.mac_address
                record.mtu = item.get("mtu") or record.mtu
                record.is_up = item.get("is_up", record.is_up)
                record.vlan_id = item.get("vlan_id") or record.vlan_id
                record.addresses = item.get("addresses", [])
                record.last_seen_at = now
                if record.role == InterfaceRole.OTHER.value:
                    record.role = _infer_role(item, default4, default6)
        write_audit(
            db,
            action="network.detect_interfaces",
            user=user,
            resource_type="network_interfaces",
            detail={"count": len(seen), "default4": default4, "default6": default6},
        )
        return {
            "status": "ok",
            "interfaces": interfaces,
            "default_gateway_interface4": default4,
            "default_gateway_interface6": default6,
        }
    except HostError as exc:
        return {"status": "error", "message": exc.message, "interfaces": []}
