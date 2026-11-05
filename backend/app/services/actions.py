from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import NotFound, ValidationFailed
from app.models import (
    ConfigurationBackup,
    Dns64Settings,
    FirewallSettings,
    JoolInstance,
    Nat64Session,
    NetworkSettings,
    User,
)
from app.models.enums import JoolStatus
from app.services import backup as backup_service
from app.services import dns64, firewall, ipv4, network
from app.services.audit import write_audit
from app.services.host import host
from app.services.jool import JoolError, jool

INSTANCE_RE = re.compile(r"^[A-Za-z0-9_-]{1,15}$")
DURATION_RE = re.compile(r"^\d+(?::\d{1,2}){0,2}(?:\.\d{1,3})?$|^\d+(?:\.\d{1,3})?(?:ms|s|m|h)?$")
GLOBAL_SETTINGS = {
    "manually-enabled": "bool",
    "address-dependent-filtering": "bool",
    "drop-icmpv6-info": "bool",
    "drop-externally-initiated-tcp": "bool",
    "logging-bib": "bool",
    "logging-session": "bool",
    "source-icmpv6-errors-better": "bool",
    "handle-rst-during-fin-rcv": "bool",
    "ss-enabled": "bool",
    "udp-timeout": "duration",
    "tcp-est-timeout": "duration",
    "tcp-trans-timeout": "duration",
    "icmp-timeout": "duration",
    "lowest-ipv6-mtu": "int",
    "maximum-simultaneous-opens": "int",
    "f-args": "int",
    "ss-flush-deadline": "int",
    "ss-capacity": "int",
    "ss-max-sessions-per-packet": "int",
    "mtu-plateaus": "string",
}


@dataclass
class Plan:
    action: str
    summary: str
    risk: str = "medium"
    target: str | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    commands: list[list[str]] = field(default_factory=list)
    details: dict = field(default_factory=dict)
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    reversible: bool = True
    requires_maintenance: bool = False
    host_available: bool | None = None

    @property
    def valid(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "summary": self.summary,
            "risk": self.risk,
            "target": self.target,
            "warnings": self.warnings,
            "errors": self.errors,
            "commands": self.commands,
            "details": self.details,
            "before": self.before,
            "after": self.after,
            "reversible": self.reversible,
            "requires_maintenance": self.requires_maintenance,
            "host_available": self.host_available,
            "valid": self.valid,
        }


@dataclass
class Action:
    name: str
    title: str
    handler: Callable[[Session, dict], Plan]
    executor: Callable[[Session, dict, Plan, User | None], dict]
    permission: str = "operator"


def _param(payload: dict, key: str) -> Any:
    value = payload.get(key)
    if value is None or value == "":
        raise ValidationFailed(f"Missing parameter: {key}")
    return value


def _instance(db: Session, payload: dict) -> JoolInstance:
    instance_id = payload.get("instance_id")
    name = payload.get("instance") or payload.get("name")
    record = None
    if instance_id is not None:
        record = db.get(JoolInstance, int(instance_id))
    elif name:
        record = db.scalar(select(JoolInstance).where(JoolInstance.name == str(name)))
    if not record:
        raise NotFound("Jool instance", instance_id or name)
    return record


def _host_errors(plan: Plan) -> None:
    plan.host_available = host.available
    if not host.available:
        plan.errors.append(host.unavailable_reason() or "Host integration unavailable")


def _jool_ready(plan: Plan) -> bool:
    if not host.available:
        plan.errors.append(host.unavailable_reason() or "Host integration unavailable")
        return False
    try:
        info = jool.status()
    except Exception as exc:
        plan.errors.append(f"Could not determine Jool status: {exc}")
        return False
    if not info.compatible:
        plan.errors.append(info.message or "Jool 4.x is required")
        return False
    return True


def _transient(base, payload: dict, allowed: set[str]):
    data = {column: getattr(base, column) for column in allowed if hasattr(base, column)}
    for key, value in payload.items():
        if key in allowed:
            data[key] = value
    return SimpleNamespace(**data)


def _named_instance(db: Session, name: str) -> JoolInstance:
    record = db.scalar(select(JoolInstance).where(JoolInstance.name == name))
    if not record:
        raise NotFound("Jool instance", name)
    return record


