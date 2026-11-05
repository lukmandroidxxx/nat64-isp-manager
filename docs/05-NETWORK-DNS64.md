# 05 – Network, DNS64 and firewall

This document describes the host configuration this application renders and
applies. All examples are generated from the current models; they are not
hand-maintained production files. Never edit a managed file by hand: the next
approved apply overwrites it.

## 1. Managed paths

| File | Owner / mode | Managed by |
|---|---|---|
| `/etc/netplan/90-nat64-manager.yaml` | `root:root 0640` | Network Interfaces page |
| `/etc/sysctl.d/90-nat64-manager.conf` | `root:root 0640` | Network Interfaces page |
| `/etc/bind/nat64-manager.conf` | `root:bind 0640` | DNS64 page |
| `/etc/nftables.d/nat64-manager.nft` | `root:root 0640` | Firewall page |
| `/etc/keepalived/keepalived.conf` | `root:root 0640` | High Availability page |

The helper hardcodes these paths. The corresponding environment variables
(`NETWORK_MANAGED_NETPLAN`, `NETWORK_MANAGED_SYSCTL`, `DNS_MANAGED_CONFIG`,
`FIREWALL_MANAGED_FILE`) must match them; do not relocate the files. The
firewall table name is fixed at `nat64mgr` in the helper; keep
`FIREWALL_TABLE_NAME=nat64mgr`. A mismatch fails closed rather than touching an
unexpected table.

## 2. Reference topology

| Role | Interface | Address |
|---|---|---|
| WAN IPv4 | `wan0` | `198.51.100.10/24`, gateway `198.51.100.1` |
| WAN IPv6 | `wan0` | `2001:db8:100::10/64`, gateway `2001:db8:100::1` |
| LAN IPv6 | `lan0` | `2001:db8:64::1/64` |
| Client prefix | `lan0` | `2001:db8:64::/64` |
| NAT64 pool6 | – | `64:ff9b::/96` |
| Public pool4 | WAN | `203.0.113.1` – `203.0.113.32` |

The public IPv4 addresses must be routed to the gateway or configured on the WAN
interface. Jool translates packets; it does not make upstream routing work.

## 3. netplan rendering

### 3.1 What it manages

The Network Interfaces page discovers interfaces with `ip -j` and lets the
operator assign roles (WAN4, WAN6, LAN6, other). The renderer:

- writes a netplan file with `version: 2` and `renderer: networkd`;
- includes only interfaces selected as WAN4, WAN6 or LAN6;
- optionally writes an MTU on each selected interface (`manage_mtu`);
- optionally writes default routes (`manage_routes`) to the matching WAN
  interface, using the configured gateways;
- **does not assign IP addresses, create VLANs, bonds or bridges, or manage
  unrelated interfaces.** Addresses are expected to come from another netplan
  file, the image, DHCP/SLAAC, or manual configuration.

This is deliberate: the application avoids taking ownership of addressing it did
not create. If you need full interface addressing, manage it in a separate,
lower-numbered netplan file (for example `50-cloud-init.yaml`) and use this file
only for MTU and routes.

### 3.2 Generated sample

With `manage_mtu=true`, MTU `1500`, `manage_routes=true`, WAN4 and WAN6 both on
`wan0`, and LAN6 on `lan0`:

```yaml
# Managed by NAT64 ISP Manager. Changes are applied only through an approved change request.
network:
  version: 2
  renderer: networkd
  ethernets:
    lan0:
      mtu: 1500
    wan0:
      mtu: 1500
      routes:
        - to: 0.0.0.0/0
          via: 198.51.100.1
        - to: ::/0
          via: 2001:db8:100::1
```

If WAN4 and WAN6 are different interfaces, each default route is attached to its
own interface. If no interface is selected for a family, that route is omitted
with a warning rather than guessed.

### 3.3 sysctl rendering

```ini
# Managed by NAT64 ISP Manager. Applied with sysctl --system.
net.ipv4.ip_forward=1
net.ipv6.conf.all.forwarding=1
net.ipv6.conf.default.forwarding=1
net.ipv4.conf.wan0.rp_filter=2
```

`rp_filter=2` (loose mode) is used on the WAN4 interface because strict reverse
path filtering can drop NAT64 return traffic on multi-homed gateways. This is a
deliberate trade-off; tighten it only after verifying your routing.

### 3.4 Validation and apply sequence

1. The backend validates interface names, NAT64 prefix length
   (`/32,/40,/48,/56,/64,/96`), MTU (`576–9216`), addresses, and gateways.
2. The helper validates sysctl syntax and runs `netplan generate` against a
   temporary root directory when supported.
3. On apply, the helper snapshots both managed files under
   `/var/backups/nat64-manager/network-*`.
