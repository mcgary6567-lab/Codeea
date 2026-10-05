import { useState } from "react";
import { Zap, Repeat, Trash2 } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Badge, Empty, Field, Loading, Modal, useAction } from "./ui";
import { pkr } from "../lib/format";
import { useAuth } from "../App";
import { PhotoButton, photoUrl } from "./Capture";

const thisMonth = () => new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 7);
const KIND: Record<string, string> = { electricity: "⚡ Bijli", gas: "🔥 Gas", water: "💧 Water", phone: "☎️ Phone / internet" };

/** Fixed monthly costs that book themselves, and utility bills read from a photo (with a warning when higher than last month). */
export function FixedCosts({ categories, stations, onClose }: { categories: any[]; stations: any[]; onClose: () => void }) {
  const rec = useApi<any[]>("/recurring-expenses");
  const bills = useApi<any[]>("/utility-bills");
  const { can } = useAuth();
  const { busy, run } = useAction();
  const [r, setR] = useState({ category: "Rent", amount: "", paid_to: "", day_of_month: "1", method: "bank", station_id: "" });
  const [b, setB] = useState({ kind: "electricity", month: thisMonth(), units: "", amount: "", reference: "", station_id: String(stations[0]?.id ?? ""), photo_id: null as number | null });
  const [result, setResult] = useState<any>(null);
  return (
    <Modal open wide onClose={onClose} title="Monthly costs & utility bills">
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <section>
          <h3 className="mb-1 flex items-center gap-2 font-semibold"><Repeat size={16} /> Booked every month by themselves</h3>
          <p className="mb-2 text-xs text-slate-500">Rent, guard, internet… On its day each month the expense is added as approved; managers get a note.</p>
          {!rec.data ? <Loading /> : <ul className="divide-y divide-slate-100 text-sm">
            {rec.data.map((x) => (
              <li key={x.id} className={`flex items-center gap-2 py-2 ${x.active ? "" : "opacity-50"}`}>
                <span className="flex-1"><b>{x.category}</b>{x.paid_to ? ` → ${x.paid_to}` : ""}<span className="block text-xs text-slate-500">Day {x.day_of_month} · {x.method}{x.station_name ? ` · ${x.station_name}` : ""}{x.last_month ? ` · last booked ${x.last_month}` : ""}</span></span>
                <span className="font-semibold tabular-nums">{pkr(x.amount)}</span>
                {can("expenses.approve") && <>
                  <button className="text-xs text-brand-600 underline" onClick={() => run(() => api(`/recurring-expenses/${x.id}`, { method: "PATCH", body: { active: !x.active } }), x.active ? "Paused" : "Started").then(rec.reload)}>{x.active ? "Pause" : "Start"}</button>
                  <button aria-label="Delete" className="text-slate-400 hover:text-red-600" onClick={() => confirm("Delete this monthly expense?") && run(() => api(`/recurring-expenses/${x.id}`, { method: "DELETE" }), "Deleted").then(rec.reload)}><Trash2 size={14} /></button>
                </>}
              </li>
            ))}
            {!rec.data.length && <Empty>None yet</Empty>}
          </ul>}
          {can("expenses.approve") && <form className="mt-3 grid grid-cols-2 gap-2 rounded-lg bg-slate-50 p-3" onSubmit={async (e) => {
            e.preventDefault();
            if (await run(() => api("/recurring-expenses", { body: { category: r.category, amount: Number(r.amount), paid_to: r.paid_to || null, day_of_month: Number(r.day_of_month), method: r.method, station_id: r.station_id ? Number(r.station_id) : null } }), "Added — it will be booked by itself")) { setR({ ...r, amount: "", paid_to: "" }); rec.reload(); }
          }}>
            <Field label="Category"><select className="input" value={r.category} onChange={(e) => setR({ ...r, category: e.target.value })}>{categories.map((c) => <option key={c.id}>{c.name}</option>)}</select></Field>
            <Field label="Amount (Rs)"><input className="input" type="number" min={1} required value={r.amount} onChange={(e) => setR({ ...r, amount: e.target.value })} /></Field>
            <Field label="Paid to"><input className="input" value={r.paid_to} onChange={(e) => setR({ ...r, paid_to: e.target.value })} /></Field>
            <Field label="Day of month"><input className="input" type="number" min={1} max={31} value={r.day_of_month} onChange={(e) => setR({ ...r, day_of_month: e.target.value })} /></Field>
            <Field label="Paid by"><select className="input" value={r.method} onChange={(e) => setR({ ...r, method: e.target.value })}>{["bank", "cash", "cheque", "raast", "easypaisa", "jazzcash", "card"].map((m) => <option key={m}>{m}</option>)}</select></Field>
            <Field label="Station"><select className="input" value={r.station_id} onChange={(e) => setR({ ...r, station_id: e.target.value })}><option value="">All</option>{stations.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
            <button className="btn-primary col-span-2" disabled={busy}>Add monthly expense</button>
          </form>}
        </section>
        <section>
          <h3 className="mb-1 flex items-center gap-2 font-semibold"><Zap size={16} /> Bijli / gas bill</h3>
          <p className="mb-2 text-xs text-slate-500">Take a photo of the bill — units and amount are read by AI. If it is more than 15% above last month, the owner is told.</p>
          <form className="grid grid-cols-2 gap-2 rounded-lg bg-slate-50 p-3" onSubmit={async (e) => {
            e.preventDefault();
            const x = await run(() => api("/utility-bills", { body: { kind: b.kind, month: b.month, units: b.units ? Number(b.units) : null, amount: b.amount ? Number(b.amount) : null, reference: b.reference || null, station_id: b.station_id ? Number(b.station_id) : null, photo_id: b.photo_id } }),
              (y: any) => y.high ? `Saved — bill is ${y.change_pct}% higher than last month, owner told` : "Bill saved and added to expenses");
            if (x) { setResult(x); setB({ ...b, units: "", amount: "", reference: "", photo_id: null }); bills.reload(); }
          }}>
            <div className="col-span-2 flex items-center gap-2"><PhotoButton kind="bill" label="Photo of bill" onRead={(res, id) => setB((v) => ({ ...v, photo_id: id, units: res?.units != null ? String(res.units) : v.units, amount: res?.amount ? String(res.amount) : v.amount, reference: res?.reference ?? v.reference, month: /^\d{4}-\d{2}$/.test(res?.month ?? "") ? res.month : v.month }))} />
              <span className="text-xs text-slate-600">{b.photo_id ? "📷 Bill attached" : "or type the numbers"}</span></div>
            <Field label="Bill"><select className="input" value={b.kind} onChange={(e) => setB({ ...b, kind: e.target.value })}>{Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
            <Field label="Month"><input className="input" type="month" value={b.month} onChange={(e) => setB({ ...b, month: e.target.value })} /></Field>
            <Field label="Units"><input className="input" type="number" min={0} value={b.units} onChange={(e) => setB({ ...b, units: e.target.value })} /></Field>
            <Field label="Amount (Rs)"><input className="input" type="number" min={1} value={b.amount} onChange={(e) => setB({ ...b, amount: e.target.value })} /></Field>
            <Field label="Reference no."><input className="input" value={b.reference} onChange={(e) => setB({ ...b, reference: e.target.value })} /></Field>
            <Field label="Station"><select className="input" value={b.station_id} onChange={(e) => setB({ ...b, station_id: e.target.value })}><option value="">All</option>{stations.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
            <button className="btn-primary col-span-2" disabled={busy || (!b.amount && !b.photo_id)}>Save bill</button>
          </form>
          {result?.high && <div className="mt-2 rounded-lg bg-amber-50 p-2 text-sm text-amber-900 ring-1 ring-amber-200">⚠️ {pkr(result.amount)} is {result.change_pct}% more than last month ({pkr(result.prev_amount)}). Check for a fault, wrong reading or extra load.</div>}
          {!bills.data ? <Loading /> : <ul className="mt-3 divide-y divide-slate-100 text-sm">
            {bills.data.map((x) => (
              <li key={x.id} className="flex items-center gap-2 py-2">
                <span className="flex-1">{KIND[x.kind]} <b>{x.month}</b><span className="block text-xs text-slate-500">{x.station_name ?? "All"}{x.units != null ? ` · ${x.units.toLocaleString()} units` : ""}{x.reference ? ` · ${x.reference}` : ""}</span></span>
                {x.photo_id && <a href={photoUrl(x.photo_id)} target="_blank" rel="noreferrer" className="text-sky-700" aria-label="Bill photo">📷</a>}
                {x.change_pct != null && <Badge tone={x.change_pct > 15 ? "red" : x.change_pct < 0 ? "green" : "slate"}>{x.change_pct > 0 ? "+" : ""}{x.change_pct}%</Badge>}
                <span className="font-semibold tabular-nums">{pkr(x.amount)}</span>
              </li>
            ))}
          </ul>}
        </section>
      </div>
    </Modal>
  );
}
