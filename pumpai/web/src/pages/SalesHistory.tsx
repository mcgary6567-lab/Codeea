import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Download } from "lucide-react";
import { useApi } from "../lib/api";
import { Empty, ListTools, Loading, PageHeader, useListFilter } from "../components/ui";
import { Ur } from "../components/VoiceShell";
import { dt, num, pkr } from "../lib/format";
import { useAuth } from "../App";

const today = () => new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10);
const PRODUCT: Record<string, string> = { PMG: "Petrol", HSD: "Diesel", HOBC: "Hi-Octane", CNG: "CNG", LPG: "LPG" };

/** Every fuel sale in a date range: search by customer / gaari / slip / salesman, chips per method and product, totals, CSV. */
export default function SalesHistory() {
  const { can } = useAuth();
  const [from, setFrom] = useState(today()), [to, setTo] = useState(today());
  const [q, setQ] = useState(""), [dq, setDq] = useState("");
  const [method, setMethod] = useState("all"), [product, setProduct] = useState("all"), [station, setStation] = useState("");
  useEffect(() => { const h = setTimeout(() => setDq(q.trim()), 300); return () => clearTimeout(h); }, [q]);
  const params = new URLSearchParams({ from, to, limit: "500" });
  if (dq) params.set("q", dq); if (station) params.set("station_id", station);
  const { data } = useApi<any>(`/sales/history?${params}`);
  const stations = useApi<any>("/stations");
  const rows = useListFilter<any>(data?.rows, { keep: (r) => (method === "all" || r.payment_method === method) && (product === "all" || r.product === product) });
  const methods: string[] = [...new Set<string>((data?.rows ?? []).map((r: any) => String(r.payment_method)))];
  const products: string[] = [...new Set<string>((data?.rows ?? []).map((r: any) => String(r.product)))];
  const shownAmt = rows.reduce((a, r) => a + r.amount, 0), shownL = rows.reduce((a, r) => a + r.litres, 0);
  const csv = () => {
    const esc = (v: unknown) => `"${String(v ?? "").replaceAll('"', '""')}"`;
    const lines = [["Date", "Station", "Product", "Litres", "Rate", "Amount", "Method", "Customer", "Vehicle", "Slip", "Salesman"].map(esc).join(","),
      ...rows.map((r) => [dt(r.created_at), r.station_name, PRODUCT[r.product] ?? r.product, r.litres, r.rate, r.amount, r.payment_method, r.customer_name ?? "", r.vehicle_no ?? "", r.slip_no ?? "", r.attendant ?? ""].map(esc).join(","))];
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" })); a.download = `sales-${from}-${to}.csv`; a.click();
  };
  return (
    <div className="space-y-4">
      <PageHeader title="Sales history · سیل کی تاریخ" subtitle="Har fuel sale — customer, gaari number, slip ya salesman se dhoondein; date range, method aur product ke hisaab se."
        actions={<button className="btn-secondary" onClick={csv} disabled={!rows.length}><Download size={15} /> CSV</button>} />
      <ListTools q={q} onQ={setQ} placeholder="Customer, gaari no, slip, salesman, amount…" from={from} to={to} onFrom={setFrom} onTo={setTo}
        chips={[{ k: "all", label: "All" }, ...methods.map((m) => ({ k: m, label: m }))]} chip={method} onChip={setMethod}
        shown={rows.length} total={data?.rows?.length}
        right={<>
          {products.length > 1 && <div className="flex gap-1">{["all", ...products].map((p) => <button key={p} type="button" onClick={() => setProduct(p)} className={`rounded-full px-2.5 py-1 text-xs font-semibold ${product === p ? "bg-slate-800 text-white" : "bg-slate-100 text-slate-600"}`}>{p === "all" ? "Sab fuel" : PRODUCT[p] ?? p}</button>)}</div>}
          {(stations.data?.length ?? 0) > 1 && <select className="input w-auto" value={station} onChange={(e) => setStation(e.target.value)}><option value="">All stations</option>{stations.data.map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>}
        </>} />
      {!data ? <Loading /> : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="card p-3"><div className="text-xs text-slate-500">Sales · <Ur>سیلز</Ur></div><div className="text-lg font-bold tabular-nums">{rows.length}</div></div>
            <div className="card p-3"><div className="text-xs text-slate-500">Litres</div><div className="text-lg font-bold tabular-nums">{num(shownL, 1)} L</div></div>
            <div className="card p-3"><div className="text-xs text-slate-500">Amount · <Ur>رقم</Ur></div><div className="text-lg font-bold tabular-nums">{pkr(shownAmt)}</div></div>
            <div className="card p-3"><div className="text-xs text-slate-500">Range total (server)</div><div className="text-lg font-bold tabular-nums">{pkr(data.totals.amount)}</div><div className="text-[11px] text-slate-500">{data.totals.n} sales · {num(data.totals.litres, 0)} L{data.rows.length >= data.limit ? ` · pehli ${data.limit} dikh rahi hain` : ""}</div></div>
          </div>
          {data.totals.by_method?.length > 1 && <div className="flex flex-wrap gap-2 text-xs">{data.totals.by_method.map((m: any) => <span key={m.method} className="rounded-full bg-slate-100 px-2.5 py-1">{m.method}: <b className="tabular-nums">{pkr(m.amount)}</b> ({m.n})</span>)}</div>}
          {!rows.length ? <Empty>Is range me koi sale nahi mili · <Ur>کوئی سیل نہیں ملی</Ur></Empty> : (
            <div className="card overflow-x-auto">
              <table className="w-full min-w-[760px] text-sm">
                <thead><tr><th className="th">Date</th><th className="th">Station</th><th className="th">Fuel</th><th className="th text-right">Litres</th><th className="th text-right">Rate</th><th className="th text-right">Amount</th><th className="th">Method</th><th className="th">Customer / gaari</th><th className="th">Slip</th><th className="th">Salesman</th></tr></thead>
                <tbody>{rows.map((r) => (
                  <tr key={r.id} className="border-t border-slate-100 hover:bg-slate-50">
                    <td className="td whitespace-nowrap">{dt(r.created_at)}{r.at_close ? <span className="ml-1 rounded bg-slate-100 px-1 text-[10px]">close</span> : null}</td>
                    <td className="td">{r.station_name}</td><td className="td">{PRODUCT[r.product] ?? r.product}</td>
                    <td className="td text-right tabular-nums">{num(r.litres, 2)}</td><td className="td text-right tabular-nums">{r.rate}</td>
                    <td className="td text-right font-semibold tabular-nums">{pkr(r.amount)}{r.discount ? <span className="block text-[10px] font-normal text-rose-600">disc {pkr(r.discount)}</span> : null}</td>
                    <td className="td">{r.payment_method}</td>
                    <td className="td">{r.customer_id && can("customers.view") ? <Link className="text-brand-700 underline" to={`/customers/${r.customer_id}`}>{r.customer_name}</Link> : r.customer_name ?? ""}{r.vehicle_no ? <span className="block text-xs text-slate-500">{r.vehicle_no}</span> : null}</td>
                    <td className="td">{r.slip_no ?? ""}</td>
                    <td className="td">{r.attendant ?? ""}{can("entries.edit") ? <Link className="ml-2 text-xs text-brand-700 underline" to={`/corrections?q=${encodeURIComponent(r.vehicle_no || r.customer_name || r.slip_no || "")}`}>fix</Link> : null}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
