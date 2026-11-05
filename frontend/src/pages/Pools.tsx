import { useState } from "react";
import {
  Boxes,
  Database,
  Gauge,
  Layers,
  Pencil,
  Plus,
  RefreshCw,
  ServerCog,
  Trash2,
  Upload,
} from "lucide-react";
import { api, errorText } from "../lib/api";
import {
  formatNumber,
  formatPercent,
  formatRelative,
  titleCase,
} from "../lib/format";
import type { IPv4Address, IPv4Pool, IPv6Prefix, JoolInstance, PoolSummary } from "../lib/types";
import { usePolling } from "../hooks/usePolling";
import { useAuth } from "../state/AuthContext";
import { useToast } from "../state/ToastContext";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardHeader,
  ConfirmDialog,
  EmptyState,
  Field,
  Modal,
  PageHeader,
  ProgressBar,
  Select,
  Spinner,
  StatCard,
  Tabs,
  TextInput,
  Textarea,
  Toggle,
} from "../components/ui";
import { DataTable } from "../components/DataTable";
import { ChangeDialog, useChangeWorkflow } from "../components/ChangeWorkflow";

interface PoolFormState {
  name: string;
  description: string;
  enabled: boolean;
  jool_instance_id: string;
  color: string;
}

interface AddressFormState {
  address: string;
  port_min: string;
  port_max: string;
  label: string;
  enabled: boolean;
}

interface PrefixFormState {
  prefix: string;
  name: string;
  interface: string;
  vlan_id: string;
  is_client_prefix: boolean;
  enabled: boolean;
  description: string;
}

const EMPTY_POOL: PoolFormState = {
  name: "",
  description: "",
  enabled: true,
  jool_instance_id: "",
  color: "",
};

function emptyAddress(defaults: [number, number]): AddressFormState {
  return {
    address: "",
    port_min: String(defaults[0]),
    port_max: String(defaults[1]),
    label: "",
    enabled: true,
  };
}

const EMPTY_PREFIX: PrefixFormState = {
  prefix: "",
  name: "",
  interface: "",
  vlan_id: "",
  is_client_prefix: true,
  enabled: true,
  description: "",
};

