# 06 – Security

## 1. Objectives

The control plane can reconfigure a production gateway. Its security goals are:

1. An unauthenticated user cannot read or change anything except `/health`.
2. An authenticated user can perform only actions allowed by their role.
3. The web process cannot execute arbitrary host commands, and the browser can
   never reach Jool, nftables, netplan, BIND or a shell directly.
4. Every host mutation is reviewed before execution and leaves an audit trail.
5. Credentials and shared secrets are not exposed to the browser or logs.
6. A failed or malicious configuration cannot silently take the gateway offline;
   validation and rollback are mandatory.

This is a single-tenant operations tool. It is not a multi-tenant portal and does
not attempt to isolate subscribers from each other.

## 2. Trust boundaries

```
[ browser ] --HTTPS--> [ reverse proxy ] --loopback--> [ FastAPI (nat64mgr) ]
                                                              |
                                                     PostgreSQL (local)
                                                              |
                                              sudo -n (one command, JSON stdin)
                                                              |
                                                    [ hostctl.py (root) ]
                                                              |
                                    +-------------+-----------+-----------+
                                    |             |           |           |
                                  jool          named      nftables    netplan
```

There are three boundaries to defend:

| Boundary | Main risks | Controls |
|---|---|---|
| Browser → API | Credential theft, privilege escalation, injection, CSRF, DoS | TLS, auth, RBAC, validation, rate limits, CSP, trusted hosts |
| API → helper | Command injection, path traversal, argument confusion | One sudo command, JSON stdin, typed allowlists, fixed argv, no shell |
| Helper → host | Destructive configuration, lockout, file tampering | Managed paths, native validation, snapshots, rollback, root ownership |

## 3. Authentication and session controls

- Passwords are hashed with **Argon2** via `pwdlib`.
- Access tokens are **JWT HS256**, signed with `SECRET_KEY`, default lifetime
  15 minutes, required claims `exp`, `sub`, `iat`, `type`.
- Refresh tokens are opaque random strings (48 bytes URL-safe). Only their
  SHA-256 hash is stored. They rotate on every refresh; the old token is revoked.
- Refresh sessions are listed in Settings and can be revoked individually or
  all at once. Password change and admin reset revoke all refresh sessions.
- `LOGIN_MAX_FAILURES` (default **8**) failed logins lock the account for
  `LOGIN_LOCKOUT_MINUTES` (default 15). The counter resets on success.
- Unknown usernames perform a dummy password verification to reduce user
  enumeration by timing.
- Disabled accounts are rejected before password verification.
- Password policy: at least `PASSWORD_MIN_LENGTH` (default 12), one uppercase,
  one lowercase, one digit, one symbol.
- Login attempts are rate-limited separately (`LOGIN_RATE_LIMIT_PER_MINUTE`,
  default 10/min per IP/path).
- The SPA keeps the access token in memory. The refresh token is stored according
  to the client implementation; treat the browser profile as a credential store.

### 3.1 Bootstrap account

`DEFAULT_ADMIN_PASSWORD` is used only when no admin exists. If it is absent, a
random password is generated and written to `backend/data/bootstrap-admin.txt`
in development, or the path is logged in production. The account is flagged
`must_change_password`. After first login:

1. change the password;
2. create named accounts with least privilege;
3. disable the bootstrap account;
4. remove `DEFAULT_ADMIN_PASSWORD` from the environment file.

`bootstrap()` never overwrites an existing admin password.

## 4. Authorization

Four roles are seeded on bootstrap. Their permission sets are defined in
`backend/app/services/auth.py`:

| Permission | Viewer | Operator | Admin | Superadmin |
|---|---:|---:|---:|---:|
| `read` | yes | yes | yes | yes |
| `pools.write`, `prefixes.write`, `clients.write` | – | yes | yes | yes |
| `sessions.export` | – | yes | yes | yes |
| `changes.request` | – | yes | yes | yes |
| `backups.create` | – | yes | yes | yes |
| `dns64.write`, `network.write` | – | yes | yes | yes |
| `changes.approve`, `changes.execute` | – | – | yes | yes |
| `users.write`, `settings.write`, `firewall.write`, `jool.write`, `audit.read` | – | – | yes | yes |
| `backups.restore`, `backups.delete` | – | – | yes | yes |
| `*` | – | – | – | yes |

