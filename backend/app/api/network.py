from __future__ import annotations

import ipaddress
from types import SimpleNamespace

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.core.errors import NotFound, ValidationFailed
from app.core.routing import APIRouter
from app.models import NetworkInterface, NetworkSettings, User
from app.models.enums import InterfaceRole
from app.schemas import NetworkInterfaceUpdate, NetworkSettingsUpdate
from app.services import changes, network
from app.services.audit import write_audit
from app.services.host import host

router = APIRouter(prefix="/network", tags=["network"])

READ = require_permission("read")
WRITE = require_permission("network.write")
REQUEST = require_permission("changes.request")

NETWORK_FIELDS = (
    "wan4_interface",
    "wan6_interface",
    "lan6_interface",
    "client_prefix",
    "nat64_prefix",
    "dns_server",
    "default_gateway4",
    "default_gateway6",
    "mtu",
    "ipv4_forwarding",
    "ipv6_forwarding",
    "manage_routes",
    "manage_mtu",
    "notes",
)


def _record(db: Session) -> NetworkSettings:
    record = db.get(NetworkSettings, 1)
    if not record:
        record = NetworkSettings(id=1, nat64_prefix="64:ff9b::/96")
        db.add(record)
        db.flush()
    return record


def _draft(record: NetworkSettings, data: dict) -> SimpleNamespace:
    return SimpleNamespace(
        **{
            field: data[field] if field in data and data[field] is not None else getattr(record, field)
            for field in NETWORK_FIELDS
        }
    )


