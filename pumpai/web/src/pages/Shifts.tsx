import { useEffect, useState } from "react";
import { Clock, AlertTriangle, CheckCircle2 } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Field, Loading, Modal, PageHeader, statusTone, useAction } from "../components/ui";
import { PRODUCTS, dt, num, pkr } from "../lib/format";
import { useAuth } from "../App";

const hhmm = (h: number) => `${Math.floor(h)}h ${String(Math.floor((h % 1) * 60)).padStart(2, "0")}m`;

export default function Shifts() {
  const shifts = useApi<any[]>("/shifts");
  const stations = useApi<any[]>("/stations");
  const { busy, run } = useAction();
  const { user } = useAuth();
  const isSalesman = user?.role === "salesman";
  const [open, setOpen] = useState({ station_id: "", attendant: "" });
  const [closing, setClosing] = useState<number | null>(null);
  const [receipt, setReceipt] = useState<any>(null);

  if (!shifts.data || !stations.data) return <Loading />;
  const myOpen = isSalesman ? shifts.data.find((s) => s.status === "open") : null;

  return (
    <div className="space-y-5">
      <PageHeader title={isSalesman ? "My shift" : "Shifts"} subtitle="12-hour shifts. At the end, enter the closing meter reading of each nozzle and the cash in hand — sales, stock and cash are worked out automatically." />

      {isSalesman ? (
        myOpen ? <MyShift id={myOpen.id} onClose={() => setClosing(myOpen.id)} /> : (
          <div className="card flex flex-wrap items-center gap-4 p-5">
            <Clock className="text-slate-400" />
            <div className="flex-1"><div className="font-medium">{user?.name} · {user?.station_name}</div><div className="text-sm text-slate-600">No shift open. Opening readings are taken from the meters automatically.</div></div>
            <button className="btn-primary" disabled={busy} onClick={() => run(() => api("/shifts/open", { body: {} }), "Shift started").then(() => shifts.reload())}>Start my shift</button>
          </div>
        )
      ) : (
        <form className="card flex flex-wrap items-end gap-3 p-4" onSubmit={async (e) => {
          e.preventDefault();
          if (await run(() => api("/shifts/open", { body: { station_id: Number(open.station_id || stations.data![0].id), attendant: open.attendant } }), "Shift opened")) { setOpen({ ...open, attendant: "" }); shifts.reload(); }
        }}>
          <Field label="Station"><select className="input" value={open.station_id} onChange={(e) => setOpen({ ...open, station_id: e.target.value })}>{stations.data.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
          <Field label="Attendant name"><input className="input" required value={open.attendant} onChange={(e) => setOpen({ ...open, attendant: e.target.value })} /></Field>
          <button className="btn-primary" disabled={busy}>Open shift</button>
        </form>
      )}

      <div className="card overflow-x-auto">
        <table className="w-full">
          <thead><tr><th className="th">Shift</th><th className="th">Attendant</th><th className="th">Opened</th><th className="th">Closed</th><th className="th text-right">Litres</th><th className="th text-right">Cash expected</th><th className="th text-right">Counted</th><th className="th text-right">Variance</th><th className="th" /></tr></thead>
          <tbody>{shifts.data.map((s) => {
            const hours = (Date.now() - Date.parse(s.opened_at)) / 3600_000;
            return (
              <tr key={s.id}>
                <td className="td text-xs">#{s.id} · {s.station_name.replace("Al-Madina ", "")}</td>
                <td className="td font-medium">{s.attendant}</td>
                <td className="td text-xs">{dt(s.opened_at)}</td>
                <td className="td text-xs">{s.status === "open" ? <><Badge tone={hours >= 12 ? "red" : statusTone("open")}>open {hhmm(hours)}</Badge></> : dt(s.closed_at)}</td>
                <td className="td text-right tabular-nums">{s.litres != null ? num(s.litres) : "—"}</td>
                <td className="td text-right tabular-nums">{s.cash_expected != null ? pkr(s.cash_expected) : "—"}</td>
                <td className="td text-right tabular-nums">{s.cash_actual != null ? pkr(s.cash_actual) : "—"}</td>
                <td className={`td text-right font-medium tabular-nums ${s.variance < -500 ? "text-red-600" : s.variance > 0 ? "text-emerald-600" : ""}`}>{s.variance != null ? pkr(s.variance) : "—"}</td>
                <td className="td">{s.status === "open" && !isSalesman && <button className="btn-primary !px-2 !py-1 text-xs" onClick={() => setClosing(s.id)}>Close</button>}</td>
              </tr>
            );
          })}</tbody>
        </table>
      </div>

      {closing && <CloseShift id={closing} onClose={() => setClosing(null)} onClosed={(r) => { setClosing(null); setReceipt(r); shifts.reload(); }} />}
      {receipt && <Receipt r={receipt} onClose={() => setReceipt(null)} />}
    </div>
  );
}

function MyShift({ id, onClose }: { id: number; onClose: () => void }) {
  const { data } = useApi<any>(`/shifts/${id}/live`, 60_000);
  if (!data) return <Loading />;
  const over = data.hours_open >= 12;
  return (
    <div className={`card p-5 ${over ? "border-red-300" : ""}`}>
      <div className="flex flex-wrap items-center gap-4">
        <div className={`rounded-xl p-3 ${over ? "bg-red-100 text-red-700" : "bg-emerald-100 text-emerald-700"}`}><Clock size={22} /></div>
        <div className="flex-1">
          <div className="text-2xl font-semibold tabular-nums">{hhmm(data.hours_open)}</div>
          <div className="text-sm text-slate-600">Shift #{data.shift.id} · started {dt(data.shift.opened_at)} · {data.shift.station_name}</div>
        </div>
        <button className="btn-primary px-5 py-3 text-base" onClick={onClose}>End shift</button>
      </div>
      {over && <div className="mt-3 flex items-center gap-2 rounded-lg bg-red-50 p-2 text-sm text-red-700"><AlertTriangle size={16} /> 12 hours are over — please end your shift with the meter readings and cash.</div>}
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        {data.summary.by_product.map((p: any) => <div key={p.product} className="rounded-lg bg-slate-50 p-3"><div className="text-xs text-slate-500">{PRODUCTS[p.product]} on POS</div><div className="font-semibold tabular-nums">{num(p.litres, 1)} L · {pkr(p.amount)}</div></div>)}
        {!data.summary.by_product.length && <div className="text-sm text-slate-500">No sales entered on the POS yet. Meter litres you don't enter are booked as cash at shift end.</div>}
      </div>
    </div>
  );
}

function CloseShift({ id, onClose, onClosed }: { id: number; onClose: () => void; onClosed: (r: any) => void }) {
  const { data } = useApi<any>(`/shifts/${id}/live`);
  const [readings, setReadings] = useState<Record<string, string>>({});
  const [cash, setCash] = useState("");
  const { busy, run } = useAction();
  useEffect(() => { setReadings({}); }, [id]);
  if (!data) return <Modal open onClose={onClose} title="End shift"><Loading /></Modal>;

  // live estimate: meter litres since the last reading x current price, plus everything already on the POS
  const byProduct: Record<string, number> = {};
  for (const r of data.readings) {
    const v = Number(readings[r.nozzle_id]);
    if (v) byProduct[r.product] = (byProduct[r.product] ?? 0) + Math.max(0, v - (r.checkpoint ?? r.opening));
  }
  const totalMeterL = data.readings.reduce((a: number, r: any) => a + Math.max(0, (Number(readings[r.nozzle_id]) || (r.checkpoint ?? r.opening)) - r.opening), 0);
  const allFilled = data.readings.every((r: any) => readings[r.nozzle_id] !== undefined && readings[r.nozzle_id] !== "");

  return (
    <Modal open onClose={onClose} title={`End shift — ${data.shift.attendant}`} wide>
      <form className="space-y-4" onSubmit={async (e) => {
        e.preventDefault();
        const r = await run(() => api(`/shifts/${id}/close`, { body: { readings: Object.fromEntries(Object.entries(readings).map(([k, v]) => [k, Number(v)])), cash_actual: Number(cash) } }));
        if (r) onClosed(r);
      }}>
        <p className="text-sm text-slate-600">Step 1 — closing meter reading of every nozzle:</p>
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table className="w-full">
            <thead><tr><th className="th">Nozzle</th><th className="th text-right">Opening</th><th className="th text-right">Last reading</th><th className="th">Closing reading</th><th className="th text-right">Litres (shift)</th></tr></thead>
            <tbody>{data.readings.map((r: any) => {
              const v = Number(readings[r.nozzle_id]);
              const last = r.checkpoint ?? r.opening;
              const bad = readings[r.nozzle_id] !== undefined && readings[r.nozzle_id] !== "" && v < last;
              return (
                <tr key={r.nozzle_id}>
                  <td className="td text-sm">{r.label} <span className="text-xs text-slate-500">{PRODUCTS[r.product]}</span></td>
                  <td className="td text-right text-xs tabular-nums">{num(r.opening, 2)}</td>
                  <td className="td text-right text-xs tabular-nums">{r.checkpoint != null ? num(r.checkpoint, 2) : "—"}</td>
                  <td className="td"><input className={`input w-36 ${bad ? "border-red-400" : ""}`} type="number" step="0.01" min={last} required value={readings[r.nozzle_id] ?? ""} onChange={(e) => setReadings({ ...readings, [r.nozzle_id]: e.target.value })} /></td>
                  <td className="td text-right tabular-nums">{v >= r.opening ? num(v - r.opening, 2) : "—"}</td>
                </tr>
              );
            })}</tbody>
          </table>
        </div>
        {allFilled && (
          <div className="rounded-lg bg-slate-50 p-3 text-sm">
            <div className="font-medium">Shift total on meters: {num(totalMeterL, 2)} L</div>
            <div className="text-xs text-slate-500">Since last reading: {Object.entries(byProduct).map(([p, l]) => `${PRODUCTS[p]} ${num(l, 2)} L @ Rs ${data.prices[p]}`).join(" · ") || "—"}. Litres not entered on the POS are booked as cash sales.</div>
          </div>
        )}
        <p className="text-sm text-slate-600">Step 2 — cash in hand:</p>
        <Field label="Cash counted (Rs)"><input className="input text-xl font-semibold" type="number" min={0} required value={cash} onChange={(e) => setCash(e.target.value)} /></Field>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>End shift</button></div>
      </form>
    </Modal>
  );
}

function Receipt({ r, onClose }: { r: any; onClose: () => void }) {
  const short = r.variance < -500;
  return (
    <Modal open onClose={onClose} title={`Shift #${r.id} closed`}>
      <div className="space-y-3 text-sm">
        <div className={`flex items-center gap-2 rounded-lg p-3 ${short ? "bg-red-50 text-red-800" : "bg-emerald-50 text-emerald-800"}`}>
          {short ? <AlertTriangle size={18} /> : <CheckCircle2 size={18} />}
          <span className="font-medium">{r.variance < 0 ? `Cash short ${pkr(-r.variance)}` : r.variance > 0 ? `Cash over ${pkr(r.variance)}` : "Cash matches exactly"}</span>
        </div>
        <table className="w-full">
          <tbody>
            {r.summary.by_product.map((p: any) => <tr key={p.product}><td className="td">{PRODUCTS[p.product]}</td><td className="td text-right tabular-nums">{num(p.litres, 2)} L</td><td className="td text-right tabular-nums">{pkr(p.amount)}</td></tr>)}
            <tr className="font-semibold"><td className="td">Total sales</td><td className="td text-right tabular-nums">{num(r.summary.litres, 2)} L</td><td className="td text-right tabular-nums">{pkr(r.summary.amount)}</td></tr>
          </tbody>
        </table>
        <table className="w-full"><tbody>
          {r.summary.by_payment.map((p: any) => <tr key={p.method}><td className="td capitalize">{p.method}</td><td className="td text-right tabular-nums">{pkr(p.amount)}</td></tr>)}
          <tr><td className="td font-medium">Cash expected</td><td className="td text-right font-medium tabular-nums">{pkr(r.cash_expected)}</td></tr>
          <tr><td className="td font-medium">Cash counted</td><td className="td text-right font-medium tabular-nums">{pkr(r.cash_actual)}</td></tr>
        </tbody></table>
        <p className="text-xs text-slate-500">Stock in the tanks has been updated from the meter readings. Your manager has been sent this shift report.</p>
        <div className="flex justify-end gap-2"><button className="btn-secondary" onClick={() => window.print()}>Print</button><button className="btn-primary" onClick={onClose}>Done</button></div>
      </div>
    </Modal>
  );
}
