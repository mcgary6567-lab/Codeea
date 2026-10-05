import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Plus, Search, MessageCircle, Wallet, BellRing, Car, Pencil } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Empty, ErrorBox, Field, Loading, Modal, PageHeader, Stat, statusTone, useAction } from "../components/ui";
import { ago, d, dt, num, phone, pkr } from "../lib/format";
import { useAuth } from "../App";
import KhataStatement from "../components/KhataStatement";
import { AccountForm } from "../components/QuickAdd";
import { TYPE_ICON } from "./Pos";
import { ProofPhotos, ProofThumbs } from "../components/Capture";
import { AccountPicker } from "../components/BankParts";
import { KhataVoice } from "../components/KhataVoice";
import { Ur } from "../components/VoiceShell";

const SEGMENTS = ["", "VIP", "Regular", "At risk", "New", "Fleet", "Agri", "Institution"];
const segTone: Record<string, string> = { VIP: "violet", Regular: "green", "At risk": "red", New: "blue", Fleet: "amber", Agri: "amber", Institution: "blue" };
const TYPES = [["retail", "Retail customer"], ["fleet", "Fleet / transport"], ["farmer", "Farmer"], ["business", "Business"], ["police", "Police station"], ["school", "School / college"], ["government", "Government office"], ["hospital", "Hospital / health"]];

