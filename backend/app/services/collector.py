from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.services import monitoring

log = logging.getLogger(__name__)


class CollectorState:
    def __init__(self) -> None:
        self.started_at: datetime | None = None
        self.last_run_at: datetime | None = None
        self.last_success_at: datetime | None = None
        self.last_error: str | None = None
        self.runs = 0

    def as_dict(self) -> dict:
        return {
            "enabled": settings.collector_enabled,
            "interval_seconds": settings.collector_interval_seconds,
            "started_at": self.started_at,
            "last_run_at": self.last_run_at,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "runs": self.runs,
        }


state = CollectorState()


def collect_once(db: Session) -> dict:
    """Run one collection cycle and commit its own transaction."""
    state.last_run_at = datetime.now(UTC)
    state.runs += 1
    result: dict = {}
    try:
        result["sessions"] = monitoring.collect_sessions(db)
        metric = monitoring.collect_system_metric(db)
        result["metric_id"] = metric.id
        db.commit()
        state.last_success_at = datetime.now(UTC)
        state.last_error = None
    except Exception as exc:  # the collector must never kill the API process
        db.rollback()
        state.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        log.exception("collector cycle failed")
        result = {"error": state.last_error}
    return result


async def run_forever() -> None:
    state.started_at = datetime.now(UTC)
    interval = max(2, int(settings.collector_interval_seconds))
    while True:
        db = SessionLocal()
        try:
            await asyncio.to_thread(collect_once, db)
        finally:
            db.close()
        await asyncio.sleep(interval)
