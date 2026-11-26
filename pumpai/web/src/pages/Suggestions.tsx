import { useNavigate } from "react-router-dom";
import { AlertOctagon, AlertTriangle, CheckCircle2, Info, Lightbulb } from "lucide-react";
import { Link } from "react-router-dom";
import { api, useApi } from "../lib/api";
import { PageHeader, Loading, useAction } from "../components/ui";
import { useAuth } from "../App";

/** Severity styling — same four levels used across the dashboards. */
const LEVEL = {
  critical: { icon: AlertOctagon, cls: "text-red-600", ring: "border-l-red-500", label: "Urgent" },
  warning: { icon: AlertTriangle, cls: "text-amber-600", ring: "border-l-amber-500", label: "Soon" },
  info: { icon: Info, cls: "text-sky-600", ring: "border-l-sky-500", label: "Idea" },
  good: { icon: CheckCircle2, cls: "text-emerald-600", ring: "border-l-emerald-500", label: "Good" },
} as const;
const ACT_UR: Record<string, string> = { payment: "رقم لیں", statement: "حساب بھیجیں", open: "کلائنٹ", rates: "ریٹ", trip: "ٹرپ", fleet: "ڈرائیور", orders: "آرڈر", collect: "وصولی", edit: "تبدیل" };
const Ur = ({ children, className = "" }: { children: React.ReactNode; className?: string }) => <span lang="ur" dir="rtl" className={`font-urdu ${className}`}>{children}</span>;

/** One suggestion line with an optional action on the right. */
function Row({ s, right }: { s: any; right?: React.ReactNode }) {
  const L = LEVEL[s.level as keyof typeof LEVEL] ?? LEVEL.info;
  return (
    <li className={`flex flex-wrap items-center gap-3 border-l-4 px-4 py-3 ${L.ring}`}>
      <L.icon size={18} className={`shrink-0 ${L.cls}`} aria-label={L.label} />
      <div className="min-w-0 flex-1 basis-[calc(100%-2.5rem)] sm:basis-0">
        <div className="font-medium">{s.title}</div>
        {s.ur && <div className="text-right text-slate-800"><Ur className="leading-loose">{s.ur}</Ur></div>}
        {s.detail && <div className="text-sm text-slate-600">{s.detail}</div>}
      </div>
      {right}
    </li>
  );
}

function Card({ title, ur, hint, children }: { title: string; ur?: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-3">
        <Lightbulb size={18} className="text-amber-500" />
        <h2 className="font-semibold">{title}{ur && <> · <Ur>{ur}</Ur></>}</h2>
        {hint && <span className="hidden text-xs text-slate-500 sm:inline">— {hint}</span>}
      </div>
      {children}
    </div>
  );
}

/** Manager / owner suggestions (shifts, cash, stock, staff, khata, approvals, checks). */
function DeskSuggestions() {
  const { data } = useApi<any>("/dashboard/desk", 60_000);
  if (!data) return <Card title="Suggestions for today" ur="آج کے مشورے"><Loading /></Card>;
  return (
    <Card title="Suggestions for today" ur="آج کے مشورے" hint="from shifts, cash, stock, staff, khata, approvals and checks">
      {data.suggestions.length ? (
        <ul className="divide-y divide-slate-100">
          {data.suggestions.map((s: any, i: number) => (
            <Row key={i} s={s} right={s.to && <Link className="btn-secondary ml-8 !py-1.5 text-sm sm:ml-0" to={s.to}>{s.label ?? "Open"}</Link>} />
          ))}
        </ul>
      ) : <p className="p-4 text-sm text-slate-500">All clear — nothing needs attention right now.</p>}
    </Card>
  );
}

/** Wholesale suggestions (dues, ordering habits, rates, stock and fleet). */
function WholesaleSuggestions() {
  const { data } = useApi<any>("/wholesale/dashboard");
  const { can } = useAuth();
  const nav = useNavigate();
  const { run } = useAction();
  const manage = can("wholesale.manage");
  if (!data) return <Card title="Wholesale suggestions" ur="ہول سیل مشورے"><Loading /></Card>;
  const act = async (a: any) => {
    if (a.kind === "statement") {
      const mon = new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 7);
      return run(() => api(`/wholesale/clients/${a.client_id}/send-statement`, { body: { month: mon } }), "Statement sent on WhatsApp");
    }
    if (["trip", "fleet", "orders", "collect"].includes(a.kind)) return nav(`/wholesale?tab=${a.kind === "fleet" ? "fleet" : a.kind === "trip" ? "trips" : a.kind}`);
    return nav(`/wholesale/${a.client_id}${a.kind === "open" ? "" : `?do=${a.kind}`}`);
  };
  return (
    <Card title="Wholesale suggestions" ur="ہول سیل مشورے" hint="worked out from dues, ordering habits, rates, stock and fleet">
      {data.suggestions.length ? (
        <ul className="divide-y divide-slate-100">
          {data.suggestions.map((s: any, i: number) => (
            <Row key={i} s={s} right={s.action && manage && (
              <button className="btn-secondary ml-8 !py-1.5 text-sm sm:ml-0" onClick={() => act(s.action)}>{s.action.label}{ACT_UR[s.action.kind] && <> · <Ur>{ACT_UR[s.action.kind]}</Ur></>}</button>
            )} />
          ))}
        </ul>
      ) : <p className="p-4 text-sm text-slate-500">All clear — nothing needs attention today.</p>}
    </Card>
  );
}

/** Dedicated page that gathers the suggestions that used to sit on the dashboards. */
export default function Suggestions() {
  const { can } = useAuth();
  const desk = can("dashboard.view");
  const wholesale = can("wholesale.view");
  return (
    <div className="space-y-5">
      <PageHeader title="Suggestions" subtitle="What needs your attention today · آج کیا کرنا ہے" />
      {desk && <DeskSuggestions />}
      {wholesale && <WholesaleSuggestions />}
      {!desk && !wholesale && <p className="card p-6 text-center text-sm text-slate-500">No suggestions for your role.</p>}
    </div>
  );
}
