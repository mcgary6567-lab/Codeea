import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Plus, Search, Truck, Banknote, Send, ArrowLeft, Ban, HandCoins } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Empty, ErrorBox, Field, Loading, Modal, PageHeader, Stat, useAction } from "../components/ui";
import { PRODUCTS, dt, num, pkr, pkrShort } from "../lib/format";
import { useAuth } from "../App";
import { ProofPhotos } from "../components/Capture";
import { AccountPicker } from "../components/BankParts";
import { supplierOpts } from "../components/SupplierSelect";

const Ur = ({ children, className = "" }: { children: React.ReactNode; className?: string }) => <span lang="ur" dir="rtl" className={`font-urdu ${className}`}>{children}</span>;

/** Carriage / kiraya (bypass on our depot ID) — a separate module, run with thekedars (contractors), not wholesale clients. */
export default function Carriage() {
  const { id } = useParams();
  if (id) return <ThekedarDetail id={Number(id)} />;
  return <ThekedarList />;
}

function ThekedarList() {
  const { can } = useAuth();
  const { data, loading, error, reload } = useApi<any[]>("/carriage/thekedars");
  const [q, setQ] = useState("");
  const [add, setAdd] = useState(false);
  const list = (data ?? []).filter((k) => !q || k.name.toLowerCase().includes(q.toLowerCase()) || (k.phone ?? "").includes(q));
  return (
    <div className="space-y-4">
      <PageHeader title="Carriage / kiraya" subtitle="Bypass on our depot ID — run with thekedars"
        actions={can("carriage.manage") && <button className="btn-primary" onClick={() => setAdd(true)}><Plus size={16} /> New thekedar</button>} />
      <p className="rounded-lg bg-sky-50 px-3 py-2 text-xs text-sky-900">Depot humari ID par load karta hai aur hamein invoice deta hai — fuel ka paisa humari books mein nahi aata. Hum sirf <b>kiraya</b> thekedar se charge karte hain, jo poora munafa hai. <Ur className="block">صرف کرایہ ٹھیکیدار کے ذمے — فیول ہمارے کھاتے میں نہیں</Ur></p>
      <CarriageSummary />
      <div className="relative"><Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <input className="input pl-9" placeholder="Search thekedar…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      {error && <ErrorBox error={error} />}
      {loading ? <Loading /> : !list.length ? <Empty>Koi thekedar nahi. "New thekedar" se shuru karein.</Empty> : (
        <div className="space-y-2">
          {list.map((k) => (
            <Link key={k.id} to={`/carriage/${k.id}`} className="card flex items-center justify-between p-4 hover:ring-2 hover:ring-brand-200">
              <div className="min-w-0">
                <div className="flex items-center gap-2 font-semibold">{k.name}{k.active ? "" : <Badge tone="gray">closed</Badge>}</div>
                <div className="text-xs text-slate-500">{[k.city, k.phone].filter(Boolean).join(" · ") || "—"}</div>
              </div>
              <div className="text-right">
                <div className={`font-bold tabular-nums ${k.due > 0 ? "text-rose-600" : k.due < 0 ? "text-emerald-600" : ""}`}>{pkr(k.due)}</div>
                <div className="text-xs text-slate-400">{k.due > 0 ? "baqaya" : k.due < 0 ? "advance" : "clear"}</div>
              </div>
            </Link>
          ))}
        </div>
      )}
      {add && <ThekedarForm onClose={() => setAdd(false)} onSaved={() => { setAdd(false); reload(); }} />}
    </div>
  );
}

