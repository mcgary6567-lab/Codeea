import { useState } from "react";
import { Wallet, Plus, Minus, Banknote, Pencil } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Empty, Field, Loading, Modal, PageHeader, PhoneInput, Stat, useAction } from "../components/ui";
import { dt, pkr } from "../lib/format";
import { PhotoThumb, ProofPhotos, ProofThumbs } from "../components/Capture";
import { DAY_STATUS, LeaveForm } from "./MyAccount";
import { useAuth } from "../App";
import { useLookups } from "../lib/lookups";
import { Ur } from "../components/VoiceShell";
import { LoansBox, SlipsList, TrainingTab, CoachingTab } from "../components/StaffExtras";

const TYPE: Record<string, { label: string; tone: string; sign: string }> = {
  advance: { label: "Advance", tone: "amber", sign: "+" }, shortage: { label: "Cash short", tone: "red", sign: "+" },
  repayment: { label: "Paid back", tone: "green", sign: "−" }, deduction: { label: "Cut from salary", tone: "green", sign: "−" },
  salary: { label: "Salary paid", tone: "blue", sign: "" }, bonus: { label: "Bonus", tone: "violet", sign: "" },
};

/** Staff khata: advances, cash shortages from shifts, salary — no salary register on paper. */
export default function Staff() {
  const [tab, setTab] = useState<"accounts" | "attendance" | "calendar" | "training" | "coaching">("accounts");
  const TABS = { accounts: "Accounts & salary", attendance: "Attendance & leave", calendar: "Attendance calendar", training: "Training", coaching: "Daily coaching" } as const;
  return (
    <div className="space-y-5">
      <div className="flex gap-2 overflow-x-auto">
        {(Object.keys(TABS) as (keyof typeof TABS)[]).map((t) => <button key={t} onClick={() => setTab(t)} className={`whitespace-nowrap rounded-lg px-4 py-2 text-sm font-medium ${tab === t ? "bg-slate-900 text-white" : "bg-white ring-1 ring-slate-200"}`}>{TABS[t]}</button>)}
      </div>
      {tab === "accounts" ? <Accounts /> : tab === "attendance" ? <Attendance /> : tab === "calendar" ? <StaffCalendar /> : tab === "training" ? <TrainingTab /> : <CoachingTab />}
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
            <span className="min-w-0 flex-1 basis-full sm:basis-0"><b>{l.name}</b> · {l.from_day}{l.to_day !== l.from_day ? ` – ${l.to_day}` : ""} · {l.type}{l.reason ? ` — ${l.reason}` : ""}</span>
            <button className="btn-primary min-h-9 !py-1" onClick={() => run(() => api(`/leaves/${l.id}/approve`, { body: {} }), "Approved").then(reload)}>Approve</button>
            <button className="btn-secondary min-h-9 !py-1" onClick={() => run(() => api(`/leaves/${l.id}/reject`, { body: {} }), "Rejected").then(reload)}>Reject</button>
          </div>
        ))}</div>}
      <div className="card p-4">
        <h2 className="mb-2 font-semibold">In today ({data.present_today.length})</h2>
        <div className="flex flex-wrap gap-2">{data.present_today.map((a: any) => (
          <span key={a.id} className="badge gap-1 bg-slate-100 text-slate-700">{a.in_photo_id ? <PhotoThumb id={a.in_photo_id} size={6} /> : null}{a.name} · {new Date(a.check_in).toLocaleTimeString("en-PK", { hour: "2-digit", minute: "2-digit" })}
            {a.in_lat != null && <a href={`https://maps.google.com/?q=${a.in_lat},${a.in_lng}`} target="_blank" rel="noreferrer" title="Check-in location">📍</a>}
            {a.late_minutes > 15 && <span className="text-amber-700"> · {a.late_minutes}m late</span>}{a.away_m > 300 && <span className="text-red-600"> · {a.away_m} m away</span>}
            {a.check_out && <> · out{a.out_photo_id ? <PhotoThumb id={a.out_photo_id} size={6} /> : null}{a.out_lat != null && <a href={`https://maps.google.com/?q=${a.out_lat},${a.out_lng}`} target="_blank" rel="noreferrer" title="Check-out location">📍</a>}</>}</span>
        ))}{!data.present_today.length && <span className="text-sm text-slate-500">Nobody yet</span>}</div>
      </div>
      {/* phone: one card per person */}
      <ul className="card divide-y divide-slate-100 sm:hidden">
        {data.staff.map((s: any) => (
          <li key={s.user.id} className="px-4 py-3">
            <div className="flex items-start justify-between gap-2">
              <span className="min-w-0"><span className="block font-semibold">{s.user.name}</span><span className="text-xs text-slate-500">Duty {s.user.duty_start ?? "not set"}{s.user.weekly_off != null && ` · off ${WEEK[s.user.weekly_off]}`}</span></span>
              <span className="shrink-0 text-right"><span className="block font-semibold tabular-nums">{s.salary_cut ? pkr(s.salary_cut) : "—"}</span><span className="text-[11px] text-slate-500">Salary cut · <Ur>کٹوتی</Ur></span></span>
            </div>
            <div className="mt-1.5 flex flex-wrap gap-0.5">{s.days.map((x: any) => <span key={x.day} title={`${x.day}: ${DAY_STATUS[x.status]?.label}`} className={`h-3 w-3 rounded-sm ${DAY_STATUS[x.status]?.cls}`} />)}</div>
            <div className="mt-1 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
              <span>Present <b className="tabular-nums text-slate-700">{s.present}</b> · late <b className="tabular-nums text-slate-700">{s.late}</b> · absent <b className={`tabular-nums ${s.absent ? "text-red-600" : "text-slate-700"}`}>{s.absent}</b> · leave <b className="tabular-nums text-slate-700">{s.paid_leave + s.unpaid_leave}</b></span>
              <button className="btn-secondary min-h-9 !py-1 text-xs" onClick={() => setLeaveFor(s.user.id)}>Record leave · <Ur>چھٹی</Ur></button>
            </div>
          </li>
        ))}
      </ul>
      <div className="card hidden overflow-x-auto sm:block">
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
const WD = ["S", "M", "T", "W", "T", "F", "S"];

