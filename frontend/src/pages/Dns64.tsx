import { useEffect, useRef, useState } from "react";
import { CheckCircle2, FlaskConical, Globe2, RefreshCw, Save, Server } from "lucide-react";
import { api, errorText } from "../lib/api";
import { formatRelative, titleCase } from "../lib/format";
import type { DNS64Settings } from "../lib/types";
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
  PageHeader,
  Spinner,
  StatCard,
  Textarea,
  TextInput,
  Toggle,
} from "../components/ui";
import { ChangeDialog, useChangeWorkflow } from "../components/ChangeWorkflow";

interface DnsResponse {
  settings: DNS64Settings;
  host: { available: boolean; mode: string; reason: string | null };
  defaults: {
    upstreams: string[];
    listen_address: string;
    service_name: string;
    managed_config_path: string;
    test_domain: string;
  };
  warnings: string[];
}

interface DnsStatus {
  status: string;
  active: boolean;
  service?: string;
  answers?: string[];
  message?: string | null;
  checked_at?: string;
}

interface TestResult {
  domain: string;
  prefix: string;
  listen_address: string;
  synthesis: {
    status: string;
    synthesized: boolean | null;
    answers?: string[];
    message?: string | null;
  };
  ipv4_resolution: { status: string; addresses?: string[]; message?: string | null };
  note: string;
}

interface FormState {
  enabled: boolean;
  prefix: string;
  upstreams: string;
  listen_address: string;
  service_name: "named" | "bind9";
  managed_config_path: string;
  test_domain: string;
  exclude_prefixes: string;
  forward_only: boolean;
  dnssec_validation: boolean;
}

const EMPTY_FORM: FormState = {
  enabled: true,
  prefix: "64:ff9b::/96",
  upstreams: "",
  listen_address: "127.0.0.1",
  service_name: "named",
  managed_config_path: "",
  test_domain: "ipv4only.arpa",
  exclude_prefixes: "",
  forward_only: true,
  dnssec_validation: true,
};

function toForm(settings: DNS64Settings): FormState {
  return {
    enabled: settings.enabled,
    prefix: settings.prefix,
    upstreams: (settings.upstreams ?? []).join("\n"),
    listen_address: settings.listen_address,
    service_name: settings.service_name === "bind9" ? "bind9" : "named",
    managed_config_path: settings.managed_config_path,
    test_domain: settings.test_domain,
    exclude_prefixes: (settings.exclude_prefixes ?? []).join("\n"),
    forward_only: settings.forward_only,
    dnssec_validation: settings.dnssec_validation,
  };
}

