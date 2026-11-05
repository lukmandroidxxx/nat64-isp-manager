from __future__ import annotations

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_permission
from app.core.routing import APIRouter
from app.models import User
from app.services import collector, monitoring
from app.services.host import HostError, host

router = APIRouter(tags=["dashboard"])

READ = require_permission("read")
OPERATE = require_permission("changes.request")
ALLOWED_UNITS = {
    "nat64mgr",
    "nat64-manager",
    "nat64mgr-api",
    "named",
    "bind9",
    "nftables",
    "systemd-networkd",
    "keepalived",
    "jool",
}


@router.get("/dashboard")
def dashboard(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    return monitoring.dashboard(db)


@router.get("/system/health")
def system_health(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    return monitoring.service_health(db)


@router.get("/system/capabilities")
def capabilities(_: User = Depends(READ)) -> dict:
    return host.capabilities(force=True)


@router.get("/system/collector")
def collector_status(_: User = Depends(READ)) -> dict:
    return collector.state.as_dict()


@router.post("/system/collect")
def collect_now(_: User = Depends(OPERATE), db: Session = Depends(get_db)) -> dict:
    return {"result": collector.collect_once(db), "collector": collector.state.as_dict()}


@router.get("/system/metrics/latest")
def latest_metric(_: User = Depends(READ), db: Session = Depends(get_db)) -> dict:
    metric = monitoring.latest_metric(db)
    if not metric:
        return {"metric": None, "message": "No metrics collected yet"}
    return {
        "metric": {
            "id": metric.id,
            "captured_at": metric.captured_at,
            "cpu_percent": metric.cpu_percent,
            "memory_percent": metric.memory_percent,
            "memory_used_bytes": metric.memory_used_bytes,
            "memory_total_bytes": metric.memory_total_bytes,
            "load1": metric.load1,
            "net_rx_bps": metric.net_rx_bps,
            "net_tx_bps": metric.net_tx_bps,
            "clients_total": metric.clients_total,
            "sessions_total": metric.sessions_total,
            "tcp_sessions": metric.tcp_sessions,
            "udp_sessions": metric.udp_sessions,
            "icmp_sessions": metric.icmp_sessions,
            "pool_utilization_percent": metric.pool_utilization_percent,
            "jool_online": metric.jool_online,
            "dns_online": metric.dns_online,
            "detail": metric.detail,
        }
    }


@router.get("/system/metrics/history")
def metric_history(
    minutes: int = Query(default=60, ge=1, le=43200),
    limit: int = Query(default=720, ge=1, le=5000),
    _: User = Depends(READ),
    db: Session = Depends(get_db),
) -> dict:
    return {"items": monitoring.history(db, minutes=minutes, limit=limit)}


@router.get("/system/logs")
def system_logs(
    unit: str = Query(default="nat64mgr-api", max_length=32),
    limit: int = Query(default=300, ge=1, le=2000),
    _: User = Depends(READ),
) -> dict:
    if unit not in ALLOWED_UNITS:
        return {"status": "invalid_unit", "allowed": sorted(ALLOWED_UNITS), "lines": []}
    if not host.available:
        return {
            "status": "unsupported",
            "message": host.unavailable_reason(),
            "unit": unit,
            "lines": [],
        }
    try:
        result = host.system("journal", unit=unit, limit=limit)
        return {
            "status": "ok" if result.ok else "error",
            "unit": unit,
            "lines": result.data.get("lines") or result.stdout.splitlines()[-limit:],
            "message": result.message or None,
        }
    except HostError as exc:
        return {"status": "error", "unit": unit, "lines": [], "message": exc.message}


@router.get("/system/listen-ports")
def listen_ports(_: User = Depends(READ)) -> dict:
    if not host.available:
        return {"status": "unsupported", "message": host.unavailable_reason(), "lines": []}
    try:
        result = host.system("listen_ports")
        return {
            "status": "ok" if result.ok else "error",
            "lines": result.data.get("lines") or result.stdout.splitlines(),
            "message": result.message or None,
        }
    except HostError as exc:
        return {"status": "error", "lines": [], "message": exc.message}
