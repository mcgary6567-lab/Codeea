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
type Drop = { client_id: string; litres: string; rate: string; location: string; ref: string; order_id?: number };
const emptyDrop = (): Drop => ({ client_id: "", litres: "", rate: "", location: "", ref: "" });

export function TripForm({ onClose, onDone }: { onClose: () => void; onDone: (trip: any) => void }) {
  const { can } = useAuth();
  const admin = can("wholesale.rates");
  const stations = useApi<any[]>("/stations");
  const clients = useApi<any[]>("/wholesale/clients?q=");
  const fleet = useApi<any>("/wholesale/fleet");
  const orders = useApi<any>("/wholesale/orders");
  const [f, setF] = useState<any>({ station_id: "", product: "HSD", tanker_id: "", driver_id: "", vehicle_no: "", txn_date: today(), note: "", override_limit: false });
  const [drops, setDrops] = useState<Drop[]>([emptyDrop(), emptyDrop()]);
  const [photos, setPhotos] = useState<number[]>([]);
  useEffect(() => { if (stations.data && !f.station_id) setF((x: any) => ({ ...x, station_id: String(stations.data![0].id) })); }, [stations.data]);
  const { busy, run } = useAction();
  const active = (clients.data ?? []).filter((c) => c.active);
  const client = (id: string) => active.find((c) => c.id === Number(id));
  const rateOf = (dr: Drop) => Number(dr.rate) || client(dr.client_id)?.rates?.[f.product] || 0;
  const tanker = fleet.data?.tankers?.find((t: any) => t.id === Number(f.tanker_id));
  const station = stations.data?.find((s) => s.id === Number(f.station_id));
  const tank = station?.tanks.filter((t: any) => t.product === f.product).sort((a: any, b: any) => b.current_l - a.current_l)[0];
  const filled = drops.filter((x) => x.client_id && Number(x.litres) > 0);
  const total = filled.reduce((a, x) => a + Number(x.litres), 0);
  const amount = filled.reduce((a, x) => a + Number(x.litres) * rateOf(x), 0);
  const over = tanker?.capacity_l && total > tanker.capacity_l;
  const setDrop = (i: number, p: Partial<Drop>) => setDrops(drops.map((x, j) => (j === i ? { ...x, ...p } : x)));
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const body = {
      station_id: Number(f.station_id), product: f.product, txn_date: f.txn_date, note: f.note || null, ...fleetBody(f), photo_ids: photos,
      drops: filled.map((x) => ({ client_id: Number(x.client_id), litres: Number(x.litres), location: x.location || null, ref: x.ref || null,
        ...(admin && x.rate ? { rate: Number(x.rate) } : {}), ...(f.override_limit ? { override_limit: true } : {}), ...(x.order_id ? { order_id: x.order_id } : {}) })),
    };
    const r = await run(() => api("/wholesale/trips", { body }), (t: any) => `Trip #${t.id} saved — ${num(t.delivered_l)} L to ${t.drops.length} drops, ${pkr(t.billed)}`);
    if (r) onDone(r);
  };
  return (
    <Modal open onClose={onClose} title="Tanker trip — one tanker, several drops" wide>
      <form onSubmit={submit} className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Loaded from station"><select className="input" value={f.station_id} onChange={(e) => setF({ ...f, station_id: e.target.value })}>{(stations.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
          <Field label="Product"><select className="input" value={f.product} onChange={(e) => setF({ ...f, product: e.target.value })}>{Object.entries(PRODUCTS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
          <Field label="Date"><input className="input" type="date" value={f.txn_date} onChange={(e) => setF({ ...f, txn_date: e.target.value })} /></Field>
          <FleetPicker f={f} setF={setF} />
          <Field label="Note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
        </div>

        {(() => {
          // booked orders for this fuel that are not on the trip yet: one tap adds the drop
          const avail = (orders.data?.open ?? []).filter((o: any) => o.product === f.product && !drops.some((x) => x.order_id === o.id));
          if (!avail.length) return null;
          return (
            <div className="rounded-lg bg-amber-50 p-3 ring-1 ring-amber-200">
              <div className="mb-2 text-sm font-semibold text-amber-900">📋 Booked {PRODUCTS[f.product]} orders — tap to add as a drop</div>
              <div className="flex flex-wrap gap-2">{avail.map((o: any) => (
                <button type="button" key={o.id} className={`rounded-lg bg-white px-3 py-1.5 text-left text-sm ring-1 ${o.late ? "ring-red-300" : o.today ? "ring-amber-400" : "ring-slate-200"} hover:bg-amber-100`}
                  onClick={() => {
                    const d = { ...emptyDrop(), client_id: String(o.client_id), litres: String(o.litres), location: o.location ?? "", order_id: o.id };
                    const free = drops.findIndex((x) => !x.client_id && !x.litres);
                    setDrops(free >= 0 ? drops.map((x, j) => (j === free ? d : x)) : [...drops, d]);
                  }}>
                  <b>{o.client_name}</b> · {num(o.litres)} L<span className="block text-xs text-slate-500">{o.late ? "late · " : o.today ? "today · " : ""}{o.needed_on}{o.location ? ` · ${o.location}` : ""}</span>
                </button>))}</div>
            </div>
          );
        })()}
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table className="w-full min-w-[860px]">
            <thead><tr><th className="th w-8">#</th><th className="th">Client</th><th className="th">Drop location</th><th className="th w-28 text-right">Litres</th><th className="th w-32 text-right">Rate (Rs/L)</th><th className="th text-right">Amount</th><th className="th">Slip / ref</th><th className="th w-8" /></tr></thead>
            <tbody>{drops.map((x, i) => {
              const c = client(x.client_id);
              const card = c?.rates?.[f.product];
              return (
                <tr key={i}>
                  <td className="td text-sm text-slate-500">{i + 1}{x.order_id ? <span title="Booked order" className="block text-xs">📋</span> : null}</td>
                  <td className="td"><select className="input min-w-[200px]" value={x.client_id} onChange={(e) => setDrop(i, { client_id: e.target.value, rate: "", order_id: undefined, location: x.location || client(e.target.value)?.city || "" })}>
                    <option value="">— client —</option>{active.map((c) => <option key={c.id} value={c.id}>{c.name}{c.city ? ` · ${c.city}` : ""}</option>)}</select></td>
                  <td className="td"><input className="input min-w-[130px]" placeholder="place / pump" value={x.location} onChange={(e) => setDrop(i, { location: e.target.value })} /></td>
                  <td className="td"><input className="input text-right tabular-nums" type="number" min={1} step="0.01" value={x.litres} onChange={(e) => setDrop(i, { litres: e.target.value })} /></td>
                  <td className="td"><input className="input text-right tabular-nums" type="number" step="0.01" disabled={!admin} placeholder={card ? String(card) : c ? "no rate" : ""} value={x.rate} onChange={(e) => setDrop(i, { rate: e.target.value })} />
                    {c && !card && <div className="text-[11px] text-red-600">No {PRODUCTS[f.product]} rate</div>}</td>
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
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
          {tanker?.capacity_l && <span className={over ? "font-semibold text-red-600" : ""}>Tanker {tanker.number}: {num(tanker.capacity_l)} L{over ? ` — ${num(total - tanker.capacity_l)} L too much` : ` · ${num(tanker.capacity_l - total)} L space left`}</span>}
          {tank && <span className={tank.current_l < total ? "font-semibold text-red-600" : ""}>{tank.name}: {num(tank.current_l)} L in stock</span>}
          <span>Each client is billed at their own rate card{admin ? " (type a rate to change it for this drop)" : ""}.</span>
        </div>
        <ProofPhotos value={photos} onChange={setPhotos} hint="loaded tanker, gate pass, signed trip sheet" />
        {admin && <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={f.override_limit} onChange={(e) => setF({ ...f, override_limit: e.target.checked })} /> Allow even if a client crosses the credit limit (admin)</label>}
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy || !filled.length || Boolean(over)}><Truck size={15} /> Save trip ({filled.length} drops)</button></div>
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
          <div className="grid gap-2 sm:grid-cols-2">
            <div><b>{dt(data.trip_date)}</b> · {PRODUCTS[data.product]} from {data.station_name}{data.tank_name ? ` (${data.tank_name})` : ""}</div>
            <div>🚛 <b>{data.vehicle_no ?? "—"}</b> · 👤 {data.driver_name ?? "—"}{data.driver_phone ? ` · ${phone(data.driver_phone)}` : ""}{data.driver_cnic ? ` · CNIC ${data.driver_cnic}` : ""}{data.driver_licence ? ` · licence ${data.driver_licence}` : ""}</div>
          </div>
          <table className="w-full"><thead><tr><th className="th">#</th><th className="th">Client</th><th className="th">Location</th><th className="th text-right">Litres</th><th className="th text-right">Rate</th><th className="th text-right">Amount</th><th className="th">Ref</th><th className="th">Signature</th></tr></thead>
            <tbody>{data.drops.map((x: any, i: number) => (
              <tr key={x.id} className={x.voided ? "text-slate-400 line-through" : ""}><td className="td">{i + 1}</td><td className="td">{x.client_name}{x.phone ? <div className="text-xs text-slate-500">{phone(x.phone)}</div> : null}</td>
                <td className="td">{x.location ?? "—"}</td><td className="td text-right tabular-nums">{num(x.litres, 2)}</td><td className="td text-right tabular-nums">{x.rate}</td>
                <td className="td text-right tabular-nums">{pkr(x.amount)}</td><td className="td text-xs">{x.ref} <ProofThumbs ids={x.proof_ids} /></td><td className="td w-28 border-b border-dashed border-slate-300" /></tr>
            ))}</tbody>
            <tfoot><tr className="font-semibold"><td className="td" colSpan={3}>Total ({data.drops.filter((x: any) => !x.voided).length} drops)</td><td className="td text-right tabular-nums">{num(data.delivered_l, 2)} L</td><td className="td" /><td className="td text-right tabular-nums">{pkr(data.billed)}</td><td className="td" colSpan={2} /></tr></tfoot>
          </table>
          {data.note && <p className="text-slate-600">Note: {data.note}</p>}
          {data.proof_ids && <div className="flex items-center gap-2 text-slate-600">Photos: <ProofThumbs ids={data.proof_ids} /></div>}
          <p className="text-xs text-slate-500">Entered by {data.created_by}. To cancel one drop, void it in that client's statement — stock goes back to the tank.</p>
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
      <div className="flex items-center justify-between p-4 pb-2"><h2 className="font-semibold">Tanker trips</h2>{onNew && <button className="btn-primary" onClick={onNew}><Truck size={15} /> New tanker trip</button>}</div>
      <div className="overflow-x-auto"><table className="w-full">
        <thead><tr><th className="th">Trip</th><th className="th">Date</th><th className="th">Tanker</th><th className="th">Driver</th><th className="th">Product</th><th className="th text-right">Litres</th><th className="th text-right">Drops</th><th className="th text-right">Billed</th></tr></thead>
        <tbody>{data.map((t) => (
          <tr key={t.id} className="cursor-pointer hover:bg-slate-50" onClick={() => setOpen(t.id)}>
            <td className="td font-medium">#{t.id}</td><td className="td text-xs">{dt(t.trip_date)}</td><td className="td">{t.vehicle_no ?? "—"}</td><td className="td">{t.driver_name ?? "—"}</td>
            <td className="td">{PRODUCTS[t.product]}</td><td className="td text-right tabular-nums">{num(t.litres)} L</td><td className="td text-right">{t.drops}</td><td className="td text-right tabular-nums">{pkr(t.amount)}</td>
          </tr>
        ))}</tbody>
      </table>{!data.length && <Empty>No tanker trips yet. One trip can drop fuel at several clients — each at their own rate.</Empty>}</div>
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
    <div className="grid gap-5 xl:grid-cols-2">
      <div className="card">
        <div className="flex items-center justify-between p-4 pb-2"><h2 className="flex items-center gap-2 font-semibold"><Truck size={17} /> Tankers ({data.tankers.length})</h2>{edit && <button className="btn-secondary" onClick={() => setTk({})}><Plus size={15} /> Add tanker</button>}</div>
        <table className="w-full"><thead><tr><th className="th">Number</th><th className="th text-right">Capacity</th><th className="th">Usual driver</th><th className="th">Last trip</th><th className="th" /></tr></thead>
          <tbody>{data.tankers.map((t: any) => (
            <tr key={t.id} className={t.active ? "" : "opacity-50"}><td className="td font-medium">{t.number} {t.ownership === "hired" && <Badge tone="amber">hired</Badge>}{t.owner_name && <div className="text-xs text-slate-500">{t.owner_name}{t.owner_phone ? ` · ${t.owner_phone}` : ""}</div>}</td>
              <td className="td text-right tabular-nums">{t.capacity_l ? `${num(t.capacity_l)} L` : "—"}{t.chambers ? <div className="text-xs text-slate-500">{t.chambers} chambers</div> : null}</td>
              <td className="td text-sm">{t.driver_name ?? "—"}</td><td className="td text-xs text-slate-500">{t.last_trip ? d(t.last_trip) : "—"}</td>
              <td className="td">{edit && <button className="text-slate-400 hover:text-slate-700" onClick={() => setTk(t)} aria-label={`Edit ${t.number}`}><Pencil size={14} /></button>}</td></tr>
          ))}</tbody></table>
        {!data.tankers.length && <Empty>No tankers on file yet</Empty>}
      </div>
      <div className="card">
        <div className="flex items-center justify-between p-4 pb-2"><h2 className="flex items-center gap-2 font-semibold"><UserRound size={17} /> Drivers ({data.drivers.length})</h2>{edit && <button className="btn-secondary" onClick={() => setDr({})}><Plus size={15} /> Add driver</button>}</div>
        <table className="w-full"><thead><tr><th className="th">Driver</th><th className="th">CNIC</th><th className="th">Licence</th><th className="th">Last trip</th><th className="th" /></tr></thead>
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
    <Modal open onClose={onClose} title={initial.id ? `Tanker ${initial.number}` : "Add tanker"}>
      <form className="grid gap-3 sm:grid-cols-2" onSubmit={async (e) => {
        e.preventDefault();
        if (await run(() => initial.id ? api(`/wholesale/tankers/${initial.id}`, { method: "PATCH", body }) : api("/wholesale/tankers", { body }), "Tanker saved")) onSaved();
      }}>
        <Field label="Number plate *"><input className="input uppercase" required value={f.number} onChange={(e) => setF({ ...f, number: e.target.value })} placeholder="TLA-1234" /></Field>
        <Field label="Capacity (litres)"><input className="input" type="number" min={1} value={f.capacity_l} onChange={(e) => setF({ ...f, capacity_l: e.target.value })} /></Field>
        <Field label="Chambers"><input className="input" type="number" min={1} max={10} value={f.chambers} onChange={(e) => setF({ ...f, chambers: e.target.value })} /></Field>
        <Field label="Ownership"><select className="input" value={f.ownership} onChange={(e) => setF({ ...f, ownership: e.target.value })}><option value="own">Our own</option><option value="hired">Hired</option></select></Field>
        {f.ownership === "hired" && <><Field label="Owner name"><input className="input" value={f.owner_name} onChange={(e) => setF({ ...f, owner_name: e.target.value })} /></Field>
          <Field label="Owner phone"><input className="input" value={f.owner_phone} onChange={(e) => setF({ ...f, owner_phone: e.target.value })} /></Field></>}
        <Field label="Usual driver"><select className="input" value={f.driver_id} onChange={(e) => setF({ ...f, driver_id: e.target.value })}><option value="">—</option>{drivers.filter((x) => x.active).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
        <Field label="Notes"><input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
        {initial.id && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> In use</label>}
        <div className="flex justify-end gap-2 sm:col-span-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
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
    <Modal open onClose={onClose} title={initial.id ? `Driver: ${initial.name}` : "Add driver"}>
      <form className="grid gap-3 sm:grid-cols-2" onSubmit={async (e) => {
        e.preventDefault();
        if (await run(() => initial.id ? api(`/wholesale/drivers/${initial.id}`, { method: "PATCH", body }) : api("/wholesale/drivers", { body }), "Driver saved")) onSaved();
      }}>
        <Field label="Name *"><input className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Mobile"><input className="input" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} placeholder="03xx xxxxxxx" /></Field>
        <Field label="CNIC"><input className="input" value={f.cnic} onChange={(e) => setF({ ...f, cnic: e.target.value })} placeholder="35202-1234567-1" /></Field>
        <Field label="Licence no."><input className="input" value={f.licence_no} onChange={(e) => setF({ ...f, licence_no: e.target.value })} /></Field>
        <Field label="Licence valid till"><input className="input" type="date" value={f.licence_expiry} onChange={(e) => setF({ ...f, licence_expiry: e.target.value })} /></Field>
        <Field label="Address"><input className="input" value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} /></Field>
        <div className="sm:col-span-2"><Field label="Notes"><input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field></div>
        {initial.id && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Still working</label>}
        <div className="flex justify-end gap-2 sm:col-span-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

