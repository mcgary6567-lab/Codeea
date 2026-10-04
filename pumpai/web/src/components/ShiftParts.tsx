import { useEffect, useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Printer, Trash2 } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Field, Loading, Modal, useAction } from "./ui";
import { PhotoButton, photoUrl } from "./Capture";
import { PRODUCTS, ago, dt, num, pkr } from "../lib/format";

const Ur = ({ children }: { children: ReactNode }) => <span lang="ur" dir="rtl" className="font-urdu">{children}</span>;

/**
 * Shift handover sheet: for each nozzle, the previous shift's closing reading is the starting point.
 * The salesman checks the meter, corrects the reading if it differs, and ticks the nozzles they run.
 */
export function StartShiftSheet({ stationId, attendant, onStarted, big }: { stationId?: number; attendant?: string; onStarted: (s: any) => void; big?: boolean }) {
  const { data } = useApi<any>(`/shifts/handover${stationId ? `?station_id=${stationId}` : ""}`);
  const [vals, setVals] = useState<Record<number, string>>({});
  const [use, setUse] = useState<Record<number, boolean>>({});
  const [photos, setPhotos] = useState<number[]>([]);
  const { busy, run } = useAction();
  /** a meter photo may show several totalizers: take the one that fits this nozzle's last reading */
  const fromPhoto = (n: any) => (r: any, id: number) => {
    setPhotos((p) => [...p, id]);
    const vs: number[] = (r?.readings ?? []).map((x: any) => Number(x.value)).filter((v: number) => v >= n.last_reading && v - n.last_reading < 50_000);
    if (vs.length) setVals((v) => ({ ...v, [n.nozzle_id]: String(Math.min(...vs)) }));
  };
  useEffect(() => {
    if (!data) return;
    setVals(Object.fromEntries(data.nozzles.map((n: any) => [n.nozzle_id, String(n.last_reading)])));
    setUse(Object.fromEntries(data.nozzles.map((n: any) => [n.nozzle_id, !n.busy])));
  }, [data]);
  if (!data) return <Loading />;
  const chosen = data.nozzles.filter((n: any) => use[n.nozzle_id] && !n.busy);
  const gaps = chosen.map((n: any) => ({ n, gap: Number(vals[n.nozzle_id]) - n.last_reading })).filter((g: any) => Math.abs(g.gap) > 0.009);
  const last = data.nozzles.find((n: any) => n.handed_over_by);

  const start = async () => {
    const readings = Object.fromEntries(chosen.map((n: any) => [n.nozzle_id, Number(vals[n.nozzle_id])]));
    const r: any = await run(() => api("/shifts/open", { body: { station_id: data.station_id, attendant, readings, photo_ids: photos } }),
      (x: any) => x.handover_gaps?.length ? `Shift started. Manager alerted about ${x.handover_gaps.reduce((a: number, g: any) => a + g.litres, 0)} L meter gap.` : "Shift started");
    if (r) onStarted(r);
  };

  return (
    <div className="space-y-3 text-left">
      {last && <p className="text-sm text-slate-600">Handed over by <b>{last.handed_over_by}</b> {ago(last.handed_over_at)}. Check each meter and correct the reading if it is different.</p>}
      <div className="space-y-2">
        {data.nozzles.map((n: any) => {
          const gap = Number(vals[n.nozzle_id]) - n.last_reading;
          return (
            <div key={n.nozzle_id} className={`flex flex-wrap items-center gap-3 rounded-xl border p-3 ${n.busy ? "border-slate-200 bg-slate-50 opacity-60" : use[n.nozzle_id] ? "border-emerald-300 bg-emerald-50/40" : "border-slate-200"}`}>
              <label className="flex min-w-[160px] flex-1 items-center gap-3">
                <input type="checkbox" className="h-6 w-6" disabled={n.busy} checked={!!use[n.nozzle_id] && !n.busy} onChange={(e) => setUse({ ...use, [n.nozzle_id]: e.target.checked })} />
                <span><span className={`block font-semibold ${big ? "text-lg" : ""}`}>{n.label} · {PRODUCTS[n.product]}</span>
                  <span className="text-xs text-slate-500">{n.busy ? "In use by another open shift" : `Last closing: ${num(n.last_reading, 2)}`}</span></span>
              </label>
              {!n.busy && use[n.nozzle_id] && (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-slate-500">Meter now</span>
                  <input className={`input w-40 tabular-nums ${big ? "py-3 text-xl" : ""} ${gap < 0 ? "border-red-400" : gap > 0.009 ? "border-amber-400" : ""}`} type="number" step="0.01" min={n.last_reading}
                    value={vals[n.nozzle_id] ?? ""} onChange={(e) => setVals({ ...vals, [n.nozzle_id]: e.target.value })} aria-label={`${n.label} opening reading`} />
                  <PhotoButton kind="meter" label="Meter" big={big} hint={`Nozzle ${n.label}; last closing reading was ${n.last_reading}.`} onRead={fromPhoto(n)} />
                </div>
              )}
              {!n.busy && use[n.nozzle_id] && gap > 0.009 && <div className="w-full text-sm font-medium text-amber-700">⚠ {num(gap, 2)} L more than the last closing — the manager will be alerted.</div>}
              {!n.busy && use[n.nozzle_id] && gap < -0.009 && <div className="w-full text-sm font-medium text-red-600">Reading cannot be less than the last closing.</div>}
            </div>
          );
        })}
      </div>
      {photos.length > 0 && <p className="text-xs text-slate-500">📷 {photos.length} meter photo{photos.length === 1 ? "" : "s"} will be kept with this shift.</p>}
      {gaps.length > 0 && <p className="rounded-lg bg-amber-50 p-2 text-xs text-amber-800">Litres pumped between shifts are taken out of tank stock and reported to the manager.</p>}
      <button className={`w-full rounded-xl bg-emerald-600 font-bold text-white active:scale-95 disabled:bg-slate-300 ${big ? "py-5 text-2xl" : "py-3 text-lg"}`}
        disabled={busy || !chosen.length || gaps.some((g: any) => g.gap < 0)} onClick={start}>
        Start shift · <Ur>شفٹ شروع کریں</Ur> ({chosen.length} nozzle{chosen.length === 1 ? "" : "s"})
      </button>
    </div>
  );
}

