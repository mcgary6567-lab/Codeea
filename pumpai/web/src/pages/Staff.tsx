import { useState } from "react";
import { Wallet, Plus, Minus, Banknote } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Empty, Field, Loading, Modal, PageHeader, Stat, useAction } from "../components/ui";
import { dt, pkr } from "../lib/format";
import { photoUrl } from "../components/Capture";
import { DAY_STATUS, LeaveForm } from "./MyAccount";
import { useAuth } from "../App";
import { LoansBox, SlipsList, TrainingTab, CoachingTab } from "../components/StaffExtras";

const TYPE: Record<string, { label: string; tone: string; sign: string }> = {
  advance: { label: "Advance", tone: "amber", sign: "+" }, shortage: { label: "Cash short", tone: "red", sign: "+" },
  repayment: { label: "Paid back", tone: "green", sign: "−" }, deduction: { label: "Cut from salary", tone: "green", sign: "−" },
  salary: { label: "Salary paid", tone: "blue", sign: "" }, bonus: { label: "Bonus", tone: "violet", sign: "" },
};

/** Staff khata: advances, cash shortages from shifts, salary — no salary register on paper. */
export default function Staff() {
  const [tab, setTab] = useState<"accounts" | "attendance" | "training" | "coaching">("accounts");
  const TABS = { accounts: "Accounts & salary", attendance: "Attendance & leave", training: "Training", coaching: "Daily coaching" } as const;
  return (
    <div className="space-y-5">
      <div className="flex gap-2 overflow-x-auto">
        {(Object.keys(TABS) as (keyof typeof TABS)[]).map((t) => <button key={t} onClick={() => setTab(t)} className={`whitespace-nowrap rounded-lg px-4 py-2 text-sm font-medium ${tab === t ? "bg-slate-900 text-white" : "bg-white ring-1 ring-slate-200"}`}>{TABS[t]}</button>)}
      </div>
      {tab === "accounts" ? <Accounts /> : tab === "attendance" ? <Attendance /> : tab === "training" ? <TrainingTab /> : <CoachingTab />}
    </div>
  );
}

