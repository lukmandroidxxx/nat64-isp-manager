# 02 – Installation

This document describes a production installation on a Linux gateway and an
optional application-only Docker stack. It does **not** claim that the Linux
steps have been executed on the authoring host (Windows). Treat every command as
a procedure to run and verify in your own lab first.

## 1. Supported systems

Default installer target:

- Debian 13 or Ubuntu 24.04
- Python 3.12+
- systemd
- Jool 4.x (kernel module + userspace tools)
- BIND9
- nftables
- netplan
- PostgreSQL 14+ (production)

Debian 12 and Ubuntu 22.04 are supported when Python 3.12+ is installed
manually and selected with `PYTHON=/usr/bin/python3.12`:

```bash
sudo PYTHON=/usr/bin/python3.12 ./deploy/scripts/install.sh
```

The native installer assumes `apt-get`, `systemd`, and a source checkout. It
does not install or configure Jool, BIND9, nftables, netplan, PostgreSQL, or a
TLS reverse proxy. Those steps follow.

## 2. Reference topology

Example addresses used throughout the docs:

| Role | Interface | Address |
|---|---|---|
| WAN IPv4 | `wan0` | `198.51.100.10/24`, gateway `198.51.100.1` |
| WAN IPv6 | `wan0` | `2001:db8:100::10/64`, gateway `2001:db8:100::1` |
| LAN IPv6 | `lan0` | `2001:db8:64::1/64` |
| Client prefix | `lan0` | `2001:db8:64::/64` (SLAAC) |
| NAT64 pool6 | – | `64:ff9b::/96` |
| Public pool4 | – | `203.0.113.1` – `203.0.113.32` |

The public IPv4 addresses must be routed to the gateway by upstream equipment,
or assigned to the gateway interface. Jool translates packets, but return
traffic still has to reach the host.

## 3. OS prerequisites

```bash
sudo apt-get update
sudo apt-get install -y \
  python3 python3-venv python3-pip \
  postgresql postgresql-client \
  bind9 bind9utils bind9-dnsutils \
  nftables \
  netplan.io \
  jool-tools jool-dkms \
  nginx \
  ca-certificates curl rsync sudo systemd
```

Package names vary by distribution. Confirm the Jool version:

```bash
jool --version        # must report 4.x
modprobe jool
lsmod | grep jool
```

Persist the module across reboots:

```bash
echo jool | sudo tee /etc/modules-load.d/jool.conf
```

If Secure Boot is enabled, the DKMS module must be signed or MOK-enrolled;
otherwise `modprobe jool` fails and the application reports
`not_installed`/`unsupported`.

## 4. PostgreSQL

```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE nat64 LOGIN PASSWORD 'replace-with-a-strong-password';
CREATE DATABASE nat64 OWNER nat64;
SQL
```

Restrict network access to localhost in `pg_hba.conf` unless the database is
managed separately. The application connects with
`postgresql+psycopg://nat64:...@127.0.0.1:5432/nat64`.

Do not use SQLite in production. The wizard reports a warning for SQLite, and
`SQLITE_FALLBACK_ALLOWED=false` should remain set in production.

## 5. Application installation

From a checkout:

```bash
sudo ./deploy/scripts/install.sh
```

The installer:

1. installs `ca-certificates curl rsync sudo systemd`; when `PYTHON` is a
   command name it also installs the matching Python/venv/pip packages, and
   when `PYTHON` is an absolute path it expects that interpreter to be
   installed already;
2. verifies Python >= 3.12;
3. creates the `nat64mgr` system user/group and directories;
4. copies the source tree to `/opt/nat64-manager` (excluding `.git`, `.venv`,
   `node_modules`, caches, `backend/data`, `.env`);
5. builds the frontend if `frontend/dist/index.html` is missing (requires Node
   20+), otherwise uses the prebuilt `dist`;
6. creates `/opt/nat64-manager/venv` and installs `backend/requirements.txt`;
7. installs the root-owned helper as `/opt/nat64-manager/hostctl.py`
   (`root:root 0755`), then makes the tree root-owned and not group/world
   writable;
8. installs and validates `/etc/sudoers.d/nat64-manager` with the resolved
   Python interpreter path;
9. creates `/etc/nat64-manager/nat64-manager.env` from `.env.example`,
   generates `SECRET_KEY` and `APP_ENCRYPTION_KEY`, and sets `HOSTCTL_PYTHON`
   to the same resolved interpreter path;
