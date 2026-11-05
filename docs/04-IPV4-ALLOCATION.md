# 04 – IPv4 allocation and capacity

## 1. The model in one paragraph

NAT64 does **not** assign a public IPv4 address to a subscriber. Instead, Jool
maintains a pool4 of public IPv4 addresses and chooses an address/port
translation **per connection** (more precisely, per binding). Many IPv6 clients
share the same IPv4 addresses, and multiple concurrent connections can use
different ports on the same address. An address row in this application is a
**pool4 entry**, not a subscriber assignment, not a lease, and not a reservation.

Anyone operating this system must understand this before sizing a deployment.
Trying to give each subscriber a dedicated public IPv4 address is a different
product (a static NAT / 1:1 NAT design) and is not what this software does.

## 2. Pool4 entry anatomy

A pool entry is defined by:

| Field | Meaning |
|---|---|
| `address` | One public IPv4 address, for example `203.0.113.7` |
| `port_min` | First port (or ICMP identifier) Jool may use |
| `port_max` | Last port Jool may use |
| `enabled` | Whether the entry should exist in Jool |
| `label` | Free-form operator note |
| `status` | `pending`, `active`, `disabled`, `error`, `exhausted` |
| `last_synced_at` | Last successful pool4 synchronization |

Pools group entries and bind them to one Jool instance:

```
IPv4 pool "public-a"  ->  jool_instance_id  ->  Jool instance "nat64"
  address 203.0.113.1  ports 61001-65535
  address 203.0.113.2  ports 61001-65535
  ...
```

The default port range is `61001-65535` (`JOOL_POOL4_DEFAULT_PORT_MIN/MAX`).
Only individual addresses are accepted; a subnet must be expanded first. The
CIDR import accepts up to 1024 addresses per request and rejects addresses
outside IPv4 or ranges larger than the limit. Within a pool, two entries for the
same address may not have overlapping port ranges.

## 3. How Jool allocates

For each new flow, Jool selects an available transport address from pool4:

- **TCP** uses a TCP port.
- **UDP** uses a UDP port.
- **ICMP** uses the ICMP identifier field.

The three protocol spaces are independent. A TCP port `61001` on
`203.0.113.1` and a UDP port `61001` on the same address do not collide.

Jool reuses a binding when it can. A BIB entry maps a client transport tuple to
a pool4 transport tuple; a session is a binding plus a remote destination.
Multiple sessions can therefore share one BIB entry and one translated port
when the client reuses the same source port toward multiple destinations. In
practice most client connections use distinct ephemeral source ports and
consume distinct pool4 ports.

Consequences:

- allocation is **dynamic**, driven by live traffic, not by the UI;
- the subscriber inventory does not own IPv4 addresses;
- adding an address to the database does nothing until pool4 is synchronized;
- a pool4 address must be routed to the gateway by upstream equipment;
- removing a pool4 entry cascades to Jool BIB/session state for that entry.

## 4. Capacity formula

For one pool4 entry:

```
transport capacity = (port_max - port_min + 1) * 3
```

The `* 3` accounts for TCP, UDP and ICMP. With the default range:

```
(65535 - 61001 + 1) * 3 = 4535 * 3 = 13,605 transport endpoints per address
```

For a pool of `N` addresses with the same range:

```
total transport capacity = N * 13,605
```

Examples:

| Pool | Approximate transport capacity |
|---|---:|
| 1 address | 13,605 |
| 8 addresses | 108,840 |
| 16 addresses | 217,680 |
| 32 addresses | 435,360 |
| 64 addresses | 870,720 |

This is capacity in **distinct translated transport identifiers**, not a promise
of how many subscriber connections will succeed. BIB reuse, protocol mix,
timeouts, remote destinations and kernel limits all affect the real number. It
is an upper bound for planning, not a benchmark.

### 4.1 Why sessions can exceed used ports

A single translated port can be referenced by more than one session when a
client reuses its source port toward several destinations. Therefore:

```
ports_used <= active sessions
```

is normal. The UI reports both:

- `sessions` – observed active sessions using the address;
- `ports_used` – distinct translated ports per protocol actually observed;
- `bindings` – distinct `(client_ipv6, client_port)` pairs observed on the
  address (a UI approximation, not a Jool BIB dump).

Capacity utilisation is calculated from `ports_used`, not from session count.

## 5. What the UI shows

The IPv4 Pool page and dashboard expose, per address:

| Field | Meaning |
|---|---|
| `ports_total` | `(port_max - port_min + 1) * 3` |
| `ports_used` | Sum of distinct translated ports observed per protocol |
| `usage_percent` | `ports_used / ports_total`, rounded |
| `usage_basis` | Always `observed_active_sessions` |
| `tcp`, `udp`, `icmp` | Active sessions by protocol |
| `bindings` | Distinct client transport tuples seen |
| `sessions` | Active sessions translated through the address |
| `status` | Derived state, see below |

