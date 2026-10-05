import { useState } from "react";
import { Landmark, Calculator, ArrowDownCircle, ArrowUpCircle } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Field, Loading, Modal, PageHeader, Stat, useAction } from "../components/ui";
import { dt, pkr } from "../lib/format";
import { PhotoButton, photoUrl, ProofPhotos, ProofThumbs } from "../components/Capture";

const IN: Record<string, string> = { shift_cash: "Cash handed over from shifts", khata_cash: "Khata payments in cash", wholesale_cash: "Wholesale payments in cash", prepaid_cash: "Coupons sold & wallet deposits (cash)", staff_repaid: "Staff advances paid back" };
const OUT: Record<string, string> = { bank_deposits: "Deposited in bank", expenses: "Cash expenses (office)", supplier_payments: "Supplier paid in cash", staff_advances: "Staff advances / bonus" };

/** Office cash book: what should be in the drawer now, bank deposits and cash counts — no cash register on paper. */
export default function Cash() {
  const { data, reload } = useApi<any>("/cash");
  const [form, setForm] = useState<null | "count" | "deposit">(null);
  if (!data) return <Loading />;
  return (
    <div className="space-y-5">
      <PageHeader title="Cash & bank" subtitle="Office cash worked out from shifts, payments, expenses and bank deposits"
        actions={<>
          <button className="btn-secondary" onClick={() => setForm("count")}><Calculator size={15} /> Count cash</button>
          <button className="btn-primary" onClick={() => setForm("deposit")}><Landmark size={15} /> Bank deposit</button>
        </>} />
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="Cash in hand (should be)" value={pkr(data.cash_in_hand)} tone="green"
          hint={data.last_count ? `Last counted ${pkr(data.last_count.amount)} by ${data.last_count.by}, ${dt(data.last_count.at)}` : "Count the cash once to start the book"} />
        <Stat label="Came in since then" value={pkr(data.total_in)} icon={<ArrowDownCircle size={16} />} tone="blue" />
        <Stat label="Went out since then" value={pkr(data.total_out)} icon={<ArrowUpCircle size={16} />} tone="amber" />
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <div className="card p-4">
          <h2 className="mb-2 font-semibold">Cash movement since the last count</h2>
          {Object.entries(IN).map(([k, l]) => <Line key={k} k={`+ ${l}`} v={data.ins[k]} />)}
          {Object.entries(OUT).map(([k, l]) => <Line key={k} k={`− ${l}`} v={data.outs[k]} neg />)}
          <div className="mt-2 flex justify-between border-t-2 border-slate-800 pt-2 text-lg font-bold"><span>Cash in hand</span><span className="tabular-nums">{pkr(data.cash_in_hand)}</span></div>
        </div>
        <div className="card p-4">
          <h2 className="mb-2 font-semibold">Bank deposits</h2>
          <ul className="divide-y divide-slate-100 text-sm">
            {data.deposits.map((d: any) => (
              <li key={d.id} className="flex items-center gap-2 py-2">
                <span className="flex-1"><b>{d.bank}</b>{d.slip_ref ? ` · slip ${d.slip_ref}` : ""}<span className="block text-xs text-slate-500">{dt(d.created_at)} · {d.deposited_by}</span></span>
                {d.photo_id && <a href={photoUrl(d.photo_id)} target="_blank" rel="noreferrer" className="text-sky-700" aria-label="Deposit slip photo">📷</a>}
                <span className="font-semibold tabular-nums">{pkr(d.amount)}</span>
              </li>
            ))}
            {!data.deposits.length && <li className="py-4 text-center text-slate-500">No deposits yet</li>}
          </ul>
          <h2 className="mb-2 mt-4 font-semibold">Cash counts</h2>
          <ul className="divide-y divide-slate-100 text-sm">
            {data.counts.map((c: any) => (
              <li key={c.id} className="flex justify-between py-1.5"><span>{dt(c.created_at)} · {c.counted_by}</span>
                <span className="tabular-nums">{pkr(c.amount)} {c.variance ? <span className={c.variance < 0 ? "text-red-600" : "text-emerald-700"}>({c.variance < 0 ? "short" : "over"} {pkr(Math.abs(c.variance))})</span> : null} <ProofThumbs ids={c.proof_ids} /></span></li>
            ))}
          </ul>
        </div>
      </div>
      {form && <CashForm kind={form} inHand={data.cash_in_hand} onClose={() => setForm(null)} onDone={() => { setForm(null); reload(); }} />}
    </div>
  );
}

const Line = ({ k, v, neg }: { k: string; v: number; neg?: boolean }) => (
  <div className="flex justify-between border-b border-slate-100 py-1.5 text-sm"><span className="text-slate-600">{k}</span><span className={`tabular-nums ${neg && v ? "text-red-600" : ""}`}>{pkr(v)}</span></div>
);

function CashForm({ kind, inHand, onClose, onDone }: { kind: "count" | "deposit"; inHand: number; onClose: () => void; onDone: () => void }) {
  const [photos, setPhotos] = useState<number[]>([]);
  const [f, setF] = useState({ amount: kind === "deposit" ? String(Math.max(0, Math.floor(inHand / 1000) * 1000)) : "", bank: "", slip_ref: "", note: "", photo_id: null as number | null });
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={kind === "count" ? "Count the office cash" : "Cash deposited in bank"}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const r = kind === "count"
          ? await run(() => api("/cash/count", { body: { amount: Number(f.amount), note: f.note || null, photo_ids: photos } }), (x: any) => x.variance ? `Counted. ${x.variance < 0 ? "Short" : "Over"} ${pkr(Math.abs(x.variance))} against the book` : "Counted — matches the book")
          : await run(() => api("/cash/deposits", { body: { amount: Number(f.amount), bank: f.bank, slip_ref: f.slip_ref || null, photo_id: f.photo_id, note: f.note || null } }), "Deposit saved");
        if (r) onDone();
      }}>
        {kind === "deposit" && <div className="flex items-center gap-2 rounded-lg bg-sky-50 p-2 text-sm">
          <PhotoButton kind="receipt" label="Photo of deposit slip" onRead={(r, id) => setF((x) => ({ ...x, photo_id: id, amount: r?.amount ? String(r.amount) : x.amount, bank: r?.paid_to ?? x.bank }))} />
          <span className="text-slate-600">{f.photo_id ? "📷 Slip attached" : "Keep the bank slip as proof"}</span>
        </div>}
        <Field label={kind === "count" ? "Cash counted (Rs)" : "Amount deposited (Rs)"}><input className="input py-3 text-2xl" type="number" min={0} required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
        {kind === "count" && <p className="text-sm text-slate-600">The book says <b>{pkr(inHand)}</b> should be in hand.</p>}
        {kind === "deposit" && <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Bank"><input className="input" required placeholder="e.g. HBL Ferozepur Road" value={f.bank} onChange={(e) => setF({ ...f, bank: e.target.value })} /></Field>
          <Field label="Slip no."><input className="input" value={f.slip_ref} onChange={(e) => setF({ ...f, slip_ref: e.target.value })} /></Field>
        </div>}
        <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        {kind === "count" && <ProofPhotos value={photos} onChange={setPhotos} hint="counted notes / cash register" />}
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}
