import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AlertOctagon, AlertTriangle, CheckCircle2, Clock, Droplets, Info, Lightbulb, Receipt, BookOpen, Tag, BarChart3, Users } from "lucide-react";
import { useApi } from "../lib/api";
import { Loading } from "./ui";
import { BankSummary } from "./BankParts";
import { OnlineToday } from "./OnlineMoney";
import { useAuth } from "../App";
import { num, pkr, pkrShort } from "../lib/format";

const LEVEL = {
  critical: { icon: AlertOctagon, cls: "text-red-600", ring: "border-l-red-500", label: "Urgent" },
  warning: { icon: AlertTriangle, cls: "text-amber-600", ring: "border-l-amber-500", label: "Soon" },
  info: { icon: Info, cls: "text-sky-600", ring: "border-l-sky-500", label: "Idea" },
  good: { icon: CheckCircle2, cls: "text-emerald-600", ring: "border-l-emerald-500", label: "Good" },
} as const;
const STAFF: Record<string, { label: string; dot: string }> = {
  present: { label: "On duty", dot: "bg-emerald-500" }, late: { label: "Late", dot: "bg-amber-500" }, missing: { label: "Not in", dot: "bg-red-500" },
  due: { label: "Not due yet", dot: "bg-slate-300" }, leave: { label: "On leave", dot: "bg-blue-400" }, off: { label: "Weekly off", dot: "bg-slate-300" },
};
const ACTIONS = [
  { to: "/shifts", label: "Shifts", sub: "open / close", icon: Clock, cls: "bg-brand-600 text-white" },
  { to: "/stock", label: "Dip & stock", sub: "tanks, tanker", icon: Droplets, cls: "bg-slate-800 text-white" },
  { to: "/expenses", label: "Expense", sub: "add / approve", icon: Receipt, cls: "bg-amber-500 text-white" },
  { to: "/khata", label: "Khata", sub: "payment, reminder", icon: BookOpen, cls: "bg-emerald-600 text-white" },
  { to: "/prices", label: "Prices", sub: "today's rates", icon: Tag, cls: "bg-white text-slate-800 ring-1 ring-slate-200" },
  { to: "/reports", label: "Reports", sub: "day, month", icon: BarChart3, cls: "bg-white text-slate-800 ring-1 ring-slate-200" },
];
const Kpi = ({ label, value, sub, accent, to }: { label: string; value: React.ReactNode; sub?: React.ReactNode; accent?: string; to?: string }) => {
  const body = (
    <div className="card h-full p-4 transition hover:ring-slate-300">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-bold tabular-nums ${accent ?? "text-slate-900"}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-500">{sub}</div>}
    </div>
  );
  return to ? <Link to={to}>{body}</Link> : body;
};

