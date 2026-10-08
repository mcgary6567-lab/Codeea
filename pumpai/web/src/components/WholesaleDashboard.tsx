import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from "recharts";
import { AlertOctagon, AlertTriangle, ArrowDownRight, ArrowUpRight, Banknote, CalendarClock, CheckCircle2, ClipboardList, Info, Lightbulb, Plus, Search, Truck, Wallet } from "lucide-react";
import { ChequeForm, OrderForm, PromiseForm } from "./WholesaleDesk";
import { WholesaleVoice } from "./WholesaleVoice";
import { api, useApi } from "../lib/api";
import { Loading, Modal, useAction } from "./ui";
import { PRODUCTS, PRODUCT_COLORS, num, pkr, pkrShort } from "../lib/format";
import { useAuth } from "../App";

/* chart roles: series slots 1 and 3 of the validated palette; recessive grid and axes */
const BILLED = "#2a78d6", RECEIVED = "#1baf7a", GRID = "#e5e7eb", AXIS = "#6b7280";
/* due ageing: one hue, light → dark as money gets older (sequential, labelled — never colour alone) */
const ACT_UR: Record<string, string> = { payment: "رقم لیں", statement: "حساب بھیجیں", open: "کلائنٹ", rates: "ریٹ", trip: "ٹرپ", fleet: "ڈرائیور", orders: "آرڈر", collect: "وصولی", edit: "تبدیل" };
const AGE = [
  { k: "d0_15", label: "0–15 days", fill: "#fde68a" }, { k: "d16_30", label: "16–30 days", fill: "#fbbf24" },
  { k: "d31_60", label: "31–60 days", fill: "#d97706" }, { k: "d60", label: "60+ days", fill: "#92400e" },
] as const;
const LEVEL = {
  critical: { icon: AlertOctagon, cls: "text-red-600", ring: "border-l-red-500", label: "Urgent" },
  warning: { icon: AlertTriangle, cls: "text-amber-600", ring: "border-l-amber-500", label: "Soon" },
  info: { icon: Info, cls: "text-sky-600", ring: "border-l-sky-500", label: "Idea" },
  good: { icon: CheckCircle2, cls: "text-emerald-600", ring: "border-l-emerald-500", label: "Good" },
} as const;
const HEALTH: Record<string, { label: string; dot: string }> = {
  green: { label: "Good", dot: "bg-emerald-500" }, amber: { label: "Watch", dot: "bg-amber-500" }, red: { label: "Risk", dot: "bg-red-500" },
};

const Change = ({ v }: { v: number | null }) => v == null ? null : (
  <span className={`inline-flex items-center gap-0.5 text-xs font-semibold ${v >= 0 ? "text-emerald-700" : "text-red-600"}`}>
    {v >= 0 ? <ArrowUpRight size={13} /> : <ArrowDownRight size={13} />}{Math.abs(v)}% vs last month
  </span>
);
const Ur = ({ children, className = "" }: { children: React.ReactNode; className?: string }) => <span lang="ur" dir="rtl" className={`font-urdu ${className}`}>{children}</span>;
const Kpi = ({ label, ur, value, sub, accent }: { label: string; ur?: string; value: string; sub?: React.ReactNode; accent?: string }) => (
  <div className="card h-full p-4">
    <div className="flex flex-wrap items-baseline justify-between gap-x-2 text-xs font-medium uppercase tracking-wide text-slate-500"><span>{label}</span>{ur && <Ur className="text-sm normal-case">{ur}</Ur>}</div>
    <div className={`mt-1 text-2xl font-bold tabular-nums ${accent ?? "text-slate-900"}`}>{value}</div>
    {sub && <div className="mt-1 text-xs text-slate-500">{sub}</div>}
  </div>
);

