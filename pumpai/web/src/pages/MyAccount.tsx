import { useState } from "react";
import { LogIn, LogOut, CalendarPlus } from "lucide-react";
import { api, useApi, setToken } from "../lib/api";
import { Badge, Field, Loading, Modal, PageHeader, useAction, useToast } from "../components/ui";
import { pkr } from "../lib/format";
import { LedgerList } from "./Staff";
import { Leaderboard } from "./Team";
import { SlipsList } from "../components/StaffExtras";
import { LiveSelfie } from "../components/LiveSelfie";

const Ur = ({ children }: { children: React.ReactNode }) => <span lang="ur" dir="rtl" className="font-urdu">{children}</span>;
export const DAY_STATUS: Record<string, { label: string; cls: string }> = {
  present: { label: "Present", cls: "bg-emerald-500" }, late: { label: "Late", cls: "bg-amber-500" }, absent: { label: "Absent", cls: "bg-red-500" },
  off: { label: "Weekly off", cls: "bg-slate-300" }, leave_paid: { label: "Leave", cls: "bg-blue-400" }, leave_sick: { label: "Sick leave", cls: "bg-blue-400" },
  leave_unpaid: { label: "Unpaid leave", cls: "bg-violet-400" }, not_yet: { label: "Today", cls: "bg-white ring-2 ring-slate-300" },
};

