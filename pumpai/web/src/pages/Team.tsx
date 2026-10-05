import { useEffect, useState } from "react";
import { Star, Trophy, Percent } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Empty, Loading, PageHeader, Stat, useAction } from "../components/ui";
import { dt, num, pkr } from "../lib/format";
import { useAuth } from "../App";

const CAT_LABEL: Record<string, string> = { lubricant: "Lubricants / engine oil", filter: "Filters", coolant: "Coolant", tyre: "Tyres", battery: "Batteries", tuck: "Tuck shop", service: "Services", other: "Other" };
const Stars = ({ v }: { v: number | null }) => v == null ? <span className="text-slate-400">—</span>
  : <span className="whitespace-nowrap"><span className="text-amber-500">{"★".repeat(Math.round(v))}</span><span className="text-slate-300">{"★".repeat(5 - Math.round(v))}</span> <b className="tabular-nums">{v}</b></span>;

/** Customer ratings after each fill, the weekly leaderboard and salesman commission. */
export default function Team() {
  const [tab, setTab] = useState<"ratings" | "leaderboard" | "commission">("ratings");
  return (
    <div className="space-y-5">
      <PageHeader title="Ratings & commission" subtitle="What customers say after each fill, who is doing best, and the commission added to salaries" />
      <div className="flex gap-1 overflow-x-auto border-b border-slate-200">
        {([["ratings", "Customer ratings", Star], ["leaderboard", "Leaderboard", Trophy], ["commission", "Commission", Percent]] as const).map(([k, l, I]) => (
          <button key={k} onClick={() => setTab(k)} className={`-mb-px flex items-center gap-2 whitespace-nowrap border-b-2 px-4 py-2 text-sm font-medium ${tab === k ? "border-brand-600 text-brand-700" : "border-transparent text-slate-500"}`}><I size={15} />{l}</button>
        ))}
      </div>
      {tab === "ratings" ? <Ratings /> : tab === "leaderboard" ? <Leaderboard /> : <Commission />}
    </div>
  );
}

