import { useMemo, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  ArrowUpRight,
  Database,
  Gauge,
  Globe2,
  Network,
  RefreshCw,
  ServerCog,
  Shield,
  Users,
  Waypoints,
} from "lucide-react";
import {
  formatBps,
  formatBytes,
  formatDate,
  formatNumber,
  formatPercent,
  formatRelative,
  titleCase,
} from "../lib/format";
import type { Dashboard as DashboardData } from "../lib/types";
import { usePolling } from "../hooks/usePolling";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardHeader,
  PageHeader,
  ProgressBar,
  Spinner,
  StatCard,
} from "../components/ui";
import { DataTable } from "../components/DataTable";

function ServiceTile({
  icon,
  name,
  status,
  detail,
  to,
}: {
  icon: ReactNode;
  name: string;
  status: string;
  detail: React.ReactNode;
  to?: string;
}) {
  const content = (
    <div className="flex items-start justify-between gap-3 rounded-xl border border-slate-200 bg-white p-4 transition-colors hover:border-brand-300 dark:border-slate-800 dark:bg-slate-900 dark:hover:border-brand-700">
      <div className="flex min-w-0 items-start gap-3">
        <div className="rounded-lg bg-slate-100 p-2 text-slate-500 dark:bg-slate-800 dark:text-slate-400">
          {icon}
        </div>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">{name}</p>
          <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{detail}</div>
        </div>
      </div>
      <Badge status={status} dot>
        {titleCase(status)}
      </Badge>
    </div>
  );
  return to ? <Link to={to}>{content}</Link> : content;
}

