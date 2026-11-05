from __future__ import annotations

from fastapi import Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.common import csv_response
from app.core.database import get_db
from app.core.deps import require_permission
from app.core.errors import NotFound
from app.core.routing import APIRouter
from app.models import IPv6Client, IPv6Prefix, User
from app.schemas import ClientUpdate
from app.services.audit import write_audit

router = APIRouter(prefix="/clients", tags=["clients"])

READ = require_permission("read")
WRITE = require_permission("clients.write")

CLIENT_COLUMNS = [
    "id",
    "address",
    "hostname",
    "mac_address",
    "interface",
    "vlan_id",
    "prefix",
    "active_session_count",
    "is_blocked",
    "first_seen_at",
    "last_seen_at",
    "notes",
]


def _match_prefix(address: str, prefixes: list[IPv6Prefix]) -> IPv6Prefix | None:
    import ipaddress

    try:
        ip = ipaddress.IPv6Address(address)
    except ValueError:
        return None
    best: tuple[int, IPv6Prefix] | None = None
    for prefix in prefixes:
        try:
            net = ipaddress.IPv6Network(prefix.prefix, strict=False)
        except ValueError:
            continue
        if ip in net and (best is None or net.prefixlen > best[0]):
            best = (net.prefixlen, prefix)
    return best[1] if best else None


def _client_dict(record: IPv6Client, prefix: IPv6Prefix | None = None) -> dict:
    return {
        "id": record.id,
        "address": record.address,
        "hostname": record.hostname,
        "mac_address": record.mac_address,
        "interface": record.interface,
        "vlan_id": record.vlan_id,
        "prefix_id": record.prefix_id,
        "prefix": prefix.prefix if prefix else None,
        "active_session_count": record.active_session_count,
        "is_blocked": record.is_blocked,
        "first_seen_at": record.first_seen_at,
        "last_seen_at": record.last_seen_at,
        "notes": record.notes,
        "identity_note": (
            "Client identity is the observed IPv6 address. Privacy extensions can rotate it, so it is not "
            "a durable subscriber identity."
        ),
    }


@router.get("")
def list_clients(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    search: str | None = Query(default=None, max_length=128),
    prefix_id: int | None = None,
    blocked: bool | None = None,
    active: bool | None = None,
    sort: str = Query(default="last_seen_at"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    _: User = Depends(READ),
    db: Session = Depends(get_db),
) -> dict:
    conditions = []
    if search:
        like = f"%{search}%"
        conditions.append(
            or_(
                IPv6Client.address.ilike(like),
                IPv6Client.hostname.ilike(like),
                IPv6Client.mac_address.ilike(like),
                IPv6Client.notes.ilike(like),
            )
        )
    if prefix_id is not None:
        conditions.append(IPv6Client.prefix_id == prefix_id)
    if blocked is not None:
        conditions.append(IPv6Client.is_blocked.is_(blocked))
    if active is True:
        conditions.append(IPv6Client.active_session_count > 0)
    elif active is False:
        conditions.append(IPv6Client.active_session_count == 0)
    sort_column = {
        "last_seen_at": IPv6Client.last_seen_at,
        "first_seen_at": IPv6Client.first_seen_at,
        "address": IPv6Client.address,
        "active_session_count": IPv6Client.active_session_count,
        "hostname": IPv6Client.hostname,
    }.get(sort, IPv6Client.last_seen_at)
    ordering = sort_column.asc() if order == "asc" else sort_column.desc()
    total = db.scalar(select(func.count(IPv6Client.id)).where(*conditions)) or 0
    rows = db.scalars(
        select(IPv6Client)
        .where(*conditions)
        .order_by(ordering)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    prefixes = {p.id: p for p in db.scalars(select(IPv6Prefix)).all()}
    return {
        "items": [_client_dict(row, prefixes.get(row.prefix_id)) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size if page_size else 0,
        "filters": {
            "search": search,
            "prefix_id": prefix_id,
            "blocked": blocked,
            "active": active,
            "sort": sort,
            "order": order,
        },
    }


@router.get("/export.csv")
def export_clients(
    search: str | None = Query(default=None, max_length=128),
    blocked: bool | None = None,
    _: User = Depends(READ),
    db: Session = Depends(get_db),
):
    conditions = []
    if search:
        like = f"%{search}%"
        conditions.append(
            or_(
                IPv6Client.address.ilike(like),
                IPv6Client.hostname.ilike(like),
                IPv6Client.mac_address.ilike(like),
            )
        )
    if blocked is not None:
        conditions.append(IPv6Client.is_blocked.is_(blocked))
    prefixes = {p.id: p for p in db.scalars(select(IPv6Prefix)).all()}
    rows = db.scalars(
        select(IPv6Client).where(*conditions).order_by(IPv6Client.last_seen_at.desc())
    ).all()
    return csv_response(
        "ipv6-clients.csv",
        CLIENT_COLUMNS,
        (_client_dict(row, prefixes.get(row.prefix_id)) for row in rows),
    )


@router.get("/summary")
def client_summary(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    total = db.scalar(select(func.count(IPv6Client.id))) or 0
    active = db.scalar(
        select(func.count(IPv6Client.id)).where(IPv6Client.active_session_count > 0)
    ) or 0
    blocked = db.scalar(select(func.count(IPv6Client.id)).where(IPv6Client.is_blocked.is_(True))) or 0
    return {
        "known": total,
        "active": active,
        "blocked": blocked,
        "identity_note": "Counts are observed IPv6 addresses, not unique subscribers.",
    }


@router.get("/{client_id}")
def get_client(client_id: int, _: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    record = db.get(IPv6Client, client_id)
    if not record:
        raise NotFound("IPv6 client", client_id)
    prefix = db.get(IPv6Prefix, record.prefix_id) if record.prefix_id else None
    return _client_dict(record, prefix)


@router.patch("/{client_id}")
def update_client(
    client_id: int,
    payload: ClientUpdate,
    request: Request,
    user: User = Depends(WRITE),
    db: Session = Depends(get_db),
) -> dict:
    record = db.get(IPv6Client, client_id)
    if not record:
        raise NotFound("IPv6 client", client_id)
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        if hasattr(record, key):
            setattr(record, key, value)
    write_audit(
        db,
        action="ipv6_client.update",
        user=user,
        request=request,
        resource_type="ipv6_client",
        resource_id=record.id,
        detail={key: str(value) for key, value in changes.items()},
    )
    prefix = db.get(IPv6Prefix, record.prefix_id) if record.prefix_id else None
    result = _client_dict(record, prefix)
    if "is_blocked" in changes:
        result["firewall_note"] = (
            "The block takes effect in nftables only after an approved firewall.apply change request."
        )
    return result


@router.post("/{client_id}/block")
def block_client(
    client_id: int,
    request: Request,
    blocked: bool = Query(default=True),
    user: User = Depends(WRITE),
    db: Session = Depends(get_db),
) -> dict:
    record = db.get(IPv6Client, client_id)
    if not record:
        raise NotFound("IPv6 client", client_id)
    record.is_blocked = blocked
    write_audit(
        db,
        action="ipv6_client.block" if blocked else "ipv6_client.unblock",
        user=user,
        request=request,
        resource_type="ipv6_client",
        resource_id=record.id,
        detail={"address": record.address},
    )
    return {
        "id": record.id,
        "address": record.address,
        "is_blocked": blocked,
        "firewall_note": "Apply the firewall change request to push this block to nftables.",
    }
