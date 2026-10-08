import { useState } from "react";
import { Plus, RefreshCw, Check, X } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Field, Loading, Modal, PageHeader, useAction } from "../components/ui";
import { d } from "../lib/format";
import { PhotoButton, PhotoThumb } from "../components/Capture";
import { useAuth } from "../App";

const Ur = ({ children }: { children: React.ReactNode }) => <span lang="ur" dir="rtl" className="font-urdu">{children}</span>;

/** Licences & certificates plus the station checklist, for managers. */
export default function Compliance() {
  const [tab, setTab] = useState<"licences" | "checklist">("licences");
  return (
    <div className="space-y-5">
      <PageHeader title="Licences & checklist" subtitle="Expiry reminders for every licence and certificate, and the daily safety / quality checks" />
      <div className="flex gap-2">
        {(["licences", "checklist"] as const).map((t) => <button key={t} onClick={() => setTab(t)} className={`rounded-lg px-4 py-2 text-sm font-medium ${tab === t ? "bg-slate-900 text-white" : "bg-white ring-1 ring-slate-200"}`}>{t === "licences" ? "Licences & certificates" : "Daily checklist"}</button>)}
      </div>
      {tab === "licences" ? <Licences /> : <ChecklistManager />}
    </div>
  );
}

function Licences() {
  const { data, reload } = useApi<any[]>("/licences");
  const stations = useApi<any[]>("/stations");
  const [edit, setEdit] = useState<any>(null);
  if (!data) return <Loading />;
  const tone = (n: number) => (n < 0 ? "red" : n <= 7 ? "red" : n <= 30 ? "amber" : "green");
  return (
    <div className="card">
      <div className="flex items-center justify-between p-4 pb-2"><h2 className="font-semibold">Licences & certificates</h2><button className="btn-primary" onClick={() => setEdit({})}><Plus size={15} /> Add</button></div>
      {/* phone: one card per licence */}
      <ul className="divide-y divide-slate-100 border-t border-slate-100 sm:hidden">
        {data.map((l) => (
          <li key={l.id} className="px-4 py-3">
            <div className="flex items-start justify-between gap-2">
              <span className="flex min-w-0 items-center gap-1 font-semibold">{l.name}{l.photo_id ? <PhotoThumb id={l.photo_id} size={7} /> : null}</span>
              <span className="shrink-0 text-right text-sm tabular-nums">{d(l.expires_on)}</span>
            </div>
            <div className="break-words text-xs text-slate-500">{[l.number, l.authority, l.station_name ?? "All stations"].filter(Boolean).join(" · ")}</div>
            <div className="mt-1.5 flex items-center justify-between gap-2">
              <Badge tone={tone(l.days_left)}>{l.days_left < 0 ? `Expired ${-l.days_left} days ago` : l.days_left === 0 ? "Expires today" : `${l.days_left} days left`}</Badge>
              <button className="btn-secondary min-h-9 !py-1 text-xs" onClick={() => setEdit(l)}><RefreshCw size={13} /> Renew / edit</button>
            </div>
          </li>
        ))}
      </ul>
      <div className="hidden overflow-x-auto sm:block">
      <table className="w-full">
        <thead><tr><th className="th">Licence</th><th className="th">Number / authority</th><th className="th">Station</th><th className="th">Expires</th><th className="th" /></tr></thead>
        <tbody>{data.map((l) => (
          <tr key={l.id}>
            <td className="td font-medium"><span className="flex items-center gap-1">{l.name}{l.photo_id ? <PhotoThumb id={l.photo_id} size={7} /> : null}</span></td>
            <td className="td text-sm text-slate-600">{[l.number, l.authority].filter(Boolean).join(" · ")}</td>
            <td className="td text-sm">{l.station_name ?? "All"}</td>
            <td className="td"><div className="text-sm">{d(l.expires_on)}</div><Badge tone={tone(l.days_left)}>{l.days_left < 0 ? `Expired ${-l.days_left} days ago` : l.days_left === 0 ? "Expires today" : `${l.days_left} days left`}</Badge></td>
            <td className="td text-right"><button className="btn-secondary !px-2 !py-1 text-xs" onClick={() => setEdit(l)}><RefreshCw size={13} /> Renew / edit</button></td>
          </tr>
        ))}</tbody>
      </table>
      </div>
      {edit && <LicenceForm l={edit} stations={stations.data ?? []} onClose={() => setEdit(null)} onDone={() => { setEdit(null); reload(); }} />}
    </div>
  );
}

