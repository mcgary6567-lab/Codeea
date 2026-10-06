import { useEffect, useMemo, useState } from "react";
import { Clock, AlertTriangle } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Field, Loading, Modal, PageHeader, statusTone, useAction } from "../components/ui";
import { PRODUCTS, dt, num, pkr } from "../lib/format";
import { useAuth } from "../App";
import { PhotoButton, photoUrl } from "../components/Capture";
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

const NOTES = [5000, 1000, 500, 100, 50, 20, 10];
const ONLINE = [["card", "Card machine", "کارڈ مشین"], ["jazzcash", "JazzCash", "جاز کیش"], ["easypaisa", "Easypaisa", "ایزی پیسہ"], ["raast", "Raast / QR", "راست"]] as const;
const rs = (v: number) => `Rs ${Math.round(v).toLocaleString("en-IN")}`;

/**
 * End of a rush day in three steps: (1) closing meters (+ litres put back after a test), (2) online money totals and any
 * khata slips not entered during the day — the server works out the cash, exactly as it will save it — (3) count the notes, close.
 */
function CloseShift({ id, onClose, onClosed }: { id: number; onClose: () => void; onClosed: (r: any) => void }) {
  const { data } = useApi<any>(`/shifts/${id}/live`);
  const accts = useApi<any[]>("/pos/khata-accounts");
  const [readings, setReadings] = useState<Record<string, string>>({});
  const [test, setTest] = useState<Record<string, string>>({});
  const [showTest, setShowTest] = useState(false);
  const [digital, setDigital] = useState<Record<string, string>>({});
  const [slips, setSlips] = useState<any[]>([]);
  const [slipForm, setSlipForm] = useState<any>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [direct, setDirect] = useState("");
  const [photos, setPhotos] = useState<number[]>([]);
  const [preview, setPreview] = useState<any>(null);
  const [perr, setPerr] = useState("");
  const { busy, run } = useAction();

  const allFilled = !!data && data.readings.every((r: any) => readings[r.nozzle_id] !== undefined && readings[r.nozzle_id] !== "" && Number(readings[r.nozzle_id]) >= (r.checkpoint ?? r.opening));
  const body = useMemo(() => ({
    readings: Object.fromEntries(Object.entries(readings).map(([k, v]) => [k, Number(v)])),
    digital: Object.fromEntries(Object.entries(digital).filter(([, v]) => Number(v) > 0).map(([k, v]) => [k, Number(v)])),
    test: Object.fromEntries(Object.entries(test).filter(([, v]) => Number(v) > 0).map(([k, v]) => [k, Number(v)])),
    khata: slips.map(({ name: _n, ...k }) => k),
  }), [readings, digital, test, slips]);
  // the server works it out (nothing saved) whenever something changes
  useEffect(() => {
    if (!allFilled) { setPreview(null); return; }
    const h = setTimeout(() => {
      api(`/shifts/${id}/preview`, { body }).then((r) => { setPreview(r); setPerr(""); }).catch((e) => { setPreview(null); setPerr(e.message); });
    }, 350);
    return () => clearTimeout(h);
  }, [JSON.stringify(body), allFilled]);

  if (!data) return <Modal open onClose={onClose} title="End shift"><Loading /></Modal>;
  const products = [...new Set<string>(data.readings.map((r: any) => r.product))];
  const notesTotal = NOTES.reduce((a, d) => a + d * (Number(notes[d]) || 0), 0);
  const counted = notesTotal > 0 ? notesTotal + (Number(direct) || 0) : Number(direct) || 0;
  const expected = preview?.cash_expected ?? null;
  const diff = expected != null ? Math.round(counted - expected) : null;

  return (
    <Modal open onClose={onClose} title={`End shift — ${data.shift.attendant}`} wide>
      <form className="space-y-5" onSubmit={async (e) => {
        e.preventDefault();
        const r = await run(() => api(`/shifts/${id}/close`, { body: { ...body, cash_actual: counted, cash_notes: Object.fromEntries(Object.entries(notes).filter(([, v]) => Number(v) > 0).map(([k, v]) => [k, Number(v)])), photo_ids: photos } }),
          (x: any) => x.checkout_missing ? "Shift closed · Now check out with selfie in My account · چیک آؤٹ کریں" : "Shift closed · شفٹ بند");
        if (r) onClosed(r);
      }}>
        {/* ---------- step 1 ---------- */}
        <section>
          <h3 className="mb-2 font-semibold">1 · Closing meter readings · <Ur>آخری میٹر ریڈنگ</Ur></h3>
          <div className="divide-y divide-slate-100 rounded-xl border border-slate-200">
            {data.readings.map((r: any) => {
              const v = Number(readings[r.nozzle_id]);
              const last = r.checkpoint ?? r.opening;
              const filled = readings[r.nozzle_id] !== undefined && readings[r.nozzle_id] !== "";
              const bad = filled && v < last;
              return (
                <div key={r.nozzle_id} className="space-y-1.5 px-3 py-2.5">
                  <div className="flex items-baseline justify-between gap-2 text-sm">
                    <span className="min-w-0 font-medium">{r.label} <span className="text-xs font-normal text-slate-500">{PRODUCTS[r.product]} · Rs {data.prices[r.product]}/L</span></span>
                    <span className="shrink-0 font-semibold tabular-nums">{filled && !bad ? `${num(v - r.opening, 2)} L` : ""}</span>
                  </div>
                  <div className="text-xs text-slate-500">Opening {num(r.opening, 2)}{r.checkpoint != null && <> · price change at {num(r.checkpoint, 2)}</>}</div>
                  <div className="flex items-center gap-2">
                    <input className={`input min-w-0 flex-1 text-lg tabular-nums ${bad ? "border-red-400" : ""}`} type="number" step="0.01" min={last} required inputMode="decimal"
                      value={readings[r.nozzle_id] ?? ""} onChange={(e) => setReadings({ ...readings, [r.nozzle_id]: e.target.value })} aria-label={`${r.label} closing reading`} placeholder="Closing reading" />
                    <PhotoButton kind="meter" label="Meter" className="shrink-0" hint={`Nozzle ${r.label}; opening reading was ${r.opening}.`} onRead={(res, pid) => {
                      setPhotos((p) => [...p, pid]);
                      const vs: number[] = (res?.readings ?? []).map((x: any) => Number(x.value)).filter((x: number) => x >= last && x - last < 50_000);
                      if (vs.length) setReadings((rd) => ({ ...rd, [r.nozzle_id]: String(Math.min(...vs)) }));
                    }} />
                  </div>
                  {bad && <p className="text-xs font-medium text-red-600">Cannot be less than {num(last, 2)}</p>}
                  {showTest && <label className="flex items-center gap-2 text-sm"><span className="shrink-0 text-slate-600">Test / back to tank · <Ur>ٹیسٹ</Ur></span>
                    <input className="input min-h-9 w-24 !py-1 text-right" type="number" min={0} step="0.01" inputMode="decimal" value={test[r.nozzle_id] ?? ""} onChange={(e) => setTest({ ...test, [r.nozzle_id]: e.target.value })} aria-label={`${r.label} test litres`} /> L</label>}
                </div>
              );
            })}
          </div>
          {!showTest && <button type="button" className="mt-1 py-2 text-sm text-brand-700 underline" onClick={() => setShowTest(true)}>+ Litres put back in the tank after a test · <Ur>ٹیسٹ کا تیل واپس</Ur></button>}
        </section>

        {/* ---------- step 2 ---------- */}
        <section>
          <h3 className="mb-2 font-semibold">2 · Online money & khata slips · <Ur>آن لائن رقم اور کھاتہ پرچی</Ur></h3>
          <p className="mb-2 text-xs text-slate-500">Totals for the whole shift — e.g. the card machine's settlement slip, or the JazzCash / Easypaisa / Raast received today. Leave empty if none.</p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {ONLINE.map(([k, en, ur]) => (
              <label key={k} className="block rounded-xl bg-slate-50 p-2 text-xs text-slate-600">{en} · <Ur>{ur}</Ur>
                <input className="input mt-1 min-h-9 text-right tabular-nums" type="number" min={0} inputMode="numeric" placeholder="Rs" value={digital[k] ?? ""} onChange={(e) => setDigital({ ...digital, [k]: e.target.value })} aria-label={`${en} total`} />
              </label>
            ))}
          </div>
          <div className="mt-3 rounded-xl border border-slate-200">
            <div className="flex items-center justify-between gap-2 px-3 py-2">
              <span className="text-sm font-medium">Khata slips not entered yet · <Ur>رہ جانے والی کھاتہ پرچیاں</Ur></span>
              <button type="button" className="btn-secondary min-h-9 shrink-0 whitespace-nowrap !px-3 !py-1 text-sm" onClick={() => setSlipForm({ customer_id: "", product: products[0], litres: "", vehicle_no: "", slip_no: "" })}>+ Add · <Ur>شامل</Ur></button>
            </div>
            {slips.length > 0 && <ul className="divide-y divide-slate-100 border-t border-slate-100">{slips.map((k, i) => (
              <li key={i} className="flex items-center gap-2 px-3 py-2 text-sm">
                {k.photo_id && <img src={photoUrl(k.photo_id)} alt="Slip photo" className="h-9 w-9 shrink-0 rounded border border-slate-200 object-cover" />}
                <span className="min-w-0 flex-1"><b>{k.name}</b><span className="block text-xs text-slate-500">{PRODUCTS[k.product]} {num(k.litres, 2)} L · {k.vehicle_no} · slip {k.slip_no}</span></span>
                <button type="button" className="min-h-9 px-2 text-red-600" onClick={() => setSlips(slips.filter((_, j) => j !== i))} aria-label="Remove slip">✕</button>
              </li>
            ))}</ul>}
            {slipForm && (
              <div className="grid grid-cols-2 gap-2 border-t border-slate-100 p-3">
                <div className="col-span-2 flex items-center gap-2">
                  {slipForm.photo_id && <img src={photoUrl(slipForm.photo_id)} alt="Slip photo" className="h-12 w-12 shrink-0 rounded-lg border border-slate-200 object-cover" />}
                  <PhotoButton kind="slip" className="flex-1" label={slipForm.photo_id ? "Retake photo · دوبارہ" : "Photo of slip · پرچی کی تصویر"} onRead={(r, id) => setSlipForm((f: any) => ({
                    ...f, photo_id: id,
                    // fill in what the photo shows, only where nothing is typed yet
                    slip_no: f.slip_no || (r?.slip_no ? String(r.slip_no) : ""),
                    vehicle_no: f.vehicle_no || (r?.vehicle_no ? String(r.vehicle_no).toUpperCase() : ""),
                    litres: f.litres || (Number(r?.litres) > 0 ? String(r.litres) : ""),
                    product: r?.product && products.includes(r.product) && !f.litres ? r.product : f.product,
                  }))} />
                </div>
                <select className="input col-span-2" value={slipForm.customer_id} onChange={(e) => setSlipForm({ ...slipForm, customer_id: e.target.value })} aria-label="Khata customer">
                  <option value="">— customer · گاہک —</option>{(accts.data ?? []).map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
                <select className="input" value={slipForm.product} onChange={(e) => setSlipForm({ ...slipForm, product: e.target.value })} aria-label="Fuel">{products.map((p) => <option key={p} value={p}>{PRODUCTS[p]}</option>)}</select>
                <input className="input" type="number" min={0} step="0.01" inputMode="decimal" placeholder="Litres" value={slipForm.litres} onChange={(e) => setSlipForm({ ...slipForm, litres: e.target.value })} aria-label="Litres" />
                <input className="input uppercase" placeholder="Vehicle no. · گاڑی" value={slipForm.vehicle_no} onChange={(e) => setSlipForm({ ...slipForm, vehicle_no: e.target.value })} aria-label="Vehicle number" />
                <input className="input" placeholder="Slip no. · پرچی" value={slipForm.slip_no} onChange={(e) => setSlipForm({ ...slipForm, slip_no: e.target.value })} aria-label="Slip number" />
                <div className="col-span-2 flex justify-end gap-2">
                  <button type="button" className="btn-secondary min-h-9" onClick={() => setSlipForm(null)}>Cancel</button>
                  <button type="button" className="btn-primary min-h-9" disabled={!slipForm.customer_id || !(Number(slipForm.litres) > 0) || slipForm.vehicle_no.trim().length < 2 || !slipForm.slip_no.trim()}
                    onClick={() => { const c = (accts.data ?? []).find((a: any) => String(a.id) === String(slipForm.customer_id)); setSlips([...slips, { customer_id: Number(slipForm.customer_id), name: c?.name, product: slipForm.product, litres: Number(slipForm.litres), vehicle_no: slipForm.vehicle_no.trim().toUpperCase(), slip_no: slipForm.slip_no.trim(), photo_id: slipForm.photo_id ?? null }]); setSlipForm(null); }}>Add slip</button>
                </div>
              </div>
            )}
          </div>

          {/* the server's working */}
          {!allFilled ? <p className="mt-3 rounded-xl bg-slate-50 p-3 text-sm text-slate-500">Enter every closing reading to see the cash · <Ur>تمام ریڈنگ ڈالیں</Ur></p>
            : perr ? <p className="mt-3 flex gap-2 rounded-xl bg-red-50 p-3 text-sm text-red-700"><AlertTriangle size={16} className="mt-0.5 shrink-0" />{perr}</p>
            : !preview ? <div className="mt-3"><Loading /></div> : (
              <div className="mt-3 space-y-2">
                {preview.fuels.filter((f: any) => f.meter_l > 0).map((f: any) => (
                  <div key={f.product} className="rounded-xl bg-slate-50 p-3 text-sm">
                    <div className="mb-1 font-semibold">{PRODUCTS[f.product]}</div>
                    <Row k="Meter · میٹر" v={`${num(f.meter_l, 2)} L`} />
                    {f.test_l > 0 && <Row k="− Test / back to tank" v={`${num(f.test_l, 2)} L`} />}
                    {f.khata_l > 0 && <Row k="− Khata · کھاتہ" v={`${num(f.khata_l, 2)} L`} />}
                    {f.digital_l > 0 && <Row k="− Online · آن لائن" v={`${num(f.digital_l, 2)} L (${rs(f.digital)})`} />}
                    {f.other_l > 0 && <Row k="− Coupon / wallet / points" v={`${num(f.other_l, 2)} L`} />}
                    <div className="mt-1 flex justify-between border-t border-slate-200 pt-1 font-semibold"><span>= Cash · <Ur>نقد</Ur><span className="block text-xs font-normal text-slate-500">{num(f.cash_l, 2)} L × {f.rate}</span></span><span className="tabular-nums">{rs(f.cash)}</span></div>
                  </div>
                ))}
                <div className="rounded-xl bg-slate-800 p-3 text-white">
                  <Row k="Cash sales (fuel + shop)" v={rs(preview.summary.cash_sales)} />
                  {preview.summary.expenses_total > 0 && <Row k="− Expenses paid from the bag · خرچے" v={rs(preview.summary.expenses_total)} />}
                  <div className="mt-1 flex items-baseline justify-between gap-2 border-t border-white/30 pt-1 text-lg font-bold"><span className="min-w-0">Should be in the bag · <Ur>تھیلے میں</Ur></span><span className="shrink-0 whitespace-nowrap tabular-nums">{rs(preview.cash_expected)}</span></div>
                </div>
              </div>
            )}
        </section>

        {/* ---------- step 3 ---------- */}
        <section>
          <h3 className="mb-2 font-semibold">3 · Count the cash · <Ur>کیش گنیں</Ur></h3>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {NOTES.map((d) => (
              <label key={d} className="flex items-center gap-2 rounded-xl bg-slate-50 px-2 py-1.5">
                <span className="w-12 shrink-0 whitespace-nowrap text-right text-sm font-semibold tabular-nums">{d}</span><span className="text-slate-400">×</span>
                <input className="input min-h-9 min-w-0 !py-1 text-center" type="number" min={0} inputMode="numeric" value={notes[d] ?? ""} onChange={(e) => setNotes({ ...notes, [d]: e.target.value })} aria-label={`Rs ${d} notes`} />
              </label>
            ))}
            <label className="flex items-center gap-2 rounded-xl bg-slate-50 px-2 py-1.5"><span className="w-14 shrink-0 text-right text-sm font-semibold">{notesTotal > 0 ? "Coins" : "Total"}</span><span className="text-slate-400">=</span>
              <input className="input min-h-9 !py-1 text-center" type="number" min={0} inputMode="numeric" value={direct} onChange={(e) => setDirect(e.target.value)} aria-label={notesTotal > 0 ? "Coins" : "Cash total"} /></label>
          </div>
          <div className="mt-2 rounded-xl bg-white p-3 ring-1 ring-slate-200">
            <div className="flex items-baseline justify-between text-xl font-bold"><span>Counted · <Ur>گنا</Ur></span><span className="tabular-nums">{rs(counted)}</span></div>
            {diff != null && counted > 0 && <div className={`text-right text-sm font-semibold ${diff < -100 ? "text-red-600" : "text-emerald-700"}`}>{diff === 0 ? "Matches · درست" : diff < 0 ? `Short ${rs(-diff)} · کم` : `Over ${rs(diff)} · زیادہ`}</div>}
          </div>
        </section>

        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button type="button" className="btn-secondary min-h-11" onClick={onClose}>Cancel · منسوخ</button>
          <button className="min-h-12 rounded-xl bg-emerald-600 px-6 py-3 text-lg font-bold text-white shadow active:scale-[.98] disabled:bg-slate-300" disabled={busy || !preview || counted <= 0}>
            Close shift & hand in cash · <Ur>شفٹ بند کریں</Ur>
          </button>
        </div>
      </form>
    </Modal>
  );
}
const Row = ({ k, v }: { k: string; v: string }) => <div className="flex justify-between gap-3"><span className="min-w-0">{k}</span><span className="shrink-0 tabular-nums">{v}</span></div>;
