from __future__ import annotations

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.core.errors import NotFound
from app.core.routing import APIRouter
from app.models import IPv4Pool, JoolInstance, Nat64Session, User
from app.schemas import JoolGlobalUpdate, JoolInstanceCreate, JoolInstanceUpdate, JoolRecreate
from app.services import actions, changes, ipv4
from app.services.host import host
from app.services.jool import JoolError, jool

router = APIRouter(prefix="/jool", tags=["jool"])

READ = require_permission("read")
REQUEST = require_permission("changes.request")


def _instance(db: Session, instance_id: int) -> JoolInstance:
    record = db.get(JoolInstance, instance_id)
    if not record:
        raise NotFound("Jool instance", instance_id)
    return record


def _instance_dict(db: Session, record: JoolInstance, *, include_observed: bool = False) -> dict:
    active_sessions = db.scalar(
        select(func.count(Nat64Session.id)).where(
            Nat64Session.instance_id == record.id, Nat64Session.status == "active"
        )
    ) or 0
    pool_count = db.scalar(
        select(func.count(IPv4Pool.id)).where(IPv4Pool.jool_instance_id == record.id)
    ) or 0
    data = {
        "id": record.id,
        "name": record.name,
        "display_name": record.display_name,
        "framework": record.framework,
        "namespace": record.namespace,
        "pool6": record.pool6,
        "observed_pool6": record.observed_pool6,
        "enabled": record.enabled,
        "paused": record.paused,
        "status": record.status,
        "jool_version": record.jool_version,
        "last_seen_at": record.last_seen_at,
        "last_synced_at": record.last_synced_at,
        "last_error": record.last_error,
        "config_version": record.config_version,
        "active_sessions": active_sessions,
        "pool_count": pool_count,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    if include_observed:
        data["observed_pool4"] = record.observed_pool4
        data["observed_stats"] = record.observed_stats
    return data


@router.get("/status")
def status(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    info = jool.status()
    rows = db.scalars(select(JoolInstance).order_by(JoolInstance.name)).all()
    return {
        "status": info.status.value,
        "installed": info.installed,
        "version": info.version,
        "compatible": info.compatible,
        "message": info.message,
        "capabilities": info.capabilities,
        "commands": info.commands,
        "instances": [_instance_dict(db, row) for row in rows],
    }


@router.get("/instances")
def list_instances(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(JoolInstance).order_by(JoolInstance.name)).all()
    return {"items": [_instance_dict(db, row) for row in rows]}


@router.get("/instances/{instance_id}")
def get_instance(
    instance_id: int, _: User = Depends(READ), db: Session = Depends(get_db)
) -> dict:
    return _instance_dict(db, _instance(db, instance_id), include_observed=True)


@router.get("/instances/{instance_id}/pool4")
def instance_pool4(
    instance_id: int, _: User = Depends(READ), db: Session = Depends(get_db)
) -> dict:
    record = _instance(db, instance_id)
    desired = ipv4.desired_entries(db, record.id)
    result: dict = {
        "instance": record.name,
        "desired": desired,
        "observed": record.observed_pool4,
        "host_available": host.available,
    }
    if not host.available:
        result.update(
            {
                "status": "unsupported",
                "message": host.unavailable_reason(),
                "sync_plan": None,
            }
        )
        return result
    try:
        plan = ipv4.build_sync_plan(db, record)
        result.update({"status": "ok", "sync_plan": plan.as_dict()})
    except JoolError as exc:
        result.update({"status": "error", "message": exc.message})
    return result


@router.get("/instances/{instance_id}/globals")
def instance_globals(
    instance_id: int, _: User = Depends(READ), db: Session = Depends(get_db)
) -> dict:
    record = _instance(db, instance_id)
    if not host.available:
        return {
            "status": "unsupported",
            "message": host.unavailable_reason(),
            "settings": {},
            "instance": record.name,
        }
    try:
        settings, commands = jool.global_settings(record.name)
        return {"status": "ok", "settings": settings, "commands": commands, "instance": record.name}
    except JoolError as exc:
        return {"status": "error", "message": exc.message, "settings": {}, "instance": record.name}


@router.post("/instances", status_code=202)
def create_instance(
    payload: JoolInstanceCreate,
    user: User = Depends(REQUEST),
    db: Session = Depends(get_db),
) -> dict:
    change = changes.create_change(
        db,
        action_name="jool.create_instance",
        payload=payload.model_dump(),
        user=user,
    )
    return changes.change_dict(change)


@router.patch("/instances/{instance_id}")
def update_instance(
    instance_id: int,
    payload: JoolInstanceUpdate,
    user: User = Depends(REQUEST),
    db: Session = Depends(get_db),
) -> dict:
    """Rename/disable are database-only metadata changes; they never touch Jool."""
    record = _instance(db, instance_id)
    changes_in = payload.model_dump(exclude_unset=True)
    for key, value in changes_in.items():
        if value is not None and hasattr(record, key):
            setattr(record, key, value)
    return _instance_dict(db, record)


@router.post("/instances/{instance_id}/pause", status_code=202)
def pause_instance(
    instance_id: int, user: User = Depends(REQUEST), db: Session = Depends(get_db)
) -> dict:
    _instance(db, instance_id)
    change = changes.create_change(
        db, action_name="jool.pause", payload={"instance_id": instance_id}, user=user
    )
    return changes.change_dict(change)


@router.post("/instances/{instance_id}/resume", status_code=202)
def resume_instance(
    instance_id: int, user: User = Depends(REQUEST), db: Session = Depends(get_db)
) -> dict:
    _instance(db, instance_id)
    change = changes.create_change(
        db, action_name="jool.resume", payload={"instance_id": instance_id}, user=user
    )
    return changes.change_dict(change)


@router.post("/instances/{instance_id}/recreate", status_code=202)
def recreate_instance(
    instance_id: int,
    payload: JoolRecreate,
    user: User = Depends(REQUEST),
    db: Session = Depends(get_db),
) -> dict:
    if payload.instance_id != instance_id:
        payload.instance_id = instance_id
    change = changes.create_change(
        db, action_name="jool.recreate_instance", payload=payload.model_dump(), user=user
    )
    return changes.change_dict(change)


@router.post("/instances/{instance_id}/sync-pool4", status_code=202)
def sync_pool4(
    instance_id: int, user: User = Depends(REQUEST), db: Session = Depends(get_db)
) -> dict:
    _instance(db, instance_id)
    change = changes.create_change(
        db, action_name="pool4.sync", payload={"instance_id": instance_id}, user=user
    )
    return changes.change_dict(change)


@router.post("/global", status_code=202)
def update_global(
    payload: JoolGlobalUpdate,
    user: User = Depends(REQUEST),
    db: Session = Depends(get_db),
) -> dict:
    change = changes.create_change(
        db, action_name="jool.update_global", payload=payload.model_dump(), user=user
    )
    return changes.change_dict(change)


@router.get("/actions")
def action_catalog(_: User = Depends(READ)) -> dict:
    return {
        "items": [
            {"name": action.name, "title": action.title, "permission": action.permission}
            for action in actions.ACTIONS.values()
        ]
    }
