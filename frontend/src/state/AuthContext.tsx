import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, loginRequest, refreshRequest, tokens } from "../lib/api";
import type { Permission, User } from "../lib/types";

type AuthStatus = "loading" | "authenticated" | "anonymous";

interface AuthContextValue {
  status: AuthStatus;
  user: User | null;
  login: (username: string, password: string) => Promise<User>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
  can: (permission: Permission | Permission[]) => boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<User | null>(null);

  const loadMe = useCallback(async () => {
    const me = await api.get<User>("/auth/me", undefined, { retryAuth: true });
    setUser(me);
    setStatus("authenticated");
    return me;
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!tokens.refreshToken) {
        setStatus("anonymous");
        return;
      }
      const access = await refreshRequest();
      if (!access) {
        if (!cancelled) setStatus("anonymous");
        return;
      }
      try {
        const me = await api.get<User>("/auth/me", undefined, { retryAuth: false });
        if (!cancelled) {
          setUser(me);
          setStatus("authenticated");
        }
      } catch {
        if (!cancelled) {
          tokens.clear();
          setStatus("anonymous");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    return tokens.subscribe(() => {
      if (!tokens.refreshToken) {
        setUser(null);
        setStatus("anonymous");
      }
    });
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const response = await loginRequest(username, password);
    tokens.setTokens(response.access_token, response.refresh_token);
    setUser(response.user);
    setStatus("authenticated");
    return response.user;
  }, []);

  const logout = useCallback(async () => {
    const refreshToken = tokens.refreshToken;
    try {
      if (refreshToken) await api.post("/auth/logout", { refresh_token: refreshToken });
    } catch {
      /* the local session is cleared regardless */
    } finally {
      tokens.clear();
      setUser(null);
      setStatus("anonymous");
    }
  }, []);

  const refreshUser = useCallback(async () => {
    await loadMe();
  }, [loadMe]);

  const changePassword = useCallback(async (currentPassword: string, newPassword: string) => {
    await api.post("/auth/change-password", {
      current_password: currentPassword,
      new_password: newPassword,
    });
    tokens.clear();
    setUser(null);
    setStatus("anonymous");
  }, []);

  const can = useCallback(
    (permission: Permission | Permission[]) => {
      if (!user) return false;
      if (user.permissions.includes("*") || user.is_superuser) return true;
      const required = Array.isArray(permission) ? permission : [permission];
      return required.some((item) => user.permissions.includes(item));
    },
    [user],
  );

  const value = useMemo(
    () => ({ status, user, login, logout, refreshUser, changePassword, can }),
    [status, user, login, logout, refreshUser, changePassword, can],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
