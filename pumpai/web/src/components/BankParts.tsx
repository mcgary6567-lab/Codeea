import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeftRight, Banknote, Building2, Landmark, Pencil, Plus, Printer, Trash2, AlertTriangle, CreditCard, Zap, Smartphone } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Field, Loading, Modal, useAction } from "./ui";
import { dt, pkr } from "../lib/format";
import { PK_BANKS, ALL_PK_BANKS, bankInfo, logoUrl } from "../lib/banks";
import { ProofPhotos, ProofThumbs } from "./Capture";
import { useAuth } from "../App";

const Ur = ({ children }: { children: React.ReactNode }) => <span lang="ur" dir="rtl" className="font-urdu">{children}</span>;
const isCash = (m?: string | null) => !m || /^cash$/i.test(m.trim());
const today = () => new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10);

/** The bank's logo; a badge in the bank's colour with its short name when the logo can't load (offline). */
export function BankLogo({ name, size = 40 }: { name?: string | null; size?: number }) {
  const b = bankInfo(name);
  const [failed, setFailed] = useState(false);
  const box = { width: size, height: size };
  if (b && !failed) return (
    <span style={box} className="flex shrink-0 items-center justify-center overflow-hidden rounded-xl bg-white ring-1 ring-slate-200">
      <img src={logoUrl(b)} alt={b.short} loading="lazy" referrerPolicy="no-referrer" style={{ width: size * 0.72, height: size * 0.72 }} className="object-contain"
        onError={() => setFailed(true)} onLoad={(e) => { if ((e.target as HTMLImageElement).naturalWidth <= 16 && size > 24) setFailed(true); }} />
    </span>
  );
  const short = b?.short ?? ((name ?? "").split(/\s+/).filter((w) => /^[A-Z]/.test(w)).map((w) => w[0]).join("").slice(0, 3) || "B");
  return (
    <span style={{ ...box, background: b?.color ?? "#0f766e", fontSize: Math.max(9, size * (short.length > 3 ? 0.24 : 0.3)) }}
      className="flex shrink-0 items-center justify-center rounded-xl font-bold leading-none text-white">{short}</span>
  );
}

/** Pick a bank from the list of Pakistani banks, with logos and search; "Other" lets you type one. */
export function BankNamePicker({ value, onChange, required }: { value: string; onChange: (v: string) => void; required?: boolean }) {
  const [open, setOpen] = useState(!value);
  const [q, setQ] = useState("");
  const [other, setOther] = useState(!!value && !ALL_PK_BANKS.includes(value));
  if (!open && value) return (
    <div className="flex items-center gap-3 rounded-xl bg-slate-50 p-2 ring-1 ring-slate-200">
      <BankLogo name={value} size={40} /><span className="flex-1 font-semibold">{value}</span>
      <button type="button" className="btn-secondary !py-1.5 text-sm" onClick={() => setOpen(true)}>Change</button>
    </div>
  );
  const term = q.trim().toLowerCase();
  return (
    <div className="space-y-2 rounded-xl p-2 ring-1 ring-slate-200">
      <input className="sr-only" tabIndex={-1} required={required} value={value} onChange={() => {}} aria-hidden />
      <input className="input" placeholder="Search bank · بینک تلاش کریں" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="max-h-72 space-y-3 overflow-y-auto pr-1">
        {PK_BANKS.map((g) => {
          const list = g.banks.filter((b) => !term || b.name.toLowerCase().includes(term) || b.short.toLowerCase().includes(term));
          if (!list.length) return null;
          return (
            <div key={g.group}>
              <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{g.group}</div>
              <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
                {list.map((b) => (
                  <button type="button" key={b.name} onClick={() => { onChange(b.name); setOther(false); setOpen(false); setQ(""); }}
                    className={`flex min-w-0 items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-slate-50 ${value === b.name ? "bg-brand-50 ring-2 ring-brand-500" : "ring-1 ring-slate-200"}`}>
                    <BankLogo name={b.name} size={28} /><span className="min-w-0 leading-tight">{b.name}</span>
                  </button>
                ))}
              </div>
            </div>
          );
        })}
      </div>
      {other ? <div className="flex gap-2"><input className="input" autoFocus placeholder="Bank name" value={value} onChange={(e) => onChange(e.target.value)} />
        {value && <button type="button" className="btn-primary" onClick={() => setOpen(false)}>OK</button>}</div>
        : <button type="button" className="py-2 text-sm text-brand-700 underline" onClick={() => { setOther(true); onChange(""); }}>Bank not in the list? Type its name</button>}
    </div>
  );
}

