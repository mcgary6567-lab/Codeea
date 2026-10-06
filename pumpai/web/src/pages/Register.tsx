import { useState } from "react";
import { Printer } from "lucide-react";
import { useApi } from "../lib/api";
import { Loading, PageHeader, ErrorBox } from "../components/ui";
import { num } from "../lib/format";

const today = () => new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10);
const daysAgo = (n: number) => new Date(Date.now() + 5 * 3600_000 - n * 86_400_000).toISOString().slice(0, 10);
const L = (v: number | null | undefined) => (v == null ? "—" : num(v, 0));
/** time of the day's dip (Pakistan time), e.g. "6:00 pm" */
const at = (iso?: string | null) => (iso ? new Date(iso).toLocaleTimeString("en-PK", { timeZone: "Asia/Karachi", hour: "numeric", minute: "2-digit" }) : "");

/** Daily stock register and monthly return in the Explosives / OGRA format — made from the app's own records, ready to print. */
export default function Register() {
  const stations = useApi<any[]>("/stations");
  const [f, setF] = useState({ station_id: 0, mode: "days" as "days" | "month", from: daysAgo(6), to: today(), month: today().slice(0, 7) });
  const station = f.station_id || stations.data?.[0]?.id;
  const q = f.mode === "month" ? `month=${f.month}` : `from=${f.from}&to=${f.to}`;
  const r = useApi<any>(station ? `/register?station_id=${station}&${q}` : null);
  if (!stations.data) return <Loading />;
  const d = r.data;
  return (
    <div className="space-y-5">
      <div className="print:hidden">
        <PageHeader title="Stock register" subtitle="Daily stock register and monthly return (Explosives / OGRA format), made from sales, tankers and dips"
          actions={<button className="btn-primary" onClick={() => window.print()} disabled={!d}><Printer size={15} /> Print</button>} />
        <div className="card mt-4 flex flex-wrap items-end gap-3 p-3 text-sm">
          <label className="grid gap-1"><span className="text-slate-500">Station</span>
            <select className="input" value={station} onChange={(e) => setF({ ...f, station_id: Number(e.target.value) })}>
              {stations.data.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select></label>
          <div className="flex rounded-lg bg-slate-100 p-1">
            {(["days", "month"] as const).map((m) => <button key={m} onClick={() => setF({ ...f, mode: m })} className={`min-h-9 rounded-md px-3 py-1.5 ${f.mode === m ? "bg-white shadow font-semibold" : "text-slate-600"}`}>{m === "days" ? "Daily register" : "Monthly return"}</button>)}
          </div>
          {f.mode === "days" ? <>
            <label className="grid gap-1"><span className="text-slate-500">From</span><input type="date" className="input" value={f.from} max={f.to} onChange={(e) => setF({ ...f, from: e.target.value })} /></label>
            <label className="grid gap-1"><span className="text-slate-500">To</span><input type="date" className="input" value={f.to} max={today()} onChange={(e) => setF({ ...f, to: e.target.value })} /></label>
          </> : <label className="grid gap-1"><span className="text-slate-500">Month</span><input type="month" className="input" value={f.month} max={today().slice(0, 7)} onChange={(e) => setF({ ...f, month: e.target.value })} /></label>}
        </div>
      </div>
      {r.error && <ErrorBox error={r.error} />}
      {!d ? <Loading /> : (
        <div className="card p-4 print:p-0 print:shadow-none print:ring-0">
          <div className="border-b-2 border-slate-800 pb-2 text-center">
            <h1 className="text-xl font-bold uppercase tracking-wide">{f.mode === "month" ? "Monthly stock return" : "Daily stock register"} — petroleum products</h1>
            <div className="text-sm"><b>{d.tenant?.name}</b> · {d.station.name}{d.station.address ? `, ${d.station.address}` : ""}</div>
            <div className="text-sm">Period: <b>{d.from}</b> to <b>{d.to}</b></div>
            {d.licences.length > 0 && <div className="mt-1 text-xs text-slate-600">{d.licences.map((l: any) => `${l.name}${l.number ? ` No. ${l.number}` : ""}${l.authority ? ` (${l.authority})` : ""}, valid till ${l.expires_on}`).join(" · ")}</div>}
          </div>
          {f.mode === "month" && (<>
            {/* phone: one card per product */}
            <ul className="mt-4 divide-y divide-slate-200 border-y border-slate-800 text-sm sm:hidden print:hidden">
              {d.products.map((p: any) => (
                <li key={p.product} className="py-2">
                  <div className="flex items-start justify-between gap-2"><span className="min-w-0 font-semibold">{p.name}</span><span className="shrink-0 text-right"><span className="block font-semibold tabular-nums">{L(p.month.closing)} L</span><span className="text-[11px] text-slate-500">closing</span></span></div>
                  <div className="text-xs text-slate-500">Opening <b className="tabular-nums text-slate-700">{L(p.month.opening)}</b> · received <b className="tabular-nums text-slate-700">{L(p.month.receipts)}</b> · sold <b className="tabular-nums text-slate-700">{L(p.month.sales)}</b> · wholesale <b className="tabular-nums text-slate-700">{L(p.month.wholesale)}</b> · gain / loss <b className={`tabular-nums ${p.month.gain_loss < 0 ? "text-red-700" : "text-slate-700"}`}>{L(p.month.gain_loss)}</b> · variation <b className="tabular-nums text-slate-700">{p.month.variation_pct}%</b></div>
                </li>))}
            </ul>
            <table className="mt-4 hidden w-full text-sm sm:table print:table">
              <thead><tr className="border-b border-slate-800 text-left text-xs uppercase">
                <th className="py-1.5">Product</th><th className="text-right">Opening (L)</th><th className="text-right">Received (L)</th><th className="text-right">Sold retail (L)</th>
                <th className="text-right">Wholesale (L)</th><th className="text-right">Gain / loss (L)</th><th className="text-right">Closing (L)</th><th className="text-right">Variation</th>
              </tr></thead>
              <tbody>{d.products.map((p: any) => (
                <tr key={p.product} className="border-b border-slate-200 tabular-nums">
                  <td className="py-1.5 font-semibold">{p.name}</td><td className="text-right">{L(p.month.opening)}</td><td className="text-right">{L(p.month.receipts)}</td><td className="text-right">{L(p.month.sales)}</td>
                  <td className="text-right">{L(p.month.wholesale)}</td><td className={`text-right ${p.month.gain_loss < 0 ? "text-red-700" : ""}`}>{L(p.month.gain_loss)}</td><td className="text-right font-semibold">{L(p.month.closing)}</td>
                  <td className="text-right">{p.month.variation_pct}%</td>
                </tr>))}</tbody>
            </table>
          </>)}
          {d.products.map((p: any) => (
            <div key={p.product} className="mt-5 break-inside-avoid">
              <h2 className="font-semibold">{p.name} <span className="text-sm font-normal text-slate-500">· {p.tanks} tank{p.tanks > 1 ? "s" : ""}, capacity {L(p.capacity)} L</span></h2>
              {/* phone: one card per day */}
              <ul className="mt-1 divide-y divide-slate-200 rounded-lg border border-slate-300 sm:hidden print:hidden">
                {p.days.map((x: any) => (
                  <li key={x.day} className="px-3 py-2">
                    <div className="flex items-start justify-between gap-2"><span className="min-w-0 text-sm font-medium">{x.day}</span><span className="shrink-0 text-right"><span className="block font-semibold tabular-nums">{L(x.closing)} L</span><span className="text-[11px] text-slate-500">closing c/f</span></span></div>
                    <div className="text-xs text-slate-500">Opening <b className="tabular-nums text-slate-700">{L(x.opening)}</b>{x.receipts ? <> · received <b className="tabular-nums text-slate-700">{L(x.receipts)}</b></> : null} · sold <b className="tabular-nums text-slate-700">{L(x.sales)}</b>{x.wholesale ? <> · wholesale <b className="tabular-nums text-slate-700">{L(x.wholesale)}</b></> : null}</div>
                    <div className="text-xs text-slate-500">Book <b className="tabular-nums text-slate-700">{L(x.book_closing)}</b> · dip <b className="tabular-nums text-slate-700">{L(x.dip_closing)}</b>{x.dip_at ? <span className="text-slate-400"> at {at(x.dip_at)}</span> : null}{x.gain_loss ? <> · gain / loss <b className={`tabular-nums ${x.gain_loss < 0 ? "text-red-700" : "text-slate-700"}`}>{L(x.gain_loss)}</b></> : null}</div>
                    {x.receipts ? <div className="break-words text-[11px] text-slate-400">{x.receipt_lines.map((rl: any) => `${rl.tanker_no ?? ""} inv ${L(rl.invoice_l)}`).join("; ")}</div> : null}
                  </li>
                ))}
              </ul>
              <div className="hidden overflow-x-auto sm:block print:block">
                <table className="mt-1 w-full min-w-[760px] border border-slate-300 text-xs">
                  <thead className="bg-slate-50"><tr className="text-left">
                    {["Date", "Opening stock", "Received (tanker / invoice)", "Total", "Sold (meter)", "Wholesale", "Book closing", "Dip closing", "Gain / loss", "Closing c/f", "Sign"].map((h) =>
                      <th key={h} className="border border-slate-300 px-1.5 py-1 font-semibold">{h}</th>)}
                  </tr></thead>
                  <tbody className="tabular-nums">
                    {p.days.map((x: any) => (
                      <tr key={x.day}>
                        <td className="border border-slate-300 px-1.5 py-1 whitespace-nowrap">{x.day}</td>
                        <td className="border border-slate-300 px-1.5 text-right">{L(x.opening)}</td>
                        <td className="border border-slate-300 px-1.5">{x.receipts ? <>{L(x.receipts)}<span className="block text-[10px] text-slate-500">{x.receipt_lines.map((rl: any) => `${rl.tanker_no ?? ""} inv ${L(rl.invoice_l)}`).join("; ")}</span></> : "—"}</td>
                        <td className="border border-slate-300 px-1.5 text-right">{L(x.total)}</td>
                        <td className="border border-slate-300 px-1.5 text-right">{L(x.sales)}</td>
                        <td className="border border-slate-300 px-1.5 text-right">{x.wholesale ? L(x.wholesale) : "—"}</td>
                        <td className="border border-slate-300 px-1.5 text-right">{L(x.book_closing)}</td>
                        <td className="border border-slate-300 px-1.5 text-right">{L(x.dip_closing)}{x.dip_at ? <span className="block text-[10px] text-slate-500">{at(x.dip_at)}</span> : null}</td>
                        <td className={`border border-slate-300 px-1.5 text-right ${x.gain_loss < 0 ? "text-red-700" : ""}`}>{x.gain_loss ? L(x.gain_loss) : "—"}</td>
                        <td className="border border-slate-300 px-1.5 text-right font-semibold">{L(x.closing)}</td>
                        <td className="border border-slate-300 px-1.5 w-16" />
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
          <div className="mt-8 grid grid-cols-2 gap-8 text-sm">
            <div className="border-t border-slate-500 pt-1 text-center">Manager signature</div>
            <div className="border-t border-slate-500 pt-1 text-center">Licensee / owner signature & stamp</div>
          </div>
          <p className="mt-3 text-[11px] text-slate-500">Litres. Book closing = opening + received − sold − wholesale. Dip closing is the dip at the time shown (sales after it are in the book, not in the dip). Gain / loss is the dip correction on that day. Generated by PumpAI from the pump's own records.</p>
        </div>
      )}
    </div>
  );
}
