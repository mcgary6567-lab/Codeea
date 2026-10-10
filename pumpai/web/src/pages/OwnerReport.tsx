import { useState } from "react";
import { Printer } from "lucide-react";
import { useApi } from "../lib/api";
import { Loading, PageHeader } from "../components/ui";
import { PrintFooter, PrintHeader } from "../components/Letterhead";
import { pkr, num } from "../lib/format";

/**
 * One-page monthly report for the owner: profit & loss, balance sheet, khata aging and tank stock gain/loss —
 * all on the pump's letterhead, ready to Print or Save as PDF (and send on WhatsApp).
 */
const monthEnd = (m: string) => { const [y, mo] = m.split("-").map(Number); return `${m}-${String(new Date(Date.UTC(y, mo, 0)).getUTCDate()).padStart(2, "0")}`; };
const monthName = (m: string) => new Date(`${m}-01T00:00:00`).toLocaleString("en-PK", { month: "long", year: "numeric" });

export default function OwnerReport() {
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7));
  const pl = useApi<any>(`/analysis/pl?month=${month}`);
  const aging = useApi<any>("/khata/aging");
  const gl = useApi<any>(`/analysis/gain-loss?from=${month}-01&to=${monthEnd(month)}`);
  const p = pl.data?.pl, bs = pl.data?.balance_sheet;

  const Row = ({ label, value, bold, tone }: { label: string; value: number; bold?: boolean; tone?: "red" | "green" }) => (
    <div className={`flex items-center justify-between py-1 ${bold ? "border-t border-slate-300 font-semibold" : ""}`}>
      <span className={bold ? "" : "text-slate-600"}>{label}</span>
      <span className={`tabular-nums ${tone === "red" ? "text-red-600" : tone === "green" ? "text-emerald-700" : ""}`}>{pkr(value)}</span>
    </div>
  );
  const lossTanks = ((gl.data?.tanks ?? []) as any[]).filter((t) => t.gain_loss_l < 0);

  return (
    <div>
      <div className="print:hidden">
        <PageHeader title="Owner's monthly report · مالک کی ماہانہ رپورٹ" subtitle="P&L, balance sheet, khata aging aur stock loss — ek safhe par, letterhead ke saath"
          actions={<div className="flex items-center gap-2">
            <input type="month" className="input w-auto" value={month} onChange={(e) => setMonth(e.target.value)} />
            <button className="btn-primary" onClick={() => window.print()}><Printer size={15} /> Print / PDF</button>
          </div>} />
      </div>

      {!p || !bs ? <Loading /> : (
        <div className="card mx-auto max-w-3xl p-5 print:border-0 print:shadow-none">
          <PrintHeader />
          <h2 className="mb-1 mt-2 text-center text-lg font-bold">Monthly Report — {monthName(month)}</h2>

          <div className="mt-4 grid gap-5 sm:grid-cols-2">
            {/* Profit & loss */}
            <section>
              <h3 className="mb-1 font-bold text-brand-800">Profit &amp; Loss</h3>
              <Row label="Fuel (retail)" value={p.income.fuel_retail} />
              <Row label="Fuel (wholesale)" value={p.income.fuel_wholesale} />
              <Row label="Shop & lubricants" value={p.income.shop} />
              <Row label="Total income" value={p.income.total} bold />
              <div className="mt-2" />
              <Row label="Cost of fuel" value={p.cost_of_sales.fuel} />
              <Row label="Cost of shop" value={p.cost_of_sales.shop} />
              {p.cost_of_sales.stock_gain_loss ? <Row label="Stock gain/loss" value={p.cost_of_sales.stock_gain_loss} /> : null}
              <Row label="Gross profit" value={p.gross_profit} bold tone={p.gross_profit >= 0 ? "green" : "red"} />
              {p.other_income ? <Row label="+ Other income (rent, carriage)" value={p.other_income} tone="green" /> : null}
              <Row label="Expenses (incl. card fee)" value={p.expenses.total} tone="red" />
              <Row label="NET PROFIT" value={p.net_profit} bold tone={p.net_profit >= 0 ? "green" : "red"} />
              <div className="mt-1 text-right text-xs text-slate-500">Margin {num(p.margin_pct, 1)}% · {num(p.litres.retail + p.litres.wholesale, 0)} L sold</div>
            </section>

            {/* Balance sheet */}
            <section>
              <h3 className="mb-1 font-bold text-brand-800">Balance Sheet (aaj)</h3>
              <div className="text-xs font-semibold text-slate-500">Assets</div>
              {Object.entries(bs.assets).filter(([, v]) => (v as number) !== 0).map(([k, v]) => <Row key={k} label={k.replace(/_/g, " ")} value={v as number} />)}
              <Row label="Total assets" value={bs.total_assets} bold />
              <div className="mt-2 text-xs font-semibold text-slate-500">Liabilities</div>
              {Object.entries(bs.liabilities).filter(([, v]) => (v as number) !== 0).map(([k, v]) => <Row key={k} label={k.replace(/_/g, " ")} value={v as number} />)}
              <Row label="Total liabilities" value={bs.total_liabilities} bold />
              <Row label="NET WORTH" value={bs.net_worth} bold tone={bs.net_worth >= 0 ? "green" : "red"} />
            </section>

            {/* Khata aging */}
            <section>
              <h3 className="mb-1 font-bold text-brand-800">Khata aging (udhaar ki umar)</h3>
              {aging.data ? <>
                {aging.data.buckets.map((b: any) => <Row key={b.bucket} label={b.bucket} value={b.amount} tone={b.bucket === "90+ days" ? "red" : undefined} />)}
                <Row label="Total receivable" value={aging.data.total} bold />
                <Row label="Overdue (31+ din)" value={aging.data.overdue} tone="red" />
                {aging.data.list.slice(0, 5).some((c: any) => c.overdue > 0) && <>
                  <div className="mt-2 text-xs font-semibold text-slate-500">Top overdue customers</div>
                  {aging.data.list.filter((c: any) => c.overdue > 0).slice(0, 5).map((c: any) => (
                    <div key={c.id} className="flex justify-between py-0.5 text-sm"><span className="truncate pr-2">{c.name}</span><span className="tabular-nums text-red-600">{pkr(c.overdue)}</span></div>
                  ))}
                </>}
              </> : <div className="text-sm text-slate-500">Loading…</div>}
            </section>

            {/* Stock loss */}
            <section>
              <h3 className="mb-1 font-bold text-brand-800">Stock gain / loss (is mahine)</h3>
              {!gl.data ? <div className="text-sm text-slate-500">Loading…</div> : !lossTanks.length ? <div className="text-sm text-slate-600">Koi khaas loss nahi. Sab tank theek. ✅</div> : (
                <table className="w-full text-sm">
                  <thead><tr><th className="th">Tank</th><th className="th text-right">Loss (L)</th><th className="th text-right">%</th><th className="th text-right">Value</th></tr></thead>
                  <tbody>{lossTanks.map((t) => (
                    <tr key={t.id}><td className="td">{t.name} <span className="text-xs text-slate-400">{t.product}</span></td>
                      <td className="td text-right tabular-nums text-red-600">{num(t.gain_loss_l, 1)}</td>
                      <td className={`td text-right tabular-nums ${t.status === "leak_suspected" ? "font-semibold text-red-600" : ""}`}>{num(t.pct, 2)}%</td>
                      <td className="td text-right tabular-nums">{pkr(Math.abs(t.value))}</td></tr>
                  ))}</tbody>
                </table>
              )}
              <div className="mt-2 text-xs text-slate-500">Staff advances outstanding: {pkr(bs.assets.staff_advances ?? 0)}</div>
            </section>
          </div>

          <PrintFooter />
        </div>
      )}
    </div>
  );
}
