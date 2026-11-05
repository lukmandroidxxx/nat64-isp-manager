import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { Layout } from "./components/Layout";
import { AuthProvider, useAuth } from "./state/AuthContext";
import { ThemeProvider } from "./state/ThemeContext";
import { ToastProvider } from "./state/ToastContext";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Jool from "./pages/Jool";
import Pools from "./pages/Pools";
import Clients from "./pages/Clients";
import Sessions from "./pages/Sessions";
import Dns64 from "./pages/Dns64";
import Firewall from "./pages/Firewall";
import NetworkPage from "./pages/Network";
import Monitoring from "./pages/Monitoring";
import Logs from "./pages/Logs";
import Backups from "./pages/Backups";
import SettingsPage from "./pages/Settings";
import Changes from "./pages/Changes";
import Ha from "./pages/Ha";
import Wizard from "./pages/Wizard";
import NotFound from "./pages/NotFound";

function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center gap-3 text-sm text-slate-500 dark:text-slate-400">
        <Loader2 className="h-5 w-5 animate-spin" /> Restoring session…
      </div>
    );
  }
  if (status === "anonymous") {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return <>{children}</>;
}

export default function App() {
  return (
    <ThemeProvider>
      <ToastProvider>
        <AuthProvider>
          <BrowserRouter>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route
                element={
                  <RequireAuth>
                    <Layout />
                  </RequireAuth>
                }
              >
                <Route index element={<Dashboard />} />
                <Route path="/jool" element={<Jool />} />
                <Route path="/pools" element={<Pools />} />
                <Route path="/clients" element={<Clients />} />
                <Route path="/sessions" element={<Sessions />} />
                <Route path="/dns64" element={<Dns64 />} />
                <Route path="/firewall" element={<Firewall />} />
                <Route path="/network" element={<NetworkPage />} />
                <Route path="/monitoring" element={<Monitoring />} />
                <Route path="/logs" element={<Logs />} />
                <Route path="/backups" element={<Backups />} />
                <Route path="/settings" element={<SettingsPage />} />
                <Route path="/changes" element={<Changes />} />
                <Route path="/ha" element={<Ha />} />
                <Route path="/wizard" element={<Wizard />} />
                <Route path="*" element={<NotFound />} />
              </Route>
            </Routes>
          </BrowserRouter>
        </AuthProvider>
      </ToastProvider>
    </ThemeProvider>
  );
}
