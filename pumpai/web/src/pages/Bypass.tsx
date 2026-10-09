import { useMemo, useState } from "react";
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Cell } from "recharts";
import { Plus, Truck, Factory, Download, Printer, HandCoins, ArrowLeft, Ban } from "lucide-react";
import { api, linkToken, useApi } from "../lib/api";
import { Empty, ErrorBox, Field, Loading, Modal, Stat, useAction } from "../components/ui";
import { PRODUCTS, activeProducts, dt, num, pkr, pkrShort } from "../lib/format";
import { useAuth } from "../App";
import { ProofPhotos, ProofThumbs } from "../components/Capture";
import { AccountPicker } from "../components/BankParts";
import { supplierOpts } from "../components/SupplierSelect";
import { FleetPicker, fleetBody } from "../components/WholesaleFleet";

const Ur = ({ children, className = "" }: { children: React.ReactNode; className?: string }) => <span lang="ur" dir="rtl" className={`font-urdu ${className}`}>{children}</span>;

/** Bypass delivery — shown inside the Wholesale "Tanker trips" tab: buy fuel from suppliers at the depot and deliver straight to wholesale clients. */
export function BypassPanel() {
  const { can } = useAuth();
  const sups = useApi<any[]>("/bypass/suppliers");
  const dels = useApi<any[]>("/bypass/deliveries");
  const stock = useApi<any>("/bypass/stock");
  const monthly = useApi<any[]>("/bypass/monthly?months=6");
  const [openSup, setOpenSup] = useState<number | null>(null);
  const refresh = () => { sups.reload(); dels.reload(); stock.reload(); monthly.reload(); };
  const owedTotal = (sups.data ?? []).reduce((a, s) => a + (s.bypass_owed > 0 ? s.bypass_owed : 0), 0);
  const stockLines = (stock.data?.by_product ?? []) as any[];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><h2 className="flex items-center gap-2 text-lg font-semibold"><Truck size={18} className="text-slate-500" /> Bypass supplier accounts · <Ur>بائی پاس</Ur></h2>
          <p className="text-sm text-slate-500">Depot se seedha client ko — naya bypass "New trip" button se banayein (Depot bypass mode)</p></div>
      </div>
      <p className="rounded-lg bg-sky-50 px-3 py-2 text-xs text-sky-900">Hum supplier se cost par maal lete hain aur client ko unke rate par dete hain — farq (margin) humara munafa. Supplier ka bypass khata pump-stock se bilkul alag hai, aur jitna maal liya usse zyada deliver nahi ho sakta. <Ur className="block">سپلائر کا بائی پاس کھاتہ پمپ اسٹاک سے الگ</Ur></p>

      <div className="flex flex-wrap gap-3">
        <Stat label="We owe bypass suppliers" value={pkrShort(owedTotal)} tone="red" />
        <Stat label="Bypass stock on hand" value={pkrShort(stock.data?.total?.value ?? 0)} hint={stockLines.length ? stockLines.map((p) => `${num(p.litres)} L ${PRODUCTS[p.product]}`).join(" · ") : "koi stock nahi"} />
        <Stat label="Is mahine ka munafa" value={pkrShort(stock.data?.month?.profit ?? 0)} tone={(stock.data?.month?.profit ?? 0) >= 0 ? "green" : "red"}
          hint={stock.data?.month ? `bika ${pkrShort(stock.data.month.billed)} − cost ${pkrShort(stock.data.month.cost)}` : "—"} />
        <Stat label="Pichle mahine ka munafa" value={pkrShort(stock.data?.month?.last?.profit ?? 0)} tone={(stock.data?.month?.last?.profit ?? 0) >= 0 ? "green" : "red"}
          hint={stock.data?.month?.last ? `${stock.data.month.last.month} · bika ${pkrShort(stock.data.month.last.billed)} − cost ${pkrShort(stock.data.month.last.cost)}` : "—"} />
        <Stat label={`${stock.data?.month?.year?.year ?? ""} ka munafa`} value={pkrShort(stock.data?.month?.year?.profit ?? 0)} tone={(stock.data?.month?.year?.profit ?? 0) >= 0 ? "green" : "red"}
          hint={stock.data?.month?.year ? `is saal · bika ${pkrShort(stock.data.month.year.billed)} − cost ${pkrShort(stock.data.month.year.cost)}` : "—"} />
      </div>

      <MonthlyProfitChart data={monthly.data ?? []} />

      <div className="card overflow-hidden">
        <h2 className="border-b p-4 font-semibold"><Factory size={15} className="mr-1 inline text-slate-500" /> Bypass suppliers · <Ur>سپلائر</Ur></h2>
        {sups.loading ? <Loading /> : sups.error ? <ErrorBox error={sups.error} /> : !(sups.data ?? []).length ? <Empty>Koi supplier nahi.</Empty> : (
          <ul className="divide-y divide-slate-100">
            {(sups.data ?? []).map((s) => (
              <li key={s.id}><button className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-slate-50" onClick={() => setOpenSup(s.id)}>
                <div><div className="font-medium">{[s.company, s.name].filter(Boolean).join(" — ")}</div><div className="text-xs text-slate-400">{s.depot_name ?? "—"}</div></div>
                <div className={`text-right font-bold tabular-nums ${s.bypass_owed > 0 ? "text-rose-600" : s.bypass_owed < 0 ? "text-emerald-600" : "text-slate-400"}`}>{pkr(s.bypass_owed)}<div className="text-xs font-normal text-slate-400">{s.bypass_owed > 0 ? "we owe" : s.bypass_owed < 0 ? "advance" : "clear"}</div></div>
              </button></li>
            ))}
          </ul>
        )}
      </div>

      <div className="card overflow-hidden">
        <h2 className="border-b p-4 font-semibold">Recent deliveries · <Ur>حالیہ</Ur></h2>
        {!(dels.data ?? []).length ? <Empty>Abhi tak koi delivery nahi.</Empty> : (
          <ul className="divide-y divide-slate-100 text-sm">
            {(dels.data ?? []).map((d) => (
              <li key={d.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
                <div className="min-w-0"><div className="font-medium">#{d.id} · {num(d.drop_litres || d.litres)} L</div><div className="flex flex-wrap items-center gap-1 text-xs text-slate-400">{dt(d.txn_date)}{d.vehicle_no ? ` · ${d.vehicle_no}` : ""}{d.note ? ` · ${d.note}` : ""}<ProofThumbs ids={d.proof_ids} onChanged={() => dels.reload()} /></div></div>
                <div className="shrink-0 text-right tabular-nums">
                  <div className={`font-semibold ${d.margin > 0 ? "text-emerald-600" : d.margin < 0 ? "text-rose-600" : "text-slate-500"}`}>munafa {pkr(d.margin)}</div>
                  <div className="text-xs text-slate-400">{pkr(d.billed)} − cost {pkr(d.sold_cost)}</div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {openSup && <SupplierStatement id={openSup} onClose={() => setOpenSup(null)} onChanged={refresh} />}
    </div>
  );
}

/** Monthly bypass munafa — a small bar chart (green = faida, red = nuqsan) over the last few months. */
function MonthlyProfitChart({ data }: { data: any[] }) {
  if (!data.length || data.every((d) => !d.profit && !d.billed)) return null;
  const GRID = "#e5e7eb", AXIS = "#6b7280";
  const label = (ym: string) => { const [, m] = (ym ?? "").split("-"); return ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(m)] ?? ym; };
  return (
    <div className="card p-4">
      <h2 className="font-semibold">Mahine ka munafa · <Ur>ماہانہ منافع</Ur></h2>
      <p className="mb-2 text-xs text-slate-500">Client se mila − delivered cost, har mahine</p>
      <div className="h-56"><ResponsiveContainer>
        <BarChart data={data} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="month" tickFormatter={label} tick={{ fill: AXIS, fontSize: 11 }} tickLine={false} axisLine={{ stroke: GRID }} />
          <YAxis tickFormatter={(v) => (Math.abs(v) >= 100_000 ? `${Math.round(v / 100_000)}L` : Math.abs(v) >= 1000 ? `${Math.round(v / 1000)}k` : v)} tick={{ fill: AXIS, fontSize: 11 }} tickLine={false} axisLine={false} width={44} />
          <Tooltip cursor={{ fill: "rgba(15,23,42,.05)" }} formatter={(v: number) => [pkr(v), "Munafa"]} labelFormatter={(ym) => label(ym) + " " + String(ym).slice(0, 4)} />
          <Bar dataKey="profit" radius={[4, 4, 0, 0]} maxBarSize={40}>
            {data.map((d, i) => <Cell key={i} fill={d.profit >= 0 ? "#1baf7a" : "#e11d48"} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer></div>
    </div>
  );
}

/** The bypass delivery form body (no Modal wrapper) — shown inside the "New trip" form's Depot-bypass mode. */
export function BypassDeliveryForm({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { can } = useAuth();
  const suppliers = useApi<any[]>("/bypass/suppliers");
  const clients = useApi<any[]>("/wholesale/clients");
  const stations = useApi<any[]>("/stations");
  const orders = useApi<any>("/wholesale/orders");
  const [station, setStation] = useState("");
  const [fl, setFl] = useState<any>({ tanker_id: "", driver_id: "", vehicle_no: "" });
  const [note, setNote] = useState("");
  const [buys, setBuys] = useState([{ supplier_id: "", product: "HSD", litres: "", amount: "" }]);
  const [drops, setDrops] = useState<any[]>([{ client_id: "", product: "HSD", litres: "", amount: "", order_id: undefined, location: "" }]);
  const [photos, setPhotos] = useState<number[]>([]);
  const { busy, run } = useAction();
  const stock = useApi<any>("/bypass/stock");
  const held = (p: string) => (stock.data?.by_product ?? []).find((x: any) => x.product === p)?.litres ?? 0;

  const sumBy = (rows: { product: string; litres: string }[]) => rows.reduce((m, r) => { const p = r.product; m[p] = (m[p] ?? 0) + (Number(r.litres) || 0); return m; }, {} as Record<string, number>);
  const bought = useMemo(() => sumBy(buys), [buys]);
  const dropped = useMemo(() => sumBy(drops), [drops]);
  // a drop can draw from the stock already held plus what is being bought right now
  const overBy = useMemo(() => Object.entries(dropped).filter(([p, l]) => l > held(p) + (bought[p] ?? 0) + 0.01).map(([p, l]) => ({ p, l, have: held(p) + (bought[p] ?? 0) })), [dropped, bought, stock.data]);
  // the client's price comes from their own rate card (CRM): drop amount = litres × card rate
  const clientById = (id: string) => (clients.data ?? []).find((c: any) => String(c.id) === String(id));
  const clientRate = (d: any) => Number(clientById(d.client_id)?.rates?.[d.product] ?? 0);
  const dropLineAmt = (d: any) => Math.round(Number(d.litres || 0) * clientRate(d) * 100) / 100;
  const buyAmt = buys.reduce((a, b) => a + (Number(b.amount) || 0), 0);
  const dropAmt = drops.reduce((a, d) => a + dropLineAmt(d), 0);
  const profit = dropAmt - buyAmt;
  const validBuys = buys.filter((b) => b.supplier_id && Number(b.litres) > 0 && Number(b.amount) > 0);
  const validDrops = drops.filter((d) => d.client_id && d.client_id !== "__new__" && Number(d.litres) > 0 && clientRate(d) > 0);
  // a chosen client+product with litres but no rate card set yet
  const noRate = drops.filter((d) => d.client_id && d.client_id !== "__new__" && Number(d.litres) > 0 && clientRate(d) <= 0);
  const valid = !!station && (validBuys.length > 0 || validDrops.length > 0) && overBy.length === 0 && noRate.length === 0;

  return (
      <form className="space-y-4" onSubmit={async (e) => {
        e.preventDefault();
        const body = {
          station_id: Number(station), ...fleetBody(fl), note: note || null, photo_ids: photos,
          purchases: validBuys.map((b) => ({ supplier_id: Number(b.supplier_id), product: b.product, litres: Number(b.litres), amount: Number(b.amount) })),
          drops: validDrops.map((d) => ({ client_id: Number(d.client_id), product: d.product, litres: Number(d.litres), order_id: d.order_id ?? null, location: d.location || null })),
        };
        if (await run(() => api("/bypass/deliveries", { body }), (r: any) => `Delivery #${r.id} saved. Bought ${pkr(r.bought)}, billed ${pkr(r.billed)}, munafa ${pkr(r.margin)}`)) onDone();
      }}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Station (books under) *"><select className="input" required value={station} onChange={(e) => setStation(e.target.value)}>
            <option value="">— choose —</option>{(stations.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
          <FleetPicker f={fl} setF={setFl} />
        </div>

        {/* Supplier purchase lines — supplier + litre + full amount */}
        <div className="rounded-xl bg-slate-50 p-3">
          <div className="mb-2 text-sm font-semibold">Maal kahan se liya (supplier + litre + amount) <span className="font-normal text-slate-400">— optional, sirf stock se bechna hai to khaali</span> · <Ur>خریداری</Ur></div>
          <div className="space-y-2">
            {buys.map((b, i) => (
              <div key={i} className="grid grid-cols-12 gap-2">
                <div className="col-span-5">
                  <select className="input w-full" value={b.supplier_id} onChange={(e) => setBuys(buys.map((x, j) => j === i ? { ...x, supplier_id: e.target.value } : x))}>
                    <option value="">— supplier —</option>{supplierOpts(suppliers.data ?? [])}{can("suppliers.manage") && <option value="__new__">+ Naya supplier add karein</option>}</select>
                  {b.supplier_id === "__new__" && <QuickAdd placeholder="Supplier ka naam" onCancel={() => setBuys(buys.map((x, j) => j === i ? { ...x, supplier_id: "" } : x))}
                    onAdd={async (name) => { const s = await api("/suppliers", { body: { name } }); await suppliers.reload(); setBuys(buys.map((x, j) => j === i ? { ...x, supplier_id: String(s.id) } : x)); }} />}
                </div>
                <select className="input col-span-3" value={b.product} onChange={(e) => setBuys(buys.map((x, j) => j === i ? { ...x, product: e.target.value } : x))}>{activeProducts.map((pr) => <option key={pr.code} value={pr.code}>{pr.name}</option>)}</select>
                <input className="input col-span-2" type="number" placeholder="litre" value={b.litres} onChange={(e) => setBuys(buys.map((x, j) => j === i ? { ...x, litres: e.target.value } : x))} />
                <input className="input col-span-2" type="number" placeholder="Rs amount" value={b.amount} onChange={(e) => setBuys(buys.map((x, j) => j === i ? { ...x, amount: e.target.value } : x))} />
                {buys.length > 1 && <button type="button" className="col-span-12 -mt-1 text-right text-xs text-red-600" onClick={() => setBuys(buys.filter((_, j) => j !== i))}>Remove line</button>}
              </div>
            ))}
          </div>
          <button type="button" className="mt-2 text-xs font-medium text-brand-700 hover:underline" onClick={() => setBuys([...buys, { supplier_id: "", product: "HSD", litres: "", amount: "" }])}>+ Add supplier</button>
          <div className="mt-1 text-xs text-slate-500">Khareeda: {Object.entries(bought).map(([p, l]) => `${num(l)} L ${PRODUCTS[p]}`).join(" · ") || "—"} · Supplier ko dene hain {pkr(buyAmt)}</div>
        </div>

        {/* Booked orders — one tap adds the order as a drop (same as a pump trip) */}
        {(() => {
          const avail = (orders.data?.open ?? []).filter((o: any) => PRODUCTS[o.product] && !drops.some((x) => x.order_id === o.id));
          if (!avail.length) return null;
          return (
            <div className="rounded-lg bg-amber-50 p-3 ring-1 ring-amber-200">
              <div className="mb-2 text-sm font-semibold text-amber-900">📋 Booked orders — tap to add as a drop</div>
              <div className="flex flex-wrap gap-2">{avail.map((o: any) => (
                <button type="button" key={o.id} className={`rounded-lg bg-white px-3 py-1.5 text-left text-sm ring-1 ${o.late ? "ring-red-300" : o.today ? "ring-amber-400" : "ring-slate-200"} hover:bg-amber-100`}
                  onClick={() => {
                    const d = { client_id: String(o.client_id), product: o.product, litres: String(o.litres), amount: "", order_id: o.id, location: o.location ?? "" };
                    const free = drops.findIndex((x) => !x.client_id && !x.litres && !x.amount);
                    setDrops(free >= 0 ? drops.map((x, j) => (j === free ? d : x)) : [...drops, d]);
                  }}>
                  <b>{o.client_name}</b> · {PRODUCTS[o.product]} {num(o.litres)} L<span className="block text-xs text-slate-500">{o.late ? "late · " : o.today ? "today · " : ""}{o.needed_on}{o.location ? ` · ${o.location}` : ""}</span>
                </button>))}</div>
            </div>
          );
        })()}

        {/* Client drops — client + litre + full amount client pays */}
        <div className="rounded-xl bg-slate-50 p-3">
          <div className="mb-2 text-sm font-semibold">Kahan drop kiya (client + litre) <span className="font-normal text-slate-400">— rate client ke apne card se, amount khud ban jaega</span> · <Ur>ڈراپ</Ur></div>
          <div className="space-y-2">
            {drops.map((d, i) => {
              const rate = clientRate(d);
              const lineAmt = dropLineAmt(d);
              return (
              <div key={i} className="grid grid-cols-12 gap-2">
                <div className="col-span-5">
                  <select className="input w-full" value={d.client_id} onChange={(e) => setDrops(drops.map((x, j) => j === i ? { ...x, client_id: e.target.value } : x))}>
                    <option value="">— client —</option>{(clients.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}{can("wholesale.manage") && <option value="__new__">+ Naya client add karein</option>}</select>
                  {d.client_id === "__new__" && <QuickAdd placeholder="Client ka naam" onCancel={() => setDrops(drops.map((x, j) => j === i ? { ...x, client_id: "" } : x))}
                    onAdd={async (name) => { const c = await api("/wholesale/clients", { body: { name } }); await clients.reload(); setDrops(drops.map((x, j) => j === i ? { ...x, client_id: String(c.id) } : x)); }} />}
                </div>
                <select className="input col-span-3" value={d.product} onChange={(e) => setDrops(drops.map((x, j) => j === i ? { ...x, product: e.target.value } : x))}>{activeProducts.map((pr) => <option key={pr.code} value={pr.code}>{pr.name}</option>)}</select>
                <input className="input col-span-2" type="number" placeholder="litre" value={d.litres} onChange={(e) => setDrops(drops.map((x, j) => j === i ? { ...x, litres: e.target.value } : x))} />
                <div className="col-span-2 flex items-center justify-end rounded-lg bg-white px-2 text-right text-sm tabular-nums ring-1 ring-slate-200" title={rate > 0 ? `Rate Rs ${rate}/L (card)` : "Rate card nahi"}>
                  {d.client_id && d.client_id !== "__new__" ? (rate > 0 ? pkr(lineAmt) : <span className="text-rose-600">rate?</span>) : <span className="text-slate-400">Rs</span>}
                </div>
                <div className="col-span-12 -mt-1 flex items-center justify-between">
                  <span className="text-xs text-slate-500">{d.order_id ? "📋 booked order · " : ""}{rate > 0 ? `Rate Rs ${rate}/L (card)` : d.client_id && d.client_id !== "__new__" ? <span className="text-rose-600">Is client ka {PRODUCTS[d.product]} rate card nahi — pehle rate set karein</span> : ""}</span>
                  {drops.length > 1 && <button type="button" className="text-xs text-red-600" onClick={() => setDrops(drops.filter((_, j) => j !== i))}>Remove drop</button>}
                </div>
              </div>
            );})}
          </div>
          <button type="button" className="mt-2 text-xs font-medium text-brand-700 hover:underline" onClick={() => setDrops([...drops, { client_id: "", product: "HSD", litres: "", amount: "", order_id: undefined, location: "" }])}>+ Add drop</button>
          <div className="mt-1 text-xs text-slate-500">Diya: {Object.entries(dropped).map(([p, l]) => `${num(l)} L ${PRODUCTS[p]}`).join(" · ") || "—"} · Client se lene hain {pkr(dropAmt)}{Object.keys({ ...dropped }).map((p) => held(p) > 0 ? ` · ${PRODUCTS[p]} held stock ${num(held(p))} L` : "").join("")}</div>
          <p className="mt-1 text-xs text-slate-400">Client ka rate uske apne rate card (CRM) se uthta hai — litre × rate = amount. Rate badalna ho to client ke rate card me badlein. Jitna khareeda + held stock se zyada drop nahi ho sakta.</p>
        </div>

        {/* Live profit = client amount − supplier amount */}
        {(buyAmt > 0 || dropAmt > 0) && <div className={`flex items-center justify-between rounded-xl px-3 py-2 text-sm font-semibold ${profit >= 0 ? "bg-emerald-50 text-emerald-800" : "bg-rose-50 text-rose-700"}`}>
          <span>Munafa (client amount − supplier amount) · <Ur>منافع</Ur></span><span className="tabular-nums">{pkr(profit)}</span>
        </div>}

        {overBy.length > 0 && <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm font-medium text-rose-700">
          {overBy.map((o) => `${PRODUCTS[o.p]}: ${num(o.l)} L drop ho raha hai lekin sirf ${num(o.have)} L available (held + abhi khareeda) — itna stock nahi.`).join(" ")} <Ur className="block">دستیاب سے زیادہ ڈیلیور نہیں ہو سکتا</Ur>
        </div>}

        <Field label="Note"><input className="input" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        <ProofPhotos value={photos} onChange={setPhotos} hint="depot invoice / bilty" />
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy || !valid}><Truck size={15} /> Save delivery</button></div>
      </form>
  );
}

/** Inline "tap to add" a new party (supplier / client) without leaving the bypass form. */
function QuickAdd({ placeholder, onAdd, onCancel }: { placeholder: string; onAdd: (name: string) => Promise<void>; onCancel: () => void }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const save = async () => {
    if (name.trim().length < 2) { setErr("Naam chhota hai"); return; }
    setBusy(true); setErr("");
    try { await onAdd(name.trim()); } catch (e: any) { setErr(e?.message ?? "Save nahi hua"); setBusy(false); }
  };
  return (
    <div className="mt-1 flex gap-1">
      <input autoFocus className="input flex-1" placeholder={placeholder} value={name} onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); save(); } }} />
      <button type="button" className="btn-primary !px-2" disabled={busy} onClick={save}><Plus size={14} /></button>
      <button type="button" className="btn-secondary !px-2" onClick={onCancel}>✕</button>
      {err && <span className="self-center text-xs text-rose-600">{err}</span>}
    </div>
  );
}

function SupplierStatement({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged: () => void }) {
  const { can } = useAuth();
  const [range, setRange] = useState({ from: "", to: "" });
  const qs = [range.from && `from=${range.from}`, range.to && `to=${range.to}`].filter(Boolean).join("&");
  const { data, reload } = useApi<any>(`/bypass/suppliers/${id}/statement${qs ? `?${qs}` : ""}`);
  const [pay, setPay] = useState(false);
  const refresh = () => { reload(); onChanged(); };
  const csvUrl = `/api/bypass/suppliers/${id}/statement.csv?${qs ? qs + "&" : ""}token=${linkToken()}`;
  const s = data?.supplier;
  return (
    <Modal open onClose={onClose} title={s ? [s.company, s.name].filter(Boolean).join(" — ") : "Bypass supplier"} wide>
      {!data ? <Loading /> : (
        <div className="space-y-4">
          <div className="own-title hidden print:block">
            <div className="flex items-end justify-between border-b border-slate-300 pb-2">
              <div><div className="text-lg font-bold">{[s.company, s.name].filter(Boolean).join(" — ")}</div><div className="text-xs text-slate-600">{[s.depot_name ? `Depot: ${s.depot_name}` : "", "Bypass supplier statement"].filter(Boolean).join(" · ")}</div></div>
              <div className="text-right text-sm">We owe: <b className="tabular-nums">{pkr(s.bypass_owed)}</b></div>
            </div>
          </div>
          <div className="flex flex-wrap items-end gap-3 print:hidden">
            <Stat label="We owe (bypass)" value={pkr(s.bypass_owed)} tone="red" />
            {can("wholesale.manage") && <button className="btn-primary min-h-10" onClick={() => setPay(true)}><HandCoins size={15} /> Pay supplier</button>}
            <Field label="From"><input className="input" type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} /></Field>
            <Field label="To"><input className="input" type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} /></Field>
            <a className="btn-secondary min-h-10" href={csvUrl}><Download size={15} /> Excel / CSV</a>
            <button className="btn-secondary min-h-10" onClick={() => window.print()}><Printer size={15} /> Print</button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="border-b text-left text-xs text-slate-500"><th className="py-2">Date</th><th>Entry</th><th className="text-right">We owe</th><th className="text-right">Paid</th><th className="text-right">Balance</th>{can("wholesale.void") && <th></th>}</tr></thead>
              <tbody>
                <tr className="border-b bg-slate-50"><td className="py-2" colSpan={4}>Opening</td><td className="text-right tabular-nums">{pkr(data.opening_balance)}</td>{can("wholesale.void") && <td></td>}</tr>
                {data.lines.map((l: any) => (
                  <tr key={`${l.kind}-${l.id}`} className="border-b">
                    <td className="py-2 whitespace-nowrap">{dt(l.txn_date)}</td>
                    <td><span className="flex flex-wrap items-center gap-1">{l.kind === "purchase" ? `Bypass fuel${l.litres ? ` · ${num(l.litres)} L ${PRODUCTS[l.product] ?? l.product} @ ${l.cost_rate}` : ""}` : `Payment${l.mode && l.mode !== "we_pay" ? ` · ${l.mode}` : ""}${l.method ? ` · ${l.method}` : ""}`}{l.ref ? <span className="text-xs text-slate-400"> · {l.ref}</span> : ""}<ProofThumbs ids={l.proof_ids} onChanged={refresh} /></span></td>
                    <td className="text-right tabular-nums text-rose-600">{l.debit ? pkr(l.debit) : ""}</td>
                    <td className="text-right tabular-nums text-emerald-600">{l.credit ? pkr(l.credit) : ""}</td>
                    <td className="text-right font-medium tabular-nums">{pkr(l.balance)}</td>
                    {can("wholesale.void") && <td className="text-right">{<button className="text-rose-600 hover:underline" title="Void" onClick={async () => { if (confirm("Void this entry?")) { await api(`/bypass/${l.kind === "purchase" ? "purchases" : "payments"}/${l.id}/void`, { body: {} }).catch((e) => alert(e.message)); refresh(); } }}><Ban size={14} /></button>}</td>}
                  </tr>
                ))}
                <tr className="border-t-2 font-bold"><td className="py-2" colSpan={4}>Closing (we owe)</td><td className="text-right tabular-nums">{pkr(data.closing_balance)}</td>{can("wholesale.void") && <td></td>}</tr>
              </tbody>
            </table>
          </div>
        </div>
      )}
      {pay && <PaySupplier id={id} owed={s?.bypass_owed ?? 0} onClose={() => setPay(false)} onDone={() => { setPay(false); refresh(); }} />}
    </Modal>
  );
}

function PaySupplier({ id, owed, onClose, onDone }: { id: number; owed: number; onClose: () => void; onDone: () => void }) {
  const clients = useApi<any[]>("/wholesale/clients");
  const [f, setF] = useState({ amount: "", mode: "we_pay", method: "Bank transfer", client_id: "", ref: "", note: "" });
  const [acc, setAcc] = useState<number | null>(null);
  const [photos, setPhotos] = useState<number[]>([]);
  const { busy, run } = useAction();
  const needsClient = f.mode !== "we_pay";
  const needsBank = f.mode !== "client_direct";
  const MODES: [string, string, string][] = [
    ["we_pay", "Hum pay karein", "humare bank/cash se supplier ko"],
    ["client_direct", "Client ne direct diya", "client ne supplier ko khud diya — uski due bhi kam"],
    ["through_us", "Client → hum → supplier", "client ne hamein bheja, hum ne aage diya (net-zero)"],
  ];
  const valid = Number(f.amount) > 0 && (!needsClient || f.client_id) && (!needsBank || f.mode === "client_direct" || true);
  return (
    <Modal open onClose={onClose} title="Pay bypass supplier">
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const body: any = { amount: Number(f.amount), mode: f.mode, ref: f.ref || null, note: f.note || null, photo_ids: photos };
        if (needsClient) body.client_id = Number(f.client_id);
        if (needsBank) { body.method = f.method; body.account_id = acc; }
        if (await run(() => api(`/bypass/suppliers/${id}/payment`, { body }), (r: any) => `Done. We now owe ${pkr(r.bypass_owed)}${r.client_due !== undefined ? ` · client due ${pkr(r.client_due)}` : ""}`)) onDone();
      }}>
        <p className="text-sm text-slate-600">We owe: <b>{pkr(owed)}</b></p>
        <fieldset>
          <legend className="label">Kaun pay kar raha hai? · <Ur>کون ادائیگی</Ur></legend>
          <div className="grid grid-cols-1 gap-2">
            {MODES.map(([m, title, sub]) => (
              <button type="button" key={m} onClick={() => setF({ ...f, mode: m })} aria-pressed={f.mode === m}
                className={`rounded-xl px-3 py-2 text-left text-sm ${f.mode === m ? "bg-slate-800 font-semibold text-white" : "bg-slate-100 text-slate-700"}`}>{title}<span className={`block text-xs font-normal ${f.mode === m ? "text-white/80" : "text-slate-500"}`}>{sub}</span></button>
            ))}
          </div>
        </fieldset>
        <Field label="Amount (Rs) *"><input className="input" type="number" min={1} required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
        {needsClient && <Field label="Which client paid? · کون سا کلائنٹ *"><select className="input" required value={f.client_id} onChange={(e) => setF({ ...f, client_id: e.target.value })}>
          <option value="">— choose client —</option>{(clients.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name} · due {pkr(c.due)}</option>)}</select></Field>}
        {needsBank && <>
          <Field label={f.mode === "through_us" ? "Client sent to / we forwarded from which account?" : "Method"}><select className="input" value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })}>{["Bank transfer", "Cash", "Cheque", "Raast", "JazzCash", "Easypaisa", "Online"].map((m) => <option key={m}>{m}</option>)}</select></Field>
          <AccountPicker method={f.method} value={acc} onChange={setAcc} />
        </>}
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Ref / slip"><input className="input" value={f.ref} onChange={(e) => setF({ ...f, ref: e.target.value })} /></Field>
          <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        </div>
        <ProofPhotos value={photos} onChange={setPhotos} hint="payment slip / screenshot" />
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy || !valid}>Save payment</button></div>
      </form>
    </Modal>
  );
}
