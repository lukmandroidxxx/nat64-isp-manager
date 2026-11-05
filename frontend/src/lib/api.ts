const API_PREFIX = "/api/v1";
const REFRESH_STORAGE_KEY = "nat64mgr.refresh";

export class ApiError extends Error {
  status: number;
  code: string;
  detail: unknown;
  requestId: string | null;

  constructor(status: number, code: string, message: string, detail: unknown = null, requestId: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.requestId = requestId;
  }
}

type Listener = () => void;

class TokenStore {
  accessToken: string | null = null;
  private listeners = new Set<Listener>();

  get refreshToken(): string | null {
    try {
      return localStorage.getItem(REFRESH_STORAGE_KEY);
    } catch {
      return null;
    }
  }

  setTokens(access: string, refresh: string) {
    this.accessToken = access;
    try {
      localStorage.setItem(REFRESH_STORAGE_KEY, refresh);
    } catch {
      /* storage disabled */
    }
    this.emit();
  }

  clear() {
    this.accessToken = null;
    try {
      localStorage.removeItem(REFRESH_STORAGE_KEY);
    } catch {
      /* storage disabled */
    }
    this.emit();
  }

  subscribe(listener: Listener) {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private emit() {
    this.listeners.forEach((listener) => listener());
  }
}

export const tokens = new TokenStore();

function buildQuery(params?: Record<string, unknown>): string {
  if (!params) return "";
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      value.forEach((item) => search.append(key, String(item)));
    } else {
      search.set(key, String(value));
    }
  }
  const query = search.toString();
  return query ? `?${query}` : "";
}

async function parseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function normaliseError(status: number, body: unknown, headers: Headers): ApiError {
  const requestId = headers.get("x-request-id");
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "object" && detail !== null) {
      const record = detail as Record<string, unknown>;
      const code = typeof record.code === "string" ? record.code : `http_${status}`;
      const message =
        typeof record.message === "string" ? record.message : `Request failed with status ${status}`;
      return new ApiError(status, code, message, record.detail ?? record, requestId);
    }
    if (typeof detail === "string") {
      return new ApiError(status, `http_${status}`, detail, null, requestId);
    }
    if (Array.isArray(detail)) {
      const first = detail[0] as { msg?: string; loc?: unknown[] } | undefined;
      const message = first?.msg ?? `Validation failed (${status})`;
      return new ApiError(status, "validation_failed", message, detail, requestId);
    }
  }
  return new ApiError(status, `http_${status}`, `Request failed with status ${status}`, body, requestId);
}

let refreshInFlight: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = tokens.refreshToken;
  if (!refreshToken) return null;
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const response = await fetch(`${API_PREFIX}/auth/refresh`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
        if (!response.ok) {
          tokens.clear();
          return null;
        }
        const data = (await response.json()) as { access_token: string; refresh_token: string };
        tokens.setTokens(data.access_token, data.refresh_token);
        return data.access_token;
      } catch {
        return null;
      } finally {
        refreshInFlight = null;
      }
    })();
  }
  return refreshInFlight;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  params?: Record<string, unknown>;
  signal?: AbortSignal;
  timeoutMs?: number;
  retryAuth?: boolean;
  raw?: boolean;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, params, signal, timeoutMs = 20000, retryAuth = true, raw = false } = options;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  const forwardAbort = () => controller.abort();
  signal?.addEventListener("abort", forwardAbort);

  const headers: Record<string, string> = { accept: "application/json" };
  if (body !== undefined) headers["content-type"] = "application/json";
  if (tokens.accessToken) headers.authorization = `Bearer ${tokens.accessToken}`;

  let response: Response;
  try {
    response = await fetch(`${API_PREFIX}${path}${buildQuery(params)}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      credentials: "same-origin",
    });
  } catch (error) {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", forwardAbort);
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(0, "timeout", "The request timed out. Check the server and try again.");
    }
    throw new ApiError(0, "network_error", "Cannot reach the server. Check your connection.");
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", forwardAbort);
  }

  if (response.status === 401 && retryAuth && tokens.refreshToken && !path.startsWith("/auth/")) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      return request<T>(path, { ...options, retryAuth: false });
    }
  }

  if (raw) {
    if (!response.ok) throw normaliseError(response.status, await parseBody(response), response.headers);
    return response as T;
  }

  const payload = await parseBody(response);
  if (!response.ok) throw normaliseError(response.status, payload, response.headers);
  return payload as T;
}

export const api = {
  get: <T>(path: string, params?: Record<string, unknown>, options: Omit<RequestOptions, "method" | "params"> = {}) =>
    request<T>(path, { ...options, method: "GET", params }),
  post: <T>(path: string, body?: unknown, params?: Record<string, unknown>) =>
    request<T>(path, { method: "POST", body, params }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: "PATCH", body }),
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: "PUT", body }),
  delete: <T>(path: string, params?: Record<string, unknown>) =>
    request<T>(path, { method: "DELETE", params }),
};

export async function downloadFile(path: string, filename: string, params?: Record<string, unknown>) {
  const response = await request<Response>(path, { method: "GET", params, raw: true, timeoutMs: 120000 });
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export async function loginRequest(username: string, password: string) {
  return request<{
    access_token: string;
    refresh_token: string;
    token_type: string;
    expires_in: number;
    refresh_expires_in: number;
    user: import("./types").User;
  }>("/auth/login", {
    method: "POST",
    body: { username, password },
    retryAuth: false,
  });
}

export async function refreshRequest() {
  const refreshToken = tokens.refreshToken;
  if (!refreshToken) return null;
  try {
    const response = await fetch(`${API_PREFIX}/auth/refresh`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!response.ok) {
      tokens.clear();
      return null;
    }
    const data = (await response.json()) as { access_token: string; refresh_token: string };
    tokens.setTokens(data.access_token, data.refresh_token);
    return data.access_token;
  } catch {
    return null;
  }
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "network_error" || error.code === "timeout") return error.message;
    return error.requestId ? `${error.message} (request ${error.requestId})` : error.message;
  }
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}

export function errorDetails(error: unknown): string[] {
  if (!(error instanceof ApiError)) return [];
  const detail = error.detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object") {
        const record = item as Record<string, unknown>;
        const loc = Array.isArray(record.loc) ? record.loc.filter((part) => part !== "body").join(".") : "";
        return loc ? `${loc}: ${String(record.msg ?? "")}` : String(record.msg ?? JSON.stringify(item));
      }
      return String(item);
    });
  }
  if (typeof detail === "string") return [detail];
  if (detail && typeof detail === "object") {
    const record = detail as Record<string, unknown>;
    if (Array.isArray(record.errors)) return record.errors.map(String);
    return Object.entries(record).map(([key, value]) => `${key}: ${String(value)}`);
  }
  return [];
}
