import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from "recharts";
import { Fuel, Wallet, BookOpen, Bell, MessageCircle, Sparkles, TrendingUp, Users, CreditCard, BarChart3, Send } from "lucide-react";
import { api, useApi, useLiveEvents } from "../lib/api";
import { PageHeader, Stat, Loading, ErrorBox, Badge, severityTone } from "../components/ui";
import { PRODUCTS, PRODUCT_COLORS, num, pkrShort, d, ago } from "../lib/format";
import { QuickAddTiles } from "../components/QuickAdd";

const insightIcon: Record<string, any> = { fuel: Fuel, trend: TrendingUp, users: Users, credit: CreditCard, chart: BarChart3 };
const toneCls: Record<string, string> = { good: "border-l-emerald-500", warn: "border-l-amber-500", bad: "border-l-red-500", info: "border-l-blue-500" };

export default function Dashboard() {
  const { data, error, reload } = useApi<any>("/dashboard", 60_000);
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

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Today's sales" value={pkrShort(k.today.amount)} icon={<Wallet size={16} />} tone="green"
          hint={<>{num(k.today.litres)} L · {k.today.txns} txns {k.today.vs_yesterday_pct !== null && <span className={k.today.vs_yesterday_pct >= 0 ? "text-emerald-600" : "text-slate-500"}>· {k.today.vs_yesterday_pct > 0 ? "+" : ""}{k.today.vs_yesterday_pct}% vs same time yesterday</span>}</>} />
        <Stat label="Khata outstanding" value={pkrShort(k.khata.outstanding)} icon={<BookOpen size={16} />} tone="amber" hint={`${k.khata.debtors} customers owe`} />
        <Stat label="WhatsApp" value={<span>{k.whatsapp.ai_replies_today} <span className="text-sm font-normal text-slate-500">AI replies today</span></span>} icon={<MessageCircle size={16} />} tone="blue"
          hint={`${k.whatsapp.unread} unread · ${k.whatsapp.human} need a human · ${k.pending_orders} pending orders`} />
        <Stat label="Open alerts" value={k.open_alerts} icon={<Bell size={16} />} tone={k.open_alerts ? "red" : "slate"} hint={<Link to="/alerts" className="text-brand-600 hover:underline">Review alerts →</Link>} />
      </div>

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
            <Link to="/stock" className="text-xs text-brand-600 hover:underline">Manage stock →</Link>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {data.tanks.map((t: any) => {
              const low = t.days_to_reorder <= 1.5;
              return (
                <div key={t.id} className="rounded-lg border border-slate-200 p-3">
                  <div className="flex items-center justify-between text-sm">
                    <span className="font-medium">{t.name}</span>
                    <span className="text-xs text-slate-500">{t.station_name.replace("Al-Madina ", "")}</span>
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
            <div className="mb-2 flex items-center justify-between"><h2 className="font-semibold">Latest alerts</h2><Link to="/alerts" className="text-xs text-brand-600 hover:underline">All →</Link></div>
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