function CarriageSummary() {
  const { data } = useApi<any>("/carriage/summary");
  if (!data || (!data.kiraya_total && !data.held_total && !data.fuel_by_depot?.length)) return null;
  return (
    <div className="card p-4">
      <h2 className="mb-3 flex items-center gap-2 font-semibold"><Truck size={16} className="text-slate-500" /> This month · <Ur>اس مہینے</Ur></h2>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <div className="rounded-xl bg-emerald-50 p-3"><div className="text-xs text-emerald-800">Kiraya earned (profit)</div><div className="text-xl font-bold tabular-nums">{pkr(data.kiraya_total)}</div></div>
        <div className="rounded-xl bg-slate-50 p-3"><div className="text-xs text-slate-600">Fuel routed (all depots)</div><div className="text-xl font-bold tabular-nums">{pkrShort(data.fuel_by_depot.reduce((a: number, d: any) => a + d.fuel, 0))}</div></div>
        <div className={`rounded-xl p-3 ${data.held_total > 0 ? "bg-amber-50" : "bg-slate-50"}`}><div className="text-xs text-slate-600">Held — to forward</div><div className={`text-xl font-bold tabular-nums ${data.held_total > 0 ? "text-amber-700" : ""}`}>{pkr(data.held_total)}</div></div>
      </div>
      {data.kiraya_by_thekedar?.length > 0 && <div className="mt-3">
        <div className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">Kiraya by thekedar</div>
        <ul className="divide-y divide-slate-100 text-sm">{data.kiraya_by_thekedar.slice(0, 5).map((c: any) => (
          <li key={c.name} className="flex justify-between py-1.5"><span>{c.name} <span className="text-xs text-slate-400">({c.trips})</span></span><b className="tabular-nums">{pkr(c.kiraya)}</b></li>))}</ul>
      </div>}
      {data.fuel_by_depot?.length > 0 && <div className="mt-3">
        <div className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">Fuel money by depot</div>
        <ul className="divide-y divide-slate-100 text-sm">{data.fuel_by_depot.slice(0, 5).map((d: any) => (
          <li key={d.depot ?? "—"} className="flex justify-between py-1.5"><span>{d.depot ?? "—"} <span className="text-xs text-slate-400">{d.through_us ? `through us ${pkrShort(d.through_us)}` : ""}{d.direct ? ` · direct ${pkrShort(d.direct)}` : ""}</span></span><b className="tabular-nums">{pkr(d.fuel)}</b></li>))}</ul>
      </div>}
    </div>
  );
}

function ThekedarDetail({ id }: { id: number }) {
  const { can } = useAuth();
  const nav = useNavigate();
  const { data, loading, error, reload } = useApi<any>(`/carriage/thekedars/${id}`);
  const [action, setAction] = useState<null | "carriage" | "payment" | "fuelpay" | "edit">(null);
  const [heldKey, setHeldKey] = useState(0);
  const refresh = () => { reload(); setHeldKey((k) => k + 1); };
  if (loading) return <Loading />;
  if (error || !data) return <ErrorBox error={error ?? "Not found"} />;
  const k = data.thekedar;
  return (
    <div className="space-y-4">
      <button className="flex items-center gap-1 text-sm text-slate-500" onClick={() => nav("/carriage")}><ArrowLeft size={15} /> All thekedars</button>
      <div className="card p-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h1 className="text-lg font-bold">{k.name}{k.active ? "" : <Badge tone="gray">closed</Badge>}</h1>
            <div className="text-sm text-slate-500">{[k.city, k.phone].filter(Boolean).join(" · ") || "—"}</div>
          </div>
          <div className="text-right">
            <div className={`text-2xl font-bold tabular-nums ${k.due > 0 ? "text-rose-600" : k.due < 0 ? "text-emerald-600" : ""}`}>{pkr(k.due)}</div>
            <div className="text-xs text-slate-400">{k.due > 0 ? "baqaya kiraya" : k.due < 0 ? "advance" : "clear"}</div>
          </div>
        </div>
        {can("carriage.manage") && <div className="mt-3 flex flex-wrap gap-2">
          <button className="btn-primary" onClick={() => setAction("carriage")}><Truck size={15} /> Bill kiraya · <Ur>کرایہ</Ur></button>
          <button className="btn-secondary" onClick={() => setAction("payment")}><HandCoins size={15} /> Payment · <Ur>وصولی</Ur></button>
          <button className="btn-secondary" onClick={() => setAction("fuelpay")}><Banknote size={15} /> Fuel payment · <Ur>فیول</Ur></button>
          {can("carriage.manage") && <button className="btn-secondary" onClick={() => setAction("edit")}>Edit</button>}
        </div>}
      </div>

      <HeldFuel thekedarId={k.id} refreshKey={heldKey} onChanged={refresh} />

      <div className="card overflow-hidden">
        <h2 className="border-b p-4 font-semibold">Statement · <Ur>کھاتہ</Ur></h2>
        <Statement data={data} canVoid={can("carriage.void")} onChanged={refresh} />
      </div>

      {action === "carriage" && <CarriageEntry thekedar={k} onClose={() => setAction(null)} onDone={() => { setAction(null); refresh(); }} />}
      {action === "payment" && <PaymentEntry thekedar={k} onClose={() => setAction(null)} onDone={() => { setAction(null); refresh(); }} />}
      {action === "fuelpay" && <FuelPaymentEntry thekedar={k} onClose={() => setAction(null)} onDone={() => { setAction(null); refresh(); }} />}
      {action === "edit" && <ThekedarForm initial={k} onClose={() => setAction(null)} onSaved={() => { setAction(null); refresh(); }} />}
    </div>
  );
}

