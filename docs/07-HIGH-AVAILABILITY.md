# 07 – High availability

> **Status: experimental, not verified.** The keepalived renderer, change
> workflow and peer checks are implemented and unit-tested, but no failover test
> has been executed on a Linux lab in this build. Do not claim HA or session
> continuity for production until you complete the validation plan in section 9.
> There is no automated test for VRRP, failover, split-brain or session survival.

## 1. Scope

The application supports a **two-node active/passive VRRP** design for the
gateway:

- two Linux gateways run the same application and host services;
- keepalived advertises a virtual IP (VIP) on a shared L2 segment;
- the MASTER holds the VIP; the BACKUP takes over if advertisements stop;
- operators can register peer nodes and check their `/health`;
- non-secret configuration can be exported/imported between nodes.

### Non-goals

The current build does **not**:

- replicate PostgreSQL (each node has its own database);
- replicate Jool BIB/session state;
- synchronize conntrack state;
- transfer DNS zones, caches or keys;
- configure Jool session synchronization (the settings are stored, but the
  keepalived renderer does not emit Jool SS directives);
- automate fencing, STONITH, quorum or split-brain resolution;
- provide a load-balanced active/active cluster;
- guarantee that existing NAT64 sessions survive failover.

## 2. Topology

```
        clients / access network
                  |
            VIP 203.0.113.10
                  |
        +---------+---------+
        |                   |
   gateway-1           gateway-2
   MASTER              BACKUP
   priority 150        priority 100
   198.51.100.11       198.51.100.12
        |                   |
   PostgreSQL          PostgreSQL
   Jool/BIND/nft       Jool/BIND/nft
   keepalived          keepalived
```

Both nodes need the same pool4 addresses routed to the VIP/L2 segment, the same
pool6, the same DNS64 prefix and compatible firewall policy. The VIP is commonly
the address clients use as their DNS server and/or next hop; design the routing
so that takeover actually redirects traffic, not just the VIP.

## 3. Settings

Configured on the High Availability page:

| Field | Meaning | Validation |
|---|---|---|
| `enabled` | Render a keepalived instance | Required |
| `mode` | Stored mode label (default `keepalived`) | – |
| `virtual_ip` | IPv4 or IPv6 VIP | Must parse as an IP |
| `vrrp_interface` | Interface carrying VRRP | `[A-Za-z0-9_.:@-]{1,64}` |
| `vrrp_router_id` | VRRP VRID | 1–255 |
| `priority` | VRRP priority | 1–254 |
| `peer_address` | Informational peer address | – |
| `auth_secret` | VRRP `auth_pass` | Stored encrypted; never returned |
| `session_sync_enabled` | Stores intent only | – |
| `session_sync_multicast` | Stored only | – |
| `session_sync_interface` | Stored only | – |

Environment-controlled values:

| Variable | Meaning |
|---|---|
| `HA_ENABLED` | Initial default only |
| `HA_NODE_NAME` | `router_id` in `global_defs` (sanitized) |
| `HA_ROLE` | `primary` → `state MASTER`; anything else → `state BACKUP` |
| `HA_HEARTBEAT_SECONDS` | `advert_int` (minimum 1) |
| `HA_SYNC_TOKEN` | Shared secret for `/api/v1/ha/import` |

`HA_ROLE` is a **process-wide** setting. A `standalone` node with HA enabled
renders `state BACKUP`; this is harmless for a single node (it becomes MASTER
when no higher-priority advertisements exist) but is confusing. Set
`HA_ROLE=primary` on the intended master and `HA_ROLE=backup` on the peer.

Storing settings does **not** apply them. The API response explicitly says
`applied_to_host: false`. You must create and approve `ha.apply`.

## 4. Rendered keepalived configuration

Preview and change-plan content is redacted. This is the shape:

