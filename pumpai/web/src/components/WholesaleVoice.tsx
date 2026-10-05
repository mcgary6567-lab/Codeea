import { useState } from "react";
import { Volume2, X, Check, AlertTriangle } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Field, useAction } from "./ui";
import { PRODUCTS, num, pkr } from "../lib/format";
import { ProofPhotos } from "./Capture";
import { VoiceShell, speak, Ur } from "./VoiceShell";
import { AccountPicker, BankLogo, BankNamePicker } from "./BankParts";
import { useAuth } from "../App";

const INTENT: Record<string, { en: string; ur: string; cls: string }> = {
  supply: { en: "Supply", ur: "سپلائی", cls: "bg-brand-600" }, return: { en: "Fuel return", ur: "واپسی", cls: "bg-amber-600" },
  payment: { en: "Payment received", ur: "ادائیگی وصول", cls: "bg-emerald-600" }, order: { en: "Order", ur: "آرڈر", cls: "bg-amber-500" },
  promise: { en: "Payment promise", ur: "وعدہ", cls: "bg-sky-600" }, cheque: { en: "Cheque received", ur: "چیک", cls: "bg-violet-600" },
  trip: { en: "Tanker trip", ur: "ٹینکر ٹرپ", cls: "bg-slate-800" }, balance: { en: "Balance", ur: "بقایا", cls: "bg-slate-600" },
  today: { en: "Today", ur: "آج", cls: "bg-slate-600" }, unknown: { en: "Not understood", ur: "سمجھ نہیں آیا", cls: "bg-red-600" },
};
const EXAMPLES = [
  { en: "Shah Transport ko 5000 litre diesel bheja tanker 3412", ur: "شاہ ٹرانسپورٹ کو پانچ ہزار لیٹر ڈیزل بھیجا" },
  { en: "Malik Petroleum se 2 lakh bank transfer mila", ur: "ملک پیٹرولیم سے دو لاکھ ملے" },
  { en: "Green Fields ka kal 8000 litre diesel ka order", ur: "گرین فیلڈز کا کل آٹھ ہزار لیٹر کا آرڈر" },
  { en: "Shah Transport jumma ko 3 lakh dega", ur: "شاہ ٹرانسپورٹ جمعہ کو تین لاکھ دے گا" },
  { en: "Malik ka baqaya kitna hai?", ur: "ملک کا بقایا کتنا ہے؟" },
  { en: "Shah ko 5000 aur Malik ko 3000 litre diesel", ur: "ایک ٹینکر، دو کلائنٹس" },
];
const METHODS = ["Cash", "Bank transfer", "Cheque", "Raast", "JazzCash", "Easypaisa"];
const today = (n = 0) => new Date(Date.now() + 5 * 3600_000 + n * 86_400_000).toISOString().slice(0, 10);

/**
 * Wholesale by voice: say the entry in Urdu / Roman Urdu / English, check the card, press Save.
 * Nothing is saved until the officer confirms.
 */
export function WholesaleVoice({ clientId, onDone, compact }: { clientId?: number; onDone: () => void; compact?: boolean }) {
  return (
    <VoiceShell endpoint="/wholesale/ai/command" body={{ client_id: clientId ?? null }} compact={compact} examples={EXAMPLES}
      sub='Supply, payment, order, promise, cheque, return — or ask "baqaya kitna hai?"' placeholder="e.g. Shah ko 5000 litre diesel bheja · لکھیں"
      result={(r, close) => <Result r={r} onClose={close} onSaved={() => { close(); onDone(); }} />} />
  );
}

