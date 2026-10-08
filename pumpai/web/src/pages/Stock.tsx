import { useEffect, useState } from "react";
import { api, useApi } from "../lib/api";
import { Badge, Field, Loading, Modal, PageHeader, useAction } from "../components/ui";
import { PRODUCTS } from "../lib/format";
import { PhotoButton, PhotoThumb, ProofPhotos, ProofThumbs } from "../components/Capture";
import { SupplierForm } from "../components/QuickAdd";
import { supplierOpts } from "../components/SupplierSelect";
import { useAuth } from "../App";
import { PRODUCT_COLORS, dt, num } from "../lib/format";

export default function Stock() {
  const dash = useApi<any>("/dashboard");
  const stock = useApi<any>("/stock");
  const suppliers = useApi<any[]>("/suppliers");
  const [addSup, setAddSup] = useState(false);
  const { can } = useAuth();
  const { busy, run } = useAction();
  const [dip, setDip] = useState({ tank_id: "", measured_l: "", cm: "" });
  const [dipPhotos, setDipPhotos] = useState<number[]>([]);
  const [dipL, setDipL] = useState<{ litres?: number; error?: string } | null>(null);
  const [dipSure, setDipSure] = useState(false);
  const [order, setOrder] = useState<any>(null);
  const [chart, setChart] = useState<any>(null);
  const orders = useApi<any[]>("/stock/orders");
  const dipTank = Number(dip.tank_id) || dash.data?.tanks[0]?.id;
  // dip in cm → litres from the tank's chart, as you type
  useEffect(() => {
    if (!dip.cm || !dipTank) { setDipL(null); return; }
    const id = setTimeout(() => api(`/tanks/${dipTank}/dip-litres?cm=${Number(dip.cm)}`).then((x) => setDipL({ litres: x.litres })).catch((e) => setDipL({ error: e.message })), 250);
    return () => clearTimeout(id);
  }, [dip.cm, dipTank]);
  const [del, setDel] = useState<any>({ tank_id: "", invoice_l: "", received_l: "", tanker_no: "", supplier_id: "", purchase_rate: "", freight: "", photo_id: null });
  if (!dash.data || !stock.data) return <Loading />;
  const tanks = dash.data.tanks;
  const refresh = () => { dash.reload(); stock.reload(); };
  const tankOpts = tanks.map((t: any) => <option key={t.id} value={t.id}>{t.name} · {t.station_name}</option>);

  return (
    <div className="space-y-5">
      <PageHeader title="Tanks & stock" subtitle="Wet-stock reconciliation: dip vs book stock, tanker short-delivery detection and AI stock-out forecasts" />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
        {tanks.map((t: any) => (
          <div key={t.id} className="card min-w-0 p-3">
            <div className="text-xs text-slate-500">{t.station_name}</div>
            <div className="font-medium">{t.name}</div>
            <div className="relative mx-auto my-3 h-28 w-16 overflow-hidden rounded-b-xl rounded-t-md border-2 border-slate-300 bg-slate-50" role="meter" aria-valuenow={t.fill_pct} aria-label={`${t.name} level`}>
              <div className="absolute bottom-0 w-full" style={{ height: `${t.fill_pct}%`, background: t.days_to_reorder <= 1.5 ? "#e34948" : PRODUCT_COLORS[t.product] }} />
              <div className="absolute w-full border-t border-dashed border-slate-500" style={{ bottom: `${t.reorder_pct}%` }} title="Reorder level" />
            </div>
            <div className="text-center text-sm font-semibold tabular-nums">{t.fill_pct}%</div>
            <div className="text-center text-xs text-slate-500">{num(t.current_l)} / {num(t.capacity_l)} L</div>
            <div className={`mt-1 text-center text-xs ${t.days_to_reorder <= 1.5 ? "font-medium text-red-600" : "text-slate-500"}`}>Empty in {t.days_to_empty} d</div>
            {(() => {
              const open = (orders.data ?? []).find((o: any) => o.tank_id === t.id && o.status === "ordered");
              return open
                ? <div className="mt-2 rounded-lg bg-blue-50 p-1.5 text-center text-xs text-blue-800">🚛 Ordered {num(open.litres)} L</div>
                : <button className={`mt-2 min-h-9 w-full rounded-lg py-1.5 text-xs font-semibold ${t.days_to_reorder <= 1.5 ? "bg-red-600 text-white" : "bg-slate-100 text-slate-700"}`}
                    onClick={() => api(`/stock/order-suggestion/${t.id}`).then(setOrder)}>Order tanker</button>;
            })()}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
        <form className="card min-w-0 space-y-3 p-4" onSubmit={async (e) => {
          e.preventDefault();
          const body = { ...(dip.cm ? { tank_id: dipTank, measured_cm: Number(dip.cm) } : { tank_id: dipTank, measured_l: Number(dip.measured_l) }), photo_ids: dipPhotos, confirm: dipSure };
          const r = await run(() => api("/stock/dip", { body }), (x: any) => `Dip saved: ${num(x.measured_l)} L. Variance ${x.variance_pct}%`);
          if (r) { setDip({ ...dip, measured_l: "", cm: "" }); setDipPhotos([]); setDipSure(false); refresh(); }
        }}>
          <div className="flex items-center justify-between"><h2 className="font-semibold">Record dip reading</h2>
            <button type="button" className="-my-2 min-h-9 px-1 text-xs text-brand-600 hover:underline" onClick={() => api(`/tanks/${dipTank}/chart`).then(setChart)}>Dip chart</button></div>
          <Field label="Tank · ٹینک"><select className="input" value={dip.tank_id || String(dipTank ?? "")} onChange={(e) => { setDip({ ...dip, tank_id: e.target.value }); setDipSure(false); }}>{tankOpts}</select></Field>
          {(() => { const tk = tanks.find((t: any) => t.id === dipTank); return tk ? <p className="-mt-1 text-xs text-slate-500">Book stock now · <span lang="ur" dir="rtl" className="font-urdu">سسٹم میں</span> <b className="tabular-nums">{num(tk.current_l)} L</b> of {num(tk.capacity_l)} L</p> : null; })()}
          <div className="grid grid-cols-2 gap-2">
            <Field label="Dip stick (cm) · ڈپ"><input className="input py-3 text-xl" type="number" step="0.1" min={0} value={dip.cm} onChange={(e) => { setDip({ ...dip, cm: e.target.value, measured_l: "" }); setDipSure(false); }} /></Field>
            <Field label="…or litres"><input className="input py-3 text-xl" type="number" min={0} disabled={Boolean(dip.cm)} required={!dip.cm} value={dip.cm && dipL?.litres != null ? String(dipL.litres) : dip.measured_l} onChange={(e) => { setDip({ ...dip, measured_l: e.target.value }); setDipSure(false); }} /></Field>
          </div>
          {dipL?.error && <p className="text-xs text-red-600">{dipL.error}</p>}
          {dip.cm && dipL?.litres != null && <p className="text-sm text-slate-600">{dip.cm} cm = <b>{num(dipL.litres)} L</b> from the dip chart</p>}
          {(() => {
            const tk = tanks.find((t: any) => t.id === dipTank);
            const m = dip.cm ? dipL?.litres : dip.measured_l !== "" ? Number(dip.measured_l) : undefined;
            if (!tk || m == null || !(tk.current_l > 0)) return null;
            const diff = m - tk.current_l, pct = (diff / tk.current_l) * 100, big = Math.abs(pct) >= 5;
            return (
              <div className={`rounded-lg px-2.5 py-2 text-sm ${big ? "bg-red-50 text-red-800" : Math.abs(pct) >= 0.5 ? "bg-amber-50 text-amber-800" : "bg-emerald-50 text-emerald-800"}`}>
                Book {num(tk.current_l)} L → dip {num(m)} L: <b className="tabular-nums">{diff >= 0 ? "+" : "−"}{num(Math.abs(diff))} L ({pct >= 0 ? "+" : ""}{pct.toFixed(2)}%)</b>
                {big && <label className="mt-1.5 flex items-start gap-2 font-medium"><input type="checkbox" className="mt-1 h-4 w-4" checked={dipSure} onChange={(e) => setDipSure(e.target.checked)} />
                  <span>Big difference — I checked the dip stick again, it is right · <span lang="ur" dir="rtl" className="font-urdu">میں نے دوبارہ چیک کیا</span></span></label>}
              </div>
            );
          })()}
          <ProofPhotos value={dipPhotos} onChange={setDipPhotos} hint="dip stick showing the reading" />
          <button className="btn-primary" disabled={busy}>Save dip · <span lang="ur" dir="rtl" className="font-urdu">محفوظ</span></button>
        </form>
        <form className="card min-w-0 space-y-3 p-4" onSubmit={async (e) => {
          e.preventDefault();
          const r = await run(() => api("/stock/delivery", { body: { tank_id: Number(del.tank_id || tanks[0].id), invoice_l: Number(del.invoice_l), received_l: Number(del.received_l), tanker_no: del.tanker_no,
            supplier_id: del.supplier_id ? Number(del.supplier_id) : null, purchase_rate: del.purchase_rate ? Number(del.purchase_rate) : null, freight: del.freight ? Number(del.freight) : null, photo_id: del.photo_id ?? null } }),
            (x: any) => `Delivery saved. Shortage ${x.shortage_pct}%${x.claim_id ? " — a shortage claim was opened (Accounts & tax → Tanker claims)" : ""}`);
          if (r) { setDel({ ...del, invoice_l: "", received_l: "", tanker_no: "", purchase_rate: "", freight: "", photo_id: null }); refresh(); suppliers.reload(); }
        }}>
          <div className="flex items-center justify-between gap-2">
            <h2 className="font-semibold">Receive tanker delivery</h2>
            <PhotoButton kind="invoice" label="Photo of invoice" onRead={(r, id) => {
              const tank = r?.product ? tanks.find((t: any) => t.product === r.product) : null;
              const sup = r?.supplier_name ? (suppliers.data ?? []).find((s: any) => s.name.toLowerCase().split(/\s+/).some((w: string) => w.length > 2 && r.supplier_name.toLowerCase().includes(w))) : null;
              setDel((x: any) => ({ ...x, photo_id: id, tank_id: tank ? String(tank.id) : x.tank_id,
                invoice_l: r?.invoice_litres ? String(r.invoice_litres) : x.invoice_l, tanker_no: r?.tanker_no ?? x.tanker_no,
                supplier_id: sup ? String(sup.id) : x.supplier_id, purchase_rate: r?.rate_per_litre ? String(r.rate_per_litre) : x.purchase_rate }));
            }} />
          </div>
          {del.photo_id && <p className="flex items-center gap-2 text-xs text-slate-500"><PhotoThumb id={del.photo_id} size={10} /> Invoice photo attached{del.invoice_l ? " — check the filled numbers, then enter the litres received from the dip" : ""}.</p>}
          <Field label="Tank"><select className="input" value={del.tank_id} onChange={(e) => setDel({ ...del, tank_id: e.target.value })}>{tankOpts}</select></Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Invoice litres"><input className="input" type="number" min={1} required value={del.invoice_l} onChange={(e) => setDel({ ...del, invoice_l: e.target.value })} /></Field>
            <Field label="Received (dip difference)"><input className="input" type="number" min={1} required value={del.received_l} onChange={(e) => setDel({ ...del, received_l: e.target.value })} /></Field>
            <Field label="Tanker no."><input className="input" value={del.tanker_no} onChange={(e) => setDel({ ...del, tanker_no: e.target.value })} /></Field>
            <div className="col-span-2"><Field label="Supplier (depot → company → banda) · سپلائر"><select className="input" required value={del.supplier_id} onChange={(e) => setDel({ ...del, supplier_id: e.target.value })}>
              <option value="">— choose supplier —</option>{supplierOpts(suppliers.data ?? [])}</select>
              {can("suppliers.manage") && <button type="button" className="mt-1 min-h-9 text-xs font-medium text-brand-700 hover:underline" onClick={() => setAddSup(true)}>+ New supplier · نیا سپلائر</button>}</Field></div>
            <Field label="Purchase rate (Rs/L, from invoice) · ریٹ"><input className="input" type="number" step="0.01" min={1} required value={del.purchase_rate} onChange={(e) => setDel({ ...del, purchase_rate: e.target.value })} /></Field>
            <Field label="Freight paid (Rs, optional)"><input className="input" type="number" min={0} value={del.freight} onChange={(e) => setDel({ ...del, freight: e.target.value })} />
              <span className="mt-0.5 block text-[11px] text-slate-500">For comparing depots only — pay it as an expense (Tanker freight) · <span lang="ur" dir="rtl" className="font-urdu">کرایہ خرچے میں لکھیں</span></span></Field>
            {Number(del.invoice_l) > 0 && Number(del.received_l) > 0 && (() => {
              const short = Number(del.invoice_l) - Number(del.received_l), pct = (short / Number(del.invoice_l)) * 100;
              return <div className={`col-span-2 rounded-lg px-2 py-1.5 text-xs ${pct >= 0.3 ? "bg-red-50 font-medium text-red-700" : "bg-slate-50 text-slate-600"}`}>
                {short > 0 ? <>Short {num(short)} L ({pct.toFixed(2)}%) · <span lang="ur" dir="rtl" className="font-urdu">کم آیا</span>{short >= 1 ? " — above the allowed loss it goes on a shortage claim" : ""}</> : short < 0 ? <>Received {num(-short)} L more than the invoice — check the dip</> : <>Full quantity received ✓</>}
              </div>;
            })()}
            {del.supplier_id && Number(del.invoice_l) > 0 && Number(del.purchase_rate) > 0 && <div className="col-span-2 text-xs text-slate-600">Adds Rs {Math.round(Number(del.invoice_l) * Number(del.purchase_rate)).toLocaleString("en-IN")} to what we owe this supplier.</div>}
          </div>
          <button className="btn-primary" disabled={busy}>Save delivery</button>
        </form>
      </div>
      {(orders.data ?? []).length > 0 && <History title="Tanker orders" right={4} rows={orders.data!} cols={[["When", (r) => dt(r.created_at)], ["Supplier", (r) => r.supplier_name], ["Fuel", (r) => `${PRODUCTS[r.product]} ${num(r.litres)} L`], ["Station", (r) => r.station_name], ["Status", (r) => <Badge tone={r.status === "delivered" ? "green" : r.status === "ordered" ? "blue" : "slate"}>{r.status}</Badge>]]} />}
      <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
        <History title="Dip readings" right={4} rows={stock.data.dips} cols={[["When", (r) => dt(r.created_at)], ["Tank", (r) => `${r.station} ${r.tank}`], ["Book", (r) => num(r.book_l)], ["Dip", (r) => <>{num(r.measured_l)}{r.measured_cm != null ? <span className="text-xs text-slate-400"> ({r.measured_cm} cm)</span> : null}</>], ["Var %", (r) => <span className={Math.abs(r.variance_pct) >= 0.5 ? "font-semibold text-red-600" : ""}>{r.variance_pct}%</span>], ["Photo", (r) => (r.proof_ids?.length ? <ProofThumbs ids={r.proof_ids} /> : null)]]} />
        <History title="Deliveries" right={4} rows={stock.data.deliveries} cols={[["When", (r) => dt(r.created_at)], ["Tank", (r) => `${r.station} ${r.tank}`], ["Tanker", (r) => <span className="flex items-center gap-1">{r.tanker_no}{r.photo_id ? <PhotoThumb id={r.photo_id} size={7} /> : null}</span>], ["Supplier", (r) => r.supplier ?? "—"], ["Invoice/Recv", (r) => `${num(r.invoice_l)} / ${num(r.received_l)}`], ["Short %", (r) => <span className={r.shortage_pct >= 0.3 ? "font-semibold text-red-600" : ""}>{r.shortage_pct}%</span>]]} />
      </div>
      {order && <OrderModal s={order} suppliers={suppliers.data ?? []} onClose={() => setOrder(null)} onDone={() => { setOrder(null); orders.reload(); }} />}
      {chart && <ChartModal c={chart} onClose={() => setChart(null)} />}
      {addSup && <SupplierForm onClose={() => setAddSup(false)} onSaved={(sp: any) => { setAddSup(false); suppliers.reload(); if (sp?.id) setDel((x: any) => ({ ...x, supplier_id: String(sp.id) })); }} />}
    </div>
  );
}

/** One-tap tanker order: suggested supplier and litres; the order goes to the supplier on WhatsApp. */
function OrderModal({ s, suppliers, onClose, onDone }: { s: any; suppliers: any[]; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ supplier_id: String(s.supplier_id ?? suppliers[0]?.id ?? ""), litres: String(s.litres || ""), note: "" });
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={`Order tanker — ${s.tank.station_name} ${s.tank.name}`}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        if (await run(() => api("/stock/orders", { body: { tank_id: s.tank.id, supplier_id: Number(f.supplier_id), litres: Number(f.litres), note: f.note || null } }), (x: any) => x.whatsapp === "sent" ? "Order sent to the supplier on WhatsApp" : "Order saved (supplier has no WhatsApp number)")) onDone();
      }}>
        <p className="text-sm text-slate-600">{PRODUCTS[s.tank.product]} · space in tank: <b>{num(s.room)} L</b></p>
        <Field label="Supplier"><select className="input" value={f.supplier_id} onChange={(e) => setF({ ...f, supplier_id: e.target.value })}>{supplierOpts(suppliers, true)}</select></Field>
        <Field label="Litres"><input className="input py-3 text-2xl" type="number" min={1000} step={1000} max={s.room} required value={f.litres} onChange={(e) => setF({ ...f, litres: e.target.value })} /></Field>
        <div className="flex flex-wrap gap-2">{[10000, 20000, 30000, 40000].filter((x) => x <= s.room).map((x) => <button type="button" key={x} className="min-h-9 rounded-lg bg-slate-100 px-3 py-1.5 text-sm" onClick={() => setF({ ...f, litres: String(x) })}>{num(x)} L</button>)}</div>
        <Field label="Note for supplier (optional)"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} placeholder="e.g. deliver before 6pm" /></Field>
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Send order</button></div>
      </form>
    </Modal>
  );
}

