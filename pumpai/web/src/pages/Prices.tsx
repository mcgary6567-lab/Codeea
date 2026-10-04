import { useEffect, useState } from "react";
import { api, useApi } from "../lib/api";
import { Loading, PageHeader, useAction } from "../components/ui";
import { PRODUCTS, dt, pkr } from "../lib/format";
import { useAuth } from "../App";

export default function Prices() {
  const { data, reload } = useApi<any>("/prices");
  const { busy, run } = useAction();
  const { can } = useAuth();
  const [vals, setVals] = useState<Record<string, string>>({});
  const [broadcast, setBroadcast] = useState(true);
  const [note, setNote] = useState("");
  useEffect(() => { if (data) setVals(Object.fromEntries(Object.entries(data.current).map(([k, v]: any) => [k, String(v.price)]))); }, [data]);
  if (!data) return <Loading />;

  const changed = Object.entries(vals).filter(([k, v]) => Number(v) > 0 && Number(v) !== data.current[k]?.price);
  const submit = async () => {
    const r: any = await run(() => api("/prices", { body: { prices: Object.fromEntries(changed.map(([k, v]) => [k, Number(v)])), broadcast, note: note || undefined } }),
      (x: any) => `Prices updated. ${x.salesmen_notified} salesmen notified to change the dispenser. Stock revaluation ${pkr(x.stock_revaluation)}${x.wholesale_rates_updated ? ` · ${x.wholesale_rates_updated} wholesale rates moved with the pump price` : ""}${x.broadcast_queued ? ` · broadcasting to ${x.broadcast_queued} customers` : ""}`);
    if (r) { setNote(""); reload(); }
  };

  return (
    <div>
      <PageHeader title="Fuel prices" subtitle="Update on each government price notification (usually the 1st and 16th). The WhatsApp bot uses these instantly." />
      <div className={`grid gap-5 ${can("prices.update") ? "lg:grid-cols-[420px_1fr]" : ""}`}>
        {!can("prices.update") && (
          <div className="grid gap-3 sm:grid-cols-3">
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
