# 08 – Testing and the Linux lab

## 1. What has actually been run

| Layer | Environment | Status in this repository |
|---|---|---|
| Backend unit/API tests | Windows authoring host, Python 3.14, SQLite | `84 passed, 5 skipped` (the skipped tests are the Linux integration suite) |
| Frontend typecheck/build | Windows authoring host, Node 26 | Build succeeds; Vite emits a chunk-size warning |
| Browser smoke checks | Windows authoring host, in-app browser | Exercised authenticated pages and polling against a local SQLite server |
| SQLite migration | Windows authoring host | `upgrade → downgrade → upgrade` verified |
| PostgreSQL migration/runtime | Not available on the authoring host | **Not verified** |
| Docker Compose build/runtime | Docker not available on the authoring host | **Not executed** |
| Linux network-namespace integration | Not available on the authoring host | **Not executed** |
| 500-client/1000-session lab load | Not available on the authoring host | **Not executed** |
| Real Jool/BIND/nftables/netplan/keepalived | Not available on the authoring host | **Not executed** |
| HA failover / session continuity | Not available on the authoring host | **Not executed** |

The Windows host has no Jool kernel module, no root, no BIND, no nftables and no
netplan. Anything that requires those is reported as `UNSUPPORTED` by the
application and is **not** claimed as passing. Run the Linux suite on a lab host
before production.

## 2. Test layers

### 2.1 Unit and API tests

Run from `backend/`:

```bash
# Windows
..\.venv\Scripts\python.exe -m pytest -q

# Linux/macOS
../.venv/bin/python -m pytest -q
```

The default run collects `tests/` including `tests/integration`, but integration
tests skip on non-Linux/non-root hosts. To exclude them deterministically:

```bash
python3 -m pytest -q -m "not integration"
```

Coverage by file:

| File | Focus |
|---|---|
| `test_security.py` | Argon2 hashing, password policy, JWT claims/tampering, refresh-token hashing, Fernet round-trip |
| `test_parser.py` | Jool version, durations, instance table, pool4 CSV, session CSV variants, stats, globals |
| `test_ipv4.py` | Address canonicalisation, CIDR import, overlap/duplicate rules, update/delete, observed usage, summary capacity, honest host-unavailable plan |
| `test_renderers.py` | DNS64 fragment/exclusions, firewall validation/rendering/anti-lockout guard, netplan/sysctl rendering, `ip -j`, named stats |
| `test_hostctl.py` | Helper envelope validation, instance/protocol/prefix/port/interface parsing, NUL/oversize rejection, no shell/eval in source |
| `test_monitoring.py` | Session upsert/dedupe/expiry, client matching and counts, filters, honest dashboard, metric persistence |
| `test_changes.py` | Dry-run failure, approve/execute lifecycle, expiration, critical self-approval rule, superadmin-only action re-check on dry-run/approve/execute, reject/cancel, pagination |
| `test_backup.py` | Schema validation, checksum/file creation, merge restore, replace superuser requirement, bad mode |
| `test_api.py` | Public health, auth guard, viewer read-only, pool CRUD, honest unsupported changes, firewall DB-only CRUD, backup flow, CSV exports, wizard/settings, network preview honesty, read-after-write, HA secret redaction |
| `test_scale.py` | 500 clients / 1000 sessions pagination and filters; pool aggregation at volume |

These tests use SQLite and a stubbed/unsupported host. They prove application
logic, validation, RBAC, persistence and honesty. They do **not** prove kernel
translation.

### 2.2 Static checks

```bash
cd backend
..\.venv\Scripts\python.exe -m ruff check app tests ..\deploy\hostctl
```

The helper is also compiled as part of the release check:

```bash
python3 -m py_compile deploy/hostctl/hostctl.py
```

### 2.3 Frontend

```bash
cd frontend
npm ci
npm run build     # tsc -b && vite build
npm run lint      # if configured in your checkout
```

A successful production build proves type correctness and bundling, not runtime
behaviour against a live gateway.

## 3. Linux network-namespace integration suite

### 3.1 Requirements

- Linux with root (network namespaces and Jool require privileges);
- `iproute2` (`ip`);
- `iputils-ping` (`ping`);
- Jool 4 kernel module and tools (`jool-dkms`, `jool-tools`);
- Python 3.12+ with `requirements-dev.txt` (pytest, httpx, etc.);
- `psutil` for load-test CPU/RAM sampling (optional but recommended);
- the project virtualenv at `.venv/`, or `PYTHON=/path/to/python3`.

The suite creates and removes three namespaces it owns. It does not modify the
host's Jool instance, firewall or routing table.

### 3.2 Topology

`scripts/netns_lab.sh` (single source of truth):

