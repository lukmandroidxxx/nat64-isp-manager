import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Cable,
  CheckCircle2,
  FileCode2,
  Network,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
} from "lucide-react";
import { api, errorText } from "../lib/api";
import { formatDate, formatRelative, titleCase } from "../lib/format";
import type { NetworkInterface, NetworkSettings } from "../lib/types";
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
  PageHeader,
  Select,
  Spinner,
  StatCard,
  TextInput,
  Textarea,
  Toggle,
} from "../components/ui";
import { DataTable } from "../components/DataTable";
import { ChangeDialog, useChangeWorkflow } from "../components/ChangeWorkflow";

interface NetworkResponse {
  settings: NetworkSettings;
  preview: {
    netplan_yaml: string;
    sysctl_content: string;
    hash: string | null;
    warnings: string[];
    errors: string[];
    validated_on_host: boolean;
    host_available: boolean;
    note: string;
  };
  status: { status: string; message?: string; pending_confirmation: boolean; last_applied_at?: string };
  interfaces: NetworkInterface[];
  host: { available: boolean; mode: string; reason: string | null };
  warnings: string[];
}

interface FormState {
  wan4_interface: string;
  wan6_interface: string;
  lan6_interface: string;
  client_prefix: string;
  nat64_prefix: string;
  dns_server: string;
  default_gateway4: string;
  default_gateway6: string;
  mtu: string;
  ipv4_forwarding: boolean;
  ipv6_forwarding: boolean;
  manage_routes: boolean;
  manage_mtu: boolean;
  notes: string;
}

const EMPTY_FORM: FormState = {
  wan4_interface: "",
  wan6_interface: "",
  lan6_interface: "",
  client_prefix: "",
  nat64_prefix: "64:ff9b::/96",
  dns_server: "",
  default_gateway4: "",
  default_gateway6: "",
  mtu: "1500",
  ipv4_forwarding: true,
  ipv6_forwarding: true,
  manage_routes: false,
  manage_mtu: false,
  notes: "",
};

function toForm(settings: NetworkSettings): FormState {
  return {
    wan4_interface: settings.wan4_interface ?? "",
    wan6_interface: settings.wan6_interface ?? "",
    lan6_interface: settings.lan6_interface ?? "",
    client_prefix: settings.client_prefix ?? "",
    nat64_prefix: settings.nat64_prefix,
    dns_server: settings.dns_server ?? "",
    default_gateway4: settings.default_gateway4 ?? "",
    default_gateway6: settings.default_gateway6 ?? "",
    mtu: String(settings.mtu),
    ipv4_forwarding: settings.ipv4_forwarding,
    ipv6_forwarding: settings.ipv6_forwarding,
    manage_routes: settings.manage_routes,
    manage_mtu: settings.manage_mtu,
    notes: settings.notes ?? "",
  };
}

const ROLES = ["wan4", "wan6", "lan6", "lan4", "management", "other"] as const;

