import { useState } from "react";
import { AlertTriangle, Check, Volume2, X } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Field, useAction } from "./ui";
import { pkr } from "../lib/format";
import { ProofPhotos } from "./Capture";
import { AccountPicker } from "./BankParts";
import { VoiceShell, speak, Ur } from "./VoiceShell";

const INTENT: Record<string, { en: string; ur: string; cls: string }> = {
  payment: { en: "Payment received", ur: "ادائیگی وصول", cls: "bg-emerald-600" }, charge: { en: "Add to khata", ur: "کھاتے میں لکھیں", cls: "bg-amber-600" },
  reminder: { en: "WhatsApp reminder", ur: "یاد دہانی", cls: "bg-sky-600" }, bill: { en: "Send bill", ur: "بل بھیجیں", cls: "bg-violet-600" },
  balance: { en: "Balance", ur: "بقایا", cls: "bg-slate-600" }, top: { en: "Who owes most", ur: "سب سے زیادہ ادھار", cls: "bg-slate-600" },
  unknown: { en: "Not understood", ur: "سمجھ نہیں آیا", cls: "bg-red-600" },
};
const EXAMPLES = [
  { en: "Police Station Kahna se 20 hazar naqd mile", ur: "پولیس اسٹیشن کاہنہ سے بیس ہزار ملے" },
  { en: "Rescue 1122 ke khate mein 5000 likh do tyre repair", ur: "ریسکیو کے کھاتے میں پانچ ہزار لکھ دو" },
  { en: "City Bakers ka khata kitna hai?", ur: "سٹی بیکرز کا کھاتہ کتنا ہے؟" },
  { en: "City Bakers ko reminder bhejo", ur: "سٹی بیکرز کو یاد دہانی بھیجو" },
  { en: "Sab se zyada udhaar kis ka hai?", ur: "سب سے زیادہ ادھار کس کا ہے؟" },
];
const METHODS = ["Cash", "Bank transfer", "Cheque", "Raast", "JazzCash", "Easypaisa"];

/** Khata by voice: payment, charge, reminder, bill — or ask a balance. Nothing is saved until confirmed. */
export function KhataVoice({ customerId, onDone, compact }: { customerId?: number; onDone: () => void; compact?: boolean }) {
  return (
    <VoiceShell endpoint="/khata/ai/command" body={{ customer_id: customerId ?? null }} compact={compact} examples={EXAMPLES}
      sub='Payment received, add to khata, reminder, bill — or ask "khata kitna hai?"' placeholder="e.g. Police Station se 20 hazar mile · لکھیں"
      result={(r, close) => <Result r={r} onClose={close} onSaved={() => { close(); onDone(); }} />} />
  );
}

