import { useState } from "react";
import { useApi } from "../lib/api";
import { Loading, PageHeader, SearchBar, Stat, matchQuery } from "../components/ui";
import { PRODUCTS, pkr, num, dt } from "../lib/format";

/** Who gave khata discounts and limit-overrides, and how much — a check on the two money "levers". */
export default function DiscountReport() {
  const [days, setDays] = useState(30);
  const to = new Date().toISOString();
  const from = new Date(Date.now() - days * 86_400_000).toISOString();
  const { data } = useApi<any>(`/reports/discounts?from=${from}&to=${to}`);
  const [q, setQ] = useState("");
  if (!data) return <Loading />;
  return (
    <div>
      <PageHeader title="Discounts & overrides · رعایت و اجازت" subtitle="Khata discount, POS par direct discount (reference ke saath) aur vehicle-limit override kis ne diya — CEO ki nazar"
        actions={<select className="input w-auto" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[[7, "7 din"], [30, "30 din"], [90, "90 din"], [365, "1 saal"]].map(([d, l]) => <option key={d} value={d}>{l}</option>)}
        </select>} />
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Total discount diya" value={pkr(data.discount_total)} tone="amber" hint={`${data.discount_count} sales`} />
        <Stat label="Khata discount" value={pkr(data.khata_total ?? 0)} />
        <Stat label="Direct discount (POS)" value={pkr(data.direct_total ?? 0)} tone="red" hint={`${data.direct_count ?? 0} sales`} />
        <Stat label="Limit overrides" value={data.override_count} tone="red" />
      </div>
      <SearchBar value={q} onChange={setQ} placeholder="Reference, person ya customer dhoondein…" className="mb-4" />

      {data.by_reference?.length > 0 && (
        <div className="card mb-4 p-4">
          <h2 className="mb-2 font-semibold">Direct discount by reference · <span className="font-urdu" lang="ur" dir="rtl">کس کو دی</span></h2>
          <table className="w-full text-sm">
            <thead><tr><th className="th">Reference (kis ko / kyun)</th><th className="th text-right">Times</th><th className="th text-right">Total</th></tr></thead>
            <tbody>{data.by_reference.filter((c: any) => matchQuery(q, c.ref)).map((c: any) => (
              <tr key={c.ref}><td className="td">{c.ref}</td><td className="td text-right tabular-nums">{c.count}</td><td className="td text-right font-semibold tabular-nums text-rose-700">{pkr(c.total)}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}

      {data.by_person.length > 0 && (
        <div className="card mb-4 p-4">
          <h2 className="mb-2 font-semibold">Discount by person</h2>
          <table className="w-full text-sm">
            <thead><tr><th className="th">Name</th><th className="th">Role</th><th className="th text-right">Count</th><th className="th text-right">Total</th></tr></thead>
            <tbody>{data.by_person.filter((p: any) => matchQuery(q, p.name, p.role)).map((p: any) => (
              <tr key={p.name}><td className="td">{p.name}</td><td className="td capitalize text-slate-500">{p.role}</td>
                <td className="td text-right tabular-nums">{p.count}</td><td className="td text-right font-semibold tabular-nums">{pkr(p.total)}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}

      {data.by_customer?.length > 0 && (
        <div className="card mb-4 p-4">
          <h2 className="mb-2 font-semibold">Discount by khata customer</h2>
          <table className="w-full text-sm">
            <thead><tr><th className="th">Customer</th><th className="th text-right">Times</th><th className="th text-right">Total discount</th></tr></thead>
            <tbody>{data.by_customer.filter((c: any) => matchQuery(q, c.name)).map((c: any) => (
              <tr key={c.name}><td className="td">{c.name}</td><td className="td text-right tabular-nums">{c.count}</td><td className="td text-right font-semibold tabular-nums text-amber-700">{pkr(c.total)}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr><th className="th">When</th><th className="th">Customer / reference</th><th className="th">Fuel</th><th className="th text-right">Litres</th><th className="th text-right">Discount</th><th className="th">Kind</th><th className="th">Override</th><th className="th">By</th></tr></thead>
          <tbody>{data.list.map((r: any) => (
            <tr key={r.id}>
              <td className="td text-xs text-slate-500">{dt(r.created_at)}</td>
              <td className="td">{r.discount_kind === "direct" ? <>{r.discount_ref || <span className="text-red-600">no reference</span>}<span className="block text-xs text-slate-400">{r.payment_method}</span></> : <>{r.customer_name ?? "—"}{r.vehicle_no ? <span className="block text-xs text-slate-400">{r.vehicle_no}</span> : null}</>}</td>
              <td className="td">{PRODUCTS[r.product] ?? r.product}</td>
              <td className="td text-right tabular-nums">{num(r.litres, 2)}</td>
              <td className="td text-right tabular-nums font-semibold text-amber-700">{r.discount > 0 ? pkr(r.discount) : "—"}</td>
              <td className="td">{r.discount > 0 ? <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${r.discount_kind === "direct" ? "bg-rose-100 text-rose-700" : "bg-amber-100 text-amber-800"}`}>{r.discount_kind === "direct" ? "Direct" : "Khata"}</span> : "—"}</td>
              <td className="td">{r.over_limit ? <span className="rounded bg-red-100 px-1.5 py-0.5 text-xs font-semibold text-red-700">Limit over</span> : "—"}</td>
              <td className="td text-xs">{r.by_name ?? "—"}</td>
            </tr>
          ))}</tbody>
        </table>
        {!data.list.length && <p className="p-6 text-center text-sm text-slate-500">Is arse mein koi discount ya override nahi.</p>}
      </div>
    </div>
  );
}
