import { useMemo, useState } from "react";
import {
  Activity,
  Cpu,
  Database,
  Gauge,
  HardDrive,
  Network,
  Play,
  RefreshCw,
  ServerCog,
  TerminalSquare,
} from "lucide-react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, errorText } from "../lib/api";
import {
  formatBps,
  formatBytes,
  formatDate,
  formatNumber,
  formatPercent,
  formatRelative,
  titleCase,
} from "../lib/format";
import type { SystemMetric } from "../lib/types";
import { usePolling } from "../hooks/usePolling";
import { useAuth } from "../state/AuthContext";
import { useToast } from "../state/ToastContext";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardHeader,
  CodeBlock,
  EmptyState,
  KeyValue,
  PageHeader,
  Spinner,
  StatCard,
  Tabs,
} from "../components/ui";

interface MetricResponse {
  metric: (SystemMetric & Record<string, unknown>) | null;
  message?: string;
}

interface CollectorState {
  enabled: boolean;
  interval_seconds: number;
  started_at: string | null;
  last_run_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  runs: number;
}

interface HealthResponse {
  host: { mode: string; available: boolean; reason: string | null; platform?: string };
  jool: { status: string; instances: unknown[]; message: string | null };
  dns64: { status: string; enabled: boolean; prefix?: string; last_error?: string | null };
  firewall: { status: string; enabled: boolean; applied_at: string | null; last_error: string | null };
  network: { status: string; pending_confirmation: boolean; last_applied_at: string | null };
  database: { dialect: string };
  collector: { enabled: boolean; interval_seconds: number };
}

interface HistoryPoint {
  captured_at: string;
  cpu_percent: number | null;
  memory_percent: number | null;
  net_rx_bps: number | null;
  net_tx_bps: number | null;
  clients_total: number | null;
  sessions_total: number | null;
  tcp_sessions: number | null;
  udp_sessions: number | null;
  icmp_sessions: number | null;
  pool_utilization_percent: number | null;
}

interface LogsResponse {
  status: string;
  unit?: string;
  lines: string[];
  message?: string | null;
  allowed?: string[];
}