function Statement({ data, canVoid, onChanged }: { data: any; canVoid: boolean; onChanged: () => void }) {
  const { busy, run } = useAction();
  const lines: any[] = data.lines ?? [];
  if (!lines.length) return <Empty>Koi entry nahi.</Empty>;
  const label = (r: any) => r.type === "carriage" ? `Kiraya${r.litres ? ` · ${num(r.litres, 2)} L ${r.product ? PRODUCTS[r.product] : "fuel"}` : ""}${r.govt_pct ? ` · ${pkr(r.gross_amount)} − govt ${r.govt_pct}%` : ""}${r.ref ? ` · inv ${r.ref}` : ""}`
    : r.type === "payment" ? `Payment${r.method ? ` · ${r.method}` : ""}${r.ref ? ` · ${r.ref}` : ""}`
      : r.type === "adjustment" ? `Adjustment${r.note ? ` · ${r.note}` : ""}`
        : r.type === "fuel_note" ? `Fuel ${pkr(r.amount)} · ${r.fuel_mode === "direct" ? "depot ko direct" : r.fuel_status === "forwarded" ? "through us (forwarded)" : "through us (held)"}${r.depot_name ? ` · ${r.depot_name}` : ""}` : r.type;
  const voidIt = (r: any) => {
    if (r.type === "fuel_note") return run(() => api(`/carriage/fuel-payments/${String(r.id).replace("bfp", "")}/void`, { body: {} }), "Reversed").then(onChanged);
    return run(() => api(`/carriage/txns/${r.id}/void`, { body: { reason: "correction" } }), "Reversed").then(onChanged);
  };
  return (
    <ul className="divide-y divide-slate-100">
      <li className="flex justify-between px-4 py-2 text-sm text-slate-500"><span>Opening</span><b className="tabular-nums">{pkr(data.opening_balance)}</b></li>
      {lines.map((r) => (
        <li key={`${r.type}-${r.id}`} className={`flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 text-sm ${r.voided ? "opacity-40 line-through" : ""} ${r.type === "fuel_note" ? "bg-slate-50/60" : ""}`}>
          <div className="min-w-0 flex-1">
            <div className="font-medium">{label(r)}</div>
            <div className="text-xs text-slate-400">{dt(r.txn_date)}{r.note && r.type !== "adjustment" ? ` · ${r.note}` : ""}</div>
          </div>
          <div className="w-24 text-right tabular-nums text-rose-600">{r.debit ? pkr(r.debit) : ""}</div>
          <div className="w-24 text-right tabular-nums text-emerald-600">{r.credit ? pkr(r.credit) : ""}</div>
          <div className="w-28 text-right font-semibold tabular-nums">{r.type === "fuel_note" ? "" : pkr(r.balance)}</div>
          {canVoid && !r.voided && <button className="text-xs text-rose-600 hover:underline disabled:opacity-40" disabled={busy} onClick={() => voidIt(r)} title="Void"><Ban size={14} /></button>}
        </li>
      ))}
      <li className="flex justify-between border-t-2 px-4 py-2 font-bold"><span>Closing (baqaya)</span><b className="tabular-nums">{pkr(data.closing_balance)}</b></li>
    </ul>
  );
}

