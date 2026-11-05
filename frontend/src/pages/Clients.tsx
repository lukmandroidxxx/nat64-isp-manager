import { useState } from "react";
import {
  Ban,
  CheckCircle2,
  Download,
  Pencil,
  RefreshCw,
  Search,
  ShieldCheck,
  Users,
} from "lucide-react";
import { api, downloadFile, errorText } from "../lib/api";
import { formatNumber, formatRelative } from "../lib/format";
import type { Client, IPv6Prefix, Paginated } from "../lib/types";
import { usePolling } from "../hooks/usePolling";
import { useAuth } from "../state/AuthContext";
import { useToast } from "../state/ToastContext";
import {
  Alert,
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Modal,
  PageHeader,
  Pagination,
  Select,
  Spinner,
  StatCard,
  TextInput,
  Textarea,
} from "../components/ui";
import { DataTable } from "../components/DataTable";

interface ClientSummary {
  known: number;
  active: number;
  blocked: number;
  identity_note: string;
}

interface FilterState {
  search: string;
  prefixId: string;
  blocked: string;
  active: string;
  sort: string;
  order: string;
}

const DEFAULT_FILTERS: FilterState = {
  search: "",
  prefixId: "",
  blocked: "",
  active: "",
  sort: "last_seen_at",
  order: "desc",
};