/** Whole team's attendance this month, who is in today, and leave requests. */
function Attendance() {
  const { data, reload } = useApi<any>("/attendance");
  const { run } = useAction();
  const [leaveFor, setLeaveFor] = useState<number | null>(null);
  if (!data) return <Loading />;
  const pending = data.leaves.filter((l: any) => l.status === "pending");
  return (
    <div className="space-y-5">
      <PageHeader title="Attendance & leave" subtitle={`Check-ins (selfie + location), lateness, weekly off and leave — ${data.month}`} />
      {pending.length > 0 && <div className="card p-4"><h2 className="mb-2 font-semibold">Leave requests</h2>
        {pending.map((l: any) => (
          <div key={l.id} className="flex flex-wrap items-center gap-2 border-b border-slate-100 py-2 text-sm">
            <span className="flex-1"><b>{l.name}</b> · {l.from_day}{l.to_day !== l.from_day ? ` – ${l.to_day}` : ""} · {l.type}{l.reason ? ` — ${l.reason}` : ""}</span>
            <button className="btn-primary !py-1" onClick={() => run(() => api(`/leaves/${l.id}/approve`, { body: {} }), "Approved").then(reload)}>Approve</button>
            <button className="btn-secondary !py-1" onClick={() => run(() => api(`/leaves/${l.id}/reject`, { body: {} }), "Rejected").then(reload)}>Reject</button>
          </div>
        ))}</div>}
      <div className="card p-4">
        <h2 className="mb-2 font-semibold">In today ({data.present_today.length})</h2>
        <div className="flex flex-wrap gap-2">{data.present_today.map((a: any) => (
          <span key={a.id} className="badge gap-1 bg-slate-100 text-slate-700">{a.in_photo_id ? <a href={photoUrl(a.in_photo_id)} target="_blank" rel="noreferrer">📷</a> : null}{a.name} · {new Date(a.check_in).toLocaleTimeString("en-PK", { hour: "2-digit", minute: "2-digit" })}
            {a.in_lat != null && <a href={`https://maps.google.com/?q=${a.in_lat},${a.in_lng}`} target="_blank" rel="noreferrer" title="Check-in location">📍</a>}
            {a.late_minutes > 15 && <span className="text-amber-700"> · {a.late_minutes}m late</span>}{a.away_m > 300 && <span className="text-red-600"> · {a.away_m} m away</span>}
            {a.check_out && <> · out{a.out_photo_id ? <a href={photoUrl(a.out_photo_id)} target="_blank" rel="noreferrer"> 📷</a> : null}{a.out_lat != null && <a href={`https://maps.google.com/?q=${a.out_lat},${a.out_lng}`} target="_blank" rel="noreferrer" title="Check-out location">📍</a>}</>}</span>
        ))}{!data.present_today.length && <span className="text-sm text-slate-500">Nobody yet</span>}</div>
      </div>
      <div className="card overflow-x-auto">
        <table className="w-full">
          <thead><tr><th className="th">Name</th><th className="th">Duty</th><th className="th">This month</th><th className="th text-right">Present</th><th className="th text-right">Late</th><th className="th text-right">Absent</th><th className="th text-right">Leave</th><th className="th text-right">Salary cut</th><th className="th" /></tr></thead>
          <tbody>{data.staff.map((s: any) => (
            <tr key={s.user.id}>
              <td className="td font-medium">{s.user.name}</td>
              <td className="td text-xs text-slate-600">{s.user.duty_start ?? <span className="text-slate-400">not set</span>}{s.user.weekly_off != null && ` · off ${WEEK[s.user.weekly_off]}`}</td>
              <td className="td"><div className="flex flex-wrap gap-0.5">{s.days.map((x: any) => <span key={x.day} title={`${x.day}: ${DAY_STATUS[x.status]?.label}`} className={`h-3 w-3 rounded-sm ${DAY_STATUS[x.status]?.cls}`} />)}</div></td>
              <td className="td text-right tabular-nums">{s.present}</td><td className="td text-right tabular-nums">{s.late}</td>
              <td className={`td text-right tabular-nums ${s.absent ? "font-semibold text-red-600" : ""}`}>{s.absent}</td><td className="td text-right tabular-nums">{s.paid_leave + s.unpaid_leave}</td>
              <td className="td text-right tabular-nums">{s.salary_cut ? pkr(s.salary_cut) : "—"}</td>
              <td className="td text-right"><button className="btn-secondary !px-2 !py-1 text-xs" onClick={() => setLeaveFor(s.user.id)}>Record leave</button></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      {leaveFor && <LeaveForm userId={leaveFor} onClose={() => setLeaveFor(null)} onDone={() => { setLeaveFor(null); reload(); }} />}
    </div>
  );
}

const WEEK = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function Accounts() {
  const { data, reload } = useApi<any[]>("/staff");
  const [open, setOpen] = useState<number | null>(null);
  if (!data) return <Loading />;
  const owed = data.reduce((a, u) => a + Math.max(0, u.balance), 0);
  return (
    <div className="space-y-5">
      <PageHeader title="Staff accounts" subtitle="Advances, cash shortages from shifts and salary — every entry kept automatically" />
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="Staff owe (advances + short)" value={pkr(owed)} tone="amber" icon={<Wallet size={16} />} />
        <Stat label="Short this month" value={pkr(data.reduce((a, u) => a + u.shortages_this_month, 0))} tone="red" />
        <Stat label="Salary paid this month" value={`${data.filter((u) => u.salary_paid_this_month).length} / ${data.filter((u) => u.salary && u.active).length}`} tone="blue" hint="staff with a salary set" />
      </div>
      <div className="card overflow-x-auto">
        <table className="w-full">
          <thead><tr><th className="th">Name</th><th className="th">Role</th><th className="th text-right">Salary</th><th className="th text-right">Owes</th><th className="th text-right">Short this month</th><th className="th">This month</th></tr></thead>
          <tbody>{data.map((u) => (
            <tr key={u.id} className={`cursor-pointer hover:bg-slate-50 ${u.active ? "" : "opacity-50"}`} onClick={() => setOpen(u.id)}>
              <td className="td font-medium">{u.name}<div className="text-xs text-slate-500">{u.station_name ?? ""}</div></td>
              <td className="td text-sm capitalize">{u.role}</td>
              <td className="td text-right tabular-nums">{u.salary ? pkr(u.salary) : <span className="text-slate-400">not set</span>}</td>
              <td className={`td text-right font-semibold tabular-nums ${u.balance > 0 ? "text-amber-700" : ""}`}>{pkr(u.balance)}</td>
              <td className={`td text-right tabular-nums ${u.shortages_this_month ? "text-red-600" : "text-slate-400"}`}>{pkr(u.shortages_this_month)}</td>
              <td className="td">{u.salary_paid_this_month ? <Badge tone="green">Salary paid</Badge> : u.salary ? <Badge tone="amber">Salary due</Badge> : null}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      {open && <StaffDetail id={open} onClose={() => { setOpen(null); reload(); }} />}
    </div>
  );
}

function StaffDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const { data, reload } = useApi<any>(`/staff/${id}`);
  const slips = useApi<any[]>(`/staff/${id}/slips`);
  const [slipUrl, setSlipUrl] = useState<string | null>(null);
  const { can } = useAuth();
  const { busy, run } = useAction();
  const [form, setForm] = useState<null | "advance" | "repayment" | "salary" | "set-salary">(null);
  const [f, setF] = useState({ amount: "", note: "", deduct: "", bonus: "", salary: "", cut: "" });
  if (!data) return <Modal open onClose={onClose} title="Staff account"><Loading /></Modal>;
  const u = data.user;
  const done = () => { setForm(null); setF({ amount: "", note: "", deduct: "", bonus: "", salary: "", cut: "" }); reload(); };
  const cut = f.cut === "" ? data.attendance?.salary_cut ?? 0 : Number(f.cut) || 0;
  const gross = (u.salary ?? 0) - cut + (Number(f.bonus) || 0) + (data.commission ?? 0);
  const loan = Math.max(0, Math.min(data.loan_due ?? 0, gross - (Number(f.deduct) || 0)));
  const net = gross - (Number(f.deduct) || 0) - loan;
  return (
    <Modal open onClose={onClose} title={`${u.name} — staff account`} wide>
      <div className="flex flex-wrap items-center gap-3">
        <div className="rounded-xl bg-amber-50 px-4 py-3"><div className="text-xs text-amber-800">Owes the business</div><div className="text-2xl font-bold tabular-nums">{pkr(data.balance)}</div></div>
        <div className="rounded-xl bg-slate-50 px-4 py-3"><div className="text-xs text-slate-600">Monthly salary</div><div className="text-2xl font-bold tabular-nums">{u.salary ? pkr(u.salary) : "—"}</div></div>
        <div className="ml-auto flex flex-wrap gap-2">
          <button className="btn-secondary" onClick={() => setForm("advance")}><Plus size={15} /> Advance</button>
          <button className="btn-secondary" disabled={data.balance <= 0} onClick={() => setForm("repayment")}><Minus size={15} /> Paid back</button>
          {can("users.manage") && <button className="btn-secondary" onClick={() => { setF({ ...f, salary: String(u.salary ?? "") }); setForm("set-salary"); }}>Set salary</button>}
          <button className="btn-primary" disabled={!u.salary} onClick={() => { setF({ ...f, deduct: String(Math.max(0, Math.min(data.balance - (data.loans_left ?? 0), u.salary ?? 0)) || "") }); setForm("salary"); }}><Banknote size={15} /> Pay salary</button>
        </div>
      </div>

      {form && (
        <form className="mt-4 space-y-3 rounded-xl border border-slate-200 p-3" onSubmit={async (e) => {
          e.preventDefault();
          const call = form === "salary" ? api(`/staff/${id}/pay-salary`, { body: { deduct: Number(f.deduct) || 0, bonus: Number(f.bonus) || 0, absence_cut: cut } })
            : form === "set-salary" ? api(`/staff/${id}`, { method: "PATCH", body: { salary: Number(f.salary) || null } })
            : api(`/staff/${id}/entry`, { body: { type: form, amount: Number(f.amount), note: f.note || null } });
          const r: any = await run(() => call, form === "salary" ? `Salary paid: ${pkr(net)} — slip sent on WhatsApp` : "Saved");
          if (r) { if (r.slip_url) { setSlipUrl(r.slip_url); slips.reload(); } done(); }
        }}>
          {form === "set-salary" && <Field label="Monthly salary (Rs)"><input className="input text-lg" type="number" min={0} required value={f.salary} onChange={(e) => setF({ ...f, salary: e.target.value })} /></Field>}
          {(form === "advance" || form === "repayment") && <div className="grid gap-3 sm:grid-cols-2">
            <Field label={form === "advance" ? "Advance given (Rs)" : "Amount paid back (Rs)"}><input className="input text-lg" type="number" min={1} max={form === "repayment" ? data.balance : undefined} required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
            <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} placeholder="e.g. for family need" /></Field>
          </div>}
          {form === "salary" && <>
            <div className="grid gap-3 sm:grid-cols-4">
              <Field label="Salary"><input className="input" disabled value={pkr(u.salary)} /></Field>
              <Field label={`Absence cut (${data.attendance?.unpaid_days ?? 0} unpaid days)`}><input className="input" type="number" min={0} value={f.cut === "" ? String(data.attendance?.salary_cut ?? 0) : f.cut} onChange={(e) => setF({ ...f, cut: e.target.value })} /></Field>
              <Field label="Bonus (optional)"><input className="input" type="number" min={0} value={f.bonus} onChange={(e) => setF({ ...f, bonus: e.target.value })} /></Field>
              <Field label={`Cut advance / short (owes ${pkr(Math.max(0, data.balance - (data.loans_left ?? 0)))})`}><input className="input" type="number" min={0} max={Math.max(0, Math.min(data.balance - (data.loans_left ?? 0), u.salary + (Number(f.bonus) || 0)))} value={f.deduct} onChange={(e) => setF({ ...f, deduct: e.target.value })} /></Field>
            </div>
            {data.commission > 0 && <div className="text-sm text-slate-600">+ Commission this month (shop / fuel): <b>{pkr(data.commission)}</b> — added by itself</div>}
            {loan > 0 && <div className="text-sm text-slate-600">− Loan instalment: <b>{pkr(loan)}</b> — cut by itself</div>}
            <div className="rounded-lg bg-emerald-50 p-3 text-lg">Hand over in cash: <b className="tabular-nums">{pkr(net)}</b></div>
          </>}
          <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={() => setForm(null)}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
        </form>
      )}
      {slipUrl && <a href={slipUrl} target="_blank" rel="noreferrer" className="mt-3 block rounded-lg bg-emerald-50 p-3 text-sm font-semibold text-emerald-800 ring-1 ring-emerald-200">📄 Open the salary slip (PDF) — also sent on WhatsApp</a>}
      <SlipsList slips={slips.data ?? []} />
      <LoansBox userId={u.id} loans={data.loans ?? []} onChanged={reload} />
      <DutyForm u={u} att={data.attendance} onSaved={reload} />
      <LedgerList lines={data.lines} />
    </Modal>
  );
}

function DutyForm({ u, att, onSaved }: { u: any; att: any; onSaved: () => void }) {
  const [start, setStart] = useState(att?.user.duty_start ?? "");
  const [off, setOff] = useState(att?.user.weekly_off == null ? "" : String(att.user.weekly_off));
  const { run } = useAction();
  return (
    <form className="mt-4 flex flex-wrap items-end gap-2 rounded-xl bg-slate-50 p-3" onSubmit={(e) => { e.preventDefault(); run(() => api(`/staff/${u.id}/duty`, { method: "PATCH", body: { duty_start: start || null, weekly_off: off === "" ? null : Number(off) } }), "Duty saved").then(onSaved); }}>
      <Field label="Duty starts"><input className="input" type="time" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
      <Field label="Weekly off"><select className="input" value={off} onChange={(e) => setOff(e.target.value)}><option value="">None</option>{WEEK.map((w, i) => <option key={w} value={i}>{w}</option>)}</select></Field>
      <button className="btn-secondary">Save duty</button>
      {att?.tracked && <span className="ml-auto text-sm text-slate-600">This month: present {att.present}, late {att.late}, absent {att.absent}</span>}
    </form>
  );
}

export function LedgerList({ lines }: { lines: any[] }) {
  if (!lines.length) return <Empty>No entries yet.</Empty>;
  return (
    <div className="mt-4 max-h-[50vh] overflow-auto rounded-lg border border-slate-200">
      <table className="w-full">
        <thead className="sticky top-0"><tr><th className="th">Date</th><th className="th">Entry</th><th className="th">Note</th><th className="th text-right">Amount</th><th className="th text-right">Owes after</th></tr></thead>
        <tbody>{lines.map((l) => (
          <tr key={l.id}>
            <td className="td whitespace-nowrap text-xs">{dt(l.created_at)}</td>
            <td className="td"><Badge tone={TYPE[l.type]?.tone}>{TYPE[l.type]?.label ?? l.type}</Badge></td>
            <td className="td text-sm text-slate-600">{l.note}</td>
            <td className="td text-right tabular-nums">{TYPE[l.type]?.sign}{pkr(l.amount)}</td>
            <td className="td text-right font-medium tabular-nums">{pkr(l.balance)}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}
