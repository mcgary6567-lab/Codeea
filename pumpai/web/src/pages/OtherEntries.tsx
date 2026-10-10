import { useEffect, useState } from "react";
import { api, useApi } from "../lib/api";
import { Field, Loading, PageHeader, Stat, useAction } from "../components/ui";
import { AccountPicker } from "../components/BankParts";
import { pkr, num, dt } from "../lib/format";
import { TrendingUp, TrendingDown, Percent, Trash2, Search, Banknote, Landmark, Smartphone, ArrowLeftRight } from "lucide-react";

/** Payment methods with icons — same set the cashier uses. Cash → note count; anything else → a bank/wallet account. */
const PAY = [
  { m: "Cash", ur: "نقد", icon: Banknote },
  { m: "Bank transfer", ur: "بینک", icon: Landmark },
  { m: "Raast", ur: "راست", icon: ArrowLeftRight },
  { m: "JazzCash", ur: "جاز کیش", icon: Smartphone },
  { m: "Easypaisa", ur: "ایزی پیسہ", icon: Smartphone },
] as const;
const DENOMS = [5000, 1000, 500, 100, 50, 20, 10];
const isCashM = (m: string) => /^cash$/i.test(m);
const notesTotal = (n: Record<string, string>) => DENOMS.reduce((a, d) => a + d * (Number(n[d]) || 0), 0);

type Kind = "income" | "expense" | "discount";
type PType = "khata" | "wholesale" | "carriage" | "other";

const KINDS: { k: Kind; en: string; short: string; ur: string; icon: any; tone: "green" | "red" | "amber" }[] = [
  { k: "income", en: "Other income", short: "Income", ur: "اضافی آمدن", icon: TrendingUp, tone: "green" },
  { k: "expense", en: "Other expense", short: "Expense", ur: "اضافی خرچہ", icon: TrendingDown, tone: "red" },
  { k: "discount", en: "Other discount", short: "Discount", ur: "رعایت", icon: Percent, tone: "amber" },
];
const PTYPES: { v: PType; label: string }[] = [
  { v: "khata", label: "Khata customer" }, { v: "wholesale", label: "Wholesale client" }, { v: "carriage", label: "Thekedar" }, { v: "other", label: "Other (type name)" },
];

