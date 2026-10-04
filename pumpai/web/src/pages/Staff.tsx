import { useState } from "react";
import { Wallet, Plus, Minus, Banknote } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Empty, Field, Loading, Modal, PageHeader, Stat, useAction } from "../components/ui";
import { dt, pkr } from "../lib/format";
import { useAuth } from "../App";

const TYPE: Record<string, { label: string; tone: string; sign: string }> = {
  advance: { label: "Advance", tone: "amber", sign: "+" }, shortage: { label: "Cash short", tone: "red", sign: "+" },
  repayment: { label: "Paid back", tone: "green", sign: "−" }, deduction: { label: "Cut from salary", tone: "green", sign: "−" },
  salary: { label: "Salary paid", tone: "blue", sign: "" }, bonus: { label: "Bonus", tone: "violet", sign: "" },
};

/** Staff khata: advances, cash shortages from shifts, salary — no salary register on paper. */
export default function Staff() {
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
  const { can } = useAuth();
  const { busy, run } = useAction();
  const [form, setForm] = useState<null | "advance" | "repayment" | "salary" | "set-salary">(null);
  const [f, setF] = useState({ amount: "", note: "", deduct: "", bonus: "", salary: "" });
  if (!data) return <Modal open onClose={onClose} title="Staff account"><Loading /></Modal>;
  const u = data.user;
  const done = () => { setForm(null); setF({ amount: "", note: "", deduct: "", bonus: "", salary: "" }); reload(); };
  const net = (u.salary ?? 0) + (Number(f.bonus) || 0) - (Number(f.deduct) || 0);
  return (
    <Modal open onClose={onClose} title={`${u.name} — staff account`} wide>
      <div className="flex flex-wrap items-center gap-3">
        <div className="rounded-xl bg-amber-50 px-4 py-3"><div className="text-xs text-amber-800">Owes the business</div><div className="text-2xl font-bold tabular-nums">{pkr(data.balance)}</div></div>
        <div className="rounded-xl bg-slate-50 px-4 py-3"><div className="text-xs text-slate-600">Monthly salary</div><div className="text-2xl font-bold tabular-nums">{u.salary ? pkr(u.salary) : "—"}</div></div>
        <div className="ml-auto flex flex-wrap gap-2">
          <button className="btn-secondary" onClick={() => setForm("advance")}><Plus size={15} /> Advance</button>
          <button className="btn-secondary" disabled={data.balance <= 0} onClick={() => setForm("repayment")}><Minus size={15} /> Paid back</button>
          {can("users.manage") && <button className="btn-secondary" onClick={() => { setF({ ...f, salary: String(u.salary ?? "") }); setForm("set-salary"); }}>Set salary</button>}
          <button className="btn-primary" disabled={!u.salary} onClick={() => { setF({ ...f, deduct: String(Math.min(data.balance, u.salary ?? 0) || "") }); setForm("salary"); }}><Banknote size={15} /> Pay salary</button>
        </div>
      </div>

      {form && (
        <form className="mt-4 space-y-3 rounded-xl border border-slate-200 p-3" onSubmit={async (e) => {
          e.preventDefault();
          const call = form === "salary" ? api(`/staff/${id}/pay-salary`, { body: { deduct: Number(f.deduct) || 0, bonus: Number(f.bonus) || 0 } })
            : form === "set-salary" ? api(`/staff/${id}`, { method: "PATCH", body: { salary: Number(f.salary) || null } })
            : api(`/staff/${id}/entry`, { body: { type: form, amount: Number(f.amount), note: f.note || null } });
          if (await run(() => call, form === "salary" ? `Salary paid: ${pkr(net)}` : "Saved")) done();
        }}>
          {form === "set-salary" && <Field label="Monthly salary (Rs)"><input className="input text-lg" type="number" min={0} required value={f.salary} onChange={(e) => setF({ ...f, salary: e.target.value })} /></Field>}
          {(form === "advance" || form === "repayment") && <div className="grid gap-3 sm:grid-cols-2">
            <Field label={form === "advance" ? "Advance given (Rs)" : "Amount paid back (Rs)"}><input className="input text-lg" type="number" min={1} max={form === "repayment" ? data.balance : undefined} required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
            <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} placeholder="e.g. for family need" /></Field>
          </div>}
          {form === "salary" && <>
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="Salary"><input className="input" disabled value={pkr(u.salary)} /></Field>
              <Field label="Bonus (optional)"><input className="input" type="number" min={0} value={f.bonus} onChange={(e) => setF({ ...f, bonus: e.target.value })} /></Field>
              <Field label={`Cut advance / short (owes ${pkr(data.balance)})`}><input className="input" type="number" min={0} max={Math.min(data.balance, u.salary + (Number(f.bonus) || 0))} value={f.deduct} onChange={(e) => setF({ ...f, deduct: e.target.value })} /></Field>
            </div>
            <div className="rounded-lg bg-emerald-50 p-3 text-lg">Hand over in cash: <b className="tabular-nums">{pkr(net)}</b></div>
          </>}
          <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={() => setForm(null)}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
        </form>
      )}
      <LedgerList lines={data.lines} />
    </Modal>
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
