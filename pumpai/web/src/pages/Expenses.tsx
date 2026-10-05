import { useEffect, useState } from "react";
import { Plus, Download, Check, X, Trash2, Settings2, Repeat } from "lucide-react";
import { api, linkToken, useApi } from "../lib/api";
import { Badge, Empty, Field, Loading, Modal, PageHeader, Stat, statusTone, useAction } from "../components/ui";
import { FixedCosts } from "../components/FixedCosts";
import { d, pkr, pkrShort } from "../lib/format";
import { useAuth } from "../App";
import { PhotoButton, photoUrl } from "../components/Capture";
import { AccountPicker } from "../components/BankParts";

const METHODS = ["cash", "bank", "jazzcash", "easypaisa", "raast", "cheque", "card"];
const thisMonth = () => new Date().toISOString().slice(0, 7);

export default function Expenses() {
  const { can, user } = useAuth();
  const [month, setMonth] = useState(thisMonth());
  const [category, setCategory] = useState("");
  const [status, setStatus] = useState("");
  const qs = new URLSearchParams(Object.entries({ month, category, status }).filter(([, v]) => v)).toString();
  const { data, reload } = useApi<any>(`/expenses?${qs}`);
  const cats = useApi<any>("/expense-categories");
  const stations = useApi<any[]>("/stations");
  const { busy, run } = useAction();
  const [adding, setAdding] = useState(false);
  const [setup, setSetup] = useState(false);
  const [fixed, setFixed] = useState(false);
  if (!data || !cats.data) return <Loading />;
  const s = data.summary;
  const change = s.previous_month ? Math.round(((s.total - s.previous_month) / s.previous_month) * 100) : null;
  const maxCat = Math.max(1, ...s.by_category.map((c: any) => Math.max(c.spent, c.budget ?? 0)));
  const csvUrl = `/api/expenses.csv?${qs}&token=${linkToken()}`;
  const refresh = () => { reload(); cats.reload(); };

  const actions = (e: any) => (
    <div className="flex justify-end gap-1">
      {e.status === "pending" && can("expenses.approve") && <>
        <button className="btn-primary min-h-9 !px-2 !py-1 sm:min-h-0" title="Approve" aria-label="Approve" disabled={busy} onClick={() => run(() => api(`/expenses/${e.id}/approve`, { body: {} }), "Approved").then(refresh)}><Check size={14} /></button>
        <button className="btn-secondary min-h-9 !px-2 !py-1 sm:min-h-0" title="Reject" aria-label="Reject" disabled={busy} onClick={() => run(() => api(`/expenses/${e.id}/reject`, { body: {} }), "Rejected").then(refresh)}><X size={14} /></button>
      </>}
      {(can("expenses.approve") || (e.status === "pending" && e.created_by === user?.name)) &&
        <button className="btn-secondary min-h-9 !px-2 !py-1 text-red-600 sm:min-h-0" title="Delete" aria-label="Delete" disabled={busy} onClick={() => confirm("Delete this expense?") && run(() => api(`/expenses/${e.id}`, { method: "DELETE" }), "Deleted").then(refresh)}><Trash2 size={14} /></button>}
    </div>
  );
  return (
    <div className="space-y-5">
      <PageHeader title="Expenses" subtitle={`Manager entries above ${pkr(data.approval_limit)} need admin approval`}
        actions={<>
          {can("expenses.approve") && <button className="btn-secondary" onClick={() => setSetup(true)}><Settings2 size={15} /> Categories & budgets</button>}
          <button className="btn-secondary" onClick={() => setFixed(true)}><Repeat size={15} /> Monthly & bills</button>
          <a className="btn-secondary" href={csvUrl}><Download size={15} /> Excel / CSV</a>
          <button className="btn-primary" onClick={() => setAdding(true)}><Plus size={16} /> Add expense</button>
        </>} />

      <div className="grid grid-cols-2 items-end gap-3 sm:flex sm:flex-wrap">
        <div className="col-span-2 sm:col-span-1"><Field label="Month"><input className="input" type="month" value={month} onChange={(e) => setMonth(e.target.value || thisMonth())} /></Field></div>
        <Field label="Category"><select className="input" value={category} onChange={(e) => setCategory(e.target.value)}><option value="">All</option>{cats.data.categories.map((c: any) => <option key={c.id}>{c.name}</option>)}</select></Field>
        <Field label="Status"><select className="input" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All</option><option value="pending">Pending</option><option value="approved">Approved</option><option value="rejected">Rejected</option></select></Field>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Expenses this month" value={pkrShort(s.total)} tone="red" hint={change !== null ? `${change > 0 ? "+" : ""}${change}% vs last month (${pkrShort(s.previous_month)})` : undefined} />
        <Stat label="Revenue this month" value={pkrShort(s.revenue.total)} tone="green" hint={`Retail ${pkrShort(s.revenue.retail)} · Wholesale ${pkrShort(s.revenue.wholesale)}`} />
        <Stat label="Revenue − expenses" value={pkrShort(s.revenue_minus_expenses)} tone="blue" hint="Before fuel purchase cost" />
        <Stat label="Waiting approval" value={s.pending.n} tone={s.pending.n ? "amber" : "slate"} hint={s.pending.n ? pkr(s.pending.s) : "Nothing pending"} />
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[360px_minmax(0,1fr)]">
        <div className="card min-w-0 p-4">
          <h2 className="mb-3 font-semibold">By category</h2>
          <div className="space-y-2.5">
            {s.by_category.filter((c: any) => c.spent > 0 || c.budget).map((c: any) => (
              <div key={c.category}>
                <div className="flex justify-between text-xs"><span>{c.category}</span><span className={`tabular-nums ${c.over_budget ? "font-semibold text-red-600" : "text-slate-600"}`}>{pkr(c.spent)}{c.budget ? ` / ${pkrShort(c.budget)}` : ""}</span></div>
                <div className="relative mt-1 h-1.5 rounded-full bg-slate-100">
                  <div className="h-full rounded-full" style={{ width: `${(c.spent / maxCat) * 100}%`, background: c.over_budget ? "#e34948" : "#2a78d6" }} />
                  {c.budget ? <div className="absolute top-[-2px] h-2.5 w-0.5 bg-slate-500" style={{ left: `${(c.budget / maxCat) * 100}%` }} title="Budget" /> : null}
                </div>
                {c.avg_3m > 0 && <div className="text-[11px] text-slate-400">3-month avg {pkr(c.avg_3m)}</div>}
              </div>
            ))}
          </div>
          <h3 className="mb-1 mt-4 text-sm font-semibold">Paid by</h3>
          <div className="flex flex-wrap gap-1.5">{s.by_method.map((m: any) => <Badge key={m.method}>{m.method}: {pkrShort(m.total)}</Badge>)}</div>
        </div>
        {/* phone: one card per expense */}
        <ul className="card min-w-0 divide-y divide-slate-100 sm:hidden">
          {data.expenses.map((e: any) => (
            <li key={e.id} className="px-4 py-3">
              <div className="flex items-start justify-between gap-2">
                <span className="min-w-0"><span className="block font-semibold">{e.paid_to ?? e.category}</span><span className="text-xs text-slate-500">{e.category} · {d(e.expense_date)} · {e.method}</span></span>
                <span className="shrink-0 font-semibold tabular-nums">{pkr(e.amount)}</span>
              </div>
              {(e.note || e.receipt_ref || e.photo_id) && <div className="break-words text-xs text-slate-500">{[e.note, e.receipt_ref].filter(Boolean).join(" · ")}{e.photo_id ? <a className="ml-1 text-sky-700 underline" href={photoUrl(e.photo_id)} target="_blank" rel="noreferrer">📷 bill</a> : null}</div>}
              <div className="mt-1.5 flex flex-wrap items-center justify-between gap-2">
                <span className="flex flex-wrap items-center gap-1.5 text-[11px] text-slate-400"><Badge tone={statusTone(e.status === "approved" ? "delivered" : e.status === "rejected" ? "cancelled" : "pending")}>{e.status}</Badge>{e.station_name ?? "All"} · {e.created_by}</span>
                {actions(e)}
              </div>
            </li>
          ))}
          {!data.expenses.length && <li><Empty>No expenses for this filter</Empty></li>}
        </ul>
        <div className="card hidden min-w-0 overflow-x-auto sm:block">
          <table className="w-full">
            <thead><tr><th className="th">Date</th><th className="th">Category</th><th className="th">Paid to / note</th><th className="th">Station</th><th className="th text-right">Amount</th><th className="th">Status</th><th className="th" /></tr></thead>
            <tbody>{data.expenses.map((e: any) => (
              <tr key={e.id}>
                <td className="td text-xs">{d(e.expense_date)}</td>
                <td className="td text-sm">{e.category}</td>
                <td className="td text-xs"><div>{e.paid_to ?? "—"} <span className="text-slate-400">· {e.method}</span></div><div className="text-slate-500">{[e.note, e.receipt_ref].filter(Boolean).join(" · ")}{e.photo_id ? <a className="ml-1 text-sky-700 underline" href={photoUrl(e.photo_id)} target="_blank" rel="noreferrer">📷 bill</a> : null}</div></td>
                <td className="td text-xs">{e.station_name ?? "All"}</td>
                <td className="td text-right font-medium tabular-nums">{pkr(e.amount)}</td>
                <td className="td"><Badge tone={statusTone(e.status === "approved" ? "delivered" : e.status === "rejected" ? "cancelled" : "pending")}>{e.status}</Badge><div className="text-[11px] text-slate-400">{e.created_by}</div></td>
                <td className="td">
                  {actions(e)}
                </td>
              </tr>
            ))}</tbody>
          </table>
          {!data.expenses.length && <Empty>No expenses for this filter</Empty>}
        </div>
      </div>

      {adding && <ExpenseForm categories={cats.data.categories} stations={stations.data ?? []} limit={data.approval_limit} canApprove={can("expenses.approve")} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); refresh(); }} />}
      {fixed && <FixedCosts categories={cats.data.categories} stations={stations.data ?? []} onClose={() => { setFixed(false); refresh(); }} />}
      {setup && <CategorySetup data={cats.data} onClose={() => setSetup(false)} onChanged={refresh} />}
    </div>
  );
}

