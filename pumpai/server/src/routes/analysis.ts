/**
 * Owner's analysis: pump health score, station comparison, salesman risk, monthly profit & loss and
 * balance sheet, payment-statement reconciliation, price-change planner and tank gain/loss.
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, pkDate, pkStart, pkEnd, getSetting } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { AppError, round2, pkr, currentPrices } from "../services.js";
import { PRODUCTS } from "../config.js";
import { buildReport, balances } from "./reports.js";
import { cashPosition } from "./backoffice.js";
import { bankAccounts } from "./banks.js";
import { shopSummary } from "./shop.js";
import { bypassStockValue } from "./bypass.js";
import { tankOutlook } from "../ai/analytics.js";
import { checklistToday } from "./compliance.js";
import { sendDirect } from "../whatsapp/cloud.js";
import { cashWithSalesmen } from "./cashier.js";

export const analysis = Router();
analysis.use("/analysis", requirePerm("reports.view"));
const DAY = 86_400_000;
const r0 = (n: number) => Math.round(n ?? 0);
const range = (q: { from?: string; to?: string }, days = 30) => ({
  from: q.from ? pkStart(q.from) : new Date(Date.now() - days * DAY).toISOString(),
  to: q.to ? pkEnd(q.to) : new Date().toISOString(),
});
const dates = z.object({ from: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(), to: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional() });

/* ================= Station comparison ================= */
export function stationComparison(t: number, from: string, to: string) {
  return all("SELECT id, name, city FROM stations WHERE tenant_id=? ORDER BY id", t).map((s) => {
    const sales = get(`SELECT COALESCE(SUM(litres),0) l, COALESCE(SUM(amount),0) a, COUNT(*) n,
        COALESCE(SUM(CASE WHEN payment_method='khata' THEN amount END),0) khata, COALESCE(SUM(CASE WHEN payment_method='cash' THEN amount END),0) cash
      FROM sales WHERE station_id=? AND created_at >= ? AND created_at < ?`, s.id, from, to)!;
    const shop = shopSummary(t, from, to, s.id);
    const exp = get("SELECT COALESCE(SUM(amount),0) v FROM expenses WHERE tenant_id=? AND station_id=? AND status='approved' AND created_at >= ? AND created_at < ?", t, s.id, from, to)!.v;
    const sh = get("SELECT COUNT(*) n, COALESCE(SUM(variance),0) v, COALESCE(SUM(CASE WHEN variance < -100 THEN 1 END),0) short FROM shifts WHERE station_id=? AND status='closed' AND closed_at >= ? AND closed_at < ?", s.id, from, to)!;
    const dip = get(`SELECT COALESCE(SUM(d.measured_l - d.book_l),0) v FROM dip_readings d JOIN tanks t ON t.id=d.tank_id WHERE t.station_id=? AND d.created_at >= ? AND d.created_at < ?`, s.id, from, to)!.v;
    return {
      id: s.id, name: s.name, city: s.city, litres: r0(sales.l), fuel_sales: r0(sales.a), shop_sales: r0(shop.sales), shop_profit: r0(shop.profit), expenses: r0(exp),
      sales_count: sales.n, avg_sale: sales.n ? r0(sales.a / sales.n) : 0, khata_share_pct: sales.a ? round2((sales.khata / sales.a) * 100) : 0,
      cash_share_pct: sales.a ? round2((sales.cash / sales.a) * 100) : 0, shifts: sh.n, cash_variance: r0(sh.v), short_shifts: sh.short, dip_gain_loss_l: r0(dip),
    };
  });
}
analysis.get("/analysis/stations", h((req) => { const r = range(parse(dates, req.query)); return { ...r, stations: stationComparison(tid(req), r.from, r.to) }; }));

