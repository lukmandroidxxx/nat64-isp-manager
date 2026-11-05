from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.core.errors import Conflict, NotFound
from app.core.routing import APIRouter
from app.models import ConfigurationBackup, User
from app.schemas import BackupCreate, BackupRestore
from app.services import backup as backup_service
from app.services import changes
from app.services.audit import write_audit

router = APIRouter(prefix="/backups", tags=["backups"])

READ = require_permission("read")
CREATE = require_permission("backups.create")
RESTORE = require_permission("backups.restore")
DELETE = require_permission("backups.delete")


def _backup(db: Session, backup_id: int) -> ConfigurationBackup:
    record = db.get(ConfigurationBackup, backup_id)
    if not record:
        raise NotFound("Backup", backup_id)
    return record


def _summary(record: ConfigurationBackup) -> dict:
    return {
        "id": record.id,
        "name": record.name,
        "kind": record.kind,
        "checksum": record.checksum,
        "size_bytes": record.size_bytes,
        "note": record.note,
        "created_by": record.created_by,
        "is_automatic": record.is_automatic,
        "created_at": record.created_at,
        "file_path": record.file_path,
        "counts": {
            "pools": len(record.content.get("ipv4_pools", [])),
            "prefixes": len(record.content.get("ipv6_prefixes", [])),
            "instances": len(record.content.get("jool_instances", [])),
            "firewall_rules": len((record.content.get("firewall") or {}).get("rules", [])),
        },
    }


def _checksum(content: dict) -> str:
    raw = json.dumps(content, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


@router.get("")
def list_backups(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    return {
        "items": backup_service.list_backups(db),
        "note": "Backups contain control-plane configuration only; users, sessions, audit logs and secrets are excluded.",
    }


@router.post("", status_code=201)
def create_backup(
    payload: BackupCreate,
    request: Request,
    user: User = Depends(CREATE),
    db: Session = Depends(get_db),
) -> dict:
    record = backup_service.create_backup(
        db, name=payload.name, user=user, note=payload.note, kind="manual", is_automatic=False
    )
    return _summary(record)


@router.get("/{backup_id}")
def get_backup(
    backup_id: int,
    include_content: bool = Query(default=False),
    _: User = Depends(READ),
    db: Session = Depends(get_db),
) -> dict:
    record = _backup(db, backup_id)
    data = _summary(record)
    if include_content:
        data["content"] = record.content
    return data


@router.get("/{backup_id}/validate")
def validate_backup(backup_id: int, _: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    record = _backup(db, backup_id)
    errors: list[str] = []
    try:
        backup_service.validate_backup(record.content)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    actual = _checksum(record.content)
    return {
        "id": record.id,
        "valid": not errors and actual == record.checksum,
        "errors": errors,
        "checksum_stored": record.checksum,
        "checksum_actual": actual,
        "counts": _summary(record)["counts"],
    }


@router.get("/{backup_id}/download")
def download_backup(backup_id: int, _: User = Depends(READ), db: Session = Depends(get_db)) -> Response:
    record = _backup(db, backup_id)
    body = json.dumps(record.content, indent=2, sort_keys=True, default=str)
    filename = f"nat64-backup-{record.id}.json"
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{backup_id}/restore", status_code=202)
def restore_backup(
    backup_id: int,
    payload: BackupRestore,
    request: Request,
    user: User = Depends(RESTORE),
    db: Session = Depends(get_db),
) -> dict:
    record = _backup(db, backup_id)
    change = changes.create_change(
        db,
        action_name="backup.restore",
        payload={"backup_id": record.id, "mode": payload.mode},
        user=user,
    )
    return {
        **changes.change_dict(change),
        "note": (
            "Restoring changes control-plane database configuration only. Re-apply Jool pool4, BIND, nftables "
            "and netplan separately through approved changes."
        ),
    }


@router.delete("/{backup_id}")
def delete_backup(
    backup_id: int,
    request: Request,
    user: User = Depends(DELETE),
    db: Session = Depends(get_db),
) -> dict:
    record = _backup(db, backup_id)
    if record.kind == "automatic":
        raise Conflict("Automatic pre-change backups cannot be deleted from the UI")
    write_audit(
        db,
        action="backup.delete",
        user=user,
        request=request,
        resource_type="configuration_backup",
        resource_id=record.id,
        detail={"name": record.name, "checksum": record.checksum},
    )
    if record.file_path:
        try:
            Path(record.file_path).unlink(missing_ok=True)
        except OSError:
            pass
    db.delete(record)
    return {"deleted": True, "id": backup_id}
