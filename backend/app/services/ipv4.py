from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.config import settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.models import IPv4Address, IPv4Pool, JoolInstance, Nat64Session, User
from app.services.audit import write_audit
from app.services.jool import JoolError, jool
from app.services.jool.parser import JoolPool4Entry

log = logging.getLogger(__name__)
PROTOCOLS = ("tcp", "udp", "icmp")


@dataclass
class PoolSyncPlan:
    instance_id: int
    instance_name: str
    adds: list[dict] = field(default_factory=list)
    removes: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unmanaged: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    commands: list[list[str]] = field(default_factory=list)
    host_available: bool = True
    sessions_affected: int = 0

    @property
    def valid(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "instance_name": self.instance_name,
            "adds": self.adds,
            "removes": self.removes,
            "warnings": self.warnings,
            "unmanaged": self.unmanaged,
            "errors": self.errors,
            "commands": self.commands,
            "host_available": self.host_available,
            "sessions_affected": self.sessions_affected,
        }


def canonical_ipv4(value: str) -> str:
    try:
        network = ipaddress.ip_network(value.strip(), strict=False)
    except ValueError as exc:
        raise ValidationFailed(f"Invalid IPv4 address: {value}") from exc
    if network.version != 4:
        raise ValidationFailed(f"Not an IPv4 address: {value}")
    if network.prefixlen != 32:
        raise ValidationFailed("Pool entries must be individual IPv4 addresses, not subnets")
    return str(network.network_address)


def parse_ipv4_import(text: str, limit: int = 1024) -> list[str]:
    tokens: list[str] = []
    for line in text.replace(",", "\n").replace(";", "\n").splitlines():
        tokens.extend(part for part in line.split() if part.strip())
    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        try:
            network = ipaddress.ip_network(token, strict=False)
        except ValueError as exc:
            raise ValidationFailed(f"Invalid IPv4 address or CIDR: {token}") from exc
        if network.version != 4:
            raise ValidationFailed(f"Not an IPv4 address: {token}")
        if network.num_addresses > limit:
            raise ValidationFailed(f"CIDR {token} expands to more than the {limit}-address import limit")
        for address in network:
            text_address = str(address)
            if text_address not in seen:
                seen.add(text_address)
                result.append(text_address)
            if len(result) > limit:
                raise ValidationFailed(f"Import exceeds the {limit}-address limit per request")
    if not result:
        raise ValidationFailed("No IPv4 addresses found in import")
    return result


def _ensure_no_overlap(
    db: Session,
    pool_id: int,
    address: str,
    port_min: int,
    port_max: int,
    exclude_id: int | None = None,
) -> None:
    if port_min < 1 or port_max > 65535 or port_min > port_max:
        raise ValidationFailed("Port range must satisfy 1 <= min <= max <= 65535")
    stmt = select(IPv4Address).where(IPv4Address.pool_id == pool_id, IPv4Address.address == address)
    if exclude_id is not None:
        stmt = stmt.where(IPv4Address.id != exclude_id)
    for existing in db.scalars(stmt).all():
        if port_min <= existing.port_max and port_max >= existing.port_min:
            raise Conflict(
                f"Port range {port_min}-{port_max} overlaps existing range "
                f"{existing.port_min}-{existing.port_max} for {address}"
            )


def add_address(
    db: Session,
    pool_id: int,
    address: str,
    port_min: int,
    port_max: int,
    user: User | None = None,
    enabled: bool = True,
    label: str | None = None,
) -> IPv4Address:
    pool = db.get(IPv4Pool, pool_id)
    if not pool:
        raise NotFound("IPv4 pool", pool_id)
    canonical = canonical_ipv4(address)
    _ensure_no_overlap(db, pool_id, canonical, port_min, port_max)
    duplicate = db.scalar(
        select(IPv4Address).where(
            IPv4Address.pool_id == pool_id,
            IPv4Address.address == canonical,
            IPv4Address.port_min == port_min,
            IPv4Address.port_max == port_max,
        )
    )
    if duplicate:
        raise Conflict(f"{canonical} {port_min}-{port_max} already exists in this pool")
    record = IPv4Address(
        pool_id=pool_id,
        address=canonical,
        port_min=port_min,
        port_max=port_max,
        enabled=enabled,
        label=label,
        status="pending",
    )
    db.add(record)
    db.flush()
    write_audit(
        db,
        action="ipv4_address.create",
        user=user,
        resource_type="ipv4_address",
        resource_id=record.id,
        detail={"pool_id": pool_id, "address": canonical, "ports": [port_min, port_max]},
    )
    return record


