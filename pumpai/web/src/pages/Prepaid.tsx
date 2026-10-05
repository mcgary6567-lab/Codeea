import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import QRCode from "qrcode";
import { Ticket, Wallet, Plus, Printer, ArrowLeft, Ban } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Empty, ErrorBox, Field, Loading, Modal, PageHeader, Stat, useAction } from "../components/ui";
import { dt, pkr, PRODUCTS } from "../lib/format";
import { useAuth } from "../App";
import { AccountPicker } from "../components/BankParts";

const METHODS = ["cash", "bank", "raast", "easypaisa", "jazzcash", "cheque"];

/** Prepaid fuel: coupon books sold in advance, and company wallets that are paid first and used per fill. */
export default function Prepaid() {
  const [tab, setTab] = useState<"coupons" | "wallets">(() => (location.hash === "#wallets" ? "wallets" : "coupons"));
  return (
    <div className="space-y-5">
      <PageHeader title="Coupons & wallets" subtitle="Money received first, fuel given later — scanned at the POS, no paper khata" />
      <div className="flex gap-1 border-b border-slate-200">
        {([["coupons", "Fuel coupons", Ticket], ["wallets", "Company wallets", Wallet]] as const).map(([k, l, I]) => (
          <button key={k} onClick={() => { setTab(k); history.replaceState(null, "", `#${k}`); }}
            className={`-mb-px flex items-center gap-2 border-b-2 px-4 py-2 text-sm font-medium ${tab === k ? "border-brand-600 text-brand-700" : "border-transparent text-slate-500"}`}><I size={15} />{l}</button>
        ))}
      </div>
      {tab === "coupons" ? <Coupons /> : <Wallets />}
    </div>
  );
}

function Coupons() {
  const { data, reload } = useApi<any>("/coupons");
  const { can } = useAuth();
  const { run } = useAction();
  const [sell, setSell] = useState(false);
  if (!data) return <Loading />;
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="Unused coupons (fuel owed)" value={pkr(data.summary.outstanding)} hint={`${data.summary.active} coupons not used yet`} tone="amber" />
        <Stat label="Used" value={pkr(data.summary.used_value)} hint={`${data.summary.used} coupons scanned at the pump`} tone="green" />
        <div className="card flex items-center justify-center p-4"><button className="btn-primary" onClick={() => setSell(true)}><Plus size={15} /> Sell a coupon book</button></div>
      </div>
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500"><tr>
            <th className="px-3 py-2">Book</th><th>Buyer</th><th className="text-right">Coupons</th><th className="text-right">Value</th><th>Used</th><th>Expiry</th><th /></tr></thead>
          <tbody className="divide-y divide-slate-100">
            {data.batches.map((b: any) => (
              <tr key={b.batch}>
                <td className="px-3 py-2"><b className="font-mono">{b.batch}</b><span className="block text-xs text-slate-500">{dt(b.sold_at)} · {b.sold_by} · {b.method}</span></td>
                <td>{b.buyer ?? "—"}{b.product && <span className="block text-xs text-slate-500">{PRODUCTS[b.product]} only</span>}</td>
                <td className="text-right tabular-nums">{b.n} × {pkr(b.value)}</td>
                <td className="text-right font-semibold tabular-nums">{pkr(b.n * b.value)}</td>
                <td><div className="h-2 w-24 overflow-hidden rounded bg-slate-100"><div className="h-full bg-emerald-500" style={{ width: `${(b.used / b.n) * 100}%` }} /></div><span className="text-xs text-slate-500">{b.used} used{b.void ? ` · ${b.void} cancelled` : ""}</span></td>
                <td className="text-xs">{b.expires_on ?? "—"}</td>
                <td className="whitespace-nowrap px-3 text-right">
                  <Link className="btn-secondary px-2 py-1 text-xs" to={`/coupons/${b.batch}`}><Printer size={13} /> Print / view</Link>
                  {can("settings.manage") && b.n - b.used - b.void > 0 && <button className="ml-1 rounded p-1 text-slate-400 hover:text-red-600" title="Cancel unused coupons of this book"
                    onClick={() => { const reason = prompt("Why cancel the unused coupons of this book? (lost, refunded…)"); if (reason) run(() => api("/coupons/void", { body: { batch: b.batch, reason } }), "Unused coupons cancelled").then(reload); }}><Ban size={15} /></button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!data.batches.length && <Empty>No coupons sold yet. Sell a book to a company or for gifts — each coupon is scanned once at the POS.</Empty>}
      </div>
      {sell && <SellCoupons onClose={() => setSell(false)} onDone={() => { setSell(false); reload(); }} />}
    </>
  );
}

function SellCoupons({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ count: "10", value: "1000", product: "", buyer: "", method: "cash", expires_on: "" });
  const { busy, run } = useAction();
  const total = (Number(f.count) || 0) * (Number(f.value) || 0);
  return (
    <Modal open onClose={onClose} title="Sell a coupon book">
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const r = await run(() => api("/coupons", { body: { count: Number(f.count), value: Number(f.value), product: f.product || null, buyer: f.buyer || null, method: f.method, expires_on: f.expires_on || null } }), "Coupons made — print them");
        if (r) { onDone(); location.assign(`/coupons/${(r as any).batch}`); }
      }}>
        <div className="grid grid-cols-2 gap-3">
          <Field label="How many coupons"><input className="input" type="number" min={1} max={500} required value={f.count} onChange={(e) => setF({ ...f, count: e.target.value })} /></Field>
          <Field label="Value of each (Rs)">
            <div className="flex gap-1">{[500, 1000, 2000, 5000].map((v) => <button type="button" key={v} onClick={() => setF({ ...f, value: String(v) })} className={`flex-1 rounded-lg py-2 text-sm ${f.value === String(v) ? "bg-brand-600 text-white" : "bg-slate-100"}`}>{v.toLocaleString()}</button>)}</div>
          </Field>
          <Field label="Fuel"><select className="input" value={f.product} onChange={(e) => setF({ ...f, product: e.target.value })}><option value="">Any fuel</option>{Object.entries(PRODUCTS).map(([k, v]) => <option key={k} value={k}>{v} only</option>)}</select></Field>
          <Field label="Valid till (optional)"><input className="input" type="date" value={f.expires_on} onChange={(e) => setF({ ...f, expires_on: e.target.value })} /></Field>
          <Field label="Sold to"><input className="input" placeholder="Company / person" value={f.buyer} onChange={(e) => setF({ ...f, buyer: e.target.value })} /></Field>
          <Field label="Money received by"><select className="input" value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })}>{METHODS.map((m) => <option key={m}>{m}</option>)}</select></Field>
        </div>
        <div className="rounded-lg bg-emerald-50 p-3 text-center">Total received: <b className="text-xl">{pkr(total)}</b></div>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy || !total}>Make coupons</button></div>
      </form>
    </Modal>
  );
}

