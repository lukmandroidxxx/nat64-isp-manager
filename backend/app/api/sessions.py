from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import Depends, Query, Request
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.common import csv_response
from app.core.database import SessionLocal, get_db
from app.core.deps import require_permission
from app.core.errors import NotFound
from app.core.routing import APIRouter
from app.models import Nat64Session, User
from app.services import monitoring
from app.services.audit import write_audit

router = APIRouter(prefix="/sessions", tags=["sessions"])

READ = require_permission("read")
EXPORT = require_permission("sessions.export")
ADMIN = require_permission("settings.write")

SESSION_COLUMNS = [
    "id",
    "instance_name",
    "protocol",
    "client_ipv6",
    "client_port",
    "translated_ipv4",
    "translated_port",
    "remote_ipv4",
    "remote_port",
    "jool_state",
    "expires_in_seconds",
    "status",
    "first_seen_at",
    "last_seen_at",
    "expired_at",
    "bytes_up",
    "bytes_down",
]


def _filters(
    protocol: str | None,
    status: str | None,
    client: str | None,
    translated: str | None,
    remote: str | None,
    instance: str | None,
    search: str | None,
) -> list:
    conditions = []
    if protocol:
        conditions.append(Nat64Session.protocol == protocol)
    if status and status != "all":
        conditions.append(Nat64Session.status == status)
    if client:
        conditions.append(Nat64Session.client_ipv6.contains(client))
    if translated:
        conditions.append(Nat64Session.translated_ipv4 == translated)
    if remote:
        conditions.append(Nat64Session.remote_ipv4.contains(remote))
    if instance:
        conditions.append(Nat64Session.instance_id == int(instance))
    if search:
        like = f"%{search}%"
        conditions.append(
            or_(
                Nat64Session.client_ipv6.ilike(like),
                Nat64Session.translated_ipv4.ilike(like),
                Nat64Session.remote_ipv4.ilike(like),
                Nat64Session.jool_state.ilike(like),
            )
        )
    return conditions


@router.get("")
def list_sessions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    protocol: str | None = Query(default=None, pattern="^(tcp|udp|icmp)$"),
    status: str | None = Query(default="active", pattern="^(active|expired|stale|all)$"),
    client: str | None = Query(default=None, max_length=64),
    translated: str | None = Query(default=None, max_length=43),
    remote: str | None = Query(default=None, max_length=64),
    instance: str | None = Query(default=None, max_length=16),
    search: str | None = Query(default=None, max_length=128),
    sort: str = Query(default="last_seen_at"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    _: User = Depends(READ),
    db: Session = Depends(get_db),
) -> dict:
    return monitoring.sessions_page(
        db,
        page=page,
        page_size=page_size,
        protocol=protocol,
        status=status,
        client=client,
        translated=translated,
        remote=remote,
        instance=instance,
        search=search,
        sort=sort,
        order=order,
    )


@router.get("/export.csv")
def export_sessions(
    protocol: str | None = Query(default=None, pattern="^(tcp|udp|icmp)$"),
    status: str | None = Query(default="active", pattern="^(active|expired|stale|all)$"),
    client: str | None = Query(default=None, max_length=64),
    translated: str | None = Query(default=None, max_length=43),
    remote: str | None = Query(default=None, max_length=64),
    instance: str | None = Query(default=None, max_length=16),
    search: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=100_000, ge=1, le=1_000_000),
    _: User = Depends(EXPORT),
    db: Session = Depends(get_db),
):
    conditions = _filters(protocol, status, client, translated, remote, instance, search)
    # Use an independent session because a streaming response outlives the
    # request-scoped dependency session.
    db.close()

    def rows():
        session = SessionLocal()
        try:
            result = session.scalars(
                select(Nat64Session)
                .options(selectinload(Nat64Session.instance))
                .where(*conditions)
                .order_by(Nat64Session.last_seen_at.desc())
                .limit(limit)
                .execution_options(yield_per=1000)
            )
            for row in result:
                yield monitoring.session_dict(row)
        finally:
            session.close()

    return csv_response("nat64-sessions.csv", SESSION_COLUMNS, rows())


@router.get("/{session_id}")
def get_session(session_id: int, _: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    row = db.scalar(
        select(Nat64Session)
        .options(selectinload(Nat64Session.instance))
        .where(Nat64Session.id == session_id)
    )
    if not row:
        raise NotFound("NAT64 session", session_id)
    return monitoring.session_dict(row)


@router.delete("/expired")
def purge_expired(
    request: Request,
    retention_hours: int = Query(default=24, ge=1, le=8760),
    user: User = Depends(ADMIN),
    db: Session = Depends(get_db),
) -> dict:
    cutoff = datetime.now(UTC) - timedelta(hours=retention_hours)
    result = db.execute(
        delete(Nat64Session).where(
            Nat64Session.status.in_(["expired", "stale"]), Nat64Session.last_seen_at < cutoff
        )
    )
    deleted = int(result.rowcount or 0)
    write_audit(
        db,
        action="nat64_session.purge",
        user=user,
        request=request,
        resource_type="nat64_session",
        detail={"deleted": deleted, "retention_hours": retention_hours},
    )
    return {"deleted": deleted, "retention_hours": retention_hours}
