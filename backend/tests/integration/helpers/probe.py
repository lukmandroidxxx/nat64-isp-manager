#!/usr/bin/env python3
"""One-shot client probe for the Linux NAT64 lab.

    ip netns exec nat64-client python3 backend/tests/integration/helpers/probe.py \
        --protocol tcp --target 64:ff9b::c633:6464 --port 8080

Exit code 0 means the echo response matched. The JSON on stdout is suitable for
test assertions; it never fabricates a success when the socket or ping fails.
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time


def probe_tcp(target: str, port: int, source: str | None, timeout: float) -> dict:
    family = socket.AF_INET6 if ":" in target else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    if source:
        sock.bind((source, 0))
    started = time.monotonic()
    try:
        sock.connect((target, port))
        sock.sendall(b"ping")
        data = sock.recv(256)
        elapsed = time.monotonic() - started
        return {
            "ok": data.startswith(b"pong:"),
            "protocol": "tcp",
            "target": target,
            "port": port,
            "source": source,
            "response": data.decode("utf-8", "replace"),
            "elapsed_seconds": round(elapsed, 6),
        }
    except Exception as exc:
        return {
            "ok": False,
            "protocol": "tcp",
            "target": target,
            "port": port,
            "source": source,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        sock.close()


def probe_udp(target: str, port: int, source: str | None, timeout: float) -> dict:
    family = socket.AF_INET6 if ":" in target else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    if source:
        sock.bind((source, 0))
    started = time.monotonic()
    try:
        sock.sendto(b"ping", (target, port))
        data, _peer = sock.recvfrom(256)
        elapsed = time.monotonic() - started
        return {
            "ok": data.startswith(b"pong:"),
            "protocol": "udp",
            "target": target,
            "port": port,
            "source": source,
            "response": data.decode("utf-8", "replace"),
            "elapsed_seconds": round(elapsed, 6),
        }
    except Exception as exc:
        return {
            "ok": False,
            "protocol": "udp",
            "target": target,
            "port": port,
            "source": source,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        sock.close()


def probe_icmp(target: str, timeout: float) -> dict:
    command = ["ping", "-6", "-c", "1", "-W", str(max(1, round(timeout))), target]
    started = time.monotonic()
    proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 10)
    return {
        "ok": proc.returncode == 0,
        "protocol": "icmp",
        "target": target,
        "command": command,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-2000:],
        "stderr": proc.stderr[-2000:],
        "elapsed_seconds": round(time.monotonic() - started, 6),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", choices=("tcp", "udp", "icmp"), default="tcp")
    parser.add_argument("--target", required=True)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--source")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    if args.protocol == "tcp":
        result = probe_tcp(args.target, args.port, args.source, args.timeout)
    elif args.protocol == "udp":
        result = probe_udp(args.target, args.port, args.source, args.timeout)
    else:
        result = probe_icmp(args.target, args.timeout)

    print(json.dumps(result, separators=(",", ":")), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
