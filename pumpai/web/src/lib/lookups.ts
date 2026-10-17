/**
 * The admin-managed lists (customer types, machine types, shop categories, booking services…) from
 * Settings → Lists. Loaded once after sign-in and shared by every page, so a new entry shows up in
 * every dropdown at once. `useLookups(kind)` re-renders when the lists change.
 */
import { useEffect, useState } from "react";
import { api } from "./api";

export type LookupEntry = { id: number; kind: string; key: string; label: string; extra: Record<string, any>; sort: number; active: boolean };
export type LookupField = { key: string; label: string; type: "text" | "number" | "boolean" };
type Data = { kinds: Record<string, { label: string; hint: string; fields: LookupField[] }>; lists: Record<string, LookupEntry[]> };

let cache: Data | null = null;
let loading: Promise<Data> | null = null;
const subs = new Set<() => void>();

export function loadLookups(force = false): Promise<Data> {
  if (cache && !force) return Promise.resolve(cache);
  if (!loading) loading = api<Data>("/lookups").then((d) => { cache = d; loading = null; subs.forEach((f) => f()); return d; }).catch((e) => { loading = null; throw e; });
  return loading;
}
/** After the admin changes a list: refetch and re-render everything that uses it. */
export const invalidateLookups = () => loadLookups(true);

const entry = (kind: string, key: string | null | undefined) => (key ? cache?.lists[kind]?.find((x) => x.key === key) : undefined);
/** Label of a key (falls back to the key itself before the lists have loaded or for an unknown value). */
export const lookupLabel = (kind: string, key: string | null | undefined, fallback?: string) => entry(kind, key)?.label ?? fallback ?? key ?? "";
/** Emoji icon of a key, if the list carries one. */
export const lookupIcon = (kind: string, key: string | null | undefined, fallback = "") => String(entry(kind, key)?.extra?.icon ?? fallback);
/** Icon + label, e.g. "🚓 Police station". */
export const lookupText = (kind: string, key: string | null | undefined) => { const e = entry(kind, key); return e ? `${e.extra?.icon ? e.extra.icon + " " : ""}${e.label}` : key ?? ""; };

/**
 * A `{key: label}` / `{key: icon}` object backed by the live lists, with a hardcoded fallback for
 * before the lists load. Lets old call sites like `TYPE_ICON[c.type]` keep working while reflecting
 * the admin's custom entries. (Default lists match the fallbacks, so nothing flickers.)
 */
export const labelMap = (kind: string, fallback: Record<string, string> = {}) =>
  new Proxy(fallback, { get: (f, k) => (typeof k === "string" ? (entry(kind, k)?.label ?? f[k] ?? k) : (f as any)[k]) }) as Record<string, string>;
export const iconMap = (kind: string, fallback: Record<string, string> = {}) =>
  new Proxy(fallback, { get: (f, k) => (typeof k === "string" ? (String(entry(kind, k)?.extra?.icon ?? f[k] ?? "")) : (f as any)[k]) }) as Record<string, string>;

export function useLookups(kind?: string) {
  const [, tick] = useState(0);
  useEffect(() => {
    const f = () => tick((x) => x + 1);
    subs.add(f);
    loadLookups().then(f).catch(() => {});
    return () => { subs.delete(f); };
  }, []);
  const list: LookupEntry[] = kind ? cache?.lists[kind] ?? [] : [];
  return {
    ready: Boolean(cache), list, kinds: cache?.kinds ?? {}, lists: cache?.lists ?? {},
    label: (key: string | null | undefined, fallback?: string) => lookupLabel(kind ?? "", key, fallback),
    icon: (key: string | null | undefined, fallback = "") => lookupIcon(kind ?? "", key, fallback),
    text: (key: string | null | undefined) => lookupText(kind ?? "", key),
    /** key → label map for a select */
    options: list.map((x) => [x.key, `${x.extra?.icon ? x.extra.icon + " " : ""}${x.label}`] as [string, string]),
  };
}
