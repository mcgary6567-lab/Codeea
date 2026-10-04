import { useCallback, useEffect, useRef, useState } from "react";

const TOKEN_KEY = "pumpai_token";
export const getToken = () => { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } };
export const setToken = (t: string | null) => { try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); } catch { /* storage blocked */ } };

export class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }

const DEVICE_KEY = "pumpai_device";
/** Links this tablet to the business so staff can sign in with name + PIN. */
export const getDevice = () => { try { return localStorage.getItem(DEVICE_KEY); } catch { return null; } };
export const setDevice = (t: string | null) => { try { t ? localStorage.setItem(DEVICE_KEY, t) : localStorage.removeItem(DEVICE_KEY); } catch { /* storage blocked */ } };

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown; headers?: Record<string, string> } = {}): Promise<T> {
  const token = getToken();
  const res = await fetch(`/api${path}`, {
    method: opts.method ?? (opts.body ? "POST" : "GET"),
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}), ...opts.headers },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && !path.startsWith("/auth/")) {
    setToken(null);
    window.location.href = "/login";
  }
  if (!res.ok) throw new ApiError(res.status, data.error ?? `Request failed (${res.status})`);
  return data as T;
}

/** Fetch on mount + manual reload; optional polling. */
export function useApi<T = any>(path: string | null, pollMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const pathRef = useRef(path);
  pathRef.current = path;
  const reload = useCallback(async () => {
    if (!pathRef.current) return;
    try {
      const d = await api<T>(pathRef.current);
      setData(d);
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    setLoading(true);
    reload();
    if (!pollMs) return;
    const id = setInterval(reload, pollMs);
    return () => clearInterval(id);
  }, [path, pollMs, reload]);
  return { data, error, loading, reload, setData };
}

/** Subscribe to server-sent live events (new WhatsApp messages, orders...). */
export function useLiveEvents(onEvent: (e: any) => void, enabled = true) {
  const cb = useRef(onEvent);
  cb.current = onEvent;
  useEffect(() => {
    const token = getToken();
    if (!token || !enabled) return;
    const es = new EventSource(`/api/events?token=${encodeURIComponent(token)}`);
    es.onmessage = (m) => { try { cb.current(JSON.parse(m.data)); } catch { /* ignore */ } };
    return () => es.close();
  }, [enabled]);
}
