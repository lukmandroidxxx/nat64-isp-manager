from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def _prepare_sqlite(url: str) -> None:
    if url.startswith("sqlite:///"):
        raw = url.removeprefix("sqlite:///")
        path = Path(raw)
        if not path.is_absolute():
            path = settings.backend_dir / path
        path.parent.mkdir(parents=True, exist_ok=True)


_prepare_sqlite(settings.database_url)

connect_args = {"check_same_thread": False} if settings.is_sqlite else {}
engine = create_engine(
    settings.database_url,
    echo=settings.database_echo,
    pool_pre_ping=True,
    connect_args=connect_args,
    future=True,
)


if settings.is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db(request: Request) -> Generator[Session, None, None]:
    db = SessionLocal()
    # Exposed so the API route can commit before the response is sent instead
    # of relying on this generator's post-response cleanup.
    request.state.db = db
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
