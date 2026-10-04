import { useState } from "react";
import { Download, Upload } from "lucide-react";
import { api, getToken, useApi } from "../lib/api";
import { Badge, Empty, Field, Loading, PageHeader, Stat, useAction } from "../components/ui";
import { dt, num, pkr } from "../lib/format";

const TABS = [["health", "Health"], ["stations", "Stations"], ["staff", "Staff risk"], ["pl", "Profit & loss"], ["reconcile", "Statement check"], ["prices", "Price planner"], ["tanks", "Tank gain / loss"]] as const;
type Tab = (typeof TABS)[number][0];

/** Owner's analysis in one place. */
export default function Insights() {
  const [tab, setTab] = useState<Tab>("health");
  return (
    <div className="space-y-5">
      <PageHeader title="Owner insights" subtitle="Health score, stations, staff, profit & loss, payment statements, price changes and tank losses" />
      <div className="flex flex-wrap gap-2">{TABS.map(([k, l]) => <button key={k} onClick={() => setTab(k)} className={`rounded-lg px-3 py-2 text-sm font-medium ${tab === k ? "bg-slate-900 text-white" : "bg-white ring-1 ring-slate-200"}`}>{l}</button>)}</div>
      {tab === "health" && <Health />}{tab === "stations" && <Stations />}{tab === "staff" && <StaffRisk />}{tab === "pl" && <PL />}
      {tab === "reconcile" && <Reconcile />}{tab === "prices" && <Prices />}{tab === "tanks" && <Tanks />}
    </div>
  );
}

const scoreTone = (s: number) => (s >= 80 ? "text-emerald-600" : s >= 60 ? "text-amber-600" : "text-red-600");
const barTone = (s: number) => (s >= 80 ? "bg-emerald-500" : s >= 60 ? "bg-amber-500" : "bg-red-500");