# ---------------------------------------------------------------------------
# Jool instance actions
# ---------------------------------------------------------------------------


def plan_create_instance(db: Session, payload: dict) -> Plan:
    name = str(_param(payload, "name"))
    plan = Plan(action="jool.create_instance", summary=f"Create Jool NAT64 instance {name}", target=name)
    if not INSTANCE_RE.fullmatch(name):
        plan.errors.append("Instance name must match [A-Za-z0-9_-]{1,15}")
    try:
        pool6 = str(ipaddress.IPv6Network(str(_param(payload, "pool6")), strict=True))
        if ipaddress.IPv6Network(pool6).prefixlen not in {32, 40, 48, 56, 64, 96}:
            plan.errors.append("pool6 must be /32, /40, /48, /56, /64 or /96")
    except ValueError as exc:
        pool6 = str(payload.get("pool6") or "")
        plan.errors.append(f"Invalid pool6 prefix: {exc}")
    framework = str(payload.get("framework", "netfilter"))
    if framework != "netfilter":
        plan.errors.append("Only the netfilter framework is supported")
    if db.scalar(select(JoolInstance).where(JoolInstance.name == name)):
        plan.errors.append(f"Instance {name!r} already exists in the database")
    plan.after = {"name": name, "pool6": pool6, "framework": framework}
    plan.commands = [["jool", "instance", "add", name, "--netfilter", "--pool6", pool6]]
    plan.details["note"] = "pool6 is immutable after creation; changing it requires recreation and drops sessions."
    if not _jool_ready(plan):
        return plan
    try:
        live = jool.list_instances()
        if any(item.name == name for item in live):
            plan.errors.append(f"Instance {name!r} already exists in Jool")
        else:
            plan.details["observed_instances"] = [item.name for item in live]
    except JoolError as exc:
        plan.errors.append(f"Could not list Jool instances: {exc.message}")
    return plan


def execute_create_instance(db: Session, payload: dict, plan: Plan, user: User | None) -> dict:
    name = payload["name"]
    pool6 = str(ipaddress.IPv6Network(str(payload["pool6"]), strict=True))
    commands = jool.add_instance(name, pool6, "netfilter")
    record = JoolInstance(
        name=name,
        pool6=pool6,
        framework="netfilter",
        display_name=payload.get("display_name"),
        enabled=True,
        status=JoolStatus.ONLINE.value,
    )
    db.add(record)
    db.flush()
    write_audit(
        db,
        action="jool.instance.create",
        user=user,
        resource_type="jool_instance",
        resource_id=record.id,
        detail={"name": name, "pool6": pool6},
    )
    return {"commands": commands, "instance_id": record.id, "note": "Add pool4 entries through an approved pool synchronization."}


def plan_remove_instance(db: Session, payload: dict) -> Plan:
    record = _instance(db, payload)
    plan = Plan(
        action="jool.remove_instance",
        summary=f"Remove Jool instance {record.name}",
        target=record.name,
        risk="high",
        requires_maintenance=True,
        reversible=False,
    )
    plan.before = {"name": record.name, "pool6": record.pool6, "status": record.status}
    active = db.scalar(
        select(Nat64Session.id).where(Nat64Session.instance_id == record.id, Nat64Session.status == "active")
    )
    plan.details["active_sessions"] = int(
        db.scalar(
            select(Nat64Session.id)
            .where(Nat64Session.instance_id == record.id, Nat64Session.status == "active")
            .limit(1)
        )
        is not None
    )
    if active:
        plan.warnings.append("Removing the instance drops all active NAT64 sessions and BIB state for this instance.")
    plan.warnings.append("The database record is retained but marked disabled; pool4 entries are not deleted.")
    plan.commands = [["jool", "instance", "remove", record.name]]
    _jool_ready(plan)
    return plan


def execute_remove_instance(db: Session, payload: dict, plan: Plan, user: User | None) -> dict:
    record = _instance(db, payload)
    commands = jool.remove_instance(record.name)
    record.enabled = False
    record.status = JoolStatus.OFFLINE.value
    record.paused = False
    record.last_error = "Instance removed through an approved change request"
    now = _now()
    for row in db.scalars(
        select(Nat64Session).where(Nat64Session.instance_id == record.id, Nat64Session.status == "active")
    ).all():
        row.status = "expired"
        row.expired_at = now
    write_audit(
        db,
        action="jool.instance.remove",
        user=user,
        resource_type="jool_instance",
        resource_id=record.id,
        detail={"name": record.name},
    )
    return {"commands": commands, "removed": True}


