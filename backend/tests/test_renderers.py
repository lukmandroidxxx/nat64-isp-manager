from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.core.errors import ValidationFailed
from app.models import FirewallRule, FirewallSettings, IPv4Pool, NetworkSettings
from app.services import dns64, firewall, ipv4, network


def _suffix() -> str:
    return uuid4().hex[:8]


def test_dns64_render_contains_forwarders_dns64_and_exclusions():
    content = dns64.render_config(
        enabled=True,
        prefix="64:ff9b::/96",
        upstreams=["1.1.1.1", "8.8.8.8", "1.1.1.1"],
        exclude_prefixes=["fe80::/10", "64:ff9b::/96"],
    )
    assert "forwarders {" in content
    assert "1.1.1.1;" in content
    assert content.count("1.1.1.1;") == 1
    assert "forward only;" in content
    assert "dnssec-validation auto;" in content
    assert "dns64 64:ff9b::/96 {" in content
    assert "fe80::/10;" in content
    assert content.count("64:ff9b::/96;") == 1
    assert dns64.content_hash(content) == dns64.content_hash(content)


def test_dns64_disabled_render_explains_behavior():
    content = dns64.render_config(
        enabled=False,
        prefix="64:ff9b::/96",
        upstreams=["9.9.9.9"],
        exclude_prefixes=[],
        forward_only=False,
        dnssec_validation=False,
    )
    assert "dns64 64:ff9b::/96" not in content
    assert "DNS64 disabled" in content
    assert "dnssec-validation no;" in content
    assert "forward only;" not in content


def test_dns64_validators():
    assert str(dns64.validate_ipv6_prefix("2001:db8::/96")) == "2001:db8::/96"
    with pytest.raises(ValidationFailed):
        dns64.validate_ipv6_prefix("64:ff9b::1/96")
    with pytest.raises(ValidationFailed):
        dns64.validate_ipv6_prefix("64:ff9b::/95")
    assert dns64.validate_upstreams(["1.1.1.1", " 1.1.1.1 ", "2606:4700:4700::1111"]) == [
        "1.1.1.1",
        "2606:4700:4700::1111",
    ]
    with pytest.raises(ValidationFailed):
        dns64.validate_upstreams([])
    with pytest.raises(ValidationFailed):
        dns64.validate_upstreams([f"10.0.0.{i}" for i in range(9)])


def test_firewall_cidr_port_and_rule_validation():
    assert firewall.validate_cidr("203.0.113.0/24", "ipv4") == "203.0.113.0/24"
    assert firewall.validate_cidr("2001:db8::/32", "ipv6") == "2001:db8::/32"
    with pytest.raises(ValidationFailed):
        firewall.validate_cidr("2001:db8::/32", "ipv4")
    with pytest.raises(ValidationFailed):
        firewall.validate_cidr("nope")
    with pytest.raises(ValidationFailed):
        firewall.validate_port(0, "port")
    with pytest.raises(ValidationFailed):
        firewall.validate_port(65536, "port")

    rule = FirewallRule(
        name="bad direction",
        direction="sideways",
        family="inet",
        action="drop",
    )
    with pytest.raises(ValidationFailed):
        firewall.validate_rule(rule)
    rule = FirewallRule(
        name="ports without protocol",
        direction="forward",
        family="inet",
        action="drop",
        source_port=1234,
    )
    with pytest.raises(ValidationFailed):
        firewall.validate_rule(rule)


def test_firewall_render_ruleset_and_control_plane_guard(db):
    settings = db.get(FirewallSettings, 1)
    assert settings is not None
    original = {
        "enabled": settings.enabled,
        "default_forward_policy": settings.default_forward_policy,
        "default_input_policy": settings.default_input_policy,
        "management_allowlist": list(settings.management_allowlist or []),
        "management_ports": list(settings.management_ports or []),
        "protect_control_plane": settings.protect_control_plane,
    }
    try:
        settings.enabled = True
        settings.default_forward_policy = "drop"
        settings.default_input_policy = "drop"
        settings.management_allowlist = ["203.0.113.0/24", "2001:db8:ffff::/48"]
        settings.management_ports = [22, 443]
        settings.protect_control_plane = True
        rule = FirewallRule(
            name=f"deny-{_suffix()}",
            enabled=True,
            priority=10,
            family="inet",
            direction="forward",
            action="drop",
            protocol="tcp",
            destination_cidr="192.0.2.0/24",
            destination_port=25,
        )
        db.add(rule)
        pool = IPv4Pool(name=f"fw-pool-{_suffix()}")
        db.add(pool)
        db.flush()
        ipv4.add_address(db, pool.id, "198.51.100.9", 61001, 65535)
        db.flush()

        content = firewall.render_ruleset(db, settings)
        assert f"table inet {firewall.TABLE} {{" in content
        assert "set pool4 {" in content
        assert "198.51.100.9/32" in content
        assert "ip saddr @mgmt4 tcp dport { 22, 443 } accept" in content
        assert "tcp dport 22 drop" in content
        assert "ct state established,related accept" in content
        assert "ip daddr @pool4 accept" in content
        assert "ip daddr 192.0.2.0/24 tcp dport 25 drop" in content
        assert "policy drop;" in content

        settings.management_allowlist = []
        with pytest.raises(ValidationFailed):
            firewall.render_ruleset(db, settings)
    finally:
        for key, value in original.items():
            setattr(settings, key, value)


