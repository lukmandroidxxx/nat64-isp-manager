from __future__ import annotations

import pytest

from app.services.jool.parser import (
    JoolParseError,
    parse_boolean,
    parse_duration,
    parse_global_display,
    parse_instance_table,
    parse_pool4_csv,
    parse_session_csv,
    parse_stats_csv,
    parse_version,
    version_tuple_str,
)

POOL4_CSV = """Address,Ports,Mark,Max Iterations
203.0.113.10,61001-65535,0,
198.51.100.20,1024-2047,7,1024
"""

SESSION_CSV = """Protocol,IPv6 Remote Address,IPv6 Remote L4 ID,IPv4 Local Address,IPv4 Local L4 ID,IPv4 Remote Address,IPv4 Remote L4 ID,Expires In,State
tcp,2001:db8:1::10,51000,203.0.113.10,40001,192.0.2.9,443,00:05:00,ESTABLISHED
udp,2001:db8:1::11,51001,203.0.113.10,40002,192.0.2.10,53,00:01:30,
icmp,2001:db8:1::12,,203.0.113.10,,192.0.2.11,,,
"""


def test_parse_version_and_tuple():
    assert parse_version("Jool 4.1.13 (2024-01-01)") == (4, 1, 13)
    assert parse_version("no version here") is None
    assert version_tuple_str((4, 1, 13)) == "4.1.13"
    assert version_tuple_str(None) is None


def test_parse_duration_formats():
    assert parse_duration("00:05:00") == 300
    assert parse_duration("05:30") == 330
    assert parse_duration("12.5") == 12
    assert parse_duration("1 day 02:00:00") == 93600
    assert parse_duration("-") is None
    assert parse_duration(None) is None


def test_parse_instance_table_ignores_headers_and_separators():
    output = """
+-----------+--------+-----------+
| Namespace | Name   | Framework |
+-----------+--------+-----------+
| (root)    | nat64  | netfilter |
| abc123    | other  | iptables  |
+-----------+--------+-----------+
"""
    instances = parse_instance_table(output)
    assert [(item.name, item.framework) for item in instances] == [
        ("nat64", "netfilter"),
        ("other", "iptables"),
    ]


def test_parse_pool4_csv():
    entries = parse_pool4_csv(POOL4_CSV, "tcp")
    assert len(entries) == 2
    first, second = entries
    assert first.address == "203.0.113.10"
    assert (first.port_min, first.port_max) == (61001, 65535)
    assert first.capacity == 4535
    assert second.mark == 7
    assert second.max_iterations == "1024"
    assert all(entry.protocol == "tcp" for entry in entries)


def test_parse_pool4_headerless_fallback():
    output = "203.0.113.10 61001-65535\n"
    entries = parse_pool4_csv(output, "udp")
    assert entries and entries[0].protocol == "udp"


def test_parse_pool4_invalid_range_is_skipped():
    output = "Address,Ports\n203.0.113.10,not-a-range\n198.51.100.4,10-20\n"
    entries = parse_pool4_csv(output, "tcp")
    assert [entry.address for entry in entries] == ["198.51.100.4"]


def test_parse_pool4_rejects_inverted_range():
    with pytest.raises(JoolParseError):
        from app.services.jool.parser import _pool4_entry

        _pool4_entry("203.0.113.10", "100-50", "tcp", 0, None)


def test_parse_session_csv():
    records, warnings = parse_session_csv(SESSION_CSV, "tcp")
    assert warnings == []
    assert len(records) == 3
    tcp = records[0]
    assert tcp.protocol == "tcp"
    assert tcp.client_ipv6 == "2001:db8:1::10"
    assert tcp.translated_ipv4 == "203.0.113.10"
    assert tcp.expires_in_seconds == 300
    assert tcp.jool_state == "ESTABLISHED"
    assert records[1].protocol == "udp"
    assert records[2].protocol == "icmp"
    assert records[2].client_port is None


def test_parse_session_csv_header_variants():
    output = "protocol,source ipv6,ipv6 l4 id,translated ipv4,ipv4 local port,destination ipv4,destination port\n"
    output += "tcp,2001:db8::1,1,203.0.113.1,2,192.0.2.1,3\n"
    records, warnings = parse_session_csv(output, "tcp")
    assert warnings == []
    assert records[0].translated_port == 2


def test_parse_session_csv_unknown_header():
    records, warnings = parse_session_csv("foo,bar\n1,2\n", "tcp")
    assert records == []
    assert warnings and "Unrecognized" in warnings[0]


def test_parse_session_csv_bad_rows_are_reported():
    output = "Protocol,IPv6 Remote Address,IPv6 Remote L4 ID,IPv4 Local Address,IPv4 Local L4 ID,IPv4 Remote Address,IPv4 Remote L4 ID\n"
    output += "tcp,2001:db8::1,1\n"
    output += "sctp,2001:db8::2,2,203.0.113.2,3,192.0.2.2,4\n"
    records, warnings = parse_session_csv(output, "tcp")
    assert records == []
    assert any("too few columns" in warning for warning in warnings)
    assert any("unknown protocol" in warning for warning in warnings)


def test_parse_stats_csv_table_and_fallback():
    table = "Stat,Value\nJSTAT_TOTAL=1,42\nJSTAT_BIB=3,7\n"
    assert parse_stats_csv(table) == {"JSTAT_TOTAL=1": 42, "JSTAT_BIB=3": 7}
    fallback = "JSTAT_TOTAL: 12\nJSTAT_BIB=3\n"
    assert parse_stats_csv(fallback) == {"JSTAT_TOTAL": 12, "JSTAT_BIB": 3}
    assert parse_stats_csv("") == {}


def test_parse_global_display_and_booleans():
    output = "manually-enabled: true\nudp-timeout: 5m\nnot a key: value\n"
    assert parse_global_display(output) == {"manually-enabled": "true", "udp-timeout": "5m"}
    assert parse_boolean("true") is True
    assert parse_boolean("0") is False
    assert parse_boolean(None, default=False) is False