```
client fd00:64::2/64 -- veth -- fd00:64::1/64 gw 198.51.100.1/24 -- veth -- 198.51.100.100/24 server
      route 64:ff9b::/96 via fd00:64::1          pool4 203.0.113.1..32
```

- namespaces: `nat64-client`, `nat64-gw`, `nat64-server`;
- instance: `nat64test`, framework `netfilter`, pool6 `64:ff9b::/96`;
- pool4: 32 addresses, TCP/UDP/ICMP, `61001-65535`;
- target IPv6 address `64:ff9b::c633:6464` embeds `198.51.100.100`.

### 3.3 Run

From the repository root:

```bash
sudo ./scripts/run-integration-tests.sh

# with the opt-in 500/1000 application load
sudo ./scripts/run-integration-tests.sh --load

# pass pytest arguments after --
sudo ./scripts/run-integration-tests.sh -- -k pool4 -vv

# choose a Python explicitly
sudo PYTHON=/usr/bin/python3.12 ./scripts/run-integration-tests.sh
```

The runner checks Linux, root, tools and pytest, creates `tests/results/`, sets
`NAT64_LAB_LOAD=0` (or `1` with `--load`), changes to `backend/`, and runs:

```bash
python -m pytest tests/integration -m integration -v
```

### 3.4 Coverage

| Test | What it proves |
|---|---|
| `test_tcp_is_translated_and_visible_in_jool` | TCP round-trip through Jool, Jool session visible, pool4 address used |
| `test_udp_is_translated_and_visible_in_jool` | UDP round-trip and session visibility |
| `test_icmp_echo_is_translated` | ICMPv6 echo translated to ICMPv4 |
| `test_pool4_exposes_many_addresses_and_allocates_per_connection` | 32-address pool4 present; five client IPv6 addresses produce sessions; translated addresses stay inside `203.0.113.1–32` |
| `test_500_clients_1000_sessions_lab` (opt-in) | 500 source addresses / 1000 application sessions; writes success ratio and latency percentiles; requires ≥99% success in the disposable lab |

The suite does **not** cover:

- BIND9 DNS64 synthesis;
- nftables return-traffic policy;
- netplan apply/rollback;
- host reboot or service ordering;
- keepalived election, failover, split-brain or session continuity;
- packet-forwarding throughput or a production SLA;
- PostgreSQL behaviour.

Those require separate manual lab work (section 5).

### 3.5 Manual lab exploration

```bash
sudo ./scripts/netns_lab.sh up
sudo ./scripts/netns_lab.sh status

# Server side
sudo ip netns exec nat64-server python3 backend/tests/integration/helpers/echo_server.py \
  --protocol tcp --bind 198.51.100.100 --port 8080 --count 1

# Client side (second terminal)
sudo ip netns exec nat64-client python3 backend/tests/integration/helpers/probe.py \
  --protocol tcp --target 64:ff9b::c633:6464 --port 8080

# Inspect Jool
sudo ip netns exec nat64-gw jool -i nat64test pool4 display --tcp
sudo ip netns exec nat64-gw jool -i nat64test session display --numeric

sudo ./scripts/netns_lab.sh down
```

## 4. Application load test

`scripts/load_test.py` is an **application-level** generator. It opens TCP
connections from many source IPv6 addresses to an echo server through NAT64 and
records success, connect/RTT percentiles and host CPU/RAM. It is not a
packet-forwarding benchmark and makes no performance claim.

```bash
# Inside the client namespace, with the echo server running in the server namespace
sudo ip netns exec nat64-client python3 scripts/load_test.py \
  --source-prefix fd00:64::/64 --clients 500 --sessions 1000 \
  --target 64:ff9b::c633:6464 --port 8080 \
  --assign-addresses --interface veth-client \
  --output tests/results/load-500x1000.json
```

The report contains:

- `workload` – clients, sessions, concurrency, target;
- `result` – attempted, succeeded, failed, success ratio, app round-trips/s,
  connect and RTT p50/p95/p99/max, first 20 errors;
- `system` – CPU/memory before/after and whether `psutil` was available;
- `host` – hostname, platform, Python, CPU count.

Interpretation:

- success ratio is for this echo workload only;
- latency includes Python asyncio, the client namespace and the echo server;
- CPU/RAM is host-wide, not per-core or per-Jool;
- a failure means the lab setup, file descriptors, conntrack or Jool limits need
  investigation; it is not automatically a product bug;
- **do not** translate these numbers into an SLA or a production capacity claim
  without a controlled test on production-equivalent hardware.

## 5. Manual checks that are not automated

Run these on the target or an equivalent lab and record the output.

### 5.1 BIND9 DNS64