def bulk_add(
    db: Session,
    pool_id: int,
    addresses: list[str],
    port_min: int,
    port_max: int,
    user: User | None = None,
    enabled: bool = True,
) -> list[IPv4Address]:
    created: list[IPv4Address] = []
    for address in addresses:
        created.append(add_address(db, pool_id, address, port_min, port_max, user=user, enabled=enabled))
    return created


def update_address(db: Session, address_id: int, user: User | None = None, **changes) -> IPv4Address:
    record = db.get(IPv4Address, address_id)
    if not record:
        raise NotFound("IPv4 address", address_id)
    if "address" in changes and changes["address"] is not None:
        changes["address"] = canonical_ipv4(changes["address"])
    port_min = changes.get("port_min", record.port_min)
    port_max = changes.get("port_max", record.port_max)
    target_address = changes.get("address", record.address)
    if any(key in changes for key in ("address", "port_min", "port_max")):
        _ensure_no_overlap(db, record.pool_id, target_address, port_min, port_max, exclude_id=record.id)
    for key, value in changes.items():
        if value is not None and hasattr(record, key):
            setattr(record, key, value)
    record.status = "pending"
    write_audit(
        db,
        action="ipv4_address.update",
        user=user,
        resource_type="ipv4_address",
        resource_id=record.id,
        detail={k: str(v) for k, v in changes.items()},
    )
    db.flush()
    return record


def delete_address(db: Session, address_id: int, user: User | None = None) -> None:
    record = db.get(IPv4Address, address_id)
    if not record:
        raise NotFound("IPv4 address", address_id)
    write_audit(
        db,
        action="ipv4_address.delete",
        user=user,
        resource_type="ipv4_address",
        resource_id=record.id,
        detail={"address": record.address, "pool_id": record.pool_id},
    )
    db.delete(record)
    db.flush()


def desired_entries(db: Session, instance_id: int) -> list[dict]:
    stmt = (
        select(IPv4Address)
        .join(IPv4Pool, IPv4Address.pool_id == IPv4Pool.id)
        .where(
            IPv4Pool.jool_instance_id == instance_id,
            IPv4Pool.enabled.is_(True),
            IPv4Address.enabled.is_(True),
        )
        .order_by(IPv4Address.address)
    )
    entries = []
    for address in db.scalars(stmt).all():
        for protocol in PROTOCOLS:
            entries.append(
                {
                    "protocol": protocol,
                    "address": address.address,
                    "port_min": address.port_min,
                    "port_max": address.port_max,
                    "pool_id": address.pool_id,
                    "address_id": address.id,
                }
            )
    return entries


def managed_addresses(db: Session, instance_id: int) -> set[str]:
    stmt = (
        select(IPv4Address.address)
        .join(IPv4Pool, IPv4Address.pool_id == IPv4Pool.id)
        .where(IPv4Pool.jool_instance_id == instance_id)
    )
    return set(db.scalars(stmt).all())


def _entry_key(entry: dict | JoolPool4Entry) -> tuple:
    if isinstance(entry, JoolPool4Entry):
        return entry.protocol, entry.address, entry.port_min, entry.port_max
    return entry["protocol"], entry["address"], entry["port_min"], entry["port_max"]


def build_sync_plan(db: Session, instance: JoolInstance) -> PoolSyncPlan:
    plan = PoolSyncPlan(instance_id=instance.id, instance_name=instance.name)
    try:
        observed: dict[str, list[JoolPool4Entry]] = {}
        for protocol in PROTOCOLS:
            observed[protocol] = jool.pool4_display(instance.name, protocol)
    except JoolError as exc:
        plan.host_available = False
        plan.errors.append(f"Jool query failed: {exc.message}")
        return plan

    desired = desired_entries(db, instance.id)
    desired_by_key = {_entry_key(item): item for item in desired}
    observed_by_key = {
        _entry_key(entry): entry for entries in observed.values() for entry in entries
    }
    managed = managed_addresses(db, instance.id)

    for key, item in desired_by_key.items():
        if key not in observed_by_key:
            plan.adds.append(
                {
                    "protocol": item["protocol"],
                    "address": item["address"],
                    "port_min": item["port_min"],
                    "port_max": item["port_max"],
                    "reason": "enabled in database but absent from Jool pool4",
                }
            )
    for key, entry in observed_by_key.items():
        if key in desired_by_key:
            continue
        if entry.address in managed:
            plan.removes.append(
                {
                    "protocol": entry.protocol,
                    "address": entry.address,
                    "port_min": entry.port_min,
                    "port_max": entry.port_max,
                    "reason": "present in Jool pool4 but disabled or removed in database",
                }
            )
        else:
            plan.unmanaged.append(
                {
                    "protocol": entry.protocol,
                    "address": entry.address,
                    "port_min": entry.port_min,
                    "port_max": entry.port_max,
                }
            )

    if plan.unmanaged:
        plan.warnings.append(
            f"{len(plan.unmanaged)} Jool pool4 entries are not managed by this application and will be left untouched"
        )
    if plan.removes:
        addresses = {(item["address"]) for item in plan.removes}
        plan.sessions_affected = (
            db.scalar(
                select(func.count(Nat64Session.id)).where(
                    Nat64Session.instance_id == instance.id,
                    Nat64Session.status == "active",
                    Nat64Session.translated_ipv4.in_(addresses),
                )
            )
            or 0
        )
        plan.warnings.append(
            f"Removing pool4 entries can drop BIB/session state. {plan.sessions_affected} active sessions currently "
            "use the affected address(es). Jool cascades removals to BIB entries by design."
        )
    if not plan.adds and not plan.removes:
        plan.warnings.append("Jool pool4 already matches the database; no changes are required")
    return plan