/**
 * "Which of our bank accounts?" on a payment form — big tiles with the bank's logo. Hidden for cash.
 * Choosing it keeps every bank's balance right; leaving it empty still saves (listed under "not linked").
 */
export function AccountPicker({ value, onChange, method, label, required }: { value: number | null; onChange: (id: number | null) => void; method?: string | null; label?: string; required?: boolean }) {
  const { data } = useApi<any>("/bank/accounts/pick");
  const { can } = useAuth();
  const list: any[] = data?.accounts ?? [];
  // only one account: choose it by itself
  useEffect(() => { if (!isCash(method) && value == null && list.length === 1) onChange(list[0].id); }, [method, list.length]);
  if (isCash(method)) return null;
  if (data && !list.length) return (
    <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">
      No bank accounts added yet{can("bank.manage") ? <> — <Link className="font-semibold text-brand-700 underline" to="/cash?bank=add">add your banks</Link></> : " — ask the owner to add them in Cash & bank"}, so each bank's balance can be kept.
    </p>
  );
  return (
    <fieldset>
      <legend className="label">{label ?? "Into which bank account? · کس بینک میں"}</legend>
      <input className="sr-only" tabIndex={-1} required={required} value={value ?? ""} onChange={() => {}} aria-hidden />
      <div className="grid gap-2 sm:grid-cols-2">
        {list.map((a) => (
          <button type="button" key={a.id} onClick={() => onChange(value === a.id && !required ? null : a.id)} aria-pressed={value === a.id}
            className={`flex min-w-0 items-center gap-2.5 rounded-xl px-3 py-2 text-left text-sm transition ${value === a.id ? "bg-brand-50 ring-2 ring-brand-600" : "bg-white ring-1 ring-slate-200 hover:bg-slate-50"}`}>
            <BankLogo name={a.bank} size={34} /><span className="min-w-0 flex-1 leading-tight"><span className="block font-semibold">{a.bank}</span>
              <span className="block text-xs text-slate-500">{a.name.slice(a.bank.length).trim() || (a.kind === "wallet" ? "Wallet" : "Account")}</span></span>
            {value === a.id && <span className="text-brand-700">✓</span>}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

const KINDS = [
  { k: "withdraw", en: "Cash taken out of bank", ur: "بینک سے نقد نکالا", sign: -1 },
  { k: "charges", en: "Bank charges / tax", ur: "بینک چارجز", sign: -1 },
  { k: "owner_out", en: "Owner took money", ur: "مالک نے رقم لی", sign: -1 },
  { k: "other_out", en: "Other payment out", ur: "دوسری ادائیگی", sign: -1 },
  { k: "profit", en: "Bank profit", ur: "بینک منافع", sign: 1 },
  { k: "owner_in", en: "Owner put money in", ur: "مالک نے رقم ڈالی", sign: 1 },
  { k: "other_in", en: "Other money in", ur: "دوسری آمدن", sign: 1 },
];
const POS = [
  { k: "card", en: "Card machine", icon: CreditCard }, { k: "raast", en: "Raast / QR", icon: Zap },
  { k: "easypaisa", en: "Easypaisa", icon: Smartphone }, { k: "jazzcash", en: "JazzCash", icon: Smartphone },
];

/** Cash & bank page: how much is in each bank, with statements and bank-only entries. */
export function BankAccounts({ cashInHand, onChanged }: { cashInHand: number; onChanged: () => void }) {
  const { can } = useAuth();
  const { data, reload } = useApi<any>("/bank/accounts");
  const [form, setForm] = useState<null | { kind: "account"; acc?: any } | { kind: "entry"; acc?: any } | { kind: "transfer" } | { kind: "statement"; acc: any }>(null);
  const [showClosed, setShowClosed] = useState(false);
  const manage = can("bank.manage");
  useEffect(() => { if (manage && new URLSearchParams(location.search).get("bank") === "add") setForm({ kind: "account" }); }, []);
  if (!data) return <Loading />;
  const open = data.accounts.filter((a: any) => a.active);
  const closed = data.accounts.filter((a: any) => !a.active);
  const done = () => { setForm(null); reload(); onChanged(); };
  const u = data.unlinked;
  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 bg-gradient-to-r from-brand-700 to-brand-600 px-4 py-4 text-white">
        <Landmark size={22} />
        <div className="flex-1">
          <div className="text-sm opacity-90">Money in banks · <Ur>بینکوں میں رقم</Ur></div>
          <div className="text-3xl font-bold tabular-nums">{pkr(data.total)}</div>
        </div>
        <div className="text-right text-sm">
          <div className="opacity-90">Cash in hand + banks</div>
          <div className="text-xl font-bold tabular-nums">{pkr(data.total + cashInHand)}</div>
        </div>
      </div>
      {manage && <div className="flex flex-wrap gap-2 border-b border-slate-100 px-4 py-3">
        <button className="btn-primary" onClick={() => setForm({ kind: "account" })}><Plus size={15} /> Add bank account</button>
        <button className="btn-secondary" disabled={!open.length} onClick={() => setForm({ kind: "entry", acc: { kind: "withdraw" } })}><Banknote size={15} /> Cash out of bank</button>
        <button className="btn-secondary" disabled={open.length < 2} onClick={() => setForm({ kind: "transfer" })}><ArrowLeftRight size={15} /> Transfer</button>
        <button className="btn-secondary" disabled={!open.length} onClick={() => setForm({ kind: "entry" })}><Pencil size={15} /> Charges / profit / other</button>
      </div>}
      {!open.length ? (
        <div className="p-6 text-center text-slate-600">
          <Building2 className="mx-auto mb-2 text-slate-400" size={32} />
          No bank accounts yet. {manage ? "Add each bank account once with today's balance — after that the system keeps every bank's balance by itself." : "Ask the owner to add them."}
        </div>
      ) : (
        <div className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-3">
          {open.map((a: any) => (
            <button key={a.id} onClick={() => setForm({ kind: "statement", acc: a })} className="min-w-0 rounded-2xl p-4 text-left ring-1 ring-slate-200 transition hover:bg-slate-50 hover:ring-brand-300">
              <div className="flex items-start gap-3">
                <BankLogo name={a.bank} size={44} />
                <span className="min-w-0 flex-1">
                  <span className="block font-semibold">{a.bank}</span>
                  <span className="block break-words text-xs text-slate-500">{[a.branch, a.account_no && `A/C ${a.account_no}`, a.title].filter(Boolean).join(" · ") || (a.kind === "wallet" ? "Mobile wallet" : "Account")}</span>
                </span>
              </div>
              <div className={`mt-3 text-2xl font-bold tabular-nums ${a.balance < 0 ? "text-red-600" : "text-slate-900"}`}>{pkr(a.balance)}</div>
              <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-slate-500">
                <span className="whitespace-nowrap text-emerald-700">▲ {pkr(a.month_in)}</span><span className="whitespace-nowrap text-red-600">▼ {pkr(a.month_out)}</span><span className="whitespace-nowrap">this month</span>
              </div>
            </button>
          ))}
        </div>
      )}
      {(u.received > 0 || u.paid > 0 || u.pos > 0) && open.length > 0 && (
        <div className="mx-4 mb-4 flex gap-2 rounded-xl bg-amber-50 p-3 text-sm text-amber-900 ring-1 ring-amber-200">
          <AlertTriangle size={18} className="mt-0.5 shrink-0" />
          <div>
            <b>Not linked to a bank (last 30 days):</b>{" "}
            {[u.received > 0 && `${pkr(u.received)} received by bank / online`, u.paid > 0 && `${pkr(u.paid)} paid by bank / cheque`, u.pos > 0 && `${pkr(u.pos)} POS ${u.pos_methods.join(" / ")} sales`].filter(Boolean).join(" · ")}.
            <span className="block text-xs">Choose the bank account on new payments{u.pos > 0 && manage ? ", and link the POS payment methods below" : ""} so each bank's balance matches the bank.</span>
          </div>
        </div>
      )}
      {open.length > 0 && (
        <div className="border-t border-slate-100 px-4 py-3">
          <div className="mb-2 text-sm font-semibold">POS sales go into · <Ur>پی او ایس کی رقم کس بینک میں</Ur></div>
          <div className="grid gap-2 sm:grid-cols-2">
            {POS.map((p) => (
              <label key={p.k} className="flex flex-wrap items-center gap-2 rounded-xl bg-slate-50 px-3 py-2 text-sm">
                <p.icon size={16} className="shrink-0 text-slate-500" /><span className="shrink-0 sm:w-28">{p.en}</span>
                <select className="input min-w-0 flex-[1_1_100%] !py-1.5 text-sm sm:flex-1" disabled={!manage} value={data.pos_map[p.k] ?? ""}
                  onChange={async (e) => { await api("/bank/pos-map", { method: "PUT", body: { [p.k]: e.target.value ? Number(e.target.value) : null } }); reload(); }}>
                  <option value="">— not linked —</option>
                  {open.map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              </label>
            ))}
          </div>
        </div>
      )}
      {closed.length > 0 && (
        <div className="border-t border-slate-100 px-4 py-2 text-sm">
          <button className="text-slate-500 underline" onClick={() => setShowClosed(!showClosed)}>{showClosed ? "Hide" : "Show"} {closed.length} closed account{closed.length === 1 ? "" : "s"}</button>
          {showClosed && <ul className="mt-2 divide-y divide-slate-100">{closed.map((a: any) => (
            <li key={a.id} className="flex items-center justify-between py-1.5"><span>{a.name}</span><span className="flex items-center gap-3 tabular-nums">{pkr(a.balance)}
              <button className="text-brand-700 underline" onClick={() => setForm({ kind: "statement", acc: a })}>Statement</button></span></li>))}</ul>}
        </div>
      )}
      {form?.kind === "account" && <AccountForm acc={form.acc} onClose={() => setForm(null)} onDone={done} />}
      {form?.kind === "entry" && <EntryForm accounts={open} start={form.acc?.kind} onClose={() => setForm(null)} onDone={done} />}
      {form?.kind === "transfer" && <TransferForm accounts={open} onClose={() => setForm(null)} onDone={done} />}
      {form?.kind === "statement" && <Statement acc={form.acc} manage={manage} onEdit={() => setForm({ kind: "account", acc: form.acc })} onClose={() => setForm(null)} onChanged={() => { reload(); onChanged(); }} />}
    </div>
  );
}

function AccountForm({ acc, onClose, onDone }: { acc?: any; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ bank: acc?.bank ?? "", branch: acc?.branch ?? "", title: acc?.title ?? "", account_no: acc?.account_no ?? "", kind: acc?.kind ?? "current",
    opening_balance: acc ? String(acc.opening_balance) : "", opening_date: acc?.opening_date ?? today(), note: acc?.note ?? "", active: acc ? Boolean(acc.active) : true });
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={acc ? `Edit ${acc.bank}` : "Add bank account · بینک اکاؤنٹ"}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const body = { ...f, opening_balance: Number(f.opening_balance || 0), branch: f.branch || null, title: f.title || null, account_no: f.account_no || null, note: f.note || null };
        const r = await run(() => acc ? api(`/bank/accounts/${acc.id}`, { method: "PATCH", body }) : api("/bank/accounts", { body }), acc ? "Account saved" : "Bank account added");
        if (r) onDone();
      }}>
        <div><span className="label">Bank · بینک</span><BankNamePicker required value={f.bank} onChange={(bank) => setF({ ...f, bank, kind: /Easypaisa|JazzCash|SadaPay|NayaPay|UPaisa|Omni|Konnect/i.test(bank) ? "wallet" : f.kind })} /></div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Branch"><input className="input" placeholder="e.g. Ferozepur Road, Lahore" value={f.branch} onChange={(e) => setF({ ...f, branch: e.target.value })} /></Field>
          <Field label="Account no. / IBAN"><input className="input" value={f.account_no} onChange={(e) => setF({ ...f, account_no: e.target.value })} /></Field>
          <Field label="Account title"><input className="input" placeholder="e.g. Al-Madina Petroleum" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
          <Field label="Type"><select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}><option value="current">Current</option><option value="savings">Savings</option><option value="wallet">Mobile wallet</option></select></Field>
          <Field label="Balance in the bank (Rs)"><input className="input py-2.5 text-xl" type="number" step="0.01" required value={f.opening_balance} onChange={(e) => setF({ ...f, opening_balance: e.target.value })} /></Field>
          <Field label="…at the start of this day"><input className="input" type="date" required value={f.opening_date} onChange={(e) => setF({ ...f, opening_date: e.target.value })} /></Field>
        </div>
        <p className="text-xs text-slate-500">Write the balance from the bank statement / app for the start of that day. From then on, deposits, payments and POS card / Raast sales linked to this account are added by themselves.</p>
        {acc && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Account in use (untick when the account is closed)</label>}
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

function EntryForm({ accounts, start, onClose, onDone }: { accounts: any[]; start?: string; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ account_id: accounts.length === 1 ? String(accounts[0].id) : "", kind: start ?? "charges", amount: "", party: "", ref: "", note: "", txn_date: today() });
  const [photos, setPhotos] = useState<number[]>([]);
  const { busy, run } = useAction();
  const k = KINDS.find((x) => x.k === f.kind)!;
  return (
    <Modal open onClose={onClose} title={f.kind === "withdraw" ? "Cash taken out of bank" : "Bank entry"}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const r = await run(() => api("/bank/entries", { body: { ...f, account_id: Number(f.account_id), amount: Number(f.amount), party: f.party || null, ref: f.ref || null, note: f.note || null, photo_ids: photos } }),
          f.kind === "withdraw" ? "Saved — the cash is added to the office cash book" : "Saved");
        if (r) onDone();
      }}>
        <Field label="What happened?"><select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
          {KINDS.map((x) => <option key={x.k} value={x.k}>{x.sign > 0 ? "＋" : "−"} {x.en} · {x.ur}</option>)}</select></Field>
        <Field label="Bank account"><select className="input" required value={f.account_id} onChange={(e) => setF({ ...f, account_id: e.target.value })}>
          <option value="">— Choose —</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name} ({pkr(a.balance)})</option>)}</select></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Amount (Rs)"><input className="input py-2.5 text-xl" type="number" min={1} step="0.01" required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
          <Field label="Date"><input className="input" type="date" required value={f.txn_date} max={today()} onChange={(e) => setF({ ...f, txn_date: e.target.value })} /></Field>
          <Field label={f.kind === "withdraw" ? "Taken by" : "Paid to / from"}><input className="input" value={f.party} onChange={(e) => setF({ ...f, party: e.target.value })} /></Field>
          <Field label="Cheque / ref no."><input className="input" value={f.ref} onChange={(e) => setF({ ...f, ref: e.target.value })} /></Field>
        </div>
        <Field label="Note"><input className="input" placeholder={k.en} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        <ProofPhotos value={photos} onChange={setPhotos} hint="cheque, bank slip or statement line" />
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

