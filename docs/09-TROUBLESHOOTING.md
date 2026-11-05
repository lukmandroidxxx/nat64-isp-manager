# 09 – Troubleshooting

Start with the control plane, then the host service, then the data plane. Do not
"fix" a symptom by disabling validation, inserting fake data or bypassing the
change workflow.

## 1. First five checks

```bash
curl -fsS http://127.0.0.1:8000/health | python3 -m json.tool
systemctl status nat64-manager.service --no-pager
journalctl -u nat64-manager.service -n 200 --no-pager
sudo -u nat64mgr sudo -n /usr/bin/python3 /opt/nat64-manager/hostctl.py <<'JSON'
{"op": "ping", "params": {}}
JSON
```

Expected: HTTP 200, `"status": "ok"`, `"database": {"ok": true}`, and a helper
response with `"ok": true`. On a real gateway also check
`host.available: true` and `services.jool.status: online`.

If the helper ping fails, stop there. The application cannot manage Jool, BIND,
nftables, netplan or keepalived without it.

## 2. Host integration unavailable / `UNSUPPORTED`

Symptom: pages show `UNSUPPORTED`, `host.available: false`, or plans contain
"Host integration unavailable".

Causes and fixes:

| Cause | Check | Fix |
|---|---|---|
| Running on Windows/macOS | `uname -s` | Use a Linux gateway; Docker Compose is app-only |
| `HOST_CONTROL_MODE=disabled` | environment file | Set `sudo` on a native gateway |
| Helper path wrong | `HOSTCTL_PATH` | Must be `/opt/nat64-manager/hostctl.py` |
| Helper not executable/owned | `ls -l` | `install -m 0755 -o root -g root ...` |
| Sudoers missing/invalid | `visudo -cf /etc/sudoers.d/nat64-manager` | Reinstall the policy |
| Wrong Python path | `HOSTCTL_PYTHON` vs sudoers | Make them identical |
| `nat64mgr` cannot sudo | run the ping as `nat64mgr` | Check `Defaults:nat64mgr !requiretty` and the exact command |
| Helper returns an error | run the ping manually | Read the typed error code/message |

Do not set `HOST_CONTROL_MODE=direct` to "make it work" unless you intentionally
run the API as root. That removes the privilege boundary and is not supported for
production.

## 3. Helper errors

The helper returns JSON with `ok: false`, `code`, `message`, `data`, `stdout`,
`stderr`, `commands`.

| Code | Meaning | Action |
|---|---|---|
| `invalid_request`, `invalid_json` | Envelope/params malformed | Check the caller; do not send raw commands |
| `unknown_op`, `unknown_action` | Unsupported operation | Upgrade or correct the request |
| `invalid_instance`, `invalid_prefix`, `invalid_port`, `invalid_interface` | Validation rejected input | Fix the value; the helper will not "try anyway" |
| `unsafe_dns_config` | Forbidden BIND directive | Remove `zone`/`view`/`include`/`acl`/`key`/`controls`/`logging`/etc. |
| `unsafe_nft_config` | Ruleset escapes the managed table | Only one `table inet nat64mgr`; no `flush ruleset`/`include`/other tables |
| `unsafe_ha_config` | Scripts/notifications/includes in keepalived | Use the minimal VRRP renderer |
| `netplan_not_installed` | `netplan` missing | Install `netplan.io` |
| `netplan_invalid` | `netplan generate` rejected YAML | Fix MTU/route/interface values; inspect stderr |
| `rollback_timer_failed` | Could not schedule `systemd-run` | Change was reverted; check systemd-run/systemd |
| `nft_validation_failed` | `nft -c` rejected the ruleset | Inspect the returned stderr |
| `nft_apply_failed` | Live apply failed | Table may be partially applied; use console and re-apply |
| `ha_validation_failed` | keepalived rejected config | Check VRID/priority/interface/VIP/secret |
| `ha_apply_failed` | keepalived restart failed | Previous config restored; check `journalctl -u keepalived` |
| `package_install_disabled` | Jool auto-install not allowed | Install manually or create the allow flag deliberately |
| `invalid_snapshot` | Snapshot outside backup root or contains `..` | Use the snapshot path recorded by the apply |
| `internal_error` | Unexpected helper failure | Check journal and the typed message; do not expose tracebacks |

