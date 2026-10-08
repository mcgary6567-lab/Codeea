import { useState } from "react";
import { Landmark, Calculator, ArrowDownCircle, ArrowUpCircle, Printer } from "lucide-react";
import { Download } from "lucide-react";
import { api, linkToken, useApi } from "../lib/api";
import { Field, Loading, Modal, PageHeader, Stat, useAction } from "../components/ui";
import { dt, pkr } from "../lib/format";
import { PhotoButton, PhotoThumb, ProofPhotos, ProofThumbs } from "../components/Capture";
import { AccountPicker, BankAccounts, BankLogo, BankNamePicker } from "../components/BankParts";
import { useAuth } from "../App";
import { toWords } from "../lib/words";
import { PrintFooter, PrintHeader } from "../components/Letterhead";
import { Ur } from "../components/VoiceShell";

const IN: Record<string, string> = { shift_cash: "Cash handed over from shifts", khata_cash: "Khata payments in cash", wholesale_cash: "Wholesale payments in cash", prepaid_cash: "Coupons sold & wallet deposits (cash)", staff_repaid: "Staff advances paid back", bank_withdrawals: "Cash taken out of bank", other_cash: "Other money in (cash counter)" };
const OUT: Record<string, string> = { bank_deposits: "Deposited in bank", expenses: "Cash expenses (office)", supplier_payments: "Supplier paid in cash", staff_advances: "Staff advances / bonus", other_cash: "Other payments (cash counter)" };

