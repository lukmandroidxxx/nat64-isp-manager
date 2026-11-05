# NAT64 ISP Manager — Project Plan & Architecture

> Working name: **NAT64 ISP Manager**
> Target: ISP / RT-RW Net / hotspot operators running IPv6-only access networks with a
> Jool Stateful NAT64 gateway and a pool of public IPv4 addresses.

## 1. Scope and non-negotiable behaviour

This application is a **real control and monitoring plane** for a Linux NAT64 gateway.
It is not a dashboard simulation.

- It reads live kernel/Jool/BIND/nftables state and shows an explicit error or
  `NOT_IMPLEMENTED`/`UNSUPPORTED` state when the host cannot provide data.
- No random or synthetic values are generated for production status.
- Every host-mutating action is staged as a **change request**, rendered as a
  human-readable plan plus dry-run output, and applied only after an administrator
  approves it.
- The browser never receives a shell. The backend never executes user-provided
  command strings. Commands are built from typed parameters and validated by a
  root-owned privileged helper.
- IPv4 pool allocation is **dynamic per connection** (RFC 6056-style port/address
  masking in Jool), not one public IPv4 per subscriber. The same IPv6 client can
  use different public IPv4 transport addresses for different simultaneous
  connections.

## 2. Environment reality check

This project was generated on a Windows workstation. The following is therefore
explicitly separated:

| Capability | Windows dev host | Linux gateway |
|---|---|---|
| Web UI, API, DB, auth, validation, change plans | Runs (SQLite fallback) | Runs (PostgreSQL) |
| Jool control / session collection | `UNSUPPORTED` (visible in UI) | Implemented, requires Jool 4 |
| BIND9 DNS64 management | `UNSUPPORTED` | Implemented |
| nftables / netplan / sysctl | `UNSUPPORTED` | Implemented |
| Real NAT64 data-plane tests | Cannot run | Provided under `backend/tests/integration` |

Nothing in this repository claims that the Linux data-plane tests have passed on
the authoring machine. The integration suite must be executed on a Linux host
with root, Jool, BIND9, nftables, and network namespaces. Results are written to
`tests/results/` by the supplied scripts.

## 3. High-level architecture

```
                    +---------------------------------------------------+
                    |                Browser (React SPA)                |
                    |  Dashboard / Pools / Sessions / Wizard / Settings |
                    +-------------------------+-------------------------+
                                              | HTTPS + JWT
                    +-------------------------v-------------------------+
                    |              FastAPI control plane                |
                    |  RBAC, audit, rate limit, change approvals        |
                    |  SQLAlchemy -> PostgreSQL   Redis (optional)      |
                    +----+----------------------+-----------------------+
                         |                      |
             read/write  |                      |  metrics every 5-10 s
                 +-------v-------+      +-------v------------------------+
                 | root helper   |      | monitoring collector            |
                 | hostctl.py    |      | Jool session/stats, psutil, DNS |
                 | (sudo, stdin  |      +-------+------------------------+
                 |  JSON only)   |              |
                 +---+---+---+---+              |
                     |   |   |                      observed state
          +----------+   |   +-----------+          (read back)
          |              |               |
   +------v-----+  +-----v------+  +-----v----------+
   | Jool 4     |  | BIND9      |  | nftables       |
   | kernel     |  | DNS64      |  | netplan/sysctl |
   +------+-----+  +-----+------+  +----------------+
          |              |
   IPv6-only LAN     client DNS
          |
   IPv4 Internet (pool4, many public addresses)
```

### 3.1 Control plane

- Python 3.12+ / FastAPI / SQLAlchemy 2 / Alembic / PostgreSQL.
- Redis is optional. When `REDIS_URL` is set it is used for API rate limiting and
  short-lived cache. Without Redis, a process-local limiter is used and the UI
  reports `redis: disabled`.
- Background collector runs inside the API process. Production deployments use a
  **single worker** (or a separate collector process with a DB lock) to avoid
  duplicate collection. The collector writes `system_metrics` and refreshes
  `nat64_sessions` / `ipv6_clients`.

### 3.2 Host control boundary