/* ================= Salesman risk ================= */
export function staffRisk(t: number, from: string, to: string) {
  return all("SELECT id, name FROM users WHERE tenant_id=? AND role='salesman' ORDER BY name", t).map((u) => {
    const sh = get(`SELECT COUNT(*) n, COALESCE(SUM(variance),0) v, COALESCE(SUM(CASE WHEN variance < -100 THEN 1 END),0) short, COALESCE(MIN(variance),0) worst
      FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.attendant=? AND sh.status='closed' AND sh.closed_at >= ? AND sh.closed_at < ?`, t, u.name, from, to)!;
    const pos = get(`SELECT COALESCE(SUM(CASE WHEN s.source='pos' THEN s.litres END),0) pos, COALESCE(SUM(CASE WHEN s.source='meter' THEN s.litres END),0) meter
      FROM sales s JOIN shifts sh ON sh.id=s.shift_id JOIN stations st ON st.id=sh.station_id WHERE st.tenant_id=? AND sh.attendant=? AND s.created_at >= ? AND s.created_at < ?`, t, u.name, from, to)!;
    const undos = get("SELECT COUNT(*) n FROM audit_log WHERE tenant_id=? AND action IN ('sale_undo','shop_undo') AND user_name=? AND created_at >= ? AND created_at < ?", t, u.name, from, to)!.n;
    const late = get("SELECT COUNT(*) n FROM attendance WHERE user_id=? AND late_minutes > 15 AND check_in >= ? AND check_in < ?", u.id, from, to)!.n;
    const fails = get("SELECT COUNT(*) n FROM checklist_entries WHERE tenant_id=? AND done_by=? AND ok=0 AND created_at >= ? AND created_at < ?", t, u.name, from, to)!.n;
    const total = pos.pos + pos.meter;
    const unentered = total ? round2((pos.meter / total) * 100) : 0;
    // 0 = no concern … 100 = look closely
    const score = Math.min(100, Math.round(
      Math.min(40, (sh.short / Math.max(1, sh.n)) * 80) + Math.min(25, unentered / 2) + Math.min(15, undos * 3) + Math.min(10, late * 2) + Math.min(10, fails * 3)));
    const reasons = [
      sh.short ? `${sh.short} of ${sh.n} shifts short (total ${pkr(-Math.min(0, sh.v))}, worst ${pkr(-sh.worst)})` : null,
      unentered >= 10 ? `${unentered}% of litres not entered on the POS (booked from the meter)` : null,
      undos ? `${undos} sales undone` : null, late ? `${late} days late` : null, fails ? `${fails} failed checks` : null,
    ].filter(Boolean);
    return { id: u.id, name: u.name, shifts: sh.n, short_shifts: sh.short, cash_variance: r0(sh.v), unentered_pct: unentered, undos, late_days: late, failed_checks: fails, score, level: score >= 50 ? "high" : score >= 25 ? "watch" : "ok", reasons };
  }).sort((a, b) => b.score - a.score);
}
analysis.get("/analysis/staff-risk", h((req) => { const r = range(parse(dates, req.query)); return { ...r, staff: staffRisk(tid(req), r.from, r.to) }; }));

/** Monday morning: the owner gets the week's staff risk on WhatsApp. */
export async function weeklyStaffRisk(t: number) {
  const rows = staffRisk(t, new Date(Date.now() - 7 * DAY).toISOString(), new Date().toISOString()).filter((r) => r.score >= 25);
  const owner = getSetting(t, "owner_phone") || get("SELECT owner_phone FROM tenants WHERE id=?", t)?.owner_phone;
  if (!rows.length || !owner) return 0;
  await sendDirect(t, { phone: owner, name: "Owner" }, "staff_risk", null,
    `🕵️ Haftay ki staff report\n${rows.map((r) => `• ${r.name} — risk ${r.score}/100: ${r.reasons.join("; ")}`).join("\n")}`);
  return rows.length;
}

/* ================= Profit & loss and balance sheet ================= */
export function profitAndLoss(t: number, month: string) {
  const from = pkStart(`${month}-01`);
  const lastDay = new Date(Date.UTC(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 0)).getUTCDate();
  const to = Math.min(Date.parse(pkEnd(`${month}-${String(lastDay).padStart(2, "0")}`)), Date.now());
  const r = buildReport(t, from, new Date(to).toISOString());
  const s = r.summary;
  const fuelCost = s.fuel_cost_estimate ?? 0;
  const dipLoss = round2(r.stock.products.reduce((a: number, p: any) => a + (p.dip_adjust_l ?? 0) * (p.avg_cost ?? 0), 0));
  const shopCost = round2(s.shop_sales - s.shop_profit);
  const income = {
    fuel_retail: r0(s.retail_sales), fuel_wholesale: r0(s.wholesale_net), shop: r0(s.shop_sales), total: r0(s.revenue),
  };
  const cost = { fuel: r0(fuelCost), shop: r0(shopCost), stock_gain_loss: r0(dipLoss), total: r0(fuelCost + shopCost - dipLoss) };
  const gross = r0(income.total - cost.total);
  return {
    month, from, to: new Date(to).toISOString(), income, cost_of_sales: cost, gross_profit: gross,
    expenses: { total: r0(r.expenses.total), by_category: r.expenses.by_category },
    net_profit: r0(gross - r.expenses.total),
    margin_pct: income.total ? round2(((gross - r.expenses.total) / income.total) * 100) : 0,
    litres: { retail: r0(s.retail_litres), wholesale: r0(s.wholesale_litres) },
  };
}