/** Top of the manager / owner dashboard: one-tap actions, today's numbers, suggestions, live shifts and staff. */
export function ManagerDesk({ k }: { k: any }) {
  const { data } = useApi<any>("/dashboard/desk", 60_000);
  const nav = useNavigate();
  const { can } = useAuth();
  const [all, setAll] = useState(false);
  const vs = k.today.vs_yesterday_pct;
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-3 gap-3 md:grid-cols-6">
        {ACTIONS.map((a) => (
          <Link key={a.to} to={a.to} className={`flex flex-col items-center gap-1 rounded-2xl px-2 py-3 text-center shadow-sm active:scale-[.98] sm:flex-row sm:gap-2.5 sm:px-3 sm:text-left ${a.cls}`}>
            <a.icon size={22} className="shrink-0" /><span className="min-w-0 max-w-full"><span className="block text-sm font-semibold leading-tight sm:truncate sm:text-base">{a.label}</span><span className="hidden truncate text-xs opacity-75 sm:block">{a.sub}</span></span>
          </Link>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Today's sales" value={pkrShort(k.today.amount)} accent="text-emerald-700" to="/reports"
          sub={vs == null ? `${k.today.txns} sales` : <span className={vs >= 0 ? "font-semibold text-emerald-700" : "font-semibold text-red-600"}>{vs >= 0 ? "▲" : "▼"} {Math.abs(vs)}% vs same time yesterday</span>} />
        <Kpi label="Litres today" value={`${num(k.today.litres)} L`} sub={`${k.today.txns} sales`} to="/reports" />
        <Kpi label="Cash in open shifts" value={data ? pkrShort(data.cash_in_shifts) : "…"} sub={data ? `${data.shifts.length} shift${data.shifts.length === 1 ? "" : "s"} running` : undefined} to="/shifts" />
        <Kpi label="Khata outstanding" value={pkrShort(k.khata.outstanding)} accent="text-amber-700" to="/khata"
          sub={data?.khata_overdue ? <span className="font-medium text-red-600">{pkrShort(data.khata_overdue)} unpaid 30+ days</span> : `${k.khata.debtors} customers owe`} />
        <Kpi label="Staff on duty" value={data ? `${data.staff_summary.on_duty} / ${data.staff_summary.total}` : "…"} to="/staff"
          sub={data ? <>{data.staff_summary.late ? <span className="text-amber-700">{data.staff_summary.late} late </span> : null}{data.staff_summary.missing ? <span className="font-medium text-red-600">· {data.staff_summary.missing} not in</span> : data.staff_summary.late ? null : "everyone on time"}</> : undefined} />
        <Kpi label="Open alerts" value={k.open_alerts} accent={k.open_alerts ? "text-red-600" : undefined} sub={`${k.whatsapp.human} chats need a person`} to="/alerts" />
      </div>

      {/* the owner overview above already has it for users who can see reports */}
      {!can("reports.view") && data?.online && <OnlineToday o={data.online} onBank={can("bank.view") ? () => nav("/cash") : undefined} />}
      {can("bank.view") && !can("reports.view") && <BankSummary />}

      <div className="card overflow-hidden">
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-3"><Lightbulb size={18} className="text-amber-500" /><h2 className="font-semibold">Suggestions for today</h2>
          <span className="hidden text-xs text-slate-500 sm:inline">— from shifts, cash, stock, staff, khata, approvals and checks</span></div>
        {!data ? <Loading /> : data.suggestions.length ? (
          <ul className="divide-y divide-slate-100">
            {(all ? data.suggestions : data.suggestions.slice(0, 6)).map((s: any, i: number) => {
              const L = LEVEL[s.level as keyof typeof LEVEL];
              return (
                <li key={i} className={`flex flex-wrap items-center gap-3 border-l-4 px-4 py-3 ${L.ring}`}>
                  <L.icon size={18} className={`shrink-0 ${L.cls}`} aria-label={L.label} />
                  <div className="min-w-0 flex-1 basis-[calc(100%-2.5rem)] sm:basis-0"><div className="font-medium">{s.title}</div><div className="text-sm text-slate-600">{s.detail}</div></div>
                  {s.to && <Link className="btn-secondary ml-8 !py-1.5 text-sm sm:ml-0" to={s.to}>{s.label ?? "Open"}</Link>}
                </li>
              );
            })}
          </ul>
        ) : <p className="p-4 text-sm text-slate-500">All clear — nothing needs attention right now.</p>}
        {data && data.suggestions.length > 6 && <button className="w-full border-t border-slate-100 py-2 text-sm font-medium text-brand-700 hover:bg-slate-50" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show all ${data.suggestions.length} suggestions`}</button>}
      </div>

      {data && (
        <div className="grid gap-5 lg:grid-cols-2">
          <div className="card">
            <div className="flex items-center justify-between p-4 pb-2"><h2 className="flex items-center gap-2 font-semibold"><Clock size={16} /> Shifts running now</h2><Link to="/shifts" className="inline-block py-2 text-xs text-brand-600 hover:underline">All shifts →</Link></div>
            {data.shifts.length ? (
              <table className="w-full"><thead><tr><th className="th">Salesman</th><th className="th">Open for</th><th className="th text-right">Litres</th><th className="th text-right">Sales</th><th className="th text-right">Cash in bag</th></tr></thead>
                <tbody>{data.shifts.map((s: any) => (
                  <tr key={s.id}><td className="td"><div className="font-medium">{s.attendant}</div><div className="text-xs text-slate-500">{s.station.replace(/^Al-Madina /, "")}</div></td>
                    <td className={`td text-sm ${s.hours >= 12 ? "font-semibold text-red-600" : ""}`}>{Math.floor(s.hours)}h {Math.round((s.hours % 1) * 60)}m</td>
                    <td className="td text-right tabular-nums">{num(s.litres)} L</td><td className="td text-right tabular-nums">{pkr(s.amount)}</td><td className="td text-right font-medium tabular-nums">{pkr(s.cash_expected)}</td></tr>
                ))}</tbody></table>
            ) : <p className="px-4 pb-4 text-sm text-slate-500">No shift is open right now.</p>}
          </div>
          <div className="card">
            <div className="flex items-center justify-between p-4 pb-2"><h2 className="flex items-center gap-2 font-semibold"><Users size={16} /> Staff today</h2><Link to="/staff" className="inline-block py-2 text-xs text-brand-600 hover:underline">Attendance →</Link></div>
            {data.staff.length ? (
              <ul className="grid gap-x-4 px-4 pb-4 sm:grid-cols-2">
                {data.staff.map((s: any) => (
                  <li key={s.id} className="flex items-center gap-2 border-b border-slate-100 py-2 text-sm">
                    <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${STAFF[s.status].dot}`} />
                    <span className="min-w-0 flex-1 truncate"><b className="font-medium">{s.name}</b> <span className="text-xs capitalize text-slate-500">{s.role}</span></span>
                    <span className="text-xs text-slate-600">{STAFF[s.status].label}{s.check_in ? ` · ${new Date(s.check_in).toLocaleTimeString("en-PK", { hour: "2-digit", minute: "2-digit" })}` : ` · duty ${s.duty_start}`}{s.status === "late" ? ` (${s.late_minutes}m)` : ""}</span>
                  </li>
                ))}
              </ul>
            ) : <p className="px-4 pb-4 text-sm text-slate-500">Set duty times on the Staff page to track attendance.</p>}
          </div>
        </div>
      )}
    </div>
  );
}
