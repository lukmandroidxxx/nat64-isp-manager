import { useMemo, useState } from "react";
import {
  Download,
  FileText,
  Filter,
  RefreshCw,
  ScrollText,
  Search,
  TerminalSquare,
  X,
} from "lucide-react";
import { downloadFile, errorText } from "../lib/api";
import { formatDate } from "../lib/format";
import type { AuditEntry, Paginated } from "../lib/types";
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
  Field,
  KeyValue,
  Modal,
  PageHeader,
  Pagination,
  Select,
  Spinner,
  Tabs,
  TextInput,
} from "../components/ui";
import { DataTable } from "../components/DataTable";

interface AuditResponse extends Paginated<AuditEntry> {
  filters: Record<string, unknown>;
}

interface ActionCount {
  action: string;
  count: number;
}

interface LogsResponse {
  status: string;
  unit?: string;
  lines: string[];
  message?: string | null;
  allowed?: string[];
}

const UNITS = [
  "nat64mgr-api",
  "nat64mgr",
  "nat64-manager",
  "named",
  "bind9",
  "nftables",
  "systemd-networkd",
  "keepalived",
  "jool",
];

const PAGE_SIZES = [25, 50, 100, 200];

interface Filters {
  username: string;
  action: string;
  success: string;
  search: string;
  start: string;
  end: string;
  pageSize: string;
}

const EMPTY_FILTERS: Filters = {
  username: "",
  action: "",
  success: "",
  search: "",
  start: "",
  end: "",
  pageSize: "50",
};

function toIso(value: string): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

