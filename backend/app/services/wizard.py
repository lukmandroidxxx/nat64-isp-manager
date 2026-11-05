from __future__ import annotations

import ipaddress
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import Conflict, ValidationFailed
from app.models import (
    Dns64Settings,
    FirewallSettings,
    InstallationState,
    JoolInstance,
    NetworkInterface,
    NetworkSettings,
    User,
)
from app.models.enums import ServiceStatus
from app.services import dns64, ipv4
from app.services.audit import write_audit
from app.services.host import host
from app.services.jool import JoolError, jool


def _json_safe(value):
    """Convert datetimes inside check results to ISO strings.

    ``last_check`` is persisted in a JSON column, so anything stored there must
    be JSON-serializable. The API contract uses ISO timestamps, so serialising
    here keeps the stored value and the response aligned.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


STEP_DEFINITIONS = [
    {"id": 1, "key": "host", "title": "Host prerequisites", "required": True,
     "description": "Linux, root helper, Jool 4 userspace/kernel module, nftables, BIND and netplan."},
    {"id": 2, "key": "database", "title": "Database", "required": True,
     "description": "PostgreSQL is required in production. SQLite is accepted for development only."},
    {"id": 3, "key": "interfaces", "title": "Network interfaces", "required": True,
     "description": "Select WAN IPv4, WAN IPv6 and LAN IPv6 interfaces and detect their addresses."},
    {"id": 4, "key": "prefix", "title": "NAT64 prefix (pool6)", "required": True,
     "description": "Choose an immutable NAT64 pool6 prefix and keep DNS64 in sync."},
    {"id": 5, "key": "pool4", "title": "Public IPv4 pool", "required": True,
     "description": "Create a pool and add at least one public IPv4 address with a port range."},
    {"id": 6, "key": "jool", "title": "Jool instance", "required": True,
     "description": "Create or import the Jool 4 netfilter instance that carries the prefix."},
    {"id": 7, "key": "pool4_sync", "title": "Synchronize pool4", "required": True,
     "description": "Verify that live Jool pool4 matches the database for TCP, UDP and ICMP."},
    {"id": 8, "key": "dns64", "title": "DNS64", "required": True,
     "description": "Validate BIND, apply the DNS64 fragment and test RFC 7050 AAAA synthesis."},
    {"id": 9, "key": "firewall", "title": "Firewall", "required": False,
     "description": "Optionally enable the managed nftables table with a safe management allowlist."},
    {"id": 10, "key": "security", "title": "Security", "required": True,
     "description": "Admin password changed, HTTPS/production settings, secrets, and audit."},
    {"id": 11, "key": "verification", "title": "Verification", "required": True,
     "description": "Confirm every required check passes before marking installation complete."},
]


def state_record(db: Session) -> InstallationState:
    record = db.get(InstallationState, 1)
    if not record:
        record = InstallationState(id=1, current_step=1, completed=False, steps={}, last_check={})
        db.add(record)
        db.flush()
    return record


def _check(step: int, status: str, message: str, detail: dict | None = None, required: bool | None = None) -> dict:
    definition = next(item for item in STEP_DEFINITIONS if item["id"] == step)
    return {
        "id": step,
        "key": definition["key"],
        "title": definition["title"],
        "required": definition["required"] if required is None else required,
        "status": status,
        "message": message,
        "detail": detail or {},
    }


def _host_check(db: Session) -> dict:
    caps = host.capabilities(force=True)
    if not host.available:
        return _check(
            1,
            "unsupported",
            host.unavailable_reason() or "Host integration unavailable",
            {"mode": host.mode, "platform": caps.get("platform"), "capabilities": caps},
        )
    binaries = caps.get("binaries") or {}
    missing = [name for name in ("jool", "nft", "named-checkconf", "netplan", "ip") if not binaries.get(name)]
    info = jool.status(force=True)
    if missing:
        return _check(1, "failed", f"Missing required binaries: {', '.join(missing)}", {"binaries": binaries})
    if not info.compatible:
        return _check(1, "failed", info.message or "Jool 4.x is required", {"jool": info.__dict__})
    return _check(
        1,
        "ok",
        f"Host ready; Jool {info.version}",
        {"binaries": {k: bool(v) for k, v in binaries.items()}, "jool_version": info.version},
    )


def _database_check(db: Session) -> dict:
    dialect = db.get_bind().dialect.name
    count = db.scalar(select(func.count(User.id))) or 0
    if dialect == "sqlite":
        return _check(
            2,
            "warning",
            "SQLite is active. Use PostgreSQL for production; SQLite is for development and tests only.",
            {"dialect": dialect, "users": count, "production": settings.is_production},
        )
    return _check(2, "ok", f"Database dialect: {dialect}", {"dialect": dialect, "users": count})


def _interfaces_check(db: Session) -> dict:
    network = db.get(NetworkSettings, 1)
    if not network:
        return _check(3, "failed", "Network settings are missing")
    configured = [name for name in (network.wan4_interface, network.wan6_interface, network.lan6_interface) if name]
    detected = db.scalars(select(NetworkInterface)).all()
    if not configured:
        return _check(
            3,
            "failed",
            "No WAN/LAN interfaces are configured",
            {"detected": [item.name for item in detected], "host_available": host.available},
        )
    names = {item.name for item in detected}
    missing = [name for name in configured if names and name not in names]
    if missing:
        return _check(3, "warning", f"Configured interfaces not detected: {', '.join(missing)}", {"configured": configured})
    if not host.available:
        return _check(
            3,
            "warning",
            "Interfaces are configured, but live detection is unsupported on this host",
            {"configured": configured, "mode": host.mode},
        )
    return _check(3, "ok", "Interfaces configured", {"configured": configured})


def _prefix_check(db: Session) -> dict:
    network = db.get(NetworkSettings, 1)
    if not network or not network.nat64_prefix:
        return _check(4, "failed", "NAT64 prefix is not configured")
    try:
        prefix = ipaddress.IPv6Network(network.nat64_prefix, strict=True)
    except ValueError as exc:
        return _check(4, "failed", f"Invalid NAT64 prefix: {exc}")
    if prefix.prefixlen not in {32, 40, 48, 56, 64, 96}:
        return _check(4, "failed", "NAT64 prefix must be /32, /40, /48, /56, /64 or /96")
    dns = db.get(Dns64Settings, 1)
    if dns and dns.prefix != str(prefix):
        return _check(
            4,
            "warning",
            f"DNS64 prefix {dns.prefix} differs from NAT64 prefix {prefix}",
            {"nat64_prefix": str(prefix), "dns64_prefix": dns.prefix},
        )
    return _check(4, "ok", f"NAT64 prefix {prefix}", {"prefix": str(prefix)})


def _pool4_check(db: Session) -> dict:
    summary = ipv4.summary(db)
    if summary["enabled_address_count"] == 0:
        return _check(5, "failed", "No enabled public IPv4 addresses in any pool", summary)
    if summary["pool_count"] == 0:
        return _check(5, "failed", "No IPv4 pools configured", summary)
    return _check(5, "ok", f"{summary['enabled_address_count']} enabled IPv4 address(es)", summary)


def _jool_check(db: Session) -> dict:
    instances = db.scalars(select(JoolInstance).where(JoolInstance.enabled.is_(True))).all()
    if not instances:
        return _check(6, "failed", "No enabled Jool instance in the database")
    info = jool.status()
    if not host.available:
        return _check(
            6,
            "unsupported",
            host.unavailable_reason() or "Live Jool verification is unavailable",
            {"instances": [item.name for item in instances], "status": info.status.value},
        )
    if not info.compatible:
        return _check(6, "failed", info.message or "Jool 4.x is required")
    try:
        live = {item.name: item for item in jool.list_instances()}
    except JoolError as exc:
        return _check(6, "failed", f"Could not list Jool instances: {exc.message}")
    missing = [item.name for item in instances if item.name not in live]
    if missing:
        return _check(6, "failed", f"Instances missing from Jool: {', '.join(missing)}", {"live": sorted(live)})
    return _check(6, "ok", "Jool instances present", {"live": sorted(live), "jool_version": info.version})


def _pool4_sync_check(db: Session) -> dict:
    instances = db.scalars(select(JoolInstance).where(JoolInstance.enabled.is_(True))).all()
    if not instances:
        return _check(7, "failed", "No enabled Jool instance")
    if not host.available:
        return _check(7, "unsupported", host.unavailable_reason() or "Live pool4 verification is unavailable")
    details = {}
    failed = False
    for instance in instances:
        plan = ipv4.build_sync_plan(db, instance)
        details[instance.name] = plan.as_dict()
        if not plan.valid:
            failed = True
    if failed:
        return _check(7, "failed", "pool4 plan has errors", details)
    pending = sum(len(item["adds"]) + len(item["removes"]) for item in details.values())
    if pending:
        return _check(7, "warning", f"{pending} pool4 change(s) pending synchronization", details)
    return _check(7, "ok", "Jool pool4 matches the database", details)


def _dns_check(db: Session) -> dict:
    record = db.get(Dns64Settings, 1)
    if not record:
        return _check(8, "failed", "DNS64 settings are missing")
    if not record.enabled:
        return _check(8, "warning", "DNS64 is disabled; IPv6-only clients cannot resolve A-only names")
    info = dns64.health(db, record)
    synthesis = dns64.test_synthesis(db, record)
    if info["status"] == ServiceStatus.UNSUPPORTED.value:
        return _check(8, "unsupported", info.get("message") or "DNS64 health cannot be verified here", {"health": info})
    if info["status"] != ServiceStatus.ONLINE.value:
        return _check(8, "failed", info.get("message") or f"DNS64 status: {info['status']}", {"health": info})
    if synthesis.get("synthesized") is False:
        return _check(8, "failed", "AAAA synthesis test did not return an address inside the NAT64 prefix", synthesis)
    return _check(8, "ok", "BIND DNS64 is online and RFC 7050 synthesis passed", {"health": info, "synthesis": synthesis})


def _firewall_check(db: Session) -> dict:
    record = db.get(FirewallSettings, 1)
    if not record or not record.enabled:
        return _check(9, "warning", "Managed firewall is disabled", {"enabled": False})
    if not record.management_allowlist:
        return _check(9, "failed", "Firewall enabled without a management allowlist (lockout risk)")
    if not host.available:
        return _check(9, "unsupported", host.unavailable_reason() or "nftables unavailable")
    return _check(
        9,
        "ok",
        "Firewall enabled with management allowlist",
        {"enabled": True, "management_allowlist": record.management_allowlist},
    )


def _security_check(db: Session) -> dict:
    admins = db.scalar(
        select(func.count(User.id)).where(User.is_active.is_(True), User.is_superuser.is_(True))
    ) or 0
    must_change = db.scalar(
        select(func.count(User.id)).where(User.must_change_password.is_(True))
    ) or 0
    warnings = []
    if admins == 0:
        return _check(10, "failed", "No active superadmin account")
    if must_change:
        warnings.append(f"{must_change} account(s) must change their initial password")
    if settings.is_production:
        if not settings.secure_cookies:
            warnings.append("SECURE_COOKIES is disabled in production")
        if settings.secret_key and len(settings.secret_key) < 32:
            warnings.append("SECRET_KEY is too short")
        if not settings.app_encryption_key:
            warnings.append("APP_ENCRYPTION_KEY is not set; secrets use a derived development key")
    status = "warning" if warnings else "ok"
    return _check(10, status, "; ".join(warnings) if warnings else "Administrator and secrets configured",
                  {"active_superadmins": admins, "must_change_password": must_change, "production": settings.is_production})


def run_checks(db: Session) -> dict:
    checks = [
        _host_check(db),
        _database_check(db),
        _interfaces_check(db),
        _prefix_check(db),
        _pool4_check(db),
        _jool_check(db),
        _pool4_sync_check(db),
        _dns_check(db),
        _firewall_check(db),
        _security_check(db),
    ]
    required_failed = [
        item for item in checks if item["required"] and item["status"] in {"failed", "unsupported"}
    ]
    verification = _check(
        11,
        "ok" if not required_failed else "failed",
        "All required checks passed"
        if not required_failed
        else f"{len(required_failed)} required check(s) failed or are unsupported",
        {"failed_keys": [item["key"] for item in required_failed]},
    )
    checks.append(verification)
    # Preserve the furthest incomplete step for the wizard UI.
    current = next(
        (item["id"] for item in checks if item["required"] and item["status"] in {"failed", "unsupported"}),
        len(STEP_DEFINITIONS),
    )
    return {
        "checks": checks,
        "current_step": current,
        "complete": not required_failed,
        "required_failed": len(required_failed),
        "checked_at": datetime.now(UTC),
    }


def get_state(db: Session) -> dict:
    record = state_record(db)
    return {
        "current_step": record.current_step,
        "completed": record.completed,
        "started_at": record.started_at,
        "completed_at": record.completed_at,
        "steps": record.steps or {},
        "last_check": record.last_check or {},
        "definitions": STEP_DEFINITIONS,
        "host_mode": host.mode,
        "host_available": host.available,
    }


def check_all(db: Session, *, user: User | None = None) -> dict:
    record = state_record(db)
    if not record.started_at:
        record.started_at = datetime.now(UTC)
    result = run_checks(db)
    safe_result = _json_safe(result)
    record.last_check = safe_result
    record.current_step = result["current_step"]
    if not result["complete"] and record.completed:
        record.completed = False
        record.completed_at = None
    write_audit(
        db,
        action="wizard.check",
        user=user,
        resource_type="installation_state",
        resource_id=record.id,
        success=result["complete"],
        detail={"required_failed": result["required_failed"], "current_step": result["current_step"]},
    )
    db.flush()
    return safe_result


def update_step(db: Session, step: int, data: dict, *, user: User | None = None) -> dict:
    if not 1 <= step <= len(STEP_DEFINITIONS):
        raise ValidationFailed("Step must be between 1 and 11")
    record = state_record(db)
    if not record.started_at:
        record.started_at = datetime.now(UTC)
    steps = dict(record.steps or {})
    existing = dict(steps.get(str(step)) or {})
    existing.update(data or {})
    existing["saved_at"] = datetime.now(UTC).isoformat()
    steps[str(step)] = existing
    record.steps = steps
    if record.current_step <= step < len(STEP_DEFINITIONS):
        record.current_step = step + 1
    write_audit(
        db,
        action="wizard.step.save",
        user=user,
        resource_type="installation_state",
        resource_id=record.id,
        detail={"step": step, "keys": sorted((data or {}).keys())},
    )
    db.flush()
    return get_state(db)


def complete(db: Session, *, user: User | None = None) -> dict:
    record = state_record(db)
    result = check_all(db, user=user)
    if not result["complete"]:
        raise Conflict("Cannot complete installation while required checks fail", result)
    record.completed = True
    record.completed_at = datetime.now(UTC)
    record.current_step = len(STEP_DEFINITIONS)
    write_audit(
        db,
        action="wizard.complete",
        user=user,
        resource_type="installation_state",
        resource_id=record.id,
        detail={"checked_at": result["checked_at"].isoformat() if result.get("checked_at") else None},
    )
    db.flush()
    return get_state(db)


def reset(db: Session, *, user: User | None = None) -> dict:
    record = state_record(db)
    record.completed = False
    record.completed_at = None
    record.current_step = 1
    record.steps = {}
    record.last_check = {}
    record.started_at = datetime.now(UTC)
    write_audit(db, action="wizard.reset", user=user, resource_type="installation_state", resource_id=record.id, detail={})
    db.flush()
    return get_state(db)
