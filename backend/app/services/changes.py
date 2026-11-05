from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.models import ChangeRequest, User
from app.models.enums import ChangeStatus, RoleName
from app.services import actions as action_registry
from app.services import backup as backup_service
from app.services.audit import write_audit

CHANGE_TTL_MINUTES = 60
EXECUTABLE = {ChangeStatus.APPROVED.value}
PENDING = {ChangeStatus.PENDING_APPROVAL.value, ChangeStatus.APPROVED.value}


def _now() -> datetime:
    return datetime.now(UTC)


def change_dict(change: ChangeRequest, *, include_payload: bool = True) -> dict:
    data = {
        "id": change.id,
        "action": change.action,
        "target": change.target,
        "summary": change.summary,
        "risk": change.risk,
        "status": change.status,
        "requested_by": change.requested_by,
        "approved_by": change.approved_by,
        "rejected_by": change.rejected_by,
        "requested_at": change.requested_at,
        "approved_at": change.approved_at,
        "executed_at": change.executed_at,
        "expires_at": change.expires_at,
        "plan": change.plan,
        "result": change.result,
        "error": change.error,
        "created_at": change.created_at,
        "updated_at": change.updated_at,
    }
    if include_payload:
        data["payload"] = change.payload
    return data


def _enforce_action_permission(action_name: str, user: User) -> None:
    """Re-check the action's own permission level.

    Route dependencies already gate create/approve/execute by RBAC permission,
    but a superadmin-only action must not become executable by an admin merely
    because a superadmin created the change request.
    """
    action = action_registry.get_action(action_name)
    is_super = user.is_superuser or user.role_name == RoleName.SUPERADMIN.value
    if action.permission == "superadmin" and not is_super:
        raise PermissionDenied("This action requires superadmin")
    if action.permission == "admin" and not is_super and user.role_name not in {
        RoleName.ADMIN.value,
        RoleName.SUPERADMIN.value,
    }:
        raise PermissionDenied("This action requires admin")


def create_change(
    db: Session,
    *,
    action_name: str,
    payload: dict,
    user: User,
) -> ChangeRequest:
    _enforce_action_permission(action_name, user)
    action = action_registry.get_action(action_name)
    try:
        plan = action.handler(db, payload)
    except Exception as exc:
        plan = action_registry.Plan(
            action=action_name,
            summary=f"{action.title} (dry run failed)",
            errors=[f"{type(exc).__name__}: {exc}"],
        )
    status = ChangeStatus.PENDING_APPROVAL.value if plan.valid else ChangeStatus.DRY_RUN_FAILED.value
    change = ChangeRequest(
        action=action_name,
        target=plan.target,
        payload=payload,
        plan=plan.as_dict(),
        status=status,
        summary=plan.summary,
        risk=plan.risk,
        requested_by=user.username,
        requested_at=_now(),
        expires_at=_now() + timedelta(minutes=CHANGE_TTL_MINUTES),
        result={},
    )
    db.add(change)
    db.flush()
    write_audit(
        db,
        action="change.create",
        user=user,
        resource_type="change_request",
        resource_id=change.id,
        success=plan.valid,
        detail={
            "action": action_name,
            "status": status,
            "risk": plan.risk,
            "errors": plan.errors,
            "warnings": plan.warnings,
        },
    )
    return change


def dry_run(db: Session, change: ChangeRequest, user: User) -> ChangeRequest:
    if change.status not in {
        ChangeStatus.DRAFT.value,
        ChangeStatus.DRY_RUN_FAILED.value,
        ChangeStatus.PENDING_APPROVAL.value,
    }:
        raise Conflict(f"Cannot re-run dry-run for a change in status {change.status}")
    _enforce_action_permission(change.action, user)
    action = action_registry.get_action(change.action)
    plan = action.handler(db, change.payload or {})
    change.plan = plan.as_dict()
    change.status = ChangeStatus.PENDING_APPROVAL.value if plan.valid else ChangeStatus.DRY_RUN_FAILED.value
    change.summary = plan.summary
    change.risk = plan.risk
    change.target = plan.target
    change.error = None if plan.valid else "; ".join(plan.errors)[:4000]
    write_audit(
        db,
        action="change.dry_run",
        user=user,
        resource_type="change_request",
        resource_id=change.id,
        success=plan.valid,
        detail={"status": change.status, "errors": plan.errors, "warnings": plan.warnings},
    )
    db.flush()
    return change


def approve_change(db: Session, change: ChangeRequest, user: User) -> ChangeRequest:
    if change.status != ChangeStatus.PENDING_APPROVAL.value:
        raise Conflict(f"Only pending changes can be approved (current: {change.status})")
    _enforce_action_permission(change.action, user)
    if change.expires_at and _as_utc(change.expires_at) < _now():
        change.status = ChangeStatus.FAILED.value
        change.error = "Change request expired before approval"
        raise Conflict("Change request has expired; create a new one")
    if (
        change.risk == "critical"
        and change.requested_by == user.username
        and not user.is_superuser
    ):
        raise PermissionDenied("A critical change must be approved by a different administrator")
    change.status = ChangeStatus.APPROVED.value
    change.approved_by = user.username
    change.approved_at = _now()
    change.expires_at = _now() + timedelta(minutes=CHANGE_TTL_MINUTES)
    write_audit(
        db,
        action="change.approve",
        user=user,
        resource_type="change_request",
        resource_id=change.id,
        detail={"action": change.action, "risk": change.risk, "requested_by": change.requested_by},
    )
    db.flush()
    return change


