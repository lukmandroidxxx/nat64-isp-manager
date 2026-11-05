#!/usr/bin/env python3
"""NAT64 application-level load generator for the Linux lab.

Run inside the client network namespace, with the echo server running in the
server namespace:

    ip netns exec nat64-client python3 scripts/load_test.py \
        --source-prefix fd00:64::/64 --clients 500 --sessions 1000 \
        --target 64:ff9b::c633:6464 --port 8080 \
        --assign-addresses --interface veth-client \
        --output tests/results/load-500x1000.json

This measures application round-trip success, connection/RTT latency, and
system CPU/RAM on the host. It is not a packet-forwarding throughput benchmark,
and it does not claim a performance target. Every failure is recorded.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import ipaddress
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

try:
    import psutil
except ImportError:  # psutil is optional for the load generator
    psutil = None


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return round(ordered[index], 6)


def address_list(prefix: str, count: int, offset: int) -> list[str]:
    network = ipaddress.ip_network(prefix, strict=False)
    if network.version != 6:
        raise SystemExit("--source-prefix must be an IPv6 prefix")
    start = int(network.network_address) + offset
    addresses = []
    for index in range(count):
        candidate = ipaddress.ip_address(start + index)
        if candidate not in network:
            raise SystemExit(f"prefix {prefix} is too small for {count} addresses at offset {offset}")
        addresses.append(str(candidate))
    return addresses


def assign_addresses(addresses: list[str], interface: str) -> list[str]:
    added: list[str] = []
    existing = subprocess.run(
        ["ip", "-6", "-o", "addr", "show", "dev", interface],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    for address in addresses:
        if f" {address}/" in existing:
            continue
        proc = subprocess.run(
            ["ip", "-6", "addr", "add", f"{address}/128", "dev", interface],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise SystemExit(f"could not add {address} to {interface}: {proc.stderr.strip()}")
        added.append(address)
    return added


def remove_addresses(addresses: list[str], interface: str) -> None:
    for address in addresses:
        with contextlib.suppress(Exception):
            subprocess.run(
                ["ip", "-6", "addr", "del", f"{address}/128", "dev", interface],
                capture_output=True,
                text=True,
                check=False,
            )


async def one_session(
    source: str,
    target: str,
    port: int,
    payload: bytes,
    timeout: float,
    semaphore: asyncio.Semaphore,
) -> dict:
    started = time.monotonic()
    async with semaphore:
        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    target,
                    port,
                    family=socket.AF_INET6,
                    local_addr=(source, 0),
                ),
                timeout=timeout,
            )
            connected = time.monotonic()
            writer.write(payload)
            await writer.drain()
            response = await asyncio.wait_for(reader.read(256), timeout=timeout)
            finished = time.monotonic()
            return {
                "ok": response.startswith(b"pong:"),
                "source": source,
                "connect_seconds": connected - started,
                "rtt_seconds": finished - connected,
                "response": response[:64].decode("utf-8", "replace"),
            }
        except Exception as exc:
            return {
                "ok": False,
                "source": source,
                "error": f"{type(exc).__name__}: {exc}",
            }
        finally:
            if writer is not None:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()


async def run(args: argparse.Namespace, sources: list[str]) -> dict:
    sessions = [
        sources[index % len(sources)]
        for index in range(args.sessions)
    ]
    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    payload = b"load"
    started = time.monotonic()
    tasks = [
        one_session(source, args.target, args.port, payload, args.timeout, semaphore)
        for source in sessions
    ]
    results = await asyncio.gather(*tasks)
    elapsed = time.monotonic() - started

    successful = [item for item in results if item["ok"]]
    failed = [item for item in results if not item["ok"]]
    connect_latencies = [item["connect_seconds"] for item in successful]
    rtts = [item["rtt_seconds"] for item in successful]
    return {
        "elapsed_seconds": round(elapsed, 6),
        "attempted": len(results),
        "succeeded": len(successful),
        "failed": len(failed),
        "success_ratio": round(len(successful) / len(results), 6) if results else 0.0,
        "packet_loss_ratio": round(len(failed) / len(results), 6) if results else 0.0,
        "app_round_trips_per_second": round(len(successful) / elapsed, 3) if elapsed else None,
        "payload_bytes_per_second": round(len(successful) * len(payload) / elapsed, 3) if elapsed else None,
        "connect_seconds": {
            "p50": percentile(connect_latencies, 0.50),
            "p95": percentile(connect_latencies, 0.95),
            "p99": percentile(connect_latencies, 0.99),
            "max": round(max(connect_latencies), 6) if connect_latencies else None,
        },
        "rtt_seconds": {
            "p50": percentile(rtts, 0.50),
            "p95": percentile(rtts, 0.95),
            "p99": percentile(rtts, 0.99),
            "max": round(max(rtts), 6) if rtts else None,
        },
        "errors": [item["error"] for item in failed[:20]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-prefix", default="fd00:64::/64")
    parser.add_argument("--source-offset", type=int, default=1000)
    parser.add_argument("--clients", type=int, default=500)
    parser.add_argument("--sessions", type=int, default=1000)
    parser.add_argument("--target", default="64:ff9b::c633:6464")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--concurrency", type=int, default=1000)
    parser.add_argument("--address-interface", "--interface", dest="interface", default="veth-client")
    parser.add_argument("--assign-addresses", action="store_true")
    parser.add_argument("--keep-addresses", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()

    if args.sessions < args.clients:
        raise SystemExit("--sessions must be at least --clients")
    sources = address_list(args.source_prefix, args.clients, args.source_offset)
    added: list[str] = []
    if args.assign_addresses:
        added = assign_addresses(sources, args.interface)

    before_cpu = before_mem = None
    if psutil is not None:
        psutil.cpu_percent(interval=None)
        before_cpu = psutil.cpu_percent(interval=0.2)
        before_mem = psutil.virtual_memory().percent

    try:
        result = asyncio.run(run(args, sources))
    finally:
        if added and not args.keep_addresses:
            remove_addresses(added, args.interface)

    after_cpu = after_mem = None
    if psutil is not None:
        after_cpu = psutil.cpu_percent(interval=0.2)
        after_mem = psutil.virtual_memory().percent

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "kind": "nat64-application-load",
        "note": (
            "Application-level echo load through NAT64. Success/latency describe this "
            "workload only; they are not a forwarding throughput or SLA claim."
        ),
        "host": {
            "hostname": platform.node(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "workload": {
            "clients": args.clients,
            "sessions": args.sessions,
            "concurrency": args.concurrency,
            "source_prefix": args.source_prefix,
            "target": args.target,
            "target_port": args.port,
            "assigned_addresses": len(added) if args.assign_addresses else 0,
        },
        "system": {
            "cpu_percent_before": before_cpu,
            "cpu_percent_after": after_cpu,
            "memory_percent_before": before_mem,
            "memory_percent_after": after_mem,
            "psutil_available": psutil is not None,
        },
        "result": result,
    }

    output = Path(args.output) if args.output else None
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if result["succeeded"] == result["attempted"] else 1


if __name__ == "__main__":
    sys.exit(main())
