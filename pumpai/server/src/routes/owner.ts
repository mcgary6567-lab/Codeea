/**
 * Owner overview: the whole business on one screen — where the money is right now (office cash, with the salesmen,
 * banks, cheques not yet cleared), what others owe and what we owe, today's money in / out across every module,
 * and this month's profit. Every number comes from the same books the cashier, banks and accounts use.
 */
import { Router } from "express";
import { get, pkDate, pkDayStart, pkStart } from "../db.js";
import { h, tid, requirePerm } from "../auth.js";
import { round2 } from "../services.js";
import { cashPosition } from "./backoffice.js";
import { bankAccounts } from "./banks.js";
import { balances, buildReport } from "./reports.js";
import { profitAndLoss } from "./analysis.js";
import { cashierDayBook, onlineToday, cashWithSalesmen } from "./cashier.js";

export const owner = Router();

owner.get("/owner/overview", requirePerm("reports.view"), h((req) => {
  const t = tid(req);
  const today = pkDate(), dayStart = pkDayStart(), nowIso = new Date().toISOString();
  const one = (sql: string, ...a: unknown[]) => round2(get(sql, ...(a as []))!.v ?? 0);
  const cnt = (sql: string, ...a: unknown[]) => Number(get(sql, ...(a as []))!.n ?? 0);

  // ---- where the money is ----
  const cash = cashPosition(t);
  const ws = cashWithSalesmen(t);
  const banks = bankAccounts(t);
  const chqIn = get(`SELECT COUNT(*) n, COALESCE(SUM(amount),0) v FROM (
      SELECT amount FROM wholesale_cheques WHERE tenant_id=? AND status IN ('in_hand','deposited')
      UNION ALL SELECT amount FROM cheques WHERE tenant_id=? AND direction='in' AND status IN ('in_hand','deposited'))`, t, t)!;
  const chqOut = get("SELECT COUNT(*) n, COALESCE(SUM(amount),0) v FROM cheques WHERE tenant_id=? AND direction='out' AND status='issued'", t)!;
  const money = {
    office_cash: cash.cash_in_hand, last_count: cash.last_count,
    with_salesmen: ws.total, open_shifts: ws.open_shifts,
    banks: banks.total, accounts: banks.accounts.filter((a) => a.active).map((a) => ({ id: a.id, bank: a.bank, name: a.name, balance: a.balance })),
    total: round2(cash.cash_in_hand + ws.total + banks.total),
    cheques_in: { n: chqIn.n, amount: round2(chqIn.v) },
  };

  // ---- owed to us / we owe ----
  const b = balances(t);
  const staffAdv = Math.max(0, one(`SELECT COALESCE(SUM(CASE WHEN type IN ('advance','shortage') THEN amount WHEN type IN ('repayment','deduction') THEN -amount ELSE 0 END),0) v FROM staff_ledger WHERE tenant_id=?`, t));
  const owedToUs = { khata: b.receivables.khata_total, wholesale: b.receivables.wholesale_total, staff_advances: round2(staffAdv) };
  const weOwe = {
    suppliers: b.payables.suppliers_total,
    customer_advances: round2(b.payables.total - b.payables.suppliers_total),
    withholding_tax: one("SELECT COALESCE(SUM(amount),0) v FROM tax_withholdings WHERE tenant_id=? AND cpr_no IS NULL", t),
    unused_coupons: one("SELECT COALESCE(SUM(value),0) v FROM fuel_coupons WHERE tenant_id=? AND status='active'", t),
    pending_expenses: b.payables.pending_expenses.amount,
  };
  const sum = (o: Record<string, number>) => round2(Object.values(o).reduce((a, v) => a + v, 0));

  // ---- today ----
  const r = buildReport(t, dayStart, nowIso).summary;
  const book = cashierDayBook(t, today);
  const today_ = {
    revenue: r.revenue, retail_sales: r.retail_sales, retail_litres: r.retail_litres, retail_txns: r.retail_txns,
    wholesale: r.wholesale_net, wholesale_litres: r.wholesale_litres, shop: r.shop_sales,
    expenses: r.expenses, profit_estimate: r.gross_profit_estimate,
    money_in: { cash: book.totals.in_cash, bank: book.totals.in_bank }, money_out: { cash: book.totals.out_cash, bank: book.totals.out_bank },
    deposited: book.totals.deposited,
  };

  // ---- what every module did today ----
  const activity = [
    { key: "pos", to: "/pos", en: "Fuel sales", ur: "سیل", n: r.retail_txns, v: r.retail_sales },
    { key: "shop", to: "/shop", en: "Shop sales", ur: "دکان", n: cnt("SELECT COUNT(*) n FROM shop_sales WHERE tenant_id=? AND created_at >= ?", t, dayStart), v: r.shop_sales },
    { key: "khata_in", to: "/khata", en: "Khata payments", ur: "کھاتہ وصولی", n: cnt("SELECT COUNT(*) n FROM khata_ledger k JOIN customers c ON c.id=k.customer_id WHERE c.tenant_id=? AND k.type='credit' AND k.created_at >= ?", t, dayStart),
      v: one("SELECT COALESCE(SUM(k.amount),0) v FROM khata_ledger k JOIN customers c ON c.id=k.customer_id WHERE c.tenant_id=? AND k.type='credit' AND k.created_at >= ?", t, dayStart) },
    { key: "khata_out", to: "/khata", en: "Fuel on khata", ur: "ادھار", n: cnt("SELECT COUNT(*) n FROM khata_ledger k JOIN customers c ON c.id=k.customer_id WHERE c.tenant_id=? AND k.type='debit' AND k.created_at >= ?", t, dayStart),
      v: one("SELECT COALESCE(SUM(k.amount),0) v FROM khata_ledger k JOIN customers c ON c.id=k.customer_id WHERE c.tenant_id=? AND k.type='debit' AND k.created_at >= ?", t, dayStart) },
    { key: "ws_supply", to: "/wholesale", en: "Wholesale supply", ur: "ہول سیل سپلائی", n: cnt("SELECT COUNT(*) n FROM wholesale_txns WHERE tenant_id=? AND type='supply' AND voided=0 AND created_at >= ?", t, dayStart), v: r.wholesale_net },
    { key: "ws_pay", to: "/wholesale?tab=collect", en: "Wholesale payments", ur: "ہول سیل وصولی", n: cnt("SELECT COUNT(*) n FROM wholesale_txns WHERE tenant_id=? AND type='payment' AND voided=0 AND LOWER(COALESCE(method,''))<>'paid to depot' AND created_at >= ?", t, dayStart),
      v: one("SELECT COALESCE(SUM(amount),0) v FROM wholesale_txns WHERE tenant_id=? AND type='payment' AND voided=0 AND LOWER(COALESCE(method,''))<>'paid to depot' AND created_at >= ?", t, dayStart) },
    { key: "cashier", to: "/cashier", en: "Cash counter vouchers", ur: "کیشیئر رسیدیں", n: cnt("SELECT COUNT(*) n FROM cashier_vouchers WHERE tenant_id=? AND created_at >= ?", t, dayStart),
      v: one("SELECT COALESCE(SUM(amount),0) v FROM cashier_vouchers WHERE tenant_id=? AND created_at >= ?", t, dayStart) },
    { key: "cheques", to: "/cashier?tab=cheques", en: "Cheques in / out", ur: "چیک", n: cnt(`SELECT (SELECT COUNT(*) FROM cheques WHERE tenant_id=? AND created_at >= ?) + (SELECT COUNT(*) FROM wholesale_cheques WHERE tenant_id=? AND created_at >= ?) n`, t, dayStart, t, dayStart), v: null },
    { key: "suppliers", to: "/suppliers", en: "Paid to suppliers", ur: "سپلائر ادائیگی", n: cnt("SELECT COUNT(*) n FROM supplier_txns WHERE tenant_id=? AND type='payment' AND COALESCE(method,'')<>'WHT' AND LOWER(COALESCE(method,''))<>'paid to depot' AND created_at >= ?", t, dayStart),
      v: one("SELECT COALESCE(SUM(amount),0) v FROM supplier_txns WHERE tenant_id=? AND type='payment' AND COALESCE(method,'')<>'WHT' AND LOWER(COALESCE(method,''))<>'paid to depot' AND created_at >= ?", t, dayStart) },
    { key: "expenses", to: "/expenses", en: "Expenses", ur: "خرچے", n: cnt("SELECT COUNT(*) n FROM expenses WHERE tenant_id=? AND status='approved' AND created_at >= ?", t, dayStart), v: r.expenses },
    { key: "stock_in", to: "/stock", en: "Tankers received", ur: "ٹینکر آئے", n: cnt("SELECT COUNT(*) n FROM deliveries d JOIN tanks k ON k.id=d.tank_id JOIN stations s ON s.id=k.station_id WHERE s.tenant_id=? AND d.created_at >= ?", t, dayStart),
      v: null, l: one("SELECT COALESCE(SUM(d.received_l),0) v FROM deliveries d JOIN tanks k ON k.id=d.tank_id JOIN stations s ON s.id=k.station_id WHERE s.tenant_id=? AND d.created_at >= ?", t, dayStart) },
    { key: "bank", to: "/cash", en: "Put in bank", ur: "بینک میں جمع", n: cnt("SELECT COUNT(*) n FROM bank_deposits WHERE tenant_id=? AND created_at >= ?", t, dayStart), v: book.totals.deposited },
  ];

  // ---- online money: today per method (and where it lands), plus this month's total per method ----
  const onlineMonth = onlineToday(t, pkStart(`${today.slice(0, 7)}-01`));
  const online = { ...onlineToday(t), month: onlineMonth.methods.map((m) => ({ method: m.method, total: m.total })), month_total: onlineMonth.total };

  // ---- this month ----
  const pl = profitAndLoss(t, today.slice(0, 7), req.user!.role === "admin");

  return {
    as_of: nowIso, money,
    owed_to_us: { ...owedToUs, total: sum(owedToUs) },
    we_owe: { ...weOwe, total: sum(weOwe), cheques_issued: { n: chqOut.n, amount: round2(chqOut.v) } },
    net_position: round2(money.total + sum(owedToUs) - sum(weOwe)),
    today: today_, activity, online,
    month: { month: pl.month, income: pl.income.total, gross_profit: pl.gross_profit, expenses: pl.expenses.total, net_profit: pl.net_profit, margin_pct: pl.margin_pct },
  };
}));
