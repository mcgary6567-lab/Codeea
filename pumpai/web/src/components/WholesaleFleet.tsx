import { useEffect, useMemo, useState } from "react";
import { Plus, Printer, Trash2, Truck, UserRound, Pencil } from "lucide-react";
import { api, useApi } from "../lib/api";
import { ProofPhotos, ProofThumbs } from "./Capture";
import { Badge, Empty, Field, Loading, Modal, useAction } from "./ui";
import { PRODUCTS, d, dt, num, phone, pkr } from "../lib/format";
import { useAuth } from "../App";

const today = () => new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10);

/**
 * Tanker + driver pickers from the fleet register. Choosing a tanker fills its usual driver;
 * "Other" lets a hired vehicle be typed in.
 */
export function FleetPicker({ f, setF }: { f: any; setF: (f: any) => void }) {
  const fleet = useApi<any>("/wholesale/fleet");
  const tankers = (fleet.data?.tankers ?? []).filter((t: any) => t.active);
  const drivers = (fleet.data?.drivers ?? []).filter((x: any) => x.active);
  const driver = drivers.find((x: any) => x.id === Number(f.driver_id));
  return (
    <>
      <Field label="Tanker / vehicle">
        <select className="input" value={f.tanker_id ?? ""} onChange={(e) => {
          const t = tankers.find((x: any) => x.id === Number(e.target.value));
          setF({ ...f, tanker_id: e.target.value, vehicle_no: "", driver_id: t?.driver_id ? String(t.driver_id) : f.driver_id });
        }}>
          <option value="">— choose tanker —</option>
          {tankers.map((t: any) => <option key={t.id} value={t.id}>{t.number}{t.capacity_l ? ` · ${num(t.capacity_l)} L` : ""}{t.ownership === "hired" ? " · hired" : ""}</option>)}
          <option value="other">Other (type number)</option>
        </select>
      </Field>
      <Field label="Driver">
        <select className="input" value={f.driver_id ?? ""} onChange={(e) => setF({ ...f, driver_id: e.target.value })}>
          <option value="">— choose driver —</option>
          {drivers.map((x: any) => <option key={x.id} value={x.id}>{x.name}{x.phone ? ` · ${x.phone}` : ""}{x.licence_expired ? " · licence expired!" : ""}</option>)}
        </select>
      </Field>
      {f.tanker_id === "other" && <Field label="Vehicle no."><input className="input uppercase" required value={f.vehicle_no ?? ""} onChange={(e) => setF({ ...f, vehicle_no: e.target.value })} /></Field>}
      {driver?.licence_expired && <p className="text-xs font-medium text-red-600 sm:col-span-2">⚠ {driver.name}'s licence expired on {driver.licence_expiry}.</p>}
      {!fleet.data?.tankers?.length && fleet.data && <p className="text-xs text-slate-500 sm:col-span-2">Add your tankers and drivers once under Wholesale → Tankers & drivers; then just pick them here.</p>}
    </>
  );
}
/** Fleet fields for an API body. */
export const fleetBody = (f: any) => ({
  tanker_id: f.tanker_id && f.tanker_id !== "other" ? Number(f.tanker_id) : null,
  driver_id: f.driver_id ? Number(f.driver_id) : null,
  vehicle_no: f.tanker_id === "other" ? f.vehicle_no || null : null,
});

/* ---------------- One tanker, many drops ---------------- */
type Drop = { client_id: string; litres: string; rate: string; location: string; ref: string; order_id?: number; product?: string };
/** A trip's fuel: "HSD", or "HSD+PMG" when one tanker carried both (separate chambers). */
const depotName = (n: string) => (/depot/i.test(n ?? "") ? n : `${n} depot`);
export const fuels = (p: string) => (p ?? "").split("+").map((x) => PRODUCTS[x] ?? x).join(" + ");
const emptyDrop = (): Drop => ({ client_id: "", litres: "", rate: "", location: "", ref: "" });
/** Where a client's fuel is dropped: the address saved on the client. */
const clientAddress = (c: any): string => {
  const [addr, city] = [c?.address, c?.city].map((x) => (x ?? "").trim());
  return addr && city && !addr.toLowerCase().includes(city.toLowerCase()) ? `${addr}, ${city}` : addr || city;
};

export function TripForm({ onClose, onDone }: { onClose: () => void; onDone: (trip: any) => void }) {
  const { can } = useAuth();
  const admin = can("wholesale.rates");
  const stations = useApi<any[]>("/stations");
  const clients = useApi<any[]>("/wholesale/clients?q=");
  const fleet = useApi<any>("/wholesale/fleet");
  const orders = useApi<any>("/wholesale/orders");
  const depots = useApi<any[]>("/wholesale/depots");
  const [f, setF] = useState<any>({ station_id: "", product: "HSD", tanker_id: "", driver_id: "", vehicle_no: "", txn_date: today(), note: "", override_limit: false,
    source: "pump", supplier_id: "", depot_ref: "", freight_by: "rate", freight: "" });
  // depot-direct: purchase rate and billed litres per fuel (blank = the supplier's last rate / the litres dropped)
  const [cost, setCost] = useState<Record<string, string>>({});
  const [inv, setInv] = useState<Record<string, string>>({});
  const [drops, setDrops] = useState<Drop[]>([emptyDrop(), emptyDrop()]);
  const [photos, setPhotos] = useState<number[]>([]);
  useEffect(() => { if (stations.data && !f.station_id) setF((x: any) => ({ ...x, station_id: String(stations.data![0].id) })); }, [stations.data]);
  const { busy, run } = useAction();
  const active = (clients.data ?? []).filter((c) => c.active);
  const client = (id: string) => active.find((c) => c.id === Number(id));
  const fuelOf = (dr: Drop) => dr.product || f.product;
  const rateOf = (dr: Drop) => Number(dr.rate) || client(dr.client_id)?.rates?.[fuelOf(dr)] || 0;
  const tanker = fleet.data?.tankers?.find((t: any) => t.id === Number(f.tanker_id));
  const station = stations.data?.find((s) => s.id === Number(f.station_id));
  const filled = drops.filter((x) => x.client_id && Number(x.litres) > 0);
  // one tanker can carry diesel and petrol in separate chambers: stock comes out of each fuel's own tank
  const used = [...new Set([f.product, ...filled.map(fuelOf)])];
  const mixed = new Set(filled.map(fuelOf)).size > 1;
  const stockLines = used.map((p) => ({ p, tank: station?.tanks.filter((t: any) => t.product === p).sort((a: any, b: any) => b.current_l - a.current_l)[0],
    need: filled.filter((x) => fuelOf(x) === p).reduce((a, x) => a + Number(x.litres), 0) })).filter((x) => x.tank && (x.need > 0 || x.p === f.product));
  const total = filled.reduce((a, x) => a + Number(x.litres), 0);
  const amount = filled.reduce((a, x) => a + Number(x.litres) * rateOf(x), 0);
  const over = tanker?.capacity_l && total > tanker.capacity_l;
  const depot = f.source === "depot";
  const supplier = depots.data?.find((d) => d.id === Number(f.supplier_id));
  const buyFuels = filled.length ? [...new Set(filled.map(fuelOf))] : [f.product];
  const buy = buyFuels.map((p) => {
    const need = filled.filter((x) => fuelOf(x) === p).reduce((a, x) => a + Number(x.litres), 0);
    const rate = cost[p] !== undefined ? Number(cost[p]) : Number(supplier?.rates?.[p] ?? 0);
    const litres = Number(inv[p]) || need;
    return { p, need, rate, litres, amount: litres * rate };
  });
  const buyCost = buy.reduce((a, x) => a + x.amount, 0);
  const freight = depot && f.freight_by !== "rate" ? Number(f.freight) || 0 : 0;
  const depotReady = !depot || (supplier && buy.every((x) => x.rate > 0) && (f.freight_by === "rate" || freight > 0));
  const setDrop = (i: number, p: Partial<Drop>) => setDrops(drops.map((x, j) => (j === i ? { ...x, ...p } : x)));
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const body = {
      station_id: Number(f.station_id), product: f.product, txn_date: f.txn_date, note: f.note || null, ...fleetBody(f), photo_ids: photos,
      ...(depot ? { source: "depot", supplier_id: Number(f.supplier_id), depot_ref: f.depot_ref || null, freight_by: f.freight_by, freight: freight || null,
        cost_rates: Object.fromEntries(buy.map((x) => [x.p, x.rate])), invoice_l: Object.fromEntries(buy.filter((x) => Number(inv[x.p]) > 0).map((x) => [x.p, Number(inv[x.p])])) } : {}),
      drops: filled.map((x) => ({ client_id: Number(x.client_id), product: fuelOf(x), litres: Number(x.litres), location: x.location || null, ref: x.ref || null,
        ...(admin && x.rate ? { rate: Number(x.rate) } : {}), ...(f.override_limit ? { override_limit: true } : {}), ...(x.order_id ? { order_id: x.order_id } : {}) })),
    };
    const r = await run(() => api("/wholesale/trips", { body }), (t: any) => `Trip #${t.id} saved — ${num(t.delivered_l)} L to ${t.drops.length} drops, ${pkr(t.billed)}`);
    if (r) onDone(r);
  };
  return (
    <Modal open onClose={onClose} title="Tanker trip — one tanker, several drops" wide>
      <form onSubmit={submit} className="space-y-4">
        <div>
          <div className="mb-1 text-sm font-medium text-slate-700">Fuel loaded from · <span lang="ur" className="font-urdu">تیل کہاں سے</span></div>
          <div className="grid grid-cols-2 gap-2">{([["pump", "⛽ Our pump", "ہمارے پمپ سے"], ["depot", "🏭 Depot direct (bypass)", "ڈپو سے سیدھا"]] as const).map(([k, l, u]) => (
            <button type="button" key={k} onClick={() => setF({ ...f, source: k })} aria-pressed={f.source === k}
              className={`rounded-lg px-3 py-2 text-left text-sm ring-1 ${f.source === k ? "bg-brand-50 font-semibold text-brand-800 ring-brand-500" : "bg-white text-slate-600 ring-slate-200"}`}>
              {l}<span lang="ur" className="block font-urdu text-xs font-normal">{u}</span></button>))}</div>
        </div>
        {depot && (
          <div className="space-y-3 rounded-lg bg-sky-50 p-3 ring-1 ring-sky-200">
            <p className="text-xs text-sky-900">The tanker loads at the supplier's depot and goes straight to the clients — our tanks do not change. The supplier's bill is added to their account; each client is billed as usual.</p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <Field label="Supplier (depot)"><select className="input" required value={f.supplier_id} onChange={(e) => { setF({ ...f, supplier_id: e.target.value }); setCost({}); }}>
                <option value="">— choose supplier —</option>{(depots.data ?? []).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></Field>
              <Field label="Depot invoice / bilty no."><input className="input" value={f.depot_ref} onChange={(e) => setF({ ...f, depot_ref: e.target.value })} /></Field>
              <Field label="Freight · کرایہ"><select className="input" value={f.freight_by} onChange={(e) => setF({ ...f, freight_by: e.target.value })}>
                <option value="rate">Included in the rate</option><option value="supplier">Separate — on the supplier's bill</option><option value="cash">Separate — paid in cash</option></select></Field>
              {f.freight_by !== "rate" && <Field label="Freight amount (Rs)"><input className="input text-right tabular-nums" type="number" min={1} step="0.01" required value={f.freight} onChange={(e) => setF({ ...f, freight: e.target.value })} /></Field>}
            </div>
          </div>
        )}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Field label={depot ? "Pump (books under)" : "Loaded from station"}><select className="input" value={f.station_id} onChange={(e) => setF({ ...f, station_id: e.target.value })}>{(stations.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
          <Field label={mixed ? "Main fuel (drops can differ)" : "Fuel"}><select className="input" value={f.product} onChange={(e) => setF({ ...f, product: e.target.value })}>{Object.entries(PRODUCTS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
          <Field label="Date"><input className="input" type="date" value={f.txn_date} onChange={(e) => setF({ ...f, txn_date: e.target.value })} /></Field>
          <FleetPicker f={f} setF={setF} />
          <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        </div>

        {(() => {
          // booked orders not on the trip yet (any fuel — one tanker can carry both): one tap adds the drop
          const avail = (orders.data?.open ?? []).filter((o: any) => PRODUCTS[o.product] && !drops.some((x) => x.order_id === o.id));
          if (!avail.length) return null;
          return (
            <div className="rounded-lg bg-amber-50 p-3 ring-1 ring-amber-200">
              <div className="mb-2 text-sm font-semibold text-amber-900">📋 Booked orders — tap to add as a drop</div>
              <div className="flex flex-wrap gap-2">{avail.map((o: any) => (
                <button type="button" key={o.id} className={`rounded-lg bg-white px-3 py-1.5 text-left text-sm ring-1 ${o.late ? "ring-red-300" : o.today ? "ring-amber-400" : "ring-slate-200"} hover:bg-amber-100`}
                  onClick={() => {
                    const d = { ...emptyDrop(), client_id: String(o.client_id), litres: String(o.litres), location: o.location ?? "", order_id: o.id, product: o.product };
                    const free = drops.findIndex((x) => !x.client_id && !x.litres);
                    setDrops(free >= 0 ? drops.map((x, j) => (j === free ? d : x)) : [...drops, d]);
                  }}>
                  <b>{o.client_name}</b> · {PRODUCTS[o.product]} {num(o.litres)} L<span className="block text-xs text-slate-500">{o.late ? "late · " : o.today ? "today · " : ""}{o.needed_on}{o.location ? ` · ${o.location}` : ""}</span>
                </button>))}</div>
            </div>
          );
        })()}
        <div className="space-y-2 sm:hidden">
          {drops.map((x, i) => {
            const c = client(x.client_id);
            const fuel = fuelOf(x);
            const card = c?.rates?.[fuel];
            return (
              <div key={i} className="space-y-2 rounded-lg p-3 ring-1 ring-slate-200">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-slate-600">Drop {i + 1}{x.order_id ? " · 📋 booked" : ""}</span>
                  {drops.length > 1 && <button type="button" className="-m-2 p-2.5 text-slate-400 hover:text-red-600" onClick={() => setDrops(drops.filter((_, j) => j !== i))} aria-label="Remove drop"><Trash2 size={18} /></button>}
                </div>
                <select className="input" aria-label={`Drop ${i + 1} client`} value={x.client_id} onChange={(e) => setDrop(i, { client_id: e.target.value, rate: "", order_id: undefined, location: clientAddress(client(e.target.value)) })}>
                  <option value="">— client —</option>{active.map((c) => <option key={c.id} value={c.id}>{c.name}{c.city ? ` · ${c.city}` : ""}</option>)}</select>
                {x.location && <div className="-mt-1 text-xs text-slate-500">📍 {x.location}</div>}
                <div className="grid grid-cols-2 gap-2">
                  <select className="input" aria-label={`Drop ${i + 1} fuel`} value={fuel} onChange={(e) => setDrop(i, { product: e.target.value, rate: "", order_id: undefined })}>{Object.entries(PRODUCTS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
                  <input className="input text-right tabular-nums" type="number" inputMode="decimal" min={1} step="0.01" placeholder="Litres" aria-label={`Drop ${i + 1} litres`} value={x.litres} onChange={(e) => setDrop(i, { litres: e.target.value })} />
                  <input className="input text-right tabular-nums" type="number" inputMode="decimal" step="0.01" disabled={!admin} aria-label={`Drop ${i + 1} rate`} placeholder={card ? `Rate ${card}` : c ? "no rate" : "Rate"} value={x.rate} onChange={(e) => setDrop(i, { rate: e.target.value })} />
                  <input className="input" placeholder="Slip / ref" aria-label={`Drop ${i + 1} slip`} value={x.ref} onChange={(e) => setDrop(i, { ref: e.target.value })} />
                </div>
                <div className="flex justify-between text-sm">{c && !card ? <span className="text-xs text-red-600">No {PRODUCTS[fuel]} rate</span> : <span />}
                  <b className="tabular-nums">{Number(x.litres) > 0 && rateOf(x) ? pkr(Number(x.litres) * rateOf(x)) : "—"}</b></div>
              </div>
            );
          })}
          <div className="flex items-center justify-between gap-2 rounded-lg bg-slate-50 p-3 text-sm font-semibold">
            <button type="button" className="btn-secondary" onClick={() => setDrops([...drops, emptyDrop()])}><Plus size={15} /> Add drop</button>
            <span className="text-right tabular-nums"><span className={over ? "text-red-600" : ""}>{num(total, 2)} L</span><span className="block">{pkr(amount)}</span></span>
          </div>
        </div>
        <div className="hidden overflow-x-auto rounded-lg border border-slate-200 sm:block">
          <table className="w-full min-w-[820px]">
            <thead><tr><th className="th w-8">#</th><th className="th">Client</th><th className="th w-32">Fuel</th><th className="th w-28 text-right">Litres</th><th className="th w-32 text-right">Rate (Rs/L)</th><th className="th text-right">Amount</th><th className="th">Slip / ref</th><th className="th w-8" /></tr></thead>
            <tbody>{drops.map((x, i) => {
              const c = client(x.client_id);
              const fuel = fuelOf(x);
              const card = c?.rates?.[fuel];
              return (
                <tr key={i}>
                  <td className="td text-sm text-slate-500">{i + 1}{x.order_id ? <span title="Booked order" className="block text-xs">📋</span> : null}</td>
                  <td className="td"><select className="input min-w-[200px]" value={x.client_id} onChange={(e) => setDrop(i, { client_id: e.target.value, rate: "", order_id: undefined, location: clientAddress(client(e.target.value)) })}>
                    <option value="">— client —</option>{active.map((c) => <option key={c.id} value={c.id}>{c.name}{c.city ? ` · ${c.city}` : ""}</option>)}</select>
                    {x.location && <div className="mt-0.5 truncate text-[11px] text-slate-500">📍 {x.location}</div>}</td>
                  <td className="td"><select className="input min-w-[120px]" aria-label={`Drop ${i + 1} fuel`} value={fuel} onChange={(e) => setDrop(i, { product: e.target.value, rate: "", order_id: undefined })}>{Object.entries(PRODUCTS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></td>
                                    <td className="td"><input className="input text-right tabular-nums" type="number" min={1} step="0.01" value={x.litres} onChange={(e) => setDrop(i, { litres: e.target.value })} /></td>
                  <td className="td"><input className="input text-right tabular-nums" type="number" step="0.01" disabled={!admin} placeholder={card ? String(card) : c ? "no rate" : ""} value={x.rate} onChange={(e) => setDrop(i, { rate: e.target.value })} />
                    {c && !card && <div className="text-[11px] text-red-600">No {PRODUCTS[fuel]} rate</div>}</td>
                  <td className="td text-right text-sm tabular-nums">{Number(x.litres) > 0 && rateOf(x) ? pkr(Number(x.litres) * rateOf(x)) : "—"}</td>
                  <td className="td"><input className="input w-24" value={x.ref} onChange={(e) => setDrop(i, { ref: e.target.value })} /></td>
                  <td className="td">{drops.length > 1 && <button type="button" className="text-slate-400 hover:text-red-600" onClick={() => setDrops(drops.filter((_, j) => j !== i))} aria-label="Remove drop"><Trash2 size={15} /></button>}</td>
                </tr>
              );
            })}</tbody>
            <tfoot><tr className="bg-slate-50 font-semibold"><td className="td" colSpan={3}>
              <button type="button" className="btn-secondary !py-1 text-xs" onClick={() => setDrops([...drops, emptyDrop()])}><Plus size={13} /> Add drop</button></td>
              <td className={`td text-right tabular-nums ${over ? "text-red-600" : ""}`}>{num(total, 2)} L</td><td className="td" /><td className="td text-right tabular-nums">{pkr(amount)}</td><td className="td" colSpan={2} /></tr></tfoot>
          </table>
        </div>
        {depot && (
          <div className="space-y-2 rounded-lg bg-sky-50 p-3 ring-1 ring-sky-200">
            <div className="text-sm font-semibold text-sky-900">Supplier's bill · <span lang="ur" className="font-urdu">سپلائر کا بل</span></div>
            <div className="space-y-2">{buy.map((x) => (
              <div key={x.p} className="grid grid-cols-2 gap-2 sm:grid-cols-4 sm:items-end">
                <div className="col-span-2 text-sm font-medium sm:col-span-1 sm:pb-2">{PRODUCTS[x.p]}{x.need ? <span className="text-slate-500"> · {num(x.need)} L dropped</span> : null}</div>
                <Field label="Purchase rate (Rs/L)"><input className="input text-right tabular-nums" type="number" step="0.01" min={0} aria-label={`${PRODUCTS[x.p]} purchase rate`}
                  value={cost[x.p] ?? (supplier?.rates?.[x.p] ?? "")} onChange={(e) => setCost({ ...cost, [x.p]: e.target.value })} /></Field>
                <Field label="Depot billed (L)"><input className="input text-right tabular-nums" type="number" step="0.01" min={0} placeholder={x.need ? String(x.need) : ""} aria-label={`${PRODUCTS[x.p]} billed litres`}
                  value={inv[x.p] ?? ""} onChange={(e) => setInv({ ...inv, [x.p]: e.target.value })} /></Field>
                <div className="pb-2 text-right text-sm tabular-nums">{x.amount ? pkr(x.amount) : "—"}
                  {x.litres > x.need && x.need > 0 && <span className="block text-[11px] text-amber-700">{num(x.litres - x.need, 2)} L short — claimed from the supplier (beyond the allowed loss)</span>}</div>
              </div>))}</div>
            {supplier && <p className="text-[11px] text-slate-500">Rate filled from {supplier.name}'s last bill — change it if this invoice is different.</p>}
          </div>
        )}
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
          {tanker?.capacity_l && <span className={over ? "font-semibold text-red-600" : ""}>Tanker {tanker.number}: {num(tanker.capacity_l)} L{over ? ` — ${num(total - tanker.capacity_l)} L too much` : ` · ${num(tanker.capacity_l - total)} L space left`}</span>}
          {!depot && stockLines.map(({ p, tank, need }) => <span key={p} className={tank.current_l < need ? "font-semibold text-red-600" : ""}>{tank.name}: {num(tank.current_l)} L in stock{mixed ? ` · ${PRODUCTS[p]} on this trip ${num(need)} L` : ""}</span>)}
          {mixed && !depot && <span className="font-medium text-brand-700">Mixed load — each fuel comes out of its own tank.</span>}
          {depot && filled.length > 0 && <span className="font-medium">Supplier bill {pkr(buyCost + (f.freight_by === "supplier" ? freight : 0))}{freight ? ` · freight ${pkr(freight)}` : ""} · clients billed {pkr(amount)} ·{" "}
            <b className={amount - buyCost - freight < 0 ? "text-red-600" : "text-emerald-700"}>profit {pkr(amount - buyCost - freight)}</b></span>}
          <span>Each client is billed at their own rate card{admin ? " (type a rate to change it for this drop)" : ""}.</span>
        </div>
        <ProofPhotos value={photos} onChange={setPhotos} hint="loaded tanker, gate pass, signed trip sheet" />
        {admin && <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={f.override_limit} onChange={(e) => setF({ ...f, override_limit: e.target.checked })} /> Allow even if a client crosses the credit limit (admin)</label>}
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy || !filled.length || Boolean(over) || !depotReady}><Truck size={15} /> Save trip ({filled.length} drops)</button></div>
      </form>
    </Modal>
  );
}

/** Printable trip sheet: tanker, driver and every drop. */
export function TripSheet({ id, onClose }: { id: number; onClose: () => void }) {
  const { data } = useApi<any>(`/wholesale/trips/${id}`);
  return (
    <Modal open onClose={onClose} title={`Trip sheet #${id}`} wide>
      {!data ? <Loading /> : (
        <div className="space-y-3 text-sm">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <div><b>{dt(data.trip_date)}</b> · {fuels(data.product)} {data.depot ? <>straight from <b>{depotName(data.supplier_name)}</b>{data.depot_ref ? ` (${data.depot_ref})` : ""} · {data.station_name}'s books</> : <>from {data.station_name}{data.tank_name ? ` (${data.tank_name})` : ""}</>}</div>
            <div>🚛 <b>{data.vehicle_no ?? "—"}</b> · 👤 {data.driver_name ?? "—"}{data.driver_phone ? ` · ${phone(data.driver_phone)}` : ""}{data.driver_cnic ? ` · CNIC ${data.driver_cnic}` : ""}{data.driver_licence ? ` · licence ${data.driver_licence}` : ""}</div>
          </div>
          <ul className="divide-y divide-slate-100 rounded-lg ring-1 ring-slate-200 sm:hidden print:hidden">{data.drops.map((x: any, i: number) => (
            <li key={x.id} className={`space-y-0.5 p-3 ${x.voided ? "text-slate-400 line-through" : ""}`}>
              <div className="flex justify-between gap-2"><b className="min-w-0">{i + 1}. {x.client_name}</b><b className="shrink-0 tabular-nums">{pkr(x.amount)}</b></div>
              <div className="flex justify-between gap-2 text-xs text-slate-600"><span>{PRODUCTS[x.product]} · {num(x.litres, 2)} L × {x.rate}</span><span className="shrink-0">{x.ref} <ProofThumbs ids={x.proof_ids} /></span></div>
              {(x.location || x.phone) && <div className="text-xs text-slate-500">{x.location ? `📍 ${x.location}` : ""}{x.phone ? ` · ${phone(x.phone)}` : ""}</div>}
            </li>))}
            <li className="flex justify-between p-3 font-semibold"><span>Total ({data.drops.filter((x: any) => !x.voided).length} drops)</span><span className="text-right tabular-nums">{num(data.delivered_l, 2)} L<span className="block">{pkr(data.billed)}</span></span></li>
          </ul>
          <div className="hidden overflow-x-auto sm:block print:block"><table className="w-full min-w-[640px]"><thead><tr><th className="th">#</th><th className="th">Client</th><th className="th">Location</th>{data.product.includes("+") && <th className="th">Fuel</th>}<th className="th text-right">Litres</th><th className="th text-right">Rate</th><th className="th text-right">Amount</th><th className="th">Ref</th><th className="th">Signature</th></tr></thead>
            <tbody>{data.drops.map((x: any, i: number) => (
              <tr key={x.id} className={x.voided ? "text-slate-400 line-through" : ""}><td className="td">{i + 1}</td><td className="td">{x.client_name}{x.phone ? <div className="text-xs text-slate-500">{phone(x.phone)}</div> : null}</td>
                <td className="td">{x.location ?? "—"}</td>{data.product.includes("+") && <td className="td">{PRODUCTS[x.product]}</td>}<td className="td text-right tabular-nums">{num(x.litres, 2)}</td><td className="td text-right tabular-nums">{x.rate}</td>
                <td className="td text-right tabular-nums">{pkr(x.amount)}</td><td className="td text-xs">{x.ref} <ProofThumbs ids={x.proof_ids} /></td><td className="td w-28 border-b border-dashed border-slate-300" /></tr>
            ))}</tbody>
            <tfoot><tr className="font-semibold"><td className="td" colSpan={data.product.includes("+") ? 4 : 3}>Total ({data.drops.filter((x: any) => !x.voided).length} drops)</td><td className="td text-right tabular-nums">{num(data.delivered_l, 2)} L</td><td className="td" /><td className="td text-right tabular-nums">{pkr(data.billed)}</td><td className="td" colSpan={2} /></tr></tfoot>
          </table></div>
          {data.depot && (
            <div className="rounded-lg bg-sky-50 p-3 text-sm ring-1 ring-sky-200">
              <div className="font-semibold">🏭 Depot direct — not from our tanks</div>
              {data.depot.purchases.map((x: any, i: number) => <div key={i}>{x.product ? `${PRODUCTS[x.product]}: ${num(x.litres, 2)} L × ${x.rate}` : "Freight on the bill"} = {pkr(x.amount)}</div>)}
              {data.freight_by === "cash" && <div>Freight paid in cash: {pkr(data.freight)}</div>}
              {data.depot.short_l > 0 && <div className="text-amber-700">Depot billed {num(data.invoice_l, 2)} L, clients got {num(data.invoice_l - data.depot.short_l, 2)} L — {num(data.depot.short_l, 2)} L short</div>}
              {data.depot.claims.map((c: any) => <div key={c.id} className="text-amber-800">Shortage claim #{c.id} on {data.supplier_name}: {PRODUCTS[c.product]} {num(c.litres, 2)} L = {pkr(c.amount)} · {c.status.replace("_", " ")}{c.recovered ? ` · got ${pkr(c.recovered)}` : ""} <span className="text-xs text-slate-500">(Accounts → Claims)</span></div>)}
              <div className="mt-1 font-semibold">Profit on this trip: <span className={data.depot.profit < 0 ? "text-red-600" : "text-emerald-700"}>{pkr(data.depot.profit)}</span></div>
            </div>
          )}
          {data.note && <p className="text-slate-600">Note: {data.note}</p>}
          {data.proof_ids && <div className="flex items-center gap-2 text-slate-600">Photos: <ProofThumbs ids={data.proof_ids} /></div>}
          <p className="text-xs text-slate-500">Entered by {data.created_by}. To cancel one drop, void it in that client's statement — {data.depot ? "the supplier's bill stays (fix it in the supplier's account if the depot takes the fuel back)." : "stock goes back to the tank."}</p>
          <div className="flex justify-end print:hidden"><button className="btn-secondary" onClick={() => window.print()}><Printer size={15} /> Print</button></div>
        </div>
      )}
    </Modal>
  );
}

export function TripsTab({ onNew }: { onNew?: () => void }) {
  const { data } = useApi<any[]>("/wholesale/trips");
  const [open, setOpen] = useState<number | null>(null);
  if (!data) return <Loading />;
  return (
    <div className="card">
      <div className="flex flex-wrap items-center justify-between gap-2 p-4 pb-2"><h2 className="font-semibold">Tanker trips · <span lang="ur" className="font-urdu">ٹینکر ٹرپ</span></h2>{onNew && <button className="btn-primary" onClick={onNew}><Truck size={15} /> New trip · <span lang="ur" className="font-urdu">نیا ٹرپ</span></button>}</div>
      <ul className="divide-y divide-slate-100 sm:hidden">{data.map((t) => (
        <li key={t.id} className="cursor-pointer space-y-1 px-4 py-3 active:bg-slate-50" onClick={() => setOpen(t.id)}>
          <div className="flex justify-between gap-2"><span className="font-medium">#{t.id} · {t.vehicle_no ?? "—"}</span><span className="font-semibold tabular-nums">{pkr(t.amount)}</span></div>
          <div className="flex justify-between gap-2 text-xs text-slate-500"><span className="truncate">{dt(t.trip_date)} · {t.driver_name ?? "—"}</span><span className="shrink-0">{t.source === "depot" ? "🏭 " : ""}{fuels(t.product)} · {num(t.litres)} L · {t.drops} drops</span></div>
        </li>
      ))}</ul>
      <div className="hidden overflow-x-auto sm:block"><table className="w-full">
        <thead><tr><th className="th">Trip</th><th className="th">Date</th><th className="th">Tanker</th><th className="th">Driver</th><th className="th">Product</th><th className="th text-right">Litres</th><th className="th text-right">Drops</th><th className="th text-right">Billed</th></tr></thead>
        <tbody>{data.map((t) => (
          <tr key={t.id} className="cursor-pointer hover:bg-slate-50" onClick={() => setOpen(t.id)}>
            <td className="td font-medium">#{t.id}</td><td className="td text-xs">{dt(t.trip_date)}</td><td className="td">{t.vehicle_no ?? "—"}</td><td className="td">{t.driver_name ?? "—"}</td>
            <td className="td">{fuels(t.product)}{t.source === "depot" && <div className="text-xs text-sky-700">🏭 {depotName(t.supplier_name)}</div>}</td><td className="td text-right tabular-nums">{num(t.litres)} L</td><td className="td text-right">{t.drops}</td><td className="td text-right tabular-nums">{pkr(t.amount)}</td>
          </tr>
        ))}</tbody>
      </table></div>{!data.length && <Empty>No tanker trips yet · ابھی کوئی ٹرپ نہیں. One trip can drop fuel at several clients — each at their own rate.</Empty>}
      {open && <TripSheet id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

/* ---------------- Tankers & drivers register ---------------- */
export function FleetTab({ start }: { start?: "tanker" | "driver" | null }) {
  const { can } = useAuth();
  const { data, reload } = useApi<any>("/wholesale/fleet");
  const [tk, setTk] = useState<any | null>(start === "tanker" ? {} : null);
  const [dr, setDr] = useState<any | null>(start === "driver" ? {} : null);
  const edit = can("wholesale.manage");
  if (!data) return <Loading />;
  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
      <div className="card">
        <div className="flex items-center justify-between p-4 pb-2"><h2 className="flex items-center gap-2 whitespace-nowrap font-semibold"><Truck size={17} /> Tankers · <span lang="ur" className="font-urdu">ٹینکر</span> ({data.tankers.length})</h2>{edit && <button className="btn-secondary whitespace-nowrap" onClick={() => setTk({})}><Plus size={15} /> Add · <span lang="ur" className="font-urdu">شامل کریں</span></button>}</div>
        <ul className="divide-y divide-slate-100 sm:hidden">{data.tankers.map((t: any) => (
          <li key={t.id} className={`flex items-start gap-3 px-4 py-3 ${t.active ? "" : "opacity-50"}`}>
            <div className="min-w-0 flex-1">
              <div className="font-medium">{t.number} {t.ownership === "hired" && <Badge tone="amber">hired</Badge>}</div>
              <div className="text-xs text-slate-500">{t.capacity_l ? `${num(t.capacity_l)} L` : "—"}{t.chambers ? ` · ${t.chambers} chambers` : ""} · 👤 {t.driver_name ?? "—"}</div>
              {t.owner_name && <div className="text-xs text-slate-500">Owner: {t.owner_name}{t.owner_phone ? ` · ${t.owner_phone}` : ""}</div>}
              <div className="text-[11px] text-slate-400">Last trip {t.last_trip ? d(t.last_trip) : "—"}</div>
            </div>
            {edit && <button className="p-1 text-slate-400" onClick={() => setTk(t)} aria-label={`Edit ${t.number}`}><Pencil size={16} /></button>}
          </li>
        ))}</ul>
        <table className="hidden w-full sm:table"><thead><tr><th className="th">Number</th><th className="th text-right">Capacity</th><th className="th">Usual driver</th><th className="th">Last trip</th><th className="th" /></tr></thead>
          <tbody>{data.tankers.map((t: any) => (
            <tr key={t.id} className={t.active ? "" : "opacity-50"}><td className="td font-medium">{t.number} {t.ownership === "hired" && <Badge tone="amber">hired</Badge>}{t.owner_name && <div className="text-xs text-slate-500">{t.owner_name}{t.owner_phone ? ` · ${t.owner_phone}` : ""}</div>}</td>
              <td className="td text-right tabular-nums">{t.capacity_l ? `${num(t.capacity_l)} L` : "—"}{t.chambers ? <div className="text-xs text-slate-500">{t.chambers} chambers</div> : null}</td>
              <td className="td text-sm">{t.driver_name ?? "—"}</td><td className="td text-xs text-slate-500">{t.last_trip ? d(t.last_trip) : "—"}</td>
              <td className="td">{edit && <button className="text-slate-400 hover:text-slate-700" onClick={() => setTk(t)} aria-label={`Edit ${t.number}`}><Pencil size={14} /></button>}</td></tr>
          ))}</tbody></table>
        {!data.tankers.length && <Empty>No tankers on file yet</Empty>}
      </div>
      <div className="card">
        <div className="flex items-center justify-between p-4 pb-2"><h2 className="flex items-center gap-2 whitespace-nowrap font-semibold"><UserRound size={17} /> Drivers · <span lang="ur" className="font-urdu">ڈرائیور</span> ({data.drivers.length})</h2>{edit && <button className="btn-secondary whitespace-nowrap" onClick={() => setDr({})}><Plus size={15} /> Add · <span lang="ur" className="font-urdu">شامل کریں</span></button>}</div>
        <ul className="divide-y divide-slate-100 sm:hidden">{data.drivers.map((x: any) => (
          <li key={x.id} className={`flex items-start gap-3 px-4 py-3 ${x.active ? "" : "opacity-50"}`}>
            <div className="min-w-0 flex-1">
              <div className="font-medium">{x.name}{x.phone && <span className="ml-2 text-xs font-normal text-slate-500">{phone(x.phone)}</span>}</div>
              <div className="text-xs text-slate-500">CNIC {x.cnic ?? "—"} · Licence {x.licence_no ?? "—"}</div>
              {x.licence_expiry && <div className={`text-xs ${x.licence_expired ? "font-semibold text-red-600" : "text-slate-500"}`}>{x.licence_expired ? "Licence expired · لائسنس ختم " : "Valid till "}{x.licence_expiry}</div>}
              <div className="text-[11px] text-slate-400">Last trip {x.last_trip ? d(x.last_trip) : "—"}</div>
            </div>
            {edit && <button className="p-1 text-slate-400" onClick={() => setDr(x)} aria-label={`Edit ${x.name}`}><Pencil size={16} /></button>}
          </li>
        ))}</ul>
        <table className="hidden w-full sm:table"><thead><tr><th className="th">Driver</th><th className="th">CNIC</th><th className="th">Licence</th><th className="th">Last trip</th><th className="th" /></tr></thead>
          <tbody>{data.drivers.map((x: any) => (
            <tr key={x.id} className={x.active ? "" : "opacity-50"}><td className="td font-medium">{x.name}{x.phone && <div className="text-xs text-slate-500">{phone(x.phone)}</div>}</td>
              <td className="td text-xs">{x.cnic ?? "—"}</td>
              <td className="td text-xs">{x.licence_no ?? "—"}{x.licence_expiry && <div className={x.licence_expired ? "font-semibold text-red-600" : "text-slate-500"}>{x.licence_expired ? "expired " : "till "}{x.licence_expiry}</div>}</td>
              <td className="td text-xs text-slate-500">{x.last_trip ? d(x.last_trip) : "—"}</td>
              <td className="td">{edit && <button className="text-slate-400 hover:text-slate-700" onClick={() => setDr(x)} aria-label={`Edit ${x.name}`}><Pencil size={14} /></button>}</td></tr>
          ))}</tbody></table>
        {!data.drivers.length && <Empty>No drivers on file yet</Empty>}
      </div>
      {tk && <TankerForm initial={tk} drivers={data.drivers} onClose={() => setTk(null)} onSaved={() => { setTk(null); reload(); }} />}
      {dr && <DriverForm initial={dr} onClose={() => setDr(null)} onSaved={() => { setDr(null); reload(); }} />}
    </div>
  );
}

function TankerForm({ initial, drivers, onClose, onSaved }: { initial: any; drivers: any[]; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState<any>({ number: "", capacity_l: "", chambers: "", ownership: "own", owner_name: "", owner_phone: "", driver_id: "", notes: "", active: true, ...initial,
    ...(initial.id ? { capacity_l: initial.capacity_l ?? "", chambers: initial.chambers ?? "", driver_id: initial.driver_id ?? "", active: Boolean(initial.active) } : {}) });
  const { busy, run } = useAction();
  const body = useMemo(() => ({ number: f.number, capacity_l: f.capacity_l ? Number(f.capacity_l) : null, chambers: f.chambers ? Number(f.chambers) : null, ownership: f.ownership,
    owner_name: f.owner_name || null, owner_phone: f.owner_phone || null, driver_id: f.driver_id ? Number(f.driver_id) : null, notes: f.notes || null, active: f.active }), [f]);
  return (
    <Modal open onClose={onClose} title={initial.id ? `Tanker ${initial.number}` : "Add tanker · نیا ٹینکر"}>
      <form className="grid grid-cols-1 gap-3 sm:grid-cols-2" onSubmit={async (e) => {
        e.preventDefault();
        if (await run(() => initial.id ? api(`/wholesale/tankers/${initial.id}`, { method: "PATCH", body }) : api("/wholesale/tankers", { body }), "Tanker saved")) onSaved();
      }}>
        <Field label="Number plate · نمبر پلیٹ *"><input className="input uppercase" required value={f.number} onChange={(e) => setF({ ...f, number: e.target.value })} placeholder="TLA-1234" /></Field>
        <Field label="Capacity (litres) · گنجائش"><input className="input" type="number" min={1} value={f.capacity_l} onChange={(e) => setF({ ...f, capacity_l: e.target.value })} /></Field>
        <Field label="Chambers · خانے"><input className="input" type="number" min={1} max={10} value={f.chambers} onChange={(e) => setF({ ...f, chambers: e.target.value })} /></Field>
        <Field label="Ownership · ملکیت"><select className="input" value={f.ownership} onChange={(e) => setF({ ...f, ownership: e.target.value })}><option value="own">Our own · اپنا</option><option value="hired">Hired · کرائے کا</option></select></Field>
        {f.ownership === "hired" && <><Field label="Owner name · مالک کا نام"><input className="input" value={f.owner_name} onChange={(e) => setF({ ...f, owner_name: e.target.value })} /></Field>
          <Field label="Owner phone · مالک کا فون"><input className="input" value={f.owner_phone} onChange={(e) => setF({ ...f, owner_phone: e.target.value })} /></Field></>}
        <Field label="Usual driver · ڈرائیور"><select className="input" value={f.driver_id} onChange={(e) => setF({ ...f, driver_id: e.target.value })}><option value="">—</option>{drivers.filter((x) => x.active).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
        <Field label="Notes · نوٹ"><input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
        {initial.id && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> In use</label>}
        <div className="flex justify-end gap-2 sm:col-span-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel · منسوخ</button><button className="btn-primary" disabled={busy}>Save · محفوظ کریں</button></div>
      </form>
    </Modal>
  );
}

function DriverForm({ initial, onClose, onSaved }: { initial: any; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState<any>({ name: "", phone: "", cnic: "", licence_no: "", licence_expiry: "", address: "", notes: "", active: true,
    ...Object.fromEntries(Object.entries(initial).map(([k, v]) => [k, v ?? ""])), ...(initial.id ? { active: Boolean(initial.active) } : {}) });
  const { busy, run } = useAction();
  const body = { name: f.name, phone: f.phone || null, cnic: f.cnic || null, licence_no: f.licence_no || null, licence_expiry: f.licence_expiry || null, address: f.address || null, notes: f.notes || null, active: f.active };
  return (
    <Modal open onClose={onClose} title={initial.id ? `Driver: ${initial.name}` : "Add driver · نیا ڈرائیور"}>
      <form className="grid grid-cols-1 gap-3 sm:grid-cols-2" onSubmit={async (e) => {
        e.preventDefault();
        if (await run(() => initial.id ? api(`/wholesale/drivers/${initial.id}`, { method: "PATCH", body }) : api("/wholesale/drivers", { body }), "Driver saved")) onSaved();
      }}>
        <Field label="Name · نام *"><input className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Mobile · موبائل"><input className="input" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} placeholder="03xx xxxxxxx" /></Field>
        <Field label="CNIC · شناختی کارڈ"><input className="input" value={f.cnic} onChange={(e) => setF({ ...f, cnic: e.target.value })} placeholder="35202-1234567-1" /></Field>
        <Field label="Licence no. · لائسنس نمبر"><input className="input" value={f.licence_no} onChange={(e) => setF({ ...f, licence_no: e.target.value })} /></Field>
        <Field label="Licence valid till · لائسنس کی میعاد"><input className="input" type="date" value={f.licence_expiry} onChange={(e) => setF({ ...f, licence_expiry: e.target.value })} /></Field>
        <Field label="Address · پتہ"><input className="input" value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} /></Field>
        <div className="sm:col-span-2"><Field label="Notes · نوٹ"><input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field></div>
        {initial.id && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Still working</label>}
        <div className="flex justify-end gap-2 sm:col-span-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel · منسوخ</button><button className="btn-primary" disabled={busy}>Save · محفوظ کریں</button></div>
      </form>
    </Modal>
  );
}

