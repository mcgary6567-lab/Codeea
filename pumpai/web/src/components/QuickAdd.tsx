import { useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, Trash2 } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Field, Modal, useAction } from "./ui";
import { useAuth } from "../App";
import { ClientForm } from "../pages/Wholesale";
import { UserForm } from "../pages/Users";
import { PRODUCTS } from "../lib/format";

/** Everything an admin/manager can add from one place. Items are filtered by the user's permissions. */
export const ADD_ITEMS = [
  { key: "khata", icon: "📒", label: "Khata account", hint: "Police, school, govt office, fleet… with vehicles & credit limit", perm: "credit.set_limit" },
  { key: "customer", icon: "👤", label: "Customer", hint: "Retail / loyalty customer (no credit)", perm: "customers.create" },
  { key: "supplier", icon: "🏭", label: "Supplier", hint: "Fuel depot we buy stock from", perm: "suppliers.manage" },
  { key: "wholesale", icon: "🚛", label: "Wholesale client", hint: "Dealer or bulk buyer with own rates", perm: "wholesale.manage" },
  { key: "station", icon: "⛽", label: "Station", hint: "A new petrol pump location", perm: "stations.manage" },
  { key: "tank", icon: "🛢️", label: "Tank & nozzles", hint: "Add a tank to a station", perm: "stock.manage" },
  { key: "user", icon: "👥", label: "Staff user", hint: "Salesman, manager or wholesale officer login", perm: "users.manage" },
  { key: "category", icon: "🧾", label: "Expense category", hint: "With optional monthly budget", perm: "expenses.approve" },
] as const;
type Key = (typeof ADD_ITEMS)[number]["key"];

export function useAddItems() {
  const { can, user } = useAuth();
  return user?.role === "salesman" ? [] : ADD_ITEMS.filter((i) => can(i.perm));
}

/** "+ Add new" button with a menu (sidebar). */
export function QuickAddButton() {
  const items = useAddItems();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<Key | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  if (!items.length) return null;
  return (
    <div className="relative px-3 pb-3" ref={ref}>
      <button onClick={() => setOpen(!open)} className="flex w-full items-center justify-center gap-2 rounded-lg bg-emerald-400 py-2 text-sm font-semibold text-emerald-950 hover:bg-emerald-300">
        <Plus size={16} /> Add new
      </button>
      {open && (
        <div className="absolute left-3 z-50 mt-2 w-72 overflow-hidden rounded-xl border border-slate-200 bg-white text-slate-900 shadow-xl">
          {items.map((i) => (
            <button key={i.key} onClick={() => { setForm(i.key); setOpen(false); }} className="flex w-full items-start gap-3 px-4 py-2.5 text-left hover:bg-slate-50">
              <span className="text-xl">{i.icon}</span>
              <span><span className="block text-sm font-medium">{i.label}</span><span className="text-xs text-slate-500">{i.hint}</span></span>
            </button>
          ))}
        </div>
      )}
      {form && <AddForm kind={form} onClose={() => setForm(null)} />}
    </div>
  );
}

/** Big tiles for the dashboard. */
export function QuickAddTiles() {
  const items = useAddItems();
  const [form, setForm] = useState<Key | null>(null);
  if (!items.length) return null;
  return (
    <div className="card p-4">
      <h2 className="mb-3 font-semibold">Add new</h2>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-8">
        {items.map((i) => (
          <button key={i.key} onClick={() => setForm(i.key)} className="flex flex-col items-center gap-1 rounded-xl border border-slate-200 p-3 text-center hover:border-brand-500 hover:bg-emerald-50">
            <span className="text-2xl">{i.icon}</span><span className="text-xs font-medium">{i.label}</span>
          </button>
        ))}
      </div>
      {form && <AddForm kind={form} onClose={() => setForm(null)} />}
    </div>
  );
}