function Result({ r, onClose, onSaved }: { r: any; onClose: () => void; onSaved: () => void }) {
  const { can } = useAuth();
  const clients = useApi<any[]>("/wholesale/clients?q=");
  const fleet = useApi<any>("/wholesale/fleet");
  const [f, setF] = useState<any>({
    client_id: r.client_id ? String(r.client_id) : "", product: r.product ?? "HSD", litres: r.litres ?? "", amount: r.amount ?? "", rate: r.rate ?? "",
    method: r.method ?? "Cash", date: r.date ?? (r.intent === "order" || r.intent === "promise" ? today(1) : today()), bank: r.bank ?? "", cheque_no: r.cheque_no ?? "",
    tanker_id: r.tanker_id ? String(r.tanker_id) : "", driver_id: r.driver_id ? String(r.driver_id) : "", vehicle_no: r.tanker_id ? "" : r.tanker ?? "", location: r.location ?? "",
    drops: r.drops?.map((d: any) => ({ ...d })) ?? [],
  });
  const [account, setAccount] = useState<number | null>(null);
  const [photos, setPhotos] = useState<number[]>([]);
  const { busy, run } = useAction();
  const I = INTENT[r.intent] ?? INTENT.unknown;
  const set = (k: string, v: any) => setF({ ...f, [k]: v });
  const client = (clients.data ?? []).find((c) => c.id === Number(f.client_id));
  const rate = Number(f.rate) || client?.rates?.[f.product] || 0;
  const pv = r.preview;

  if (r.answer) return (
    <div className="space-y-2">
      <div className="flex items-center gap-2"><span className={`rounded-lg px-2.5 py-1 text-sm font-bold text-white ${I.cls}`}>{I.en} · <Ur>{I.ur}</Ur></span>
        <span className="flex-1 truncate text-xs text-slate-500">“{r.heard}”</span>
        <button className="btn-secondary !px-2 !py-1" onClick={() => speak(r.answer.ur)} aria-label="Read aloud"><Volume2 size={15} /></button>
        <button className="text-slate-400" onClick={onClose} aria-label="Close"><X size={18} /></button></div>
      <p className="text-lg" lang="ur" dir="rtl"><span className="font-urdu leading-loose">{r.answer.ur}</span></p>
      <p className="text-sm text-slate-600">{r.answer.en}</p>
    </div>
  );

  if (r.intent === "unknown") return (
    <div className="flex items-start gap-2 text-sm">
      <AlertTriangle className="shrink-0 text-amber-600" size={18} />
      <div className="flex-1">Could not understand “{r.heard}”. Say the client's name, litres or rupees and what happened — e.g. “Shah ko 5000 litre diesel bheja”. · <Ur>کلائنٹ کا نام، لیٹر یا رقم اور کام بتائیں</Ur></div>
      <button className="text-slate-400" onClick={onClose} aria-label="Close"><X size={18} /></button>
    </div>
  );

  const save = async () => {
    const id = Number(f.client_id);
    const fl = { tanker_id: f.tanker_id ? Number(f.tanker_id) : null, driver_id: f.driver_id ? Number(f.driver_id) : null, vehicle_no: f.tanker_id ? null : f.vehicle_no || null };
    const rateBody = can("wholesale.rates") && f.rate ? { rate: Number(f.rate) } : {};
    const calls: Record<string, () => Promise<any>> = {
      supply: () => api(`/wholesale/clients/${id}/supply`, { body: { station_id: r.station_id, product: f.product, litres: Number(f.litres), txn_date: f.date, location: f.location || null, photo_ids: photos, ...fl, ...rateBody } }),
      return: () => api(`/wholesale/clients/${id}/return`, { body: { station_id: r.station_id, product: f.product, litres: Number(f.litres), txn_date: f.date, vehicle_no: r.tanker ?? null, photo_ids: photos } }),
      payment: () => api(`/wholesale/clients/${id}/payment`, { body: { amount: Number(f.amount), method: f.method, txn_date: f.date, photo_ids: photos, account_id: account } }),
      order: () => api(`/wholesale/clients/${id}/orders`, { body: { product: f.product, litres: Number(f.litres), needed_on: f.date, location: f.location || null } }),
      promise: () => api(`/wholesale/clients/${id}/promises`, { body: { amount: Number(f.amount), promised_on: f.date } }),
      cheque: () => api(`/wholesale/clients/${id}/cheques`, { body: { amount: Number(f.amount), bank: f.bank, cheque_no: f.cheque_no, cheque_date: f.date, photo_ids: photos } }),
      trip: () => api("/wholesale/trips", { body: { station_id: r.station_id, product: f.product, txn_date: f.date, photo_ids: photos, ...fl, drops: f.drops.map((d: any) => ({ client_id: d.client_id, litres: Number(d.litres) })) } }),
    };
    const done = await run(calls[r.intent], (x: any) => x?.due_after != null ? `Saved · محفوظ — due now ${pkr(x.due_after)}` : "Saved · محفوظ ہو گیا");
    if (done) onSaved();
  };
  const needPhoto = r.intent === "cheque" || (r.intent === "payment" && f.method === "Cheque");
  const ready = r.intent === "trip" ? f.drops.length > 0 && r.station_id : f.client_id && (["supply", "return", "order"].includes(r.intent) ? Number(f.litres) > 0 : Number(f.amount) > 0)
    && (!["supply", "return", "trip"].includes(r.intent) || r.station_id) && (!needPhoto || photos.length) && (r.intent !== "cheque" || (f.bank && f.cheque_no));

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className={`rounded-lg px-2.5 py-1 text-sm font-bold text-white ${I.cls}`}>{I.en} · <Ur>{I.ur}</Ur></span>
        <span className="flex-1 truncate text-xs text-slate-500" title={r.heard}>“{r.heard}”{r.engine === "claude" ? " · AI" : ""}</span>
        {r.confirm_ur && <button className="btn-secondary !px-2 !py-1" onClick={() => speak(r.confirm_ur)} aria-label="Read aloud"><Volume2 size={15} /></button>}
        <button className="text-slate-400" onClick={onClose} aria-label="Close"><X size={18} /></button>
      </div>
      {r.confirm_ur && <p className="text-lg"><Ur className="leading-loose">{r.confirm_ur}</Ur></p>}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {r.intent !== "trip" && <Field label="Client · کلائنٹ"><select className="input" value={f.client_id} onChange={(e) => set("client_id", e.target.value)}>
          <option value="">— choose —</option>{(clients.data ?? []).filter((c) => c.active).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>}
        {["supply", "return", "order", "trip"].includes(r.intent) && <Field label="Fuel · تیل"><select className="input" value={f.product} onChange={(e) => set("product", e.target.value)}>
          {Object.entries(PRODUCTS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>}
        {["supply", "return", "order"].includes(r.intent) && <Field label="Litres · لیٹر"><input className="input text-lg" type="number" value={f.litres} onChange={(e) => set("litres", e.target.value)} /></Field>}
        {["payment", "promise", "cheque"].includes(r.intent) && <Field label="Amount (Rs) · رقم"><input className="input text-lg" type="number" value={f.amount} onChange={(e) => set("amount", e.target.value)} /></Field>}
        {r.intent === "payment" && <Field label="Method · طریقہ"><select className="input" value={f.method} onChange={(e) => set("method", e.target.value)}>{METHODS.map((m) => <option key={m}>{m}</option>)}</select></Field>}
        {r.intent === "cheque" && <Field label="Cheque no. · چیک نمبر"><input className="input" value={f.cheque_no} onChange={(e) => set("cheque_no", e.target.value)} /></Field>}
        <Field label={r.intent === "order" ? "Deliver on · کب" : r.intent === "promise" ? "Will pay on · کب" : r.intent === "cheque" ? "Cheque date · تاریخ" : "Date · تاریخ"}>
          <input className="input" type="date" value={f.date} onChange={(e) => set("date", e.target.value)} /></Field>
        {(r.intent === "supply" || r.intent === "trip") && <>
          <Field label="Tanker · ٹینکر"><select className="input" value={f.tanker_id} onChange={(e) => set("tanker_id", e.target.value)}><option value="">{f.vehicle_no || "— none —"}</option>{(fleet.data?.tankers ?? []).filter((t: any) => t.active).map((t: any) => <option key={t.id} value={t.id}>{t.number}</option>)}</select></Field>
          <Field label="Driver · ڈرائیور"><select className="input" value={f.driver_id} onChange={(e) => set("driver_id", e.target.value)}><option value="">— tanker's driver —</option>{(fleet.data?.drivers ?? []).filter((d: any) => d.active).map((d: any) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></Field>
        </>}
        {(r.intent === "supply" || r.intent === "order") && <Field label="Drop location · جگہ"><input className="input" value={f.location} onChange={(e) => set("location", e.target.value)} /></Field>}
        {r.intent === "supply" && can("wholesale.rates") && <Field label="Rate (blank = rate card)"><input className="input" type="number" step="0.01" placeholder={client?.rates?.[f.product] ? String(client.rates[f.product]) : ""} value={f.rate} onChange={(e) => set("rate", e.target.value)} /></Field>}
      </div>
      {r.intent === "cheque" && <div><span className="label">Bank on the cheque · بینک</span>
        {f.bank ? <div className="flex items-center gap-2 rounded-xl bg-slate-50 p-2"><BankLogo name={f.bank} size={32} /><b className="flex-1">{f.bank}</b><button className="text-sm text-brand-700 underline" onClick={() => set("bank", "")}>Change</button></div>
          : <BankNamePicker required value={f.bank} onChange={(v) => set("bank", v)} />}</div>}
      {r.intent === "payment" && <AccountPicker method={f.method} value={account} onChange={setAccount} />}

      {r.intent === "trip" && (
        <div className="rounded-xl ring-1 ring-slate-200">
          {f.drops.map((d: any, i: number) => {
            const pr = pv?.drops?.find((x: any) => x.client_id === d.client_id);
            return (
              <div key={i} className="flex items-center gap-2 border-b border-slate-100 px-3 py-2 last:border-0">
                <span className="flex-1 font-medium">{d.client_name}</span>
                <input className="input w-28 text-right" type="number" value={d.litres} onChange={(e) => setF({ ...f, drops: f.drops.map((x: any, j: number) => (j === i ? { ...x, litres: e.target.value } : x)) })} /> L
                <span className="w-28 text-right text-sm tabular-nums text-slate-600">{pr?.rate ? pkr(pr.rate * Number(d.litres)) : "no rate"}</span>
                <button className="text-slate-400 hover:text-red-600" onClick={() => setF({ ...f, drops: f.drops.filter((_: any, j: number) => j !== i) })} aria-label="Remove"><X size={15} /></button>
              </div>
            );
          })}
        </div>
      )}

      {/* what it means for the account */}
      {r.intent !== "trip" && pv && f.client_id && Number(f.client_id) === r.client_id && (
        <div className="flex flex-wrap gap-x-5 gap-y-1 rounded-xl bg-slate-50 px-3 py-2 text-sm">
          <span>Due now · <Ur>بقایا</Ur>: <b>{pkr(pv.due)}</b></span>
          {["supply", "return"].includes(r.intent) && Number(f.litres) > 0 && rate > 0 && <span>{num(Number(f.litres))} L × Rs {rate} = <b>{pkr(Number(f.litres) * rate)}</b></span>}
          {["supply", "return", "payment"].includes(r.intent) && <span>Due after · <Ur>بعد میں</Ur>: <b>{pkr(pv.due + (r.intent === "supply" ? Number(f.litres) * rate : r.intent === "return" ? -Number(f.litres) * rate : -Number(f.amount)))}</b></span>}
        </div>
      )}
      {(r.intent === "supply" || r.intent === "return" || r.intent === "trip") && r.station_name && <p className="text-xs text-slate-500">From / into {r.station_name}</p>}
      {pv?.over_limit && <Warn>Crosses the credit limit ({pkr(pv.credit_limit)}) — the admin has to allow it · <Ur>ادھار حد سے زیادہ</Ur></Warn>}
      {pv?.no_rate && <Warn>No rate set for this fuel — ask the admin to set the rate first · <Ur>ریٹ نہیں لگا</Ur></Warn>}
      {pv?.stock_short && <Warn>Not enough stock in the tank · <Ur>ٹینک میں تیل کم ہے</Ur></Warn>}
      {r.missing?.length > 0 && <Warn>Please fill: {r.missing.map((m: string) => m.replace("_id", "").replace("_", " ")).join(", ")} · <Ur>باقی خانے بھریں</Ur></Warn>}
      {(needPhoto || ["supply", "return", "trip"].includes(r.intent)) && <ProofPhotos value={photos} onChange={setPhotos} required={needPhoto} hint={needPhoto ? "photo of the cheque" : "delivery note / chalan (optional)"} />}

      <div className="flex gap-2">
        <button className="btn-secondary flex-1 sm:flex-none" onClick={onClose}><X size={16} /> Cancel · <Ur>منسوخ</Ur></button>
        <button className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-emerald-600 px-6 py-3 text-lg font-bold text-white shadow active:scale-95 disabled:bg-slate-300 sm:flex-none" disabled={busy || !ready} onClick={save}>
          <Check size={22} /> Save · <Ur>محفوظ کریں</Ur>
        </button>
      </div>
    </div>
  );
}

const Warn = ({ children }: { children: React.ReactNode }) => <p className="flex items-start gap-1.5 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900"><AlertTriangle size={15} className="mt-0.5 shrink-0" /><span>{children}</span></p>;
