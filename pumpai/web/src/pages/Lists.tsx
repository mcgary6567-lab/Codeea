import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Plus, Trash2, Fuel } from "lucide-react";
import { api } from "../lib/api";
import { Field, Loading, PageHeader, useAction } from "../components/ui";
import { invalidateLookups, type LookupEntry, type LookupField } from "../lib/lookups";
import { loadProducts } from "../lib/format";

/**
 * Settings → Lists: the owner adds, renames, reorders, switches off or deletes the entries every
 * dropdown uses — customer types, machine types, shop categories, utility bills, booking services,
 * training topics, job titles, complaint categories.
 */
export default function Lists() {
  const [data, setData] = useState<{ kinds: Record<string, { label: string; hint: string; fields: LookupField[] }>; lists: Record<string, LookupEntry[]> } | null>(null);
  const [kind, setKind] = useState("customer_type");
  const load = () => api("/lookups?all=1").then(setData);
  useEffect(() => { load(); }, []);
  const changed = async () => { await load(); await invalidateLookups(); };
  if (!data) return <Loading />;
  const def = data.kinds[kind];
  const list = data.lists[kind] ?? [];
  return (
    <div className="space-y-4">
      <PageHeader title="Lists · فہرستیں" subtitle="Everything the dropdowns offer. Add what your pump needs; switch off what it doesn't. Changes show everywhere at once." />
      <div className="flex gap-1 overflow-x-auto rounded-xl bg-white p-1.5 shadow-sm ring-1 ring-slate-200" role="tablist">
        {Object.entries(data.kinds).map(([k, v]) => (
          <button key={k} role="tab" aria-selected={k === kind} onClick={() => setKind(k)} className={`whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium ${k === kind ? "bg-brand-600 text-white" : "text-slate-600 hover:bg-slate-100"}`}>{v.label}</button>
        ))}
      </div>
      <div className="card p-4">
        <h2 className="font-semibold">{def.label}</h2>
        <p className="mb-3 text-sm text-slate-600">{def.hint}</p>
        <div className="space-y-2">
          {list.map((e, i) => <Row key={e.id} e={e} fields={def.fields} first={i === 0} last={i === list.length - 1} onChanged={changed} />)}
        </div>
        <AddRow kind={kind} fields={def.fields} onAdded={changed} />
      </div>
      <FuelProducts />
    </div>
  );
}

