from __future__ import annotations

import ipaddress
import json
import logging
import time
from datetime import UTC, datetime, timedelta

import psutil
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models import (
    Dns64Settings,
    FirewallSettings,
    IPv4Address,
    IPv4Pool,
    IPv6Client,
    IPv6Prefix,
    JoolInstance,
    Nat64Session,
    NetworkSettings,
    SystemMetric,
)
from app.models.enums import JoolStatus, ServiceStatus
from app.services.host import HostError, host
from app.services.jool import jool
from app.services.jool.adapter import JoolObservation
from app.services.jool.parser import JoolSessionRecord

log = logging.getLogger(__name__)

_net_previous: dict[str, tuple[float, float, float]] = {}
_cpu_primed = False


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _stat(stats: dict[str, int], *names: str) -> int | None:
    """Look up a Jool counter without inventing a value.

    Jool's counter names vary between releases. Match exact names first, then a
    case-insensitive suffix. Return None when the counter is absent.
    """
    lowered = {str(key).lower(): value for key, value in stats.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    for name in names:
        suffix = name.lower()
        for key, value in lowered.items():
            if key.endswith(suffix):
                return value
    return None


def upsert_instance_observation(db: Session, instance: JoolInstance, observation: JoolObservation) -> None:
    now = _utcnow()
    instance.status = observation.status.value
    instance.observed_pool6 = observation.pool6
    instance.last_seen_at = now if observation.status in {JoolStatus.ONLINE, JoolStatus.PAUSED} else instance.last_seen_at
    instance.last_synced_at = now
    instance.last_error = "; ".join(observation.errors)[:2000] if observation.errors else None
    instance.paused = observation.paused
    if observation.settings:
        instance.observed_stats = {
            "stats": observation.stats,
            "settings": observation.settings,
            "observed_at": now.isoformat(),
        }
    if observation.pool4:
        instance.observed_pool4 = {
            protocol: [
                {
                    "address": entry.address,
                    "port_min": entry.port_min,
                    "port_max": entry.port_max,
                }
                for entry in entries
            ]
            for protocol, entries in observation.pool4.items()
        }
    if observation.status not in {JoolStatus.ONLINE, JoolStatus.PAUSED}:
        return
    sync_sessions(db, instance, observation.sessions, now)
    if observation.stats:
        update_pool4_session_stats(db, instance.id, now)


def sync_sessions(
    db: Session,
    instance: JoolInstance,
    records: list[JoolSessionRecord],
    now: datetime | None = None,
) -> dict:
    """Upsert observed sessions and expire rows that Jool no longer reports."""
    now = now or _utcnow()
    records = records[: settings.max_sessions_per_sync]
    existing = {
        (
            row.protocol,
            row.client_ipv6,
            row.client_port,
            row.translated_ipv4,
            row.translated_port,
            row.remote_ipv4,
            row.remote_port,
        ): row
        for row in db.scalars(
            select(Nat64Session).where(
                Nat64Session.instance_id == instance.id,
                Nat64Session.status == "active",
            )
        ).all()
    }
    address_rows = {
        row.address: row
        for row in db.scalars(
            select(IPv4Address)
            .join(IPv4Pool, IPv4Address.pool_id == IPv4Pool.id)
            .where(IPv4Pool.jool_instance_id == instance.id)
        ).all()
    }
    seen: set[tuple] = set()
    created = updated = 0
    for record in records:
        key = record.key
        if key in seen:
            continue
        seen.add(key)
        row = existing.get(key)
        if row is None:
            row = Nat64Session(
                instance_id=instance.id,
                protocol=record.protocol,
                client_ipv6=record.client_ipv6,
                client_port=record.client_port,
                translated_ipv4=record.translated_ipv4,
                translated_port=record.translated_port,
                remote_ipv4=record.remote_ipv4,
                remote_port=record.remote_port,
                expires_in_seconds=record.expires_in_seconds,
                jool_state=record.jool_state,
                status="active",
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(row)
            created += 1
        else:
            row.last_seen_at = now
            row.expires_in_seconds = record.expires_in_seconds
            row.jool_state = record.jool_state
            row.status = "active"
            row.expired_at = None
            updated += 1
        address = address_rows.get(record.translated_ipv4)
        row.pool4_address_id = address.id if address else None
        if address:
            address.last_session_at = now
    expired = 0
    for key, row in existing.items():
        if key not in seen:
            row.status = "expired"
            row.expired_at = now
            expired += 1
    db.flush()
    return {"observed": len(records), "created": created, "updated": updated, "expired": expired}


def update_pool4_session_stats(db: Session, instance_id: int, now: datetime) -> None:
    rows = db.execute(
        select(
            Nat64Session.translated_ipv4,
            Nat64Session.protocol,
            Nat64Session.translated_port,
        ).where(Nat64Session.instance_id == instance_id, Nat64Session.status == "active")
    ).all()
    ports: dict[str, set[tuple[str, int | None]]] = {}
    for row in rows:
        ports.setdefault(row.translated_ipv4, set()).add((row.protocol, row.translated_port))
    addresses = db.scalars(
        select(IPv4Address).join(IPv4Pool).where(IPv4Pool.jool_instance_id == instance_id)
    ).all()
    for address in addresses:
        if address.address in ports:
            address.last_session_at = now


def sync_clients(db: Session, now: datetime | None = None) -> dict:
    now = now or _utcnow()
    counts = {
        row.client_ipv6: row.count
        for row in db.execute(
            select(Nat64Session.client_ipv6, func.count(Nat64Session.id).label("count"))
            .where(Nat64Session.status == "active")
            .group_by(Nat64Session.client_ipv6)
        ).all()
    }
    prefixes = db.scalars(select(IPv6Prefix).where(IPv6Prefix.enabled.is_(True))).all()
    known = {row.address: row for row in db.scalars(select(IPv6Client)).all()}
    neighbors: dict[str, dict] = {}
    if host.available:
        try:
            result = host.ip("neighbors")
            if result.ok:
                for item in json.loads(result.stdout or "[]"):
                    address = item.get("dst")
                    if address and ":" in address:
                        neighbors[address] = item
        except (HostError, json.JSONDecodeError):
            pass

    touched = 0
    for address, count in counts.items():
        row = known.get(address)
        if row is None:
            row = IPv6Client(
                address=address,
                first_seen_at=now,
                last_seen_at=now,
                active_session_count=count,
            )
            db.add(row)
            known[address] = row
        else:
            row.last_seen_at = now
            row.active_session_count = count
        row.prefix_id = _match_prefix(address, prefixes)
        neighbor = neighbors.get(address)
        if neighbor:
            row.mac_address = neighbor.get("lladdr") or row.mac_address
            row.interface = neighbor.get("dev") or row.interface
        touched += 1
    # Active session count for clients that no longer have sessions.
    for address, row in known.items():
        if address not in counts:
            row.active_session_count = 0
    db.flush()
    return {"clients": len(known), "active_clients": touched, "neighbor_entries": len(neighbors)}


def _match_prefix(address: str, prefixes: list[IPv6Prefix]) -> int | None:
    try:
        ip = ipaddress.IPv6Address(address)
    except ValueError:
        return None
    best: tuple[int, int] | None = None
    for prefix in prefixes:
        try:
            network = ipaddress.IPv6Network(prefix.prefix, strict=False)
        except ValueError:
            continue
        if ip in network and (best is None or network.prefixlen > best[1]):
            best = (prefix.id, network.prefixlen)
    return best[0] if best else None


def collect_sessions(db: Session) -> dict:
    """Poll every enabled instance. A host failure marks status, never fakes data."""
    instances = db.scalars(select(JoolInstance).where(JoolInstance.enabled.is_(True))).all()
    results = []
    for instance in instances:
        try:
            observation = jool.observe(instance.name, include_sessions=settings.collect_all_protocols)
        except Exception as exc:  # defensive: collector must not kill the loop
            log.exception("Jool observation failed for %s", instance.name)
            instance.status = JoolStatus.ERROR.value
            instance.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            results.append({"instance": instance.name, "status": "error", "errors": [instance.last_error]})
            continue
        upsert_instance_observation(db, instance, observation)
        results.append(
            {
                "instance": instance.name,
                "status": observation.status.value,
                "sessions": len(observation.sessions),
                "warnings": observation.session_warnings,
                "errors": observation.errors,
            }
        )
    client_summary = sync_clients(db)
    _prune_sessions(db)
    return {"instances": results, "clients": client_summary}


def _prune_sessions(db: Session) -> int:
    cutoff = _utcnow() - timedelta(hours=max(1, settings.session_retention_hours))
    result = db.execute(
        delete(Nat64Session).where(
            Nat64Session.status.in_(["expired", "stale"]),
            Nat64Session.last_seen_at < cutoff,
        )
    )
    return int(result.rowcount or 0)


def collect_system_metric(db: Session, *, include_services: bool = True) -> SystemMetric:
    global _cpu_primed
    now = _utcnow()
    try:
        if not _cpu_primed:
            psutil.cpu_percent(interval=None)
            _cpu_primed = True
        cpu = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        load1 = None
        try:
            load1 = psutil.getloadavg()[0]
        except (AttributeError, OSError):
            load1 = None
        counters = psutil.net_io_counters()
        previous = _net_previous.get("global")
        mono = time.monotonic()
        rx_bps = tx_bps = None
        if previous and mono > previous[0]:
            elapsed = mono - previous[0]
            rx_bps = max(0.0, (counters.bytes_recv - previous[1]) / elapsed)
            tx_bps = max(0.0, (counters.bytes_sent - previous[2]) / elapsed)
        _net_previous["global"] = (mono, counters.bytes_recv, counters.bytes_sent)

        session_counts = dict(
            db.execute(
                select(Nat64Session.protocol, func.count(Nat64Session.id))
                .where(Nat64Session.status == "active")
                .group_by(Nat64Session.protocol)
            ).all()
        )
        totals = {
            "clients": db.scalar(
                select(func.count(IPv6Client.id)).where(IPv6Client.active_session_count > 0)
            )
            or 0,
            "sessions": sum(session_counts.values()),
            "tcp": session_counts.get("tcp", 0),
            "udp": session_counts.get("udp", 0),
            "icmp": session_counts.get("icmp", 0),
        }
        from app.services import ipv4

        pool_summary = ipv4.summary(db)
        jool_online = bool(
            db.scalar(select(func.count(JoolInstance.id)).where(JoolInstance.status == JoolStatus.ONLINE.value))
        )
        dns_state = db.get(Dns64Settings, 1)
        dns_online = bool(dns_state and dns_state.last_status == ServiceStatus.ONLINE.value)
        metric = SystemMetric(
            captured_at=now,
            cpu_percent=round(cpu, 2),
            memory_percent=round(memory.percent, 2),
            memory_used_bytes=memory.used,
            memory_total_bytes=memory.total,
            load1=round(load1, 3) if load1 is not None else None,
            net_rx_bps=round(rx_bps, 2) if rx_bps is not None else None,
            net_tx_bps=round(tx_bps, 2) if tx_bps is not None else None,
            clients_total=totals["clients"],
            sessions_total=totals["sessions"],
            tcp_sessions=totals["tcp"],
            udp_sessions=totals["udp"],
            icmp_sessions=totals["icmp"],
            pool_utilization_percent=pool_summary["utilization_percent"],
            jool_online=jool_online,
            dns_online=dns_online,
            detail={
                "net_bytes_recv": counters.bytes_recv,
                "net_bytes_sent": counters.bytes_sent,
                "pool": pool_summary,
                "host_mode": host.mode,
                "services_checked": include_services,
            },
        )
        db.add(metric)
        db.flush()
        _prune_metrics(db)
        return metric
    except Exception as exc:
        log.exception("System metric collection failed")
        metric = SystemMetric(
            captured_at=now,
            detail={"error": f"{type(exc).__name__}: {exc}", "host_mode": host.mode},
        )
        db.add(metric)
        db.flush()
        return metric


def _prune_metrics(db: Session) -> None:
    cutoff = _utcnow() - timedelta(days=max(1, settings.metrics_retention_days))
    db.execute(delete(SystemMetric).where(SystemMetric.captured_at < cutoff))


def latest_metric(db: Session) -> SystemMetric | None:
    return db.scalar(select(SystemMetric).order_by(SystemMetric.captured_at.desc()).limit(1))


def history(db: Session, minutes: int = 60, limit: int = 720) -> list[dict]:
    minutes = max(1, min(minutes, 60 * 24 * 30))
    limit = max(1, min(limit, 5000))
    cutoff = _utcnow() - timedelta(minutes=minutes)
    rows = db.scalars(
        select(SystemMetric)
        .where(SystemMetric.captured_at >= cutoff)
        .order_by(SystemMetric.captured_at.asc())
        .limit(limit)
    ).all()
    return [
        {
            "captured_at": row.captured_at,
            "cpu_percent": row.cpu_percent,
            "memory_percent": row.memory_percent,
            "net_rx_bps": row.net_rx_bps,
            "net_tx_bps": row.net_tx_bps,
            "clients_total": row.clients_total,
            "sessions_total": row.sessions_total,
            "tcp_sessions": row.tcp_sessions,
            "udp_sessions": row.udp_sessions,
            "icmp_sessions": row.icmp_sessions,
            "pool_utilization_percent": row.pool_utilization_percent,
        }
        for row in rows
    ]


def service_health(db: Session) -> dict:
    instances = db.scalars(select(JoolInstance)).all()
    jool_status = {
        "status": JoolStatus.UNSUPPORTED.value if not host.available else (
            JoolStatus.ONLINE.value
            if any(item.status in {JoolStatus.ONLINE.value, JoolStatus.PAUSED.value} for item in instances)
            else JoolStatus.OFFLINE.value
        ),
        "instances": [
            {
                "name": item.name,
                "status": item.status,
                "paused": item.paused,
                "pool6": item.pool6,
                "last_seen_at": item.last_seen_at,
                "last_error": item.last_error,
            }
            for item in instances
        ],
        "message": host.unavailable_reason() if not host.available else None,
        "capabilities": host.capabilities(),
    }
    dns_record = db.get(Dns64Settings, 1)
    if dns_record:
        dns = {
            "status": dns_record.last_status,
            "enabled": dns_record.enabled,
            "prefix": dns_record.prefix,
            "last_checked_at": dns_record.last_checked_at,
            "last_error": dns_record.last_error,
        }
    else:
        dns = {"status": "not_configured", "enabled": False}
    firewall_record = db.get(FirewallSettings, 1)
    firewall = {
        "status": (
            "disabled"
            if not firewall_record or not firewall_record.enabled
            else ServiceStatus.UNSUPPORTED.value
            if not host.available
            else "configured"
        ),
        "enabled": bool(firewall_record and firewall_record.enabled),
        "applied_at": firewall_record.applied_at if firewall_record else None,
        "last_error": firewall_record.last_error if firewall_record else None,
    }
    network_record = db.get(NetworkSettings, 1)
    network = {
        "status": ServiceStatus.UNSUPPORTED.value if not host.available else "configured",
        "pending_confirmation": bool(network_record and network_record.pending_confirmation),
        "last_applied_at": network_record.last_applied_at if network_record else None,
    }
    return {
        "host": {
            "mode": host.mode,
            "available": host.available,
            "reason": host.unavailable_reason(),
            "platform": host.capabilities().get("platform"),
        },
        "jool": jool_status,
        "dns64": dns,
        "firewall": firewall,
        "network": network,
        "database": {"dialect": db.get_bind().dialect.name if db.get_bind() else "unknown"},
        "collector": {"enabled": settings.collector_enabled, "interval_seconds": settings.collector_interval_seconds},
    }


def dashboard(db: Session) -> dict:
    from app.services import ipv4

    metric = latest_metric(db)
    session_counts = dict(
        db.execute(
            select(Nat64Session.protocol, func.count(Nat64Session.id))
            .where(Nat64Session.status == "active")
            .group_by(Nat64Session.protocol)
        ).all()
    )
    total_sessions = sum(session_counts.values())
    active_clients = db.scalar(
        select(func.count(IPv6Client.id)).where(IPv6Client.active_session_count > 0)
    ) or 0
    known_clients = db.scalar(select(func.count(IPv6Client.id))) or 0
    pool_summary = ipv4.summary(db)
    top_clients = [
        {
            "address": row.address,
            "mac_address": row.mac_address,
            "interface": row.interface,
            "active_session_count": row.active_session_count,
            "first_seen_at": row.first_seen_at,
            "last_seen_at": row.last_seen_at,
            "is_blocked": row.is_blocked,
        }
        for row in db.scalars(
            select(IPv6Client)
            .where(IPv6Client.active_session_count > 0)
            .order_by(IPv6Client.active_session_count.desc())
            .limit(10)
        ).all()
    ]
    from app.models import ChangeRequest

    pending_changes = db.scalar(
        select(func.count(ChangeRequest.id)).where(
            ChangeRequest.status.in_(["pending_approval", "approved"])
        )
    ) or 0
    return {
        "generated_at": _utcnow(),
        "services": service_health(db),
        "totals": {
            "sessions_active": total_sessions,
            "sessions_tcp": session_counts.get("tcp", 0),
            "sessions_udp": session_counts.get("udp", 0),
            "sessions_icmp": session_counts.get("icmp", 0),
            "clients_active": active_clients,
            "clients_known": known_clients,
            "pending_changes": pending_changes,
            "expired_retained": db.scalar(
                select(func.count(Nat64Session.id)).where(Nat64Session.status == "expired")
            )
            or 0,
        },
        "pool": pool_summary,
        "system": {
            "cpu_percent": metric.cpu_percent if metric else None,
            "memory_percent": metric.memory_percent if metric else None,
            "memory_used_bytes": metric.memory_used_bytes if metric else None,
            "memory_total_bytes": metric.memory_total_bytes if metric else None,
            "load1": metric.load1 if metric else None,
            "net_rx_bps": metric.net_rx_bps if metric else None,
            "net_tx_bps": metric.net_tx_bps if metric else None,
            "captured_at": metric.captured_at if metric else None,
        },
        "traffic": {
            "per_client_bytes": None,
            "note": "Jool does not expose per-client byte counters; upload/download totals are not available.",
        },
        "allocation": {
            "mode": "dynamic_per_connection",
            "note": (
                "Jool allocates an IPv4 transport address and port per connection. "
                "An IPv6 client is not pinned to one public IPv4 address."
            ),
            "utilization_basis": "observed_active_sessions",
        },
        "top_clients": top_clients,
        "limitations": [
            "Session first_seen_at is first observation by this manager, not Jool session creation time.",
            "Pool utilization counts observed active sessions, not exact BIB entries per IPv4 address.",
            "Per-client traffic counters are not provided by Jool.",
            "HA session continuity is experimental and not verified by automated tests.",
        ],
    }


def sessions_page(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 50,
    protocol: str | None = None,
    status: str | None = "active",
    client: str | None = None,
    translated: str | None = None,
    remote: str | None = None,
    instance: str | None = None,
    search: str | None = None,
    sort: str = "last_seen_at",
    order: str = "desc",
) -> dict:
    page = max(1, page)
    page_size = max(1, min(page_size, 500))
    conditions = []
    if protocol:
        conditions.append(Nat64Session.protocol == protocol)
    if status and status != "all":
        conditions.append(Nat64Session.status == status)
    if client:
        conditions.append(Nat64Session.client_ipv6.contains(client))
    if translated:
        conditions.append(Nat64Session.translated_ipv4 == translated)
    if remote:
        conditions.append(Nat64Session.remote_ipv4.contains(remote))
    if instance:
        conditions.append(Nat64Session.instance_id == int(instance))
    if search:
        like = f"%{search}%"
        from sqlalchemy import or_

        conditions.append(
            or_(
                Nat64Session.client_ipv6.ilike(like),
                Nat64Session.translated_ipv4.ilike(like),
                Nat64Session.remote_ipv4.ilike(like),
                Nat64Session.jool_state.ilike(like),
            )
        )
    sort_column = {
        "last_seen_at": Nat64Session.last_seen_at,
        "first_seen_at": Nat64Session.first_seen_at,
        "client_ipv6": Nat64Session.client_ipv6,
        "translated_ipv4": Nat64Session.translated_ipv4,
        "remote_ipv4": Nat64Session.remote_ipv4,
        "expires_in_seconds": Nat64Session.expires_in_seconds,
        "protocol": Nat64Session.protocol,
    }.get(sort, Nat64Session.last_seen_at)
    ordering = sort_column.asc() if order == "asc" else sort_column.desc()
    total = db.scalar(select(func.count(Nat64Session.id)).where(*conditions)) or 0
    rows = db.scalars(
        select(Nat64Session)
        .options(selectinload(Nat64Session.instance))
        .where(*conditions)
        .order_by(ordering)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [session_dict(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size if page_size else 0,
        "filters": {
            "protocol": protocol,
            "status": status,
            "client": client,
            "translated": translated,
            "remote": remote,
            "instance": instance,
            "search": search,
            "sort": sort,
            "order": order,
        },
        "traffic": {"bytes_up": None, "bytes_down": None, "note": "Not provided by Jool"},
    }


def session_dict(row: Nat64Session) -> dict:
    return {
        "id": row.id,
        "instance_id": row.instance_id,
        "instance_name": row.instance.name if row.instance else None,
        "protocol": row.protocol,
        "client_ipv6": row.client_ipv6,
        "client_port": row.client_port,
        "translated_ipv4": row.translated_ipv4,
        "translated_port": row.translated_port,
        "remote_ipv4": row.remote_ipv4,
        "remote_port": row.remote_port,
        "expires_in_seconds": row.expires_in_seconds,
        "jool_state": row.jool_state,
        "status": row.status,
        "first_seen_at": row.first_seen_at,
        "last_seen_at": row.last_seen_at,
        "expired_at": row.expired_at,
        "bytes_up": None,
        "bytes_down": None,
        "traffic_note": "Jool does not expose per-session byte counters",
    }
