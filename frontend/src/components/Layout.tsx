import { useEffect, useMemo, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Activity,
  BookOpenCheck,
  ChevronsUpDown,
  DatabaseBackup,
  GitPullRequest,
  Globe2,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Menu,
  Moon,
  Network,
  Router,
  ScrollText,
  ServerCog,
  Settings,
  Shield,
  Sun,
  Users,
  Waypoints,
  X,
} from "lucide-react";
import { errorText } from "../lib/api";
import { titleCase } from "../lib/format";
import { useAuth } from "../state/AuthContext";
import { useTheme } from "../state/ThemeContext";
import { useToast } from "../state/ToastContext";
import { usePolling } from "../hooks/usePolling";
import { Alert, Badge, Button, Field, Modal, TextInput, cn } from "./ui";

interface NavItem {
  to: string;
  label: string;
  icon: typeof LayoutDashboard;
  permission?: string;
  end?: boolean;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    label: "Overview",
    items: [
      { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
      { to: "/changes", label: "Change Requests", icon: GitPullRequest },
    ],
  },
  {
    label: "NAT64",
    items: [
      { to: "/jool", label: "Jool Management", icon: ServerCog },
      { to: "/pools", label: "IPv4 Pool", icon: DatabaseBackup },
      { to: "/clients", label: "IPv6 Clients", icon: Users },
      { to: "/sessions", label: "NAT64 Sessions", icon: Waypoints },
      { to: "/dns64", label: "DNS64", icon: Globe2 },
    ],
  },
  {
    label: "Network",
    items: [
      { to: "/firewall", label: "Firewall", icon: Shield },
      { to: "/network", label: "Network Interfaces", icon: Network },
      { to: "/ha", label: "High Availability", icon: Router },
    ],
  },
  {
    label: "System",
    items: [
      { to: "/monitoring", label: "System Monitoring", icon: Activity },
      { to: "/logs", label: "Logs", icon: ScrollText },
      { to: "/backups", label: "Backup & Restore", icon: DatabaseBackup },
    ],
  },
  {
    label: "Administration",
    items: [
      { to: "/settings", label: "Settings", icon: Settings },
      { to: "/wizard", label: "Installation Wizard", icon: BookOpenCheck },
    ],
  },
];