/** Fuel products: add / rename / recolour / hide / delete. The code is what records store and never changes. */
function FuelProducts() {
  const [list, setList] = useState<any[] | null>(null);
  const [add, setAdd] = useState({ code: "", name: "", short: "", colour: "#2a78d6", ur: "" });
  const { busy, run } = useAction();
  const load = () => api("/products?all=1").then((d) => setList(d.products));
  useEffect(() => { load(); }, []);
  const changed = async () => { await load(); await loadProducts(); };
  return (
    <div className="card p-4">
      <h2 className="flex items-center gap-2 font-semibold"><Fuel size={17} className="text-brand-600" /> Fuel products</h2>
      <p className="mb-3 text-sm text-slate-600">The fuels your pump sells. Hide one you don't sell; its past records and reports stay. The code (e.g. PMG) is what every sale stores and can't change — add a new product to rename the code.</p>
      {!list ? <Loading /> : (
        <div className="space-y-2">
          {list.map((p) => <FuelRow key={p.id} p={p} onChanged={changed} />)}
          <form className="mt-3 flex flex-wrap items-end gap-2 rounded-lg bg-slate-50 p-2" onSubmit={async (e) => {
            e.preventDefault();
            if (await run(() => api("/products", { body: { code: add.code.trim(), name: add.name.trim(), short: add.short || undefined, colour: add.colour, ur: add.ur || undefined } }), "Fuel added")) { setAdd({ code: "", name: "", short: "", colour: "#2a78d6", ur: "" }); await changed(); }
          }}>
            <Field label="Code"><input className="input w-24 font-mono uppercase" required placeholder="PMG97" value={add.code} onChange={(e) => setAdd({ ...add, code: e.target.value.toUpperCase() })} /></Field>
            <Field label="Name"><input className="input w-44" required placeholder="XTRON 97 Premium" value={add.name} onChange={(e) => setAdd({ ...add, name: e.target.value })} /></Field>
            <Field label="Short"><input className="input w-28" placeholder="XTRON" value={add.short} onChange={(e) => setAdd({ ...add, short: e.target.value })} /></Field>
            <Field label="Urdu"><input className="input w-28 font-urdu" dir="rtl" value={add.ur} onChange={(e) => setAdd({ ...add, ur: e.target.value })} /></Field>
            <Field label="Colour"><input className="h-10 w-12 cursor-pointer rounded" type="color" value={add.colour} onChange={(e) => setAdd({ ...add, colour: e.target.value })} /></Field>
            <button className="btn-primary" disabled={busy || !add.code.trim() || !add.name.trim()}><Plus size={15} /> Add</button>
          </form>
        </div>
      )}
    </div>
  );
}
function FuelRow({ p, onChanged }: { p: any; onChanged: () => Promise<void> }) {
  const [f, setF] = useState({ name: p.name, short: p.short ?? "", colour: p.colour ?? "#334155", ur: p.ur ?? "" });
  const { busy, run } = useAction();
  useEffect(() => { setF({ name: p.name, short: p.short ?? "", colour: p.colour ?? "#334155", ur: p.ur ?? "" }); }, [p]);
  const dirty = f.name !== p.name || f.short !== (p.short ?? "") || f.colour !== (p.colour ?? "#334155") || f.ur !== (p.ur ?? "");
  const small = "btn-secondary min-h-9 !px-2 !py-1 text-xs sm:min-h-0";
  return (
    <div className={`flex flex-wrap items-end gap-2 rounded-lg border border-slate-200 p-2 ${p.active ? "" : "bg-slate-50 opacity-60"}`}>
      <span className="self-center font-mono text-xs font-bold text-slate-500" title="Stored code (cannot change)">{p.code}</span>
      <Field label="Name"><input className="input w-40" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      <Field label="Short"><input className="input w-24" value={f.short} onChange={(e) => setF({ ...f, short: e.target.value })} /></Field>
      <Field label="Urdu"><input className="input w-24 font-urdu" dir="rtl" value={f.ur} onChange={(e) => setF({ ...f, ur: e.target.value })} /></Field>
      <Field label="Colour"><input className="h-10 w-12 cursor-pointer rounded" type="color" value={f.colour} onChange={(e) => setF({ ...f, colour: e.target.value })} /></Field>
      <span className="ml-auto flex flex-wrap gap-1 self-center">
        {dirty && <button className="btn-primary min-h-9 !px-3 !py-1 text-xs sm:min-h-0" disabled={busy} onClick={() => run(() => api(`/products/${p.id}`, { method: "PATCH", body: { name: f.name, short: f.short || undefined, colour: f.colour, ur: f.ur || undefined } }), "Saved").then(onChanged)}>Save</button>}
        <button className={small} disabled={busy} onClick={() => run(() => api(`/products/${p.id}`, { method: "PATCH", body: { active: !p.active } }), p.active ? "Hidden" : "Shown").then(onChanged)}>{p.active ? "Hide" : "Show"}</button>
        <button className={`${small} !text-rose-700`} disabled={busy} onClick={() => confirm(`Delete "${p.name}"? Only possible if nothing uses it.`) && run(() => api(`/products/${p.id}`, { method: "DELETE" }), "Deleted").then(onChanged)}><Trash2 size={14} /></button>
      </span>
    </div>
  );
}

function ExtraInputs({ fields, value, onChange }: { fields: LookupField[]; value: Record<string, any>; onChange: (v: Record<string, any>) => void }) {
  return (
    <>
      {fields.map((f) => f.type === "boolean"
        ? <label key={f.key} className="flex items-center gap-1.5 whitespace-nowrap text-sm"><input type="checkbox" checked={Boolean(value[f.key])} onChange={(ev) => onChange({ ...value, [f.key]: ev.target.checked })} /> {f.label}</label>
        : <Field key={f.key} label={f.label}><input className={`input ${f.type === "number" ? "w-24" : f.key === "icon" ? "w-16 text-center" : "w-40"}`} type={f.type === "number" ? "number" : "text"} value={value[f.key] ?? ""} onChange={(ev) => onChange({ ...value, [f.key]: f.type === "number" ? (ev.target.value === "" ? "" : Number(ev.target.value)) : ev.target.value })} /></Field>)}
    </>
  );
}

