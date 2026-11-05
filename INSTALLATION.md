# Installation quick start

This file is a short index. The authoritative deployment procedure is
[`docs/02-INSTALLATION.md`](docs/02-INSTALLATION.md).

## Choose a mode

| Mode | Use it for | Host control |
|---|---|---|
| Docker Compose | UI/API evaluation, development, demos | Disabled by design |
| Native Linux gateway | Production NAT64 service | Enabled through `hostctl.py` |
| Local development | Backend/frontend work with SQLite | Simulated/unsupported |

## Docker evaluation stack

```bash
cp .env.example .env
# Edit .env:
#   POSTGRES_PASSWORD
#   SECRET_KEY (at least 32 characters)
#   APP_ENCRYPTION_KEY (Fernet key)
#   DEFAULT_ADMIN_PASSWORD
#   TRUSTED_HOSTS=127.0.0.1,localhost
#   CORS_ORIGINS=http://127.0.0.1:8080
docker compose up -d --build
```

Open `http://127.0.0.1:8080`. The Compose stack runs PostgreSQL, the API, and
nginx. It sets `HOST_CONTROL_MODE=disabled` and `COLLECTOR_ENABLED=false`; Jool,
BIND9, nftables, netplan and keepalived are not reachable from the container.
Host-dependent screens will report `UNSUPPORTED`. That is expected, not a bug.

## Local development

Prerequisites: Python 3.12+, Node.js 20+.

```bash
# From the repository root
python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
# Linux/macOS
# .venv/bin/python -m pip install -r backend/requirements-dev.txt

cd backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

In a second terminal:

```bash
cd frontend
npm ci
npm run dev
```

The Vite server proxies `/api` and `/health` to `http://127.0.0.1:8000`, so open
`http://127.0.0.1:5173`.

With no `DEFAULT_ADMIN_PASSWORD`, the first backend start generates a random
bootstrap password and writes it to `backend/data/bootstrap-admin.txt`
(development only). Read it, sign in, change the password, then delete the file.
In production, set `DEFAULT_ADMIN_PASSWORD` for the first start, then remove it
from the environment file.

## Native Linux gateway

```bash
sudo ./deploy/scripts/install.sh
sudoedit /etc/nat64-manager/nat64-manager.env   # replace every CHANGE_ME
sudo systemctl start nat64-manager-migrate.service
sudo systemctl start nat64-manager.service
```

Then complete the OS-level steps (PostgreSQL, BIND9 include, Jool, nftables,
netplan, TLS reverse proxy) and the in-app 11-step Installation Wizard exactly
as described in [`docs/02-INSTALLATION.md`](docs/02-INSTALLATION.md).

Do not expose the API directly to the internet. Terminate TLS with nginx or
Caddy on the same host and keep uvicorn bound to `127.0.0.1:8000`.

## Verification after installation

```bash
curl -fsS http://127.0.0.1:8000/health | python3 -m json.tool
systemctl status nat64-manager.service --no-pager
journalctl -u nat64-manager.service -n 100 --no-pager
```

A healthy control plane reports `"status": "ok"` and
`"database.ok": true`. On a real gateway, `host.available` must be `true` and
`services.jool.status` must be `online` before the wizard can pass step 1.

## Upgrading

1. Back up configuration in **Backup & Restore** and copy it off-host.
2. Stop the API.
3. Replace the application tree (keep `/etc/nat64-manager`, `/var/lib/nat64-manager`
   and `/var/backups/nat64-manager`).
4. Reinstall the helper and sudoers policy from `deploy/`.
5. Run `systemctl start nat64-manager-migrate.service`.
6. Start the API, sign in, and re-run the Installation Wizard checks.

Never edit host-managed files (`/etc/netplan/90-nat64-manager.yaml`,
`/etc/nftables.d/nat64-manager.nft`, `/etc/bind/nat64-manager.conf`,
`/etc/keepalived/keepalived.conf`) by hand; the next approved apply overwrites
them.

## Production checklist

- [ ] PostgreSQL is the database; SQLite is not used.
- [ ] `SECRET_KEY`, `APP_ENCRYPTION_KEY`, database password and bootstrap
      password are unique and stored outside the repository.
- [ ] `ENVIRONMENT=production`, `DEBUG=false`, `ENABLE_DOCS=false`.
- [ ] `TRUSTED_HOSTS` and `CORS_ORIGINS` name your real origin, not `*`.
- [ ] `SECURE_COOKIES=true` behind HTTPS.
- [ ] uvicorn is bound to loopback behind nginx/Caddy.
- [ ] The helper is root-owned `0755`; sudoers permits only that exact command.
- [ ] A management allowlist is configured before enabling the firewall.
- [ ] A configuration backup exists and has been test-restored on a lab host.
- [ ] The Linux integration suite has been run on the target or an equivalent
      lab (see [`docs/08-TESTING-LINUX-LAB.md`](docs/08-TESTING-LINUX-LAB.md)).
