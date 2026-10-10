import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi, useLiveEvents } from "../lib/api";
import { Badge, Empty, Loading, PageHeader, statusTone, useAction } from "../components/ui";
import { dt, num, phone, pkr } from "../lib/format";

const NEXT: Record<string, string[]> = { pending: ["confirmed", "cancelled"], confirmed: ["dispatched", "cancelled"], dispatched: ["delivered"] };

export default function Orders() {
  const { data, reload } = useApi<any[]>("/orders");
  const stations = useApi<any[]>("/stations");
  const { busy, run } = useAction();
  const [stationId, setStationId] = useState<number | "">("");
  useLiveEvents((e) => e.type === "order" && reload());
  if (!data) return <Loading />;
  const buttons = (o: any) => (NEXT[o.status] ?? []).map((s) => (
    <button key={s} disabled={busy} className={`${s === "cancelled" ? "btn-secondary" : "btn-primary"} min-h-9 !px-2.5 !py-1 text-xs sm:min-h-0`}
      onClick={() => run(() => api(`/orders/${o.id}`, { method: "PATCH", body: { status: s, station_id: stationId || stations.data?.[0]?.id } }), `Order #${o.id} ${s} — customer notified`).then(reload)}>
      {s === "confirmed" ? "Confirm" : s === "dispatched" ? "Dispatch" : s === "delivered" ? "Mark delivered" : "Cancel"}
    </button>
  ));

  return (
    <div>
      <PageHeader title="Fuel orders" subtitle="Bulk & delivery orders booked by the WhatsApp AI agent. Customers get a WhatsApp update at each step."
        actions={<label className="flex items-center gap-2 text-sm text-slate-600">Deliver from
          <select className="input w-auto" value={stationId} onChange={(e) => setStationId(Number(e.target.value))}>
            {(stations.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>} />
      {/* phone: one card per order */}
      <ul className="card divide-y divide-slate-100 sm:hidden">
        {data.map((o) => (
          <li key={o.id} className="px-4 py-3">
            <div className="flex items-start justify-between gap-2">
              <span className="min-w-0"><Link to={`/customers/${o.customer_id}`} className="block font-semibold hover:underline">{o.customer_name}</Link><span className="text-xs text-slate-500">#{o.id} · {dt(o.created_at)} · {phone(o.phone)}</span></span>
              <span className="shrink-0 text-right"><span className="block font-semibold tabular-nums">{pkr(o.amount)}</span><span className="text-xs text-slate-500">{num(o.litres)} L {o.product}</span></span>
            </div>
            <div className="mt-0.5 break-words text-xs text-slate-500">{o.address}{o.deliver_at ? ` · ${o.deliver_at}` : ""} · <span className="capitalize">{o.payment}</span> · @ Rs {o.rate}</div>
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              <Badge tone={statusTone(o.status)}>{o.status}</Badge>{o.source === "whatsapp" && <span className="text-xs text-slate-400">via WhatsApp</span>}
              <span className="ml-auto flex flex-wrap gap-1.5">{buttons(o)}</span>
            </div>
          </li>
        ))}
        {!data.length && <li><Empty>No orders yet. Customers can order on WhatsApp: "500 litre diesel chahiye".</Empty></li>}
      </ul>
      <div className="card hidden overflow-x-auto sm:block">
        <table className="w-full">
          <thead><tr><th className="th">#</th><th className="th">Customer</th><th className="th">Order</th><th className="th">Deliver</th><th className="th">Payment</th><th className="th">Status</th><th className="th">Actions</th></tr></thead>
          <tbody>
            {data.map((o) => (
              <tr key={o.id}>
                <td className="td text-xs text-slate-500">#{o.id}<br />{dt(o.created_at)}</td>
                <td className="td"><Link to={`/customers/${o.customer_id}`} className="font-medium hover:underline">{o.customer_name}</Link><div className="text-xs text-slate-500">{phone(o.phone)}</div></td>
                <td className="td"><div className="font-medium">{num(o.litres)} L {o.product}</div><div className="text-xs text-slate-500">{pkr(o.amount)} @ Rs {o.rate}</div></td>
                <td className="td text-sm">{o.address}<div className="text-xs text-slate-500">{o.deliver_at}</div></td>
                <td className="td text-sm capitalize">{o.payment}</td>
                <td className="td"><Badge tone={statusTone(o.status)}>{o.status}</Badge> {o.source === "whatsapp" && <span className="text-xs text-slate-400">via WhatsApp</span>}</td>
                <td className="td">
                  <div className="flex flex-wrap gap-1">{buttons(o)}</div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!data.length && <Empty>No orders yet. Customers can order on WhatsApp: "500 litre diesel chahiye".</Empty>}
      </div>
    </div>
  );
}
