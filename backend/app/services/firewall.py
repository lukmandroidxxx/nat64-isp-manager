from __future__ import annotations

import hashlib
import ipaddress
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ValidationFailed
from app.models import (
    FirewallRule,
    FirewallSettings,
    IPv4Address,
    IPv4Pool,
    JoolInstance,
    NetworkSettings,
    User,
)
from app.models.enums import ServiceStatus
from app.services.audit import write_audit
from app.services.host import HostError, host

log = logging.getLogger(__name__)
TABLE = settings.firewall_table_name
PROTOCOLS = {"tcp", "udp", "icmp", "icmpv6"}


@dataclass
class FirewallPlan:
    content: str
    hash: str
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    validations: dict = field(default_factory=dict)
    host_available: bool = True

    @property
    def valid(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "content": self.content,
            "hash": self.hash,
            "warnings": self.warnings,
            "errors": self.errors,
            "validations": self.validations,
            "host_available": self.host_available,
        }


def validate_cidr(value: str | None, family: str | None = None) -> str | None:
    if not value:
        return None
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError as exc:
        raise ValidationFailed(f"Invalid CIDR: {value}") from exc
    if family == "ipv4" and network.version != 4:
        raise ValidationFailed(f"Expected IPv4 CIDR: {value}")
    if family == "ipv6" and network.version != 6:
        raise ValidationFailed(f"Expected IPv6 CIDR: {value}")
    return str(network)


def validate_port(value: int | None, name: str) -> int | None:
    if value is None:
        return None
    if not (1 <= int(value) <= 65535):
        raise ValidationFailed(f"{name} must be between 1 and 65535")
    return int(value)


def validate_rule(rule: FirewallRule) -> list[str]:
    warnings: list[str] = []
    if rule.direction not in {"input", "forward"}:
        raise ValidationFailed("Firewall rule direction must be input or forward")
    if rule.family not in {"inet", "ipv4", "ipv6"}:
        raise ValidationFailed("Firewall family must be inet, ipv4 or ipv6")
    if rule.action not in {"accept", "drop", "reject", "log"}:
        raise ValidationFailed("Firewall action must be accept, drop, reject or log")
    if rule.protocol and rule.protocol not in PROTOCOLS:
        raise ValidationFailed("Firewall protocol must be tcp, udp, icmp or icmpv6")
    if (rule.source_port or rule.destination_port) and rule.protocol not in {"tcp", "udp"}:
        raise ValidationFailed("Source/destination ports require tcp or udp protocol")
    validate_cidr(rule.source_cidr, "ipv4" if rule.family == "ipv4" else "ipv6" if rule.family == "ipv6" else None)
    validate_cidr(
        rule.destination_cidr, "ipv4" if rule.family == "ipv4" else "ipv6" if rule.family == "ipv6" else None
    )
    validate_port(rule.source_port, "source_port")
    validate_port(rule.destination_port, "destination_port")
    if rule.rate_limit_per_second is not None and rule.rate_limit_per_second < 1:
        raise ValidationFailed("Rate limit must be positive")
    if rule.action == "accept":
        warnings.append(f"Rule {rule.name!r} accepts matching traffic; verify it cannot bypass a denylist")
    if rule.direction == "input" and rule.action in {"accept", "drop", "reject"} and not rule.source_cidr:
        warnings.append(f"Input rule {rule.name!r} has no source restriction")
    return warnings


def _match(rule: FirewallRule) -> list[str]:
    parts: list[str] = []
    if rule.source_cidr:
        network = ipaddress.ip_network(rule.source_cidr, strict=False)
        parts.append(f"{'ip' if network.version == 4 else 'ip6'} saddr {network}")
    if rule.destination_cidr:
        network = ipaddress.ip_network(rule.destination_cidr, strict=False)
        parts.append(f"{'ip' if network.version == 4 else 'ip6'} daddr {network}")
    if rule.protocol:
        parts.append(rule.protocol)
        if rule.source_port:
            parts.append(f"sport {rule.source_port}")
        if rule.destination_port:
            parts.append(f"dport {rule.destination_port}")
    return parts


def _set(name: str, kind: str, elements: list[str]) -> list[str]:
    lines = [f"    set {name} {{", f"        type {kind};", "        flags interval;"]
    if elements:
        lines.append(f"        elements = {{ {', '.join(elements)} }}")
    lines.append("    }")
    return lines


