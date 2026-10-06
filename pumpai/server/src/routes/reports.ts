/**
 * Business reports for any period (24h, 7 days, month, 6 months, year or custom):
 * sales, stock movement, expenses, khata, wholesale, shifts, receivables (owed to us)
 * and payables (we owe). Stock is reconstructed from the movement ledger, so opening and
 * closing stock are exact for any past date.
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, pkDayStart, METER } from "../db.js";
import { notDepot } from "./banks.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { AppError, round2, currentPrices } from "../services.js";
import { PRODUCTS } from "../config.js";
import { clientDue } from "./wholesale.js";
import { supplierOwed } from "./suppliers.js";
import { tankOutlook } from "../ai/analytics.js";
import { shopSummary } from "./shop.js";

export const reports = Router();
reports.use("/reports", requirePerm("reports.view"));

const PK = "'+5 hours'"; // Pakistan time (UTC+5, no DST) for grouping by hour/day/month
const r0 = (n: number) => Math.round(n ?? 0);
const DAY = 86_400_000;

/**
 * Sales per meter (No.1, No.2 …) from the meter readings of the shifts in the period: litres the meter
 * moved, and the money at the average rate that fuel sold for at that station in the period.
 */
export function meterSales(t: number, from: string, to: string, stationId?: number) {
  const rows = all(`SELECT n.id nozzle_id, n.meter_no, ${METER} meter, n.label, st.id station_id, st.name station, tk.product,
      ROUND(SUM(COALESCE(r.closing, r.checkpoint, r.opening) - r.opening), 2) litres, COUNT(DISTINCT r.shift_id) shifts,
      GROUP_CONCAT(DISTINCT sh.attendant) salesmen, n.totalizer
    FROM meter_readings r JOIN shifts sh ON sh.id=r.shift_id JOIN nozzles n ON n.id=r.nozzle_id JOIN tanks tk ON tk.id=n.tank_id
    JOIN stations st ON st.id=n.station_id
    WHERE st.tenant_id=? AND sh.opened_at >= ? AND sh.opened_at < ? ${stationId ? "AND st.id=" + Number(stationId) : ""}
    GROUP BY n.id ORDER BY st.name, n.meter_no`, t, from, to);
  const rate = new Map(all(`SELECT s.station_id, s.product, SUM(s.amount)/SUM(s.litres) r FROM sales s JOIN stations st ON st.id=s.station_id
    WHERE st.tenant_id=? AND s.created_at >= ? AND s.created_at < ? AND s.litres > 0 GROUP BY s.station_id, s.product`, t, from, to)
    .map((x) => [`${x.station_id}:${x.product}`, x.r as number]));
  return rows.map((r) => {
    const rt = rate.get(`${r.station_id}:${r.product}`);
    return { ...r, rate: rt ? Math.round(rt * 100) / 100 : null, amount: rt ? Math.round(r.litres * rt) : null, salesmen: String(r.salesmen ?? "").split(",").filter(Boolean) };
  });
}

