import { useState } from "react";
import { Wrench, Plus, AlertTriangle, CheckCircle2, Clock, ShieldCheck } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Empty, Field, Loading, Modal, PageHeader, Stat, useAction } from "../components/ui";
import { pkr } from "../lib/format";
import { useAuth } from "../App";
import { labelMap } from "../lib/lookups";
import { PhotoButton, PhotoThumb } from "../components/Capture";

// machine-type labels come from Settings → Lists (icon + name), with these as the fallback
export const TYPE_LABEL = labelMap("machine_type", {
  dispenser: "⛽ Dispenser", generator: "⚡ Generator", compressor: "🛞 Air compressor", submersible_pump: "🛢️ Submersible pump", fan: "🌀 Fan", light: "💡 Lights",
  ups: "🔋 UPS", inverter: "🔋 Inverter", air_conditioner: "❄️ AC", cctv: "📹 CCTV", water_pump: "💧 Water pump", car_wash: "🚿 Car wash", other: "🔧 Other",
});
const STATUS: Record<string, { label: string; tone: string }> = { working: { label: "Working", tone: "green" }, faulty: { label: "Not working", tone: "red" }, under_repair: { label: "Under repair", tone: "amber" }, retired: { label: "Retired", tone: "slate" } };
const LOG: Record<string, { label: string; tone: string }> = { service: { label: "Service", tone: "blue" }, fault: { label: "Fault", tone: "red" }, repair: { label: "Repair", tone: "green" }, reading: { label: "Hours", tone: "slate" }, note: { label: "Note", tone: "slate" } };

function DueBadge({ m }: { m: any }) {
  const d = m.due;
  if (d.service === "overdue") return <Badge tone="red">Service {d.hours_left != null && d.hours_left < 0 ? `${-d.hours_left} h` : `${-d.days_to_service} days`} late</Badge>;
  if (d.service === "due_soon") return <Badge tone="amber">Service {d.hours_left != null && d.hours_left <= 25 ? `in ${d.hours_left} h` : d.days_to_service === 0 ? "today" : `in ${d.days_to_service} days`}</Badge>;
  if (d.service === "ok") return <span className="text-xs text-slate-500">Next service {m.next_service_on ?? "—"}{d.hours_left != null ? ` / ${d.hours_left} h` : ""}</span>;
  return null;
}

