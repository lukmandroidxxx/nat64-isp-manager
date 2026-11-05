#!/usr/bin/env python3
"""Echo server used by the Linux network-namespace lab.

Run inside the server namespace:

    ip netns exec nat64-server python3 backend/tests/integration/helpers/echo_server.py \
        --protocol tcp --bind 198.51.100.100 --port 8080 --count 1

The first stdout line is a JSON readiness record. The final line is a JSON
summary. The process exits non-zero when the requested count is not reached
before the timeout.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import resource
import socket
import sys
import time


def raise_fd_limit() -> None:
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = min(65536, hard) if hard != resource.RLIM_INFINITY else 65536
    if soft < target:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))


def emit(payload: dict) -> None:
    print(json.dumps(payload, separators=(",", ":")), flush=True)


async def serve_tcp(bind: str, port: int, count: int, timeout: float) -> dict:
    stats = {"handled": 0, "errors": 0}
    done = asyncio.Event()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            data = await asyncio.wait_for(reader.read(256), timeout=10)
            writer.write(b"pong:" + data[:128])
            await writer.drain()
            stats["handled"] += 1
        except Exception:
            stats["errors"] += 1
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            if stats["handled"] + stats["errors"] >= count:
                done.set()

    server = await asyncio.start_server(
        handle,
        host=bind,
        port=port,
        family=socket.AF_INET,
        backlog=2048,
        reuse_address=True,
    )
    emit({"ready": True, "protocol": "tcp", "bind": bind, "port": port, "pid": __import__("os").getpid()})
    timed_out = False
    try:
        await asyncio.wait_for(done.wait(), timeout=timeout)
    except TimeoutError:
        timed_out = True
    finally:
        server.close()
        await server.wait_closed()
    return {**stats, "requested": count, "timed_out": timed_out}


class UdpEcho(asyncio.DatagramProtocol):
    def __init__(self, count: int, done: asyncio.Event) -> None:
        self.count = count
        self.done = done
        self.handled = 0
        self.errors = 0
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            if self.transport is not None:
                self.transport.sendto(b"pong:" + data[:128], addr)
            self.handled += 1
        except Exception:
            self.errors += 1
        if self.handled + self.errors >= self.count:
            self.done.set()

    def error_received(self, exc: Exception) -> None:
        self.errors += 1

    def connection_lost(self, exc: Exception | None) -> None:
        if exc is not None:
            self.errors += 1


async def serve_udp(bind: str, port: int, count: int, timeout: float) -> dict:
    loop = asyncio.get_running_loop()
    done = asyncio.Event()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: UdpEcho(count, done),
        local_addr=(bind, port),
        family=socket.AF_INET,
    )
    emit({"ready": True, "protocol": "udp", "bind": bind, "port": port, "pid": __import__("os").getpid()})
    timed_out = False
    try:
        await asyncio.wait_for(done.wait(), timeout=timeout)
    except TimeoutError:
        timed_out = True
    finally:
        transport.close()
    return {
        "handled": protocol.handled,
        "errors": protocol.errors,
        "requested": count,
        "timed_out": timed_out,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", choices=("tcp", "udp"), default="tcp")
    parser.add_argument("--bind", default="198.51.100.100")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    raise_fd_limit()
    started = time.monotonic()
    if args.protocol == "tcp":
        result = asyncio.run(serve_tcp(args.bind, args.port, args.count, args.timeout))
    else:
        result = asyncio.run(serve_udp(args.bind, args.port, args.count, args.timeout))
    result["elapsed_seconds"] = round(time.monotonic() - started, 6)
    emit(result)
    return 0 if result["handled"] >= args.count else 1


if __name__ == "__main__":
    sys.exit(main())