function Result({ r, onClose, onSaved }: { r: any; onClose: () => void; onSaved: () => void }) {
  const customers = useApi<any[]>("/khata");
  const [f, setF] = useState<any>({ customer_id: r.customer_id ? String(r.customer_id) : "", amount: r.amount ?? "", method: r.method ?? "Cash", note: r.note ?? "", notify: true });
  const [account, setAccount] = useState<number | null>(null);
  const [photos, setPhotos] = useState<number[]>([]);
  const { busy, run } = useAction();
  const I = INTENT[r.intent] ?? INTENT.unknown;
  const set = (k: string, v: any) => setF({ ...f, [k]: v });
  const c = (customers.data ?? []).find((x) => x.id === Number(f.customer_id));
  const head = (
    <div className="flex items-center gap-2">
      <span className={`rounded-lg px-2.5 py-1 text-sm font-bold text-white ${I.cls}`}>{I.en} · <Ur>{I.ur}</Ur></span>
      <span className="flex-1 truncate text-xs text-slate-500" title={r.heard}>“{r.heard}”{r.engine === "claude" ? " · AI" : ""}</span>
      {(r.answer || r.confirm_ur) && <button className="btn-secondary !px-2 !py-1" onClick={() => speak(r.answer?.ur ?? r.confirm_ur)} aria-label="Read aloud"><Volume2 size={15} /></button>}
      <button className="text-slate-400" onClick={onClose} aria-label="Close"><X size={18} /></button>
    </div>
  );
  if (r.answer) return (
    <div className="space-y-2">{head}
      <p className="text-lg"><Ur className="leading-loose">{r.answer.ur}</Ur></p>
      <p className="text-sm text-slate-600">{r.answer.en}</p>
    </div>
  );
  if (r.intent === "unknown") return (
    <div className="space-y-2">{head}
      <p className="flex gap-2 text-sm"><AlertTriangle className="shrink-0 text-amber-600" size={18} />Say the customer's name and the amount — e.g. “Police Station se 20 hazar mile”. · <Ur>گاہک کا نام اور رقم بتائیں</Ur></p>
    </div>
  );
  const id = Number(f.customer_id);
  const isCheque = /cheque/i.test(f.method);
  const save = () => {
    const call = {
      payment: () => api(`/customers/${id}/khata`, { body: { type: "credit", amount: Number(f.amount), method: f.method, note: "Payment received", notify: f.notify, photo_ids: photos, account_id: account } }),
      charge: () => api(`/customers/${id}/khata`, { body: { type: "debit", amount: Number(f.amount), note: f.note || "Charge", photo_ids: photos } }),
      reminder: () => api(`/customers/${id}/remind`, { body: {} }),
      bill: () => api(`/customers/${id}/send-bill`, { body: {} }),
    }[r.intent as "payment" | "charge" | "reminder" | "bill"];
    return run(call, r.intent === "payment" ? (x: any) => `Saved · محفوظ — khata now ${pkr(x.balance)}` : r.intent === "charge" ? (x: any) => `Added · لکھ دیا — khata now ${pkr(x.balance)}` : "Sent on WhatsApp · بھیج دیا").then((ok) => { if (ok) onSaved(); });
  };
  const money = r.intent === "payment" || r.intent === "charge";
  const after = c && Number(f.amount) ? (r.intent === "payment" ? c.balance - Number(f.amount) : r.intent === "charge" ? c.balance + Number(f.amount) : null) : null;
  const ready = id && (!money || Number(f.amount) > 0) && (!isCheque || r.intent !== "payment" || photos.length);
  return (
    <div className="space-y-3">{head}
      {r.confirm_ur && <p className="text-lg"><Ur className="leading-loose">{r.confirm_ur}</Ur></p>}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Field label="Customer · گاہک"><select className="input" value={f.customer_id} onChange={(e) => set("customer_id", e.target.value)}>
          <option value="">— choose —</option>{(customers.data ?? []).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
        {money && <Field label="Amount (Rs) · رقم"><input className="input text-lg" type="number" value={f.amount} onChange={(e) => set("amount", e.target.value)} /></Field>}
        {r.intent === "payment" && <Field label="Method · طریقہ"><select className="input" value={f.method} onChange={(e) => set("method", e.target.value)}>{METHODS.map((m) => <option key={m}>{m}</option>)}</select></Field>}
        {r.intent === "charge" && <Field label="For what · کس چیز کا"><input className="input" value={f.note} onChange={(e) => set("note", e.target.value)} /></Field>}
      </div>
      {r.intent === "payment" && <AccountPicker method={f.method} value={account} onChange={setAccount} />}
      {c && <div className="flex flex-wrap gap-x-5 gap-y-1 rounded-xl bg-slate-50 px-3 py-2 text-sm">
        <span>Khata now · <Ur>ابھی</Ur>: <b>{pkr(c.balance)}</b></span>
        {c.credit_limit > 0 && <span>Limit · <Ur>حد</Ur>: {pkr(c.credit_limit)}</span>}
        {after != null && <span>After · <Ur>بعد میں</Ur>: <b>{pkr(after)}</b></span>}
      </div>}
      {r.intent === "charge" && c?.credit_limit > 0 && after != null && after > c.credit_limit && <p className="flex gap-1.5 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900"><AlertTriangle size={15} className="mt-0.5 shrink-0" />Goes over the credit limit · <Ur>ادھار حد سے زیادہ</Ur></p>}
      {(r.intent === "reminder" || r.intent === "bill") && c && !c.phone && <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">No WhatsApp number for this customer · <Ur>نمبر نہیں ہے</Ur></p>}
      {r.intent === "payment" && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.notify} onChange={(e) => set("notify", e.target.checked)} /> Send the receipt on WhatsApp · <Ur>رسید واٹس ایپ پر</Ur></label>}
      {money && <ProofPhotos value={photos} onChange={setPhotos} required={r.intent === "payment" && isCheque} hint={r.intent === "payment" ? "receipt / cheque / screenshot" : "bill / slip (optional)"} />}
      <div className="flex gap-2">
        <button className="btn-secondary flex-1 sm:flex-none" onClick={onClose}><X size={16} /> Cancel · <Ur>منسوخ</Ur></button>
        <button className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-emerald-600 px-6 py-3 text-lg font-bold text-white shadow active:scale-95 disabled:bg-slate-300 sm:flex-none" disabled={busy || !ready} onClick={save}>
          <Check size={22} /> {money ? <>Save · <Ur>محفوظ کریں</Ur></> : <>Send · <Ur>بھیجیں</Ur></>}
        </button>
      </div>
    </div>
  );
}
