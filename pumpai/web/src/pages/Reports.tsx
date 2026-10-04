import { useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ResponsiveContainer, ComposedChart, Bar, Line, LineChart, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from "recharts";
import { Download, Printer, ArrowDownLeft, ArrowUpRight } from "lucide-react";
import { useApi } from "../lib/api";
import { Badge, Empty, ErrorBox, Loading, PageHeader, Stat } from "../components/ui";
import { PRODUCTS, PRODUCT_COLORS, ago, dt, num, pkr, pkrShort } from "../lib/format";

const DAY = 86_400_000;
const PRESETS = [
  { key: "24h", label: "24 hours", days: 1 }, { key: "7d", label: "7 days", days: 7 }, { key: "1m", label: "1 month", days: 30 },
  { key: "6m", label: "6 months", days: 182 }, { key: "1y", label: "1 year", days: 365 }, { key: "custom", label: "Custom", days: 0 },
];
const TABS = [
  ["overview", "Overview"], ["sales", "Sales"], ["stock", "Stock"], ["expenses", "Expenses"], ["receivables", "Receivables (owed to us)"],
  ["payables", "Payables (we owe)"], ["khata", "Khata"], ["wholesale", "Wholesale"], ["shifts", "Shifts & cash"],
] as const;
type Tab = (typeof TABS)[number][0];

/* ---------- tiny table + CSV helpers ---------- */
type Col = { h: string; v: (r: any) => ReactNode; csv?: (r: any) => unknown; right?: boolean };
type TableDef = { title: string; rows: any[]; cols: Col[] };

function Table({ title, rows, cols }: TableDef) {
  return (
    <div className="card overflow-hidden break-inside-avoid">
      <h3 className="px-4 pb-2 pt-3 text-sm font-semibold">{title} <span className="font-normal text-slate-400">({rows.length})</span></h3>
      {rows.length ? (
        <div className="max-h-[480px] overflow-auto print:max-h-none">
          <table className="w-full">
            <thead className="sticky top-0"><tr>{cols.map((c) => <th key={c.h} className={`th ${c.right ? "text-right" : ""}`}>{c.h}</th>)}</tr></thead>
            <tbody>{rows.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c.h} className={`td text-sm ${c.right ? "text-right tabular-nums" : ""}`}>{c.v(r)}</td>)}</tr>)}</tbody>
          </table>
        </div>
      ) : <Empty>Nothing in this period</Empty>}
    </div>
  );
}