/** Monthly attendance calendar for hand-marked staff (night guard, cleaner, non-login staff): tap a day to mark present/absent. */
function StaffCalendar() {
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7));
  const [all, setAll] = useState(false);
  const { data, reload } = useApi<any>(`/attendance/calendar?month=${month}${all ? "&all=1" : ""}`);
  const { run, busy } = useAction();
  const shift = (n: number) => { const [y, m] = month.split("-").map(Number); const d = new Date(Date.UTC(y, m - 1 + n, 1)); setMonth(d.toISOString().slice(0, 7)); };
  const thisMonth = new Date().toISOString().slice(0, 7);
  const toggle = (uid: number, day: string, cur: string) => {
    if (cur === "future" || cur === "before" || busy) return;
    const present = !(cur === "present" || cur === "late");
    run(() => api("/attendance/mark", { body: { user_id: uid, day, present } }), present ? "Marked present" : "Marked absent").then(reload);
  };
  return (
    <div className="space-y-4">
      <PageHeader title="Attendance calendar" subtitle="Day-wise present/absent for hand-marked staff — tap any day to mark" />
      <div className="card flex flex-wrap items-center justify-between gap-3 p-3">
        <div className="flex items-center gap-2">
          <button className="btn-secondary min-h-10 !px-3" onClick={() => shift(-1)} aria-label="Previous month">‹</button>
          <input type="month" value={month} max={thisMonth} onChange={(e) => setMonth(e.target.value)} className="input !w-auto min-h-10" />
          <button className="btn-secondary min-h-10 !px-3 disabled:opacity-40" disabled={month >= thisMonth} onClick={() => shift(1)} aria-label="Next month">›</button>
        </div>
        <label className="flex items-center gap-2 text-sm text-slate-600"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} className="h-4 w-4" /> Show all staff</label>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
        {([["present", "Present"], ["absent", "Absent"], ["off", "Weekly off"], ["leave_paid", "Leave"], ["leave_unpaid", "Unpaid"], ["late", "Late"]] as const).map(([k, l]) =>
          <span key={k} className="flex items-center gap-1"><span className={`h-3 w-3 rounded-sm ${DAY_STATUS[k]?.cls}`} /> {l}</span>)}
      </div>
      {!data ? <Loading /> : !data.staff.length ? <Empty>{all ? "No staff to show. Add staff members in the Accounts tab." : "No non-login staff yet. Tick ‘Show all staff’, or add staff members in the Accounts tab."}</Empty>
        : <div className="card overflow-x-auto">
          <table className="min-w-full border-separate" style={{ borderSpacing: 0 }}>
            <thead>
              <tr>
                <th className="sticky left-0 z-10 bg-white px-3 py-2 text-left text-xs font-semibold text-slate-500">Staff</th>
                {data.days.map((d: string) => { const dow = new Date(`${d}T12:00:00+05:00`).getUTCDay(); return (
                  <th key={d} className={`px-0 pb-1 pt-2 text-center text-[10px] font-medium ${dow === 0 || dow === 5 ? "text-amber-600" : "text-slate-400"}`} style={{ minWidth: 26 }}>
                    <span className="block leading-none">{WD[dow]}</span><span className="block leading-tight">{Number(d.slice(8))}</span></th>); })}
                <th className="px-2 text-right text-xs font-semibold text-slate-500">P / A</th>
              </tr>
            </thead>
            <tbody>
              {data.staff.map((s: any) => (
                <tr key={s.id}>
                  <td className="sticky left-0 z-10 whitespace-nowrap bg-white px-3 py-1.5 pr-4">
                    <span className="block text-sm font-medium leading-tight">{s.name}</span>
                    <span className="block text-[11px] text-slate-500">{s.job_title || s.role}{s.weekly_off != null ? ` · off ${WEEK[s.weekly_off]}` : ""}</span>
                  </td>
                  {data.days.map((d: string) => { const st = s.marks[d]; const cell = DAY_STATUS[st]; const tap = st !== "future" && st !== "before"; return (
                    <td key={d} className="p-0 text-center">
                      <button type="button" disabled={!tap} onClick={() => toggle(s.id, d, st)} title={`${d}: ${cell?.label ?? st}`}
                        className={`m-px h-6 w-6 rounded-sm align-middle text-[10px] font-semibold text-white ${cell?.cls ?? "bg-slate-50"} ${tap ? "cursor-pointer hover:ring-2 hover:ring-slate-400" : "cursor-default"} ${st === "off" || st === "future" || st === "before" ? "!text-slate-400" : ""}`}>
                        {st === "present" || st === "late" ? "✓" : st === "absent" ? "" : ""}</button>
                    </td>); })}
                  <td className="whitespace-nowrap px-2 text-right text-xs tabular-nums"><b className="text-emerald-600">{s.present}</b> / <b className={s.absent ? "text-red-600" : "text-slate-400"}>{s.absent}</b></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>}
      <p className="text-xs text-slate-500">Tip: unmarked past days count as absent. Tap a day to switch present/absent. Use <b>Record leave</b> on the Attendance tab for leave days.</p>
    </div>
  );
}

function Accounts() {
  const { data, reload } = useApi<any[]>("/staff");
  const stations = useApi<any[]>("/stations");
  const { can } = useAuth();
  const [open, setOpen] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  if (!data) return <Loading />;
  const owed = data.reduce((a, u) => a + Math.max(0, u.balance), 0);
  return (
    <div className="space-y-5">
      <PageHeader title="Staff accounts" subtitle="Advances, cash shortages from shifts and salary — every entry kept automatically"
        actions={can("staff.manage") && <button className="btn-primary" onClick={() => setAdding(true)}><Plus size={16} /> Add staff member</button>} />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Stat label="Staff owe (advances + short)" value={pkr(owed)} tone="amber" icon={<Wallet size={16} />} />
        <Stat label="Short this month" value={pkr(data.reduce((a, u) => a + u.shortages_this_month, 0))} tone="red" />
        <Stat label="Salary paid this month" value={`${data.filter((u) => u.salary_paid_this_month).length} / ${data.filter((u) => u.salary && u.active).length}`} tone="blue" hint="staff with a salary set" />
      </div>
      {/* phone: one card per person */}
      <ul className="card divide-y divide-slate-100 sm:hidden">
        {data.map((u) => (
          <li key={u.id} className={`cursor-pointer px-4 py-3 active:bg-slate-50 ${u.active ? "" : "opacity-50"}`} onClick={() => setOpen(u.id)}>
            <div className="flex items-start justify-between gap-2">
              <span className="min-w-0"><span className="block font-semibold">{u.name}</span><span className="text-xs capitalize text-slate-500">{u.job_title || u.role}{u.station_name ? ` · ${u.station_name}` : ""}</span></span>
              <span className="shrink-0 text-right"><span className={`block font-semibold tabular-nums ${u.balance > 0 ? "text-amber-700" : ""}`}>{pkr(u.balance)}</span><span className="text-[11px] text-slate-500">Owes · <Ur>بقایا</Ur></span></span>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
              <span>Salary <span className="tabular-nums text-slate-700">{u.salary ? pkr(u.salary) : "not set"}</span></span>
              <span>Short this month <span className={`tabular-nums ${u.shortages_this_month ? "text-red-600" : "text-slate-700"}`}>{pkr(u.shortages_this_month)}</span></span>
              {u.salary_paid_this_month ? <Badge tone="green">Salary paid</Badge> : u.salary ? <Badge tone="amber">Salary due</Badge> : null}
            </div>
          </li>
        ))}
      </ul>
      <div className="card hidden overflow-x-auto sm:block">
        <table className="w-full">
          <thead><tr><th className="th">Name</th><th className="th">Role</th><th className="th text-right">Salary</th><th className="th text-right">Owes</th><th className="th text-right">Short this month</th><th className="th">This month</th></tr></thead>
          <tbody>{data.map((u) => (
            <tr key={u.id} className={`cursor-pointer hover:bg-slate-50 ${u.active ? "" : "opacity-50"}`} onClick={() => setOpen(u.id)}>
              <td className="td font-medium">{u.name}<div className="text-xs text-slate-500">{u.station_name ?? ""}</div></td>
              <td className="td text-sm capitalize">{u.job_title || u.role}</td>
              <td className="td text-right tabular-nums">{u.salary ? pkr(u.salary) : <span className="text-slate-400">not set</span>}</td>
              <td className={`td text-right font-semibold tabular-nums ${u.balance > 0 ? "text-amber-700" : ""}`}>{pkr(u.balance)}</td>
              <td className={`td text-right tabular-nums ${u.shortages_this_month ? "text-red-600" : "text-slate-400"}`}>{pkr(u.shortages_this_month)}</td>
              <td className="td">{u.salary_paid_this_month ? <Badge tone="green">Salary paid</Badge> : u.salary ? <Badge tone="amber">Salary due</Badge> : null}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      {open && <StaffDetail id={open} onClose={() => { setOpen(null); reload(); }} />}
      {adding && <AddStaffMember stations={stations.data ?? []} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); reload(); }} />}
    </div>
  );
}

/** Add an employee who does not log in to the app (guard, cleaner, electrician…) — they flow into payroll like anyone else. */
function AddStaffMember({ stations, onClose, onSaved }: { stations: any[]; onClose: () => void; onSaved: () => void }) {
  const jobs = useLookups("job_title");
  const [f, setF] = useState({ name: "", job_title: "Chowkidar (night)", custom: "", salary: "", phone: "", duty_start: "", weekly_off: "", station_id: "" });
  const { busy, run } = useAction();
  const title = f.job_title === "Other" ? f.custom : f.job_title;
  return (
    <Modal open onClose={onClose} title="Add staff member (no app login)">
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const body = { name: f.name, job_title: title || null, salary: Number(f.salary) || null, phone: f.phone || null, duty_start: f.duty_start || null, weekly_off: f.weekly_off === "" ? null : Number(f.weekly_off), station_id: f.station_id ? Number(f.station_id) : null };
        if (await run(() => api("/staff/members", { body }), "Staff member added")) onSaved();
      }}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name"><input className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Job / designation"><select className="input" value={f.job_title} onChange={(e) => setF({ ...f, job_title: e.target.value })}>{jobs.list.map((j) => <option key={j.key}>{j.label}</option>)}<option>Other</option></select></Field>
          {f.job_title === "Other" && <Field label="Write the job"><input className="input" value={f.custom} onChange={(e) => setF({ ...f, custom: e.target.value })} /></Field>}
          <Field label="Monthly salary (Rs)"><input className="input" type="number" min={0} value={f.salary} onChange={(e) => setF({ ...f, salary: e.target.value })} /></Field>
          <Field label="Phone"><PhoneInput value={f.phone} onChange={(v) => setF({ ...f, phone: v })} /></Field>
          <Field label="Duty time (optional)"><input className="input" type="time" value={f.duty_start} onChange={(e) => setF({ ...f, duty_start: e.target.value })} /></Field>
          <Field label="Weekly off (optional)"><select className="input" value={f.weekly_off} onChange={(e) => setF({ ...f, weekly_off: e.target.value })}><option value="">None</option>{WEEK.map((d, i) => <option key={i} value={i}>{d}</option>)}</select></Field>
          {stations.length > 1 && <Field label="Station"><select className="input" value={f.station_id} onChange={(e) => setF({ ...f, station_id: e.target.value })}><option value="">All</option>{stations.map((st) => <option key={st.id} value={st.id}>{st.name}</option>)}</select></Field>}
        </div>
        <p className="text-xs text-slate-500">This person does not log in — they are only for payroll, attendance and advances. Set a duty time only if you want their attendance counted; otherwise they get full salary.</p>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy || !f.name}>Add</button></div>
      </form>
    </Modal>
  );
}

function StaffDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const { data, reload } = useApi<any>(`/staff/${id}`);
  const slips = useApi<any[]>(`/staff/${id}/slips`);
  const [slipUrl, setSlipUrl] = useState<string | null>(null);
  const { can } = useAuth();
  const { busy, run } = useAction();
  const [form, setForm] = useState<null | "advance" | "repayment" | "salary" | "set-salary">(null);
  const [editing, setEditing] = useState(false);
  const [f, setF] = useState({ amount: "", note: "", deduct: "", bonus: "", salary: "", cut: "" });
  const [photos, setPhotos] = useState<number[]>([]);
  if (!data) return <Modal open onClose={onClose} title="Staff account"><Loading /></Modal>;
  const u = data.user;
  const done = () => { setForm(null); setF({ amount: "", note: "", deduct: "", bonus: "", salary: "", cut: "" }); setPhotos([]); reload(); };
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
          {can("staff.manage") && u.role === "staff" && <button className="btn-secondary" onClick={() => setEditing(true)}><Pencil size={14} /> Edit details</button>}
          {can("staff.manage") && <button className="btn-secondary" onClick={() => run(() => api("/attendance/mark", { body: { user_id: id, present: true } }), "Marked present today").then(() => reload())}>✅ Present today</button>}
          <button className="btn-primary" disabled={!u.salary} onClick={() => { setF({ ...f, deduct: String(Math.max(0, Math.min(data.balance - (data.loans_left ?? 0), u.salary ?? 0)) || "") }); setForm("salary"); }}><Banknote size={15} /> Pay salary</button>
        </div>
      </div>

      {form && (
        <form className="mt-4 space-y-3 rounded-xl border border-slate-200 p-3" onSubmit={async (e) => {
          e.preventDefault();
          const call = form === "salary" ? api(`/staff/${id}/pay-salary`, { body: { deduct: Number(f.deduct) || 0, bonus: Number(f.bonus) || 0, absence_cut: cut, photo_ids: photos } })
            : form === "set-salary" ? api(`/staff/${id}`, { method: "PATCH", body: { salary: Number(f.salary) || null } })
            : api(`/staff/${id}/entry`, { body: { type: form, amount: Number(f.amount), note: f.note || null, photo_ids: photos } });
          const r: any = await run(() => call, form === "salary" ? `Salary paid: ${pkr(net)} — slip sent on WhatsApp` : "Saved");
          if (r) { if (r.slip_url) { setSlipUrl(r.slip_url); slips.reload(); } done(); }
        }}>
          {form === "set-salary" && <Field label="Monthly salary (Rs)"><input className="input text-lg" type="number" min={0} required value={f.salary} onChange={(e) => setF({ ...f, salary: e.target.value })} /></Field>}
          {(form === "advance" || form === "repayment") && <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label={form === "advance" ? "Advance given (Rs)" : "Amount paid back (Rs)"}><input className="input text-lg" type="number" min={1} max={form === "repayment" ? data.balance : undefined} required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
            <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} placeholder="e.g. for family need" /></Field>
          </div>}
          {form === "salary" && <>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
              <Field label="Salary"><input className="input" disabled value={pkr(u.salary)} /></Field>
              <Field label={`Absence cut (${data.attendance?.unpaid_days ?? 0} unpaid days)`}><input className="input" type="number" min={0} value={f.cut === "" ? String(data.attendance?.salary_cut ?? 0) : f.cut} onChange={(e) => setF({ ...f, cut: e.target.value })} /></Field>
              <Field label="Bonus (optional)"><input className="input" type="number" min={0} value={f.bonus} onChange={(e) => setF({ ...f, bonus: e.target.value })} /></Field>
              <Field label={`Cut advance / short (owes ${pkr(Math.max(0, data.balance - (data.loans_left ?? 0)))})`}><input className="input" type="number" min={0} max={Math.max(0, Math.min(data.balance - (data.loans_left ?? 0), u.salary + (Number(f.bonus) || 0)))} value={f.deduct} onChange={(e) => setF({ ...f, deduct: e.target.value })} /></Field>
            </div>
            {data.commission > 0 && <div className="text-sm text-slate-600">+ Commission this month (shop / fuel): <b>{pkr(data.commission)}</b> — added by itself</div>}
            {loan > 0 && <div className="text-sm text-slate-600">− Loan instalment: <b>{pkr(loan)}</b> — cut by itself</div>}
            <div className="rounded-lg bg-emerald-50 p-3 text-lg">Hand over in cash: <b className="tabular-nums">{pkr(net)}</b></div>
          </>}
          {form !== "set-salary" && <ProofPhotos value={photos} onChange={setPhotos} required={form === "salary"} hint={form === "salary" ? "signed salary sheet / thumb impression" : form === "advance" ? "signed advance slip" : "receipt"} />}
          <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={() => setForm(null)}>Cancel</button><button className="btn-primary" disabled={busy || (form === "salary" && !photos.length)}>Save</button></div>
        </form>
      )}
      {slipUrl && <a href={slipUrl} target="_blank" rel="noreferrer" className="mt-3 block rounded-lg bg-emerald-50 p-3 text-sm font-semibold text-emerald-800 ring-1 ring-emerald-200">📄 Open the salary slip (PDF) — also sent on WhatsApp</a>}
      <SlipsList slips={slips.data ?? []} />
      <LoansBox userId={u.id} loans={data.loans ?? []} onChanged={reload} />
      <DutyForm u={u} att={data.attendance} onSaved={reload} />
      <LedgerList lines={data.lines} />
      {editing && <EditStaffMember u={u} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); reload(); }} />}
    </Modal>
  );
}

