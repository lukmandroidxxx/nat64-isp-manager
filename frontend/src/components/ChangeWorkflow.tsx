import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardCheck,
  Play,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { api, errorDetails, errorText } from "../lib/api";
import type { ChangeRequest } from "../lib/types";
import { formatDate, prettyJson, titleCase } from "../lib/format";
import { useAuth } from "../state/AuthContext";
import { useToast } from "../state/ToastContext";
import { Alert, Badge, Button, CodeBlock, Field, KeyValue, Modal, Textarea } from "./ui";

interface WorkflowState {
  change: ChangeRequest | null;
  open: boolean;
  submit: (path: string, payload: unknown) => Promise<ChangeRequest>;
  show: (change: ChangeRequest) => void;
  close: () => void;
}

export function useChangeWorkflow(): WorkflowState {
  const [change, setChange] = useState<ChangeRequest | null>(null);
  const [open, setOpen] = useState(false);

  const submit = useCallback(async (path: string, payload: unknown) => {
    const created = await api.post<ChangeRequest>(path, payload);
    setChange(created);
    setOpen(true);
    return created;
  }, []);

  const show = useCallback((created: ChangeRequest) => {
    setChange(created);
    setOpen(true);
  }, []);

  return { change, open, submit, show, close: () => setOpen(false) };
}

