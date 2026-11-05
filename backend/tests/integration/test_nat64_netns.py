"""Linux network-namespace integration tests for the NAT64 data plane.

These tests require root, Linux, Jool 4, iproute2 and ping. They are marked
``integration`` and skip cleanly everywhere else. The Windows unit/API run does
not execute them.

The topology is created by ``scripts/netns_lab.sh``. That script is the single
source of truth for namespace addresses:

    client fd00:64::2/64 --- fd00:64::1/64 gw 198.51.100.1/24 --- 198.51.100.100/24 server
    pool6 64:ff9b::/96, pool4 203.0.113.1..32 (default 32 addresses)

Run explicitly:

    sudo ./scripts/run-integration-tests.sh

Nothing in this file asserts a performance SLA. The opt-in 500/1000 test records
application-level success and latency in ``tests/results/``.
"""
from __future__ import annotations

import contextlib
import json
import os
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
LAB = ROOT / "scripts" / "netns_lab.sh"
LOAD = ROOT / "scripts" / "load_test.py"
HELPERS = Path(__file__).resolve().parent / "helpers"
ECHO = HELPERS / "echo_server.py"
PROBE = HELPERS / "probe.py"

GW_NS = "nat64-gw"
CLIENT_NS = "nat64-client"
SERVER_NS = "nat64-server"
INSTANCE = "nat64test"
SERVER_V4 = "198.51.100.100"
TARGET_V6 = "64:ff9b::c633:6464"  # 64:ff9b::198.51.100.100
CLIENT_V6 = "fd00:64::2"


def _have_tool(name: str) -> bool:
    return shutil.which(name) is not None or Path("/usr/sbin", name).exists()


