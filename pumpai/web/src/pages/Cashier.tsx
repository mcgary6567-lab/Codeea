import { useEffect, useMemo, useRef, useState } from "react";
import { PrintFooter, PrintHeader } from "../components/Letterhead";
import { Link, useSearchParams } from "react-router-dom";
import {
  ArrowDownCircle, ArrowUpCircle, Banknote, BookOpenText, Calculator, Check, ChevronLeft, ChevronRight, FileCheck2, HandCoins, Landmark, Printer, Search, Users, X,
} from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Empty, ErrorBox, Field, Loading, Modal, PageHeader, useAction } from "../components/ui";
import { ago, dt, phone, pkr, pkrShort } from "../lib/format";
import { toWords } from "../lib/words";
import { ProofPhotos, ProofThumbs } from "../components/Capture";
import { AccountPicker, BankAccounts, BankLogo, BankNamePicker, BankSummary } from "../components/BankParts";
import { Ur } from "../components/VoiceShell";
import { CashForm, DepositSlip } from "./Cash";
import { OnlineToday } from "../components/OnlineMoney";
import { useAuth } from "../App";

const today = () => new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10);
const TABS = [
  { k: "desk", en: "Desk", ur: "ڈیسک", perm: "cashier.desk" },
  { k: "receive", en: "Receive", ur: "وصولی", perm: "cash.receive" },
  { k: "pay", en: "Pay", ur: "ادائیگی", perm: "cash.pay" },
  { k: "cheques", en: "Cheques", ur: "چیک", perm: "cheques.manage" },
  { k: "handover", en: "Salesmen", ur: "سیلزمین کیش", perm: "shifts.handover" },
  { k: "daybook", en: "Day book", ur: "روزنامچہ", perm: "cashier.desk" },
  { k: "bank", en: "Bank", ur: "کیش اور بینک", perm: "cash.book" },
];

/** The cash counter: every rupee in and out, cheques, the salesmen's cash, banks — one screen for the cashier. */
export default function Cashier() {
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "desk";
  const go = (k: string, extra: Record<string, string> = {}) => setParams(k === "desk" ? {} : { tab: k, ...extra });
  const [slip, setSlip] = useState<any>(null);
  const [key, setKey] = useState(0);
  const done = (r: any) => { setSlip(r); setKey((k) => k + 1); };
  // on a phone the tab row scrolls sideways: keep the open tab in view (only sideways, never the page)
  const tabsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const row = tabsRef.current, el = row?.querySelector<HTMLElement>(`[data-tab="${tab}"]`);
    if (row && el) row.scrollLeft = el.offsetLeft - row.offsetLeft - (row.clientWidth - el.clientWidth) / 2;
  }, [tab]);
  return (
    <div className="min-w-0 space-y-5">
      <PageHeader title="Cashier · کیشیئر" subtitle="Money received and paid, cheques, the salesmen's cash and the banks — with a voucher for every entry" />
      <div ref={tabsRef} className="flex gap-1 overflow-x-auto border-b border-slate-200">
        {TABS.filter((t) => can(t.perm)).map((t) => (
          <button key={t.k} onClick={() => go(t.k)} data-tab={t.k} className={`whitespace-nowrap border-b-2 px-3 py-1.5 text-center text-sm leading-tight ${tab === t.k ? "border-brand-600 font-medium text-brand-700" : "border-transparent text-slate-600"}`}>
            {t.en}<Ur className="block text-xs">{t.ur}</Ur>
          </button>
        ))}
      </div>
      {tab === "desk" && <Desk key={key} go={go} onSlip={setSlip} />}
      {tab === "receive" && <MoneyForm key={`in-${params.toString()}-${key}`} dir="in" preset={Object.fromEntries(params)} onDone={done} />}
      {tab === "pay" && <MoneyForm key={`out-${params.toString()}-${key}`} dir="out" preset={Object.fromEntries(params)} onDone={done} />}
      {tab === "cheques" && <Cheques go={go} />}
      {tab === "handover" && <Handover />}
      {tab === "daybook" && <DayBook />}
      {tab === "bank" && <CashBank start={params.get("do")} />}
      {slip && <VoucherSlip r={slip} onClose={() => setSlip(null)} />}
    </div>
  );
}

/* ================= desk ================= */
const TONE: Record<string, string> = { red: "bg-red-500", amber: "bg-amber-500", blue: "bg-sky-500", green: "bg-emerald-500" };