Every API route declares a permission dependency. Additional invariants:

- a user cannot disable themselves or change their own role;
- only a superadmin can grant or modify the superadmin role;
- `backup.restore` in **replace** mode requires a superuser;
- `ha.apply` is a superadmin action;
- Jool instance remove/recreate, pool4 sync, firewall apply, network
  apply/rollback, backup restore and HA apply are admin-level actions.

Role permissions are re-seeded on startup; they are not editable through the UI.

## 5. Change separation

Host mutations are not direct REST calls. They are `ChangeRequest` objects:

1. **Plan** – typed payload, dry run, no host mutation.
2. **Approve** – a different authorised user reviews the exact commands, risk,
   warnings and reversibility. Critical changes cannot be self-approved by a
   non-superuser.
3. **Execute** – only `approved` and unexpired (60 minutes). An automatic
   configuration backup is written first; if it fails, execution stops.
4. **Audit** – every transition is recorded with actor, timestamp and result.

This separation is a security control, not only a UX feature. Scripts should
create requests and wait for human approval, never bypass the workflow. The
database is the source of truth for the plan; the executor re-renders secrets
only at the moment of execution (for keepalived) and otherwise uses the stored,
already-validated parameters.

## 6. Host helper and sudoers

The backend user `nat64mgr` may run exactly one command as root, with no
arguments:

```sudoers
Defaults:nat64mgr !requiretty
Defaults:nat64mgr secure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

Cmnd_Alias NAT64_HOSTCTL = /usr/bin/python3 /opt/nat64-manager/hostctl.py

nat64mgr ALL=(root) NOPASSWD: NAT64_HOSTCTL
```

The helper is installed `root:root 0755`; the application tree is `root:root`
and not group/world writable. `HOSTCTL_PYTHON` must match the interpreter in the
sudoers rule exactly (`/usr/bin/python3` by default). The installer resolves
`PYTHON` and writes the same absolute path to both files. If you change the
interpreter manually, update both and re-run `visudo -cf`.

### 6.1 Input handling

The backend writes JSON `{"op": "...", "params": {...}}` to stdin. The helper:

- requires a JSON object, a known `op`, and an object `params`;
- validates every field with regexes, allowlists, integer bounds and
  `ipaddress` parsing;
- builds fixed `argv` arrays and calls `subprocess` with `shell=False`;
- never passes user text as a shell fragment, never uses `os.system`, and never
  evaluates command output;
- maps unknown operations/actions to `unknown_op`/`unknown_action` errors;
- caps request/output size and returns structured errors without tracebacks.

Typed operation groups: `jool`, `ip`, `system`, `dns`, `firewall`, `network`,
`packages`, `selftest`, `ha`, plus `ping`.

### 6.2 File and path controls

Only these paths are writable:

```
/etc/bind/nat64-manager.conf
/etc/netplan/90-nat64-manager.yaml
/etc/sysctl.d/90-nat64-manager.conf
/etc/nftables.d/nat64-manager.nft
/etc/keepalived/keepalived.conf
```

The DNS read action rejects any other path. Network rollback rejects snapshot
paths outside `/var/backups/nat64-manager` and any path containing `..`. Writes
are atomic (temporary file in the same directory, fsync, `os.replace`), with
explicit modes and `root:bind` ownership for the BIND include.

### 6.3 Content controls

| Config | Extra validation |
|---|---|
| DNS fragment | rejects `zone`, `view`, `include`, `acl`, `key`, `controls`, `logging`, `server`, `master`, `allow-transfer`; brace balance; `named-checkconf` |
| nftables | rejects `flush ruleset`, `include`, shebangs, `delete table inet`, non-`inet` tables; requires exactly one `table inet nat64mgr`; `nft -c` |
| netplan | YAML/sysctl syntax, `netplan generate` (temporary root dir when supported) |
| keepalived | rejects `vrrp_script`, `notify_*`, `script`, `include`, `misc`, `track_script`; exactly one `vrrp_instance`; `keepalived -t` |
| Jool | instance name regex, pool6/Jool-version checks, typed global settings, fixed argv |

