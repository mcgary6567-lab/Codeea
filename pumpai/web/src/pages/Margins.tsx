import { useEffect, useState } from "react";
import { api, useApi } from "../lib/api";
import { Loading, PageHeader, Stat, useAction } from "../components/ui";
import { PRODUCTS, pkr, num } from "../lib/format";

/** Product-wise margin (sale rate − purchase cost) and the owner's monthly targets vs actual. */
export default function Margins() {
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7));
  const m = useApi<any>(`/analysis/margins?month=${month}`);
  const tg = useApi<any>(`/analysis/targets?month=${month}`);
  if (!m.data) return <Loading />;
  const rows = m.data.products;
  return (
    <div>
      <PageHeader title="Margins & targets · منافع و ہدف" subtitle="Har fuel par kitna munafa (sale rate − purchase cost), aur mahine ka target vs actual"
        actions={<input type="month" className="input w-auto" value={month} onChange={(e) => setMonth(e.target.value)} />} />

      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3">
        <Stat label="Litres sold" value={num(m.data.total.litres)} />
        <Stat label="Revenue" value={pkr(m.data.total.revenue)} />
        <Stat label="Gross margin profit" value={pkr(m.data.total.profit)} tone={m.data.total.profit >= 0 ? "green" : "red"} />
      </div>

      <div className="card mb-5 overflow-x-auto">
        <table className="w-full">
          <thead><tr><th className="th">Fuel</th><th className="th text-right">Litres</th><th className="th text-right">Avg sale</th><th className="th text-right">Avg cost</th><th className="th text-right">Margin/L</th><th className="th text-right">Profit</th></tr></thead>
          <tbody>{rows.map((r: any) => (
            <tr key={r.product}>
              <td className="td font-medium">{r.name}</td>
              <td className="td text-right tabular-nums">{num(r.litres)}</td>
              <td className="td text-right tabular-nums">Rs {r.avg_sale_rate.toFixed(2)}</td>
              <td className="td text-right tabular-nums text-slate-500">Rs {r.avg_cost.toFixed(2)}</td>
              <td className={`td text-right tabular-nums font-semibold ${r.margin_per_l >= 0 ? "text-emerald-700" : "text-red-600"}`}>Rs {r.margin_per_l.toFixed(2)}</td>
              <td className="td text-right tabular-nums font-semibold">{pkr(r.profit)}</td>
            </tr>
          ))}</tbody>
        </table>
        {!rows.length && <p className="p-6 text-center text-sm text-slate-500">Is mahine koi fuel sale nahi.</p>}
      </div>

      {tg.data && <TargetCard month={month} data={tg.data} onSaved={() => tg.reload()} />}
    </div>
  );
}

function TargetCard({ month, data, onSaved }: { month: string; data: any; onSaved: () => void }) {
  const { busy, run } = useAction();
  const [f, setF] = useState<Record<string, string>>({});
  useEffect(() => { setF({ sales_litres: data.target.sales_litres ?? "", revenue: data.target.revenue ?? "", net_profit: data.target.net_profit ?? "" }); }, [data]);
  const save = () => run(() => api("/analysis/targets", { method: "PUT", body: { month, sales_litres: f.sales_litres === "" ? null : Number(f.sales_litres), revenue: f.revenue === "" ? null : Number(f.revenue), net_profit: f.net_profit === "" ? null : Number(f.net_profit) } }), "Targets saved").then(onSaved);
  const metrics: [string, string, (n: number) => string][] = [
    ["sales_litres", "Sale (litres)", (n) => num(n)],
    ["revenue", "Revenue (Rs)", (n) => pkr(n)],
    ["net_profit", "Net profit (Rs)", (n) => pkr(n)],
  ];
  return (
    <div className="card p-4">
      <h2 className="mb-3 font-semibold">Monthly targets vs actual · <span lang="ur" dir="rtl" className="font-urdu">ہدف</span></h2>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {metrics.map(([k, label, fmt]) => {
          const target = Number(f[k]) || 0, actual = data.actual[k] ?? 0;
          const pct = target > 0 ? Math.min(100, Math.round((actual / target) * 100)) : 0;
          return (
            <div key={k} className="rounded-xl bg-slate-50 p-3 ring-1 ring-slate-200">
              <div className="text-sm font-medium">{label}</div>
              <input className="input mt-1" type="number" placeholder="Target set karein" value={f[k] ?? ""} onChange={(e) => setF({ ...f, [k]: e.target.value })} />
              <div className="mt-2 text-xs text-slate-500">Actual: <b className="tabular-nums text-slate-700">{fmt(actual)}</b></div>
              {target > 0 && <>
                <div className="mt-1 h-2 rounded-full bg-slate-200"><div className="h-full rounded-full" style={{ width: `${pct}%`, background: pct >= 100 ? "#1baf7a" : pct >= 60 ? "#2a78d6" : "#e0a106" }} /></div>
                <div className="mt-0.5 text-right text-xs tabular-nums text-slate-500">{pct}% of target</div>
              </>}
            </div>
          );
        })}
      </div>
      <button className="btn-primary mt-3" disabled={busy} onClick={save}>Save targets</button>
    </div>
  );
}
