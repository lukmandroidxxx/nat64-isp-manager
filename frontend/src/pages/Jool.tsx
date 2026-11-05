import { useEffect, useState } from "react";
import {
  AlertTriangle,
  Boxes,
  Globe2,
  Pause,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  ServerCog,
  Settings2,
  Trash2,
} from "lucide-react";
import { api, errorText } from "../lib/api";
import { formatNumber, formatRelative, titleCase } from "../lib/format";
import type { JoolInstance } from "../lib/types";
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
  KeyValue,
  Modal,
  PageHeader,
  Spinner,
  StatCard,
  TextInput,
  Toggle,
} from "../components/ui";
import { DataTable } from "../components/DataTable";
import { ChangeDialog, useChangeWorkflow } from "../components/ChangeWorkflow";

interface JoolStatus {
  status: string;
  installed: boolean;
  version: string | null;
  compatible: boolean;
  message: string | null;
  instances: unknown[];
}

interface DetailState {
  instance: JoolInstance;
  loading: boolean;
  error: string | null;
  pool4: Record<string, unknown> | null;
  globals: Record<string, unknown> | null;
}

const EMPTY_FORM = { name: "", pool6: "64:ff9b::/96", display_name: "" };

export default function Jool() {
  const toast = useToast();
  const { can } = useAuth();
  const canRequest = can("changes.request");
  const canWrite = can("jool.write");
  const workflow = useChangeWorkflow();

  const status = usePolling<JoolStatus>("/jool/status", undefined, { interval: 10000 });
  const instances = usePolling<{ items: JoolInstance[] }>("/jool/instances", undefined, {
    interval: 5000,
  });

  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [busy, setBusy] = useState(false);

  const [editTarget, setEditTarget] = useState<JoolInstance | null>(null);
  const [editForm, setEditForm] = useState({ display_name: "", enabled: true });

  const [recreateTarget, setRecreateTarget] = useState<JoolInstance | null>(null);
  const [recreatePool6, setRecreatePool6] = useState("");

  const [globalTarget, setGlobalTarget] = useState<JoolInstance | null>(null);
  const [globalForm, setGlobalForm] = useState({ key: "pool6", value: "" });

  const [detail, setDetail] = useState<DetailState | null>(null);

  const [removeTarget, setRemoveTarget] = useState<JoolInstance | null>(null);

  const data = status.data;
  const rows = instances.data?.items ?? [];

  useEffect(() => {
    if (!detail) return;
    let cancelled = false;
    const load = async () => {
      try {
        const [pool4, globals] = await Promise.all([
          api.get<Record<string, unknown>>(`/jool/instances/${detail.instance.id}/pool4`),
          api.get<Record<string, unknown>>(`/jool/instances/${detail.instance.id}/globals`),
        ]);
        if (!cancelled) {
          setDetail((current) =>
            current ? { ...current, loading: false, error: null, pool4, globals } : current,
          );
        }
      } catch (error) {
        if (!cancelled) {
          setDetail((current) =>
            current ? { ...current, loading: false, error: errorText(error) } : current,
          );
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [detail?.instance.id]);

  const openCreate = () => {
    setForm(EMPTY_FORM);
    setCreateOpen(true);
  };

  const submitCreate = async () => {
    setBusy(true);
    try {
      if (!form.name.trim()) throw new Error("Instance name is required");
      if (!form.pool6.trim()) throw new Error("pool6 prefix is required");
      await workflow.submit("/jool/instances", {
        name: form.name.trim(),
        pool6: form.pool6.trim(),
        display_name: form.display_name.trim() || null,
      });
      setCreateOpen(false);
    } catch (error) {
      toast.error("Could not create change request", errorText(error));
    } finally {
      setBusy(false);
    }
  };

  const openEdit = (instance: JoolInstance) => {
    setEditTarget(instance);
    setEditForm({ display_name: instance.display_name ?? "", enabled: instance.enabled });
  };

  const saveEdit = async () => {
    if (!editTarget) return;
    setBusy(true);
    try {
      await api.patch(`/jool/instances/${editTarget.id}`, {
        display_name: editForm.display_name.trim() || null,
        enabled: editForm.enabled,
      });
      toast.success("Instance metadata updated", "No host command was run.");
      setEditTarget(null);
      await instances.refresh();
    } catch (error) {
      toast.error("Could not update instance", errorText(error));
    } finally {
      setBusy(false);
    }
  };

  const changeAction = async (path: string, payload: unknown, title: string) => {
    try {
      await workflow.submit(path, payload);
      toast.info("Change request created", title);
    } catch (error) {
      toast.error("Could not create change request", errorText(error));
    }
  };

  const openRecreate = (instance: JoolInstance) => {
    setRecreateTarget(instance);
    setRecreatePool6(instance.pool6);
  };

  const submitRecreate = async () => {
    if (!recreateTarget) return;
    setBusy(true);
    try {
      await workflow.submit(`/jool/instances/${recreateTarget.id}/recreate`, {
        instance_id: recreateTarget.id,
        pool6: recreatePool6.trim(),
      });
      setRecreateTarget(null);
    } catch (error) {
      toast.error("Could not create change request", errorText(error));
    } finally {
      setBusy(false);
    }
  };

  const openGlobals = (instance: JoolInstance) => {
    setGlobalTarget(instance);
    setGlobalForm({ key: "pool6", value: instance.pool6 });
  };

  const submitGlobal = async () => {
    if (!globalTarget) return;
    setBusy(true);
    try {
      await workflow.submit("/jool/global", {
        instance_id: globalTarget.id,
        key: globalForm.key.trim(),
        value: globalForm.value.trim(),
      });
      setGlobalTarget(null);
    } catch (error) {
      toast.error("Could not create change request", errorText(error));
    } finally {
      setBusy(false);
    }
  };

  const submitRemove = async () => {
    if (!removeTarget) return;
    setBusy(true);
    try {
      await workflow.submit(`/changes`, {
        action: "jool.remove_instance",
        payload: { instance_id: removeTarget.id },
      });
      setRemoveTarget(null);
    } catch (error) {
      toast.error("Could not create change request", errorText(error));
    } finally {
      setBusy(false);
    }
  };

  const refreshAll = () => {
    void status.refresh();
    void instances.refresh();
  };

  const globals = (detail?.globals?.settings ?? null) as Record<string, unknown> | null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Jool Management"
        breadcrumb="NAT64"
        description="Jool 4 netfilter instances, their NAT64 prefix, lifecycle actions and global settings. Every host change is a dry-run plus approval change request."
        actions={
          <>
            <Button variant="outline" size="sm" onClick={refreshAll} loading={status.loading || instances.loading}>
              <RefreshCw className="h-4 w-4" /> Refresh
            </Button>
            {canRequest ? (
              <Button variant="primary" size="sm" onClick={openCreate}>
                <Plus className="h-4 w-4" /> New instance
              </Button>
            ) : null}
          </>
        }
      />

      {status.error ? (
        <Alert tone="error" title="Jool status unavailable">
          {status.error}
        </Alert>
      ) : null}

      {data?.status === "unsupported" ? (
        <Alert tone="warning" title="Jool host integration is unavailable">
          {data.message ??
            "The privileged helper is not reachable. Instance records are configuration only; no Jool commands will be run."}
        </Alert>
      ) : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Host status"
          value={titleCase(data?.status ?? "unknown")}
          hint={data?.message ?? (data?.installed ? "Jool binary detected" : "Not detected")}
          icon={<ServerCog className="h-5 w-5" />}
          tone={data?.status === "online" ? "green" : data?.status === "unsupported" ? "violet" : "amber"}
        />
        <StatCard
          label="Jool version"
          value={data?.version ?? "—"}
          hint={data?.compatible ? "Compatible with Jool 4" : "Compatibility not confirmed"}
          icon={<Settings2 className="h-5 w-5" />}
        />
        <StatCard
          label="Configured instances"
          value={formatNumber(rows.length)}
          hint="Database records; live state requires the host helper"
          icon={<Boxes className="h-5 w-5" />}
          tone="violet"
        />
        <StatCard
          label="Active sessions"
          value={formatNumber(rows.reduce((total, row) => total + row.active_sessions, 0))}
          hint="Observed by the collector across instances"
          icon={<Globe2 className="h-5 w-5" />}
        />
      </div>

      <Card>
        <CardHeader
          title="NAT64 instances"
          description="pool6 is immutable on a live instance. Changing it requires a recreate, which drops sessions and BIB state."
          icon={<ServerCog className="h-4 w-4" />}
        />
        {instances.loading && !instances.data ? <Spinner label="Loading instances" /> : null}
        {!instances.loading && rows.length === 0 ? (
          <EmptyState
            icon={<ServerCog className="h-6 w-6" />}
            title="No Jool instances configured"
            description="Create a change request that adds a Jool 4 netfilter instance. On a Windows or unprivileged host the plan will be marked unsupported and cannot execute."
            action={
              canRequest ? (
                <Button variant="primary" onClick={openCreate}>
                  <Plus className="h-4 w-4" /> New instance
                </Button>
              ) : undefined
            }
          />
        ) : null}
        {rows.length > 0 ? (
          <DataTable
            rows={rows}
            rowKey={(row) => row.id}
            dense
            columns={[
              {
                key: "name",
                header: "Instance",
                render: (row) => (
                  <div>
                    <code className="text-xs font-semibold">{row.name}</code>
                    <p className="text-[11px] text-slate-400">{row.display_name ?? "No display name"}</p>
                  </div>
                ),
              },
              { key: "pool6", header: "pool6", render: (row) => <code className="text-xs">{row.pool6}</code> },
              {
                key: "status",
                header: "Status",
                render: (row) => (
                  <Badge status={row.paused ? "paused" : row.status} dot>
                    {row.paused ? "Paused" : titleCase(row.status)}
                  </Badge>
                ),
              },
              {
                key: "sessions",
                header: "Sessions",
                render: (row) => <span className="text-xs">{formatNumber(row.active_sessions)}</span>,
              },
              {
                key: "pools",
                header: "Pools",
                render: (row) => <span className="text-xs">{formatNumber(row.pool_count)}</span>,
              },
              {
                key: "enabled",
                header: "Enabled",
                render: (row) =>
                  row.enabled ? <Badge tone="green">Yes</Badge> : <Badge tone="slate">No</Badge>,
              },
              {
                key: "seen",
                header: "Last seen",
                render: (row) => (
                  <span className="text-xs text-slate-500 dark:text-slate-400">{formatRelative(row.last_seen_at)}</span>
                ),
              },
              {
                key: "actions",
                header: "",
                headerClassName: "text-right",
                className: "text-right",
                render: (row) => (
                  <div className="flex flex-wrap justify-end gap-1">
                    <Button variant="ghost" size="sm" onClick={() => setDetail({ instance: row, loading: true, error: null, pool4: null, globals: null })}>
                      Details
                    </Button>
                    {canRequest ? (
                      <>
                        <Button
                          variant="ghost"
                          size="icon"
                          title={row.paused ? "Resume NAT64" : "Pause NAT64"}
                          aria-label={row.paused ? "Resume NAT64" : "Pause NAT64"}
                          onClick={() =>
                            void changeAction(
                              `/jool/instances/${row.id}/${row.paused ? "resume" : "pause"}`,
                              {},
                              row.paused ? "Resume NAT64" : "Pause NAT64",
                            )
                          }
                        >
                          {row.paused ? <Play className="h-4 w-4 text-emerald-500" /> : <Pause className="h-4 w-4" />}
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          title="Update global setting"
                          aria-label="Update global setting"
                          onClick={() => openGlobals(row)}
                        >
                          <Settings2 className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          title="Recreate instance"
                          aria-label="Recreate instance"
                          onClick={() => openRecreate(row)}
                        >
                          <RotateCcw className="h-4 w-4 text-amber-500" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => void changeAction(`/jool/instances/${row.id}/sync-pool4`, {}, "Synchronise pool4")}
                        >
                          Sync pool4
                        </Button>
                      </>
                    ) : null}
                    {canWrite ? (
                      <Button variant="ghost" size="icon" onClick={() => openEdit(row)} aria-label="Edit metadata">
                        <Pencil className="h-4 w-4" />
                      </Button>
                    ) : null}
                    {canRequest ? (
                      <Button variant="ghost" size="icon" onClick={() => setRemoveTarget(row)} aria-label="Remove instance">
                        <Trash2 className="h-4 w-4 text-rose-500" />
                      </Button>
                    ) : null}
                  </div>
                ),
              },
            ]}
          />
        ) : null}
      </Card>

      <Alert tone="info" title="What this page does not do">
        It never exposes the Jool CLI or a shell. Instance creation, pool4 synchronisation, pause, resume,
        recreate and global updates are typed operations executed by the root-owned helper only after an
        administrator approves the rendered plan. Disabling or renaming an instance in the database does not
        stop NAT64; pause is a separate approved change.
      </Alert>

      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Create Jool instance"
        description="Creates a dry-run change request. The plan is validated before it can be approved."
        footer={
          <>
            <Button variant="outline" onClick={() => setCreateOpen(false)} disabled={busy}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void submitCreate()} loading={busy}>
              Create change request
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Instance name" required hint="1–15 characters, used as the Jool instance identifier.">
            <TextInput
              value={form.name}
              maxLength={15}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="nat64"
            />
          </Field>
          <Field label="pool6 (NAT64 prefix)" required hint="Immutable while the instance is live.">
            <TextInput
              value={form.pool6}
              onChange={(event) => setForm({ ...form, pool6: event.target.value })}
              placeholder="64:ff9b::/96"
            />
          </Field>
          <Field label="Display name">
            <TextInput
              value={form.display_name}
              maxLength={128}
              onChange={(event) => setForm({ ...form, display_name: event.target.value })}
            />
          </Field>
          <Alert tone="warning">
            Creating a new instance has no effect until the change is approved and executed by the privileged
            helper.
          </Alert>
        </div>
      </Modal>

      <Modal
        open={editTarget !== null}
        onClose={() => setEditTarget(null)}
        title={`Edit ${editTarget?.name ?? "instance"} metadata`}
        description="Database-only edit. This never writes to the host."
        footer={
          <>
            <Button variant="outline" onClick={() => setEditTarget(null)} disabled={busy}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void saveEdit()} loading={busy}>
              Save metadata
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Display name">
            <TextInput
              value={editForm.display_name}
              maxLength={128}
              onChange={(event) => setEditForm({ ...editForm, display_name: event.target.value })}
            />
          </Field>
          <Toggle
            checked={editForm.enabled}
            onChange={(enabled) => setEditForm({ ...editForm, enabled })}
            label="Instance enabled in the manager"
            description="Disabling here only affects manager-side synchronisation and reporting, not live NAT64."
          />
        </div>
      </Modal>

      <Modal
        open={recreateTarget !== null}
        onClose={() => setRecreateTarget(null)}
        title={`Recreate ${recreateTarget?.name ?? "instance"}`}
        description="Deletes and recreates the Jool instance with a new pool6 prefix."
        footer={
          <>
            <Button variant="outline" onClick={() => setRecreateTarget(null)} disabled={busy}>
              Cancel
            </Button>
            <Button variant="danger" onClick={() => void submitRecreate()} loading={busy}>
              Create recreate request
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Alert tone="error" title="All sessions and BIB state will be dropped">
            Recreating an instance interrupts established NAT64 sessions. pool6 cannot be changed in place;
            this is the only supported way to change it.
          </Alert>
          <Field label="New pool6 prefix" required>
            <TextInput
              value={recreatePool6}
              onChange={(event) => setRecreatePool6(event.target.value)}
              placeholder="64:ff9b::/96"
            />
          </Field>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Current prefix: <code>{recreateTarget?.pool6 ?? "—"}</code>. Keep DNS64 in sync with the new prefix.
          </p>
        </div>
      </Modal>

      <Modal
        open={globalTarget !== null}
        onClose={() => setGlobalTarget(null)}
        title={`Update global setting on ${globalTarget?.name ?? "instance"}`}
        description="Typed key/value update. The helper allowlists supported Jool globals and rejects the rest."
        footer={
          <>
            <Button variant="outline" onClick={() => setGlobalTarget(null)} disabled={busy}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void submitGlobal()} loading={busy}>
              Create change request
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Key" required hint="Examples: manually-enabled, pool6, logging-debug, mtu-plateau.">
            <TextInput
              value={globalForm.key}
              onChange={(event) => setGlobalForm({ ...globalForm, key: event.target.value })}
            />
          </Field>
          <Field label="Value" required>
            <TextInput
              value={globalForm.value}
              onChange={(event) => setGlobalForm({ ...globalForm, value: event.target.value })}
            />
          </Field>
          <Alert tone="warning">
            Supported keys and value formats are enforced by the host helper. An unknown key fails the dry run
            instead of reaching the host.
          </Alert>
        </div>
      </Modal>

      <Modal
        open={detail !== null}
        onClose={() => setDetail(null)}
        title={`Instance detail: ${detail?.instance.name ?? ""}`}
        description="Desired database pool4 compared with the observed live pool4. Read-only."
        size="lg"
        footer={
          <Button variant="outline" onClick={() => setDetail(null)}>
            Close
          </Button>
        }
      >
        {detail?.loading ? <Spinner label="Reading instance state" /> : null}
        {detail?.error ? (
          <Alert tone="error" title="Could not read instance">
            {detail.error}
          </Alert>
        ) : null}
        {detail && !detail.loading ? (
          <div className="space-y-5">
            <KeyValue
              items={[
                { label: "Instance", value: <code>{detail.instance.name}</code> },
                { label: "pool6", value: <code>{detail.instance.pool6}</code> },
                { label: "Observed pool6", value: detail.instance.observed_pool6 ?? "—" },
                { label: "Framework", value: detail.instance.framework },
                { label: "Status", value: titleCase(detail.instance.status) },
                { label: "Config version", value: detail.instance.config_version },
              ]}
            />

            <div>
              <h3 className="mb-2 text-sm font-semibold text-slate-800 dark:text-slate-200">pool4 state</h3>
              <pre className="code-block max-h-64">
                {JSON.stringify(
                  {
                    status: detail.pool4?.status,
                    message: detail.pool4?.message,
                    desired: detail.pool4?.desired,
                    observed: detail.pool4?.observed,
                    sync_plan: detail.pool4?.sync_plan,
                  },
                  null,
                  2,
                )}
              </pre>
            </div>

            <div>
              <h3 className="mb-2 text-sm font-semibold text-slate-800 dark:text-slate-200">Global settings</h3>
              {globals && Object.keys(globals).length > 0 ? (
                <KeyValue
                  items={Object.entries(globals).map(([key, value]) => ({
                    label: key,
                    value: typeof value === "object" ? JSON.stringify(value) : String(value),
                  }))}
                />
              ) : (
                <Alert tone="info">
                  {String(detail.globals?.message ?? "Global settings are not available from this host.")}
                </Alert>
              )}
            </div>
          </div>
        ) : null}
      </Modal>

      <ConfirmDialog
        open={removeTarget !== null}
        onClose={() => setRemoveTarget(null)}
        onConfirm={() => void submitRemove()}
        title={`Remove instance ${removeTarget?.name ?? ""}?`}
        description="Creates a change request. Execution deletes the live instance and its state."
        confirmLabel="Create removal request"
        danger
        loading={busy}
      >
        <div className="space-y-2 text-sm text-slate-600 dark:text-slate-300">
          <p className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-500" />
            Removing a Jool instance drops all active sessions and BIB entries that belong to it. Remove
            associated pool assignments first if you intend to recreate it.
          </p>
        </div>
      </ConfirmDialog>

      <ChangeDialog
        workflow={workflow}
        title="Jool change request"
        onUpdated={() => {
          void instances.refresh();
          void status.refresh();
        }}
      />
    </div>
  );
}