export default function Customers() {
  const { id } = useParams();
  const { can } = useAuth();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [seg, setSeg] = useState("");
  const list = useApi<any[]>(`/customers?q=${encodeURIComponent(q)}&segment=${encodeURIComponent(seg)}`);
  const [adding, setAdding] = useState(false);

  return (
    <div>
      <PageHeader title="Customers" subtitle="AI-segmented CRM with churn & credit-risk scores"
        actions={<button className="btn-primary" onClick={() => setAdding(true)}><Plus size={16} /> {can("credit.set_limit") ? "Add customer / khata" : "Add customer"}</button>} />
      <div className="card">
        <div className="flex flex-wrap gap-2 border-b border-slate-200 p-3">
          <div className="relative min-w-0 flex-1 basis-56"><Search size={15} className="absolute left-2.5 top-2.5 text-slate-400" /><input className="input pl-8" placeholder="Search by name or phone" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <select className="input w-auto" value={seg} onChange={(e) => setSeg(e.target.value)}>
            {SEGMENTS.map((s) => <option key={s} value={s}>{s || "All segments"}</option>)}
          </select>
        </div>
        {list.error && <div className="p-3"><ErrorBox error={list.error} /></div>}
        {/* phone: one card per customer */}
        <ul className="divide-y divide-slate-100 sm:hidden">
          {(list.data ?? []).map((c) => (
            <li key={c.id} className="cursor-pointer px-4 py-3 active:bg-slate-50" onClick={() => nav(`/customers/${c.id}`)}>
              <div className="flex items-start justify-between gap-2">
                <span className="min-w-0 font-semibold">{TYPE_ICON[c.type] && c.type !== "retail" ? `${TYPE_ICON[c.type]} ` : ""}{c.name}</span>
                <span className="shrink-0 text-right"><span className="block font-semibold tabular-nums">{pkr(c.spend_30d ?? 0)}</span><span className="text-[11px] text-slate-500">30 days · <Ur>خرچ</Ur></span></span>
              </div>
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-slate-500">
                {c.segment && <Badge tone={segTone[c.segment]}>{c.segment}</Badge>}
                <span>{phone(c.phone)}</span>
                {c.balance > 0 && <span className="font-medium text-slate-700">Khata {pkr(c.balance)}</span>}
                <span>{num(c.loyalty_points)} pts</span>
                <span>Visit {ago(c.last_visit_at)}</span>
                {!c.opt_in && <span>opted out</span>}
              </div>
              <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
                <span className="flex items-center gap-1.5">Churn <RiskBar v={c.churn_score * 100} /></span>
                {c.credit_limit > 0 && <span className="flex items-center gap-1.5">Credit <RiskBar v={c.risk_score} /></span>}
              </div>
            </li>
          ))}
        </ul>
        {list.loading && !list.data && <div className="sm:hidden"><Loading /></div>}
        {list.data && !list.data.length && <div className="sm:hidden"><Empty>No customers found</Empty></div>}
        <div className="hidden overflow-x-auto sm:block">
          <table className="w-full">
            <thead><tr>
              <th className="th">Customer</th><th className="th">Segment</th><th className="th text-right">30-day spend</th><th className="th text-right">Khata</th>
              <th className="th text-right">Points</th><th className="th">Churn risk</th><th className="th">Credit risk</th><th className="th">Last visit</th>
            </tr></thead>
            <tbody>
              {(list.data ?? []).map((c) => (
                <tr key={c.id} className="cursor-pointer hover:bg-slate-50" onClick={() => nav(`/customers/${c.id}`)}>
                  <td className="td"><div className="font-medium">{TYPE_ICON[c.type] && c.type !== "retail" ? `${TYPE_ICON[c.type]} ` : ""}{c.name}</div><div className="text-xs text-slate-500">{phone(c.phone)} · {c.type}{!c.opt_in && " · opted out"}</div></td>
                  <td className="td">{c.segment && <Badge tone={segTone[c.segment]}>{c.segment}</Badge>}</td>
                  <td className="td text-right tabular-nums">{pkr(c.spend_30d ?? 0)}</td>
                  <td className="td text-right tabular-nums">{c.balance > 0 ? pkr(c.balance) : "—"}</td>
                  <td className="td text-right tabular-nums">{num(c.loyalty_points)}</td>
                  <td className="td"><RiskBar v={c.churn_score * 100} /></td>
                  <td className="td">{c.credit_limit > 0 ? <RiskBar v={c.risk_score} /> : <span className="text-xs text-slate-400">no credit</span>}</td>
                  <td className="td text-xs text-slate-500">{ago(c.last_visit_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {list.loading && !list.data && <Loading />}
          {list.data && !list.data.length && <Empty>No customers found</Empty>}
        </div>
      </div>
      {adding && <AccountForm khata={can("credit.set_limit")} onClose={() => setAdding(false)} onSaved={(c) => { setAdding(false); list.reload(); nav(`/customers/${c.id}`); }} />}
      {id && <CustomerDetail id={id} onClose={() => nav("/customers")} onChanged={list.reload} />}
    </div>
  );
}

function RiskBar({ v }: { v: number }) {
  const val = Math.round(v ?? 0);
  const color = val >= 60 ? "#e34948" : val >= 35 ? "#eda100" : "#1baf7a";
  return (
    <div className="flex items-center gap-2" title={`${val}/100`}>
      <div className="h-1.5 w-16 rounded-full bg-slate-100"><div className="h-full rounded-full" style={{ width: `${val}%`, background: color }} /></div>
      <span className="w-6 text-xs tabular-nums text-slate-600">{val}</span>
    </div>
  );
}

function CustomerForm({ open, onClose, onSaved, initial }: { open: boolean; onClose: () => void; onSaved: (c: any) => void; initial?: any }) {
  const { can } = useAuth();
  const [f, setF] = useState<any>(initial ?? { name: "", phone: "", type: "retail", city: "", credit_limit: 0, opt_in: true });
  const { busy, run } = useAction();
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const body: any = { ...f, credit_limit: Number(f.credit_limit) || 0 };
    if (!can("credit.set_limit")) delete body.credit_limit;
    const r = await run(() => initial ? api(`/customers/${initial.id}`, { method: "PATCH", body }) : api("/customers", { body }), "Customer saved");
    if (r) { onSaved(r); onClose(); }
  };
  return (
    <Modal open={open} onClose={onClose} title={initial ? "Edit customer" : "Add customer"}>
      <form onSubmit={save} className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Name"><input className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="WhatsApp number"><input className="input" required placeholder="03xx xxxxxxx" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
        <Field label="Type"><select className="input" value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })}>
          {TYPES.map(([t, l]) => <option key={t} value={t}>{l}</option>)}</select></Field>
        <Field label="City"><input className="input" value={f.city ?? ""} onChange={(e) => setF({ ...f, city: e.target.value })} /></Field>
        {can("credit.set_limit") ? <Field label="Khata credit limit (Rs)"><input className="input" type="number" min={0} value={f.credit_limit} onChange={(e) => setF({ ...f, credit_limit: e.target.value })} /></Field>
          : <div className="pt-5 text-xs text-slate-500">Khata credit limits are set by the admin.</div>}
        <label className="flex items-center gap-2 pt-6 text-sm"><input type="checkbox" checked={!!f.opt_in} onChange={(e) => setF({ ...f, opt_in: e.target.checked })} /> WhatsApp marketing opt-in</label>
        <div className="sm:col-span-2 flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

function CustomerDetail({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const { can } = useAuth();
  const { data: c, reload, error } = useApi<any>(`/customers/${id}`);
  const { busy, run } = useAction();
  const [pay, setPay] = useState<"credit" | "debit" | null>(null);
  const [amount, setAmount] = useState("");
  const [photos, setPhotos] = useState<number[]>([]);
  const [method, setMethod] = useState("Cash");
  const [account, setAccount] = useState<number | null>(null);
  const [msg, setMsg] = useState("");
  const [edit, setEdit] = useState(false);
  const [plate, setPlate] = useState("");
  const [vfuel, setVfuel] = useState("");
  const [vlimit, setVlimit] = useState("");
  const [bill, setBill] = useState(false);
  const refresh = () => { reload(); onChanged(); };

  return (
    <Modal open onClose={onClose} title={c?.name ?? "Customer"} wide>
      {error && <ErrorBox error={error} />}
      {!c ? <Loading /> : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
            {phone(c.phone)} · {c.type} · {c.city ?? "—"} {c.segment && <Badge tone={segTone[c.segment]}>{c.segment}</Badge>} {!c.opt_in && <Badge tone="slate">Opted out</Badge>}
            {can("customers.edit") && <button className="ml-auto min-h-9 px-1 text-xs text-brand-600 hover:underline" onClick={() => setEdit(true)}><Pencil size={12} className="inline" /> Edit</button>}
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Khata balance" value={pkr(c.balance)} hint={c.credit_limit ? `Limit ${pkr(c.credit_limit)}` : "No credit"} />
            <Stat label="Loyalty points" value={num(c.loyalty_points)} />
            <Stat label="Churn risk" value={`${Math.round(c.churn_score * 100)}%`} />
            <Stat label="Credit risk" value={c.credit_limit ? `${Math.round(c.risk_score)}/100` : "—"} />
          </div>
          <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap">
            {c.conversation && can("whatsapp.inbox") && <Link className="btn-secondary" to={`/inbox/${c.conversation.id}`}><MessageCircle size={15} /> Open chat</Link>}
            {can("khata.manage") && <button className="btn-secondary flex-col !gap-0 text-center sm:flex-row sm:!gap-1.5" onClick={() => setPay("credit")}><Wallet size={15} /> Receive payment<span className="hidden sm:inline"> · </span><Ur className="block text-xs sm:inline sm:text-sm">رقم وصول</Ur></button>}
            {can("khata.manage") && <button className="btn-secondary flex-col !gap-0 text-center sm:flex-row sm:!gap-1.5" onClick={() => setPay("debit")}>+ Add charge<span className="hidden sm:inline"> · </span><Ur className="block text-xs sm:inline sm:text-sm">کھاتے میں لکھیں</Ur></button>}
            {can("khata.manage") && (c.credit_limit > 0 || c.balance) ? <button className="btn-secondary flex-col !gap-0 text-center sm:flex-row sm:!gap-1.5" onClick={() => setBill(true)}>📄 Bill / statement<span className="hidden sm:inline"> · </span><Ur className="block text-xs sm:inline sm:text-sm">بل</Ur></button> : null}
            {c.balance > 0 && can("khata.manage") && <button className="btn-secondary flex-col !gap-0 text-center sm:flex-row sm:!gap-1.5" disabled={busy} onClick={() => run(() => api(`/customers/${c.id}/remind`, { body: {} }), "Reminder with payment link sent on WhatsApp").then(refresh)}><BellRing size={15} /> Send reminder<span className="hidden sm:inline"> · </span><Ur className="block text-xs sm:inline sm:text-sm">یاد دہانی</Ur></button>}
          </div>
          {can("khata.manage") && (c.credit_limit > 0 || c.balance) ? <KhataVoice compact customerId={c.id} onDone={refresh} /> : null}
          {pay && (
            <form className="flex flex-wrap items-end gap-2 rounded-lg bg-slate-50 p-3" onSubmit={async (e) => {
              e.preventDefault();
              const r = await run(() => api(`/customers/${c.id}/khata`, { body: { type: pay, amount: Number(amount), method, note: pay === "credit" ? "Payment received" : "Manual charge", photo_ids: photos, account_id: pay === "credit" ? account : null } }), pay === "credit" ? "Payment recorded & receipt sent on WhatsApp" : "Charge added");
              if (r) { setPay(null); setAmount(""); setPhotos([]); refresh(); }
            }}>
              <Field label={pay === "credit" ? "Payment amount · رقم وصول" : "Charge amount · کھاتے میں رقم"}><input className="input w-40" type="number" min={1} required value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
              <Field label="Method · طریقہ"><select className="input w-40" value={method} onChange={(e) => setMethod(e.target.value)}>{["Cash", "JazzCash", "Easypaisa", "Raast", "Bank transfer", "Cheque"].map((m) => <option key={m}>{m}</option>)}</select></Field>
              {pay === "credit" && <div className="w-full"><AccountPicker method={method} value={account} onChange={setAccount} /></div>}
              <div className="w-full"><ProofPhotos value={photos} onChange={setPhotos} required={pay === "credit" && method === "Cheque"} hint={pay === "credit" ? "cheque, receipt, payment screenshot" : "bill / slip for the charge"} /></div>
              <button className="btn-primary" disabled={busy || (pay === "credit" && method === "Cheque" && !photos.length)}>Save · محفوظ کریں</button><button type="button" className="btn-secondary" onClick={() => setPay(null)}>Cancel · منسوخ</button>
            </form>
          )}
          {can("whatsapp.inbox") && <form className="flex gap-2" onSubmit={async (e) => { e.preventDefault(); if (await run(() => api("/whatsapp/send", { body: { customer_id: c.id, text: msg } }), "Sent on WhatsApp")) { setMsg(""); refresh(); } }}>
            <input className="input" placeholder="Send a WhatsApp message…" value={msg} onChange={(e) => setMsg(e.target.value)} />
            <button className="btn-primary" disabled={busy || !msg}>Send</button>
          </form>}
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <section className="min-w-0">
              <h3 className="mb-2 text-sm font-semibold">Khata ledger · <Ur>کھاتہ</Ur></h3>
              <div className="max-h-64 overflow-y-auto rounded-lg border border-slate-200">
                <ul className="divide-y divide-slate-100 sm:hidden">
                  {c.ledger.map((l: any) => (
                    <li key={l.id} className="px-3 py-2">
                      <div className="flex items-start justify-between gap-2"><span className="min-w-0 text-sm">{l.product ? <>{num(l.litres, 2)} L {l.product} @ Rs {l.rate}</> : l.note ?? l.ref}</span>
                        <span className={`shrink-0 text-sm font-medium tabular-nums ${l.type === "credit" ? "text-emerald-600" : ""}`}>{l.type === "credit" ? "−" : "+"}{pkr(l.amount)}</span></div>
                      <div className="text-xs text-slate-500">{[d(l.created_at), l.vehicle_no, l.slip_no && `Slip ${l.slip_no}`].filter(Boolean).join(" · ")}</div>
                      <ProofThumbs ids={l.proof_ids} />
                    </li>
                  ))}
                </ul>
                <table className="hidden w-full sm:table"><tbody>
                  {c.ledger.map((l: any) => (
                    <tr key={l.id}><td className="td text-xs text-slate-500">{d(l.created_at)}</td>
                      <td className="td text-xs">{l.product ? <>{num(l.litres, 2)} L {l.product} @ Rs {l.rate}<div className="text-slate-400">{[l.vehicle_no, l.slip_no && `Slip ${l.slip_no}`].filter(Boolean).join(" · ")}</div></> : l.note ?? l.ref} <ProofThumbs ids={l.proof_ids} /></td>
                      <td className={`td text-right text-sm tabular-nums ${l.type === "credit" ? "text-emerald-600" : ""}`}>{l.type === "credit" ? "−" : "+"}{pkr(l.amount)}</td></tr>
                  ))}
                </tbody></table>
                {!c.ledger.length && <Empty>No khata entries</Empty>}
              </div>
            </section>
            <section className="min-w-0">
              <h3 className="mb-2 text-sm font-semibold">Recent purchases</h3>
              <div className="max-h-64 overflow-y-auto rounded-lg border border-slate-200">
                <ul className="divide-y divide-slate-100 sm:hidden">
                  {c.sales.map((s: any) => (
                    <li key={s.id} className="px-3 py-2">
                      <div className="flex items-start justify-between gap-2"><span className="min-w-0 text-sm">{num(s.litres, 1)} L {s.product}</span><span className="shrink-0 text-sm font-medium tabular-nums">{pkr(s.amount)}</span></div>
                      <div className="text-xs text-slate-500">{dt(s.created_at)} · <span className="capitalize">{s.payment_method}</span></div>
                    </li>
                  ))}
                </ul>
                <table className="hidden w-full sm:table"><tbody>
                  {c.sales.map((s: any) => (
                    <tr key={s.id}><td className="td text-xs text-slate-500">{dt(s.created_at)}</td><td className="td text-xs">{num(s.litres, 1)} L {s.product}</td>
                      <td className="td text-xs capitalize">{s.payment_method}</td><td className="td text-right text-sm tabular-nums">{pkr(s.amount)}</td></tr>
                  ))}
                </tbody></table>
                {!c.sales.length && <Empty>No purchases yet</Empty>}
              </div>
            </section>
            <section className="min-w-0">
              <h3 className="mb-2 flex items-center gap-1 text-sm font-semibold"><Car size={14} /> Vehicles</h3>
              <div className="flex flex-wrap gap-2">{c.vehicles.map((v: any) => (
                <span key={v.id} className="badge gap-1 bg-slate-100 text-slate-700">{v.plate_no} {v.fuel && `· ${v.fuel}`}
                  {can("customers.edit") ? <button className="min-h-9 px-1 text-brand-700 underline sm:min-h-0" title="Daily litre limit" onClick={() => {
                    const x = prompt(`Daily litre limit for ${v.plate_no} (empty = no limit)`, v.daily_limit_l ?? "");
                    if (x !== null) run(() => api(`/customers/${c.id}/vehicles/${v.id}`, { method: "PATCH", body: { fuel: v.fuel ?? null, daily_limit_l: Number(x) > 0 ? Number(x) : null } }), "Limit saved").then(reload);
                  }}>{v.daily_limit_l ? `${v.daily_limit_l} L/day` : "no limit"}</button> : v.daily_limit_l ? `· ${v.daily_limit_l} L/day` : null}
                  {can("customers.edit") && <button aria-label={`Remove ${v.plate_no}`} className="min-h-9 min-w-9 text-base text-slate-400 hover:text-red-600 sm:min-h-0 sm:min-w-0 sm:text-xs" onClick={() => confirm(`Remove ${v.plate_no}?`) && run(() => api(`/customers/${c.id}/vehicles/${v.id}`, { method: "DELETE" }), "Vehicle removed").then(reload)}>×</button>}
                </span>))}</div>
              <form className="mt-2 grid grid-cols-2 gap-2 sm:flex" onSubmit={async (e) => { e.preventDefault(); if (await run(() => api(`/customers/${c.id}/vehicles`, { body: { plate_no: plate, fuel: vfuel || null, daily_limit_l: Number(vlimit) > 0 ? Number(vlimit) : null } }), c.credit_limit > 0 ? "Vehicle added — salesmen notified" : "Vehicle added")) { setPlate(""); setVlimit(""); reload(); } }}>
                <input className="input col-span-2" placeholder="LEA-1234" value={plate} onChange={(e) => setPlate(e.target.value)} />
                <select className="input sm:w-28" value={vfuel} onChange={(e) => setVfuel(e.target.value)} aria-label="Fuel"><option value="">Any fuel</option><option value="PMG">Petrol</option><option value="HOBC">Hi-Octane</option><option value="HSD">Diesel</option></select>
                <input className="input sm:w-28" type="number" min={1} placeholder="L / day" aria-label="Daily litre limit" value={vlimit} onChange={(e) => setVlimit(e.target.value)} />
                <button className="btn-secondary col-span-2" disabled={!plate}>Add</button>
              </form>
            </section>
            <section className="min-w-0">
              <h3 className="mb-2 text-sm font-semibold">Orders & complaints</h3>
              <ul className="space-y-1 text-sm">
                {c.orders.map((o: any) => <li key={"o" + o.id}>#{o.id} {num(o.litres)} L {o.product} — <Badge tone={statusTone(o.status)}>{o.status}</Badge></li>)}
                {c.complaints.map((k: any) => <li key={"k" + k.id}>C-{k.id} {k.category} — <Badge tone={statusTone(k.status)}>{k.status}</Badge></li>)}
                {!c.orders.length && !c.complaints.length && <li className="text-slate-500">None</li>}
              </ul>
            </section>
          </div>
          {edit && <CustomerForm open initial={c} onClose={() => setEdit(false)} onSaved={refresh} />}
          {bill && <KhataStatement customerId={c.id} onClose={() => setBill(false)} />}
        </div>
      )}
    </Modal>
  );
}