function TransferForm({ accounts, onClose, onDone }: { accounts: any[]; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ from_id: "", to_id: "", amount: "", ref: "", note: "", txn_date: today() });
  const [photos, setPhotos] = useState<number[]>([]);
  const { busy, run } = useAction();
  const opts = (skip: string) => accounts.filter((a) => String(a.id) !== skip).map((a) => <option key={a.id} value={a.id}>{a.name} ({pkr(a.balance)})</option>);
  return (
    <Modal open onClose={onClose} title="Move money between banks · ٹرانسفر">
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const r = await run(() => api("/bank/transfers", { body: { from_id: Number(f.from_id), to_id: Number(f.to_id), amount: Number(f.amount), ref: f.ref || null, note: f.note || null, txn_date: f.txn_date, photo_ids: photos } }), "Transfer saved");
        if (r) onDone();
      }}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="From"><select className="input" required value={f.from_id} onChange={(e) => setF({ ...f, from_id: e.target.value })}><option value="">— Choose —</option>{opts(f.to_id)}</select></Field>
          <Field label="To"><select className="input" required value={f.to_id} onChange={(e) => setF({ ...f, to_id: e.target.value })}><option value="">— Choose —</option>{opts(f.from_id)}</select></Field>
          <Field label="Amount (Rs)"><input className="input py-2.5 text-xl" type="number" min={1} step="0.01" required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
          <Field label="Date"><input className="input" type="date" required value={f.txn_date} max={today()} onChange={(e) => setF({ ...f, txn_date: e.target.value })} /></Field>
          <Field label="IBFT / ref no."><input className="input" value={f.ref} onChange={(e) => setF({ ...f, ref: e.target.value })} /></Field>
          <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        </div>
        <ProofPhotos value={photos} onChange={setPhotos} hint="transfer receipt / screenshot" />
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

