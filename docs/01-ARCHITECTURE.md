# 01 – Architecture

## 1. Purpose

NAT64 ISP Manager is a control plane for an ISP access network that gives
IPv6-only subscribers access to IPv4-only destinations. It does not forward
packets itself. Packet translation is performed by **Jool 4** in the Linux
kernel; DNS64 is performed by **BIND9**; filtering by **nftables**; and interface
configuration by **netplan**.

The application provides:

- safe configuration and visibility of the Jool instance and its pool4;
- IPv6 client and NAT64 session inventory;
- a BIND DNS64 fragment, nftables ruleset, and netplan/sysctl configuration;
- an approval workflow around every host mutation;
- backups, audit, monitoring, HA preview, and an installation wizard.

### Non-goals

- It is not a BGP/PPP/BNG/AAA system. Subscriber sessions are provisioned by the
  access network, not by this application.
- It does not allocate an IPv4 address exclusively to a subscriber.
- It does not provide per-subscriber accounting, quotas, or CGNAT logging for
  law-enforcement retention beyond the session snapshot described below.
- It does not implement OIDC/SAML/MFA.
- It does not replace a monitoring platform for long-term metrics.

## 2. Deployment topologies

### 2.1 Native gateway (production)

All components run on one Linux gateway:

```
             management network / VPN
                      |
                 nginx or Caddy (TLS)
                      |
             127.0.0.1:8000 uvicorn (nat64mgr)
                /              \
      PostgreSQL            hostctl.py (root)
                                  |
        +-------------------------+-------------------------+
        |            |            |            |            |
      jool        named       nftables     netplan      keepalived
        |            |            |            |
   kernel NAT64   DNS64       filtering    interfaces/routes
```

The backend runs as the unprivileged `nat64mgr` user. The only privileged path
is a root-owned Python helper invoked through a single sudoers rule with a JSON
request on stdin. There is no shell, no command string, and no Jool CLI exposed
to the browser.

The API process also runs the collector. Run **exactly one** uvicorn worker;
adding workers would duplicate collection. Scale the UI by putting a reverse
proxy in front, or move collection into a separate service before scaling API
workers.

### 2.2 Application-only Docker Compose

`docker-compose.yml` runs PostgreSQL, the API image, and nginx on
`http://127.0.0.1:8080`. It explicitly sets:

```yaml
HOST_CONTROL_MODE: disabled
COLLECTOR_ENABLED: "false"
```

This stack is for evaluating the UI, API, database, approval flow, and
documentation. It cannot see the host's Jool, BIND, nftables, netplan or
keepalived state. Host-dependent endpoints return `UNSUPPORTED` with a reason;
they never simulate success. Do not use this mode to manage a production
gateway.

### 2.3 Development

Windows/macOS/Linux with SQLite, the Vite dev server, and host control either
`auto` (detected and usually unsupported) or `disabled`. Development mode must
never be used to claim Linux data-plane validation.

## 3. Components

### 3.1 Frontend

React 19 + TypeScript + Vite. The SPA:

- uses a single same-origin API client and an access token held in memory with a
  refresh flow;
- polls most operational pages every 5 seconds (`usePolling`, default 5000 ms);
- provides dark/light themes, responsive layouts, tables, dialogs, toasts;
- exports sessions, clients, and audit entries as CSV;
- includes 15 routes: the 12 required pages plus Change Requests, High
  Availability, and the Installation Wizard.

There is **no WebSocket endpoint**. "Live" updates are HTTP polling; the
"updated at" indicator reflects the last successful poll.

### 3.2 API

FastAPI under `/api/v1`, plus unauthenticated `/health`. Routers cover auth,
users/roles, dashboard, Jool, pools, clients, sessions, DNS64, firewall,
network, changes, backups, settings, wizard, HA, and audit.

Cross-cutting behaviour:

- JWT access and refresh tokens; refresh tokens rotate and are tracked in
  `auth_sessions`;
- Argon2 password hashing via `pwdlib`;
- account lockout after repeated failures;
- per-user/IP rate limiting (in-process; Redis is optional and not required);
- request IDs and structured error envelopes;
- security headers and optional strict host checking;
- `CommitAPIRoute` commits the database transaction before the response is sent,
  while error responses roll back. This prevents read-after-write surprises
  (for example, a POST followed immediately by a GET).

### 3.3 Database

SQLAlchemy 2.0 with Alembic migrations. PostgreSQL is required in production.
SQLite is supported for development and tests; the wizard reports a warning when
SQLite is active. The schema is dialect-neutral and uses JSON columns for plans,
audit detail, metrics, and flexible settings.