export function balanceSheet(t: number) {
  const b = balances(t);
  const cash = cashPosition(t).cash_in_hand;
  const fuel = buildReport(t, new Date(Date.now() - DAY).toISOString(), new Date().toISOString()).stock.products
    .reduce((a: number, p: any) => a + (p.closing_value ?? 0), 0);
  const shopStock = get("SELECT COALESCE(SUM(stock*cost),0) v FROM shop_items WHERE tenant_id=? AND active=1", t)!.v;
  const staffOwe = get(`SELECT COALESCE(SUM(CASE WHEN type IN ('advance','shortage') THEN amount WHEN type IN ('repayment','deduction') THEN -amount ELSE 0 END),0) v FROM staff_ledger WHERE tenant_id=?`, t)!.v;
  const one = (sql: string) => r0(get(sql, t)!.v ?? 0);
  const banks = bankAccounts(t).total;
  // cheques received but not yet cleared are still money owed to us (they sit in the receivables), so they are not added again
  // cash taken by the salesmen and not yet handed to the cashier is still the pump's money
  const assets = { cash_in_hand: r0(cash), cash_with_salesmen: r0(cashWithSalesmen(t).total), banks: r0(banks), fuel_stock: r0(fuel), shop_stock: r0(shopStock), khata_receivable: r0(b.receivables.khata_total), wholesale_receivable: r0(b.receivables.wholesale_total), carriage_receivable: r0(b.receivables.carriage_total ?? 0),
    bypass_stock: r0(bypassStockValue(t)), staff_advances: r0(Math.max(0, staffOwe)) };
  const liabilities = {
    suppliers: r0(b.payables.suppliers_total), customer_advances: r0(b.payables.total - b.payables.suppliers_total), expenses_pending: r0(b.payables.pending_expenses.amount),
    unused_coupons: one("SELECT COALESCE(SUM(value),0) v FROM fuel_coupons WHERE tenant_id=? AND status='active'"),
    withholding_tax_payable: one("SELECT COALESCE(SUM(amount),0) v FROM tax_withholdings WHERE tenant_id=? AND cpr_no IS NULL"),
    bypass_suppliers: r0((get("SELECT COALESCE(SUM(amount),0) v FROM bypass_purchases WHERE tenant_id=? AND voided=0", t)!.v as number)
      - (get("SELECT COALESCE(SUM(amount),0) v FROM bypass_supplier_payments WHERE tenant_id=? AND voided=0", t)!.v as number)),
  };
  const totalA = Object.values(assets).reduce((a, v) => a + v, 0), totalL = Object.values(liabilities).reduce((a, v) => a + v, 0);
  return { as_of: new Date().toISOString(), assets, liabilities, total_assets: totalA, total_liabilities: totalL, net_worth: totalA - totalL,
    note: "Fixed assets (land, building, dispensers) are not in the app; add them for a full balance sheet." };
}

analysis.get("/analysis/pl", h((req) => {
  const month = parse(z.object({ month: z.string().regex(/^\d{4}-\d{2}$/).optional() }), req.query).month ?? pkDate().slice(0, 7);
  return { pl: profitAndLoss(tid(req), month), balance_sheet: balanceSheet(tid(req)) };
}));
analysis.get("/analysis/pl.csv", (req, res, next) => {
  try {
    const month = String(req.query.month ?? pkDate().slice(0, 7));
    const p = profitAndLoss(tid(req), month), b = balanceSheet(tid(req));
    const rows: [string, string | number][] = [
      [`Profit & loss — ${month}`, ""], ["Fuel sales (pump)", p.income.fuel_retail], ["Fuel sales (wholesale)", p.income.fuel_wholesale], ["Shop sales", p.income.shop], ["Total income", p.income.total],
      ["Fuel cost", -p.cost_of_sales.fuel], ["Shop cost", -p.cost_of_sales.shop], ["Stock gain / loss (dips)", p.cost_of_sales.stock_gain_loss], ["Gross profit", p.gross_profit],
      ...p.expenses.by_category.map((e: any) => [`Expense: ${e.category}`, -e.amount] as [string, number]), ["Net profit", p.net_profit], ["", ""],
      ["Balance sheet (today)", ""], ...Object.entries(b.assets).map(([k, v]) => [`Asset: ${k.replace(/_/g, " ")}`, v] as [string, number]),
      ...Object.entries(b.liabilities).map(([k, v]) => [`Liability: ${k.replace(/_/g, " ")}`, -v] as [string, number]), ["Net worth", b.net_worth],
    ];
    res.setHeader("Content-Type", "text/csv; charset=utf-8");
    res.setHeader("Content-Disposition", `attachment; filename="profit-loss-${month}.csv"`);
    res.send("﻿" + rows.map(([k, v]) => `"${String(k).replaceAll('"', '""')}",${v}`).join("\n"));
  } catch (e) { next(e); }
});

