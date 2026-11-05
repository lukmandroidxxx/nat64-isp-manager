import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Download,
  GitBranch,
  Globe2,
  Pencil,
  Plus,
  RefreshCw,
  Router,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  Wifi,
} from "lucide-react";
import { api, errorText } from "../lib/api";
import { formatDate, formatRelative, prettyJson, titleCase } from "../lib/format";
import type { ChangeRequest, HaNode, HaSettings } from "../lib/types";
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
  ConfirmDialog,
  EmptyState,
  Field,
  KeyValue,
  Modal,
  PageHeader,
  Select,
  Spinner,
  StatCard,
  Tabs,
  Textarea,
  TextInput,
  Toggle,
} from "../components/ui";
import { DataTable } from "../components/DataTable";
import { ChangeDialog, useChangeWorkflow } from "../components/ChangeWorkflow";

type HaTab = "overview" | "settings" | "nodes" | "preview";

interface HaStatus {
  enabled: boolean;
  role: string;
  node_name: string;
  virtual_ip: string | null;
  mode: string;
  nodes: HaNode[];
  settings: HaSettings;
  continuity: { session_sync_setting: boolean; verified: boolean; note: string };
}

interface HaPreview {
  content: string | null;
  hash: string | null;
  errors?: string[];
  warnings?: string[];
  settings?: HaSettings;
  state?: string;
}

interface HaForm {
  enabled: boolean;
  mode: string;
  virtual_ip: string;
  vrrp_interface: string;
  vrrp_router_id: string;
  priority: string;
  peer_address: string;
  session_sync_enabled: boolean;
  session_sync_multicast: string;
  session_sync_interface: string;
}

interface NodeForm {
  name: string;
  role: string;
  address: string;
  api_url: string;
  enabled: boolean;
  notes: string;
}

interface NodeModalState {
  mode: "create" | "edit";
  node: HaNode | null;
  form: NodeForm;
}

const EMPTY_NODE_FORM: NodeForm = {
  name: "",
  role: "backup",
  address: "",
  api_url: "",
  enabled: true,
  notes: "",
};

function toHaForm(settings: HaSettings): HaForm {
  return {
    enabled: settings.enabled,
    mode: settings.mode,
    virtual_ip: settings.virtual_ip ?? "",
    vrrp_interface: settings.vrrp_interface ?? "",
    vrrp_router_id: settings.vrrp_router_id ? String(settings.vrrp_router_id) : "",
    priority: settings.priority ? String(settings.priority) : "",
    peer_address: settings.peer_address ?? "",
    session_sync_enabled: settings.session_sync_enabled,
    session_sync_multicast: settings.session_sync_multicast ?? "",
    session_sync_interface: settings.session_sync_interface ?? "",
  };
}

function nodeTone(status: string): "green" | "amber" | "red" | "slate" {
  if (status === "online") return "green";
  if (status === "unreachable" || status.startsWith("http_")) return "red";
  if (status === "unconfigured") return "amber";
  return "slate";
}