/** Dispensers, generator, compressor, fans, lights… service schedule, faults, repairs and warranty. */
export default function Machines() {
  const { data, reload } = useApi<any>("/machines");
  const stations = useApi<any[]>("/stations");
  const { can } = useAuth();
  const manager = can("alerts.view");
  const [open, setOpen] = useState<number | null>(null);
  const [edit, setEdit] = useState<any>(null);
  const [filter, setFilter] = useState("");
  if (!data) return <Loading />;
  const s = data.summary;
  const list = data.machines.filter((m: any) => !filter || (filter === "attention" ? m.status === "faulty" || m.status === "under_repair" || ["overdue", "due_soon"].includes(m.due.service) || m.due.warranty === "ending" : m.type === filter));
  const groups = [...new Set<string>(list.map((m: any) => m.station_name ?? "All stations"))];
  return (
    <div className="space-y-5">
      <PageHeader title="Machines" subtitle="Dispensers, generator, compressor, fans, lights — when they were serviced, when next, faults and warranty"
        actions={manager && <button className="btn-primary" onClick={() => setEdit({})}><Plus size={15} /> Add machine</button>} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Machines" value={s.total} icon={<Wrench size={16} />} />
        <Stat label="Not working" value={s.faulty} tone={s.faulty ? "red" : "slate"} icon={<AlertTriangle size={16} />} />
        <Stat label="Service late" value={s.overdue} tone={s.overdue ? "red" : "slate"} icon={<Clock size={16} />} />
        <Stat label="Service this week" value={s.due_soon} tone={s.due_soon ? "amber" : "slate"} />
        <Stat label="Spent (90 days)" value={pkr(s.spent_90d)} hint={`${s.warranty_ending} warranties ending`} icon={<ShieldCheck size={16} />} />
      </div>
      <div className="-mx-4 flex gap-1.5 overflow-x-auto px-4 pb-1 text-sm sm:mx-0 sm:flex-wrap sm:px-0">
        {[["", "All"], ["attention", "Needs attention"], ...data.types.filter((t: string) => data.machines.some((m: any) => m.type === t)).map((t: string) => [t, TYPE_LABEL[t]])].map(([k, l]) =>
          <button key={k} onClick={() => setFilter(k)} className={`min-h-9 shrink-0 whitespace-nowrap rounded-full px-3 py-1 sm:min-h-0 ${filter === k ? "bg-brand-600 text-white" : "bg-white ring-1 ring-slate-200"}`}>{l}</button>)}
      </div>
      {groups.map((g) => (
        <div key={g} className="card overflow-hidden">
          <div className="bg-slate-50 px-4 py-2 text-sm font-semibold">{g}</div>
          <div className="divide-y divide-slate-100">
            {list.filter((m: any) => (m.station_name ?? "All stations") === g).map((m: any) => (
              <button key={m.id} onClick={() => setOpen(m.id)} className={`flex w-full flex-wrap items-center gap-3 px-4 py-3 text-left hover:bg-slate-50 ${m.status === "retired" ? "opacity-50" : ""}`}>
                <span className="w-40 shrink-0 text-sm">{TYPE_LABEL[m.type] ?? m.type}</span>
                <span className="min-w-0 flex-1"><b>{m.name}</b><span className="block text-xs text-slate-500">{[m.make, m.model, m.location, m.hours != null ? `${m.hours.toLocaleString()} h` : null].filter(Boolean).join(" · ")}</span></span>
                <DueBadge m={m} />
                {m.due.warranty === "ending" && <Badge tone="amber">Warranty {m.due.warranty_days} days left</Badge>}
                {m.open_faults > 0 && <Badge tone="red">{m.open_faults} open fault</Badge>}
                <Badge tone={STATUS[m.status]?.tone}>{STATUS[m.status]?.label}</Badge>
              </button>
            ))}
          </div>
        </div>
      ))}
      {!list.length && <Empty>No machines here yet. Add the dispensers, generator, compressor, fans and lights to keep their service on time.</Empty>}
      {open && <MachineDetail id={open} manager={manager} onEdit={(m) => { setOpen(null); setEdit(m); }} onClose={() => { setOpen(null); reload(); }} />}
      {edit && <MachineForm m={edit} stations={stations.data ?? []} types={data.types} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); reload(); }} />}
    </div>
  );
}