function chartTime(value: string): string {
  const text = /(Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`;
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

const CHART_COLORS = {
  cpu: "#2563eb",
  memory: "#7c3aed",
  rx: "#059669",
  tx: "#d97706",
  sessions: "#0891b2",
};

export default function Monitoring() {
  const toast = useToast();
  const { can } = useAuth();
  const canOperate = can("changes.request");

  const [minutes, setMinutes] = useState(60);
  const [range, setRange] = useState<"60" | "360" | "1440">("60");
  const [busy, setBusy] = useState(false);

  const latest = usePolling<MetricResponse>("/system/metrics/latest", undefined, { interval: 5000 });
  const history = usePolling<{ items: HistoryPoint[] }>(
    "/system/metrics/history",
    { minutes, limit: 500 },
    { interval: 10000 },
  );
  const collector = usePolling<CollectorState>("/system/collector", undefined, { interval: 10000 });
  const health = usePolling<HealthResponse>("/system/health", undefined, { interval: 10000 });
  const ports = usePolling<LogsResponse>("/system/listen-ports", undefined, { interval: 20000 });

  const metric = latest.data?.metric ?? null;
  const points = history.data?.items ?? [];

  const sessionSeries = useMemo(
    () =>
      points.map((point) => ({
        ...point,
        label: chartTime(point.captured_at),
      })),
    [points],
  );

  const applyRange = (value: "60" | "360" | "1440") => {
    setRange(value);
    setMinutes(Number(value));
  };

  const collectNow = async () => {
    setBusy(true);
    try {
      const result = await api.post<{ result: unknown; collector: CollectorState }>("/system/collect");
      toast.success("Collection complete", "A new metric sample was stored and sessions were polled.");
      collector.setData(result.collector);
      void latest.refresh();
      void history.refresh();
    } catch (error) {
      toast.error("Collection failed", errorText(error));
    } finally {
      setBusy(false);
    }
  };

  const refreshAll = () => {
    void latest.refresh();
    void history.refresh();
    void collector.refresh();
    void health.refresh();
    void ports.refresh();
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="System Monitoring"
        description="Collector samples for CPU, memory, network throughput, NAT64 sessions and pool utilisation. Samples come from the server running this manager."
        breadcrumb="System"
        actions={
          <>
            <Button variant="outline" size="sm" onClick={refreshAll} loading={latest.loading || history.loading}>
              <RefreshCw className="h-4 w-4" /> Refresh
            </Button>
            {canOperate ? (
              <Button variant="primary" size="sm" onClick={() => void collectNow()} loading={busy}>
                <Play className="h-4 w-4" /> Collect now
              </Button>
            ) : null}
          </>
        }
      />

      {latest.error ? (
        <Alert tone="error" title="Metrics unavailable">
          {latest.error}
        </Alert>
      ) : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="CPU"
          value={formatPercent(metric?.cpu_percent)}
          hint={metric?.captured_at ? `Sampled ${formatRelative(metric.captured_at)}` : "No samples yet"}
          icon={<Cpu className="h-5 w-5" />}
          tone="blue"
        />
        <StatCard
          label="Memory"
          value={formatPercent(metric?.memory_percent)}
          hint={
            metric?.memory_used_bytes != null && metric?.memory_total_bytes != null
              ? `${formatBytes(Number(metric.memory_used_bytes))} of ${formatBytes(Number(metric.memory_total_bytes))}`
              : "Not reported"
          }
          icon={<HardDrive className="h-5 w-5" />}
          tone="violet"
        />
        <StatCard
          label="Network in / out"
          value={formatBps(metric?.net_rx_bps)}
          hint={`Out ${formatBps(metric?.net_tx_bps)}`}
          icon={<Network className="h-5 w-5" />}
          tone="green"
        />
        <StatCard
          label="Active sessions"
          value={formatNumber((metric?.sessions_total as number | undefined) ?? null)}
          hint={`TCP ${formatNumber((metric?.tcp_sessions as number | undefined) ?? null)} · UDP ${formatNumber(
            (metric?.udp_sessions as number | undefined) ?? null,
          )} · ICMP ${formatNumber((metric?.icmp_sessions as number | undefined) ?? null)}`}
          icon={<Activity className="h-5 w-5" />}
          tone="amber"
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Clients with sessions"
          value={formatNumber((metric?.clients_total as number | undefined) ?? null)}
          hint="Observed IPv6 clients with at least one active session"
          icon={<Database className="h-5 w-5" />}
        />
        <StatCard
          label="Pool utilisation"
          value={formatPercent((metric?.pool_utilization_percent as number | undefined) ?? null)}
          hint="Observed active session transports, not exact BIB counts"
          icon={<Gauge className="h-5 w-5" />}
        />
        <StatCard
          label="Load average (1m)"
          value={(metric?.load1 as number | undefined) ?? "—"}
          hint="Unavailable on platforms without load averages"
          icon={<ServerCog className="h-5 w-5" />}
        />
        <StatCard
          label="Collector runs"
          value={formatNumber(collector.data?.runs ?? 0)}
          hint={
            collector.data?.enabled
              ? `Running every ${collector.data.interval_seconds}s`
              : "Collector is disabled by configuration"
          }
          icon={<Activity className="h-5 w-5" />}
          tone={collector.data?.enabled ? "green" : "slate"}
        />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs
          value={range}
          onChange={applyRange}
          tabs={[
            { id: "60", label: "Last hour" },
            { id: "360", label: "Last 6 hours" },
            { id: "1440", label: "Last 24 hours" },
          ]}
        />
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {formatNumber(points.length)} sample{points.length === 1 ? "" : "s"} retained in this window
        </p>
      </div>

      {history.loading && points.length === 0 ? <Spinner label="Loading metric history" /> : null}

      {!history.loading && points.length === 0 ? (
        <Card>
          <EmptyState
            icon={<Activity className="h-6 w-6" />}
            title="No metric history in this window"
            description="The collector is disabled or has not stored a sample yet. Use Collect now to take one sample, or enable the collector in the backend configuration. No synthetic values are displayed."
            action={
              canOperate ? (
                <Button variant="primary" onClick={() => void collectNow()} loading={busy}>
                  <Play className="h-4 w-4" /> Collect now
                </Button>
              ) : undefined
            }
          />
        </Card>
      ) : null}

      {points.length > 0 ? (
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
          <Card>
            <CardHeader
              title="CPU and memory"
              description="Percent utilisation at each collector sample."
              icon={<Cpu className="h-4 w-4" />}
            />
            <div className="h-64 p-4">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={sessionSeries}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#94a3b8" opacity={0.2} />
                  <XAxis dataKey="label" tick={{ fontSize: 11 }} minTickGap={24} />
                  <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} unit="%" width={40} />
                  <Tooltip />
                  <Legend />
                  <Line
                    type="monotone"
                    dataKey="cpu_percent"
                    name="CPU"
                    stroke={CHART_COLORS.cpu}
                    dot={false}
                    isAnimationActive={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="memory_percent"
                    name="Memory"
                    stroke={CHART_COLORS.memory}
                    dot={false}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Card>
            <CardHeader
              title="Session counts"
              description="Active NAT64 sessions reported by the collector."
              icon={<Activity className="h-4 w-4" />}
            />
            <div className="h-64 p-4">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={sessionSeries}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#94a3b8" opacity={0.2} />
                  <XAxis dataKey="label" tick={{ fontSize: 11 }} minTickGap={24} />
                  <YAxis tick={{ fontSize: 11 }} width={48} allowDecimals={false} />
                  <Tooltip />
                  <Legend />
                  <Line
                    type="monotone"
                    dataKey="sessions_total"
                    name="Total"
                    stroke={CHART_COLORS.sessions}
                    dot={false}
                    isAnimationActive={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="tcp_sessions"
                    name="TCP"
                    stroke={CHART_COLORS.cpu}
                    dot={false}
                    isAnimationActive={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="udp_sessions"
                    name="UDP"
                    stroke={CHART_COLORS.memory}
                    dot={false}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Card className="xl:col-span-2">
            <CardHeader
              title="Network throughput"
              description="Bytes per second measured between collector samples."
              icon={<Network className="h-4 w-4" />}
            />
            <div className="h-64 p-4">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={sessionSeries}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#94a3b8" opacity={0.2} />
                  <XAxis dataKey="label" tick={{ fontSize: 11 }} minTickGap={24} />
                  <YAxis tick={{ fontSize: 11 }} width={64} tickFormatter={(value) => formatBps(Number(value))} />
                  <Tooltip formatter={(value: number) => formatBps(value)} />
                  <Legend />
                  <Line
                    type="monotone"
                    dataKey="net_rx_bps"
                    name="In"
                    stroke={CHART_COLORS.rx}
                    dot={false}
                    isAnimationActive={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="net_tx_bps"
                    name="Out"
                    stroke={CHART_COLORS.tx}
                    dot={false}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader
            title="Collector"
            description="Background job that polls Jool and stores system samples."
            icon={<Activity className="h-4 w-4" />}
            actions={<Badge status={collector.data?.enabled ? "active" : "disabled"} dot />}
          />
          <div className="space-y-4 p-5">
            <KeyValue
              items={[
                { label: "Enabled", value: collector.data?.enabled ? "Yes" : "No" },
                { label: "Interval", value: collector.data ? `${collector.data.interval_seconds}s` : "—" },
                { label: "Started", value: formatDate(collector.data?.started_at) },
                { label: "Last run", value: formatRelative(collector.data?.last_run_at) },
                { label: "Last success", value: formatRelative(collector.data?.last_success_at) },
                { label: "Runs", value: formatNumber(collector.data?.runs ?? 0) },
              ]}
            />
            {collector.data?.last_error ? (
              <Alert tone="error" title="Last collector error">
                {collector.data.last_error}
              </Alert>
            ) : null}
            {collector.data?.enabled === false ? (
              <Alert tone="info" title="Collector disabled">
                Metrics are only produced when the collector runs or when an operator uses Collect now. Enable
                <code className="mx-1">COLLECTOR_ENABLED</code> on a Linux deployment for continuous sampling.
              </Alert>
            ) : null}
          </div>
        </Card>

        <Card>
          <CardHeader
            title="Service health"
            description="Last stored service observations, not a live probe."
            icon={<ServerCog className="h-4 w-4" />}
          />
          <div className="p-5">
            <KeyValue
              items={[
                {
                  label: "Host helper",
                  value: (
                    <Badge status={health.data?.host.available ? "online" : health.data?.host.mode ?? "unknown"} dot>
                      {health.data?.host.available ? "Available" : titleCase(health.data?.host.mode ?? "unknown")}
                    </Badge>
                  ),
                },
                {
                  label: "Jool",
                  value: <Badge status={health.data?.jool.status ?? "unknown"}>{titleCase(health.data?.jool.status ?? "unknown")}</Badge>,
                },
                {
                  label: "DNS64",
                  value: <Badge status={health.data?.dns64.status ?? "unknown"}>{titleCase(health.data?.dns64.status ?? "unknown")}</Badge>,
                },
                {
                  label: "Firewall",
                  value: <Badge status={health.data?.firewall.status ?? "unknown"}>{titleCase(health.data?.firewall.status ?? "unknown")}</Badge>,
                },
                {
                  label: "Network",
                  value: <Badge status={health.data?.network.status ?? "unknown"}>{titleCase(health.data?.network.status ?? "unknown")}</Badge>,
                },
                {
                  label: "Database",
                  value: health.data?.database.dialect ?? "—",
                },
              ]}
            />
            {health.data?.host.reason ? (
              <Alert tone="warning" className="mt-4">
                {health.data.host.reason}
              </Alert>
            ) : null}
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader
          title="Listening ports"
          description="Read-only output from the privileged helper. No commands are accepted from the browser."
          icon={<TerminalSquare className="h-4 w-4" />}
        />
        <div className="p-5">
          {ports.data?.status === "unsupported" ? (
            <Alert tone="warning" title="Listening ports are unavailable">
              {ports.data.message ?? "Host integration requires Linux."}
            </Alert>
          ) : ports.data?.lines?.length ? (
            <CodeBlock title={ports.data.unit ?? "ss -lntup"}>{ports.data.lines.join("\n")}</CodeBlock>
          ) : (
            <p className="text-sm text-slate-500 dark:text-slate-400">
              {ports.data?.message ?? "No listening port information is available."}
            </p>
          )}
        </div>
      </Card>
    </div>
  );
}
