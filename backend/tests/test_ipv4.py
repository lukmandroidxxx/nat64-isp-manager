from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.errors import Conflict, ValidationFailed
from app.models import IPv4Address, IPv4Pool, JoolInstance, Nat64Session, User
from app.services import ipv4


def _suffix() -> str:
    return uuid4().hex[:10]


def _admin(db) -> User:
    return db.scalar(select(User).where(User.username == "admin"))


def _pool(db, *, enabled: bool = True) -> IPv4Pool:
    pool = IPv4Pool(name=f"test-pool-{_suffix()}", enabled=enabled)
    db.add(pool)
    db.flush()
    return pool


def _instance(db) -> JoolInstance:
    instance = JoolInstance(
        name=f"t{_suffix()}"[:15],
        pool6="64:ff9b::/96",
        framework="netfilter",
        enabled=True,
        status="online",
    )
    db.add(instance)
    db.flush()
    return instance


def test_canonical_ipv4():
    assert ipv4.canonical_ipv4("203.0.113.10/32") == "203.0.113.10"
    assert ipv4.canonical_ipv4(" 203.0.113.10 ") == "203.0.113.10"
    with pytest.raises(ValidationFailed):
        ipv4.canonical_ipv4("203.0.113.0/24")
    with pytest.raises(ValidationFailed):
        ipv4.canonical_ipv4("2001:db8::1")
    with pytest.raises(ValidationFailed):
        ipv4.canonical_ipv4("not-an-ip")


def test_parse_ipv4_import_dedupes_and_expands_cidr():
    parsed = ipv4.parse_ipv4_import("203.0.113.10, 203.0.113.10\n198.51.100.0/30; 203.0.113.11")
    assert parsed[:2] == ["203.0.113.10", "198.51.100.0"]
    assert "198.51.100.3" in parsed
    assert parsed.count("203.0.113.10") == 1
    assert "203.0.113.11" in parsed
    with pytest.raises(ValidationFailed):
        ipv4.parse_ipv4_import("")
    with pytest.raises(ValidationFailed):
        ipv4.parse_ipv4_import("2001:db8::1")
    with pytest.raises(ValidationFailed):
        ipv4.parse_ipv4_import("10.0.0.0/8", limit=16)


def test_add_address_validates_overlap_and_duplicates(db):
    pool = _pool(db)
    admin = _admin(db)
    first = ipv4.add_address(db, pool.id, "203.0.113.10", 61001, 65535, user=admin)
    assert first.address == "203.0.113.10"
    assert first.status == "pending"

    second = ipv4.add_address(db, pool.id, "203.0.113.10", 1000, 2000, user=admin)
    assert second.port_max == 2000

    with pytest.raises(Conflict):
        ipv4.add_address(db, pool.id, "203.0.113.10", 1500, 2500, user=admin)
    with pytest.raises(Conflict):
        ipv4.add_address(db, pool.id, "203.0.113.10", 61001, 65535, user=admin)
    with pytest.raises(ValidationFailed):
        ipv4.add_address(db, pool.id, "203.0.113.10", 0, 100, user=admin)
    with pytest.raises(ValidationFailed):
        ipv4.add_address(db, pool.id, "203.0.113.0/24", 100, 200, user=admin)


def test_update_and_delete_address(db):
    pool = _pool(db)
    record = ipv4.add_address(db, pool.id, "198.51.100.7", 30000, 40000)
    updated = ipv4.update_address(db, record.id, enabled=False, label="spare")
    assert updated.enabled is False
    assert updated.label == "spare"
    assert updated.status == "pending"

    ipv4.delete_address(db, record.id)
    assert db.get(IPv4Address, record.id) is None


def test_pool_usage_and_enrichment_are_observed_session_based(db):
    instance = _instance(db)
    pool = _pool(db)
    pool.jool_instance_id = instance.id
    address = ipv4.add_address(db, pool.id, "203.0.113.50", 61001, 65535)
    db.add(
        Nat64Session(
            instance_id=instance.id,
            protocol="tcp",
            client_ipv6="2001:db8:1::10",
            client_port=51000,
            translated_ipv4="203.0.113.50",
            translated_port=40000,
            remote_ipv4="192.0.2.9",
            remote_port=443,
            status="active",
            first_seen_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
    )
    db.flush()

    usage = ipv4.pool_usage(db, instance.id)
    bucket = usage["203.0.113.50"]
    assert bucket["sessions"] == 1
    assert bucket["bindings"] == 1
    assert bucket["tcp"] == 1
    assert bucket["ports_used"]["tcp"] == 1

    enriched = ipv4.enrich_address_stats(db, [address], instance.id)[0]
    assert enriched["usage_basis"] == "observed_active_sessions"
    assert enriched["sessions"] == 1
    assert enriched["ports_used"] == 1
    assert enriched["ports_total"] == (65535 - 61001 + 1) * 3
    assert enriched["usage_percent"] > 0

    listed = next(item for item in ipv4.list_pools(db) if item["id"] == pool.id)
    assert listed["address_count"] == 1
    assert listed["sessions_total"] == 1
    assert listed["usage_percent"] > 0


def test_summary_shape_and_capacity(db):
    summary = ipv4.summary(db)
    assert summary["usage_basis"] == "observed_active_sessions"
    for key in (
        "pool_count",
        "address_count",
        "enabled_address_count",
        "active_address_count",
        "transport_capacity",
        "observed_ports_used",
        "utilization_percent",
    ):
        assert key in summary
    assert summary["default_port_range"][0] <= summary["default_port_range"][1]


def test_build_sync_plan_reports_unavailable_host_instead_of_fake_state(db):
    instance = _instance(db)
    plan = ipv4.build_sync_plan(db, instance)
    assert plan.valid is False
    assert plan.host_available is False
    assert plan.adds == []
    assert plan.errors
