import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from "recharts";
import { Fuel, Wallet, Sparkles, TrendingUp, Users, CreditCard, BarChart3, Send, Receipt, Truck, Droplets, Scale, Coins } from "lucide-react";
import { api, useApi, useLiveEvents } from "../lib/api";
import { PageHeader, Loading, ErrorBox, Badge, Modal, severityTone } from "../components/ui";
import { PRODUCTS, PRODUCT_COLORS, num, pkr, pkrShort, d, ago } from "../lib/format";
import { QuickAddTiles } from "../components/QuickAdd";
import { HealthCard } from "./Insights";
import { ManagerDesk } from "../components/ManagerDesk";
import { OwnerOverview } from "../components/OwnerOverview";
import { useAuth } from "../App";

const insightIcon: Record<string, any> = { fuel: Fuel, trend: TrendingUp, users: Users, credit: CreditCard, chart: BarChart3 };
const toneCls: Record<string, string> = { good: "border-l-emerald-500", warn: "border-l-amber-500", bad: "border-l-red-500", info: "border-l-blue-500" };

export default function Dashboard() {
  const { data, error, reload } = useApi<any>("/dashboard", 60_000);
  const { can } = useAuth();
  useLiveEvents((e) => { if (e.type === "order") reload(); });

  const chart = useMemo(() => {
    if (!data) return [];
    const rows: any[] = data.series.dates.map((date: string, i: number) => {
      const r: any = { date: d(date) };
      for (const p of Object.keys(PRODUCTS)) r[p] = Math.round(data.series.series[p]?.[i] ?? 0);
      return r;
    });
    rows.pop(); // today's partial day would look like a drop
    const last = rows[rows.length - 1];
    for (const p of Object.keys(PRODUCTS)) last[`${p}_f`] = last[p]; // join actual → forecast
    data.forecast.futureDates.forEach((date: string, i: number) => {
      const r: any = { date: d(date) };
      for (const p of Object.keys(PRODUCTS)) r[`${p}_f`] = data.forecast.products[p]?.forecast[i];
      rows.push(r);
    });
    return rows;
  }, [data]);

  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const k = data.kpis;

  return (
    <div className="space-y-5">
      <PageHeader title="Dashboard" subtitle={`Live business overview · updated ${ago(k.generated_at)}`} />

      {can("reports.view") && <OwnerOverview />}

      <ManagerDesk k={k} />

      <Link to="/insights" className="block"><HealthCard compact /></Link>
      {data.day && <TodayBook b={data.day} />}
      {can("reports.view") && data.biz_profit && <BizProfit p={data.biz_profit} />}

      <div className="grid items-start gap-5 lg:grid-cols-3">
        <div className="card p-4 lg:col-span-2">
          <div className="mb-1 flex items-center justify-between">
            <h2 className="font-semibold">Daily litres by product</h2>
            <span className="text-xs text-slate-500">Solid = actual · dashed = AI forecast (7 days)</span>
          </div>
          <div className="h-72">
            <ResponsiveContainer>
              <LineChart data={chart} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="#e2e8f0" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: "#64748b" }} tickLine={false} axisLine={{ stroke: "#cbd5e1" }} minTickGap={28} />
                <YAxis tick={{ fontSize: 11, fill: "#64748b" }} tickLine={false} axisLine={false} width={48} tickFormatter={(v) => `${Math.round(v / 1000)}k`} />
                <Tooltip formatter={(v: any, name: string) => [`${num(v)} L`, name.endsWith("forecast") ? name : name]} contentStyle={{ borderRadius: 8, fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                {Object.keys(PRODUCTS).map((p) => (
                  <Line key={p} dataKey={p} name={PRODUCTS[p]} stroke={PRODUCT_COLORS[p]} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
                ))}
                {Object.keys(PRODUCTS).map((p) => (
                  <Line key={p + "f"} dataKey={`${p}_f`} name={`${PRODUCTS[p]} forecast`} stroke={PRODUCT_COLORS[p]} strokeWidth={2} strokeDasharray="5 4" dot={false} legendType="none" />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="card p-4">
          <h2 className="mb-3 flex items-center gap-2 font-semibold"><Sparkles size={16} className="text-violet-500" /> AI insights</h2>
          <div className="space-y-2.5">
            {data.insights.map((c: any, i: number) => {
              const Icon = insightIcon[c.icon] ?? Sparkles;
              return (
                <div key={i} className={`rounded-lg border border-slate-200 border-l-4 ${toneCls[c.tone]} p-3`}>
                  <div className="flex items-start gap-2 text-sm font-medium"><Icon size={15} className="mt-0.5 shrink-0 text-slate-500" />{c.title}</div>
                  <p className="mt-1 text-xs text-slate-600">{c.body}</p>
                </div>
              );
            })}
            {!data.insights.length && <p className="text-sm text-slate-500">All good — nothing needs attention.</p>}
          </div>
        </div>
      </div>

      <QuickAddTiles />

      <AskAI />

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="card p-4 lg:col-span-2">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-semibold">Tank stock & stock-out prediction</h2>
            <Link to="/stock" className="inline-block py-2 text-xs text-brand-600 hover:underline">Manage stock →</Link>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {data.tanks.map((t: any) => {
              const low = t.days_to_reorder <= 1.5;
              return (
                <div key={t.id} className="rounded-lg border border-slate-200 p-3">
                  <div className="flex items-center justify-between text-sm">
                    <span className="font-medium">{t.name}</span>
                    <span className="text-xs text-slate-500">{t.station_name}</span>
                  </div>
                  <div className="mt-2 h-2.5 overflow-hidden rounded-full bg-slate-100" role="meter" aria-valuenow={t.fill_pct} aria-valuemin={0} aria-valuemax={100} aria-label={`${t.name} fill level`}>
                    <div className="h-full rounded-full" style={{ width: `${t.fill_pct}%`, background: low ? "#e34948" : PRODUCT_COLORS[t.product] }} />
                  </div>
                  <div className="mt-2 flex justify-between text-xs text-slate-600">
                    <span>{num(t.current_l)} / {num(t.capacity_l)} L ({t.fill_pct}%)</span>
                    <span>~{num(t.forecast_daily_l)} L/day</span>
                  </div>
                  <div className={`mt-1 text-xs ${low ? "font-medium text-red-600" : "text-slate-500"}`}>
                    {low ? `⚠ Order ${num(t.suggested_order_l)} L now · empty in ${t.days_to_empty} days` : `Reorder in ${t.days_to_reorder} days · empty in ${t.days_to_empty} days`}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="space-y-5">
          <div className="card p-4">
            <h2 className="mb-3 font-semibold">Payment mix · 7 days</h2>
            {(() => {
              const total = data.payment_mix.reduce((a: number, r: any) => a + r.v, 0) || 1;
              return data.payment_mix.map((r: any) => (
                <div key={r.k} className="mb-2">
                  <div className="flex justify-between text-xs"><span className="capitalize">{r.k}</span><span className="tabular-nums text-slate-600">{pkrShort(r.v)} · {Math.round((r.v / total) * 100)}%</span></div>
                  <div className="mt-1 h-1.5 rounded-full bg-slate-100"><div className="h-full rounded-full bg-[#2a78d6]" style={{ width: `${(r.v / total) * 100}%` }} /></div>
                </div>
              ));
            })()}
          </div>
          <div className="card p-4">
            <div className="mb-2 flex items-center justify-between"><h2 className="font-semibold">Latest alerts</h2><Link to="/alerts" className="inline-block py-2 text-xs text-brand-600 hover:underline">All →</Link></div>
            <ul className="space-y-2">
              {data.alerts.slice(0, 5).map((a: any) => (
                <li key={a.id} className="text-sm">
                  <Badge tone={severityTone(a.severity)}>{a.severity}</Badge> <span className="ml-1">{a.title}</span>
                  <div className="text-xs text-slate-400">{ago(a.created_at)}</div>
                </li>
              ))}
              {!data.alerts.length && <li className="text-sm text-slate-500">No open alerts 🎉</li>}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Today (Pakistan midnight → now): sold, spent, supplied, stock left and what it is worth. Same numbers as Reports → 24h. */
/** Wholesale + bypass + carriage (kiraya) profit for the owner — today / this week / this month. */
function BizProfit({ p }: { p: any }) {
  const [win, setWin] = useState<"today" | "week" | "month">("month");
  const w = p[win] ?? { wholesale: 0, bypass: 0, carriage: 0, total: 0 };
  const rows = [
    { k: "Wholesale clients", ur: "ہول سیل", v: w.wholesale, cls: "text-sky-700", icon: "🚚" },
    { k: "Bypass (depot → client)", ur: "بائی پاس", v: w.bypass, cls: "text-indigo-700", icon: "🛢️" },
    { k: "Carriage / kiraya", ur: "کرایہ", v: w.carriage, cls: "text-amber-700", icon: "🚛" },
  ];
  const max = Math.max(1, ...rows.map((r) => Math.abs(r.v)));
  return (
    <div className="card p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">Wholesale aur kiraya ka munafa · <span lang="ur" dir="rtl" className="font-urdu">منافع</span></h2>
        <div className="flex rounded-lg bg-slate-100 p-1 text-sm">
          {([["today", "Aaj"], ["week", "Hafta"], ["month", "Mahina"]] as const).map(([key, lbl]) => (
            <button key={key} onClick={() => setWin(key)} className={`min-h-8 rounded-md px-3 py-1 ${win === key ? "bg-white font-semibold shadow" : "text-slate-600"}`}>{lbl}</button>
          ))}
        </div>
      </div>
      <div className="mb-3 rounded-xl bg-emerald-50 p-3 text-center ring-1 ring-emerald-200">
        <div className="text-xs font-medium uppercase tracking-wide text-emerald-700">Total munafa · {win === "today" ? "aaj" : win === "week" ? "is hafte" : "is mahine"}</div>
        <div className="text-3xl font-extrabold tabular-nums text-emerald-800">{pkr(w.total)}</div>
      </div>
      <div className="space-y-2">
        {rows.map((r) => (
          <div key={r.k}>
            <div className="flex items-baseline justify-between text-sm"><span>{r.icon} {r.k} · <span lang="ur" dir="rtl" className="font-urdu text-slate-500">{r.ur}</span></span><span className={`font-semibold tabular-nums ${r.cls}`}>{pkr(r.v)}</span></div>
            <div className="mt-0.5 h-1.5 overflow-hidden rounded-full bg-slate-100"><div className={`h-full rounded-full ${r.v >= 0 ? "bg-emerald-500" : "bg-rose-500"}`} style={{ width: `${(Math.abs(r.v) / max) * 100}%` }} /></div>
          </div>
        ))}
      </div>
      <p className="mt-2 text-xs text-slate-400">Wholesale/bypass = rate − humari khareed cost; kiraya = poora kiraya (humari lagat nahi).</p>
    </div>
  );
}

function TodayBook({ b }: { b: any }) {
  const tile = (icon: any, label: string, urdu: string, value: string, lines: (string | false)[], tone: string) => (
    <div className="rounded-lg border border-slate-200 p-3">
      <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-slate-500">
        <span className={`rounded-md p-1 ${tone}`}>{icon}</span>{label}<span className="font-urdu ml-auto normal-case text-slate-400">{urdu}</span>
      </div>
      <div className="mt-2 text-xl font-semibold tabular-nums text-slate-900">{value}</div>
      {lines.filter(Boolean).map((l, i) => <div key={i} className="text-xs text-slate-500">{l}</div>)}
    </div>
  );
  const s = b.sales;
  const [past, setPast] = useState(false);
  return (
    <div className="card p-4">
      {past && <ClosedDays onClose={() => setPast(false)} />}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">Today's book <span className="font-urdu ml-1 text-sm font-normal text-slate-500">آج کا حساب</span>
          <span className="ml-2 text-xs font-normal text-slate-400">since 12:00 am · updates every minute</span></h2>
        <span className="flex gap-3">
          <button className="inline-block py-2 text-xs text-brand-600 hover:underline" onClick={() => setPast(true)}>Closed days</button>
          <Link to="/cash" className="inline-block py-2 text-xs text-brand-600 hover:underline">Cash & bank →</Link>
          <Link to="/reports" className="inline-block py-2 text-xs text-brand-600 hover:underline">Full report →</Link>
        </span>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {tile(<Wallet size={14} />, "Sales", "سیل", pkr(s.revenue), [
          `Pump ${pkr(s.retail)} · ${num(s.retail_litres)} L · ${s.txns} sales`,
          s.wholesale ? `Wholesale ${pkr(s.wholesale)} · ${num(s.wholesale_litres)} L` : false,
          `Cash ${pkrShort(s.cash)} · Digital ${pkrShort(s.digital)} · Khata ${pkrShort(s.khata)}`,
        ], "bg-emerald-100 text-emerald-700")}
        {tile(<Receipt size={14} />, "Expenses", "خرچہ", pkr(b.expenses.total), [
          b.expenses.by_category.slice(0, 3).map((c: any) => `${c.category} ${pkrShort(c.amount)}`).join(" · ") || "No expenses yet",
          b.expenses.pending.count > 0 && `⏳ ${b.expenses.pending.count} waiting approval (${pkr(b.expenses.pending.amount)})`,
        ], "bg-amber-100 text-amber-700")}
        {tile(<Truck size={14} />, "Supply received", "سپلائی آئی", `${num(b.supply.litres)} L`, [
          `${b.supply.deliveries} tanker${b.supply.deliveries === 1 ? "" : "s"} · cost ${pkr(b.supply.cost)}`,
          ...b.supply.list.slice(0, 2).map((x: any) => `${PRODUCTS[x.product]} ${num(x.received_l)} L${x.supplier ? ` · ${x.supplier}` : ""}`),
        ], "bg-blue-100 text-blue-700")}
        {tile(<Droplets size={14} />, "Stock left", "باقی سٹاک", `${num(b.stock.litres)} L`,
          b.stock.products.map((p: any) => `${p.name} ${num(p.closing_l)} L (sold ${num(p.sold_l)}, in ${num(p.received_l)})`), "bg-slate-100 text-slate-600")}
        {tile(<Coins size={14} />, "Stock value", "سٹاک کی مالیت", pkr(b.stock.value_at_cost), [
          "at purchase cost",
          b.stock.value_at_sale != null && `${pkr(b.stock.value_at_sale)} at today's selling price`,
          b.profit.net != null && `Today's profit (est.) ${pkr(b.profit.net)} after expenses`,
        ], "bg-violet-100 text-violet-700")}
        {tile(<Scale size={14} />, "Balances now", "لینا / دینا", pkr(b.receivables), [
          "people owe us (khata + wholesale)",
          `We owe ${pkr(b.payables)} (suppliers, advances)`,
          b.shifts.closed > 0 && `${b.shifts.closed} shift${b.shifts.closed === 1 ? "" : "s"} closed · cash ${b.shifts.variance < 0 ? "short" : "over"} ${pkr(Math.abs(b.shifts.variance))}`,
        ], "bg-red-100 text-red-700")}
      </div>
      {/* phone: one card per fuel */}
      <ul className="mt-4 space-y-2 sm:hidden">
        {b.stock.products.map((p: any) => (
          <li key={p.product} className="rounded-xl bg-slate-50 p-3 text-sm tabular-nums">
            <div className="flex items-center justify-between font-semibold"><span><span className="mr-2 inline-block h-2.5 w-2.5 rounded-full" style={{ background: PRODUCT_COLORS[p.product] }} />{p.name}</span><span>{num(p.closing_l)} L left</span></div>
            <div className="mt-1 text-slate-600">{num(p.opening_l)} opening + {num(p.received_l)} in − {num(p.sold_l)} sold{p.dip_adjust_l ? ` (dip ${p.dip_adjust_l > 0 ? "+" : ""}${num(p.dip_adjust_l)})` : ""}</div>
            <div className="text-xs text-slate-500">Value {pkr(p.value_at_cost)} at cost · {pkr(p.value_at_sale)} at sale price</div>
          </li>
        ))}
        <li className="flex justify-between px-3 text-sm font-semibold"><span>Total</span><span>{num(b.stock.litres)} L · {pkr(b.stock.value_at_cost)}</span></li>
      </ul>
      <div className="mt-4 hidden overflow-x-auto sm:block">
        <table className="w-full min-w-[640px] text-sm">
          <thead><tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
            <th className="py-2 pr-3">Fuel</th><th className="py-2 pr-3 text-right">Opening</th><th className="py-2 pr-3 text-right">+ Received</th>
            <th className="py-2 pr-3 text-right">− Sold</th><th className="py-2 pr-3 text-right">= Left now</th>
            <th className="py-2 pr-3 text-right">Cost / L</th><th className="py-2 pr-3 text-right">Value at cost</th><th className="py-2 text-right">Value at sale price</th>
          </tr></thead>
          <tbody className="tabular-nums">
            {b.stock.products.map((p: any) => (
              <tr key={p.product} className="border-b border-slate-100">
                <td className="py-2 pr-3 font-medium"><span className="mr-2 inline-block h-2.5 w-2.5 rounded-full" style={{ background: PRODUCT_COLORS[p.product] }} />{p.name}</td>
                <td className="py-2 pr-3 text-right">{num(p.opening_l)} L</td>
                <td className="py-2 pr-3 text-right">{num(p.received_l)} L</td>
                <td className="py-2 pr-3 text-right">{num(p.sold_l)} L{p.dip_adjust_l ? <span className="block text-xs text-slate-400">dip {p.dip_adjust_l > 0 ? "+" : ""}{num(p.dip_adjust_l)} L</span> : null}</td>
                <td className="py-2 pr-3 text-right font-semibold">{num(p.closing_l)} L</td>
                <td className="py-2 pr-3 text-right">{p.cost_rate != null ? `Rs ${num(p.cost_rate, 2)}` : "—"}</td>
                <td className="py-2 pr-3 text-right">{pkr(p.value_at_cost)}</td>
                <td className="py-2 text-right">{pkr(p.value_at_sale)}</td>
              </tr>
            ))}
            <tr className="font-semibold">
              <td className="py-2 pr-3">Total</td><td colSpan={3} /><td className="py-2 pr-3 text-right">{num(b.stock.litres)} L</td><td />
              <td className="py-2 pr-3 text-right">{pkr(b.stock.value_at_cost)}</td><td className="py-2 text-right">{pkr(b.stock.value_at_sale)}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Days closed at midnight, each with its printable report (the same link the owner gets on WhatsApp). */
function ClosedDays({ onClose }: { onClose: () => void }) {
  const { data } = useApi<any[]>("/day-closes");
  return (
    <Modal open onClose={onClose} title="Closed days">
      {!data ? <Loading /> : !data.length ? <p className="text-sm text-slate-500">No day closed yet. Each day closes by itself just after midnight.</p> : (
        <ul className="divide-y divide-slate-100">
          {data.map((d) => (
            <li key={d.id} className="flex items-center justify-between py-2 text-sm">
              <span>{new Date(`${d.day}T12:00:00+05:00`).toLocaleDateString("en-PK", { weekday: "short", day: "numeric", month: "short", year: "numeric" })}</span>
              <a className="inline-flex min-h-10 items-center px-2 text-brand-600 hover:underline" href={d.url} target="_blank" rel="noreferrer">Day report →</a>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}

const SUGGESTIONS = [
  "Kal kitna diesel bikega aur tanker kab mangwana chahiye?",
  "Which attendant has the most cash shortage this week?",
  "Top 5 khata customers and how risky they are",
  "Compare this week's petrol sales vs last week",
];

function AskAI() {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [ans, setAns] = useState<{ answer: string; engine: string } | null>(null);
  const ask = async (question: string) => {
    if (!question.trim()) return;
    setQ(question);
    setBusy(true);
    try { setAns(await api("/ai/ask", { body: { question } })); }
    catch (e: any) { setAns({ answer: e.message, engine: "error" }); }
    finally { setBusy(false); }
  };
  return (
    <div className="card bg-gradient-to-br from-violet-50 to-white p-4">
      <h2 className="flex items-center gap-2 font-semibold"><Sparkles size={16} className="text-violet-500" /> Ask your business anything</h2>
      <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); ask(q); }}>
        <input className="input" placeholder="e.g. Is hafte ka munafa kitna raha? / Which tank runs out first?" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn-primary" disabled={busy}><Send size={15} />{busy ? "Thinking…" : "Ask"}</button>
      </form>
      <div className="mt-2 flex flex-wrap gap-2">
        {SUGGESTIONS.map((s) => <button key={s} onClick={() => ask(s)} className="rounded-full border border-violet-200 bg-white px-3 py-1 text-xs text-violet-700 hover:bg-violet-50">{s}</button>)}
      </div>
      {ans && (
        <div className="mt-3 whitespace-pre-wrap rounded-lg border border-slate-200 bg-white p-3 text-sm leading-relaxed">
          {ans.answer}
          <div className="mt-2 text-xs text-slate-400">Answered by {ans.engine === "claude" ? "Claude AI" : ans.engine === "rules" ? "built-in analytics" : "—"}</div>
        </div>
      )}
    </div>
  );
}