export default function Dashboard() {
  const { data, error, loading, refresh, updatedAt } = usePolling<DashboardData>("/dashboard");

  const topClients = data?.top_clients ?? [];
  const services = data?.services;
  const system = data?.system;
  const allocation = data?.allocation;

  const sessionTotal = data?.totals.sessions_active ?? 0;
  const utilization = data?.pool.utilization_percent ?? 0;
  const utilizationTone = utilization >= 90 ? "red" : utilization >= 70 ? "amber" : "green";

  const limitations = useMemo(() => data?.limitations ?? [], [data]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Dashboard"
        breadcrumb="Overview"
        description="Live control-plane state from the database and the privileged Linux host helper. Counters refresh every five seconds."
        actions={
          <>
            <span className="hidden text-xs text-slate-400 sm:inline">
              Updated {updatedAt ? formatRelative(new Date(updatedAt).toISOString()) : "—"}
            </span>
            <Button variant="outline" size="sm" onClick={() => void refresh()} loading={loading}>
              <RefreshCw className="h-4 w-4" /> Refresh
            </Button>
          </>
        }
      />

      {error ? (
        <Alert tone="error" title="Dashboard data unavailable">
          {error}
        </Alert>
      ) : null}

      {loading && !data ? <Spinner label="Loading dashboard" /> : null}

      {data?.services.host.available === false ? (
        <Alert tone="warning" title="Host data plane is not available on this server">
          {data.services.host.reason ??
            "The privileged helper is unavailable, so Jool, BIND, nftables and netplan state cannot be observed. Database records are still shown, and no host commands will be run."}
        </Alert>
      ) : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Active NAT64 sessions"
          value={formatNumber(sessionTotal)}
          hint={`TCP ${formatNumber(data?.totals.sessions_tcp)} · UDP ${formatNumber(
            data?.totals.sessions_udp,
          )} · ICMP ${formatNumber(data?.totals.sessions_icmp)}`}
          icon={<Waypoints className="h-5 w-5" />}
          tone="blue"
        />
        <StatCard
          label="IPv6 clients"
          value={formatNumber(data?.totals.clients_active)}
          hint={`${formatNumber(data?.totals.clients_known)} known · identity is observed IPv6 address`}
          icon={<Users className="h-5 w-5" />}
          tone="violet"
        />
        <StatCard
          label="Observed pool utilisation"
          value={formatPercent(utilization)}
          hint={`${formatNumber(data?.pool.observed_ports_used)} of ${formatNumber(
            data?.pool.transport_capacity,
          )} observed transport ports`}
          icon={<Gauge className="h-5 w-5" />}
          tone={utilizationTone}
          footer={
            <div className="mt-3">
              <ProgressBar value={utilization} tone={utilizationTone} />
            </div>
          }
        />
        <StatCard
          label="Pending change requests"
          value={formatNumber(data?.totals.pending_changes)}
          hint="Host-mutating actions wait for dry-run and approval"
          icon={<Activity className="h-5 w-5" />}
          tone={data?.totals.pending_changes ? "amber" : "green"}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
        <ServiceTile
          icon={<ServerCog className="h-4 w-4" />}
          name="Jool 4"
          status={services?.jool.status ?? "unknown"}
          detail={
            services?.jool.message ??
            `${formatNumber(services?.jool.instances?.length ?? 0)} configured instance(s)`
          }
          to="/jool"
        />
        <ServiceTile
          icon={<Globe2 className="h-4 w-4" />}
          name="DNS64 (BIND)"
          status={services?.dns64.status ?? "unknown"}
          detail={
            services?.dns64.enabled
              ? `${services.dns64.prefix} · ${services.dns64.last_error ?? "no reported error"}`
              : "Disabled in DNS64 settings"
          }
          to="/dns64"
        />
        <ServiceTile
          icon={<Shield className="h-4 w-4" />}
          name="nftables firewall"
          status={services?.firewall.status ?? "unknown"}
          detail={
            services?.firewall.enabled
              ? `Managed table active · applied ${formatRelative(services.firewall.applied_at)}`
              : "Managed table not enabled"
          }
          to="/firewall"
        />
        <ServiceTile
          icon={<Network className="h-4 w-4" />}
          name="Network / netplan"
          status={services?.network.status ?? "unknown"}
          detail={
            services?.network.pending_confirmation
              ? "Rollback timer is pending confirmation"
              : `Last applied ${formatRelative(services?.network.last_applied_at)}`
          }
          to="/network"
        />
        <ServiceTile
          icon={<Database className="h-4 w-4" />}
          name="Database"
          status="ok"
          detail={
            services?.database.dialect === "sqlite"
              ? "SQLite — development only, PostgreSQL required in production"
              : `PostgreSQL (${services?.database.dialect})`
          }
          to="/settings"
        />
        <ServiceTile
          icon={<Activity className="h-4 w-4" />}
          name="Metrics collector"
          status={services?.collector.enabled ? "active" : "disabled"}
          detail={`${services?.collector.interval_seconds ?? 5}s interval · ${
            data?.system.captured_at ? `last sample ${formatRelative(data.system.captured_at)}` : "no samples yet"
          }`}
          to="/monitoring"
        />
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader
            title="Top clients by active sessions"
            description="The client identity is the observed IPv6 address. IPv6 privacy extensions can rotate this address."
            icon={<Users className="h-4 w-4" />}
            actions={
              <Link to="/clients">
                <Button variant="ghost" size="sm">
                  View clients <ArrowUpRight className="h-3.5 w-3.5" />
                </Button>
              </Link>
            }
          />
          <DataTable
            rows={topClients}
            rowKey={(row) => row.id}
            empty="No IPv6 clients have been observed by the collector yet."
            columns={[
              {
                key: "address",
                header: "IPv6 address",
                render: (row) => <code className="text-xs">{row.address}</code>,
              },
              {
                key: "hostname",
                header: "Hostname",
                render: (row) => row.hostname ?? <span className="text-slate-400">—</span>,
              },
              {
                key: "sessions",
                header: "Active sessions",
                render: (row) => (
                  <span className={row.active_session_count > 0 ? "font-semibold" : "text-slate-400"}>
                    {formatNumber(row.active_session_count)}
                  </span>
                ),
              },
              {
                key: "state",
                header: "State",
                render: (row) =>
                  row.is_blocked ? <Badge tone="red">Blocked</Badge> : <Badge tone="green">Allowed</Badge>,
              },
              {
                key: "last",
                header: "Last seen",
                render: (row) => (
                  <span className="text-xs text-slate-500 dark:text-slate-400">
                    {formatRelative(row.last_seen_at)}
                  </span>
                ),
              },
            ]}
          />
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader
              title="Allocation model"
              description="How the public IPv4 pool is used"
              icon={<Gauge className="h-4 w-4" />}
            />
            <div className="space-y-3 p-5 text-sm text-slate-600 dark:text-slate-300">
              <p className="font-medium text-slate-800 dark:text-slate-100">
                {allocation?.mode ?? "dynamic_per_connection"}
              </p>
              <p>{allocation?.note ?? "Each NAT64 connection is allocated a public IPv4 transport address by Jool."}</p>
              <div className="rounded-lg bg-slate-50 p-3 text-xs dark:bg-slate-800/60">
                <span className="font-semibold">Utilisation basis:</span>{" "}
                {allocation?.utilization_basis ?? "observed_active_sessions"}. Values are observed active
                session transports, not exact per-address BIB counts.
              </div>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                The same IPv4 address and port range is shared by many subscribers concurrently. IPv4
                addresses are never leased exclusively to a subscriber.
              </p>
            </div>
          </Card>

          <Card>
            <CardHeader title="System sample" description="Latest collector observation" icon={<Activity className="h-4 w-4" />} />
            <div className="grid grid-cols-2 gap-4 p-5 text-sm">
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-400">CPU</p>
                <p className="mt-1 text-lg font-semibold text-slate-800 dark:text-slate-100">
                  {formatPercent(system?.cpu_percent)}
                </p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-400">Memory</p>
                <p className="mt-1 text-lg font-semibold text-slate-800 dark:text-slate-100">
                  {formatPercent(system?.memory_percent)}
                </p>
                {system?.memory_used_bytes != null && system.memory_total_bytes != null ? (
                  <p className="text-xs text-slate-500">
                    {formatBytes(system.memory_used_bytes)} / {formatBytes(system.memory_total_bytes)}
                  </p>
                ) : null}
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-400">Network in</p>
                <p className="mt-1 font-semibold text-slate-800 dark:text-slate-100">
                  {formatBps(system?.net_rx_bps)}
                </p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-400">Network out</p>
                <p className="mt-1 font-semibold text-slate-800 dark:text-slate-100">
                  {formatBps(system?.net_tx_bps)}
                </p>
              </div>
              <div className="col-span-2 text-xs text-slate-500 dark:text-slate-400">
                Captured {system?.captured_at ? formatDate(system.captured_at) : "never"}
              </div>
            </div>
          </Card>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Traffic accounting limitation" description="What Jool exposes to this manager" />
          <div className="space-y-2 p-5 text-sm text-slate-600 dark:text-slate-300">
            <p>
              Per-session byte counters are <strong>not available</strong> from the Jool session CSV.
              Session rows display <code>null</code> traffic rather than fabricated values.
            </p>
            <p className="text-xs text-slate-500 dark:text-slate-400">{data?.traffic.note}</p>
          </div>
        </Card>

        <Card>
          <CardHeader title="Known limitations" description="Explicitly reported by the server" />
          <ul className="space-y-2 p-5 text-sm text-slate-600 dark:text-slate-300">
            {limitations.length === 0 ? (
              <li className="text-slate-500 dark:text-slate-400">No limitations reported.</li>
            ) : (
              limitations.map((item, index) => (
                <li key={index} className="flex gap-2">
                  <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" />
                  <span>{item}</span>
                </li>
              ))
            )}
          </ul>
        </Card>
      </div>

      {data ? (
        <p className="text-right text-[11px] text-slate-400">
          Snapshot generated {formatDate(data.generated_at)}
        </p>
      ) : null}
    </div>
  );
}