10. installs and enables the two systemd units.

It deliberately does **not** start the API unless `--start` is supplied.

### 5.1 Environment file

Edit `/etc/nat64-manager/nat64-manager.env` (mode `0640 root:nat64mgr`) and
replace every `CHANGE_ME`:

- `DATABASE_URL` – PostgreSQL password;
- `DEFAULT_ADMIN_PASSWORD` – first-run bootstrap only;
- `CORS_ORIGINS`, `TRUSTED_HOSTS` – your real UI origin/hostname;
- `SECRET_KEY` – at least 32 random characters;
- `APP_ENCRYPTION_KEY` – Fernet key for secrets at rest.

Generate secrets with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set `ENVIRONMENT=production`, `DEBUG=false`, `ENABLE_DOCS=false`, and
`SECURE_COOKIES=true` when serving over HTTPS.

`HOSTCTL_PYTHON` must match the interpreter in the sudoers rule exactly. The
installer resolves `PYTHON` (for example `python3` or
`/usr/bin/python3.12`) and writes the same absolute path to both the environment
file and `/etc/sudoers.d/nat64-manager`. If you later change the interpreter,
update both files and run `visudo -cf /etc/sudoers.d/nat64-manager`.

### 5.2 Database migration

```bash
sudo systemctl start nat64-manager-migrate.service
sudo systemctl status nat64-manager-migrate.service --no-pager
```

The unit runs `alembic upgrade head` from `/opt/nat64-manager/backend` as
`nat64mgr`. Re-run it after every application upgrade.

### 5.3 Start the API

```bash
sudo systemctl start nat64-manager.service
sudo systemctl status nat64-manager.service --no-pager
curl -fsS http://127.0.0.1:8000/health | python3 -m json.tool
```

Expected control-plane health:

- HTTP 200;
- `"status": "ok"`;
- `"database": {"ok": true, ...}`;
- `"host": {"available": true, ...}` on a real gateway;
- `"services": {"jool": {"status": "online", ...}}` once Jool is usable.

If `host.available` is false, check the helper path, sudoers, and
`HOST_CONTROL_MODE`. Do not continue to the wizard until host access works.

Verify the helper directly as the service account:

```bash
sudo -u nat64mgr sudo -n /usr/bin/python3 /opt/nat64-manager/hostctl.py <<'JSON'
{"op": "ping", "params": {}}
JSON
```

The response must be a JSON object with `"ok": true`.

## 6. Jool 4

Jool instances are normally created through the UI (Jool Management or the
wizard). For a manual bootstrap:

```bash
sudo modprobe jool
sudo jool instance add nat64 --netfilter --pool6 64:ff9b::/96
sudo jool -i nat64 global update manually-enabled true
sudo jool -i nat64 instance display
```

Then register the same instance in the application (Import/Create) so the
database and live state agree. The application only supports the **netfilter**
framework and Jool **4.x**. Jool 3 is rejected.

Key rules:

- `pool6` is immutable on a live instance. Changing the NAT64 prefix requires
  recreating the instance, which drops BIB/session state. The UI warns about
  this.
- The DNS64 prefix must equal the Jool `pool6`.
- pool4 entries are added per protocol (TCP, UDP, ICMP) per IPv4 address and
  port range.

## 7. BIND9 DNS64

1. Install BIND9 (`named` or `bind9` service name, depending on distribution).
2. Ensure `/etc/bind/nat64-manager.conf` exists before including it. An empty
   file is valid inside `options {}`:

   ```bash
   sudo install -m 0640 -o root -g bind /dev/null /etc/bind/nat64-manager.conf
   ```

3. Add the include **inside** the `options { ... }` block of
   `/etc/bind/named.conf.options`:

   ```
   options {
       directory "/var/cache/bind";
       recursion yes;
       allow-recursion { 2001:db8:64::/64; localhost; };

       include "/etc/bind/nat64-manager.conf";
   };
   ```

4. Validate and reload:

   ```bash
   sudo named-checkconf
   sudo systemctl restart named    # or bind9
   ```

5. In the UI, configure DNS64 (prefix, upstreams, forward-only, DNSSEC) and
   apply it through a change request. The helper:

   - rejects fragment-level directives such as `zone`, `view`, `include`,
     `controls`, `key`, `acl`, and `logging`;
   - wraps the fragment in `options { ... }` and runs `named-checkconf` on a
     temporary file;
   - writes the real include atomically as `root:bind 0640`;
   - runs `named-checkconf` again and restores the previous file on failure;
   - reloads with `rndc reconfig` and falls back to a service restart.

