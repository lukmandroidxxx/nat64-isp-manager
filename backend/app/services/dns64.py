from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ValidationFailed
from app.models import Dns64Settings, User
from app.models.enums import ServiceStatus
from app.services.audit import write_audit
from app.services.host import HostError, host

log = logging.getLogger(__name__)

MANAGED_HEADER = (
    "# Managed by NAT64 ISP Manager. Do not edit manually.\n"
    "# This file must be included INSIDE the options { } block of named.conf.options.\n"
    "# Regenerate from the web UI; direct edits are overwritten on the next apply.\n"
)


@dataclass
class DnsPlan:
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


def validate_ipv6_prefix(value: str, *, pool6: bool = True) -> ipaddress.IPv6Network:
    try:
        network = ipaddress.IPv6Network(value, strict=True)
    except ValueError as exc:
        raise ValidationFailed(f"Invalid IPv6 prefix (host bits must be zero): {value}") from exc
    if pool6 and network.prefixlen not in {32, 40, 48, 56, 64, 96}:
        raise ValidationFailed("DNS64 prefix must be /32, /40, /48, /56, /64 or /96")
    return network


def validate_upstreams(upstreams: list[str]) -> list[str]:
    result: list[str] = []
    for item in upstreams:
        try:
            address = ipaddress.ip_address(item.strip())
        except ValueError as exc:
            raise ValidationFailed(f"Invalid upstream DNS server: {item}") from exc
        text = str(address)
        if text not in result:
            result.append(text)
    if not result:
        raise ValidationFailed("At least one upstream DNS server is required")
    if len(result) > 8:
        raise ValidationFailed("At most 8 upstream DNS servers are supported")
    return result


def render_config(
    *,
    enabled: bool,
    prefix: str,
    upstreams: list[str],
    exclude_prefixes: list[str],
    forward_only: bool = True,
    dnssec_validation: bool = True,
) -> str:
    prefix_net = validate_ipv6_prefix(prefix)
    upstreams = validate_upstreams(upstreams)
    exclude: list[str] = [str(prefix_net)]
    for value in exclude_prefixes:
        try:
            net = ipaddress.IPv6Network(value, strict=False)
        except ValueError as exc:
            raise ValidationFailed(f"Invalid DNS64 exclusion prefix: {value}") from exc
        if str(net) not in exclude:
            exclude.append(str(net))

    lines = [MANAGED_HEADER, f"// generated_at: {datetime.now(UTC).isoformat()}", ""]
    lines.append("forwarders {")
    for server in upstreams:
        lines.append(f"    {server};")
    lines.append("};")
    if forward_only:
        lines.append("forward only;")
    lines.append(f'dnssec-validation {"auto" if dnssec_validation else "no"};')
    if enabled:
        lines.append("")
        lines.append(f"dns64 {prefix_net} {{")
        lines.append("    clients { any; };")
        lines.append("    mapped { any; };")
        lines.append("    exclude {")
        for net in exclude:
            lines.append(f"        {net};")
        lines.append("    };")
        lines.append("};")
    else:
        lines.append("")
        lines.append("// DNS64 disabled by administrator. A/AAAA forwarding remains configured.")
    lines.append("")
    return "\n".join(lines)


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def build_plan(db: Session, record: Dns64Settings) -> DnsPlan:
    content = render_config(
        enabled=record.enabled,
        prefix=record.prefix,
        upstreams=list(record.upstreams or []),
        exclude_prefixes=list(record.exclude_prefixes or []),
        forward_only=record.forward_only,
        dnssec_validation=record.dnssec_validation,
    )
    digest = content_hash(content)
    warnings: list[str] = []
    errors: list[str] = []
    if not record.enabled:
        warnings.append("DNS64 is disabled. IPv6-only clients will not resolve A-only domains.")
    from app.models import NetworkSettings

    network = db.get(NetworkSettings, 1)
    if network and network.nat64_prefix != record.prefix:
        warnings.append(
            f"DNS64 prefix {record.prefix} differs from the configured NAT64 pool6 {network.nat64_prefix}; "
            "synthesized addresses may not traverse Jool."
        )
    if not host.available:
        errors.append(host.unavailable_reason() or "Host integration unavailable")
        host_available = False
    else:
        host_available = True
    validations = {"syntax": None, "named_checkconf": None, "host": host_available}
    if host_available:
        try:
            result = host.dns("validate", content=content)
            if result.ok:
                validations["syntax"] = "ok"
                validations["named_checkconf"] = "ok"
            else:
                validations["syntax"] = "failed"
                validations["named_checkconf"] = result.message
                errors.append(f"BIND validation failed: {result.message}")
        except HostError as exc:
            validations["syntax"] = "error"
            validations["named_checkconf"] = exc.message
            errors.append(f"BIND validation failed: {exc.message}")
    return DnsPlan(
        content=content,
        hash=digest,
        warnings=warnings,
        errors=errors,
        validations=validations,
        host_available=host_available,
    )


def apply_plan(db: Session, record: Dns64Settings, user: User | None = None) -> dict:
    plan = build_plan(db, record)
    if not plan.valid:
        raise ValidationFailed("DNS64 change is not valid", plan.as_dict())
    result = host.dns("apply", content=plan.content, service=record.service_name)
    if not result.ok:
        raise ValidationFailed(f"BIND apply failed: {result.message}", result.as_dict())
    record.last_render_hash = plan.hash
    record.config_version = (record.config_version or 0) + 1
    record.applied_at = datetime.now(UTC)
    record.last_error = None
    record.last_status = "online"
    write_audit(
        db,
        action="dns64.apply",
        user=user,
        resource_type="dns64_settings",
        resource_id=record.id,
        detail={"hash": plan.hash, "enabled": record.enabled, "prefix": record.prefix},
    )
    return {"plan": plan.as_dict(), "result": result.as_dict()}


