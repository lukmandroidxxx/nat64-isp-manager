from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.errors import Conflict, PermissionDenied, ValidationFailed
from app.models import ChangeRequest, User
from app.models.enums import ChangeStatus
from app.services import actions, changes


def _admin(db) -> User:
    return db.scalar(select(User).where(User.username == "admin"))


def _make_change(
    db,
    *,
    action: str = "dns64.apply",
    payload: dict | None = None,
    user: User | None = None,
    status: str = ChangeStatus.PENDING_APPROVAL.value,
    risk: str = "medium",
    plan: dict | None = None,
    requested_by: str | None = None,
    expires_at: datetime | None = None,
) -> ChangeRequest:
    change = ChangeRequest(
        action=action,
        target="test-target",
        payload=payload or {},
        plan=plan
        if plan is not None
        else {
            "action": action,
            "summary": "test plan",
            "risk": risk,
            "valid": True,
            "warnings": [],
            "errors": [],
            "commands": [],
            "details": {},
        },
        status=status,
        summary="test plan",
        risk=risk,
        requested_by=requested_by or (user.username if user else "admin"),
        requested_at=datetime.now(UTC),
        expires_at=expires_at or datetime.now(UTC) + timedelta(minutes=30),
        result={},
    )
    db.add(change)
    db.flush()
    return change


def test_create_change_records_invalid_dry_run(db):
    admin = _admin(db)
    change = changes.create_change(
        db,
        action_name="jool.create_instance",
        payload={"name": "bad name!", "pool6": "not-a-prefix"},
        user=admin,
    )
    assert change.status == ChangeStatus.DRY_RUN_FAILED.value
    assert change.plan["errors"]
    assert change.expires_at is not None
    assert changes.change_dict(change)["requested_by"] == admin.username


def test_approve_execute_lifecycle_with_stubbed_executor(db, monkeypatch):
    admin = _admin(db)
    change = _make_change(db, user=admin)

    approved = changes.approve_change(db, change, admin)
    assert approved.status == ChangeStatus.APPROVED.value
    assert approved.approved_by == admin.username

    action = actions.get_action("dns64.apply")
    monkeypatch.setattr(
        action,
        "executor",
        lambda db, payload, plan, user: {"stubbed": True, "by": user.username},
    )

    executed = changes.execute_change(db, approved, admin)
    assert executed.status == ChangeStatus.APPLIED.value
    assert executed.executed_at is not None
    assert executed.result["result"]["stubbed"] is True
    assert executed.result["result"]["by"] == admin.username
    assert executed.result["backup_id"] is not None


def test_execute_requires_approved_status(db):
    admin = _admin(db)
    change = _make_change(db, user=admin, status=ChangeStatus.PENDING_APPROVAL.value)
    with pytest.raises(Conflict):
        changes.execute_change(db, change, admin)
    change.status = ChangeStatus.APPROVED.value
    change.plan = {**change.plan, "valid": False}
    with pytest.raises(ValidationFailed):
        changes.execute_change(db, change, admin)


def test_expired_change_cannot_be_approved(db):
    admin = _admin(db)
    change = _make_change(
        db,
        user=admin,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    with pytest.raises(Conflict):
        changes.approve_change(db, change, admin)
    assert change.status == ChangeStatus.FAILED.value


def test_critical_change_requires_a_different_approver(db, operator_user):
    change = _make_change(
        db,
        user=operator_user,
        risk="critical",
        requested_by=operator_user.username,
    )
    with pytest.raises(PermissionDenied):
        changes.approve_change(db, change, operator_user)


def test_superadmin_only_action_cannot_be_approved_or_executed_by_admin(db, admin_user):
    superadmin = _admin(db)
    assert superadmin.is_superuser is True

    change = _make_change(db, action="ha.apply", user=superadmin, requested_by=superadmin.username)
    assert change.action == "ha.apply"

    with pytest.raises(PermissionDenied):
        changes.dry_run(db, change, admin_user)
    with pytest.raises(PermissionDenied):
        changes.approve_change(db, change, admin_user)

    # The superadmin who may approve it is still allowed through the new gate.
    approved = changes.approve_change(db, change, superadmin)
    assert approved.status == ChangeStatus.APPROVED.value

    with pytest.raises(PermissionDenied):
        changes.execute_change(db, change, admin_user)
    assert change.status == ChangeStatus.APPROVED.value


def test_reject_and_cancel(db):
    admin = _admin(db)
    first = _make_change(db, user=admin)
    rejected = changes.reject_change(db, first, admin, reason="not in maintenance window")
    assert rejected.status == ChangeStatus.REJECTED.value
    assert rejected.rejected_by == admin.username
    assert "maintenance" in rejected.error

    second = _make_change(db, user=admin)
    cancelled = changes.cancel_change(db, second, admin)
    assert cancelled.status == ChangeStatus.CANCELLED.value
    with pytest.raises(Conflict):
        changes.reject_change(db, cancelled, admin)


def test_dry_run_refresh_updates_plan(db):
    admin = _admin(db)
    change = _make_change(
        db,
        action="jool.create_instance",
        payload={"name": "invalid name", "pool6": "bad"},
        user=admin,
        status=ChangeStatus.DRY_RUN_FAILED.value,
        plan={"valid": False, "errors": ["old"]},
    )
    refreshed = changes.dry_run(db, change, admin)
    assert refreshed.status == ChangeStatus.DRY_RUN_FAILED.value
    assert refreshed.error


def test_list_changes_paginates_and_counts_pending(db):
    admin = _admin(db)
    for _ in range(3):
        _make_change(db, user=admin)
    page = changes.list_changes(db, page=1, page_size=2)
    assert page["page"] == 1
    assert page["page_size"] == 2
    assert page["total"] >= 3
    assert page["pages"] >= 2
    assert page["pending"] >= 1
    assert len(page["items"]) <= 2


def test_get_change_not_found(db):
    from app.core.errors import NotFound

    with pytest.raises(NotFound):
        changes.get_change(db, 999_999_999)