The rendered fragment contains `forwarders`, `forward only` (optional),
`dnssec-validation`, and a `dns64 <prefix>` block with exclusions. See
[`05-NETWORK-DNS64.md`](05-NETWORK-DNS64.md) for a sample.

## 8. nftables

The application manages only `table inet nat64mgr`, written to
`/etc/nftables.d/nat64-manager.nft`. It does not flush or replace other tables.

1. Create the include directory and ensure the main nftables configuration
   loads it:

   ```bash
   sudo mkdir -p /etc/nftables.d
   # /etc/nftables.conf
   include "/etc/nftables.d/*.nft"
   ```

2. Apply the ruleset from the UI. The helper stages the content, runs
   `nft -c -f <staged>`, applies it, then writes the managed file atomically.

3. **Before enabling**, set a management allowlist (`mgmt4`/`mgmt6`) and
   management ports. The renderer refuses to enable control-plane protection
   without an allowlist, specifically to avoid SSH lockout.

4. To recover from a bad firewall change, use the provider console or a
   physically attached keyboard, then:

   ```bash
   sudo nft delete table inet nat64mgr
   sudo rm -f /etc/nftables.d/nat64-manager.nft
   ```

The generated table includes conntrack established/related accepts, pool6 and
pool4 forward accepts, optional MSS clamping, invalid-packet drops, optional
SYN rate limiting, per-rule matches, and explicit default policies. Details and
a sample are in [`05-NETWORK-DNS64.md`](05-NETWORK-DNS64.md).

## 9. netplan and sysctl

The application manages:

- `/etc/netplan/90-nat64-manager.yaml`
- `/etc/sysctl.d/90-nat64-manager.conf`

It configures selected interfaces (MTU optional, addresses/routes optional) and
IPv4/IPv6 forwarding. It does not manage unrelated interfaces.

Apply through the UI. The helper:

1. validates YAML and sysctl content;
2. snapshots the previous files under `/var/backups/nat64-manager/network-*`;
3. writes the files atomically;
4. runs `netplan generate`, `sysctl --system`, `netplan apply`;
5. schedules an automatic rollback with `systemd-run` after
   `NETWORK_ROLLBACK_SECONDS` (default 120, clamped to 30–900);
6. restores the snapshot and re-applies if any step or the timer scheduling
   fails.

**After applying, confirm the change in the UI before the timer expires.**
Confirmation cancels the rollback. If you lose connectivity and cannot confirm,
the gateway restores the previous network configuration automatically.

A sample netplan/sysctl file is in
[`05-NETWORK-DNS64.md`](05-NETWORK-DNS64.md).

## 10. TLS reverse proxy

Keep uvicorn on `127.0.0.1:8000`. Use nginx or Caddy on the same host.

### nginx

```bash
sudo cp deploy/nginx/nat64-manager.conf /etc/nginx/conf.d/nat64-manager.conf
```

Edit `server_name` and certificate paths. The production template already
targets `server 127.0.0.1:8000;`. The Compose stack uses the separate
`deploy/nginx/compose-nginx.conf` with `server api:8000;`. Validate and reload:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

The backend already sets CSP, HSTS, frame and content-type headers; do not
duplicate conflicting headers in the proxy.

### Caddy

`deploy/caddy/Caddyfile` targets `127.0.0.1:8000` and obtains certificates
automatically. Replace the hostname and ACME email.

### Headers and origins

Set `TRUSTED_HOSTS` to the public hostname and `CORS_ORIGINS` to the exact UI
origin. Never leave `*` in production. If TLS terminates at the proxy, set
`SECURE_COOKIES=true` and ensure `--proxy-headers`/forwarded IPs are configured
(the systemd unit runs uvicorn with `--proxy-headers` and trusts
`127.0.0.1`).

## 11. First login and the 11-step wizard

Open the UI and sign in with:

- `DEFAULT_ADMIN_USERNAME` (default `admin`);
- `DEFAULT_ADMIN_PASSWORD` on first start. If unset, the generated password is
  written to `backend/data/bootstrap-admin.txt` in development, or logged as a
  path in production. Change it immediately and remove the bootstrap password
  from the environment.

The wizard steps are:

