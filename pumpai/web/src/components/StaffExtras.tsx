import { useState } from "react";
import { GraduationCap, HandCoins, FileText, MessageSquareHeart, Send, RefreshCw } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Empty, Field, Loading, PageHeader, useAction } from "./ui";
import { num, pkr } from "../lib/format";
import { ProofPhotos } from "./Capture";

const STATUS: Record<string, { label: string; cls: string }> = {
  done: { label: "OK", cls: "bg-emerald-100 text-emerald-800" }, due_soon: { label: "Due soon", cls: "bg-amber-100 text-amber-800" },
  overdue: { label: "Overdue", cls: "bg-red-100 text-red-700" }, missing: { label: "Needed", cls: "bg-red-50 text-red-600 ring-1 ring-red-200" }, none: { label: "—", cls: "text-slate-400" },
};

/** Loans with monthly instalments (inside the staff account). */
export function LoansBox({ userId, loans, onChanged }: { userId: number; loans: any[]; onChanged: () => void }) {
  const [f, setF] = useState({ amount: "", instalment: "", note: "" });
  const [photos, setPhotos] = useState<number[]>([]);
  const [open, setOpen] = useState(false);
  const { busy, run } = useAction();
  const active = loans.filter((l) => l.status !== "closed");
  return (
    <div className="mt-4 rounded-xl border border-slate-200 p-3">
      <div className="flex items-center gap-2"><HandCoins size={16} className="text-brand-600" /><h3 className="flex-1 font-semibold">Loans</h3>
        <button className="btn-secondary px-2 py-1 text-xs" onClick={() => setOpen(!open)}>Give a loan</button></div>
      {active.map((l) => (
        <div key={l.id} className="mt-2 text-sm">
          <div className="flex justify-between"><span>#{l.id} {l.note ?? ""} · {pkr(l.instalment)}/month</span><span className="tabular-nums">{pkr(l.remaining)} left of {pkr(l.amount)}</span></div>
          <div className="mt-1 h-1.5 rounded bg-slate-100"><div className="h-full rounded bg-emerald-500" style={{ width: `${(l.paid / l.amount) * 100}%` }} /></div>
          <div className="text-xs text-slate-500">{l.months_left} month{l.months_left === 1 ? "" : "s"} to go{l.status === "paused" ? " · paused" : ""}
            <button className="ml-2 underline" onClick={() => run(() => api(`/loans/${l.id}`, { method: "PATCH", body: { status: l.status === "paused" ? "active" : "paused" } }), l.status === "paused" ? "Instalments start again" : "Paused for this month").then(onChanged)}>{l.status === "paused" ? "Resume" : "Pause"}</button></div>
        </div>
      ))}
      {!active.length && !open && <p className="mt-1 text-sm text-slate-500">No loan. The monthly instalment is cut from the salary by itself.</p>}
      {open && <form className="mt-2 grid gap-2 sm:grid-cols-4" onSubmit={async (e) => {
        e.preventDefault();
        if (await run(() => api(`/staff/${userId}/loans`, { body: { amount: Number(f.amount), instalment: Number(f.instalment), note: f.note || null, photo_ids: photos } }), "Loan given — paid from the office cash")) { setOpen(false); setF({ amount: "", instalment: "", note: "" }); setPhotos([]); onChanged(); }
      }}>
        <input className="input" type="number" min={1} required placeholder="Loan Rs" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} />
        <input className="input" type="number" min={1} required placeholder="Per month Rs" value={f.instalment} onChange={(e) => setF({ ...f, instalment: e.target.value })} />
        <input className="input" placeholder="Reason" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} />
        <div className="sm:col-span-4"><ProofPhotos value={photos} onChange={setPhotos} hint="signed loan paper / CNIC copy" /></div>
        <button className="btn-primary" disabled={busy}>Give loan{Number(f.amount) && Number(f.instalment) ? ` (${Math.ceil(Number(f.amount) / Number(f.instalment))} months)` : ""}</button>
      </form>}
    </div>
  );
}

