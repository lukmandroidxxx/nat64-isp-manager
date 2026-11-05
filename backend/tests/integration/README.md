# Linux integration tests

These tests exercise the real NAT64 data plane. They require a Linux host with
root, network namespaces, iproute2, iputils-ping, Jool 4 kernel and userspace
packages, and (for the load test) Python 3.12+ with `psutil` available to the
loader.

They are **not executed by the Windows unit/API test run**. On non-Linux hosts
every test in this directory is collected and skipped by the `lab` fixture. The
results below have not been produced in this repository's authoring
environment (Windows, no Jool/BIND/nftables/root), so they must not be reported
as passing until you run them on a Linux gateway or lab host.

## What is covered

| Test | Verifies |
|---|---|
| `test_tcp_is_translated_and_visible_in_jool` | TCP client -> NAT64 -> IPv4 echo server, and a Jool session using a pool4 address |
| `test_udp_is_translated_and_visible_in_jool` | UDP datagram translation and a visible Jool session |
| `test_icmp_echo_is_translated` | ICMPv6 echo translated through Jool |
| `test_pool4_exposes_many_addresses_and_allocates_per_connection` | 32-address pool4, five source IPv6 addresses, sessions allocated per connection; translated addresses stay inside pool4 |
| `test_500_clients_1000_sessions_lab` (opt-in) | 500 source addresses and 1000 application sessions through NAT64; writes a JSON report with success ratio and latency percentiles |

The suite does **not** cover BIND9 DNS64, nftables return traffic, host
reboots, keepalived failover, or packet-forwarding throughput. Those require
separate lab work and must be validated manually; see
`docs/08-TESTING-LINUX-LAB.md`.

## Topology

`scripts/netns_lab.sh` creates:

```
client fd00:64::2/64 -- veth -- fd00:64::1/64 gw 198.51.100.1/24 -- veth -- 198.51.100.100/24 server
      route 64:ff9b::/96 via fd00:64::1          pool4 203.0.113.1..32
```

The gateway namespace runs one Jool 4 netfilter instance named `nat64test` with
pool6 `64:ff9b::/96`. The script only touches the three namespaces it creates.

## Run

From the repository root on a Linux host:

```bash
# One-time prerequisites (Debian/Ubuntu example)
sudo apt-get install -y iproute2 iputils-ping jool-tools jool-dkms
sudo modprobe jool

# Unit/API suite first (no root, SQLite)
cd backend
python3 -m pytest -q -m "not integration"

# Data-plane suite
cd ..
sudo ./scripts/run-integration-tests.sh

# Include the opt-in 500/1000 load test
sudo ./scripts/run-integration-tests.sh --load

# Pass pytest arguments after --
sudo ./scripts/run-integration-tests.sh -- -k pool4 -vv
```

If the project virtualenv is not at `.venv/`, set `PYTHON` or invoke the
interpreter that has `requirements-dev.txt` installed:

```bash
sudo PYTHON=/usr/bin/python3.12 ./scripts/run-integration-tests.sh
```

## Manual lab checks

The script leaves the lab up while pytest runs and removes it afterwards. To
explore by hand:

```bash
sudo ./scripts/netns_lab.sh up
sudo ./scripts/netns_lab.sh status

# Echo server in the server namespace
sudo ip netns exec nat64-server python3 backend/tests/integration/helpers/echo_server.py \
  --protocol tcp --bind 198.51.100.100 --port 8080 --count 1

# Probe from the client namespace (separate terminal)
sudo ip netns exec nat64-client python3 backend/tests/integration/helpers/probe.py \
  --protocol tcp --target 64:ff9b::c633:6464 --port 8080

# Inspect Jool
sudo ip netns exec nat64-gw jool -i nat64test pool4 display --tcp
sudo ip netns exec nat64-gw jool -i nat64test session display --numeric

sudo ./scripts/netns_lab.sh down
```

`64:ff9b::c633:6464` embeds `198.51.100.100` in the `64:ff9b::/96` prefix.

## Load test

`scripts/load_test.py` measures application-level echo success, connect and RTT
latency, and host CPU/RAM. It is a workload description, not a forwarding
throughput benchmark or an SLA claim. The opt-in test requires at least 99%
application success in the disposable lab; any failure is retained in the JSON
report.

```bash
sudo ./scripts/run-integration-tests.sh --load
cat tests/results/load-500x1000.json
```

The report is written only when `--output` is supplied. The integration test
uses `tests/results/load-500x1000.json`.

## Troubleshooting

- **`jool: command not found`** — install `jool-tools`; check `jool --version`
  reports 4.x. Jool 3 is rejected by the application.
- **`modprobe: FATAL: Module jool not found`** — install `jool-dkms` and check
  Secure Boot/MOK signing on the lab host.
- **No namespace traffic** — verify forwarding sysctls and that the client has
  the `64:ff9b::/96` route (`ip -n nat64-client -6 route`).
- **Sessions never appear** — run `jool -i nat64test global display` inside the
  gateway namespace and confirm `pool6`, `pool4` and that the instance is
  enabled.
- **Load test timeouts** — lower `--concurrency`, raise `--timeout`, and check
  `ulimit -n` and kernel conntrack/Jool limits. Do not interpret a failed lab
  run as production capacity.