### 3.4 Collector

A background task started by the API when `COLLECTOR_ENABLED=true`. Every
`COLLECTOR_INTERVAL_SECONDS` (default 5 s) it:

1. checks Jool status and version;
2. collects session CSV output for enabled instances;
3. upserts sessions and derives client inventory;
4. expires/stales sessions that disappeared;
5. samples system metrics;
6. prunes data older than the configured retention.

The collector is read-only with respect to the host. All host mutations go
through explicit change requests.

### 3.5 Host helper

`deploy/hostctl/hostctl.py` is a standalone Python program installed as
`root:root 0755`. The backend invokes it as:

```
sudo -n /usr/bin/python3 /opt/nat64-manager/hostctl.py
```

and writes a JSON object `{"op": "...", "params": {...}}` to its stdin. The
helper validates every parameter with allowlists and strict parsers, executes
only fixed argv arrays (never a shell), snapshots files before mutation, and
returns structured stdout/stderr/commands. Operations are grouped under
`jool`, `ip`, `system`, `dns`, `firewall`, `network`, `packages`, `selftest`,
and `ha`.

The sudoers policy allows exactly one command and no arguments. See
[`06-SECURITY.md`](06-SECURITY.md).

### 3.6 Host services

| Service | Managed state | Apply path |
|---|---|---|
| Jool 4 | instances, global flags, pool4 entries | direct typed commands via helper |
| BIND9 | `/etc/bind/nat64-manager.conf` included inside `options {}` | change request → validate → write → restart |
| nftables | `table inet nat64mgr` in `/etc/nftables.d/nat64-manager.nft` | change request → `nft -c` → apply |
| netplan | `/etc/netplan/90-nat64-manager.yaml`, `/etc/sysctl.d/90-nat64-manager.conf` | change request → snapshot → apply → timed rollback until confirmed |
| keepalived | `/etc/keepalived/keepalived.conf` | change request → validate → write → restart |

## 4. Request flow

### 4.1 Read path

1. Browser calls `GET /api/v1/...` with a bearer token.
2. FastAPI validates the token, loads the user and RBAC permissions.
3. The router queries PostgreSQL.
4. For host state, the service calls the helper; if host control is disabled or
   the platform is unsupported, the response carries `UNSUPPORTED` and a reason.
5. Response returns; the SPA polls again after 5 seconds.

### 4.2 Host mutation path

1. User selects an action in the UI.
2. `POST /api/v1/changes` (or a resource-specific endpoint that creates a
   change) builds a **dry-run plan** without touching the host.
3. The plan is stored with summary, risk, target, warnings, errors, the exact
   argv commands that would run, before/after state, and reversibility.
4. An authorised approver approves the request. Critical changes cannot be
   self-approved by a non-superuser.
5. Execution creates an automatic configuration backup (except for
   `backup.restore`), then calls the action executor.
6. The executor calls the host helper with typed parameters, and the helper
   performs the change.
7. Result, error, executor identity, and timestamp are stored; an audit entry is
   written. Requests expire after 60 minutes.

## 5. Data model

| Table | Purpose |
|---|---|
| `roles`, `users`, `auth_sessions` | RBAC, accounts, refresh-token rotation |
| `ipv4_pools`, `ipv4_addresses` | Public IPv4 pool4 and per-address port ranges |
| `jool_instances` | Jool instance name, pool6, framework, observed pool4, sync state |
| `ipv6_prefixes`, `ipv6_clients` | Client prefixes and observed IPv6 client inventory |
| `nat64_sessions` | Observed active/expired/stale NAT64 sessions |
| `dns64_settings` | Prefix, upstreams, BIND paths, health and synthesis state |
| `firewall_settings`, `firewall_rules` | Managed nftables policy and typed rules |
| `network_settings`, `network_interfaces` | Interface roles, addresses, routes, MTU, snapshots |
| `traffic_samples`, `system_metrics` | Sampled operational history |
| `audit_logs` | Durable audit trail (JSON detail) |
| `configuration_backups` | Versioned configuration snapshots with checksums |
| `change_requests` | Dry-run plans, approvals, execution results |
| `settings` | Application key/value settings |
| `installation_state` | Wizard progress and last check results |
| `ha_settings`, `ha_nodes` | VRRP parameters, encrypted secret, peer inventory |

### 5.1 Session semantics

A NAT64 session row is a snapshot of a Jool session as observed by the
collector:

- `first_seen_at` is when **this manager first observed** the session, not the
  Jool creation time. Jool does not expose a creation timestamp in the session
  listing used here.
- `last_seen_at` advances while the session remains present.
- `expires_in_seconds` is Jool's remaining lifetime where available.
- `bytes_up`/`bytes_down` are `null`. The available Jool session output does not
  include per-session byte counters. Traffic sampling may still show aggregate
  interface counters, but those are not attributed to a session.
- Rows transition to `stale` when the collector cannot confirm them recently,
  and to `expired` when they disappear or expire. Retention is controlled by
  `SESSION_RETENTION_HOURS`.

### 5.2 Allocation semantics

An address row in `ipv4_addresses` is a **pool4 entry**, not a subscriber
assignment. Jool chooses an address/port translation per connection and reuses
bindings where possible. Utilisation is derived from observed active sessions
and is explicitly labelled as such. Read
[`04-IPV4-ALLOCATION.md`](04-IPV4-ALLOCATION.md) before sizing a deployment.

## 6. Security architecture

Three boundaries are enforced:

1. **Browser → API:** authenticated, authorised, validated, rate-limited.
   The browser has no access to a shell or raw host command.
2. **API → helper:** a single sudoers command with JSON on stdin. Every
   parameter is parsed, range-checked, allowlisted, and converted to a fixed
   argv array. Command output is logged but never executed.
3. **Helper → host:** the helper writes only managed paths, snapshots before
   rewriting, validates configuration with native checkers before reload, and
   supports rollback for network changes.

Details and threat model: [`06-SECURITY.md`](06-SECURITY.md).

## 7. Availability architecture

The application supports a two-node keepalived VRRP design:

- a virtual IP on the LAN/WAN interface;
- MASTER/BACKUP state derived from `HA_ROLE`;
- VRRP authentication secret encrypted at rest;
- peer health checks through `/health`;
- export/import of non-secret configuration.

It does **not** replicate PostgreSQL, Jool BIB/session state, conntrack state, or
DNS zone data, and it does not automate fencing. Session continuity is
experimental and must be validated in a lab. See
[`07-HIGH-AVAILABILITY.md`](07-HIGH-AVAILABILITY.md).

## 8. Failure behaviour

| Failure | Behaviour |
|---|---|
| Host helper missing/unsupported | Host endpoints return `UNSUPPORTED`; UI shows the reason; no fake data |
| Jool missing or version < 4 | Status `not_installed`/`version_incompatible`; pool4 sync cannot run |
| Collector cannot read Jool | Health degrades; last-known session data remains until retention |
| BIND apply invalid | `named-checkconf` rejects before restart; previous file retained |
| nftables apply invalid | `nft -c` rejects; existing table retained |
| netplan apply succeeds but operator never confirms | Automatic rollback after the configured timer (default 120 s, 30–900 s bounds) |
| rollback timer cannot be scheduled | Change is reverted immediately and execution fails |
| API request fails | Transaction rolls back; error envelope includes request ID |
| Backup creation fails before a change | Execution stops; host is not mutated |

## 9. Technology stack

- Backend: Python 3.12+, FastAPI, SQLAlchemy 2, Alembic, Pydantic 2, PyJWT,
  pwdlib/Argon2, psutil, cryptography, httpx.
- Database: PostgreSQL 14+ (production), SQLite (development/tests).
- Frontend: React 19, TypeScript, Vite 6, React Router, Tailwind, Recharts,
  lucide-react.
- Host: Jool 4.x, BIND9, nftables, netplan, systemd, keepalived (optional),
  Debian 13 or Ubuntu 24.04 (default installer targets).
- Reverse proxy: nginx or Caddy.

## 10. Repository map

```
backend/app/core/        settings, database, security, RBAC, routing, errors, logging
backend/app/models/      SQLAlchemy models and enums
backend/app/services/    auth, jool, ipv4, monitoring, dns64, firewall, network,
                         changes, actions, backup, ha, wizard, collector, host client
backend/app/api/         FastAPI routers
backend/tests/           unit/API tests and Linux integration suite
backend/alembic/         schema migrations
frontend/src/            pages, components, hooks, API client, theme
deploy/hostctl/          privileged helper
deploy/scripts/          native installer
deploy/systemd/          API and migration units
deploy/sudoers/          exact sudo policy
deploy/nginx|caddy/      reverse proxy templates
deploy/docker/           application image and entrypoint
scripts/                 netns lab, load test, integration runner
docs/                     this documentation set
```