const KIND_TAG: Record<string, string> = { deposit: "Cash deposit", receipt: "Received", payment: "Paid", expense: "Expense", pos: "POS", transfer: "Transfer", withdraw: "Cash out", charges: "Charges", profit: "Profit", owner_in: "Owner in", owner_out: "Owner out", other_in: "Other in", other_out: "Other out" };

function Statement({ acc, manage, onEdit, onClose, onChanged }: { acc: any; manage: boolean; onEdit: () => void; onClose: () => void; onChanged: () => void }) {
  const [range, setRange] = useState({ from: `${today().slice(0, 7)}-01`, to: today() });
  const { data, reload } = useApi<any>(`/bank/accounts/${acc.id}/statement?from=${range.from}&to=${range.to}`);
  const { busy, run } = useAction();
  return (
    <Modal open wide onClose={onClose} title={acc.name}>
      <div className="space-y-3">
        <div className="flex items-center gap-3"><BankLogo name={acc.bank} size={48} />
          <div className="min-w-0 text-sm text-slate-600"><b className="block text-base text-slate-900">{acc.bank}</b>{[acc.branch, acc.account_no && `A/C ${acc.account_no}`, acc.title].filter(Boolean).join(" · ")}</div></div>
        <div className="grid grid-cols-2 items-end gap-2 print:hidden sm:flex sm:flex-wrap">
          <Field label="From"><input className="input" type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} /></Field>
          <Field label="To"><input className="input" type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} /></Field>
          <span className="hidden flex-1 sm:block" />
          <button className="btn-secondary" onClick={() => window.print()}><Printer size={15} /> Print</button>
          {manage && <button className="btn-secondary" onClick={onEdit}><Pencil size={15} /> Edit account</button>}
        </div>
        {!data ? <Loading /> : <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[["Opening", data.opening, ""], ["Money in", data.money_in, "text-emerald-700"], ["Money out", data.money_out, "text-red-600"], ["Closing", data.closing, "font-bold"]].map(([l, v, c]) => (
              <div key={l as string} className="rounded-xl bg-slate-50 p-3"><div className="text-xs text-slate-500">{l}</div><div className={`text-lg tabular-nums ${c}`}>{pkr(v as number)}</div></div>
            ))}
          </div>
          {/* phone: one card per line */}
          <ul className="divide-y divide-slate-100 rounded-xl ring-1 ring-slate-200 sm:hidden">
            {data.lines.map((l: any, i: number) => (
              <li key={i} className="flex gap-3 px-3 py-2.5 text-sm">
                <div className="min-w-0 flex-1">
                  <div className="text-xs text-slate-500">{dt(l.at)} · <span className="font-medium text-slate-600">{KIND_TAG[l.kind] ?? l.kind}</span></div>
                  <div className="break-words">{l.text} <ProofThumbs ids={l.proof_ids} /></div>
                </div>
                <div className="shrink-0 text-right tabular-nums">
                  <div className={`font-semibold ${l.amount > 0 ? "text-emerald-700" : "text-red-600"}`}>{l.amount > 0 ? "+" : "−"}{pkr(Math.abs(l.amount))}</div>
                  <div className="text-xs text-slate-500">{pkr(l.balance)}</div>
                </div>
              </li>
            ))}
            {!data.lines.length && <li className="py-6 text-center text-slate-500">Nothing in this period</li>}
          </ul>
          <div className="hidden max-h-[55vh] overflow-auto rounded-xl ring-1 ring-slate-200 sm:block">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-white"><tr><th className="th">Date</th><th className="th">Details</th><th className="th text-right">In</th><th className="th text-right">Out</th><th className="th text-right">Balance</th></tr></thead>
              <tbody>
                {data.lines.map((l: any, i: number) => (
                  <tr key={i} className="border-t border-slate-100">
                    <td className="td whitespace-nowrap text-xs text-slate-500">{dt(l.at)}</td>
                    <td className="td"><span className="mr-1 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-600">{KIND_TAG[l.kind] ?? l.kind}</span>{l.text}
                      {l.who && <span className="text-xs text-slate-400"> · {l.who}</span>} <ProofThumbs ids={l.proof_ids} />
                      {manage && l.txn_id && <button className="ml-1 align-middle text-slate-400 hover:text-red-600 print:hidden" title="Delete this entry" disabled={busy}
                        onClick={async () => { if (confirm(l.kind === "transfer" ? "Delete this transfer (both banks)?" : "Delete this bank entry?") && await run(() => api(`/bank/txns/${l.txn_id}`, { method: "DELETE" }), "Deleted")) { reload(); onChanged(); } }}><Trash2 size={14} /></button>}
                    </td>
                    <td className="td text-right tabular-nums text-emerald-700">{l.amount > 0 ? pkr(l.amount) : ""}</td>
                    <td className="td text-right tabular-nums text-red-600">{l.amount < 0 ? pkr(-l.amount) : ""}</td>
                    <td className="td text-right tabular-nums">{pkr(l.balance)}</td>
                  </tr>
                ))}
                {!data.lines.length && <tr><td colSpan={5} className="td py-6 text-center text-slate-500">Nothing in this period</td></tr>}
              </tbody>
            </table>
          </div>
        </>}
      </div>
    </Modal>
  );
}

