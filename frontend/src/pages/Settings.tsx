import { useMemo, useState } from "react";
import {
  KeyRound,
  Lock,
  MonitorSmartphone,
  Pencil,
  Plus,
  RefreshCw,
  Server,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  UserCog,
  Users,
} from "lucide-react";
import { api, errorText } from "../lib/api";
import { formatDate, formatRelative, prettyJson, titleCase } from "../lib/format";
import type { AuthSession, Role, SettingItem, User } from "../lib/types";
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
  Select,
  Spinner,
  StatCard,
  Tabs,
  Textarea,
  TextInput,
  Toggle,
} from "../components/ui";
import { DataTable } from "../components/DataTable";

type SettingsTab = "application" | "settings" | "users" | "sessions";

interface ApplicationInfo {
  app: {
    name: string;
    version: string;
    environment: string;
    api_prefix: string;
    timezone: string;
    demo_mode: boolean;
    docs_enabled: boolean;
  };
  database: { dialect: string; sqlite: boolean; production_ready: boolean };
  host: { mode: string; available: boolean; reason: string | null };
  security: {
    access_token_expire_minutes: number;
    refresh_token_expire_days: number;
    password_min_length: number;
    login_max_failures: number;
    login_lockout_minutes: number;
    rate_limits: { enabled: boolean; per_minute: number; login_per_minute: number };
    secure_cookies: boolean;
  };
  features: {
    collector_enabled: boolean;
    collector_interval_seconds: number;
    ha_enabled: boolean;
    ha_role: string;
    allow_package_install: boolean;
    serve_frontend: boolean;
  };
  paths: {
    dns_managed_config: string;
    network_managed_netplan: string;
    network_managed_sysctl: string;
    firewall_managed_file: string;
    backup_dir: string;
  };
  warnings: string[];
}

interface SettingForm {
  mode: "create" | "edit";
  key: string;
  value: string;
  description: string;
}

interface UserForm {
  username: string;
  password: string;
  role: string;
  email: string;
  full_name: string;
  is_active: boolean;
}

interface UserModalState {
  mode: "create" | "edit";
  user: User | null;
  form: UserForm;
}

const ROLES = ["viewer", "operator", "admin", "superadmin"];

const EMPTY_USER_FORM: UserForm = {
  username: "",
  password: "",
  role: "viewer",
  email: "",
  full_name: "",
  is_active: true,
};

function describeValue(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") return value;
  try {
    const text = JSON.stringify(value);
    return text.length > 90 ? `${text.slice(0, 87)}…` : text;
  } catch {
    return String(value);
  }
}

function sessionState(session: AuthSession): string {
  if (session.revoked_at) return "revoked";
  if (new Date(session.expires_at).getTime() < Date.now()) return "expired";
  return "active";
}