```conf
# Managed by NAT64 ISP Manager. Do not edit manually.
global_defs {
    router_id gateway-1
    enable_script_security
}

vrrp_instance NAT64_GATEWAY {
    state MASTER
    interface eth0
    virtual_router_id 51
    priority 150
    advert_int 10
    authentication {
        auth_type PASS
        auth_pass ********
    }
    virtual_ipaddress {
        203.0.113.10
    }
}
```

The real `auth_pass` is decrypted and re-rendered with `redact=False` only inside
the execution path, immediately before the helper is called. The browser, audit
detail and change plan never receive it.

The helper validates the content before writing:

- exactly one `vrrp_instance` and a `global_defs` block;
- brace balance;
- rejects `vrrp_script`, `notify_*`, `script`, `include`, `misc` and
  `track_script` tokens (no arbitrary root scripts);
- runs `keepalived -t -f <staged>` (or `--config-test --use-file` on versions
  that use that syntax).

It writes `/etc/keepalived/keepalived.conf` as `root:root 0640`, restarts
`keepalived`, and restores the previous file and restarts again if the restart
fails.

## 5. Apply workflow

1. Save settings (database only, audited).
2. Preview the redacted configuration.
3. Create `ha.apply` (superadmin permission required to approve/execute).
4. Approve as a different superadmin if the requester is not one.
5. Execute. A pre-change configuration backup is written first.
6. The helper validates, writes and restarts keepalived.
7. Check `systemctl status keepalived` and `ip addr show` on both nodes.

`ha.apply` is critical. It does not configure the peer, the VIP routing, the
firewall or Jool. Apply the same settings with the opposite `HA_ROLE` on the
other node, and ensure the VIP is not also statically configured on both.

## 6. Peer nodes and health checks

Register peers under High Availability → Nodes:

| Field | Meaning |
|---|---|
| `name` | Unique label |
| `role` | Informational (`primary`/`backup`) |
| `address` | Host address |
| `api_url` | Base URL used for checks, for example `http://198.51.100.12:8000` |
| `enabled` | Skip checks when false |
| `notes` | Free text |

`POST /api/v1/ha/nodes/check` requests `<api_url>/health` with a 4-second
timeout. It records `online`, `http_<code>`, `unreachable`, `disabled` or
`unconfigured`, the last-seen time, the peer's Jool version and its reported
config version. This is a **control-plane** check. It does not prove that VRRP,
Jool or forwarding is healthy. It is not used as a keepalived health script (the
renderer deliberately rejects scripts).

## 7. Configuration export/import

`GET /api/v1/ha/export` returns a non-secret configuration bundle:

- pools and addresses;
- IPv6 prefixes;
- Jool instance definitions;
- DNS64 settings;
- firewall settings and rules;
- network settings;
- application settings.

It excludes users, roles, auth sessions, audit logs, metrics, NAT64 sessions, HA
settings/nodes and encrypted secrets. The response includes a hash and a warning
that host state is not synchronized.

`POST /api/v1/ha/import` requires the `X-HA-Sync-Token` header and an exact match
with `HA_SYNC_TOKEN`. It creates an internal backup record and performs a
**merge** restore into the database. It does **not** write host files, sync
pool4, restart BIND, apply nftables or apply netplan. After import, review the
configuration and run the relevant change requests on that node.

If `HA_SYNC_TOKEN` is unset, import fails with a conflict rather than accepting
unauthenticated writes. Set a long random value and protect it like any other
secret. This endpoint is for node-to-node use on a trusted management network,
not for the public internet.

## 8. Failover behaviour and limitations

### What VRRP provides

When the BACKUP stops receiving advertisements for the master-down interval
(roughly `3 * advert_int + skew`), it transitions to MASTER and claims the VIP.
Existing TCP flows through the failed node are lost. New flows that arrive at
the new MASTER can be translated if Jool, pool4, routing, firewall and DNS64 are
already configured identically.

### What is not preserved