def plan_recreate_instance(db: Session, payload: dict) -> Plan:
    record = _instance(db, payload)
    plan = Plan(
        action="jool.recreate_instance",
        summary=f"Recreate Jool instance {record.name} with a new pool6",
        target=record.name,
        risk="critical",
        requires_maintenance=True,
        reversible=True,
    )
    try:
        pool6 = str(ipaddress.IPv6Network(str(_param(payload, "pool6")), strict=True))
        if ipaddress.IPv6Network(pool6).prefixlen not in {32, 40, 48, 56, 64, 96}:
            plan.errors.append("pool6 must be /32, /40, /48, /56, /64 or /96")
    except ValueError as exc:
        pool6 = str(payload.get("pool6") or "")
        plan.errors.append(f"Invalid pool6 prefix: {exc}")
    if pool6 == record.pool6:
        plan.errors.append("The new pool6 is identical to the current pool6")
    plan.before = {"name": record.name, "pool6": record.pool6}
    plan.after = {"name": record.name, "pool6": pool6}
    plan.warnings.append(
        "Jool cannot change pool6 on a live instance. The instance is removed and recreated; all sessions/BIB "
        "entries are dropped and all pool4 entries must be synchronized again."
    )
    plan.details["recovery"] = "If re-adding the instance fails, the executor attempts to restore the previous pool6."
    plan.commands = [
        ["jool", "instance", "remove", record.name],
        ["jool", "instance", "add", record.name, "--netfilter", "--pool6", pool6],
    ]
    if not _jool_ready(plan):
        return plan
    try:
        live = jool.list_instances()
        if not any(item.name == record.name for item in live):
            plan.errors.append(f"Instance {record.name!r} is not present in Jool")
    except JoolError as exc:
        plan.errors.append(f"Could not list Jool instances: {exc.message}")
    return plan


def execute_recreate_instance(db: Session, payload: dict, plan: Plan, user: User | None) -> dict:
    record = _instance(db, payload)
    pool6 = str(ipaddress.IPv6Network(str(payload["pool6"]), strict=True))
    old_pool6 = record.pool6
    commands = jool.remove_instance(record.name)
    try:
        commands.extend(jool.add_instance(record.name, pool6, "netfilter"))
    except JoolError as exc:
        rollback_note = "Automatic restore of the previous instance was attempted."
        try:
            commands.extend(jool.add_instance(record.name, old_pool6, "netfilter"))
        except JoolError as rollback_exc:
            rollback_note = f"Automatic restore FAILED: {rollback_exc.message}"
        raise ValidationFailed(
            f"Recreating instance failed: {exc.message}. {rollback_note}",
            {"commands": commands},
        ) from exc
    record.pool6 = pool6
    record.status = JoolStatus.ONLINE.value
    record.enabled = True
    now = _now()
    for row in db.scalars(
        select(Nat64Session).where(Nat64Session.instance_id == record.id, Nat64Session.status == "active")
    ).all():
        row.status = "expired"
        row.expired_at = now
    write_audit(
        db,
        action="jool.instance.recreate",
        user=user,
        resource_type="jool_instance",
        resource_id=record.id,
        detail={"old_pool6": old_pool6, "new_pool6": pool6},
    )
    return {
        "commands": commands,
        "previous_pool6": old_pool6,
        "pool6": pool6,
        "note": "Sessions were dropped. Re-synchronize pool4 before restoring service.",
    }


def _plan_pause_resume(db: Session, payload: dict, resume: bool) -> Plan:
    record = _instance(db, payload)
    verb = "Resume" if resume else "Pause"
    plan = Plan(
        action="jool.resume" if resume else "jool.pause",
        summary=f"{verb} NAT64 translation for {record.name}",
        target=record.name,
        risk="medium" if resume else "high",
    )
    plan.before = {"paused": record.paused, "status": record.status}
    plan.after = {"paused": not resume}
    if not resume:
        plan.warnings.append("Pausing translation stops new NAT64 flows. Existing sessions may expire.")
    plan.commands = [
        ["jool", "-i", record.name, "global", "update", "manually-enabled", "true" if resume else "false"]
    ]
    _jool_ready(plan)
    return plan