export function AddForm({ kind, onClose, onSaved }: { kind: Key; onClose: () => void; onSaved?: (r: any) => void }) {
  const nav = useNavigate();
  const stations = useApi<any[]>(kind === "tank" || kind === "user" ? "/stations" : null);
  const done = (r: any, go?: string) => { onClose(); onSaved?.(r); if (go) nav(go); };
  switch (kind) {
    case "khata": return <AccountForm khata onClose={onClose} onSaved={(c) => done(c, `/customers/${c.id}`)} />;
    case "customer": return <AccountForm onClose={onClose} onSaved={(c) => done(c, `/customers/${c.id}`)} />;
    case "supplier": return <SupplierForm onClose={onClose} onSaved={(s) => done(s, "/suppliers")} />;
    case "wholesale": return <ClientForm onClose={onClose} onSaved={(c) => done(c, `/wholesale/${c.id}`)} />;
    case "station": return <StationForm onClose={onClose} onSaved={(s) => done(s, "/settings")} />;
    case "tank": return stations.data ? <TankForm stations={stations.data} onClose={onClose} onSaved={(t) => done(t, "/stock")} /> : null;
    case "user": return stations.data ? <UserForm initial={{ role: "salesman", station_id: stations.data[0]?.id }} stations={stations.data} onClose={onClose} onSaved={() => done(null, "/users")} /> : null;
    case "category": return <CategoryForm onClose={onClose} onSaved={(c) => done(c, "/expenses")} />;
  }
}

const TYPES: [string, string, string][] = [
  ["police", "🚓", "Police station"], ["school", "🏫", "School / college"], ["government", "🏛️", "Government office"], ["hospital", "🚑", "Hospital / health"],
  ["fleet", "🚚", "Fleet / transport"], ["farmer", "🚜", "Farmer"], ["business", "🏢", "Business"], ["retail", "🚗", "Retail customer"],
];

