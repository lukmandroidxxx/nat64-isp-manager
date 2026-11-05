from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog, User


def write_audit(
    db: Session,
    *,
    action: str,
    user: User | None = None,
    request: Request | None = None,
    resource_type: str | None = None,
    resource_id: Any = None,
    success: bool = True,
    detail: dict | None = None,
    username: str | None = None,
) -> AuditLog:
    ip = None
    user_agent = None
    if request is not None:
        forwarded = request.headers.get("x-forwarded-for")
        ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else None)
        user_agent = request.headers.get("user-agent")
    entry = AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else username,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        success=success,
        ip_address=ip,
        user_agent=(user_agent or "")[:512] or None,
        detail=detail or {},
    )
    db.add(entry)
    return entry
