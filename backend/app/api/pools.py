from __future__ import annotations

import ipaddress

from fastapi import Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import require_permission
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.routing import APIRouter
from app.models import IPv4Address, IPv4Pool, IPv6Prefix, JoolInstance, Nat64Session, User
from app.schemas import (
    AddressCreate,
    AddressImport,
    AddressUpdate,
    PoolCreate,
    PoolUpdate,
    PrefixCreate,
    PrefixUpdate,
)
from app.services import ipv4, network
from app.services.audit import write_audit

router = APIRouter(tags=["ipv4-pools"])

READ = require_permission("read")
POOLS = require_permission("pools.write")
PREFIXES = require_permission("prefixes.write")


def _pool(db: Session, pool_id: int) -> IPv4Pool:
    record = db.get(IPv4Pool, pool_id)
    if not record:
        raise NotFound("IPv4 pool", pool_id)
    return record


def _validate_instance(db: Session, instance_id: int | None) -> None:
    if instance_id is not None and not db.get(JoolInstance, instance_id):
        raise ValidationFailed(f"Jool instance {instance_id} does not exist")


@router.get("/pools/summary")
def pool_summary(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    return ipv4.summary(db)


@router.get("/pools")
def list_pools(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    return {"items": ipv4.list_pools(db), "defaults": ipv4.summary(db)}


@router.post("/pools", status_code=201)
def create_pool(
    payload: PoolCreate,
    request: Request,
    user: User = Depends(POOLS),
    db: Session = Depends(get_db),
) -> dict:
    if db.scalar(select(IPv4Pool).where(IPv4Pool.name == payload.name)):
        raise Conflict("A pool with this name already exists")
    _validate_instance(db, payload.jool_instance_id)
    record = IPv4Pool(
        name=payload.name,
        description=payload.description,
        enabled=payload.enabled,
        jool_instance_id=payload.jool_instance_id,
        color=payload.color,
    )
    db.add(record)
    db.flush()
    write_audit(
        db,
        action="ipv4_pool.create",
        user=user,
        request=request,
        resource_type="ipv4_pool",
        resource_id=record.id,
        detail={"name": record.name},
    )
    return {"id": record.id, "name": record.name}


@router.get("/pools/{pool_id}")
def get_pool(pool_id: int, _: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    record = _pool(db, pool_id)
    data = next((item for item in ipv4.list_pools(db) if item["id"] == record.id), None)
    if data is None:
        raise NotFound("IPv4 pool", pool_id)
    return data


@router.patch("/pools/{pool_id}")
def update_pool(
    pool_id: int,
    payload: PoolUpdate,
    request: Request,
    user: User = Depends(POOLS),
    db: Session = Depends(get_db),
) -> dict:
    record = _pool(db, pool_id)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("name") and db.scalar(
        select(IPv4Pool).where(IPv4Pool.name == changes["name"], IPv4Pool.id != pool_id)
    ):
        raise Conflict("A pool with this name already exists")
    if "jool_instance_id" in changes:
        _validate_instance(db, changes["jool_instance_id"])
    for key, value in changes.items():
        if hasattr(record, key):
            setattr(record, key, value)
    write_audit(
        db,
        action="ipv4_pool.update",
        user=user,
        request=request,
        resource_type="ipv4_pool",
        resource_id=record.id,
        detail={key: str(value) for key, value in changes.items()},
    )
    return {"id": record.id, "updated": sorted(changes)}


@router.delete("/pools/{pool_id}")
def delete_pool(
    pool_id: int,
    request: Request,
    force: bool = Query(default=False),
    user: User = Depends(POOLS),
    db: Session = Depends(get_db),
) -> dict:
    record = _pool(db, pool_id)
    addresses = [row.address for row in record.addresses]
    if addresses and not force:
        active = db.scalar(
            select(func.count(Nat64Session.id)).where(
                Nat64Session.status == "active", Nat64Session.translated_ipv4.in_(addresses)
            )
        ) or 0
        if active:
            raise Conflict(
                f"{active} active session(s) use this pool; disable it or pass force=true to delete anyway",
                {"active_sessions": active},
            )
    write_audit(
        db,
        action="ipv4_pool.delete",
        user=user,
        request=request,
        resource_type="ipv4_pool",
        resource_id=record.id,
        detail={"name": record.name, "addresses": len(addresses), "force": force},
    )
    db.delete(record)
    return {"deleted": True, "id": pool_id}


@router.post("/pools/{pool_id}/addresses", status_code=201)
def add_address(
    pool_id: int,
    payload: AddressCreate,
    request: Request,
    user: User = Depends(POOLS),
    db: Session = Depends(get_db),
) -> dict:
    record = ipv4.add_address(
        db,
        pool_id,
        payload.address,
        payload.port_min,
        payload.port_max,
        user=user,
        enabled=payload.enabled,
        label=payload.label,
    )
    return {"id": record.id, "address": record.address, "status": record.status}


@router.post("/pools/{pool_id}/addresses/import", status_code=201)
def import_addresses(
    pool_id: int,
    payload: AddressImport,
    request: Request,
    user: User = Depends(POOLS),
    db: Session = Depends(get_db),
) -> dict:
    _pool(db, pool_id)
    addresses = ipv4.parse_ipv4_import(payload.text)
    existing = {
        row.address
        for row in db.scalars(
            select(IPv4Address).where(
                IPv4Address.pool_id == pool_id,
                IPv4Address.port_min == payload.port_min,
                IPv4Address.port_max == payload.port_max,
            )
        ).all()
    }
    created: list[str] = []
    skipped: list[str] = []
    for address in addresses:
        if address in existing:
            skipped.append(address)
            continue
        ipv4.add_address(
            db,
            pool_id,
            address,
            payload.port_min,
            payload.port_max,
            user=user,
            enabled=payload.enabled,
        )
        created.append(address)
    return {
        "parsed": len(addresses),
        "created": created,
        "skipped_existing": skipped,
        "defaults": [settings.jool_pool4_default_port_min, settings.jool_pool4_default_port_max],
    }


@router.get("/addresses/{address_id}")
def get_address(
    address_id: int, _: User = Depends(READ), db: Session = Depends(get_db)
) -> dict:
    record = db.get(IPv4Address, address_id)
    if not record:
        raise NotFound("IPv4 address", address_id)
    enriched = ipv4.enrich_address_stats(db, [record], record.pool.jool_instance_id)
    return enriched[0]


@router.patch("/addresses/{address_id}")
def update_address(
    address_id: int,
    payload: AddressUpdate,
    request: Request,
    user: User = Depends(POOLS),
    db: Session = Depends(get_db),
) -> dict:
    changes = payload.model_dump(exclude_unset=True)
    record = ipv4.update_address(db, address_id, user=user, **changes)
    return {"id": record.id, "status": record.status}


@router.delete("/addresses/{address_id}")
def delete_address(
    address_id: int,
    request: Request,
    user: User = Depends(POOLS),
    db: Session = Depends(get_db),
) -> dict:
    record = db.get(IPv4Address, address_id)
    if not record:
        raise NotFound("IPv4 address", address_id)
    active = db.scalar(
        select(func.count(Nat64Session.id)).where(
            Nat64Session.status == "active", Nat64Session.translated_ipv4 == record.address
        )
    ) or 0
    if active:
        raise Conflict(
            f"{active} active session(s) use {record.address}. Disable the address and synchronize pool4 instead.",
            {"active_sessions": active},
        )
    ipv4.delete_address(db, address_id, user=user)
    return {"deleted": True, "id": address_id}


# ---------------------------------------------------------------------------
# IPv6 prefixes
# ---------------------------------------------------------------------------


def _prefix_dict(record: IPv6Prefix) -> dict:
    return {
        "id": record.id,
        "prefix": record.prefix,
        "name": record.name,
        "interface": record.interface,
        "vlan_id": record.vlan_id,
        "is_client_prefix": record.is_client_prefix,
        "enabled": record.enabled,
        "description": record.description,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _validate_prefix(value: str) -> str:
    try:
        prefix = ipaddress.IPv6Network(value, strict=True)
    except ValueError as exc:
        raise ValidationFailed(f"Invalid IPv6 prefix: {exc}") from exc
    if prefix.prefixlen > 64:
        raise ValidationFailed("Client prefixes must be /64 or shorter")
    return str(prefix)


@router.get("/prefixes")
def list_prefixes(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(IPv6Prefix).order_by(IPv6Prefix.prefix)).all()
    return {"items": [_prefix_dict(row) for row in rows]}


@router.post("/prefixes", status_code=201)
def create_prefix(
    payload: PrefixCreate,
    request: Request,
    user: User = Depends(PREFIXES),
    db: Session = Depends(get_db),
) -> dict:
    prefix = _validate_prefix(payload.prefix)
    if db.scalar(select(IPv6Prefix).where(IPv6Prefix.prefix == prefix)):
        raise Conflict("This prefix already exists")
    if payload.interface:
        network.validate_interface_name(payload.interface)
    record = IPv6Prefix(
        prefix=prefix,
        name=payload.name,
        interface=payload.interface,
        vlan_id=payload.vlan_id,
        is_client_prefix=payload.is_client_prefix,
        enabled=payload.enabled,
        description=payload.description,
    )
    db.add(record)
    db.flush()
    write_audit(
        db,
        action="ipv6_prefix.create",
        user=user,
        request=request,
        resource_type="ipv6_prefix",
        resource_id=record.id,
        detail={"prefix": prefix},
    )
    return _prefix_dict(record)


@router.patch("/prefixes/{prefix_id}")
def update_prefix(
    prefix_id: int,
    payload: PrefixUpdate,
    request: Request,
    user: User = Depends(PREFIXES),
    db: Session = Depends(get_db),
) -> dict:
    record = db.get(IPv6Prefix, prefix_id)
    if not record:
        raise NotFound("IPv6 prefix", prefix_id)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("prefix"):
        changes["prefix"] = _validate_prefix(changes["prefix"])
        duplicate = db.scalar(
            select(IPv6Prefix).where(
                IPv6Prefix.prefix == changes["prefix"], IPv6Prefix.id != prefix_id
            )
        )
        if duplicate:
            raise Conflict("This prefix already exists")
    if changes.get("interface"):
        network.validate_interface_name(changes["interface"])
    for key, value in changes.items():
        if hasattr(record, key):
            setattr(record, key, value)
    write_audit(
        db,
        action="ipv6_prefix.update",
        user=user,
        request=request,
        resource_type="ipv6_prefix",
        resource_id=record.id,
        detail={key: str(value) for key, value in changes.items()},
    )
    return _prefix_dict(record)


@router.delete("/prefixes/{prefix_id}")
def delete_prefix(
    prefix_id: int,
    request: Request,
    user: User = Depends(PREFIXES),
    db: Session = Depends(get_db),
) -> dict:
    record = db.get(IPv6Prefix, prefix_id)
    if not record:
        raise NotFound("IPv6 prefix", prefix_id)
    write_audit(
        db,
        action="ipv6_prefix.delete",
        user=user,
        request=request,
        resource_type="ipv6_prefix",
        resource_id=record.id,
        detail={"prefix": record.prefix},
    )
    db.delete(record)
    return {"deleted": True, "id": prefix_id}
