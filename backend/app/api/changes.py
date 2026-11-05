from __future__ import annotations

from fastapi import Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.core.routing import APIRouter
from app.models import User
from app.schemas import ChangeCreate, ChangeReject
from app.services import actions, changes

router = APIRouter(prefix="/changes", tags=["changes"])

READ = require_permission("read")
REQUEST = require_permission("changes.request")
APPROVE = require_permission("changes.approve")
EXECUTE = require_permission("changes.execute")


@router.get("")
def list_changes(
    status: str | None = Query(default=None, max_length=32),
    action: str | None = Query(default=None, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _: User = Depends(READ),
    db: Session = Depends(get_db),
) -> dict:
    return changes.list_changes(db, status=status, action=action, page=page, page_size=page_size)


@router.get("/actions")
def action_catalog(_: User = Depends(READ)) -> dict:
    return {
        "items": [
            {
                "name": action.name,
                "title": action.title,
                "permission": action.permission,
            }
            for action in actions.ACTIONS.values()
        ]
    }


@router.post("", status_code=201)
def create_change(
    payload: ChangeCreate,
    request: Request,
    user: User = Depends(REQUEST),
    db: Session = Depends(get_db),
) -> dict:
    if payload.action not in actions.ACTIONS:
        from app.core.errors import ValidationFailed

        raise ValidationFailed(
            f"Unknown change action: {payload.action}",
            {"allowed": sorted(actions.ACTIONS)},
        )
    change = changes.create_change(
        db, action_name=payload.action, payload=payload.payload, user=user
    )
    return changes.change_dict(change)


@router.get("/{change_id}")
def get_change(change_id: int, _: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    return changes.change_dict(changes.get_change(db, change_id))


@router.post("/{change_id}/dry-run")
def dry_run(
    change_id: int,
    request: Request,
    user: User = Depends(REQUEST),
    db: Session = Depends(get_db),
) -> dict:
    change = changes.get_change(db, change_id)
    return changes.change_dict(changes.dry_run(db, change, user))


@router.post("/{change_id}/approve")
def approve(
    change_id: int,
    request: Request,
    user: User = Depends(APPROVE),
    db: Session = Depends(get_db),
) -> dict:
    change = changes.get_change(db, change_id)
    return changes.change_dict(changes.approve_change(db, change, user))


@router.post("/{change_id}/reject")
def reject(
    change_id: int,
    payload: ChangeReject,
    request: Request,
    user: User = Depends(APPROVE),
    db: Session = Depends(get_db),
) -> dict:
    change = changes.get_change(db, change_id)
    return changes.change_dict(
        changes.reject_change(db, change, user, reason=payload.reason)
    )


@router.post("/{change_id}/execute")
def execute(
    change_id: int,
    request: Request,
    user: User = Depends(EXECUTE),
    db: Session = Depends(get_db),
) -> dict:
    change = changes.get_change(db, change_id)
    result = changes.execute_change(db, change, user)
    return changes.change_dict(result)


@router.post("/{change_id}/cancel")
def cancel(
    change_id: int,
    request: Request,
    user: User = Depends(REQUEST),
    db: Session = Depends(get_db),
) -> dict:
    change = changes.get_change(db, change_id)
    return changes.change_dict(changes.cancel_change(db, change, user))


@router.get("/{change_id}/audit")
def change_audit(
    change_id: int,
    _: User = Depends(READ),
    db: Session = Depends(get_db),
) -> dict:
    from sqlalchemy import select

    from app.models import AuditLog

    changes.get_change(db, change_id)
    rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.resource_type == "change_request", AuditLog.resource_id == str(change_id))
        .order_by(AuditLog.created_at)
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "created_at": row.created_at,
                "username": row.username,
                "action": row.action,
                "success": row.success,
                "detail": row.detail,
            }
            for row in rows
        ]
    }