- Production backend runs as `nat64mgr`, not root.
- A root-owned helper `/opt/nat64-manager/hostctl.py` is invoked with `sudo -n`.
  It accepts a JSON document on stdin with a **fixed operation name and typed
  fields**, validates everything again, and only then executes a documented
  command vector with `shell=False`.
- Sudoers grants only that helper, with no arguments.
- The helper is installed root-owned, mode 0755, in a directory not writable by
  `nat64mgr`.
- Read-only diagnostics (`ip -j`, `dig`, `jool display`, `nft list`) also go
  through the helper so the backend never needs broad capabilities.
- `ntfy`/email/webhook alerts are optional and out of scope for v1.

### 3.3 Data flow

1. Admin edits intended state in DB (pools, DNS settings, firewall rules, etc.).
2. Admin requests a dry-run. Backend validates and stores a `ChangeRequest` with a
   detailed plan and (where supported) real dry-run output.
3. Admin approves. Backend executes via `hostctl`, captures stdout/stderr/exit
   code, writes audit log, and marks the request applied or failed.
4. Monitoring reads observed state from Jool/BIND/kernel and stores snapshots.
5. UI shows desired vs observed and highlights drift.

## 4. NAT64 data model and allocation semantics

### 4.1 One Jool instance, many pool4 addresses

Jool 4's pool4 is a table of transport addresses. A single NAT64 instance can
hold many IPv4 addresses and port ranges. There is no need to shard by instance
to exceed 32 public addresses. Each database `ipv4_addresses` row is rendered as
a pool4 entry for TCP, UDP, and ICMP with its configured port range. Jool chooses
an IPv4 address + port per connection using a hash of packet attributes and
collision resolution; it does **not** pin an IPv4 address to a subscriber.

Consequences shown in the UI:

- "Usage" is based on **observed active sessions / available transport
  addresses**, not on "one customer per IP".
- A client may appear under more than one public IPv4 address at the same time.
- Per-IP TCP/UDP/ICMP numbers are counts of currently observed Jool sessions for
  that translated address. They are not lifetime totals.
- BIB entries without a live session can hold ports. Jool exposes the total BIB
  count only per instance, not per address. The UI labels pool usage as
  "observed session utilization" and shows the instance BIB total separately.

### 4.2 Pool4 entries

- Each address row has `port_min`/`port_max` (default 61001–65535, avoiding the
  Linux ephemeral range 32768–61000). Ranges for a single address must not
  overlap across enabled rows for the same protocol. Validation rejects overlap.
- Import supports one IPv4 per line, comma/space separated, or CIDR. CIDR is
  expanded up to 1024 addresses per request.
- `enabled=false` removes the entry from Jool on the next approved sync. Removal
  cascades to BIB entries by default (`--quick` is **not** used), which can drop
  related sessions. The plan warns about this.

### 4.3 Sessions and clients

Jool session CSV provides protocol, client IPv6/port, translated IPv4/port,
remote IPv4/port, expiry, and TCP state. It does **not** provide session creation
time or byte counters. Therefore:

- `nat64_sessions.first_seen_at` is when this control plane first observed the
  session. The UI calls it "First observed", not "Created".
- `nat64_sessions.expires_in` and `jool_state` are stored as observed.
- Sessions disappear from Jool on expiry; the collector marks them `expired` and
  retains a configurable history window (default 24 h) for audit/search.
- Client identity is the observed IPv6 address plus network attachment (neighbor
  table MAC/interface when available). IPv6 privacy extensions mean an address is
  not a stable device identity. The UI states this and groups nothing implicitly.
- Per-client upload/download bytes are **not available** from Jool. The API
  returns `null` and the UI shows "Not available". No synthetic traffic numbers.

### 4.4 pool6

Jool NAT64 pool6 is fixed at instance creation. Changing it requires deleting and
recreating the instance, which drops BIB/session state. The API models this as
`jool.recreate_instance` with an explicit warning and a maintenance window; it
never pretends a live prefix change is possible.

## 5. DNS64

- BIND9 is managed through a dedicated include file
  `/etc/bind/nat64-manager.conf`, included from `named.conf.options` by the
  installer. The app does not rewrite unrelated BIND configuration.
