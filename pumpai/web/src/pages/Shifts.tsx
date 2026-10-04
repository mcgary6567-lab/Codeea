import { useState } from "react";
import { api, useApi } from "../lib/api";
import { Badge, Field, Loading, Modal, PageHeader, statusTone, useAction } from "../components/ui";
import { dt, num, pkr } from "../lib/format";
import { useAuth } from "../App";

export default function Shifts() {
  const shifts = useApi<any[]>("/shifts");
  const stations = useApi<any[]>("/stations");
  const { busy, run } = useAction();
  const { user } = useAuth();
  const isSalesman = user?.role === "salesman";
  const [open, setOpen] = useState({ station_id: "", attendant: "" });
  const [closing, setClosing] = useState<any>(null);
  const [readings, setReadings] = useState<Record<string, string>>({});
  const [cash, setCash] = useState("");

  if (!shifts.data || !stations.data) return <Loading />;
  const startClose = (s: any) => {
    setClosing(s);
    setReadings(Object.fromEntries(s.readings.map((r: any) => [r.nozzle_id, ""])));
    setCash("");
  };

  return (
    <div>
      <PageHeader title="Shifts" subtitle="Open with automatic meter readings; close with closing totalizers and counted cash. Shortages are flagged instantly." />
      <form className="card mb-5 flex flex-wrap items-end gap-3 p-4" onSubmit={async (e) => {
        e.preventDefault();
        if (await run(() => api("/shifts/open", { body: { station_id: Number(open.station_id || stations.data![0].id), attendant: isSalesman ? undefined : open.attendant } }), "Shift opened")) { setOpen({ ...open, attendant: "" }); shifts.reload(); }
      }}>
        {isSalesman ? (
          <div className="flex-1 text-sm text-slate-600">
            <div className="font-medium text-slate-900">{user?.name} · {user?.station_name}</div>
            {shifts.data.some((s) => s.status === "open") ? "Your shift is open. Close it at the end of duty with meter readings and cash." : "Start your shift to begin recording sales."}
          </div>
        ) : (
          <>
            <Field label="Station"><select className="input" value={open.station_id} onChange={(e) => setOpen({ ...open, station_id: e.target.value })}>{stations.data.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
            <Field label="Attendant name"><input className="input" required value={open.attendant} onChange={(e) => setOpen({ ...open, attendant: e.target.value })} /></Field>
          </>
        )}
        <button className="btn-primary" disabled={busy || (isSalesman && shifts.data.some((s) => s.status === "open"))}>{isSalesman ? "Start my shift" : "Open shift"}</button>
      </form>
      <div className="card overflow-x-auto">
        <table className="w-full">
          <thead><tr><th className="th">Shift</th><th className="th">Attendant</th><th className="th">Opened</th><th className="th">Closed</th><th className="th text-right">Litres</th><th className="th text-right">Cash expected</th><th className="th text-right">Counted</th><th className="th text-right">Variance</th><th className="th" /></tr></thead>
          <tbody>{shifts.data.map((s) => (
            <tr key={s.id}>
              <td className="td text-xs">#{s.id} · {s.station_name.replace("Al-Madina ", "")}</td>
              <td className="td font-medium">{s.attendant}</td>
              <td className="td text-xs">{dt(s.opened_at)}</td>
              <td className="td text-xs">{s.status === "open" ? <Badge tone={statusTone("open")}>open</Badge> : dt(s.closed_at)}</td>
              <td className="td text-right tabular-nums">{s.litres != null ? num(s.litres) : "—"}</td>
              <td className="td text-right tabular-nums">{s.cash_expected != null ? pkr(s.cash_expected) : "—"}</td>
              <td className="td text-right tabular-nums">{s.cash_actual != null ? pkr(s.cash_actual) : "—"}</td>
              <td className={`td text-right font-medium tabular-nums ${s.variance < -500 ? "text-red-600" : s.variance > 0 ? "text-emerald-600" : ""}`}>{s.variance != null ? pkr(s.variance) : "—"}</td>
              <td className="td">{s.status === "open" && <button className="btn-primary !px-2 !py-1 text-xs" onClick={() => startClose(s)}>Close</button>}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <Modal open={!!closing} onClose={() => setClosing(null)} title={`Close shift #${closing?.id} — ${closing?.attendant}`}>
        {closing && (
          <form className="space-y-3" onSubmit={async (e) => {
            e.preventDefault();
            const r: any = await run(() => api(`/shifts/${closing.id}/close`, { body: { readings: Object.fromEntries(Object.entries(readings).map(([k, v]) => [k, Number(v)])), cash_actual: Number(cash) } }),
              (s: any) => `Shift closed. ${num(s.litres)} L, variance ${pkr(s.variance)}`);
            if (r) { setClosing(null); shifts.reload(); }
          }}>
            <p className="text-sm text-slate-600">Enter closing totalizer readings. Litres not entered as sales are booked as cash sales automatically.</p>
            {closing.readings.map((r: any) => (
              <Field key={r.nozzle_id} label={`${r.label} — opening ${num(r.opening, 2)}`}>
                <input className="input" type="number" step="0.01" min={r.opening} required value={readings[r.nozzle_id] ?? ""} onChange={(e) => setReadings({ ...readings, [r.nozzle_id]: e.target.value })} />
              </Field>
            ))}
            <Field label="Cash counted (Rs)"><input className="input" type="number" min={0} required value={cash} onChange={(e) => setCash(e.target.value)} /></Field>
            <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={() => setClosing(null)}>Cancel</button><button className="btn-primary" disabled={busy}>Close shift</button></div>
          </form>
        )}
      </Modal>
    </div>
  );
}