function splitLines(value: string): string[] {
  return value
    .split(/[\s,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export default function Dns64() {
  const toast = useToast();
  const { can } = useAuth();
  const canWrite = can("dns64.write");
  const canRequest = can("changes.request");
  const workflow = useChangeWorkflow();

  const initial = usePolling<DnsResponse>("/dns64", undefined, { interval: 0 });
  const status = usePolling<DnsStatus>("/dns64/status", undefined, { interval: 15000 });
  const stats = usePolling<Record<string, unknown>>("/dns64/stats", undefined, { interval: 60000 });

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [initialised, setInitialised] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const seeded = useRef(false);

  useEffect(() => {
    if (initial.data && !seeded.current) {
      seeded.current = true;
      setForm(toForm(initial.data.settings));
      setPreview(initial.data.settings.preview);
      setInitialised(true);
    }
  }, [initial.data]);

  const payload = () => ({
    enabled: form.enabled,
    prefix: form.prefix.trim(),
    upstreams: splitLines(form.upstreams),
    listen_address: form.listen_address.trim(),
    service_name: form.service_name,
    managed_config_path: form.managed_config_path.trim(),
    test_domain: form.test_domain.trim(),
    exclude_prefixes: splitLines(form.exclude_prefixes),
    forward_only: form.forward_only,
    dnssec_validation: form.dnssec_validation,
  });

  const runPreview = async () => {
    setBusy("preview");
    try {
      const result = await api.post<{ content: string; hash: string; validated_on_host: boolean }>(
        "/dns64/preview",
        payload(),
      );
      setPreview(result.content);
      toast.success("Configuration rendered", "Syntax is validated on the host during the change dry run.");
    } catch (error) {
      toast.error("Preview failed", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const runTest = async () => {
    setBusy("test");
    setTestResult(null);
    try {
      const result = await api.post<TestResult>("/dns64/test", { domain: form.test_domain.trim() || null });
      setTestResult(result);
      if (result.synthesis.synthesized) {
        toast.success("DNS64 synthesis verified", `${result.domain} answered inside ${result.prefix}.`);
      } else {
        toast.warning(
          "No synthesis observed",
          result.synthesis.message ?? "The test did not return an AAAA answer inside the configured prefix.",
        );
      }
    } catch (error) {
      toast.error("DNS64 test failed", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const openApply = async () => {
    setBusy("apply");
    try {
      await workflow.submit("/dns64/apply", payload());
    } catch (error) {
      toast.error("Could not create change request", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const settings = initial.data?.settings;
  const hostAvailable = initial.data?.host.available ?? false;
  const warnings = initial.data?.warnings ?? [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="DNS64"
        breadcrumb="NAT64"
        description="Bind 9 DNS64 fragment that synthesises AAAA records inside the NAT64 prefix. Configuration changes are applied through an approved change request."
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                seeded.current = false;
                setInitialised(false);
                void initial.refresh();
                void status.refresh();
              }}
              loading={initial.loading}
            >
              <RefreshCw className="h-4 w-4" /> Reload
            </Button>
            {canWrite ? (
              <Button variant="outline" size="sm" onClick={() => void runPreview()} loading={busy === "preview"}>
                <Server className="h-4 w-4" /> Preview config
              </Button>
            ) : null}
            {canRequest ? (
              <Button
                variant="primary"
                size="sm"
                onClick={() => void openApply()}
                loading={busy === "apply"}
                disabled={!initialised}
              >
                <Save className="h-4 w-4" /> Apply via change
              </Button>
            ) : null}
          </>
        }
      />

      {initial.error ? (
        <Alert tone="error" title="DNS64 configuration unavailable">
          {initial.error}
        </Alert>
      ) : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Service status"
          value={titleCase(status.data?.status ?? settings?.last_status ?? "unknown")}
          hint={status.data?.message ?? settings?.last_error ?? "No reported error"}
          icon={<Globe2 className="h-5 w-5" />}
          tone={
            status.data?.status === "online"
              ? "green"
              : status.data?.status === "unsupported"
                ? "violet"
                : "amber"
          }
        />
        <StatCard
          label="NAT64 prefix"
          value={<code className="text-lg">{settings?.prefix ?? "—"}</code>}
          hint={form.enabled ? "DNS64 enabled" : "DNS64 disabled"}
          icon={<CheckCircle2 className="h-5 w-5" />}
          tone={form.enabled ? "green" : "slate"}
        />
        <StatCard
          label="Last synthesis test"
          value={
            settings?.last_synthesis_ok == null ? "Not run" : settings.last_synthesis_ok ? "Passed" : "Failed"
          }
          hint={formatRelative(settings?.last_synthesis_at)}
          icon={<FlaskConical className="h-5 w-5" />}
          tone={settings?.last_synthesis_ok ? "green" : settings?.last_synthesis_ok === false ? "red" : "slate"}
        />
        <StatCard
          label="Config version"
          value={settings?.config_version ?? 0}
          hint={`Applied ${formatRelative(settings?.applied_at)}`}
          icon={<Server className="h-5 w-5" />}
        />
      </div>

      {status.data?.status === "unsupported" ? (
        <Alert tone="warning" title="BIND service is not observable from this server">
          {status.data.message ??
            "The privileged helper is unavailable. The form still edits the stored configuration and can render a preview, but apply cannot execute."}
        </Alert>
      ) : null}

      {warnings.map((warning, index) => (
        <Alert key={index} tone="info">
          {warning}
        </Alert>
      ))}

      {initial.loading && !initial.data ? <Spinner label="Loading DNS64 settings" /> : null}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader
            title="DNS64 settings"
            description="These values are stored in the database. Apply writes the managed BIND fragment and reloads named."
            icon={<Globe2 className="h-4 w-4" />}
          />
          <div className="space-y-4 p-5">
            <Toggle
              checked={form.enabled}
              onChange={(enabled) => setForm({ ...form, enabled })}
              label="DNS64 enabled"
              description="When disabled the rendered fragment omits the dns64 block."
            />
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="NAT64 prefix" required hint="Use /96 unless Jool uses another prefix length.">
                <TextInput
                  value={form.prefix}
                  onChange={(event) => setForm({ ...form, prefix: event.target.value })}
                  placeholder="64:ff9b::/96"
                />
              </Field>
              <Field label="Test domain" required hint="RFC 7050 test name.">
                <TextInput
                  value={form.test_domain}
                  onChange={(event) => setForm({ ...form, test_domain: event.target.value })}
                  placeholder="ipv4only.arpa"
                />
              </Field>
              <Field label="Listen address" required>
                <TextInput
                  value={form.listen_address}
                  onChange={(event) => setForm({ ...form, listen_address: event.target.value })}
                  placeholder="127.0.0.1"
                />
              </Field>
              <Field label="Service name" required>
                <select
                  className="input"
                  value={form.service_name}
                  onChange={(event) =>
                    setForm({ ...form, service_name: event.target.value as "named" | "bind9" })
                  }
                >
                  <option value="named">named</option>
                  <option value="bind9">bind9</option>
                </select>
              </Field>
            </div>
            <Field label="Managed config path" required hint="Included inside the options { } block of named.conf.options.">
              <TextInput
                value={form.managed_config_path}
                onChange={(event) => setForm({ ...form, managed_config_path: event.target.value })}
                placeholder="/etc/bind/nat64-manager.conf"
              />
            </Field>
            <Field label="Upstream resolvers" required hint="One per line or comma separated. Loopback resolvers may cause a loop.">
              <Textarea
                value={form.upstreams}
                onChange={(event) => setForm({ ...form, upstreams: event.target.value })}
                className="min-h-[90px]"
                placeholder={"1.1.1.1\n8.8.8.8"}
              />
            </Field>
            <Field label="Exclude prefixes" hint="RFC 6052 exclusions and internal ranges. One per line.">
              <Textarea
                value={form.exclude_prefixes}
                onChange={(event) => setForm({ ...form, exclude_prefixes: event.target.value })}
                className="min-h-[90px]"
              />
            </Field>
            <Toggle
              checked={form.forward_only}
              onChange={(forward_only) => setForm({ ...form, forward_only })}
              label="Forward only"
              description="Prevents the resolver from attempting recursive lookups outside the configured forwarders."
            />
            <Toggle
              checked={form.dnssec_validation}
              onChange={(dnssec_validation) => setForm({ ...form, dnssec_validation })}
              label="DNSSEC validation"
              description="Synthesised answers are not DNSSEC-validated; upstream validation still applies."
            />
          </div>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader
              title="Synthesis verification"
              description="Runs a live AAAA query through the configured resolver and checks that the answer lies inside the NAT64 prefix."
              icon={<FlaskConical className="h-4 w-4" />}
              actions={
                <Button variant="outline" size="sm" onClick={() => void runTest()} loading={busy === "test"}>
                  Run test
                </Button>
              }
            />
            <div className="space-y-4 p-5">
              {testResult ? (
                <>
                  <KeyValue
                    items={[
                      { label: "Domain", value: <code>{testResult.domain}</code> },
                      { label: "Prefix", value: <code>{testResult.prefix}</code> },
                      {
                        label: "Synthesis",
                        value: (
                          <Badge status={testResult.synthesis.synthesized ? "ok" : "failed"} dot>
                            {testResult.synthesis.synthesized
                              ? "Synthesised"
                              : titleCase(testResult.synthesis.status)}
                          </Badge>
                        ),
                      },
                      { label: "Answers", value: (testResult.synthesis.answers ?? []).join(", ") || "—" },
                      {
                        label: "IPv4 resolution",
                        value: titleCase(testResult.ipv4_resolution.status),
                      },
                    ]}
                  />
                  {testResult.synthesis.message ? (
                    <Alert tone="warning">{testResult.synthesis.message}</Alert>
                  ) : null}
                  <p className="text-xs text-slate-500 dark:text-slate-400">{testResult.note}</p>
                </>
              ) : (
                <p className="text-sm text-slate-500 dark:text-slate-400">
                  No live test has been run in this browser session. A result of “unsupported” means the host
                  helper is unreachable, not that DNS64 works.
                </p>
              )}
            </div>
          </Card>

          <Card>
            <CardHeader title="Query statistics" description="Parsed from the BIND statistics channel when available." />
            <div className="p-5">
              {stats.data && Object.keys(stats.data).length ? (
                <pre className="code-block max-h-64">{JSON.stringify(stats.data, null, 2)}</pre>
              ) : (
                <p className="text-sm text-slate-500 dark:text-slate-400">
                  {String(
                    (stats.data as { message?: string } | null)?.message ??
                      "No statistics are available. A server without host access returns an unsupported status rather than synthetic counters.",
                  )}
                </p>
              )}
            </div>
          </Card>
        </div>
      </div>

      <Card>
        <CardHeader
          title="Rendered managed fragment"
          description="Preview generated from the current form values. It is validated on the host during the change dry run."
          icon={<Server className="h-4 w-4" />}
        />
        <div className="p-5">
          <CodeBlock title={settings?.managed_config_path}>{preview ?? settings?.preview ?? ""}</CodeBlock>
          <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">{settings?.managed_file_note}</p>
        </div>
      </Card>

      {!hostAvailable ? (
        <Alert tone="info" title="Host state is unavailable">
          {initial.data?.host.reason ??
            "The manager cannot write BIND configuration on this server. Use the preview and change workflow for a Linux host with the privileged helper installed."}
        </Alert>
      ) : null}

      <ChangeDialog
        workflow={workflow}
        title="DNS64 apply change"
        onUpdated={() => {
          seeded.current = false;
          void initial.refresh();
          void status.refresh();
        }}
      />
    </div>
  );
}