function ThekedarForm({ initial, onClose, onSaved }: { initial?: any; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ name: initial?.name ?? "", phone: initial?.phone ?? "", city: initial?.city ?? "", cnic: initial?.cnic ?? "", address: initial?.address ?? "", opening_balance: initial ? "" : "", notes: initial?.notes ?? "" });
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={initial ? `Edit — ${initial.name}` : "New thekedar"}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const body: any = { name: f.name, phone: f.phone || null, city: f.city || null, cnic: f.cnic || null, address: f.address || null, notes: f.notes || null };
        if (!initial && f.opening_balance !== "") body.opening_balance = Number(f.opening_balance);
        const call = initial ? api(`/carriage/thekedars/${initial.id}`, { method: "PATCH", body }) : api("/carriage/thekedars", { body });
        if (await run(() => call, initial ? "Updated" : "Thekedar added")) onSaved();
      }}>
        <Field label="Name · نام *"><input className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Phone (WhatsApp)"><input className="input" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} placeholder="923…" /></Field>
          <Field label="City"><input className="input" value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} /></Field>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="CNIC"><input className="input" value={f.cnic} onChange={(e) => setF({ ...f, cnic: e.target.value })} /></Field>
          {!initial && <Field label="Opening balance (baqaya)"><input className="input" type="number" value={f.opening_balance} onChange={(e) => setF({ ...f, opening_balance: e.target.value })} placeholder="0" /></Field>}
        </div>
        <Field label="Address"><input className="input" value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} /></Field>
        <Field label="Notes"><input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy || !f.name}>{initial ? "Save" : "Add"}</button></div>
      </form>
    </Modal>
  );
}

