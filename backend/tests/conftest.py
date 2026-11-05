"""Shared pytest fixtures.

Environment is configured *before* importing the application because settings
and the SQLAlchemy engine are module-level singletons.
"""
from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="nat64mgr-tests-"))
_DB = _TMP / "test.db"

os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{_DB.as_posix()}",
        "SECRET_KEY": "test-secret-key-with-at-least-32-characters-1234567890",
        "DEFAULT_ADMIN_PASSWORD": "AdminPassw0rd!xyz",
        "COLLECTOR_ENABLED": "false",
        "RATE_LIMIT_ENABLED": "false",
        "LOGIN_RATE_LIMIT_PER_MINUTE": "1000",
        "RATE_LIMIT_PER_MINUTE": "10000",
        "BACKUP_DIR": str(_TMP / "backups"),
        "HOST_CONTROL_MODE": "auto",
        "ENVIRONMENT": "development",
        "ENABLE_DOCS": "true",
    }
)

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.core.security import hash_password
from app.main import app
from app.models import Base, Role, User
from app.models.enums import RoleName
from app.services import auth as auth_service

ADMIN_USERNAME = settings.default_admin_username
ADMIN_PASSWORD = "AdminPassw0rd!xyz"
API = settings.api_prefix


@pytest.fixture(scope="session", autouse=True)
def _database_schema():
    """Create the schema and bootstrap roles for tests that do not start the app.

    ``client`` starts the real lifespan (which does the same work idempotently),
    but service-level tests only use the ``db`` fixture.  Without this fixture
    those tests would run against an empty SQLite file.
    """
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        auth_service.bootstrap(session)
        session.commit()
    finally:
        session.close()
    yield


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def admin_tokens(client) -> dict:
    response = client.post(
        f"{API}/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(scope="session")
def auth_headers(admin_tokens) -> dict:
    return {"Authorization": f"Bearer {admin_tokens['access_token']}"}


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def make_user(
    db,
    username: str,
    *,
    role: str = RoleName.VIEWER.value,
    password: str = "UserPassw0rd!xyz",
    is_active: bool = True,
) -> User:
    role_record = db.query(Role).filter(Role.name == role).one()
    user = User(
        username=username,
        email=f"{username}@example.invalid",
        password_hash=hash_password(password),
        role_id=role_record.id,
        is_active=is_active,
        is_superuser=role == RoleName.SUPERADMIN.value,
        password_changed_at=datetime.now(UTC),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def viewer_user(db):
    return make_user(db, f"viewer_{uuid4().hex[:10]}", role=RoleName.VIEWER.value)


@pytest.fixture()
def operator_user(db):
    return make_user(db, f"operator_{uuid4().hex[:10]}", role=RoleName.OPERATOR.value)


@pytest.fixture()
def admin_user(db):
    return make_user(db, f"admin_{uuid4().hex[:10]}", role=RoleName.ADMIN.value)


def login(client, username: str, password: str):
    return client.post(f"{API}/auth/login", json={"username": username, "password": password})


def headers_for(client, username: str, password: str) -> dict:
    response = login(client, username, password)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