function LicenceForm({ l, stations, onClose, onDone }: { l: any; stations: any[]; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ name: l.name ?? "", number: l.number ?? "", authority: l.authority ?? "", station_id: l.station_id ? String(l.station_id) : "", issued_on: l.issued_on ?? "", expires_on: l.expires_on ?? "", note: l.note ?? "", photo_id: null as number | null });
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={l.id ? `Renew / edit — ${l.name}` : "Add licence or certificate"}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const body = { ...f, station_id: f.station_id ? Number(f.station_id) : null, issued_on: f.issued_on || null, number: f.number || null, authority: f.authority || null, note: f.note || null };
        if (await run(() => (l.id ? api(`/licences/${l.id}`, { method: "PATCH", body }) : api("/licences", { body })), "Saved")) onDone();
      }}>
        <Field label="Name"><input className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="e.g. Explosives licence" /></Field>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Number"><input className="input" value={f.number} onChange={(e) => setF({ ...f, number: e.target.value })} /></Field>
          <Field label="Issued by"><input className="input" value={f.authority} onChange={(e) => setF({ ...f, authority: e.target.value })} /></Field>
          <Field label="Issued on"><input className="input" type="date" value={f.issued_on} onChange={(e) => setF({ ...f, issued_on: e.target.value })} /></Field>
          <Field label="Expires on"><input className="input" type="date" required value={f.expires_on} onChange={(e) => setF({ ...f, expires_on: e.target.value })} /></Field>
          <Field label="Station"><select className="input" value={f.station_id} onChange={(e) => setF({ ...f, station_id: e.target.value })}><option value="">All / head office</option>{stations.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
        </div>
        <div className="flex items-center gap-2 text-sm"><PhotoButton kind="proof" label="Photo of certificate" onRead={(_, id) => setF((x) => ({ ...x, photo_id: id }))} />{f.photo_id ? <PhotoThumb id={f.photo_id} size={10} /> : null}</div>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

function ChecklistManager() {
  const stations = useApi<any[]>("/stations");
  const [sid, setSid] = useState<number | null>(null);
  const station = sid ?? stations.data?.[0]?.id;
  const [adding, setAdding] = useState(false);
  if (!stations.data || !station) return <Loading />;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <select className="input w-auto" value={station} onChange={(e) => setSid(Number(e.target.value))}>{stations.data.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
        <button className="btn-secondary ml-auto" onClick={() => setAdding(true)}><Plus size={15} /> Add check</button>
      </div>
      <Checklist stationId={station} key={station} />
      {adding && <CheckItemForm onClose={() => setAdding(false)} />}
    </div>
  );
}

function CheckItemForm({ onClose }: { onClose: () => void }) {
  const [f, setF] = useState({ title: "", urdu: "", frequency: "daily", kind: "check", unit: "", min_ok: "", max_ok: "", needs_photo: false });
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title="Add a check">
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const body = { ...f, urdu: f.urdu || null, unit: f.unit || null, min_ok: f.min_ok === "" ? null : Number(f.min_ok), max_ok: f.max_ok === "" ? null : Number(f.max_ok) };
        if (await run(() => api("/checklist/items", { body }), "Check added")) { onClose(); location.reload(); }
      }}>
        <Field label="What to check"><input className="input" required value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
        <Field label="Urdu (optional)"><input className="input font-urdu" dir="rtl" value={f.urdu} onChange={(e) => setF({ ...f, urdu: e.target.value })} /></Field>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="How often"><select className="input" value={f.frequency} onChange={(e) => setF({ ...f, frequency: e.target.value })}><option value="daily">Every day</option><option value="weekly">Every week</option></select></Field>
          <Field label="Answer"><select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}><option value="check">OK / problem</option><option value="number">A reading (number)</option></select></Field>
          {f.kind === "number" && <>
            <Field label="Unit"><input className="input" value={f.unit} onChange={(e) => setF({ ...f, unit: e.target.value })} placeholder="mm, ml, kg/m³" /></Field>
            <div className="grid grid-cols-2 gap-2"><Field label="Lowest OK"><input className="input" type="number" step="any" value={f.min_ok} onChange={(e) => setF({ ...f, min_ok: e.target.value })} /></Field>
              <Field label="Highest OK"><input className="input" type="number" step="any" value={f.max_ok} onChange={(e) => setF({ ...f, max_ok: e.target.value })} /></Field></div>
          </>}
        </div>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.needs_photo} onChange={(e) => setF({ ...f, needs_photo: e.target.checked })} /> Photo required</label>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