/** Bill the fixed kiraya (+ optionally record the fuel money in the same entry). */
function CarriageEntry({ thekedar, onClose, onDone }: { thekedar: any; onClose: () => void; onDone: () => void }) {
  const depots = useApi<any[]>("/carriage/depots");
  const [f, setF] = useState({ supplier_id: "", invoice_ref: "", vehicle_no: "", amount: "", govt_pct: "", note: "" });
  const [lines, setLines] = useState<{ product: string; litres: string }[]>([{ product: "HSD", litres: "" }]);
  const [photos, setPhotos] = useState<number[]>([]);
  const [withFuel, setWithFuel] = useState(false);
  const [fu, setFu] = useState({ amount: "", mode: "direct", in_method: "Bank transfer", forward_now: true, fwd_method: "Bank transfer" });
  const [inAcc, setInAcc] = useState<number | null>(null);
  const [fwdAcc, setFwdAcc] = useState<number | null>(null);
  const { busy, run } = useAction();
  const totalL = lines.reduce((a, l) => a + (Number(l.litres) || 0), 0);
  const gross = Number(f.amount) || 0; // kiraya as written on the invoice
  const pct = Math.min(100, Math.max(0, Number(f.govt_pct) || 0));
  const govtCut = Math.round(gross * pct / 100);
  const kiraya = Math.round(gross - govtCut); // net kiraya billed to the thekedar
  const fuelThrough = withFuel && fu.mode === "through_us";
  const valid = f.supplier_id && totalL > 0 && kiraya > 0 && (!withFuel || (Number(fu.amount) > 0 && (!fuelThrough || inAcc)));
  return (
    <Modal open onClose={onClose} title={`Bill kiraya — ${thekedar.name}`}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const body: any = {
          supplier_id: Number(f.supplier_id), invoice_ref: f.invoice_ref || null, vehicle_no: f.vehicle_no || null, photo_ids: photos,
          lines: lines.filter((l) => Number(l.litres) > 0).map((l) => ({ product: l.product, litres: Number(l.litres) })),
          amount: Number(f.amount), govt_pct: pct || null, note: f.note || null,
          fuel: withFuel ? { mode: fu.mode, amount: Number(fu.amount), ...(fuelThrough ? { in_method: fu.in_method, in_account_id: inAcc, forward_now: fu.forward_now, fwd_method: fu.fwd_method, fwd_account_id: fwdAcc ?? inAcc } : {}) } : null,
        };
        if (await run(() => api(`/carriage/thekedars/${thekedar.id}/carriage`, { body }), (r: any) => `Kiraya ${pkr(r.kiraya)} billed. Baqaya ${pkr(r.due)}`)) onDone();
      }}>
        <Field label="Depot (our ID) · depot → company → banda *"><select className="input" required value={f.supplier_id} onChange={(e) => setF({ ...f, supplier_id: e.target.value })}>
          <option value="">— choose depot —</option>{supplierOpts(depots.data ?? [])}</select></Field>
        <div className="space-y-2">
          <span className="label">Fuel lifted (for the record) · <Ur>کتنا تیل</Ur></span>
          {lines.map((l, i) => (
            <div key={i} className="flex gap-2">
              <select className="input" value={l.product} onChange={(e) => setLines(lines.map((x, j) => j === i ? { ...x, product: e.target.value } : x))}>{Object.entries(PRODUCTS).map(([key, v]) => <option key={key} value={key}>{v}</option>)}</select>
              <input className="input" type="number" min={0} placeholder="litres" value={l.litres} onChange={(e) => setLines(lines.map((x, j) => j === i ? { ...x, litres: e.target.value } : x))} />
              {lines.length > 1 && <button type="button" className="min-h-10 px-2 text-red-600" aria-label="Remove" onClick={() => setLines(lines.filter((_, j) => j !== i))}>✕</button>}
            </div>
          ))}
          <button type="button" className="text-xs font-medium text-brand-700 hover:underline" onClick={() => setLines([...lines, { product: "PMG", litres: "" }])}>+ Add fuel</button>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Invoice no. (depot) · انوائس"><input className="input" value={f.invoice_ref} onChange={(e) => setF({ ...f, invoice_ref: e.target.value })} /></Field>
          <Field label="Tanker / vehicle"><input className="input" value={f.vehicle_no} onChange={(e) => setF({ ...f, vehicle_no: e.target.value })} /></Field>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Kiraya (as written on the depot invoice) · کرایہ *"><input className="input" type="number" min={1} required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} placeholder="e.g. 8000" /></Field>
          <Field label="Govt cut % (optional) · گورنمنٹ کٹوتی"><input className="input" type="number" min={0} max={100} step="0.01" value={f.govt_pct} onChange={(e) => setF({ ...f, govt_pct: e.target.value })} placeholder="e.g. 10" /></Field>
        </div>
        {pct > 0 && gross > 0 && <div className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">Kiraya {pkr(gross)} − Govt {pct}% ({pkr(govtCut)}) = <b>{pkr(kiraya)}</b> final · <Ur>گورنمنٹ کٹوتی کے بعد</Ur></div>}
        <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>

        <label className="flex items-center gap-2 rounded-lg bg-slate-50 px-3 py-2 text-sm"><input type="checkbox" className="h-5 w-5" checked={withFuel} onChange={(e) => setWithFuel(e.target.checked)} /> Fuel money bhi abhi record karein? · <Ur>فیول کا پیسہ بھی</Ur></label>
        {withFuel && <div className="space-y-3 rounded-xl bg-slate-50 p-3">
          <Field label="Fuel amount (Rs) · depot invoice *"><input className="input" type="number" min={1} value={fu.amount} onChange={(e) => setFu({ ...fu, amount: e.target.value })} /></Field>
          <div className="grid grid-cols-1 gap-2">
            <button type="button" onClick={() => setFu({ ...fu, mode: "direct" })} aria-pressed={fu.mode === "direct"} className={`rounded-xl px-3 py-2 text-left text-sm ${fu.mode === "direct" ? "bg-slate-800 font-semibold text-white" : "bg-white ring-1 ring-slate-200"}`}>Thekedar paid the depot <b>direct</b> (screenshot)</button>
            <button type="button" onClick={() => setFu({ ...fu, mode: "through_us" })} aria-pressed={fu.mode === "through_us"} className={`rounded-xl px-3 py-2 text-left text-sm ${fu.mode === "through_us" ? "bg-slate-800 font-semibold text-white" : "bg-white ring-1 ring-slate-200"}`}>Thekedar sent it to <b>us</b> → we forward</button>
          </div>
          {fuelThrough && <div className="space-y-2">
            <AccountPicker method={fu.in_method} value={inAcc} onChange={setInAcc} label="Sent to which account? · کس اکاؤنٹ میں" required />
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" className="h-5 w-5" checked={fu.forward_now} onChange={(e) => setFu({ ...fu, forward_now: e.target.checked })} /> Forward to the depot now</label>
            {fu.forward_now && <AccountPicker method={fu.fwd_method} value={fwdAcc} onChange={setFwdAcc} label="Forwarded from which account?" />}
          </div>}
        </div>}

        <ProofPhotos value={photos} onChange={setPhotos} hint="depot invoice photo" />
        <div className="rounded-lg bg-slate-50 px-3 py-2 text-sm">Total {num(totalL)} L · Kiraya billed: <b className="tabular-nums">{pkr(kiraya)}</b> <span className="text-slate-500">(thekedar ke zimme, poora munafa)</span>{withFuel && Number(fu.amount) > 0 && <span className="block text-slate-500">Fuel Rs {num(Number(fu.amount))} — {fu.mode === "direct" ? "depot ko direct" : "humare through"}</span>}</div>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy || !valid}>Bill kiraya</button></div>
      </form>
    </Modal>
  );
}