function BrandMark() {
  return (
    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-600 text-white shadow-sm">
      <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M4 7h11a5 5 0 0 1 0 10H8" strokeLinecap="round" />
        <path d="M7 4 4 7l3 3" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M17 20l3-3-3-3" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
}

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const { user, can } = useAuth();
  const { data: status } = usePolling<{ host: { mode: string; available: boolean; reason: string | null } }>(
    "/system/health",
    undefined,
    { interval: 15000 },
  );

  const groups = useMemo(
    () =>
      NAV_GROUPS.map((group) => ({
        ...group,
        items: group.items.filter((item) => !item.permission || can(item.permission as never)),
      })).filter((group) => group.items.length > 0),
    [can],
  );

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-16 shrink-0 items-center gap-3 border-b border-slate-200 px-5 dark:border-slate-800">
        <BrandMark />
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-900 dark:text-white">NAT64 ISP Manager</p>
          <p className="text-[11px] uppercase tracking-wider text-slate-400">Jool 4 control plane</p>
        </div>
      </div>

      <nav className="min-h-0 flex-1 space-y-5 overflow-y-auto px-3 py-4">
        {groups.map((group) => (
          <div key={group.label}>
            <p className="mb-1 px-3 text-[10px] font-semibold uppercase tracking-widest text-slate-400 dark:text-slate-500">
              {group.label}
            </p>
            <div className="space-y-0.5">
              {group.items.map((item) => {
                const Icon = item.icon;
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    onClick={onNavigate}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                        isActive
                          ? "bg-brand-50 text-brand-700 dark:bg-brand-500/10 dark:text-brand-300"
                          : "text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800/70 dark:hover:text-slate-100",
                      )
                    }
                  >
                    <Icon className="h-4 w-4 shrink-0" />
                    <span className="truncate">{item.label}</span>
                  </NavLink>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      <div className="shrink-0 border-t border-slate-200 p-3 dark:border-slate-800">
        <div className="rounded-lg bg-slate-50 p-3 text-xs dark:bg-slate-800/60">
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium text-slate-700 dark:text-slate-300">Host control</span>
            <Badge status={status?.host?.available ? "online" : status?.host?.mode ?? "unknown"} dot>
              {status?.host?.available ? "Available" : titleCase(status?.host?.mode ?? "unknown")}
            </Badge>
          </div>
          {status?.host && !status.host.available ? (
            <p className="mt-1.5 leading-snug text-slate-500 dark:text-slate-400">
              {status.host.reason ?? "Host helper unavailable"}
            </p>
          ) : null}
          {user ? (
            <p className="mt-2 truncate text-[11px] text-slate-400">
              {user.username} · {titleCase(user.role)}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ChangePasswordModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { changePassword } = useAuth();
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setCurrent("");
      setNext("");
      setConfirm("");
      setError(null);
    }
  }, [open]);

  const submit = async () => {
    setError(null);
    if (next.length < 12) {
      setError("The new password must be at least 12 characters.");
      return;
    }
    if (next !== confirm) {
      setError("The new passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await changePassword(current, next);
      toast.success("Password changed", "Sign in again with your new password.");
      onClose();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Change password"
      description="Changing the password revokes all refresh sessions and signs you out."
      size="sm"
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" onClick={submit} loading={busy}>
            Change password
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {error ? <Alert tone="error">{error}</Alert> : null}
        <Field label="Current password" required>
          <TextInput type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" />
        </Field>
        <Field label="New password" required hint="At least 12 characters, including upper, lower, digit and symbol.">
          <TextInput type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" />
        </Field>
        <Field label="Confirm new password" required>
          <TextInput type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" />
        </Field>
      </div>
    </Modal>
  );
}

export function Layout() {
  const { user, logout } = useAuth();
  const { theme, toggle } = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [userMenu, setUserMenu] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);

  const health = usePolling<{ status: string }>("/system/health", undefined, { interval: 10000 });
  const pending = usePolling<{ pending: number }>("/changes", { page_size: 1 }, { interval: 15000 });

  useEffect(() => {
    setMobileOpen(false);
    setUserMenu(false);
  }, [location.pathname]);

  const title = useMemo(() => {
    const groups = NAV_GROUPS.flatMap((group) => group.items);
    const match = groups
      .filter((item) => (item.end ? location.pathname === item.to : location.pathname.startsWith(item.to)))
      .sort((a, b) => b.to.length - a.to.length)[0];
    return match?.label ?? "Dashboard";
  }, [location.pathname]);

  useEffect(() => {
    document.title = `${title} · NAT64 ISP Manager`;
  }, [title]);

  return (
    <div className="flex min-h-screen bg-slate-50 dark:bg-slate-950">
      <aside className="hidden w-64 shrink-0 border-r border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 lg:block">
        <div className="sticky top-0 h-screen">
          <SidebarContent />
        </div>
      </aside>

      {mobileOpen ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-slate-950/60 backdrop-blur-sm" onClick={() => setMobileOpen(false)} />
          <aside className="relative h-full w-72 max-w-[85vw] border-r border-slate-200 bg-white shadow-2xl dark:border-slate-800 dark:bg-slate-900">
            <button
              type="button"
              className="absolute right-3 top-4 rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800"
              onClick={() => setMobileOpen(false)}
              aria-label="Close navigation"
            >
              <X className="h-5 w-5" />
            </button>
            <SidebarContent onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-16 items-center justify-between gap-3 border-b border-slate-200 bg-white/90 px-4 backdrop-blur dark:border-slate-800 dark:bg-slate-900/90 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <button
              type="button"
              className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 lg:hidden"
              onClick={() => setMobileOpen(true)}
              aria-label="Open navigation"
            >
              <Menu className="h-5 w-5" />
            </button>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-slate-800 dark:text-slate-100">{title}</p>
              <div className="flex items-center gap-1.5 text-[11px] text-slate-400">
                <span
                  className={cn(
                    "inline-block h-1.5 w-1.5 rounded-full",
                    health.error ? "bg-amber-500" : "bg-emerald-500",
                  )}
                />
                {health.error ? "Reconnecting…" : `Live · ${titleCase(health.data?.status ?? "ok")}`}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-1.5 sm:gap-2">
            {pending.data?.pending ? (
              <button
                type="button"
                onClick={() => navigate("/changes")}
                className="hidden items-center gap-1.5 rounded-full border border-amber-300 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-800 hover:bg-amber-100 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-300 sm:inline-flex"
              >
                <GitPullRequest className="h-3.5 w-3.5" />
                {pending.data.pending} pending
              </button>
            ) : null}
            <Button variant="ghost" size="icon" onClick={toggle} aria-label="Toggle theme">
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </Button>

            <div className="relative">
              <button
                type="button"
                onClick={() => setUserMenu((open) => !open)}
                className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-slate-100 dark:hover:bg-slate-800"
              >
                <div className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-600 text-xs font-semibold uppercase text-white">
                  {(user?.username ?? "?").slice(0, 2)}
                </div>
                <div className="hidden text-left sm:block">
                  <p className="max-w-[10rem] truncate text-xs font-semibold text-slate-800 dark:text-slate-100">
                    {user?.username}
                  </p>
                  <p className="text-[10px] uppercase tracking-wide text-slate-400">{titleCase(user?.role)}</p>
                </div>
                <ChevronsUpDown className="h-3.5 w-3.5 text-slate-400" />
              </button>
              {userMenu ? (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setUserMenu(false)} />
                  <div className="absolute right-0 z-20 mt-2 w-56 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-lg dark:border-slate-800 dark:bg-slate-900">
                    <div className="border-b border-slate-100 px-4 py-3 dark:border-slate-800">
                      <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                        {user?.full_name ?? user?.username}
                      </p>
                      <p className="truncate text-xs text-slate-500 dark:text-slate-400">{user?.email ?? "No email"}</p>
                    </div>
                    <div className="p-1">
                      <button
                        type="button"
                        onClick={() => {
                          setUserMenu(false);
                          setPasswordOpen(true);
                        }}
                        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
                      >
                        <KeyRound className="h-4 w-4" /> Change password
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setUserMenu(false);
                          void logout();
                        }}
                        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-950/40"
                      >
                        <LogOut className="h-4 w-4" /> Sign out
                      </button>
                    </div>
                  </div>
                </>
              ) : null}
            </div>
          </div>
        </header>

        {user?.must_change_password ? (
          <div className="px-4 pt-4 sm:px-6">
            <Alert
              tone="warning"
              title="Change your bootstrap password"
              actions={
                <Button size="sm" variant="outline" onClick={() => setPasswordOpen(true)}>
                  <KeyRound className="h-4 w-4" /> Change password now
                </Button>
              }
            >
              This account is still using a password that must be changed before it can be considered
              secure.
            </Alert>
          </div>
        ) : null}

        <main className="min-w-0 flex-1 px-4 py-6 sm:px-6">
          <Outlet />
        </main>

        <footer className="border-t border-slate-200 px-4 py-3 text-[11px] text-slate-400 dark:border-slate-800 sm:px-6">
          NAT64 ISP Manager · dynamic per-connection IPv4 allocation · host data-plane changes require
          approval
        </footer>
      </div>

      <ChangePasswordModal open={passwordOpen} onClose={() => setPasswordOpen(false)} />
    </div>
  );
}
