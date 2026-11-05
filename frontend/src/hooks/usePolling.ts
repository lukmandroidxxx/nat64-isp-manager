import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorText } from "../lib/api";

interface PollingState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  updatedAt: number | null;
}

interface PollingOptions {
  interval?: number;
  enabled?: boolean;
}

export function usePolling<T>(
  path: string,
  params?: Record<string, unknown>,
  { interval = 5000, enabled = true }: PollingOptions = {},
) {
  const paramKey = JSON.stringify(params ?? {});
  const [state, setState] = useState<PollingState<T>>({
    data: null,
    error: null,
    loading: true,
    updatedAt: null,
  });
  const requestId = useRef(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const load = useCallback(
    async (silent = false) => {
      const id = ++requestId.current;
      if (!silent) setState((current) => ({ ...current, loading: current.data === null }));
      try {
        const data = await api.get<T>(path, JSON.parse(paramKey) as Record<string, unknown>);
        if (!mounted.current || id !== requestId.current) return;
        setState({ data, error: null, loading: false, updatedAt: Date.now() });
      } catch (error) {
        if (!mounted.current || id !== requestId.current) return;
        setState((current) => ({ ...current, error: errorText(error), loading: false }));
      }
    },
    [path, paramKey],
  );

  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    let timer: number | undefined;

    const run = async () => {
      await load(timer !== undefined);
      if (!stopped && interval > 0) {
        timer = window.setTimeout(run, interval);
      }
    };

    void run();

    const onVisible = () => {
      if (document.visibilityState === "visible" && !stopped) {
        if (timer !== undefined) window.clearTimeout(timer);
        void run();
      }
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, interval, load]);

  const refresh = useCallback(() => load(true), [load]);
  return { ...state, refresh, setData: (data: T) => setState((current) => ({ ...current, data })) };
}

export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}
