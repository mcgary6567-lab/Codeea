import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { useApi } from "../lib/api";
import { Loading, PageHeader } from "../components/ui";
import { pkr, num } from "../lib/format";

const Ur = ({ children, className = "" }: { children: React.ReactNode; className?: string }) => <span lang="ur" dir="rtl" className={`font-urdu ${className}`}>{children}</span>;

/**
 * Profit explainer: shows the owner, in plain words, exactly how this month's profit is built —
 * income minus cost of fuel/shop minus expenses = net profit. Every line opens to its detail.
 */
export default function ProfitExplainer() {
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7));
  const { data } = useApi<any>(`/analysis/pl?month=${month}`);
  const m = useApi<any>(`/analysis/margins?month=${month}`);
  const [open, setOpen] = useState<string | null>("income");
  if (!data) return <Loading />;
  const p = data.pl;
  const toggle = (k: string) => setOpen((x) => (x === k ? null : k));

  // one row of the waterfall: label, signed amount, colour, and an openable detail panel
  const Row = ({ id, sign, label, ur, amount, tone, detail }: { id: string; sign: "+" | "−" | "="; label: string; ur: string; amount: number; tone?: "green" | "red" | "bold"; detail?: React.ReactNode }) => (
    <div className={`${sign === "=" ? "border-t-2 border-slate-300" : "border-t border-slate-100"}`}>
      <button type="button" onClick={() => detail && toggle(id)} className={`flex w-full items-center gap-3 px-4 py-3 text-left ${detail ? "hover:bg-slate-50" : "cursor-default"}`}>
        <span className={`w-5 text-center text-lg font-bold ${sign === "−" ? "text-red-500" : sign === "+" ? "text-emerald-600" : "text-slate-400"}`}>{sign}</span>
        <span className={`flex-1 ${sign === "=" ? "text-base font-bold" : "font-medium"}`}>{label} <Ur className="text-sm font-normal text-slate-500">{ur}</Ur></span>
        <span className={`tabular-nums ${sign === "=" ? "text-xl font-extrabold" : "font-semibold"} ${tone === "red" ? "text-red-600" : tone === "green" ? "text-emerald-700" : ""}`}>{pkr(amount)}</span>
        {detail && <ChevronDown size={16} className={`shrink-0 text-slate-400 transition ${open === id ? "rotate-180" : ""}`} />}
      </button>
      {detail && open === id && <div className="bg-slate-50 px-4 pb-3 pt-1 text-sm">{detail}</div>}
    </div>
  );

  const fuels = m.data?.products ?? [];
  const detailTable = (rows: React.ReactNode) => <table className="w-full"><tbody>{rows}</tbody></table>;
  const line = (a: string, b: string, cls = "") => <tr><td className="py-0.5 pr-2 text-slate-600">{a}</td><td className={`py-0.5 text-right tabular-nums ${cls}`}>{b}</td></tr>;

  return (
    <div>
      <PageHeader title="Profit explainer · منافع کی تفصیل" subtitle="Aap ka munafa kahan se aur kaise banta hai — step by step. Har line par click karke tafseel dekhein."
        actions={<input type="month" className="input w-auto" value={month} onChange={(e) => setMonth(e.target.value)} />} />

      {/* hero */}
      <div className={`mb-4 rounded-2xl p-5 text-white ${p.net_profit >= 0 ? "bg-emerald-700" : "bg-rose-700"}`}>
        <div className="text-sm opacity-90">Is mahine ka net munafa · <Ur>خالص منافع</Ur></div>
        <div className="text-4xl font-extrabold tabular-nums">{pkr(p.net_profit)}</div>
        <div className="mt-1 text-sm opacity-90">Margin {num(p.margin_pct, 1)}% · {num(p.litres.retail + p.litres.wholesale, 0)} L bika · revenue {pkr(p.income.total)}</div>
      </div>

      {/* waterfall */}
      <div className="card overflow-hidden">
        <Row id="income" sign="+" label="Total income" ur="کل آمدن" amount={p.income.total} tone="green" detail={detailTable(<>
          {line("Fuel — retail (POS)", pkr(p.income.fuel_retail))}
          {line("Fuel — wholesale", pkr(p.income.fuel_wholesale))}
          {line("Shop & lubricants", pkr(p.income.shop))}
          {p.discount_given > 0 && line("Khata discount diya (fuel se minus ho chuka)", `− ${pkr(p.discount_given)}`)}
          {fuels.length > 0 && <tr><td colSpan={2} className="pt-2 text-xs font-semibold text-slate-500">Per fuel (sale rate)</td></tr>}
          {fuels.map((f: any) => line(`${f.name} — ${num(f.litres, 0)} L`, `${pkr(f.revenue)} @ Rs ${f.avg_sale_rate.toFixed(2)}`))}
        </>)} />

        <Row id="fuelcost" sign="−" label="Cost of fuel sold" ur="ایندھن کی لاگت" amount={p.cost_of_sales.fuel} tone="red" detail={detailTable(<>
          <tr><td colSpan={2} className="pb-1 text-xs text-slate-500">Weighted-average purchase cost × litres bike (120 din ka avg).</td></tr>
          {fuels.map((f: any) => line(`${f.name} — ${num(f.litres, 0)} L × Rs ${f.avg_cost.toFixed(2)}`, pkr(round(f.litres * f.avg_cost))))}
        </>)} />

        {p.cost_of_sales.shop > 0 && <Row id="shopcost" sign="−" label="Cost of shop items" ur="دکان کی لاگت" amount={p.cost_of_sales.shop} tone="red" />}
        {p.cost_of_sales.stock_gain_loss !== 0 && <Row id="dip" sign={p.cost_of_sales.stock_gain_loss >= 0 ? "+" : "−"} label={p.cost_of_sales.stock_gain_loss >= 0 ? "Stock gain (dip)" : "Stock loss (dip)"} ur="اسٹاک کمی/زیادتی" amount={Math.abs(p.cost_of_sales.stock_gain_loss)} tone={p.cost_of_sales.stock_gain_loss >= 0 ? "green" : "red"} detail={<p className="text-slate-600">Dip aur book stock ka farq (zyada = gain, kam = chori/evaporation ka loss), avg cost par value hoke munafe par asar.</p>} />}

        <Row id="gross" sign="=" label="Gross profit" ur="مجموعی منافع" amount={p.gross_profit} tone={p.gross_profit >= 0 ? "green" : "red"} />

        {p.other_income > 0 && <Row id="other" sign="+" label="Other income (rent, carriage)" ur="دیگر آمدن" amount={p.other_income} tone="green" detail={<p className="text-slate-600">Dukaan ka kiraya + carriage/kiraya income (fuel ke ilawa).</p>} />}

        <Row id="exp" sign="−" label="Expenses" ur="اخراجات" amount={p.expenses.total} tone="red" detail={detailTable(
          (p.expenses.by_category ?? []).length
            ? (p.expenses.by_category as any[]).map((c) => line(c.category, pkr(c.amount)))
            : line("Koi kharcha nahi", "—"))} />

        <Row id="net" sign="=" label="NET PROFIT" ur="خالص منافع" amount={p.net_profit} tone={p.net_profit >= 0 ? "green" : "red"} />
      </div>

      {/* plain formula */}
      <div className="card mt-4 p-4 text-sm text-slate-600">
        <div className="mb-1 font-semibold text-slate-800">Formula (seedhi baat)</div>
        <p>Net profit = <b>Total income</b> − <b>Cost of fuel</b> − <b>Cost of shop</b> (± stock dip) <b>+ Other income</b> (rent, carriage) − <b>Expenses</b>.</p>
        <p className="mt-1">Fuel ki cost = har product ki <b>pichhle 120 din ki average purchase rate</b> × us mahine bike litres. Is liye munafa sahi aane ke liye <b>har tanker par purchase rate theek daalna</b> zaroori hai — warna us fuel ka cost "—" aata hai.</p>
        <p className="mt-1 text-xs text-slate-500">Expenses mein manual kharche + <b>bank card fee (MDR 1.8%)</b> + bank charges + staff bonus sab shaamil hain (ledger se). Wholesale/bypass ka munafa income mein pehle se hai.</p>
      </div>
    </div>
  );
}

const round = (n: number) => Math.round(n);