export function HealthCard({ compact }: { compact?: boolean }) {
  const { data } = useApi<any>("/analysis/health");
  if (!data) return compact ? null : <Loading />;
  return (
    <div className="card p-4">
      <div className="flex items-center gap-4">
        <div className={`text-5xl font-bold tabular-nums ${scoreTone(data.score)}`}>{data.score}<span className="text-lg text-slate-400">/100</span></div>
        <div><div className="font-semibold">Pump health · <span lang="ur" className="font-urdu">پمپ کی صحت</span></div><div className="text-sm capitalize text-slate-500">{data.level}</div></div>
      </div>
      <div className={`mt-3 grid gap-2 ${compact ? "sm:grid-cols-3 lg:grid-cols-6" : "sm:grid-cols-2"}`}>
        {data.parts.map((p: any) => (
          <div key={p.key} className="rounded-lg bg-slate-50 p-2">
            <div className="flex justify-between text-sm"><span className="font-medium">{p.label}</span><span className={`font-semibold ${scoreTone(p.score)}`}>{p.score}</span></div>
            <div className="mt-1 h-1.5 rounded-full bg-slate-200"><div className={`h-full rounded-full ${barTone(p.score)}`} style={{ width: `${p.score}%` }} /></div>
            {!compact && <div className="mt-1 text-xs text-slate-500">{p.note}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
const Health = () => <HealthCard />;

function Stations() {
  const { data } = useApi<any>("/analysis/stations");
  if (!data) return <Loading />;
  const rows: [string, (s: any) => string][] = [["Litres", (s) => `${num(s.litres)} L`], ["Fuel sales", (s) => pkr(s.fuel_sales)], ["Shop sales", (s) => pkr(s.shop_sales)], ["Shop profit", (s) => pkr(s.shop_profit)],
    ["Expenses", (s) => pkr(s.expenses)], ["Average sale", (s) => pkr(s.avg_sale)], ["Cash share", (s) => `${s.cash_share_pct}%`], ["Khata share", (s) => `${s.khata_share_pct}%`],
    ["Shifts (short)", (s) => `${s.shifts} (${s.short_shifts})`], ["Cash short / over", (s) => pkr(s.cash_variance)], ["Dip gain / loss", (s) => `${num(s.dip_gain_loss_l)} L`]];
  return (
    <div className="card overflow-x-auto"><h2 className="p-4 pb-2 font-semibold">Last 30 days</h2>
      <table className="w-full"><thead><tr><th className="th" />{data.stations.map((s: any) => <th key={s.id} className="th text-right">{s.name.replace("Al-Madina ", "")}</th>)}</tr></thead>
        <tbody>{rows.map(([l, f]) => <tr key={l}><td className="td text-sm font-medium">{l}</td>{data.stations.map((s: any) => <td key={s.id} className="td text-right tabular-nums">{f(s)}</td>)}</tr>)}</tbody></table>
    </div>
  );
}

function StaffRisk() {
  const { data } = useApi<any>("/analysis/staff-risk");
  if (!data) return <Loading />;
  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-600">Last 30 days. The score adds up short shifts, litres not entered on the POS (booked from the meter), undone sales, late days and failed checks. The owner gets it on WhatsApp every Monday.</p>
      {data.staff.map((s: any) => (
        <div key={s.id} className="card flex flex-wrap items-center gap-4 p-4">
          <div className={`text-3xl font-bold tabular-nums ${s.level === "high" ? "text-red-600" : s.level === "watch" ? "text-amber-600" : "text-emerald-600"}`}>{s.score}</div>
          <div className="min-w-[200px] flex-1"><div className="font-semibold">{s.name} <Badge tone={s.level === "high" ? "red" : s.level === "watch" ? "amber" : "green"}>{s.level}</Badge></div>
            <ul className="mt-1 text-sm text-slate-600">{s.reasons.length ? s.reasons.map((r: string) => <li key={r}>• {r}</li>) : <li>No concerns</li>}</ul></div>
          <div className="text-right text-xs text-slate-500">{s.shifts} shifts · {pkr(s.cash_variance)} cash</div>
        </div>
      ))}
      {!data.staff.length && <Empty>No salesmen yet</Empty>}
    </div>
  );
}

function PL() {
  const [month, setMonth] = useState(new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 7));
  const { data } = useApi<any>(`/analysis/pl?month=${month}`);
  if (!data) return <Loading />;
  const p = data.pl, b = data.balance_sheet;
  const L = ({ k, v, b: bold, neg }: { k: string; v: number; b?: boolean; neg?: boolean }) => (
    <div className={`flex justify-between border-b border-slate-100 py-1.5 text-sm ${bold ? "font-semibold" : ""}`}><span>{k}</span><span className={`tabular-nums ${neg ? "text-red-600" : ""}`}>{neg && v ? "−" : ""}{pkr(Math.abs(v))}</span></div>
  );
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Month"><input className="input" type="month" value={month} onChange={(e) => setMonth(e.target.value)} /></Field>
        <a className="btn-secondary" href={`/api/analysis/pl.csv?month=${month}&token=${encodeURIComponent(getToken() ?? "")}`}><Download size={15} /> Excel</a>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="Income" value={pkr(p.income.total)} tone="blue" />
        <Stat label="Gross profit" value={pkr(p.gross_profit)} tone="green" />
        <Stat label="Net profit" value={pkr(p.net_profit)} tone={p.net_profit >= 0 ? "green" : "red"} hint={`${p.margin_pct}% of income`} />
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <div className="card p-4"><h2 className="mb-2 font-semibold">Profit & loss — {month}</h2>
          <L k="Fuel sales (pump)" v={p.income.fuel_retail} /><L k="Fuel sales (wholesale)" v={p.income.fuel_wholesale} /><L k="Shop sales" v={p.income.shop} /><L k="Total income" v={p.income.total} b />
          <L k="Fuel cost" v={p.cost_of_sales.fuel} neg /><L k="Shop cost" v={p.cost_of_sales.shop} neg />
          <div className="flex justify-between border-b border-slate-100 py-1.5 text-sm"><span>Stock gain / loss (dips)</span><span className={`tabular-nums ${p.cost_of_sales.stock_gain_loss < 0 ? "text-red-600" : "text-emerald-700"}`}>{pkr(p.cost_of_sales.stock_gain_loss)}</span></div>
          <L k="Gross profit" v={p.gross_profit} b />
          {p.expenses.by_category.map((e: any) => <L key={e.category} k={e.category} v={e.amount} neg />)}
          <L k="Net profit" v={p.net_profit} b />
        </div>
        <div className="card p-4"><h2 className="mb-2 font-semibold">Balance sheet — today</h2>
          {Object.entries(b.assets).map(([k, v]) => <L key={k} k={k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())} v={v as number} />)}
          <L k="Total assets" v={b.total_assets} b />
          {Object.entries(b.liabilities).map(([k, v]) => <L key={k} k={k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())} v={v as number} neg />)}
          <L k="Net worth" v={b.net_worth} b />
          <p className="mt-2 text-xs text-slate-500">{b.note}</p>
        </div>
      </div>
    </div>
  );
}

function Reconcile() {
  const [method, setMethod] = useState("easypaisa");
  const [r, setR] = useState<any>(null);
  const { busy, run } = useAction();
  const load = async (file?: File) => { if (!file) return; const csv = await file.text(); const x = await run(() => api("/analysis/reconcile", { body: { method, csv } })); if (x) setR(x); };
  return (
    <div className="space-y-4">
      <div className="card space-y-3 p-4">
        <p className="text-sm text-slate-600">Download the statement (CSV) from Easypaisa / JazzCash merchant, the card machine portal or Raast, and upload it. Each payment is matched to a sale of the same amount within 2 hours.</p>
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Statement of"><select className="input" value={method} onChange={(e) => setMethod(e.target.value)}>{["easypaisa", "jazzcash", "card", "raast"].map((m) => <option key={m}>{m}</option>)}</select></Field>
          <label className="btn-primary cursor-pointer"><Upload size={15} /> {busy ? "Checking…" : "Upload CSV"}<input type="file" accept=".csv,text/csv" className="hidden" onChange={(e) => load(e.target.files?.[0])} /></label>
        </div>
      </div>
      {r && <>
        <div className="grid gap-3 sm:grid-cols-3">
          <Stat label="Matched" value={`${r.matched} / ${r.statement_lines}`} tone="green" hint={pkr(r.matched_amount)} />
          <Stat label="Money with no sale" value={pkr(r.money_without_sale_total)} tone="amber" hint={`${r.money_without_sale.length} payments — khata/wholesale payment or not entered`} />
          <Stat label="Sales with no money" value={pkr(r.sales_without_money_total)} tone="red" hint={`${r.sales_without_money.length} sales — check these`} />
        </div>
        <div className="grid gap-5 lg:grid-cols-2">
          <div className="card p-4"><h2 className="mb-2 font-semibold">Sales with no money in the statement</h2>
            {r.sales_without_money.map((s: any) => <div key={`${s.kind}${s.id}`} className="flex justify-between border-b border-slate-100 py-1.5 text-sm"><span>{dt(s.created_at)} · {s.station?.replace("Al-Madina ", "")} · {s.kind} #{s.id}</span><b className="tabular-nums">{pkr(s.amount)}</b></div>)}
            {!r.sales_without_money.length && <p className="text-sm text-emerald-700">All sales were paid ✓</p>}</div>
          <div className="card p-4"><h2 className="mb-2 font-semibold">Money with no matching sale</h2>
            {r.money_without_sale.map((l: any, i: number) => <div key={i} className="flex justify-between border-b border-slate-100 py-1.5 text-sm"><span>{l.at ? dt(l.at) : "—"} · {l.ref}</span><b className="tabular-nums">{pkr(l.amount)}</b></div>)}
            {!r.money_without_sale.length && <p className="text-sm text-emerald-700">Every payment matched a sale ✓</p>}</div>
        </div>
      </>}
    </div>
  );
}

function Prices() {
  const { data } = useApi<any>("/analysis/price-planner");
  if (!data) return <Loading />;
  return (
    <div className="space-y-3">
      <div className="card p-4 text-sm">Next usual price revision: <b>{data.next_revision}</b> ({data.days_to_next} day{data.days_to_next === 1 ? "" : "s"}). Prices are usually changed on the 1st and 16th.</div>
      <div className="grid gap-4 lg:grid-cols-3">{data.products.map((p: any) => (
        <div key={p.product} className="card p-4">
          <div className="flex items-center justify-between"><h3 className="font-semibold">{p.name}</h3><Badge tone={p.trend === "up" ? "red" : p.trend === "down" ? "green" : "slate"}>{p.trend === "up" ? "going up" : p.trend === "down" ? "going down" : "no trend"}</Badge></div>
          <div className="mt-1 text-sm text-slate-600">Rs {p.price.toFixed(2)} · stock {num(p.stock_l)} L</div>
          <div className="mt-1 text-xs text-slate-500">Last changes: {p.last_changes.map((c: number) => `${c > 0 ? "+" : ""}${c}`).join(", ") || "—"}</div>
          <table className="mt-2 w-full text-sm"><tbody>{p.scenarios.map((s: any) => <tr key={s.change}><td className="py-0.5">If {s.change > 0 ? "+" : ""}Rs {s.change}/L</td><td className={`text-right tabular-nums ${s.stock_effect < 0 ? "text-red-600" : "text-emerald-700"}`}>{s.stock_effect > 0 ? "+" : ""}{pkr(s.stock_effect)} on stock</td></tr>)}</tbody></table>
          <p className="mt-2 rounded-lg bg-slate-50 p-2 text-sm">{p.advice}</p>
        </div>
      ))}</div>
    </div>
  );
}

function Tanks() {
  const { data } = useApi<any>("/analysis/gain-loss");
  if (!data) return <Loading />;
  return (
    <div className="card overflow-x-auto"><h2 className="p-4 pb-2 font-semibold">Dip gain / loss — last 30 days</h2>
      <table className="w-full"><thead><tr><th className="th">Tank</th><th className="th text-right">Dips</th><th className="th text-right">Sold</th><th className="th text-right">Gain / loss</th><th className="th text-right">% of sold</th><th className="th text-right">Value</th><th className="th">Status</th></tr></thead>
        <tbody>{data.tanks.map((t: any) => (
          <tr key={t.id}><td className="td font-medium">{t.station.replace("Al-Madina ", "")} · {t.name}</td><td className="td text-right">{t.dips}</td><td className="td text-right tabular-nums">{num(t.throughput_l)} L</td>
            <td className={`td text-right tabular-nums ${t.gain_loss_l < 0 ? "text-red-600" : "text-emerald-700"}`}>{num(t.gain_loss_l)} L</td><td className="td text-right tabular-nums">{t.pct}%</td><td className="td text-right tabular-nums">{pkr(t.value)}</td>
            <td className="td"><Badge tone={t.status === "leak_suspected" ? "red" : t.status === "watch" ? "amber" : "green"}>{t.status === "leak_suspected" ? "Possible leak — check tank & lines" : t.status === "watch" ? "Watch" : "OK"}</Badge></td></tr>
        ))}</tbody></table>
      <p className="p-4 pt-2 text-xs text-slate-500">Up to 0.25% loss from evaporation and temperature is normal; more than 0.5% of the litres sold needs a leak check.</p>
    </div>
  );
}