## 4. Jool

### 4.1 Not installed / version incompatible

```bash
jool --version
modprobe jool
lsmod | grep jool
```

- Jool 3 is rejected. Install Jool 4.
- `modprobe: Module jool not found`: install `jool-dkms` for the running kernel.
- Secure Boot: the module must be signed/MOK-enrolled.
- Persist with `echo jool > /etc/modules-load.d/jool.conf`.

### 4.2 Instance missing

```bash
jool instance display
jool -i <name> instance display
```

The application requires the instance to exist in live Jool and to be registered
in the database. Create/import it through Jool Management, then re-run the
wizard. The framework must be `netfilter`.

### 4.3 pool4 mismatch

Symptom: pool4 sync plan has adds/removes, or the address status is `pending`.

```bash
jool -i <name> pool4 display --tcp
jool -i <name> pool4 display --udp
jool -i <name> pool4 display --icmp
```

- Entries present in the database but absent in Jool appear as adds.
- Entries present in Jool but not managed by the application appear as
  `unmanaged` and are left alone.
- Removing an entry cascades BIB/session state; review the warning and affected
  session count.
- Sync is a change request; approve and execute it.
- A pool4 address must be routed to the gateway. Adding it to Jool does not make
  upstream return traffic work.

### 4.4 pool6 cannot be changed

Jool does not allow changing `pool6` on a live instance. Use **Recreate
instance** (critical, admin). It removes and re-adds the instance and drops all
BIB/session state. The DNS64 prefix must match the new pool6.

### 4.5 Pause/resume

`jool.pause` / `jool.resume` toggle the instance. Pausing stops translation for
all clients; it is not a per-client control. Use the client block feature for an
individual IPv6 client.

## 5. DNS64

### 5.1 BIND will not start after an apply

```bash
sudo named-checkconf
sudo journalctl -u named -n 100 --no-pager
cat /etc/bind/nat64-manager.conf
```

The helper restores the previous file on failure. If BIND is still down, remove
the include line from `named.conf.options` temporarily, start BIND, then fix the
fragment through the UI. Never hand-edit the managed include.

Common causes:

- the include is outside `options {}`;
- a forbidden directive was added by hand;
- a malformed prefix or exclusion;
- `forwarders` empty or invalid;
- service name is `bind9`, not `named` (set `DNS_SERVICE_NAME`).

### 5.2 Synthesis test fails

```bash
dig @127.0.0.1 AAAA ipv4only.arpa +short
dig @127.0.0.1 A ipv4only.arpa +short
```

- No A answer: upstream resolution is broken (forwarders, firewall, routing).
- A answer but no AAAA: DNS64 disabled, wrong prefix, or BIND did not reload.
- AAAA exists but is outside the prefix: wrong `dns64` block or stale config.
- `rndc reconfig` fails: check `/etc/bind/rndc.key` permissions and the service.

The application's "test synthesis" only checks that the answer lies inside the
configured prefix. It does not prove Jool translated the resulting address.

### 5.3 Prefix mismatch

The DNS64 prefix must equal the Jool `pool6` and the network NAT64 prefix. A
mismatch produces a warning in the DNS64 plan. Clients will receive addresses
Jool cannot translate. Fix the prefix and re-apply; changing pool6 requires an
instance recreate.

### 5.4 Clients cannot resolve anything

- Confirm the client's DNS server points at the gateway/VIP.
- Confirm recursion is allowed for the client prefix in `named.conf.options`.
- Confirm the firewall accepts TCP/UDP 53 from `client6`.
- Confirm DNS64 is enabled if clients are IPv6-only and destinations are A-only.

## 6. Firewall and lockout

### 6.1 SSH/UI is unreachable after apply

Use the provider console, IPMI, or physical keyboard. Then:

```bash
sudo nft list table inet nat64mgr
sudo nft delete table inet nat64mgr
sudo rm -f /etc/nftables.d/nat64-manager.nft
```

Fix the management allowlist (`mgmt4`/`mgmt6`) and management ports in the UI.
The renderer refuses to enable control-plane protection without an allowlist;
this error means someone bypassed or misconfigured it.

### 6.2 NAT64 return traffic is dropped

Check the forward chain:

```bash
sudo nft list chain inet nat64mgr forward
```

It must accept established/related, pool6 destinations and pool4 destinations
before the default drop. If you added a broad drop rule with higher priority,
remove or narrow it.

### 6.3 Apply says firewall is enabled but table is absent

The apply may have failed after validation. Check the change result and
`journalctl`; re-apply. `nft list table inet nat64mgr` should succeed when the
table is present. If the host rebooted, ensure `/etc/nftables.conf` includes
`/etc/nftables.d/*.nft` and the nftables service is enabled.

### 6.4 "Control-plane protection requires a management allowlist"

Add at least one IPv4 or IPv6 management CIDR. This is intentional anti-lockout
validation; do not remove it.

## 7. Network / netplan

### 7.1 Lost connectivity after apply

Do nothing and wait for the rollback timer (default 120 s, 30–900 s). The helper
restores the snapshot and re-applies. If you still have access, click **Confirm**
only when you are sure the new configuration is correct; otherwise click
**Rollback**.

Recover from the console:

```bash
ls -1 /var/backups/nat64-manager/network-*
sudo cat /etc/netplan/90-nat64-manager.yaml
sudo netplan generate
sudo netplan apply
```

Restore a snapshot manually only as a last resort; prefer the UI rollback so the
database state stays consistent.

### 7.2 `rollback_timer_failed`

The helper restores the snapshot and fails the apply when `systemd-run` cannot
schedule the timer. Check:

```bash
systemctl status systemd-run
systemctl status nat64mgr-netrollback.timer
journalctl -u nat64mgr-netrollback -n 100 --no-pager
```

Fix systemd or the timer unit collision, then retry the change. The helper will
not leave an unprotected change applied.

### 7.3 Wrong default route / WAN4 vs WAN6

The renderer attaches the IPv4 default route to the WAN4 interface and the IPv6
default route to the WAN6 interface. If both roles point at the same interface,
both routes go there. If a route is missing, the corresponding interface role or
gateway is unset. The renderer deliberately omits rather than guessing.

### 7.4 Interface addresses are missing

The managed netplan file does **not** assign addresses; it only manages MTU and
default routes for selected interfaces. Configure addressing in another netplan
file or via your image/DHCP/SLAAC. See
[`05-NETWORK-DNS64.md`](05-NETWORK-DNS64.md) section 3.

## 8. Database and migrations

### 8.1 API fails at startup

```bash
journalctl -u nat64-manager.service -n 200 --no-pager
systemctl status nat64-manager-migrate.service --no-pager
```

Common causes:

- PostgreSQL not running or wrong `DATABASE_URL`;
- wrong password or `pg_hba.conf` rejection;
- migrations not applied;
- `SECRET_KEY` shorter than 32 characters;
- `APP_ENCRYPTION_KEY` invalid (must be a Fernet key or a string that can be
  derived);
- port 8000 already in use.

### 8.2 Migration fails

```bash
cd /opt/nat64-manager/backend
sudo -u nat64mgr /opt/nat64-manager/venv/bin/alembic current
sudo -u nat64mgr /opt/nat64-manager/venv/bin/alembic upgrade head
```

Read the first error, not the last stack frame. Common causes are a database
owned by another role, an interrupted migration, or a schema created by an older
build. Take a database backup before manual intervention.

### 8.3 SQLite warning in the wizard

SQLite is for development/tests only. Move to PostgreSQL:

```env
DATABASE_URL=postgresql+psycopg://nat64:...@127.0.0.1:5432/nat64
SQLITE_FALLBACK_ALLOWED=false
```

Run migrations, create the bootstrap admin, and re-enter configuration.