```bash
sudo named-checkconf
dig @127.0.0.1 AAAA ipv4only.arpa +short
dig @127.0.0.1 A ipv4only.arpa +short
sudo rndc reconfig
sudo journalctl -u named -n 100 --no-pager
```

Expected: at least one AAAA answer inside the configured NAT64 prefix; A answers
still resolve. Test from an IPv6-only client, not only from the gateway.

### 5.2 nftables

```bash
sudo nft -c -f /etc/nftables.d/nat64-manager.nft
sudo nft list table inet nat64mgr
sudo nft list ruleset | head -100
```

Then generate NAT64 TCP, UDP and ICMP traffic and confirm return traffic is not
dropped. Check counters if you add them, and test with the default forward policy
set to drop.

### 5.3 netplan and sysctl

```bash
sudo netplan get
sysctl net.ipv4.ip_forward net.ipv6.conf.all.forwarding
cat /etc/netplan/90-nat64-manager.yaml
cat /etc/sysctl.d/90-nat64-manager.conf
```

Apply a harmless MTU change, verify connectivity, confirm the change in the UI,
and confirm the rollback timer is gone:

```bash
systemctl status nat64mgr-netrollback.timer --no-pager
```

Then deliberately let a change expire without confirming and verify the previous
configuration is restored.

### 5.4 Reboot and service ordering

Reboot the gateway. After boot:

```bash
systemctl status nat64-manager nat64-manager-migrate named nftables keepalived --no-pager
jool --version
jool instance display
ip -br addr
ip route
nft list table inet nat64mgr
curl -fsS http://127.0.0.1:8000/health | python3 -m json.tool
```

Confirm the API starts after PostgreSQL and the collector reconnects to Jool.

### 5.5 Jool pool4 persistence

Jool instances are not automatically recreated by this application after a
reboot unless your host configuration does so. Verify whether your distribution
or a systemd unit restores the instance and pool4. If not, document a boot-time
procedure; the application will report `not_installed`/missing pool4 rather than
inventing state.

### 5.6 PostgreSQL

On a host with PostgreSQL:

```bash
cd backend
DATABASE_URL=postgresql+psycopg://... alembic upgrade head
DATABASE_URL=... alembic downgrade -1
DATABASE_URL=... alembic upgrade head
python3 -m pytest -q -m "not integration"
```

Run the API against PostgreSQL and repeat the browser smoke checks. SQLite
passing does not prove PostgreSQL behaviour (locking, JSON, timezone handling).

### 5.7 HA failover

See [`07-HIGH-AVAILABILITY.md`](07-HIGH-AVAILABILITY.md) section 9. This is
mandatory before claiming HA. At minimum: election, planned failover, unplanned
failover, failback, split-brain drill, reboot, and long-lived session behaviour.

## 6. Results and artifacts

| Path | Contents |
|---|---|
| `tests/results/load-500x1000.json` | Load report (only when `--load` or `--output` is used) |
| `backend/data/` | Development SQLite database and backups (not for production) |
| `/var/backups/nat64-manager/` | Network snapshots and application backups on a real host |

No result files from Linux runs are committed in this repository. If you run the
suite, keep the JSON reports with your change record and state the exact host,
kernel, Jool version and date.

## 7. Interpreting failures

| Symptom | Likely cause |
|---|---|
| Integration tests skip | Not Linux, not root, or a tool (`ip`, `jool`, `ping`, `sysctl`) is missing |
| `modprobe: Module jool not found` | Missing `jool-dkms`, Secure Boot/MOK, or wrong kernel |
| Namespace setup fails | A previous lab was not removed; run `netns_lab.sh down`, then `up` |
| No TCP/UDP response | Forwarding sysctls, client route to pool6, Jool instance state, or pool4 missing |
| Sessions never visible | Jool instance disabled, pool6 mismatch, or traffic not reaching the gateway namespace |
| Load test timeouts | `ulimit -n`, concurrency too high, conntrack limits, or echo server count |
| 99% threshold fails | Inspect the JSON errors; do not hide them or raise the threshold without understanding the cause |
| Firewall test unreachable after apply | Use console, delete `table inet nat64mgr`, fix the allowlist, re-apply |
| netplan change lost connectivity | Wait for the rollback timer or restore the snapshot from the console |

## 8. Suggested CI

A minimal CI can run without root:

```bash
cd backend
python -m ruff check app tests ../deploy/hostctl
python -m pytest -q -m "not integration"
python -m py_compile ../deploy/hostctl/hostctl.py
cd ../frontend
npm ci
npm run build
```

Keep the Linux integration suite as a manual/lab job on a self-hosted runner with
root and Jool. Never mark it as required-but-skipped in a way that implies it
passed.
