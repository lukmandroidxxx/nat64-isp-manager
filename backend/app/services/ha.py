from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.core.security import decrypt_secret, encrypt_secret
from app.models import HaNode, HaSettings, User
from app.services import backup as backup_service
from app.services.audit import write_audit

log = logging.getLogger(__name__)

INTERFACE_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,64}$")
NODE_STATE = {"MASTER", "BACKUP", "FAULT"}


def _now() -> datetime:
    return datetime.now(UTC)


def get_settings(db: Session) -> HaSettings:
    record = db.get(HaSettings, 1)
    if record:
        return record
    try:
        # SAVEPOINT so a concurrent bootstrap/worker insert does not poison the
        # surrounding transaction.
        with db.begin_nested():
            record = HaSettings(id=1, enabled=settings.ha_enabled, priority=100)
            db.add(record)
            db.flush()
        return record
    except IntegrityError:
        existing = db.scalars(
            select(HaSettings)
            .where(HaSettings.id == 1)
            .execution_options(populate_existing=True)
        ).first()
        if existing is None:
            raise
        return existing


def settings_dict(db: Session, record: HaSettings) -> dict:
    return {
        "id": record.id,
        "enabled": record.enabled,
        "mode": record.mode,
        "virtual_ip": record.virtual_ip,
        "vrrp_interface": record.vrrp_interface,
        "vrrp_router_id": record.vrrp_router_id,
        "priority": record.priority,
        "peer_address": record.peer_address,
        "session_sync_enabled": record.session_sync_enabled,
        "session_sync_multicast": record.session_sync_multicast,
        "session_sync_interface": record.session_sync_interface,
        "last_failover_at": record.last_failover_at,
        "last_error": record.last_error,
        "auth_secret_set": bool(record.auth_secret_encrypted),
        "node_name": settings.ha_node_name,
        "role": settings.ha_role,
        "heartbeat_seconds": settings.ha_heartbeat_seconds,
        "warnings": [
            "Failover and NAT64 session continuity are experimental and not verified by automated tests.",
        ],
    }


def update_settings(db: Session, record: HaSettings, payload: dict, *, user: User | None = None) -> HaSettings:
    allowed = {
        "enabled",
        "mode",
        "virtual_ip",
        "vrrp_interface",
        "vrrp_router_id",
        "priority",
        "peer_address",
        "session_sync_enabled",
        "session_sync_multicast",
        "session_sync_interface",
    }
    for key, value in payload.items():
        if key == "auth_secret" and value:
            record.auth_secret_encrypted = encrypt_secret(str(value))
        elif key in allowed:
            setattr(record, key, value)
    if record.virtual_ip:
        try:
            ipaddress.ip_address(record.virtual_ip)
        except ValueError as exc:
            raise ValidationFailed("virtual_ip must be an IPv4 or IPv6 address") from exc
    if record.vrrp_interface and not INTERFACE_RE.fullmatch(record.vrrp_interface):
        raise ValidationFailed("Invalid VRRP interface name")
    if record.vrrp_router_id is not None and not (1 <= int(record.vrrp_router_id) <= 255):
        raise ValidationFailed("VRRP router id must be between 1 and 255")
    if record.priority is not None and not (1 <= int(record.priority) <= 254):
        raise ValidationFailed("VRRP priority must be between 1 and 254")
    write_audit(
        db,
        action="ha.update_settings",
        user=user,
        resource_type="ha_settings",
        resource_id=record.id,
        detail={"keys": sorted(payload), "enabled": record.enabled},
    )
    db.flush()
    return record


def render_keepalived(db: Session, *, redact: bool = True) -> dict:
    record = get_settings(db)
    warnings = [
        "This configuration is rendered from database values; it is not tested by the automated suite.",
        "NAT64 session continuity across failover is experimental (Jool SS) and not guaranteed.",
    ]
    errors: list[str] = []
    if not record.enabled:
        errors.append("HA is disabled in HA settings")
    if not record.virtual_ip:
        errors.append("virtual_ip is required")
    if not record.vrrp_interface:
        errors.append("vrrp_interface is required")
    secret = decrypt_secret(record.auth_secret_encrypted)
    if not secret:
        errors.append("A VRRP authentication secret is required")
    if record.session_sync_enabled and not record.session_sync_interface:
        warnings.append("Session synchronization is enabled but no sync interface is configured")
    if errors:
        raise ValidationFailed("Cannot render keepalived configuration", errors)
    state = "MASTER" if settings.ha_role == "primary" else "BACKUP"
    node_id = re.sub(r"[^A-Za-z0-9_.-]", "-", settings.ha_node_name)[:64] or "gateway-1"
    lines = [
        "# Managed by NAT64 ISP Manager. Do not edit manually.",
        "global_defs {",
        f"    router_id {node_id}",
        "    enable_script_security",
        "}",
        "",
        "vrrp_instance NAT64_GATEWAY {",
        f"    state {state}",
        f"    interface {record.vrrp_interface}",
        f"    virtual_router_id {record.vrrp_router_id}",
        f"    priority {record.priority}",
        f"    advert_int {max(1, settings.ha_heartbeat_seconds)}",
        "    authentication {",
        "        auth_type PASS",
        f"        auth_pass {secret}",
        "    }",
        "    virtual_ipaddress {",
        f"        {record.virtual_ip}",
        "    }",
        "}",
        "",
    ]
    content = "\n".join(lines)
    digest = hashlib.sha256(content.encode()).hexdigest()
    # Never return the VRRP shared secret to a browser. Preview and change-plan
    # responses show the rendered file with the credential masked; execution
    # re-renders with redact=False immediately before handing it to the helper.
    display_content = content.replace(f"auth_pass {secret}", "auth_pass ********") if redact else content
    return {
        "content": display_content,
        "hash": digest,
        "settings": settings_dict(db, record),
        "warnings": warnings,
        "state": state,
        "redacted": redact,
    }