export function buildReport(t: number, from: string, to: string) {
  const span = Date.parse(to) - Date.parse(from);
  const grain = span <= 2 * DAY ? "hour" : span <= 93 * DAY ? "day" : "month";
  const fmt = grain === "hour" ? "%Y-%m-%d %H:00" : grain === "day" ? "%Y-%m-%d" : "%Y-%m";
  const bucket = (col: string) => `strftime('${fmt}', ${col}, ${PK})`;
  const fromDate = new Date(Date.parse(from) + 5 * 3600_000).toISOString().slice(0, 10); // for date-only columns
  const toDate = new Date(Date.parse(to) + 5 * 3600_000).toISOString().slice(0, 10);
  const S = `FROM sales s JOIN stations st ON st.id=s.station_id WHERE st.tenant_id=? AND s.created_at >= ? AND s.created_at < ?`;
  const W = `FROM wholesale_txns w WHERE w.tenant_id=? AND w.voided=0 AND w.txn_date >= ? AND w.txn_date < ?`;
  const E = `FROM expenses e WHERE e.tenant_id=? AND e.status='approved' AND e.expense_date >= ? AND e.expense_date <= ?`;
  const P = [t, from, to] as const;
  const PE = [t, fromDate, toDate] as const;

  /* ---------- Sales ---------- */
  const salesByProduct = all(`SELECT s.product, ROUND(SUM(s.litres),2) litres, ROUND(SUM(s.amount),2) amount, COUNT(*) txns ${S} GROUP BY s.product`, ...P);
  const retail = salesByProduct.reduce((a, r) => ({ litres: a.litres + r.litres, amount: a.amount + r.amount, txns: a.txns + r.txns }), { litres: 0, amount: 0, txns: 0 });
  const trendRows = all(`SELECT ${bucket("s.created_at")} b, s.product, SUM(s.litres) l, SUM(s.amount) a ${S} GROUP BY b, s.product`, ...P);
  const wTrend = all(`SELECT ${bucket("w.txn_date")} b, SUM(CASE WHEN type='supply' THEN amount WHEN type='return' THEN -amount ELSE 0 END) a,
      SUM(CASE WHEN type='supply' THEN litres WHEN type='return' THEN -litres ELSE 0 END) l ${W} AND w.type IN ('supply','return') GROUP BY b`, ...P);
  const eTrend = all(`SELECT strftime('${grain === "month" ? "%Y-%m" : "%Y-%m-%d"}', e.expense_date) b, SUM(e.amount) a ${E} GROUP BY b`, ...PE);
  const buckets = new Map<string, any>();
  const B = (k: string) => buckets.get(k) ?? buckets.set(k, { bucket: k, PMG: 0, HOBC: 0, HSD: 0, retail: 0, wholesale: 0, wholesale_l: 0, expenses: 0 }).get(k);
  for (const r of trendRows) { const b = B(r.b); b[r.product] += r.l; b.retail += r.a; }
  for (const r of wTrend) { const b = B(r.b); b.wholesale += r.a; b.wholesale_l += r.l; }
  for (const r of eTrend) B(grain === "hour" ? `${r.b} 00:00` : r.b).expenses += r.a; // hourly view: expenses land at midnight
  const trend = [...buckets.values()].sort((a, b) => a.bucket.localeCompare(b.bucket))
    .map((b) => ({ ...b, PMG: r0(b.PMG), HOBC: r0(b.HOBC), HSD: r0(b.HSD), retail: r0(b.retail), wholesale: r0(b.wholesale), wholesale_l: r0(b.wholesale_l), expenses: r0(b.expenses) }));

  const sales = {
    by_product: salesByProduct,
    by_station: all(`SELECT st.name station, ROUND(SUM(s.litres)) litres, ROUND(SUM(s.amount)) amount, COUNT(*) txns ${S} GROUP BY st.id ORDER BY amount DESC`, ...P),
    by_payment: all(`SELECT s.payment_method method, ROUND(SUM(s.amount)) amount, COUNT(*) txns ${S} GROUP BY s.payment_method ORDER BY amount DESC`, ...P),
    top_customers: [] as any[],
    by_meter: meterSales(t, from, to),
  };
  sales.top_customers = all(
    `SELECT c.name, c.type, ROUND(SUM(s.litres)) litres, ROUND(SUM(s.amount)) amount, COUNT(*) visits
     FROM sales s JOIN stations st ON st.id=s.station_id JOIN customers c ON c.id=s.customer_id
     WHERE st.tenant_id=? AND s.created_at >= ? AND s.created_at < ? GROUP BY c.id ORDER BY amount DESC LIMIT 15`, ...P);

  /* ---------- Wholesale ---------- */
  const wTot = get(`SELECT COALESCE(SUM(CASE WHEN type='supply' THEN litres END),0) supplied_l, COALESCE(SUM(CASE WHEN type='supply' THEN amount END),0) billed,
      COALESCE(SUM(CASE WHEN type='return' THEN litres END),0) returned_l, COALESCE(SUM(CASE WHEN type='return' THEN amount END),0) returned,
      COALESCE(SUM(CASE WHEN type='payment' THEN amount END),0) received, COALESCE(SUM(CASE WHEN type='adjustment' THEN amount END),0) adjustments ${W}`, ...P)!;
  const wholesale = {
    ...wTot, net_billed: round2(wTot.billed - wTot.returned),
    by_client: all(`SELECT c.id, c.name, ROUND(COALESCE(SUM(CASE WHEN w.type='supply' THEN w.litres END),0)) supplied_l,
        ROUND(COALESCE(SUM(CASE WHEN w.type='return' THEN w.litres END),0)) returned_l,
        ROUND(COALESCE(SUM(CASE WHEN w.type='supply' THEN w.amount WHEN w.type='return' THEN -w.amount END),0)) billed,
        ROUND(COALESCE(SUM(CASE WHEN w.type='payment' THEN w.amount END),0)) received
      FROM wholesale_clients c LEFT JOIN wholesale_txns w ON w.client_id=c.id AND w.voided=0 AND w.txn_date >= ? AND w.txn_date < ?
      WHERE c.tenant_id=? GROUP BY c.id ORDER BY billed DESC`, from, to, t).map((c) => ({ ...c, due_now: clientDue(c.id) })),
    by_product: all(`SELECT product, ROUND(SUM(CASE WHEN type='supply' THEN litres ELSE -litres END)) net_l, ROUND(SUM(CASE WHEN type='supply' THEN amount ELSE -amount END)) amount
      ${W} AND type IN ('supply','return') GROUP BY product`, ...P),
  };

  /* ---------- Stock movement (reconstructed) ---------- */
  const tanks = all("SELECT t.*, s.name station_name FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=?", t);
  const moves = (since: string, until?: string) => {
    const end = until ? "AND x.ts < ?" : "";
    const args = (extra: (string | number)[]) => [t, since, ...(until ? [until] : []), ...extra];
    const q = (sql: string) => Object.fromEntries(all(sql, ...args([])).map((r) => [r.product, r.v ?? 0]));
    return {
      received: q(`SELECT t.product, SUM(x.received_l) v FROM (SELECT tank_id, received_l, created_at ts FROM deliveries) x JOIN tanks t ON t.id=x.tank_id JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? AND x.ts >= ? ${end} GROUP BY t.product`),
      retail_out: q(`SELECT x.product, SUM(x.litres) v FROM (SELECT product, litres, station_id, created_at ts FROM sales) x JOIN stations s ON s.id=x.station_id WHERE s.tenant_id=? AND x.ts >= ? ${end} GROUP BY x.product`),
      wholesale_out: q(`SELECT x.product, SUM(x.litres) v FROM (SELECT tenant_id, product, litres, created_at ts FROM wholesale_txns WHERE type='supply' AND voided=0 AND tank_id IS NOT NULL) x WHERE x.tenant_id=? AND x.ts >= ? ${end} GROUP BY x.product`),
      returns_in: q(`SELECT x.product, SUM(x.litres) v FROM (SELECT tenant_id, product, litres, created_at ts FROM wholesale_txns WHERE type='return' AND voided=0 AND tank_id IS NOT NULL) x WHERE x.tenant_id=? AND x.ts >= ? ${end} GROUP BY x.product`),
      dip_adjust: q(`SELECT t.product, SUM(x.measured_l - x.book_l) v FROM (SELECT tank_id, measured_l, book_l, created_at ts FROM dip_readings) x JOIN tanks t ON t.id=x.tank_id JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? AND x.ts >= ? ${end} GROUP BY t.product`),
    };
  };
  const current: Record<string, number> = {};
  for (const tk of tanks) current[tk.product] = (current[tk.product] ?? 0) + tk.current_l;
  const stockAt = (ts: string) => {
    const m = moves(ts);
    return Object.fromEntries(Object.keys(current).map((p) => [p,
      (current[p] ?? 0) - (m.received[p] ?? 0) - (m.returns_in[p] ?? 0) + (m.retail_out[p] ?? 0) + (m.wholesale_out[p] ?? 0) - (m.dip_adjust[p] ?? 0)]));
  };
  const opening = stockAt(from);
  const closing = Date.parse(to) >= Date.now() - 60_000 ? current : stockAt(to);
  const inPeriod = moves(from, to);
  const products = Object.keys(PRODUCTS).filter((p) => p in current);

  /* ---------- Purchases & cost ---------- */
  // fuel bought into our tanks (depot-direct trips never entered stock: their cost is counted against their own sales below)
  const purchases = all(`SELECT product, SUM(litres) litres, SUM(amount) cost FROM supplier_txns WHERE tenant_id=? AND type='purchase' AND trip_id IS NULL AND txn_date >= ? AND txn_date < ? GROUP BY product`, ...P);
  const direct = get(`SELECT COALESCE(SUM(amount),0) cost FROM supplier_txns WHERE tenant_id=? AND type='purchase' AND trip_id IS NOT NULL AND txn_date >= ? AND txn_date < ?`, ...P)!.cost as number;
  const directL: Record<string, number> = Object.fromEntries(all(`SELECT w.product, SUM(w.litres) l FROM wholesale_txns w JOIN wholesale_trips tr ON tr.id=w.trip_id
    WHERE w.tenant_id=? AND w.voided=0 AND w.type='supply' AND tr.source='depot' AND w.txn_date >= ? AND w.txn_date < ? GROUP BY w.product`, ...P).map((x) => [x.product, x.l as number]));
  const avgCost: Record<string, number | null> = {};
  for (const p of products) {
    // weighted average purchase rate over the 120 days up to the end of the period
    const r = get(`SELECT SUM(amount) a, SUM(litres) l FROM supplier_txns WHERE tenant_id=? AND type='purchase' AND trip_id IS NULL AND product=? AND txn_date < ? AND txn_date >= ?`,
      t, p, to, new Date(Date.parse(to) - 120 * DAY).toISOString())!;
    // no purchases in that window → fall back to the last purchase rate ever recorded
    avgCost[p] = r.l ? r.a / r.l
      : get(`SELECT rate FROM supplier_txns WHERE tenant_id=? AND type='purchase' AND product=? AND rate > 0 AND txn_date < ? ORDER BY txn_date DESC LIMIT 1`, t, p, to)?.rate ?? null;
  }
  const stock = {
    products: products.map((p) => {
      const soldL = (inPeriod.retail_out[p] ?? 0) + (inPeriod.wholesale_out[p] ?? 0) - (inPeriod.returns_in[p] ?? 0);
      return {
        product: p, name: PRODUCTS[p], opening_l: r0(opening[p]), received_l: r0(inPeriod.received[p]), returns_in_l: r0(inPeriod.returns_in[p]),
        retail_sold_l: r0(inPeriod.retail_out[p]), wholesale_out_l: r0(inPeriod.wholesale_out[p]), dip_adjust_l: r0(inPeriod.dip_adjust[p]),
        closing_l: r0(closing[p]), net_sold_l: r0(soldL), direct_l: r0(directL[p]), avg_cost: avgCost[p] ? round2(avgCost[p]!) : null,
        closing_value: avgCost[p] ? r0((closing[p] ?? 0) * avgCost[p]!) : null,
      };
    }),
    tanks: tankOutlook(t).map((x) => ({ station: x.station_name, tank: x.name, product: x.product, capacity_l: x.capacity_l, current_l: r0(x.current_l), fill_pct: x.fill_pct, days_to_empty: x.days_to_empty })),
    deliveries: all(`SELECT d.created_at, s.name station, t.name tank, t.product, d.supplier, d.tanker_no, d.invoice_l, d.received_l, d.shortage_pct, d.purchase_rate
      FROM deliveries d JOIN tanks t ON t.id=d.tank_id JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? AND d.created_at >= ? AND d.created_at < ? ORDER BY d.created_at DESC`, ...P),
    dips: all(`SELECT d.created_at, s.name station, t.name tank, t.product, d.book_l, d.measured_l, d.variance_pct
      FROM dip_readings d JOIN tanks t ON t.id=d.tank_id JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? AND d.created_at >= ? AND d.created_at < ? ORDER BY d.created_at DESC`, ...P),
    purchases: purchases.map((p) => ({ product: p.product, litres: r0(p.litres), cost: r0(p.cost) })),
    direct: { litres: r0(Object.values(directL).reduce((a, v) => a + v, 0)), cost: r0(direct) },
  };

  /* ---------- Expenses ---------- */
  const expTotal = get(`SELECT COALESCE(SUM(e.amount),0) s, COUNT(*) n ${E}`, ...PE)!;
  const expenses = {
    total: round2(expTotal.s), count: expTotal.n,
    by_category: all(`SELECT e.category, ROUND(SUM(e.amount)) amount, COUNT(*) n ${E} GROUP BY e.category ORDER BY amount DESC`, ...PE),
    by_station: all(`SELECT COALESCE(s.name,'Head office / all') station, ROUND(SUM(e.amount)) amount FROM expenses e LEFT JOIN stations s ON s.id=e.station_id
      WHERE e.tenant_id=? AND e.status='approved' AND e.expense_date >= ? AND e.expense_date <= ? GROUP BY e.station_id ORDER BY amount DESC`, ...PE),
    by_method: all(`SELECT e.method, ROUND(SUM(e.amount)) amount ${E} GROUP BY e.method ORDER BY amount DESC`, ...PE),
    list: all(`SELECT e.expense_date, e.category, e.amount, e.paid_to, e.method, e.note, e.created_by ${E} ORDER BY e.expense_date DESC, e.id DESC LIMIT 300`, ...PE),
  };

  /* ---------- Khata (retail credit) in period ---------- */
  const kTot = get(`SELECT COALESCE(SUM(CASE WHEN k.type='debit' THEN k.amount END),0) given, COALESCE(SUM(CASE WHEN k.type='credit' THEN k.amount END),0) collected
    FROM khata_ledger k JOIN customers c ON c.id=k.customer_id WHERE c.tenant_id=? AND k.created_at >= ? AND k.created_at < ?`, ...P)!;
  const khata = {
    given: round2(kTot.given), collected: round2(kTot.collected), net_change: round2(kTot.given - kTot.collected),
    by_customer: all(`SELECT c.id, c.name, c.type, c.credit_limit, c.balance balance_now,
        ROUND(COALESCE(SUM(CASE WHEN k.type='debit' THEN k.amount END),0)) given, ROUND(COALESCE(SUM(CASE WHEN k.type='credit' THEN k.amount END),0)) collected
      FROM customers c JOIN khata_ledger k ON k.customer_id=c.id AND k.created_at >= ? AND k.created_at < ?
      WHERE c.tenant_id=? GROUP BY c.id ORDER BY given DESC`, from, to, t),
  };

  /* ---------- Shifts / cash ---------- */
  const shifts = {
    totals: get(`SELECT COUNT(*) n, COALESCE(SUM(sh.cash_expected),0) expected, COALESCE(SUM(sh.cash_actual),0) counted, COALESCE(SUM(sh.variance),0) variance
      FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.status='closed' AND sh.closed_at >= ? AND sh.closed_at < ?`, ...P),
    by_attendant: all(`SELECT sh.attendant, s.name station, COUNT(*) shifts, ROUND(SUM(sh.litres)) litres, ROUND(SUM(sh.cash_expected)) expected, ROUND(SUM(sh.cash_actual)) counted,
        ROUND(SUM(sh.variance)) variance, ROUND(MIN(sh.variance)) worst
      FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.status='closed' AND sh.closed_at >= ? AND sh.closed_at < ?
      GROUP BY sh.attendant, s.id ORDER BY variance`, ...P),
  };

  /* ---------- Summary ---------- */
  const tankCogs = products.reduce<number | null>((a, p) => {
    const row = stock.products.find((x) => x.product === p)!;
    if (row.net_sold_l === 0) return a;
    return avgCost[p] == null || a == null ? null : a + row.net_sold_l * avgCost[p]!;
  }, 0);
  // + what depot-direct trips cost (the supplier's bill, freight on it included); cash freight is already an expense
  const cogs = tankCogs == null ? null : tankCogs + direct;
  const shopS = shopSummary(t, from, to);
  const revenue = round2(retail.amount + wholesale.net_billed + shopS.sales);
  const digital = sales.by_payment.filter((m) => ["jazzcash", "easypaisa", "raast", "card"].includes(m.method)).reduce((a, m) => a + m.amount, 0);
  const summary = {
    revenue, retail_sales: round2(retail.amount), retail_litres: r0(retail.litres), retail_txns: retail.txns,
    wholesale_net: wholesale.net_billed, wholesale_litres: r0(wholesale.supplied_l - wholesale.returned_l),
    expenses: expenses.total,
    fuel_cost_estimate: cogs == null ? null : r0(cogs),
    gross_profit_estimate: cogs == null ? null : r0(revenue - cogs - shopS.cost),
    net_profit_estimate: cogs == null ? null : r0(revenue - cogs - shopS.cost - expenses.total),
    shop_sales: shopS.sales, shop_profit: shopS.profit,
    purchases_cost: r0(stock.purchases.reduce((a, p) => a + p.cost, 0)),
    money_in: {
      cash_sales: r0(sales.by_payment.find((m) => m.method === "cash")?.amount ?? 0), digital_sales: r0(digital),
      khata_collected: r0(khata.collected), wholesale_received: r0(wholesale.received - (get(`SELECT COALESCE(SUM(amount),0) v ${W} AND type='payment' AND NOT ${notDepot("method")}`, ...P)!.v as number)),
    },
    money_out: {
      expenses: r0(expenses.total),
      supplier_payments: r0(get(`SELECT COALESCE(SUM(amount),0) s FROM supplier_txns WHERE tenant_id=? AND type='payment' AND COALESCE(method,'')<>'WHT' AND ${notDepot("method")} AND txn_date >= ? AND txn_date < ?`, ...P)!.s),
    },
    cash_variance: r0(shifts.totals!.variance),
  };

  return { from, to, grain, summary, trend, sales, stock, expenses, khata, wholesale, shifts, ...balances(t) };
}