def _execute_pause_resume(db: Session, payload: dict, plan: Plan, user: User | None, resume: bool) -> dict:
    record = _instance(db, payload)
    commands = jool.resume(record.name) if resume else jool.pause(record.name)
    record.paused = not resume
    record.status = JoolStatus.ONLINE.value if resume else JoolStatus.PAUSED.value
    write_audit(
        db,
        action="jool.resume" if resume else "jool.pause",
        user=user,
        resource_type="jool_instance",
        resource_id=record.id,
        detail={"paused": record.paused},
    )
    return {"commands": commands, "paused": record.paused}


def plan_pause(db: Session, payload: dict) -> Plan:
    return _plan_pause_resume(db, payload, False)


def plan_resume(db: Session, payload: dict) -> Plan:
    return _plan_pause_resume(db, payload, True)


def execute_pause(db: Session, payload: dict, plan: Plan, user: User | None) -> dict:
    return _execute_pause_resume(db, payload, plan, user, False)


def execute_resume(db: Session, payload: dict, plan: Plan, user: User | None) -> dict:
    return _execute_pause_resume(db, payload, plan, user, True)


def _validate_global_value(key: str, value: Any) -> list[str]:
    errors: list[str] = []
    kind = GLOBAL_SETTINGS.get(key)
    if not kind:
        return [f"Setting is not allowed: {key}"]
    text = str(value)
    if kind == "bool" and text.lower() not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
        errors.append(f"{key} requires a boolean value")
    elif kind == "int":
        try:
            int(text)
        except ValueError:
            errors.append(f"{key} requires an integer value")
    elif kind == "duration" and not DURATION_RE.fullmatch(text):
        errors.append(f"{key} requires a duration such as 5m, 300s or 00:05:00")
    elif kind == "string" and (len(text) > 512 or "\n" in text):
        errors.append(f"{key} is invalid")
    return errors


def plan_update_global(db: Session, payload: dict) -> Plan:
    record = _instance(db, payload)
    key = str(_param(payload, "key"))
    value = _param(payload, "value")
    plan = Plan(
        action="jool.update_global",
        summary=f"Update Jool global setting {key}={value} on {record.name}",
        target=record.name,
        risk="medium",
        details={"key": key, "value": str(value)},
    )
    plan.errors.extend(_validate_global_value(key, value))
    if key in {"manually-enabled", "ss-enabled"}:
        plan.warnings.append(
            "Session synchronization (ss-*) is experimental and its failover continuity is not verified by this build."
        )
    plan.commands = [["jool", "-i", record.name, "global", "update", key, str(value)]]
    _jool_ready(plan)
    return plan


def execute_update_global(db: Session, payload: dict, plan: Plan, user: User | None) -> dict:
    record = _instance(db, payload)
    key = str(payload["key"])
    value = str(payload["value"])
    commands = jool.update_global(record.name, key, value)
    if key == "manually-enabled":
        record.paused = value.lower() in {"false", "0", "no", "off"}
    write_audit(
        db,
        action="jool.update_global",
        user=user,
        resource_type="jool_instance",
        resource_id=record.id,
        detail={"key": key, "value": value},
    )
    return {"commands": commands, "key": key, "value": value}


# ---------------------------------------------------------------------------
# pool4
# ---------------------------------------------------------------------------


def plan_pool4_sync(db: Session, payload: dict) -> Plan:
    record = _instance(db, payload)
    plan = Plan(
        action="pool4.sync",
        summary=f"Synchronize Jool pool4 for {record.name} with the database",
        target=record.name,
        details={},
    )
    _jool_ready(plan)
    if plan.errors:
        return plan
    try:
        sync = ipv4.build_sync_plan(db, record)
    except Exception as exc:
        plan.errors.append(f"Could not build pool4 plan: {exc}")
        return plan
    plan.details = sync.as_dict()
    plan.warnings.extend(sync.warnings)
    plan.errors.extend(sync.errors)
    plan.host_available = sync.host_available
    plan.after = {
        "adds": len(sync.adds),
        "removes": len(sync.removes),
        "unmanaged_kept": len(sync.unmanaged),
    }
    if sync.sessions_affected:
        plan.risk = "high"
        plan.requires_maintenance = True
    return plan


