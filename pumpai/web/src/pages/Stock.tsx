import { useState } from "react";
import { api, useApi } from "../lib/api";
import { Field, Loading, PageHeader, useAction } from "../components/ui";
import { PRODUCT_COLORS, dt, num } from "../lib/format";

export default function Stock() {
  const dash = useApi<any>("/dashboard");
  const stock = useApi<any>("/stock");
  const suppliers = useApi<any[]>("/suppliers");
  const { busy, run } = useAction();
  const [dip, setDip] = useState({ tank_id: "", measured_l: "" });
  const [del, setDel] = useState({ tank_id: "", invoice_l: "", received_l: "", tanker_no: "", supplier_id: "", purchase_rate: "" });
  if (!dash.data || !stock.data) return <Loading />;
  const tanks = dash.data.tanks;
  const refresh = () => { dash.reload(); stock.reload(); };
  const tankOpts = tanks.map((t: any) => <option key={t.id} value={t.id}>{t.station_name.replace("Al-Madina ", "")} · {t.name}</option>);

  return (
    <div className="space-y-5">
      <PageHeader title="Tanks & stock" subtitle="Wet-stock reconciliation: dip vs book stock, tanker short-delivery detection and AI stock-out forecasts" />
      <div className="grid gap-3 md:grid-cols-3 lg:grid-cols-5">
        {tanks.map((t: any) => (
          <div key={t.id} className="card p-3">
            <div className="text-xs text-slate-500">{t.station_name.replace("Al-Madina ", "")}</div>
            <div className="font-medium">{t.name}</div>
            <div className="relative mx-auto my-3 h-28 w-16 overflow-hidden rounded-b-xl rounded-t-md border-2 border-slate-300 bg-slate-50" role="meter" aria-valuenow={t.fill_pct} aria-label={`${t.name} level`}>
              <div className="absolute bottom-0 w-full" style={{ height: `${t.fill_pct}%`, background: t.days_to_reorder <= 1.5 ? "#e34948" : PRODUCT_COLORS[t.product] }} />
              <div className="absolute w-full border-t border-dashed border-slate-500" style={{ bottom: `${t.reorder_pct}%` }} title="Reorder level" />
            </div>
            <div className="text-center text-sm font-semibold tabular-nums">{t.fill_pct}%</div>
            <div className="text-center text-xs text-slate-500">{num(t.current_l)} / {num(t.capacity_l)} L</div>
            <div className={`mt-1 text-center text-xs ${t.days_to_reorder <= 1.5 ? "font-medium text-red-600" : "text-slate-500"}`}>Empty in {t.days_to_empty} d</div>
          </div>
        ))}
      </div>
      <div className="grid gap-5 md:grid-cols-2">
        <form className="card space-y-3 p-4" onSubmit={async (e) => {
          e.preventDefault();
          const r = await run(() => api("/stock/dip", { body: { tank_id: Number(dip.tank_id || tanks[0].id), measured_l: Number(dip.measured_l) } }), (x: any) => `Dip saved. Variance ${x.variance_pct}%`);
          if (r) { setDip({ ...dip, measured_l: "" }); refresh(); }
        }}>
          <h2 className="font-semibold">Record dip reading</h2>
          <Field label="Tank"><select className="input" value={dip.tank_id} onChange={(e) => setDip({ ...dip, tank_id: e.target.value })}>{tankOpts}</select></Field>
          <Field label="Measured litres (from dip chart)"><input className="input" type="number" min={0} required value={dip.measured_l} onChange={(e) => setDip({ ...dip, measured_l: e.target.value })} /></Field>
          <button className="btn-primary" disabled={busy}>Save dip</button>
        </form>
        <form className="card space-y-3 p-4" onSubmit={async (e) => {
          e.preventDefault();
          const r = await run(() => api("/stock/delivery", { body: { tank_id: Number(del.tank_id || tanks[0].id), invoice_l: Number(del.invoice_l), received_l: Number(del.received_l), tanker_no: del.tanker_no,
            supplier_id: del.supplier_id ? Number(del.supplier_id) : null, purchase_rate: del.purchase_rate ? Number(del.purchase_rate) : null } }), (x: any) => `Delivery saved. Shortage ${x.shortage_pct}%`);
          if (r) { setDel({ ...del, invoice_l: "", received_l: "", tanker_no: "", purchase_rate: "" }); refresh(); suppliers.reload(); }
        }}>
          <h2 className="font-semibold">Receive tanker delivery</h2>
          <Field label="Tank"><select className="input" value={del.tank_id} onChange={(e) => setDel({ ...del, tank_id: e.target.value })}>{tankOpts}</select></Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Invoice litres"><input className="input" type="number" min={1} required value={del.invoice_l} onChange={(e) => setDel({ ...del, invoice_l: e.target.value })} /></Field>
            <Field label="Received (dip difference)"><input className="input" type="number" min={1} required value={del.received_l} onChange={(e) => setDel({ ...del, received_l: e.target.value })} /></Field>
            <Field label="Tanker no."><input className="input" value={del.tanker_no} onChange={(e) => setDel({ ...del, tanker_no: e.target.value })} /></Field>
            <Field label="Supplier"><select className="input" value={del.supplier_id} onChange={(e) => setDel({ ...del, supplier_id: e.target.value })}>
              <option value="">— not on account —</option>{(suppliers.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
            {del.supplier_id && <Field label="Purchase rate (Rs/L, from invoice)"><input className="input" type="number" step="0.01" min={1} required value={del.purchase_rate} onChange={(e) => setDel({ ...del, purchase_rate: e.target.value })} /></Field>}
            {del.supplier_id && Number(del.invoice_l) > 0 && Number(del.purchase_rate) > 0 && <div className="col-span-2 text-xs text-slate-600">Adds Rs {Math.round(Number(del.invoice_l) * Number(del.purchase_rate)).toLocaleString("en-IN")} to what we owe this supplier.</div>}
          </div>
          <button className="btn-primary" disabled={busy}>Save delivery</button>
        </form>
      </div>
      <div className="grid gap-5 md:grid-cols-2">
        <History title="Dip readings" rows={stock.data.dips} cols={[["When", (r) => dt(r.created_at)], ["Tank", (r) => `${r.station.replace("Al-Madina ", "")} ${r.tank}`], ["Book", (r) => num(r.book_l)], ["Dip", (r) => num(r.measured_l)], ["Var %", (r) => <span className={Math.abs(r.variance_pct) >= 0.5 ? "font-semibold text-red-600" : ""}>{r.variance_pct}%</span>]]} />
        <History title="Deliveries" rows={stock.data.deliveries} cols={[["When", (r) => dt(r.created_at)], ["Tank", (r) => `${r.station.replace("Al-Madina ", "")} ${r.tank}`], ["Tanker", (r) => r.tanker_no], ["Invoice/Recv", (r) => `${num(r.invoice_l)} / ${num(r.received_l)}`], ["Short %", (r) => <span className={r.shortage_pct >= 0.3 ? "font-semibold text-red-600" : ""}>{r.shortage_pct}%</span>]]} />
      </div>
    </div>
  );
}

function History({ title, rows, cols }: { title: string; rows: any[]; cols: [string, (r: any) => React.ReactNode][] }) {
  return (
    <div className="card">
      <h2 className="p-3 font-semibold">{title}</h2>
      <div className="max-h-80 overflow-auto">
        <table className="w-full"><thead><tr>{cols.map(([h]) => <th key={h} className="th">{h}</th>)}</tr></thead>
          <tbody>{rows.map((r) => <tr key={r.id}>{cols.map(([h, f]) => <td key={h} className="td text-xs">{f(r)}</td>)}</tr>)}</tbody></table>
      </div>
    </div>
  );
}