/**
 * Today's book for the owner's dashboard (Pakistan midnight → now): what was sold, spent and
 * received, how much fuel is left and what it is worth, and who owes whom. Built from the same
 * report as the Reports page so the numbers always agree.
 */
export function dayBook(t: number, from = pkDayStart(), to = new Date().toISOString()) {
  const r = buildReport(t, from, to);
  const prices = currentPrices(t, to);
  const stock = r.stock.products.map((p) => {
    const price = prices[p.product]?.price ?? null;
    return {
      product: p.product, name: p.name, opening_l: p.opening_l, received_l: p.received_l,
      sold_l: p.net_sold_l, dip_adjust_l: p.dip_adjust_l, closing_l: p.closing_l,
      cost_rate: p.avg_cost, value_at_cost: p.closing_value,
      sale_rate: price, value_at_sale: price == null ? null : r0(p.closing_l * price),
    };
  });
  const sum = (k: "value_at_cost" | "value_at_sale") => stock.some((x) => x[k] == null) ? null : stock.reduce((a, x) => a + (x[k] ?? 0), 0);
  const pay = (m: string) => r.sales.by_payment.find((x: any) => x.method === m)?.amount ?? 0;
  return {
    from, to,
    sales: {
      revenue: r.summary.revenue, retail: r.summary.retail_sales, retail_litres: r.summary.retail_litres, txns: r.summary.retail_txns,
      wholesale: r.summary.wholesale_net, wholesale_litres: r.summary.wholesale_litres, shop: r.summary.shop_sales, shop_profit: r.summary.shop_profit,
      cash: r0(pay("cash")), digital: r.summary.money_in.digital_sales, khata: r0(pay("khata")),
    },
    expenses: { total: r.expenses.total, count: r.expenses.count, by_category: r.expenses.by_category, pending: r.payables.pending_expenses },
    supply: {
      deliveries: r.stock.deliveries.length, litres: r0(r.stock.deliveries.reduce((a: number, d: any) => a + d.received_l, 0)),
      cost: r.summary.purchases_cost, list: r.stock.deliveries,
    },
    stock: { products: stock, litres: r0(stock.reduce((a, x) => a + x.closing_l, 0)), value_at_cost: sum("value_at_cost"), value_at_sale: sum("value_at_sale") },
    profit: { gross: r.summary.gross_profit_estimate, net: r.summary.net_profit_estimate },
    money_in: r.summary.money_in, money_out: r.summary.money_out,
    shifts: { closed: r.shifts.totals!.n, cash_expected: r0(r.shifts.totals!.expected), cash_counted: r0(r.shifts.totals!.counted), variance: r0(r.shifts.totals!.variance) },
    receivables: r.receivables.total, payables: r.payables.total,
  };
}