export default function Logs() {
  const toast = useToast();
  const { can } = useAuth();
  const canAudit = can("audit.read");

  const [tab, setTab] = useState<"audit" | "journal">("audit");
  const [form, setForm] = useState<Filters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<Filters>(EMPTY_FILTERS);
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState<AuditEntry | null>(null);

  const [unit, setUnit] = useState("nat64mgr-api");
  const [limit, setLimit] = useState("300");

  const audit = usePolling<AuditResponse>(
    "/audit",
    {
      page,
      page_size: Number(applied.pageSize),
      username: applied.username || undefined,
      action: applied.action || undefined,
      success: applied.success === "" ? undefined : applied.success === "true",
      search: applied.search || undefined,
      start: toIso(applied.start),
      end: toIso(applied.end),
    },
    { interval: 0, enabled: canAudit },
  );
  const actions = usePolling<{ items: ActionCount[] }>("/audit/actions", undefined, {
    interval: 0,
    enabled: canAudit,
  });
  const journal = usePolling<LogsResponse>(
    "/system/logs",
    { unit, limit: Number(limit) },
    { interval: 0 },
  );

  const rows = audit.data?.items ?? [];
  const total = audit.data?.total ?? 0;
  const pages = audit.data?.pages ?? 0;
  const pageSize = Number(applied.pageSize) || 50;

  const actionOptions = useMemo(() => actions.data?.items ?? [], [actions.data]);

  const applyFilters = () => {
    setApplied(form);
    setPage(1);
  };

  const clearFilters = () => {
    setForm(EMPTY_FILTERS);
    setApplied(EMPTY_FILTERS);
    setPage(1);
  };

  const exportCsv = async () => {
    try {
      await downloadFile("/audit/export.csv", "audit-log.csv", {
        username: applied.username || undefined,
        action: applied.action || undefined,
        success: applied.success === "" ? undefined : applied.success === "true",
        search: applied.search || undefined,
        start: toIso(applied.start),
        end: toIso(applied.end),
      });
      toast.success("Audit log exported", "The CSV contains the filtered rows.");
    } catch (error) {
      toast.error("Export failed", errorText(error));
    }
  };

  const activeFilterCount = [
    applied.username,
    applied.action,
    applied.success,
    applied.search,
    applied.start,
    applied.end,
  ].filter(Boolean).length;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Logs"
        description="Immutable audit trail for security-relevant actions, plus read-only systemd journal output for allowlisted units. No shell is exposed."
        breadcrumb="System"
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                void audit.refresh();
                void actions.refresh();
                void journal.refresh();
              }}
              loading={audit.loading || journal.loading}
            >
              <RefreshCw className="h-4 w-4" /> Refresh
            </Button>
            {tab === "audit" && canAudit ? (
              <Button variant="primary" size="sm" onClick={() => void exportCsv()}>
                <Download className="h-4 w-4" /> Export CSV
              </Button>
            ) : null}
          </>
        }
      />

      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "audit", label: "Audit log", badge: canAudit ? <Badge tone="slate">{total}</Badge> : undefined },
          { id: "journal", label: "System journal" },
        ]}
      />

      {tab === "audit" ? (
        <>
          {!canAudit ? (
            <Alert tone="warning" title="Audit log requires the audit.read permission">
              Your role cannot read audit records. Ask an administrator for access if you need this
              information.
            </Alert>
          ) : null}

          {audit.error ? (
            <Alert tone="error" title="Audit log unavailable">
              {audit.error}
            </Alert>
          ) : null}

          <Card>
            <CardHeader
              title="Filters"
              description={
                activeFilterCount
                  ? `${activeFilterCount} filter${activeFilterCount === 1 ? "" : "s"} applied`
                  : "Showing all audit events, newest first."
              }
              icon={<Filter className="h-4 w-4" />}
              actions={
                activeFilterCount ? (
                  <Button variant="ghost" size="sm" onClick={clearFilters}>
                    <X className="h-4 w-4" /> Clear
                  </Button>
                ) : undefined
              }
            />
            <div className="grid grid-cols-1 gap-4 p-5 sm:grid-cols-2 xl:grid-cols-3">
              <Field label="Search">
                <div className="relative">
                  <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
                  <TextInput
                    className="pl-9"
                    value={form.search}
                    placeholder="Action, user or resource"
                    onChange={(event) => setForm({ ...form, search: event.target.value })}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") applyFilters();
                    }}
                  />
                </div>
              </Field>
              <Field label="Username">
                <TextInput
                  value={form.username}
                  placeholder="admin"
                  onChange={(event) => setForm({ ...form, username: event.target.value })}
                />
              </Field>
              <Field label="Action">
                <Select
                  value={form.action}
                  onChange={(event) => setForm({ ...form, action: event.target.value })}
                >
                  <option value="">All actions</option>
                  {actionOptions.map((item) => (
                    <option key={item.action} value={item.action}>
                      {item.action} ({item.count})
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Result">
                <Select
                  value={form.success}
                  onChange={(event) => setForm({ ...form, success: event.target.value })}
                >
                  <option value="">Any result</option>
                  <option value="true">Success</option>
                  <option value="false">Failure</option>
                </Select>
              </Field>
              <Field label="Start">
                <TextInput
                  type="datetime-local"
                  value={form.start}
                  onChange={(event) => setForm({ ...form, start: event.target.value })}
                />
              </Field>
              <Field label="End">
                <TextInput
                  type="datetime-local"
                  value={form.end}
                  onChange={(event) => setForm({ ...form, end: event.target.value })}
                />
              </Field>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 px-5 py-3 dark:border-slate-800">
              <div className="flex items-center gap-2">
                <span className="text-xs text-slate-500 dark:text-slate-400">Rows per page</span>
                <Select
                  className="w-24"
                  value={form.pageSize}
                  onChange={(event) => setForm({ ...form, pageSize: event.target.value })}
                >
                  {PAGE_SIZES.map((size) => (
                    <option key={size} value={String(size)}>
                      {size}
                    </option>
                  ))}
                </Select>
              </div>
              <Button variant="primary" size="sm" onClick={applyFilters} loading={audit.loading}>
                <Filter className="h-4 w-4" /> Apply filters
              </Button>
            </div>
          </Card>

          <Card>
            <CardHeader
              title="Audit events"
              description="Audit rows are append-only. Detail payloads can contain client addresses and change plans."
              icon={<ScrollText className="h-4 w-4" />}
            />
            {audit.loading && !audit.data ? <Spinner label="Loading audit events" /> : null}
            <DataTable
              rows={rows}
              rowKey={(row) => row.id}
              loading={audit.loading}
              empty="No audit events match these filters."
              columns={[
                {
                  key: "created",
                  header: "Time",
                  render: (row) => <span className="whitespace-nowrap text-xs">{formatDate(row.created_at)}</span>,
                },
                {
                  key: "action",
                  header: "Action",
                  render: (row) => <code className="text-xs">{row.action}</code>,
                },
                {
                  key: "user",
                  header: "User",
                  render: (row) => (
                    <span className="text-xs">{row.username ?? <span className="text-slate-400">anonymous</span>}</span>
                  ),
                },
                {
                  key: "resource",
                  header: "Resource",
                  render: (row) =>
                    row.resource_type ? (
                      <span className="text-xs">
                        {row.resource_type}
                        {row.resource_id ? <span className="text-slate-400"> / {row.resource_id}</span> : null}
                      </span>
                    ) : (
                      <span className="text-slate-400">—</span>
                    ),
                },
                {
                  key: "result",
                  header: "Result",
                  render: (row) => (
                    <Badge tone={row.success ? "green" : "red"} dot>
                      {row.success ? "Success" : "Failure"}
                    </Badge>
                  ),
                },
                {
                  key: "ip",
                  header: "Source",
                  render: (row) => (
                    <span className="text-xs text-slate-500 dark:text-slate-400">{row.ip_address ?? "—"}</span>
                  ),
                },
                {
                  key: "actions",
                  header: "",
                  headerClassName: "text-right",
                  className: "text-right",
                  render: (row) => (
                    <Button variant="ghost" size="sm" onClick={() => setDetail(row)}>
                      Details
                    </Button>
                  ),
                },
              ]}
            />
            {audit.data ? (
              <Pagination page={page} pages={pages} total={total} pageSize={pageSize} onPage={setPage} />
            ) : null}
          </Card>
        </>
      ) : (
        <>
          <Card>
            <CardHeader
              title="Journal output"
              description="Only allowlisted unit names are accepted. The helper runs journalctl with typed parameters."
              icon={<TerminalSquare className="h-4 w-4" />}
              actions={
                <Button variant="outline" size="sm" onClick={() => void journal.refresh()} loading={journal.loading}>
                  <RefreshCw className="h-4 w-4" /> Reload
                </Button>
              }
            />
            <div className="grid grid-cols-1 gap-4 p-5 sm:grid-cols-3">
              <Field label="Unit">
                <Select value={unit} onChange={(event) => setUnit(event.target.value)}>
                  {(journal.data?.allowed ?? UNITS).map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Lines" hint="Maximum 2000.">
                <Select value={limit} onChange={(event) => setLimit(event.target.value)}>
                  {["100", "300", "500", "1000", "2000"].map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </Select>
              </Field>
              <div className="flex items-end">
                <Button variant="primary" size="sm" onClick={() => void journal.refresh()} loading={journal.loading}>
                  <FileText className="h-4 w-4" /> Load journal
                </Button>
              </div>
            </div>
          </Card>

          {journal.error ? (
            <Alert tone="error" title="Journal unavailable">
              {journal.error}
            </Alert>
          ) : null}

          {journal.data?.status === "unsupported" ? (
            <Alert tone="warning" title="Journal is unavailable from this server">
              {journal.data.message ?? "The privileged Linux host helper is not reachable."}
            </Alert>
          ) : null}

          {journal.data?.status === "invalid_unit" ? (
            <Alert tone="error" title="Unit rejected">
              The requested unit is not in the allowlist.
            </Alert>
          ) : null}

          <Card>
            <CardHeader
              title={journal.data?.unit ?? unit}
              description={journal.data?.message ?? "Read-only journal output."}
              icon={<TerminalSquare className="h-4 w-4" />}
            />
            <div className="p-5">
              {journal.loading && !journal.data ? (
                <Spinner label="Reading journal" />
              ) : journal.data?.lines?.length ? (
                <CodeBlock title={`journalctl -u ${journal.data.unit ?? unit} -n ${limit}`}>
                  {journal.data.lines.join("\n")}
                </CodeBlock>
              ) : (
                <p className="text-sm text-slate-500 dark:text-slate-400">
                  {journal.data?.message ?? "No journal lines were returned."}
                </p>
              )}
            </div>
          </Card>
        </>
      )}

      <Modal
        open={detail !== null}
        onClose={() => setDetail(null)}
        title={`Audit event ${detail?.id ?? ""}`}
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
                { label: "Action", value: <code>{detail.action}</code> },
                {
                  label: "Result",
                  value: (
                    <Badge tone={detail.success ? "green" : "red"} dot>
                      {detail.success ? "Success" : "Failure"}
                    </Badge>
                  ),
                },
                { label: "User", value: detail.username ?? "anonymous" },
                { label: "Time", value: formatDate(detail.created_at) },
                { label: "Resource", value: detail.resource_type ?? "—" },
                { label: "Resource ID", value: detail.resource_id ?? "—" },
                { label: "Source IP", value: detail.ip_address ?? "—" },
                { label: "User agent", value: detail.user_agent ?? "—" },
                { label: "Request ID", value: detail.request_id ?? "—" },
              ]}
            />
            <div>
              <p className="label">Detail</p>
              <pre className="code-block">{JSON.stringify(detail.detail, null, 2)}</pre>
            </div>
          </div>
        ) : null}
      </Modal>

      <Alert tone="info" title="What is logged">
        Logins, failed logins, user and role changes, setting changes, backup operations, change-request
        lifecycle events and host execution attempts are recorded. Entries are not editable from the UI.
      </Alert>
    </div>
  );
}
