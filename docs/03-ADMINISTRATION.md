# 03 – Administration

## 1. Signing in

The UI uses username/password authentication:

- passwords are hashed with Argon2;
- a short-lived access token (default 15 min) and a rotating refresh token
  (default 7 days) are issued;
- refresh tokens are tracked server-side and can be revoked;
- repeated failures lock the account for `LOGIN_LOCKOUT_MINUTES` (default 15)
  after `LOGIN_MAX_FAILURES` (default 8);
- login attempts are rate-limited separately from general API traffic.

If the account was created with a generated password (`must_change_password`),
the UI requires a password change. Password policy:

- at least `PASSWORD_MIN_LENGTH` characters (default 12);
- at least one uppercase, one lowercase, one digit, and one symbol.

## 2. Navigation

| Page | Purpose |
|---|---|
| **Dashboard** | NAT64 health, client/session counts, pool utilisation, traffic and service status; polled every 5 s |
| **Change Requests** | Dry-run plans, approval, rejection, execution, cancellation, audit |
| **Jool Management** | Instance status, create/import, pause/resume, recreate, globals, pool4 display, pool4 sync |
| **IPv4 Pool** | Pools, addresses, port ranges, CIDR import, observed utilisation |
| **IPv6 Clients** | Observed client inventory, search, block/unblock, CSV export |
| **NAT64 Sessions** | Active/expired/stale session list, filters, CSV export, expiry purge |
| **DNS64** | Prefix/upstreams settings, preview, apply, health, RFC 7050 synthesis test, stats |
| **Firewall** | Typed rules, settings, preview, apply, status |
| **Network Interfaces** | Interface roles, detection, netplan/sysctl preview, apply, confirm, rollback |
| **High Availability** | VRRP settings, preview, apply, peer nodes, health check, export/import |
| **System Monitoring** | Current metrics, history charts, collector state, listen ports |
| **Logs** | Application/audit view and journal excerpts for allowlisted units |
| **Backup & Restore** | Create, download, validate, restore, delete configuration backups |
| **Settings** | Application settings, users and roles, account/password, session revocation |
| **Installation Wizard** | 11 ordered checks and notes |

## 3. Roles and permissions

| Permission | Viewer | Operator | Admin | Superadmin |
|---|---:|---:|---:|---:|
| `read` | yes | yes | yes | yes |
| `pools.write`, `prefixes.write`, `clients.write` | – | yes | yes | yes |
| `sessions.export` | – | yes | yes | yes |
| `changes.request` | – | yes | yes | yes |
| `dns64.write`, `network.write` | – | yes | yes | yes |
| `backups.create` | – | yes | yes | yes |
| `changes.approve`, `changes.execute` | – | – | yes | yes |
| `users.write`, `settings.write`, `firewall.write`, `jool.write`, `audit.read` | – | – | yes | yes |
| `backups.restore`, `backups.delete` | – | – | yes | yes |
| everything (`*`) | – | – | – | yes |

Additional rules enforced in code:

- You cannot disable your own account or change your own role.
- Only a superadmin can grant or modify the superadmin role.
- `backup.restore` is an admin action; **replace** mode additionally requires
  the executing user to be a superuser.
- `ha.apply` is a **superadmin** change action.
- Jool instance remove/recreate, pool4 sync, firewall apply, network
  apply/rollback and backup restore require the admin role (or superadmin).
  `ha.apply` requires superadmin specifically.
- A **critical** change cannot be approved by the same non-superuser who
  requested it. Use two administrators for critical work.

Users are disabled, not deleted, so audit history and foreign keys remain
intact. Password resets revoke all refresh sessions for that user.

## 4. Change-request workflow

Every host-mutating action is a `ChangeRequest`. Some pages expose a direct
"Apply" button; it still creates a change request and requires approval.

### 4.1 States

| Status | Meaning |
|---|---|
| `draft` | Created but not valid/not dry-run |
| `dry_run_ok` | Informational legacy state |
| `dry_run_failed` | Plan contains errors; cannot approve |
| `pending_approval` | Valid plan waiting for an approver |
| `approved` | Approved; can be executed before expiry |
| `applied` | Executor completed successfully |
| `failed` | Execution or approval failed |
| `rejected` | Approver rejected |
| `cancelled` | Requester/approver cancelled |

### 4.2 Lifecycle

1. **Request** – action and typed payload are submitted. The server runs the
   planner immediately and stores the plan; status is `pending_approval` if
   valid, otherwise `dry_run_failed`.
2. **Review** – open the detail drawer. Review summary, risk, target, warnings,
   errors, exact argv commands, before/after state, reversibility, and whether
   maintenance is required.