@pytest.fixture(scope="module")
def lab() -> str:
    if sys.platform != "linux":
        pytest.skip("Linux network namespaces are required")
    if os.geteuid() != 0:
        pytest.skip("integration lab requires root")
    missing = [tool for tool in ("ip", "jool", "ping", "sysctl") if not _have_tool(tool)]
    if missing:
        pytest.skip(f"missing lab tools: {', '.join(missing)}")
    if not LAB.exists():
        pytest.skip(f"lab script not found at {LAB}")

    up = subprocess.run(
        ["bash", str(LAB), "up"],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if up.returncode != 0:
        pytest.fail(f"netns lab setup failed:\n{up.stdout}\n{up.stderr}")
    try:
        yield up.stdout
    finally:
        subprocess.run(["bash", str(LAB), "down"], capture_output=True, text=True, timeout=120, check=False)


def _run_in(netns: str, args: list[str], timeout: float = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ip", "netns", "exec", netns, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@contextlib.contextmanager
def echo_server(protocol: str, count: int, timeout: float = 180):
    process = subprocess.Popen(
        [
            "ip",
            "netns",
            "exec",
            SERVER_NS,
            sys.executable,
            str(ECHO),
            "--protocol",
            protocol,
            "--bind",
            SERVER_V4,
            "--port",
            "8080",
            "--count",
            str(count),
            "--timeout",
            str(timeout),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    ready, _, _ = select.select([process.stdout], [], [], 15)
    if not ready:
        process.terminate()
        stderr = process.stderr.read() if process.stderr else ""
        raise AssertionError(f"echo server did not become ready: {stderr}")
    line = process.stdout.readline().strip()
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        process.terminate()
        raise AssertionError(f"invalid echo readiness line: {line!r}") from exc
    assert record.get("ready") is True, record
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=10)
        if process.poll() is None:
            process.kill()


def probe(protocol: str, *, source: str | None = None, port: int = 8080) -> dict:
    args = [
        "ip",
        "netns",
        "exec",
        CLIENT_NS,
        sys.executable,
        str(PROBE),
        "--protocol",
        protocol,
        "--target",
        TARGET_V6,
        "--port",
        str(port),
    ]
    if source:
        args.extend(["--source", source])
    result = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
    payload = result.stdout.strip().splitlines()
    if not payload:
        raise AssertionError(f"probe produced no output: {result.stderr}")
    body = json.loads(payload[-1])
    body["_returncode"] = result.returncode
    return body


def jool_sessions() -> str:
    result = _run_in(GW_NS, ["jool", "-i", INSTANCE, "session", "display", "--numeric", "--csv"])
    assert result.returncode == 0, result.stderr
    return result.stdout


def wait_for_session(client: str, timeout: float = 8) -> str:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        last = jool_sessions()
        if client in last:
            return last
        time.sleep(0.25)
    raise AssertionError(f"no Jool session for {client} within {timeout}s; last output:\n{last}")


def test_tcp_is_translated_and_visible_in_jool(lab):
    with echo_server("tcp", 1):
        result = probe("tcp")
    assert result["ok"] is True, result
    assert result["response"].startswith("pong:")

    sessions = wait_for_session(CLIENT_V6)
    assert SERVER_V4 in sessions
    assert "203.0.113." in sessions


def test_udp_is_translated_and_visible_in_jool(lab):
    with echo_server("udp", 1):
        result = probe("udp")
    assert result["ok"] is True, result
    assert result["response"].startswith("pong:")

    sessions = wait_for_session(CLIENT_V6)
    assert "UDP" in sessions.upper() or "udp" in sessions.lower()


def test_icmp_echo_is_translated(lab):
    result = probe("icmp")
    assert result["ok"] is True, result
    assert "1 received" in result["stdout"] or "1 packets received" in result["stdout"]


def test_pool4_exposes_many_addresses_and_allocates_per_connection(lab):
    tcp_pool = _run_in(GW_NS, ["jool", "-i", INSTANCE, "pool4", "display", "--tcp"])
    assert tcp_pool.returncode == 0, tcp_pool.stderr
    assert "203.0.113.1" in tcp_pool.stdout
    assert "203.0.113.32" in tcp_pool.stdout

    aliases = [f"fd00:64::{index:x}" for index in range(0x10, 0x15)]
    for alias in aliases:
        added = _run_in(CLIENT_NS, ["ip", "-6", "addr", "add", f"{alias}/128", "dev", "veth-client"])
        assert added.returncode == 0, added.stderr

    with echo_server("tcp", len(aliases)):
        results = [probe("tcp", source=alias) for alias in aliases]
    assert all(item["ok"] for item in results), results

    deadline = time.monotonic() + 8
    sessions = ""
    while time.monotonic() < deadline:
        sessions = jool_sessions()
        if all(alias in sessions for alias in aliases):
            break
        time.sleep(0.25)
    else:
        raise AssertionError(f"not every client address appeared in Jool sessions:\n{sessions}")

    # Every translated address must come from the configured pool4 range.
    translated = {
        token
        for line in sessions.splitlines()
        for token in line.replace(",", " ").split()
        if token.startswith("203.0.113.")
    }
    assert translated
    assert all(1 <= int(token.rsplit(".", 1)[1]) <= 32 for token in translated)


@pytest.mark.skipif(
    os.environ.get("NAT64_LAB_LOAD") != "1",
    reason="set NAT64_LAB_LOAD=1 to run the 500-client/1000-session lab load",
)
def test_500_clients_1000_sessions_lab(lab):
    """Opt-in volume test. Records results; does not assert a latency target."""
    output = ROOT / "tests" / "results" / "load-500x1000.json"
    with echo_server("tcp", 1000, timeout=300):
        result = subprocess.run(
            [
                "ip",
                "netns",
                "exec",
                CLIENT_NS,
                sys.executable,
                str(LOAD),
                "--clients",
                "500",
                "--sessions",
                "1000",
                "--target",
                TARGET_V6,
                "--port",
                "8080",
                "--timeout",
                "20",
                "--assign-addresses",
                "--interface",
                "veth-client",
                "--output",
                str(output),
            ],
            capture_output=True,
            text=True,
            timeout=420,
            check=False,
        )
    assert output.exists(), f"load report was not written:\n{result.stdout}\n{result.stderr}"
    report = json.loads(output.read_text(encoding="utf-8"))
    outcome = report["result"]
    assert outcome["attempted"] == 1000, outcome
    # A lab should complete nearly all app-level echoes. Failures are reported
    # verbatim; the script's JSON keeps every error class.
    assert outcome["success_ratio"] >= 0.99, json.dumps(report, indent=2)[:8000]
    print(json.dumps({"load": outcome, "system": report["system"]}, indent=2))