def execute_pool4_sync(db: Session, payload: dict, plan: Plan, user: User | None) -> dict:
    record = _instance(db, payload)
    result = ipv4.apply_sync_plan(db, record, user=user)
    if result["errors"]:
        raise ValidationFailed("pool4 synchronization completed with errors", result)
    return result


# ---------------------------------------------------------------------------
# DNS64, firewall, network
# ---------------------------------------------------------------------------

DNS_FIELDS = {
    "enabled",
    "prefix",
    "upstreams",
    "listen_address",
    "service_name",
    "managed_config_path",
    "test_domain",
    "exclude_prefixes",
    "forward_only",
    "dnssec_validation",
}
FIREWALL_FIELDS = {
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
}
NETWORK_FIELDS = {
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
}


def _singleton(db: Session, model, factory):
    record = db.get(model, 1)
    if not record:
        record = factory()
        db.add(record)
        db.flush()
    return record


def plan_dns64_apply(db: Session, payload: dict) -> Plan:
    base = _singleton(db, Dns64Settings, lambda: Dns64Settings(id=1, upstreams=list(settings.dns_default_upstreams)))
    draft = _transient(base, payload, DNS_FIELDS)
    plan = Plan(
        action="dns64.apply",
        summary="Render and apply the managed BIND DNS64 configuration",
        target=base.managed_config_path,
        details={"settings": {key: getattr(draft, key) for key in sorted(DNS_FIELDS)}},
    )
    plan.before = {key: getattr(base, key) for key in sorted(DNS_FIELDS)}
    plan.after = plan.details["settings"]
    plan.commands = [["named-checkconf", "<temporary options wrapper>"], ["rndc", "reconfig"]]
    try:
        rendered = dns64.build_plan(db, draft)
    except Exception as exc:
        plan.errors.append(f"DNS64 validation failed: {exc}")
        return plan
    plan.details.update(
        {
            "content": rendered.content,
            "hash": rendered.hash,
            "validations": rendered.validations,
        }
    )
    plan.warnings.extend(rendered.warnings)
    plan.errors.extend(rendered.errors)
    plan.host_available = rendered.host_available
    if rendered.hash == base.last_render_hash:
        plan.warnings.append("Rendered configuration is identical to the last applied hash")
    return plan


def execute_dns64_apply(db: Session, payload: dict, plan: Plan, user: User | None) -> dict:
    base = _singleton(db, Dns64Settings, lambda: Dns64Settings(id=1, upstreams=list(settings.dns_default_upstreams)))
    for key, value in payload.items():
        if key in DNS_FIELDS:
            setattr(base, key, value)
    result = dns64.apply_plan(db, base, user=user)
    return result


def plan_firewall_apply(db: Session, payload: dict) -> Plan:
    base = _singleton(db, FirewallSettings, lambda: FirewallSettings(id=1))
    draft = _transient(base, payload, FIREWALL_FIELDS)
    plan = Plan(
        action="firewall.apply",
        summary="Render and apply the managed nftables ruleset (table inet nat64mgr)",
        target=f"inet {settings.firewall_table_name}",
        risk="high",
        details={"settings": {key: getattr(draft, key) for key in sorted(FIREWALL_FIELDS)}},
    )
    plan.before = {key: getattr(base, key) for key in sorted(FIREWALL_FIELDS)}
    plan.after = plan.details["settings"]
    plan.commands = [["nft", "-c", "-f", "<staged ruleset>"], ["nft", "-f", "<staged ruleset>"]]
    try:
        rendered = firewall.build_plan(db, draft)
    except Exception as exc:
        plan.errors.append(f"Firewall validation failed: {exc}")
        return plan
    plan.details.update(
        {
            "content": rendered.content,
            "hash": rendered.hash,
            "validations": rendered.validations,
            "disabled_behavior": "delete managed table only" if not draft.enabled else None,
        }
    )
    plan.warnings.extend(rendered.warnings)
    plan.errors.extend(rendered.errors)
    plan.host_available = rendered.host_available
    return plan