/** The checklist with big buttons: OK / problem, a reading where needed, and a photo where required. */
export function Checklist({ stationId }: { stationId?: number }) {
  const { data, reload } = useApi<any>(`/checklist${stationId ? `?station_id=${stationId}` : ""}`);
  const { user } = useAuth();
  if (!data) return <Loading />;
  return (
    <div className="space-y-3">
      <div className="rounded-2xl bg-white p-4 ring-1 ring-slate-200">
        <div className="flex items-center justify-between"><span className="text-lg font-semibold">Today · <Ur>آج</Ur></span><span className="text-2xl font-bold tabular-nums">{data.done} / {data.total}</span></div>
        <div className="mt-2 h-3 rounded-full bg-slate-100"><div className="h-full rounded-full bg-emerald-500" style={{ width: `${(data.done / Math.max(1, data.total)) * 100}%` }} /></div>
      </div>
      {!data.items.length && <p className="rounded-2xl bg-white p-6 text-center text-slate-500 ring-1 ring-slate-200">No checks for today. The manager adds them in Safety &amp; checks. · <Ur>آج کوئی چیک نہیں</Ur></p>}
      {data.items.map((i: any) => <CheckRow key={i.id} i={i} stationId={data.station_id} onDone={reload} canRedo={user?.role !== "salesman"} />)}
    </div>
  );
}

function CheckRow({ i, stationId, onDone, canRedo }: { i: any; stationId: number; onDone: () => void; canRedo: boolean }) {
  const [value, setValue] = useState("");
  const [photo, setPhoto] = useState<number | null>(null);
  const { busy, run } = useAction();
  const e = i.entry;
  const send = (ok: boolean) => run(() => api(`/checklist/${i.id}`, { body: { station_id: stationId, ok, value: value === "" ? null : Number(value), photo_id: photo } }),
    (r: any) => r.ok ? "Done ✓" : "Problem reported to the manager").then((r) => { if (r) { setValue(""); setPhoto(null); onDone(); } });
  return (
    <div className={`rounded-2xl p-4 ring-2 ${e ? (e.ok ? "bg-emerald-50 ring-emerald-300" : "bg-red-50 ring-red-300") : "bg-white ring-slate-200"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-[200px] flex-1">
          <div className="text-lg font-semibold">{i.title}{i.frequency === "weekly" && <span className="ml-2 text-xs font-normal text-slate-500">weekly</span>}</div>
          {i.urdu && <div className="text-slate-600"><Ur>{i.urdu}</Ur></div>}
          {i.kind === "number" && (i.min_ok != null || i.max_ok != null) && <div className="text-xs text-slate-500">OK range: {i.min_ok ?? "…"} to {i.max_ok ?? "…"} {i.unit}</div>}
        </div>
        {e && !canRedo ? (
          <div className={`text-lg font-bold ${e.ok ? "text-emerald-700" : "text-red-700"}`}>{e.ok ? "✓ Done" : "✗ Problem"}{e.value != null && ` · ${e.value} ${i.unit ?? ""}`}<div className="text-xs font-normal text-slate-500">{e.done_by}</div></div>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            {e && <span className="text-sm text-slate-600">{e.ok ? "✓" : "✗"} {e.value != null && `${e.value} ${i.unit ?? ""}`} by {e.done_by}</span>}
            {i.kind === "number" && <input className="input w-28 py-3 text-xl" type="number" step="any" inputMode="decimal" placeholder={i.unit ?? ""} value={value} onChange={(x) => setValue(x.target.value)} aria-label={`${i.title} reading`} />}
            {i.needs_photo ? <PhotoButton kind="proof" label={photo ? "📷 ✓" : "Photo"} big onRead={(_, id) => setPhoto(id)} /> : null}
            <button disabled={busy || (i.needs_photo && !photo) || (i.kind === "number" && value === "")} onClick={() => send(true)} className="flex items-center gap-1 rounded-xl bg-emerald-600 px-5 py-3 text-lg font-bold text-white disabled:bg-slate-300"><Check /> OK</button>
            {i.kind === "check" && <button disabled={busy} onClick={() => send(false)} className="flex items-center gap-1 rounded-xl bg-red-600 px-4 py-3 text-lg font-bold text-white"><X /> Problem</button>}
          </div>
        )}
      </div>
      {e?.photo_id && <div className="mt-2"><PhotoThumb id={e.photo_id} size={12} /></div>}
    </div>
  );
}
