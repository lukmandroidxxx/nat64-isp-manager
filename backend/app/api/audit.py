from __future__ import annotations

from datetime import datetime

from fastapi import Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.common import csv_response
from app.core.database import get_db
from app.core.deps import require_permission
from app.core.errors import NotFound
from app.core.routing import APIRouter
from app.models import AuditLog, User

router = APIRouter(prefix="/audit", tags=["audit"])

READ = require_permission("audit.read")

AUDIT_COLUMNS = [
    "id",
    "created_at",
    "username",
    "action",
    "resource_type",
    "resource_id",
    "success",
    "ip_address",
    "user_agent",
    "detail",
]


def _audit_dict(row: AuditLog) -> dict:
    return {
        "id": row.id,
        "created_at": row.created_at,
        "username": row.username,
        "action": row.action,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "success": row.success,
        "ip_address": row.ip_address,
        "user_agent": row.user_agent,
        "detail": row.detail,
    }


def _filters(
    username: str | None,
    action: str | None,
    resource_type: str | None,
    success: bool | None,
    search: str | None,
    start: datetime | None,
    end: datetime | None,
) -> list:
    conditions = []
    if username:
        conditions.append(AuditLog.username == username)
    if action:
        conditions.append(AuditLog.action == action)
    if resource_type:
        conditions.append(AuditLog.resource_type == resource_type)
    if success is not None:
        conditions.append(AuditLog.success.is_(success))
    if start:
        conditions.append(AuditLog.created_at >= start)
    if end:
        conditions.append(AuditLog.created_at <= end)
    if search:
        like = f"%{search}%"
        conditions.append(
            or_(
                AuditLog.action.ilike(like),
                AuditLog.username.ilike(like),
                AuditLog.resource_type.ilike(like),
                AuditLog.resource_id.ilike(like),
            )
        )
    return conditions


@router.get("")
def list_audit(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    username: str | None = Query(default=None, max_length=64),
    action: str | None = Query(default=None, max_length=128),
    resource_type: str | None = Query(default=None, max_length=64),
    success: bool | None = None,
    search: str | None = Query(default=None, max_length=128),
    start: datetime | None = None,
    end: datetime | None = None,
    _: User = Depends(READ),
    db: Session = Depends(get_db),
) -> dict:
    conditions = _filters(username, action, resource_type, success, search, start, end)
    total = db.scalar(select(func.count(AuditLog.id)).where(*conditions)) or 0
    rows = db.scalars(
        select(AuditLog)
        .where(*conditions)
        .order_by(AuditLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_audit_dict(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size if page_size else 0,
        "filters": {
            "username": username,
            "action": action,
            "resource_type": resource_type,
            "success": success,
            "search": search,
            "start": start,
            "end": end,
        },
    }


@router.get("/actions")
def list_actions(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    rows = db.execute(
        select(AuditLog.action, func.count(AuditLog.id).label("count"))
        .group_by(AuditLog.action)
        .order_by(AuditLog.action)
    ).all()
    return {"items": [{"action": row.action, "count": row.count} for row in rows]}


@router.get("/export.csv")
def export_audit(
    username: str | None = Query(default=None, max_length=64),
    action: str | None = Query(default=None, max_length=128),
    resource_type: str | None = Query(default=None, max_length=64),
    success: bool | None = None,
    search: str | None = Query(default=None, max_length=128),
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(default=50_000, ge=1, le=500_000),
    _: User = Depends(READ),
    db: Session = Depends(get_db),
):
    conditions = _filters(username, action, resource_type, success, search, start, end)
    rows = db.scalars(
        select(AuditLog).where(*conditions).order_by(AuditLog.created_at.desc()).limit(limit)
    ).all()

    def serialized():
        for row in rows:
            item = _audit_dict(row)
            item["detail"] = str(item["detail"])
            yield item

    return csv_response("audit-log.csv", AUDIT_COLUMNS, serialized())


@router.get("/{audit_id}")
def get_audit(audit_id: int, _: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    row = db.get(AuditLog, audit_id)
    if not row:
        raise NotFound("Audit log", audit_id)
    return _audit_dict(row)