/** Printable sheet of coupons with QR codes (8 per A4 page). */
export function CouponSheet() {
  const { batch } = useParams();
  const { data, error } = useApi<any>(`/coupons/batch/${batch}`);
  if (error) return <div className="p-6"><ErrorBox error={error} /></div>;
  if (!data) return <Loading />;
  return (
    <div className="min-h-screen bg-slate-100 p-4 print:bg-white print:p-0">
      <div className="mx-auto mb-4 flex max-w-4xl flex-wrap items-center gap-2 print:hidden">
        <Link to="/prepaid" className="btn-secondary"><ArrowLeft size={15} /> Back</Link>
        <h1 className="text-lg font-semibold">Coupon book {data.batch}</h1>
        <button className="btn-primary ml-auto" onClick={() => window.print()}><Printer size={15} /> Print</button>
      </div>
      <div className="mx-auto grid max-w-4xl gap-3 sm:grid-cols-2 print:grid-cols-2 print:gap-2">
        {data.coupons.map((c: any) => <CouponCard key={c.code} business={data.tenant} c={c} />)}
      </div>
    </div>
  );
}

function CouponCard({ business, c }: { business: string; c: any }) {
  const [svg, setSvg] = useState("");
  useEffect(() => { QRCode.toString(`PUMPAI-${c.code}`, { type: "svg", margin: 1, errorCorrectionLevel: "M" }).then(setSvg); }, [c.code]);
  return (
    <div className={`relative flex h-[58mm] break-inside-avoid overflow-hidden rounded-xl border-2 border-dashed border-amber-600 bg-amber-50 print:bg-white ${c.status !== "active" ? "opacity-60" : ""}`}>
      <div className="flex flex-1 flex-col justify-between p-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-amber-800">⛽ {business}</div>
          <div className="text-sm font-semibold">Fuel coupon · <span lang="ur" dir="rtl" className="font-urdu">فیول کوپن</span></div>
        </div>
        <div className="text-4xl font-black tabular-nums">Rs {c.value.toLocaleString()}</div>
        <div className="text-[11px] leading-snug text-slate-600">
          {c.product ? `${PRODUCTS[c.product]} only · ` : ""}{c.expires_on ? `Valid till ${c.expires_on} · ` : ""}One time use<br />
          <span className="font-mono text-xs">{c.code}</span>
        </div>
      </div>
      <div className="flex w-[42%] items-center justify-center bg-white p-2" aria-label={`QR code ${c.code}`} dangerouslySetInnerHTML={{ __html: svg }} />
      {c.status !== "active" && <div className="absolute right-2 top-2 print:hidden"><Badge tone={c.status === "used" ? "green" : "red"}>{c.status === "used" ? `Used ${c.used_at?.slice(0, 10)}${c.vehicle_no ? ` · ${c.vehicle_no}` : ""}` : "Cancelled"}</Badge></div>}
    </div>
  );
}