/** Dip chart editor: paste the OMC chart (cm and litres per line) or make one from the tank diameter. */
function ChartModal({ c, onClose }: { c: any; onClose: () => void }) {
  const [text, setText] = useState(c.rows.map((r: any) => `${r.cm} ${r.litres}`).join("\n"));
  const [diameter, setDiameter] = useState("");
  const { busy, run } = useAction();
  const save = (body: any) => run(() => api(`/tanks/${c.tank.id}/chart`, { method: "PUT", body }), (x: any) => `Chart saved: ${x.rows} rows, full = ${num(x.max_litres)} L`).then((x) => x && onClose());
  return (
    <Modal open onClose={onClose} title={`Dip chart — ${c.tank.name}`} wide>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div>
          <Field label="One row per line: cm litres (from the oil company's chart)">
            <textarea className="input h-72 font-mono text-sm" value={text} onChange={(e) => setText(e.target.value)} />
          </Field>
          <button className="btn-primary mt-2" disabled={busy} onClick={() => {
            const rows = text.split(/\n+/).map((l: string) => l.trim().split(/[\s,;\t]+/).map(Number)).filter((x: number[]) => x.length >= 2 && !x.some(isNaN)).map(([cm, litres]: number[]) => ({ cm, litres }));
            save({ rows });
          }}>Save chart</button>
        </div>
        <div className="space-y-3 rounded-lg bg-slate-50 p-3 text-sm">
          <p>No chart yet? Make an approximate one from the tank's inside diameter (horizontal round tank, {num(c.tank.capacity_l)} L). Replace it with the oil company's chart when you have it.</p>
          <Field label="Inside diameter (cm)"><input className="input" type="number" min={50} max={600} value={diameter} onChange={(e) => setDiameter(e.target.value)} /></Field>
          <button className="btn-secondary" disabled={busy || !diameter} onClick={() => save({ diameter_cm: Number(diameter) })}>Make chart</button>
        </div>
      </div>
    </Modal>
  );
}