function Row({ e, fields, first, last, onChanged }: { e: LookupEntry; fields: LookupField[]; first: boolean; last: boolean; onChanged: () => Promise<void> }) {
  const [label, setLabel] = useState(e.label);
  const [extra, setExtra] = useState<Record<string, any>>(e.extra ?? {});
  const { busy, run } = useAction();
  useEffect(() => { setLabel(e.label); setExtra(e.extra ?? {}); }, [e]);
  const dirty = label !== e.label || JSON.stringify(extra) !== JSON.stringify(e.extra ?? {});
  const small = "btn-secondary min-h-9 !px-2 !py-1 text-xs sm:min-h-0";
  return (
    <div className={`flex flex-wrap items-end gap-2 rounded-lg border border-slate-200 p-2 ${e.active ? "" : "bg-slate-50 opacity-60"}`}>
      <span className="flex flex-col gap-0.5 self-center">
        <button className="text-slate-400 hover:text-slate-700 disabled:opacity-30" disabled={first || busy} aria-label="Move up" onClick={() => run(() => api(`/lookups/${e.id}/move`, { body: { dir: "up" } }), "Moved").then(onChanged)}><ArrowUp size={14} /></button>
        <button className="text-slate-400 hover:text-slate-700 disabled:opacity-30" disabled={last || busy} aria-label="Move down" onClick={() => run(() => api(`/lookups/${e.id}/move`, { body: { dir: "down" } }), "Moved").then(onChanged)}><ArrowDown size={14} /></button>
      </span>
      <Field label="Name"><input className="input w-48 sm:w-56" value={label} onChange={(ev) => setLabel(ev.target.value)} /></Field>
      <ExtraInputs fields={fields} value={extra} onChange={setExtra} />
      <span className="self-center font-mono text-[11px] text-slate-400" title="Stored value (cannot change)">{e.key}</span>
      <span className="ml-auto flex flex-wrap gap-1">
        {dirty && <button className="btn-primary min-h-9 !px-3 !py-1 text-xs sm:min-h-0" disabled={busy} onClick={() => run(() => api(`/lookups/${e.id}`, { method: "PATCH", body: { label, extra } }), "Saved").then(onChanged)}>Save</button>}
        <button className={small} disabled={busy} onClick={() => run(() => api(`/lookups/${e.id}`, { method: "PATCH", body: { active: !e.active } }), e.active ? "Switched off" : "Switched on").then(onChanged)}>{e.active ? "Switch off" : "Switch on"}</button>
        <button className={`${small} !text-rose-700`} disabled={busy} aria-label="Delete" onClick={() => confirm(`Delete "${e.label}"? Only possible if nothing uses it.`) && run(() => api(`/lookups/${e.id}`, { method: "DELETE" }), "Deleted").then(onChanged)}><Trash2 size={14} /></button>
      </span>
    </div>
  );
}

function AddRow({ kind, fields, onAdded }: { kind: string; fields: LookupField[]; onAdded: () => Promise<void> }) {
  const [label, setLabel] = useState("");
  const [extra, setExtra] = useState<Record<string, any>>({});
  const { busy, run } = useAction();
  return (
    <form className="mt-3 flex flex-wrap items-end gap-2 rounded-lg bg-slate-50 p-2" onSubmit={async (ev) => {
      ev.preventDefault();
      if (await run(() => api("/lookups", { body: { kind, label: label.trim(), extra } }), "Added")) { setLabel(""); setExtra({}); await onAdded(); }
    }}>
      <Field label="New entry"><input className="input w-48 sm:w-56" required minLength={1} placeholder="e.g. NGO / Transporter" value={label} onChange={(ev) => setLabel(ev.target.value)} /></Field>
      <ExtraInputs fields={fields} value={extra} onChange={setExtra} />
      <button className="btn-primary" disabled={busy || !label.trim()}><Plus size={15} /> Add</button>
    </form>
  );
}