4. It writes both files atomically.
5. It runs `netplan generate`, `sysctl --system`, then `netplan apply`.
6. It starts a rollback timer with
   `systemd-run --unit=nat64mgr-netrollback --on-active <seconds>`.
   `NETWORK_ROLLBACK_SECONDS` is clamped to **30–900** (default 120).
7. If any step fails, or the timer cannot be scheduled, it restores the snapshot
   and re-applies. It never leaves an unconfirmed change applied without a
   rollback timer.
8. **Confirm** in the UI stops `nat64mgr-netrollback.timer` and clears
   `pending_confirmation`.
9. **Rollback** restores the last snapshot, runs `netplan generate` and
   `netplan apply`, and stops the timer.

> Losing management connectivity is the expected reason for the timer. If you
> cannot confirm, the gateway restores the previous networking configuration
> automatically. Keep console/IPMI access available for critical changes.

`network.apply` and `network.rollback` are critical, admin-level change
requests. They require a second administrator for approval when the requester is
not a superuser.

## 4. BIND9 DNS64

### 4.1 What DNS64 does

DNS64 synthesizes AAAA records from A records so IPv6-only clients can reach
IPv4-only destinations. The synthesized IPv6 address embeds the destination IPv4
address in the configured NAT64 prefix. That prefix **must equal the Jool
`pool6`**; otherwise the client receives an address Jool will not translate.

The application renders a fragment that is valid **only inside** BIND's
`options { ... }` block. It is included from `named.conf.options`:

```conf
options {
    directory "/var/cache/bind";
    recursion yes;
    allow-recursion { 2001:db8:64::/64; localhost; };

    include "/etc/bind/nat64-manager.conf";
};
```

Create an empty include before the first apply so BIND can start:

```bash
sudo install -m 0640 -o root -g bind /dev/null /etc/bind/nat64-manager.conf
```

### 4.2 Generated fragment

With DNS64 enabled, prefix `64:ff9b::/96`, upstreams `1.1.1.1` and `8.8.8.8`,
forward-only, DNSSEC validation enabled, and no extra exclusions:

```conf
# Managed by NAT64 ISP Manager. Do not edit manually.
# This file must be included INSIDE the options { } block of named.conf.options.
# Regenerate from the web UI; direct edits are overwritten on the next apply.
// generated_at: 2026-01-01T00:00:00+00:00

forwarders {
    1.1.1.1;
    8.8.8.8;
};
forward only;
dnssec-validation auto;

dns64 64:ff9b::/96 {
    clients { any; };
    mapped { any; };
    exclude {
        64:ff9b::/96;
    };
};
```

When DNS64 is disabled, the fragment keeps the forwarders and DNSSEC setting but
replaces the `dns64` block with a comment. A/AAAA forwarding continues; IPv6-only
clients can no longer reach A-only destinations.

The primary prefix is always inserted into `exclude` so DNS64 does not
re-synthesize an address that already lies in the NAT64 prefix. Additional
exclusion prefixes are appended. Exclusions are useful for IPv6 ranges that
must never be synthesized, such as special-use or local prefixes.

### 4.3 Validation and reload

The helper:

1. rejects fragment-level directives that could escape `options {}` or take over
   BIND: `zone`, `view`, `include`, `acl`, `key`, `controls`, `logging`, `server`,
   `master`, `allow-transfer`;
2. checks brace balance and size/NUL limits;
3. wraps the fragment in `options { ... }` and runs `named-checkconf` on a
   temporary file;
4. writes the real include atomically as `root:bind 0640`;
5. runs `named-checkconf` on the live configuration;
6. reloads with `rndc reconfig`, falling back to a service restart on failure;
7. restores the previous file if validation or reload fails.

### 4.4 Health and synthesis test

- **Health** checks the configured service (`named` or `bind9`) and queries
  `DNS_LISTEN_ADDRESS` for the test domain.
- **Test synthesis** queries `ipv4only.arpa` AAAA. It succeeds only when at least
  one returned address lies inside the configured prefix.
- A mismatch between the DNS64 prefix and the network NAT64 prefix produces a
  warning in the plan.

The test proves that this BIND instance synthesized an address for that domain
at that moment. It does not prove that Jool translated it, that upstream routing
works, or that every client can reach it.

## 5. nftables

### 5.1 Scope

Only `table inet nat64mgr` is managed. The helper refuses content containing
`flush ruleset`, `include`, `delete table inet`, or another table family, and it
requires exactly one `table inet nat64mgr`. Other nftables tables, Jool's
netfilter hooks, and host firewall policy are untouched.

The managed file must be loaded by the main nftables configuration:

```conf
# /etc/nftables.conf
include "/etc/nftables.d/*.nft"
```

