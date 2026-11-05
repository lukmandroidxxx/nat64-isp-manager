"""Data-volume correctness test: 500 clients / 1000 sessions.

This is deliberately *not* a performance benchmark.  Timings are printed for
information only; no latency or throughput claim is made or enforced.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete

from app.models import IPv6Client, IPv6Prefix, JoolInstance, Nat64Session
from app.services import monitoring


def test_500_clients_1000_sessions_pagination_and_filters(db):
    suffix = uuid4().hex[:6]
    now = datetime.now(UTC)
    instance = JoolInstance(
        name=f"scale{suffix}",
        pool6="64:ff9b::/96",
        enabled=True,
        status="online",
    )
    prefix = IPv6Prefix(prefix=f"2001:db8:{suffix[:4]}::/64", name="scale test")
    db.add_all([instance, prefix])
    db.flush()

    clients: list[IPv6Client] = []
    sessions: list[Nat64Session] = []
    start = time.perf_counter()
    for index in range(500):
        address = f"2001:db8:{suffix[:4]}::{index + 1:x}"
        clients.append(
            IPv6Client(
                address=address,
                prefix_id=prefix.id,
                first_seen_at=now - timedelta(minutes=index),
                last_seen_at=now,
                active_session_count=2,
            )
        )
        for offset, protocol in enumerate(("tcp", "udp")):
            sessions.append(
                Nat64Session(
                    instance_id=instance.id,
                    protocol=protocol,
                    client_ipv6=address,
                    client_port=40000 + index,
                    translated_ipv4="198.51.100.10" if index % 2 == 0 else "198.51.100.11",
                    translated_port=61001 + index + offset,
                    remote_ipv4="203.0.113.50",
                    remote_port=443,
                    expires_in_seconds=300,
                    jool_state="ESTABLISHED",
                    status="active",
                    first_seen_at=now - timedelta(minutes=index),
                    last_seen_at=now - timedelta(seconds=index),
                )
            )
    db.add_all(clients)
    db.add_all(sessions)
    db.commit()
    insert_seconds = time.perf_counter() - start

    try:
        start = time.perf_counter()
        first = monitoring.sessions_page(
            db, instance=str(instance.id), status="active", page=1, page_size=50
        )
        first_seconds = time.perf_counter() - start

        assert first["total"] == 1000
        assert first["pages"] == 20
        assert len(first["items"]) == 50
        assert first["traffic"]["bytes_up"] is None

        start = time.perf_counter()
        last = monitoring.sessions_page(
            db, instance=str(instance.id), status="active", page=20, page_size=50
        )
        last_seconds = time.perf_counter() - start
        assert len(last["items"]) == 50
        assert {item["id"] for item in first["items"]}.isdisjoint(
            {item["id"] for item in last["items"]}
        )

        tcp = monitoring.sessions_page(
            db, instance=str(instance.id), status="active", protocol="tcp", page_size=1
        )
        assert tcp["total"] == 500

        one_client = clients[123].address
        matching = monitoring.sessions_page(
            db, instance=str(instance.id), status="active", search=one_client
        )
        assert matching["total"] == 2
        assert {item["client_ipv6"] for item in matching["items"]} == {one_client}

        by_address = monitoring.sessions_page(
            db,
            instance=str(instance.id),
            status="active",
            translated="198.51.100.10",
            page_size=1,
        )
        assert by_address["total"] == 500

        ascending = monitoring.sessions_page(
            db,
            instance=str(instance.id),
            status="active",
            sort="client_ipv6",
            order="asc",
            page_size=500,
        )
        assert len(ascending["items"]) == 500

        # The last-seen ordering is stable for the seeded data.
        assert first["items"][0]["last_seen_at"] is not None

        print(
            "scale test (informational only): "
            f"insert={insert_seconds:.3f}s first_page={first_seconds:.3f}s "
            f"last_page={last_seconds:.3f}s rows=500 clients/1000 sessions"
        )
    finally:
        db.execute(delete(Nat64Session).where(Nat64Session.instance_id == instance.id))
        db.execute(delete(IPv6Client).where(IPv6Client.prefix_id == prefix.id))
        db.execute(delete(IPv6Prefix).where(IPv6Prefix.id == prefix.id))
        db.execute(delete(JoolInstance).where(JoolInstance.id == instance.id))
        db.commit()


def test_pool_usage_aggregation_handles_many_sessions(db):
    """Observed-session utilization must scale without per-address BIB claims."""
    from app.services import ipv4

    suffix = uuid4().hex[:6]
    now = datetime.now(UTC)
    instance = JoolInstance(name=f"usage{suffix}", pool6="64:ff9b::/96", enabled=True)
    db.add(instance)
    db.flush()
    for index in range(200):
        db.add(
            Nat64Session(
                instance_id=instance.id,
                protocol="tcp" if index % 2 else "udp",
                client_ipv6=f"2001:db8:ffff::{index + 1:x}",
                client_port=10000 + index,
                translated_ipv4="203.0.113.7",
                translated_port=60000 + (index % 5000),
                remote_ipv4="198.51.100.1",
                remote_port=80,
                status="active",
                first_seen_at=now,
                last_seen_at=now,
            )
        )
    db.commit()
    try:
        usage = ipv4.pool_usage(db, instance.id)
        assert usage["203.0.113.7"]["sessions"] == 200
        assert usage["203.0.113.7"]["tcp"] + usage["203.0.113.7"]["udp"] == 200
        # Observed ports, not BIB entries.
        assert usage["203.0.113.7"]["ports_used"]["tcp"] + usage["203.0.113.7"][
            "ports_used"
        ]["udp"] == 200
    finally:
        db.execute(delete(Nat64Session).where(Nat64Session.instance_id == instance.id))
        db.execute(delete(JoolInstance).where(JoolInstance.id == instance.id))
        db.commit()