1. **Host prerequisites** – Linux, helper, Jool 4, nftables, BIND, netplan.
2. **Database** – PostgreSQL in production (SQLite only a warning).
3. **Network interfaces** – WAN4, WAN6, LAN6 selection and detection.
4. **NAT64 prefix (pool6)** – immutable prefix; must match DNS64.
5. **Public IPv4 pool** – at least one enabled address with a port range.
6. **Jool instance** – enabled database instance present in live Jool.
7. **Synchronize pool4** – live Jool pool4 matches the database for all three
   protocols.
8. **DNS64** – BIND online and RFC 7050 synthesis returns an address inside the
   prefix.
9. **Firewall** – optional; warning if disabled, failure if enabled without a
   management allowlist.
10. **Security** – active superadmin, password rotation, production secrets.
11. **Verification** – all required checks pass.

A step that cannot be verified on the host is reported as `unsupported`; it does
not pass silently. On a real gateway, step 11 stays failed until every required
check is genuinely `ok`.

## 12. Docker Compose (application-only)

```bash
cp .env.example .env
# edit POSTGRES_PASSWORD, SECRET_KEY, APP_ENCRYPTION_KEY,
# DEFAULT_ADMIN_PASSWORD, and set:
#   TRUSTED_HOSTS=127.0.0.1,localhost
#   CORS_ORIGINS=http://127.0.0.1:8080
docker compose up -d --build
docker compose ps
docker compose logs -f api
```

Open `http://127.0.0.1:8080`.

What this mode provides: UI, API, PostgreSQL, migrations at container start,
backup volume, nginx. What it does not provide: host Jool/BIND/nftables/netplan
control, session collection, or production TLS. `HOST_CONTROL_MODE=disabled`
and `COLLECTOR_ENABLED=false` are fixed in the Compose file.

The container entrypoint runs `alembic upgrade head` with up to 30 retries before
starting uvicorn. The image runs as the unprivileged `nat64mgr` user and has no
sudo or host helper access.

## 13. Upgrade

1. Create a configuration backup in the UI and copy the JSON off-host.
2. `sudo systemctl stop nat64-manager.service`.
3. Replace `/opt/nat64-manager` from the new release (preserve `/etc`,
   `/var/lib`, `/var/backups`).
4. Reinstall the helper and sudoers policy:

   ```bash
   sudo install -m 0755 -o root -g root deploy/hostctl/hostctl.py /opt/nat64-manager/hostctl.py
   sudo install -m 0440 -o root -g root deploy/sudoers/nat64-manager /etc/sudoers.d/nat64-manager
   sudo visudo -cf /etc/sudoers.d/nat64-manager
   ```

5. `sudo systemctl start nat64-manager-migrate.service`
6. `sudo systemctl start nat64-manager.service`
7. Sign in, review Change Requests, and re-run the wizard checks.

Review `PROJECT_PLAN.md` and the release notes for schema or behaviour changes.
Downgrades are not automatic; restore the database and configuration backup on a
lab host first.

## 14. Uninstall

```bash
sudo systemctl disable --now nat64-manager.service nat64-manager-migrate.service
sudo rm /etc/systemd/system/nat64-manager.service /etc/systemd/system/nat64-manager-migrate.service
sudo rm /etc/sudoers.d/nat64-manager
sudo systemctl daemon-reload
```

Remove `/opt/nat64-manager`, `/etc/nat64-manager`, `/var/lib/nat64-manager`, and
`/var/backups/nat64-manager` only after exporting configuration and backups.
Removing the managed netplan/nftables/BIND include files changes host
connectivity and filtering; do it deliberately from a console.

## 15. Post-install verification checklist

| Check | Command | Expected |
|---|---|---|
| API liveness | `curl -fsS http://127.0.0.1:8000/health` | HTTP 200, `status: ok` |
| Database | health JSON | `database.ok: true`, dialect `postgresql` |
| Helper | `sudo -u nat64mgr sudo -n ... hostctl.py` with `ping` | `ok: true` |
| Jool | `jool --version`, `jool instance display` | 4.x, instance present |
| pool4 | `jool -i <name> pool4 display --tcp` | DB addresses/ranges present |
| DNS64 | `dig @127.0.0.1 AAAA ipv4only.arpa` | AAAA inside pool6 |
| Firewall | `nft list table inet nat64mgr` | managed table present |
| Network | `netplan get`, `sysctl net.ipv4.ip_forward` | values match UI |
| Proxy | `curl -I https://<host>/` | valid TLS, security headers |
| Wizard | UI step 11 | all required checks `ok` |

These are procedures, not recorded results. Record your own output before
declaring the installation verified.