Status derivation:

1. `disabled` if the entry or its pool is disabled;
2. `exhausted` if observed usage is at least **95%**;
3. `error` if the last synchronization recorded an error;
4. `pending` if the entry has not been synchronized successfully;
5. otherwise the stored `active` state.

These are **observations**, not authoritative Jool BIB counters. The Jool
session listing available to the collector does not expose per-address BIB
occupancy. A quiet address can still have BIB entries that are not represented
by an active session at the sampled moment. Do not use the percentage as a
billing or admission-control source.

## 6. Synchronizing pool4

Database edits do not touch Jool. Synchronization is a change request
(`pool4.sync`) that:

1. reads live pool4 for TCP, UDP and ICMP from Jool;
2. builds the desired set from enabled pools and enabled addresses, one entry
   per protocol per address;
3. computes **adds**: desired but absent;
4. computes **removes**: present in Jool and belonging to a managed address, but
   disabled or removed in the database;
5. reports **unmanaged** entries that exist in Jool but do not belong to any
   managed address; these are left untouched and generate a warning;
6. reports how many active sessions use addresses that would be removed;
7. executes typed `jool pool4 add/remove` calls through the helper;
8. re-reads pool4 and records `observed_pool4`, `last_synced_at` and per-address
   status.

A plan with errors cannot be executed. Removing an entry is destructive to
BIB/session state by Jool design; the plan warns and counts affected sessions.
Adding an address requires routing to be in place first.

## 7. Exhaustion

Exhaustion is per address, per protocol, over the configured port range. It can
occur even when other addresses in the pool are idle if Jool has already chosen
an address, but Jool normally spreads allocations across the pool. When no
transport address is available, new connections cannot be translated and fail;
existing sessions continue until they expire or are removed.

Symptoms:

- `usage_percent` at or above 95% on one or more addresses;
- rising connection failures while the gateway CPU and interfaces are healthy;
- Jool session output stops showing new client tuples;
- pool4 display shows the expected addresses and ranges but allocations stall.

Response:

1. Confirm the failure is NAT64 port exhaustion, not routing, DNS or firewall.
2. Add one or more routed IPv4 addresses to the pool.
3. Run pool4 sync and approve it.
4. Watch `ports_used` per address for several minutes.
5. If only one protocol is exhausted, check whether the workload is dominated by
   that protocol; the port range is shared within a protocol space.
6. Do **not** widen the port range to 1–65535 casually: low ports are privileged
   and may conflict with host services or upstream policy. Keep 61001–65535
   unless you have a specific reason.

Exhaustion is not fixed by creating more subscribers, pools or UI records. Only
additional routed addresses and port capacity (or reduced concurrency/timeouts)
add capacity.

## 8. Sizing guidance (not a guarantee)

Use the formula to check order of magnitude, then test with your own traffic.

Example: 500 IPv6-only clients, 200 concurrent sessions each:

```
active sessions = 500 * 200 = 100,000
capacity at 32 addresses = 435,360 transport endpoints
naive utilisation = 23%
```

That looks comfortable, but:

- sessions are not evenly spread across protocols;
- some clients may open thousands of short-lived connections;
- BIB entries can outlive the session sample;
- the collector samples every 5 seconds, so short spikes are invisible;
- upstream conntrack, Jool timeouts and file-descriptor limits may fail first.

A reasonable operational rule is to keep steady-state observed usage below
60–70% and alert at 85%, leaving room for spikes. This is an operational
guideline, not a tested SLA. Record your own observations in the Linux lab and
in production before making commitments.

## 9. Common misconceptions

| Misconception | Reality |
|---|---|
| “Each client gets one IPv4 address.” | No. Clients share pool4 addresses per connection. |
| “A client is assigned `203.0.113.5`.” | No. The address is a pool entry; the mapping is per binding. |
| “Adding an address takes effect immediately.” | Only after an approved pool4 sync. |
| “The usage percentage is exact BIB usage.” | It is derived from observed active sessions. |
| “32 addresses means 32 subscribers.” | No. It is hundreds of thousands of transport endpoints. |
| “Pool4 removal is harmless.” | It cascades to BIB/session state for that address. |
| “The gateway can use addresses that are not routed to it.” | No. Upstream routing/ARP must deliver return traffic. |
| “A larger port range always helps.” | Only within the same protocol; check upstream and host conflicts. |

## 10. Related pages

- IPv4 Pool page – add/edit/import addresses, view observed usage.
- Jool Management – pool4 display and synchronization.
- Change Requests – dry-run plan, approval, execution, audit.
- [`05-NETWORK-DNS64.md`](05-NETWORK-DNS64.md) – routing, DNS64 and firewall.
- [`03-ADMINISTRATION.md`](03-ADMINISTRATION.md) – roles and change workflow.
