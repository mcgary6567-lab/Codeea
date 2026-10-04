import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Bell, CheckCheck, TrendingUp, TrendingDown } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Field, Modal, useAction } from "./ui";
import { PRODUCTS, ago, num } from "../lib/format";

type NotifData = { items: any[]; unread: number; pending_ack: any[]; open_shift: { id: number; readings: any[] } | null };
type CtxT = { data: NotifData | null; reload: () => void; snoozed: number | null; setSnoozed: (id: number | null) => void };
const Ctx = createContext<CtxT>({ data: null, reload: () => {}, snoozed: null, setSnoozed: () => {} });
export const useNotifications = () => useContext(Ctx);

export function NotificationsProvider({ children }: { children: ReactNode }) {
  const { data, reload } = useApi<NotifData>("/notifications", 20_000);
  const [snoozed, setSnoozed] = useState<number | null>(null);
  return (
    <Ctx.Provider value={{ data, reload, snoozed, setSnoozed }}>
      {children}
      <PriceChangeGate />
    </Ctx.Provider>
  );
}

export function NotificationBell({ dark }: { dark?: boolean }) {
  const { data, reload, setSnoozed } = useNotifications();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const unread = data?.unread ?? 0;
  return (
    <div className="relative" ref={ref}>
      <button aria-label={`Notifications${unread ? ` (${unread} unread)` : ""}`} onClick={() => setOpen(!open)}
        className={`relative rounded-lg p-2 ${dark ? "text-emerald-100 hover:bg-white/10" : "text-slate-600 hover:bg-slate-100"}`}>
        <Bell size={19} />
        {unread > 0 && <span className="absolute -right-0.5 -top-0.5 min-w-[18px] rounded-full bg-red-600 px-1 text-center text-[11px] font-semibold leading-[18px] text-white">{unread}</span>}
      </button>
      {open && (
        <div className={`absolute ${dark ? "left-0" : "right-0"} z-50 mt-2 w-[min(92vw,380px)] overflow-hidden rounded-xl border border-slate-200 bg-white text-slate-900 shadow-xl`}>
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
            <span className="text-sm font-semibold">Notifications</span>
            <PhoneAlerts />
            {unread > 0 && <button className="flex items-center gap-1 text-xs text-brand-600 hover:underline" onClick={() => api("/notifications/read-all", { body: {} }).then(reload)}><CheckCheck size={13} /> Mark all read</button>}
          </div>
          <div className="max-h-[420px] overflow-y-auto">
            {(data?.items ?? []).map((n) => (
              <div key={n.id} className={`border-b border-slate-100 px-4 py-3 text-sm ${n.read_at ? "" : "bg-emerald-50/60"}`}
                onClick={() => !n.read_at && api(`/notifications/${n.id}/read`, { body: {} }).then(reload)}>
                <div className="font-medium">{n.title}</div>
                {n.body && <div className="mt-0.5 whitespace-pre-line text-xs text-slate-600">{n.body}</div>}
                {n.ack_required && !n.acked_at && <button className="mt-1 text-xs font-medium text-brand-600 hover:underline" onClick={() => { setSnoozed(null); setOpen(false); }}>Confirm now →</button>}
                <div className="mt-1 flex justify-between text-[11px] text-slate-400">
                  <span>{ago(n.created_at)}</span>
                  {n.ack_required && <span className={n.acked_at ? "text-emerald-600" : "font-medium text-red-600"}>{n.acked_at ? "Confirmed" : "Needs your confirmation"}</span>}
                </div>
              </div>
            ))}
            {!data?.items.length && <div className="p-6 text-center text-sm text-slate-500">No notifications</div>}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Shown to a salesman until they confirm a price change. On shift they also enter the current meter
 * reading of every nozzle, so litres pumped before the change are billed at the old rate.
 */
function PriceChangeGate() {
  const { data, reload, snoozed: later, setSnoozed: setLater } = useNotifications();
  const n = data?.pending_ack.find((x) => x.type === "price_change");
  const [readings, setReadings] = useState<Record<string, string>>({});
  const { busy, run } = useAction();
  useEffect(() => { setReadings({}); }, [n?.id]);
  if (!n || later === n.id) return null;
  const shift = data!.open_shift;
  const changes: any[] = n.data?.changes ?? [];
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const body = shift ? { readings: Object.fromEntries(shift.readings.map((r) => [r.nozzle_id, Number(readings[r.nozzle_id])])) } : {};
    const r: any = await run(() => api(`/notifications/${n.id}/ack`, { body }), (x: any) =>
      x.settled?.some((s: any) => s.unrecorded > 0) ? `Confirmed. Litres before the change billed at the old rate: ${x.settled.filter((s: any) => s.unrecorded > 0).map((s: any) => `${PRODUCTS[s.product]} ${num(s.unrecorded, 2)} L`).join(", ")}` : "Confirmed — new prices are active");
    if (r) reload();
  };
  return (
    <Modal open onClose={() => setLater(n.id)} title="⛽ Fuel price changed">
      <form onSubmit={submit} className="space-y-4">
        <div className="space-y-2">
          {changes.map((c) => (
            <div key={c.product} className={`flex items-center justify-between rounded-lg border p-3 ${c.diff > 0 ? "border-red-200 bg-red-50" : "border-emerald-200 bg-emerald-50"}`}>
              <div>
                <div className="font-semibold">{PRODUCTS[c.product]}</div>
                <div className="text-sm text-slate-600">{c.old != null && <>Rs {c.old.toFixed(2)} → </>}<b>Rs {c.new.toFixed(2)}</b> / L</div>
              </div>
              {c.diff != null && <div className={`flex items-center gap-1 text-lg font-bold ${c.diff > 0 ? "text-red-700" : "text-emerald-700"}`}>
                {c.diff > 0 ? <TrendingUp size={18} /> : <TrendingDown size={18} />}{c.diff > 0 ? "+" : "−"}Rs {Math.abs(c.diff).toFixed(2)}
              </div>}
            </div>
          ))}
        </div>
        <p className="text-sm text-slate-700"><b>1.</b> Dispenser par naya rate set karein. {shift && <><br /><b>2.</b> Abhi har nozzle ki meter reading likhein — is se pehle ka tel purane rate par hisab hoga.</>}</p>
        {shift && (
          <div className="grid gap-2 sm:grid-cols-2">
            {shift.readings.map((r) => (
              <Field key={r.nozzle_id} label={`${r.label} (${PRODUCTS[r.product]}) — last ${num(r.last_reading, 2)}`}>
                <input className="input" type="number" step="0.01" min={r.last_reading} required value={readings[r.nozzle_id] ?? ""} onChange={(e) => setReadings({ ...readings, [r.nozzle_id]: e.target.value })} />
              </Field>
            ))}
          </div>
        )}
        <p className="text-xs text-slate-500">Sales on the POS are paused until you confirm.</p>
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-secondary" onClick={() => setLater(n.id)}>Later</button>
          <button className="btn-primary" disabled={busy}>Dispenser updated — confirm</button>
        </div>
      </form>
    </Modal>
  );
}

const b64 = (s: string) => { const p = "=".repeat((4 - (s.length % 4)) % 4); const raw = atob((s + p).replace(/-/g, "+").replace(/_/g, "/")); return Uint8Array.from(raw, (c) => c.charCodeAt(0)); };

/** Turn on alerts on this phone / computer (Web Push through the app's service worker). */
function PhoneAlerts() {
  const [state, setState] = useState<"off" | "on" | "busy" | "na">(() => ("serviceWorker" in navigator && "PushManager" in window ? "off" : "na"));
  useEffect(() => {
    if (state === "na") return;
    navigator.serviceWorker.getRegistration().then((r) => r?.pushManager.getSubscription()).then((s) => { if (s) setState("on"); }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (state === "na") return null;
  const toggle = async () => {
    setState("busy");
    try {
      const reg = (await navigator.serviceWorker.getRegistration()) ?? (await navigator.serviceWorker.register("/sw.js"));
      const cur = await reg.pushManager.getSubscription();
      if (cur) { await api("/push/unsubscribe", { body: { endpoint: cur.endpoint } }); await cur.unsubscribe(); setState("off"); return; }
      if ((await Notification.requestPermission()) !== "granted") { setState("off"); return; }
      const { key } = await api("/push/key");
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64(key) });
      await api("/push/subscribe", { body: sub.toJSON() });
      await api("/push/test", { body: {} });
      setState("on");
    } catch { setState("off"); }
  };
  return (
    <button onClick={toggle} disabled={state === "busy"} className={`ml-auto mr-3 rounded-full px-2 py-0.5 text-xs ${state === "on" ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-600"}`}>
      📲 {state === "on" ? "Phone alerts on" : state === "busy" ? "…" : "Turn on phone alerts"}
    </button>
  );
}
