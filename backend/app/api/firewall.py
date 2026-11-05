from __future__ import annotations

from fastapi import Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.core.errors import NotFound, ValidationFailed
from app.core.routing import APIRouter
from app.models import FirewallRule, FirewallSettings, User
from app.schemas import FirewallRuleCreate, FirewallRuleUpdate, FirewallSettingsUpdate
from app.services import changes, firewall
from app.services.audit import write_audit

router = APIRouter(prefix="/firewall", tags=["firewall"])

READ = require_permission("read")
REQUEST = require_permission("changes.request")
WRITE = require_permission("firewall.write")

SETTINGS_FIELDS = (
    "enabled",
    "default_forward_policy",
    "default_input_policy",
    "new_connection_rate",
    "rate_burst",
    "log_dropped",
    "management_allowlist",
    "management_ports",
    "protect_control_plane",
    "syn_flood_protection",
    "invalid_packet_protection",
    "mss_clamping",
)


def _settings(db: Session) -> FirewallSettings:
    record = db.get(FirewallSettings, 1)
    if not record:
        record = FirewallSettings(id=1, management_ports=[22, 443])
        db.add(record)
        db.flush()
    return record


def _settings_dict(record: FirewallSettings) -> dict:
    return {key: getattr(record, key) for key in SETTINGS_FIELDS} | {
        "id": record.id,
        "config_version": record.config_version,
        "last_render_hash": record.last_render_hash,
        "applied_at": record.applied_at,
        "last_error": record.last_error,
    }