function downloadCsv(name: string, tables: TableDef[]) {
  const esc = (v: unknown) => `"${String(v ?? "").replaceAll('"', '""')}"`;
  const text = (x: ReactNode | unknown) => (typeof x === "string" || typeof x === "number" ? x : "");
  const lines: string[] = [];
  for (const t of tables) {
    lines.push(esc(t.title), t.cols.map((c) => esc(c.h)).join(","));
    for (const r of t.rows) lines.push(t.cols.map((c) => esc(c.csv ? c.csv(r) : text(c.v(r)))).join(","));
    lines.push("");
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" }));
  a.download = `${name}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

const money = (k: string): Col => ({ h: "", v: (r) => pkr(r[k]), csv: (r) => Math.round(r[k] ?? 0), right: true });
const m = (h: string, k: string): Col => ({ ...money(k), h });
const l = (h: string, k: string): Col => ({ h, v: (r) => num(r[k]), csv: (r) => Math.round(r[k] ?? 0), right: true });
const txt = (h: string, k: string | ((r: any) => string)): Col => ({ h, v: (r) => (typeof k === "function" ? k(r) : r[k]) ?? "—" });

const localInput = (iso: string) => new Date(Date.parse(iso) + 5 * 3600_000).toISOString().slice(0, 16); // Pakistan time for <input type=datetime-local>
const fromLocal = (v: string) => new Date(Date.parse(v + ":00Z") - 5 * 3600_000).toISOString();

export default function Reports() {
  const [preset, setPreset] = useState("1m");
  const [custom, setCustom] = useState({ from: localInput(new Date(Date.now() - 7 * DAY).toISOString()), to: localInput(new Date().toISOString()) });
  const [tab, setTab] = useState<Tab>("overview");
  const [nowKey] = useState(() => Date.now());

  const { from, to } = useMemo(() => {
    if (preset === "custom") return { from: fromLocal(custom.from), to: fromLocal(custom.to) };
    const days = PRESETS.find((p) => p.key === preset)!.days;
    return { from: new Date(nowKey - days * DAY).toISOString(), to: new Date(nowKey).toISOString() };
  }, [preset, custom, nowKey]);

  const { data: r, error, loading } = useApi<any>(`/reports?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`);
  const tables = r ? tablesFor(tab, r) : [];
  const periodLabel = `${dt(from)} → ${dt(to)}`;

  return (
    <div className="space-y-5">
      <PageHeader title="Reports" subtitle={`Pakistan time · ${periodLabel}`}
        actions={<div className="flex gap-2 print:hidden">
          <button className="btn-secondary" disabled={!r} onClick={() => downloadCsv(`pumpai-${tab}-${from.slice(0, 10)}_${to.slice(0, 10)}`, tables)}><Download size={15} /> Excel / CSV</button>
          <button className="btn-secondary" onClick={() => window.print()}><Printer size={15} /> Print / PDF</button>
        </div>} />

      <div className="flex flex-wrap items-end gap-2 print:hidden">
        {PRESETS.map((p) => (
          <button key={p.key} onClick={() => setPreset(p.key)} className={`rounded-full px-4 py-1.5 text-sm ${preset === p.key ? "bg-brand-600 text-white" : "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>{p.label}</button>
        ))}
        {preset === "custom" && (
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-xs text-slate-600">From<input className="input mt-0.5" type="datetime-local" value={custom.from} onChange={(e) => e.target.value && setCustom({ ...custom, from: e.target.value })} /></label>
            <label className="text-xs text-slate-600">To<input className="input mt-0.5" type="datetime-local" value={custom.to} onChange={(e) => e.target.value && setCustom({ ...custom, to: e.target.value })} /></label>
          </div>
        )}
      </div>

      <div className="flex gap-1 overflow-x-auto border-b border-slate-200 print:hidden">
        {TABS.map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)} className={`whitespace-nowrap border-b-2 px-3 py-2 text-sm ${tab === k ? "border-brand-600 font-medium text-brand-700" : "border-transparent text-slate-600 hover:text-slate-900"}`}>{label}</button>
        ))}
      </div>
      <h2 className="hidden text-lg font-semibold print:block">{TABS.find(([k]) => k === tab)![1]}</h2>

      {error && <ErrorBox error={error} />}
      {!r || loading ? <Loading /> : (
        <div className="space-y-5">
          {tab === "overview" && <Overview r={r} />}
          {tab === "sales" && <SalesHead r={r} />}
          {tab === "stock" && <StockHead r={r} />}
          {tab === "expenses" && <ExpensesHead r={r} />}
          {tab === "receivables" && <ReceivablesHead r={r} />}
          {tab === "payables" && <PayablesHead r={r} />}
          {tab === "khata" && <div className="grid grid-cols-3 gap-3"><Stat label="Credit given" value={pkrShort(r.khata.given)} /><Stat label="Collected" value={pkrShort(r.khata.collected)} tone="green" /><Stat label="Net change" value={pkrShort(r.khata.net_change)} tone={r.khata.net_change > 0 ? "amber" : "green"} hint="Positive = more credit given than collected" /></div>}
          {tab === "wholesale" && <div className="grid grid-cols-2 gap-3 lg:grid-cols-4"><Stat label="Supplied" value={`${num(r.wholesale.supplied_l)} L`} hint={`Returned ${num(r.wholesale.returned_l)} L`} /><Stat label="Net billed" value={pkrShort(r.wholesale.net_billed)} /><Stat label="Received" value={pkrShort(r.wholesale.received)} tone="green" /><Stat label="Due now (all clients)" value={pkrShort(r.receivables.wholesale_total)} tone="amber" /></div>}
          {tab === "shifts" && <div className="grid grid-cols-2 gap-3 lg:grid-cols-4"><Stat label="Closed shifts" value={r.shifts.totals.n} /><Stat label="Cash expected" value={pkrShort(r.shifts.totals.expected)} /><Stat label="Cash counted" value={pkrShort(r.shifts.totals.counted)} /><Stat label="Total variance" value={pkr(r.shifts.totals.variance)} tone={r.shifts.totals.variance < -1000 ? "red" : "green"} /></div>}
          <div className={`grid gap-5 ${tables.length > 1 ? "xl:grid-cols-2" : ""}`}>{tables.map((t) => <Table key={t.title} {...t} />)}</div>
        </div>
      )}
    </div>
  );
}