const QUICK_EXP = [["Tea & food", "☕"], ["Generator fuel", "⚡"], ["Maintenance & repairs", "🔧"], ["Other", "📝"]];

/** Expenses paid from the shift's cash. */
export function ShiftExpenses({ shiftId, expenses, onChange }: { shiftId: number; expenses: any[]; onChange: () => void }) {
  const cats = useApi<string[]>("/shifts/expense-categories");
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ category: "Tea & food", amount: "", note: "", photo_id: null as number | null });
  const { busy, run } = useAction();
  const total = expenses.reduce((a, e) => a + e.amount, 0);
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="font-semibold">Expenses from cash · <Ur>خرچہ</Ur></span>
        <button className="rounded-lg bg-amber-500 px-3 py-1.5 text-sm font-semibold text-white" onClick={() => setOpen(true)}>+ Add</button>
      </div>
      <ul className="space-y-1 text-sm">
        {expenses.map((e) => (
          <li key={e.id} className="flex items-center gap-2">
            <span className="flex-1">{e.category}{e.note ? <span className="text-xs text-slate-500"> · {e.note}</span> : null}{e.status === "pending" && <span className="ml-1 text-xs text-amber-600">(awaiting approval)</span>}</span>
            <span className="tabular-nums">{pkr(e.amount)}</span>
            <button aria-label="Remove expense" className="text-slate-400 hover:text-red-600" onClick={() => run(() => api(`/shifts/${shiftId}/expenses/${e.id}`, { method: "DELETE" }), "Removed").then(onChange)}><Trash2 size={14} /></button>
          </li>
        ))}
        {!expenses.length && <li className="text-slate-500">None</li>}
        {expenses.length > 0 && <li className="flex justify-between border-t border-slate-200 pt-1 font-semibold"><span>Total</span><span className="tabular-nums">{pkr(total)}</span></li>}
      </ul>
      <Modal open={open} onClose={() => setOpen(false)} title="Expense paid from shift cash">
        <form className="space-y-3" onSubmit={async (e) => {
          e.preventDefault();
          if (await run(() => api(`/shifts/${shiftId}/expenses`, { body: { category: f.category, amount: Number(f.amount), note: f.note || null, photo_id: f.photo_id } }), (r: any) => r.expense.status === "pending" ? "Saved — waiting for manager approval" : "Expense saved")) {
            setOpen(false); setF({ ...f, amount: "", note: "", photo_id: null }); onChange();
          }
        }}>
          <div className="flex items-center gap-2 rounded-lg bg-sky-50 p-2 text-sm">
            <PhotoButton kind="receipt" label="Photo of bill" onRead={(r, id) => setF((x) => ({
              ...x, photo_id: id, amount: r?.amount ? String(r.amount) : x.amount,
              category: r?.category && (cats.data ?? []).includes(r.category) ? r.category : x.category,
              note: [r?.description, r?.paid_to].filter(Boolean).join(" · ") || x.note,
            }))} />
            <span className="text-slate-600">{f.photo_id ? "📷 Bill photo attached" : "Have a bill? Take its photo."}</span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            {QUICK_EXP.map(([c, icon]) => (
              <button type="button" key={c} aria-pressed={f.category === c} onClick={() => setF({ ...f, category: c })}
                className={`rounded-xl border-2 p-3 text-left text-base font-medium ${f.category === c ? "border-amber-500 bg-amber-50" : "border-slate-200"}`}>{icon} {c}</button>
            ))}
          </div>
          <Field label="Other category"><select className="input" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{(cats.data ?? []).map((c) => <option key={c}>{c}</option>)}</select></Field>
          <Field label="Amount (Rs)"><input className="input py-3 text-2xl" type="number" inputMode="numeric" min={1} required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
          <Field label="What for?"><input className="input" placeholder="e.g. chai, generator diesel 5 L" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
          <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={() => setOpen(false)}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
        </form>
      </Modal>
    </div>
  );
}