- Rendered content includes optional `forwarders` and a `dns64` clause with the
  configured prefix, exclusions (pool6, client prefixes, loopback, link-local),
  and `clients { any; }`.
- Changes are validated with `named-checkconf -z` before install and applied with
  `rndc reconfig` (fallback: restart `named`/`bind9`).
- Health check uses `dig` against the local resolver plus `systemctl is-active`.
- AAAA synthesis test queries `ipv4only.arpa` (RFC 7050). Success requires an
  AAAA answer inside the configured pool6 prefix. The IPv4-only resolution test
  queries `A ipv4only.arpa` and expects an IPv4 answer.
- Query statistics come from `rndc stats`/`named.stats`. BIND does not expose
  per-qtype counters in stock statistics; the UI shows total queries, cache hits,
  recursion, and failures, clearly labelled. It does not invent A/AAAA split.

## 6. Firewall

- Managed rules live only in the table `inet nat64mgr`; unrelated tables
  (including Jool's own rules and Docker's) are untouched.
- Input and forward chains use `ct state established,related accept` first, so
  legitimate NAT64 return traffic is not cut. Forward base policy is `accept`
  unless the administrator explicitly chooses drop.
- Pool4 and pool6 destinations are accepted explicitly for inbound translated
  traffic.
- Allowlist/denylist sets, per-rule rate limits, global new-connection rate
  limiting, logging, and control-plane protection are rendered from DB rules.
- Enabling the firewall requires:
  - at least one management allowlist entry (to prevent SSH lockout),
  - a passed `nft -c -f` dry run,
  - explicit approval.
- The plan warns that an incorrect allowlist can lock out the administrator.

## 7. Network configuration and rollback

The app manages only its own files:

- `/etc/sysctl.d/90-nat64-manager.conf` (IPv4/IPv6 forwarding, optional rp_filter)
- `/etc/netplan/90-nat64-manager.yaml` (MTU, optional default routes; never
  reassigns WAN addresses silently)
- `/etc/bind/nat64-manager.conf`
- `/etc/nftables.d/nat64-manager.nft`

Every applied config is copied to a `ConfigurationBackup` DB row (JSON content +
SHA-256) and to `/var/backups/nat64-manager/`. Network apply is transactional:
`netplan generate` must pass, then apply; a systemd rollback timer restores the
previous file after 120 s unless the admin confirms. If the timer/helper is
unavailable, the API reports it and does not apply.

## 8. Security

- Argon2id password hashing (via `pwdlib`), password policy, account lockout after
  repeated failures.
- Short-lived JWT access tokens; refresh tokens are random opaque values stored
  hashed in `auth_sessions`, rotated on refresh, revocable.
- RBAC: `viewer`, `operator`, `admin`, `superadmin`.
  - viewer: read-only
  - operator: request changes, manage non-sensitive records, export
  - admin: approve/apply changes, users, backups, settings
  - superadmin: role changes, HA, destructive restore
- API rate limiting (Redis or in-process), login-specific limiter, audit log for
  auth and every mutation/approval.
- Secrets (SMTP/HA tokens/API keys) encrypted at rest with Fernet using
  `APP_ENCRYPTION_KEY`; never returned by the API.
- CORS allowlist; security headers; HSTS when HTTPS; HTTPS terminated by Caddy or
  nginx in the supplied deployment files.
- CSRF: bearer-token API does not use cookie auth; if cookie mode is enabled the
  backend requires a CSRF header. The default SPA stores tokens in memory +
  sessionStorage and sends `Authorization: Bearer`.
- Jool CLI, BIND files, and shell are never exposed to the browser.

## 9. High availability (honest scope)

- Design: two gateways, VRRP/keepalived VIP, shared PostgreSQL/Redis or async
  config sync, BIND primary/secondary, identical pool4.
- Implemented in v1:
  - HA node registry + health endpoint + config version/hash,
  - `/api/v1/ha/sync` pull/push of non-secret configuration with scoped API token,
  - keepalived config rendering and a change request to install it on a node,
  - optional Jool session synchronization settings (`ss-enabled`, `session proxy`)
    in the UI as **experimental**.