def list_nodes(db: Session) -> list[dict]:
    rows = db.scalars(select(HaNode).order_by(HaNode.name)).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "role": row.role,
            "address": row.address,
            "api_url": row.api_url,
            "enabled": row.enabled,
            "status": row.status,
            "last_seen_at": row.last_seen_at,
            "config_version": row.config_version,
            "config_hash": row.config_hash,
            "jool_version": row.jool_version,
            "notes": row.notes,
        }
        for row in rows
    ]


def create_node(db: Session, payload: dict, user: User | None = None) -> HaNode:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValidationFailed("Node name is required")
    if db.scalar(select(HaNode).where(HaNode.name == name)):
        raise Conflict("HA node already exists")
    node = HaNode(
        name=name,
        role=payload.get("role", "backup"),
        address=payload.get("address"),
        api_url=payload.get("api_url"),
        enabled=bool(payload.get("enabled", True)),
        notes=payload.get("notes"),
    )
    db.add(node)
    db.flush()
    write_audit(db, action="ha.node.create", user=user, resource_type="ha_node", resource_id=node.id, detail={"name": name})
    return node


def update_node(db: Session, node_id: int, payload: dict, user: User | None = None) -> HaNode:
    node = db.get(HaNode, node_id)
    if not node:
        raise NotFound("HA node", node_id)
    for key in ("name", "role", "address", "api_url", "enabled", "notes"):
        if key in payload and payload[key] is not None:
            setattr(node, key, payload[key])
    write_audit(db, action="ha.node.update", user=user, resource_type="ha_node", resource_id=node.id, detail={"keys": sorted(payload)})
    db.flush()
    return node


def delete_node(db: Session, node_id: int, user: User | None = None) -> None:
    node = db.get(HaNode, node_id)
    if not node:
        raise NotFound("HA node", node_id)
    write_audit(db, action="ha.node.delete", user=user, resource_type="ha_node", resource_id=node.id, detail={"name": node.name})
    db.delete(node)


def sync_export(db: Session) -> dict:
    content = backup_service.serialize_config(db)
    raw = repr(sorted(content.items())).encode()
    return {
        "app": "nat64-isp-manager",
        "schema": backup_service.BACKUP_SCHEMA,
        "exported_at": _now(),
        "config_hash": hashlib.sha256(raw).hexdigest(),
        "config": content,
        "excludes": ["users", "roles", "auth_sessions", "audit_logs", "secrets"],
        "warning": "Non-secret configuration only. Host state is not synchronized by this endpoint.",
    }


def sync_import(db: Session, payload: dict, user: User | None = None) -> dict:
    config = payload.get("config") if isinstance(payload, dict) else None
    if not isinstance(config, dict):
        raise ValidationFailed("HA sync payload must contain a config object")
    backup_service.validate_backup(config)
    from app.models import ConfigurationBackup

    backup = ConfigurationBackup(
        name=f"ha-sync-{_now().strftime('%Y%m%dT%H%M%SZ')}",
        kind="ha_sync",
        content=config,
        checksum=str(payload.get("config_hash") or ""),
        size_bytes=len(repr(config).encode()),
        note="Imported through HA configuration sync",
        created_by=user.username if user else "ha-sync",
        is_automatic=True,
    )
    db.add(backup)
    db.flush()
    result = backup_service.restore_backup(db, backup, user=user, mode="merge")
    write_audit(db, action="ha.sync_import", user=user, resource_type="ha_sync", detail={"backup_id": backup.id})
    return {**result, "config_hash": payload.get("config_hash")}


async def check_nodes(db: Session, *, user: User | None = None) -> list[dict]:
    updated = []
    async with httpx.AsyncClient(timeout=4.0) as client:
        for node in db.scalars(select(HaNode)).all():
            if not node.enabled or not node.api_url:
                node.status = "disabled" if not node.enabled else "unconfigured"
                updated.append({"id": node.id, "name": node.name, "status": node.status})
                continue
            url = node.api_url.rstrip("/") + "/health"
            try:
                response = await client.get(url)
                if response.status_code == 200:
                    data = response.json()
                    node.status = "online"
                    node.last_seen_at = _now()
                    node.jool_version = (data.get("services") or {}).get("jool", {}).get("version") if isinstance(data, dict) else None
                    node.config_version = int(data.get("config_version") or node.config_version or 0)
                else:
                    node.status = f"http_{response.status_code}"
            except Exception as exc:
                node.status = "unreachable"
                log.info("HA node %s unreachable: %s", node.name, exc)
            updated.append({"id": node.id, "name": node.name, "status": node.status, "last_seen_at": node.last_seen_at})
    db.flush()
    return updated


def require_sync_token(token: str | None) -> None:
    if not settings.ha_sync_token:
        raise Conflict("HA_SYNC_TOKEN is not configured on this node")
    if not token or token != settings.ha_sync_token:
        raise PermissionDenied("Invalid HA sync token")


def status(db: Session) -> dict:
    record = get_settings(db)
    return {
        "enabled": record.enabled,
        "role": settings.ha_role,
        "node_name": settings.ha_node_name,
        "virtual_ip": record.virtual_ip,
        "mode": record.mode,
        "nodes": list_nodes(db),
        "settings": settings_dict(db, record),
        "continuity": {
            "session_sync_setting": record.session_sync_enabled,
            "verified": False,
            "note": "NAT64 session survival after failover is experimental and not verified by this build.",
        },
    }
