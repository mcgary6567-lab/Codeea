import { useState } from "react";
import { Plus, Wallet } from "lucide-react";
import { api, useApi } from "../lib/api";
import { ProofPhotos, ProofThumbs } from "../components/Capture";
import { AccountPicker } from "../components/BankParts";
import { Badge, Empty, Field, Loading, Modal, PageHeader, Stat, useAction } from "../components/ui";
import { Ur } from "../components/VoiceShell";
import { PRODUCTS, ago, dt, num, phone, pkr, pkrShort } from "../lib/format";

export default function Suppliers() {
  const { data, reload } = useApi<any[]>("/suppliers");
  const [open, setOpen] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  if (!data) return <Loading />;
  const total = data.reduce((a, s) => a + Math.max(0, s.owed), 0);

  return (
    <div className="space-y-5">
      <PageHeader title="Suppliers" subtitle="Fuel bought on credit and payments to depots. Tanker deliveries with a supplier and purchase rate are added here automatically."
        actions={<button className="btn-primary" onClick={() => setAdding(true)}><Plus size={16} /> Add supplier</button>} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        <Stat label="We owe suppliers" value={pkrShort(total)} tone="red" />
        <Stat label="Suppliers" value={data.length} />
        <Stat label="Bought this month" value={`${num(data.reduce((a, s) => a + s.month_l, 0))} L`} />
      </div>
      {/* phone: one card per supplier */}
      <ul className="card divide-y divide-slate-100 sm:hidden">
        {data.map((s) => (
          <li key={s.id} className="cursor-pointer px-4 py-3 active:bg-slate-50" onClick={() => setOpen(s.id)}>
            <div className="flex items-start justify-between gap-2">
              <span className="min-w-0"><span className="block font-semibold">{s.name}</span>{s.phone && <span className="text-xs text-slate-500">{phone(s.phone)}</span>}</span>
              <span className="shrink-0 text-right"><span className="block font-semibold tabular-nums">{pkr(s.owed)}</span><span className="text-[11px] text-slate-500">We owe · <Ur>ادائیگی باقی</Ur></span></span>
            </div>
            <div className="mt-0.5 text-xs text-slate-500">{num(s.month_l)} L this month · bought {ago(s.last_purchase)} · paid {ago(s.last_payment)}</div>
          </li>
        ))}
        {!data.length && <li><Empty>No suppliers yet</Empty></li>}
      </ul>
      <div className="card hidden overflow-x-auto sm:block">
        <table className="w-full">
          <thead><tr><th className="th">Supplier</th><th className="th text-right">We owe</th><th className="th text-right">This month</th><th className="th">Last purchase</th><th className="th">Last payment</th></tr></thead>
          <tbody>{data.map((s) => (
            <tr key={s.id} className="cursor-pointer hover:bg-slate-50" onClick={() => setOpen(s.id)}>
              <td className="td"><div className="font-medium">{s.name}</div><div className="text-xs text-slate-500">{s.phone ? phone(s.phone) : ""}</div></td>
              <td className="td text-right font-semibold tabular-nums">{pkr(s.owed)}</td>
              <td className="td text-right tabular-nums">{num(s.month_l)} L</td>
              <td className="td text-xs text-slate-500">{ago(s.last_purchase)}</td>
              <td className="td text-xs text-slate-500">{ago(s.last_payment)}</td>
            </tr>
          ))}</tbody>
        </table>
        {!data.length && <Empty>No suppliers yet</Empty>}
      </div>
      {open && <SupplierDetail id={open} onClose={() => setOpen(null)} onChanged={reload} />}
      {adding && <AddSupplier onClose={() => setAdding(false)} onSaved={() => { setAdding(false); reload(); }} />}
    </div>
  );
}

function AddSupplier({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ name: "", phone: "", opening_balance: "0" });
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title="Add supplier">
      <form className="space-y-3" onSubmit={async (e) => { e.preventDefault(); if (await run(() => api("/suppliers", { body: { name: f.name, phone: f.phone || null, opening_balance: Number(f.opening_balance) || 0 } }), "Supplier added")) onSaved(); }}>
        <Field label="Name (e.g. PSO depot)"><input className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Phone"><input className="input" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
        <Field label="Opening balance we owe (Rs)"><input className="input" type="number" value={f.opening_balance} onChange={(e) => setF({ ...f, opening_balance: e.target.value })} /></Field>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

