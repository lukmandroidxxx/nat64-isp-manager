# NAT64 ISP Manager

A control plane for an IPv6-only ISP access network that reaches the IPv4
internet through Jool 4 NAT64, BIND9 DNS64, nftables and netplan on a Linux
gateway. It provides a web UI, a typed API, PostgreSQL-backed state, an approval
workflow for host changes, session/client visibility, and a root-owned host
helper that never exposes a shell or the Jool CLI to the browser.

The application is designed around one gateway serving hundreds of IPv6-only
clients from a pool of public IPv4 addresses. IPv4 addresses are **shared
dynamically per connection**. They are not assigned exclusively to subscribers.

## Status and verification

This repository was authored on Windows. The web application, database layer,
API, unit/API tests and production frontend build run there. The Linux data
plane components (Jool, BIND9, nftables, netplan, keepalived) were **not
available on the authoring host** and have **not been executed here**. The
integration and load scripts under `scripts/` and `backend/tests/integration/`
are provided for a Linux lab and must be run there before production use.

Nothing in this project should be read as a claim that:

- the Linux integration tests have passed;
- a particular throughput, latency, or client count has been benchmarked;
- failover preserves established sessions;
- a configuration is safe for your network without review.

The UI reports `UNSUPPORTED`, `NOT_IMPLEMENTED`, or `null` where a capability or
measurement is unavailable. It never fabricates operational data.

## What it does

- **Jool 4 lifecycle** – create/remove/recreate netfilter instances, pause and
  resume, update global settings, display pool4 and sessions.
- **IPv4 pool management** – pools, individual addresses, per-address port
  ranges, CIDR import, overlap validation, and observed per-connection
  utilisation.
- **pool4 reconciliation** – compare the database with live Jool pool4 for TCP,
  UDP and ICMP, then apply additions/removals through an approved change.
- **IPv6 clients and NAT64 sessions** – searchable lists, summaries, session
  expiry metadata, CSV exports, stale/expired session handling.
- **DNS64** – render and validate a BIND `options` fragment, apply it through
  the helper, check service health, and test RFC 7050 synthesis.
- **Firewall** – typed nftables rules rendered into an `inet nat64mgr` table
  with pool6/pool4 accepts, conntrack handling, MSS clamping, optional control
  plane allowlists, and lockout warnings.
- **Network** – netplan and sysctl rendering with a timed automatic rollback,
  explicit confirmation, and snapshot restore.
- **Change approval** – every host-mutating action is a `ChangeRequest` with a
  dry-run plan, approval, execution, audit trail, and automatic pre-change
  backup.
- **Backup and restore** – configuration JSON with checksum validation and
  merge/replace restore modes.
- **High availability** – keepalived VRRP rendering, peer health checks, and
  non-secret configuration export/import. Session continuity is explicitly
  experimental and not automated.
- **Observability** – dashboard, system metrics, audit log, application logs,
  and 5-second UI polling.
- **Installation wizard** – 11 ordered checks from host prerequisites to final
  verification.

## Required pages

Dashboard, Jool Management, IPv4 Pool, IPv6 Clients, NAT64 Sessions, DNS64,
Firewall, Network Interfaces, System Monitoring, Logs, Backup & Restore,
Settings. The UI additionally provides Change Requests, High Availability, and
the Installation Wizard.

## Architecture at a glance

```
Browser (React SPA)
        |
        | HTTPS, same origin, polling every 5 s
        v
FastAPI (single worker, unprivileged nat64mgr)
        |                    \
        |                     \ sudo -n, JSON on stdin
        v                      v
PostgreSQL            root-owned hostctl.py
(or SQLite dev)              |
                             +-- jool 4 (typed arguments only)
                             +-- named/BIND9 (validated fragment)
                             +-- nftables (managed table)
                             +-- netplan/sysctl (snapshot + timer)
                             +-- keepalived (validated config)
```

The backend never accepts a command string from a browser. See
[`docs/06-SECURITY.md`](docs/06-SECURITY.md) for the trust boundary.

## Supported platforms

| Component | Development | Production gateway |
|---|---|---|
| API / UI / DB | Windows, macOS, Linux | Linux (Debian 13 / Ubuntu 24.04 by default) |
| Database | SQLite | PostgreSQL 14+ |
| Jool / BIND / nftables / netplan | `UNSUPPORTED` | Jool 4.x, BIND9, nftables, netplan |
| Docker Compose | UI-only evaluation | Application components only; host services stay native |

