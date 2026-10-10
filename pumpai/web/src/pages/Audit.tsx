import { useState } from "react";
import { Download, ChevronDown, ChevronRight, ShieldCheck, Scale, CheckCircle2, AlertTriangle, Loader2 } from "lucide-react";
import { api, linkToken, useApi } from "../lib/api";
import { Badge, Empty, Field, Loading, PageHeader } from "../components/ui";
import { dt, pkr } from "../lib/format";

const KINDS: [string, string, string][] = [
  ["", "All", "slate"], ["price", "Prices", "blue"], ["undo", "Undo", "amber"], ["delete", "Deleted", "red"], ["edit", "Edited", "violet"],
  ["create", "Added", "green"], ["approval", "Approvals", "blue"], ["settings", "Settings", "slate"], ["money", "Coupons / wallet / loans", "green"], ["login", "Sign-ins", "slate"],
];
const tone = (k: string) => KINDS.find((x) => x[0] === k)?.[2] ?? "slate";
const show = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));

type CheckRow = { label: string; book: number; ledger: number; diff: number; ok: boolean; note?: string };
type CheckResult = { ok: boolean; checked_at: string; max_diff: number; checks: CheckRow[]; failed: string[] };

/** CEO-only "Hisaab check" — one button re-runs the whole-system reconciliation live and proves everything tallies to 0 rupee. */
function BooksCheck() {
  const [res, setRes] = useState<CheckResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const run = async () => {
    setLoading(true); setErr(""); setRes(null);
    try { setRes(await api<CheckResult>("/books/check")); }
    catch (e: any) { setErr(e?.message ?? "Check nahi chal saka"); }
    finally { setLoading(false); }
  };
  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Scale size={18} className="text-brand-600" />
          <div>
            <div className="font-medium">Hisaab check — poora system</div>
            <div className="text-xs text-slate-500">Har balance (cash, bank, khata, wholesale, carriage, supplier, coupon, wallet, staff, stock) ko ledger se milata hai. Koi bhi entry theek karne ke baad press karein — pakka ho jayega ke 1 rupaye ka bhi farq nahi.</div>
          </div>
        </div>
        <button className="btn-primary" onClick={run} disabled={loading}>
          {loading ? <Loader2 size={15} className="animate-spin" /> : <Scale size={15} />} Hisaab check karein
        </button>
      </div>
      {err && <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>}
      {res && (
        <div className="mt-3">
          {res.ok ? (
            <div className="flex items-center gap-2 rounded-lg bg-emerald-50 px-3 py-2.5 text-emerald-800">
              <CheckCircle2 size={18} /> <span className="font-medium">Sab tally — 0 rupaye ka farq.</span>
              <span className="text-xs text-emerald-600">Sabse bada farq: {pkr(res.max_diff)}</span>
            </div>
          ) : (
            <div className="flex items-center gap-2 rounded-lg bg-amber-50 px-3 py-2.5 text-amber-800">
              <AlertTriangle size={18} /> <span className="font-medium">{res.failed.length} jagah farq hai</span>
              <span className="text-xs text-amber-600">Sabse bada farq: {pkr(res.max_diff)}</span>
            </div>
          )}
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-slate-500"><tr><th className="py-1.5">Mad</th><th className="text-right">Book</th><th className="text-right">Ledger</th><th className="text-right">Farq</th><th className="py-1.5 pl-3">Haalat</th></tr></thead>
              <tbody>
                {res.checks.map((c) => (
                  <tr key={c.label} className="border-t border-slate-100">
                    <td className="py-1.5 pr-3">{c.label}{c.note && <span className="block text-[11px] text-amber-600">{c.note}</span>}</td>
                    <td className="text-right tabular-nums">{pkr(c.book)}</td>
                    <td className="text-right tabular-nums">{pkr(c.ledger)}</td>
                    <td className={`text-right tabular-nums ${Math.abs(c.diff) > 1 ? "font-semibold text-red-700" : "text-slate-400"}`}>{pkr(c.diff)}</td>
                    <td className="py-1.5 pl-3">{c.ok ? <Badge tone="green">OK</Badge> : <Badge tone="red">Farq</Badge>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-2 text-[11px] text-slate-400">Checked at {dt(res.checked_at)}</div>
        </div>
      )}
    </div>
  );
}

/** Who changed what, when — prices, undo, deletes, edits, approvals, settings and sign-ins. */
export default function Audit() {
  const [f, setF] = useState({ kind: "", user_id: "", from: "", to: "", q: "", page: 1 });
  const qs = new URLSearchParams(Object.entries(f).filter(([k, v]) => k !== "page" && v !== "").map(([k, v]) => [k, String(v)]));
  if (f.page > 1) qs.set("page", String(f.page));
  const { data } = useApi<any>(`/audit?${qs}`);
  const [open, setOpen] = useState<number | null>(null);
  return (
    <div className="space-y-4">
      <PageHeader title="Audit log" subtitle="Every change in the system: who did it, when, and what it was before"
        actions={<a className="btn-secondary" href={`/api/audit.csv?${qs}&token=${linkToken()}`}><Download size={15} /> Excel / CSV</a>} />
      <BooksCheck />
      <div className="-mx-4 flex gap-1.5 overflow-x-auto px-4 pb-1 sm:mx-0 sm:flex-wrap sm:px-0">
        {KINDS.map(([k, l]) => <button key={k} onClick={() => setF({ ...f, kind: k, page: 1 })} className={`min-h-9 shrink-0 whitespace-nowrap rounded-full px-3 py-1 text-sm sm:min-h-0 ${f.kind === k ? "bg-brand-600 text-white" : "bg-white ring-1 ring-slate-200"}`}>{l}</button>)}
      </div>
      <div className="card flex flex-wrap items-end gap-3 p-3">
        <Field label="Who"><select className="input" value={f.user_id} onChange={(e) => setF({ ...f, user_id: e.target.value, page: 1 })}><option value="">Everyone</option>{(data?.users ?? []).map((u: any) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></Field>
        <Field label="From"><input type="date" className="input" value={f.from} onChange={(e) => setF({ ...f, from: e.target.value, page: 1 })} /></Field>
        <Field label="To"><input type="date" className="input" value={f.to} onChange={(e) => setF({ ...f, to: e.target.value, page: 1 })} /></Field>
        <Field label="Search"><input className="input" placeholder="name, amount, customer…" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value, page: 1 })} /></Field>
      </div>
      {!data ? <Loading /> : (
        <div className="card divide-y divide-slate-100">
          {data.rows.map((r: any) => {
            const changes = r.data?.changes ?? [];
            const isOpen = open === r.id;
            return (
              <div key={r.id}>
                <button onClick={() => setOpen(isOpen ? null : r.id)} className="flex w-full items-start gap-3 px-4 py-2.5 text-left hover:bg-slate-50">
                  {isOpen ? <ChevronDown size={16} className="mt-0.5 text-slate-400" /> : <ChevronRight size={16} className="mt-0.5 text-slate-400" />}
                  <span className="hidden w-36 shrink-0 text-xs text-slate-500 sm:block">{dt(r.at)}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-xs text-slate-500 sm:hidden">{dt(r.at)}</span><span className="font-medium">{r.user ?? "System"}</span> <span className="text-slate-700">{r.label}</span>
                    {changes.length > 0 && <span className="block break-words text-xs text-slate-500 sm:truncate">{changes.slice(0, 3).map((c: any) => `${c.field}: ${show(c.from)} → ${show(c.to)}`).join(" · ")}</span>}
                  </span>
                  <span className="shrink-0"><Badge tone={tone(r.kind)}>{KINDS.find((k) => k[0] === r.kind)?.[1] ?? r.kind}</Badge></span>
                </button>
                {isOpen && (
                  <div className="overflow-x-auto bg-slate-50 px-4 pb-3 text-sm sm:pl-11">
                    {changes.length > 0 && <table className="mb-2 w-full max-w-2xl text-xs"><thead className="text-left text-slate-500"><tr><th className="py-1">Field</th><th>Before</th><th>After</th></tr></thead>
                      <tbody>{changes.map((c: any) => <tr key={c.field} className="border-t border-slate-200"><td className="py-1 font-medium">{c.field}</td><td className="text-red-700 line-through decoration-red-300">{show(c.from)}</td><td className="text-emerald-700">{show(c.to)}</td></tr>)}</tbody></table>}
                    {r.data?.deleted && <div className="mb-2 text-xs"><b>Deleted record:</b> <span className="break-all font-mono">{JSON.stringify(r.data.deleted)}</span></div>}
                    {r.data && !changes.length && !r.data.deleted && <div className="break-all font-mono text-xs text-slate-600">{JSON.stringify(r.data.body ?? r.data)}</div>}
                    <div className="mt-1 font-mono text-[11px] text-slate-400">{r.ref}</div>
                  </div>
                )}
              </div>
            );
          })}
          {!data.rows.length && <Empty><ShieldCheck className="mx-auto mb-1 text-slate-300" />Nothing found for this filter</Empty>}
        </div>
      )}
      {data && data.pages > 1 && <div className="flex flex-wrap items-center justify-center gap-3 text-center text-sm">
        <button className="btn-secondary" disabled={f.page <= 1} onClick={() => setF({ ...f, page: f.page - 1 })}>Newer</button>
        <span>Page {data.page} of {data.pages} · {data.total.toLocaleString()} entries</span>
        <button className="btn-secondary" disabled={f.page >= data.pages} onClick={() => setF({ ...f, page: f.page + 1 })}>Older</button>
      </div>}
    </div>
  );
}
