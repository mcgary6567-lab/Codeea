import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import { Field, Loading, PageHeader, useAction } from "../components/ui";
import { invalidateLookups, type LookupEntry, type LookupField } from "../lib/lookups";

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