/** Office cash book: what should be in the drawer now, bank deposits and cash counts — no cash register on paper. */
export default function Cash() {
  const { data, reload } = useApi<any>("/cash");
  const [form, setForm] = useState<null | "count" | "deposit">(null);
  const [slip, setSlip] = useState<any>(null);
  const { can } = useAuth();
  if (!data) return <Loading />;
  return (
    <div className="space-y-5">
      <PageHeader title="Cash & bank" subtitle="Office cash worked out from shifts, payments, expenses and bank deposits"
        actions={<>
          <a className="btn-secondary" href={`/api/cash.csv?token=${linkToken()}`}><Download size={15} /> Excel / CSV</a>
          <button className="btn-secondary" onClick={() => setForm("count")}><Calculator size={15} /> Count cash</button>
          <button className="btn-primary" onClick={() => setForm("deposit")}><Landmark size={15} /> Bank deposit</button>
        </>} />
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="Cash in hand (should be)" value={pkr(data.cash_in_hand)} tone="green"
          hint={data.last_count ? `Last counted ${pkr(data.last_count.amount)} by ${data.last_count.by}, ${dt(data.last_count.at)}` : "Count the cash once to start the book"} />
        <Stat label="Came in since then" value={pkr(data.total_in)} icon={<ArrowDownCircle size={16} />} tone="blue" />
        <Stat label="Went out since then" value={pkr(data.total_out)} icon={<ArrowUpCircle size={16} />} tone="amber" />
      </div>
      {can("bank.view") && <BankAccounts cashInHand={data.cash_in_hand} onChanged={reload} />}
      <div className="grid gap-5 lg:grid-cols-2">
        <div className="card p-4">
          <h2 className="mb-2 font-semibold">Cash movement since the last count</h2>
          {Object.entries(IN).filter(([k]) => k in data.ins).map(([k, l]) => <Line key={k} k={`+ ${l}`} v={data.ins[k]} />)}
          {Object.entries(OUT).filter(([k]) => k in data.outs).map(([k, l]) => <Line key={k} k={`− ${l}`} v={data.outs[k]} neg />)}
          <div className="mt-2 flex justify-between border-t-2 border-slate-800 pt-2 text-lg font-bold"><span>Cash in hand</span><span className="tabular-nums">{pkr(data.cash_in_hand)}</span></div>
        </div>
        <div className="card p-4">
          <h2 className="mb-2 font-semibold">Bank deposits</h2>
          <ul className="divide-y divide-slate-100 text-sm">
            {data.deposits.map((d: any) => (
              <li key={d.id} className="flex items-center gap-2 py-2">
                <BankLogo name={d.bank} size={32} />
                <span className="min-w-0 flex-1"><b>{d.bank}</b>{d.slip_ref ? ` · slip ${d.slip_ref}` : ""}<span className="block text-xs text-slate-500">{dt(d.created_at)} · {d.deposited_by}</span></span>
                {d.photo_id && <PhotoThumb id={d.photo_id} size={7} />}
                <span className="font-semibold tabular-nums">{pkr(d.amount)}</span>
                <button className="btn-secondary min-h-9 !px-2 !py-1" aria-label="Print deposit slip" title="Print deposit slip" onClick={() => setSlip(d)}><Printer size={14} /></button>
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
      {form && <CashForm kind={form} inHand={data.cash_in_hand} onClose={() => setForm(null)} onDone={(r) => { setForm(null); reload(); if (r?.deposit) setSlip(r.deposit); }} />}
      {slip && <DepositSlip d={slip} onClose={() => setSlip(null)} />}
    </div>
  );
}

const Line = ({ k, v, neg }: { k: string; v: number; neg?: boolean }) => (
  <div className="flex justify-between border-b border-slate-100 py-1.5 text-sm"><span className="text-slate-600">{k}</span><span className={`tabular-nums ${neg && v ? "text-red-600" : ""}`}>{pkr(v)}</span></div>
);

export function CashForm({ kind, inHand, onClose, onDone }: { kind: "count" | "deposit"; inHand: number; onClose: () => void; onDone: (r?: any) => void }) {
  const [photos, setPhotos] = useState<number[]>([]);
  const [f, setF] = useState({ amount: kind === "deposit" ? String(Math.max(0, Math.floor(inHand / 1000) * 1000)) : "", bank: "", slip_ref: "", note: "", photo_id: null as number | null });
  const [account, setAccount] = useState<number | null>(null);
  const { data: picks } = useApi<any>(kind === "deposit" ? "/bank/accounts/pick" : null);
  const hasAccounts = (picks?.accounts?.length ?? 0) > 0;
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={kind === "count" ? "Count the office cash" : "Cash deposited in bank"}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const r = kind === "count"
          ? await run(() => api("/cash/count", { body: { amount: Number(f.amount), note: f.note || null, photo_ids: photos } }), (x: any) => x.variance ? `Counted. ${x.variance < 0 ? "Short" : "Over"} ${pkr(Math.abs(x.variance))} against the book` : "Counted — matches the book")
          : await run(() => api("/cash/deposits", { body: { amount: Number(f.amount), account_id: hasAccounts ? account : null, bank: hasAccounts ? null : f.bank, slip_ref: f.slip_ref || null, photo_id: f.photo_id, note: f.note || null } }), "Deposit saved");
        if (r) onDone(r);
      }}>
        {kind === "deposit" && <div className="flex items-center gap-2 rounded-lg bg-sky-50 p-2 text-sm">
          <PhotoButton kind="receipt" label="Photo of deposit slip" onRead={(r, id) => setF((x) => ({ ...x, photo_id: id, amount: r?.amount ? String(r.amount) : x.amount }))} />
          <span className="flex items-center gap-2 text-slate-600">{f.photo_id ? <><PhotoThumb id={f.photo_id} size={10} /> Slip attached</> : "Keep the bank slip as proof"}</span>
        </div>}
        <Field label={kind === "count" ? "Cash counted (Rs)" : "Amount deposited (Rs)"}><input className="input py-3 text-2xl" type="number" min={0} required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
        {kind === "count" && <p className="text-sm text-slate-600">The book says <b>{pkr(inHand)}</b> should be in hand.</p>}
        {kind === "deposit" && <>
          {hasAccounts ? <AccountPicker method="bank" required label="Deposited in which account? · کس اکاؤنٹ میں" value={account} onChange={setAccount} />
            : <div><span className="label">Bank · بینک</span><BankNamePicker required value={f.bank} onChange={(bank) => setF({ ...f, bank })} /></div>}
          <Field label="Slip no."><input className="input" value={f.slip_ref} onChange={(e) => setF({ ...f, slip_ref: e.target.value })} /></Field>
        </>}
        <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        {kind === "count" && <ProofPhotos value={photos} onChange={setPhotos} hint="counted notes / cash register" />}
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

/** Paying-in slip for a cash deposit: two copies on one A4 (bank copy, our copy), notes table left blank to fill by hand. */
export function DepositSlip({ d, onClose }: { d: any; onClose: () => void }) {
  const { tenant } = useAuth();
  const NOTES = [5000, 1000, 500, 100, 50, 20, 10];
  const body = (copy: string) => (
    <div className="space-y-3 text-sm print:space-y-1.5">
      <div className="text-center">
        <div className="font-semibold">Cash deposit slip · <Ur>بینک جمع پرچی</Ur></div>
        <div className="text-xs text-slate-500">DS-{String(d.id).padStart(5, "0")} · {dt(d.created_at)}</div>
        <span className="mt-1 hidden rounded border border-slate-800 px-2 text-[10px] font-bold uppercase tracking-wide print:inline-block">{copy}</span>
      </div>
      <dl className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1 print:gap-y-0.5">
        <dt className="text-slate-500">Bank</dt><dd className="font-medium">{d.acc_bank ?? d.bank}{d.acc_branch ? ` · ${d.acc_branch}` : ""}</dd>
        <dt className="text-slate-500">Account title</dt><dd className="font-medium">{d.acc_title || tenant?.name}</dd>
        <dt className="text-slate-500">Account no.</dt><dd className="font-mono font-semibold tracking-wide">{d.acc_no || "________________________"}</dd>
        <dt className="text-slate-500">Deposited by</dt><dd>{d.deposited_by}{d.station_name ? ` · ${d.station_name}` : ""}</dd>
        {d.slip_ref && <><dt className="text-slate-500">Bank slip no.</dt><dd>{d.slip_ref}</dd></>}
        {d.note && <><dt className="text-slate-500">Note</dt><dd>{d.note}</dd></>}
      </dl>
      <div className="rounded-xl bg-emerald-50 p-3 text-center print:px-2 print:py-1">
        <div className="text-3xl font-bold tabular-nums print:text-xl">{pkr(d.amount)}</div>
        <div className="text-xs text-slate-600">{toWords(d.amount)}</div>
      </div>
      {/* notes count, filled in by hand at the bank counter */}
      <div className="grid grid-cols-2 gap-1.5 text-xs sm:grid-cols-4 print:grid-cols-4">
        {[...NOTES.map((n) => `Rs ${n.toLocaleString()}`), "Coins"].map((n) => (
          <div key={n} className="flex items-end gap-1 rounded border border-slate-300 px-2 py-1.5 print:py-1"><span className="shrink-0 text-slate-600">{n} ×</span><span className="flex-1 border-b border-dotted border-slate-400">&nbsp;</span></div>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-6 pt-6 text-center text-xs text-slate-500 print:pt-4">
        <div className="border-t border-slate-400 pt-1">Depositor · <Ur>جمع کنندہ</Ur></div>
        <div className="border-t border-slate-400 pt-1">Bank cashier &amp; stamp · <Ur>بینک مہر</Ur></div>
      </div>
    </div>
  );
  return (
    <Modal open onClose={onClose} title={`Deposit slip DS-${String(d.id).padStart(5, "0")}`}>
      <div className="voucher-2up">
        <div className="print:break-inside-avoid"><PrintHeader />{body("Bank copy · بینک کی کاپی")}<PrintFooter /></div>
        <div className="my-3 hidden border-t-2 border-dashed border-slate-400 pt-1 text-center text-[10px] text-slate-500 print:block">✂ cut here · یہاں سے کاٹیں</div>
        <div className="hidden print:block print:break-inside-avoid"><PrintHeader />{body("Our copy · ہماری کاپی")}<PrintFooter /></div>
      </div>
      <div className="mt-4 flex justify-end gap-2 print:hidden">
        <button className="btn-secondary" onClick={onClose}>Close · <Ur>بند</Ur></button>
        <button className="btn-primary" onClick={() => window.print()}><Printer size={15} /> Print 2 copies · <Ur>پرنٹ</Ur></button>
      </div>
    </Modal>
  );
}