/** CEO-only: record other income / expense / discount, tied to a party with a reason, and see a per-party report. */
export default function OtherEntries() {
  const [kind, setKind] = useState<Kind>("income");
  const meta = KINDS.find((x) => x.k === kind)!;
  const today = new Date().toISOString().slice(0, 10);
  const [day, setDay] = useState(today);
  const { data: report, reload } = useApi<any>(`/other-entries?kind=${kind}&from=${day}&to=${day}`);

  return (
    <div>
      <PageHeader title="Other income / expense / discount · متفرق" subtitle="Sirf CEO — misc aamdan, kharcha ya ra'ayat kisi party ke saath, wajah ke sath. Profit aur report me update." />
      <div className="mb-4 grid grid-cols-3 gap-1.5 sm:gap-2">
        {KINDS.map((x) => (
          <button key={x.k} onClick={() => setKind(x.k)} className={`flex flex-col items-center justify-center gap-1 rounded-xl px-1 py-2 text-xs font-semibold sm:flex-row sm:gap-1.5 sm:px-3 sm:py-2.5 sm:text-sm ${kind === x.k ? (x.tone === "green" ? "bg-emerald-600 text-white" : x.tone === "red" ? "bg-rose-600 text-white" : "bg-amber-500 text-white") : "bg-slate-100 text-slate-600"}`}>
            <x.icon size={16} className="shrink-0" /> <span>{x.short}</span>
          </button>
        ))}
      </div>

      <EntryForm kind={kind} meta={meta} onSaved={reload} />

      <div className="mt-6">
        <Field label="Date · تاریخ"><input type="date" className="input w-full sm:w-auto" value={day} max={today} onChange={(e) => setDay(e.target.value)} /></Field>
      </div>
      {!report ? <Loading /> : (
        <>
          <div className="my-3 grid grid-cols-2 gap-3">
            <Stat label={`${meta.en} (din ka total)`} value={pkr(report.total)} tone={meta.tone} />
            <Stat label="Entries" value={report.count} />
          </div>
          {report.by_party.length === 0 ? <p className="card p-4 text-center text-sm text-slate-500">Is din koi {meta.short.toLowerCase()} nahi.</p> : (
            <div className="card mb-4 p-4">
              <h2 className="mb-2 font-semibold">Party ke hisab se · فی پارٹی</h2>
              <div className="-mx-1 overflow-x-auto">
                <table className="w-full min-w-[320px] text-sm">
                  <thead><tr><th className="th">Party</th><th className="th hidden sm:table-cell">Type</th><th className="th text-right">#</th><th className="th text-right">{meta.short}</th></tr></thead>
                  <tbody>{report.by_party.map((p: any, i: number) => (
                    <tr key={i} className="border-t"><td className="td font-medium">{p.party_name}<span className="block text-xs text-slate-400 sm:hidden">{PTYPES.find((x) => x.v === p.party_type)?.label ?? p.party_type}</span></td><td className="td hidden text-slate-500 sm:table-cell">{PTYPES.find((x) => x.v === p.party_type)?.label ?? p.party_type}</td><td className="td text-right">{p.count}</td><td className="td text-right font-semibold">{pkr(p.total)}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
          )}
          {report.list.length > 0 && (
            <div className="card p-4">
              <h2 className="mb-2 font-semibold">Entries · تفصیل</h2>
              <div className="space-y-2">
                {report.list.map((e: any) => (
                  <div key={e.id} className="flex items-center gap-2 rounded-lg border p-2 text-sm">
                    <div className="min-w-0 flex-1">
                      <div className="font-medium">{e.party_name} · <span className="font-semibold">{pkr(e.amount)}</span></div>
                      <div className="truncate text-xs text-slate-500">{e.reason}{e.method ? ` · ${e.method}` : ""} · {dt(e.created_at)}</div>
                    </div>
                    <button onClick={() => { if (confirm(`Undo this ${meta.en.toLowerCase()} of ${pkr(e.amount)}?`)) api(`/other-entries/${e.id}`, { method: "DELETE" }).then(reload); }}
                      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-500 hover:bg-rose-100 hover:text-rose-600" aria-label="Undo"><Trash2 size={15} /></button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function EntryForm({ kind, meta, onSaved }: { kind: Kind; meta: { en: string; tone: string }; onSaved: () => void }) {
  const { busy, run } = useAction();
  const [ptype, setPtype] = useState<PType>("khata");
  const [q, setQ] = useState("");
  const [party, setParty] = useState<{ id: number; name: string } | null>(null);
  const [otherName, setOtherName] = useState("");
  const [reason, setReason] = useState("");
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState("Cash");
  const [account, setAccount] = useState<number | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const { data: parties } = useApi<any>(ptype === "other" ? null : `/other-entries/parties?q=${encodeURIComponent(q)}`);
  const cash = isCashM(method);

  // discount can't use a free-text party
  useEffect(() => { if (kind === "discount" && ptype === "other") setPtype("khata"); }, [kind, ptype]);
  useEffect(() => { setParty(null); }, [ptype]);
  useEffect(() => { setAccount(null); }, [method]); // a new method needs its own account chosen

  const list = ptype === "other" ? [] : (parties?.[ptype] ?? []);
  const reset = () => { setParty(null); setOtherName(""); setReason(""); setAmount(""); setQ(""); setMethod("Cash"); setAccount(null); setNotes({}); };

  const submit = () => {
    const amt = Number(amount);
    if (!(amt > 0)) return run(() => Promise.reject(new Error("Amount theek likhein")), "");
    const b: any = { kind, party_type: ptype, reason, amount: amt,
      ...(ptype === "other" ? { party_name: otherName } : { party_id: party?.id }),
      ...(kind === "discount" ? {} : { method, account_id: cash ? null : account,
        notes: cash ? Object.fromEntries(Object.entries(notes).filter(([, n]) => Number(n) > 0).map(([k, n]) => [k, Number(n)])) : undefined }) };
    run(() => api("/other-entries", { body: b }), `${meta.en} saved`).then(() => { reset(); onSaved(); });
  };

  const ready = reason.trim().length >= 2 && Number(amount) > 0 && (ptype === "other" ? otherName.trim().length >= 2 : !!party)
    && (kind === "discount" || cash || !!account);

  return (
    <div className="card space-y-3 p-4">
      <h2 className="font-semibold">Nayi entry · نئی اندراج</h2>
      <Field label="Kis party ka? · کس کی؟">
        <div className="flex flex-wrap gap-1.5">
          {PTYPES.filter((p) => !(kind === "discount" && p.v === "other")).map((p) => (
            <button key={p.v} onClick={() => setPtype(p.v)} className={`rounded-full px-3 py-1 text-xs font-semibold ${ptype === p.v ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-600"}`}>{p.label}</button>
          ))}
        </div>
      </Field>

      {ptype === "other" ? (
        <Field label="Naam · نام"><input className="input" value={otherName} onChange={(e) => setOtherName(e.target.value)} placeholder="e.g. Scrap wala / Mistri" /></Field>
      ) : (
        <Field label="Party dhoondein aur chunein · منتخب کریں">
          <div className="relative mb-2"><Search size={15} className="absolute left-2.5 top-2.5 text-slate-400" /><input className="input pl-8" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Naam ya phone se dhoondein…" /></div>
          <select className="input" value={party?.id ?? ""} onChange={(e) => { const c = list.find((x: any) => String(x.id) === e.target.value); setParty(c ? { id: c.id, name: c.name } : null); }}>
            <option value="">— {list.length} party mili, chunein —</option>
            {list.map((c: any) => <option key={c.id} value={c.id}>{c.name}{c.business_name ? ` · ${c.business_name}` : c.phone ? ` · ${c.phone}` : ""}</option>)}
          </select>
        </Field>
      )}

      <Field label="Kis cheez ka? (wajah) · وجہ"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder={kind === "income" ? "e.g. Generator kiraya" : kind === "expense" ? "e.g. Gate welding" : "e.g. Purani adjustment"} /></Field>
      <Field label="Amount (Rs)"><input className="input text-lg font-semibold" type="number" inputMode="decimal" min={1} value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0" /></Field>

      {kind !== "discount" && (
        <Field label={kind === "income" ? "Paisa kahan aaya? · رقم کہاں آئی" : "Paisa kahan se gaya? · رقم کہاں سے گئی"}>
          <div className="grid grid-cols-3 gap-1.5 sm:grid-cols-5">
            {PAY.map((p) => (
              <button type="button" key={p.m} onClick={() => setMethod(p.m)} aria-pressed={method === p.m}
                className={`flex flex-col items-center justify-center gap-1 rounded-xl px-1 py-2 text-xs font-semibold ${method === p.m ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-700"}`}>
                <p.icon size={18} className="shrink-0" /> <span className="leading-tight">{p.m === "Bank transfer" ? "Bank" : p.m}</span>
              </button>
            ))}
          </div>
        </Field>
      )}
      {kind !== "discount" && cash && <NoteGrid notes={notes} onChange={setNotes} amount={Number(amount) || 0} />}
      {kind !== "discount" && !cash && <AccountPicker method={method} value={account} onChange={setAccount} required label="Kaun sa account? · کون سا اکاؤنٹ" />}

      {kind === "discount" && <p className="rounded-lg bg-amber-50 p-2 text-xs text-amber-800">Discount se is party ka outstanding {pkr(Number(amount) || 0)} kam ho jayega, aur profit bhi utna kam hoga.</p>}
      <button disabled={busy || !ready} onClick={submit} className={`btn-primary w-full ${!ready ? "opacity-60" : ""}`}>{busy ? "Saving…" : `${meta.en} save karein`}</button>
    </div>
  );
}

/** Cash note breakdown — count the notes, check it matches the amount (same as the cashier). */
function NoteGrid({ notes, onChange, amount }: { notes: Record<string, string>; onChange: (n: Record<string, string>) => void; amount: number }) {
  const total = notesTotal(notes);
  const diff = total - amount;
  return (
    <fieldset className="rounded-xl bg-slate-50 p-3">
      <legend className="label">Cash notes · <span lang="ur" dir="rtl" className="font-urdu">نوٹوں کی تفصیل</span> <span className="text-xs font-normal text-slate-400">(optional)</span></legend>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3">
        {DENOMS.map((dn) => {
          const n = Number(notes[dn]) || 0;
          return (
            <div key={dn} className="flex items-center gap-2">
              <span className="w-12 shrink-0 text-right text-sm tabular-nums text-slate-600">{dn}</span>
              <span className="text-slate-400">×</span>
              <input className="input !py-1.5 w-16 text-center" type="number" min={0} inputMode="numeric" value={notes[dn] ?? ""} aria-label={`Rs ${dn} notes`}
                onChange={(e) => onChange({ ...notes, [dn]: e.target.value })} />
              <span className="min-w-0 flex-1 text-right text-xs tabular-nums text-slate-500">{n > 0 ? pkr(dn * n) : ""}</span>
            </div>
          );
        })}
      </div>
      {total > 0 && (
        <div className={`mt-2 flex items-center justify-between rounded-lg px-3 py-1.5 text-sm ${diff === 0 ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-900"}`}>
          <span>Notes total <b className="tabular-nums">{pkr(total)}</b></span>
          <span>{diff === 0 ? "Matches ✓" : diff > 0 ? `Rs ${num(Math.abs(diff))} zyada` : `Rs ${num(Math.abs(diff))} kam`}</span>
        </div>
      )}
    </fieldset>
  );
}