The native installer supports Debian 13 and Ubuntu 24.04 out of the box.
Debian 12 and Ubuntu 22.04 work when Python 3.12+ is installed first and
selected with `PYTHON=/usr/bin/python3.12`.

## Quick start

- **Evaluation only (Docker):** `cp .env.example .env`, set the required
  secrets/passwords, set `TRUSTED_HOSTS=127.0.0.1,localhost` and
  `CORS_ORIGINS=http://127.0.0.1:8080`, then `docker compose up -d --build`
  and open `http://127.0.0.1:8080`. This stack has no host control and no
  collector.
- **Development:** run the backend with SQLite and the Vite dev server.
  See [`INSTALLATION.md`](INSTALLATION.md).
- **Production gateway:** follow [`docs/02-INSTALLATION.md`](docs/02-INSTALLATION.md).

## Documentation

| Document | Contents |
|---|---|
| [`docs/01-ARCHITECTURE.md`](docs/01-ARCHITECTURE.md) | Components, data model, request flow, deployment modes |
| [`docs/02-INSTALLATION.md`](docs/02-INSTALLATION.md) | Native gateway install, Docker evaluation stack, first login, wizard |
| [`docs/03-ADMINISTRATION.md`](docs/03-ADMINISTRATION.md) | UI pages, roles, change workflow, backups, exports, routine tasks |
| [`docs/04-IPV4-ALLOCATION.md`](docs/04-IPV4-ALLOCATION.md) | Dynamic per-connection allocation, capacity math, pool4 sync |
| [`docs/05-NETWORK-DNS64.md`](docs/05-NETWORK-DNS64.md) | Interfaces, netplan/sysctl, BIND DNS64, nftables |
| [`docs/06-SECURITY.md`](docs/06-SECURITY.md) | Threat model, host boundary, RBAC, secrets, hardening |
| [`docs/07-HIGH-AVAILABILITY.md`](docs/07-HIGH-AVAILABILITY.md) | VRRP, peer checks, config sync, limitations |
| [`docs/08-TESTING-LINUX-LAB.md`](docs/08-TESTING-LINUX-LAB.md) | Unit tests, netns integration, load test, manual lab checks |
| [`docs/09-TROUBLESHOOTING.md`](docs/09-TROUBLESHOOTING.md) | Diagnostics and recovery for common failures |
| [`PROJECT_PLAN.md`](PROJECT_PLAN.md) | Full design brief, scope decisions, and delivery stages |

## Repository layout

```
backend/            FastAPI application, SQLAlchemy models, Alembic migrations, tests
frontend/           React 19 + TypeScript + Vite SPA
deploy/hostctl/     Root-owned typed host helper
deploy/scripts/     Native installer
deploy/systemd/     API and migration units
deploy/sudoers/     Exact sudo policy for the helper
deploy/nginx/       Production TLS and Compose nginx templates
deploy/caddy/       Alternative Caddy template
deploy/docker/      Multi-stage application image and entrypoint
scripts/            netns lab, load generator, integration runner
docs/               Operator and design documentation
```

## Tests

```bash
# Backend unit/API tests (SQLite, no root)
cd backend
python -m pytest -q -m "not integration"

# Frontend typecheck and production build
cd frontend
npm run build

# Linux data plane (root, Jool 4, netns) - see docs/08
sudo ./scripts/run-integration-tests.sh
```

The Windows test run does not execute the `integration` marker. Integration
tests skip when not on Linux or not root, and the 500-client/1000-session load
test is opt-in via `NAT64_LAB_LOAD=1` or `--load`.

## Known limitations

- pool6 is immutable in a running Jool instance; changing it recreates the
  instance and drops BIB/session state.
- Jool's session listing does not provide a creation timestamp or per-session
  byte counters. The UI shows `first_seen_at` ("First observed by this manager")
  and returns `null` traffic counters.
- Pool utilisation is based on observed active sessions, not exact BIB totals.
- There is no per-subscriber IPv4 reservation, bandwidth quota, or accounting.
- Polling is HTTP every 5 seconds; there is no WebSocket endpoint.
- The Docker Compose stack intentionally cannot manage the host data plane.
- HA does not replicate the database, Jool BIB state, or conntrack state. See
  [`docs/07-HIGH-AVAILABILITY.md`](docs/07-HIGH-AVAILABILITY.md).
- No MFA, OIDC/SAML, or multi-tenant separation is implemented.