def test_firewall_disabled_plan_deletes_only_managed_table(db):
    settings = db.get(FirewallSettings, 1)
    original = settings.enabled
    try:
        settings.enabled = False
        plan = firewall.build_plan(db, settings)
        assert f"table inet {firewall.TABLE}" in plan.content
        assert "deletes the managed nftables table" in plan.content
        assert plan.warnings
    finally:
        settings.enabled = original


def test_network_validators_and_render():
    record = NetworkSettings(
        id=1,
        wan4_interface="wan0",
        wan6_interface="wan0",
        lan6_interface="lan0",
        client_prefix="2001:db8:100::/64",
        nat64_prefix="64:ff9b::/96",
        default_gateway4="192.0.2.1",
        default_gateway6="2001:db8::1",
        mtu=1400,
        ipv4_forwarding=True,
        ipv6_forwarding=True,
        manage_routes=True,
        manage_mtu=True,
    )
    assert network.validate_interface_name("eth0.100") == "eth0.100"
    with pytest.raises(ValidationFailed):
        network.validate_interface_name("bad interface")
    with pytest.raises(ValidationFailed):
        network.validate_interface_name("a" * 65)
    assert network.validate_ipv4("192.0.2.9", "gw") == "192.0.2.9"
    with pytest.raises(ValidationFailed):
        network.validate_ipv4("2001:db8::1", "gw")
    assert network.validate_ipv6("2001:db8::1", "gw") == "2001:db8::1/128"

    warnings = network.validate_settings(record)
    assert isinstance(warnings, list)

    netplan = network.render_netplan(record)
    sysctl = network.render_sysctl(record)
    assert "version: 2" in netplan
    assert "wan0:" in netplan
    assert "mtu: 1400" in netplan
    assert "0.0.0.0/0" in netplan
    assert "::/0" in netplan
    assert "net.ipv4.ip_forward=1" in sysctl
    assert "net.ipv6.conf.all.forwarding=1" in sysctl
    assert "net.ipv4.conf.wan0.rp_filter=2" in sysctl
    assert network.content_hash(netplan, sysctl) == network.content_hash(netplan, sysctl)


def test_network_validation_rejects_bad_prefix_and_mtu():
    record = NetworkSettings(
        id=1,
        nat64_prefix="64:ff9b::/95",
        mtu=1500,
        ipv4_forwarding=True,
        ipv6_forwarding=True,
        manage_routes=False,
        manage_mtu=False,
    )
    with pytest.raises(ValidationFailed):
        network.validate_settings(record)
    record.nat64_prefix = "64:ff9b::/96"
    record.mtu = 100
    with pytest.raises(ValidationFailed):
        network.validate_settings(record)


def test_parse_ip_json():
    payload = json.dumps(
        [
            {
                "ifname": "wan0",
                "address": "aa:bb:cc:dd:ee:ff",
                "mtu": 1500,
                "operstate": "UP",
                "flags": ["BROADCAST", "UP"],
                "addr_info": [
                    {"family": "inet", "local": "192.0.2.9", "prefixlen": 24, "scope": "global"},
                    {"family": "inet6", "local": "2001:db8::9", "prefixlen": 64, "scope": "global"},
                ],
                "linkinfo": {"info_kind": "vlan"},
            },
            {"ifname": "lan0.100", "operstate": "DOWN", "addr_info": []},
        ]
    )
    interfaces = network.parse_ip_json(payload)
    assert interfaces[0]["name"] == "wan0"
    assert interfaces[0]["is_up"] is True
    assert interfaces[0]["addresses"][0]["address"] == "192.0.2.9/24"
    assert interfaces[1]["vlan_id"] == 100
    assert network.parse_ip_json("[]") == []
    with pytest.raises(ValidationFailed):
        network.parse_ip_json("{not json")


def test_parse_named_stats():
    content = """
++ Incoming Requests ++
    100 QUERY
     90 RESPONSE

++ Incoming Queries ++
     60 A
     35 AAAA

++ Resolver Statistics ++
      4 QueryFailure
     12 QueryCacheHit

++ Cache DB RRsets ++
     99 cache hits
"""
    stats = dns64.parse_named_stats(content)
    assert stats["total_queries"] == 100
    assert stats["a_queries"] == 60
    assert stats["aaaa_queries"] == 35
    assert stats["responses"] == 90
    assert stats["query_failures"] == 4
    assert stats["cache_hits"] == 12
    assert stats["source"] == "named.stats"