function Desk({ go, onSlip }: { go: (k: string, extra?: Record<string, string>) => void; onSlip: (v: any) => void }) {
  const { data, error } = useApi<any>("/cashier/desk", 60_000);
  const { can } = useAuth();
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const actions = [
    { k: "receive", en: "Receive", ur: "وصولی", icon: ArrowDownCircle, cls: "bg-emerald-600", perm: "cash.receive" },
    { k: "pay", en: "Pay", ur: "ادائیگی", icon: ArrowUpCircle, cls: "bg-rose-600", perm: "cash.pay" },
    { k: "cheques", en: "Cheques", ur: "چیک", icon: FileCheck2, cls: "bg-violet-600", perm: "cheques.manage", n: data.cheques.to_deposit.n },
    { k: "handover", en: "Salesmen", ur: "سیلزمین کیش", icon: Users, cls: "bg-amber-600", perm: "shifts.handover", n: data.handovers.pending.length },
    { k: "bank", en: "Deposit", ur: "بینک جمع", icon: Landmark, cls: "bg-sky-600", perm: "cash.book", extra: { do: "deposit" } },
    { k: "bank", en: "Count", ur: "کیش گنیں", icon: Calculator, cls: "bg-slate-700", perm: "cash.book", extra: { do: "count" } },
    { k: "daybook", en: "Day book", ur: "روزنامچہ", icon: BookOpenText, cls: "bg-teal-600", perm: "cashier.desk" },
  ].filter((a) => can(a.perm));
  const c = data.cheques;
  return (
    <div className="space-y-5">
      <div className="overflow-hidden rounded-2xl bg-gradient-to-br from-brand-700 to-brand-600 p-4 text-white shadow">
        <div className="flex items-center gap-1.5 text-sm opacity-90"><Banknote size={16} /> Cash in hand · <Ur>ہاتھ میں نقد</Ur></div>
        <div className="text-3xl font-bold tabular-nums sm:text-4xl">{pkr(data.cash.in_hand)}</div>
        <div className="mt-1 text-xs opacity-80">{data.cash.last_count ? <>Last counted {pkr(data.cash.last_count.amount)} by {data.cash.last_count.by}, {ago(data.cash.last_count.at)}</> : "Not counted yet — count the cash once to start the book"}</div>
        <div className="mt-3 grid grid-cols-3 gap-2 text-sm">
          <div className="rounded-xl bg-white/10 p-2"><div className="text-xs opacity-80">Came in · <Ur>آیا</Ur></div><Amt v={data.today.in_cash + data.today.in_bank} /></div>
          <div className="rounded-xl bg-white/10 p-2"><div className="text-xs opacity-80">Went out · <Ur>گیا</Ur></div><Amt v={data.today.out_cash + data.today.out_bank} /></div>
          <div className="rounded-xl bg-white/10 p-2"><div className="text-xs opacity-80">To bank · <Ur>بینک</Ur></div><Amt v={data.today.deposited} /></div>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-2 sm:grid-cols-7">
        {actions.map((a) => (
          <button key={a.en} onClick={() => go(a.k, a.extra)} className={`relative flex flex-col items-center gap-1 rounded-2xl px-1 py-3 text-white shadow-sm active:scale-95 ${a.cls}`}>
            <a.icon size={22} /><span className="text-xs font-semibold leading-tight">{a.en}</span><Ur className="text-[11px] leading-none opacity-90">{a.ur}</Ur>
            {a.n ? <span className="absolute -right-1 -top-1 rounded-full bg-white px-1.5 text-xs font-bold text-slate-900 shadow">{a.n}</span> : null}
          </button>
        ))}
      </div>

      {data.tips.length > 0 && (
        <div className="card">
          <h2 className="p-4 pb-1 font-semibold">What to do now · <Ur>ابھی کیا کرنا ہے</Ur></h2>
          <ul className="divide-y divide-slate-100">
            {data.tips.map((t: any) => (
              <li key={t.key}><button onClick={() => go(t.tab === "count" || t.tab === "bank" ? "bank" : t.tab, t.tab === "count" ? { do: "count" } : t.tab === "bank" ? { do: "deposit" } : {})}
                className="flex w-full items-start gap-3 px-4 py-2.5 text-left hover:bg-slate-50">
                <span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${TONE[t.tone]}`} />
                <span className="min-w-0 flex-1 text-sm">{t.en}<Ur className="block text-[13px] text-slate-500">{t.ur}</Ur></span>
              </button></li>
            ))}
          </ul>
        </div>
      )}

      {can("cheques.manage") && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Mini label="Cheques in hand" ur="ہاتھ میں چیک" n={c.in_hand.n} v={c.in_hand.amount} onClick={() => go("cheques")} />
          <Mini label="Ready to deposit" ur="جمع کروانے ہیں" n={c.to_deposit.n} v={c.to_deposit.amount} tone="text-sky-700" onClick={() => go("cheques")} />
          <Mini label="Our cheques due (7 days)" ur="ہمارے چیک" n={c.issued_due.n} v={c.issued_due.amount} tone="text-amber-700" onClick={() => go("cheques")} />
          <Mini label="Bounced (30 days)" ur="واپس آئے" n={c.bounced.n} v={c.bounced.amount} tone="text-red-600" onClick={() => go("cheques")} />
        </div>
      )}

      <OnlineToday o={data.online} onBank={can("cash.book") ? () => go("bank") : undefined} />

      {can("bank.view") && <BankSummary />}

      <div className="grid gap-5 lg:grid-cols-2 [&>*]:min-w-0">
        {data.handovers.pending.length > 0 && can("shifts.handover") && (
          <div className="card">
            <div className="flex items-start justify-between gap-3 p-4 pb-1"><h2 className="font-semibold">Cash to take from salesmen · <Ur>سیلزمین سے کیش</Ur></h2><span className="whitespace-nowrap font-semibold tabular-nums">{pkr(data.handovers.pending_amount)}</span></div>
            <ul className="divide-y divide-slate-100">{data.handovers.pending.map((s: any) => (
              <li key={s.id}><button onClick={() => go("handover")} className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm hover:bg-slate-50">
                <span className="min-w-0 flex-1"><b>{s.attendant}</b><span className="block text-xs text-slate-500">{s.station_name} · shift #{s.id} · closed {ago(s.closed_at)}</span></span>
                <span className="shrink-0 font-semibold tabular-nums">{pkr(s.cash_actual)}</span></button></li>
            ))}</ul>
          </div>
        )}
        {data.promised.length > 0 && can("cash.receive") && (
          <div className="card">
            <h2 className="p-4 pb-1 font-semibold">Promised payments · <Ur>وعدے</Ur></h2>
            <ul className="divide-y divide-slate-100">{data.promised.map((p: any) => (
              <li key={`${p.client_id}-${p.promised_on}`} className="flex items-center gap-2 px-4 py-2.5 text-sm">
                <span className="min-w-0 flex-1"><b>{p.name}</b><span className={`block text-xs ${p.state === "broken" ? "text-red-600" : "text-slate-500"}`}>{p.state === "broken" ? "Promise broken" : "Today"} · <span className="whitespace-nowrap">{p.promised_on}</span> · <Ur>{p.state === "broken" ? "وعدہ ٹوٹا" : "آج"}</Ur></span></span>
                <span className="shrink-0 font-semibold tabular-nums">{pkr(p.amount)}</span>
                <button className="btn-secondary min-h-9 shrink-0 !px-3 !py-1.5 text-xs" onClick={() => go("receive", { type: "wholesale", id: String(p.client_id), amount: String(p.amount) })}>Receive</button>
              </li>
            ))}</ul>
          </div>
        )}
        {data.payables.length > 0 && can("cash.pay") && (
          <div className="card">
            <h2 className="p-4 pb-1 font-semibold">Suppliers to pay · <Ur>سپلائر کو دینا ہے</Ur></h2>
            <ul className="divide-y divide-slate-100">{data.payables.map((s: any) => (
              <li key={s.id} className="flex items-center gap-2 px-4 py-2.5 text-sm">
                <span className="min-w-0 flex-1 font-medium">{s.name}</span><span className="shrink-0 font-semibold tabular-nums">{pkr(s.owed)}</span>
                <button className="btn-secondary min-h-9 shrink-0 !px-3 !py-1.5 text-xs" onClick={() => go("pay", { type: "supplier", id: String(s.id) })}>Pay</button>
              </li>
            ))}</ul>
          </div>
        )}
        <div className="card">
          <h2 className="p-4 pb-1 font-semibold">Today's vouchers · <Ur>آج کی رسیدیں</Ur></h2>
          <ul className="divide-y divide-slate-100">{data.vouchers.map((v: any) => (
            <li key={v.id}><button onClick={() => onSlip({ voucher: v, account: v.account })} className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm hover:bg-slate-50">
              <span className={`w-16 shrink-0 font-mono text-xs ${v.direction === "in" ? "text-emerald-700" : "text-rose-700"}`}>{v.no}</span>
              <span className="min-w-0 flex-1 truncate">{v.party_name}<span className="block text-xs text-slate-500">{v.method} · {ago(v.created_at)}</span></span>
              <span className={`font-semibold tabular-nums ${v.direction === "in" ? "text-emerald-700" : "text-rose-700"}`}>{v.direction === "in" ? "+" : "−"}{pkr(v.amount)}</span>
            </button></li>
          ))}{!data.vouchers.length && <li><Empty>No vouchers yet today · <Ur>آج کوئی رسید نہیں</Ur></Empty></li>}</ul>
        </div>
      </div>
    </div>
  );
}

/** Full rupees where there is room; "Rs 39.2 L" in the small boxes on a phone. */
const Amt = ({ v }: { v: number }) => (
  <div className="font-semibold tabular-nums"><span className="sm:hidden">{pkrShort(v)}</span><span className="hidden sm:inline">{pkr(v)}</span></div>
);

const Mini = ({ label, ur, n, v, tone = "", onClick }: { label: string; ur: string; n: number; v: number; tone?: string; onClick: () => void }) => (
  <button onClick={onClick} className="card min-w-0 p-3 text-left hover:bg-slate-50">
    <div className="text-xs text-slate-500">{label}<Ur className="block">{ur}</Ur></div>
    <div className={`truncate text-lg font-bold tabular-nums ${tone}`}>{pkr(v)}</div><div className="text-xs text-slate-500">{n} cheque{n === 1 ? "" : "s"}</div>
  </button>
);

/* ================= receive / pay ================= */
const IN_TYPES = [["khata", "Khata customer", "کھاتہ گاہک"], ["wholesale", "Wholesale client", "ہول سیل کلائنٹ"], ["other", "Other", "دیگر"]] as const;
const OUT_TYPES = [["supplier", "Supplier", "سپلائر"], ["expense", "Expense", "خرچہ"], ["staff", "Staff advance", "ملازم ایڈوانس"], ["other", "Other", "دیگر"]] as const;
const METHODS = [["Cash", "نقد"], ["Bank transfer", "بینک"], ["Raast", "راست"], ["JazzCash", "جاز کیش"], ["Easypaisa", "ایزی پیسہ"], ["Cheque", "چیک"]] as const;
const isCash = (m: string) => m === "Cash";
/** A wholesale client paid our supplier (depot) straight — the bypass: no money here, both accounts go down. */
const DEPOT_PAY = "Paid to depot";

function MoneyForm({ dir, preset, onDone }: { dir: "in" | "out"; preset: Record<string, string>; onDone: (r: any) => void }) {
  const types = dir === "in" ? IN_TYPES : OUT_TYPES;
  const [type, setType] = useState<string>(types.some((t) => t[0] === preset.type) ? preset.type : types[0][0]);
  const [party, setParty] = useState<any>(null);
  const [f, setF] = useState({ amount: preset.amount ?? "", method: preset.method ?? "Cash", party_name: "", category: "", note: "", ref: "", bank: "", cheque_no: "", cheque_date: today(), notify: true });
  const [account, setAccount] = useState<number | null>(null);
  const [depotId, setDepotId] = useState<number | null>(null);
  const [photos, setPhotos] = useState<number[]>([]);
  const cats = useApi<any>(dir === "out" && type === "expense" ? "/expense-categories" : null);
  const picks = useApi<any>("/bank/accounts/pick");
  const { busy, run } = useAction();
  const set = (k: string, v: any) => setF((x) => ({ ...x, [k]: v }));
  const cheque = f.method === "Cheque";
  const depot = f.method === DEPOT_PAY;
  const canDepot = dir === "in" && type === "wholesale";
  const depots = useApi<any>(canDepot ? "/cashier/parties?kind=supplier" : null);
  const depotRow = depots.data?.supplier?.find((x: any) => x.id === depotId);
  const needsParty = type !== "other" && type !== "expense";
  const amount = Number(f.amount) || 0;
  const after = party?.balance != null && amount ? (dir === "in" ? party.balance - amount : type === "staff" ? party.balance + amount : party.balance - amount) : null;
  const methods = METHODS.filter(([m]) => !(type === "staff" && m === "Cheque"));
  const ourBank = dir === "out" && cheque && account ? picks.data?.accounts?.find((a: any) => a.id === account)?.bank : null;

  // preset from the desk (e.g. "Pay" next to a supplier, "Receive" next to a promise)
  useEffect(() => {
    if (!preset.id || !needsParty) return;
    api(`/cashier/parties?kind=${type}`).then((r) => setParty((r[type] ?? []).find((x: any) => String(x.id) === preset.id) ?? null)).catch(() => {});
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const body = {
      party_type: type, party_id: party?.id ?? null, party_name: f.party_name || null, category: f.category || null, amount, method: f.method,
      account_id: isCash(f.method) || depot ? null : account, ...(depot ? { supplier_id: depotId } : {}), ref: f.ref || null, note: f.note || null, photo_ids: photos, notify: f.notify,
      cheque: cheque ? { bank: ourBank ?? f.bank, cheque_no: f.cheque_no, cheque_date: f.cheque_date } : null,
    };
    const r = await run(() => api(`/cashier/${dir === "in" ? "receive" : "pay"}`, { body }), (x: any) => `${x.voucher.no} saved · محفوظ`);
    if (r) onDone({ ...r, party_type: type, party, cheque: body.cheque, account: picks.data?.accounts?.find((a: any) => a.id === account)?.name ?? null });
  };

  return (
    <form onSubmit={submit} className="card mx-auto max-w-2xl space-y-4 p-4">
      <h2 className="text-lg font-semibold">{dir === "in" ? <>Money received · <Ur>رقم وصول</Ur></> : <>Payment · <Ur>ادائیگی</Ur></>}</h2>
      <div className={`grid gap-2 ${types.length === 4 ? "grid-cols-2 sm:grid-cols-4" : "grid-cols-3"}`}>
        {types.map(([k, en, ur]) => (
          <button type="button" key={k} onClick={() => { setType(k); setParty(null); if ((k === "staff" && cheque) || (k !== "wholesale" && depot)) set("method", "Cash"); }} aria-pressed={type === k}
            className={`rounded-xl px-2 py-2 text-sm leading-tight ${type === k ? "bg-brand-600 font-semibold text-white" : "bg-slate-100 text-slate-700"}`}>{en}<Ur className="block text-xs">{ur}</Ur></button>
        ))}
      </div>

      {needsParty && <PartyPicker kind={type} value={party} onChange={setParty} dir={dir} />}
      {type === "expense" && <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Expense head · خرچے کی قسم *"><select className="input" required value={f.category} onChange={(e) => set("category", e.target.value)}>
          <option value="">— choose —</option>{(cats.data?.categories ?? []).map((c: any) => <option key={c.id}>{c.name}</option>)}</select></Field>
        <Field label="Paid to · کس کو"><input className="input" value={f.party_name} onChange={(e) => set("party_name", e.target.value)} placeholder="e.g. Electrician" /></Field>
      </div>}
      {type === "other" && <div className="grid gap-3 sm:grid-cols-2">
        <Field label={dir === "in" ? "Received from · کس سے *" : "Paid to · کس کو *"}><input className="input" required value={f.party_name} onChange={(e) => set("party_name", e.target.value)} /></Field>
        <Field label="For what · کس لیے"><input className="input" value={f.category} onChange={(e) => set("category", e.target.value)} placeholder={dir === "in" ? "e.g. Scrap sale, rent" : "e.g. Committee, donation"} /></Field>
      </div>}

      <Field label="Amount (Rs) · رقم *"><input className="input py-3 text-2xl font-semibold" type="number" min={1} step="any" required value={f.amount} onChange={(e) => set("amount", e.target.value)} /></Field>
      {amount > 0 && <p className="-mt-2 text-xs text-slate-500">{toWords(amount)}</p>}

      <fieldset>
        <legend className="label">How · <Ur>کیسے</Ur></legend>
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
          {methods.map(([m, ur]) => (
            <button type="button" key={m} onClick={() => set("method", m)} aria-pressed={f.method === m}
              className={`rounded-xl px-1 py-2 text-xs leading-tight ${f.method === m ? "bg-slate-800 font-semibold text-white" : "bg-slate-100 text-slate-700"}`}>{m}<Ur className="block">{ur}</Ur></button>
          ))}
        </div>
        {canDepot && <button type="button" onClick={() => set("method", DEPOT_PAY)} aria-pressed={depot}
          className={`mt-2 w-full rounded-xl px-3 py-2.5 text-left text-sm leading-tight ${depot ? "bg-slate-800 font-semibold text-white" : "bg-slate-100 text-slate-700"}`}>
          🏭 Client paid our depot direct (bypass) · <Ur>کلائنٹ نے سیدھا ڈپو کو دیا</Ur></button>}
      </fieldset>

      {depot && (
        <div className="space-y-2 rounded-xl bg-sky-50 p-3">
          <p className="text-sm text-sky-900">No money comes to us: {party?.name ?? "the client"}'s due goes down, and so does what we owe the depot. Nothing in the cash book or the bank.
            <Ur className="block">ہمارے پاس رقم نہیں آئی — کلائنٹ کا بقایا اور ڈپو کا بقایا دونوں کم ہوں گے</Ur></p>
          <fieldset>
            <legend className="label">Which depot did the client pay? · <Ur>کس ڈپو کو</Ur> *</legend>
            <div className="grid gap-2 sm:grid-cols-2">{(depots.data?.supplier ?? []).map((x: any) => (
              <button type="button" key={x.id} aria-pressed={depotId === x.id} onClick={() => setDepotId(x.id)}
                className={`flex min-h-12 items-center justify-between gap-2 rounded-xl bg-white px-3 py-2 text-left text-sm ${depotId === x.id ? "ring-2 ring-brand-600" : "ring-1 ring-slate-200"}`}>
                <span className="min-w-0 font-semibold">{x.name}</span><span className="shrink-0 text-xs text-slate-500">{x.balance < 0 ? <>advance with them <b className="tabular-nums">{pkr(-x.balance)}</b></> : <>we owe <b className="tabular-nums">{pkr(x.balance)}</b></>}</span></button>))}</div>
          </fieldset>
          {depotRow && amount > 0 && <p className="text-sm">We owe {depotRow.name}: <b>{pkr(depotRow.balance)}</b> → after · <Ur>بعد میں</Ur>: <b>{pkr(depotRow.balance - amount)}</b></p>}
        </div>
      )}

      {!isCash(f.method) && !depot && !(dir === "in" && cheque) && (
        <AccountPicker method={cheque ? "bank" : f.method} value={account} onChange={setAccount} required={dir === "out" || type === "other"}
          label={dir === "in" ? undefined : cheque ? "Cheque from which of our accounts? · کس اکاؤنٹ کا چیک" : "Paid from which account? · کس اکاؤنٹ سے"} />
      )}
      {cheque && (
        <div className="space-y-3 rounded-xl bg-violet-50 p-3">
          <p className="text-sm text-violet-900">{dir === "in" ? "The cheque goes into the register. It counts as paid when it clears." : "The cheque goes into the register. It counts as paid when the bank pays it."}
            <Ur className="block">{dir === "in" ? "چیک کلیئر ہونے پر ادائیگی شمار ہوگی" : "بینک سے کیش ہونے پر ادائیگی شمار ہوگی"}</Ur></p>
          {dir === "in" && <div><span className="label">Cheque of which bank? · <Ur>کس بینک کا چیک</Ur></span><BankNamePicker required value={f.bank} onChange={(v) => set("bank", v)} /></div>}
          <div className="grid grid-cols-2 gap-3">
            <Field label="Cheque no. · چیک نمبر *"><input className="input" required value={f.cheque_no} onChange={(e) => set("cheque_no", e.target.value)} inputMode="numeric" /></Field>
            <Field label="Cheque date · تاریخ *"><input className="input" type="date" required value={f.cheque_date} onChange={(e) => set("cheque_date", e.target.value)} /></Field>
          </div>
        </div>
      )}

      {party && <div className="flex flex-wrap gap-x-5 gap-y-1 rounded-xl bg-slate-50 px-3 py-2 text-sm">
        <span>{dir === "in" ? "Owes now" : type === "staff" ? "Advance now" : "We owe"} · <Ur>ابھی</Ur>: <b>{pkr(party.balance)}</b></span>
        {after != null && !cheque && <span>After · <Ur>بعد میں</Ur>: <b>{pkr(after)}</b></span>}
      </div>}

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Ref / slip no. · حوالہ"><input className="input" value={f.ref} onChange={(e) => set("ref", e.target.value)} /></Field>
        <Field label="Note · نوٹ"><input className="input" value={f.note} onChange={(e) => set("note", e.target.value)} /></Field>
      </div>
      <ProofPhotos value={photos} onChange={setPhotos} required={cheque} hint={cheque ? "photo of the cheque" : depot ? "depot's deposit slip / receipt" : "receipt / slip / screenshot"} />
      {dir === "in" && type === "khata" && <label className="flex items-center gap-2 py-1 text-sm"><input type="checkbox" className="h-5 w-5 shrink-0" checked={f.notify} onChange={(e) => set("notify", e.target.checked)} /> Send the receipt on WhatsApp · <Ur>رسید واٹس ایپ پر</Ur></label>}

      <button className={`flex w-full items-center justify-center gap-2 rounded-xl px-6 py-3.5 text-lg font-bold text-white shadow active:scale-[.98] disabled:bg-slate-300 ${dir === "in" ? "bg-emerald-600" : "bg-rose-600"}`}
        disabled={busy || !amount || (needsParty && !party) || (cheque && !photos.length) || (depot && !depotId)}>
        <Check size={22} /> {dir === "in" ? <>Save receipt · <Ur>محفوظ</Ur></> : <>Save payment · <Ur>محفوظ</Ur></>}
      </button>
    </form>
  );
}

function PartyPicker({ kind, value, onChange, dir }: { kind: string; value: any; onChange: (p: any) => void; dir: "in" | "out" }) {
  const [q, setQ] = useState("");
  const { data } = useApi<any>(value ? null : `/cashier/parties?kind=${kind}&q=${encodeURIComponent(q)}`);
  const label: Record<string, string> = { khata: "Customer · گاہک", wholesale: "Client · کلائنٹ", supplier: "Supplier · سپلائر", staff: "Staff member · ملازم" };
  if (value) return (
    <div className="flex items-center gap-3 rounded-xl bg-brand-50 p-3 ring-1 ring-brand-200">
      <span className="min-w-0 flex-1"><b className="block">{value.name}</b><span className="text-xs text-slate-600">{value.phone ? phone(value.phone) : value.role ?? ""}</span></span>
      <span className="text-right"><span className="block font-semibold tabular-nums">{pkr(value.balance)}</span><span className="text-[11px] text-slate-500">{kind === "supplier" ? "we owe" : kind === "staff" ? "advance" : "owes"}</span></span>
      <button type="button" className="-mr-1 p-2.5 text-slate-500" onClick={() => onChange(null)} aria-label="Change"><X size={20} /></button>
    </div>
  );
  return (
    <div>
      <span className="label">{label[kind]} *</span>
      <div className="relative"><Search size={15} className="absolute left-2.5 top-3 text-slate-400" /><input className="input pl-8" placeholder="Search name or phone" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      <ul className="mt-1 max-h-64 divide-y divide-slate-100 overflow-y-auto rounded-xl ring-1 ring-slate-200">
        {(data?.[kind] ?? []).slice(0, 12).map((p: any) => (
          <li key={p.id}><button type="button" onClick={() => onChange(p)} className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-slate-50">
            <span className="min-w-0 flex-1">{p.name}{p.business_name ? <span className="text-slate-500"> · {p.business_name}</span> : null}</span>
            <span className={`shrink-0 tabular-nums ${p.balance > 0 && dir === "in" ? "font-semibold text-amber-700" : "text-slate-500"}`}>{pkr(p.balance)}</span>
          </button></li>
        ))}
        {data && !data[kind]?.length && <li className="px-3 py-3 text-center text-sm text-slate-500">Nothing found</li>}
      </ul>
    </div>
  );
}


export function VoucherSlip({ r, onClose }: { r: any; onClose: () => void }) {
  const v = r.voucher;
  const isIn = v.direction === "in";
  return (
    <Modal open onClose={onClose} title={`${isIn ? "Receipt" : "Payment"} voucher ${v.no}`}>
      {/* on paper: two copies on one page, each with the letterhead — the top one for the party, the bottom one stays in the office */}
      <div className="voucher-2up">
        <div className="print:break-inside-avoid"><PrintHeader /><VoucherBody r={r} copy={isIn ? "Party copy · گاہک کی کاپی" : "Payee copy · وصول کنندہ کی کاپی"} /><PrintFooter /></div>
        <div className="my-3 hidden border-t-2 border-dashed border-slate-400 pt-1 text-center text-[10px] text-slate-500 print:block">✂ cut here · یہاں سے کاٹیں</div>
        <div className="hidden print:block print:break-inside-avoid"><PrintHeader /><VoucherBody r={r} copy="Office copy · دفتر کی کاپی" /><PrintFooter /></div>
      </div>
      <div className="mt-4 flex justify-end gap-2 print:hidden">
        <button className="btn-secondary" onClick={onClose}>Close · <Ur>بند</Ur></button>
        <button className="btn-primary" onClick={() => window.print()}><Printer size={15} /> Print 2 copies · <Ur>پرنٹ</Ur></button>
      </div>
    </Modal>
  );
}

function VoucherBody({ r, copy }: { r: any; copy: string }) {
  const { tenant } = useAuth();
  const v = r.voucher;
  const isIn = v.direction === "in";
  return (
      <div className="space-y-3 text-sm print:space-y-1.5">
        <div className="text-center">
          <div className="text-lg font-bold print:hidden">{tenant?.name}</div>
          <div className="font-semibold">{isIn ? <>Receipt voucher · <Ur>رسید</Ur></> : <>Payment voucher · <Ur>ادائیگی واؤچر</Ur></>}</div>
          <div className="text-xs text-slate-500">{v.no} · {dt(v.created_at)}</div>
          <span className="mt-1 hidden rounded border border-slate-800 px-2 text-[10px] font-bold uppercase tracking-wide print:inline-block">{copy}</span>
        </div>
        <div className={`rounded-xl p-3 text-center print:px-2 print:py-1 ${isIn ? "bg-emerald-50" : "bg-rose-50"}`}>
          <div className="text-3xl font-bold tabular-nums print:text-xl">{pkr(v.amount)}</div><div className="text-xs text-slate-600">{toWords(v.amount)}</div>
        </div>
        <dl className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1.5 print:gap-y-0.5">
          <dt className="text-slate-500">{isIn ? "Received from" : "Paid to"}</dt><dd className="font-medium">{v.party_name}</dd>
          {v.category && <><dt className="text-slate-500">For</dt><dd>{v.category}</dd></>}
          <dt className="text-slate-500">How</dt><dd>{v.method}{r.account ? ` · ${r.account}` : ""}</dd>
          {r.cheque && <><dt className="text-slate-500">Cheque</dt><dd>{r.cheque.bank} · {r.cheque.cheque_no} · dated {r.cheque.cheque_date}</dd></>}
          {v.ref && !r.cheque && <><dt className="text-slate-500">Ref</dt><dd>{v.ref}</dd></>}
          {v.note && <><dt className="text-slate-500">Note</dt><dd>{v.note}</dd></>}
          {r.balance_after != null && <><dt className="text-slate-500">{r.party_type === "supplier" ? <>We still owe · <Ur>باقی دینا</Ur></> : r.party_type === "staff" ? <>Total advance · <Ur>کل ایڈوانس</Ur></> : <>Still owes · <Ur>باقی</Ur></>}</dt><dd className="font-semibold">{pkr(r.balance_after)}</dd></>}
          <dt className="text-slate-500">By</dt><dd>{v.created_by}</dd>
        </dl>
        {r.message && <p className="rounded-lg bg-amber-50 px-3 py-2 text-amber-900 print:hidden">{r.message}</p>}
        {/* on paper too: an expense not yet approved, or turned down, must not pass as a settled payment */}
        {(r.approval === "pending" || r.approval === "rejected") && <div className="rounded border-2 border-dashed border-amber-600 px-2 py-1 text-center print:py-0 text-xs font-bold uppercase tracking-wide text-amber-800">
          {r.approval === "pending" ? <>Awaiting owner approval · <Ur>مالک کی منظوری باقی</Ur></> : <>Rejected · <Ur>نامنظور</Ur></>}</div>}
        <div className={`grid ${v.category ? "grid-cols-3" : "grid-cols-2"} gap-6 pt-8 text-center text-xs text-slate-500 print:pt-5`}>
          <div className="border-t border-slate-400 pt-1">{r.from_expenses ? <>Prepared by · <Ur>تیار کنندہ</Ur></> : <>Cashier · <Ur>کیشیئر</Ur></>}</div>
          {v.category && <div className="border-t border-slate-400 pt-1">Approved by · <Ur>منظور</Ur>{r.approved_by ? <div className="text-slate-700">{r.approved_by}</div> : null}</div>}
          <div className="border-t border-slate-400 pt-1">{isIn ? "Paid by" : "Received by"} · <Ur>دستخط</Ur></div></div>
      </div>
  );
}

/* ================= cheques ================= */
const STATUS: Record<string, { en: string; ur: string; tone: string }> = {
  in_hand: { en: "In hand", ur: "ہاتھ میں", tone: "blue" }, deposited: { en: "Deposited", ur: "جمع", tone: "amber" }, cleared: { en: "Cleared", ur: "کلیئر", tone: "green" },
  bounced: { en: "Bounced", ur: "واپس", tone: "red" }, returned: { en: "Given back", ur: "واپس دیا", tone: "slate" }, issued: { en: "Issued", ur: "جاری", tone: "violet" }, cancelled: { en: "Cancelled", ur: "منسوخ", tone: "slate" },
};
const FILTERS = [["open", "Open", "کھلے"], ["in", "Received", "وصول شدہ"], ["out", "Issued", "جاری کیے"], ["bounced", "Bounced", "واپس"], ["all", "All", "سب"]] as const;

function Cheques({ go }: { go: (k: string, extra?: Record<string, string>) => void }) {
  const { data, reload, error } = useApi<any>("/cashier/cheques");
  const { can } = useAuth();
  const [filter, setFilter] = useState("open");
  const [act, setAct] = useState<{ q: any; action: string } | null>(null);
  const list = useMemo(() => (data?.cheques ?? []).filter((q: any) =>
    filter === "all" ? true : filter === "bounced" ? q.status === "bounced" : filter === "in" ? q.direction === "in" && ["in_hand", "deposited"].includes(q.status)
      : filter === "out" ? q.direction === "out" && q.status === "issued" : ["in_hand", "deposited", "issued"].includes(q.status)), [data, filter]);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const t = data.totals;
  const now = today();
  const actions = (q: any): [string, string, string][] =>
    q.direction === "in"
      ? q.status === "in_hand" ? [["deposit", "Deposit", "جمع کریں"], ["return", "Give back", "واپس دیں"], ["bounce", "Bounced", "واپس آیا"]] : q.status === "deposited" ? [["clear", "Cleared", "کلیئر"], ["bounce", "Bounced", "واپس آیا"]] : []
      : q.status === "issued" ? [["clear", "Paid by bank", "کیش ہوگیا"], ["bounce", "Bounced", "واپس آیا"], ["cancel", "Cancel", "منسوخ"]] : [];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 print:hidden">
        <Mini label="In hand" ur="ہاتھ میں" n={t.in_hand.n} v={t.in_hand.amount} onClick={() => setFilter("in")} />
        <Mini label="Ready to deposit" ur="جمع کروانے ہیں" n={t.to_deposit.n} v={t.to_deposit.amount} tone="text-sky-700" onClick={() => setFilter("in")} />
        <Mini label="In bank, not cleared" ur="بینک میں" n={t.deposited.n} v={t.deposited.amount} tone="text-amber-700" onClick={() => setFilter("in")} />
        <Mini label="Our cheques out" ur="ہمارے چیک" n={t.issued.n} v={t.issued.amount} tone="text-violet-700" onClick={() => setFilter("out")} />
      </div>
      <div className="flex flex-wrap items-center gap-2 print:hidden">
        <div className="flex gap-1 overflow-x-auto">
          {FILTERS.map(([k, en, ur]) => <button key={k} onClick={() => setFilter(k)} className={`min-h-9 whitespace-nowrap rounded-full px-3 py-1 text-sm ${filter === k ? "bg-slate-800 text-white" : "bg-slate-100"}`}>{en} · <Ur>{ur}</Ur></button>)}
        </div>
        <div className="flex flex-1 justify-end gap-2">
          {can("cash.receive") && <button className="btn-secondary !px-3 text-sm" onClick={() => go("receive", { method: "Cheque" })}>+ Received · <Ur>وصول</Ur></button>}
          {can("cash.pay") && <button className="btn-secondary !px-3 text-sm" onClick={() => go("pay", { method: "Cheque" })}>+ Issue · <Ur>جاری</Ur></button>}
          <button className="btn-secondary min-h-10 !px-3" aria-label="Print" disabled={!list.length} onClick={() => window.print()}><Printer size={15} /><span className="hidden sm:inline"> Print · <Ur>پرنٹ</Ur></span></button>
        </div>
      </div>
      {/* on paper: the chosen list as a register, e.g. the cheques to take to the bank */}
      <div className="hidden print:block">
        <h2 className="text-center text-base font-bold uppercase tracking-wide">Cheque register · {FILTERS.find(([k]) => k === filter)?.[1]}</h2>
        <div className="mb-2 text-center text-xs text-slate-600">{list.length} cheque{list.length === 1 ? "" : "s"} · as on {dt(new Date().toISOString())}</div>
        <table className="w-full">
          <thead><tr><th className="th">#</th><th className="th">Cheque date</th><th className="th">Party</th><th className="th">Bank · cheque no.</th><th className="th">In / out</th><th className="th">Status</th><th className="th text-right">Amount</th></tr></thead>
          <tbody>{list.map((q: any, i: number) => (
            <tr key={`${q.src}-${q.id}`}>
              <td className="td">{i + 1}</td><td className="td whitespace-nowrap">{q.cheque_date}</td>
              <td className="td">{q.party_name}<div className="text-slate-500">{q.party_type}{q.reason ? ` · ${q.reason}` : ""}</div></td>
              <td className="td">{q.bank}<div className="text-slate-500"># {q.cheque_no}{q.account_name ? ` · ${q.account_name}` : ""}</div></td>
              <td className="td">{q.direction === "in" ? "Received" : "Issued"}</td><td className="td">{(STATUS[q.status] ?? STATUS.in_hand).en}</td>
              <td className="td whitespace-nowrap text-right tabular-nums">{pkr(q.amount)}</td>
            </tr>))}</tbody>
          <tfoot>
            {/* totals of cheques still to be paid only: bounced, cleared, returned or cancelled ones are listed but not added */}
            {[["in", ["in_hand", "deposited"], "Received, not yet cleared (in hand + in bank)"], ["out", ["issued"], "Our cheques not yet paid by the bank"]].map(([dir, sts, label]) => {
              const open = list.filter((q: any) => q.direction === dir && (sts as string[]).includes(q.status));
              return open.length ? <tr key={dir as string}><td className="td" colSpan={6}>{label as string} · {open.length}</td><td className="td whitespace-nowrap text-right tabular-nums">{pkr(open.reduce((a: number, q: any) => a + q.amount, 0))}</td></tr> : null;
            })}
          </tfoot>
        </table>
        <div className="mt-10 grid grid-cols-2 gap-10 text-center text-xs text-slate-600"><div className="border-t border-slate-500 pt-1">Cashier · <Ur>کیشیئر</Ur></div><div className="border-t border-slate-500 pt-1">Checked by · <Ur>چیک کیا</Ur></div></div>
      </div>
      <ul className="space-y-2 print:hidden">
        {list.map((q: any) => {
          const s = STATUS[q.status] ?? STATUS.in_hand;
          const due = ["in_hand", "issued"].includes(q.status) && q.cheque_date <= now;
          return (
            <li key={`${q.src}-${q.id}`} className="card p-3">
              <div className="flex items-start gap-3">
                <BankLogo name={q.bank} size={38} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2"><b>{q.party_name}</b><Badge tone={s.tone}>{s.en} · {s.ur}</Badge>
                    <span className={`text-xs ${q.direction === "in" ? "text-emerald-700" : "text-rose-700"}`}>{q.direction === "in" ? "↓ received" : "↑ issued"} · {q.party_type}</span></div>
                  <div className="text-xs text-slate-500">{q.bank} · # {q.cheque_no} · <span className={`whitespace-nowrap ${due ? "font-semibold text-sky-700" : ""}`}>dated {q.cheque_date}{due && q.status === "in_hand" ? " — deposit now" : ""}</span>
                    {q.account_name ? ` · ${q.account_name}` : ""}</div>
                  {q.reason && <div className="text-xs text-red-600">{q.reason}</div>}
                </div>
                <div className="text-right"><div className="font-bold tabular-nums">{pkr(q.amount)}</div><ProofThumbs ids={q.proof_ids} /></div>
              </div>
              {actions(q).length > 0 && <div className="mt-2 flex flex-wrap gap-2">
                {actions(q).map(([a, en, ur]) => (
                  <button key={a} onClick={() => setAct({ q, action: a })} className={`min-h-9 rounded-lg px-3 py-1.5 text-xs font-semibold ${a === "bounce" ? "bg-red-50 text-red-700" : a === "clear" || a === "deposit" ? "bg-emerald-600 text-white" : "bg-slate-100 text-slate-700"}`}>{en} · <Ur>{ur}</Ur></button>
                ))}
              </div>}
            </li>
          );
        })}
        {!list.length && <li><Empty>No cheques here · <Ur>کوئی چیک نہیں</Ur></Empty></li>}
      </ul>
      {act && <ChequeAct q={act.q} action={act.action} onClose={() => setAct(null)} onDone={() => { setAct(null); reload(); }} />}
    </div>
  );
}

function ChequeAct({ q, action, onClose, onDone }: { q: any; action: string; onClose: () => void; onDone: () => void }) {
  const [account, setAccount] = useState<number | null>(q.account_id ?? null);
  const [reason, setReason] = useState("");
  const { busy, run } = useAction();
  const needAccount = action === "deposit" || (action === "clear" && !q.account_id);
  const title: Record<string, string> = { deposit: "Deposit in bank · بینک میں جمع", clear: "Cleared · کلیئر", bounce: "Bounced · واپس آیا", return: "Give back · واپس دیں", cancel: "Cancel cheque · منسوخ" };
  const url = q.src === "w" ? `/wholesale/cheques/${q.id}/${action}` : `/cashier/cheques/${q.id}/${action}`;
  return (
    <Modal open onClose={onClose} title={title[action]}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        if (await run(() => api(url, { body: { account_id: account, reason: reason || null } }), "Saved · محفوظ")) onDone();
      }}>
        <p className="text-sm"><b>{q.party_name}</b> · {q.bank} # {q.cheque_no} · <b>{pkr(q.amount)}</b></p>
        {action === "clear" && <p className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-900">{q.direction === "in" ? "The payment is entered in their account and the bank balance goes up." : "The payment is entered in the supplier's account and the bank balance goes down."}</p>}
        {action === "bounce" && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">The owner gets an alert{q.direction === "in" ? " and the customer a WhatsApp message" : ""}. · <Ur>مالک کو اطلاع جائے گی</Ur></p>}
        {needAccount && <AccountPicker method="bank" required value={account} onChange={setAccount} label="Which account? · کون سا اکاؤنٹ" />}
        {["bounce", "return", "cancel"].includes(action) && <Field label="Reason · وجہ"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder={action === "bounce" ? "e.g. Insufficient funds" : ""} /></Field>}
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel · منسوخ</button><button className="btn-primary" disabled={busy || (needAccount && !account)}>Save · محفوظ کریں</button></div>
      </form>
    </Modal>
  );
}

/* ================= salesmen's cash ================= */
function Handover() {
  const { data, reload, error } = useApi<any>("/cashier/handovers");
  const [amt, setAmt] = useState<Record<number, string>>({});
  const [note, setNote] = useState<Record<number, string>>({});
  const [allDone, setAllDone] = useState(false);
  const { busy, run } = useAction();
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  return (
    <div className="grid gap-5 lg:grid-cols-2 [&>*]:min-w-0">
      <div className="space-y-3">
        <h2 className="font-semibold">Cash to take · <Ur>کیش وصول کرنا ہے</Ur></h2>
        {data.pending.map((s: any) => {
          const got = Number(amt[s.id] ?? s.cash_actual);
          const diff = Math.round(got - s.cash_actual);
          return (
            <div key={s.id} className="card space-y-2 p-3">
              <div><b>{s.attendant}</b><div className="text-xs text-slate-500">{s.station_name} · shift #{s.id} · closed {dt(s.closed_at)}</div></div>
              <div className="flex items-baseline justify-between gap-2 rounded-lg bg-slate-50 px-2.5 py-1.5 text-sm"><span className="min-w-0 text-slate-600">Salesman counted · <Ur>گنا</Ur></span><b className="shrink-0 whitespace-nowrap tabular-nums">{pkr(s.cash_actual)}</b></div>
              {s.variance ? <p className={`text-xs ${s.variance < 0 ? "text-red-600" : "text-emerald-700"}`}>At closing: {s.variance < 0 ? "short" : "over"} {pkr(Math.abs(s.variance))} against the sales</p> : null}
              <div className="grid grid-cols-[1fr,auto] gap-2">
                <label className="min-w-0"><span className="sr-only">Cash received</span>
                  <input className="input text-lg font-semibold tabular-nums" type="number" min={0} inputMode="numeric" value={amt[s.id] ?? String(s.cash_actual)} onChange={(e) => setAmt({ ...amt, [s.id]: e.target.value })} aria-label="Cash received" /></label>
                <button className="btn-primary self-start whitespace-nowrap" disabled={busy} onClick={async () => {
                  if (await run(() => api(`/cashier/handovers/${s.id}`, { body: { amount: got, note: note[s.id] || null } }), (r: any) => r.difference < 0 ? `Received — short ${pkr(-r.difference)}` : "Received · وصول")) reload();
                }}><Check size={16} /> Received · <Ur>وصول</Ur></button>
              </div>
              <p className="-mt-1 text-xs text-slate-500">You got · <Ur>آپ کو ملا</Ur> <b className="whitespace-nowrap tabular-nums">{pkr(got || 0)}</b></p>
              {diff !== 0 && <p className={`text-sm font-semibold ${diff < 0 ? "text-red-600" : "text-emerald-700"}`}>{diff < 0 ? `Short ${pkr(-diff)} · کم` : `Over ${pkr(diff)} · زیادہ`}</p>}
              {diff !== 0 && <input className="input" placeholder="Why the difference? · فرق کی وجہ" value={note[s.id] ?? ""} onChange={(e) => setNote({ ...note, [s.id]: e.target.value })} />}
            </div>
          );
        })}
        {!data.pending.length && <div className="card"><Empty>All shift cash received · <Ur>سب کیش وصول ہو گیا</Ur></Empty></div>}
      </div>
      <div className="card">
        <h2 className="p-4 pb-1 font-semibold">Received · <Ur>وصول شدہ</Ur></h2>
        <ul className="divide-y divide-slate-100">{(allDone ? data.done : data.done.slice(0, 8)).map((s: any) => {
          const diff = Math.round((s.handed_amount ?? 0) - (s.cash_actual ?? 0));
          return (
            <li key={s.id} className="flex items-center gap-2 px-4 py-2 text-sm">
              <span className="min-w-0 flex-1"><b>{s.attendant}</b> · #{s.id}<span className="block text-xs text-slate-500">{dt(s.handed_at)} · {s.handed_to}{s.handover_note ? ` · ${s.handover_note}` : ""}</span></span>
              <span className="shrink-0 text-right"><b className="tabular-nums">{pkr(s.handed_amount)}</b>{diff !== 0 && <span className={`block text-xs ${diff < 0 ? "text-red-600" : "text-emerald-700"}`}>{diff < 0 ? "short" : "over"} {pkr(Math.abs(diff))}</span>}</span>
            </li>
          );
        })}</ul>
        {data.done.length > 8 && !allDone && <button className="w-full border-t border-slate-100 py-2.5 text-sm font-medium text-brand-700" onClick={() => setAllDone(true)}>Show all {data.done.length} · <Ur>سب دیکھیں</Ur></button>}
      </div>
    </div>
  );
}

/* ================= day book ================= */
const DAYBOOK_FILTERS = [
  { k: "all", en: "All", ur: "سب" }, { k: "cash_in", en: "Cash in", ur: "نقد آیا" }, { k: "cash_out", en: "Cash out", ur: "نقد گیا" },
  { k: "bank", en: "Bank / digital", ur: "بینک" }, { k: "shift", en: "Shift cash", ur: "شفٹ" },
] as const;
const shiftDay = (d: string, n: number) => new Date(Date.parse(d + "T00:00:00Z") + n * 86400_000).toISOString().slice(0, 10);

function DayBook() {
  const [date, setDate] = useState(today());
  const [filter, setFilter] = useState<string>("all");
  const [q, setQ] = useState("");
  const [shown, setShown] = useState(60);
  const { data, error } = useApi<any>(`/cashier/daybook?date=${date}`);
  useEffect(() => setShown(60), [date, filter, q]);
  const rows = useMemo(() => {
    const all: any[] = data?.rows ?? [];
    const term = q.trim().toLowerCase();
    return all.filter((r) => {
      if (filter === "cash_in" && !(r.dir === "in" && r.cash)) return false;
      if (filter === "cash_out" && !(r.dir === "out" && r.cash)) return false;
      if (filter === "bank" && (r.cash || r.dir === "contra") && r.what !== "Cash deposited in bank") return false;
      if (filter === "shift" && !/^Shift cash/.test(r.what)) return false;
      return !term || [r.what, r.party, r.method, r.account, r.who, String(Math.round(r.amount))].join(" ").toLowerCase().includes(term);
    });
  }, [data, filter, q]);
  const isToday = date === today();
  const Line = ({ k, ur, v, sign, tone = "" }: { k: string; ur: string; v: number; sign: string; tone?: string }) => (
    <div className="flex items-baseline justify-between gap-3 py-1 text-sm"><span className="min-w-0">{sign} {k} · <Ur className="text-slate-500">{ur}</Ur></span><span className={`shrink-0 whitespace-nowrap font-semibold tabular-nums ${tone}`}>{pkr(v)}</span></div>
  );
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 print:hidden">
        <div className="flex items-center gap-1">
          <button className="btn-secondary min-h-10 !px-2" aria-label="Previous day" onClick={() => setDate(shiftDay(date, -1))}><ChevronLeft size={18} /></button>
          <input type="date" className="input min-h-10 w-[9.5rem] sm:w-auto" value={date} max={today()} onChange={(e) => e.target.value && setDate(e.target.value)} aria-label="Day" />
          <button className="btn-secondary min-h-10 !px-2" aria-label="Next day" disabled={isToday} onClick={() => setDate(shiftDay(date, 1))}><ChevronRight size={18} /></button>
        </div>
        {!isToday && <button className="btn-secondary min-h-10 !px-3 text-sm" onClick={() => setDate(today())}>Today<span className="hidden sm:inline"> · <Ur>آج</Ur></span></button>}
        <button className="btn-secondary min-h-10 !px-3 sm:ml-auto" aria-label="Print" onClick={() => window.print()}><Printer size={15} /><span className="hidden sm:inline"> Print · <Ur>پرنٹ</Ur></span></button>
      </div>
      {error && <ErrorBox error={error} />}
      {!data ? <Loading /> : <>
        <div className="grid gap-3 md:grid-cols-2 [&>*]:min-w-0">
          <div className="card p-4">
            <h3 className="mb-1 font-semibold">Cash · <Ur>نقد</Ur></h3>
            {data.cash.starts_today && <p className="mb-1 rounded-lg bg-amber-50 px-2 py-1.5 text-xs text-amber-800">The cash book starts with the cash count on this day; entries before it are listed but not added · <Ur>کیش بک اس دن کی گنتی سے شروع ہوئی</Ur></p>}
            {data.cash.opening != null ? <Line sign="" k={data.cash.starts_today ? "Cash counted" : "Opening cash"} ur={data.cash.starts_today ? "گنا گیا" : "شروع میں"} v={data.cash.opening} />
              : <p className="mb-1 rounded-lg bg-amber-50 px-2 py-1.5 text-xs text-amber-800">The cash was first counted later — no cash balance for this day · <Ur>اس دن کیش گنا نہیں گیا تھا</Ur></p>}
            <Line sign="+" k="Cash in" ur="آیا" v={data.totals.in_cash} tone="text-emerald-700" />
            <Line sign="−" k="Cash out" ur="گیا" v={data.totals.out_cash} tone="text-rose-700" />
            {data.totals.deposited > 0 && <Line sign="−" k="Put in bank" ur="بینک میں جمع" v={data.totals.deposited} tone="text-sky-700" />}
            {data.totals.withdrawn > 0 && <Line sign="+" k="Taken from bank" ur="بینک سے نکالا" v={data.totals.withdrawn} tone="text-sky-700" />}
            {Math.abs(data.totals.counted ?? 0) >= 1 && <Line sign={data.totals.counted < 0 ? "−" : "+"} k={data.totals.counted < 0 ? "Short at cash count" : "Over at cash count"} ur="گنتی میں فرق" v={Math.abs(data.totals.counted)} tone="text-amber-700" />}
            <div className="mt-1 flex items-baseline justify-between gap-3 border-t-2 border-slate-800 pt-1.5"><span className="min-w-0 font-semibold">= {isToday ? "Cash now" : "Closing cash"} · <Ur>آخر میں</Ur></span><span className="shrink-0 whitespace-nowrap text-lg font-bold tabular-nums">{data.cash.closing != null ? pkr(data.cash.closing) : "—"}</span></div>
          </div>
          <div className="card p-4">
            <h3 className="mb-1 font-semibold">Bank / digital · <Ur>بینک</Ur></h3>
            <Line sign="+" k="In" ur="آیا" v={data.totals.in_bank} tone="text-emerald-700" />
            <Line sign="−" k="Out" ur="گیا" v={data.totals.out_bank} tone="text-rose-700" />
            <p className="mt-1 text-xs text-slate-500">{data.rows.length} entries on this day · <Ur>اس دن کی اندراجات</Ur></p>
          </div>
        </div>
        <div className="card">
          <div className="space-y-2 border-b border-slate-100 p-3 print:hidden">
            <div className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-0.5">{DAYBOOK_FILTERS.map((f) => (
              <button key={f.k} onClick={() => setFilter(f.k)} aria-pressed={filter === f.k}
                className={`min-h-9 shrink-0 whitespace-nowrap rounded-full px-3 text-sm ring-1 ${filter === f.k ? "bg-brand-600 text-white ring-brand-600" : "bg-white text-slate-700 ring-slate-200"}`}>{f.en} · <Ur>{f.ur}</Ur></button>
            ))}</div>
            <label className="relative block"><Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input className="input min-h-10 pl-9" placeholder="Search name, amount… · تلاش" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search the day book" /></label>
            {(filter !== "all" || q) && <p className="text-xs text-slate-500">{rows.length} of {data.rows.length} · in {pkr(rows.filter((r) => r.dir === "in").reduce((a, r) => a + r.amount, 0))} · out {pkr(rows.filter((r) => r.dir === "out").reduce((a, r) => a + r.amount, 0))}</p>}
          </div>
          <ul className="divide-y divide-slate-100">
            {rows.slice(0, shown).map((r: any, i: number) => (
              <li key={i} className={`flex items-center gap-3 px-4 py-2 text-sm ${r.before_start ? "opacity-60" : ""}`}>
                <span className="min-w-0 flex-1"><span className="block font-medium">{r.what}{r.before_start && <span className="ml-1 text-xs font-normal text-amber-700">(before the count)</span>}</span><span className="block break-words text-xs text-slate-500">{[new Date(r.at).toLocaleTimeString("en-PK", { hour: "numeric", minute: "2-digit" }), r.party, r.method, r.account, r.who].filter(Boolean).join(" · ")}</span></span>
                <span className={`shrink-0 self-start whitespace-nowrap font-semibold tabular-nums ${r.dir === "in" ? "text-emerald-700" : r.dir === "out" ? "text-rose-700" : r.dir === "count" ? "text-amber-700" : "text-sky-700"}`}>{r.dir === "in" ? "+" : r.dir === "out" ? "−" : r.dir === "count" ? (r.start ? "=" : r.signed < 0 ? "−" : r.signed > 0 ? "+" : "✓") : "⇄"} {pkr(r.amount)}</span>
              </li>
            ))}
            {!rows.length && <li><Empty>{data.rows.length ? "Nothing matches · کچھ نہیں ملا" : <>Nothing on this day · <Ur>اس دن کچھ نہیں</Ur></>}</Empty></li>}
          </ul>
          {rows.length > shown && <button className="w-full border-t border-slate-100 py-2.5 text-sm font-medium text-brand-700 print:hidden" onClick={() => setShown(shown + 100)}>Show more ({rows.length - shown} left) · <Ur>مزید</Ur></button>}
        </div>
      </>}
    </div>
  );
}

/* ================= cash & bank ================= */
const NOTES = [5000, 1000, 500, 100, 50, 20, 10];

function CashBank({ start }: { start: string | null }) {
  const { data, reload } = useApi<any>("/cash");
  const { can } = useAuth();
  const [, setParams] = useSearchParams();
  const [form, setForm] = useState<string | null>(start);
  const [depSlip, setDepSlip] = useState<any>(null);
  const close = () => { setForm(null); setParams({ tab: "bank" }, { replace: true }); };
  if (!data) return <Loading />;
  return (
    <div className="space-y-5">
      <div className="card flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
        <div className="min-w-0 flex-1"><div className="text-sm text-slate-500">Cash in hand (should be) · <Ur>ہاتھ میں نقد</Ur></div><div className="text-2xl font-bold tabular-nums">{pkr(data.cash_in_hand)}</div>
          <div className="text-xs text-slate-500">{data.last_count ? `Last counted ${pkr(data.last_count.amount)} by ${data.last_count.by}, ${dt(data.last_count.at)}` : "Not counted yet"}</div></div>
        <div className="grid grid-cols-2 gap-2 sm:flex">
          <button className="btn-secondary" onClick={() => setForm("count")}><Calculator size={15} /> Count · <Ur>گنیں</Ur></button>
          <button className="btn-primary" onClick={() => setForm("deposit")}><Landmark size={15} /> Deposit · <Ur>جمع</Ur></button>
        </div>
      </div>
      {can("bank.view") && <BankAccounts cashInHand={data.cash_in_hand} onChanged={reload} />}
      <div className="card p-4 text-sm">
        <h2 className="mb-2 font-semibold">Last cash counts · <Ur>پچھلی گنتی</Ur></h2>
        <ul className="divide-y divide-slate-100">{data.counts.map((c: any) => (
          <li key={c.id} className="flex flex-wrap justify-between gap-x-3 py-1.5"><span>{dt(c.created_at)} · {c.counted_by}</span>
            <span className="tabular-nums">{pkr(c.amount)} {c.variance ? <span className={c.variance < 0 ? "text-red-600" : "text-emerald-700"}>({c.variance < 0 ? "short" : "over"} {pkr(Math.abs(c.variance))})</span> : null}</span></li>
        ))}{!data.counts.length && <li className="py-2 text-slate-500">No counts yet</li>}</ul>
        {can("expenses.view") && <Link to="/cash" className="mt-2 inline-block text-brand-700 underline">Full cash book →</Link>}
      </div>
      {form === "count" && <CountCash inHand={data.cash_in_hand} onClose={close} onDone={() => { close(); reload(); }} />}
      {form === "deposit" && <CashForm kind="deposit" inHand={data.cash_in_hand} onClose={close} onDone={(r) => { close(); reload(); if (r?.deposit) setDepSlip(r.deposit); }} />}
      {depSlip && <DepositSlip d={depSlip} onClose={() => setDepSlip(null)} />}
    </div>
  );
}

/** Count the drawer note by note; the difference from the book shows as you type. */
function CountCash({ inHand, onClose, onDone }: { inHand: number; onClose: () => void; onDone: () => void }) {
  const [n, setN] = useState<Record<string, string>>({});
  const [coins, setCoins] = useState("");
  const [note, setNote] = useState("");
  const [photos, setPhotos] = useState<number[]>([]);
  const { busy, run } = useAction();
  const total = NOTES.reduce((a, d) => a + d * (Number(n[d]) || 0), 0) + (Number(coins) || 0);
  const diff = Math.round(total - inHand);
  return (
    <Modal open onClose={onClose} title="Count the cash · کیش گنیں">
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const notes = Object.fromEntries(Object.entries(n).filter(([, v]) => Number(v) > 0).map(([k, v]) => [k, Number(v)]));
        if (await run(() => api("/cash/count", { body: { amount: total, notes, note: note || null, photo_ids: photos } }), (x: any) => x.variance ? `${x.variance < 0 ? "Short" : "Over"} ${pkr(Math.abs(x.variance))}` : "Matches the book · درست")) onDone();
      }}>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {NOTES.map((d) => (
            <label key={d} className="flex items-center gap-2 rounded-xl bg-slate-50 px-2 py-1.5">
              <span className="w-14 shrink-0 text-right text-sm font-semibold">Rs {d}</span><span className="text-slate-400">×</span>
              <input className="input !py-1.5 text-center" type="number" min={0} inputMode="numeric" value={n[d] ?? ""} onChange={(e) => setN({ ...n, [d]: e.target.value })} aria-label={`Rs ${d} notes`} />
            </label>
          ))}
          <label className="flex items-center gap-2 rounded-xl bg-slate-50 px-2 py-1.5"><span className="w-14 shrink-0 text-right text-sm font-semibold">Coins</span><span className="text-slate-400">=</span>
            <input className="input !py-1.5 text-center" type="number" min={0} value={coins} onChange={(e) => setCoins(e.target.value)} aria-label="Coins" /></label>
        </div>
        <div className="rounded-xl bg-slate-800 p-3 text-white">
          <div className="flex justify-between text-sm opacity-80"><span>Book says · <Ur>حساب کے مطابق</Ur></span><span className="tabular-nums">{pkr(inHand)}</span></div>
          <div className="flex items-baseline justify-between gap-2 text-xl font-bold"><span className="whitespace-nowrap">Counted · <Ur>گنا</Ur></span><span className="tabular-nums">{pkr(total)}</span></div>
          {total > 0 && <div className={`text-right text-sm font-semibold ${diff < 0 ? "text-red-300" : diff > 0 ? "text-emerald-300" : "text-emerald-300"}`}>{diff === 0 ? "Matches · درست" : diff < 0 ? `Short ${pkr(-diff)} · کم` : `Over ${pkr(diff)} · زیادہ`}</div>}
        </div>
        <Field label="Note · نوٹ"><input className="input" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        <ProofPhotos value={photos} onChange={setPhotos} hint="counted notes" />
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel · منسوخ</button><button className="btn-primary" disabled={busy || total <= 0}><HandCoins size={15} /> Save · <Ur>محفوظ</Ur></button></div>
      </form>
    </Modal>
  );
}