def apply_sync_plan(db: Session, instance: JoolInstance, user: User | None = None) -> dict:
    plan = build_sync_plan(db, instance)
    if not plan.valid:
        raise Conflict("Pool4 synchronization is not possible", plan.as_dict())
    commands: list = []
    errors: list[str] = []
    for item in plan.adds:
        try:
            commands.extend(
                jool.pool4_add(
                    instance.name,
                    item["protocol"],
                    item["address"],
                    item["port_min"],
                    item["port_max"],
                )
            )
        except JoolError as exc:
            errors.append(f"add {item['protocol']} {item['address']}: {exc.message}")
    for item in plan.removes:
        try:
            commands.extend(
                jool.pool4_remove(
                    instance.name,
                    item["protocol"],
                    item["address"],
                    item["port_min"],
                    item["port_max"],
                )
            )
        except JoolError as exc:
            errors.append(f"remove {item['protocol']} {item['address']}: {exc.message}")
    now = datetime.now(UTC)
    if not errors:
        stmt = (
            select(IPv4Address)
            .join(IPv4Pool, IPv4Address.pool_id == IPv4Pool.id)
            .where(IPv4Pool.jool_instance_id == instance.id, IPv4Address.enabled.is_(True), IPv4Pool.enabled.is_(True))
        )
        for address in db.scalars(stmt).all():
            address.status = "active"
            address.last_synced_at = now
            address.error_message = None
        observed_now: dict[str, list[dict]] = {}
        for protocol in PROTOCOLS:
            try:
                entries = jool.pool4_display(instance.name, protocol)
            except JoolError as exc:
                errors.append(f"re-read pool4 {protocol}: {exc.message}")
                continue
            observed_now[protocol] = [
                {
                    "address": entry.address,
                    "port_min": entry.port_min,
                    "port_max": entry.port_max,
                }
                for entry in entries
            ]
        if observed_now:
            instance.observed_pool4 = observed_now
        instance.last_synced_at = now
    else:
        for message in errors:
            for address in db.scalars(
                select(IPv4Address).join(IPv4Pool).where(IPv4Pool.jool_instance_id == instance.id)
            ).all():
                address.status = "error"
                address.error_message = message[:500]
    write_audit(
        db,
        action="pool4.sync",
        user=user,
        resource_type="jool_instance",
        resource_id=instance.id,
        success=not errors,
        detail={"adds": len(plan.adds), "removes": len(plan.removes), "errors": errors},
    )
    return {
        "plan": plan.as_dict(),
        "commands": commands,
        "errors": errors,
        "applied": not errors,
    }


def pool_usage(db: Session, instance_id: int | None = None) -> dict:
    """Aggregate observed active sessions per translated address.

    These are *observed session transport addresses*, not per-address BIB totals.
    Multiple sessions can reuse one BIB/transport address.
    """
    stmt = select(
        Nat64Session.translated_ipv4,
        Nat64Session.protocol,
        Nat64Session.translated_port,
        Nat64Session.client_ipv6,
        Nat64Session.client_port,
    ).where(Nat64Session.status == "active")
    if instance_id is not None:
        stmt = stmt.where(Nat64Session.instance_id == instance_id)
    by_address: dict[str, dict] = {}
    for row in db.execute(stmt).all():
        address = row.translated_ipv4
        bucket = by_address.setdefault(
            address,
            {
                "sessions": 0,
                "clients": set(),
                "ports": {"tcp": set(), "udp": set(), "icmp": set()},
                "protocols": {"tcp": 0, "udp": 0, "icmp": 0},
            },
        )
        bucket["sessions"] += 1
        bucket["protocols"][row.protocol] += 1
        bucket["clients"].add((row.client_ipv6, row.client_port))
        if row.translated_port is not None:
            bucket["ports"].setdefault(row.protocol, set()).add(row.translated_port)
    result = {}
    for address, bucket in by_address.items():
        result[address] = {
            "sessions": bucket["sessions"],
            "bindings": len(bucket["clients"]),
            "tcp": bucket["protocols"]["tcp"],
            "udp": bucket["protocols"]["udp"],
            "icmp": bucket["protocols"]["icmp"],
            "ports_used": {proto: len(values) for proto, values in bucket["ports"].items()},
        }
    return result