def _collect_set_elements(
    db: Session,
) -> tuple[list[str], list[str], list[str], list[str], list[str], list[str]]:
    pool4: list[str] = []
    stmt = (
        select(IPv4Address)
        .join(IPv4Pool)
        .where(IPv4Address.enabled.is_(True), IPv4Pool.enabled.is_(True))
        .order_by(IPv4Address.address)
    )
    for address in db.scalars(stmt).all():
        text = str(ipaddress.ip_network(address.address + "/32"))
        if text not in pool4:
            pool4.append(text)
    network = db.get(NetworkSettings, 1)
    pool6: list[str] = []
    if network and network.nat64_prefix:
        pool6.append(str(ipaddress.ip_network(network.nat64_prefix, strict=False)))
    else:
        for instance in db.scalars(select(JoolInstance)).all():
            value = str(ipaddress.ip_network(instance.pool6, strict=False))
            if value not in pool6:
                pool6.append(value)
    client6: list[str] = []
    if network and network.client_prefix:
        client6.append(str(ipaddress.ip_network(network.client_prefix, strict=False)))
    firewall = db.get(FirewallSettings, 1)
    mgmt4: list[str] = []
    mgmt6: list[str] = []
    if firewall:
        for cidr in firewall.management_allowlist or []:
            net = ipaddress.ip_network(cidr, strict=False)
            (mgmt4 if net.version == 4 else mgmt6).append(str(net))
    from app.models import IPv6Client

    blocked6: list[str] = []
    for client in db.scalars(select(IPv6Client).where(IPv6Client.is_blocked.is_(True))).all():
        try:
            blocked6.append(str(ipaddress.ip_network(client.address + "/128", strict=False)))
        except ValueError:
            continue
    return pool4, pool6, client6, mgmt4, mgmt6, blocked6


def render_ruleset(db: Session, firewall: FirewallSettings) -> str:
    rules = db.scalars(
        select(FirewallRule)
        .where(FirewallRule.enabled.is_(True))
        .order_by(FirewallRule.priority, FirewallRule.id)
    ).all()
    pool4, pool6, client6, mgmt4, mgmt6, blocked6 = _collect_set_elements(db)
    warnings: list[str] = []

    if (
        firewall.enabled
        and firewall.protect_control_plane
        and not (mgmt4 or mgmt6)
    ):
        raise ValidationFailed(
            "Control-plane protection requires at least one management allowlist CIDR to avoid SSH lockout"
        )
    for rule in rules:
        warnings.extend(validate_rule(rule))

    lines = [
        "# Managed by NAT64 ISP Manager. Do not edit manually.",
        "# Apply through the web UI change-approval workflow.",
        f"table inet {TABLE} {{",
    ]
    lines.extend(_set("pool4", "ipv4_addr", pool4))
    lines.extend(_set("pool6", "ipv6_addr", pool6))
    lines.extend(_set("mgmt4", "ipv4_addr", mgmt4))
    lines.extend(_set("mgmt6", "ipv6_addr", mgmt6))
    lines.extend(_set("client6", "ipv6_addr", client6))
    lines.extend(_set("blocked6", "ipv6_addr", blocked6))
    lines.append("")

    input_rules = [r for r in rules if r.direction == "input"]
    forward_rules = [r for r in rules if r.direction == "forward"]

    # INPUT ---------------------------------------------------------------
    lines.append("    chain input {")
    lines.append("        type filter hook input priority -10; policy accept;")
    lines.append("        ct state established,related accept")
    lines.append('        iifname "lo" accept')
    if firewall.invalid_packet_protection:
        lines.append("        ct state invalid drop")
    if firewall.syn_flood_protection:
        rate = max(1, int(firewall.new_connection_rate))
        burst = max(1, int(firewall.rate_burst))
        lines.append(f"        tcp flags syn limit rate over {rate}/second burst {burst} packets drop")
    if firewall.protect_control_plane and (mgmt4 or mgmt6):
        ports = ", ".join(str(p) for p in (firewall.management_ports or [])) or "22"
        if mgmt4:
            lines.append(f"        ip saddr @mgmt4 tcp dport {{ {ports} }} accept")
        if mgmt6:
            lines.append(f"        ip6 saddr @mgmt6 tcp dport {{ {ports} }} accept")
        for port in firewall.management_ports or []:
            lines.append(f"        tcp dport {port} drop")
    if client6:
        lines.append("        ip6 saddr @client6 udp dport 53 accept")
        lines.append("        ip6 saddr @client6 tcp dport 53 accept")
    lines.append("        ip6 nexthdr ipv6-icmp accept")
    lines.append("        udp dport 547 accept")
    for rule in input_rules:
        lines.extend(_render_rule(rule))
    if firewall.default_input_policy == "drop":
        lines.append("        drop")
    else:
        lines.append("        accept")
    lines.append("    }")
    lines.append("")

    # FORWARD -------------------------------------------------------------
    lines.append("    chain forward {")
    policy = "drop" if firewall.default_forward_policy == "drop" else "accept"
    lines.append(f"        type filter hook forward priority -10; policy {policy};")
    lines.append("        ct state established,related accept")
    if firewall.invalid_packet_protection:
        lines.append("        ct state invalid drop")
    if blocked6:
        lines.append("        ip6 saddr @blocked6 drop")
    if pool6:
        lines.append("        ip6 daddr @pool6 accept")
    if pool4:
        lines.append("        ip daddr @pool4 accept")
    if firewall.mss_clamping:
        lines.append("        tcp flags syn tcp option maxseg size set rt mtu")
    if firewall.syn_flood_protection:
        rate = max(1, int(firewall.new_connection_rate))
        burst = max(1, int(firewall.rate_burst))
        lines.append(f"        tcp flags syn limit rate over {rate}/second burst {burst} packets drop")
    for rule in forward_rules:
        lines.extend(_render_rule(rule))
    if firewall.default_forward_policy == "drop":
        lines.append("        drop")
    else:
        lines.append("        accept")
    lines.append("    }")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _render_rule(rule: FirewallRule) -> list[str]:
    parts = _match(rule)
    out: list[str] = []
    prefix = f"        # {rule.name}\n"
    if rule.log or rule.action == "log":
        if parts:
            out.append(f"        {' '.join(parts)} log prefix \"nat64mgr {rule.name}: \" level info")
        else:
            out.append(f"        log prefix \"nat64mgr {rule.name}: \" level info")
    if rule.action == "log":
        return [prefix, *out]
    action = rule.action
    limit = ""
    if rule.rate_limit_per_second:
        burst = max(1, rule.rate_limit_per_second)
        limit = f" limit rate over {rule.rate_limit_per_second}/second burst {burst} packets"
    if parts:
        out.append(f"        {' '.join(parts)}{limit} {action}")
    else:
        out.append(f"        {limit.strip()} {action}".strip())
    return [prefix, *out]


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def build_plan(db: Session, firewall: FirewallSettings) -> FirewallPlan:
    if firewall.enabled:
        content = render_ruleset(db, firewall)
    else:
        content = (
            f"# Firewall disabled. Applying this change deletes the managed nftables table inet {TABLE}.\n"
            f"# Existing Jool netfilter rules and unrelated nftables tables are not touched.\n"
        )
    digest = content_hash(content)
    warnings: list[str] = []
    errors: list[str] = []
    if firewall.enabled:
        warnings.append(
            "The firewall is enabled: verify the management allowlist and SSH port before applying. "
            "An incorrect allowlist can lock you out."
        )
        if firewall.default_input_policy == "drop":
            warnings.append("Input default policy is drop; DNS, DHCPv6 and ICMPv6 essentials are explicitly allowed.")
        if firewall.default_forward_policy == "drop":
            warnings.append("Forward default policy is drop; NAT64 pool6/pool4 and established traffic are explicitly allowed.")
    else:
        warnings.append("Applying with the firewall disabled removes only the managed nat64mgr table.")
    host_available = host.available
    validations = {"syntax": None, "nft_check": None, "host": host_available}
    if not host_available:
        errors.append(host.unavailable_reason() or "Host integration unavailable")
    elif firewall.enabled:
        try:
            result = host.firewall("validate", content=content)
            validations["syntax"] = "ok" if result.ok else "failed"
            validations["nft_check"] = result.message or "ok"
            if not result.ok:
                errors.append(f"nftables validation failed: {result.message}")
        except HostError as exc:
            validations["syntax"] = "error"
            validations["nft_check"] = exc.message
            errors.append(f"nftables validation failed: {exc.message}")
    else:
        validations["syntax"] = "skip (firewall disabled)"
    return FirewallPlan(
        content=content,
        hash=digest,
        warnings=warnings,
        errors=errors,
        validations=validations,
        host_available=host_available,
    )


