import { useEffect, useState } from "react";
import { api, useApi } from "../lib/api";
import { Tv, ExternalLink } from "lucide-react";
import { Loading, PageHeader, useAction } from "../components/ui";
import { PRODUCTS, dt, pkr } from "../lib/format";
import { useAuth } from "../App";

export default function Prices() {
  const { data, reload } = useApi<any>("/prices");
  const { busy, run } = useAction();
  const { can } = useAuth();
  const requests = useApi<any[]>(can("prices.update") ? "/price-requests" : null);
  const [vals, setVals] = useState<Record<string, string>>({});
  const [broadcast, setBroadcast] = useState(true);
  const [note, setNote] = useState("");
  useEffect(() => { if (data) setVals(Object.fromEntries(Object.entries(data.current).map(([k, v]: any) => [k, String(v.price)]))); }, [data]);
  if (!data) return <Loading />;

  const changed = Object.entries(vals).filter(([k, v]) => Number(v) > 0 && Number(v) !== data.current[k]?.price);
  const submit = async () => {
    const r: any = await run(() => api("/prices", { body: { prices: Object.fromEntries(changed.map(([k, v]) => [k, Number(v)])), broadcast, note: note || undefined } }),
      (x: any) => x.pending ? "Sent to the admin for approval — prices change when the admin approves" : `Prices updated. ${x.salesmen_notified} salesmen notified to change the dispenser. Stock revaluation ${pkr(x.stock_revaluation)}${x.wholesale_rates_updated ? ` · ${x.wholesale_rates_updated} wholesale rates moved with the pump price` : ""}${x.broadcast_queued ? ` · broadcasting to ${x.broadcast_queued} customers` : ""}`);
    if (r) { setNote(""); reload(); requests.reload(); }
  };

  return (
    <div>
      <PageHeader title="Fuel prices" subtitle="Update on each government price notification (usually the 1st and 16th). The WhatsApp bot uses these instantly." />
      {(requests.data ?? []).filter((r) => r.status === "pending").map((r) => (
        <div key={r.id} className="card mb-4 flex flex-wrap items-center gap-3 border-l-4 border-l-amber-500 p-4">
          <span className="flex-1 text-sm"><b>Waiting for admin approval</b> — {Object.entries(r.prices).map(([p, v]: any) => `${PRODUCTS[p]} Rs ${v}`).join(", ")} · asked by {r.requested_by} {dt(r.created_at)}</span>
          {can("settings.manage") && <>
            <button className="btn-primary" disabled={busy} onClick={() => run(() => api(`/price-requests/${r.id}/approve`, { body: {} }), "Approved — prices are now live").then(() => { reload(); requests.reload(); })}>Approve</button>
            <button className="btn-secondary" disabled={busy} onClick={() => run(() => api(`/price-requests/${r.id}/reject`, { body: {} }), "Rejected").then(() => requests.reload())}>Reject</button>
          </>}
        </div>
      ))}
      <div className={`grid gap-5 ${can("prices.update") ? "lg:grid-cols-[420px_1fr]" : ""}`}>
        {!can("prices.update") && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {Object.keys(PRODUCTS).map((p) => (
              <div key={p} className="card p-4"><div className="text-sm text-slate-500">{PRODUCTS[p]}</div><div className="text-2xl font-semibold tabular-nums">Rs {data.current[p]?.price.toFixed(2)}</div><div className="text-xs text-slate-400">per litre</div></div>
            ))}
          </div>
        )}
        {can("prices.update") && <div className="card space-y-3 p-4">
          {Object.keys(PRODUCTS).map((p) => (
            <label key={p} className="flex items-center justify-between gap-3">
              <span className="text-sm font-medium">{PRODUCTS[p]} <span className="text-xs text-slate-500">({p})</span></span>
              <div className="flex items-center gap-1"><span className="text-sm text-slate-500">Rs</span><input className="input w-32 text-right" type="number" step="0.01" value={vals[p] ?? ""} onChange={(e) => setVals({ ...vals, [p]: e.target.value })} /></div>
            </label>
          ))}
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={broadcast} onChange={(e) => setBroadcast(e.target.checked)} /> Broadcast new prices on WhatsApp to opted-in customers</label>
          {broadcast && <input className="input" placeholder="Optional note, e.g. 'Raat 12 baje se laagu'" value={note} onChange={(e) => setNote(e.target.value)} />}
          <button className="btn-primary w-full" disabled={busy || !changed.length} onClick={submit}>Update {changed.length || ""} price{changed.length === 1 ? "" : "s"}</button>
        </div>}
        <div className="space-y-5">
        <TvBoard canEdit={can("prices.update")} />
        {data.last_change && (
          <div className="card">
            <h2 className="px-4 pt-3 font-semibold">Dispenser update — last price change {dt(data.last_change.at)}</h2>
            <p className="px-4 text-xs text-slate-500">{data.last_change.changes.map((c: any) => `${PRODUCTS[c.product]} ${c.diff > 0 ? "+" : "−"}Rs ${Math.abs(c.diff ?? 0).toFixed(2)}`).join(" · ")}</p>
            <table className="mt-2 w-full">
              <thead><tr><th className="th">Salesman</th><th className="th">Station</th><th className="th">Status</th></tr></thead>
              <tbody>{data.last_change.acks.map((a: any) => (
                <tr key={a.name}><td className="td text-sm">{a.name}</td><td className="td text-sm">{a.station ?? "—"}</td>
                  <td className="td text-sm">{a.acked_at ? <span className="text-emerald-700">✓ Confirmed {dt(a.acked_at)}{a.with_readings ? " · meter readings taken" : ""}</span> : <span className="font-medium text-red-600">Not confirmed yet</span>}</td></tr>
              ))}</tbody>
            </table>
            {!data.last_change.acks.length && <p className="p-4 text-sm text-slate-500">No salesmen to notify.</p>}
          </div>
        )}
        <div className="card">
          <table className="w-full">
            <thead><tr><th className="th">Effective</th><th className="th">Product</th><th className="th text-right">Price / L</th><th className="th">By</th></tr></thead>
            <tbody>{data.history.map((h: any) => (
              <tr key={h.id}><td className="td text-xs">{dt(h.effective_from)}</td><td className="td text-sm">{PRODUCTS[h.product]}</td><td className="td text-right tabular-nums">Rs {h.price.toFixed(2)}</td><td className="td text-xs text-slate-500">{h.created_by}</td></tr>
            ))}</tbody>
          </table>
        </div>
        </div>
      </div>
    </div>
  );
}

