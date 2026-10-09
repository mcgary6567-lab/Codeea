import { useState } from "react";
import { useNavigate } from "react-router-dom";
import KhataStatement from "../components/KhataStatement";
import { KhataVoice } from "../components/KhataVoice";
import { Ur } from "../components/VoiceShell";
import { TYPE_ICON } from "./Pos";
import { BellRing } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Empty, Loading, PageHeader, Stat, useAction } from "../components/ui";
import { ago, phone, pkr, pkrShort } from "../lib/format";

export default function Khata() {
  const { data, reload } = useApi<any[]>("/khata");
  const nav = useNavigate();
  const { busy, run } = useAction();
  const [group, setGroup] = useState("");
  const [bill, setBill] = useState<number | null>(null);
  if (!data) return <Loading />;
  const INST = ["police", "school", "government", "hospital"];
  const rows = data.filter((c) => !group || (group === "institution" ? INST.includes(c.type) : c.type === group));
  const total = data.reduce((a, c) => a + Math.max(0, c.balance), 0);
  const nearLimit = data.filter((c) => c.credit_limit && c.balance >= 0.8 * c.credit_limit).length;
  const highRisk = data.filter((c) => c.risk_score >= 60).length;

  return (
    <div>
      <PageHeader title="Khata (credit accounts) · کھاتہ" subtitle="Automatic WhatsApp reminders with JazzCash / Easypaisa / Raast links run daily at 11am · روزانہ 11 بجے واٹس ایپ یاد دہانی"
        actions={<button className="btn-secondary" disabled={busy} onClick={() => run(() => api("/automations/khata_reminders/run", { body: {} }), (r: any) => r.result).then(reload)}><BellRing size={15} /> Send reminders now · <Ur>ابھی یاد دہانی بھیجیں</Ur></button>} />
      <div className="mb-4"><KhataVoice onDone={reload} /></div>
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Total outstanding · کل ادھار" value={pkrShort(total)} />
        <Stat label="Credit customers · گاہک" value={data.length} />
        <Stat label="Near limit (80%+) · حد کے قریب" value={nearLimit} tone="amber" />
        <Stat label="High credit risk · خطرہ" value={highRisk} tone="red" />
      </div>
      <AgingCard onOpen={(id) => nav(`/customers/${id}`)} />
      <OpenGovtBills onOpen={setBill} />
      <div className="-mx-4 mb-3 flex gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:flex-wrap sm:px-0">
        {[["", "All · سب"], ["institution", "🏛️ Police / Govt / Schools · سرکاری"], ["fleet", "🚚 Fleets · گاڑیاں"], ["farmer", "🚜 Farmers · زمیندار"], ["business", "🏢 Businesses · کاروبار"], ["retail", "🚗 Retail · عام"]].map(([k, l]) => (
          <button key={k} onClick={() => setGroup(k)} className={`min-h-9 shrink-0 whitespace-nowrap rounded-full px-3 py-1.5 text-sm ${group === k ? "bg-brand-600 text-white" : "border border-slate-300 bg-white"}`}>
            {l} <span className="opacity-70">{pkrShort(data.filter((c) => !k || (k === "institution" ? INST.includes(c.type) : c.type === k)).reduce((a, c) => a + Math.max(0, c.balance), 0))}</span>
          </button>
        ))}
      </div>
      {/* phone: one card per customer */}
      <ul className="card divide-y divide-slate-100 sm:hidden">
        {rows.map((c) => {
          const util = c.credit_limit ? Math.min(100, (c.balance / c.credit_limit) * 100) : 0;
          return (
            <li key={c.id} className="px-4 py-3 active:bg-slate-50" onClick={() => nav(`/customers/${c.id}`)}>
              <div className="flex items-start gap-2">
                <span className="min-w-0 flex-1"><span className="block font-semibold">{TYPE_ICON[c.type] && c.type !== "retail" ? `${TYPE_ICON[c.type]} ` : ""}{c.name}</span>
                  <span className="text-xs text-slate-500">{phone(c.phone)}{c.khata_blocked ? <span className="ml-1 rounded bg-red-100 px-1.5 font-semibold text-red-700">On hold · روکا</span> : null}</span></span>
                <span className="shrink-0 text-right"><span className="block font-bold tabular-nums">{pkr(c.balance)}</span><span className="text-xs text-slate-500">{ago(c.last_payment) === "—" ? "never paid" : `paid ${ago(c.last_payment)}`}</span></span>
              </div>
              {c.credit_limit > 0 && <div className="mt-2 flex items-center gap-2"><div className="h-1.5 flex-1 rounded-full bg-slate-100"><div className="h-full rounded-full" style={{ width: `${util}%`, background: util >= 80 ? "#e34948" : "#2a78d6" }} /></div>
                <span className="text-xs tabular-nums text-slate-600">{Math.round(util)}% of {pkr(c.credit_limit)}</span></div>}
              <div className="mt-2 flex items-center justify-between text-xs">
                <span className={c.risk_score >= 60 ? "font-semibold text-red-600" : "text-slate-500"}>Risk · <Ur>خطرہ</Ur> {Math.round(c.risk_score)}</span>
                <button className="btn-secondary min-h-9 !px-3 !py-1 text-xs" onClick={(e) => { e.stopPropagation(); setBill(c.id); }}>📄 Bill · <Ur>بل</Ur></button>
              </div>
            </li>
          );
        })}
        {!rows.length && <li><Empty>No credit customers · کوئی کھاتہ نہیں</Empty></li>}
      </ul>
      <div className="card hidden overflow-x-auto sm:block">
        <table className="w-full">
          <thead><tr><th className="th">Customer · <Ur>گاہک</Ur></th><th className="th text-right">Balance · <Ur>بقایا</Ur></th><th className="th text-right">Limit · <Ur>حد</Ur></th><th className="th">Used · <Ur>استعمال</Ur></th><th className="th">Risk · <Ur>خطرہ</Ur></th><th className="th">Last payment · <Ur>آخری ادائیگی</Ur></th><th className="th" /></tr></thead>
          <tbody>
            {rows.map((c) => {
              const util = c.credit_limit ? Math.min(100, (c.balance / c.credit_limit) * 100) : 0;
              return (
                <tr key={c.id} className="cursor-pointer hover:bg-slate-50" onClick={() => nav(`/customers/${c.id}`)}>
                  <td className="td"><div className="font-medium">{TYPE_ICON[c.type] && c.type !== "retail" ? `${TYPE_ICON[c.type]} ` : ""}{c.name}</div><div className="text-xs text-slate-500">{phone(c.phone)} · {c.type}{c.khata_blocked ? <span className="ml-1 rounded bg-red-100 px-1.5 font-semibold text-red-700">On hold</span> : null}</div></td>
                  <td className="td text-right font-medium tabular-nums">{pkr(c.balance)}</td>
                  <td className="td text-right tabular-nums text-slate-600">{pkr(c.credit_limit)}</td>
                  <td className="td"><div className="h-1.5 w-28 rounded-full bg-slate-100"><div className="h-full rounded-full" style={{ width: `${util}%`, background: util >= 80 ? "#e34948" : "#2a78d6" }} /></div><span className="text-xs text-slate-500">{Math.round(util)}%</span></td>
                  <td className={`td text-sm tabular-nums ${c.risk_score >= 60 ? "font-semibold text-red-600" : ""}`}>{Math.round(c.risk_score)}</td>
                  <td className="td text-xs text-slate-500">{ago(c.last_payment)}</td>
                  <td className="td"><button className="btn-secondary !px-2 !py-1 text-xs" onClick={(e) => { e.stopPropagation(); setBill(c.id); }}>📄 Bill · <Ur>بل</Ur></button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!rows.length && <Empty>No credit customers · کوئی کھاتہ نہیں</Empty>}
      </div>
      {bill && <KhataStatement customerId={bill} onClose={() => setBill(null)} />}
    </div>
  );
}

/** Khata aging: how much of the outstanding is 0-30 / 31-60 / 61-90 / 90+ days old (oldest charges cleared first). */
function AgingCard({ onOpen }: { onOpen: (id: number) => void }) {
  const { data } = useApi<any>("/khata/aging");
  const [open, setOpen] = useState(false);
  if (!data || !data.total) return null;
  const tone = ["text-slate-700", "text-amber-600", "text-orange-600", "text-red-600"];
  const overdue = (data.list as any[]).filter((c) => c.overdue > 0);
  return (
    <div className="card mb-4 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="flex-1 font-semibold">Aging — kitna purana udhaar · <Ur>ادھار کی عمر</Ur></h2>
        <span className="text-sm text-slate-500">Overdue (31+ din): <b className="text-red-600">{pkr(data.overdue)}</b></span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
        {data.buckets.map((b: any, i: number) => (
          <div key={b.bucket} className="rounded-xl bg-slate-50 p-3 ring-1 ring-slate-200">
            <div className="text-xs text-slate-500">{b.bucket}</div>
            <div className={`text-lg font-bold tabular-nums ${tone[i]}`}>{pkr(b.amount)}</div>
          </div>
        ))}
      </div>
      {overdue.length > 0 && <button className="mt-3 text-sm font-medium text-brand-700 underline" onClick={() => setOpen((x) => !x)}>
        {open ? "Chhupayein" : `Overdue customers dekhein (${overdue.length}) ·`} <Ur>دیر سے ادائیگی</Ur>
      </button>}
      {open && (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr><th className="th">Customer</th><th className="th text-right">31-60</th><th className="th text-right">61-90</th><th className="th text-right">90+</th><th className="th text-right">Total</th></tr></thead>
            <tbody>{overdue.map((c) => (
              <tr key={c.id} className="cursor-pointer hover:bg-slate-50" onClick={() => onOpen(c.id)}>
                <td className="td">{TYPE_ICON[c.type] && c.type !== "retail" ? `${TYPE_ICON[c.type]} ` : ""}{c.name}</td>
                <td className="td text-right tabular-nums text-amber-600">{c.buckets[1] ? pkr(c.buckets[1]) : "—"}</td>
                <td className="td text-right tabular-nums text-orange-600">{c.buckets[2] ? pkr(c.buckets[2]) : "—"}</td>
                <td className="td text-right tabular-nums font-semibold text-red-600">{c.buckets[3] ? pkr(c.buckets[3]) : "—"}</td>
                <td className="td text-right font-semibold tabular-nums">{pkr(c.balance)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Government bills submitted and not yet paid, oldest first. */
function OpenGovtBills({ onOpen }: { onOpen: (id: number) => void }) {
  const { data } = useApi<any[]>("/govt-bills");
  const open = (data ?? []).filter((b) => b.status === "submitted" || b.status === "partly").sort((a, b) => (b.days_waiting ?? 0) - (a.days_waiting ?? 0));
  if (!open.length) return null;
  return (
    <div className="card mb-4 p-4">
      <h2 className="mb-2 font-semibold">Government bills waiting for payment · <Ur>سرکاری بل باقی</Ur> · {pkr(open.reduce((a, b) => a + b.outstanding, 0))}</h2>
      {open.map((b) => (
        <button key={b.id} onClick={() => onOpen(b.customer_id)} className="flex min-h-9 w-full items-center justify-between gap-2 border-b border-slate-100 py-1.5 text-left text-sm hover:bg-slate-50">
          <span className="min-w-0">{b.customer_name} · {b.bill_no}{b.po_number ? ` · PO ${b.po_number}` : ""}</span>
          <span className={`shrink-0 text-right tabular-nums ${b.days_waiting > 45 ? "font-semibold text-red-600" : ""}`}>{pkr(b.outstanding)} · {b.days_waiting} days</span>
        </button>
      ))}
    </div>
  );
}
