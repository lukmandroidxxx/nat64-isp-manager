import { useEffect, useRef, useState } from "react";
import {
  ArrowDownUp,
  ListFilter,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { api, errorText } from "../lib/api";
import { formatDate, formatRelative, titleCase } from "../lib/format";
import type { FirewallRule, FirewallSettings } from "../lib/types";
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
  Field,
  KeyValue,
  Modal,
  PageHeader,
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

interface FirewallResponse {
  settings: FirewallSettings;
  rules: FirewallRule[];
  status: { status: string; enabled: boolean; table_present: boolean; message?: string };
  scope: { managed_table: string; note: string };
  warnings: string[];
}

interface SettingsForm {
  enabled: boolean;
  default_forward_policy: "accept" | "drop";
  default_input_policy: "accept" | "drop";
  new_connection_rate: string;
  rate_burst: string;
  log_dropped: boolean;
  management_allowlist: string;
  management_ports: string;
  protect_control_plane: boolean;
  syn_flood_protection: boolean;
  invalid_packet_protection: boolean;
  mss_clamping: boolean;
}

interface RuleForm {
  name: string;
  description: string;
  enabled: boolean;
  priority: string;
  family: "inet" | "ipv4" | "ipv6";
  direction: "input" | "forward";
  action: "accept" | "drop" | "reject" | "log";
  protocol: "" | "tcp" | "udp" | "icmp" | "icmpv6";
  source_cidr: string;
  destination_cidr: string;
  source_port: string;
  destination_port: string;
  rate_limit_per_second: string;
  log: boolean;
}

const EMPTY_SETTINGS: SettingsForm = {
  enabled: false,
  default_forward_policy: "accept",
  default_input_policy: "accept",
  new_connection_rate: "500",
  rate_burst: "100",
  log_dropped: true,
  management_allowlist: "",
  management_ports: "22,443",
  protect_control_plane: true,
  syn_flood_protection: true,
  invalid_packet_protection: true,
  mss_clamping: false,
};

const EMPTY_RULE: RuleForm = {
  name: "",
  description: "",
  enabled: true,
  priority: "100",
  family: "inet",
  direction: "forward",
  action: "drop",
  protocol: "",
  source_cidr: "",
  destination_cidr: "",
  source_port: "",
  destination_port: "",
  rate_limit_per_second: "",
  log: false,
};

function toSettingsForm(settings: FirewallSettings): SettingsForm {
  return {
    enabled: settings.enabled,
    default_forward_policy: settings.default_forward_policy === "drop" ? "drop" : "accept",
    default_input_policy: settings.default_input_policy === "drop" ? "drop" : "accept",
    new_connection_rate: String(settings.new_connection_rate),
    rate_burst: String(settings.rate_burst),
    log_dropped: settings.log_dropped,
    management_allowlist: (settings.management_allowlist ?? []).join("\n"),
    management_ports: (settings.management_ports ?? []).join(","),
    protect_control_plane: settings.protect_control_plane,
    syn_flood_protection: settings.syn_flood_protection,
    invalid_packet_protection: settings.invalid_packet_protection,
    mss_clamping: settings.mss_clamping,
  };
}

function ruleToForm(rule: FirewallRule): RuleForm {
  return {
    name: rule.name,
    description: rule.description ?? "",
    enabled: rule.enabled,
    priority: String(rule.priority),
    family: (rule.family as RuleForm["family"]) ?? "inet",
    direction: (rule.direction as RuleForm["direction"]) ?? "forward",
    action: (rule.action as RuleForm["action"]) ?? "drop",
    protocol: (rule.protocol as RuleForm["protocol"]) ?? "",
    source_cidr: rule.source_cidr ?? "",
    destination_cidr: rule.destination_cidr ?? "",
    source_port: rule.source_port ? String(rule.source_port) : "",
    destination_port: rule.destination_port ? String(rule.destination_port) : "",
    rate_limit_per_second: "",
    log: rule.log,
  };
}

function splitCidrs(value: string): string[] {
  return value
    .split(/[\s,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export default function Firewall() {
  const toast = useToast();
  const { can } = useAuth();
  const canWrite = can("firewall.write");
  const canRequest = can("changes.request");
  const workflow = useChangeWorkflow();

  const initial = usePolling<FirewallResponse>("/firewall", undefined, { interval: 0 });
  const rules = usePolling<{ items: FirewallRule[] }>("/firewall/rules", undefined, { interval: 5000 });
  const preview = usePolling<{ content: string; hash: string | null; error: string | null }>(
    "/firewall/preview",
    undefined,
    { interval: 0 },
  );

  const [tab, setTab] = useState<"rules" | "settings">("rules");
  const [form, setForm] = useState<SettingsForm>(EMPTY_SETTINGS);
  const [initialised, setInitialised] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const seeded = useRef(false);

  const [ruleModal, setRuleModal] = useState(false);
  const [editingRule, setEditingRule] = useState<FirewallRule | null>(null);
  const [ruleForm, setRuleForm] = useState<RuleForm>(EMPTY_RULE);
  const [deleteRule, setDeleteRule] = useState<FirewallRule | null>(null);

  useEffect(() => {
    if (initial.data && !seeded.current) {
      seeded.current = true;
      setForm(toSettingsForm(initial.data.settings));
      setInitialised(true);
    }
  }, [initial.data]);

  const settingsPayload = () => ({
    enabled: form.enabled,
    default_forward_policy: form.default_forward_policy,
    default_input_policy: form.default_input_policy,
    new_connection_rate: Number(form.new_connection_rate),
    rate_burst: Number(form.rate_burst),
    log_dropped: form.log_dropped,
    management_allowlist: splitCidrs(form.management_allowlist),
    management_ports: splitCidrs(form.management_ports.replace(/,/g, " ")).map(Number),
    protect_control_plane: form.protect_control_plane,
    syn_flood_protection: form.syn_flood_protection,
    invalid_packet_protection: form.invalid_packet_protection,
    mss_clamping: form.mss_clamping,
  });

  const openApply = async () => {
    setBusy("apply");
    try {
      await workflow.submit("/firewall/apply", settingsPayload());
    } catch (error) {
      toast.error("Could not create change request", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const openCreateRule = () => {
    setEditingRule(null);
    setRuleForm(EMPTY_RULE);
    setRuleModal(true);
  };

  const openEditRule = (rule: FirewallRule) => {
    setEditingRule(rule);
    setRuleForm(ruleToForm(rule));
    setRuleModal(true);
  };

  const saveRule = async () => {
    setBusy("rule");
    try {
      const payload = {
        name: ruleForm.name.trim(),
        description: ruleForm.description.trim() || null,
        enabled: ruleForm.enabled,
        priority: Number(ruleForm.priority),
        family: ruleForm.family,
        direction: ruleForm.direction,
        action: ruleForm.action,
        protocol: ruleForm.protocol || null,
        source_cidr: ruleForm.source_cidr.trim() || null,
        destination_cidr: ruleForm.destination_cidr.trim() || null,
        source_port: ruleForm.source_port ? Number(ruleForm.source_port) : null,
        destination_port: ruleForm.destination_port ? Number(ruleForm.destination_port) : null,
        rate_limit_per_second: ruleForm.rate_limit_per_second
          ? Number(ruleForm.rate_limit_per_second)
          : null,
        log: ruleForm.log,
      };
      if (!payload.name) throw new Error("Rule name is required");
      if (editingRule) {
        await api.patch(`/firewall/rules/${editingRule.id}`, payload);
        toast.success("Rule updated", "Database only. Apply the firewall to reach nftables.");
      } else {
        await api.post("/firewall/rules", payload);
        toast.success("Rule created", "Database only. Apply the firewall to reach nftables.");
      }
      setRuleModal(false);
      await rules.refresh();
      void preview.refresh();
    } catch (error) {
      toast.error("Could not save rule", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const toggleRule = async (rule: FirewallRule) => {
    try {
      await api.post(`/firewall/rules/${rule.id}/enabled`, undefined, { enabled: !rule.enabled });
      await rules.refresh();
      void preview.refresh();
    } catch (error) {
      toast.error("Could not toggle rule", errorText(error));
    }
  };

  const confirmDeleteRule = async () => {
    if (!deleteRule) return;
    setBusy("delete");
    try {
      await api.delete(`/firewall/rules/${deleteRule.id}`);
      toast.success("Rule deleted", "Apply the firewall to remove it from nftables.");
      setDeleteRule(null);
      await rules.refresh();
      void preview.refresh();
    } catch (error) {
      toast.error("Could not delete rule", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const data = initial.data;
  const ruleRows = rules.data?.items ?? [];
  const enabledRules = ruleRows.filter((rule) => rule.enabled).length;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Firewall"
        breadcrumb="Network"
        description="Managed nftables table inet nat64mgr. Rules are stored in the database and only reach the host through an approved firewall.apply change."
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                seeded.current = false;
                setInitialised(false);
                void initial.refresh();
                void rules.refresh();
                void preview.refresh();
              }}
              loading={initial.loading}
            >
              <RefreshCw className="h-4 w-4" /> Reload
            </Button>
            {canWrite ? (
              <Button variant="primary" size="sm" onClick={openCreateRule}>
                <Plus className="h-4 w-4" /> New rule
              </Button>
            ) : null}
          </>
        }
      />

      {initial.error ? (
        <Alert tone="error" title="Firewall state unavailable">
          {initial.error}
        </Alert>
      ) : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Firewall status"
          value={titleCase(data?.status.status ?? "unknown")}
          hint={data?.status.table_present ? "Managed table present in nftables" : "Managed table not observed"}
          icon={<Shield className="h-5 w-5" />}
          tone={data?.status.enabled ? "green" : data?.status.status === "unsupported" ? "violet" : "slate"}
        />
        <StatCard
          label="Rules"
          value={ruleRows.length}
          hint={`${enabledRules} enabled · priority order`}
          icon={<ListFilter className="h-5 w-5" />}
          tone="violet"
        />
        <StatCard
          label="Last applied"
          value={data?.settings.applied_at ? formatRelative(data.settings.applied_at) : "Never"}
          hint={data?.settings.last_error ?? "No apply error recorded"}
          icon={<ShieldCheck className="h-5 w-5" />}
        />
        <StatCard
          label="Config version"
          value={data?.settings.config_version ?? 0}
          hint={data?.scope.managed_table ?? "inet nat64mgr"}
          icon={<ArrowDownUp className="h-5 w-5" />}
        />
      </div>

      {data?.warnings.map((warning, index) => (
        <Alert key={index} tone={warning.toLowerCase().includes("lock") ? "warning" : "info"}>
          {warning}
        </Alert>
      ))}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs
          value={tab}
          onChange={setTab}
          tabs={[
            { id: "rules", label: "Rules", badge: <Badge tone="slate">{ruleRows.length}</Badge> },
            { id: "settings", label: "Settings & apply" },
          ]}
        />
        {tab === "settings" && canRequest ? (
          <Button variant="primary" size="sm" onClick={() => void openApply()} loading={busy === "apply"} disabled={!initialised}>
            <Save className="h-4 w-4" /> Apply via change
          </Button>
        ) : null}
      </div>

      {tab === "rules" ? (
        <Card>
          <CardHeader
            title="Firewall rules"
            description="Ordered by priority. Enabling the managed table requires a management allowlist to avoid lockout."
            icon={<ListFilter className="h-4 w-4" />}
          />
          {rules.loading && !rules.data ? <Spinner label="Loading rules" /> : null}
          <DataTable
            rows={ruleRows}
            rowKey={(row) => row.id}
            dense
            empty="No rules yet. The managed table still accepts established traffic and NAT64 transport by default."
            columns={[
              { key: "priority", header: "Priority", render: (row) => <span className="text-xs">{row.priority}</span> },
              {
                key: "name",
                header: "Rule",
                render: (row) => (
                  <div>
                    <p className="text-xs font-semibold">{row.name}</p>
                    <p className="text-[11px] text-slate-400">{row.description ?? "No description"}</p>
                  </div>
                ),
              },
              { key: "family", header: "Family", render: (row) => <code className="text-xs">{row.family}</code> },
              { key: "direction", header: "Direction", render: (row) => titleCase(row.direction) },
              {
                key: "match",
                header: "Match",
                render: (row) => (
                  <span className="text-xs">
                    {row.protocol ? `${row.protocol.toUpperCase()} ` : ""}
                    {row.source_cidr ?? "any"} → {row.destination_cidr ?? "any"}
                    {row.destination_port ? `:${row.destination_port}` : ""}
                  </span>
                ),
              },
              {
                key: "action",
                header: "Action",
                render: (row) => (
                  <Badge tone={row.action === "accept" ? "green" : row.action === "drop" ? "red" : "amber"}>
                    {titleCase(row.action)}
                  </Badge>
                ),
              },
              { key: "log", header: "Log", render: (row) => (row.log ? "Yes" : "No") },
              {
                key: "enabled",
                header: "Enabled",
                render: (row) =>
                  canWrite ? (
                    <button
                      type="button"
                      onClick={() => void toggleRule(row)}
                      className="text-xs"
                      aria-label={row.enabled ? "Disable rule" : "Enable rule"}
                    >
                      {row.enabled ? <Badge tone="green" dot>Enabled</Badge> : <Badge tone="slate">Disabled</Badge>}
                    </button>
                  ) : row.enabled ? (
                    <Badge tone="green" dot>Enabled</Badge>
                  ) : (
                    <Badge tone="slate">Disabled</Badge>
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
                      <Button variant="ghost" size="icon" onClick={() => openEditRule(row)} aria-label="Edit rule">
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button variant="ghost" size="icon" onClick={() => setDeleteRule(row)} aria-label="Delete rule">
                        <Trash2 className="h-4 w-4 text-rose-500" />
                      </Button>
                    </div>
                  ) : null,
              },
            ]}
          />
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
          <Card>
            <CardHeader
              title="Firewall settings"
              description="Applied by the privileged helper. The management allowlist is mandatory before enabling."
              icon={<Shield className="h-4 w-4" />}
            />
            <div className="space-y-4 p-5">
              <Toggle
                checked={form.enabled}
                onChange={(enabled) => setForm({ ...form, enabled })}
                label="Managed firewall enabled"
                description="Replaces only the inet nat64mgr table. Other nftables tables are untouched."
              />
              {form.enabled && splitCidrs(form.management_allowlist).length === 0 ? (
                <Alert tone="warning" title="Management allowlist is empty">
                  Enabling without an allowlist can lock you out of SSH and the web UI. Add at least one
                  management CIDR before applying.
                </Alert>
              ) : null}
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field label="Default forward policy">
                  <Select
                    value={form.default_forward_policy}
                    onChange={(event) =>
                      setForm({ ...form, default_forward_policy: event.target.value as "accept" | "drop" })
                    }
                  >
                    <option value="accept">accept</option>
                    <option value="drop">drop</option>
                  </Select>
                </Field>
                <Field label="Default input policy">
                  <Select
                    value={form.default_input_policy}
                    onChange={(event) =>
                      setForm({ ...form, default_input_policy: event.target.value as "accept" | "drop" })
                    }
                  >
                    <option value="accept">accept</option>
                    <option value="drop">drop</option>
                  </Select>
                </Field>
                <Field label="New connections per second" required>
                  <TextInput
                    type="number"
                    min={1}
                    value={form.new_connection_rate}
                    onChange={(event) => setForm({ ...form, new_connection_rate: event.target.value })}
                  />
                </Field>
                <Field label="Burst packets" required>
                  <TextInput
                    type="number"
                    min={1}
                    value={form.rate_burst}
                    onChange={(event) => setForm({ ...form, rate_burst: event.target.value })}
                  />
                </Field>
              </div>
              <Field label="Management allowlist" hint="CIDRs permitted to reach the management ports. One per line.">
                <Textarea
                  value={form.management_allowlist}
                  onChange={(event) => setForm({ ...form, management_allowlist: event.target.value })}
                  className="min-h-[80px]"
                  placeholder={"203.0.113.0/24\n2001:db8:100::/64"}
                />
              </Field>
              <Field label="Management ports" required hint="Comma separated.">
                <TextInput
                  value={form.management_ports}
                  onChange={(event) => setForm({ ...form, management_ports: event.target.value })}
                  placeholder="22,443"
                />
              </Field>
              <Toggle
                checked={form.log_dropped}
                onChange={(log_dropped) => setForm({ ...form, log_dropped })}
                label="Log dropped packets"
              />
              <Toggle
                checked={form.protect_control_plane}
                onChange={(protect_control_plane) => setForm({ ...form, protect_control_plane })}
                label="Protect control plane"
                description="Rate-limits control-plane traffic and restricts management ports to the allowlist."
              />
              <Toggle
                checked={form.syn_flood_protection}
                onChange={(syn_flood_protection) => setForm({ ...form, syn_flood_protection })}
                label="SYN flood protection"
              />
              <Toggle
                checked={form.invalid_packet_protection}
                onChange={(invalid_packet_protection) => setForm({ ...form, invalid_packet_protection })}
                label="Drop invalid packets"
              />
              <Toggle
                checked={form.mss_clamping}
                onChange={(mss_clamping) => setForm({ ...form, mss_clamping })}
                label="TCP MSS clamping"
                description="Useful when PMTU discovery is unreliable; applied to forwarded TCP."
              />
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Applied {formatDate(data?.settings.applied_at)} · last error:{" "}
                {data?.settings.last_error ?? "none"}
              </p>
            </div>
          </Card>

          <div className="space-y-6">
            <Card>
              <CardHeader
                title="Rendered ruleset"
                description="Preview of the nftables content. Validation and apply happen only after approval."
                icon={<ShieldCheck className="h-4 w-4" />}
                actions={
                  <Button variant="outline" size="sm" onClick={() => void preview.refresh()} loading={preview.loading}>
                    <RefreshCw className="h-4 w-4" /> Re-render
                  </Button>
                }
              />
              <div className="p-5">
                {preview.data?.error ? (
                  <Alert tone="error" title="Render failed">
                    {preview.data.error}
                  </Alert>
                ) : (
                  <CodeBlock title={data?.scope.managed_table}>{preview.data?.content ?? ""}</CodeBlock>
                )}
              </div>
            </Card>
            <Card>
              <CardHeader title="Scope" icon={<ShieldAlert className="h-4 w-4" />} />
              <div className="p-5">
                <KeyValue
                  items={[
                    { label: "Managed table", value: <code>{data?.scope.managed_table ?? "inet nat64mgr"}</code> },
                    { label: "Status", value: titleCase(data?.status.status ?? "unknown") },
                    { label: "Table present", value: data?.status.table_present ? "Yes" : "No" },
                    { label: "Version", value: data?.settings.config_version ?? 0 },
                  ]}
                />
                <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">{data?.scope.note}</p>
              </div>
            </Card>
          </div>
        </div>
      )}

      <Modal
        open={ruleModal}
        onClose={() => setRuleModal(false)}
        title={editingRule ? `Edit rule ${editingRule.name}` : "Create firewall rule"}
        description="Database-only edit. It takes effect in nftables after an approved firewall.apply change."
        size="lg"
        footer={
          <>
            <Button variant="outline" onClick={() => setRuleModal(false)} disabled={busy !== null}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void saveRule()} loading={busy === "rule"}>
              {editingRule ? "Save rule" : "Create rule"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Name" required>
              <TextInput value={ruleForm.name} onChange={(e) => setRuleForm({ ...ruleForm, name: e.target.value })} />
            </Field>
            <Field label="Priority" required hint="Lower values are evaluated first.">
              <TextInput
                type="number"
                min={1}
                value={ruleForm.priority}
                onChange={(e) => setRuleForm({ ...ruleForm, priority: e.target.value })}
              />
            </Field>
            <Field label="Family">
              <Select
                value={ruleForm.family}
                onChange={(e) => setRuleForm({ ...ruleForm, family: e.target.value as RuleForm["family"] })}
              >
                <option value="inet">inet</option>
                <option value="ipv4">ipv4</option>
                <option value="ipv6">ipv6</option>
              </Select>
            </Field>
            <Field label="Direction">
              <Select
                value={ruleForm.direction}
                onChange={(e) =>
                  setRuleForm({ ...ruleForm, direction: e.target.value as RuleForm["direction"] })
                }
              >
                <option value="forward">forward</option>
                <option value="input">input</option>
              </Select>
            </Field>
            <Field label="Action">
              <Select
                value={ruleForm.action}
                onChange={(e) => setRuleForm({ ...ruleForm, action: e.target.value as RuleForm["action"] })}
              >
                <option value="accept">accept</option>
                <option value="drop">drop</option>
                <option value="reject">reject</option>
                <option value="log">log</option>
              </Select>
            </Field>
            <Field label="Protocol">
              <Select
                value={ruleForm.protocol}
                onChange={(e) =>
                  setRuleForm({ ...ruleForm, protocol: e.target.value as RuleForm["protocol"] })
                }
              >
                <option value="">Any</option>
                <option value="tcp">TCP</option>
                <option value="udp">UDP</option>
                <option value="icmp">ICMP</option>
                <option value="icmpv6">ICMPv6</option>
              </Select>
            </Field>
            <Field label="Source CIDR">
              <TextInput
                value={ruleForm.source_cidr}
                onChange={(e) => setRuleForm({ ...ruleForm, source_cidr: e.target.value })}
                placeholder="2001:db8:100::/64"
              />
            </Field>
            <Field label="Destination CIDR">
              <TextInput
                value={ruleForm.destination_cidr}
                onChange={(e) => setRuleForm({ ...ruleForm, destination_cidr: e.target.value })}
                placeholder="0.0.0.0/0"
              />
            </Field>
            <Field label="Source port">
              <TextInput
                type="number"
                min={1}
                max={65535}
                value={ruleForm.source_port}
                onChange={(e) => setRuleForm({ ...ruleForm, source_port: e.target.value })}
              />
            </Field>
            <Field label="Destination port">
              <TextInput
                type="number"
                min={1}
                max={65535}
                value={ruleForm.destination_port}
                onChange={(e) => setRuleForm({ ...ruleForm, destination_port: e.target.value })}
              />
            </Field>
            <Field label="Rate limit per second">
              <TextInput
                type="number"
                min={1}
                value={ruleForm.rate_limit_per_second}
                onChange={(e) => setRuleForm({ ...ruleForm, rate_limit_per_second: e.target.value })}
              />
            </Field>
          </div>
          <Field label="Description">
            <Textarea
              value={ruleForm.description}
              onChange={(e) => setRuleForm({ ...ruleForm, description: e.target.value })}
              className="min-h-[70px]"
            />
          </Field>
          <Toggle checked={ruleForm.enabled} onChange={(enabled) => setRuleForm({ ...ruleForm, enabled })} label="Rule enabled" />
          <Toggle checked={ruleForm.log} onChange={(log) => setRuleForm({ ...ruleForm, log })} label="Log matching packets" />
          <Alert tone="info">
            Only the managed inet nat64mgr table is replaced. Established/related and NAT64 pool6/pool4
            traffic is preserved by the rendered ruleset.
          </Alert>
        </div>
      </Modal>

      <ConfirmDialog
        open={deleteRule !== null}
        onClose={() => setDeleteRule(null)}
        onConfirm={() => void confirmDeleteRule()}
        title={`Delete rule ${deleteRule?.name ?? ""}?`}
        description="The rule is removed from the database immediately. nftables changes only after an approved apply."
        confirmLabel="Delete rule"
        danger
        loading={busy === "delete"}
      />

      <ChangeDialog
        workflow={workflow}
        title="Firewall apply change"
        onUpdated={() => {
          seeded.current = false;
          void initial.refresh();
          void preview.refresh();
        }}
      />
    </div>
  );
}