Refusing scripts and notifications in keepalived is deliberate: this application
renders a minimal VRRP instance, not arbitrary root scripts.

### 6.4 Rollback

- Network apply starts a `systemd-run` timer (30–900 s). If the timer cannot be
  scheduled, the snapshot is restored and the apply fails.
- BIND, nftables and keepalived keep the previous file and restore it when
  validation or service restart fails.
- Jool pool4 removal is not reversible at the session level; the change plan
  warns and counts affected sessions.

## 7. Secrets

| Secret | Storage | Exposure |
|---|---|---|
| User passwords | Argon2 hash | Never returned |
| `SECRET_KEY` | environment file | Used for JWT and fallback encryption |
| `APP_ENCRYPTION_KEY` | environment file | Fernet key for secrets at rest |
| HA VRRP `auth_pass` | Fernet-encrypted in `ha_settings` | Preview/plan show `********`; execution re-renders unredacted immediately before the helper call |
| `HA_SYNC_TOKEN` | environment | Required header for `/ha/import`; not returned by the API |
| Database password | environment | Not returned |
| Bootstrap password | environment or generated file | Change immediately; remove the file |

If `APP_ENCRYPTION_KEY` is absent, the application derives a Fernet key from
`SECRET_KEY`. That is a development fallback: rotating `SECRET_KEY` then makes
stored HA secrets undecryptable. Set a real `APP_ENCRYPTION_KEY` in production
and back it up separately from the database.

Configuration backups and HA exports exclude users, roles, auth sessions, audit
logs, metrics, sessions, HA settings/nodes and all encrypted secrets. Restoring a
backup cannot restore a password or the VRRP secret.

## 8. Network exposure

- Bind uvicorn to `127.0.0.1:8000` and terminate TLS with nginx or Caddy on the
  same host. Do not expose uvicorn directly.
- `TRUSTED_HOSTS` must list the real public hostname(s), never `*`, in
  production. `TrustedHostMiddleware` rejects other Host headers.
- `CORS_ORIGINS` must be the exact UI origin. Credentials are allowed, so a
  wildcard is unsafe.
- `ENABLE_DOCS=false` removes `/docs` and the OpenAPI schema.
- `SECURE_COOKIES=true` behind HTTPS; HSTS is emitted automatically in
  production even if the flag is false.