/** A staff member's own page: attendance, leave, advances and salary. */
export default function MyAccount() {
  const { data } = useApi<any>("/me/account");
  const att = useApi<any>("/attendance/me");
  const [leave, setLeave] = useState(false);
  const toast = useToast();
  const [proofFor, setProofFor] = useState<"in" | "out" | null>(null);
  if (!data || !att.data) return <Loading />;
  const a = att.data;
  const mark = async (kind: "in" | "out", proof: { photo_id: number; lat: number; lng: number; accuracy: number }) => {
    // throws on failure so the camera window stays open with the message
    await api(`/attendance/check-${kind}`, { body: proof });
    setProofFor(null);
    toast("ok", kind === "in" ? "Checked in · حاضری لگ گئی" : "Checked out");
    att.reload();
  };
  return (
    <div className="space-y-4">
      <PageHeader title="My account" subtitle="Attendance, leave, advances and salary" />
      <div className="rounded-2xl bg-white p-4 ring-1 ring-slate-200">
        <div className="mb-3 text-lg font-semibold">Attendance today · <Ur>آج کی حاضری</Ur></div>
        {!a.today ? (
          <button type="button" onClick={() => setProofFor("in")} className="flex w-full items-center justify-center gap-3 rounded-2xl bg-emerald-600 px-3 py-4 text-xl font-bold text-white active:scale-95 sm:py-5 sm:text-2xl">
            <LogIn size={28} className="shrink-0" /><span className="flex flex-col items-center leading-tight sm:flex-row sm:gap-2"><span className="whitespace-nowrap">Check in with selfie</span><Ur>حاضری لگائیں</Ur></span>
          </button>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            <div className="text-lg">✅ In at <b>{new Date(a.today.check_in).toLocaleTimeString("en-PK", { hour: "2-digit", minute: "2-digit" })}</b>
              {a.today.late_minutes > 15 && <Badge tone="amber">{a.today.late_minutes} min late</Badge>}
              {a.today.check_out && <> · out at <b>{new Date(a.today.check_out).toLocaleTimeString("en-PK", { hour: "2-digit", minute: "2-digit" })}</b></>}</div>
            {!a.today.check_out && (
              <button type="button" onClick={() => setProofFor("out")} className="ml-auto flex items-center gap-2 rounded-xl bg-slate-800 px-5 py-3 text-lg font-semibold text-white">
                <LogOut /> Check out
              </button>
            )}
          </div>
        )}
        {a.month.tracked ? (
          <>
            <div className="mt-4 flex flex-wrap gap-1" aria-label="This month">
              {a.month.days.map((x: any) => <span key={x.day} title={`${x.day}: ${DAY_STATUS[x.status]?.label}`} className={`flex h-8 w-8 items-center justify-center rounded-md text-xs font-semibold text-white ${DAY_STATUS[x.status]?.cls}`}>{Number(x.day.slice(8))}</span>)}
            </div>
            <div className="mt-2 flex flex-wrap gap-3 text-sm text-slate-600">
              <span>Present <b>{a.month.present}</b></span><span>Late <b>{a.month.late}</b></span><span>Absent <b className="text-red-600">{a.month.absent}</b></span><span>Leave <b>{a.month.paid_leave + a.month.unpaid_leave}</b></span>
              {a.month.salary_cut > 0 && <span className="text-red-600">Salary cut so far {pkr(a.month.salary_cut)}</span>}
            </div>
          </>
        ) : <p className="mt-3 text-sm text-slate-500">Your duty time is not set yet, so attendance is not counted.</p>}
        <LiveSelfie open={proofFor !== null} title={proofFor === "out" ? "Check out · selfie + location" : "Check in · selfie + location"}
          onClose={() => setProofFor(null)} onDone={(p) => mark(proofFor ?? "in", p)} />
        <button className="btn-secondary mt-3" onClick={() => setLeave(true)}><CalendarPlus size={15} /> Ask for leave · <Ur>چھٹی</Ur></button>
        {a.leaves.length > 0 && <ul className="mt-2 text-sm">{a.leaves.slice(0, 3).map((l: any) => <li key={l.id}>{l.from_day}{l.to_day !== l.from_day ? ` – ${l.to_day}` : ""} · {l.type} · <Badge tone={l.status === "approved" ? "green" : l.status === "rejected" ? "red" : "amber"}>{l.status}</Badge></li>)}</ul>}
      </div>
      <div className="flex flex-wrap gap-3">
        <div className="rounded-2xl bg-amber-50 px-5 py-4 ring-1 ring-amber-200"><div className="text-sm text-amber-800">To adjust from salary · <Ur>تنخواہ سے کٹے گا</Ur></div><div className="text-3xl font-bold tabular-nums">{pkr(data.balance)}</div></div>
        {data.user.salary ? <div className="rounded-2xl bg-slate-50 px-5 py-4 ring-1 ring-slate-200"><div className="text-sm text-slate-600">Monthly salary</div><div className="text-3xl font-bold tabular-nums">{pkr(data.user.salary)}</div></div> : null}
      </div>
      {data.user.role === "salesman" && <div className="rounded-2xl bg-white p-4 ring-1 ring-slate-200">
        <div className="mb-1 text-lg font-semibold">🏆 This week · <Ur>اس ہفتے</Ur></div>
        <Leaderboard compact />
      </div>}
      {(data.loans?.length > 0 || data.slips?.length > 0) && <div className="rounded-2xl bg-white p-4 ring-1 ring-slate-200">
        {data.loans?.map((l: any) => <div key={l.id} className="mb-2 text-sm"><b>Loan</b> {l.note ?? ""}: {pkr(l.remaining)} left · {pkr(l.instalment)} every month · {l.months_left} months to go</div>)}
        {data.slips?.length > 0 && <><div className="font-semibold">Salary slips · <Ur>تنخواہ کی پرچی</Ur></div><SlipsList slips={data.slips} /></>}
      </div>}
      <MyTraining />
      <div className="card p-3"><LedgerList lines={data.lines} /></div>
      {leave && <LeaveForm onClose={() => setLeave(false)} onDone={() => { setLeave(false); att.reload(); }} />}
      <ChangePassword />
    </div>
  );
}