def enrich_address_stats(db: Session, addresses: list[IPv4Address], instance_id: int | None) -> list[dict]:
    usage = pool_usage(db, instance_id)
    output = []
    for address in addresses:
        stats = usage.get(address.address, {})
        capacity_per_proto = max(0, address.port_max - address.port_min + 1)
        total_capacity = capacity_per_proto * 3
        used_ports = sum((stats.get("ports_used") or {}).values())
        usage_percent = round((used_ports / total_capacity) * 100, 2) if total_capacity else 0.0
        status = address.status
        if not address.enabled:
            status = "disabled"
        elif usage_percent >= 95:
            status = "exhausted"
        elif address.error_message:
            status = "error"
        elif status == "pending":
            status = "pending"
        output.append(
            {
                "id": address.id,
                "pool_id": address.pool_id,
                "address": address.address,
                "port_min": address.port_min,
                "port_max": address.port_max,
                "port_capacity": capacity_per_proto,
                "enabled": address.enabled,
                "label": address.label,
                "status": status,
                "error_message": address.error_message,
                "last_synced_at": address.last_synced_at,
                "last_session_at": address.last_session_at,
                "tcp": stats.get("tcp", 0),
                "udp": stats.get("udp", 0),
                "icmp": stats.get("icmp", 0),
                "bindings": stats.get("bindings", 0),
                "sessions": stats.get("sessions", 0),
                "ports_used": used_ports,
                "ports_total": total_capacity,
                "usage_percent": usage_percent,
                "usage_basis": "observed_active_sessions",
            }
        )
    return output


def list_pools(db: Session) -> list[dict]:
    pools = db.scalars(
        select(IPv4Pool)
        .options(selectinload(IPv4Pool.addresses), joinedload(IPv4Pool.jool_instance))
        .order_by(IPv4Pool.name)
    ).all()
    result = []
    for pool in pools:
        instance_id = pool.jool_instance_id
        addresses = enrich_address_stats(db, list(pool.addresses), instance_id)
        enabled = [a for a in addresses if a["enabled"]]
        result.append(
            {
                "id": pool.id,
                "name": pool.name,
                "description": pool.description,
                "enabled": pool.enabled,
                "jool_instance_id": pool.jool_instance_id,
                "jool_instance_name": pool.jool_instance.name if pool.jool_instance else None,
                "color": pool.color,
                "created_at": pool.created_at,
                "address_count": len(addresses),
                "enabled_address_count": len(enabled),
                "addresses": addresses,
                "total_capacity": sum(a["ports_total"] for a in enabled),
                "total_used": sum(a["ports_used"] for a in enabled),
                "usage_percent": round(
                    (sum(a["ports_used"] for a in enabled) / sum(a["ports_total"] for a in enabled) * 100)
                    if sum(a["ports_total"] for a in enabled)
                    else 0.0,
                    2,
                ),
                "sessions_total": sum(a["sessions"] for a in enabled),
            }
        )
    return result


def summary(db: Session) -> dict:
    address_count = db.scalar(select(func.count(IPv4Address.id))) or 0
    enabled_count = db.scalar(select(func.count(IPv4Address.id)).where(IPv4Address.enabled.is_(True))) or 0
    pool_count = db.scalar(select(func.count(IPv4Pool.id))) or 0
    total_capacity = db.scalar(
        select(func.sum(IPv4Address.port_max - IPv4Address.port_min + 1)).where(IPv4Address.enabled.is_(True))
    ) or 0
    usage = pool_usage(db)
    used = sum(len(bucket["ports"].get(proto, set())) for bucket in usage.values() for proto in PROTOCOLS)
    active_addresses = len(usage)
    return {
        "pool_count": pool_count,
        "address_count": address_count,
        "enabled_address_count": enabled_count,
        "active_address_count": active_addresses,
        "transport_capacity": int(total_capacity) * 3,
        "observed_ports_used": used,
        "utilization_percent": round(used / (int(total_capacity) * 3) * 100, 2) if total_capacity else 0.0,
        "usage_basis": "observed_active_sessions",
        "default_port_range": [settings.jool_pool4_default_port_min, settings.jool_pool4_default_port_max],
    }
