import { useState } from "react";
import { Check, X } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Loading, Modal, PageHeader, useAction } from "../components/ui";

const Ur = ({ children }: { children: React.ReactNode }) => <span lang="ur" dir="rtl" className="font-urdu">{children}</span>;
const SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const SLOTS = ["Day 8am–8pm", "Night 8pm–8am", "Full 24h", "Morning", "Evening"];

/** Weekly duty roster: who works which shift on which day. Absent-alert uses this too. */
export default function Roster() {
  const { data, reload } = useApi<any>("/roster");
  const today = useApi<any>("/roster/today");
  const [edit, setEdit] = useState<null | { user: any; weekday: number; cur: any }>(null);
  if (!data) return <Loading />;
  const cell = (userId: number, wd: number) => (data.roster as any[]).find((r) => r.user_id === userId && r.weekday === wd);
  const stationName = (id: number | null) => (data.stations.find((s: any) => s.id === id)?.name ?? "");

  return (
    <div className="space-y-4">
      <PageHeader title="Duty roster · ڈیوٹی روسٹر" subtitle="Hafte ka schedule — kaun kis din kis shift par. Jo rostered ho aur na aaye uska absent alert manager ko jata hai." />

      {today.data && today.data.rostered.length > 0 && (
        <div className="card p-4">
          <h2 className="mb-2 font-semibold">Aaj ({today.data.weekday}) · <Ur>آج</Ur></h2>
          <div className="flex flex-wrap gap-2">
            {today.data.rostered.map((r: any) => (
              <span key={r.user_id} className={`rounded-lg px-3 py-1.5 text-sm ring-1 ${r.present ? "bg-emerald-50 text-emerald-800 ring-emerald-200" : "bg-red-50 text-red-700 ring-red-200"}`}>
                {r.present ? "✓" : "✗"} {r.name}{r.slot ? ` · ${r.slot}` : ""}{r.station_name ? ` · ${r.station_name}` : ""}
              </span>
            ))}
          </div>
          <p className="mt-2 text-xs text-slate-500">Hara = aa gaya, surkh = abhi tak nahi aaya.</p>
        </div>
      )}

      <div className="card overflow-x-auto">
        <table className="w-full min-w-[640px]">
          <thead><tr><th className="th text-left">Staff</th>{SHORT.map((d, i) => <th key={i} className="th text-center">{d}</th>)}</tr></thead>
          <tbody>
            {data.staff.map((u: any) => (
              <tr key={u.id}>
                <td className="td"><div className="font-medium">{u.name}</div><div className="text-xs text-slate-500 capitalize">{u.job_title || u.role}</div></td>
                {SHORT.map((_, wd) => {
                  const c = cell(u.id, wd);
                  return (
                    <td key={wd} className="td text-center">
                      <button onClick={() => setEdit({ user: u, weekday: wd, cur: c })}
                        className={`min-h-8 w-full rounded-lg px-1 py-1 text-xs ${c ? "bg-brand-50 font-medium text-brand-800 ring-1 ring-brand-200" : "text-slate-300 hover:bg-slate-50"}`}>
                        {c ? <>{c.slot || "On"}{c.station_id ? <span className="block text-[10px] text-slate-500">{stationName(c.station_id)}</span> : null}</> : "+"}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
            {!data.staff.length && <tr><td className="td text-slate-500" colSpan={8}>Koi staff nahi.</td></tr>}
          </tbody>
        </table>
      </div>

      {edit && <RosterCell data={data} e={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); reload(); today.reload(); }} />}
    </div>
  );
}

function RosterCell({ data, e, onClose, onSaved }: { data: any; e: any; onClose: () => void; onSaved: () => void }) {
  const { busy, run } = useAction();
  const [slot, setSlot] = useState(e.cur?.slot ?? "");
  const [station, setStation] = useState(e.cur?.station_id ? String(e.cur.station_id) : "");
  const save = () => run(() => api("/roster", { method: "PUT", body: { user_id: e.user.id, weekday: e.weekday, slot: slot || null, station_id: station ? Number(station) : null } }), "Roster saved").then((r) => { if (r) onSaved(); });
  const remove = () => run(() => api(`/roster/${e.user.id}/${e.weekday}`, { method: "DELETE" }), "Removed").then((r) => { if (r) onSaved(); });
  return (
    <Modal open onClose={onClose} title={`${e.user.name} — ${["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][e.weekday]}`}>
      <div className="space-y-3">
        <div>
          <div className="mb-1 text-sm font-medium">Shift</div>
          <div className="flex flex-wrap gap-2">
            {SLOTS.map((s) => <button key={s} onClick={() => setSlot(s)} className={`rounded-lg px-3 py-1.5 text-sm ${slot === s ? "bg-slate-900 text-white" : "bg-slate-100"}`}>{s}</button>)}
          </div>
          <input className="input mt-2" placeholder="Ya apni shift likhein" value={slot} onChange={(ev) => setSlot(ev.target.value)} />
        </div>
        {data.stations.length > 1 && (
          <div><div className="mb-1 text-sm font-medium">Station</div>
            <select className="input" value={station} onChange={(ev) => setStation(ev.target.value)}><option value="">Any</option>{data.stations.map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></div>
        )}
        <div className="flex items-center justify-end gap-2">
          {e.cur && <button type="button" className="btn-secondary mr-auto !text-rose-700" disabled={busy} onClick={remove}><X size={15} /> Remove</button>}
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy} onClick={save}><Check size={15} /> Save</button>
        </div>
      </div>
    </Modal>
  );
}