export default function SettingsPage() {
  const { user, can, changePassword } = useAuth();
  const toast = useToast();

  const canWriteSettings = can("settings.write");
  const canManageUsers = can("users.write");
  const canApprove = can("changes.approve");
  const canExecute = can("changes.execute");

  const [tab, setTab] = useState<SettingsTab>("application");
  const [busy, setBusy] = useState<string | null>(null);

  const application = usePolling<ApplicationInfo>("/settings/application", undefined, { interval: 0 });
  const settings = usePolling<{ items: SettingItem[]; note: string }>("/settings", undefined, {
    interval: 0,
  });
  const users = usePolling<{ items: User[] }>("/users", undefined, {
    interval: 10000,
    enabled: canManageUsers,
  });
  const roles = usePolling<{ items: Role[] }>("/roles", undefined, {
    interval: 0,
    enabled: canManageUsers,
  });
  const sessions = usePolling<{ items: AuthSession[] }>("/auth/sessions", undefined, {
    interval: 15000,
  });

  const [settingForm, setSettingForm] = useState<SettingForm | null>(null);
  const [deleteSetting, setDeleteSetting] = useState<SettingItem | null>(null);
  const [userModal, setUserModal] = useState<UserModalState | null>(null);
  const [revokeOpen, setRevokeOpen] = useState(false);
  const [passwordForm, setPasswordForm] = useState({ current: "", next: "", confirm: "" });

  const settingRows = settings.data?.items ?? [];
  const userRows = users.data?.items ?? [];
  const roleRows = roles.data?.items ?? [];
  const sessionRows = sessions.data?.items ?? [];
  const roleOptions = useMemo(
    () => (roleRows.length ? roleRows.map((role) => role.name) : ROLES),
    [roleRows],
  );

  const refreshAll = () => {
    void application.refresh();
    void settings.refresh();
    if (canManageUsers) {
      void users.refresh();
      void roles.refresh();
    }
    void sessions.refresh();
  };

  const openCreateSetting = () => {
    setSettingForm({ mode: "create", key: "", value: "", description: "" });
  };

  const openEditSetting = (item: SettingItem) => {
    setSettingForm({
      mode: "edit",
      key: item.key,
      value: prettyJson(item.value),
      description: item.description ?? "",
    });
  };

  const saveSetting = async () => {
    if (!settingForm) return;
    const key = settingForm.key.trim();
    if (!/^[a-z0-9][a-z0-9_.-]{1,127}$/.test(key)) {
      toast.error(
        "Invalid setting key",
        "Use 2–128 lowercase letters, digits, dots, dashes or underscores.",
      );
      return;
    }
    let value: unknown;
    try {
      value = settingForm.value.trim() === "" ? null : JSON.parse(settingForm.value);
    } catch {
      value = settingForm.value;
    }
    setBusy("setting");
    try {
      await api.put(`/settings/${encodeURIComponent(key)}`, {
        value,
        description: settingForm.description.trim() || null,
      });
      toast.success("Setting saved", `${key} was stored and audited.`);
      setSettingForm(null);
      await settings.refresh();
    } catch (error) {
      toast.error("Could not save setting", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const confirmDeleteSetting = async () => {
    if (!deleteSetting) return;
    setBusy("delete-setting");
    try {
      await api.delete(`/settings/${encodeURIComponent(deleteSetting.key)}`);
      toast.success("Setting deleted", deleteSetting.key);
      setDeleteSetting(null);
      await settings.refresh();
    } catch (error) {
      toast.error("Could not delete setting", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const openCreateUser = () => {
    setUserModal({ mode: "create", user: null, form: { ...EMPTY_USER_FORM } });
  };

  const openEditUser = (row: User) => {
    setUserModal({
      mode: "edit",
      user: row,
      form: {
        username: row.username,
        password: "",
        role: row.role,
        email: row.email ?? "",
        full_name: row.full_name ?? "",
        is_active: row.is_active,
      },
    });
  };

  const saveUser = async () => {
    if (!userModal) return;
    const form = userModal.form;
    if (userModal.mode === "create" && form.username.trim().length < 3) {
      toast.error("Username is too short", "Use at least three characters.");
      return;
    }
    if (userModal.mode === "create" && form.password.length < 8) {
      toast.error("Password is too short", "Use at least eight characters.");
      return;
    }
    setBusy("user");
    try {
      if (userModal.mode === "create") {
        await api.post<User>("/users", {
          username: form.username.trim(),
          password: form.password,
          role: form.role,
          email: form.email.trim() || null,
          full_name: form.full_name.trim() || null,
          is_active: form.is_active,
        });
        toast.success("User created", `${form.username} can now sign in.`);
      } else if (userModal.user) {
        await api.patch<User>(`/users/${userModal.user.id}`, {
          role: form.role,
          email: form.email.trim() || null,
          full_name: form.full_name.trim() || null,
          is_active: form.is_active,
          ...(form.password ? { password: form.password } : {}),
        });
        toast.success("User updated", `${form.username} was updated and audited.`);
      }
      setUserModal(null);
      await users.refresh();
    } catch (error) {
      toast.error("Could not save user", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const revokeAll = async () => {
    setBusy("revoke");
    try {
      const result = await api.post<{ revoked: number }>("/auth/sessions/revoke-all");
      toast.success("Sessions revoked", `${result.revoked} refresh session(s) were revoked.`);
      setRevokeOpen(false);
      await sessions.refresh();
    } catch (error) {
      toast.error("Could not revoke sessions", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const submitPassword = async () => {
    if (passwordForm.next.length < 8) {
      toast.error("Password is too short", "Use at least eight characters.");
      return;
    }
    if (passwordForm.next !== passwordForm.confirm) {
      toast.error("Passwords do not match", "Re-enter the new password confirmation.");
      return;
    }
    setBusy("password");
    try {
      await changePassword(passwordForm.current, passwordForm.next);
      toast.success("Password changed", "All refresh sessions were revoked. Sign in again.");
    } catch (error) {
      toast.error("Could not change password", errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const app = application.data;
  const activeSessions = sessionRows.filter((row) => sessionState(row) === "active").length;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Settings"
        description="Application information, key/value configuration, user accounts, roles and the current administrator's sessions. Host-facing settings live on the DNS64, Firewall, Network and HA pages."
        breadcrumb="Administration"
        actions={
          <Button variant="outline" size="sm" onClick={refreshAll} loading={application.loading || settings.loading}>
            <RefreshCw className="h-4 w-4" /> Refresh
          </Button>
        }
      />

      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "application", label: "Application", badge: <Badge tone="slate">{app?.app.version ?? "—"}</Badge> },
          { id: "settings", label: "Key/value settings" },
          ...(canManageUsers
            ? [{ id: "users" as SettingsTab, label: "Users & roles", badge: <Badge tone="slate">{userRows.length}</Badge> }]
            : []),
          { id: "sessions", label: "Account & sessions" },
        ]}
      />

      {application.error ? (
        <Alert tone="error" title="Application information unavailable">
          {application.error}
        </Alert>
      ) : null}

      {tab === "application" ? (
        application.loading && !app ? (
          <Spinner label="Loading application information" />
        ) : app ? (
          <>
            {!app.host.available ? (
              <Alert tone="warning" title="Privileged host integration is unavailable">
                {app.host.reason ?? "This server cannot reach the privileged Linux host helper."} Read-only
                configuration is still available, but host changes cannot be applied from this instance.
              </Alert>
            ) : null}

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <StatCard
                label="Version"
                value={app.app.version}
                hint={`${titleCase(app.app.environment)} environment`}
                icon={<Settings2 className="h-5 w-5" />}
                tone="blue"
              />
              <StatCard
                label="Database"
                value={app.database.dialect}
                hint={app.database.production_ready ? "Production ready" : "Development / tests only"}
                icon={<Server className="h-5 w-5" />}
                tone={app.database.production_ready ? "green" : "amber"}
              />
              <StatCard
                label="Host helper"
                value={titleCase(app.host.mode)}
                hint={app.host.available ? "Privileged helper reachable" : "Typed helper operations disabled"}
                icon={<ShieldCheck className="h-5 w-5" />}
                tone={app.host.available ? "green" : "slate"}
              />
              <StatCard
                label="Collector"
                value={app.features.collector_enabled ? "Enabled" : "Disabled"}
                hint={
                  app.features.collector_enabled
                    ? `Samples every ${app.features.collector_interval_seconds}s`
                    : "No background metric sampling"
                }
                icon={<SlidersHorizontal className="h-5 w-5" />}
                tone={app.features.collector_enabled ? "green" : "slate"}
              />
            </div>

            <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
              <Card>
                <CardHeader
                  title="Application"
                  description="Non-secret runtime values reported by the backend."
                  icon={<Settings2 className="h-4 w-4" />}
                />
                <div className="p-5">
                  <KeyValue
                    items={[
                      { label: "Name", value: app.app.name },
                      { label: "Version", value: app.app.version },
                      { label: "Environment", value: titleCase(app.app.environment) },
                      { label: "API prefix", value: <code>{app.app.api_prefix}</code> },
                      { label: "Timezone", value: app.app.timezone },
                      { label: "Demo mode", value: app.app.demo_mode ? "Yes" : "No" },
                      { label: "API docs", value: app.app.docs_enabled ? "Enabled" : "Disabled" },
                    ]}
                  />
                </div>
              </Card>

              <Card>
                <CardHeader
                  title="Security"
                  description="Effective configuration. Secrets are never returned."
                  icon={<Lock className="h-4 w-4" />}
                />
                <div className="p-5">
                  <KeyValue
                    items={[
                      {
                        label: "Access token lifetime",
                        value: `${app.security.access_token_expire_minutes} minutes`,
                      },
                      {
                        label: "Refresh token lifetime",
                        value: `${app.security.refresh_token_expire_days} days`,
                      },
                      { label: "Password minimum", value: `${app.security.password_min_length} characters` },
                      {
                        label: "Login lockout",
                        value: `${app.security.login_max_failures} failures / ${app.security.login_lockout_minutes} minutes`,
                      },
                      {
                        label: "Rate limiting",
                        value: app.security.rate_limits.enabled
                          ? `${app.security.rate_limits.per_minute}/min API · ${app.security.rate_limits.login_per_minute}/min login`
                          : "Disabled",
                      },
                      { label: "Secure cookies", value: app.security.secure_cookies ? "Yes" : "No" },
                    ]}
                  />
                </div>
              </Card>

              <Card>
                <CardHeader
                  title="Features"
                  description="Runtime toggles from environment configuration."
                  icon={<UserCog className="h-4 w-4" />}
                />
                <div className="p-5">
                  <KeyValue
                    items={[
                      { label: "Collector", value: app.features.collector_enabled ? "Enabled" : "Disabled" },
                      {
                        label: "Collector interval",
                        value: `${app.features.collector_interval_seconds} seconds`,
                      },
                      { label: "HA", value: app.features.ha_enabled ? "Enabled" : "Disabled" },
                      { label: "HA role", value: titleCase(app.features.ha_role) },
                      {
                        label: "Package installs",
                        value: app.features.allow_package_install ? "Allowed" : "Disabled",
                      },
                      { label: "Serve frontend", value: app.features.serve_frontend ? "Yes" : "No" },
                    ]}
                  />
                </div>
              </Card>

              <Card>
                <CardHeader
                  title="Managed paths"
                  description="Files written only through the privileged host helper."
                  icon={<Server className="h-4 w-4" />}
                />
                <div className="p-5">
                  <KeyValue
                    items={[
                      { label: "DNS64 config", value: <code className="break-all text-xs">{app.paths.dns_managed_config}</code> },
                      { label: "Netplan", value: <code className="break-all text-xs">{app.paths.network_managed_netplan}</code> },
                      { label: "sysctl", value: <code className="break-all text-xs">{app.paths.network_managed_sysctl}</code> },
                      { label: "Firewall", value: <code className="break-all text-xs">{app.paths.firewall_managed_file}</code> },
                      { label: "Backups", value: <code className="break-all text-xs">{app.paths.backup_dir}</code> },
                    ]}
                  />
                </div>
              </Card>
            </div>

            {app.warnings.map((warning) => (
              <Alert key={warning} tone="info" title="Configuration note">
                {warning}
              </Alert>
            ))}
          </>
        ) : null
      ) : null}

      {tab === "settings" ? (
        <>
          <Alert tone="info" title="Database-backed application settings">
            These keys are separate from DNS64, firewall, network and HA configuration, which have dedicated
            pages. Values are stored as JSON and every change is audited.{" "}
            {settings.data?.note ?? ""}
          </Alert>

          {settings.error ? (
            <Alert tone="error" title="Settings unavailable">
              {settings.error}
            </Alert>
          ) : null}

          <Card>
            <CardHeader
              title="Key/value settings"
              description="Use dotted lowercase keys such as app.ui.refresh_seconds."
              icon={<SlidersHorizontal className="h-4 w-4" />}
              actions={
                canWriteSettings ? (
                  <Button variant="primary" size="sm" onClick={openCreateSetting}>
                    <Plus className="h-4 w-4" /> New setting
                  </Button>
                ) : undefined
              }
            />
            {settings.loading && !settings.data ? <Spinner label="Loading settings" /> : null}
            {!settings.loading && settingRows.length === 0 ? (
              <EmptyState
                icon={<SlidersHorizontal className="h-6 w-6" />}
                title="No key/value settings"
                description="Application settings are optional. Create one when a deployment needs a stored value that is not covered by a dedicated configuration page."
                action={
                  canWriteSettings ? (
                    <Button variant="primary" onClick={openCreateSetting}>
                      <Plus className="h-4 w-4" /> New setting
                    </Button>
                  ) : undefined
                }
              />
            ) : null}
            {settingRows.length > 0 ? (
              <DataTable
                rows={settingRows}
                rowKey={(row) => row.key}
                loading={settings.loading}
                dense
                columns={[
                  {
                    key: "key",
                    header: "Key",
                    render: (row) => (
                      <div>
                        <code className="text-xs font-semibold">{row.key}</code>
                        {row.description ? (
                          <p className="text-[11px] text-slate-400">{row.description}</p>
                        ) : null}
                      </div>
                    ),
                  },
                  {
                    key: "value",
                    header: "Value",
                    render: (row) => (
                      <code className="break-all text-xs text-slate-600 dark:text-slate-300">
                        {describeValue(row.value)}
                      </code>
                    ),
                  },
                  {
                    key: "updated",
                    header: "Updated",
                    render: (row) => (
                      <div>
                        <p className="text-xs">{formatRelative(row.updated_at)}</p>
                        <p className="text-[11px] text-slate-400">{row.updated_by ?? "system"}</p>
                      </div>
                    ),
                  },
                  {
                    key: "actions",
                    header: "",
                    headerClassName: "text-right",
                    className: "text-right",
                    render: (row) =>
                      canWriteSettings ? (
                        <div className="flex justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={`Edit ${row.key}`}
                            onClick={() => openEditSetting(row)}
                          >
                            <Pencil className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={`Delete ${row.key}`}
                            onClick={() => setDeleteSetting(row)}
                          >
                            <Trash2 className="h-4 w-4 text-rose-500" />
                          </Button>
                        </div>
                      ) : (
                        <span className="text-xs text-slate-400">Read only</span>
                      ),
                  },
                ]}
              />
            ) : null}
          </Card>
        </>
      ) : null}

      {tab === "users" && canManageUsers ? (
        <>
          {users.error ? (
            <Alert tone="error" title="Users unavailable">
              {users.error}
            </Alert>
          ) : null}

          <Card>
            <CardHeader
              title="User accounts"
              description="Accounts are never deleted from this UI; disable an account to block sign-in while retaining its audit history."
              icon={<Users className="h-4 w-4" />}
              actions={
                <Button variant="primary" size="sm" onClick={openCreateUser}>
                  <Plus className="h-4 w-4" /> New user
                </Button>
              }
            />
            {users.loading && !users.data ? <Spinner label="Loading users" /> : null}
            <DataTable
              rows={userRows}
              rowKey={(row) => row.id}
              loading={users.loading}
              dense
              empty="No user accounts were returned."
              columns={[
                {
                  key: "user",
                  header: "User",
                  render: (row) => (
                    <div>
                      <p className="text-xs font-semibold">{row.username}</p>
                      <p className="text-[11px] text-slate-400">{row.full_name ?? row.email ?? "No profile"}</p>
                    </div>
                  ),
                },
                {
                  key: "role",
                  header: "Role",
                  render: (row) => (
                    <Badge tone={row.role === "superadmin" ? "violet" : row.role === "admin" ? "blue" : "slate"}>
                      {titleCase(row.role)}
                    </Badge>
                  ),
                },
                {
                  key: "state",
                  header: "State",
                  render: (row) => (
                    <div className="space-y-1">
                      <Badge status={row.is_active ? "active" : "disabled"} dot>
                        {row.is_active ? "Active" : "Disabled"}
                      </Badge>
                      {row.must_change_password ? <Badge tone="amber">Password change required</Badge> : null}
                    </div>
                  ),
                },
                {
                  key: "login",
                  header: "Last login",
                  render: (row) => (
                    <div>
                      <p className="text-xs">{formatRelative(row.last_login_at)}</p>
                      <p className="text-[11px] text-slate-400">{row.last_login_ip ?? "—"}</p>
                    </div>
                  ),
                },
                {
                  key: "created",
                  header: "Created",
                  render: (row) => <span className="text-xs">{formatDate(row.created_at)}</span>,
                },
                {
                  key: "actions",
                  header: "",
                  headerClassName: "text-right",
                  className: "text-right",
                  render: (row) => (
                    <Button variant="ghost" size="sm" onClick={() => openEditUser(row)}>
                      <Pencil className="h-4 w-4" /> Edit
                    </Button>
                  ),
                },
              ]}
            />
          </Card>

          <Card>
            <CardHeader
              title="Roles and permissions"
              description="Built-in roles. Permissions are enforced by the API, not only by hidden UI controls."
              icon={<ShieldCheck className="h-4 w-4" />}
            />
            <div className="grid grid-cols-1 gap-4 p-5 md:grid-cols-2 xl:grid-cols-4">
              {roleRows.map((role) => (
                <div key={role.id} className="rounded-xl border border-slate-200 p-4 dark:border-slate-800">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-semibold">{titleCase(role.name)}</p>
                    <Badge tone={role.name === "superadmin" ? "violet" : "slate"}>
                      {role.permissions.length} perms
                    </Badge>
                  </div>
                  <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                    {role.description ?? "No description"}
                  </p>
                  <div className="mt-3 flex flex-wrap gap-1">
                    {role.permissions.slice(0, 8).map((permission) => (
                      <span
                        key={permission}
                        className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600 dark:bg-slate-800 dark:text-slate-300"
                      >
                        {permission}
                      </span>
                    ))}
                    {role.permissions.length > 8 ? (
                      <span className="text-[10px] text-slate-400">+{role.permissions.length - 8}</span>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          </Card>

          <Alert tone="info" title="Approval separation">
            You can {canApprove ? "" : "not "}approve changes and {canExecute ? "" : "not "}execute approved
            changes with your current role. Critical changes cannot be approved by the administrator who
            requested them unless that account is a superadmin.
          </Alert>
        </>
      ) : null}

      {tab === "sessions" ? (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard
              label="Signed in as"
              value={user?.username ?? "—"}
              hint={user ? `${titleCase(user.role)} · ${user.email ?? "no email"}` : "Unknown account"}
              icon={<UserCog className="h-5 w-5" />}
              tone="blue"
            />
            <StatCard
              label="Active refresh sessions"
              value={activeSessions}
              hint={`${sessionRows.length} session record(s) retained`}
              icon={<MonitorSmartphone className="h-5 w-5" />}
              tone={activeSessions > 0 ? "green" : "slate"}
            />
            <StatCard
              label="Permissions"
              value={user?.permissions.length ?? 0}
              hint={user?.permissions.includes("*") ? "Wildcard access" : "Role-derived permissions"}
              icon={<ShieldCheck className="h-5 w-5" />}
            />
            <StatCard
              label="Last login"
              value={formatRelative(user?.last_login_at)}
              hint={user?.last_login_ip ?? "No source address recorded"}
              icon={<KeyRound className="h-5 w-5" />}
            />
          </div>

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <Card>
              <CardHeader
                title="Change password"
                description="Changing your password revokes every refresh session, including this one."
                icon={<Lock className="h-4 w-4" />}
              />
              <div className="space-y-4 p-5">
                <Field label="Current password" required>
                  <TextInput
                    type="password"
                    autoComplete="current-password"
                    value={passwordForm.current}
                    onChange={(event) => setPasswordForm({ ...passwordForm, current: event.target.value })}
                  />
                </Field>
                <Field label="New password" required hint="At least eight characters.">
                  <TextInput
                    type="password"
                    autoComplete="new-password"
                    value={passwordForm.next}
                    onChange={(event) => setPasswordForm({ ...passwordForm, next: event.target.value })}
                  />
                </Field>
                <Field label="Confirm new password" required>
                  <TextInput
                    type="password"
                    autoComplete="new-password"
                    value={passwordForm.confirm}
                    onChange={(event) => setPasswordForm({ ...passwordForm, confirm: event.target.value })}
                  />
                </Field>
                <Button variant="primary" onClick={() => void submitPassword()} loading={busy === "password"}>
                  <KeyRound className="h-4 w-4" /> Change password
                </Button>
              </div>
            </Card>

            <Card>
              <CardHeader
                title="Revoke all sessions"
                description="Invalidates every refresh token for your account. Access tokens expire shortly afterwards."
                icon={<MonitorSmartphone className="h-4 w-4" />}
              />
              <div className="space-y-4 p-5">
                <Alert tone="warning" title="You will be signed out">
                  Other browsers and API clients using your refresh token will also lose access. Sign in again
                  with your current password.
                </Alert>
                <Button variant="danger" onClick={() => setRevokeOpen(true)}>
                  <MonitorSmartphone className="h-4 w-4" /> Revoke all sessions
                </Button>
              </div>
            </Card>
          </div>

          {sessions.error ? (
            <Alert tone="error" title="Sessions unavailable">
              {sessions.error}
            </Alert>
          ) : null}

          <Card>
            <CardHeader
              title="My refresh sessions"
              description="Opaque refresh tokens are stored hashed. This table never shows the token itself."
              icon={<MonitorSmartphone className="h-4 w-4" />}
              actions={
                <Button variant="outline" size="sm" onClick={() => void sessions.refresh()} loading={sessions.loading}>
                  <RefreshCw className="h-4 w-4" /> Refresh
                </Button>
              }
            />
            {sessions.loading && !sessions.data ? <Spinner label="Loading sessions" /> : null}
            <DataTable
              rows={sessionRows}
              rowKey={(row) => row.id}
              loading={sessions.loading}
              dense
              empty="No refresh sessions are recorded for this account."
              columns={[
                {
                  key: "state",
                  header: "State",
                  render: (row) => (
                    <Badge status={sessionState(row)} dot>
                      {titleCase(sessionState(row))}
                    </Badge>
                  ),
                },
                {
                  key: "created",
                  header: "Created",
                  render: (row) => <span className="text-xs">{formatDate(row.created_at)}</span>,
                },
                {
                  key: "used",
                  header: "Last used",
                  render: (row) => (
                    <span className="text-xs">{row.last_used_at ? formatRelative(row.last_used_at) : "never"}</span>
                  ),
                },
                {
                  key: "expires",
                  header: "Expires",
                  render: (row) => <span className="text-xs">{formatDate(row.expires_at)}</span>,
                },
                {
                  key: "ip",
                  header: "Source",
                  render: (row) => (
                    <span className="text-xs text-slate-500 dark:text-slate-400">{row.ip_address ?? "—"}</span>
                  ),
                },
                {
                  key: "agent",
                  header: "Client",
                  render: (row) => (
                    <span className="block max-w-xs truncate text-xs text-slate-500 dark:text-slate-400" title={row.user_agent ?? ""}>
                      {row.user_agent ?? "—"}
                    </span>
                  ),
                },
                {
                  key: "reason",
                  header: "Revoked",
                  render: (row) => (
                    <span className="text-xs text-slate-500 dark:text-slate-400">
                      {row.revoke_reason ?? "—"}
                    </span>
                  ),
                },
              ]}
            />
          </Card>

          <Alert tone="info" title="Session security">
            Access tokens are short-lived JWTs. Refresh tokens are random opaque values stored as hashes and
            rotated on use. Revoking all sessions is recorded in the audit log.
          </Alert>
        </>
      ) : null}

      <Modal
        open={settingForm !== null}
        onClose={() => setSettingForm(null)}
        title={settingForm?.mode === "create" ? "Create setting" : "Edit setting"}
        description="Values are stored as JSON. Plain text is accepted when it is not valid JSON."
        footer={
          <>
            <Button variant="outline" onClick={() => setSettingForm(null)} disabled={busy === "setting"}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void saveSetting()} loading={busy === "setting"}>
              Save setting
            </Button>
          </>
        }
      >
        {settingForm ? (
          <div className="space-y-4">
            <Field label="Key" required hint="Lowercase letters, digits, dots, dashes and underscores.">
              <TextInput
                value={settingForm.key}
                disabled={settingForm.mode === "edit"}
                placeholder="app.ui.refresh_seconds"
                onChange={(event) => setSettingForm({ ...settingForm, key: event.target.value })}
              />
            </Field>
            <Field label="Value" hint='Examples: 30, "text", true, {"enabled": true}'>
              <Textarea
                value={settingForm.value}
                className="min-h-[120px]"
                onChange={(event) => setSettingForm({ ...settingForm, value: event.target.value })}
              />
            </Field>
            <Field label="Description">
              <TextInput
                value={settingForm.description}
                maxLength={4000}
                onChange={(event) => setSettingForm({ ...settingForm, description: event.target.value })}
              />
            </Field>
          </div>
        ) : null}
      </Modal>

      <ConfirmDialog
        open={deleteSetting !== null}
        onClose={() => setDeleteSetting(null)}
        onConfirm={() => void confirmDeleteSetting()}
        title={`Delete setting ${deleteSetting?.key ?? ""}?`}
        description="The key and value are removed from the database. This action is audited."
        confirmLabel="Delete setting"
        danger
        loading={busy === "delete-setting"}
      />

      <Modal
        open={userModal !== null}
        onClose={() => setUserModal(null)}
        title={userModal?.mode === "create" ? "Create user" : `Edit ${userModal?.user?.username ?? "user"}`}
        description={
          userModal?.mode === "edit"
            ? "Leave the password blank to keep the current password. Setting a password revokes that account's sessions."
            : "Passwords must satisfy the server password policy."
        }
        footer={
          <>
            <Button variant="outline" onClick={() => setUserModal(null)} disabled={busy === "user"}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void saveUser()} loading={busy === "user"}>
              {userModal?.mode === "create" ? "Create user" : "Save changes"}
            </Button>
          </>
        }
      >
        {userModal ? (
          <div className="space-y-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="Username" required>
                <TextInput
                  value={userModal.form.username}
                  disabled={userModal.mode === "edit"}
                  onChange={(event) =>
                    setUserModal({ ...userModal, form: { ...userModal.form, username: event.target.value } })
                  }
                />
              </Field>
              <Field
                label={userModal.mode === "create" ? "Password" : "New password"}
                required={userModal.mode === "create"}
              >
                <TextInput
                  type="password"
                  autoComplete="new-password"
                  value={userModal.form.password}
                  onChange={(event) =>
                    setUserModal({ ...userModal, form: { ...userModal.form, password: event.target.value } })
                  }
                />
              </Field>
              <Field label="Role" required>
                <Select
                  value={userModal.form.role}
                  onChange={(event) =>
                    setUserModal({ ...userModal, form: { ...userModal.form, role: event.target.value } })
                  }
                >
                  {roleOptions.map((role) => (
                    <option key={role} value={role}>
                      {titleCase(role)}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Email">
                <TextInput
                  type="email"
                  value={userModal.form.email}
                  onChange={(event) =>
                    setUserModal({ ...userModal, form: { ...userModal.form, email: event.target.value } })
                  }
                />
              </Field>
            </div>
            <Field label="Full name">
              <TextInput
                value={userModal.form.full_name}
                onChange={(event) =>
                  setUserModal({ ...userModal, form: { ...userModal.form, full_name: event.target.value } })
                }
              />
            </Field>
            <Toggle
              checked={userModal.form.is_active}
              onChange={(checked) =>
                setUserModal({ ...userModal, form: { ...userModal.form, is_active: checked } })
              }
              label="Account active"
              description="Disabled accounts cannot sign in, but their audit history is retained."
            />
            {userModal.mode === "edit" && userModal.user ? (
              <Alert tone="info">
                Email and full name cannot be cleared through this form; enter a replacement value instead.
                You cannot change your own role or disable your own account.
              </Alert>
            ) : null}
          </div>
        ) : null}
      </Modal>

      <ConfirmDialog
        open={revokeOpen}
        onClose={() => setRevokeOpen(false)}
        onConfirm={() => void revokeAll()}
        title="Revoke all refresh sessions?"
        description="Every browser and API client using your account will have to sign in again."
        confirmLabel="Revoke all sessions"
        danger
        loading={busy === "revoke"}
      />
    </div>
  );
}