export function ChangeDialog({
  workflow,
  title,
  onUpdated,
}: {
  workflow: { change: ChangeRequest | null; open: boolean; close: () => void };
  title?: string;
  onUpdated?: (change: ChangeRequest) => void;
}) {
  const { change, open, close } = workflow;
  const { user, can } = useAuth();
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [local, setLocal] = useState<ChangeRequest | null>(change);

  useEffect(() => {
    setLocal(change);
  }, [change]);

  const current = local ?? change;

  const act = async (operation: string) => {
    if (!current) return;
    setBusy(operation);
    try {
      let updated: ChangeRequest;
      if (operation === "dry-run") {
        updated = await api.post<ChangeRequest>(`/changes/${current.id}/dry-run`);
      } else if (operation === "approve") {
        updated = await api.post<ChangeRequest>(`/changes/${current.id}/approve`);
      } else if (operation === "execute") {
        updated = await api.post<ChangeRequest>(`/changes/${current.id}/execute`);
      } else if (operation === "cancel") {
        updated = await api.post<ChangeRequest>(`/changes/${current.id}/cancel`);
      } else if (operation === "reject") {
        updated = await api.post<ChangeRequest>(`/changes/${current.id}/reject`, {
          reason: reason || undefined,
        });
        setRejectOpen(false);
      } else {
        throw new Error("Unknown operation");
      }
      setLocal(updated);
      onUpdated?.(updated);
      if (updated.status === "applied") {
        toast.success("Change applied", `${updated.action} was executed successfully.`);
      } else if (updated.status === "failed" || updated.status === "dry_run_failed") {
        toast.error("Change did not succeed", updated.error ?? "Review the plan details.");
      } else {
        toast.info("Change updated", `${updated.action} is now ${titleCase(updated.status)}.`);
      }
    } catch (error) {
      toast.error("Change action failed", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  if (!current) {
    return (
      <Modal open={open} onClose={close} title={title ?? "Change request"} size="lg">
        <p className="text-sm text-slate-500">No change request selected.</p>
      </Modal>
    );
  }

  const plan = current.plan;
  const hostUnavailable = plan?.host_available === false;
  const criticalSelfApproval =
    current.risk === "critical" && current.requested_by === user?.username && !user?.is_superuser;

  const canApprove = can("changes.approve") && current.status === "pending_approval" && !criticalSelfApproval;
  const canExecute = can("changes.execute") && current.status === "approved" && plan?.valid !== false && !hostUnavailable;
  const canRetry = can("changes.request") && ["dry_run_failed", "pending_approval"].includes(current.status);
  const canReject = can("changes.approve") && ["pending_approval", "approved"].includes(current.status);
  const canCancel = can("changes.request") && !["applied", "failed", "cancelled", "rejected"].includes(current.status);

  return (
    <Modal
      open={open}
      onClose={close}
      title={title ?? titleCase(current.action)}
      description={
        <span className="flex flex-wrap items-center gap-2">
          <Badge status={current.status} dot /> <span className="text-slate-400">·</span>
          <span>Risk: {titleCase(current.risk)}</span> <span className="text-slate-400">·</span>
          <span>Requested by {current.requested_by}</span>
        </span>
      }
      size="lg"
      footer={
        <>
          <Button variant="outline" onClick={close} disabled={busy !== null}>
            Close
          </Button>
          {canRetry ? (
            <Button variant="secondary" onClick={() => act("dry-run")} loading={busy === "dry-run"}>
              <RefreshCw className="h-4 w-4" /> Re-run dry run
            </Button>
          ) : null}
          {canReject ? (
            <Button variant="danger" onClick={() => setRejectOpen(true)} disabled={busy !== null}>
              <XCircle className="h-4 w-4" /> Reject
            </Button>
          ) : null}
          {canCancel ? (
            <Button variant="secondary" onClick={() => act("cancel")} loading={busy === "cancel"}>
              Cancel
            </Button>
          ) : null}
          {canApprove ? (
            <Button variant="primary" onClick={() => act("approve")} loading={busy === "approve"}>
              <ShieldCheck className="h-4 w-4" /> Approve
            </Button>
          ) : null}
          {canExecute ? (
            <Button variant="success" onClick={() => act("execute")} loading={busy === "execute"}>
              <Play className="h-4 w-4" /> Execute
            </Button>
          ) : null}
        </>
      }
    >
      <div className="space-y-5">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge status={current.status}>{titleCase(current.status)}</Badge>
          {plan?.reversible ? (
            <span className="text-xs text-slate-500 dark:text-slate-400">Reversible</span>
          ) : (
            <Badge tone="amber">Not reversible</Badge>
          )}
          {plan?.requires_maintenance ? <Badge tone="amber">Maintenance window</Badge> : null}
          {current.expires_at ? (
            <span className="text-xs text-slate-500 dark:text-slate-400">
              Expires {formatDate(current.expires_at)}
            </span>
          ) : null}
        </div>

        {hostUnavailable ? (
          <Alert tone="warning" title="Host integration is unavailable on this server">
            This plan was validated against server configuration only. Nothing has been executed, and
            execution is disabled until the plan can be produced with a reachable Linux host helper.
            {plan?.errors?.length ? <p className="mt-1">{plan.errors.join("; ")}</p> : null}
          </Alert>
        ) : null}

        {plan?.errors?.length ? (
          <Alert tone="error" title="Plan errors">
            <ul className="list-disc space-y-1 pl-4">
              {plan.errors.map((error, index) => (
                <li key={index}>{error}</li>
              ))}
            </ul>
          </Alert>
        ) : null}

        {plan?.warnings?.length ? (
          <Alert tone="warning" title="Warnings">
            <ul className="list-disc space-y-1 pl-4">
              {plan.warnings.map((warning, index) => (
                <li key={index}>{warning}</li>
              ))}
            </ul>
          </Alert>
        ) : null}

        {criticalSelfApproval && current.status === "pending_approval" ? (
          <Alert tone="error" title="A different administrator must approve this critical change">
            You requested this critical change. A second administrator with approve permission must
            review and approve it.
          </Alert>
        ) : null}

        {current.error ? (
          <Alert tone={current.status === "applied" ? "info" : "error"} title="Change message">
            {current.error}
          </Alert>
        ) : null}

        <div className="rounded-xl border border-slate-200 p-4 dark:border-slate-800">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-800 dark:text-slate-200">
            <ClipboardCheck className="h-4 w-4 text-brand-500" /> Plan
          </h3>
          <KeyValue
            items={[
              { label: "Action", value: <code>{current.action}</code> },
              { label: "Target", value: current.target ?? "—" },
              { label: "Summary", value: current.summary ?? "—" },
              { label: "Valid", value: plan ? (plan.valid ? "Yes" : "No") : "—" },
              { label: "Requested", value: formatDate(current.requested_at) },
              { label: "Approved by", value: current.approved_by ?? "—" },
            ]}
          />
        </div>

        {plan?.commands?.length ? (
          <CodeBlock title="Commands executed by the privileged helper">{plan.commands.map((cmd) => cmd.join(" ")).join("\n")}</CodeBlock>
        ) : null}

        {plan?.before && Object.keys(plan.before).length ? (
          <CodeBlock title="Before">{prettyJson(plan.before)}</CodeBlock>
        ) : null}
        {plan?.after && Object.keys(plan.after).length ? (
          <CodeBlock title="After">{prettyJson(plan.after)}</CodeBlock>
        ) : null}
        {plan?.details && Object.keys(plan.details).length ? (
          <details className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
            <summary className="cursor-pointer text-sm font-medium text-slate-700 dark:text-slate-300">
              Raw plan details
            </summary>
            <pre className="code-block mt-3">{prettyJson(plan.details)}</pre>
          </details>
        ) : null}

        {current.result && Object.keys(current.result).length ? (
          <div>
            <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-800 dark:text-slate-200">
              {current.status === "applied" ? (
                <CheckCircle2 className="h-4 w-4 text-emerald-500" />
              ) : (
                <AlertTriangle className="h-4 w-4 text-amber-500" />
              )}
              Result
            </h3>
            <pre className="code-block">{prettyJson(current.result)}</pre>
          </div>
        ) : null}

        {plan?.host_available === true && current.status === "approved" ? (
          <Alert tone="info" title="Execute only after reviewing the plan">
            Execution creates an automatic pre-change configuration backup, then runs the allowlisted
            helper operations. A failure is recorded in the audit log.
          </Alert>
        ) : null}

        {current.status === "dry_run_failed" ? (
          <Alert tone="warning" title="Dry run failed">
            The request was saved for the audit trail, but it cannot be approved or executed in this
            state. Fix the underlying problem and create a new request.
          </Alert>
        ) : null}
      </div>

      <Modal
        open={rejectOpen}
        onClose={() => setRejectOpen(false)}
        title="Reject change request"
        size="sm"
        footer={
          <>
            <Button variant="outline" onClick={() => setRejectOpen(false)}>
              Cancel
            </Button>
            <Button variant="danger" onClick={() => act("reject")} loading={busy === "reject"}>
              Reject request
            </Button>
          </>
        }
      >
        <Field label="Reason" hint="Recorded in the audit log.">
          <Textarea value={reason} onChange={(event) => setReason(event.target.value)} maxLength={4000} />
        </Field>
        <div className="mt-3">
          <Alert tone="warning">
            <ShieldAlert className="mr-1 inline h-4 w-4" /> Rejecting is final for this request.
          </Alert>
        </div>
      </Modal>
    </Modal>
  );
}

export function planErrorLines(error: unknown): string[] {
  return errorDetails(error);
}