/** Full shift report / receipt. */
export function ShiftReport({ id, onClose }: { id: number; onClose: () => void }) {
  const { data: r } = useApi<any>(`/shifts/${id}/report`);
  return (
    <Modal open onClose={onClose} title={`Shift report #${id}`} wide>
      {!r ? <Loading /> : <ReportBody r={r} />}
      <div className="mt-4 flex justify-end gap-2 print:hidden"><button className="btn-secondary" onClick={() => window.print()}><Printer size={15} /> Print</button><button className="btn-primary" onClick={onClose}>Done</button></div>
    </Modal>
  );
}

function ReportBody({ r }: { r: any }) {
  const s = r.summary;
  const sh = r.shift;
  const closed = sh.status === "closed";
  const hours = (Date.parse(sh.closed_at ?? new Date().toISOString()) - Date.parse(sh.opened_at)) / 3600_000;
  const Row = ({ k, v, b, c }: { k: ReactNode; v: ReactNode; b?: boolean; c?: string }) => (
    <div className={`flex justify-between border-b border-slate-100 py-1.5 text-sm ${b ? "font-semibold" : ""} ${c ?? ""}`}><span>{k}</span><span className="tabular-nums">{v}</span></div>
  );
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap justify-between gap-2 text-sm">
        <div><div className="text-lg font-bold">{sh.attendant}</div><div className="text-slate-600">{sh.station_name}</div></div>
        <div className="text-right text-slate-600"><div>{dt(sh.opened_at)} → {closed ? dt(sh.closed_at) : "still open"}</div><div>{Math.floor(hours)}h {Math.round((hours % 1) * 60)}m</div></div>
      </div>
      {closed && (
        <div className={`flex items-center gap-2 rounded-lg p-3 ${sh.variance < -500 ? "bg-red-50 text-red-800" : "bg-emerald-50 text-emerald-800"}`}>
          {sh.variance < -500 ? <AlertTriangle size={18} /> : <CheckCircle2 size={18} />}
          <span className="font-medium">{sh.variance < 0 ? `Cash short ${pkr(-sh.variance)}` : sh.variance > 0 ? `Cash over ${pkr(sh.variance)}` : "Cash matches exactly"}</span>
        </div>
      )}
      {r.handover_gaps.length > 0 && <div className="rounded-lg bg-amber-50 p-2 text-sm text-amber-800">⚠ Meter gap at handover: {r.handover_gaps.map((g: any) => `${g.label} ${num(g.litres, 2)} L`).join(", ")} (not billed)</div>}

      <div>
        <h3 className="mb-1 text-sm font-semibold">Meter readings</h3>
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table className="w-full"><thead><tr>{["Nozzle", "Handed over", "Opening", "Closing", "Litres"].map((h, i) => <th key={h} className={`th ${i > 1 ? "text-right" : ""}`}>{h}</th>)}</tr></thead>
            <tbody>{r.readings.map((x: any) => (
              <tr key={x.nozzle_id}><td className="td text-sm">{x.label} <span className="text-xs text-slate-500">{PRODUCTS[x.product]}</span></td>
                <td className="td text-xs text-slate-500">{x.handover_prev != null ? num(x.handover_prev, 2) : "—"}</td>
                <td className="td text-right tabular-nums">{num(x.opening, 2)}</td><td className="td text-right tabular-nums">{x.closing != null ? num(x.closing, 2) : "—"}</td>
                <td className="td text-right font-medium tabular-nums">{x.litres != null ? num(x.litres, 2) : "—"}</td></tr>
            ))}</tbody></table>
        </div>
        {r.photos?.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-2">
            {r.photos.map((p: any) => (
              <a key={p.id} href={photoUrl(p.id)} target="_blank" rel="noreferrer" className="block text-center text-xs text-slate-500">
                <img src={photoUrl(p.id)} alt={`Meter photo at ${p.ref.startsWith("shift-open") ? "start" : "end"}`} className="h-20 w-28 rounded-lg border border-slate-200 object-cover" />
                {p.ref.startsWith("shift-open") ? "At start" : "At end"}
              </a>
            ))}
          </div>
        )}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <h3 className="mb-1 text-sm font-semibold">Fuel sold (litres × rate)</h3>
          {r.by_rate.map((x: any) => <Row key={x.product + x.rate} k={`${PRODUCTS[x.product]} ${num(x.litres, 2)} L × Rs ${x.rate}`} v={pkr(x.amount)} />)}
          <Row k={`Total ${num(s.litres, 2)} L`} v={pkr(s.amount)} b />
        </div>
        <div>
          <h3 className="mb-1 text-sm font-semibold">Where the money went</h3>
          <Row k="📒 Khata (credit)" v={pkr(s.khata)} />
          {s.by_payment.filter((p: any) => ["easypaisa", "jazzcash", "card", "raast"].includes(p.method)).map((p: any) => <Row key={p.method} k={<span className="capitalize">📱 {p.method}</span>} v={pkr(p.amount)} />)}
          <Row k="💵 Cash sales" v={pkr(s.cash_sales)} />
          <Row k="− Expenses paid from cash" v={pkr(s.expenses_total)} c="text-red-700" />
          <Row k="= Cash to hand over" v={pkr(s.cash_expected)} b />
          {closed && <Row k="Cash counted" v={pkr(sh.cash_actual)} b />}
        </div>
      </div>

      {r.khata.length > 0 && (
        <div>
          <h3 className="mb-1 text-sm font-semibold">Khata accounts</h3>
          <table className="w-full"><tbody>{r.khata.map((k: any) => (
            <tr key={k.id}><td className="td text-sm">{k.name}<div className="text-xs text-slate-500">{k.slips} slip{k.slips > 1 ? "s" : ""}{k.slip_nos.length ? `: ${k.slip_nos.join(", ")}` : ""}</div></td>
              <td className="td text-right tabular-nums">{num(k.litres, 2)} L</td><td className="td text-right tabular-nums">{pkr(k.amount)}</td></tr>
          ))}</tbody></table>
        </div>
      )}
      {s.expenses.length > 0 && (
        <div>
          <h3 className="mb-1 text-sm font-semibold">Expenses</h3>
          {s.expenses.map((e: any) => <Row key={e.id} k={<>{e.category}{e.note ? <span className="text-xs text-slate-500"> · {e.note}</span> : null}{e.status === "pending" ? <span className="text-xs text-amber-600"> (awaiting approval)</span> : null}</>} v={pkr(e.amount)} />)}
        </div>
      )}
    </div>
  );
}