function MachineForm({ m, stations, types, onClose, onSaved }: { m: any; stations: any[]; types: string[]; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState<any>({ name: "", type: "dispenser", station_id: stations[0]?.id ?? "", make: "", model: "", serial_no: "", location: "", installed_on: "", cost: "", vendor: "", vendor_phone: "",
    warranty_until: "", service_every_days: "", service_every_hours: "", last_service_on: "", hours: "", notes: "", status: "working", ...Object.fromEntries(Object.entries(m).map(([k, v]) => [k, v ?? ""])) });
  const { busy, run } = useAction();
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const num = (v: any) => (v === "" || v == null ? null : Number(v));
  const str = (v: any) => (v === "" ? null : v);
  return (
    <Modal open wide onClose={onClose} title={m.id ? `Edit — ${m.name}` : "Add a machine"}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const body = { name: f.name, type: f.type, station_id: num(f.station_id), make: str(f.make), model: str(f.model), serial_no: str(f.serial_no), location: str(f.location), installed_on: str(f.installed_on),
          cost: num(f.cost), vendor: str(f.vendor), vendor_phone: str(f.vendor_phone), warranty_until: str(f.warranty_until), service_every_days: num(f.service_every_days),
          service_every_hours: num(f.service_every_hours), last_service_on: str(f.last_service_on), hours: num(f.hours), notes: str(f.notes), ...(m.id ? { status: f.status } : {}) };
        if (await run(() => api(m.id ? `/machines/${m.id}` : "/machines", { method: m.id ? "PATCH" : "POST", body }), "Saved")) onSaved();
      }}>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Field label="Name *"><input className="input" required placeholder="e.g. Dispenser 1, Generator 30 kVA" value={f.name} onChange={set("name")} /></Field>
          <Field label="Type"><select className="input" value={f.type} onChange={set("type")}>{types.map((t) => <option key={t} value={t}>{TYPE_LABEL[t] ?? t}</option>)}</select></Field>
          <Field label="Station"><select className="input" value={f.station_id} onChange={set("station_id")}><option value="">All stations</option>{stations.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
          <Field label="Make"><input className="input" placeholder="Tokheim, Perkins…" value={f.make} onChange={set("make")} /></Field>
          <Field label="Model"><input className="input" value={f.model} onChange={set("model")} /></Field>
          <Field label="Serial no."><input className="input" value={f.serial_no} onChange={set("serial_no")} /></Field>
          <Field label="Where"><input className="input" placeholder="Island 1, back yard…" value={f.location} onChange={set("location")} /></Field>
          <Field label="Installed on"><input className="input" type="date" value={f.installed_on} onChange={set("installed_on")} /></Field>
          <Field label="Price paid (Rs)"><input className="input" type="number" min={0} value={f.cost} onChange={set("cost")} /></Field>
          <Field label="Mechanic / company"><input className="input" value={f.vendor} onChange={set("vendor")} /></Field>
          <Field label="Mechanic phone"><input className="input" value={f.vendor_phone} onChange={set("vendor_phone")} /></Field>
          <Field label="Warranty till"><input className="input" type="date" value={f.warranty_until} onChange={set("warranty_until")} /></Field>
          <Field label="Service every (days)"><input className="input" type="number" min={1} placeholder="90" value={f.service_every_days} onChange={set("service_every_days")} /></Field>
          <Field label="…or every (running hours)"><input className="input" type="number" min={1} placeholder="250 for a generator" value={f.service_every_hours} onChange={set("service_every_hours")} /></Field>
          <Field label="Last service on"><input className="input" type="date" value={f.last_service_on} onChange={set("last_service_on")} /></Field>
          {(f.type === "generator" || f.service_every_hours) && <Field label="Hours meter now"><input className="input" type="number" min={0} value={f.hours} onChange={set("hours")} /></Field>}
          {m.id && <Field label="Status"><select className="input" value={f.status} onChange={set("status")}>{Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select></Field>}
        </div>
        <Field label="Notes"><input className="input" value={f.notes} onChange={set("notes")} /></Field>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

function MachineDetail({ id, manager, onEdit, onClose }: { id: number; manager: boolean; onEdit: (m: any) => void; onClose: () => void }) {
  const { data, reload } = useApi<any>(`/machines/${id}`);
  const { busy, run } = useAction();
  const [f, setF] = useState({ kind: manager ? "service" : "fault", description: "", cost: "", done_by: "", hours: "", downtime_hours: "", photo_id: null as number | null });
  if (!data) return <Modal open onClose={onClose} title="Machine"><Loading /></Modal>;
  const kinds = manager ? ["service", "fault", "repair", "reading", "note"] : ["fault", "reading"];
  return (
    <Modal open wide onClose={onClose} title={data.name}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge tone={STATUS[data.status]?.tone}>{STATUS[data.status]?.label}</Badge><DueBadge m={data} />
        {data.warranty_until && <Badge tone={data.due.warranty === "expired" ? "slate" : data.due.warranty === "ending" ? "amber" : "green"}>Warranty {data.due.warranty === "expired" ? "ended" : "till"} {data.warranty_until}</Badge>}
        <span className="ml-auto text-slate-600">Spent {pkr(data.spent_total)} · down {data.downtime_hours} h</span>
        {manager && <button className="btn-secondary px-2 py-1 text-xs" onClick={() => onEdit(data)}>Edit</button>}
      </div>
      <div className="mt-2 text-xs text-slate-500">{[TYPE_LABEL[data.type], data.make, data.model, data.serial_no && `S/N ${data.serial_no}`, data.location, data.installed_on && `installed ${data.installed_on}`,
        data.vendor && `mechanic ${data.vendor}${data.vendor_phone ? ` (${data.vendor_phone})` : ""}`].filter(Boolean).join(" · ")}</div>
      <form className="mt-4 space-y-2 rounded-xl bg-slate-50 p-3" onSubmit={async (e) => {
        e.preventDefault();
        const body = { kind: f.kind, description: f.description || undefined, cost: f.cost ? Number(f.cost) : null, done_by: f.done_by || null, hours: f.hours ? Number(f.hours) : null, downtime_hours: f.downtime_hours ? Number(f.downtime_hours) : null, photo_id: f.photo_id };
        if (await run(() => api(`/machines/${id}/logs`, { body }), f.kind === "fault" ? "Fault reported — the manager has been told" : "Saved")) { setF({ ...f, description: "", cost: "", done_by: "", hours: "", downtime_hours: "", photo_id: null }); reload(); }
      }}>
        <div className="flex flex-wrap gap-1.5">{kinds.map((k) => <button type="button" key={k} onClick={() => setF({ ...f, kind: k })} className={`min-h-9 rounded-full px-3 py-1 text-sm sm:min-h-0 ${f.kind === k ? "bg-brand-600 text-white" : "bg-white ring-1 ring-slate-200"}`}>{k === "fault" ? "Report fault" : LOG[k].label}</button>)}</div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-4">
          <input className="input sm:col-span-2" placeholder={f.kind === "fault" ? "What is wrong? *" : f.kind === "service" ? "What was done (oil, filters, calibration…)" : "Details"} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} required={f.kind === "fault"} />
          {["service", "repair"].includes(f.kind) && <><input className="input" type="number" min={0} placeholder="Cost Rs (goes to expenses)" value={f.cost} onChange={(e) => setF({ ...f, cost: e.target.value })} />
            <input className="input" placeholder="Done by" value={f.done_by} onChange={(e) => setF({ ...f, done_by: e.target.value })} /></>}
          {(f.kind === "reading" || (f.kind === "service" && data.service_every_hours)) && <input className="input" type="number" min={0} placeholder="Hours meter" value={f.hours} onChange={(e) => setF({ ...f, hours: e.target.value })} />}
          {f.kind === "repair" && <input className="input" type="number" min={0} placeholder="Hours it was down" value={f.downtime_hours} onChange={(e) => setF({ ...f, downtime_hours: e.target.value })} />}
        </div>
        <div className="flex items-center gap-2">
          <PhotoButton kind="proof" label="Photo" onRead={(_r, pid) => setF((x) => ({ ...x, photo_id: pid }))} />{f.photo_id ? <PhotoThumb id={f.photo_id} size={10} /> : null}
          <button className="btn-primary ml-auto" disabled={busy}>{f.kind === "fault" ? <><AlertTriangle size={15} /> Report</> : <><CheckCircle2 size={15} /> Save</>}</button>
        </div>
      </form>
      <ul className="mt-4 max-h-80 divide-y divide-slate-100 overflow-y-auto text-sm">
        {data.logs.map((l: any) => (
          <li key={l.id} className="flex items-start gap-3 py-2">
            <span className="w-24 shrink-0 text-xs text-slate-500">{l.day}</span>
            <Badge tone={LOG[l.kind]?.tone}>{LOG[l.kind]?.label}</Badge>
            <span className="flex-1">{l.description}<span className="block text-xs text-slate-500">{[l.done_by, l.hours != null && `${l.hours} h`, l.downtime_hours && `down ${l.downtime_hours} h`, l.kind === "fault" && (l.resolved_at ? "fixed" : "open"), l.created_by && `by ${l.created_by}`].filter(Boolean).join(" · ")}</span></span>
            {l.photo_id ? <PhotoThumb id={l.photo_id} size={8} /> : null}
            {l.cost ? <span className="tabular-nums">{pkr(l.cost)}</span> : null}
          </li>
        ))}
        {!data.logs.length && <Empty>No history yet</Empty>}
      </ul>
    </Modal>
  );
}
