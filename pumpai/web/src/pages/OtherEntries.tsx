import { useEffect, useState } from "react";
import { api, useApi } from "../lib/api";
import { Field, Loading, PageHeader, Stat, useAction } from "../components/ui";
import { pkr, num, dt } from "../lib/format";
import { TrendingUp, TrendingDown, Percent, Trash2, Search } from "lucide-react";

type Kind = "income" | "expense" | "discount";
type PType = "khata" | "wholesale" | "carriage" | "other";

const KINDS: { k: Kind; en: string; ur: string; icon: any; tone: "green" | "red" | "amber"; verb: string }[] = [
  { k: "income", en: "Other income", ur: "اضافی آمدن", icon: TrendingUp, tone: "green", verb: "aayi" },
  { k: "expense", en: "Other expense", ur: "اضافی خرچہ", icon: TrendingDown, tone: "red", verb: "gayi" },
  { k: "discount", en: "Other discount", ur: "رعایت", icon: Percent, tone: "amber", verb: "di" },
];
const PTYPES: { v: PType; label: string }[] = [
  { v: "khata", label: "Khata customer" }, { v: "wholesale", label: "Wholesale client" }, { v: "carriage", label: "Thekedar" }, { v: "other", label: "Other (type name)" },
];

/** CEO-only: record other income / expense / discount, tied to a party with a reason, and see a per-party report. */
export default function OtherEntries() {
  const [kind, setKind] = useState<Kind>("income");
  const meta = KINDS.find((x) => x.k === kind)!;
  const today = new Date().toISOString().slice(0, 10);
  const [from, setFrom] = useState(`${today.slice(0, 7)}-01`);
  const [to, setTo] = useState(today);
  const { data: report, reload } = useApi<any>(`/other-entries?kind=${kind}&from=${from}&to=${to}`);

  return (
    <div>
      <PageHeader title="Other income / expense / discount · متفرق" subtitle="Sirf CEO — misc aamdan, kharcha ya ra'ayat kisi party ke saath, wajah ke sath. Profit aur report me update." />
      <div className="mb-4 flex gap-2">
        {KINDS.map((x) => (
          <button key={x.k} onClick={() => setKind(x.k)} className={`flex flex-1 items-center justify-center gap-1.5 rounded-xl px-3 py-2.5 text-sm font-semibold ${kind === x.k ? (x.tone === "green" ? "bg-emerald-600 text-white" : x.tone === "red" ? "bg-rose-600 text-white" : "bg-amber-500 text-white") : "bg-slate-100 text-slate-600"}`}>
            <x.icon size={16} /> {x.en}
          </button>
        ))}
      </div>

      <EntryForm kind={kind} meta={meta} onSaved={reload} />

      <div className="mt-6 flex flex-wrap items-end gap-2">
        <Field label="From"><input type="date" className="input w-auto" value={from} max={to} onChange={(e) => setFrom(e.target.value)} /></Field>
        <Field label="To"><input type="date" className="input w-auto" value={to} min={from} onChange={(e) => setTo(e.target.value)} /></Field>
      </div>
      {!report ? <Loading /> : (
        <>
          <div className="my-3 grid grid-cols-2 gap-3">
            <Stat label={`${meta.en} (total)`} value={pkr(report.total)} tone={meta.tone} />
            <Stat label="Entries" value={report.count} />
          </div>
          {report.by_party.length === 0 ? <p className="card p-4 text-center text-sm text-slate-500">Is range me koi {meta.en.toLowerCase()} nahi.</p> : (
            <div className="card mb-4 p-4">
              <h2 className="mb-2 font-semibold">Party ke hisab se · فی پارٹی</h2>
              <table className="w-full text-sm">
                <thead><tr><th className="th">Party</th><th className="th">Type</th><th className="th text-right">Entries</th><th className="th text-right">{meta.en}</th></tr></thead>
                <tbody>{report.by_party.map((p: any, i: number) => (
                  <tr key={i} className="border-t"><td className="td font-medium">{p.party_name}</td><td className="td text-slate-500">{PTYPES.find((x) => x.v === p.party_type)?.label ?? p.party_type}</td><td className="td text-right">{p.count}</td><td className="td text-right font-semibold">{pkr(p.total)}</td></tr>
                ))}</tbody>
              </table>
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
  const [dest, setDest] = useState("cash"); // "cash" or a bank account id (string)
  const { data: parties } = useApi<any>(ptype === "other" ? null : `/other-entries/parties?q=${encodeURIComponent(q)}`);
  const { data: acctData } = useApi<any>(kind === "discount" ? null : "/bank/accounts/pick");
  const accounts = acctData?.accounts ?? [];

  // discount can't use a free-text party
  useEffect(() => { if (kind === "discount" && ptype === "other") setPtype("khata"); }, [kind, ptype]);
  useEffect(() => { setParty(null); }, [ptype]);

  const list = ptype === "other" ? [] : (parties?.[ptype] ?? []);
  const reset = () => { setParty(null); setOtherName(""); setReason(""); setAmount(""); setQ(""); setDest("cash"); };

  const submit = () => {
    const amt = Number(amount);
    if (!(amt > 0)) return run(() => Promise.reject(new Error("Amount theek likhein")), "");
    const bank = dest !== "cash" ? Number(dest) : null;
    const b: any = { kind, party_type: ptype, reason, amount: amt,
      ...(ptype === "other" ? { party_name: otherName } : { party_id: party?.id }),
      ...(kind === "discount" ? {} : { method: bank ? "bank" : "cash", account_id: bank }) };
    run(() => api("/other-entries", { body: b }), `${meta.en} saved`).then(() => { reset(); onSaved(); });
  };

  const ready = reason.trim().length >= 2 && Number(amount) > 0 && (ptype === "other" ? otherName.trim().length >= 2 : !!party);

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
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Amount (Rs)"><input className="input" type="number" inputMode="decimal" min={1} value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0" /></Field>
        {kind !== "discount" && (
          <Field label={kind === "income" ? "Paisa kahan aaya? · کہاں آیا" : "Paisa kahan se gaya? · کہاں سے"}>
            <select className="input" value={dest} onChange={(e) => setDest(e.target.value)}>
              <option value="cash">Cash (hath me)</option>
              {accounts.map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </Field>
        )}
      </div>
      {kind === "discount" && <p className="rounded-lg bg-amber-50 p-2 text-xs text-amber-800">Discount se is party ka outstanding {pkr(Number(amount) || 0)} kam ho jayega, aur profit bhi utna kam hoga.</p>}
      <button disabled={busy || !ready} onClick={submit} className={`btn-primary w-full ${!ready ? "opacity-60" : ""}`}>{busy ? "Saving…" : `${meta.en} save karein`}</button>
    </div>
  );
}
