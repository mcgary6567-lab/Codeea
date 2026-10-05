import { useState } from "react";
import { Plus, PackagePlus, ClipboardCheck, Pencil } from "lucide-react";
import { api, useApi } from "../lib/api";
import { ProofPhotos, ProofThumbs } from "../components/Capture";
import { Badge, Field, Loading, Modal, PageHeader, Stat, useAction } from "../components/ui";
import { dt, num, pkr } from "../lib/format";

const CATS: Record<string, string> = { lubricant: "Engine oil", filter: "Filters", coolant: "Coolant", tyre: "Tyres", battery: "Battery", tuck: "Tuck shop", service: "Service", other: "Other" };

/** Lubricants, filters and tuck shop: items, stock, purchases, counts and profit. */
export default function Shop() {
  const stations = useApi<any[]>("/stations");
  const [station, setStation] = useState<number | null>(null);
  const sid = station ?? stations.data?.[0]?.id;
  const items = useApi<any[]>(sid ? `/shop/items?station_id=${sid}&all=1` : null);
  const sum = useApi<any>("/shop/summary");
  const [form, setForm] = useState<null | { kind: "item" | "in" | "count" | "moves"; item?: any }>(null);
  if (!items.data || !sum.data || !stations.data) return <Loading />;
  const s = sum.data;
  const reload = () => { items.reload(); sum.reload(); };
  return (
    <div className="space-y-5">
      <PageHeader title="Shop & lubricants" subtitle="Engine oil, filters, tuck shop — sold from the POS, stock kept automatically"
        actions={<>
          <select className="input w-auto" value={sid} onChange={(e) => setStation(Number(e.target.value))}>{stations.data.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select>
          <button className="btn-primary" onClick={() => setForm({ kind: "item" })}><Plus size={15} /> Add item</button>
        </>} />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Shop sales today" value={pkr(s.sales)} hint={`${s.count} sales`} tone="green" />
        <Stat label="Profit today" value={pkr(s.profit)} tone="blue" />
        <Stat label="Stock value (cost)" value={pkr(s.stock_value)} />
        <Stat label="Low stock" value={s.low_stock.length} tone={s.low_stock.length ? "red" : "slate"} hint={s.low_stock.slice(0, 2).map((x: any) => x.name).join(", ")} />
      </div>
      {/* phone: one card per item */}
      <ul className="card divide-y divide-slate-100 sm:hidden">
        {items.data.map((i) => (
          <li key={i.id} className={`px-4 py-3 ${i.active ? "" : "opacity-50"}`}>
            <div className="flex items-start justify-between gap-2">
              <span className="min-w-0"><span className="block font-semibold">{i.name}</span><span className="text-xs text-slate-500">{CATS[i.category] ?? i.category}{i.barcode ? <> · <span className="font-mono">{i.barcode}</span></> : null}</span></span>
              <span className="shrink-0 text-right"><span className="block font-semibold tabular-nums">{pkr(i.price)}</span><span className="text-xs tabular-nums text-emerald-700">+{pkr(i.price - i.cost)}</span></span>
            </div>
            <div className="mt-0.5 flex flex-wrap items-center gap-x-3 text-xs text-slate-500"><span>Cost <span className="tabular-nums text-slate-700">{pkr(i.cost)}</span></span>
              <span>Stock {i.stock <= i.reorder_level && i.reorder_level > 0 ? <Badge tone="red">{num(i.stock)} {i.unit}</Badge> : <span className="tabular-nums text-slate-700">{num(i.stock)} {i.unit}</span>}</span></div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              <button className="btn-secondary min-h-9 !px-2.5 !py-1 text-xs" onClick={() => setForm({ kind: "in", item: i })}><PackagePlus size={14} /> Stock in</button>
              <button className="btn-secondary min-h-9 !px-2.5 !py-1 text-xs" onClick={() => setForm({ kind: "count", item: i })}><ClipboardCheck size={14} /> Count</button>
              <button className="btn-secondary min-h-9 !px-2.5 !py-1 text-xs" onClick={() => setForm({ kind: "moves", item: i })}>History</button>
              <button className="btn-secondary min-h-9 !px-2.5 !py-1" aria-label={`Edit ${i.name}`} onClick={() => setForm({ kind: "item", item: i })}><Pencil size={14} /></button>
            </div>
          </li>
        ))}
      </ul>
      <div className="card hidden overflow-x-auto sm:block">
        <table className="w-full">
          <thead><tr><th className="th">Item</th><th className="th">Category</th><th className="th">Barcode</th><th className="th text-right">Cost</th><th className="th text-right">Price</th><th className="th text-right">Margin</th><th className="th text-right">Stock</th><th className="th" /></tr></thead>
          <tbody>{items.data.map((i) => (
            <tr key={i.id} className={i.active ? "" : "opacity-50"}>
              <td className="td font-medium">{i.name}</td>
              <td className="td text-sm">{CATS[i.category] ?? i.category}</td>
              <td className="td font-mono text-xs text-slate-500">{i.barcode ?? "—"}</td>
              <td className="td text-right tabular-nums">{pkr(i.cost)}</td>
              <td className="td text-right tabular-nums">{pkr(i.price)}</td>
              <td className="td text-right tabular-nums text-emerald-700">{pkr(i.price - i.cost)}</td>
              <td className="td text-right tabular-nums">{i.stock <= i.reorder_level && i.reorder_level > 0 ? <Badge tone="red">{num(i.stock)} {i.unit}</Badge> : `${num(i.stock)} ${i.unit}`}</td>
              <td className="td"><div className="flex justify-end gap-1">
                <button className="btn-secondary !px-2 !py-1 text-xs" onClick={() => setForm({ kind: "in", item: i })}><PackagePlus size={14} /> Stock in</button>
                <button className="btn-secondary !px-2 !py-1 text-xs" onClick={() => setForm({ kind: "count", item: i })}><ClipboardCheck size={14} /> Count</button>
                <button className="btn-secondary !px-2 !py-1 text-xs" onClick={() => setForm({ kind: "moves", item: i })}>History</button>
                <button className="btn-secondary !px-2 !py-1" aria-label={`Edit ${i.name}`} onClick={() => setForm({ kind: "item", item: i })}><Pencil size={14} /></button>
              </div></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
        <div className="card min-w-0 p-4"><h2 className="mb-2 font-semibold">Best sellers today</h2>
          {s.top.map((t: any) => <div key={t.name} className="flex justify-between gap-2 border-b border-slate-100 py-1.5 text-sm"><span className="min-w-0">{t.name}</span><span className="shrink-0 tabular-nums">{num(t.qty)} · {pkr(t.sales)}</span></div>)}
          {!s.top.length && <p className="text-sm text-slate-500">No shop sales yet today</p>}</div>
        <div className="card p-4"><h2 className="mb-2 font-semibold">Profit by category today</h2>
          {s.by_category.map((c: any) => <div key={c.category} className="flex justify-between border-b border-slate-100 py-1.5 text-sm"><span>{CATS[c.category] ?? c.category}</span><span className="tabular-nums">{pkr(c.sales)} · profit {pkr(c.profit)}</span></div>)}</div>
      </div>
      {form?.kind === "item" && <ItemForm item={form.item} stationId={sid!} onClose={() => setForm(null)} onDone={() => { setForm(null); reload(); }} />}
      {(form?.kind === "in" || form?.kind === "count") && <StockForm kind={form.kind} item={form.item} onClose={() => setForm(null)} onDone={() => { setForm(null); reload(); }} />}
      {form?.kind === "moves" && <Moves item={form.item} onClose={() => setForm(null)} />}
    </div>
  );
}

function ItemForm({ item, stationId, onClose, onDone }: { item?: any; stationId: number; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ name: item?.name ?? "", category: item?.category ?? "lubricant", barcode: item?.barcode ?? "", unit: item?.unit ?? "pc",
    cost: String(item?.cost ?? ""), price: String(item?.price ?? ""), reorder_level: String(item?.reorder_level ?? "5"), stock: "", active: item ? Boolean(item.active) : true });
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={item ? `Edit ${item.name}` : "Add shop item"}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const body: any = { name: f.name, category: f.category, barcode: f.barcode || null, unit: f.unit, cost: Number(f.cost) || 0, price: Number(f.price), reorder_level: Number(f.reorder_level) || 0 };
        const r = item ? await run(() => api(`/shop/items/${item.id}`, { method: "PATCH", body: { ...body, active: f.active } }), "Saved")
          : await run(() => api("/shop/items", { body: { ...body, station_id: stationId, stock: Number(f.stock) || 0 } }), "Item added");
        if (r) onDone();
      }}>
        <Field label="Name"><input className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="e.g. Shell Helix HX7 (4 L)" /></Field>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Field label="Category"><select className="input" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{Object.entries(CATS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
          <Field label="Barcode"><input className="input font-mono" value={f.barcode} onChange={(e) => setF({ ...f, barcode: e.target.value })} /></Field>
          <Field label="Unit"><input className="input" value={f.unit} onChange={(e) => setF({ ...f, unit: e.target.value })} placeholder="pc / can / bottle" /></Field>
          <Field label="Cost (Rs)"><input className="input" type="number" min={0} step="0.01" value={f.cost} onChange={(e) => setF({ ...f, cost: e.target.value })} /></Field>
          <Field label="Sale price (Rs)"><input className="input" type="number" min={1} step="0.01" required value={f.price} onChange={(e) => setF({ ...f, price: e.target.value })} /></Field>
          <Field label="Alert when stock ≤"><input className="input" type="number" min={0} value={f.reorder_level} onChange={(e) => setF({ ...f, reorder_level: e.target.value })} /></Field>
          {!item && <Field label="Opening stock"><input className="input" type="number" min={0} value={f.stock} onChange={(e) => setF({ ...f, stock: e.target.value })} /></Field>}
        </div>
        {item && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Show on the POS</label>}
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

function StockForm({ kind, item, onClose, onDone }: { kind: "in" | "count"; item: any; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ qty: "", cost: String(item.cost ?? ""), supplier: "", reason: "Stock count" });
  const [photos, setPhotos] = useState<number[]>([]);
  const { busy, run } = useAction();
  return (
    <Modal open onClose={onClose} title={kind === "in" ? `Stock received — ${item.name}` : `Count stock — ${item.name}`}>
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        const r = kind === "in" ? await run(() => api(`/shop/items/${item.id}/stock-in`, { body: { qty: Number(f.qty), cost: Number(f.cost) || 0, supplier: f.supplier || null, photo_ids: photos } }), "Stock added")
          : await run(() => api(`/shop/items/${item.id}/adjust`, { body: { counted: Number(f.qty), reason: f.reason } }), (x: any) => x.difference ? `Difference ${x.difference} ${item.unit} recorded` : "Matches the book");
        if (r) onDone();
      }}>
        <p className="text-sm text-slate-600">Book stock: <b>{num(item.stock)} {item.unit}</b></p>
        <Field label={kind === "in" ? "Quantity received" : "Quantity counted on the shelf"}><input className="input py-3 text-2xl" type="number" min={kind === "in" ? 1 : 0} step="any" required value={f.qty} onChange={(e) => setF({ ...f, qty: e.target.value })} /></Field>
        {kind === "in" ? <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Cost per unit (Rs)"><input className="input" type="number" min={0} step="0.01" value={f.cost} onChange={(e) => setF({ ...f, cost: e.target.value })} /></Field>
          <Field label="Supplier"><input className="input" value={f.supplier} onChange={(e) => setF({ ...f, supplier: e.target.value })} /></Field>
          <div className="sm:col-span-2"><ProofPhotos value={photos} onChange={setPhotos} hint="supplier bill / delivery slip" /></div>
        </div> : <Field label="Reason"><select className="input" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })}>{["Stock count", "Damaged", "Expired", "Used in service", "Other"].map((x) => <option key={x}>{x}</option>)}</select></Field>}
        <div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={busy}>Save</button></div>
      </form>
    </Modal>
  );
}

function Moves({ item, onClose }: { item: any; onClose: () => void }) {
  const { data } = useApi<any[]>(`/shop/items/${item.id}/moves`);
  return (
    <Modal open onClose={onClose} title={`Stock history — ${item.name}`} wide>
      {!data ? <Loading /> : <div className="max-h-[60vh] overflow-auto"><table className="w-full">
        <thead><tr><th className="th">When</th><th className="th">Type</th><th className="th text-right">Qty</th><th className="th">Note</th><th className="th">By</th></tr></thead>
        <tbody>{data.map((m) => <tr key={m.id}><td className="td text-xs">{dt(m.created_at)}</td><td className="td text-sm capitalize">{m.type}</td><td className={`td text-right tabular-nums ${m.qty < 0 ? "text-red-600" : "text-emerald-700"}`}>{m.qty > 0 ? "+" : ""}{num(m.qty, 2)}</td><td className="td text-sm text-slate-600">{m.note ?? m.ref ?? ""} <ProofThumbs ids={m.proof_ids} /></td><td className="td text-xs">{m.created_by}</td></tr>)}</tbody>
      </table></div>}
    </Modal>
  );
}