The helper applies the ruleset directly with `nft -f`; the file is the persisted
copy, not the only apply path.

### 5.2 Generated ruleset (condensed example)

This sample shows the structure produced with firewall enabled, default input
policy accept, default forward policy drop, management allowlist, MSS clamping,
invalid-packet and SYN-flood protection. Real output includes every enabled
typed rule in priority order.

```nft
# Managed by NAT64 ISP Manager. Do not edit manually.
# Apply through the web UI change-approval workflow.
table inet nat64mgr {
    set pool4 {
        type ipv4_addr;
        flags interval;
        elements = { 203.0.113.1/32, 203.0.113.2/32 }
    }
    set pool6 {
        type ipv6_addr;
        flags interval;
        elements = { 64:ff9b::/96 }
    }
    set mgmt4 {
        type ipv4_addr;
        flags interval;
        elements = { 192.0.2.0/24 }
    }
    set mgmt6 {
        type ipv6_addr;
        flags interval;
    }
    set client6 {
        type ipv6_addr;
        flags interval;
        elements = { 2001:db8:64::/64 }
    }
    set blocked6 {
        type ipv6_addr;
        flags interval;
    }

    chain input {
        type filter hook input priority -10; policy accept;
        ct state established,related accept
        iifname "lo" accept
        ct state invalid drop
        tcp flags syn limit rate over 200/second burst 500 packets drop
        ip saddr @mgmt4 tcp dport { 22, 443 } accept
        tcp dport 22 drop
        tcp dport 443 drop
        ip6 saddr @client6 udp dport 53 accept
        ip6 saddr @client6 tcp dport 53 accept
        ip6 nexthdr ipv6-icmp accept
        udp dport 547 accept
        accept
    }

    chain forward {
        type filter hook forward priority -10; policy drop;
        ct state established,related accept
        ct state invalid drop
        ip6 saddr @blocked6 drop
        ip6 daddr @pool6 accept
        ip daddr @pool4 accept
        tcp flags syn tcp option maxseg size set rt mtu
        tcp flags syn limit rate over 200/second burst 500 packets drop
        drop
    }
}
```

### 5.3 Behaviour and safety

- `pool4` and `pool6` are populated from enabled database pools and the
  configured NAT64 prefix. `client6` comes from the client prefix. `blocked6`
  comes from blocked IPv6 clients.
- The forward chain accepts established/related traffic first, then pool6 and
  pool4 destinations, so NAT64 return traffic is not dropped by the default drop
  policy.
- DHCPv6 server traffic (`udp dport 547`) and IPv6 ICMP are accepted so the
  access network keeps working.
- Management ports are accepted only from `mgmt4`/`mgmt6`.
- If control-plane protection is enabled **without** a management allowlist, the
  renderer refuses to build a plan. This is an explicit anti-lockout rule.
- Applying with the firewall disabled deletes only `table inet nat64mgr`.
- Validation runs `nft -c -f <staged>` before the live apply.
- `firewall.apply` is an admin-level change request and requires a pre-change
  backup.

**Recovery from a bad ruleset may require an out-of-band console:**

```bash
sudo nft delete table inet nat64mgr
sudo rm -f /etc/nftables.d/nat64-manager.nft
```

### 5.4 Ordering caveat

This table uses the `filter` hook at priority `-10`. Other firewall tables may
also process the same packets. The managed table is not a complete host
firewall; it is the NAT64-specific policy layer. Review the host's other
nftables/iptables rules before assuming the effective policy.

## 6. Interaction checklist

Before applying, verify:

- [ ] pool4 addresses are routed to the gateway (or assigned to WAN);
- [ ] `pool6` matches the DNS64 prefix;
- [ ] clients have a route to `pool6` (usually via the gateway);
- [ ] the client prefix and LAN interface are correct;
- [ ] IPv4 and IPv6 forwarding are enabled;
- [ ] a management allowlist is set before enabling control-plane protection;
- [ ] BIND includes `/etc/bind/nat64-manager.conf` inside `options {}`;
- [ ] `/etc/nftables.conf` includes `/etc/nftables.d/*.nft`;
- [ ] you have console/IPMI access for network and firewall changes;
- [ ] a configuration backup exists.

## 7. What is not managed

- IP address assignment, VLANs, bonds, bridges, PPPoE, DHCPv6-PD.
- BGP/OSPF or any dynamic routing.
- BIND zones, views, ACLs, keys, logging or recursion policy outside the
  fragment.
- Other nftables tables and host firewall policy.
- keepalived scripts, notifications, tracking and failover logic.
- Jool's own netfilter rules; Jool installs those itself.

See [`09-TROUBLESHOOTING.md`](09-TROUBLESHOOTING.md) for failure recovery.