function SupplierDetail({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged: () => void }) {
  const { data: s, reload } = useApi<any>(`/suppliers/${id}`);
  const [pay, setPay] = useState({ amount: "", method: "Bank transfer", ref: "", wht: "" });
  const [photos, setPhotos] = useState<number[]>([]);
  const [account, setAccount] = useState<number | null>(null);
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={s?.name ?? "Supplier"} wide>
      {!s ? <Loading /> : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-end gap-3">
            <Stat label="We owe" value={pkr(s.owed)} tone="red" />
            <form className="grid min-w-0 flex-1 grid-cols-2 items-end gap-2 sm:flex sm:flex-wrap" onSubmit={async (e) => {
              e.preventDefault();
              if (await run(() => api(`/suppliers/${id}/payment`, { body: { amount: Number(pay.amount), method: pay.method, ref: pay.ref || null, withholding: Number(pay.wht) || 0, photo_ids: photos, account_id: account } }), (r: any) => `Payment saved. We now owe ${pkr(r.owed)}`)) { setPay({ ...pay, amount: "", ref: "", wht: "" }); setPhotos([]); reload(); onChanged(); }
            }}>
              <Field label="Pay amount (Rs)"><input className="input sm:w-40" type="number" min={1} required value={pay.amount} onChange={(e) => setPay({ ...pay, amount: e.target.value })} /></Field>
              <Field label="Method"><select className="input" value={pay.method} onChange={(e) => setPay({ ...pay, method: e.target.value })}>{["Bank transfer", "Pay order", "Online (1LINK)", "Cheque", "Cash"].map((m) => <option key={m}>{m}</option>)}</select></Field>
              <Field label="Ref"><input className="input sm:w-32" value={pay.ref} onChange={(e) => setPay({ ...pay, ref: e.target.value })} /></Field>
              <Field label="Tax withheld (Rs)"><input className="input sm:w-32" type="number" min={0} placeholder="0" value={pay.wht} onChange={(e) => setPay({ ...pay, wht: e.target.value })} /></Field>
              <div className="col-span-2 w-full"><AccountPicker method={pay.method} label="Paid from which bank? · کس بینک سے" value={account} onChange={setAccount} /></div>
              <button className="btn-primary col-span-2" disabled={busy || (pay.method === "Cheque" && !photos.length)}><Wallet size={15} /> Record payment</button>
              <div className="col-span-2 w-full"><ProofPhotos value={photos} onChange={setPhotos} required={pay.method === "Cheque"} hint="pay order, cheque, bank / 1LINK receipt" /></div>
            </form>
          </div>
          <div className="max-h-96 overflow-auto rounded-lg border border-slate-200">
            <ul className="divide-y divide-slate-100 sm:hidden">
              {s.lines.map((t: any) => (
                <li key={t.id} className="px-3 py-2">
                  <div className="flex items-start justify-between gap-2">
                    <span className="min-w-0"><Badge tone={t.type === "purchase" ? "blue" : t.type === "payment" ? "green" : "violet"}>{t.type}</Badge> <span className="text-xs text-slate-500">{dt(t.txn_date)}</span></span>
                    <span className="shrink-0 text-right tabular-nums">{t.debit ? <span className="block font-semibold">{pkr(t.debit)}</span> : null}{t.credit ? <span className="block font-semibold text-emerald-700">−{pkr(t.credit)}</span> : null}<span className="text-[11px] text-slate-500">owe {pkr(t.balance)}</span></span>
                  </div>
                  <div className="break-words text-xs text-slate-600">{t.product ? `${num(t.litres)} L ${PRODUCTS[t.product]} @ Rs ${t.rate}` : t.method} <span className="text-slate-500">{[t.ref, t.note].filter(Boolean).join(" · ")}</span></div>
                  <ProofThumbs ids={t.proof_ids} />
                </li>
              ))}
            </ul>
            <table className="hidden w-full sm:table">
              <thead className="sticky top-0"><tr><th className="th">Date</th><th className="th">Entry</th><th className="th">Details</th><th className="th text-right">Purchased</th><th className="th text-right">Paid</th><th className="th text-right">We owe</th></tr></thead>
              <tbody>{s.lines.map((t: any) => (
                <tr key={t.id}>
                  <td className="td text-xs">{dt(t.txn_date)}</td>
                  <td className="td"><Badge tone={t.type === "purchase" ? "blue" : t.type === "payment" ? "green" : "violet"}>{t.type}</Badge></td>
                  <td className="td text-xs">{t.product ? `${num(t.litres)} L ${PRODUCTS[t.product]} @ Rs ${t.rate}` : t.method} <span className="text-slate-500">{[t.ref, t.note].filter(Boolean).join(" · ")}</span> <ProofThumbs ids={t.proof_ids} /></td>
                  <td className="td text-right tabular-nums">{t.debit ? pkr(t.debit) : ""}</td>
                  <td className="td text-right tabular-nums text-emerald-700">{t.credit ? pkr(t.credit) : ""}</td>
                  <td className="td text-right font-medium tabular-nums">{pkr(t.balance)}</td>
                </tr>
              ))}</tbody>
            </table>
            {!s.lines.length && <Empty>No entries</Empty>}
          </div>
        </div>
      )}
    </Modal>
  );
}