3. **Re-run dry run** – allowed while `draft`, `dry_run_failed`, or
   `pending_approval`. This regenerates the plan from current database/host
   state.
4. **Approve** – only `pending_approval`; expires after 60 minutes. Critical
   changes need a different non-superuser approver.
5. **Execute** – must be `approved` and unexpired. The server creates an
   automatic `pre-<action>-<id>` backup first (except for `backup.restore`) and
   refuses to execute if that backup fails.
6. **Result** – success/failure, host stdout/stderr, commands, error, executor,
   timestamps. Every transition is audited.

Reject and cancel are available in the appropriate states. Expired requests
must be recreated; they are not silently revived.

### 4.3 Action catalog

| Action | Risk | Notes |
|---|---|---|
| `jool.create_instance` | medium | netfilter, pool6 |
| `jool.remove_instance` | high | drops sessions, admin |
| `jool.recreate_instance` | critical | required to change pool6; drops sessions, admin |
| `jool.pause` / `jool.resume` | medium | stops/starts translation |
| `jool.update_global` | medium | allowlisted global fields only |
| `pool4.sync` | high | adds/removes pool4; removal cascades BIB/sessions, admin |
| `dns64.apply` | high | validates and reloads BIND |
| `firewall.apply` | high | require management allowlist, admin |
| `network.apply` | critical | timed rollback until confirmed, admin |
| `network.rollback` | critical | restore snapshot, admin |
| `backup.restore` | high | admin; replace needs superuser |
| `ha.apply` | critical | superadmin; restarts keepalived |

## 5. Backups and restore

### 5.1 What is included

Configuration JSON contains:

- IPv4 pools and their addresses/port ranges;
- IPv6 prefixes;
- Jool instance definitions;
- DNS64 settings (including prefix and upstreams, no secrets);
- firewall settings and rules;
- network settings;
- application settings.

It **excludes** users, roles, auth sessions, audit logs, system metrics, NAT64
sessions, HA settings/nodes, and all encrypted secrets. This is intentional:
restoring a backup cannot restore an administrator password or the VRRP secret.

### 5.2 Create and validate

- **Create** – name, optional note; stored under `BACKUP_DIR` and in the
  database with a SHA-256 checksum.
- **Download** – JSON file.
- **Validate** – checks `app`, schema version, and field shapes.
- **Delete** – admin only; removes the record and file.

Automatic backups are created before each change execution with kind
`automatic` and name `pre-<action>-<change id>`.

### 5.3 Restore

`POST /backups/{id}/restore` creates a `backup.restore` change request with a
mode:

- **merge** – upsert recorded objects; existing objects not in the backup are
  left in place.
- **replace** – delete existing IPv4 addresses, pools, firewall rules, and
  prefixes first, then load the backup. Requires a superuser. Jool instances,
  DNS/firewall/network singletons, and settings are upserted.

Restore changes database state only. It does **not** write host files, sync
pool4, restart BIND, apply nftables, or apply netplan. After restoring:

1. review the configuration in the UI;
2. run pool4 sync as a new change;
3. re-apply DNS64, firewall, and network as needed;
4. re-run the wizard checks.

## 6. CSV exports and session data

Exports are available for sessions, clients, and audit. They are generated from
the database, not live Jool output.

- **Sessions:** `id, instance_name, protocol, client_ipv6, client_port,
  translated_ipv4, translated_port, remote_ipv4, remote_port, jool_state,
  expires_in_seconds, status, first_seen_at, last_seen_at, expired_at,
  bytes_up, bytes_down`.
  `bytes_up` and `bytes_down` are empty because Jool does not provide them.
  `first_seen_at` means "first observed by this manager", not connection start
  time.
- **Clients:** includes address, hostname, MAC, interface, VLAN, prefix, active
  session count, block flag, and first/last seen timestamps.
- **Audit:** actor, action, resource, success, IP, user agent, timestamp, and
  JSON detail.

Session rows are retained for `SESSION_RETENTION_HOURS` (default 24). The
"Sessions" page can purge expired rows (admin) with an explicit retention window.

## 7. Jool operation notes

- Import or create the instance so database and live state agree.
- `pool4` display is per protocol. Sync compares all enabled address entries for
  TCP, UDP, and ICMP.
- Entries present in Jool but not managed by this application are reported as
  `unmanaged` and left untouched.
- Removing a pool4 entry cascades Jool BIB/session state for that address. The
  plan reports how many active sessions currently use it and warns.
- `pool6` cannot be changed in place. "Recreate instance" is a critical change
  that removes and re-adds the instance, dropping all sessions.