### 8.4 Read-after-write surprises

The API commits inside `CommitAPIRoute` before responding. If a POST is not
visible to an immediate GET, check that a custom route did not bypass the router
base class and that no external replica is being read.

## 9. Authentication and access

### 9.1 Account locked

Wait `LOGIN_LOCKOUT_MINUTES` (default 15) or have an admin reset the password.
If every admin is locked out, use the database from the host:

```sql
UPDATE users
SET failed_login_count = 0, locked_until = NULL
WHERE username = 'admin';
```

Then sign in and change the password. Prefer resetting through a working admin
account.

### 9.2 Bootstrap password unknown

`bootstrap()` never overwrites an existing admin password. If the account exists
and the password is lost, reset it through another admin or update the password
hash with the application's password hashing utility. Do not delete users
casually: audit history and foreign keys depend on them.

If no admin exists, set `DEFAULT_ADMIN_PASSWORD` for one start, sign in, change
it, then remove the variable. Check `backend/data/bootstrap-admin.txt` in
development.

### 9.3 403 on an action

The role lacks the permission. See the matrix in
[`03-ADMINISTRATION.md`](03-ADMINISTRATION.md). Approve/execute requires admin;
`ha.apply` requires superadmin; replace restore requires a superuser; critical
changes need a different non-superuser approver.

### 9.4 429 Too Many Requests

The rate limiter rejected the request. Check the `Retry-After` header. Defaults
are 240 requests/min per IP/path and 10/min for login. If legitimate polling is
being limited, increase `RATE_LIMIT_PER_MINUTE` deliberately or configure Redis
for shared limits. Do not disable rate limiting as a first response.

### 9.5 Session expired / refresh fails

Access tokens last 15 minutes. Refresh rotates; a reused/revoked refresh token
fails by design (possible token theft or a second tab race). Sign in again and
revoke all sessions if compromise is suspected.

## 10. SPA and API routing

### 10.1 Deep link returns 404

The backend serves the SPA and falls back to `index.html` for unknown non-API
paths. Confirm `SERVE_FRONTEND=true` and `FRONTEND_DIST` points at the built
`dist`. `/api/*` 404s are preserved and must not be rewritten to the SPA.

### 10.2 API calls fail through the proxy

- Confirm nginx uses `server 127.0.0.1:8000` for a native install and
  `server api:8000` only in Compose.
- Confirm `TRUSTED_HOSTS` includes the public hostname.
- Confirm `CORS_ORIGINS` matches the browser origin exactly.
- Check `X-Forwarded-Proto` and `--proxy-headers`.
- Check for conflicting security headers added by the proxy.

### 10.3 Blank page / CSP errors

Open the browser console. The CSP allows same-origin scripts/styles and inline
styles. A reverse proxy that injects scripts or serves assets from another origin
will be blocked. Serve the built assets from the same origin.

### 10.4 Data does not refresh

The UI polls every 5 seconds; there is no WebSocket. A stalled "updated at"
usually means a failing API call, an expired token, or the tab is backgrounded.
Check the network log. Form state is intentionally not reset by polls.

## 11. Collector and sessions

### 11.1 No sessions appear

- `COLLECTOR_ENABLED=true` on the native gateway;
- exactly one uvicorn worker (multiple workers duplicate collection);
- Jool instance enabled and reachable by the service account;
- pool6/pool4 configured and traffic actually flowing;
- check `journalctl` for Jool parse/query errors;
- `/health` reports collector state.

### 11.2 Sessions are stale or expired

The collector marks rows stale when it cannot confirm them recently and expired
when they disappear. Retention is `SESSION_RETENTION_HOURS` (default 24). If Jool
is unreachable, last-known data remains until retention; it is not live.

### 11.3 CSV has empty byte counters

Expected. Jool's session output used here has no per-session byte counters.
`bytes_up`/`bytes_down` are empty, and `first_seen_at` means "first observed by
this manager", not connection start. Do not present this as accounting data.

### 11.4 Sessions CSV is huge