| State | Preserved? |
|---|---|
| VIP ownership | Yes, after VRRP convergence |
| PostgreSQL data | No automatic replication |
| Jool pool6/pool4 config | Only if applied identically on both nodes |
| Jool BIB/session state | No |
| conntrack state | No |
| DNS cache/zones | No |
| In-flight TCP/UDP sessions | No guarantee; expect resets/timeouts |
| Application audit continuity | Separate per node |
| HA settings | Not exported; configure each node |

### Session continuity

`session_sync_enabled` and related fields are stored and displayed, and the
renderer warns when the interface is missing. The current renderer does **not**
emit Jool SS configuration, and no test verifies survival of a session across
failover. Treat session continuity as unavailable until you implement and verify
it separately. The UI reports `continuity.verified: false`.

### Split-brain

VRRP does not provide quorum. If the peer is alive but advertisements are lost,
both nodes may claim the VIP. Prevent this with:

- a dedicated VRRP/heartbeat L2 path;
- `iptables`/`nftables` rules that permit VRRP (IP protocol 112) only on that
  path;
- switch/port isolation and storm protection;
- monitoring that alerts on duplicate VIP ownership;
- an out-of-band decision procedure to shut one node down.

There is no fencing integration. A powered-off or isolated node cannot be
forcibly fenced by this application.

## 9. Validation plan (must be executed on Linux)

Nothing below has been run in this build. Record the output and attach it to your
change record.

1. **Static validation**
   - `keepalived -t -f /etc/keepalived/keepalived.conf` on both nodes.
   - Confirm `state`, `priority`, `virtual_router_id`, `advert_int`, interface
     and VIP match the design.
   - Confirm the secret is present but never printed in the UI/API.

2. **Single-node start**
   - Start keepalived on the primary only.
   - `ip addr show <iface>` shows the VIP.
   - `journalctl -u keepalived -n 100` shows MASTER transition.

3. **Normal failover**
   - Start the backup; confirm it stays BACKUP.
   - Stop keepalived on the primary (or power it off).
   - Confirm the backup becomes MASTER within the expected interval and answers
     on the VIP.
   - Generate new NAT64 traffic through the VIP; verify translation, DNS64 and
     firewall return traffic.

4. **Failback**
   - Restart the primary with a higher priority.
   - Confirm preemption and VIP movement, or document that preemption is
     intentionally disabled.

5. **Split-brain drill**
   - Block VRRP between nodes while both are alive.
   - Confirm monitoring detects duplicate ownership and that your procedure
     resolves it safely.

6. **Session behaviour**
   - Hold long-lived TCP and UDP flows through the VIP.
   - Fail over.
   - Record whether flows survive, reset or time out. Do not assume continuity.

7. **Reboot**
   - Reboot each node and confirm keepalived, Jool, BIND, nftables, netplan and
     the API return in the correct order and the VIP settles on one node.

8. **Application checks**
   - `/health` on both nodes.
   - Peer node check reports the expected status.
   - Wizard checks pass on the active node.

## 10. Operational runbook

### Planned maintenance on the MASTER

1. Confirm the BACKUP is healthy and has the same configuration.
2. Stop keepalived on the MASTER and wait for the BACKUP to take the VIP.
3. Verify traffic and DNS64 through the VIP.
4. Perform maintenance.
5. Restart keepalived and confirm preemption or intentionally leave the BACKUP
   active.
6. Verify both nodes' configuration versions and health.

### Unplanned failover

1. Confirm which node owns the VIP (`ip addr`, `journalctl -u keepalived`).
2. Check the failed node's console/power/network.
3. Verify the new MASTER has Jool pool4, routes, firewall and DNS64 applied.
4. Check `/health`, peer checks and wizard.
5. Expect existing sessions to be lost; communicate this.
6. When the old node returns, verify it comes back as BACKUP and does not fight
   for the VIP.

### After any failover

- record the time, trigger, VIP owner and affected services;
- export configuration from both nodes and compare hashes;
- review audit logs and change requests;
- create a backup;
- investigate the root cause before declaring the cluster healthy.