/** Link for a TV / LED screen at the pump: big prices in English and Urdu, changes by itself. */
function TvBoard({ canEdit }: { canEdit: boolean }) {
  const stations = useApi<any[]>("/stations");
  const [station, setStation] = useState("");
  const link = useApi<any>(`/board-link${station ? `?station_id=${station}` : ""}`);
  const [offers, setOffers] = useState<string | null>(null);
  const { busy, run } = useAction();
  const text = offers ?? link.data?.offers ?? "";
  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Tv size={18} className="text-brand-600" /><h2 className="flex-1 font-semibold">TV rate board</h2>
        <select className="input min-h-9 w-auto py-1 text-sm" value={station} onChange={(e) => setStation(e.target.value)} aria-label="Station">
          <option value="">All products</option>{(stations.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        {link.data && <a className="btn-primary" href={link.data.url} target="_blank" rel="noreferrer"><ExternalLink size={14} /> Open board</a>}
        {link.data && <button className="btn-secondary" onClick={() => navigator.clipboard?.writeText(link.data.url).then(() => alert("Link copied — open it on the TV's browser"))}>Copy link</button>}
      </div>
      <p className="mt-1 text-xs text-slate-500">Open this link once on the TV / LED screen (smart TV browser or a small Android box). Prices update by themselves within 20 seconds of a change.</p>
      {canEdit && <div className="mt-3 flex flex-col gap-2 sm:flex-row">
        <textarea className="input min-h-[64px] flex-1 text-sm" placeholder="Offers shown on the board, one per line" value={text} onChange={(e) => setOffers(e.target.value)} />
        <button className="btn-secondary self-end" disabled={busy} onClick={() => run(() => api("/board/settings", { method: "PUT", body: { offers: text } }), "Offers updated on the board")}>Save offers</button>
      </div>}
    </div>
  );
}