/* ---------- headers / charts per tab ---------- */
function Overview({ r }: { r: any }) {
  const s = r.summary;
  const est = s.gross_profit_estimate != null;
  return (
    <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Total revenue" value={pkrShort(s.revenue)} tone="green" hint={`Retail ${pkrShort(s.retail_sales)} (${num(s.retail_litres)} L) · Wholesale ${pkrShort(s.wholesale_net)} (${num(s.wholesale_litres)} L)`} />
        <Stat label="Expenses" value={pkrShort(s.expenses)} tone="red" hint={`${r.expenses.count} entries`} />
        <Stat label="Gross profit (est.)" value={est ? pkrShort(s.gross_profit_estimate) : "—"} tone="blue" hint={est ? `Revenue − fuel cost ${pkrShort(s.fuel_cost_estimate)}` : "Add purchase rates on deliveries"} />
        <Stat label="Net profit (est.)" value={est ? pkrShort(s.net_profit_estimate) : "—"} tone={est && s.net_profit_estimate < 0 ? "red" : "green"} hint="Gross profit − expenses" />
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <div className="card p-4">
          <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold"><ArrowDownLeft size={15} className="text-emerald-600" /> Money in</h3>
          {[["Cash sales", s.money_in.cash_sales], ["Digital (JazzCash, Easypaisa, Raast, card)", s.money_in.digital_sales], ["Khata collected", s.money_in.khata_collected], ["Wholesale payments received", s.money_in.wholesale_received]].map(([k, v]) => <Row key={k as string} k={k as string} v={pkr(v as number)} />)}
          <Row k="Total" v={pkr(Object.values(s.money_in).reduce((a: number, b: any) => a + b, 0) as number)} bold />
        </div>
        <div className="card p-4">
          <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold"><ArrowUpRight size={15} className="text-red-600" /> Money out</h3>
          <Row k="Expenses" v={pkr(s.money_out.expenses)} />
          <Row k="Paid to fuel suppliers" v={pkr(s.money_out.supplier_payments)} />
          <Row k="Fuel purchased in period (cost)" v={pkr(s.purchases_cost)} muted />
          <Row k="Shift cash variance" v={pkr(s.cash_variance)} muted />
          <Row k="Total paid out" v={pkr(s.money_out.expenses + s.money_out.supplier_payments)} bold />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Owed to us (now)" value={pkrShort(r.receivables.total)} tone="amber" hint={`Khata ${pkrShort(r.receivables.khata_total)} · Wholesale ${pkrShort(r.receivables.wholesale_total)}`} />
        <Stat label="We owe (now)" value={pkrShort(r.payables.total)} tone="red" hint={`Suppliers ${pkrShort(r.payables.suppliers_total)}`} />
        <Stat label="Stock now" value={`${num(r.stock.products.reduce((a: number, p: any) => a + p.closing_l, 0))} L`} hint={r.stock.products.map((p: any) => `${p.name} ${num(p.closing_l)}`).join(" · ")} />
        <Stat label="Stock value (at cost)" value={r.stock.products.every((p: any) => p.closing_value != null) ? pkrShort(r.stock.products.reduce((a: number, p: any) => a + p.closing_value, 0)) : "—"} />
      </div>
      <div className="card p-4">
        <h3 className="mb-2 text-sm font-semibold">Revenue vs expenses by {r.grain}</h3>
        <div className="h-72">
          <ResponsiveContainer>
            <ComposedChart data={r.trend} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#e2e8f0" vertical={false} />
              <XAxis dataKey="bucket" tick={{ fontSize: 11, fill: "#64748b" }} tickLine={false} minTickGap={24} tickFormatter={(b: string) => r.grain === "hour" ? b.slice(11) : r.grain === "day" ? b.slice(5) : b} />
              <YAxis tick={{ fontSize: 11, fill: "#64748b" }} tickLine={false} axisLine={false} width={56} tickFormatter={(v) => pkrShort(v).replace("Rs ", "")} />
              <Tooltip formatter={(v: any, n: string) => [pkr(v), n]} contentStyle={{ borderRadius: 8, fontSize: 12 }} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="retail" name="Retail sales" stackId="rev" fill="#2a78d6" radius={[0, 0, 0, 0]} />
              <Bar dataKey="wholesale" name="Wholesale" stackId="rev" fill="#eb6834" radius={[4, 4, 0, 0]} />
              <Line dataKey="expenses" name="Expenses" stroke="#4a3aa7" strokeWidth={2} dot={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>
    </>
  );
}

const Row = ({ k, v, bold, muted }: { k: string; v: string; bold?: boolean; muted?: boolean }) => (
  <div className={`flex justify-between border-b border-slate-100 py-1.5 text-sm ${bold ? "font-semibold" : ""} ${muted ? "text-slate-500" : ""}`}><span>{k}</span><span className="tabular-nums">{v}</span></div>
);

function SalesHead({ r }: { r: any }) {
  return (
    <div className="card p-4">
      <h3 className="mb-2 text-sm font-semibold">Litres sold by {r.grain} (retail)</h3>
      <div className="h-64">
        <ResponsiveContainer>
          <LineChart data={r.trend} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid stroke="#e2e8f0" vertical={false} />
            <XAxis dataKey="bucket" tick={{ fontSize: 11, fill: "#64748b" }} tickLine={false} minTickGap={24} tickFormatter={(b: string) => r.grain === "hour" ? b.slice(11) : r.grain === "day" ? b.slice(5) : b} />
            <YAxis tick={{ fontSize: 11, fill: "#64748b" }} tickLine={false} axisLine={false} width={48} tickFormatter={(v) => num(v)} />
            <Tooltip formatter={(v: any, n: string) => [`${num(v)} L`, n]} contentStyle={{ borderRadius: 8, fontSize: 12 }} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            {Object.keys(PRODUCTS).map((p) => <Line key={p} dataKey={p} name={PRODUCTS[p]} stroke={PRODUCT_COLORS[p]} strokeWidth={2} dot={false} />)}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function StockHead({ r }: { r: any }) {
  return (
    <div className="card overflow-x-auto">
      <h3 className="px-4 pt-3 text-sm font-semibold">Stock movement (litres)</h3>
      <table className="w-full">
        <thead><tr>{["Product", "Opening", "+ Received", "+ Returns", "− Retail sold", "− Wholesale", "± Dip adj.", "= Closing", "Avg cost / L", "Closing value"].map((h, i) => <th key={h} className={`th ${i ? "text-right" : ""}`}>{h}</th>)}</tr></thead>
        <tbody>{r.stock.products.map((p: any) => (
          <tr key={p.product}>
            <td className="td font-medium">{p.name}</td>
            {[p.opening_l, p.received_l, p.returns_in_l, p.retail_sold_l, p.wholesale_out_l, p.dip_adjust_l].map((v, i) => <td key={i} className={`td text-right tabular-nums ${i === 5 && v < 0 ? "text-red-600" : ""}`}>{num(v)}</td>)}
            <td className="td text-right font-semibold tabular-nums">{num(p.closing_l)}</td>
            <td className="td text-right tabular-nums">{p.avg_cost ? `Rs ${p.avg_cost}` : "—"}</td>
            <td className="td text-right tabular-nums">{p.closing_value != null ? pkr(p.closing_value) : "—"}</td>
          </tr>
        ))}</tbody>
      </table>
      <p className="px-4 py-2 text-xs text-slate-500">Opening + received + returns − sold − wholesale ± dip adjustments = closing. Dip adjustment is the difference between physical dip and book stock (leakage, evaporation, meter error).</p>
    </div>
  );
}

function ExpensesHead({ r }: { r: any }) {
  const max = Math.max(1, ...r.expenses.by_category.map((c: any) => c.amount));
  return (
    <div className="grid gap-3 lg:grid-cols-[260px_1fr]">
      <Stat label="Total expenses" value={pkr(r.expenses.total)} tone="red" hint={`${r.expenses.count} entries · ${r.summary.revenue ? ((r.expenses.total / r.summary.revenue) * 100).toFixed(1) : 0}% of revenue`} />
      <div className="card space-y-1.5 p-4">
        {r.expenses.by_category.map((c: any) => (
          <div key={c.category} className="grid grid-cols-[180px_1fr_110px] items-center gap-2 text-xs">
            <span className="truncate">{c.category}</span>
            <div className="h-1.5 rounded-full bg-slate-100"><div className="h-full rounded-full bg-[#2a78d6]" style={{ width: `${(c.amount / max) * 100}%` }} /></div>
            <span className="text-right tabular-nums">{pkr(c.amount)}</span>
          </div>
        ))}
        {!r.expenses.by_category.length && <Empty>No expenses in this period</Empty>}
      </div>
    </div>
  );
}

function ReceivablesHead({ r }: { r: any }) {
  const rv = r.receivables;
  return (
    <>
      <p className="text-sm text-slate-600">Balances <b>as of now</b> (not limited to the selected period). Aging is days since the last payment.</p>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        <Stat label="Total owed to us" value={pkr(rv.total)} tone="amber" />
        <Stat label="Khata customers" value={pkr(rv.khata_total)} />
        <Stat label="Wholesale clients" value={pkr(rv.wholesale_total)} />
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        {rv.aging.map((a: any) => <Stat key={a.bucket} label={a.bucket} value={pkrShort(a.amount)} hint={`${a.count} accounts`} tone={a.bucket === "90+ days" && a.amount > 0 ? "red" : "slate"} />)}
      </div>
    </>
  );
}

function PayablesHead({ r }: { r: any }) {
  const p = r.payables;
  return (
    <>
      <p className="text-sm text-slate-600">What the business owes <b>as of now</b>: fuel suppliers, customer advances, and expenses still waiting for approval.</p>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        <Stat label="Total we owe" value={pkr(p.total)} tone="red" />
        <Stat label="Fuel suppliers" value={pkr(p.suppliers_total)} hint={<Link to="/suppliers" className="text-brand-600 hover:underline">Pay suppliers →</Link>} />
        <Stat label="Expenses awaiting approval" value={pkr(p.pending_expenses.amount)} hint={`${p.pending_expenses.count} entries`} tone="amber" />
      </div>
    </>
  );
}

/* ---------- tables per tab (also used for CSV export) ---------- */
function tablesFor(tab: Tab, r: any): TableDef[] {
  const prod = (k = "product"): Col => ({ h: "Product", v: (x) => PRODUCTS[x[k]] ?? x[k] });
  switch (tab) {
    case "overview":
      return [{ title: `Totals by ${r.grain}`, rows: [...r.trend].reverse(), cols: [txt("Period", "bucket"), l("Petrol L", "PMG"), l("Hi-Octane L", "HOBC"), l("Diesel L", "HSD"), m("Retail", "retail"), l("Wholesale L", "wholesale_l"), m("Wholesale", "wholesale"), m("Expenses", "expenses")] }];
    case "sales":
      return [
        { title: "By product", rows: r.sales.by_product, cols: [prod(), l("Litres", "litres"), m("Amount", "amount"), l("Transactions", "txns")] },
        { title: "By station", rows: r.sales.by_station, cols: [txt("Station", "station"), l("Litres", "litres"), m("Amount", "amount"), l("Transactions", "txns")] },
        { title: "By payment method", rows: r.sales.by_payment, cols: [txt("Method", "method"), m("Amount", "amount"), l("Transactions", "txns")] },
        { title: "Top customers", rows: r.sales.top_customers, cols: [txt("Customer", "name"), txt("Type", "type"), l("Litres", "litres"), m("Amount", "amount"), l("Visits", "visits")] },
      ];
    case "stock":
      return [
        { title: "Tanks now", rows: r.stock.tanks, cols: [txt("Station", "station"), txt("Tank", "tank"), l("Capacity", "capacity_l"), l("Stock L", "current_l"), { h: "Fill %", v: (x) => `${x.fill_pct}%`, csv: (x) => x.fill_pct, right: true }, { h: "Days to empty", v: (x) => x.days_to_empty, right: true }] },
        { title: "Fuel purchased (supplier invoices)", rows: r.stock.purchases, cols: [prod(), l("Litres", "litres"), m("Cost", "cost")] },
        { title: "Tanker deliveries", rows: r.stock.deliveries, cols: [{ h: "Date", v: (x) => dt(x.created_at) }, txt("Tank", (x) => `${x.station.replace("Al-Madina ", "")} ${x.tank}`), txt("Tanker", "tanker_no"), l("Invoice L", "invoice_l"), l("Received L", "received_l"), { h: "Short %", v: (x) => <span className={x.shortage_pct >= 0.3 ? "font-semibold text-red-600" : ""}>{x.shortage_pct}%</span>, csv: (x) => x.shortage_pct, right: true }, { h: "Rate", v: (x) => x.purchase_rate ? `Rs ${x.purchase_rate}` : "—", csv: (x) => x.purchase_rate, right: true }] },
        { title: "Dip readings", rows: r.stock.dips, cols: [{ h: "Date", v: (x) => dt(x.created_at) }, txt("Tank", (x) => `${x.station.replace("Al-Madina ", "")} ${x.tank}`), l("Book L", "book_l"), l("Dip L", "measured_l"), { h: "Variance", v: (x) => <span className={Math.abs(x.variance_pct) >= 0.5 ? "font-semibold text-red-600" : ""}>{x.variance_pct}%</span>, csv: (x) => x.variance_pct, right: true }] },
      ];
    case "expenses":
      return [
        { title: "By category", rows: r.expenses.by_category, cols: [txt("Category", "category"), m("Amount", "amount"), l("Entries", "n")] },
        { title: "By station", rows: r.expenses.by_station, cols: [txt("Station", "station"), m("Amount", "amount")] },
        { title: "By payment method", rows: r.expenses.by_method, cols: [txt("Method", "method"), m("Amount", "amount")] },
        { title: "All expenses", rows: r.expenses.list, cols: [txt("Date", "expense_date"), txt("Category", "category"), txt("Paid to", "paid_to"), txt("Method", "method"), m("Amount", "amount"), txt("Note", "note"), txt("By", "created_by")] },
      ];
    case "receivables":
      return [{ title: "Who owes us", rows: r.receivables.list, cols: [
        { h: "Name", v: (x) => <Link className="hover:underline" to={x.kind === "Wholesale client" ? `/wholesale/${x.id}` : `/customers/${x.id}`}>{x.name}</Link>, csv: (x) => x.name },
        { h: "Type", v: (x) => <Badge tone={x.kind === "Wholesale client" ? "amber" : "blue"}>{x.kind}</Badge>, csv: (x) => x.kind },
        txt("Phone", "phone"), m("Amount due", "amount"), m("Credit limit", "credit_limit"),
        { h: "Last payment", v: (x) => ago(x.last_payment), csv: (x) => x.last_payment?.slice(0, 10) },
        { h: "Aging", v: (x) => <Badge tone={x.aging === "90+ days" || x.aging === "No payment yet" ? "red" : x.aging === "61-90 days" ? "amber" : "slate"}>{x.aging}</Badge>, csv: (x) => x.aging },
      ] }];
    case "payables":
      return [
        { title: "Whom we owe", rows: r.payables.list, cols: [txt("Name", "name"), txt("Type", "kind"), txt("Reason", "reason"), m("Amount", "amount"), { h: "Last payment", v: (x) => ago(x.last_payment), csv: (x) => x.last_payment?.slice(0, 10) }] },
        { title: "Expenses awaiting approval", rows: r.payables.pending_expenses.list, cols: [txt("Date", "expense_date"), txt("Category", "category"), txt("Paid to", "paid_to"), m("Amount", "amount"), txt("Entered by", "created_by")] },
      ];
    case "khata":
      return [{ title: "Khata activity by customer", rows: r.khata.by_customer, cols: [
        { h: "Customer", v: (x) => <Link className="hover:underline" to={`/customers/${x.id}`}>{x.name}</Link>, csv: (x) => x.name }, txt("Type", "type"),
        m("Credit given", "given"), m("Collected", "collected"), m("Balance now", "balance_now"), m("Limit", "credit_limit")] }];
    case "wholesale":
      return [
        { title: "By client", rows: r.wholesale.by_client, cols: [{ h: "Client", v: (x) => <Link className="hover:underline" to={`/wholesale/${x.id}`}>{x.name}</Link>, csv: (x) => x.name },
          l("Supplied L", "supplied_l"), l("Returned L", "returned_l"), m("Net billed", "billed"), m("Received", "received"), m("Due now", "due_now")] },
        { title: "By product", rows: r.wholesale.by_product, cols: [prod(), l("Net litres", "net_l"), m("Amount", "amount")] },
      ];
    case "shifts":
      return [{ title: "By attendant", rows: r.shifts.by_attendant, cols: [txt("Attendant", "attendant"), txt("Station", "station"), l("Shifts", "shifts"), l("Litres", "litres"), m("Expected", "expected"), m("Counted", "counted"),
        { h: "Variance", v: (x) => <span className={x.variance < -500 ? "font-semibold text-red-600" : ""}>{pkr(x.variance)}</span>, csv: (x) => x.variance, right: true }, m("Worst shift", "worst")] }];
  }
}