/* ================= Payment statement reconciliation ================= */
type Line = { at: string | null; amount: number; ref: string; raw: string };
/** Read an Easypaisa / JazzCash / bank / card CSV: finds the date, amount and reference columns by their headings. */
export function parseStatement(csv: string): Line[] {
  const rows = csv.replace(/^﻿/, "").split(/\r?\n/).filter((l) => l.trim()).map((l) => (l.match(/("([^"]|"")*"|[^,]*)(,|$)/g) ?? []).map((c) => c.replace(/,$/, "").replace(/^"|"$/g, "").replace(/""/g, '"').trim()));
  if (rows.length < 2) throw new AppError(400, "The file has no rows");
  const head = rows[0].map((h) => h.toLowerCase());
  const col = (...names: string[]) => head.findIndex((h) => names.some((n) => h.includes(n)));
  const iDate = col("date", "time", "datetime"), iTime = head.findIndex((h, i) => i !== iDate && h.includes("time"));
  const iAmt = col("credit", "amount", "received", "paid in");
  const iRef = col("ref", "transaction", "tid", "trx", "id");
  if (iAmt < 0) throw new AppError(400, "Could not find an Amount (or Credit) column");
  return rows.slice(1).map((r) => {
    const amount = Number(String(r[iAmt] ?? "").replace(/[^0-9.\-]/g, ""));
    const ds = [r[iDate], iTime >= 0 ? r[iTime] : ""].filter(Boolean).join(" ");
    const ms = Date.parse(/[zZ+]|[+-]\d\d:?\d\d$/.test(ds) ? ds : `${ds.replace(" ", "T")}${ds.includes(":") ? "" : "T12:00"}+05:00`);
    return { at: isNaN(ms) ? null : new Date(ms).toISOString(), amount, ref: iRef >= 0 ? r[iRef] ?? "" : "", raw: r.join(" | ") };
  }).filter((l) => l.amount > 0);
}

analysis.post("/analysis/reconcile", h((req) => {
  const b = parse(z.object({ method: z.enum(["easypaisa", "jazzcash", "card", "raast"]), csv: z.string().min(10).max(2_000_000), window_minutes: z.number().min(5).max(24 * 60).default(120) }), req.body);
  const lines = parseStatement(b.csv);
  const dated = lines.filter((l) => l.at).map((l) => Date.parse(l.at!));
  if (!dated.length) throw new AppError(400, "Could not read the dates in the file");
  const from = new Date(Math.min(...dated) - b.window_minutes * 60_000).toISOString(), to = new Date(Math.max(...dated) + b.window_minutes * 60_000).toISOString();
  const sales = [
    ...all(`SELECT s.id, 'fuel' kind, s.amount, s.created_at, st.name station FROM sales s JOIN stations st ON st.id=s.station_id WHERE st.tenant_id=? AND s.payment_method=? AND s.created_at >= ? AND s.created_at <= ?`, tid(req), b.method, from, to),
    ...all(`SELECT s.id, 'shop' kind, s.total amount, s.created_at, st.name station FROM shop_sales s JOIN stations st ON st.id=s.station_id WHERE s.tenant_id=? AND s.payment_method=? AND s.created_at >= ? AND s.created_at <= ?`, tid(req), b.method, from, to),
  ].sort((a, b2) => a.created_at.localeCompare(b2.created_at));
  const used = new Set<string>();
  const matched: unknown[] = [], unmatchedLines: Line[] = [];
  for (const l of lines) {
    // same amount (±Rs 1) and the nearest time within the window
    const cands = sales.filter((s) => !used.has(`${s.kind}${s.id}`) && Math.abs(s.amount - l.amount) <= 1 && (!l.at || Math.abs(Date.parse(s.created_at) - Date.parse(l.at)) <= b.window_minutes * 60_000))
      .sort((x, y) => (l.at ? Math.abs(Date.parse(x.created_at) - Date.parse(l.at)) - Math.abs(Date.parse(y.created_at) - Date.parse(l.at)) : 0));
    if (cands[0]) { used.add(`${cands[0].kind}${cands[0].id}`); matched.push({ line: l, sale: cands[0] }); } else unmatchedLines.push(l);
  }
  const statementFrom = new Date(Math.min(...dated)).toISOString(), statementTo = new Date(Math.max(...dated)).toISOString();
  const missing = sales.filter((s) => !used.has(`${s.kind}${s.id}`) && s.created_at >= statementFrom && s.created_at <= statementTo);
  return {
    method: b.method, period: { from: statementFrom, to: statementTo }, statement_lines: lines.length, matched: matched.length,
    matched_amount: round2(matched.reduce((a: number, m: any) => a + m.line.amount, 0)),
    money_without_sale: unmatchedLines, money_without_sale_total: round2(unmatchedLines.reduce((a, l) => a + l.amount, 0)),
    sales_without_money: missing, sales_without_money_total: round2(missing.reduce((a, s) => a + s.amount, 0)),
  };
}));

/* ================= Price-change planner ================= */
export function pricePlanner(t: number) {
  const now = currentPrices(t);
  const stock = Object.fromEntries(all("SELECT t.product, SUM(t.current_l) l FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? GROUP BY t.product", t).map((r) => [r.product, r.l]));
  const today = pkDate();
  const d = Number(today.slice(8));
  // Pakistan petrol and diesel prices are normally revised on the 1st and 16th
  const nextIso = d < 16 ? `${today.slice(0, 8)}16` : new Date(Date.UTC(Number(today.slice(0, 4)), Number(today.slice(5, 7)), 1)).toISOString().slice(0, 10);
  const daysToNext = Math.round((Date.parse(`${nextIso}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / DAY);
  return {
    next_revision: nextIso, days_to_next: daysToNext,
    products: Object.keys(PRODUCTS).filter((p) => now[p]).map((p) => {
      const hist = all("SELECT price, effective_from FROM prices WHERE tenant_id=? AND product=? ORDER BY effective_from DESC, id DESC LIMIT 7", t, p);
      const changes = hist.slice(0, -1).map((h2, i) => round2(h2.price - hist[i + 1].price));
      const avg = changes.length ? round2(changes.reduce((a, c) => a + c, 0) / changes.length) : 0;
      const up = changes.filter((c) => c > 0).length, down = changes.filter((c) => c < 0).length;
      const litres = r0(stock[p] ?? 0);
      const trend = up > down ? "up" : down > up ? "down" : "flat";
      return {
        product: p, name: PRODUCTS[p], price: now[p].price, stock_l: litres, last_changes: changes, avg_change: avg, trend,
        scenarios: [-10, -5, 5, 10].map((x) => ({ change: x, stock_effect: r0(litres * x) })),
        advice: trend === "up" ? `Last changes mostly went up (avg ${avg > 0 ? "+" : ""}${avg}/L). Filling the tanks before ${nextIso} could gain about ${pkr(Math.abs(avg) * litres)} on today's stock.`
          : trend === "down" ? `Last changes mostly went down (avg ${avg}/L). Keep stock lean until ${nextIso}; a cut would cost about ${pkr(Math.abs(avg) * litres)} on today's stock.`
          : `No clear direction in recent changes. Order normally.`,
      };
    }),
  };
}
analysis.get("/analysis/price-planner", h((req) => pricePlanner(tid(req))));

/* ================= Tank gain / loss ================= */
export function gainLoss(t: number, from: string, to: string) {
  return all("SELECT t.id, t.name, t.product, s.name station FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? ORDER BY s.id, t.id", t).map((tk) => {
    const dip = get("SELECT COALESCE(SUM(measured_l - book_l),0) v, COUNT(*) n FROM dip_readings WHERE tank_id=? AND created_at >= ? AND created_at < ?", tk.id, from, to)!;
    const sold = get(`SELECT COALESCE(SUM(s.litres),0) v FROM sales s JOIN stations st ON st.id=s.station_id JOIN tanks t ON t.station_id=st.id AND t.product=s.product WHERE t.id=? AND s.created_at >= ? AND s.created_at < ?`, tk.id, from, to)!.v;
    const pct = sold ? round2((dip.v / sold) * 100) : 0;
    const cost = get("SELECT rate FROM supplier_txns WHERE tenant_id=? AND type='purchase' AND product=? ORDER BY txn_date DESC LIMIT 1", t, tk.product)?.rate ?? currentPrices(t)[tk.product]?.price ?? 0;
    return { ...tk, dips: dip.n, gain_loss_l: round2(dip.v), throughput_l: r0(sold), pct, value: r0(dip.v * cost),
      status: pct <= -0.5 ? "leak_suspected" : pct <= -0.25 ? "watch" : "ok" };
  });
}
analysis.get("/analysis/gain-loss", h((req) => { const r = range(parse(dates, req.query)); return { ...r, tanks: gainLoss(tid(req), r.from, r.to) }; }));

/* ================= Pump health score ================= */
export function healthScore(t: number) {
  const week = new Date(Date.now() - 7 * DAY).toISOString(), now = new Date().toISOString();
  const tanks = tankOutlook(t);
  const minDays = Math.min(...tanks.map((x) => x.days_to_empty));
  const stockScore = minDays >= 5 ? 100 : minDays >= 3 ? 75 : minDays >= 1.5 ? 45 : 15;
  const shifts = get("SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN variance < -500 THEN 1 END),0) bad FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.status='closed' AND sh.closed_at >= ?", t, week)!;
  const cashScore = shifts.n ? Math.round(100 - (shifts.bad / shifts.n) * 100) : 100;
  const b = balances(t);
  const old = b.receivables.aging.filter((a: any) => a.bucket === "61-90 days" || a.bucket === "90+ days").reduce((a: number, x: any) => a + x.amount, 0);
  const khataScore = b.receivables.total ? Math.round(100 - Math.min(100, (old / b.receivables.total) * 150)) : 100;
  const expired = get("SELECT COUNT(*) n FROM licences WHERE tenant_id=? AND expires_on < ?", t, pkDate())!.n;
  const soon = get("SELECT COUNT(*) n FROM licences WHERE tenant_id=? AND expires_on >= ? AND expires_on <= ?", t, pkDate(), pkDate(Date.now() + 7 * DAY))!.n;
  const checks = all("SELECT id FROM stations WHERE tenant_id=?", t).map((s) => checklistToday(t, s.id)).flat();
  const checkRatio = checks.length ? checks.filter((c) => c.done).length / checks.length : 1;
  const complianceScore = Math.max(0, Math.round(100 - expired * 40 - soon * 15 - (1 - checkRatio) * 30));
  const att = get("SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN late_minutes > 15 THEN 1 END),0) late FROM attendance WHERE tenant_id=? AND check_in >= ?", t, week)!;
  const staffScore = att.n ? Math.round(100 - (att.late / att.n) * 100) : 100;
  const thisW = get("SELECT COALESCE(SUM(s.amount),0) v FROM sales s JOIN stations st ON st.id=s.station_id WHERE st.tenant_id=? AND s.created_at >= ? AND s.created_at < ?", t, week, now)!.v;
  const lastW = get("SELECT COALESCE(SUM(s.amount),0) v FROM sales s JOIN stations st ON st.id=s.station_id WHERE st.tenant_id=? AND s.created_at >= ? AND s.created_at < ?", t, new Date(Date.now() - 14 * DAY).toISOString(), week)!.v;
  const trend = lastW ? round2(((thisW - lastW) / lastW) * 100) : 0;
  const salesScore = Math.max(0, Math.min(100, Math.round(80 + trend * 2)));
  const parts = [
    { key: "stock", label: "Stock", score: stockScore, weight: 20, note: `Lowest tank: ${minDays} days left` },
    { key: "cash", label: "Cash", score: cashScore, weight: 20, note: `${shifts.bad} of ${shifts.n} shifts short over Rs 500 this week` },
    { key: "khata", label: "Khata", score: khataScore, weight: 15, note: `${pkr(old)} owed for more than 60 days` },
    { key: "compliance", label: "Licences & checks", score: complianceScore, weight: 15, note: `${expired} expired, ${soon} expiring this week; ${Math.round(checkRatio * 100)}% of today's checks done` },
    { key: "staff", label: "Staff", score: staffScore, weight: 10, note: `${att.late} late check-ins this week` },
    { key: "sales", label: "Sales trend", score: salesScore, weight: 20, note: `${trend >= 0 ? "+" : ""}${trend}% vs last week` },
  ];
  const score = Math.round(parts.reduce((a, p) => a + p.score * p.weight, 0) / parts.reduce((a, p) => a + p.weight, 0));
  return { score, level: score >= 80 ? "good" : score >= 60 ? "fair" : "poor", parts };
}
analysis.get("/analysis/health", h((req) => healthScore(tid(req))));