- Not claimed: seamless NAT64 session survival after failover. Jool session sync
  exists but is not validated by this build's automated tests. Documentation and
  UI state this explicitly. Failover will normally drop established sessions
  unless SS is enabled **and** verified in the operator's environment.

## 10. Database schema

Required tables (implemented):

`users`, `roles`, `ipv4_pools`, `ipv4_addresses`, `ipv6_prefixes`,
`jool_instances`, `nat64_sessions`, `network_interfaces`, `dns64_settings`,
`firewall_rules`, `system_metrics`, `audit_logs`, `configuration_backups`.

Supporting tables:

`auth_sessions`, `change_requests`, `firewall_settings`, `network_settings`,
`settings`, `ipv6_clients`, `installation_state`, `ha_nodes`, `ha_settings`.

Alembic initial migration is included. A `create_all` bootstrap path exists for
SQLite development only.

## 11. UI

Dark/light theme, responsive sidebar, 12 menus, 5-second polling. Every value is
either live, explicitly `null`/`not available`, or an explicit status
(`ONLINE`, `OFFLINE`, `DEGRADED`, `UNSUPPORTED`, `NOT_IMPLEMENTED`). Mutating
flows open a plan dialog with dry-run output and require explicit approval.

## 12. Testing strategy

### Unit / API (runs anywhere, SQLite)

- Auth, RBAC, lockout, token rotation, audit.
- Pool validation, overlap detection, CIDR import, address CRUD, utilization math.
- Jool CSV parsers against official-format fixtures.
- DNS64/firewall/netplan renderers and validators.
- Session upsert/expiry, client merge.
- Change request lifecycle (dry-run, approval permission, execution, audit).
- API smoke test of all routers.

### Linux integration (root + Jool + BIND + nftables + netns)

Topology: `gw` + `client` + `server` network namespaces connected by veth.

- TCP/UDP/ICMP translation through Jool.
- DNS64 AAAA synthesis of an A-only zone hosted by BIND.
- pool4 multi-address behaviour and exhaustion.
- Multiple clients; 500 source addresses and 1000 concurrent sessions baseline.
- Session timeout, firewall return traffic, restart recovery.
- Load test records throughput, latency, CPU, RAM, packet loss to JSON.

These tests are **not** executed by the Windows unit-test run. See
`backend/tests/integration/README.md` and `scripts/run-integration-tests.sh`.

## 13. Delivery stages

| Stage | Contents | Status in this build |
|---|---|---|
| 1 | Architecture, directory structure, DB, core backend API | Implemented |
| 2 | Jool adapter, DNS64, pool manager | Implemented (Linux adapter) |
| 3 | React dashboard, monitoring, sessions | Implemented |
| 4 | Security, backup/rollback, automated tests | Implemented |
| 5 | Installation wizard, docs, deployment scripts | Implemented |

## 14. Assumptions / constraints

- Jool **4.x** userspace + kernel module. Jool 3 is rejected (different CLI).
- Linux gateway with systemd; Debian 12/Ubuntu 22.04/24.04 LTS.
- nftables >= 1.0, BIND 9.16+, Python 3.12+, Node 20+ to build frontend.
- IPv6-only clients route the pool6 prefix and DNS to the gateway; the app does
  not configure client CPEs.
- PostgreSQL is the production database. SQLite is a development/test fallback and
  is not recommended beyond single-admin lab use.
- Package installation endpoints are optional and disabled by default
  (`ALLOW_PACKAGE_INSTALL=false`).

## 15. Known limitations (shown in UI/docs)

1. No live pool6 change; instance recreation drops sessions.
2. No exact session creation timestamp from Jool; first-observed is used.
3. No per-client byte counters; displayed as not available.
4. Pool utilization is observed-session based, not BIB-exact per IP.
5. DNS qtype-level statistics not available from stock BIND stats.
6. HA session continuity is experimental and unverified.
7. Windows dev mode cannot exercise the Linux data plane.
8. The supplied integration/load tests require a dedicated lab host; do not run
   them on production without a maintenance window.
