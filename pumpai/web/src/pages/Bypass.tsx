import { useMemo, useState } from "react";
import { Plus, Truck, Factory, Download, Printer, HandCoins, ArrowLeft, Ban } from "lucide-react";
import { api, linkToken, useApi } from "../lib/api";
import { Empty, ErrorBox, Field, Loading, Modal, Stat, useAction } from "../components/ui";
import { PRODUCTS, dt, num, pkr, pkrShort } from "../lib/format";
import { useAuth } from "../App";
import { ProofPhotos } from "../components/Capture";
import { AccountPicker } from "../components/BankParts";
import { supplierOpts } from "../components/SupplierSelect";

const Ur = ({ children, className = "" }: { children: React.ReactNode; className?: string }) => <span lang="ur" dir="rtl" className={`font-urdu ${className}`}>{children}</span>;

/** Bypass delivery — shown inside the Wholesale "Tanker trips" tab: buy fuel from suppliers at the depot and deliver straight to wholesale clients. */
export function BypassPanel() {
  const { can } = useAuth();
  const sups = useApi<any[]>("/bypass/suppliers");
  const dels = useApi<any[]>("/bypass/deliveries");
  const [add, setAdd] = useState(false);
  const [openSup, setOpenSup] = useState<number | null>(null);
  const refresh = () => { sups.reload(); dels.reload(); };
  const owedTotal = (sups.data ?? []).reduce((a, s) => a + (s.bypass_owed > 0 ? s.bypass_owed : 0), 0);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><h2 className="flex items-center gap-2 text-lg font-semibold"><Truck size={18} className="text-slate-500" /> Bypass delivery · <Ur>بائی پاس</Ur></h2>
          <p className="text-sm text-slate-500">Depot se maal, seedha client ko — supplier ka alag khata</p></div>
        {can("wholesale.manage") && <button className="btn-primary" onClick={() => setAdd(true)}><Plus size={16} /> New bypass delivery</button>}
      </div>
      <p className="rounded-lg bg-sky-50 px-3 py-2 text-xs text-sky-900">Hum supplier se cost par maal lete hain aur client ko unke rate par dete hain — farq (margin) humara munafa. Supplier ka bypass khata pump-stock se bilkul alag hai, aur jitna maal liya usse zyada deliver nahi ho sakta. <Ur className="block">سپلائر کا بائی پاس کھاتہ پمپ اسٹاک سے الگ</Ur></p>

      <div className="flex flex-wrap gap-3">
        <Stat label="We owe bypass suppliers" value={pkrShort(owedTotal)} tone="red" />
      </div>

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
              <li key={d.id} className="flex items-center justify-between px-4 py-2.5">
                <div><div className="font-medium">#{d.id} · {num(d.litres)} L</div><div className="text-xs text-slate-400">{dt(d.txn_date)}{d.vehicle_no ? ` · ${d.vehicle_no}` : ""}{d.note ? ` · ${d.note}` : ""}</div></div>
                <div className="text-right tabular-nums text-slate-600">cost {pkr(d.cost)}</div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {add && <NewDelivery onClose={() => setAdd(false)} onDone={() => { setAdd(false); refresh(); }} />}
      {openSup && <SupplierStatement id={openSup} onClose={() => setOpenSup(null)} onChanged={refresh} />}
    </div>
  );
}