/** Edit a non-login staff member: name, job title, phone, station — or deactivate them (they leave payroll; history stays). */
function EditStaffMember({ u, onClose, onSaved }: { u: any; onClose: () => void; onSaved: () => void }) {
  const jobs = useLookups("job_title");
  const stations = useApi<any[]>("/stations");
  const known = jobs.list.some((j) => j.label === u.job_title);
  const [f, setF] = useState({ name: u.name ?? "", job_title: u.job_title && !known ? "Other" : (u.job_title ?? ""), custom: u.job_title && !known ? u.job_title : "", phone: u.phone ?? "", station_id: u.station_id ? String(u.station_id) : "", active: u.active !== 0 });
  const { busy, run } = useAction();
  const title = f.job_title === "Other" ? f.custom : f.job_title;
  return (
    <Modal open onClose={onClose} title={`Edit — ${u.name}`}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const body = { name: f.name, job_title: title || null, phone: f.phone || null, station_id: f.station_id ? Number(f.station_id) : null, active: f.active };
        if (await run(() => api(`/staff/${u.id}`, { method: "PATCH", body }), "Saved")) onSaved();
      }}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name"><input className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Job / designation"><select className="input" value={f.job_title} onChange={(e) => setF({ ...f, job_title: e.target.value })}>{jobs.list.map((j) => <option key={j.key}>{j.label}</option>)}<option>Other</option></select></Field>
          {f.job_title === "Other" && <Field label="Write the job"><input className="input" value={f.custom} onChange={(e) => setF({ ...f, custom: e.target.value })} /></Field>}
          <Field label="Phone"><PhoneInput value={f.phone} onChange={(v) => setF({ ...f, phone: v })} /></Field>
          {(stations.data?.length ?? 0) > 1 && <Field label="Station"><select className="input" value={f.station_id} onChange={(e) => setF({ ...f, station_id: e.target.value })}><option value="">All</option>{(stations.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>}
        </div>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Active (uncheck to remove from payroll / attendance)</label>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy || !f.name}>Save</button></div>
      </form>
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
      {/* phone: one line per entry */}
      <ul className="divide-y divide-slate-100 sm:hidden">
        {lines.map((l) => (
          <li key={l.id} className="px-3 py-2">
            <div className="flex items-start justify-between gap-2">
              <span className="min-w-0"><Badge tone={TYPE[l.type]?.tone}>{TYPE[l.type]?.label ?? l.type}</Badge> <span className="text-xs text-slate-500">{dt(l.created_at)}</span></span>
              <span className="shrink-0 text-right"><span className="block font-semibold tabular-nums">{TYPE[l.type]?.sign}{pkr(l.amount)}</span><span className="text-[11px] tabular-nums text-slate-500">owes {pkr(l.balance)}</span></span>
            </div>
            {l.note && <div className="break-words text-sm text-slate-600">{l.note}</div>}
            <ProofThumbs ids={l.proof_ids} />
          </li>
        ))}
      </ul>
      <table className="hidden w-full sm:table">
        <thead className="sticky top-0"><tr><th className="th">Date</th><th className="th">Entry</th><th className="th">Note</th><th className="th text-right">Amount</th><th className="th text-right">Owes after</th></tr></thead>
        <tbody>{lines.map((l) => (
          <tr key={l.id}>
            <td className="td whitespace-nowrap text-xs">{dt(l.created_at)}</td>
            <td className="td"><Badge tone={TYPE[l.type]?.tone}>{TYPE[l.type]?.label ?? l.type}</Badge></td>
            <td className="td text-sm text-slate-600">{l.note} <ProofThumbs ids={l.proof_ids} /></td>
            <td className="td text-right tabular-nums">{TYPE[l.type]?.sign}{pkr(l.amount)}</td>
            <td className="td text-right font-medium tabular-nums">{pkr(l.balance)}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}
