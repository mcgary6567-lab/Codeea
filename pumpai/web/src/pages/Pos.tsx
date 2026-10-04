import { useEffect, useMemo, useState } from "react";
import { Fuel } from "lucide-react";
import { api, useApi } from "../lib/api";
import { Field, Loading, PageHeader, useAction } from "../components/ui";
import { Link } from "react-router-dom";
import { PRODUCTS, PRODUCT_COLORS, dt, num, pkr } from "../lib/format";
import { useAuth } from "../App";
import { useNotifications } from "../components/Notifications";

const METHODS = ["cash", "card", "jazzcash", "easypaisa", "raast", "khata"];

export default function Pos() {
  const stations = useApi<any[]>("/stations");
  const prices = useApi<any>("/prices");
  const sales = useApi<any[]>("/sales?limit=40");
  const customers = useApi<any[]>("/customers?q=");
  const { busy, run } = useAction();
  const { user } = useAuth();
  const isSalesman = user?.role === "salesman";
  const myShifts = useApi<any[]>(isSalesman ? "/shifts" : null);
  const noShift = isSalesman && myShifts.data && !myShifts.data.some((s) => s.status === "open");
  const notif = useNotifications();
  const priceLock = isSalesman && notif.data?.pending_ack.some((n) => n.type === "price_change");
  const [f, setF] = useState<any>({ station_id: "", product: "PMG", mode: "amount", value: "", payment_method: "cash", customer_id: "", vehicle_no: "", nozzle_id: "" });
  useEffect(() => { if (stations.data && !f.station_id) setF((x: any) => ({ ...x, station_id: stations.data![0].id })); }, [stations.data]);

  const station = stations.data?.find((s) => s.id === Number(f.station_id));
  const rate = prices.data?.current?.[f.product]?.price ?? 0;
  const litres = f.mode === "litres" ? Number(f.value) : Number(f.value) / (rate || 1);
  const amount = f.mode === "amount" ? Number(f.value) : Number(f.value) * rate;
  const products = useMemo(() => [...new Set((station?.tanks ?? []).map((t: any) => t.product))] as string[], [station]);
  const nozzles = (station?.nozzles ?? []).filter((n: any) => n.product === f.product);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const body: any = { station_id: Number(f.station_id), product: f.product, payment_method: f.payment_method,
      customer_id: f.customer_id ? Number(f.customer_id) : null, vehicle_no: f.vehicle_no || null, nozzle_id: f.nozzle_id ? Number(f.nozzle_id) : null };
    body[f.mode] = Number(f.value);
    const r = await run(() => api("/sales", { body }), (s: any) => `Sale saved: ${num(s.litres, 2)} L = ${pkr(s.amount)}`);
    if (r) { setF({ ...f, value: "", vehicle_no: "" }); sales.reload(); prices.reload(); }
  };

  if (!stations.data || !prices.data) return <Loading />;
  return (
    <div>
      <PageHeader title="Sales / POS" subtitle="Quick sale entry. Stock, loyalty points and khata update automatically." />
      {priceLock && <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">⛽ Fuel price has changed. Update the dispenser and confirm in the pop-up (or the 🔔 bell) to continue selling. <button className="font-medium underline" onClick={() => notif.setSnoozed(null)}>Confirm now</button></div>}
      {noShift && <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">Your shift is not open. <Link to="/shifts" className="font-medium underline">Start your shift</Link> before recording sales.</div>}
      <div className="grid gap-5 lg:grid-cols-[380px_1fr]">
        <form onSubmit={submit} className="card space-y-3 p-4">
          <Field label="Station"><select className="input" value={f.station_id} onChange={(e) => setF({ ...f, station_id: e.target.value })}>
            {stations.data.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
          <div className="grid grid-cols-3 gap-2">
            {products.map((p) => (
              <button type="button" key={p} onClick={() => setF({ ...f, product: p, nozzle_id: "" })}
                className={`rounded-lg border-2 p-2 text-center text-sm ${f.product === p ? "border-current font-semibold" : "border-slate-200 text-slate-600"}`} style={f.product === p ? { color: PRODUCT_COLORS[p] } : {}}>
                <Fuel size={16} className="mx-auto" />{PRODUCTS[p]}<div className="text-xs text-slate-500">Rs {prices.data.current[p]?.price}</div>
              </button>
            ))}
          </div>
          <Field label="Nozzle"><select className="input" value={f.nozzle_id} onChange={(e) => setF({ ...f, nozzle_id: e.target.value })}>
            <option value="">—</option>{nozzles.map((n: any) => <option key={n.id} value={n.id}>{n.label}</option>)}</select></Field>
          <div className="flex gap-2">
            {(["amount", "litres"] as const).map((m) => <button type="button" key={m} onClick={() => setF({ ...f, mode: m })} className={`flex-1 rounded-lg py-1.5 text-sm ${f.mode === m ? "bg-brand-600 text-white" : "bg-slate-100"}`}>By {m === "amount" ? "Rupees" : "Litres"}</button>)}
          </div>
          <input className="input text-2xl font-semibold" type="number" step="0.01" min="0" required placeholder={f.mode === "amount" ? "Rs" : "Litres"} value={f.value} onChange={(e) => setF({ ...f, value: e.target.value })} />
          {Number(f.value) > 0 && <div className="rounded-lg bg-slate-50 p-2 text-sm">{num(litres, 2)} L × Rs {rate} = <b>{pkr(amount)}</b></div>}
          <div className="flex flex-wrap gap-1.5">
            {METHODS.map((m) => <button type="button" key={m} onClick={() => setF({ ...f, payment_method: m })} className={`rounded-full px-3 py-1 text-xs capitalize ${f.payment_method === m ? "bg-slate-900 text-white" : "bg-slate-100"}`}>{m}</button>)}
          </div>
          <Field label={`Customer ${f.payment_method === "khata" ? "(required)" : "(optional, earns points)"}`}>
            <select className="input" value={f.customer_id} required={f.payment_method === "khata"} onChange={(e) => setF({ ...f, customer_id: e.target.value })}>
              <option value="">Walk-in</option>
              {(customers.data ?? []).filter((c) => f.payment_method !== "khata" || c.credit_limit > 0).map((c) => <option key={c.id} value={c.id}>{c.name}{c.credit_limit ? ` (khata ${pkr(c.balance)} / ${pkr(c.credit_limit)})` : ""}</option>)}
            </select>
          </Field>
          <Field label="Vehicle no."><input className="input" placeholder="LEA-1234" value={f.vehicle_no} onChange={(e) => setF({ ...f, vehicle_no: e.target.value })} /></Field>
          <button className="btn-primary w-full py-3 text-base" disabled={busy || !!noShift || !!priceLock}>Record sale</button>
        </form>
        <div className="card overflow-x-auto">
          <table className="w-full">
            <thead><tr><th className="th">Time</th><th className="th">Station</th><th className="th">Product</th><th className="th text-right">Litres</th><th className="th text-right">Amount</th><th className="th">Payment</th><th className="th">Customer</th></tr></thead>
            <tbody>{(sales.data ?? []).map((s) => (
              <tr key={s.id}><td className="td text-xs">{dt(s.created_at)}</td><td className="td text-xs">{s.station_name}</td><td className="td text-sm">{PRODUCTS[s.product]}</td>
                <td className="td text-right tabular-nums">{num(s.litres, 2)}</td><td className="td text-right tabular-nums">{pkr(s.amount)}</td>
                <td className="td text-xs capitalize">{s.payment_method}</td><td className="td text-xs">{s.customer_name ?? "—"}</td></tr>
            ))}</tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
