import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  BookOpenCheck,
  CheckCircle2,
  ChevronRight,
  Circle,
  CircleDashed,
  ListChecks,
  Play,
  RefreshCw,
  RotateCcw,
  Save,
  ServerCog,
  ShieldCheck,
} from "lucide-react";
import { api, errorText } from "../lib/api";
import { formatDate, formatRelative, prettyJson, titleCase } from "../lib/format";
import type { WizardCheck, WizardCheckSummary, WizardState } from "../lib/types";
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
  Field,
  KeyValue,
  PageHeader,
  ProgressBar,
  Spinner,
  StatCard,
  Textarea,
  cn,
} from "../components/ui";
import { DataTable } from "../components/DataTable";

const STEP_LINKS: Record<string, { to: string; label: string }> = {
  host: { to: "/monitoring", label: "System Monitoring" },
  database: { to: "/settings", label: "Settings" },
  interfaces: { to: "/network", label: "Network Interfaces" },
  prefix: { to: "/network", label: "Network Interfaces" },
  pool4: { to: "/pools", label: "IPv4 Pool" },
  jool: { to: "/jool", label: "Jool Management" },
  pool4_sync: { to: "/jool", label: "Jool Management" },
  dns64: { to: "/dns64", label: "DNS64" },
  firewall: { to: "/firewall", label: "Firewall" },
  security: { to: "/settings", label: "Settings" },
  verification: { to: "/changes", label: "Change Requests" },
};

function checkTone(status: string | undefined): "green" | "amber" | "red" | "violet" | "slate" {
  if (status === "ok") return "green";
  if (status === "warning") return "amber";
  if (status === "failed") return "red";
  if (status === "unsupported") return "violet";
  return "slate";
}

function checkIcon(status: string | undefined) {
  if (status === "ok") return <CheckCircle2 className="h-4 w-4 text-emerald-500" />;
  if (status === "warning") return <AlertTriangle className="h-4 w-4 text-amber-500" />;
  if (status === "failed") return <AlertTriangle className="h-4 w-4 text-rose-500" />;
  if (status === "unsupported") return <CircleDashed className="h-4 w-4 text-violet-500" />;
  return <Circle className="h-4 w-4 text-slate-400" />;
}