function Ratings() {
  const [days, setDays] = useState(30);
  const { data } = useApi<any>(`/ratings?days=${days}`);
  if (!data) return <Loading />;
  const max = Math.max(1, ...data.stars.map((s: any) => s.n));
  return (
    <>
      <div className="flex items-center gap-2 text-sm">
        {[7, 30, 90].map((d) => <button key={d} onClick={() => setDays(d)} className={`min-h-9 rounded-full px-3 py-1 sm:min-h-0 ${days === d ? "bg-brand-600 text-white" : "bg-slate-100"}`}>{d} days</button>)}
        {!data.enabled && <Badge tone="amber">Asking for ratings is off (Settings → Automation)</Badge>}
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
        <Stat label="Average rating" value={<Stars v={data.summary.avg} />} tone="amber" />
        <Stat label="Ratings received" value={num(data.summary.rated)} hint={`${data.summary.response_pct}% of ${num(data.summary.asked)} asked replied`} />
        <Stat label="Bad ratings (1–2)" value={num(data.summary.low ?? 0)} hint="Each one became a complaint" tone="red" />
        <div className="card p-3">
          {data.stars.map((s: any) => (
            <div key={s.score} className="flex items-center gap-2 text-xs"><span className="w-6 text-right">{s.score}★</span>
              <div className="h-2 flex-1 rounded bg-slate-100"><div className="h-full rounded bg-amber-400" style={{ width: `${(s.n / max) * 100}%` }} /></div><span className="w-6 tabular-nums">{s.n}</span></div>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <div className="card min-w-0 p-4">
          <h2 className="mb-2 font-semibold">By salesman</h2>
          <table className="w-full text-sm"><thead className="text-left text-xs uppercase text-slate-500"><tr><th className="py-1">Salesman</th><th>Rating</th><th className="text-right">Ratings</th><th className="text-right">Bad</th></tr></thead>
            <tbody className="divide-y divide-slate-100">{data.by_salesman.map((s: any) => (
              <tr key={s.id}><td className="py-1.5 font-medium">{s.name}</td><td><Stars v={s.avg} /></td><td className="text-right tabular-nums">{s.n}</td><td className={`text-right tabular-nums ${s.low ? "font-semibold text-red-600" : ""}`}>{s.low}</td></tr>))}</tbody></table>
          {!data.by_salesman.length && <Empty>No ratings yet</Empty>}
          {data.by_station.length > 1 && <div className="mt-3 flex flex-wrap gap-3 text-sm">{data.by_station.map((s: any) => <span key={s.name} className="rounded-lg bg-slate-50 px-2 py-1">{s.name}: <Stars v={s.avg} /></span>)}</div>}
        </div>
        <div className="card min-w-0 p-4">
          <h2 className="mb-2 font-semibold">Latest ratings</h2>
          <ul className="max-h-96 divide-y divide-slate-100 overflow-y-auto text-sm">
            {data.recent.map((r: any) => (
              <li key={r.id} className="py-2"><div className="flex items-start gap-2"><Stars v={r.score} /><span className="min-w-0 flex-1 break-words">{r.customer_name}</span><span className="shrink-0 text-xs text-slate-500">{dt(r.rated_at)}</span></div>
                <div className="text-xs text-slate-500">{r.salesman_name ? `Salesman ${r.salesman_name}` : ""}{r.complaint_id ? ` · complaint #${r.complaint_id}` : ""}</div>
                {r.comment && <div className="mt-1 rounded bg-red-50 px-2 py-1 text-red-800">“{r.comment}”</div>}</li>
            ))}
          </ul>
        </div>
      </div>
      <p className="text-xs text-slate-500">After a fill on a customer's account (khata, wallet, points, coupon), they get a WhatsApp asking for 1–5 (once a day at most). A 1 or 2 asks what went wrong, opens a complaint and alerts the manager; 4–5 get your Google review link.</p>
    </>
  );
}

export function Leaderboard({ compact }: { compact?: boolean }) {
  const { data } = useApi<any>("/leaderboard?days=7");
  const { user } = useAuth();
  if (!data) return <Loading />;
  const medal = ["🥇", "🥈", "🥉"];
  return (
    <div className={compact ? "" : "card p-4"}>
      {!compact && <h2 className="mb-1 font-semibold">Last 7 days</h2>}
      <ol className="divide-y divide-slate-100">
        {data.rows.map((r: any) => (
          <li key={r.user_id} className={`flex items-center gap-3 py-2 ${r.user_id === user?.id ? "rounded-lg bg-emerald-50 px-2" : ""}`}>
            <span className="w-8 text-center text-xl">{medal[r.rank - 1] ?? `#${r.rank}`}</span>
            <span className="min-w-0 flex-1"><b>{r.name}</b><span className="block text-xs text-slate-500">{num(r.litres)} L · shop {pkr(r.shop_sales)}{r.rating ? ` · ★ ${r.rating}` : ""}{r.shortage ? ` · short ${pkr(r.shortage)}` : ""}</span></span>
            <span className="shrink-0 text-right text-sm"><b className="tabular-nums">{r.points}</b> pts<span className="block text-xs text-emerald-700">commission {pkr(r.total)}</span></span>
          </li>
        ))}
      </ol>
      {!data.rows.length && <Empty>No salesmen yet</Empty>}
      {!compact && <p className="mt-2 text-xs text-slate-500">Points: 1 per 100 L, 1 per Rs 500 shop sales, + customer rating, − cash shortages. Every Monday the salesmen get the top 3 and their own place.</p>}
    </div>
  );
}

function Commission() {
  const [month, setMonth] = useState(() => new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 7));
  const { data, reload } = useApi<any>(`/commission?month=${month}`);
  const { can } = useAuth();
  const { busy, run } = useAction();
  const [rates, setRates] = useState<Record<string, string>>({});
  const [perL, setPerL] = useState("0");
  useEffect(() => { if (data) { setRates(Object.fromEntries(Object.entries(data.rates.shop).map(([k, v]) => [k, String(v)]))); setPerL(String(data.rates.per_litre)); } }, [data?.rates]);
  if (!data) return <Loading />;
  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
      <div className="card min-w-0 p-4 lg:col-span-2">
        <div className="mb-2 flex flex-wrap items-center gap-2"><h2 className="flex-1 font-semibold">Commission — {month}</h2><input type="month" className="input w-40" value={month} onChange={(e) => setMonth(e.target.value)} aria-label="Month" /></div>
        {/* phone: one card per salesman */}
        <ul className="divide-y divide-slate-100 text-sm sm:hidden">
          {data.salesmen.map((s: any) => (
            <li key={s.user_id} className="py-2">
              <div className="flex items-start justify-between gap-2"><span className="min-w-0 font-medium">{s.name}</span><span className="shrink-0 font-bold tabular-nums">{pkr(s.total)}</span></div>
              <div className="text-xs text-slate-500">Shop {pkr(s.shop_sales)} → <span className="tabular-nums text-slate-700">{pkr(s.shop_commission)}</span> · {num(s.litres)} L → <span className="tabular-nums text-slate-700">{pkr(s.fuel_commission)}</span></div>
              {s.lines.length > 0 && <div className="text-xs text-slate-400">{s.lines.map((l: any) => `${CAT_LABEL[l.category] ?? l.category} ${pkr(l.sales)} × ${l.pct}%`).join(" · ")}</div>}
            </li>
          ))}
          <li className="flex justify-between border-t-2 border-slate-800 py-2 font-bold"><span>Total</span><span className="tabular-nums">{pkr(data.total)}</span></li>
        </ul>
        <div className="hidden overflow-x-auto sm:block">
        <table className="w-full text-sm"><thead className="text-left text-xs uppercase text-slate-500"><tr><th className="py-1">Salesman</th><th className="text-right">Shop sales</th><th className="text-right">Litres</th><th className="text-right">Shop comm.</th><th className="text-right">Fuel comm.</th><th className="text-right">Total</th></tr></thead>
          <tbody className="divide-y divide-slate-100">{data.salesmen.map((s: any) => (
            <tr key={s.user_id}><td className="py-1.5 font-medium">{s.name}<span className="block text-xs text-slate-500">{s.lines.map((l: any) => `${CAT_LABEL[l.category] ?? l.category} ${pkr(l.sales)} × ${l.pct}%`).join(" · ")}</span></td>
              <td className="text-right tabular-nums">{pkr(s.shop_sales)}</td><td className="text-right tabular-nums">{num(s.litres)}</td><td className="text-right tabular-nums">{pkr(s.shop_commission)}</td>
              <td className="text-right tabular-nums">{pkr(s.fuel_commission)}</td><td className="text-right font-bold tabular-nums">{pkr(s.total)}</td></tr>))}</tbody>
          <tfoot><tr className="border-t-2 border-slate-800 font-bold"><td className="py-1.5" colSpan={5}>Total</td><td className="text-right tabular-nums">{pkr(data.total)}</td></tr></tfoot></table>
        </div>
        <p className="mt-2 text-xs text-slate-500">Added to each salesman's salary by itself when you press “Pay salary” on Staff accounts.</p>
      </div>
      <div className="card min-w-0 p-4">
        <h2 className="mb-2 font-semibold">Commission rates</h2>
        <div className="space-y-1.5 text-sm">
          {data.categories.map((c: string) => (
            <label key={c} className="flex items-center gap-2"><span className="flex-1">{CAT_LABEL[c] ?? c}</span>
              <input className="input w-20 py-1 text-right" type="number" min={0} max={50} step={0.5} disabled={!can("settings.manage")} value={rates[c] ?? "0"} onChange={(e) => setRates({ ...rates, [c]: e.target.value })} /> %</label>
          ))}
          <label className="flex items-center gap-2 border-t border-slate-100 pt-2"><span className="flex-1">Fuel, per litre</span>Rs <input className="input w-20 py-1 text-right" type="number" min={0} max={10} step={0.05} disabled={!can("settings.manage")} value={perL} onChange={(e) => setPerL(e.target.value)} /></label>
        </div>
        {can("settings.manage") && <button className="btn-primary mt-3 w-full" disabled={busy} onClick={() => run(() => api("/commission/settings", { method: "PUT", body: { shop: Object.fromEntries(Object.entries(rates).map(([k, v]) => [k, Number(v) || 0])), per_litre: Number(perL) || 0 } }), "Rates saved").then(reload)}>Save rates</button>}
      </div>
    </div>
  );
}