def apply_plan(db: Session, firewall: FirewallSettings, user: User | None = None) -> dict:
    plan = build_plan(db, firewall)
    if not plan.valid:
        raise ValidationFailed("Firewall change is not valid", plan.as_dict())
    if firewall.enabled:
        result = host.firewall("apply", content=plan.content)
    else:
        result = host.firewall("clear")
    if not result.ok:
        raise ValidationFailed(f"Firewall apply failed: {result.message}", result.as_dict())
    firewall.last_render_hash = plan.hash
    firewall.config_version = (firewall.config_version or 0) + 1
    firewall.applied_at = datetime.now(UTC)
    firewall.last_error = None
    for rule in db.scalars(select(FirewallRule)).all():
        rule.last_applied_at = datetime.now(UTC)
    write_audit(
        db,
        action="firewall.apply",
        user=user,
        resource_type="firewall_settings",
        resource_id=firewall.id,
        detail={"hash": plan.hash, "enabled": firewall.enabled},
    )
    return {"plan": plan.as_dict(), "result": result.as_dict()}


def status(db: Session, firewall: FirewallSettings) -> dict:
    if not firewall.enabled:
        return {"status": "disabled", "enabled": False, "table_present": False}
    if not host.available:
        return {
            "status": ServiceStatus.UNSUPPORTED.value,
            "enabled": True,
            "table_present": None,
            "message": host.unavailable_reason(),
        }
    try:
        result = host.firewall("list")
        return {
            "status": ServiceStatus.ONLINE.value if result.ok else ServiceStatus.ERROR.value,
            "enabled": True,
            "table_present": result.ok,
            "message": result.message or None,
            "applied_at": firewall.applied_at,
        }
    except HostError as exc:
        return {"status": ServiceStatus.ERROR.value, "enabled": True, "table_present": None, "message": exc.message}