function NewDelivery({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const suppliers = useApi<any[]>("/bypass/suppliers");
  const clients = useApi<any[]>("/wholesale/clients");
  const stations = useApi<any[]>("/stations");
  const [station, setStation] = useState("");
  const [vehicle, setVehicle] = useState("");
  const [note, setNote] = useState("");
  const [buys, setBuys] = useState([{ supplier_id: "", product: "HSD", litres: "", cost_rate: "" }]);
  const [drops, setDrops] = useState([{ client_id: "", product: "HSD", litres: "" }]);
  const [photos, setPhotos] = useState<number[]>([]);
  const { busy, run } = useAction();

  const sumBy = (rows: { product: string; litres: string }[]) => rows.reduce((m, r) => { const p = r.product; m[p] = (m[p] ?? 0) + (Number(r.litres) || 0); return m; }, {} as Record<string, number>);
  const bought = useMemo(() => sumBy(buys), [buys]);
  const dropped = useMemo(() => sumBy(drops), [drops]);
  const overBy = useMemo(() => Object.entries(dropped).filter(([p, l]) => l > (bought[p] ?? 0) + 0.01).map(([p, l]) => ({ p, l, have: bought[p] ?? 0 })), [dropped, bought]);
  const cost = buys.reduce((a, b) => a + (Number(b.litres) || 0) * (Number(b.cost_rate) || 0), 0);
  const validBuys = buys.filter((b) => b.supplier_id && Number(b.litres) > 0 && Number(b.cost_rate) > 0);
  const validDrops = drops.filter((d) => d.client_id && Number(d.litres) > 0);
  const valid = station && validBuys.length > 0 && validDrops.length > 0 && overBy.length === 0;

  return (
    <Modal open onClose={onClose} title="New bypass delivery" wide>
      <form className="space-y-4" onSubmit={async (e) => {
        e.preventDefault();
        const body = {
          station_id: Number(station), vehicle_no: vehicle || null, note: note || null, photo_ids: photos,
          purchases: validBuys.map((b) => ({ supplier_id: Number(b.supplier_id), product: b.product, litres: Number(b.litres), cost_rate: Number(b.cost_rate) })),
          drops: validDrops.map((d) => ({ client_id: Number(d.client_id), product: d.product, litres: Number(d.litres) })),
        };
        if (await run(() => api("/bypass/deliveries", { body }), (r: any) => `Delivery #${r.id} saved. Cost ${pkr(r.cost)}, billed ${pkr(r.billed)}, margin ${pkr(r.margin)}`)) onDone();
      }}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Station (books under) *"><select className="input" required value={station} onChange={(e) => setStation(e.target.value)}>
            <option value="">— choose —</option>{(stations.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
          <Field label="Tanker / vehicle"><input className="input" value={vehicle} onChange={(e) => setVehicle(e.target.value)} /></Field>
        </div>

        {/* Supplier purchase lines */}
        <div className="rounded-xl bg-slate-50 p-3">
          <div className="mb-2 text-sm font-semibold">Maal kahan se liya (supplier + litre + cost) · <Ur>خریداری</Ur></div>
          <div className="space-y-2">
            {buys.map((b, i) => (
              <div key={i} className="grid grid-cols-12 gap-2">
                <select className="input col-span-5" value={b.supplier_id} onChange={(e) => setBuys(buys.map((x, j) => j === i ? { ...x, supplier_id: e.target.value } : x))}>
                  <option value="">— supplier —</option>{supplierOpts(suppliers.data ?? [])}</select>
                <select className="input col-span-3" value={b.product} onChange={(e) => setBuys(buys.map((x, j) => j === i ? { ...x, product: e.target.value } : x))}>{Object.entries(PRODUCTS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
                <input className="input col-span-2" type="number" placeholder="litre" value={b.litres} onChange={(e) => setBuys(buys.map((x, j) => j === i ? { ...x, litres: e.target.value } : x))} />
                <input className="input col-span-2" type="number" placeholder="Rs/L" value={b.cost_rate} onChange={(e) => setBuys(buys.map((x, j) => j === i ? { ...x, cost_rate: e.target.value } : x))} />
                {buys.length > 1 && <button type="button" className="col-span-12 -mt-1 text-right text-xs text-red-600" onClick={() => setBuys(buys.filter((_, j) => j !== i))}>Remove line</button>}
              </div>
            ))}
          </div>
          <button type="button" className="mt-2 text-xs font-medium text-brand-700 hover:underline" onClick={() => setBuys([...buys, { supplier_id: "", product: "HSD", litres: "", cost_rate: "" }])}>+ Add supplier</button>
          <div className="mt-1 text-xs text-slate-500">Purchased: {Object.entries(bought).map(([p, l]) => `${num(l)} L ${PRODUCTS[p]}`).join(" · ") || "—"} · Cost {pkr(cost)}</div>
        </div>

        {/* Client drops */}
        <div className="rounded-xl bg-slate-50 p-3">
          <div className="mb-2 text-sm font-semibold">Kahan drop kiya (client + litre, unke rate par) · <Ur>ڈراپ</Ur></div>
          <div className="space-y-2">
            {drops.map((d, i) => (
              <div key={i} className="grid grid-cols-12 gap-2">
                <select className="input col-span-7" value={d.client_id} onChange={(e) => setDrops(drops.map((x, j) => j === i ? { ...x, client_id: e.target.value } : x))}>
                  <option value="">— client —</option>{(clients.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
                <select className="input col-span-3" value={d.product} onChange={(e) => setDrops(drops.map((x, j) => j === i ? { ...x, product: e.target.value } : x))}>{Object.entries(PRODUCTS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
                <input className="input col-span-2" type="number" placeholder="litre" value={d.litres} onChange={(e) => setDrops(drops.map((x, j) => j === i ? { ...x, litres: e.target.value } : x))} />
                {drops.length > 1 && <button type="button" className="col-span-12 -mt-1 text-right text-xs text-red-600" onClick={() => setDrops(drops.filter((_, j) => j !== i))}>Remove drop</button>}
              </div>
            ))}
          </div>
          <button type="button" className="mt-2 text-xs font-medium text-brand-700 hover:underline" onClick={() => setDrops([...drops, { client_id: "", product: "HSD", litres: "" }])}>+ Add drop</button>
          <div className="mt-1 text-xs text-slate-500">Dropped: {Object.entries(dropped).map(([p, l]) => `${num(l)} L ${PRODUCTS[p]}`).join(" · ") || "—"}</div>
        </div>

        {overBy.length > 0 && <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm font-medium text-rose-700">
          {overBy.map((o) => `${PRODUCTS[o.p]}: ${num(o.l)} L drop ho raha hai lekin sirf ${num(o.have)} L khareeda — itna stock nahi.`).join(" ")} <Ur className="block">خریدے سے زیادہ ڈیلیور نہیں ہو سکتا</Ur>
        </div>}

        <Field label="Note"><input className="input" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        <ProofPhotos value={photos} onChange={setPhotos} hint="depot invoice / bilty" />
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy || !valid}><Truck size={15} /> Save delivery</button></div>
      </form>
    </Modal>
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
                    <td>{l.kind === "purchase" ? `Bypass fuel${l.litres ? ` · ${num(l.litres)} L ${PRODUCTS[l.product] ?? l.product} @ ${l.cost_rate}` : ""}` : `Payment${l.mode && l.mode !== "we_pay" ? ` · ${l.mode}` : ""}${l.method ? ` · ${l.method}` : ""}`}{l.ref ? <span className="text-xs text-slate-400"> · {l.ref}</span> : ""}</td>
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