export default function Pools() {
  const toast = useToast();
  const { can } = useAuth();
  const canWrite = can("pools.write");
  const canRequest = can("changes.request");
  const canPrefix = can("prefixes.write");
  const workflow = useChangeWorkflow();

  const [tab, setTab] = useState<"pools" | "prefixes">("pools");
  const [expanded, setExpanded] = useState<number | null>(null);

  const pools = usePolling<{ items: IPv4Pool[]; defaults: PoolSummary }>("/pools");
  const prefixes = usePolling<{ items: IPv6Prefix[] }>("/prefixes");
  const instances = usePolling<{ items: JoolInstance[] }>("/jool/instances", undefined, { interval: 30000 });

  const summary = pools.data?.defaults;
  const poolItems = pools.data?.items ?? [];
  const defaultRange = summary?.default_port_range ?? [61001, 65535];

  const [poolModal, setPoolModal] = useState(false);
  const [editingPool, setEditingPool] = useState<IPv4Pool | null>(null);
  const [poolForm, setPoolForm] = useState<PoolFormState>(EMPTY_POOL);
  const [poolBusy, setPoolBusy] = useState(false);

  const [deletePool, setDeletePool] = useState<IPv4Pool | null>(null);
  const [deletePoolForce, setDeletePoolForce] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const [addressModal, setAddressModal] = useState<{ pool: IPv4Pool; address: IPv4Address | null } | null>(null);
  const [addressForm, setAddressForm] = useState<AddressFormState>(emptyAddress([61001, 65535]));
  const [addressBusy, setAddressBusy] = useState(false);
  const [deleteAddress, setDeleteAddress] = useState<IPv4Address | null>(null);

  const [importModal, setImportModal] = useState<IPv4Pool | null>(null);
  const [importText, setImportText] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [importResult, setImportResult] = useState<{ parsed: number; created: string[]; skipped_existing: string[] } | null>(null);

  const [prefixModal, setPrefixModal] = useState(false);
  const [editingPrefix, setEditingPrefix] = useState<IPv6Prefix | null>(null);
  const [prefixForm, setPrefixForm] = useState<PrefixFormState>(EMPTY_PREFIX);
  const [prefixBusy, setPrefixBusy] = useState(false);
  const [deletePrefix, setDeletePrefix] = useState<IPv6Prefix | null>(null);

  const instanceOptions = instances.data?.items ?? [];

  const refreshAll = () => {
    void pools.refresh();
    void prefixes.refresh();
  };

  const openCreatePool = () => {
    setEditingPool(null);
    setPoolForm(EMPTY_POOL);
    setPoolModal(true);
  };

  const openEditPool = (pool: IPv4Pool) => {
    setEditingPool(pool);
    setPoolForm({
      name: pool.name,
      description: pool.description ?? "",
      enabled: pool.enabled,
      jool_instance_id: pool.jool_instance_id ? String(pool.jool_instance_id) : "",
      color: pool.color ?? "",
    });
    setPoolModal(true);
  };

  const savePool = async () => {
    setPoolBusy(true);
    try {
      const payload = {
        name: poolForm.name.trim(),
        description: poolForm.description.trim() || null,
        enabled: poolForm.enabled,
        jool_instance_id: poolForm.jool_instance_id ? Number(poolForm.jool_instance_id) : null,
        color: poolForm.color.trim() || null,
      };
      if (!payload.name) throw new Error("Pool name is required");
      if (editingPool) {
        await api.patch(`/pools/${editingPool.id}`, payload);
        toast.success("Pool updated", `${payload.name} was saved.`);
      } else {
        await api.post("/pools", payload);
        toast.success("Pool created", `${payload.name} was created.`);
      }
      setPoolModal(false);
      await pools.refresh();
    } catch (error) {
      toast.error("Could not save pool", errorText(error));
    } finally {
      setPoolBusy(false);
    }
  };

  const confirmDeletePool = async () => {
    if (!deletePool) return;
    setDeleteBusy(true);
    try {
      await api.delete(`/pools/${deletePool.id}`, { force: deletePoolForce });
      toast.success("Pool deleted", deletePool.name);
      setDeletePool(null);
      setDeletePoolForce(false);
      await pools.refresh();
    } catch (error) {
      toast.error("Could not delete pool", errorText(error));
    } finally {
      setDeleteBusy(false);
    }
  };

  const openAddAddress = (pool: IPv4Pool) => {
    setAddressModal({ pool, address: null });
    setAddressForm(emptyAddress(defaultRange));
  };

  const openEditAddress = (pool: IPv4Pool, address: IPv4Address) => {
    setAddressModal({ pool, address });
    setAddressForm({
      address: address.address,
      port_min: String(address.port_min),
      port_max: String(address.port_max),
      label: address.label ?? "",
      enabled: address.enabled,
    });
  };

  const saveAddress = async () => {
    if (!addressModal) return;
    setAddressBusy(true);
    try {
      const payload = {
        address: addressForm.address.trim(),
        port_min: Number(addressForm.port_min),
        port_max: Number(addressForm.port_max),
        enabled: addressForm.enabled,
        label: addressForm.label.trim() || null,
      };
      if (addressModal.address) {
        await api.patch(`/addresses/${addressModal.address.id}`, payload);
        toast.success("Address updated", `${addressModal.address.address} was updated in the database.`);
      } else {
        await api.post(`/pools/${addressModal.pool.id}/addresses`, payload);
        toast.success("Address added", `${payload.address} is pending pool4 synchronisation.`);
      }
      setAddressModal(null);
      await pools.refresh();
    } catch (error) {
      toast.error("Could not save address", errorText(error));
    } finally {
      setAddressBusy(false);
    }
  };

  const confirmDeleteAddress = async () => {
    if (!deleteAddress) return;
    setDeleteBusy(true);
    try {
      await api.delete(`/addresses/${deleteAddress.id}`);
      toast.success("Address deleted", deleteAddress.address);
      setDeleteAddress(null);
      await pools.refresh();
    } catch (error) {
      toast.error("Could not delete address", errorText(error));
    } finally {
      setDeleteBusy(false);
    }
  };

  const runImport = async () => {
    if (!importModal) return;
    setImportBusy(true);
    setImportResult(null);
    try {
      const result = await api.post<{ parsed: number; created: string[]; skipped_existing: string[] }>(
        `/pools/${importModal.id}/addresses/import`,
        {
          text: importText,
          port_min: defaultRange[0],
          port_max: defaultRange[1],
          enabled: true,
        },
      );
      setImportResult(result);
      setImportText("");
      await pools.refresh();
      toast.success("Address import complete", `${result.created.length} added, ${result.skipped_existing.length} skipped.`);
    } catch (error) {
      toast.error("Import failed", errorText(error));
    } finally {
      setImportBusy(false);
    }
  };

  const syncPool4 = async (pool: IPv4Pool) => {
    if (!pool.jool_instance_id) {
      toast.warning("No Jool instance", "Assign a Jool instance to this pool before synchronising pool4.");
      return;
    }
    try {
      await workflow.submit(`/jool/instances/${pool.jool_instance_id}/sync-pool4`, {});
    } catch (error) {
      toast.error("Could not create change request", errorText(error));
    }
  };

  const openCreatePrefix = () => {
    setEditingPrefix(null);
    setPrefixForm(EMPTY_PREFIX);
    setPrefixModal(true);
  };

  const openEditPrefix = (prefix: IPv6Prefix) => {
    setEditingPrefix(prefix);
    setPrefixForm({
      prefix: prefix.prefix,
      name: prefix.name ?? "",
      interface: prefix.interface ?? "",
      vlan_id: prefix.vlan_id ? String(prefix.vlan_id) : "",
      is_client_prefix: prefix.is_client_prefix,
      enabled: prefix.enabled,
      description: prefix.description ?? "",
    });
    setPrefixModal(true);
  };

  const savePrefix = async () => {
    setPrefixBusy(true);
    try {
      const payload = {
        prefix: prefixForm.prefix.trim(),
        name: prefixForm.name.trim() || null,
        interface: prefixForm.interface.trim() || null,
        vlan_id: prefixForm.vlan_id ? Number(prefixForm.vlan_id) : null,
        is_client_prefix: prefixForm.is_client_prefix,
        enabled: prefixForm.enabled,
        description: prefixForm.description.trim() || null,
      };
      if (!payload.prefix) throw new Error("IPv6 prefix is required");
      if (editingPrefix) {
        await api.patch(`/prefixes/${editingPrefix.id}`, payload);
        toast.success("Prefix updated", payload.prefix);
      } else {
        await api.post("/prefixes", payload);
        toast.success("Prefix created", payload.prefix);
      }
      setPrefixModal(false);
      await prefixes.refresh();
    } catch (error) {
      toast.error("Could not save prefix", errorText(error));
    } finally {
      setPrefixBusy(false);
    }
  };

  const confirmDeletePrefix = async () => {
    if (!deletePrefix) return;
    setDeleteBusy(true);
    try {
      await api.delete(`/prefixes/${deletePrefix.id}`);
      toast.success("Prefix deleted", deletePrefix.prefix);
      setDeletePrefix(null);
      await prefixes.refresh();
    } catch (error) {
      toast.error("Could not delete prefix", errorText(error));
    } finally {
      setDeleteBusy(false);
    }
  };

  const error = pools.error ?? prefixes.error;

  return (
    <div className="space-y-6">
      <PageHeader
        title="IPv4 Pool"
        breadcrumb="NAT64"
        description="Public IPv4 transport addresses used by Jool pool4. Allocation is dynamic per connection: addresses and ports are shared by many subscribers, never leased exclusively."
        actions={
          <>
            <Button variant="outline" size="sm" onClick={refreshAll} loading={pools.loading || prefixes.loading}>
              <RefreshCw className="h-4 w-4" /> Refresh
            </Button>
            {canWrite ? (
              <Button variant="primary" size="sm" onClick={openCreatePool}>
                <Plus className="h-4 w-4" /> New pool
              </Button>
            ) : null}
          </>
        }
      />

      {error ? (
        <Alert tone="error" title="Pool data unavailable">
          {error}
        </Alert>
      ) : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Pools"
          value={formatNumber(summary?.pool_count)}
          hint={`${formatNumber(summary?.address_count)} addresses · ${formatNumber(
            summary?.enabled_address_count,
          )} enabled`}
          icon={<Database className="h-5 w-5" />}
          tone="blue"
        />
        <StatCard
          label="Transport capacity"
          value={formatNumber(summary?.transport_capacity)}
          hint="TCP + UDP + ICMP port spaces across enabled addresses"
          icon={<Layers className="h-5 w-5" />}
          tone="violet"
        />
        <StatCard
          label="Addresses with active sessions"
          value={formatNumber(summary?.active_address_count)}
          hint="Observed translated addresses in active sessions"
          icon={<Boxes className="h-5 w-5" />}
          tone="blue"
        />
        <StatCard
          label="Observed utilisation"
          value={formatPercent(summary?.utilization_percent)}
          hint={`${formatNumber(summary?.observed_ports_used)} observed ports used`}
          icon={<Gauge className="h-5 w-5" />}
          tone={(summary?.utilization_percent ?? 0) >= 85 ? "amber" : "green"}
          footer={<div className="mt-3"><ProgressBar value={summary?.utilization_percent ?? 0} tone={(summary?.utilization_percent ?? 0) >= 85 ? "amber" : "green"} /></div>}
        />
      </div>

      <Alert tone="info" title="Dynamic per-connection allocation">
        Jool maps each new NAT64 connection to an available public IPv4 address and port from pool4. A
        subscriber can have many simultaneous connections across one or several public addresses, and
        the same public address is concurrently shared with other subscribers. Pool utilisation here is
        observed from active session transports, not an exact BIB-per-address count.
      </Alert>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs
          value={tab}
          onChange={setTab}
          tabs={[
            { id: "pools", label: "IPv4 pools", badge: <Badge tone="slate">{poolItems.length}</Badge> },
            {
              id: "prefixes",
              label: "IPv6 prefixes",
              badge: <Badge tone="slate">{prefixes.data?.items.length ?? 0}</Badge>,
            },
          ]}
        />
        {tab === "prefixes" && canPrefix ? (
          <Button variant="primary" size="sm" onClick={openCreatePrefix}>
            <Plus className="h-4 w-4" /> New prefix
          </Button>
        ) : null}
      </div>

      {tab === "pools" ? (
        <>
          {pools.loading && !pools.data ? <Spinner label="Loading pools" /> : null}
          {!pools.loading && poolItems.length === 0 ? (
            <Card>
              <EmptyState
                icon={<Database className="h-6 w-6" />}
                title="No IPv4 pools yet"
                description="Create a pool and add at least one public IPv4 address with a port range before synchronising pool4 to Jool."
                action={
                  canWrite ? (
                    <Button variant="primary" onClick={openCreatePool}>
                      <Plus className="h-4 w-4" /> Create pool
                    </Button>
                  ) : undefined
                }
              />
            </Card>
          ) : null}

          <div className="space-y-4">
            {poolItems.map((pool) => {
              const open = expanded === pool.id;
              return (
                <Card key={pool.id}>
                  <CardHeader
                    title={
                      <span className="flex items-center gap-2">
                        <span
                          className="inline-block h-2.5 w-2.5 rounded-full"
                          style={{ backgroundColor: pool.color || "#2a9bff" }}
                        />
                        {pool.name}
                        {!pool.enabled ? <Badge tone="slate">Disabled</Badge> : null}
                      </span>
                    }
                    description={
                      <>
                        {pool.description || "No description"} ·{" "}
                        {pool.jool_instance_name ? `Jool: ${pool.jool_instance_name}` : "No Jool instance assigned"} ·{" "}
                        {formatNumber(pool.address_count)} addresses
                      </>
                    }
                    actions={
                      <>
                        <Button variant="ghost" size="sm" onClick={() => setExpanded(open ? null : pool.id)}>
                          {open ? "Hide addresses" : "Addresses"}
                        </Button>
                        {canRequest ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => void syncPool4(pool)}
                            title="Create a pool4 synchronisation change request"
                          >
                            <ServerCog className="h-4 w-4" /> Sync pool4
                          </Button>
                        ) : null}
                        {canWrite ? (
                          <>
                            <Button variant="ghost" size="icon" onClick={() => openEditPool(pool)} aria-label="Edit pool">
                              <Pencil className="h-4 w-4" />
                            </Button>
                            <Button variant="ghost" size="icon" onClick={() => { setDeletePool(pool); setDeletePoolForce(false); }} aria-label="Delete pool">
                              <Trash2 className="h-4 w-4 text-rose-500" />
                            </Button>
                          </>
                        ) : null}
                      </>
                    }
                  />
                  <div className="grid grid-cols-1 gap-4 border-b border-slate-100 p-4 text-sm dark:border-slate-800 sm:grid-cols-3">
                    <div>
                      <p className="text-xs uppercase tracking-wide text-slate-400">Pool usage</p>
                      <p className="mt-1 font-semibold">{formatPercent(pool.usage_percent)}</p>
                      <ProgressBar
                        value={pool.usage_percent}
                        tone={pool.usage_percent >= 90 ? "red" : pool.usage_percent >= 70 ? "amber" : "green"}
                      />
                    </div>
                    <div>
                      <p className="text-xs uppercase tracking-wide text-slate-400">Active sessions</p>
                      <p className="mt-1 font-semibold">{formatNumber(pool.sessions_total)}</p>
                      <p className="text-xs text-slate-500 dark:text-slate-400">
                        {formatNumber(pool.total_used)} observed ports of {formatNumber(pool.total_capacity)}
                      </p>
                    </div>
                    <div className="flex items-end justify-end">
                      {canWrite ? (
                        <Button size="sm" onClick={() => openAddAddress(pool)}>
                          <Plus className="h-4 w-4" /> Add address
                        </Button>
                      ) : null}
                    </div>
                  </div>

                  {open ? (
                    <div>
                      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 text-xs text-slate-500 dark:text-slate-400">
                        <span>
                          Port ranges apply per protocol. Jool pool4 requires the address to be free of owned
                          ports outside the managed range.
                        </span>
                        {canWrite ? (
                          <Button variant="ghost" size="sm" onClick={() => { setImportModal(pool); setImportResult(null); }}>
                            <Upload className="h-4 w-4" /> Import addresses
                          </Button>
                        ) : null}
                      </div>
                      <DataTable
                        rows={pool.addresses}
                        rowKey={(row) => row.id}
                        empty="No addresses in this pool."
                        columns={[
                          {
                            key: "address",
                            header: "Public IPv4",
                            render: (row) => (
                              <div>
                                <code className="text-xs font-semibold">{row.address}</code>
                                {row.label ? (
                                  <p className="text-[11px] text-slate-400">{row.label}</p>
                                ) : null}
                              </div>
                            ),
                          },
                          {
                            key: "ports",
                            header: "Port range",
                            render: (row) => (
                              <span className="text-xs">
                                {formatNumber(row.port_min)}–{formatNumber(row.port_max)}
                                <span className="ml-1 text-slate-400">({formatNumber(row.port_capacity)}/proto)</span>
                              </span>
                            ),
                          },
                          {
                            key: "usage",
                            header: "Observed usage",
                            render: (row) => (
                              <div className="min-w-[110px]">
                                <div className="flex justify-between text-xs">
                                  <span>{formatPercent(row.usage_percent)}</span>
                                  <span className="text-slate-400">{formatNumber(row.ports_used)}</span>
                                </div>
                                <ProgressBar
                                  value={row.usage_percent}
                                  tone={row.usage_percent >= 90 ? "red" : row.usage_percent >= 70 ? "amber" : "green"}
                                />
                              </div>
                            ),
                          },
                          {
                            key: "sessions",
                            header: "Sessions",
                            render: (row) => (
                              <span className="text-xs">
                                TCP {formatNumber(row.tcp)} · UDP {formatNumber(row.udp)} · ICMP {formatNumber(row.icmp)}
                              </span>
                            ),
                          },
                          {
                            key: "status",
                            header: "Status",
                            render: (row) => (
                              <Badge status={row.enabled ? row.status : "disabled"} dot>
                                {row.enabled ? titleCase(row.status) : "Disabled"}
                              </Badge>
                            ),
                          },
                          {
                            key: "seen",
                            header: "Last session",
                            render: (row) => (
                              <span className="text-xs text-slate-500 dark:text-slate-400">
                                {formatRelative(row.last_session_at)}
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
                                  <Button variant="ghost" size="icon" onClick={() => openEditAddress(pool, row)} aria-label="Edit address">
                                    <Pencil className="h-4 w-4" />
                                  </Button>
                                  <Button variant="ghost" size="icon" onClick={() => setDeleteAddress(row)} aria-label="Delete address">
                                    <Trash2 className="h-4 w-4 text-rose-500" />
                                  </Button>
                                </div>
                              ) : null,
                          },
                        ]}
                      />
                    </div>
                  ) : null}
                </Card>
              );
            })}
          </div>
        </>
      ) : (
        <Card>
          <CardHeader
            title="IPv6 client prefixes"
            description="Prefixes observed on the LAN. Used to classify clients; they are not written to the host from this page."
          />
          <DataTable
            rows={prefixes.data?.items ?? []}
            rowKey={(row) => row.id}
            loading={prefixes.loading && !prefixes.data}
            empty="No IPv6 prefixes recorded."
            columns={[
              { key: "prefix", header: "Prefix", render: (row) => <code className="text-xs">{row.prefix}</code> },
              { key: "name", header: "Name", render: (row) => row.name ?? "—" },
              { key: "interface", header: "Interface", render: (row) => row.interface ?? "—" },
              { key: "vlan", header: "VLAN", render: (row) => row.vlan_id ?? "—" },
              {
                key: "client",
                header: "Client prefix",
                render: (row) => (row.is_client_prefix ? <Badge tone="blue">Yes</Badge> : <Badge tone="slate">No</Badge>),
              },
              {
                key: "state",
                header: "State",
                render: (row) => <Badge status={row.enabled ? "enabled" : "disabled"} dot>{row.enabled ? "Enabled" : "Disabled"}</Badge>,
              },
              {
                key: "updated",
                header: "Updated",
                render: (row) => <span className="text-xs text-slate-500">{formatRelative(row.updated_at)}</span>,
              },
              {
                key: "actions",
                header: "",
                headerClassName: "text-right",
                className: "text-right",
                render: (row) =>
                  canPrefix ? (
                    <div className="flex justify-end gap-1">
                      <Button variant="ghost" size="icon" onClick={() => openEditPrefix(row)} aria-label="Edit prefix">
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button variant="ghost" size="icon" onClick={() => setDeletePrefix(row)} aria-label="Delete prefix">
                        <Trash2 className="h-4 w-4 text-rose-500" />
                      </Button>
                    </div>
                  ) : null,
              },
            ]}
          />
        </Card>
      )}

      <Modal
        open={poolModal}
        onClose={() => setPoolModal(false)}
        title={editingPool ? `Edit pool: ${editingPool.name}` : "Create IPv4 pool"}
        description="A pool groups public IPv4 addresses that Jool may use for dynamic per-connection translation."
        footer={
          <>
            <Button variant="outline" onClick={() => setPoolModal(false)} disabled={poolBusy}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void savePool()} loading={poolBusy}>
              {editingPool ? "Save changes" : "Create pool"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Name" required>
            <TextInput value={poolForm.name} onChange={(e) => setPoolForm({ ...poolForm, name: e.target.value })} maxLength={128} />
          </Field>
          <Field label="Description">
            <Textarea value={poolForm.description} onChange={(e) => setPoolForm({ ...poolForm, description: e.target.value })} className="min-h-[70px]" />
          </Field>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Jool instance" hint="Required before pool4 can be synchronised.">
              <Select value={poolForm.jool_instance_id} onChange={(e) => setPoolForm({ ...poolForm, jool_instance_id: e.target.value })}>
                <option value="">Unassigned</option>
                {instanceOptions.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} ({item.pool6})
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Colour" hint="Hex colour used in the UI only.">
              <TextInput value={poolForm.color} onChange={(e) => setPoolForm({ ...poolForm, color: e.target.value })} placeholder="#2a9bff" />
            </Field>
          </div>
          <Toggle
            checked={poolForm.enabled}
            onChange={(enabled) => setPoolForm({ ...poolForm, enabled })}
            label="Pool enabled"
            description="Disabled pools remain in the database but are excluded from pool4 synchronisation."
          />
        </div>
      </Modal>

      <Modal
        open={addressModal !== null}
        onClose={() => setAddressModal(null)}
        title={addressModal?.address ? `Edit ${addressModal.address.address}` : `Add address to ${addressModal?.pool.name ?? "pool"}`}
        description="Public IPv4 addresses are shared dynamically across connections. Port ranges must not overlap Jool-owned ports."
        footer={
          <>
            <Button variant="outline" onClick={() => setAddressModal(null)} disabled={addressBusy}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void saveAddress()} loading={addressBusy}>
              {addressModal?.address ? "Save address" : "Add address"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Public IPv4 address" required>
            <TextInput
              value={addressForm.address}
              onChange={(e) => setAddressForm({ ...addressForm, address: e.target.value })}
              placeholder="203.0.113.10"
              disabled={Boolean(addressModal?.address)}
            />
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Port minimum" required>
              <TextInput type="number" min={1} max={65535} value={addressForm.port_min} onChange={(e) => setAddressForm({ ...addressForm, port_min: e.target.value })} />
            </Field>
            <Field label="Port maximum" required>
              <TextInput type="number" min={1} max={65535} value={addressForm.port_max} onChange={(e) => setAddressForm({ ...addressForm, port_max: e.target.value })} />
            </Field>
          </div>
          <Field label="Label">
            <TextInput value={addressForm.label} onChange={(e) => setAddressForm({ ...addressForm, label: e.target.value })} placeholder="Uplink A" />
          </Field>
          <Toggle
            checked={addressForm.enabled}
            onChange={(enabled) => setAddressForm({ ...addressForm, enabled })}
            label="Address enabled"
            description="Changing the port range or enable state requires a pool4 synchronisation change."
          />
        </div>
      </Modal>

      <Modal
        open={importModal !== null}
        onClose={() => { setImportModal(null); setImportResult(null); }}
        title={`Import addresses into ${importModal?.name ?? "pool"}`}
        description="Paste one IPv4 address per line or a comma-separated list. Duplicates are skipped."
        footer={
          <>
            <Button variant="outline" onClick={() => { setImportModal(null); setImportResult(null); }} disabled={importBusy}>
              Close
            </Button>
            <Button variant="primary" onClick={() => void runImport()} loading={importBusy} disabled={!importText.trim()}>
              Import
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <textarea
            className="input min-h-[180px] font-mono text-xs"
            value={importText}
            onChange={(e) => setImportText(e.target.value)}
            placeholder={"203.0.113.10\n203.0.113.11\n203.0.113.12"}
          />
          <p className="text-xs text-slate-500">
            Uses the default port range {defaultRange[0]}–{defaultRange[1]} for all imported addresses.
          </p>
          {importResult ? (
            <Alert tone="success" title="Import result">
              {importResult.parsed} parsed · {importResult.created.length} created ·{" "}
              {importResult.skipped_existing.length} skipped existing
              {importResult.skipped_existing.length ? (
                <p className="mt-1 font-mono text-xs">{importResult.skipped_existing.join(", ")}</p>
              ) : null}
            </Alert>
          ) : null}
        </div>
      </Modal>

      <Modal
        open={prefixModal}
        onClose={() => setPrefixModal(false)}
        title={editingPrefix ? `Edit prefix: ${editingPrefix.prefix}` : "Add IPv6 prefix"}
        footer={
          <>
            <Button variant="outline" onClick={() => setPrefixModal(false)} disabled={prefixBusy}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void savePrefix()} loading={prefixBusy}>
              {editingPrefix ? "Save changes" : "Add prefix"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="IPv6 prefix" required hint="Must be /64 or shorter and normalized.">
            <TextInput value={prefixForm.prefix} onChange={(e) => setPrefixForm({ ...prefixForm, prefix: e.target.value })} placeholder="2001:db8:100::/64" />
          </Field>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Name">
              <TextInput value={prefixForm.name} onChange={(e) => setPrefixForm({ ...prefixForm, name: e.target.value })} />
            </Field>
            <Field label="Interface">
              <TextInput value={prefixForm.interface} onChange={(e) => setPrefixForm({ ...prefixForm, interface: e.target.value })} placeholder="lan0" />
            </Field>
            <Field label="VLAN ID">
              <TextInput type="number" min={1} max={4094} value={prefixForm.vlan_id} onChange={(e) => setPrefixForm({ ...prefixForm, vlan_id: e.target.value })} />
            </Field>
          </div>
          <Field label="Description">
            <Textarea value={prefixForm.description} onChange={(e) => setPrefixForm({ ...prefixForm, description: e.target.value })} className="min-h-[70px]" />
          </Field>
          <Toggle
            checked={prefixForm.is_client_prefix}
            onChange={(is_client_prefix) => setPrefixForm({ ...prefixForm, is_client_prefix })}
            label="Client prefix"
            description="Marks this prefix as a subscriber LAN range used for client classification."
          />
          <Toggle
            checked={prefixForm.enabled}
            onChange={(enabled) => setPrefixForm({ ...prefixForm, enabled })}
            label="Enabled"
          />
        </div>
      </Modal>

      <ConfirmDialog
        open={deletePool !== null}
        onClose={() => setDeletePool(null)}
        onConfirm={() => void confirmDeletePool()}
        title={`Delete pool ${deletePool?.name ?? ""}?`}
        description="Database configuration only. Jool pool4 entries are removed only through a pool4 synchronisation change."
        confirmLabel="Delete pool"
        danger
        loading={deleteBusy}
      >
        <div className="space-y-3 text-sm text-slate-600 dark:text-slate-300">
          <p>
            {deletePool?.address_count ?? 0} address(es) will be removed from the database. Active sessions
            can block deletion unless forced.
          </p>
          <Toggle
            checked={deletePoolForce}
            onChange={setDeletePoolForce}
            label="Force deletion"
            description="Delete even when active sessions currently use these addresses."
          />
        </div>
      </ConfirmDialog>

      <ConfirmDialog
        open={deleteAddress !== null}
        onClose={() => setDeleteAddress(null)}
        onConfirm={() => void confirmDeleteAddress()}
        title={`Delete ${deleteAddress?.address ?? "address"}?`}
        description="Addresses in active use must be disabled and synchronised instead of deleted."
        confirmLabel="Delete address"
        danger
        loading={deleteBusy}
      />

      <ConfirmDialog
        open={deletePrefix !== null}
        onClose={() => setDeletePrefix(null)}
        onConfirm={() => void confirmDeletePrefix()}
        title={`Delete ${deletePrefix?.prefix ?? "prefix"}?`}
        confirmLabel="Delete prefix"
        danger
        loading={deleteBusy}
      />

      <ChangeDialog
        workflow={workflow}
        title="pool4 synchronisation change"
        onUpdated={() => {
          void pools.refresh();
        }}
      />
    </div>
  );
}
