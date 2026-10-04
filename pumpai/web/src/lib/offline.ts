import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "./api";

/**
 * POS sales made without internet are kept on the tablet and uploaded when the connection
 * returns. Each sale carries a client_uid so a sale sent twice is saved once, and offline_at so
 * it is billed at the price in force when it was made.
 */
const QUEUE = "pumpai_offline_sales";
const FAILED = "pumpai_offline_failed";
const EVT = "pumpai-queue";

export type QueuedSale = { body: any; queued_at: string; label: string; error?: string };

const read = (k: string): QueuedSale[] => { try { return JSON.parse(localStorage.getItem(k) ?? "[]"); } catch { return []; } };
const write = (k: string, v: QueuedSale[]) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage blocked */ } window.dispatchEvent(new Event(EVT)); };

export const newUid = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`);
/** fetch() rejects with a TypeError when there is no connection; HTTP errors come back as ApiError. */
export const isOffline = (e: unknown) => !(e instanceof ApiError);

export function queueSale(body: any, label: string) {
  write(QUEUE, [...read(QUEUE), { body: { ...body, offline_at: new Date().toISOString() }, queued_at: new Date().toISOString(), label }]);
}
export function dropQueued(uid: string) { write(QUEUE, read(QUEUE).filter((q) => q.body.client_uid !== uid)); }
export function dismissFailed(uid: string) { write(FAILED, read(FAILED).filter((q) => q.body.client_uid !== uid)); }

let flushing = false;
/** Upload queued sales one by one; stops at the first connection error. */
export async function flushQueue(): Promise<number> {
  if (flushing) return 0;
  flushing = true;
  let sent = 0;
  try {
    for (const q of read(QUEUE)) {
      try {
        await api("/sales", { body: q.body });
        sent++;
      } catch (e) {
        if (isOffline(e) || (e as ApiError).status >= 500 || (e as ApiError).status === 401) break;
        // rejected (e.g. shift already closed): keep it visible for the manager instead of retrying forever
        write(FAILED, [...read(FAILED), { ...q, error: (e as Error).message }]);
      }
      write(QUEUE, read(QUEUE).filter((x) => x.body.client_uid !== q.body.client_uid));
    }
  } finally { flushing = false; }
  return sent;
}

export function useOfflineQueue(onSynced?: () => void) {
  const [state, setState] = useState(() => ({ pending: read(QUEUE), failed: read(FAILED), online: navigator.onLine }));
  const refresh = useCallback(() => setState({ pending: read(QUEUE), failed: read(FAILED), online: navigator.onLine }), []);
  useEffect(() => {
    const tryFlush = async () => { refresh(); if (read(QUEUE).length && navigator.onLine && (await flushQueue())) onSynced?.(); refresh(); };
    window.addEventListener(EVT, refresh);
    window.addEventListener("online", tryFlush);
    window.addEventListener("offline", refresh);
    const id = setInterval(tryFlush, 15_000);
    tryFlush();
    return () => { window.removeEventListener(EVT, refresh); window.removeEventListener("online", tryFlush); window.removeEventListener("offline", refresh); clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return state;
}

/** Remember the last good copy of a screen's data so it still opens without internet. */
export const cacheGet = <T,>(k: string): T | null => { try { return JSON.parse(localStorage.getItem(`pumpai_cache_${k}`) ?? "null"); } catch { return null; } };
export const cacheSet = (k: string, v: unknown) => { try { localStorage.setItem(`pumpai_cache_${k}`, JSON.stringify(v)); } catch { /* storage full or blocked */ } };