def execute_firewall_apply(db: Session, payload: dict, plan: Plan, user: User | None) -> dict:
    base = _singleton(db, FirewallSettings, lambda: FirewallSettings(id=1))
    for key, value in payload.items():
        if key in FIREWALL_FIELDS:
            setattr(base, key, value)
    return firewall.apply_plan(db, base, user=user)


def plan_network_apply(db: Session, payload: dict) -> Plan:
    base = _singleton(db, NetworkSettings, lambda: NetworkSettings(id=1))
    draft = _transient(base, payload, NETWORK_FIELDS)
    plan = Plan(
        action="network.apply",
        summary="Render and apply managed netplan/sysctl configuration",
        target=settings.network_managed_netplan,
        risk="critical",
        requires_maintenance=True,
        details={"settings": {key: getattr(draft, key) for key in sorted(NETWORK_FIELDS)}},
    )
    plan.before = {key: getattr(base, key) for key in sorted(NETWORK_FIELDS)}
    plan.after = plan.details["settings"]
    plan.commands = [
        ["netplan", "generate"],
        ["sysctl", "--system"],
        ["netplan", "apply"],
        ["systemd-run", "--unit=nat64mgr-netrollback", "--on-active", str(settings.network_rollback_seconds)],
    ]
    try:
        rendered = network.build_plan(db, draft)
    except Exception as exc:
        plan.errors.append(f"Network validation failed: {exc}")
        return plan
    plan.details.update(
        {
            "netplan_yaml": rendered.netplan_yaml,
            "sysctl_content": rendered.sysctl_content,
            "hash": rendered.hash,
            "validations": rendered.validations,
            "rollback_seconds": settings.network_rollback_seconds,
        }
    )
    plan.warnings.extend(rendered.warnings)
    plan.errors.extend(rendered.errors)
    plan.host_available = rendered.host_available
    plan.reversible = True
    return plan


def execute_network_apply(db: Session, payload: dict, plan: Plan, user: User | None) -> dict:
    base = _singleton(db, NetworkSettings, lambda: NetworkSettings(id=1))
    for key, value in payload.items():
        if key in NETWORK_FIELDS:
            setattr(base, key, value)
    result = network.apply_plan(db, base, user=user)
    return result


def plan_network_rollback(db: Session, payload: dict) -> Plan:
    base = _singleton(db, NetworkSettings, lambda: NetworkSettings(id=1))
    plan = Plan(
        action="network.rollback",
        summary="Restore the last network configuration snapshot",
        target=base.last_snapshot,
        risk="critical",
        requires_maintenance=True,
    )
    if not base.last_snapshot:
        plan.errors.append("No network snapshot is recorded")
    plan.before = {"snapshot": base.last_snapshot, "pending_confirmation": base.pending_confirmation}
    plan.commands = [["netplan", "generate"], ["netplan", "apply"]]
    _host_errors(plan)
    return plan


def execute_network_rollback(db: Session, payload: dict, plan: Plan, user: User | None) -> dict:
    base = _singleton(db, NetworkSettings, lambda: NetworkSettings(id=1))
    return network.rollback(db, base, user=user)


# ---------------------------------------------------------------------------
# backup restore / HA
# ---------------------------------------------------------------------------


def plan_backup_restore(db: Session, payload: dict) -> Plan:
    backup = db.get(ConfigurationBackup, int(_param(payload, "backup_id")))
    if not backup:
        raise NotFound("Backup", payload.get("backup_id"))
    mode = str(payload.get("mode", "merge"))
    plan = Plan(
        action="backup.restore",
        summary=f"Restore control-plane configuration from backup {backup.name!r}",
        target=backup.name,
        risk="high" if mode == "replace" else "medium",
    )
    if mode not in {"merge", "replace"}:
        plan.errors.append("Restore mode must be merge or replace")
    try:
        backup_service.validate_backup(backup.content)
    except Exception as exc:
        plan.errors.append(f"Backup validation failed: {exc}")
    plan.details["mode"] = mode
    plan.details["checksum"] = backup.checksum
    plan.details["creates"] = {
        "pools": len(backup.content.get("ipv4_pools", [])),
        "prefixes": len(backup.content.get("ipv6_prefixes", [])),
        "firewall_rules": len((backup.content.get("firewall") or {}).get("rules", [])),
    }
    plan.warnings.append(
        "Restore changes the database only. Jool pool4, BIND, nftables and netplan must be re-applied separately "
        "through approved change requests."
    )
    if mode == "replace":
        plan.warnings.append("Replace mode deletes existing pools, addresses, prefixes and firewall rules.")
        plan.requires_maintenance = True
    return plan


