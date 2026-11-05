import { useState } from "react";
import {
  Archive,
  CheckCircle2,
  DatabaseBackup,
  Download,
  FileJson,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  Trash2,
} from "lucide-react";
import { api, downloadFile, errorText } from "../lib/api";
import { formatBytes, formatDate, formatRelative, titleCase } from "../lib/format";
import type { Backup, ChangeRequest } from "../lib/types";
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
  TextInput,
  Textarea,
} from "../components/ui";
import { DataTable } from "../components/DataTable";
import { ChangeDialog, useChangeWorkflow } from "../components/ChangeWorkflow";

interface ValidationResult {
  id: number;
  valid: boolean;
  errors: string[];
  checksum_stored: string;
  checksum_actual: string;
  counts: Backup["counts"];
}

function defaultName(): string {
  const now = new Date();
  const pad = (value: number) => String(value).padStart(2, "0");
  return `manual-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(
    now.getHours(),
  )}${pad(now.getMinutes())}`;
}

export default function Backups() {
  const toast = useToast();
  const { can } = useAuth();
  const canCreate = can("backups.create");
  const canRestore = can("backups.restore");
  const canDelete = can("backups.delete");
  const workflow = useChangeWorkflow();

  const backups = usePolling<{ items: Backup[]; note: string }>("/backups", undefined, { interval: 10000 });

  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState({ name: defaultName(), note: "" });
  const [busy, setBusy] = useState<string | null>(null);

  const [detail, setDetail] = useState<{ backup: Backup; loading: boolean; error: string | null } | null>(
    null,
  );
  const [validation, setValidation] = useState<ValidationResult | null>(null);

  const [restoreTarget, setRestoreTarget] = useState<Backup | null>(null);
  const [restoreMode, setRestoreMode] = useState<"merge" | "replace">("merge");

  const [deleteTarget, setDeleteTarget] = useState<Backup | null>(null);

  const rows = backups.data?.items ?? [];

  const openCreate = () => {
    setForm({ name: defaultName(), note: "" });
    setCreateOpen(true);
  };

  const createBackup = async () => {
    if (!form.name.trim()) {
      toast.error("Name is required", "Give the backup a descriptive name.");
      return;
    }
    setBusy("create");
    try {
      const created = await api.post<Backup>("/backups", {
        name: form.name.trim(),
        note: form.note.trim() || null,
      });
      toast.success("Backup created", `${created.name} is stored with a SHA-256 checksum.`);
      setCreateOpen(false);
      await backups.refresh();
    } catch (error) {
      toast.error("Could not create backup", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const openDetail = async (backup: Backup) => {
    setDetail({ backup, loading: true, error: null });
    try {
      const full = await api.get<Backup>(`/backups/${backup.id}`, { include_content: true });
      setDetail({ backup: full, loading: false, error: null });
    } catch (error) {
      setDetail({ backup, loading: false, error: errorText(error) });
    }
  };

  const validate = async (backup: Backup) => {
    setBusy(`validate-${backup.id}`);
    try {
      const result = await api.get<ValidationResult>(`/backups/${backup.id}/validate`);
      setValidation(result);
      if (result.valid) {
        toast.success("Backup is valid", "Stored checksum matches the content.");
      } else {
        toast.error("Backup validation failed", result.errors.join("; ") || "Checksum mismatch.");
      }
    } catch (error) {
      toast.error("Could not validate backup", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const download = async (backup: Backup) => {
    try {
      await downloadFile(`/backups/${backup.id}/download`, `nat64-backup-${backup.id}.json`);
    } catch (error) {
      toast.error("Download failed", errorText(error));
    }
  };

  const confirmRestore = async () => {
    if (!restoreTarget) return;
    setBusy("restore");
    try {
      const change = await api.post<ChangeRequest & { note?: string }>(
        `/backups/${restoreTarget.id}/restore`,
        { mode: restoreMode },
      );
      setRestoreTarget(null);
      workflow.show(change);
      if (change.note) {
        toast.info("Restore request created", change.note);
      }
    } catch (error) {
      toast.error("Could not create restore request", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setBusy("delete");
    try {
      await api.delete(`/backups/${deleteTarget.id}`);
      toast.success("Backup deleted", "The database row and stored file were removed.");
      setDeleteTarget(null);
      await backups.refresh();
    } catch (error) {
      toast.error("Could not delete backup", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const automaticCount = rows.filter((backup) => backup.is_automatic || backup.kind === "automatic").length;
  const totalSize = rows.reduce((total, backup) => total + (backup.size_bytes ?? 0), 0);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Backup & Restore"
        description="Configuration backups for pools, prefixes, Jool metadata, DNS64, firewall and network settings. Users, sessions, audit logs and secrets are excluded."
        breadcrumb="System"
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void backups.refresh()}
              loading={backups.loading}
            >
              <RefreshCw className="h-4 w-4" /> Refresh
            </Button>
            {canCreate ? (
              <Button variant="primary" size="sm" onClick={openCreate}>
                <Plus className="h-4 w-4" /> Create backup
              </Button>
            ) : null}
          </>
        }
      />

      {backups.error ? (
        <Alert tone="error" title="Backups unavailable">
          {backups.error}
        </Alert>
      ) : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Backups"
          value={rows.length}
          hint={`${automaticCount} automatic pre-change · ${rows.length - automaticCount} manual`}
          icon={<DatabaseBackup className="h-5 w-5" />}
        />
        <StatCard
          label="Stored size"
          value={formatBytes(totalSize)}
          hint="JSON configuration snapshots"
          icon={<Archive className="h-5 w-5" />}
          tone="violet"
        />
        <StatCard
          label="Latest backup"
          value={rows[0] ? formatRelative(rows[0].created_at) : "Never"}
          hint={rows[0]?.name ?? "No backup has been created"}
          icon={<FileJson className="h-5 w-5" />}
          tone={rows.length ? "green" : "slate"}
        />
        <StatCard
          label="Restore path"
          value="Change request"
          hint="Dry-run and administrator approval are required"
          icon={<RotateCcw className="h-5 w-5" />}
          tone="amber"
        />
      </div>

      <Alert tone="warning" title="A restore is database-only">
        Restoring a backup replaces control-plane configuration rows. It does not write Jool pool4, BIND,
        nftables or netplan. After a restore, re-apply those components through their own approved change
        requests, and verify that the restored values match the intended host state.
      </Alert>

      <Card>
        <CardHeader
          title="Configuration backups"
          description="Automatic pre-change backups cannot be deleted from the UI. Checksums are SHA-256 over canonical JSON."
          icon={<DatabaseBackup className="h-4 w-4" />}
        />
        {backups.loading && !backups.data ? <Spinner label="Loading backups" /> : null}
        {!backups.loading && rows.length === 0 ? (
          <EmptyState
            icon={<DatabaseBackup className="h-6 w-6" />}
            title="No backups yet"
            description="Create a manual backup before host changes, or wait for an approved change to create an automatic pre-change snapshot."
            action={
              canCreate ? (
                <Button variant="primary" onClick={openCreate}>
                  <Plus className="h-4 w-4" /> Create backup
                </Button>
              ) : undefined
            }
          />
        ) : null}
        {rows.length > 0 ? (
          <DataTable
            rows={rows}
            rowKey={(row) => row.id}
            loading={backups.loading}
            dense
            columns={[
              {
                key: "name",
                header: "Backup",
                render: (row) => (
                  <div>
                    <p className="text-xs font-semibold">{row.name}</p>
                    <p className="text-[11px] text-slate-400">{row.note ?? "No note"}</p>
                  </div>
                ),
              },
              {
                key: "kind",
                header: "Kind",
                render: (row) => (
                  <Badge tone={row.is_automatic || row.kind === "automatic" ? "amber" : "blue"}>
                    {titleCase(row.kind)}
                  </Badge>
                ),
              },
              {
                key: "counts",
                header: "Contents",
                render: (row) =>
                  row.counts ? (
                    <span className="text-xs">
                      {row.counts.pools} pools · {row.counts.prefixes} prefixes · {row.counts.instances} instances
                    </span>
                  ) : (
                    <span className="text-xs text-slate-400">See details</span>
                  ),
              },
              {
                key: "size",
                header: "Size",
                render: (row) => <span className="text-xs">{formatBytes(row.size_bytes)}</span>,
              },
              {
                key: "created",
                header: "Created",
                render: (row) => (
                  <div>
                    <p className="text-xs">{formatRelative(row.created_at)}</p>
                    <p className="text-[11px] text-slate-400">{row.created_by ?? "system"}</p>
                  </div>
                ),
              },
              {
                key: "actions",
                header: "",
                headerClassName: "text-right",
                className: "text-right",
                render: (row) => (
                  <div className="flex flex-wrap justify-end gap-1">
                    <Button variant="ghost" size="sm" onClick={() => void openDetail(row)}>
                      Details
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => void validate(row)}
                      loading={busy === `validate-${row.id}`}
                    >
                      Validate
                    </Button>
                    <Button variant="ghost" size="icon" aria-label="Download backup" onClick={() => void download(row)}>
                      <Download className="h-4 w-4" />
                    </Button>
                    {canRestore ? (
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="Restore backup"
                        onClick={() => {
                          setRestoreMode("merge");
                          setRestoreTarget(row);
                        }}
                      >
                        <RotateCcw className="h-4 w-4 text-amber-500" />
                      </Button>
                    ) : null}
                    {canDelete && !(row.is_automatic || row.kind === "automatic") ? (
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="Delete backup"
                        onClick={() => setDeleteTarget(row)}
                      >
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

      {validation ? (
        <Card>
          <CardHeader
            title="Validation result"
            description={`Backup ${validation.id}`}
            icon={<CheckCircle2 className="h-4 w-4" />}
            actions={
              <Button variant="ghost" size="sm" onClick={() => setValidation(null)}>
                Dismiss
              </Button>
            }
          />
          <div className="space-y-4 p-5">
            <Alert tone={validation.valid ? "success" : "error"} title={validation.valid ? "Backup is valid" : "Backup is not valid"}>
              {validation.valid
                ? "The stored checksum matches the canonical JSON content."
                : validation.errors.join("; ") || "The stored checksum does not match the content."}
            </Alert>
            <KeyValue
              items={[
                { label: "Stored checksum", value: <code className="break-all text-xs">{validation.checksum_stored}</code> },
                { label: "Actual checksum", value: <code className="break-all text-xs">{validation.checksum_actual}</code> },
                { label: "Pools", value: validation.counts?.pools ?? 0 },
                { label: "Prefixes", value: validation.counts?.prefixes ?? 0 },
                { label: "Jool instances", value: validation.counts?.instances ?? 0 },
              ]}
            />
          </div>
        </Card>
      ) : null}

      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Create configuration backup"
        description="Captures non-secret control-plane configuration only."
        footer={
          <>
            <Button variant="outline" onClick={() => setCreateOpen(false)} disabled={busy !== null}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void createBackup()} loading={busy === "create"}>
              Create backup
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Name" required hint="Use a name you can recognise later.">
            <TextInput
              value={form.name}
              maxLength={255}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
            />
          </Field>
          <Field label="Note">
            <Textarea
              value={form.note}
              maxLength={4000}
              className="min-h-[80px]"
              onChange={(event) => setForm({ ...form, note: event.target.value })}
            />
          </Field>
          <Alert tone="info">
            The backup excludes users, roles, auth sessions, audit logs and secrets by design.
          </Alert>
        </div>
      </Modal>

      <Modal
        open={detail !== null}
        onClose={() => setDetail(null)}
        title={`Backup detail: ${detail?.backup.name ?? ""}`}
        size="lg"
        footer={
          <Button variant="outline" onClick={() => setDetail(null)}>
            Close
          </Button>
        }
      >
        {detail?.loading ? <Spinner label="Loading backup" /> : null}
        {detail?.error ? (
          <Alert tone="error" title="Could not load backup">
            {detail.error}
          </Alert>
        ) : null}
        {detail && !detail.loading && !detail.error ? (
          <div className="space-y-4">
            <KeyValue
              items={[
                { label: "Name", value: detail.backup.name },
                { label: "Kind", value: titleCase(detail.backup.kind) },
                { label: "Created", value: formatDate(detail.backup.created_at) },
                { label: "Created by", value: detail.backup.created_by ?? "system" },
                { label: "Size", value: formatBytes(detail.backup.size_bytes) },
                { label: "Checksum", value: <code className="break-all text-xs">{detail.backup.checksum}</code> },
                { label: "Pools", value: detail.backup.counts?.pools ?? 0 },
                { label: "Prefixes", value: detail.backup.counts?.prefixes ?? 0 },
                { label: "Instances", value: detail.backup.counts?.instances ?? 0 },
                { label: "Firewall rules", value: detail.backup.counts?.firewall_rules ?? 0 },
              ]}
            />
            {detail.backup.note ? (
              <Alert tone="info" title="Note">
                {detail.backup.note}
              </Alert>
            ) : null}
            {detail.backup.content ? (
              <CodeBlock title="Configuration content">{JSON.stringify(detail.backup.content, null, 2)}</CodeBlock>
            ) : null}
          </div>
        ) : null}
      </Modal>

      <ConfirmDialog
        open={restoreTarget !== null}
        onClose={() => setRestoreTarget(null)}
        onConfirm={() => void confirmRestore()}
        title={`Restore ${restoreTarget?.name ?? "backup"}?`}
        description="Creates a change request. Approval and execution are still required."
        confirmLabel="Create restore request"
        danger
        loading={busy === "restore"}
      >
        <div className="space-y-4">
          <Alert tone="warning" title="Database configuration only">
            This will not re-apply Jool pool4, BIND, nftables or netplan. Re-apply those separately after the
            restore.
          </Alert>
          <Field label="Restore mode" hint="Replace clears conflicting configuration rows first. Merge updates and adds rows.">
            <Select
              value={restoreMode}
              onChange={(event) => setRestoreMode(event.target.value as "merge" | "replace")}
            >
              <option value="merge">Merge</option>
              <option value="replace">Replace</option>
            </Select>
          </Field>
          <p className="flex items-start gap-2 text-sm text-slate-600 dark:text-slate-300">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
            Review the backup content and checksum before approving the restore request.
          </p>
        </div>
      </ConfirmDialog>

      <ConfirmDialog
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => void confirmDelete()}
        title={`Delete backup ${deleteTarget?.name ?? ""}?`}
        description="Removes the database row and the stored file. Automatic backups are protected."
        confirmLabel="Delete backup"
        danger
        loading={busy === "delete"}
      />

      <ChangeDialog
        workflow={workflow}
        title="Backup restore change"
        onUpdated={() => {
          void backups.refresh();
        }}
      />
    </div>
  );
}