export default function Wizard() {
  const toast = useToast();
  const { can } = useAuth();
  const canWrite = can("settings.write");

  const wizard = usePolling<WizardState>("/wizard", undefined, { interval: 0 });

  const [selected, setSelected] = useState(1);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [resetOpen, setResetOpen] = useState(false);

  const state = wizard.data;
  const definitions = state?.definitions ?? [];
  const checks = state?.last_check?.checks ?? [];
  const selectedDefinition = definitions.find((item) => item.id === selected) ?? definitions[0];
  const selectedCheck = selectedDefinition
    ? checks.find((item) => item.id === selectedDefinition.id)
    : undefined;

  useEffect(() => {
    const saved = state?.steps?.[String(selected)] ?? {};
    setNotes(typeof saved.notes === "string" ? saved.notes : "");
  }, [selected, state?.steps]);

  const okCount = useMemo(() => checks.filter((item) => item.status === "ok").length, [checks]);
  const warningCount = checks.filter((item) => item.status === "warning").length;
  const requiredFailed = state?.last_check?.required_failed ?? 0;
  const complete = Boolean(state?.completed);
  const progress = definitions.length ? (okCount / definitions.length) * 100 : 0;

  const refresh = () => {
    void wizard.refresh();
  };

  const runChecks = async () => {
    setBusy("check");
    try {
      const result = await api.post<WizardCheckSummary>("/wizard/check");
      // Apply the result we just received instead of re-reading immediately. The
      // API commits its unit of work as the request finishes, so an instant GET
      // can still observe the previous check (and would show every step as
      // "Not checked" until a manual reload).
      if (state) {
        wizard.setData({
          ...state,
          current_step: result.current_step ?? state.current_step,
          last_check: result,
          completed: result.complete ? state.completed : false,
          completed_at: result.complete ? state.completed_at : null,
        });
      } else {
        await wizard.refresh();
      }
      if (result.current_step) setSelected(result.current_step);
      if (result.complete) {
        toast.success("All required checks passed", "You can now mark the installation complete.");
      } else {
        toast.info(
          "Checks completed",
          `${result.required_failed ?? 0} required step(s) failed or are unsupported on this host.`,
        );
      }
    } catch (error) {
      toast.error("Could not run installation checks", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const saveStep = async () => {
    setBusy("save");
    try {
      const updated = await api.post<WizardState>("/wizard/step", { step: selected, data: { notes } });
      wizard.setData(updated);
      toast.success("Step saved", `Step ${selected} notes were stored and audited.`);
    } catch (error) {
      toast.error("Could not save step", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const completeWizard = async () => {
    setBusy("complete");
    try {
      const updated = await api.post<WizardState>("/wizard/complete");
      wizard.setData(updated);
      toast.success("Installation complete", "The installation state was marked complete.");
    } catch (error) {
      toast.error("Cannot complete installation", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const resetWizard = async () => {
    setBusy("reset");
    try {
      const updated = await api.post<WizardState>("/wizard/reset");
      wizard.setData(updated);
      setSelected(1);
      setResetOpen(false);
      toast.success("Installation wizard reset", "Step notes and check results were cleared.");
    } catch (error) {
      toast.error("Could not reset wizard", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const definitionChecks = definitions.map((definition) => {
    const check = checks.find((item) => item.id === definition.id);
    const saved = state?.steps?.[String(definition.id)];
    return { definition, check, saved };
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Installation Wizard"
        description="Eleven guided steps for a production NAT64 gateway. Checks read the real database and privileged host helper; unsupported checks are reported as unsupported, never as passed."
        breadcrumb="Administration"
        actions={
          <>
            <Button variant="outline" size="sm" onClick={refresh} loading={wizard.loading}>
              <RefreshCw className="h-4 w-4" /> Reload
            </Button>
            <Button variant="secondary" size="sm" onClick={() => void runChecks()} loading={busy === "check"} disabled={!canWrite}>
              <Play className="h-4 w-4" /> Run all checks
            </Button>
            <Button
              variant="success"
              size="sm"
              onClick={() => void completeWizard()}
              loading={busy === "complete"}
              disabled={!canWrite || !state?.last_check?.complete || complete}
            >
              <CheckCircle2 className="h-4 w-4" /> Mark complete
            </Button>
          </>
        }
      />

      {wizard.error ? (
        <Alert tone="error" title="Installation state unavailable">
          {wizard.error}
        </Alert>
      ) : null}

      {state && !state.host_available ? (
        <Alert tone="warning" title="Privileged host integration is unavailable">
          Host mode is <code>{state.host_mode}</code>. Checks that need Jool, BIND, nftables, netplan or
          keepalived will report unsupported instead of passing. Run the wizard on the target Linux gateway
          for a meaningful installation verification.
        </Alert>
      ) : null}

      {complete ? (
        <Alert tone="success" title="Installation marked complete">
          Completed {formatDate(state?.completed_at)}. Re-running checks can invalidate the completed state if
          a required step starts failing.
        </Alert>
      ) : null}

      {requiredFailed > 0 ? (
        <Alert tone="error" title={`${requiredFailed} required check(s) need attention`}>
          The installation cannot be marked complete until every required step passes. Optional steps, such
          as the managed firewall, can remain warnings.
        </Alert>
      ) : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Current step"
          value={state ? `${state.current_step} / ${definitions.length || 11}` : "—"}
          hint={selectedDefinition?.title ?? "Loading wizard"}
          icon={<BookOpenCheck className="h-5 w-5" />}
          tone="blue"
        />
        <StatCard
          label="Checks passing"
          value={`${okCount}/${definitions.length || 11}`}
          hint={`${warningCount} warning(s) · ${requiredFailed} required failure(s)`}
          icon={<ListChecks className="h-5 w-5" />}
          tone={requiredFailed > 0 ? "red" : okCount > 0 ? "green" : "slate"}
        />
        <StatCard
          label="Last check"
          value={state?.last_check?.checked_at ? formatRelative(state.last_check.checked_at) : "Never"}
          hint={
            state?.last_check?.checked_at
              ? formatDate(state.last_check.checked_at)
              : "Run all checks to collect real results"
          }
          icon={<Play className="h-5 w-5" />}
        />
        <StatCard
          label="Host mode"
          value={titleCase(state?.host_mode ?? "unknown")}
          hint={state?.host_available ? "Privileged helper reachable" : "Host integration unavailable"}
          icon={<ServerCog className="h-5 w-5" />}
          tone={state?.host_available ? "green" : "slate"}
        />
      </div>

      <Card>
        <div className="flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0 flex-1">
            <ProgressBar
              value={progress}
              tone={requiredFailed > 0 ? "red" : progress >= 100 ? "green" : "blue"}
              label={`Installation progress · ${okCount} of ${definitions.length || 11} checks passing`}
            />
          </div>
          <div className="flex shrink-0 items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
            <ShieldCheck className="h-4 w-4" /> Checks are read-only
            <Button variant="ghost" size="sm" onClick={() => setResetOpen(true)} disabled={!canWrite}>
              <RotateCcw className="h-4 w-4" /> Reset
            </Button>
          </div>
        </div>
      </Card>

      {wizard.loading && !state ? <Spinner label="Loading installation wizard" /> : null}

      {state ? (
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[22rem_1fr]">
          <Card className="self-start">
            <CardHeader
              title="Steps"
              description="Required steps must pass. Optional warnings do not block completion."
              icon={<BookOpenCheck className="h-4 w-4" />}
            />
            <ol className="divide-y divide-slate-100 dark:divide-slate-800">
              {definitionChecks.map(({ definition, check, saved }) => {
                const status = check?.status;
                return (
                  <li key={definition.id}>
                    <button
                      type="button"
                      onClick={() => setSelected(definition.id)}
                      className={cn(
                        "flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/60",
                        selected === definition.id && "bg-brand-50 dark:bg-brand-950/40",
                      )}
                    >
                      <span className="mt-0.5">{checkIcon(status)}</span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2">
                          <span className="text-xs font-semibold text-slate-800 dark:text-slate-100">
                            {definition.id}. {definition.title}
                          </span>
                          {definition.required ? null : <Badge tone="slate">Optional</Badge>}
                        </span>
                        <span className="mt-1 flex flex-wrap items-center gap-1.5">
                          <Badge tone={checkTone(status)}>{status ? titleCase(status) : saved ? "Saved" : "Not checked"}</Badge>
                          {saved ? <span className="text-[10px] text-slate-400">notes saved</span> : null}
                        </span>
                      </span>
                      <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-slate-300" />
                    </button>
                  </li>
                );
              })}
            </ol>
          </Card>

          <div className="space-y-6">
            {selectedDefinition ? (
              <>
                <Card>
                  <CardHeader
                    title={`${selectedDefinition.id}. ${selectedDefinition.title}`}
                    description={selectedDefinition.description}
                    icon={checkIcon(selectedCheck?.status)}
                    actions={
                      <Badge tone={selectedDefinition.required ? "blue" : "slate"}>
                        {selectedDefinition.required ? "Required" : "Optional"}
                      </Badge>
                    }
                  />
                  <div className="space-y-4 p-5">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={checkTone(selectedCheck?.status)} dot>
                        {selectedCheck ? titleCase(selectedCheck.status) : "Not checked"}
                      </Badge>
                      {state.last_check?.checked_at ? (
                        <span className="text-xs text-slate-500 dark:text-slate-400">
                          Last checked {formatRelative(state.last_check.checked_at)}
                        </span>
                      ) : null}
                      {STEP_LINKS[selectedDefinition.key] ? (
                        <Link
                          to={STEP_LINKS[selectedDefinition.key].to}
                          className="inline-flex items-center gap-1 text-xs font-medium text-brand-600 hover:underline dark:text-brand-400"
                        >
                          Open {STEP_LINKS[selectedDefinition.key].label} <ChevronRight className="h-3 w-3" />
                        </Link>
                      ) : null}
                    </div>

                    {selectedCheck ? (
                      <Alert
                        tone={
                          selectedCheck.status === "ok"
                            ? "success"
                            : selectedCheck.status === "warning"
                              ? "warning"
                              : selectedCheck.status === "unsupported"
                                ? "info"
                                : "error"
                        }
                        title={selectedCheck.title ?? selectedDefinition.title}
                      >
                        {selectedCheck.message}
                      </Alert>
                    ) : (
                      <Alert tone="info" title="This step has not been checked">
                        Run all checks to collect the current status. A check speaks to the real host and
                        database; it does not mark a step as passed by default.
                      </Alert>
                    )}

                    {selectedCheck?.detail && Object.keys(selectedCheck.detail).length ? (
                      <details className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
                        <summary className="cursor-pointer text-sm font-medium text-slate-700 dark:text-slate-300">
                          Check details
                        </summary>
                        <pre className="code-block mt-3">{prettyJson(selectedCheck.detail)}</pre>
                      </details>
                    ) : null}

                    <Field
                      label="Step notes"
                      hint="Stored in the installation state and audit log. Notes do not change the pass/fail result."
                    >
                      <Textarea
                        value={notes}
                        disabled={!canWrite}
                        maxLength={4000}
                        placeholder="Record what was installed, which interfaces were selected, or what remains to verify."
                        onChange={(event) => setNotes(event.target.value)}
                      />
                    </Field>
                    <div className="flex flex-wrap items-center gap-2">
                      <Button
                        variant="primary"
                        size="sm"
                        onClick={() => void saveStep()}
                        loading={busy === "save"}
                        disabled={!canWrite}
                      >
                        <Save className="h-4 w-4" /> Save step notes
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => void runChecks()}
                        loading={busy === "check"}
                        disabled={!canWrite}
                      >
                        <Play className="h-4 w-4" /> Run all checks
                      </Button>
                    </div>
                  </div>
                </Card>

                {selectedDefinition.id === 11 ? (
                  <Card>
                    <CardHeader
                      title="Verification summary"
                      description="Only the API's real check results can allow completion."
                      icon={<ListChecks className="h-4 w-4" />}
                    />
                    <DataTable
                      rows={checks}
                      rowKey={(row) => row.step}
                      dense
                      empty="No check results yet. Run all checks first."
                      columns={[
                        {
                          key: "step",
                          header: "Step",
                          render: (row) => (
                            <span className="text-xs font-semibold">
                              {row.id ?? row.step}. {row.title ?? row.key ?? ""}
                            </span>
                          ),
                        },
                        {
                          key: "status",
                          header: "Status",
                          render: (row) => (
                            <Badge tone={checkTone(row.status)} dot>
                              {titleCase(row.status)}
                            </Badge>
                          ),
                        },
                        {
                          key: "required",
                          header: "Required",
                          render: (row) => (
                            <Badge tone={row.required ? "blue" : "slate"}>{row.required ? "Yes" : "No"}</Badge>
                          ),
                        },
                        {
                          key: "message",
                          header: "Message",
                          render: (row) => (
                            <span className="block max-w-xl text-xs text-slate-600 dark:text-slate-300">
                              {row.message}
                            </span>
                          ),
                        },
                      ]}
                    />
                  </Card>
                ) : null}

                {state.steps?.[String(selectedDefinition.id)] ? (
                  <Card>
                    <CardHeader
                      title="Saved step data"
                      description="Last saved notes and metadata for this step."
                      icon={<Save className="h-4 w-4" />}
                    />
                    <div className="p-5">
                      <KeyValue
                        items={Object.entries(state.steps[String(selectedDefinition.id)]).map(([key, value]) => ({
                          label: key,
                          value: typeof value === "string" ? value : prettyJson(value),
                        }))}
                      />
                    </div>
                  </Card>
                ) : null}
              </>
            ) : (
              <Card>
                <div className="p-5">
                  <WizardCheckListFallback checks={checks} />
                </div>
              </Card>
            )}
          </div>
        </div>
      ) : null}

      <Alert tone="warning" title="What completion means">
        Marking the installation complete records that the required checks passed at that moment. It does not
        start Jool, BIND, nftables or keepalived, and it does not prove NAT64 forwarding, DNS64 synthesis or
        failover continuity. Those must be verified on a Linux lab or production host.
      </Alert>

      <ConfirmDialog
        open={resetOpen}
        onClose={() => setResetOpen(false)}
        onConfirm={() => void resetWizard()}
        title="Reset the installation wizard?"
        description="Clears step notes, check results, timestamps and the completed flag. Configuration itself is not changed."
        confirmLabel="Reset wizard"
        danger
        loading={busy === "reset"}
      />
    </div>
  );
}

function WizardCheckListFallback({ checks }: { checks: WizardCheck[] }) {
  if (!checks.length) {
    return <p className="text-sm text-slate-500 dark:text-slate-400">No check results are available.</p>;
  }
  return (
    <ul className="space-y-2">
      {checks.map((check) => (
        <li key={check.step} className="flex items-center justify-between gap-3 text-sm">
          <span>{check.title ?? check.key ?? `Step ${check.step}`}</span>
          <Badge tone={checkTone(check.status)}>{titleCase(check.status)}</Badge>
        </li>
      ))}
    </ul>
  );
}