def execute_backup_restore(db: Session, payload: dict, plan: Plan, user: User | None) -> dict:
    backup = db.get(ConfigurationBackup, int(payload["backup_id"]))
    return backup_service.restore_backup(db, backup, user=user, mode=str(payload.get("mode", "merge")))


def plan_ha_apply(db: Session, payload: dict) -> Plan:
    from app.services import ha

    plan = Plan(
        action="ha.apply",
        summary="Render and install the keepalived VRRP configuration",
        target="/etc/keepalived/keepalived.conf",
        risk="critical",
        requires_maintenance=True,
    )
    if not settings.ha_enabled:
        plan.errors.append("HA is disabled by configuration (HA_ENABLED=false)")
    try:
        rendered = ha.render_keepalived(db)
    except Exception as exc:
        plan.errors.append(f"keepalived render failed: {exc}")
        return plan
    plan.details.update({"content": rendered["content"], "hash": rendered["hash"], "settings": rendered["settings"]})
    plan.warnings.extend(rendered["warnings"])
    plan.commands = [["keepalived", "-t", "-f", "<staged keepalived.conf>"], ["systemctl", "restart", "keepalived"]]
    plan.warnings.append(
        "HA failover and NAT64 session continuity are not verified by automated tests. Enable only in a lab first."
    )
    _host_errors(plan)
    return plan


def execute_ha_apply(db: Session, payload: dict, plan: Plan, user: User | None) -> dict:
    from app.services import ha

    rendered = ha.render_keepalived(db, redact=False)
    result = host.call("ha", action="apply", content=rendered["content"], timeout=60)
    if not result.ok:
        raise ValidationFailed(f"keepalived apply failed: {result.message}", result.as_dict())
    write_audit(
        db,
        action="ha.apply",
        user=user,
        resource_type="ha_settings",
        resource_id=1,
        detail={"hash": rendered["hash"]},
    )
    return {"result": result.as_dict(), "hash": rendered["hash"]}


def _now():
    from datetime import datetime

    return datetime.now(UTC)


ACTIONS: dict[str, Action] = {
    action.name: action
    for action in [
        Action("jool.create_instance", "Create Jool instance", plan_create_instance, execute_create_instance),
        Action("jool.remove_instance", "Remove Jool instance", plan_remove_instance, execute_remove_instance, "admin"),
        Action("jool.recreate_instance", "Recreate Jool instance", plan_recreate_instance, execute_recreate_instance, "admin"),
        Action("jool.pause", "Pause NAT64", plan_pause, execute_pause),
        Action("jool.resume", "Resume NAT64", plan_resume, execute_resume),
        Action("jool.update_global", "Update Jool global setting", plan_update_global, execute_update_global),
        Action("pool4.sync", "Synchronize pool4", plan_pool4_sync, execute_pool4_sync, "admin"),
        Action("dns64.apply", "Apply DNS64 configuration", plan_dns64_apply, execute_dns64_apply),
        Action("firewall.apply", "Apply firewall ruleset", plan_firewall_apply, execute_firewall_apply, "admin"),
        Action("network.apply", "Apply network configuration", plan_network_apply, execute_network_apply, "admin"),
        Action("network.rollback", "Rollback network configuration", plan_network_rollback, execute_network_rollback, "admin"),
        Action("backup.restore", "Restore backup", plan_backup_restore, execute_backup_restore, "admin"),
        Action("ha.apply", "Apply keepalived configuration", plan_ha_apply, execute_ha_apply, "superadmin"),
    ]
}


def get_action(name: str) -> Action:
    action = ACTIONS.get(name)
    if not action:
        raise ValidationFailed(f"Unknown change action: {name}")
    return action