Use filters and the paginated list first. Exports are database-backed and can be
large; the proxy allows up to 300 s for export downloads.

## 12. Backups and restore

### 12.1 Create fails

Check `BACKUP_DIR` exists and is writable by `nat64mgr`
(`/var/lib/nat64-manager/backups` on a native install). Check disk space.
Automatic backups before a change are mandatory; if creation fails, execution
stops.

### 12.2 Restore rejected

- `backup.restore` requires admin; **replace** mode requires a superuser.
- Validate the backup first (`GET /backups/{id}/validate`).
- Restore changes database state only. It does not apply host configuration.
- After restore, run pool4 sync, DNS64 apply, firewall apply and network apply as
  separate changes.

### 12.3 Secrets missing after restore

Expected. Backups exclude users, roles, auth sessions, audit logs, metrics,
sessions, HA settings/nodes and encrypted secrets. Re-enter the HA VRRP secret
and any credentials.

## 13. High availability

### 13.1 Preview has errors

- HA must be enabled.
- `virtual_ip` and `vrrp_interface` are required.
- A VRRP secret must be stored (it is never displayed).
- `ha.apply` requires superadmin approval/execution.

### 13.2 keepalived will not start

```bash
sudo keepalived -t -f /etc/keepalived/keepalived.conf
sudo journalctl -u keepalived -n 100 --no-pager
ip -br addr
```

Common causes: VRID collision, VIP already configured statically, missing
interface, invalid priority, or another VRRP instance on the segment. The helper
restores the previous configuration if the restart fails.

### 13.3 Both nodes claim the VIP

Split-brain. Isolate one node immediately, check the VRRP path and switch
configuration, then follow the runbook in
[`07-HIGH-AVAILABILITY.md`](07-HIGH-AVAILABILITY.md). There is no fencing.

### 13.4 Sessions do not survive failover

Expected in the current build. Jool BIB/session and conntrack state are not
replicated, and the renderer does not configure Jool session synchronization.
Do not claim session continuity.

### 13.5 Peer check says unreachable

- `api_url` must be reachable from this node and end with the API origin, not a
  path.
- The peer API must be running and `/health` must return 200.
- Firewall/management network must allow the check.
- TLS certificates must be valid if you use `https://`.

## 14. Installation Wizard

The wizard reports `unsupported` when it cannot verify a host check. That is not
a pass. On a real gateway, step 11 remains failed until every required check is
genuinely `ok`.

| Step | Common failure |
|---|---|
| 1 Host | helper/sudoers/Linux, Jool 4, nftables, BIND, netplan missing |
| 2 Database | SQLite in production, unreachable PostgreSQL |
| 3 Interfaces | no WAN4/WAN6/LAN6 detected or roles unset |
| 4 Prefix | invalid length, host bits set, mismatch with DNS64 |
| 5 Pool4 | no enabled address |
| 6 Jool | instance missing/disabled/wrong framework |
| 7 pool4 sync | drift between DB and live Jool |
| 8 DNS64 | BIND offline, synthesis outside prefix |
| 9 Firewall | enabled without management allowlist |
| 10 Security | no active superadmin, `must_change_password` user, weak secrets |
| 11 Verification | one or more required checks not `ok` |

## 15. Logs and diagnostics bundle

Collect before escalating:

```bash
date -u
uname -a
cat /etc/os-release
jool --version
ip -br addr
ip route
nft list ruleset
cat /etc/netplan/90-nat64-manager.yaml
cat /etc/sysctl.d/90-nat64-manager.conf
cat /etc/bind/nat64-manager.conf
cat /etc/keepalived/keepalived.conf      # redact auth_pass before sharing
curl -fsS http://127.0.0.1:8000/health
systemctl status nat64-manager named nftables keepalived --no-pager
journalctl -u nat64-manager -n 500 --no-pager
journalctl -u named -n 200 --no-pager
journalctl -u keepalived -n 200 --no-pager
```

Redact secrets, public addresses if sensitive, and the VRRP `auth_pass` before
sharing logs. Do not send the environment file or database dump.
