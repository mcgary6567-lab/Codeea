import { useState } from "react";
import { Copy, ExternalLink, KeyRound, Send } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, useAction, useToast } from "./ui";

/** Copy text, with a fallback for browsers that block the clipboard API (http, old WebViews). */
export async function copyText(text: string) {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* fall back */ }
  const ta = document.createElement("textarea");
  ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.select();
  const ok = document.execCommand("copy");
  ta.remove();
  return ok;
}

/**
 * The customer's own khata page: a private link + 6-digit PIN. Works for wholesale clients
 * (`base` = /wholesale/clients/:id/portal) and khata customers (/customers/:id/portal).
 */
export function PortalCard({ base, name, phone, canManage }: { base: string; name: string; phone?: string | null; canManage: boolean }) {
  const { data, reload } = useApi<any>(base);
  const [showPin, setShowPin] = useState(false);
  const { busy, run } = useAction();
  const toast = useToast();
  if (!data) return null;
  const copy = async (text: string, what: string) => { const ok = await copyText(text); toast(ok ? "ok" : "err", ok ? `${what} copied ✓` : "Could not copy — select and copy by hand"); };
  const act = async (path: string, ok: string, ask?: string) => {
    if (ask && !confirm(ask)) return;
    if (await run(() => api(`${base}/${path}`, { body: {} }), ok)) { reload(); if (path === "new") setShowPin(true); }
  };
  return (
    <div className="card p-4">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 font-semibold"><KeyRound size={17} /> Own khata page (link + PIN)</h2>
        {data.enabled ? <Badge tone="green">On</Badge> : <Badge tone="slate">Off</Badge>}
      </div>
      <p className="mb-3 text-sm text-slate-600">{name} opens the link on their phone and enters the PIN to see balance, every entry, payments and monthly bills.</p>
      {data.enabled ? <>
        <div className="mb-2 flex flex-wrap items-center gap-2 rounded-lg bg-slate-50 p-2 text-sm">
          <code className="min-w-0 flex-1 break-all">{data.url}</code>
          <button type="button" className="btn-secondary min-h-9 !py-1 text-xs sm:min-h-0" onClick={() => copy(data.url, "Link")}><Copy size={13} /> Copy link</button>
          <a className="btn-secondary min-h-9 !py-1 text-xs sm:min-h-0" href={data.url} target="_blank" rel="noreferrer"><ExternalLink size={13} /> Open</a>
        </div>
        <div className="mb-3 flex flex-wrap items-center gap-3 rounded-lg bg-amber-50 p-2 ring-1 ring-amber-200">
          <span className="text-sm text-amber-900">PIN</span>
          <span className="font-mono text-2xl font-bold tracking-[.3em]">{showPin ? data.pin : "••••••"}</span>
          <button type="button" className="min-h-9 px-1 text-xs underline sm:min-h-0" onClick={() => setShowPin(!showPin)}>{showPin ? "Hide" : "Show"}</button>
          <button type="button" className="btn-secondary ml-auto min-h-9 !py-1 text-xs sm:min-h-0" onClick={() => copy(`${name}\n${data.url}\nPIN: ${data.pin}`, "Link and PIN")}><Copy size={13} /> Copy link + PIN</button>
        </div>
      </> : <p className="mb-3 text-sm text-slate-500">The page is turned off — the link does not open.</p>}
      {canManage && (
        <div className="flex flex-wrap gap-2">
          {phone && <button className="btn-primary" disabled={busy} onClick={() => act("send", "Link and PIN sent on WhatsApp")}><Send size={15} /> Send link + PIN on WhatsApp</button>}
          <button className="btn-secondary" disabled={busy} onClick={() => act("new", "New link and PIN made", "Make a new link and PIN? The old link and PIN will stop working.")}>New link + PIN</button>
          {data.enabled
            ? <button className="btn-secondary" disabled={busy} onClick={() => act("off", "Page turned off", `Turn off ${name}'s page?`)}>Turn off</button>
            : <button className="btn-secondary" disabled={busy} onClick={() => act("on", "Page turned on")}>Turn on</button>}
        </div>
      )}
      {data.last_seen && <p className="mt-2 text-xs text-slate-500">Last opened {new Date(data.last_seen).toLocaleString("en-PK")}</p>}
    </div>
  );
}