- A pool4 address must be routed to the gateway. Adding an address to Jool does
  not make upstream routing or ARP work by itself.

## 8. DNS64 operation notes

- The rendered fragment is only valid inside BIND's `options {}` block.
- The helper rejects dangerous directives in the fragment (`zone`, `view`,
  `include`, `acl`, `key`, `controls`, `logging`, `server`, `master`,
  `allow-transfer`).
- The configured prefix should match the Jool pool6. The UI warns when it does
  not; mismatches break IPv6-only clients.
- "Test synthesis" queries `ipv4only.arpa` AAAA at the configured listen
  address and checks that an answer falls inside the prefix.
- DNS64 is required for A-only destinations. Disabling it leaves A/AAAA
  forwarding in place but IPv6-only clients cannot reach IPv4-only names.

## 9. Firewall operation notes

- Only `table inet nat64mgr` is managed.
- Rules are typed: family, direction, action, protocol, source/destination CIDR,
  ports, rate limit, logging. Invalid combinations are rejected at render time.
- The forward chain accepts pool6 destinations and pool4 destinations so NAT64
  return traffic is not dropped; established/related is accepted first.
- Enabling control-plane protection without a management allowlist is rejected
  to prevent SSH lockout.
- Apply is a change request and runs `nft -c` before touching the live ruleset.
- Recovery from a bad ruleset may require an out-of-band console (see
  [`09-TROUBLESHOOTING.md`](09-TROUBLESHOOTING.md)).

## 10. Network operation notes

- Interface settings render netplan for selected WAN4/WAN6/LAN6 interfaces.
  Routes are attached only when `manage_routes` and the corresponding gateway
  are set.
- MTU is optional and bounded 576–9216.
- Forwarding sysctls are written to the managed sysctl file and applied with
  `sysctl --system`.
- Apply is critical and starts a rollback timer. **Confirm** in the UI to cancel
  it. The helper refuses to leave a change applied if the timer cannot be
  scheduled.
- Rollback restores the last recorded snapshot.

## 11. High availability administration

See [`07-HIGH-AVAILABILITY.md`](07-HIGH-AVAILABILITY.md). Summary:

- configure settings and store a VRRP secret (never displayed again);
- preview keepalived output (secret masked);
- apply as a superadmin change;
- register peer nodes and run health checks;
- export/import non-secret configuration;
- do not assume session continuity.

## 12. Settings, users, and account actions

The Settings page exposes:

- application key/value settings (audited on create/update/delete);
- application settings summary (version, environment, host mode, limits);
- user list, create user, edit role/profile, enable/disable, password reset;
- role list;
- active refresh sessions and "revoke all";
- change-password for the signed-in user.

Some settings are informational only and do not override environment
variables. Environment variables remain the source of truth for bootstrap and
infrastructure paths.

## 13. Logs and monitoring

- **System Monitoring** shows current CPU/memory/load/disk and interface
  counters, Jool service state, collector state, and metric history. Values are
  sampled; they are not real-time per-packet measurements.
- **Logs** shows application logs and journal excerpts for allowlisted units
  (`nat64-manager`, `named`/`bind9`, `nftables`, `systemd-networkd`,
  `keepalived`, `jool`). The helper refuses arbitrary units.
- **Audit** is append-only through the application and records successful and
  failed attempts, including denied permissions where applicable.

## 14. Routine runbook

### Daily

- Check Dashboard health, Jool status, pool utilisation, and failed changes.
- Review new audit entries for unexpected logins or configuration changes.
- Confirm no network change is waiting for confirmation.

### Weekly

- Create and download a configuration backup; validate it.
- Review sessions/clients for anomalies and firewall drops.
- Review disk space for PostgreSQL, backups, and logs.
- Check that DNS64 synthesis still succeeds.

### Before any critical change

1. Export a backup and copy it off-host.
2. Have console/IPMI access available.
3. Schedule a maintenance window.
4. Verify the dry-run plan and exact commands.
5. Have a second administrator approve critical changes.
6. After execution, confirm network changes and re-run wizard checks.
7. Record the change ID and outcome.

## 15. API access

The API is documented at `/docs` only when `ENABLE_DOCS=true`. Production
should keep it disabled. All routes are under `/api/v1` except `/health`.

Authentication: `POST /api/v1/auth/login` returns access and refresh tokens.
Use `Authorization: Bearer <access>` for API calls and
`POST /api/v1/auth/refresh` to rotate. `POST /api/v1/auth/logout` revokes the
refresh token.

Do not automate host changes around the approval workflow. Scripts should create
change requests and wait for a human approval rather than bypassing it.
