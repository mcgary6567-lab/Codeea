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
      (x: any) => `Prices updated. Stock revaluation ${pkr(x.stock_revaluation)}${x.broadcast_queued ? ` · broadcasting to ${x.broadcast_queued} customers` : ""}`);
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
  );
}
