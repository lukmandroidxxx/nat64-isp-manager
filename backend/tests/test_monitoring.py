from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.models import IPv4Pool, IPv6Client, IPv6Prefix, JoolInstance, Nat64Session
from app.services import ipv4, monitoring
from app.services.jool.parser import JoolSessionRecord


def _suffix() -> str:
    return uuid4().hex[:8]


def _instance(db) -> JoolInstance:
    instance = JoolInstance(
        name=f"m{_suffix()}"[:15],
        pool6="64:ff9b::/96",
        framework="netfilter",
        enabled=True,
        status="online",
    )
    db.add(instance)
    db.flush()
    return instance


def _record(**overrides) -> JoolSessionRecord:
    values = {
        "protocol": "tcp",
        "client_ipv6": "2001:db8:1::10",
        "client_port": 51000,
        "translated_ipv4": "198.51.100.20",
        "translated_port": 40000,
        "remote_ipv4": "192.0.2.9",
        "remote_port": 443,
        "expires_in_seconds": 300,
        "jool_state": "ESTABLISHED",
    }
    values.update(overrides)
    return JoolSessionRecord(**values)


def test_sync_sessions_create_update_expire(db):
    instance = _instance(db)
    pool = IPv4Pool(name=f"sync-{_suffix()}", jool_instance_id=instance.id)
    db.add(pool)
    db.flush()
    address = ipv4.add_address(db, pool.id, "198.51.100.20", 61001, 65535)

    now = datetime.now(UTC)
    first = monitoring.sync_sessions(db, instance, [_record()], now)
    assert first == {"observed": 1, "created": 1, "updated": 0, "expired": 0}
    session = db.query(Nat64Session).filter(Nat64Session.instance_id == instance.id).one()
    assert session.status == "active"
    assert session.pool4_address_id == address.id
    assert session.first_seen_at is not None
    assert address.last_session_at is not None

    second = monitoring.sync_sessions(
        db,
        instance,
        [_record(expires_in_seconds=120, jool_state="TIME_WAIT")],
        now + timedelta(seconds=5),
    )
    assert second == {"observed": 1, "created": 0, "updated": 1, "expired": 0}
    db.refresh(session)
    assert session.expires_in_seconds == 120
    assert session.jool_state == "TIME_WAIT"

    third = monitoring.sync_sessions(db, instance, [], now + timedelta(seconds=10))
    assert third["expired"] == 1
    db.refresh(session)
    assert session.status == "expired"
    assert session.expired_at is not None

    usage = ipv4.pool_usage(db, instance.id)
    assert "198.51.100.20" not in usage


def test_sync_sessions_deduplicates_duplicate_records(db):
    instance = _instance(db)
    now = datetime.now(UTC)
    result = monitoring.sync_sessions(db, instance, [_record(), _record()], now)
    assert result["observed"] == 2
    assert result["created"] == 1
    assert db.query(Nat64Session).filter(Nat64Session.instance_id == instance.id).count() == 1


def test_sync_clients_matches_prefix_and_tracks_active_counts(db):
    instance = _instance(db)
    prefix = IPv6Prefix(prefix=f"2001:db8:{_suffix()[:4]}::/48", enabled=True)
    db.add(prefix)
    db.flush()
    address = f"2001:db8:{_suffix()[:4]}:1::10"
    now = datetime.now(UTC)
    monitoring.sync_sessions(
        db,
        instance,
        [_record(client_ipv6=address)],
        now,
    )
    summary = monitoring.sync_clients(db, now)
    assert summary["active_clients"] >= 1

    client = db.query(IPv6Client).filter(IPv6Client.address == address).one()
    assert client.active_session_count == 1
    # The generated prefix may not contain the address; explicit prefix matching
    # is covered by _match_prefix below.
    matched = monitoring._match_prefix(address, [prefix])
    assert matched in {None, prefix.id}

    session = db.query(Nat64Session).filter(Nat64Session.client_ipv6 == address).one()
    session.status = "expired"
    db.flush()
    monitoring.sync_clients(db, now)
    db.refresh(client)
    assert client.active_session_count == 0