/** Thekedar pays his kiraya. */
function PaymentEntry({ thekedar, onClose, onDone }: { thekedar: any; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ amount: "", method: "Cash", ref: "", note: "" });
  const [acc, setAcc] = useState<number | null>(null);
  const [photos, setPhotos] = useState<number[]>([]);
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={`Kiraya payment — ${thekedar.name}`}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const body = { amount: Number(f.amount), method: f.method, account_id: acc, ref: f.ref || null, note: f.note || null, photo_ids: photos };
        if (await run(() => api(`/carriage/thekedars/${thekedar.id}/payment`, { body }), (r: any) => `Payment liya. Baqaya ${pkr(r.due)}`)) onDone();
      }}>
        <p className="text-sm text-slate-600">Current baqaya: <b>{pkr(thekedar.due)}</b></p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Amount (Rs) · رقم *"><input className="input" type="number" min={1} required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
          <Field label="Method · طریقہ"><select className="input" value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })}>{["Cash", "Bank transfer", "Cheque", "Raast", "JazzCash", "Easypaisa", "Online"].map((m) => <option key={m}>{m}</option>)}</select></Field>
          <Field label="Ref / slip no."><input className="input" value={f.ref} onChange={(e) => setF({ ...f, ref: e.target.value })} /></Field>
          <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        </div>
        <AccountPicker method={f.method} value={acc} onChange={setAcc} />
        <ProofPhotos value={photos} onChange={setPhotos} hint="payment screenshot / slip" />
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy || !(Number(f.amount) > 0)}>Save payment</button></div>
      </form>
    </Modal>
  );
}