/** Point-in-time balances (as of now): who owes us, and whom we owe. */
export function balances(t: number) {
  const now = Date.now();
  const lastPay = (sql: string, id: number) => get(sql, id)?.d as string | null;
  const age = (since: string | null) => (since ? Math.floor((now - Date.parse(since)) / DAY) : null);
  const bucketOf = (days: number | null) => days == null ? "No payment yet" : days <= 30 ? "0-30 days" : days <= 60 ? "31-60 days" : days <= 90 ? "61-90 days" : "90+ days";

  const khataRows = all("SELECT id, name, phone, type, balance, credit_limit FROM customers WHERE tenant_id=? AND balance <> 0", t).map((c) => {
    const lp = lastPay("SELECT MAX(created_at) d FROM khata_ledger WHERE customer_id=? AND type='credit'", c.id);
    return { ...c, kind: "Khata customer", last_payment: lp, days_since_payment: age(lp) };
  });
  const wsRows = all("SELECT id, name, phone, credit_limit FROM wholesale_clients WHERE tenant_id=?", t).map((c) => {
    const lp = lastPay("SELECT MAX(txn_date) d FROM wholesale_txns WHERE client_id=? AND type='payment' AND voided=0", c.id);
    return { ...c, balance: clientDue(c.id), kind: "Wholesale client", last_payment: lp, days_since_payment: age(lp) };
  }).filter((c) => c.balance !== 0);
  const supRows = all("SELECT id, name, phone FROM suppliers WHERE tenant_id=?", t).map((s) => {
    const lp = lastPay("SELECT MAX(txn_date) d FROM supplier_txns WHERE supplier_id=? AND type='payment'", s.id);
    return { ...s, owed: supplierOwed(s.id), kind: "Supplier", last_payment: lp };
  });

  const recv = [...khataRows.filter((c) => c.balance > 0), ...wsRows.filter((c) => c.balance > 0)]
    .map((c) => ({ ...c, amount: round2(c.balance), aging: bucketOf(c.days_since_payment) })).sort((a, b) => b.amount - a.amount);
  const agingOrder = ["0-30 days", "31-60 days", "61-90 days", "90+ days", "No payment yet"];
  const payables = [
    ...supRows.filter((s) => s.owed > 0).map((s) => ({ ...s, amount: s.owed, reason: "Fuel purchased on credit" })),
    ...khataRows.filter((c) => c.balance < 0).map((c) => ({ ...c, amount: round2(-c.balance), reason: "Customer advance (paid more than used)" })),
    ...wsRows.filter((c) => c.balance < 0).map((c) => ({ ...c, amount: round2(-c.balance), reason: "Wholesale advance" })),
  ].sort((a, b) => b.amount - a.amount);
  const pendingExp = all("SELECT category, amount, paid_to, created_by, expense_date FROM expenses WHERE tenant_id=? AND status='pending' ORDER BY amount DESC", t);

  return {
    receivables: {
      total: round2(recv.reduce((a, r) => a + r.amount, 0)),
      khata_total: round2(recv.filter((r) => r.kind === "Khata customer").reduce((a, r) => a + r.amount, 0)),
      wholesale_total: round2(recv.filter((r) => r.kind === "Wholesale client").reduce((a, r) => a + r.amount, 0)),
      aging: agingOrder.map((b) => ({ bucket: b, amount: round2(recv.filter((r) => r.aging === b).reduce((a, r) => a + r.amount, 0)), count: recv.filter((r) => r.aging === b).length })),
      list: recv,
    },
    payables: {
      total: round2(payables.reduce((a, r) => a + r.amount, 0)),
      suppliers_total: round2(payables.filter((p) => p.kind === "Supplier").reduce((a, p) => a + p.amount, 0)),
      list: payables,
      pending_expenses: { count: pendingExp.length, amount: round2(pendingExp.reduce((a, e) => a + e.amount, 0)), list: pendingExp },
    },
  };
}

reports.get("/reports", h((req) => {
  const q = parse(z.object({ from: z.string().datetime({ offset: true }).optional(), to: z.string().datetime({ offset: true }).optional() }), req.query);
  const to = q.to ? new Date(q.to).toISOString() : new Date().toISOString();
  const from = q.from ? new Date(q.from).toISOString() : new Date(Date.parse(to) - 30 * DAY).toISOString();
  if (Date.parse(from) >= Date.parse(to)) throw new AppError(400, "'From' must be before 'To'");
  if (Date.parse(to) - Date.parse(from) > 3 * 366 * DAY) throw new AppError(400, "Maximum report period is 3 years");
  return buildReport(tid(req), from, to);
}));