/** New customer or khata account, with vehicles in one go. */
export function AccountForm({ khata, onClose, onSaved }: { khata?: boolean; onClose: () => void; onSaved: (c: any) => void }) {
  const { can } = useAuth();
  const canCredit = can("credit.set_limit");
  const [f, setF] = useState({ name: "", phone: "", type: khata ? "police" : "retail", city: "", credit_limit: khata ? "100000" : "0", notes: "" });
  const [vehicles, setVehicles] = useState<{ plate_no: string; fuel: string; limit?: string }[]>(khata ? [{ plate_no: "", fuel: "PMG" }] : []);
  const { busy, run } = useAction();
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const body: any = { name: f.name, phone: f.phone, type: f.type, city: f.city || null, notes: f.notes || null,
      vehicles: vehicles.filter((v) => v.plate_no.trim()).map((v) => ({ plate_no: v.plate_no, fuel: v.fuel || null, daily_limit_l: Number(v.limit) > 0 ? Number(v.limit) : null })) };
    if (canCredit) body.credit_limit = Number(f.credit_limit) || 0;
    const r = await run(() => api("/customers", { body }), (c: any) => c.credit_limit > 0 ? `${c.name} khata opened — salesmen notified` : `${c.name} added`);
    if (r) onSaved(r);
  };
  return (
    <Modal open onClose={onClose} title={khata ? "New khata account" : "New customer"} wide>
      <form onSubmit={save} className="space-y-4">
        <div>
          <span className="label">Account type</span>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {TYPES.filter(([t]) => khata || ["retail", "fleet", "farmer", "business"].includes(t)).map(([t, icon, label]) => (
              <button type="button" key={t} onClick={() => setF({ ...f, type: t })} aria-pressed={f.type === t}
                className={`flex items-center gap-2 rounded-lg border-2 p-2 text-left text-sm ${f.type === t ? "border-brand-600 bg-emerald-50" : "border-slate-200"}`}>
                <span className="text-xl">{icon}</span>{label}
              </button>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Field label={["police", "school", "government", "hospital"].includes(f.type) ? "Name (e.g. Police Station Shadbagh)" : "Name"}><input className="input" required minLength={2} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Phone / WhatsApp (contact person)"><input className="input" required placeholder="03xx xxxxxxx" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
          <Field label="City / area"><input className="input" value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} /></Field>
        </div>
        {canCredit ? (
          <Field label="Khata credit limit (Rs) — 0 means no credit"><input className="input sm:w-64" type="number" min={0} value={f.credit_limit} onChange={(e) => setF({ ...f, credit_limit: e.target.value })} /></Field>
        ) : <p className="text-xs text-slate-500">Khata credit limits are set by the admin.</p>}
        <div>
          <div className="mb-1 flex items-center justify-between"><span className="label !mb-0">Vehicles allowed to take fuel</span>
            <button type="button" className="text-xs font-medium text-brand-600 hover:underline" onClick={() => setVehicles([...vehicles, { plate_no: "", fuel: "PMG" }])}>+ Add vehicle</button></div>
          <div className="space-y-2">
            {vehicles.map((v, i) => (
              <div key={i} className="flex gap-2">
                <input className="input uppercase" placeholder={f.type === "police" ? "LEJ-1234 (Mobile 1)" : "LEA-1234"} value={v.plate_no} onChange={(e) => setVehicles(vehicles.map((x, j) => j === i ? { ...x, plate_no: e.target.value } : x))} />
                <select className="input w-36" value={v.fuel} onChange={(e) => setVehicles(vehicles.map((x, j) => j === i ? { ...x, fuel: e.target.value } : x))}>{Object.entries(PRODUCTS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
                <input className="input w-24" type="number" min={1} placeholder="L/day" aria-label="Daily litre limit" value={v.limit ?? ""} onChange={(e) => setVehicles(vehicles.map((x, j) => j === i ? { ...x, limit: e.target.value } : x))} />
                <button type="button" className="btn-secondary !px-2" aria-label="Remove vehicle" onClick={() => setVehicles(vehicles.filter((_, j) => j !== i))}><Trash2 size={15} /></button>
              </div>
            ))}
            {!vehicles.length && <p className="text-xs text-slate-500">No vehicles — any vehicle can be written at the POS.</p>}
          </div>
        </div>
        <Field label="Notes"><input className="input" placeholder="e.g. Bill monthly to SHO office, payment by cheque" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
        {Number(f.credit_limit) > 0 && <p className="rounded-lg bg-emerald-50 p-2 text-xs text-emerald-800">Salesmen and managers will be notified and the account will appear at once in the POS khata list.</p>}
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

export function SupplierForm({ onClose, onSaved }: { onClose: () => void; onSaved: (s: any) => void }) {
  const [f, setF] = useState({ name: "", phone: "", opening_balance: "0", notes: "" });
  const { busy, run } = useAction();
  return (
    <SimpleModal title="New supplier" busy={busy} onClose={onClose} onSubmit={async () => {
      const r = await run(() => api("/suppliers", { body: { name: f.name, phone: f.phone || null, opening_balance: Number(f.opening_balance) || 0, notes: f.notes || null } }), "Supplier added — managers notified");
      if (r) onSaved(r);
    }}>
      <Field label="Name (e.g. PSO Machike Depot)"><input className="input" required minLength={2} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      <Field label="Phone"><input className="input" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
      <Field label="Opening balance we owe (Rs)"><input className="input" type="number" value={f.opening_balance} onChange={(e) => setF({ ...f, opening_balance: e.target.value })} /></Field>
      <Field label="Notes"><input className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
    </SimpleModal>
  );
}

/** Add a station, or (with `edit`) change an existing one's details. */
export function StationForm({ edit, onClose, onSaved }: { edit?: any; onClose: () => void; onSaved: (s: any) => void }) {
  const [f, setF] = useState({ name: edit?.name ?? "", city: edit?.city ?? "", address: edit?.address ?? "", omc: edit?.omc ?? "PSO", timings: edit?.timings ?? "24 hours", services: edit?.services ?? "",
    lat: edit?.lat != null ? String(edit.lat) : "", lng: edit?.lng != null ? String(edit.lng) : "" });
  const { busy, run } = useAction();
  const body = { ...f, services: f.services || null, lat: f.lat ? Number(f.lat) : null, lng: f.lng ? Number(f.lng) : null };
  return (
    <SimpleModal title={edit ? "Edit station" : "New station"} busy={busy} onClose={onClose} onSubmit={async () => {
      const r = edit ? await run(() => api(`/stations/${edit.id}`, { method: "PATCH", body }), "Station saved") : await run(() => api("/stations", { body }), "Station added");
      if (r) onSaved(r);
    }}>
      <Field label="Station name"><input className="input" required minLength={2} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="City"><input className="input" value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} /></Field>
        <Field label="Oil company"><select className="input" value={f.omc} onChange={(e) => setF({ ...f, omc: e.target.value })}>{["PSO", "Shell", "TotalEnergies Parco", "Attock", "GO", "Hascol", "Byco", "Other"].map((o) => <option key={o}>{o}</option>)}</select></Field>
      </div>
      <Field label="Address"><input className="input" value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} /></Field>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Timings"><input className="input" value={f.timings} onChange={(e) => setF({ ...f, timings: e.target.value })} /></Field>
        <Field label="Services"><input className="input" placeholder="Tuck shop, air, car wash" value={f.services} onChange={(e) => setF({ ...f, services: e.target.value })} /></Field>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Latitude (for attendance distance)"><input className="input" type="number" step="any" placeholder="31.5204" value={f.lat} onChange={(e) => setF({ ...f, lat: e.target.value })} /></Field>
        <Field label="Longitude"><input className="input" type="number" step="any" placeholder="74.3587" value={f.lng} onChange={(e) => setF({ ...f, lng: e.target.value })} /></Field>
      </div>
      {!edit && <p className="text-xs text-slate-500">Next: add its tanks, then assign salesmen to it on the Users page.</p>}
    </SimpleModal>
  );
}

/** Add a tank with its meters, or (with `edit`) change a tank's name, capacity and reorder level. */
export function TankForm({ stations, stationId, edit, onClose, onSaved }: { stations: any[]; stationId?: number; edit?: any; onClose: () => void; onSaved: (t: any) => void }) {
  const [f, setF] = useState({ station_id: String(stationId ?? stations[0]?.id ?? ""), product: edit?.product ?? "PMG", name: edit?.name ?? "", capacity_l: edit ? String(edit.capacity_l) : "20000", current_l: "0", reorder_pct: edit ? String(edit.reorder_pct) : "25", nozzles: "2" });
  const { busy, run } = useAction();
  return (
    <SimpleModal title={edit ? "Edit tank" : "New tank & nozzles"} busy={busy} onClose={onClose} onSubmit={async () => {
      const r = edit
        ? await run(() => api(`/tanks/${edit.id}`, { method: "PATCH", body: { name: f.name, capacity_l: Number(f.capacity_l), reorder_pct: Number(f.reorder_pct) } }), "Tank saved")
        : await run(() => api("/tanks", { body: { station_id: Number(f.station_id), product: f.product, name: f.name || `Tank ${PRODUCTS[f.product]}`, capacity_l: Number(f.capacity_l), current_l: Number(f.current_l), reorder_pct: Number(f.reorder_pct), nozzles: Number(f.nozzles) } }), "Tank added");
      if (r) onSaved(r);
    }}>
      {!edit && <Field label="Station"><select className="input" value={f.station_id} onChange={(e) => setF({ ...f, station_id: e.target.value })}>{stations.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {!edit && <Field label="Fuel"><select className="input" value={f.product} onChange={(e) => setF({ ...f, product: e.target.value })}>{Object.entries(PRODUCTS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>}
        <Field label="Tank name"><input className="input" placeholder="Tank-4 Diesel" required={Boolean(edit)} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Capacity (litres)"><input className="input" type="number" min={1} required value={f.capacity_l} onChange={(e) => setF({ ...f, capacity_l: e.target.value })} /></Field>
        {!edit && <Field label="Current stock (litres, from dip)"><input className="input" type="number" min={0} required value={f.current_l} onChange={(e) => setF({ ...f, current_l: e.target.value })} /></Field>}
        <Field label="Reorder at (% full)"><input className="input" type="number" min={5} max={80} value={f.reorder_pct} onChange={(e) => setF({ ...f, reorder_pct: e.target.value })} /></Field>
        {!edit && <Field label="Number of nozzles"><input className="input" type="number" min={0} max={12} value={f.nozzles} onChange={(e) => setF({ ...f, nozzles: e.target.value })} /></Field>}
      </div>
      {edit && <p className="text-xs text-slate-500">Stock in the tank changes only through dips and deliveries. Fuel type cannot change once the tank has history.</p>}
    </SimpleModal>
  );
}

function CategoryForm({ onClose, onSaved }: { onClose: () => void; onSaved: (c: any) => void }) {
  const [f, setF] = useState({ name: "", monthly_budget: "" });
  const { busy, run } = useAction();
  return (
    <SimpleModal title="New expense category" busy={busy} onClose={onClose} onSubmit={async () => {
      const r = await run(() => api("/expense-categories", { body: { name: f.name, monthly_budget: f.monthly_budget ? Number(f.monthly_budget) : null } }), "Category added");
      if (r) onSaved(r);
    }}>
      <Field label="Category name"><input className="input" required minLength={2} placeholder="e.g. Fuel testing" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      <Field label="Monthly budget (Rs, optional)"><input className="input" type="number" min={0} value={f.monthly_budget} onChange={(e) => setF({ ...f, monthly_budget: e.target.value })} /></Field>
    </SimpleModal>
  );
}

function SimpleModal({ title, busy, onClose, onSubmit, children }: { title: string; busy: boolean; onClose: () => void; onSubmit: () => void; children: ReactNode }) {
  return (
    <Modal open onClose={onClose} title={title}>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); onSubmit(); }}>
        {children}
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}