def health(db: Session, record: Dns64Settings) -> dict:
    now = datetime.now(UTC)
    if not host.available:
        record.last_status = ServiceStatus.UNSUPPORTED.value
        record.last_checked_at = now
        record.last_error = host.unavailable_reason()
        return {
            "status": ServiceStatus.UNSUPPORTED.value,
            "active": False,
            "message": record.last_error,
            "checked_at": now,
        }
    try:
        status_result = host.dns("status", service=record.service_name)
        active = bool(status_result.data.get("active"))
        answers: list[str] = []
        probe_error = None
        try:
            probe = host.dns(
                "dig",
                server=record.listen_address,
                domain=record.test_domain,
                type="AAAA",
                timeout=settings.dns_query_timeout_seconds,
            )
            answers = probe.data.get("answers") or []
            if not probe.ok:
                probe_error = probe.message
        except HostError as exc:
            probe_error = exc.message
        status = ServiceStatus.ONLINE.value if active and probe_error is None else (
            ServiceStatus.DEGRADED.value if active else ServiceStatus.OFFLINE.value
        )
        record.last_status = status
        record.last_checked_at = now
        record.last_error = probe_error or status_result.message or None
        return {
            "status": status,
            "active": active,
            "service": record.service_name,
            "answers": answers,
            "message": record.last_error,
            "checked_at": now,
        }
    except HostError as exc:
        record.last_status = ServiceStatus.ERROR.value
        record.last_checked_at = now
        record.last_error = exc.message
        return {"status": ServiceStatus.ERROR.value, "active": False, "message": exc.message, "checked_at": now}


def test_synthesis(db: Session, record: Dns64Settings) -> dict:
    if not host.available:
        return {
            "status": ServiceStatus.UNSUPPORTED.value,
            "message": host.unavailable_reason(),
            "synthesized": None,
            "answers": [],
        }
    try:
        result = host.dns(
            "dig",
            server=record.listen_address,
            domain=record.test_domain,
            type="AAAA",
            timeout=settings.dns_query_timeout_seconds,
        )
    except HostError as exc:
        return {"status": "error", "message": exc.message, "synthesized": None, "answers": []}
    answers = result.data.get("answers") or []
    prefix = ipaddress.IPv6Network(record.prefix, strict=False)
    synthesized = False
    for answer in answers:
        try:
            if ipaddress.IPv6Address(answer) in prefix:
                synthesized = True
                break
        except ValueError:
            continue
    detail = {
        "status": "ok" if result.ok else "error",
        "message": result.message or None,
        "domain": record.test_domain,
        "prefix": record.prefix,
        "answers": answers,
        "synthesized": synthesized if result.ok else None,
        "checked_at": datetime.now(UTC),
    }
    record.last_synthesis_ok = synthesized if result.ok else False
    record.last_synthesis_at = datetime.now(UTC)
    record.last_synthesis_detail = {k: str(v) for k, v in detail.items()}
    return detail


def test_ipv4_resolution(db: Session, record: Dns64Settings) -> dict:
    if not host.available:
        return {"status": ServiceStatus.UNSUPPORTED.value, "message": host.unavailable_reason(), "answers": []}
    try:
        result = host.dns(
            "dig",
            server=record.listen_address,
            domain=record.test_domain,
            type="A",
            timeout=settings.dns_query_timeout_seconds,
        )
        return {
            "status": "ok" if result.ok else "error",
            "domain": record.test_domain,
            "answers": result.data.get("answers") or [],
            "message": result.message or None,
        }
    except HostError as exc:
        return {"status": "error", "message": exc.message, "answers": []}


def parse_named_stats(content: str) -> dict:
    """Extract useful counters from BIND's named.stats text format."""
    section = ""
    counters: dict[str, dict[str, int]] = {}
    for line in content.splitlines():
        line = line.rstrip()
        if line.startswith("++") and line.endswith("++"):
            section = line.strip("+ ").strip()
            continue
        match = re.match(r"^\s*(\d+)\s+([A-Za-z0-9_./-]+)\s*$", line)
        if match and section:
            counters.setdefault(section, {})[match.group(2)] = int(match.group(1))

    incoming = counters.get("Incoming Requests", {})
    queries = counters.get("Incoming Queries", {})
    resolver = counters.get("Resolver Statistics", {})
    cache = counters.get("Cache DB RRsets", {})
    a_queries = queries.get("A")
    aaaa_queries = queries.get("AAAA")
    total_queries = incoming.get("QUERY")
    if total_queries is None:
        total_queries = sum(queries.values()) if queries else None
    return {
        "total_queries": total_queries,
        "a_queries": a_queries,
        "aaaa_queries": aaaa_queries,
        "responses": incoming.get("RESPONSE"),
        "query_failures": resolver.get("QueryFailure") or resolver.get("Failure"),
        "cache_hits": resolver.get("QueryCacheHit") or cache.get("cache hits"),
        "sections": counters,
        "raw_size": len(content),
        "source": "named.stats",
    }


def query_stats(db: Session, record: Dns64Settings) -> dict:
    if not host.available:
        return {"status": ServiceStatus.UNSUPPORTED.value, "message": host.unavailable_reason()}
    try:
        result = host.dns("stats", path=settings.dns_stats_path)
        if not result.ok:
            return {"status": "error", "message": result.message}
        stats = parse_named_stats(result.data.get("content") or result.stdout or "")
        stats["status"] = "ok"
        stats["checked_at"] = datetime.now(UTC)
        return stats
    except HostError as exc:
        return {"status": "error", "message": exc.message}