function downloadJson(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export default function Ha() {
  const toast = useToast();
  const { user, can } = useAuth();
  const workflow = useChangeWorkflow();

  const canWrite = can("settings.write");
  const canRequest = can("changes.request");
  const isSuperuser = Boolean(user?.is_superuser);

  const [tab, setTab] = useState<HaTab>("overview");
  const [form, setForm] = useState<HaForm | null>(null);
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [nodeModal, setNodeModal] = useState<NodeModalState | null>(null);
  const [deleteNode, setDeleteNode] = useState<HaNode | null>(null);
  const initialized = useRef(false);

  const ha = usePolling<HaStatus>("/ha", undefined, { interval: 10000 });
  const settingsQuery = usePolling<HaSettings>("/ha/settings", undefined, { interval: 0 });
  const nodes = usePolling<{ items: HaNode[] }>("/ha/nodes", undefined, { interval: 15000 });
  const preview = usePolling<HaPreview>("/ha/preview", undefined, { interval: 0 });

  useEffect(() => {
    if (!initialized.current && settingsQuery.data) {
      setForm(toHaForm(settingsQuery.data));
      initialized.current = true;
    }
  }, [settingsQuery.data]);

  const nodeRows = nodes.data?.items ?? ha.data?.nodes ?? [];
  const onlineNodes = nodeRows.filter((node) => node.status === "online").length;
  const continuity = ha.data?.continuity;
  const previewData = preview.data;

  const refresh = () => {
    void ha.refresh();
    void nodes.refresh();
    void preview.refresh();
  };

  const saveSettings = async () => {
    if (!form) return;
    if (secret && secret.length < 8) {
      toast.error("VRRP secret is too short", "Use at least eight characters.");
      return;
    }
    setBusy("settings");
    try {
      const result = await api.patch<{ settings: HaSettings; note: string }>("/ha/settings", {
        enabled: form.enabled,
        mode: form.mode,
        virtual_ip: form.virtual_ip.trim() || null,
        vrrp_interface: form.vrrp_interface.trim() || null,
        vrrp_router_id: form.vrrp_router_id ? Number(form.vrrp_router_id) : null,
        priority: form.priority ? Number(form.priority) : null,
        peer_address: form.peer_address.trim() || null,
        session_sync_enabled: form.session_sync_enabled,
        session_sync_multicast: form.session_sync_multicast.trim() || null,
        session_sync_interface: form.session_sync_interface.trim() || null,
        ...(secret ? { auth_secret: secret } : {}),
      });
      setForm(toHaForm(result.settings));
      settingsQuery.setData(result.settings);
      setSecret("");
      toast.success("HA settings saved", result.note);
      void ha.refresh();
      void preview.refresh();
    } catch (error) {
      toast.error("Could not save HA settings", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const openCreateNode = () => {
    setNodeModal({ mode: "create", node: null, form: { ...EMPTY_NODE_FORM } });
  };

  const openEditNode = (node: HaNode) => {
    setNodeModal({
      mode: "edit",
      node,
      form: {
        name: node.name,
        role: node.role,
        address: node.address ?? "",
        api_url: node.api_url ?? "",
        enabled: node.enabled,
        notes: node.notes ?? "",
      },
    });
  };

  const saveNode = async () => {
    if (!nodeModal) return;
    const formValue = nodeModal.form;
    if (!formValue.name.trim()) {
      toast.error("Node name is required", "Give this node a unique name.");
      return;
    }
    setBusy("node");
    try {
      const payload = {
        name: formValue.name.trim(),
        role: formValue.role,
        address: formValue.address.trim() || null,
        api_url: formValue.api_url.trim() || null,
        enabled: formValue.enabled,
        notes: formValue.notes.trim() || null,
      };
      if (nodeModal.mode === "create") {
        await api.post<HaNode>("/ha/nodes", payload);
        toast.success("HA node created", payload.name);
      } else if (nodeModal.node) {
        await api.patch<HaNode>(`/ha/nodes/${nodeModal.node.id}`, payload);
        toast.success("HA node updated", payload.name);
      }
      setNodeModal(null);
      await nodes.refresh();
      void ha.refresh();
    } catch (error) {
      toast.error("Could not save HA node", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const confirmDeleteNode = async () => {
    if (!deleteNode) return;
    setBusy("delete-node");
    try {
      await api.delete(`/ha/nodes/${deleteNode.id}`);
      toast.success("HA node deleted", deleteNode.name);
      setDeleteNode(null);
      await nodes.refresh();
      void ha.refresh();
    } catch (error) {
      toast.error("Could not delete HA node", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const checkNodes = async () => {
    setBusy("check");
    try {
      const result = await api.post<{ items: { id: number; name: string; status: string }[] }>(
        "/ha/nodes/check",
      );
      const online = result.items.filter((item) => item.status === "online").length;
      toast.success(
        "Node check complete",
        `${online} of ${result.items.length} node(s) responded. Status is based on each node's /health endpoint.`,
      );
      await nodes.refresh();
      void ha.refresh();
    } catch (error) {
      toast.error("Node check failed", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const applyHa = async () => {
    if (!isSuperuser) {
      toast.error("Superadmin required", "The ha.apply action requires the superadmin role.");
      return;
    }
    setBusy("apply");
    try {
      const change = await workflow.submit("/ha/apply", {});
      toast.info("Change request created", `Request ${change.id} requires dry run and approval.`);
    } catch (error) {
      toast.error("Could not create HA apply request", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const exportConfig = async () => {
    setBusy("export");
    try {
      const data = await api.get<Record<string, unknown>>("/ha/export");
      downloadJson(`nat64-ha-export-${new Date().toISOString().slice(0, 10)}.json`, data);
      toast.success("HA configuration exported", "Secrets and user records are excluded.");
    } catch (error) {
      toast.error("Export failed", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="High Availability"
        description="keepalived VRRP renders from database settings and is installed only through an approved change request. NAT64 session survival across failover is experimental and not verified by this build."
        breadcrumb="Network"
        actions={
          <>
            <Button variant="outline" size="sm" onClick={refresh} loading={ha.loading || nodes.loading}>
              <RefreshCw className="h-4 w-4" /> Refresh
            </Button>
            <Button variant="outline" size="sm" onClick={() => void exportConfig()} loading={busy === "export"}>
              <Download className="h-4 w-4" /> Export config
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => void applyHa()}
              loading={busy === "apply"}
              disabled={!canRequest || !isSuperuser}
              title={!isSuperuser ? "Requires superadmin" : undefined}
            >
              <ShieldCheck className="h-4 w-4" /> Apply keepalived
            </Button>
          </>
        }
      />

      {ha.error ? (
        <Alert tone="error" title="HA status unavailable">
          {ha.error}
        </Alert>
      ) : null}

      <Alert tone="warning" title="Continuity is not guaranteed">
        {continuity?.note ??
          "NAT64 session survival after failover is experimental and not verified by this build."}{" "}
        This page can manage VRRP and configuration metadata. It does not prove that existing NAT64 sessions
        survive a failover, and no automated test verifies that behaviour.
      </Alert>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="HA enabled"
          value={ha.data?.enabled ? "Yes" : "No"}
          hint={ha.data?.mode ? `${titleCase(ha.data.mode)} mode` : "Mode unavailable"}
          icon={<Router className="h-5 w-5" />}
          tone={ha.data?.enabled ? "green" : "slate"}
        />
        <StatCard
          label="Node role"
          value={titleCase(ha.data?.role ?? "unknown")}
          hint={ha.data?.node_name ?? "Node name not configured"}
          icon={<GitBranch className="h-5 w-5" />}
          tone="blue"
        />
        <StatCard
          label="Virtual IP"
          value={ha.data?.virtual_ip ?? "Not set"}
          hint={
            ha.data?.settings.vrrp_interface
              ? `VRRP on ${ha.data.settings.vrrp_interface}`
              : "No VRRP interface configured"
          }
          icon={<Globe2 className="h-5 w-5" />}
          tone={ha.data?.virtual_ip ? "violet" : "slate"}
        />
        <StatCard
          label="Nodes online"
          value={`${onlineNodes}/${nodeRows.length}`}
          hint="Based on the last node check"
          icon={<Wifi className="h-5 w-5" />}
          tone={onlineNodes > 0 ? "green" : "amber"}
        />
      </div>

      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "overview", label: "Overview" },
          { id: "settings", label: "VRRP settings" },
          { id: "nodes", label: "Nodes", badge: <Badge tone="slate">{nodeRows.length}</Badge> },
          { id: "preview", label: "keepalived preview" },
        ]}
      />

      {tab === "overview" ? (
        <>
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <Card>
              <CardHeader
                title="Cluster state"
                description="Values stored in the database and environment configuration."
                icon={<Router className="h-4 w-4" />}
              />
              <div className="p-5">
                <KeyValue
                  items={[
                    { label: "Enabled", value: ha.data?.enabled ? "Yes" : "No" },
                    { label: "Mode", value: titleCase(ha.data?.mode ?? "unknown") },
                    { label: "Role", value: titleCase(ha.data?.role ?? "unknown") },
                    { label: "Node name", value: ha.data?.node_name ?? "—" },
                    { label: "Virtual IP", value: ha.data?.virtual_ip ?? "—" },
                    { label: "VRRP interface", value: ha.data?.settings.vrrp_interface ?? "—" },
                    { label: "VRRP router ID", value: ha.data?.settings.vrrp_router_id ?? "—" },
                    { label: "Priority", value: ha.data?.settings.priority ?? "—" },
                    { label: "Peer address", value: ha.data?.settings.peer_address ?? "—" },
                    { label: "Heartbeat", value: ha.data ? `${ha.data.settings.heartbeat_seconds}s` : "—" },
                    { label: "Last failover", value: formatDate(ha.data?.settings.last_failover_at) },
                    { label: "Last error", value: ha.data?.settings.last_error ?? "—" },
                  ]}
                />
              </div>
            </Card>

            <Card>
              <CardHeader
                title="Session synchronization"
                description="Jool session sync settings are stored, but continuity has not been verified."
                icon={<AlertTriangle className="h-4 w-4" />}
              />
              <div className="space-y-4 p-5">
                <KeyValue
                  items={[
                    {
                      label: "Session sync",
                      value: ha.data?.settings.session_sync_enabled ? "Enabled" : "Disabled",
                    },
                    {
                      label: "Multicast group",
                      value: ha.data?.settings.session_sync_multicast ?? "—",
                    },
                    {
                      label: "Sync interface",
                      value: ha.data?.settings.session_sync_interface ?? "—",
                    },
                    {
                      label: "Continuity verified",
                      value: continuity?.verified ? (
                        <Badge tone="green" dot>
                          Verified
                        </Badge>
                      ) : (
                        <Badge tone="red" dot>
                          Not verified
                        </Badge>
                      ),
                    },
                  ]}
                />
                <Alert tone="error" title="Do not claim seamless failover">
                  Existing NAT64 sessions, BIB entries and pool4 port bindings are stateful. VRRP moves the
                  virtual IP, but it does not by itself preserve those sessions. Plan a maintenance window and
                  test failover in a lab before relying on continuity.
                </Alert>
              </div>
            </Card>
          </div>

          <Card>
            <CardHeader
              title="Nodes"
              description="Node records are metadata for monitoring and configuration sync. They are not a live cluster membership protocol."
              icon={<Wifi className="h-4 w-4" />}
              actions={
                canWrite ? (
                  <Button variant="primary" size="sm" onClick={openCreateNode}>
                    <Plus className="h-4 w-4" /> Add node
                  </Button>
                ) : undefined
              }
            />
            {nodeRows.length === 0 ? (
              <EmptyState
                icon={<Wifi className="h-6 w-6" />}
                title="No HA nodes registered"
                description="Add the peer node and its API URL to check reachability. Node checks call the peer's /health endpoint; they do not move traffic."
              />
            ) : (
              <DataTable
                rows={nodeRows}
                rowKey={(row) => row.id}
                dense
                columns={[
                  { key: "name", header: "Node", render: (row) => <span className="text-xs font-semibold">{row.name}</span> },
                  { key: "role", header: "Role", render: (row) => <Badge>{titleCase(row.role)}</Badge> },
                  {
                    key: "status",
                    header: "Status",
                    render: (row) => (
                      <Badge tone={nodeTone(row.status)} dot>
                        {titleCase(row.status)}
                      </Badge>
                    ),
                  },
                  { key: "api", header: "API URL", render: (row) => <code className="text-xs">{row.api_url ?? "—"}</code> },
                  {
                    key: "seen",
                    header: "Last seen",
                    render: (row) => <span className="text-xs">{formatRelative(row.last_seen_at)}</span>,
                  },
                  { key: "version", header: "Config version", render: (row) => <span className="text-xs">{row.config_version}</span> },
                ]}
              />
            )}
          </Card>
        </>
      ) : null}

      {tab === "settings" ? (
        <Card>
          <CardHeader
            title="VRRP settings"
            description="Saving writes the database only. Use Apply keepalived to render and install /etc/keepalived/keepalived.conf through an approved change request."
            icon={<ShieldCheck className="h-4 w-4" />}
          />
          {!form ? (
            <Spinner label="Loading HA settings" />
          ) : (
            <div className="space-y-5 p-5">
              {!canWrite ? (
                <Alert tone="warning" title="Read-only access">
                  Your role cannot change HA settings. The form is shown for reference.
                </Alert>
              ) : null}

              <Toggle
                checked={form.enabled}
                onChange={(checked) => setForm({ ...form, enabled: checked })}
                label="Enable HA"
                description="The renderer and apply plan reject HA when this is disabled."
                disabled={!canWrite}
              />

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
                <Field label="Mode" hint="keepalived uses VRRP. manual stores metadata only.">
                  <Select
                    value={form.mode}
                    disabled={!canWrite}
                    onChange={(event) => setForm({ ...form, mode: event.target.value })}
                  >
                    <option value="keepalived">keepalived</option>
                    <option value="manual">manual</option>
                  </Select>
                </Field>
                <Field label="Virtual IP" required hint="IPv4 or IPv6 address held by the master.">
                  <TextInput
                    value={form.virtual_ip}
                    disabled={!canWrite}
                    placeholder="203.0.113.10"
                    onChange={(event) => setForm({ ...form, virtual_ip: event.target.value })}
                  />
                </Field>
                <Field label="VRRP interface" required hint="Interface that carries VRRP advertisements.">
                  <TextInput
                    value={form.vrrp_interface}
                    disabled={!canWrite}
                    placeholder="eth0"
                    onChange={(event) => setForm({ ...form, vrrp_interface: event.target.value })}
                  />
                </Field>
                <Field label="VRRP router ID" hint="1–255, identical on both nodes.">
                  <TextInput
                    type="number"
                    min={1}
                    max={255}
                    value={form.vrrp_router_id}
                    disabled={!canWrite}
                    onChange={(event) => setForm({ ...form, vrrp_router_id: event.target.value })}
                  />
                </Field>
                <Field label="Priority" hint="1–254. The master should have the higher value.">
                  <TextInput
                    type="number"
                    min={1}
                    max={254}
                    value={form.priority}
                    disabled={!canWrite}
                    onChange={(event) => setForm({ ...form, priority: event.target.value })}
                  />
                </Field>
                <Field label="Peer address" hint="Informational peer address; not used by the renderer.">
                  <TextInput
                    value={form.peer_address}
                    disabled={!canWrite}
                    placeholder="198.51.100.2"
                    onChange={(event) => setForm({ ...form, peer_address: event.target.value })}
                  />
                </Field>
                <Field
                  label="VRRP authentication secret"
                  hint={
                    ha.data?.settings.auth_secret_set
                      ? "A secret is stored. Leave blank to keep it."
                      : "Required before a keepalived render can succeed."
                  }
                >
                  <TextInput
                    type="password"
                    autoComplete="new-password"
                    value={secret}
                    disabled={!canWrite}
                    placeholder={ha.data?.settings.auth_secret_set ? "•••••••• (stored)" : "New secret"}
                    onChange={(event) => setSecret(event.target.value)}
                  />
                </Field>
              </div>

              <div className="rounded-xl border border-slate-200 p-4 dark:border-slate-800">
                <Toggle
                  checked={form.session_sync_enabled}
                  onChange={(checked) => setForm({ ...form, session_sync_enabled: checked })}
                  label="Configure Jool session synchronization metadata"
                  description="Stored only. This build does not verify that sessions survive failover."
                  disabled={!canWrite}
                />
                <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <Field label="Session sync multicast group">
                    <TextInput
                      value={form.session_sync_multicast}
                      disabled={!canWrite || !form.session_sync_enabled}
                      placeholder="ff02::1"
                      onChange={(event) => setForm({ ...form, session_sync_multicast: event.target.value })}
                    />
                  </Field>
                  <Field label="Session sync interface">
                    <TextInput
                      value={form.session_sync_interface}
                      disabled={!canWrite || !form.session_sync_enabled}
                      placeholder="eth1"
                      onChange={(event) => setForm({ ...form, session_sync_interface: event.target.value })}
                    />
                  </Field>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <Button variant="primary" onClick={() => void saveSettings()} loading={busy === "settings"} disabled={!canWrite}>
                  Save settings
                </Button>
                <Button variant="outline" onClick={() => void preview.refresh()} loading={preview.loading}>
                  Refresh preview
                </Button>
              </div>

              <Alert tone="info" title="Two-step deployment">
                Saving settings here changes the database. Nothing reaches keepalived until a superadmin
                requests Apply keepalived, a dry run succeeds, and an approver accepts the change.
              </Alert>
            </div>
          )}
        </Card>
      ) : null}

      {tab === "nodes" ? (
        <>
          <Card>
            <CardHeader
              title="HA nodes"
              description="Reachability checks call each node's /health endpoint. They do not change local or remote traffic."
              icon={<Wifi className="h-4 w-4" />}
              actions={
                <>
                  <Button variant="outline" size="sm" onClick={() => void checkNodes()} loading={busy === "check"}>
                    <RefreshCw className="h-4 w-4" /> Check nodes
                  </Button>
                  {canWrite ? (
                    <Button variant="primary" size="sm" onClick={openCreateNode}>
                      <Plus className="h-4 w-4" /> Add node
                    </Button>
                  ) : null}
                </>
              }
            />
            {nodes.error ? (
              <div className="p-5">
                <Alert tone="error" title="Nodes unavailable">
                  {nodes.error}
                </Alert>
              </div>
            ) : null}
            {nodes.loading && !nodes.data ? <Spinner label="Loading nodes" /> : null}
            {nodeRows.length === 0 && !nodes.loading ? (
              <EmptyState
                icon={<Wifi className="h-6 w-6" />}
                title="No HA nodes registered"
                description="Add the peer node with its API URL to record and check it. Without a peer, VRRP can still be configured, but no failover can be observed."
                action={
                  canWrite ? (
                    <Button variant="primary" onClick={openCreateNode}>
                      <Plus className="h-4 w-4" /> Add node
                    </Button>
                  ) : undefined
                }
              />
            ) : (
              <DataTable
                rows={nodeRows}
                rowKey={(row) => row.id}
                loading={nodes.loading}
                columns={[
                  {
                    key: "name",
                    header: "Node",
                    render: (row) => (
                      <div>
                        <p className="text-xs font-semibold">{row.name}</p>
                        <p className="text-[11px] text-slate-400">{row.notes ?? "No notes"}</p>
                      </div>
                    ),
                  },
                  { key: "role", header: "Role", render: (row) => <Badge>{titleCase(row.role)}</Badge> },
                  {
                    key: "status",
                    header: "Status",
                    render: (row) => (
                      <Badge tone={nodeTone(row.status)} dot>
                        {titleCase(row.status)}
                      </Badge>
                    ),
                  },
                  { key: "address", header: "Address", render: (row) => <code className="text-xs">{row.address ?? "—"}</code> },
                  { key: "api", header: "API URL", render: (row) => <code className="text-xs">{row.api_url ?? "—"}</code> },
                  {
                    key: "enabled",
                    header: "Enabled",
                    render: (row) => (
                      <Badge tone={row.enabled ? "green" : "slate"} dot>
                        {row.enabled ? "Yes" : "No"}
                      </Badge>
                    ),
                  },
                  {
                    key: "seen",
                    header: "Last seen",
                    render: (row) => <span className="text-xs">{formatRelative(row.last_seen_at)}</span>,
                  },
                  {
                    key: "actions",
                    header: "",
                    headerClassName: "text-right",
                    className: "text-right",
                    render: (row) =>
                      canWrite ? (
                        <div className="flex justify-end gap-1">
                          <Button variant="ghost" size="icon" aria-label={`Edit ${row.name}`} onClick={() => openEditNode(row)}>
                            <Pencil className="h-4 w-4" />
                          </Button>
                          <Button variant="ghost" size="icon" aria-label={`Delete ${row.name}`} onClick={() => setDeleteNode(row)}>
                            <Trash2 className="h-4 w-4 text-rose-500" />
                          </Button>
                        </div>
                      ) : (
                        <span className="text-xs text-slate-400">Read only</span>
                      ),
                  },
                ]}
              />
            )}
          </Card>

          <Alert tone="info" title="What node checks prove">
            A successful check proves that the peer's HTTP health endpoint answered. It does not prove that
            VRRP is exchanging advertisements, that pool4 state is synchronized, or that failover will be
            seamless.
          </Alert>
        </>
      ) : null}

      {tab === "preview" ? (
        <>
          {preview.error ? (
            <Alert tone="error" title="Preview unavailable">
              {preview.error}
            </Alert>
          ) : null}

          <Card>
            <CardHeader
              title="Rendered keepalived configuration"
              description="Generated from current database settings. The VRRP shared secret is masked. It is not installed until an approved change executes."
              icon={<ShieldAlert className="h-4 w-4" />}
              actions={
                <Button variant="outline" size="sm" onClick={() => void preview.refresh()} loading={preview.loading}>
                  <RefreshCw className="h-4 w-4" /> Re-render
                </Button>
              }
            />
            <div className="space-y-4 p-5">
              {preview.loading && !previewData ? <Spinner label="Rendering keepalived configuration" /> : null}

              {previewData?.errors?.length ? (
                <Alert tone="error" title="Render errors">
                  <ul className="list-disc space-y-1 pl-4">
                    {previewData.errors.map((error) => (
                      <li key={error}>{error}</li>
                    ))}
                  </ul>
                </Alert>
              ) : null}

              {previewData?.warnings?.length ? (
                <Alert tone="warning" title="Warnings">
                  <ul className="list-disc space-y-1 pl-4">
                    {previewData.warnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                </Alert>
              ) : null}

              {previewData?.content ? (
                <>
                  <KeyValue
                    items={[
                      { label: "State", value: previewData.state ?? "—" },
                      { label: "SHA-256", value: <code className="break-all text-xs">{previewData.hash ?? "—"}</code> },
                      {
                        label: "Auth secret",
                        value: previewData.settings?.auth_secret_set ? (
                          <Badge tone="green" dot>
                            Stored
                          </Badge>
                        ) : (
                          <Badge tone="red" dot>
                            Missing
                          </Badge>
                        ),
                      },
                    ]}
                  />
                  <CodeBlock title="/etc/keepalived/keepalived.conf">{previewData.content}</CodeBlock>
                </>
              ) : previewData && !previewData.errors?.length ? (
                <EmptyState
                  icon={<ShieldAlert className="h-6 w-6" />}
                  title="No configuration rendered"
                  description="The renderer returned no content. Check the HA settings and stored VRRP secret."
                />
              ) : null}

              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="primary"
                  onClick={() => void applyHa()}
                  loading={busy === "apply"}
                  disabled={!canRequest || !isSuperuser}
                >
                  <ShieldCheck className="h-4 w-4" /> Create apply change request
                </Button>
                {!isSuperuser ? (
                  <span className="text-xs text-slate-500 dark:text-slate-400">
                    Requires the superadmin role.
                  </span>
                ) : null}
              </div>

              {previewData?.settings ? (
                <details className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
                  <summary className="cursor-pointer text-sm font-medium text-slate-700 dark:text-slate-300">
                    Settings used for this render
                  </summary>
                  <pre className="code-block mt-3">{prettyJson(previewData.settings)}</pre>
                </details>
              ) : null}

              <Alert tone="warning" title="Rollback is manual">
                The apply plan overwrites the managed keepalived file. If it fails, restore the previous file
                from the pre-change backup and re-run the plan after correcting the settings.
              </Alert>
            </div>
          </Card>
        </>
      ) : null}

      <Modal
        open={nodeModal !== null}
        onClose={() => setNodeModal(null)}
        title={nodeModal?.mode === "create" ? "Add HA node" : `Edit ${nodeModal?.node?.name ?? "node"}`}
        description="Node records are metadata for monitoring and sync. They do not join a cluster automatically."
        footer={
          <>
            <Button variant="outline" onClick={() => setNodeModal(null)} disabled={busy === "node"}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void saveNode()} loading={busy === "node"}>
              {nodeModal?.mode === "create" ? "Add node" : "Save changes"}
            </Button>
          </>
        }
      >
        {nodeModal ? (
          <div className="space-y-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="Name" required>
                <TextInput
                  value={nodeModal.form.name}
                  onChange={(event) =>
                    setNodeModal({ ...nodeModal, form: { ...nodeModal.form, name: event.target.value } })
                  }
                />
              </Field>
              <Field label="Role">
                <Select
                  value={nodeModal.form.role}
                  onChange={(event) =>
                    setNodeModal({ ...nodeModal, form: { ...nodeModal.form, role: event.target.value } })
                  }
                >
                  <option value="primary">Primary</option>
                  <option value="backup">Backup</option>
                  <option value="standalone">Standalone</option>
                </Select>
              </Field>
              <Field label="Address" hint="Management IP or hostname.">
                <TextInput
                  value={nodeModal.form.address}
                  onChange={(event) =>
                    setNodeModal({ ...nodeModal, form: { ...nodeModal.form, address: event.target.value } })
                  }
                />
              </Field>
              <Field label="API URL" hint="Used by node checks; /health is appended.">
                <TextInput
                  value={nodeModal.form.api_url}
                  placeholder="https://198.51.100.2:8000"
                  onChange={(event) =>
                    setNodeModal({ ...nodeModal, form: { ...nodeModal.form, api_url: event.target.value } })
                  }
                />
              </Field>
            </div>
            <Field label="Notes">
              <Textarea
                value={nodeModal.form.notes}
                maxLength={4000}
                onChange={(event) =>
                  setNodeModal({ ...nodeModal, form: { ...nodeModal.form, notes: event.target.value } })
                }
              />
            </Field>
            <Toggle
              checked={nodeModal.form.enabled}
              onChange={(checked) =>
                setNodeModal({ ...nodeModal, form: { ...nodeModal.form, enabled: checked } })
              }
              label="Node enabled"
              description="Disabled nodes are skipped by node checks."
            />
          </div>
        ) : null}
      </Modal>

      <ConfirmDialog
        open={deleteNode !== null}
        onClose={() => setDeleteNode(null)}
        onConfirm={() => void confirmDeleteNode()}
        title={`Delete HA node ${deleteNode?.name ?? ""}?`}
        description="Removes the node record only. It does not change keepalived or the peer node."
        confirmLabel="Delete node"
        danger
        loading={busy === "delete-node"}
      />

      <ChangeDialog
        workflow={workflow}
        title="Apply keepalived change"
        onUpdated={(change: ChangeRequest) => {
          if (change.status === "applied") {
            void preview.refresh();
            void ha.refresh();
          }
        }}
      />
    </div>
  );
}
