from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.errors import ValidationFailed
from app.models import ConfigurationBackup, Dns64Settings, IPv4Pool, User
from app.services import backup


def _suffix() -> str:
    return uuid4().hex[:8]


def test_validate_backup_rules():
    with pytest.raises(ValidationFailed):
        backup.validate_backup([])  # type: ignore[arg-type]
    with pytest.raises(ValidationFailed):
        backup.validate_backup({"app": "something-else"})
    with pytest.raises(ValidationFailed):
        backup.validate_backup({"app": "nat64-isp-manager", "schema": 999})
    with pytest.raises(ValidationFailed):
        backup.validate_backup({"app": "nat64-isp-manager", "schema": 1, "ipv4_pools": {}})
    backup.validate_backup({"app": "nat64-isp-manager", "schema": 1, "ipv4_pools": []})


def test_create_backup_checksum_and_file(db):
    admin = db.scalar(select(User).where(User.username == "admin"))
    record = backup.create_backup(db, name=f"test-{_suffix()}", user=admin, note="unit test")
    assert record.checksum == hashlib.sha256(
        json.dumps(record.content, sort_keys=True, default=str).encode()
    ).hexdigest()
    assert record.size_bytes > 0
    assert record.created_by == admin.username
    assert record.file_path and record.file_path.endswith(".json")
    assert "users" in record.content["excluded"]
    listed = backup.list_backups(db)
    assert any(item["id"] == record.id for item in listed)


def test_restore_merge_recreates_deleted_configuration(db):
    admin = db.scalar(select(User).where(User.username == "admin"))
    name = f"restore-pool-{_suffix()}"
    pool = IPv4Pool(name=name)
    db.add(pool)
    db.flush()
    record = backup.create_backup(db, name=f"restore-{_suffix()}", user=admin)

    db.delete(pool)
    db.flush()
    assert db.scalar(select(IPv4Pool).where(IPv4Pool.name == name)) is None

    result = backup.restore_backup(db, record, user=admin, mode="merge")
    assert result["restored"] is True
    restored = db.scalar(select(IPv4Pool).where(IPv4Pool.name == name))
    assert restored is not None


def test_restore_replace_requires_superuser(db, operator_user):
    admin = db.scalar(select(User).where(User.username == "admin"))
    record = backup.create_backup(db, name=f"replace-{_suffix()}", user=admin)
    with pytest.raises(ValidationFailed):
        backup.restore_backup(db, record, user=operator_user, mode="replace")

    result = backup.restore_backup(db, record, user=admin, mode="replace")
    assert result["mode"] == "replace"
    assert db.get(Dns64Settings, 1) is not None


def test_restore_rejects_bad_mode(db):
    admin = db.scalar(select(User).where(User.username == "admin"))
    record = backup.create_backup(db, name=f"mode-{_suffix()}", user=admin)
    with pytest.raises(ValidationFailed):
        backup.restore_backup(db, record, user=admin, mode="overwrite")


def test_backup_record_roundtrip_through_db(db):
    admin = db.scalar(select(User).where(User.username == "admin"))
    created = backup.create_backup(db, name=f"roundtrip-{_suffix()}", user=admin)
    db.flush()
    fetched = db.get(ConfigurationBackup, created.id)
    assert fetched is not None
    assert fetched.checksum == created.checksum
