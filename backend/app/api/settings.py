from __future__ import annotations

import re

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import require_permission
from app.core.errors import NotFound, ValidationFailed
from app.core.routing import APIRouter
from app.models import Setting, User
from app.schemas import SettingUpdate
from app.services.audit import write_audit
from app.services.host import host

router = APIRouter(prefix="/settings", tags=["settings"])

READ = require_permission("read")
WRITE = require_permission("settings.write")

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,127}$")


def _setting_dict(record: Setting) -> dict:
    return {
        "key": record.key,
        "value": record.value,
        "description": record.description,
        "updated_by": record.updated_by,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


@router.get("")
def list_settings(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(Setting).order_by(Setting.key)).all()
    return {
        "items": [_setting_dict(row) for row in rows],
        "note": "These are application key/value settings. DNS64, firewall, network and HA settings have dedicated pages.",
    }


@router.get("/application")
def application_settings(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    """Expose non-secret runtime information only. Never returns keys or passwords."""
    return {
        "app": {
            "name": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
            "api_prefix": settings.api_prefix,
            "timezone": settings.timezone,
            "demo_mode": settings.demo_mode,
            "docs_enabled": settings.enable_docs,
        },
        "database": {
            "dialect": db.get_bind().dialect.name,
            "sqlite": settings.is_sqlite,
            "production_ready": not settings.is_sqlite,
        },
        "host": {
            "mode": host.mode,
            "available": host.available,
            "reason": host.unavailable_reason(),
        },
        "security": {
            "access_token_expire_minutes": settings.access_token_expire_minutes,
            "refresh_token_expire_days": settings.refresh_token_expire_days,
            "password_min_length": settings.password_min_length,
            "login_max_failures": settings.login_max_failures,
            "login_lockout_minutes": settings.login_lockout_minutes,
            "rate_limits": {
                "enabled": settings.rate_limit_enabled,
                "per_minute": settings.rate_limit_per_minute,
                "login_per_minute": settings.login_rate_limit_per_minute,
            },
            "secure_cookies": settings.secure_cookies,
        },
        "features": {
            "collector_enabled": settings.collector_enabled,
            "collector_interval_seconds": settings.collector_interval_seconds,
            "ha_enabled": settings.ha_enabled,
            "ha_role": settings.ha_role,
            "allow_package_install": settings.allow_package_install,
            "serve_frontend": settings.serve_frontend,
        },
        "paths": {
            "dns_managed_config": settings.dns_managed_config,
            "network_managed_netplan": settings.network_managed_netplan,
            "network_managed_sysctl": settings.network_managed_sysctl,
            "firewall_managed_file": settings.firewall_managed_file,
            "backup_dir": settings.backup_dir,
        },
        "warnings": [
            "Secrets and encryption keys are never returned by the API.",
            "SQLite is supported for development and tests only; production requires PostgreSQL.",
        ],
    }


@router.get("/{key}")
def get_setting(key: str, _: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    record = db.get(Setting, key)
    if not record:
        raise NotFound("Setting", key)
    return _setting_dict(record)


@router.put("/{key}")
def upsert_setting(
    key: str,
    payload: SettingUpdate,
    request: Request,
    user: User = Depends(WRITE),
    db: Session = Depends(get_db),
) -> dict:
    if not KEY_RE.fullmatch(key):
        raise ValidationFailed("Setting keys must be 2-128 characters of lowercase letters, digits, dot, dash or underscore")
    record = db.get(Setting, key)
    created = record is None
    if not record:
        record = Setting(key=key)
        db.add(record)
    record.value = payload.value
    if payload.description is not None:
        record.description = payload.description
    record.updated_by = user.username
    write_audit(
        db,
        action="setting.create" if created else "setting.update",
        user=user,
        request=request,
        resource_type="setting",
        resource_id=key,
        detail={"key": key, "changed": sorted(payload.value) if isinstance(payload.value, dict) else True},
    )
    db.flush()
    return _setting_dict(record)


@router.delete("/{key}")
def delete_setting(
    key: str,
    request: Request,
    user: User = Depends(WRITE),
    db: Session = Depends(get_db),
) -> dict:
    record = db.get(Setting, key)
    if not record:
        raise NotFound("Setting", key)
    write_audit(
        db,
        action="setting.delete",
        user=user,
        request=request,
        resource_type="setting",
        resource_id=key,
        detail={"key": key},
    )
    db.delete(record)
    return {"deleted": True, "key": key}