export default function NetworkPage() {
  const toast = useToast();
  const { can } = useAuth();
  const canWrite = can("network.write");
  const canRequest = can("changes.request");
  const workflow = useChangeWorkflow();

  const initial = usePolling<NetworkResponse>("/network", undefined, { interval: 0 });
  const status = usePolling<NetworkResponse["status"]>("/network/status", undefined, { interval: 10000 });

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [initialised, setInitialised] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [preview, setPreview] = useState<NetworkResponse["preview"] | null>(null);
  const [rollbackOpen, setRollbackOpen] = useState(false);
  const seeded = useRef(false);

  useEffect(() => {
    if (initial.data && !seeded.current) {
      seeded.current = true;
      setForm(toForm(initial.data.settings));
      setPreview(initial.data.preview);
      setInitialised(true);
    }
  }, [initial.data]);

  const payload = () => ({
    wan4_interface: form.wan4_interface.trim() || null,
    wan6_interface: form.wan6_interface.trim() || null,
    lan6_interface: form.lan6_interface.trim() || null,
    client_prefix: form.client_prefix.trim() || null,
    nat64_prefix: form.nat64_prefix.trim(),
    dns_server: form.dns_server.trim() || null,
    default_gateway4: form.default_gateway4.trim() || null,
    default_gateway6: form.default_gateway6.trim() || null,
    mtu: Number(form.mtu),
    ipv4_forwarding: form.ipv4_forwarding,
    ipv6_forwarding: form.ipv6_forwarding,
    manage_routes: form.manage_routes,
    manage_mtu: form.manage_mtu,
    notes: form.notes.trim() || null,
  });

  const runPreview = async () => {
    setBusy("preview");
    try {
      const result = await api.post<NetworkResponse["preview"]>("/network/preview", payload());
      setPreview(result);
      if (result.errors.length) {
        toast.warning("Preview has errors", result.errors.join("; "));
      } else {
        toast.success("Preview rendered", "Run on the host during the change dry run.");
      }
    } catch (error) {
      toast.error("Preview failed", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const openApply = async () => {
    setBusy("apply");
    try {
      await workflow.submit("/network/apply", payload());
    } catch (error) {
      toast.error("Could not create change request", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const openRollback = async () => {
    setBusy("rollback");
    try {
      await workflow.submit("/network/rollback", {});
      setRollbackOpen(false);
    } catch (error) {
      toast.error("Could not create rollback request", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const confirmNetwork = async () => {
    setBusy("confirm");
    try {
      await api.post("/network/confirm");
      toast.success("Network change confirmed", "The automatic rollback timer was cancelled.");
      void status.refresh();
      seeded.current = false;
      void initial.refresh();
    } catch (error) {
      toast.error("Could not confirm network change", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const detectInterfaces = async () => {
    setBusy("detect");
    try {
      const result = await api.post<{ status?: string; message?: string }>("/network/detect");
      toast.info("Interface detection requested", result.message ?? result.status ?? "See stored interfaces.");
      seeded.current = false;
      void initial.refresh();
    } catch (error) {
      toast.error("Detection failed", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const updateInterfaceRole = async (iface: NetworkInterface, role: string) => {
    try {
      await api.patch(`/network/interfaces/${iface.id}`, { role });
      toast.success("Interface role saved", `${iface.name} is now ${role}.`);
      void initial.refresh();
    } catch (error) {
      toast.error("Could not update interface", errorText(error));
    }
  };

  const data = initial.data;
  const settings = data?.settings;
  const interfaceRows = data?.interfaces ?? [];
  const pending = status.data?.pending_confirmation ?? settings?.pending_confirmation ?? false;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Network Interfaces"
        breadcrumb="Network"
        description="WAN/LAN interface roles, forwarding, MTU and the managed netplan fragment. Apply is a change request with an automatic rollback timer."
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
              <Button variant="outline" size="sm" onClick={() => void detectInterfaces()} loading={busy === "detect"}>
                <Search className="h-4 w-4" /> Detect interfaces
              </Button>
            ) : null}
            {canWrite ? (
              <Button variant="outline" size="sm" onClick={() => void runPreview()} loading={busy === "preview"}>
                <FileCode2 className="h-4 w-4" /> Validate preview
              </Button>
            ) : null}
            {canRequest ? (
              <Button variant="primary" size="sm" onClick={() => void openApply()} loading={busy === "apply"} disabled={!initialised}>
                <Save className="h-4 w-4" /> Apply via change
              </Button>
            ) : null}
          </>
        }
      />

      {initial.error ? (
        <Alert tone="error" title="Network configuration unavailable">
          {initial.error}
        </Alert>
      ) : null}

      {pending ? (
        <Alert
          tone="warning"
          title="A network change is pending confirmation"
          actions={
            canWrite ? (
              <Button variant="success" size="sm" onClick={() => void confirmNetwork()} loading={busy === "confirm"}>
                <CheckCircle2 className="h-4 w-4" /> Confirm connectivity
              </Button>
            ) : undefined
          }
        >
          The privileged helper will roll the managed network configuration back automatically unless you confirm
          that management connectivity still works. Confirm only after verifying access.
        </Alert>
      ) : null}

      {status.data?.status === "unsupported" ? (
        <Alert tone="warning" title="netplan is not observable from this server">
          {status.data.message ??
            "The database configuration and preview are available, but apply and rollback cannot execute without the Linux host helper."}
        </Alert>
      ) : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Network status"
          value={titleCase(status.data?.status ?? (settings?.last_render_hash ? "configured" : "draft"))}
          hint={status.data?.message ?? "Managed netplan + sysctl drop-in"}
          icon={<Network className="h-5 w-5" />}
          tone={status.data?.status === "ok" ? "green" : status.data?.status === "unsupported" ? "violet" : "amber"}
        />
        <StatCard
          label="Interfaces known"
          value={interfaceRows.length}
          hint={`${interfaceRows.filter((row) => row.is_up).length} up`}
          icon={<Cable className="h-5 w-5" />}
          tone="violet"
        />
        <StatCard
          label="Last applied"
          value={settings?.last_applied_at ? formatRelative(settings.last_applied_at) : "Never"}
          hint={settings?.rollout_confirmed_at ? `Confirmed ${formatRelative(settings.rollout_confirmed_at)}` : "Not confirmed"}
          icon={<CheckCircle2 className="h-5 w-5" />}
        />
        <StatCard
          label="Config version"
          value={settings?.config_version ?? 0}
          hint={settings?.last_render_hash ? `Hash ${settings.last_render_hash.slice(0, 12)}…` : "Not rendered on host"}
          icon={<FileCode2 className="h-5 w-5" />}
        />
      </div>

      {data?.warnings.map((warning, index) => (
        <Alert key={index} tone="info">
          {warning}
        </Alert>
      ))}

      {initial.loading && !initial.data ? <Spinner label="Loading network configuration" /> : null}

      <Card>
        <CardHeader
          title="Detected interfaces"
          description="Roles influence which addresses the collector classifies as WAN, LAN and management."
          icon={<Cable className="h-4 w-4" />}
        />
        <DataTable
          rows={interfaceRows}
          rowKey={(row) => row.id}
          dense
          empty="No interfaces detected. Detection requires the privileged Linux helper."
          columns={[
            {
              key: "name",
              header: "Interface",
              render: (row) => (
                <div>
                  <code className="text-xs font-semibold">{row.name}</code>
                  <p className="text-[11px] text-slate-400">{row.mac_address ?? "No MAC"} · {row.driver ?? "unknown driver"}</p>
                </div>
              ),
            },
            {
              key: "role",
              header: "Role",
              render: (row) =>
                canWrite ? (
                  <Select
                    className="w-36"
                    value={row.role ?? ""}
                    onChange={(event) => void updateInterfaceRole(row, event.target.value)}
                  >
                    <option value="">Unassigned</option>
                    {ROLES.map((role) => (
                      <option key={role} value={role}>
                        {role}
                      </option>
                    ))}
                  </Select>
                ) : (
                  <Badge tone={row.role ? "blue" : "slate"}>{row.role ?? "Unassigned"}</Badge>
                ),
            },
            {
              key: "state",
              header: "State",
              render: (row) => (
                <Badge status={row.is_up ? "up" : "down"} dot>
                  {row.is_up ? "Up" : "Down"}
                </Badge>
              ),
            },
            {
              key: "addr",
              header: "Addresses",
              render: (row) => (
                <span className="text-xs">
                  {(row.addresses ?? []).length
                    ? row.addresses?.map((address) => `${address.address}/${address.prefixlen}`).join(", ")
                    : "—"}
                </span>
              ),
            },
            { key: "mtu", header: "MTU", render: (row) => row.mtu ?? "—" },
            {
              key: "speed",
              header: "Speed",
              render: (row) => (row.speed_mbps ? `${row.speed_mbps} Mbps` : "—"),
            },
            {
              key: "seen",
              header: "Last seen",
              render: (row) => (
                <span className="text-xs text-slate-500">{formatRelative(row.last_seen_at)}</span>
              ),
            },
          ]}
        />
      </Card>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader
            title="Managed network settings"
            description="Database values. Apply creates a change request that writes netplan and sysctl on the host."
            icon={<Network className="h-4 w-4" />}
            actions={
              canRequest ? (
                <Button variant="outline" size="sm" onClick={() => setRollbackOpen(true)}>
                  <RotateCcw className="h-4 w-4" /> Roll back
                </Button>
              ) : undefined
            }
          />
          <div className="space-y-4 p-5">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="WAN IPv4 interface">
                <TextInput
                  value={form.wan4_interface}
                  onChange={(e) => setForm({ ...form, wan4_interface: e.target.value })}
                  placeholder="wan0"
                />
              </Field>
              <Field label="WAN IPv6 interface">
                <TextInput
                  value={form.wan6_interface}
                  onChange={(e) => setForm({ ...form, wan6_interface: e.target.value })}
                  placeholder="wan0"
                />
              </Field>
              <Field label="LAN IPv6 interface">
                <TextInput
                  value={form.lan6_interface}
                  onChange={(e) => setForm({ ...form, lan6_interface: e.target.value })}
                  placeholder="lan0"
                />
              </Field>
              <Field label="NAT64 prefix">
                <TextInput
                  value={form.nat64_prefix}
                  onChange={(e) => setForm({ ...form, nat64_prefix: e.target.value })}
                  placeholder="64:ff9b::/96"
                />
              </Field>
              <Field label="Client IPv6 prefix">
                <TextInput
                  value={form.client_prefix}
                  onChange={(e) => setForm({ ...form, client_prefix: e.target.value })}
                  placeholder="2001:db8:100::/64"
                />
              </Field>
              <Field label="DNS server handed to clients">
                <TextInput
                  value={form.dns_server}
                  onChange={(e) => setForm({ ...form, dns_server: e.target.value })}
                  placeholder="2001:db8:100::1"
                />
              </Field>
              <Field label="Default IPv4 gateway">
                <TextInput
                  value={form.default_gateway4}
                  onChange={(e) => setForm({ ...form, default_gateway4: e.target.value })}
                  placeholder="203.0.113.1"
                />
              </Field>
              <Field label="Default IPv6 gateway">
                <TextInput
                  value={form.default_gateway6}
                  onChange={(e) => setForm({ ...form, default_gateway6: e.target.value })}
                  placeholder="2001:db8::1"
                />
              </Field>
            </div>
            <Field label="MTU" hint="Applied to managed netplan interfaces when MTU management is enabled.">
              <TextInput
                type="number"
                min={576}
                max={9216}
                value={form.mtu}
                onChange={(e) => setForm({ ...form, mtu: e.target.value })}
              />
            </Field>
            <Toggle
              checked={form.ipv4_forwarding}
              onChange={(ipv4_forwarding) => setForm({ ...form, ipv4_forwarding })}
              label="IPv4 forwarding"
            />
            <Toggle
              checked={form.ipv6_forwarding}
              onChange={(ipv6_forwarding) => setForm({ ...form, ipv6_forwarding })}
              label="IPv6 forwarding"
            />
            <Toggle
              checked={form.manage_routes}
              onChange={(manage_routes) => setForm({ ...form, manage_routes })}
              label="Manage default routes"
              description="When enabled the managed netplan writes default gateways. Enable only with verification access."
            />
            <Toggle
              checked={form.manage_mtu}
              onChange={(manage_mtu) => setForm({ ...form, manage_mtu })}
              label="Manage interface MTU"
            />
            <Field label="Notes">
              <Textarea
                value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
                className="min-h-[70px]"
              />
            </Field>
          </div>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader
              title="netplan preview"
              description={data?.preview.note}
              icon={<FileCode2 className="h-4 w-4" />}
            />
            <div className="space-y-4 p-5">
              {preview?.errors.length ? (
                <Alert tone="error" title="Preview errors">
                  {preview.errors.join("; ")}
                </Alert>
              ) : null}
              {preview?.warnings.length ? (
                <Alert tone="warning" title="Preview warnings">
                  {preview.warnings.join("; ")}
                </Alert>
              ) : null}
              <CodeBlock title="/etc/netplan/90-nat64-manager.yaml">
                {preview?.netplan_yaml ?? data?.preview.netplan_yaml ?? ""}
              </CodeBlock>
              <CodeBlock title="/etc/sysctl.d/90-nat64-manager.conf">
                {preview?.sysctl_content ?? data?.preview.sysctl_content ?? ""}
              </CodeBlock>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Validation on host: {preview?.validated_on_host ? "yes" : "no"} · hash{" "}
                {preview?.hash ?? "not rendered"}
              </p>
            </div>
          </Card>

          <Card>
            <CardHeader title="Safety model" icon={<AlertTriangle className="h-4 w-4" />} />
            <div className="p-5">
              <KeyValue
                items={[
                  { label: "Host available", value: data?.host.available ? "Yes" : "No" },
                  { label: "Host mode", value: data?.host.mode ?? "—" },
                  { label: "Last applied", value: formatDate(data?.settings.last_applied_at) },
                  { label: "Pending confirmation", value: pending ? "Yes" : "No" },
                  { label: "Last snapshot", value: data?.settings.last_snapshot ?? "—" },
                ]}
              />
              <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
                Execution runs netplan generate, sysctl --system and netplan apply through the helper, then starts
                a rollback timer. Confirming cancels the timer; otherwise the helper restores the pre-change
                snapshot.
              </p>
            </div>
          </Card>
        </div>
      </div>

      <ConfirmDialog
        open={rollbackOpen}
        onClose={() => setRollbackOpen(false)}
        onConfirm={() => void openRollback()}
        title="Roll back managed network configuration"
        description="Creates a change request that restores the pre-change snapshot through the privileged helper."
        confirmLabel="Create rollback request"
        danger
        loading={busy === "rollback"}
      >
        <p className="text-sm text-slate-600 dark:text-slate-300">
          Use rollback if connectivity is degraded or the applied configuration is wrong. It is a host-mutating
          change and still requires approval and execution.
        </p>
      </ConfirmDialog>

      <ChangeDialog
        workflow={workflow}
        title="Network change request"
        onUpdated={() => {
          seeded.current = false;
          void initial.refresh();
          void status.refresh();
        }}
      />
    </div>
  );
}