/** Owner dashboard: how much is in each bank right now, plus cash in hand. */
export function BankSummary() {
  const { data } = useApi<any>("/bank/accounts", 120_000);
  const { can } = useAuth();
  if (!data) return null;
  const open = data.accounts.filter((a: any) => a.active);
  if (!open.length) return can("bank.manage") ? (
    <Link to="/cash?bank=add" className="card flex items-center gap-3 p-4 hover:bg-slate-50">
      <Landmark className="text-brand-600" /><span className="flex-1"><b>Add your bank accounts</b><span className="block text-sm text-slate-500">See how much is in each bank right here · <Ur>بینک اکاؤنٹ شامل کریں</Ur></span></span><Plus size={18} />
    </Link>
  ) : null;
  const top = Math.max(...open.map((a: any) => Math.abs(a.balance)), 1);
  const unlinked = data.unlinked.received + data.unlinked.paid + data.unlinked.pos;
  return (
    <div className="card overflow-hidden">
      <div className="grid grid-cols-2 gap-3 bg-gradient-to-r from-brand-700 to-brand-600 p-4 text-white sm:grid-cols-3">
        <div className="col-span-2 sm:col-span-1"><div className="flex items-center gap-1.5 text-sm opacity-90"><Landmark size={15} /> Money in banks · <Ur>بینکوں میں</Ur></div>
          <div className="text-3xl font-bold tabular-nums">{pkr(data.total)}</div></div>
        <div className="min-w-0"><div className="text-sm opacity-90">Cash in hand · <Ur>نقد</Ur></div><div className="whitespace-nowrap text-base font-bold tabular-nums sm:text-xl">{pkr(data.cash_in_hand)}</div></div>
        <div className="min-w-0"><div className="text-sm opacity-90">Total money · <Ur>کل رقم</Ur></div><div className="whitespace-nowrap text-base font-bold tabular-nums sm:text-xl">{pkr(data.total + data.cash_in_hand)}</div></div>
      </div>
      <ul className="divide-y divide-slate-100">
        {open.map((a: any) => (
          <li key={a.id}>
            <Link to="/cash" className="flex items-center gap-3 px-4 py-2.5 hover:bg-slate-50">
              <BankLogo name={a.bank} size={36} />
              <span className="min-w-0 flex-1">
                <span className="flex items-baseline justify-between gap-2"><span className="truncate font-semibold">{a.bank.replace(/\s*\(.*\)/, "")}</span>
                  <span className={`shrink-0 font-bold tabular-nums ${a.balance < 0 ? "text-red-600" : ""}`}>{pkr(a.balance)}</span></span>
                <span className="mt-1 block h-1.5 overflow-hidden rounded-full bg-slate-100"><span className={`block h-full rounded-full ${a.balance < 0 ? "bg-red-500" : "bg-brand-500"}`} style={{ width: `${Math.max(2, (Math.abs(a.balance) / top) * 100)}%` }} /></span>
                <span className="mt-0.5 block text-xs tabular-nums text-slate-500">
                  {a.today_in || a.today_out ? <>today <span className="text-emerald-700">+{pkr(a.today_in).replace("Rs ", "")}</span>{a.today_out ? <span className="text-red-600"> −{pkr(a.today_out).replace("Rs ", "")}</span> : null}</> : "no entry today"}
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 px-4 py-2 text-sm">
        {unlinked > 0 && <span className="flex items-center gap-1 text-amber-700"><AlertTriangle size={14} /> {pkr(unlinked)} not linked to a bank (30 days)</span>}
        <Link to="/cash" className="ml-auto inline-block py-2 font-medium text-brand-700 hover:underline">Statements & entries →</Link>
      </div>
    </div>
  );
}