/** Fuel money for a bypass — separate from kiraya. Paid to the depot direct (record only), or sent to us to forward. */
function FuelPaymentEntry({ thekedar, onClose, onDone }: { thekedar: any; onClose: () => void; onDone: () => void }) {
  const depots = useApi<any[]>("/carriage/depots");
  const [f, setF] = useState({ supplier_id: "", amount: "", mode: "direct", invoice_ref: "", note: "", in_method: "Bank transfer", in_ref: "", forward_now: true, fwd_method: "Bank transfer", fwd_ref: "" });
  const [inAcc, setInAcc] = useState<number | null>(null);
  const [fwdAcc, setFwdAcc] = useState<number | null>(null);
  const [photos, setPhotos] = useState<number[]>([]);
  const { busy, run } = useAction();
  const through = f.mode === "through_us";
  const valid = f.supplier_id && Number(f.amount) > 0 && (!through || inAcc);
  return (
    <Modal open onClose={onClose} title={`Fuel payment — ${thekedar.name}`}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const body = {
          supplier_id: Number(f.supplier_id), amount: Number(f.amount), mode: f.mode, invoice_ref: f.invoice_ref || null, note: f.note || null, photo_ids: photos,
          ...(through ? { in_method: f.in_method, in_account_id: inAcc, in_ref: f.in_ref || null, forward_now: f.forward_now, fwd_method: f.fwd_method, fwd_account_id: fwdAcc ?? inAcc, fwd_ref: f.fwd_ref || null } : {}),
        };
        if (await run(() => api(`/carriage/thekedars/${thekedar.id}/fuel-payment`, { body }), "Fuel payment saved")) onDone();
      }}>
        <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">Yeh depot ke fuel ka paisa hai — <b>humara kiraya nahi</b>. Thekedar ki due par asar nahi; sirf record (aur through-us mein humare bank se guzarta hai, net zero).</p>
        <Field label="Depot · depot → company → banda *"><select className="input" required value={f.supplier_id} onChange={(e) => setF({ ...f, supplier_id: e.target.value })}>
          <option value="">— choose depot —</option>{supplierOpts(depots.data ?? [])}</select></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Fuel amount (Rs) *"><input className="input" type="number" min={1} required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
          <Field label="Depot invoice no."><input className="input" value={f.invoice_ref} onChange={(e) => setF({ ...f, invoice_ref: e.target.value })} /></Field>
        </div>
        <fieldset>
          <legend className="label">How was it paid? · <Ur>کیسے</Ur></legend>
          <div className="grid grid-cols-1 gap-2">
            <button type="button" onClick={() => setF({ ...f, mode: "direct" })} aria-pressed={f.mode === "direct"} className={`rounded-xl px-3 py-2.5 text-left text-sm ${f.mode === "direct" ? "bg-slate-800 font-semibold text-white" : "bg-slate-100"}`}>Thekedar paid the depot <b>direct</b> — screenshot · <Ur>سیدھا ڈپو کو</Ur></button>
            <button type="button" onClick={() => setF({ ...f, mode: "through_us" })} aria-pressed={through} className={`rounded-xl px-3 py-2.5 text-left text-sm ${through ? "bg-slate-800 font-semibold text-white" : "bg-slate-100"}`}>Thekedar sent it to <b>us</b> → we forward · <Ur>ہمیں بھیجا، ہم ڈپو کو</Ur></button>
          </div>
        </fieldset>
        {through && <div className="space-y-3 rounded-xl bg-sky-50 p-3">
          <AccountPicker method={f.in_method} value={inAcc} onChange={setInAcc} label="Sent to which of our accounts? · کس اکاؤنٹ میں" required />
          <Field label="Ref (transfer)"><input className="input" value={f.in_ref} onChange={(e) => setF({ ...f, in_ref: e.target.value })} /></Field>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" className="h-5 w-5" checked={f.forward_now} onChange={(e) => setF({ ...f, forward_now: e.target.checked })} /> Forward to the depot now · <Ur>ابھی ڈپو کو</Ur></label>
          {f.forward_now && <>
            <AccountPicker method={f.fwd_method} value={fwdAcc} onChange={setFwdAcc} label="Forwarded from which account? · کس اکاؤنٹ سے" />
            <Field label="Ref (to depot)"><input className="input" value={f.fwd_ref} onChange={(e) => setF({ ...f, fwd_ref: e.target.value })} /></Field>
          </>}
          {!f.forward_now && <p className="text-xs text-amber-800">Abhi "held" dikhega — baad mein Forward kar dena. · <Ur>بعد میں فارورڈ</Ur></p>}
        </div>}
        <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        <ProofPhotos value={photos} onChange={setPhotos} hint={f.mode === "direct" ? "payment screenshot" : "bank receipt(s)"} />
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy || !valid}>Save</button></div>
      </form>
    </Modal>
  );
}

/** Fuel money we received from this thekedar but have not yet forwarded to the depot. */
function HeldFuel({ thekedarId, refreshKey, onChanged }: { thekedarId: number; refreshKey: number; onChanged: () => void }) {
  const { data, reload } = useApi<any[]>(`/carriage/fuel-held?k=${refreshKey}`);
  const { busy, run } = useAction();
  const mine = (data ?? []).filter((f) => f.thekedar_id === thekedarId);
  if (!mine.length) return null;
  return (
    <div className="card border-l-4 border-l-amber-500 p-4">
      <h2 className="mb-2 font-semibold">⏳ Fuel money held for the depot · <Ur>ڈپو کو دینا باقی</Ur></h2>
      <ul className="divide-y divide-slate-100">
        {mine.map((f) => (
          <li key={f.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
            <span className="min-w-0 flex-1"><b className="tabular-nums">{pkr(f.amount)}</b> → {f.depot_name ?? "depot"}{f.invoice_ref ? ` · inv ${f.invoice_ref}` : ""}<span className="block text-xs text-slate-500">{dt(f.txn_date)}{f.note ? ` · ${f.note}` : ""}</span></span>
            <button className="btn-primary !py-1.5 text-sm" disabled={busy} onClick={() => run(() => api(`/carriage/fuel-payments/${f.id}/forward`, { body: {} }), "Forwarded to the depot").then(() => { reload(); onChanged(); })}><Send size={14} /> Forward to depot</button>
          </li>
        ))}
      </ul>
    </div>
  );
}