def _rule_dict(rule: FirewallRule) -> dict:
    return {
        "id": rule.id,
        "name": rule.name,
        "description": rule.description,
        "enabled": rule.enabled,
        "priority": rule.priority,
        "family": rule.family,
        "direction": rule.direction,
        "action": rule.action,
        "protocol": rule.protocol,
        "source_cidr": rule.source_cidr,
        "destination_cidr": rule.destination_cidr,
        "source_port": rule.source_port,
        "destination_port": rule.destination_port,
        "rate_limit_per_second": rule.rate_limit_per_second,
        "log": rule.log,
        "counter_packets": rule.counter_packets,
        "counter_bytes": rule.counter_bytes,
        "last_applied_at": rule.last_applied_at,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


def _validate(rule: FirewallRule) -> list[str]:
    return firewall.validate_rule(rule)


@router.get("")
def get_firewall(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    record = _settings(db)
    rules = db.scalars(select(FirewallRule).order_by(FirewallRule.priority, FirewallRule.id)).all()
    return {
        "settings": _settings_dict(record),
        "rules": [_rule_dict(rule) for rule in rules],
        "status": firewall.status(db, record),
        "scope": {
            "managed_table": f"inet {firewall.TABLE}",
            "note": "Only the managed nat64mgr table is replaced. Jool's own netfilter rules and unrelated tables are untouched.",
        },
        "warnings": [
            "Rule edits are stored in the database only. nftables changes take effect after an approved firewall.apply change request.",
            "Enabling the firewall without a management allowlist can lock you out of SSH.",
        ],
    }


@router.get("/settings")
def get_settings(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    return _settings_dict(_settings(db))


@router.get("/status")
def get_status(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    return firewall.status(db, _settings(db))


@router.post("/apply", status_code=202)
def apply_firewall(
    payload: FirewallSettingsUpdate,
    user: User = Depends(REQUEST),
    db: Session = Depends(get_db),
) -> dict:
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise ValidationFailed("No firewall changes supplied")
    if data.get("management_allowlist") is not None:
        normalized: list[str] = []
        for cidr in data["management_allowlist"]:
            value = firewall.validate_cidr(cidr)
            if value and value not in normalized:
                normalized.append(value)
        data["management_allowlist"] = normalized
    if data.get("management_ports") is not None:
        for port in data["management_ports"]:
            if not 1 <= int(port) <= 65535:
                raise ValidationFailed(f"Invalid management port: {port}")
        data["management_ports"] = sorted({int(port) for port in data["management_ports"]})
    change = changes.create_change(db, action_name="firewall.apply", payload=data, user=user)
    return {
        **changes.change_dict(change),
        "note": "Review the rendered nftables ruleset in plan.details.content before approving.",
    }


@router.get("/preview")
def preview_firewall(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    record = _settings(db)
    try:
        content = firewall.render_ruleset(db, record)
        error = None
    except Exception as exc:
        content = ""
        error = f"{type(exc).__name__}: {exc}"
    return {
        "enabled": record.enabled,
        "content": content,
        "hash": firewall.content_hash(content) if content else None,
        "error": error,
        "note": "Preview only; nft -c and apply run through the privileged helper during an approved change.",
    }


# ---------------------------------------------------------------------------
# firewall rules (database-only until apply)
# ---------------------------------------------------------------------------


@router.get("/rules")
def list_rules(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(FirewallRule).order_by(FirewallRule.priority, FirewallRule.id)).all()
    return {"items": [_rule_dict(rule) for rule in rows]}


@router.post("/rules", status_code=201)
def create_rule(
    payload: FirewallRuleCreate,
    request: Request,
    user: User = Depends(WRITE),
    db: Session = Depends(get_db),
) -> dict:
    rule = FirewallRule(**payload.model_dump())
    warnings = _validate(rule)
    db.add(rule)
    db.flush()
    write_audit(
        db,
        action="firewall_rule.create",
        user=user,
        request=request,
        resource_type="firewall_rule",
        resource_id=rule.id,
        detail={"name": rule.name, "direction": rule.direction, "action": rule.action},
    )
    return {**_rule_dict(rule), "warnings": warnings}


@router.get("/rules/{rule_id}")
def get_rule(rule_id: int, _: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    rule = db.get(FirewallRule, rule_id)
    if not rule:
        raise NotFound("Firewall rule", rule_id)
    return _rule_dict(rule)


@router.patch("/rules/{rule_id}")
def update_rule(
    rule_id: int,
    payload: FirewallRuleUpdate,
    request: Request,
    user: User = Depends(WRITE),
    db: Session = Depends(get_db),
) -> dict:
    rule = db.get(FirewallRule, rule_id)
    if not rule:
        raise NotFound("Firewall rule", rule_id)
    changes_in = payload.model_dump(exclude_unset=True)
    for key, value in changes_in.items():
        if hasattr(rule, key):
            setattr(rule, key, value)
    warnings = _validate(rule)
    write_audit(
        db,
        action="firewall_rule.update",
        user=user,
        request=request,
        resource_type="firewall_rule",
        resource_id=rule.id,
        detail={"keys": sorted(changes_in)},
    )
    return {**_rule_dict(rule), "warnings": warnings}


@router.delete("/rules/{rule_id}")
def delete_rule(
    rule_id: int,
    request: Request,
    user: User = Depends(WRITE),
    db: Session = Depends(get_db),
) -> dict:
    rule = db.get(FirewallRule, rule_id)
    if not rule:
        raise NotFound("Firewall rule", rule_id)
    write_audit(
        db,
        action="firewall_rule.delete",
        user=user,
        request=request,
        resource_type="firewall_rule",
        resource_id=rule.id,
        detail={"name": rule.name},
    )
    db.delete(rule)
    return {"deleted": True, "id": rule_id, "firewall_note": "Apply the firewall to update nftables."}


@router.post("/rules/{rule_id}/enabled")
def toggle_rule(
    rule_id: int,
    request: Request,
    enabled: bool = Query(default=True),
    user: User = Depends(WRITE),
    db: Session = Depends(get_db),
) -> dict:
    rule = db.get(FirewallRule, rule_id)
    if not rule:
        raise NotFound("Firewall rule", rule_id)
    rule.enabled = enabled
    write_audit(
        db,
        action="firewall_rule.toggle",
        user=user,
        request=request,
        resource_type="firewall_rule",
        resource_id=rule.id,
        detail={"enabled": enabled},
    )
    return _rule_dict(rule)