export function LeaveForm({ userId, onClose, onDone }: { userId?: number; onClose: () => void; onDone: () => void }) {
  const today = new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10);
  const [f, setF] = useState({ from_day: today, to_day: today, type: "paid", reason: "" });
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={userId ? "Record leave" : "Ask for leave"}>
      <form className="space-y-3" onSubmit={async (e) => { e.preventDefault(); if (await run(() => api("/leaves", { body: { ...f, user_id: userId, reason: f.reason || null } }), userId ? "Leave recorded" : "Sent to the manager")) onDone(); }}>
        <div className="grid grid-cols-2 gap-3">
          <Field label="From"><input className="input" type="date" required value={f.from_day} onChange={(e) => setF({ ...f, from_day: e.target.value, to_day: e.target.value > f.to_day ? e.target.value : f.to_day })} /></Field>
          <Field label="To"><input className="input" type="date" required min={f.from_day} value={f.to_day} onChange={(e) => setF({ ...f, to_day: e.target.value })} /></Field>
        </div>
        <Field label="Type"><div className="grid grid-cols-3 gap-2">{[["paid", "Paid"], ["sick", "Sick"], ["unpaid", "Unpaid"]].map(([k, l]) => <button type="button" key={k} onClick={() => setF({ ...f, type: k })} className={`rounded-lg border-2 py-2 ${f.type === k ? "border-brand-600 bg-emerald-50" : "border-slate-200"}`}>{l}</button>)}</div></Field>
        <Field label="Reason"><input className="input" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></Field>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

function MyTraining() {
  const { data } = useApi<any>("/me/training");
  const me = data?.staff?.[0];
  if (!me) return null;
  const rows = Object.entries(me.records).filter(([, r]: [string, any]) => r.status !== "none");
  if (!rows.length) return null;
  return (
    <div className="rounded-2xl bg-white p-4 ring-1 ring-slate-200">
      <div className="mb-1 font-semibold">My training · <Ur>تربیت</Ur></div>
      {rows.map(([topic, r]: [string, any]) => (
        <div key={topic} className="flex justify-between border-b border-slate-100 py-1 text-sm"><span>{topic}</span>
          <span className={r.status === "overdue" || r.status === "missing" ? "font-semibold text-red-600" : r.status === "due_soon" ? "text-amber-700" : "text-emerald-700"}>
            {r.status === "missing" ? "Needed — ask the manager" : r.status === "overdue" ? `Overdue (${r.next_due})` : r.next_due ? `OK till ${r.next_due}` : "Done"}</span></div>
      ))}
    </div>
  );
}

/** Change your own password; other phones / browsers are signed out. */
function ChangePassword() {
  const [f, setF] = useState({ old_password: "", new_password: "", again: "" });
  const [open, setOpen] = useState(false);
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  if (!open) return <button className="btn-secondary min-h-9" onClick={() => setOpen(true)}>Change my password · <Ur>پاس ورڈ بدلیں</Ur></button>;
  return (
    <form className="space-y-3 rounded-2xl bg-white p-4 ring-1 ring-slate-200" onSubmit={async (e) => {
      e.preventDefault();
      if (f.new_password !== f.again) return toast("err", "The two new passwords are not the same");
      setBusy(true);
      try {
        const r = await api("/me/password", { body: { old_password: f.old_password, new_password: f.new_password } });
        setToken(r.token);
        toast("ok", "Password changed · پاس ورڈ بدل گیا — other phones are signed out");
        setOpen(false); setF({ old_password: "", new_password: "", again: "" });
      } catch (err: any) { toast("err", err.message); } finally { setBusy(false); }
    }}>
      <div className="font-semibold">Change my password · <Ur>پاس ورڈ بدلیں</Ur></div>
      <input className="input" type="password" autoComplete="current-password" placeholder="Current password" required value={f.old_password} onChange={(e) => setF({ ...f, old_password: e.target.value })} />
      <input className="input" type="password" autoComplete="new-password" placeholder="New password (8+ characters)" minLength={8} required value={f.new_password} onChange={(e) => setF({ ...f, new_password: e.target.value })} />
      <input className="input" type="password" autoComplete="new-password" placeholder="New password again" minLength={8} required value={f.again} onChange={(e) => setF({ ...f, again: e.target.value })} />
      <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={() => setOpen(false)}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
    </form>
  );
}