/** Wholesale home: today's actions, KPIs, suggestions, trends, ageing and client health. */
export function WholesaleDashboard({ onTrip, onAddClient, onFleet, onTab }: { onTrip: () => void; onAddClient: () => void; onFleet: () => void; onTab: (t: string) => void }) {
  const { can } = useAuth();
  const nav = useNavigate();
  const { data, reload } = useApi<any>("/wholesale/dashboard");
  const [desk, setDesk] = useState<null | "order" | "cheque" | "promise">(null);
  const [allSug, setAllSug] = useState(false);
  const [pick, setPick] = useState<null | "supply" | "payment">(null);
  const [q, setQ] = useState("");
  const { run } = useAction();
  if (!data) return <Loading />;
  const k = data.kpi;
  const manage = can("wholesale.manage");
  const act = async (a: any) => {
    if (a.kind === "trip") return onTrip();
    if (a.kind === "fleet") return onFleet();
    if (a.kind === "orders" || a.kind === "collect") return onTab(a.kind);
    if (a.kind === "statement") {
      const mon = new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 7);
      return run(() => api(`/wholesale/clients/${a.client_id}/send-statement`, { body: { month: mon } }), "Statement sent on WhatsApp");
    }
    nav(`/wholesale/${a.client_id}${a.kind === "open" ? "" : `?do=${a.kind}`}`);
  };
  const ageTotal = AGE.reduce((s, a) => s + data.ageing[a.k], 0);
  const clients = data.clients.filter((c: any) => !q || c.name.toLowerCase().includes(q.toLowerCase()));

  return (
    <div className="space-y-5">
      <WholesaleVoice onDone={reload} />
      {manage && (
        <div className="grid grid-cols-4 gap-2 sm:grid-cols-2 sm:gap-3 md:grid-cols-4 2xl:grid-cols-7">
          {[
            { label: "New supply", short: "Supply", ur: "سپلائی", sub: "one client", icon: Truck, cls: "bg-brand-600 text-white", go: () => setPick("supply") },
            { label: "Tanker trip", short: "Trip", ur: "ٹینکر ٹرپ", sub: "several drops", icon: Truck, cls: "bg-slate-800 text-white", go: onTrip },
            { label: "Receive payment", short: "Payment", ur: "رقم وصول", sub: "cash / bank", icon: Wallet, cls: "bg-emerald-600 text-white", go: () => setPick("payment") },
            { label: "Book order", short: "Order", ur: "آرڈر", sub: "litres for a day", icon: ClipboardList, cls: "bg-amber-500 text-white", go: () => setDesk("order") },
            { label: "Cheque received", short: "Cheque", ur: "چیک", sub: "post-dated too", icon: Banknote, cls: "bg-white text-slate-800 ring-1 ring-slate-200", go: () => setDesk("cheque") },
            { label: "Payment promise", short: "Promise", ur: "وعدہ", sub: "will pay on …", icon: CalendarClock, cls: "bg-white text-slate-800 ring-1 ring-slate-200", go: () => setDesk("promise") },
            { label: "Add client", short: "Client", ur: "نیا کلائنٹ", sub: "with rate card", icon: Plus, cls: "bg-white text-slate-800 ring-1 ring-slate-200", go: onAddClient },
          ].map((b) => (
            <button key={b.label} onClick={b.go} className={`flex flex-col items-center gap-1 rounded-2xl px-1 py-2.5 text-center shadow-sm active:scale-[.98] sm:flex-row sm:gap-3 sm:px-4 sm:py-3 sm:text-left ${b.cls}`}>
              <b.icon size={22} className="shrink-0" /><span className="min-w-0"><span className="block text-[13px] font-semibold leading-tight sm:hidden">{b.short}</span><Ur className="block text-xs leading-tight sm:hidden">{b.ur}</Ur>
                <span className="hidden font-semibold sm:block">{b.label} · <Ur className="font-normal">{b.ur}</Ur></span><span className="hidden text-xs opacity-75 sm:block">{b.sub}</span></span>
            </button>
          ))}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Total due" ur="کل بقایا" value={pkrShort(k.total_due)} accent="text-amber-700" sub={k.overdue_30 > 0 ? <span className="font-medium text-red-600">{pkrShort(k.overdue_30)} older than 30 days</span> : "Nothing older than 30 days"} />
        <Kpi label="Litres this month" ur="اس مہینے لیٹر" value={`${num(k.month_litres)} L`} sub={<Change v={k.month_litres_change} />} />
        <Kpi label="Billed this month" ur="اس مہینے بل" value={pkrShort(k.month_billed)} sub={`${k.trips_month} tanker trips`} />
        <Kpi label="Received this month" ur="اس مہینے وصولی" value={pkrShort(k.month_received)} accent="text-emerald-700" sub={k.collection_pct == null ? undefined : k.collection_pct > 100 ? "More than billed — old dues are coming down" : `${k.collection_pct}% of this month's billing`} />
        <Kpi label="Profit (est.)" ur="منافع" value={k.profit_estimate != null ? pkrShort(k.profit_estimate) : "—"} sub={k.profit_estimate != null
          ? <>{k.margin_per_l != null ? `Rs ${k.margin_per_l.toFixed(2)}/L over cost` : ""} · <Change v={k.month_profit_change} /> {k.last_month_profit != null && <span className="text-slate-400">(pichle mahine {pkrShort(k.last_month_profit)})</span>}</>
          : "Add purchase rates to see profit"} />
        <Kpi label="Today" ur="آج" value={`${num(k.today.litres)} L`} sub={`Received ${pkr(k.today.received)}`} />
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <button className="text-left" onClick={() => onTab("orders")}><Kpi label="Open orders" ur="باقی آرڈر" value={String(k.open_orders)} accent={k.orders_today ? "text-amber-700" : undefined} sub={`${num(k.open_orders_l)} L booked${k.orders_today ? ` · ${k.orders_today} for today / late` : ""}`} /></button>
        <button className="text-left" onClick={() => onTab("collect")}><Kpi label="Promised today" ur="آج کے وعدے" value={pkrShort(k.promised_today)} accent="text-amber-700" sub={k.broken_promises ? <span className="font-medium text-red-600">{k.broken_promises} promise{k.broken_promises > 1 ? "s" : ""} broken</span> : "no broken promises"} /></button>
        <button className="text-left" onClick={() => onTab("collect")}><Kpi label="Cheques not cleared" ur="چیک باقی" value={pkrShort(k.cheques_in_hand)} sub="in hand + deposited" /></button>
        <button className="text-left" onClick={() => onTab("collect")}><Kpi label="Collection this month" ur="وصولی %" value={k.collection_pct == null ? "—" : `${k.collection_pct}%`} accent={k.collection_pct != null && k.collection_pct < 80 ? "text-red-600" : "text-emerald-700"} sub="received ÷ billed" /></button>
      </div>

      <div className="card overflow-hidden">
        <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-3"><Lightbulb size={18} className="text-amber-500" /><h2 className="font-semibold">Suggestions for today · <Ur>آج کے مشورے</Ur></h2><span className="hidden text-xs text-slate-500 sm:inline">— worked out from dues, ordering habits, rates, stock and fleet</span></div>
        {data.suggestions.length ? (
          <ul className="divide-y divide-slate-100">
            {(allSug ? data.suggestions : data.suggestions.slice(0, 6)).map((s: any, i: number) => {
              const L = LEVEL[s.level as keyof typeof LEVEL];
              return (
                <li key={i} className={`flex flex-wrap items-center gap-3 border-l-4 px-4 py-3 ${L.ring}`}>
                  <L.icon size={18} className={`shrink-0 ${L.cls}`} aria-label={L.label} />
                  <div className="min-w-0 flex-1 basis-[calc(100%-2.5rem)] sm:basis-0"><div className="font-medium">{s.title}</div>{s.ur && <div className="text-right text-slate-800"><Ur className="leading-loose">{s.ur}</Ur></div>}<div className="text-sm text-slate-600">{s.detail}</div></div>
                  {s.action && manage && <button className="btn-secondary ml-8 !py-1.5 text-sm sm:ml-0" onClick={() => act(s.action)}>{s.action.label}{ACT_UR[s.action.kind] && <> · <Ur>{ACT_UR[s.action.kind]}</Ur></>}</button>}
                </li>
              );
            })}
          </ul>
        ) : <p className="p-4 text-sm text-slate-500">All clear — nothing needs attention today.</p>}
        {data.suggestions.length > 6 && <button className="w-full border-t border-slate-100 py-2.5 text-sm font-medium text-brand-700 hover:bg-slate-50" onClick={() => setAllSug(!allSug)}>
          {allSug ? "Show fewer" : `Show all ${data.suggestions.length} suggestions`}</button>}
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <div className="card p-4">
          <h2 className="font-semibold">Litres supplied — last 30 days · <Ur>لیٹر سپلائی</Ur></h2>
          <p className="mb-2 text-xs text-slate-500">Per day, by fuel</p>
          <div className="h-64"><ResponsiveContainer>
            <BarChart data={data.daily} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="day" tickFormatter={(d) => d.slice(8)} tick={{ fill: AXIS, fontSize: 11 }} tickLine={false} axisLine={{ stroke: GRID }} interval={2} />
              <YAxis tickFormatter={(v) => (v >= 1000 ? `${v / 1000}k` : v)} tick={{ fill: AXIS, fontSize: 11 }} tickLine={false} axisLine={false} width={36} />
              <Tooltip cursor={{ fill: "rgba(15,23,42,.05)" }} formatter={(v: number, n: string) => [`${num(v)} L`, PRODUCTS[n] ?? n]} labelFormatter={(d) => d} />
              <Legend formatter={(v) => <span className="text-xs text-slate-700">{PRODUCTS[v] ?? v}</span>} iconType="square" iconSize={10} />
              {Object.keys(PRODUCTS).map((p, i, arr) => <Bar key={p} dataKey={p} stackId="l" fill={PRODUCT_COLORS[p]} stroke="#fff" strokeWidth={1} radius={i === arr.length - 1 ? [4, 4, 0, 0] : 0} maxBarSize={22} />)}
            </BarChart>
          </ResponsiveContainer></div>
        </div>
        <div className="card p-4">
          <h2 className="font-semibold">Billed vs received — last 8 weeks · <Ur>بل اور وصولی</Ur></h2>
          <p className="mb-2 text-xs text-slate-500">When received stays below billed, dues are growing</p>
          <div className="h-64"><ResponsiveContainer>
            <BarChart data={data.weekly} margin={{ top: 4, right: 4, left: 0, bottom: 0 }} barGap={2}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="week" tick={{ fill: AXIS, fontSize: 11 }} tickLine={false} axisLine={{ stroke: GRID }} tickFormatter={(w) => `wk ${w}`} />
              <YAxis tickFormatter={(v) => (v >= 100_000 ? `${Math.round(v / 100_000)}L` : v >= 1000 ? `${Math.round(v / 1000)}k` : v)} tick={{ fill: AXIS, fontSize: 11 }} tickLine={false} axisLine={false} width={44} />
              <Tooltip cursor={{ fill: "rgba(15,23,42,.05)" }} formatter={(v: number, n: string) => [pkr(v), n === "billed" ? "Billed" : "Received"]} labelFormatter={(w) => `Week of ${w}`} />
              <Legend formatter={(v) => <span className="text-xs text-slate-700">{v === "billed" ? "Billed" : "Received"}</span>} iconType="square" iconSize={10} />
              <Bar dataKey="billed" fill={BILLED} radius={[4, 4, 0, 0]} maxBarSize={18} />
              <Bar dataKey="received" fill={RECEIVED} radius={[4, 4, 0, 0]} maxBarSize={18} />
            </BarChart>
          </ResponsiveContainer></div>
        </div>
      </div>

      <div className="card p-4">
        <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2"><h2 className="font-semibold">How old is the money owed? · <Ur>بقایا کتنا پرانا ہے</Ur></h2><span className="text-sm text-slate-600">Total due {pkr(ageTotal)}</span></div>
        {ageTotal > 0 && <div className="mb-3 flex h-4 gap-0.5 overflow-hidden rounded-md" role="img" aria-label="Due by age">
          {AGE.map((a) => data.ageing[a.k] > 0 && <div key={a.k} title={`${a.label}: ${pkr(data.ageing[a.k])}`} style={{ width: `${(data.ageing[a.k] / ageTotal) * 100}%`, background: a.fill }} />)}
        </div>}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {AGE.map((a) => (
            <div key={a.k} className="rounded-xl bg-slate-50 p-3">
              <div className="flex items-center gap-2 text-xs text-slate-600"><span className="h-3 w-3 rounded-sm" style={{ background: a.fill }} />{a.label}</div>
              <div className="mt-1 text-lg font-semibold tabular-nums">{pkrShort(data.ageing[a.k])}</div>
              <div className="text-xs text-slate-500">{ageTotal ? Math.round((data.ageing[a.k] / ageTotal) * 100) : 0}%</div>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <div className="flex flex-wrap items-center justify-between gap-2 p-4 pb-2">
          <h2 className="font-semibold">Clients at a glance · <Ur>کلائنٹس</Ur></h2>
          <div className="relative w-full sm:w-56"><Search size={15} className="absolute left-2.5 top-2.5 text-slate-400" /><input className="input pl-8" placeholder="Search client" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        </div>
        {/* phone: one card per client */}
        <ul className="divide-y divide-slate-100 sm:hidden">{clients.map((c: any) => (
          <li key={c.id}><button className="w-full px-4 py-3 text-left active:bg-slate-50" onClick={() => nav(`/wholesale/${c.id}`)}>
            <div className="flex items-start gap-2">
              <span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${HEALTH[c.health].dot}`} />
              <span className="min-w-0 flex-1"><span className="block font-semibold">{c.name}</span><span className="text-xs text-slate-500">{c.city}{c.city ? " · " : ""}{num(c.month_l)} L this month</span></span>
              <span className="text-right"><span className="block font-bold tabular-nums">{pkr(c.due)}</span><span className={`text-xs ${c.oldest_days > 30 ? "font-semibold text-red-600" : "text-slate-500"}`}>{c.due > 0 ? `oldest ${c.oldest_days} d` : "nothing due"}</span></span>
            </div>
            {c.limit_pct != null && <div className="ml-4 mt-2 flex items-center gap-2"><div className="h-1.5 flex-1 rounded-full bg-slate-100"><div className="h-full rounded-full" style={{ width: `${Math.min(100, Math.max(0, c.limit_pct))}%`, background: c.limit_pct >= 90 ? "#e34948" : c.limit_pct >= 75 ? "#eda100" : "#2a78d6" }} /></div><span className="text-xs tabular-nums text-slate-600">{c.limit_pct}% of limit</span></div>}
            <div className="ml-4 mt-1 text-xs text-slate-500">Last order {c.last_supply_days == null ? "never" : c.last_supply_days === 0 ? "today" : `${c.last_supply_days} day${c.last_supply_days === 1 ? "" : "s"} ago`}{c.margins.length ? ` · margin ${c.margins.map((m: any) => `${PRODUCTS[m.product].split(" ")[0]} Rs ${m.margin.toFixed(2)}`).join(", ")}` : ""}</div>
          </button></li>
        ))}</ul>
        <div className="hidden overflow-x-auto sm:block">
          <table className="w-full">
            <thead><tr><th className="th">Client</th><th className="th">Health</th><th className="th text-right">Due</th><th className="th">Credit limit used</th><th className="th text-right">Oldest unpaid</th><th className="th text-right">This month</th><th className="th">Last order</th><th className="th">Margin / L</th></tr></thead>
            <tbody>{clients.map((c: any) => (
              <tr key={c.id} className="cursor-pointer hover:bg-slate-50" onClick={() => nav(`/wholesale/${c.id}`)}>
                <td className="td"><div className="font-medium">{c.name}</div><div className="text-xs text-slate-500">{c.city}</div></td>
                <td className="td"><span className="inline-flex items-center gap-1.5 text-sm"><span className={`h-2.5 w-2.5 rounded-full ${HEALTH[c.health].dot}`} />{HEALTH[c.health].label}</span></td>
                <td className="td text-right font-semibold tabular-nums">{pkr(c.due)}</td>
                <td className="td">{c.limit_pct != null ? <div className="flex items-center gap-2"><div className="h-1.5 w-24 rounded-full bg-slate-100"><div className="h-full rounded-full" style={{ width: `${Math.min(100, Math.max(0, c.limit_pct))}%`, background: c.limit_pct >= 90 ? "#e34948" : c.limit_pct >= 75 ? "#eda100" : "#2a78d6" }} /></div><span className="text-xs tabular-nums text-slate-600">{c.limit_pct}%</span></div> : <span className="text-xs text-slate-400">no limit</span>}</td>
                <td className={`td text-right text-sm tabular-nums ${c.oldest_days > 30 ? "font-semibold text-red-600" : ""}`}>{c.due > 0 ? `${c.oldest_days} days` : "—"}</td>
                <td className="td text-right tabular-nums">{num(c.month_l)} L</td>
                <td className="td text-xs">{c.last_supply_days == null ? "never" : c.last_supply_days === 0 ? "today" : `${c.last_supply_days} day${c.last_supply_days === 1 ? "" : "s"} ago`}{c.usual_gap_days ? <div className="text-slate-500">usually every {c.usual_gap_days} d</div> : null}</td>
                <td className="td text-xs">{c.margins.map((m: any) => <div key={m.product} className={m.margin < 1 ? "font-semibold text-red-600" : "text-slate-700"}>{PRODUCTS[m.product]} Rs {m.margin.toFixed(2)}</div>)}{!c.margins.length && <span className="text-slate-400">—</span>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </div>

      {desk === "order" && <OrderForm onClose={() => setDesk(null)} onDone={() => { setDesk(null); reload(); }} />}
      {desk === "cheque" && <ChequeForm onClose={() => setDesk(null)} onDone={() => { setDesk(null); reload(); }} />}
      {desk === "promise" && <PromiseForm onClose={() => setDesk(null)} onDone={() => { setDesk(null); reload(); }} />}
      {pick && (
        <Modal open onClose={() => setPick(null)} title={pick === "supply" ? "Supply to which client?" : "Payment from which client?"}>
          <div className="max-h-[60vh] space-y-1 overflow-y-auto">
            {data.clients.map((c: any) => (
              <button key={c.id} className="flex w-full items-center justify-between rounded-lg px-3 py-2 text-left hover:bg-slate-50" onClick={() => nav(`/wholesale/${c.id}?do=${pick}`)}>
                <span><span className="font-medium">{c.name}</span> <span className="text-xs text-slate-500">{c.city}</span></span>
                <span className="text-sm tabular-nums text-slate-600">due {pkr(c.due)}</span>
              </button>
            ))}
          </div>
        </Modal>
      )}
    </div>
  );
}