/** Salary slips (PDF links). */
export function SlipsList({ slips }: { slips: { id: number; month: string; net: number; url: string }[] }) {
  if (!slips?.length) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-2">
      {slips.map((s) => <a key={s.id} href={s.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-lg bg-slate-100 px-2 py-1 text-xs hover:bg-slate-200"><FileText size={13} /> Slip {s.month} · {pkr(s.net)}</a>)}
    </div>
  );
}

/** Who has done which training, and when it is due again. */
export function TrainingTab() {
  const { data, reload } = useApi<any>("/training");
  const { busy, run } = useAction();
  const [f, setF] = useState({ topic: "", custom: "", done_on: new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10), trainer: "", users: [] as number[] });
  if (!data) return <Loading />;
  const topic = f.topic === "__other" ? f.custom : f.topic;
  return (
    <div className="space-y-5">
      <PageHeader title="Training" subtitle="Fire safety, emergency shutdown, POS and more — with the date each one is due again" />
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[760px] text-sm">
          <thead className="bg-slate-50 text-left text-xs text-slate-500"><tr><th className="px-3 py-2">Staff</th>{data.topics.map((t: any) => <th key={t.topic} className="px-2 py-2">{t.topic}{t.required && <span className="text-red-500"> *</span>}<div className="font-normal">every {t.months} months</div></th>)}</tr></thead>
          <tbody className="divide-y divide-slate-100">
            {data.staff.map((u: any) => (
              <tr key={u.id}>
                <td className="px-3 py-2 font-medium">{u.name}<div className="text-xs capitalize text-slate-500">{u.role}</div></td>
                {data.topics.map((t: any) => { const r = u.records[t.topic]; const st = STATUS[r.status]; return (
                  <td key={t.topic} className="px-2 py-2"><span className={`inline-block rounded px-1.5 py-0.5 text-xs font-semibold ${st.cls}`}>{st.label}</span>
                    {r.done_on && <div className="text-[11px] text-slate-500">done {r.done_on}{r.next_due ? <><br />next {r.next_due}</> : null}</div>}</td>); })}
              </tr>
            ))}
          </tbody>
        </table>
        {!data.staff.length && <Empty>No staff</Empty>}
      </div>
      <form className="card grid gap-3 p-4 sm:grid-cols-4" onSubmit={async (e) => {
        e.preventDefault();
        if (await run(() => api("/training", { body: { user_ids: f.users, topic, done_on: f.done_on, trainer: f.trainer || null } }), (r: any) => `Saved for ${r.added} staff${r.next_due ? ` — due again ${r.next_due}` : ""}`)) { setF({ ...f, users: [], trainer: "" }); reload(); }
      }}>
        <h3 className="flex items-center gap-2 font-semibold sm:col-span-4"><GraduationCap size={17} /> Record a training</h3>
        <Field label="Training"><select className="input" required value={f.topic} onChange={(e) => setF({ ...f, topic: e.target.value })}><option value="">Choose…</option>{data.topics.map((t: any) => <option key={t.topic}>{t.topic}</option>)}<option value="__other">Other…</option></select></Field>
        {f.topic === "__other" && <Field label="Name of training"><input className="input" required value={f.custom} onChange={(e) => setF({ ...f, custom: e.target.value })} /></Field>}
        <Field label="Date"><input className="input" type="date" value={f.done_on} onChange={(e) => setF({ ...f, done_on: e.target.value })} /></Field>
        <Field label="Given by"><input className="input" placeholder="e.g. Rescue 1122" value={f.trainer} onChange={(e) => setF({ ...f, trainer: e.target.value })} /></Field>
        <div className="sm:col-span-4"><div className="mb-1 text-sm text-slate-600">Who attended</div>
          <div className="flex flex-wrap gap-2">{data.staff.map((u: any) => (
            <label key={u.id} className={`cursor-pointer rounded-full px-3 py-1 text-sm ring-1 ${f.users.includes(u.id) ? "bg-brand-600 text-white ring-brand-600" : "ring-slate-300"}`}>
              <input type="checkbox" className="hidden" checked={f.users.includes(u.id)} onChange={(e) => setF({ ...f, users: e.target.checked ? [...f.users, u.id] : f.users.filter((x) => x !== u.id) })} />{u.name}</label>))}</div></div>
        <div className="sm:col-span-4"><button className="btn-primary" disabled={busy || !topic || !f.users.length}>Save training</button></div>
      </form>
    </div>
  );
}

/** Preview the daily coaching message for a salesman and send it now. */
export function CoachingTab() {
  const people = useApi<any[]>("/staff");
  const salesmen = (people.data ?? []).filter((u) => u.role === "salesman" && u.active);
  const [uid, setUid] = useState<number | null>(null);
  const [day, setDay] = useState(new Date(Date.now() + 5 * 3600_000 - 86_400_000).toISOString().slice(0, 10));
  const who = uid ?? salesmen[0]?.id ?? null;
  const { data, reload } = useApi<any>(who ? `/coaching/${who}?day=${day}` : null);
  const [text, setText] = useState<string | null>(null);
  const { busy, run } = useAction();
  if (!people.data) return <Loading />;
  const s = data?.stats;
  const msg = text ?? data?.message.text ?? "";
  return (
    <div className="space-y-5">
      <PageHeader title="Daily coaching" subtitle="Every morning at 8:30 each salesman who worked yesterday gets a short message with what went well and what to improve" />
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Salesman"><select className="input" value={who ?? ""} onChange={(e) => { setUid(Number(e.target.value)); setText(null); }}>{salesmen.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></Field>
        <Field label="Day"><input type="date" className="input" value={day} onChange={(e) => { setDay(e.target.value); setText(null); }} /></Field>
      </div>
      {!data ? (who ? <Loading /> : <Empty>No salesmen</Empty>) : (
        <div className="grid gap-5 lg:grid-cols-2">
          <div className="card p-4 text-sm">
            <h3 className="mb-2 font-semibold">{day}</h3>
            {[["Shifts closed", s.shifts], ["Litres sold", num(s.litres)], ["Sales", pkr(s.amount)], ["Litres not entered on POS", `${num(s.not_entered_l)} L (${s.not_entered_pct}%)`],
              ["Cash short / over", pkr(s.cash_variance)], ["Shop sales", pkr(s.shop_sales)], ["Sales undone", s.undone], ["Late (minutes)", s.late_minutes],
              ["Customer rating", s.rating ? `${s.rating}★ (${s.ratings})` : "—"], ["Failed checks", s.failed_checks]].map(([k, v]) => (
              <div key={k as string} className="flex justify-between border-b border-slate-100 py-1.5"><span className="text-slate-600">{k}</span><span className="tabular-nums font-medium">{v}</span></div>))}
          </div>
          <div className="card flex flex-col gap-2 p-4">
            <h3 className="flex items-center gap-2 font-semibold"><MessageSquareHeart size={17} /> Message <Badge>{data.message.engine === "claude" ? "AI" : "auto"}</Badge></h3>
            <textarea className="input min-h-[220px] flex-1 text-sm" value={msg} onChange={(e) => setText(e.target.value)} />
            <div className="flex gap-2">
              <button className="btn-secondary" onClick={() => { setText(null); reload(); }}><RefreshCw size={14} /> Again</button>
              <button className="btn-primary" disabled={busy || msg.length < 5} onClick={() => run(() => api(`/coaching/${who}/send`, { body: { text: msg } }), "Sent to the salesman (app + WhatsApp)")}><Send size={14} /> Send now</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
