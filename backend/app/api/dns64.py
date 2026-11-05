from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import require_permission
from app.core.errors import ValidationFailed
from app.core.routing import APIRouter
from app.models import Dns64Settings, User
from app.schemas import Dns64Test, Dns64Update
from app.services import changes, dns64
from app.services.audit import write_audit
from app.services.host import host

router = APIRouter(prefix="/dns64", tags=["dns64"])

READ = require_permission("read")
REQUEST = require_permission("changes.request")
WRITE = require_permission("dns64.write")


def _record(db: Session) -> Dns64Settings:
    record = db.get(Dns64Settings, 1)
    if not record:
        record = Dns64Settings(
            id=1,
            enabled=True,
            prefix="64:ff9b::/96",
            upstreams=list(settings.dns_default_upstreams),
            listen_address=settings.dns_listen_address,
            service_name=settings.dns_service_name,
            managed_config_path=settings.dns_managed_config,
            test_domain=settings.dns_test_domain,
            exclude_prefixes=["64:ff9b::/96", "fe80::/10", "::1/128"],
        )
        db.add(record)
        db.flush()
    return record


def _settings_dict(record: Dns64Settings) -> dict:
    preview = None
    preview_error = None
    try:
        preview = dns64.render_config(
            enabled=record.enabled,
            prefix=record.prefix,
            upstreams=list(record.upstreams or []),
            exclude_prefixes=list(record.exclude_prefixes or []),
            forward_only=record.forward_only,
            dnssec_validation=record.dnssec_validation,
        )
    except Exception as exc:  # invalid stored values must not break the page
        preview_error = f"{type(exc).__name__}: {exc}"
    return {
        "id": record.id,
        "enabled": record.enabled,
        "prefix": record.prefix,
        "upstreams": record.upstreams,
        "listen_address": record.listen_address,
        "service_name": record.service_name,
        "managed_config_path": record.managed_config_path,
        "test_domain": record.test_domain,
        "exclude_prefixes": record.exclude_prefixes,
        "forward_only": record.forward_only,
        "dnssec_validation": record.dnssec_validation,
        "last_status": record.last_status,
        "last_checked_at": record.last_checked_at,
        "last_error": record.last_error,
        "last_synthesis_ok": record.last_synthesis_ok,
        "last_synthesis_at": record.last_synthesis_at,
        "last_synthesis_detail": record.last_synthesis_detail,
        "config_version": record.config_version,
        "last_render_hash": record.last_render_hash,
        "applied_at": record.applied_at,
        "preview": preview,
        "preview_error": preview_error,
        "managed_file_note": (
            "The managed fragment is included INSIDE options { } in named.conf.options. "
            "Editing it directly is overwritten by the next approved apply."
        ),
    }


@router.get("")
def get_dns64(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    record = _record(db)
    return {
        "settings": _settings_dict(record),
        "host": {"available": host.available, "mode": host.mode, "reason": host.unavailable_reason()},
        "defaults": {
            "upstreams": settings.dns_default_upstreams,
            "listen_address": settings.dns_listen_address,
            "service_name": settings.dns_service_name,
            "managed_config_path": settings.dns_managed_config,
            "test_domain": settings.dns_test_domain,
        },
        "warnings": [
            "Applying DNS64 rewrites the managed BIND fragment and runs rndc reconfig through the privileged helper.",
            "Use a /96 prefix (for example 64:ff9b::/96) unless your resolver and Jool are configured for another length.",
        ],
    }


@router.get("/status")
def status(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    record = _record(db)
    return dns64.health(db, record)


@router.post("/health")
def health_now(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    return dns64.health(db, _record(db))


@router.post("/apply", status_code=202)
def apply_dns64(
    payload: Dns64Update,
    user: User = Depends(REQUEST),
    db: Session = Depends(get_db),
) -> dict:
    """Create a dry-run + approval request; never touches BIND directly."""
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise ValidationFailed("No DNS64 changes supplied")
    record = _record(db)
    # Validate values that do not require the host so a typo is rejected now.
    if data.get("prefix"):
        dns64.validate_ipv6_prefix(data["prefix"])
    if data.get("upstreams") is not None:
        data["upstreams"] = dns64.validate_upstreams(list(data["upstreams"]))
    change = changes.create_change(db, action_name="dns64.apply", payload=data, user=user)
    return {
        **changes.change_dict(change),
        "settings_before": _settings_dict(record),
        "note": "Approve and execute this change request to write the BIND fragment and reload named.",
    }


@router.post("/test")
def test_dns64(
    payload: Dns64Test,
    request: Request,
    user: User = Depends(READ),
    db: Session = Depends(get_db),
) -> dict:
    record = _record(db)
    if payload.domain:
        record.test_domain = payload.domain
    synthesis = dns64.test_synthesis(db, record)
    ipv4 = dns64.test_ipv4_resolution(db, record)
    write_audit(
        db,
        action="dns64.test",
        user=user,
        request=request,
        resource_type="dns64_settings",
        resource_id=record.id,
        success=bool(synthesis.get("synthesized")),
        detail={"domain": record.test_domain, "synthesis": synthesis.get("status"), "ipv4": ipv4.get("status")},
    )
    return {
        "domain": record.test_domain,
        "prefix": record.prefix,
        "listen_address": record.listen_address,
        "synthesis": synthesis,
        "ipv4_resolution": ipv4,
        "note": "A synthesized result is verified only when an AAAA answer falls inside the configured NAT64 prefix.",
    }


@router.get("/stats")
def dns_stats(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    return dns64.query_stats(db, _record(db))


@router.post("/preview")
def preview_dns64(payload: Dns64Update, _: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    record = _record(db)
    data = payload.model_dump(exclude_unset=True)
    try:
        content = dns64.render_config(
            enabled=data.get("enabled", record.enabled),
            prefix=data.get("prefix", record.prefix),
            upstreams=list(data.get("upstreams", record.upstreams) or []),
            exclude_prefixes=list(data.get("exclude_prefixes", record.exclude_prefixes) or []),
            forward_only=data.get("forward_only", record.forward_only),
            dnssec_validation=data.get("dnssec_validation", record.dnssec_validation),
        )
    except Exception as exc:
        raise ValidationFailed(f"DNS64 preview failed: {exc}") from exc
    return {
        "content": content,
        "hash": dns64.content_hash(content),
        "generated_at": datetime.now(UTC),
        "validated_on_host": False,
        "host_available": host.available,
        "note": "Syntax validation with named-checkconf happens during the approved change's dry run.",
    }