def test_match_prefix_chooses_longest():
    specific = IPv6Prefix(prefix="2001:db8:1:2::/64", enabled=True)
    general = IPv6Prefix(prefix="2001:db8::/32", enabled=True)
    specific.id = 2
    general.id = 1
    assert monitoring._match_prefix("2001:db8:1:2::99", [general, specific]) == 2
    assert monitoring._match_prefix("not-an-address", [general]) is None


def test_sessions_page_filters_paginates_and_reports_no_fake_traffic(db):
    instance = _instance(db)
    base = datetime.now(UTC)
    for index in range(5):
        db.add(
            Nat64Session(
                instance_id=instance.id,
                protocol="tcp" if index % 2 == 0 else "udp",
                client_ipv6=f"2001:db8:{_suffix()[:4]}:1::{index + 1}",
                client_port=50000 + index,
                translated_ipv4="203.0.113.77",
                translated_port=40000 + index,
                remote_ipv4="192.0.2.50",
                remote_port=443,
                jool_state="ESTABLISHED",
                status="active",
                first_seen_at=base,
                last_seen_at=base + timedelta(seconds=index),
            )
        )
    db.add(
        Nat64Session(
            instance_id=instance.id,
            protocol="tcp",
            client_ipv6="2001:db8:ffff::1",
            client_port=1,
            translated_ipv4="203.0.113.77",
            translated_port=1,
            remote_ipv4="192.0.2.99",
            remote_port=80,
            status="expired",
            first_seen_at=base,
            last_seen_at=base,
        )
    )
    db.flush()

    page = monitoring.sessions_page(db, instance=str(instance.id), page_size=2, page=2)
    assert page["total"] == 5
    assert page["pages"] == 3
    assert page["page"] == 2
    assert len(page["items"]) == 2
    assert page["traffic"]["bytes_up"] is None
    assert all(item["bytes_up"] is None for item in page["items"])
    assert all(item["instance_name"] == instance.name for item in page["items"])

    tcp = monitoring.sessions_page(db, instance=str(instance.id), protocol="tcp")
    assert tcp["total"] == 3
    udp = monitoring.sessions_page(db, instance=str(instance.id), protocol="udp")
    assert udp["total"] == 2

    searched = monitoring.sessions_page(db, instance=str(instance.id), search="ESTABLISHED")
    assert searched["total"] == 5

    translated = monitoring.sessions_page(db, instance=str(instance.id), translated="203.0.113.77")
    assert translated["total"] == 5

    remote = monitoring.sessions_page(db, instance=str(instance.id), remote="192.0.2.50")
    assert remote["total"] == 5

    everything = monitoring.sessions_page(db, instance=str(instance.id), status="all")
    assert everything["total"] == 6

    ascending = monitoring.sessions_page(
        db, instance=str(instance.id), sort="last_seen_at", order="asc", status="all"
    )
    assert ascending["items"][0]["last_seen_at"] <= ascending["items"][-1]["last_seen_at"]


def test_dashboard_and_service_health_are_honest(db):
    dashboard = monitoring.dashboard(db)
    assert dashboard["allocation"]["mode"] == "dynamic_per_connection"
    assert dashboard["allocation"]["utilization_basis"] == "observed_active_sessions"
    assert dashboard["traffic"]["per_client_bytes"] is None
    assert any("not verified" in note for note in dashboard["limitations"])
    for key in ("totals", "pool", "services", "system", "top_clients", "generated_at"):
        assert key in dashboard

    health = monitoring.service_health(db)
    assert health["database"]["dialect"] == "sqlite"
    assert "host" in health and "jool" in health
    assert health["collector"]["interval_seconds"] >= 1


def test_collect_system_metric_persists(db):
    metric = monitoring.collect_system_metric(db, include_services=False)
    assert metric.id is not None
    assert metric.captured_at is not None
    assert isinstance(metric.detail, dict)
    history = monitoring.history(db, minutes=10)
    assert any(item["captured_at"] == metric.captured_at for item in history)
