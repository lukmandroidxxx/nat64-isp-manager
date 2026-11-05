"""Unit tests for the root-owned typed host helper.

The helper is imported directly (it is intentionally not part of the backend
package).  Nothing here executes a privileged command.
"""
from __future__ import annotations

import importlib.util
import ipaddress
from pathlib import Path

import pytest

HOSTCTL_PATH = Path(__file__).resolve().parents[2] / "deploy" / "hostctl" / "hostctl.py"


def _load():
    spec = importlib.util.spec_from_file_location("nat64_hostctl", HOSTCTL_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def hostctl():
    return _load()


def test_ping_dispatch(hostctl):
    result = hostctl.dispatch({"op": "ping", "params": {}})
    assert result["ok"] is True
    assert result["code"] == "ok"
    assert result["data"]["helper_version"] == hostctl.VERSION
    assert isinstance(result["data"]["pid"], int)


def test_dispatch_rejects_bad_envelopes(hostctl):
    with pytest.raises(hostctl.HelperError) as exc:
        hostctl.dispatch("ping")  # type: ignore[arg-type]
    assert exc.value.code == "invalid_request"

    with pytest.raises(hostctl.HelperError) as exc:
        hostctl.dispatch({"op": "ping", "params": ["not", "an", "object"]})
    assert exc.value.code == "invalid_request"

    with pytest.raises(hostctl.HelperError) as exc:
        hostctl.dispatch({"op": "definitely-not-an-op", "params": {}})
    assert exc.value.code == "unknown_op"


def test_typed_instance_and_protocol_validation(hostctl):
    assert hostctl._instance("nat64-1") == "nat64-1"
    for bad in ["", "a" * 16, "bad name", "x;id", "../etc/passwd", 12]:
        with pytest.raises(hostctl.HelperError):
            hostctl._instance(bad)

    assert hostctl._protocol("tcp") == "tcp"
    with pytest.raises(hostctl.HelperError) as exc:
        hostctl._protocol("sctp")
    assert exc.value.code == "invalid_protocol"


def test_prefix_validation_rejects_injection_and_host_bits(hostctl):
    assert hostctl._ipv6_prefix("64:ff9b::/96", pool6=True) == ipaddress.IPv6Network(
        "64:ff9b::/96"
    )
    with pytest.raises(hostctl.HelperError):
        hostctl._ipv6_prefix("64:ff9b::/128", pool6=True)
    with pytest.raises(hostctl.HelperError):
        hostctl._ipv6_prefix("2001:db8::1/64", pool6=True)
    with pytest.raises(hostctl.HelperError):
        hostctl._ipv6_prefix("64:ff9b::/96; reboot")

    assert hostctl._ipv4_network("192.0.2.1/24") == ipaddress.IPv4Network("192.0.2.0/24")
    with pytest.raises(hostctl.HelperError):
        hostctl._ipv4_network("2001:db8::/64")


def test_port_and_interface_validation(hostctl):
    assert hostctl._port_range("61001", "65535") == (61001, 65535)
    # Jool accepts port 0 as the start of a range.
    assert hostctl._port_range(0, 0) == (0, 0)
    for lo, hi in [(-1, 10), (10, 5), (1, 70000), ("a", "b")]:
        with pytest.raises(hostctl.HelperError):
            hostctl._port_range(lo, hi)

    assert hostctl._interface("eth0.100") == "eth0.100"
    assert hostctl._interface("br-lan") == "br-lan"
    for bad in ["", "bad interface", "eth0\nreboot", "a" * 65]:
        with pytest.raises(hostctl.HelperError):
            hostctl._interface(bad)


def test_content_validation_rejects_nul_and_oversize(hostctl):
    assert hostctl._validate_content("options { };", "dns") == "options { };"
    with pytest.raises(hostctl.HelperError) as exc:
        hostctl._validate_content("abc\x00def", "dns")
    assert exc.value.code == "invalid_content"
    with pytest.raises(hostctl.HelperError):
        hostctl._validate_content("", "dns")
    with pytest.raises(hostctl.HelperError):
        hostctl._validate_content("a" * (256 * 1024 + 1), "dns")


def test_run_rejects_untyped_command_vectors(hostctl):
    with pytest.raises(hostctl.HelperError) as exc:
        hostctl.run([])
    assert exc.value.code == "invalid_argv"
    with pytest.raises(hostctl.HelperError):
        hostctl.run(["echo", 123])  # type: ignore[list-item]
    with pytest.raises(hostctl.HelperError):
        hostctl.run("echo hello")  # type: ignore[arg-type]


def test_unknown_sub_actions_fail_before_running_commands(hostctl):
    for op in ("dns", "firewall", "network", "packages", "ha", "system"):
        with pytest.raises(hostctl.HelperError) as exc:
            hostctl.dispatch({"op": op, "params": {"action": "definitely-unknown"}})
        assert exc.value.code == "unknown_action"

    # The Jool handler checks for the binary first on this host; either way it
    # must fail closed instead of interpreting the unknown action.
    with pytest.raises(hostctl.HelperError) as exc:
        hostctl.dispatch({"op": "jool", "params": {"action": "definitely-unknown"}})
    assert exc.value.code in {"unknown_action", "jool_not_installed"}


def test_helper_source_has_no_shell_or_eval():
    source = HOSTCTL_PATH.read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert "os.system(" not in source
    assert "eval(" not in source
    assert "exec(" not in source