def reject_change(db: Session, change: ChangeRequest, user: User, reason: str | None = None) -> ChangeRequest:
    if change.status not in PENDING:
        raise Conflict(f"Cannot reject a change in status {change.status}")
    change.status = ChangeStatus.REJECTED.value
    change.rejected_by = user.username
    change.error = (reason or "Rejected by administrator")[:4000]
    write_audit(
        db,
        action="change.reject",
        user=user,
        resource_type="change_request",
        resource_id=change.id,
        detail={"reason": reason},
    )
    db.flush()
    return change


def cancel_change(db: Session, change: ChangeRequest, user: User) -> ChangeRequest:
    if change.status not in {
        ChangeStatus.DRAFT.value,
        ChangeStatus.DRY_RUN_FAILED.value,
        ChangeStatus.PENDING_APPROVAL.value,
        ChangeStatus.APPROVED.value,
    }:
        raise Conflict(f"Cannot cancel a change in status {change.status}")
    change.status = ChangeStatus.CANCELLED.value
    change.error = f"Cancelled by {user.username}"
    write_audit(
        db,
        action="change.cancel",
        user=user,
        resource_type="change_request",
        resource_id=change.id,
        detail={},
    )
    db.flush()
    return change


def execute_change(db: Session, change: ChangeRequest, user: User) -> ChangeRequest:
    if change.status not in EXECUTABLE:
        raise Conflict(f"Change must be approved before execution (current: {change.status})")
    _enforce_action_permission(change.action, user)
    if change.expires_at and _as_utc(change.expires_at) < _now():
        change.status = ChangeStatus.FAILED.value
        change.error = "Change request expired before execution"
        raise Conflict("Change request has expired; create and approve a new one")
    plan = change.plan or {}
    if not plan.get("valid", False):
        raise ValidationFailed("The approved plan is not valid and cannot be executed", plan)
    action = action_registry.get_action(change.action)
    backup = None
    if change.action != "backup.restore":
        try:
            backup = backup_service.create_backup(
                db,
                name=f"pre-{change.action}-{change.id}",
                kind="automatic",
                is_automatic=True,
                note=f"Automatic snapshot before change request {change.id}",
            )
        except Exception as exc:
            change.status = ChangeStatus.FAILED.value
            change.error = f"Automatic pre-change backup failed: {exc}"
            write_audit(
                db,
                action="change.execute",
                user=user,
                resource_type="change_request",
                resource_id=change.id,
                success=False,
                detail={"error": change.error},
            )
            db.flush()
            raise ValidationFailed(change.error) from exc
    approved_plan = action_registry.Plan(
        action=plan.get("action", change.action),
        summary=plan.get("summary", change.summary or ""),
        risk=plan.get("risk", change.risk),
        target=plan.get("target"),
        warnings=list(plan.get("warnings") or []),
        errors=list(plan.get("errors") or []),
        commands=list(plan.get("commands") or []),
        details=dict(plan.get("details") or {}),
        before=dict(plan.get("before") or {}),
        after=dict(plan.get("after") or {}),
        reversible=bool(plan.get("reversible", True)),
        requires_maintenance=bool(plan.get("requires_maintenance", False)),
        host_available=plan.get("host_available"),
    )
    try:
        result = action.executor(db, change.payload or {}, approved_plan, user)
    except Exception as exc:
        change.status = ChangeStatus.FAILED.value
        change.error = f"{type(exc).__name__}: {exc}"[:4000]
        change.executed_at = _now()
        change.result = {"backup_id": backup.id if backup else None, "error": change.error}
        write_audit(
            db,
            action="change.execute",
            user=user,
            resource_type="change_request",
            resource_id=change.id,
            success=False,
            detail={"action": change.action, "error": change.error, "backup_id": backup.id if backup else None},
        )
        db.flush()
        return change
    change.status = ChangeStatus.APPLIED.value
    change.executed_at = _now()
    change.result = {"backup_id": backup.id if backup else None, "result": result}
    change.error = None
    write_audit(
        db,
        action="change.execute",
        user=user,
        resource_type="change_request",
        resource_id=change.id,
        success=True,
        detail={"action": change.action, "backup_id": backup.id if backup else None},
    )
    db.flush()
    return change


def list_changes(
    db: Session,
    *,
    status: str | None = None,
    action: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    from sqlalchemy import func

    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    conditions = []
    if status and status != "all":
        conditions.append(ChangeRequest.status == status)
    if action:
        conditions.append(ChangeRequest.action == action)
    total = db.scalar(select(func.count(ChangeRequest.id)).where(*conditions)) or 0
    rows = db.scalars(
        select(ChangeRequest)
        .where(*conditions)
        .order_by(ChangeRequest.requested_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [change_dict(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size if page_size else 0,
        "pending": db.scalar(select(func.count(ChangeRequest.id)).where(ChangeRequest.status.in_(list(PENDING)))) or 0,
    }


def get_change(db: Session, change_id: int) -> ChangeRequest:
    change = db.get(ChangeRequest, change_id)
    if not change:
        raise NotFound("Change request", change_id)
    return change


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