- Security headers set by the backend: CSP, `X-Content-Type-Options`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy`,
  `Cross-Origin-Opener-Policy`, HSTS in production. Do not duplicate conflicting
  headers in the proxy.
- `/health` is unauthenticated and intentionally minimal. It reports database
  status, host mode/availability, Jool status, collector state and demo mode. It
  does not expose secrets, user data or configuration.

### 8.1 Rate limiting

`RateLimitMiddleware` applies `RATE_LIMIT_PER_MINUTE` (default 240) per client
IP and path under `/api/v1`, excluding `/health`. The login endpoint has a
stricter limit (`LOGIN_RATE_LIMIT_PER_MINUTE`, default 10). The in-memory backend
is per process; set `REDIS_URL` if you run multiple API processes or need shared
limits. The application is designed for **one** uvicorn worker because of the
collector, so in-memory limits are normally sufficient.

## 9. Database

- PostgreSQL in production, listening on localhost or a private network with
  `pg_hba.conf` restrictions. Do not expose it to clients.
- The application account needs DDL only during migrations; a separate migration
  role is recommended but not automated.
- Backups contain configuration, not user credentials; database dumps still
  contain password hashes, audit logs and encrypted secrets. Protect them
  accordingly.
- SQLite is for development/tests only. It has no network authentication and
  weak concurrency; `SQLITE_FALLBACK_ALLOWED=false` belongs in production.

## 10. Audit and logging

`audit_logs` is append-only through the application and records:

- authentication success/failure and reasons;
- user/role changes;
- pool/prefix/client changes;
- change request transitions, approvals, executions and results;
- DNS/firewall/network/HA applies;
- backup create/restore/delete;
- settings changes.

Each entry includes actor (or username for failed login), action, resource,
success flag, IP, user agent, timestamp and JSON detail. Logs avoid passwords and
secrets. Journal excerpts for allowlisted units are read through the helper; the
browser cannot ask for arbitrary units.

The audit log is not tamper-proof against a root attacker. Ship it to a remote
log system if you need durable, independent evidence.

## 11. Hardening checklist

- [ ] `ENVIRONMENT=production`, `DEBUG=false`, `ENABLE_DOCS=false`.
- [ ] Unique `SECRET_KEY` (≥32 chars) and `APP_ENCRYPTION_KEY` stored outside
      the repository, mode `0640 root:nat64mgr`.
- [ ] PostgreSQL password unique; database not exposed publicly.
- [ ] Bootstrap password changed and removed; named accounts created.
- [ ] Superadmin accounts limited to named operators; MFA is not available, so
      protect them with network restrictions and strong unique passwords.
- [ ] `TRUSTED_HOSTS` and `CORS_ORIGINS` set to the real origin.
- [ ] `SECURE_COOKIES=true`, TLS 1.2+ at the proxy.
- [ ] API bound to loopback; management access through VPN/allowlist.
- [ ] Helper `root:root 0755`; sudoers validated with `visudo -cf`.
- [ ] Application tree `root:root`, `go-w`.
- [ ] Management allowlist configured before enabling the firewall.
- [ ] Console/IPMI available before network or firewall changes.
- [ ] Configuration backups tested and copied off-host.
- [ ] Audit logs reviewed and ideally shipped off-host.
- [ ] Host OS patched; Jool/BIND/nftables/keepalived from trusted packages.
- [ ] Unused services disabled; SSH keys instead of passwords where possible.

## 12. Residual risks and limitations

- **No MFA/OIDC/SAML.** A stolen password plus reachable UI is sufficient to
  sign in (subject to lockout and rate limits).
- **No per-user API tokens or scopes.** API automation uses a user account.
- **No CSRF tokens.** The API expects a bearer token in the `Authorization`
  header and does not rely on ambient cookies; keep it that way if you extend the
  frontend. `CSRF_ENABLED` exists but is not a complete CSRF framework.
- **In-process rate limiting** is not shared across processes unless Redis is
  configured.
- **Root helper compromise** is equivalent to host compromise. The helper reduces
  the attack surface; it does not make root harmless.
- **Encrypted secrets** are only as safe as the environment file and the host.
  Losing `APP_ENCRYPTION_KEY` makes the HA secret unrecoverable.
- **No SELinux/AppArmor profile** is shipped. Add one for the API and helper if
  your distribution supports it.
- **No automated key rotation.** Rotate `SECRET_KEY`, `APP_ENCRYPTION_KEY`,
  database passwords and the HA secret during maintenance windows.
- **Host configuration is not attested.** `nft list`, `named-checkconf`, Jool
  display and file contents are read live, but the application cannot prove the
  kernel or binaries are unmodified.
- **Session/BIB data is observational.** It is not a lawful-intercept or
  accounting record.

## 13. Incident response

1. Disable the affected user and revoke all sessions from Settings.
2. If the API is compromised, stop `nat64-manager.service`, remove network
   access to the UI, and preserve logs before restarting.
3. Rotate `SECRET_KEY`, `APP_ENCRYPTION_KEY`, the database password, the
   bootstrap password and the HA VRRP secret.
4. Review `audit_logs`, `change_requests`, systemd journal and host file
   modification times for the managed paths.
5. Compare live host state (`jool`, `nft`, `netplan get`, BIND include,
   keepalived) with the expected approved configuration.
6. If the helper or sudoers policy may have been modified, reinstall both from a
   trusted release and verify checksums before restarting the API.
7. Restore configuration from a known-good backup only after understanding the
   change; restore does not touch the host until you re-apply.
