import { useEffect, useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { AlertCircle, KeyRound, Loader2, Lock, Moon, ShieldCheck, Sun, User } from "lucide-react";
import { errorText } from "../lib/api";
import { useAuth } from "../state/AuthContext";
import { useTheme } from "../state/ThemeContext";
import { Button, Field, TextInput } from "../components/ui";

export default function Login() {
  const { status, login, user } = useAuth();
  const { theme, toggle } = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    document.title = "Sign in · NAT64 ISP Manager";
  }, []);

  if (status === "authenticated") {
    return <Navigate to={from} replace />;
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!username.trim() || !password) {
      setError("Enter your username and password.");
      return;
    }
    setBusy(true);
    try {
      await login(username.trim(), password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-slate-100 px-4 py-10 dark:bg-slate-950">
      <div
        className="pointer-events-none absolute -left-32 -top-32 h-96 w-96 rounded-full bg-brand-500/20 blur-3xl"
        aria-hidden
      />
      <div
        className="pointer-events-none absolute -bottom-40 -right-24 h-[28rem] w-[28rem] rounded-full bg-violet-500/10 blur-3xl"
        aria-hidden
      />

      <button
        type="button"
        onClick={toggle}
        className="absolute right-4 top-4 rounded-lg border border-slate-200 bg-white p-2 text-slate-500 shadow-sm hover:text-slate-800 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
        aria-label="Toggle colour theme"
      >
        {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      </button>

      <div className="relative w-full max-w-md">
        <div className="mb-6 flex flex-col items-center text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-600 text-white shadow-lg shadow-brand-600/30">
            <svg viewBox="0 0 24 24" className="h-7 w-7" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M4 7h11a5 5 0 0 1 0 10H8" strokeLinecap="round" />
              <path d="M7 4 4 7l3 3" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M17 20l3-3-3-3" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <h1 className="mt-4 text-2xl font-semibold tracking-tight text-slate-900 dark:text-white">
            NAT64 ISP Manager
          </h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Jool 4 · DNS64 · IPv4 pool orchestration
          </p>
        </div>

        <form
          onSubmit={submit}
          className="rounded-2xl border border-slate-200 bg-white p-6 shadow-xl dark:border-slate-800 dark:bg-slate-900"
        >
          <div className="mb-5 flex items-center gap-2 text-sm font-medium text-slate-700 dark:text-slate-200">
            <KeyRound className="h-4 w-4 text-brand-500" />
            Sign in to the control plane
          </div>

          {error ? (
            <div className="mb-4 flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950/50 dark:text-rose-300">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          ) : null}

          {status === "loading" ? (
            <div className="mb-4 flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Checking for an existing session…
            </div>
          ) : null}

          <div className="space-y-4">
            <Field label="Username" htmlFor="login-username" required>
              <div className="relative">
                <User className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                <TextInput
                  id="login-username"
                  className="pl-9"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  autoComplete="username"
                  autoFocus
                  disabled={busy}
                />
              </div>
            </Field>

            <Field label="Password" htmlFor="login-password" required>
              <div className="relative">
                <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                <TextInput
                  id="login-password"
                  type="password"
                  className="pl-9"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="current-password"
                  disabled={busy}
                />
              </div>
            </Field>
          </div>

          <Button type="submit" variant="primary" size="lg" className="mt-5 w-full justify-center" loading={busy}>
            Sign in
          </Button>

          <p className="mt-4 flex items-start gap-2 text-xs leading-relaxed text-slate-500 dark:text-slate-400">
            <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
            All privileged host changes are dry-run, require an approver, and are recorded in the audit
            log. Credentials are never stored in the browser beyond a rotating refresh token.
          </p>
        </form>

        <p className="mt-6 text-center text-[11px] text-slate-400 dark:text-slate-500">
          {user ? `Signed in as ${user.username}` : "Unauthorised access is prohibited and audited."}
        </p>
      </div>
    </div>
  );
}