def _settings_dict(record: NetworkSettings) -> dict:
    return {field: getattr(record, field) for field in NETWORK_FIELDS} | {
        "id": record.id,
        "config_version": record.config_version,
        "last_render_hash": record.last_render_hash,
        "last_snapshot": record.last_snapshot,
        "pending_confirmation": record.pending_confirmation,
        "last_applied_at": record.last_applied_at,
        "rollout_confirmed_at": record.rollout_confirmed_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _interface_dict(record: NetworkInterface) -> dict:
    return {
        "id": record.id,
        "name": record.name,
        "role": record.role,
        "mac_address": record.mac_address,
        "mtu": record.mtu,
        "speed_mbps": record.speed_mbps,
        "is_up": record.is_up,
        "vlan_id": record.vlan_id,
        "addresses": record.addresses or [],
        "description": record.description,
        "last_seen_at": record.last_seen_at,
    }


def _render(record: NetworkSettings, data: dict) -> dict:
    draft = _draft(record, data)
    try:
        warnings = network.validate_settings(draft)
        netplan = network.render_netplan(draft)
        sysctl = network.render_sysctl(draft)
        digest = network.content_hash(netplan, sysctl)
        errors: list[str] = []
    except Exception as exc:
        warnings = []
        netplan = ""
        sysctl = ""
        digest = None
        errors = [f"{type(exc).__name__}: {exc}"]
    return {
        "netplan_yaml": netplan,
        "sysctl_content": sysctl,
        "hash": digest,
        "warnings": warnings,
        "errors": errors,
        "validated_on_host": False,
        "host_available": host.available,
        "note": (
            "Preview only. netplan generate / sysctl --system / netplan apply run through the privileged helper "
            "during an approved network.apply change request."
        ),
    }


@router.get("")
def get_network(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    record = _record(db)
    interfaces = db.scalars(select(NetworkInterface).order_by(NetworkInterface.name)).all()
    return {
        "settings": _settings_dict(record),
        "preview": _render(record, {}),
        "status": network.status(record),
        "interfaces": [_interface_dict(item) for item in interfaces],
        "host": {"available": host.available, "mode": host.mode, "reason": host.unavailable_reason()},
        "warnings": [
            (
                "Network changes can interrupt management connectivity. Apply creates a change request and, "
                "once approved and executed, starts a rollback timer that must be confirmed."
            ),
            "This manager writes only the managed netplan file, sysctl drop-in and its own snapshots.",
        ],
    }


@router.get("/settings")
def get_settings(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    return _settings_dict(_record(db))


@router.patch("/settings")
def update_settings(
    payload: NetworkSettingsUpdate,
    request: Request,
    user: User = Depends(WRITE),
    db: Session = Depends(get_db),
) -> dict:
    """Database-only metadata edit. Nothing reaches the host until network.apply is approved."""
    record = _record(db)
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise ValidationFailed("No network settings supplied")
    for field in ("wan4_interface", "wan6_interface", "lan6_interface"):
        if field in data:
            network.validate_interface_name(data[field])
    if data.get("default_gateway4"):
        network.validate_ipv4(data["default_gateway4"], "default_gateway4")
    if data.get("default_gateway6"):
        network.validate_ipv6(data["default_gateway6"], "default_gateway6")
    for field, value in data.items():
        if field in NETWORK_FIELDS and hasattr(record, field):
            setattr(record, field, value)
    warnings = network.validate_settings(record)
    write_audit(
        db,
        action="network.update_settings",
        user=user,
        request=request,
        resource_type="network_settings",
        resource_id=record.id,
        detail={"keys": sorted(data)},
    )
    return {
        "settings": _settings_dict(record),
        "warnings": warnings,
        "applied_to_host": False,
        "note": "Stored in the database only. Create and approve a network.apply change to update the host.",
    }


@router.get("/status")
def get_status(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    return network.status(_record(db))


@router.post("/preview")
def preview_network(
    payload: NetworkSettingsUpdate, _: User = Depends(READ), db: Session = Depends(get_db)
) -> dict:
    record = _record(db)
    return _render(record, payload.model_dump(exclude_unset=True))


@router.post("/apply", status_code=202)
def apply_network(
    payload: NetworkSettingsUpdate,
    user: User = Depends(REQUEST),
    db: Session = Depends(get_db),
) -> dict:
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise ValidationFailed("No network changes supplied")
    change = changes.create_change(db, action_name="network.apply", payload=data, user=user)
    return {
        **changes.change_dict(change),
        "note": "Review plan.details.netplan_yaml and plan.details.sysctl_content before approving.",
        "rollback_timer_seconds": None if not change.plan else change.plan.get("details", {}).get("rollback_seconds"),
    }


@router.post("/rollback", status_code=202)
def rollback_network(user: User = Depends(REQUEST), db: Session = Depends(get_db)) -> dict:
    change = changes.create_change(db, action_name="network.rollback", payload={}, user=user)
    return changes.change_dict(change)


@router.post("/confirm")
def confirm_network(
    request: Request, user: User = Depends(WRITE), db: Session = Depends(get_db)
) -> dict:
    """Confirmation is a safety action: it cancels the automatic rollback timer."""
    record = _record(db)
    result = network.confirm(db, record, user=user)
    return result


@router.get("/interfaces")
def list_interfaces(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(NetworkInterface).order_by(NetworkInterface.name)).all()
    return {"items": [_interface_dict(item) for item in rows]}


@router.post("/detect")
def detect_interfaces(
    request: Request, user: User = Depends(WRITE), db: Session = Depends(get_db)
) -> dict:
    result = network.detect_interfaces(db, user=user)
    rows = db.scalars(select(NetworkInterface).order_by(NetworkInterface.name)).all()
    return {**result, "stored": [_interface_dict(item) for item in rows]}


@router.patch("/interfaces/{interface_id}")
def update_interface(
    interface_id: int,
    payload: NetworkInterfaceUpdate,
    request: Request,
    user: User = Depends(WRITE),
    db: Session = Depends(get_db),
) -> dict:
    record = db.get(NetworkInterface, interface_id)
    if not record:
        raise NotFound("Network interface", interface_id)
    data = payload.model_dump(exclude_unset=True)
    if "role" in data:
        allowed = {item.value for item in InterfaceRole}
        if data["role"] not in allowed:
            raise ValidationFailed(f"role must be one of {sorted(allowed)}")
    for field, value in data.items():
        if hasattr(record, field):
            setattr(record, field, value)
    write_audit(
        db,
        action="network.update_interface",
        user=user,
        request=request,
        resource_type="network_interface",
        resource_id=record.id,
        detail={"name": record.name, "keys": sorted(data)},
    )
    return _interface_dict(record)


@router.get("/validate-address")
def validate_address(value: str, family: int = 4, _: User = Depends(READ)) -> dict:
    try:
        parsed = (
            ipaddress.IPv4Address(value)
            if family == 4
            else ipaddress.IPv6Address(value)
        )
    except ValueError as exc:
        return {"valid": False, "family": family, "error": str(exc)}
    return {"valid": True, "family": family, "normalized": str(parsed)}