function Wallets() {
  const { data, reload } = useApi<any>("/wallets");
  const customers = useApi<any[]>("/customers?limit=500");
  const [open, setOpen] = useState<any>(null);
  const [pick, setPick] = useState("");
  if (!data) return <Loading />;
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="Money held in wallets" value={pkr(data.total)} hint="Fuel owed to companies that paid in advance" tone="blue" />
        <Stat label="Low wallets" value={data.wallets.filter((w: any) => w.low).length} hint="They got a WhatsApp to top up" tone="amber" />
        <div className="card flex items-center gap-2 p-4">
          <select className="input" value={pick} onChange={(e) => setPick(e.target.value)} aria-label="Customer">
            <option value="">Start a wallet for…</option>
            {(customers.data ?? []).filter((c) => !data.wallets.some((w: any) => w.id === c.id)).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <button className="btn-primary" disabled={!pick} onClick={() => setOpen({ id: Number(pick), name: customers.data!.find((c) => c.id === Number(pick))?.name, wallet_balance: 0 })}>Deposit</button>
        </div>
      </div>
      <div className="card divide-y divide-slate-100">
        {data.wallets.map((w: any) => (
          <button key={w.id} onClick={() => setOpen(w)} className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-slate-50">
            <Wallet className={w.low ? "text-amber-600" : "text-brand-600"} size={20} />
            <span className="flex-1"><b>{w.name}</b><span className="block text-xs text-slate-500">{w.last_deposit ? `Last deposit ${dt(w.last_deposit)}` : "No deposit yet"} · used {pkr(w.used_30d)} in 30 days{w.days_left != null ? ` · ~${w.days_left} days left` : ""}</span></span>
            {w.low && <Badge tone="amber">Low</Badge>}
            <span className="text-lg font-bold tabular-nums">{pkr(w.wallet_balance)}</span>
          </button>
        ))}
        {!data.wallets.length && <Empty>No wallets yet. A fleet or company pays first (Raast / bank), every fill is taken from the wallet, and they get the balance on WhatsApp.</Empty>}
      </div>
      {open && <WalletModal w={open} onClose={() => { setOpen(null); reload(); }} />}
    </>
  );
}

function WalletModal({ w, onClose }: { w: any; onClose: () => void }) {
  const { data, reload } = useApi<any>(`/customers/${w.id}/wallet`);
  const [f, setF] = useState({ amount: "", method: "raast", ref: "", type: "deposit" });
  const [account, setAccount] = useState<number | null>(null);
  const { busy, run } = useAction();
  const [low, setLow] = useState<string>("");
  useEffect(() => { if (data) setLow(String(data.wallet_low)); }, [data?.wallet_low]);
  return (
    <Modal open wide onClose={onClose} title={`Wallet — ${w.name}`}>
      {!data ? <Loading /> : <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-3 rounded-xl bg-sky-50 p-3">
          <span className="flex-1 text-sm text-slate-600">Balance<b className="block text-3xl text-slate-900 tabular-nums">{pkr(data.wallet_balance)}</b></span>
          <label className="text-sm">WhatsApp when below Rs<div className="flex gap-1"><input className="input w-28" type="number" min={0} value={low} onChange={(e) => setLow(e.target.value)} />
            <button className="btn-secondary" onClick={() => run(() => api(`/customers/${w.id}/wallet`, { method: "PATCH", body: { wallet_low: Number(low) } }), "Saved").then(reload)}>Save</button></div></label>
        </div>
        <form className="grid gap-2 sm:grid-cols-5" onSubmit={async (e) => {
          e.preventDefault();
          if (await run(() => api(`/customers/${w.id}/wallet/deposit`, { body: { amount: Number(f.amount), method: f.method, ref: f.ref || null, type: f.type, account_id: account } }), f.type === "deposit" ? "Deposit saved — customer told on WhatsApp" : "Saved")) { setF({ ...f, amount: "", ref: "" }); reload(); }
        }}>
          <select className="input" value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })} aria-label="Type"><option value="deposit">Deposit</option><option value="refund">Refund (money back)</option></select>
          <input className="input" type="number" min={1} required placeholder="Amount Rs" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} />
          <select className="input" value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })} aria-label="Method">{METHODS.map((m) => <option key={m}>{m}</option>)}</select>
          <input className="input" placeholder="Ref / TID" value={f.ref} onChange={(e) => setF({ ...f, ref: e.target.value })} />
          <button className="btn-primary" disabled={busy}>Save</button>
          <div className="sm:col-span-5"><AccountPicker method={f.method} label={f.type === "refund" ? "Paid back from which bank?" : "Into which bank account? · کس بینک میں"} value={account} onChange={setAccount} /></div>
        </form>
        <div className="max-h-80 overflow-y-auto">
          <table className="w-full text-sm">
            <tbody className="divide-y divide-slate-100">
              {data.lines.map((l: any) => (
                <tr key={l.id}><td className="py-1.5 text-xs text-slate-500">{dt(l.created_at)}</td>
                  <td className="capitalize">{l.type}{l.method ? ` · ${l.method}` : ""}{l.ref ? ` · ${l.ref}` : ""}<span className="block text-xs text-slate-500">{l.note}</span></td>
                  <td className={`text-right tabular-nums ${l.type === "deposit" ? "text-emerald-700" : "text-slate-800"}`}>{l.type === "deposit" ? "+" : "−"}{pkr(l.amount)}</td></tr>
              ))}
            </tbody>
          </table>
          {!data.lines.length && <Empty>No entries yet</Empty>}
        </div>
      </div>}
    </Modal>
  );
}
