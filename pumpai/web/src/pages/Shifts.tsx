import { useState } from "react";
import { Clock, AlertTriangle } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Field, Loading, Modal, PageHeader, statusTone, useAction } from "../components/ui";
import { PRODUCTS, dt, num, pkr } from "../lib/format";
import { useAuth } from "../App";
import { PhotoButton } from "../components/Capture";
import { ShiftExpenses, ShiftReport, StartShiftSheet } from "../components/ShiftParts";
import { Ur } from "../components/VoiceShell";

const hhmm = (h: number) => `${Math.floor(h)}h ${String(Math.floor((h % 1) * 60)).padStart(2, "0")}m`;

export default function Shifts() {
  const shifts = useApi<any[]>("/shifts");
  const stations = useApi<any[]>("/stations");
  const { user } = useAuth();
  const isSalesman = user?.role === "salesman";
  const [mgrOpen, setMgrOpen] = useState<{ station_id: string; attendant: string } | null>(null);
  const [closing, setClosing] = useState<number | null>(null);
  const [report, setReport] = useState<number | null>(null);

  if (!shifts.data || !stations.data) return <Loading />;
  const myOpen = isSalesman ? shifts.data.find((s) => s.status === "open") : null;

  return (
    <div className="space-y-5">
      <PageHeader title={isSalesman ? "My shift" : "Shifts"}
        subtitle="Shift start: take the meter reading handed over by the last salesman. Shift end: write the closing reading and count the cash — litres, khata, digital, expenses and cash are worked out automatically."
        actions={!isSalesman && <button className="btn-primary" onClick={() => setMgrOpen({ station_id: String(stations.data![0].id), attendant: "" })}>Open a shift</button>} />

      {isSalesman && (myOpen ? <MyShift id={myOpen.id} onEnd={() => setClosing(myOpen.id)} /> : (
        <div className="card p-5">
          <div className="mb-3 flex items-center gap-3"><Clock className="text-slate-400" /><div><div className="font-semibold">Start your shift — {user?.name} · {user?.station_name}</div><div className="text-sm text-slate-600">Check every meter you will run and confirm the reading.</div></div></div>
          <StartShiftSheet big onStarted={() => shifts.reload()} />
        </div>
      ))}

      {/* phone: one card per shift */}
      <ul className="card divide-y divide-slate-100 sm:hidden">
        {shifts.data.map((s) => {
          const hours = (Date.now() - Date.parse(s.opened_at)) / 3600_000;
          return (
            <li key={s.id} className="cursor-pointer px-4 py-3 active:bg-slate-50" onClick={() => setReport(s.id)}>
              <div className="flex items-start justify-between gap-2">
                <span className="min-w-0"><span className="block font-semibold">{s.attendant}</span><span className="text-xs text-slate-500">#{s.id} · {s.station_name}</span></span>
                <span className="shrink-0 text-right">
                  <span className={`block font-semibold tabular-nums ${s.variance < -500 ? "text-red-600" : s.variance > 0 ? "text-emerald-600" : ""}`}>{s.variance != null ? pkr(s.variance) : "—"}</span>
                  <span className="text-[11px] text-slate-500">Short / over · <Ur>کمی بیشی</Ur></span>
                </span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-slate-500">
                <span>{dt(s.opened_at)} →</span>
                {s.status === "open" ? <Badge tone={hours >= 12 ? "red" : statusTone("open")}>open {hhmm(hours)}</Badge> : <span>{dt(s.closed_at)}</span>}
              </div>
              <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-slate-500">
                <span><span className="tabular-nums text-slate-700">{s.litres != null ? num(s.litres) : "—"}</span> L</span>
                <span>Hand over <span className="tabular-nums text-slate-700">{s.cash_expected != null ? pkr(s.cash_expected) : "—"}</span></span>
                <span>Counted <span className="tabular-nums text-slate-700">{s.cash_actual != null ? pkr(s.cash_actual) : "—"}</span></span>
              </div>
              <div className="mt-2 flex justify-end" onClick={(e) => e.stopPropagation()}>
                {s.status === "open" && !isSalesman ? <button className="btn-primary min-h-9 !py-1 text-xs" onClick={() => setClosing(s.id)}>Close · <Ur>بند کریں</Ur></button>
                  : <button className="btn-secondary min-h-9 !py-1 text-xs" onClick={() => setReport(s.id)}>Report · <Ur>رپورٹ</Ur></button>}
              </div>
            </li>
          );
        })}
      </ul>
      <div className="card hidden overflow-x-auto sm:block">
        <table className="w-full">
          <thead><tr><th className="th">Shift</th><th className="th">Salesman</th><th className="th">Opened</th><th className="th">Closed</th><th className="th text-right">Litres</th><th className="th text-right">Cash to hand over</th><th className="th text-right">Counted</th><th className="th text-right">Short / over</th><th className="th" /></tr></thead>
          <tbody>{shifts.data.map((s) => {
            const hours = (Date.now() - Date.parse(s.opened_at)) / 3600_000;
            return (
              <tr key={s.id} className="cursor-pointer hover:bg-slate-50" onClick={() => setReport(s.id)}>
                <td className="td text-xs">#{s.id} · {s.station_name}</td>
                <td className="td font-medium">{s.attendant}</td>
                <td className="td text-xs">{dt(s.opened_at)}</td>
                <td className="td text-xs">{s.status === "open" ? <Badge tone={hours >= 12 ? "red" : statusTone("open")}>open {hhmm(hours)}</Badge> : dt(s.closed_at)}</td>
                <td className="td text-right tabular-nums">{s.litres != null ? num(s.litres) : "—"}</td>
                <td className="td text-right tabular-nums">{s.cash_expected != null ? pkr(s.cash_expected) : "—"}</td>
                <td className="td text-right tabular-nums">{s.cash_actual != null ? pkr(s.cash_actual) : "—"}</td>
                <td className={`td text-right font-medium tabular-nums ${s.variance < -500 ? "text-red-600" : s.variance > 0 ? "text-emerald-600" : ""}`}>{s.variance != null ? pkr(s.variance) : "—"}</td>
                <td className="td" onClick={(e) => e.stopPropagation()}>
                  {s.status === "open" && !isSalesman ? <button className="btn-primary !px-2 !py-1 text-xs" onClick={() => setClosing(s.id)}>Close</button>
                    : <button className="btn-secondary !px-2 !py-1 text-xs" onClick={() => setReport(s.id)}>Report</button>}
                </td>
              </tr>
            );
          })}</tbody>
        </table>
      </div>

      {mgrOpen && (
        <Modal open onClose={() => setMgrOpen(null)} title="Open a shift" wide>
          <div className="mb-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Station"><select className="input" value={mgrOpen.station_id} onChange={(e) => setMgrOpen({ ...mgrOpen, station_id: e.target.value })}>{stations.data.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
            <Field label="Salesman name"><input className="input" required value={mgrOpen.attendant} onChange={(e) => setMgrOpen({ ...mgrOpen, attendant: e.target.value })} /></Field>
          </div>
          {mgrOpen.attendant.length >= 2 ? <StartShiftSheet key={mgrOpen.station_id} stationId={Number(mgrOpen.station_id)} attendant={mgrOpen.attendant} onStarted={() => { setMgrOpen(null); shifts.reload(); }} />
            : <p className="text-sm text-slate-500">Enter the salesman's name to see the nozzles.</p>}
        </Modal>
      )}
      {closing && <CloseShift id={closing} onClose={() => setClosing(null)} onClosed={(r) => { setClosing(null); setReport(r.id); shifts.reload(); }} />}
      {report && <ShiftReport id={report} onClose={() => setReport(null)} />}
    </div>
  );
}

function MyShift({ id, onEnd }: { id: number; onEnd: () => void }) {
  const { data, reload } = useApi<any>(`/shifts/${id}/live`, 60_000);
  if (!data) return <Loading />;
  const s = data.summary;
  const over = data.hours_open >= 12;
  return (
    <div className={`card p-5 ${over ? "border-red-300" : ""}`}>
      <div className="flex flex-wrap items-center gap-4">
        <div className={`rounded-xl p-3 ${over ? "bg-red-100 text-red-700" : "bg-emerald-100 text-emerald-700"}`}><Clock size={22} /></div>
        <div className="min-w-0 flex-1">
          <div className="text-2xl font-semibold tabular-nums">{hhmm(data.hours_open)}</div>
          <div className="text-sm text-slate-600">Shift #{data.shift.id} · started {dt(data.shift.opened_at)} · {data.shift.station_name} · {data.readings.length} nozzles</div>
        </div>
        <button className="btn-primary px-5 py-3 text-base" onClick={onEnd}>End shift</button>
      </div>
      {over && <div className="mt-3 flex items-center gap-2 rounded-lg bg-red-50 p-2 text-sm text-red-700"><AlertTriangle size={16} /> 12 hours are over — please end your shift with the meter readings and cash.</div>}
      <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-3">
        <div className="rounded-xl bg-slate-50 p-3 text-sm">
          <div className="mb-1 font-semibold">Entered on POS so far</div>
          {s.by_product.map((p: any) => <div key={p.product} className="flex justify-between gap-2"><span>{PRODUCTS[p.product]}</span><span className="text-right tabular-nums">{num(p.litres, 1)} L · {pkr(p.amount)}</span></div>)}
          {!s.by_product.length && <div className="text-slate-500">Nothing yet. Meter litres not entered are booked as cash at the end.</div>}
        </div>
        <div className="rounded-xl bg-slate-50 p-3 text-sm">
          <div className="flex justify-between"><span>📒 Khata</span><span className="tabular-nums">{pkr(s.khata)}</span></div>
          <div className="flex justify-between"><span>📱 Digital</span><span className="tabular-nums">{pkr(s.digital)}</span></div>
          <div className="flex justify-between"><span>💵 Cash sales</span><span className="tabular-nums">{pkr(s.cash_sales)}</span></div>
          <div className="flex justify-between text-red-700"><span>− Expenses</span><span className="tabular-nums">{pkr(s.expenses_total)}</span></div>
          <div className="mt-1 flex justify-between border-t border-slate-200 pt-1 font-semibold"><span>Cash in bag (so far)</span><span className="tabular-nums">{pkr(s.cash_expected)}</span></div>
        </div>
        <div className="min-w-0 rounded-xl bg-amber-50/60 p-3"><ShiftExpenses shiftId={id} expenses={s.expenses} onChange={reload} /></div>
      </div>
    </div>
  );
}

function CloseShift({ id, onClose, onClosed }: { id: number; onClose: () => void; onClosed: (r: any) => void }) {
  const { data } = useApi<any>(`/shifts/${id}/live`);
  const [readings, setReadings] = useState<Record<string, string>>({});
  const [cash, setCash] = useState("");
  const [photos, setPhotos] = useState<number[]>([]);
  const { busy, run } = useAction();
  if (!data) return <Modal open onClose={onClose} title="End shift"><Loading /></Modal>;
  const s = data.summary;

  // estimate: meter litres not yet entered on the POS will be booked as cash at today's rate
  const meterL: Record<string, number> = {};
  for (const r of data.readings) {
    const v = Number(readings[r.nozzle_id]);
    if (readings[r.nozzle_id]) meterL[r.product] = (meterL[r.product] ?? 0) + Math.max(0, v - r.opening);
  }
  const allFilled = data.readings.every((r: any) => readings[r.nozzle_id]);
  const extraCash = Object.entries(meterL).reduce((a, [p, l]) => a + Math.max(0, l - (s.by_product.find((x: any) => x.product === p)?.litres ?? 0)) * (data.prices[p] ?? 0), 0);
  const estHandover = s.cash_expected + extraCash;
  const counted = Number(cash);

  return (
    <Modal open onClose={onClose} title={`End shift — ${data.shift.attendant}`} wide>
      <form className="space-y-4" onSubmit={async (e) => {
        e.preventDefault();
        const r = await run(() => api(`/shifts/${id}/close`, { body: { readings: Object.fromEntries(Object.entries(readings).map(([k, v]) => [k, Number(v)])), cash_actual: Number(cash), photo_ids: photos } }),
          (x: any) => x.checkout_missing ? "Shift closed · Now check out with selfie in My account · چیک آؤٹ کریں" : "Shift closed");
        if (r) onClosed(r);
      }}>
        <p className="text-sm font-medium">Step 1 — closing meter reading of every nozzle</p>
        {/* one row per nozzle: a card on phones, table-like columns from sm up */}
        <div className="divide-y divide-slate-100 rounded-lg border border-slate-200">
          <div className="hidden grid-cols-[1.4fr_1fr_2.4fr_1fr_0.9fr] sm:grid"><div className="th">Nozzle</div><div className="th text-right">Opening</div><div className="th">Closing reading</div><div className="th text-right">Litres</div><div className="th text-right">× Rate</div></div>
            {data.readings.map((r: any) => {
              const v = Number(readings[r.nozzle_id]);
              const last = r.checkpoint ?? r.opening;
              const bad = readings[r.nozzle_id] && v < last;
              return (
                <div key={r.nozzle_id} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 px-3 py-2.5 sm:grid-cols-[1.4fr_1fr_2.4fr_1fr_0.9fr] sm:px-0 sm:py-0">
                  <div className="min-w-0 text-sm font-medium sm:px-3 sm:py-2 sm:font-normal">{r.label} <span className="text-xs font-normal text-slate-500">{PRODUCTS[r.product]}</span></div>
                  <div className="col-span-2 order-3 text-xs tabular-nums text-slate-500 sm:order-none sm:col-span-1 sm:px-3 sm:py-2 sm:text-right sm:text-slate-900"><span className="sm:hidden">Opening </span>{num(r.opening, 2)}<span className="sm:hidden"> · Rs {data.prices[r.product]}/L</span>{r.checkpoint != null && <div className="text-slate-400">price change at {num(r.checkpoint, 2)}</div>}</div>
                  <div className="col-span-2 order-4 flex items-center gap-2 sm:order-none sm:col-span-1 sm:px-3 sm:py-2"><input className={`input min-w-0 flex-1 text-lg tabular-nums sm:w-40 sm:flex-none ${bad ? "border-red-400" : ""}`} type="number" step="0.01" min={last} required value={readings[r.nozzle_id] ?? ""} onChange={(e) => setReadings({ ...readings, [r.nozzle_id]: e.target.value })} aria-label={`${r.label} closing reading`} />
                    <PhotoButton kind="meter" label="Meter" className="shrink-0" hint={`Nozzle ${r.label}; opening reading was ${r.opening}.`} onRead={(res, pid) => {
                      setPhotos((p) => [...p, pid]);
                      const vs: number[] = (res?.readings ?? []).map((x: any) => Number(x.value)).filter((x: number) => x >= last && x - last < 50_000);
                      if (vs.length) setReadings((rd) => ({ ...rd, [r.nozzle_id]: String(Math.min(...vs)) }));
                    }} /></div>
                  <div className="order-2 text-right font-medium tabular-nums sm:order-none sm:px-3 sm:py-2">{readings[r.nozzle_id] && v >= r.opening ? <>{num(v - r.opening, 2)}<span className="text-xs font-normal text-slate-500 sm:hidden"> L</span></> : "—"}</div>
                  <div className="hidden text-right text-xs tabular-nums sm:block sm:px-3 sm:py-2">Rs {data.prices[r.product]}</div>
                </div>
              );
            })}
        </div>
        {allFilled && (
          <div className="grid grid-cols-1 gap-3 rounded-lg bg-slate-50 p-3 text-sm sm:grid-cols-2">
            <div>{Object.entries(meterL).map(([p, l]) => <div key={p} className="flex justify-between"><span>{PRODUCTS[p]} on meters</span><span className="tabular-nums">{num(l, 2)} L</span></div>)}</div>
            <div>
              <div className="flex justify-between"><span>📒 Khata</span><span className="tabular-nums">{pkr(s.khata)}</span></div>
              <div className="flex justify-between"><span>📱 Digital</span><span className="tabular-nums">{pkr(s.digital)}</span></div>
              <div className="flex justify-between text-red-700"><span>− Expenses from cash</span><span className="tabular-nums">{pkr(s.expenses_total)}</span></div>
              <div className="mt-1 flex justify-between border-t border-slate-200 pt-1 font-semibold"><span>≈ Cash to hand over</span><span className="tabular-nums">{pkr(estHandover)}</span></div>
            </div>
          </div>
        )}
        <p className="text-sm font-medium">Step 2 — count the cash in the bag</p>
        <Field label="Cash counted (Rs)"><input className="input text-2xl font-semibold" type="number" min={0} required value={cash} onChange={(e) => setCash(e.target.value)} /></Field>
        {allFilled && cash && <p className={`text-sm font-medium ${counted - estHandover < -500 ? "text-red-600" : "text-emerald-700"}`}>{counted - estHandover < 0 ? `≈ Rs ${Math.round(estHandover - counted).toLocaleString("en-IN")} short` : `≈ Rs ${Math.round(counted - estHandover).toLocaleString("en-IN")} over`}</p>}
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>End shift</button></div>
      </form>
    </Modal>
  );
}
