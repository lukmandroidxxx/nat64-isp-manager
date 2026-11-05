import { useState } from "react";
import { Download, Eye, RefreshCw, Search, Trash2, Waypoints } from "lucide-react";
import { api, downloadFile, errorText } from "../lib/api";
import { formatDate, formatNumber, formatRelative, titleCase } from "../lib/format";
import type { JoolInstance, Session } from "../lib/types";
import { usePolling } from "../hooks/usePolling";
import { useAuth } from "../state/AuthContext";
import { useToast } from "../state/ToastContext";
import {
  Alert,
  Badge,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  Field,
  KeyValue,
  Modal,
  PageHeader,
  Pagination,
  Select,
  Spinner,
  TextInput,
} from "../components/ui";
import { DataTable } from "../components/DataTable";

interface SessionFilters {
  protocol: string;
  status: string;
  client: string;
  translated: string;
  remote: string;
  instance: string;
  search: string;
}

const EMPTY_FILTERS: SessionFilters = {
  protocol: "",
  status: "active",
  client: "",
  translated: "",
  remote: "",
  instance: "",
  search: "",
};

export default function Sessions() {
  const toast = useToast();
  const { can } = useAuth();
  const canExport = can("sessions.export");
  const canPurge = can("settings.write");

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [filters, setFilters] = useState<SessionFilters>(EMPTY_FILTERS);
  const [searchInput, setSearchInput] = useState("");
  const [detail, setDetail] = useState<Session | null>(null);
  const [purgeOpen, setPurgeOpen] = useState(false);
  const [retention, setRetention] = useState("24");
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);

  const sessions = usePolling<{ items: Session[]; page: number; page_size: number; total: number; pages: number; traffic: { note: string } }>(
    "/sessions",
    {
      page,
      page_size: pageSize,
      protocol: filters.protocol || undefined,
      status: filters.status || "active",
      client: filters.client || undefined,
      translated: filters.translated || undefined,
      remote: filters.remote || undefined,
      instance: filters.instance || undefined,
      search: filters.search || undefined,
    },
    { interval: 5000 },
  );
  const instances = usePolling<{ items: JoolInstance[] }>("/jool/instances", undefined, { interval: 30000 });

  const rows = sessions.data?.items ?? [];
  const total = sessions.data?.total ?? 0;
  const pages = sessions.data?.pages ?? 0;

  const apply = () => {
    setPage(1);
    setFilters((current) => ({ ...current, search: searchInput.trim() }));
  };

  const reset = () => {
    setSearchInput("");
    setFilters(EMPTY_FILTERS);
    setPage(1);
  };

  const exportCsv = async () => {
    setExporting(true);
    try {
      await downloadFile("/sessions/export.csv", "nat64-sessions.csv", {
        protocol: filters.protocol || undefined,
        status: filters.status || "active",
        client: filters.client || undefined,
        translated: filters.translated || undefined,
        remote: filters.remote || undefined,
        instance: filters.instance || undefined,
        search: filters.search || undefined,
      });
      toast.success("CSV exported", "Filtered sessions were streamed to the browser.");
    } catch (error) {
      toast.error("Export failed", errorText(error));
    } finally {
      setExporting(false);
    }
  };

  const purge = async () => {
    setBusy(true);
    try {
      const result = await api.delete<{ deleted?: number; message?: string }>("/sessions/expired", {
        retention_hours: Number(retention),
      });
      toast.success("Expired sessions purged", result.message ?? `${result.deleted ?? 0} row(s) removed.`);
      setPurgeOpen(false);
      await sessions.refresh();
    } catch (error) {
      toast.error("Purge failed", errorText(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="NAT64 Sessions"
        breadcrumb="NAT64"
        description="Observed Jool session table, refreshed every five seconds. Filters apply to the database snapshot; CSV export streams the matching rows."
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => void sessions.refresh()} loading={sessions.loading}>
              <RefreshCw className="h-4 w-4" /> Refresh
            </Button>
            {canExport ? (
              <Button variant="outline" size="sm" onClick={() => void exportCsv()} loading={exporting}>
                <Download className="h-4 w-4" /> Export CSV
              </Button>
            ) : null}
            {canPurge ? (
              <Button variant="outline" size="sm" onClick={() => setPurgeOpen(true)}>
                <Trash2 className="h-4 w-4" /> Purge expired
              </Button>
            ) : null}
          </>
        }
      />

      {sessions.error ? (
        <Alert tone="error" title="Session list unavailable">
          {sessions.error}
        </Alert>
      ) : null}

      <Alert tone="info" title="Traffic counters are unavailable">
        Jool's session CSV does not expose creation timestamps or per-session byte counters.{" "}
        <strong>First observed</strong> is the first time this manager saw the row, and traffic values are{" "}
        <code>null</code>, not estimates. {sessions.data?.traffic.note ?? ""}
      </Alert>

      <Card>
        <div className="border-b border-slate-200 p-4 dark:border-slate-800">
          <form
            className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4"
            onSubmit={(event) => {
              event.preventDefault();
              apply();
            }}
          >
            <Field label="Search">
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                <TextInput
                  className="pl-9"
                  value={searchInput}
                  onChange={(event) => setSearchInput(event.target.value)}
                  placeholder="Client, translated or remote address"
                />
              </div>
            </Field>
            <Field label="Protocol">
              <Select value={filters.protocol} onChange={(e) => { setPage(1); setFilters({ ...filters, protocol: e.target.value }); }}>
                <option value="">All protocols</option>
                <option value="tcp">TCP</option>
                <option value="udp">UDP</option>
                <option value="icmp">ICMP</option>
              </Select>
            </Field>
            <Field label="Status">
              <Select value={filters.status} onChange={(e) => { setPage(1); setFilters({ ...filters, status: e.target.value }); }}>
                <option value="active">Active</option>
                <option value="expired">Expired</option>
                <option value="stale">Stale</option>
                <option value="all">All</option>
              </Select>
            </Field>
            <Field label="Jool instance">
              <Select value={filters.instance} onChange={(e) => { setPage(1); setFilters({ ...filters, instance: e.target.value }); }}>
                <option value="">All instances</option>
                {(instances.data?.items ?? []).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Client IPv6 contains">
              <TextInput value={filters.client} onChange={(e) => setFilters({ ...filters, client: e.target.value })} placeholder="2001:db8" />
            </Field>
            <Field label="Translated IPv4 exactly">
              <TextInput value={filters.translated} onChange={(e) => setFilters({ ...filters, translated: e.target.value })} placeholder="203.0.113.10" />
            </Field>
            <Field label="Remote IPv4 contains">
              <TextInput value={filters.remote} onChange={(e) => setFilters({ ...filters, remote: e.target.value })} placeholder="198.51.100" />
            </Field>
            <div className="flex items-end gap-2">
              <Button type="submit" variant="primary" size="sm">
                <Search className="h-4 w-4" /> Apply
              </Button>
              <Button type="button" variant="ghost" size="sm" onClick={reset}>
                Reset
              </Button>
            </div>
            <div className="flex items-end justify-end gap-2 lg:col-span-4">
              <span className="text-xs text-slate-500 dark:text-slate-400">Rows per page</span>
              <Select className="w-20" value={String(pageSize)} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}>
                {[25, 50, 100, 200, 500].map((size) => (
                  <option key={size} value={size}>
                    {size}
                  </option>
                ))}
              </Select>
            </div>
          </form>
        </div>

        {sessions.loading && !sessions.data ? <Spinner label="Loading sessions" /> : null}
        {!sessions.loading && rows.length === 0 ? (
          <EmptyState
            icon={<Waypoints className="h-6 w-6" />}
            title="No sessions match these filters"
            description={
              total === 0
                ? "The collector populates this table from the Jool session CSV. A server without host access reports an empty table rather than fabricated data."
                : "Reset or loosen the filters."
            }
          />
        ) : null}

        {rows.length > 0 ? (
          <>
            <DataTable
              rows={rows}
              rowKey={(row) => row.id}
              dense
              columns={[
                {
                  key: "protocol",
                  header: "Proto",
                  render: (row) => <Badge tone={row.protocol === "tcp" ? "blue" : row.protocol === "udp" ? "violet" : "slate"}>{row.protocol.toUpperCase()}</Badge>,
                },
                {
                  key: "client",
                  header: "Client IPv6 : port",
                  render: (row) => (
                    <code className="text-xs">
                      {row.client_ipv6}:{row.client_port}
                    </code>
                  ),
                },
                {
                  key: "translated",
                  header: "Translated IPv4 : port",
                  render: (row) => (
                    <code className="text-xs font-semibold">
                      {row.translated_ipv4}:{row.translated_port}
                    </code>
                  ),
                },
                {
                  key: "remote",
                  header: "Remote IPv4 : port",
                  render: (row) => (
                    <code className="text-xs">
                      {row.remote_ipv4}:{row.remote_port}
                    </code>
                  ),
                },
                { key: "instance", header: "Instance", render: (row) => row.instance_name ?? "—" },
                {
                  key: "state",
                  header: "Jool state",
                  render: (row) => <span className="text-xs">{row.jool_state ?? "—"}</span>,
                },
                {
                  key: "expires",
                  header: "Expires in",
                  render: (row) =>
                    row.status === "active" ? (
                      <span className="text-xs">{formatNumber(row.expires_in_seconds)}s</span>
                    ) : (
                      <span className="text-xs text-slate-400">{titleCase(row.status)}</span>
                    ),
                },
                {
                  key: "first",
                  header: "First observed",
                  render: (row) => (
                    <span className="text-xs text-slate-500 dark:text-slate-400" title={formatDate(row.first_seen_at)}>
                      {formatRelative(row.first_seen_at)}
                    </span>
                  ),
                },
                {
                  key: "traffic",
                  header: "Bytes up/down",
                  render: () => <span className="text-xs text-slate-400">null / null</span>,
                },
                {
                  key: "actions",
                  header: "",
                  headerClassName: "text-right",
                  className: "text-right",
                  render: (row) => (
                    <Button variant="ghost" size="icon" onClick={() => setDetail(row)} aria-label="Session details">
                      <Eye className="h-4 w-4" />
                    </Button>
                  ),
                },
              ]}
            />
            <Pagination page={sessions.data?.page ?? page} pages={pages} total={total} pageSize={pageSize} onPage={setPage} />
          </>
        ) : null}
      </Card>

      <Modal
        open={detail !== null}
        onClose={() => setDetail(null)}
        title="NAT64 session detail"
        description="Observed session transport. Counters are unavailable from Jool."
        size="lg"
        footer={
          <Button variant="outline" onClick={() => setDetail(null)}>
            Close
          </Button>
        }
      >
        {detail ? (
          <div className="space-y-4">
            <KeyValue
              items={[
                { label: "Session ID", value: detail.id },
                { label: "Instance", value: detail.instance_name ?? "—" },
                { label: "Protocol", value: detail.protocol.toUpperCase() },
                { label: "Status", value: titleCase(detail.status) },
                { label: "Client IPv6", value: <code>{detail.client_ipv6}</code> },
                { label: "Client port", value: detail.client_port },
                { label: "Translated IPv4", value: <code>{detail.translated_ipv4}</code> },
                { label: "Translated port", value: detail.translated_port },
                { label: "Remote IPv4", value: <code>{detail.remote_ipv4}</code> },
                { label: "Remote port", value: detail.remote_port },
                { label: "Jool state", value: detail.jool_state ?? "—" },
                { label: "Expires in", value: `${formatNumber(detail.expires_in_seconds)} seconds` },
                { label: "First observed", value: formatDate(detail.first_seen_at) },
                { label: "Last seen", value: formatDate(detail.last_seen_at) },
                { label: "Expired at", value: formatDate(detail.expired_at) },
                { label: "Bytes up", value: "null (not provided by Jool)" },
                { label: "Bytes down", value: "null (not provided by Jool)" },
              ]}
            />
            <Alert tone="info">{detail.traffic_note}</Alert>
          </div>
        ) : null}
      </Modal>

      <ConfirmDialog
        open={purgeOpen}
        onClose={() => setPurgeOpen(false)}
        onConfirm={() => void purge()}
        title="Purge expired session records"
        description="Deletes stored expired/stale session rows from the database only. Live Jool state is untouched."
        confirmLabel="Purge records"
        danger
        loading={busy}
      >
        <Field label="Retention (hours)" hint="Rows that expired longer ago than this are removed.">
          <TextInput type="number" min={1} max={8760} value={retention} onChange={(e) => setRetention(e.target.value)} />
        </Field>
      </ConfirmDialog>
    </div>
  );
}