/** Desktop: a table. Phone: one card per row — the 2nd column as the title, the last-but-one (or `right`) column on the right, the rest below. */
function History({ title, rows, cols, right }: { title: string; rows: any[]; cols: [string, (r: any) => React.ReactNode][]; right?: number }) {
  const ri = right ?? cols.length - 1;
  return (
    <div className="card min-w-0">
      <h2 className="p-3 font-semibold">{title}</h2>
      <ul className="max-h-96 divide-y divide-slate-100 overflow-y-auto border-t border-slate-100 sm:hidden">
        {rows.map((r) => (
          <li key={r.id} className="px-3 py-2.5">
            <div className="flex items-start justify-between gap-2"><span className="min-w-0 break-words font-medium">{cols[1][1](r)}</span><span className="shrink-0 text-right tabular-nums">{cols[ri][1](r)}</span></div>
            <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-slate-500">{cols.map(([h, f], i) => i === 1 || i === ri || f(r) == null ? null : <span key={h}>{i === 0 ? f(r) : <>{h} <span className="tabular-nums text-slate-700">{f(r)}</span></>}</span>)}</div>
          </li>
        ))}
      </ul>
      <div className="hidden max-h-80 overflow-auto sm:block">
        <table className="w-full"><thead><tr>{cols.map(([h]) => <th key={h} className="th">{h}</th>)}</tr></thead>
          <tbody>{rows.map((r) => <tr key={r.id}>{cols.map(([h, f]) => <td key={h} className="td text-xs">{f(r)}</td>)}</tr>)}</tbody></table>
      </div>
    </div>
  );
}