export default function Clients() {
  const toast = useToast();
  const { can } = useAuth();
  const canWrite = can("clients.write");
  const canExport = can("sessions.export");

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [searchInput, setSearchInput] = useState("");

  const clients = usePolling<Paginated<Client> & { filters: Record<string, unknown> }>(
    "/clients",
    {
      page,
      page_size: pageSize,
      search: filters.search || undefined,
      prefix_id: filters.prefixId || undefined,
      blocked: filters.blocked || undefined,
      active: filters.active || undefined,
      sort: filters.sort,
      order: filters.order,
    },
    { interval: 5000 },
  );
  const summary = usePolling<ClientSummary>("/clients/summary", undefined, { interval: 10000 });
  const prefixes = usePolling<{ items: IPv6Prefix[] }>("/prefixes", undefined, { interval: 30000 });

  const [editing, setEditing] = useState<Client | null>(null);
  const [form, setForm] = useState({ hostname: "", notes: "", is_blocked: false });
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);

  const applyFilters = () => {
    setPage(1);
    setFilters((current) => ({ ...current, search: searchInput.trim() }));
  };

  const resetFilters = () => {
    setSearchInput("");
    setFilters(DEFAULT_FILTERS);
    setPage(1);
  };

  const openEdit = (client: Client) => {
    setEditing(client);
    setForm({
      hostname: client.hostname ?? "",
      notes: client.notes ?? "",
      is_blocked: client.is_blocked,
    });
  };

  const save = async () => {
    if (!editing) return;
    setBusy(true);
    try {
      await api.patch<Client>(`/clients/${editing.id}`, {
        hostname: form.hostname.trim() || null,
        notes: form.notes.trim() || null,
        is_blocked: form.is_blocked,
      });
      toast.success("Client updated", editing.address);
      setEditing(null);
      await clients.refresh();
      await summary.refresh();
    } catch (error) {
      toast.error("Could not update client", errorText(error));
    } finally {
      setBusy(false);
    }
  };

  const toggleBlock = async (client: Client) => {
    try {
      await api.post(`/clients/${client.id}/block`, undefined, { blocked: !client.is_blocked });
      toast.success(
        client.is_blocked ? "Client unblocked" : "Client blocked",
        `${client.address} is now ${client.is_blocked ? "allowed" : "blocked"} for new NAT64 sessions.`,
      );
      await clients.refresh();
      await summary.refresh();
    } catch (error) {
      toast.error("Could not change block state", errorText(error));
    }
  };

  const exportCsv = async () => {
    setExporting(true);
    try {
      await downloadFile("/clients/export.csv", "nat64-ipv6-clients.csv", {
        search: filters.search || undefined,
        blocked: filters.blocked || undefined,
      });
      toast.success("CSV exported", "The browser download contains the filtered client list.");
    } catch (error) {
      toast.error("Export failed", errorText(error));
    } finally {
      setExporting(false);
    }
  };

  const rows = clients.data?.items ?? [];
  const total = clients.data?.total ?? 0;
  const pages = clients.data?.pages ?? 0;

  return (
    <div className="space-y-6">
      <PageHeader
        title="IPv6 Clients"
        breadcrumb="NAT64"
        description="Observed IPv6 client addresses, their active session counts, and operator metadata. Client identity is the observed address, not a durable subscriber identity."
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => void clients.refresh()} loading={clients.loading}>
              <RefreshCw className="h-4 w-4" /> Refresh
            </Button>
            {canExport ? (
              <Button variant="outline" size="sm" onClick={() => void exportCsv()} loading={exporting}>
                <Download className="h-4 w-4" /> Export CSV
              </Button>
            ) : null}
          </>
        }
      />

      {clients.error ? (
        <Alert tone="error" title="Client list unavailable">
          {clients.error}
        </Alert>
      ) : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="Known addresses" value={formatNumber(summary.data?.known)} hint="Observed IPv6 addresses" icon={<Users className="h-5 w-5" />} />
        <StatCard label="Active now" value={formatNumber(summary.data?.active)} hint="At least one active NAT64 session" icon={<CheckCircle2 className="h-5 w-5" />} tone="green" />
        <StatCard label="Blocked" value={formatNumber(summary.data?.blocked)} hint="Excluded from new session creation" icon={<Ban className="h-5 w-5" />} tone={(summary.data?.blocked ?? 0) > 0 ? "red" : "slate"} />
      </div>

      <Alert tone="info">{summary.data?.identity_note ?? "Counts are observed IPv6 addresses, not unique subscribers."}</Alert>

      <Card>
        <div className="border-b border-slate-200 p-4 dark:border-slate-800">
          <form
            className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-6"
            onSubmit={(event) => {
              event.preventDefault();
              applyFilters();
            }}
          >
            <Field label="Search" className="lg:col-span-2">
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                <TextInput
                  className="pl-9"
                  value={searchInput}
                  onChange={(event) => setSearchInput(event.target.value)}
                  placeholder="Address, hostname, MAC or notes"
                />
              </div>
            </Field>
            <Field label="Prefix">
              <Select value={filters.prefixId} onChange={(e) => { setPage(1); setFilters({ ...filters, prefixId: e.target.value }); }}>
                <option value="">All prefixes</option>
                {(prefixes.data?.items ?? []).map((prefix) => (
                  <option key={prefix.id} value={prefix.id}>
                    {prefix.prefix}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Blocked">
              <Select value={filters.blocked} onChange={(e) => { setPage(1); setFilters({ ...filters, blocked: e.target.value }); }}>
                <option value="">All</option>
                <option value="true">Blocked</option>
                <option value="false">Allowed</option>
              </Select>
            </Field>
            <Field label="Session state">
              <Select value={filters.active} onChange={(e) => { setPage(1); setFilters({ ...filters, active: e.target.value }); }}>
                <option value="">All</option>
                <option value="true">Active</option>
                <option value="false">Idle</option>
              </Select>
            </Field>
            <Field label="Sort">
              <Select value={`${filters.sort}:${filters.order}`} onChange={(e) => {
                const [sort, order] = e.target.value.split(":");
                setFilters({ ...filters, sort, order });
                setPage(1);
              }}>
                <option value="last_seen_at:desc">Last seen (newest)</option>
                <option value="last_seen_at:asc">Last seen (oldest)</option>
                <option value="active_session_count:desc">Sessions (most)</option>
                <option value="address:asc">Address (A→Z)</option>
                <option value="hostname:asc">Hostname (A→Z)</option>
                <option value="first_seen_at:desc">First seen (newest)</option>
              </Select>
            </Field>
            <div className="flex items-end gap-2 lg:col-span-6">
              <Button type="submit" variant="primary" size="sm">
                <Search className="h-4 w-4" /> Apply filters
              </Button>
              <Button type="button" variant="ghost" size="sm" onClick={resetFilters}>
                Reset
              </Button>
              <div className="ml-auto flex items-center gap-2">
                <span className="text-xs text-slate-500 dark:text-slate-400">Rows per page</span>
                <Select
                  className="w-20"
                  value={String(pageSize)}
                  onChange={(event) => {
                    setPageSize(Number(event.target.value));
                    setPage(1);
                  }}
                >
                  {[25, 50, 100, 200].map((size) => (
                    <option key={size} value={size}>
                      {size}
                    </option>
                  ))}
                </Select>
              </div>
            </div>
          </form>
        </div>

        {clients.loading && !clients.data ? <Spinner label="Loading clients" /> : null}
        {!clients.loading && rows.length === 0 ? (
          <EmptyState
            icon={<Users className="h-6 w-6" />}
            title="No clients match these filters"
            description={
              total === 0
                ? "Clients appear after the collector observes active NAT64 sessions. On a server without the host helper, this list stays empty."
                : "Adjust the filters or reset the search."
            }
          />
        ) : null}

        {rows.length > 0 ? (
          <>
            <DataTable
              rows={rows}
              rowKey={(row) => row.id}
              columns={[
                {
                  key: "address",
                  header: "IPv6 address",
                  render: (row) => (
                    <div>
                      <code className="text-xs font-semibold">{row.address}</code>
                      <p className="text-[11px] text-slate-400">{row.prefix ?? "No prefix"}</p>
                    </div>
                  ),
                },
                { key: "hostname", header: "Hostname", render: (row) => row.hostname ?? <span className="text-slate-400">—</span> },
                {
                  key: "mac",
                  header: "MAC",
                  render: (row) => <code className="text-xs">{row.mac_address ?? "—"}</code>,
                },
                {
                  key: "iface",
                  header: "Interface / VLAN",
                  render: (row) => (
                    <span className="text-xs">
                      {row.interface ?? "—"}
                      {row.vlan_id ? ` · VLAN ${row.vlan_id}` : ""}
                    </span>
                  ),
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
                  key: "blocked",
                  header: "Blocked",
                  render: (row) =>
                    row.is_blocked ? (
                      <Badge tone="red" dot>
                        Blocked
                      </Badge>
                    ) : (
                      <Badge tone="green" dot>
                        Allowed
                      </Badge>
                    ),
                },
                {
                  key: "last",
                  header: "Last seen",
                  render: (row) => (
                    <span className="text-xs text-slate-500 dark:text-slate-400">{formatRelative(row.last_seen_at)}</span>
                  ),
                },
                {
                  key: "notes",
                  header: "Notes",
                  render: (row) => (
                    <span className="line-clamp-2 max-w-[16rem] text-xs text-slate-500 dark:text-slate-400">
                      {row.notes ?? "—"}
                    </span>
                  ),
                },
                {
                  key: "actions",
                  header: "",
                  headerClassName: "text-right",
                  className: "text-right",
                  render: (row) =>
                    canWrite ? (
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="icon" onClick={() => openEdit(row)} aria-label="Edit client">
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => void toggleBlock(row)}
                          aria-label={row.is_blocked ? "Unblock client" : "Block client"}
                          title={row.is_blocked ? "Unblock" : "Block"}
                        >
                          {row.is_blocked ? (
                            <ShieldCheck className="h-4 w-4 text-emerald-500" />
                          ) : (
                            <Ban className="h-4 w-4 text-rose-500" />
                          )}
                        </Button>
                      </div>
                    ) : null,
                },
              ]}
            />
            <Pagination page={clients.data?.page ?? page} pages={pages} total={total} pageSize={pageSize} onPage={setPage} />
          </>
        ) : null}
      </Card>

      <Alert tone="warning" title="Identity and blocking limits">
        Blocking a client stops new session creation in this manager's classification and can be used for
        operator workflows, but it does not by itself filter packets. Enforce blocking with the managed
        firewall or Jool policy after review. IPv6 privacy extensions can make one subscriber appear as
        several addresses.
      </Alert>

      <Modal
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={`Edit client ${editing?.address ?? ""}`}
        description="Metadata is stored in the control-plane database only. It is not written to the host."
        footer={
          <>
            <Button variant="outline" onClick={() => setEditing(null)} disabled={busy}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void save()} loading={busy}>
              Save client
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
            <p>
              Prefix: {editing?.prefix ?? "—"} · Interface: {editing?.interface ?? "—"} · First seen:{" "}
              {formatRelative(editing?.first_seen_at)}
            </p>
            <p className="mt-1">{editing?.identity_note}</p>
          </div>
          <Field label="Hostname">
            <TextInput value={form.hostname} onChange={(e) => setForm({ ...form, hostname: e.target.value })} maxLength={255} />
          </Field>
          <Field label="Operator notes">
            <Textarea value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} maxLength={4000} />
          </Field>
          <label className="flex items-start gap-3 rounded-lg border border-slate-200 p-3 dark:border-slate-800">
            <input
              type="checkbox"
              className="mt-1 h-4 w-4 rounded border-slate-300 text-brand-600"
              checked={form.is_blocked}
              onChange={(e) => setForm({ ...form, is_blocked: e.target.checked })}
            />
            <span className="text-sm">
              <span className="font-medium text-slate-800 dark:text-slate-200">Block this client</span>
              <span className="mt-0.5 block text-xs text-slate-500 dark:text-slate-400">
                Requires corresponding firewall policy to have a packet-level effect.
              </span>
            </span>
          </label>
          <div className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
            <span className={`inline-block h-1.5 w-1.5 rounded-full ${editing?.is_blocked ? "bg-rose-500" : "bg-emerald-500"}`} />
            Current state: {editing?.is_blocked ? "Blocked" : "Allowed"}
          </div>
        </div>
      </Modal>
    </div>
  );
}