function ExpenseForm({ categories, stations, limit, canApprove, onClose, onSaved }: { categories: any[]; stations: any[]; limit: number; canApprove: boolean; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ category: categories[0]?.name ?? "", amount: "", paid_to: "", method: "cash", station_id: "", note: "", receipt_ref: "", expense_date: new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10), photo_id: null as number | null });
  const [account, setAccount] = useState<number | null>(null);
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title="Add expense">
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const body = { ...f, amount: Number(f.amount), station_id: f.station_id ? Number(f.station_id) : null, paid_to: f.paid_to || null, note: f.note || null, receipt_ref: f.receipt_ref || null, account_id: f.method === "cash" ? null : account };
        if (await run(() => api("/expenses", { body }), (r: any) => r.status === "pending" ? "Saved — waiting for admin approval" : "Expense saved")) onSaved();
      }}>
        <div className="flex items-center gap-2 rounded-lg bg-sky-50 p-2 text-sm">
          <PhotoButton kind="receipt" label="Photo of bill" onRead={(r, id) => setF((x) => ({
            ...x, photo_id: id, amount: r?.amount ? String(r.amount) : x.amount, paid_to: r?.paid_to ?? x.paid_to,
            note: r?.description ?? x.note, category: r?.category && categories.some((c) => c.name === r.category) ? r.category : x.category,
            expense_date: r?.date && /^\d{4}-\d{2}-\d{2}$/.test(r.date) ? r.date : x.expense_date,
          }))} />
          <span className="text-slate-600">{f.photo_id ? "📷 Bill photo attached — check the filled details" : "Take a photo of the bill to fill this form"}</span>
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Category"><select className="input" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{categories.map((c) => <option key={c.id}>{c.name}</option>)}</select></Field>
          <Field label="Amount (Rs)"><input className="input" type="number" min={1} required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
          <Field label="Paid to"><input className="input" value={f.paid_to} onChange={(e) => setF({ ...f, paid_to: e.target.value })} /></Field>
          <Field label="Paid by"><select className="input" value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })}>{METHODS.map((m) => <option key={m}>{m}</option>)}</select></Field>
          <Field label="Station"><select className="input" value={f.station_id} onChange={(e) => setF({ ...f, station_id: e.target.value })}><option value="">All / head office</option>{stations.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
          <Field label="Date"><input className="input" type="date" value={f.expense_date} onChange={(e) => setF({ ...f, expense_date: e.target.value })} /></Field>
          <Field label="Bill / receipt no."><input className="input" value={f.receipt_ref} onChange={(e) => setF({ ...f, receipt_ref: e.target.value })} /></Field>
          <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        </div>
        <AccountPicker method={f.method} label="Paid from which bank? · کس بینک سے" value={account} onChange={setAccount} />
        {!canApprove && Number(f.amount) > limit && <p className="rounded bg-amber-50 p-2 text-xs text-amber-800">Above {pkr(limit)} — this will wait for admin approval.</p>}
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

function CategorySetup({ data, onClose, onChanged }: { data: any; onClose: () => void; onChanged: () => void }) {
  const [name, setName] = useState("");
  const [limit, setLimit] = useState(String(data.approval_limit));
  const [budgets, setBudgets] = useState<Record<number, string>>({});
  useEffect(() => setBudgets(Object.fromEntries(data.categories.map((c: any) => [c.id, c.monthly_budget ? String(c.monthly_budget) : ""]))), [data]);
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title="Expense categories & budgets" wide>
      <div className="space-y-4">
        <form className="flex flex-wrap items-end gap-2" onSubmit={async (e) => { e.preventDefault(); if (await run(() => api("/expenses/settings", { method: "PUT", body: { approval_limit: Number(limit) } }), "Approval limit saved")) onChanged(); }}>
          <Field label="Manager approval limit (Rs) — above this needs admin approval"><input className="input w-48" type="number" min={0} value={limit} onChange={(e) => setLimit(e.target.value)} /></Field>
          <button className="btn-secondary" disabled={busy}>Save</button>
        </form>
        <table className="w-full">
          <thead><tr><th className="th">Category</th><th className="th">Monthly budget (Rs)</th><th className="th" /></tr></thead>
          <tbody>{data.categories.map((c: any) => (
            <tr key={c.id}><td className="td text-sm">{c.name}</td>
              <td className="td"><input className="input w-28 sm:w-40" type="number" min={0} placeholder="no budget" value={budgets[c.id] ?? ""} onChange={(e) => setBudgets({ ...budgets, [c.id]: e.target.value })} /></td>
              <td className="td"><button className="btn-secondary min-h-9 !px-2 !py-1 text-xs sm:min-h-0" disabled={busy} onClick={() => run(() => api(`/expense-categories/${c.id}`, { method: "PATCH", body: { monthly_budget: budgets[c.id] ? Number(budgets[c.id]) : null } }), "Budget saved").then(onChanged)}>Save</button></td></tr>
          ))}</tbody>
        </table>
        <form className="flex gap-2" onSubmit={async (e) => { e.preventDefault(); if (await run(() => api("/expense-categories", { body: { name } }), "Category added")) { setName(""); onChanged(); } }}>
          <input className="input" placeholder="New category name" value={name} onChange={(e) => setName(e.target.value)} />
          <button className="btn-primary" disabled={busy || name.length < 2}>Add</button>
        </form>
      </div>
    </Modal>
  );
}
