/**
 * Accounting export: the whole business as a double-entry general journal (one voucher per day per kind),
 * with a trial balance. Download as CSV (Excel / QuickBooks journal import) or Tally XML.
 */
import { Router, type Request, type Response, type NextFunction } from "express";
import { all, pkDate, pkStart, pkEnd } from "../db.js";
import { h, tid, requirePerm } from "../auth.js";
import { AppError, round2 } from "../services.js";
import { taxSettings, splitTax } from "./tax.js";
import { PRODUCTS } from "../config.js";

export const ledger = Router();

interface Line { account: string; debit: number; credit: number }
interface Voucher { date: string; no: string; type: string; narration: string; lines: Line[] }

const CASH = "Cash in hand", BANK = "Bank", DIGITAL = "Digital collections (Easypaisa/JazzCash/Card/Raast)";
const payAccount = (m: string | null | undefined) => {
  const x = (m ?? "").toLowerCase();
  if (x === "cash" || x === "") return CASH;
  if (["easypaisa", "jazzcash", "card", "raast"].includes(x)) return DIGITAL;
  if (x === "khata") return "Khata receivable";
  if (x === "loyalty") return "Loyalty points redeemed";
  if (x === "coupon") return "Fuel coupons (unused)";
  if (x === "wallet") return "Customer wallets";
  return BANK;
};

/** Money tagged by where it came from: tax paid to FBR clears the tax payable, a recovered claim, a coupon refund. */
const specialSide = (ref: string | null | undefined) =>
  !ref ? null : ref.startsWith("wht:") ? "Withholding tax payable" : ref.startsWith("claim:") ? "Shortage claims recovered" : ref.startsWith("coupon-refund:") ? "Fuel coupons (unused)" : null;

export function journal(t: number, fromDay: string, toDay: string) {
  const from = pkStart(fromDay), to = pkEnd(toDay);
  const P = [t, from, to] as const;
  const day = (iso: string) => pkDate(Date.parse(iso));
  const out: Voucher[] = [];
  const add = (date: string, type: string, narration: string, lines: Line[]) => {
    // one line per account (e.g. Easypaisa + JazzCash + card all go to digital collections)
    const net = new Map<string, number>();
    for (const l of lines) net.set(l.account, (net.get(l.account) ?? 0) + l.debit - l.credit);
    const ls = [...net.entries()].map(([account, v]) => ({ account, debit: v > 0 ? round2(v) : 0, credit: v < 0 ? round2(-v) : 0 })).filter((l) => l.debit || l.credit);
    if (ls.length) out.push({ date, no: "", type, narration, lines: ls });
  };
  const dr = (account: string, v: number): Line => (v >= 0 ? { account, debit: v, credit: 0 } : { account, debit: 0, credit: -v });
  const cr = (account: string, v: number): Line => (v >= 0 ? { account, debit: 0, credit: v } : { account, debit: -v, credit: 0 });
  const tax = taxSettings(t);

  // fuel sales: one voucher per day, money side by payment method
  const fuel = all(`SELECT date(datetime(s.created_at,'+5 hours')) d, s.payment_method m, SUM(s.amount) a, SUM(s.litres) l FROM sales s JOIN stations st ON st.id=s.station_id
    WHERE st.tenant_id=? AND s.created_at >= ? AND s.created_at < ? GROUP BY d, m`, ...P);
  for (const d of [...new Set(fuel.map((r) => r.d))]) {
    const rows = fuel.filter((r) => r.d === d);
    const total = rows.reduce((a, r) => a + r.a, 0);
    const split = splitTax(total, tax.fuel_gst_pct, true);
    add(d, "Sales", `Fuel sales ${d} (${Math.round(rows.reduce((a, r) => a + r.l, 0)).toLocaleString()} L)`,
      [...rows.map((r) => dr(payAccount(r.m), r.a)), cr("Fuel sales", split.value), cr("Output sales tax", split.tax)]);
  }
  // shop sales (sales tax split out)
  const shop = all(`SELECT date(datetime(ss.created_at,'+5 hours')) d, ss.payment_method m, SUM(ss.total) a FROM shop_sales ss WHERE ss.tenant_id=? AND ss.created_at >= ? AND ss.created_at < ? GROUP BY d, m`, ...P);
  const shopTax = all(`SELECT date(datetime(ss.created_at,'+5 hours')) d, i.category c, SUM(l.qty*l.price) a FROM shop_sale_lines l JOIN shop_items i ON i.id=l.item_id JOIN shop_sales ss ON ss.id=l.sale_id
    WHERE ss.tenant_id=? AND ss.created_at >= ? AND ss.created_at < ? GROUP BY d, c`, ...P);
  for (const d of [...new Set(shop.map((r) => r.d))]) {
    const rows = shop.filter((r) => r.d === d);
    const total = rows.reduce((a, r) => a + r.a, 0);
    const taxAmt = round2(shopTax.filter((r) => r.d === d && !tax.exempt.includes(r.c)).reduce((a, r) => a + splitTax(r.a, tax.gst_pct, true).tax, 0));
    add(d, "Sales", `Shop & lubricant sales ${d}`, [...rows.map((r) => dr(payAccount(r.m), r.a)), cr("Shop sales", total - taxAmt), cr("Output sales tax", taxAmt)]);
  }
  // khata: payments received, and charges other than fuel (late fee, adjustments)
  for (const r of all(`SELECT k.*, c.name FROM khata_ledger k JOIN customers c ON c.id=k.customer_id WHERE c.tenant_id=? AND k.created_at >= ? AND k.created_at < ? AND COALESCE(k.ref,'') NOT LIKE 'SALE%' AND COALESCE(k.ref,'') NOT LIKE 'SHOP-%'`, ...P)) {
    if (r.type === "credit") add(day(r.created_at), "Receipt", `Khata payment — ${r.name}${r.note ? ` (${r.note})` : ""}`, [dr(payAccount(r.ref), r.amount), cr("Khata receivable", r.amount)]);
    else add(day(r.created_at), "Journal", `Khata charge — ${r.name}${r.note ? ` (${r.note})` : ""}`, [dr("Khata receivable", r.amount), cr("Other income", r.amount)]);
  }
  // wholesale
  for (const r of all(`SELECT w.*, c.name FROM wholesale_txns w JOIN wholesale_clients c ON c.id=w.client_id WHERE w.tenant_id=? AND w.voided=0 AND w.created_at >= ? AND w.created_at < ?`, ...P)) {
    const d = day(r.created_at);
    if (r.type === "supply") add(d, "Sales", `Wholesale supply — ${r.name} ${r.litres} L ${PRODUCTS[r.product] ?? r.product ?? ""}`, [dr("Wholesale receivable", r.amount), cr("Wholesale sales", r.amount)]);
    if (r.type === "return") add(d, "Credit note", `Wholesale return — ${r.name} ${r.litres} L`, [dr("Wholesale sales", r.amount), cr("Wholesale receivable", r.amount)]);
    if (r.type === "payment") add(d, "Receipt", `Wholesale payment — ${r.name}${r.ref ? ` (${r.ref})` : ""}`, [dr(payAccount(r.method), r.amount), cr("Wholesale receivable", r.amount)]);
    if (r.type === "adjustment") add(d, "Journal", `Wholesale adjustment — ${r.name}${r.note ? ` (${r.note})` : ""}`, [dr("Wholesale receivable", r.amount), cr("Other income", r.amount)]);
  }
  // suppliers (purchase cost, payments, withholding, credit notes)
  for (const r of all(`SELECT s.*, p.name FROM supplier_txns s JOIN suppliers p ON p.id=s.supplier_id WHERE s.tenant_id=? AND s.created_at >= ? AND s.created_at < ?`, ...P)) {
    const d = day(r.created_at), pay = `Payable — ${r.name}`;
    if (r.type === "purchase") add(d, "Purchase", `Fuel purchase — ${r.name} ${r.litres} L ${PRODUCTS[r.product] ?? ""} @ ${r.rate}${r.ref ? ` (${r.ref})` : ""}`, [dr("Fuel purchases", r.amount), cr(pay, r.amount)]);
    if (r.type === "payment") add(d, "Payment", `${r.method === "WHT" ? "Income tax withheld" : "Paid"} — ${r.name}${r.ref ? ` (${r.ref})` : ""}`, [dr(pay, r.amount), cr(r.method === "WHT" ? "Withholding tax payable" : payAccount(r.method), r.amount)]);
    if (r.type === "adjustment") add(d, "Journal", `Supplier adjustment — ${r.name}${r.note ? ` (${r.note})` : ""}`, [dr(r.amount < 0 ? pay : "Supplier adjustments", Math.abs(r.amount)), cr(r.amount < 0 ? "Shortage claims recovered" : pay, Math.abs(r.amount))]);
  }
  // expenses
  for (const r of all(`SELECT * FROM expenses WHERE tenant_id=? AND (status='approved' OR (status='pending' AND shift_id IS NOT NULL)) AND created_at >= ? AND created_at < ?`, ...P))
    add(day(r.created_at), "Payment", `${r.category}${r.paid_to ? ` — ${r.paid_to}` : ""}${r.note ? ` (${r.note})` : ""}`, [dr(`Expense: ${r.category}`, r.amount), cr(payAccount(r.method), r.amount)]);
  // bank deposits, coupons, wallets
  for (const r of all(`SELECT * FROM bank_deposits WHERE tenant_id=? AND created_at >= ? AND created_at < ?`, ...P))
    add(day(r.created_at), "Contra", `Cash deposited — ${r.bank}${r.slip_ref ? ` slip ${r.slip_ref}` : ""}`, [dr(BANK, r.amount), cr(CASH, r.amount)]);
  // bank-only entries: cash taken out, charges, profit, owner money (transfers between own banks net to nil)
  const BANK_SIDE: Record<string, string> = { withdraw: CASH, charges: "Expense: Bank charges", profit: "Bank profit", owner_in: "Owner's capital", owner_out: "Owner's drawings", other_in: "Other income", other_out: "Other payments" };
  for (const r of all(`SELECT * FROM bank_txns WHERE tenant_id=? AND kind<>'transfer' AND txn_date >= ? AND txn_date < ?`, ...P)) {
    const other = specialSide(r.ref) ?? BANK_SIDE[r.kind] ?? "Suspense", v = Math.abs(r.amount);
    add(day(r.txn_date), r.kind === "withdraw" ? "Contra" : r.amount > 0 ? "Receipt" : "Payment", `${r.note ?? r.kind}${r.party ? ` — ${r.party}` : ""}`,
      r.amount > 0 ? [dr(BANK, v), cr(other, v)] : [dr(other, v), cr(BANK, v)]);
  }
  for (const r of all(`SELECT batch, method, MIN(sold_at) sold_at, SUM(value) v, COUNT(*) n, buyer FROM fuel_coupons WHERE tenant_id=? AND sold_at >= ? AND sold_at < ? GROUP BY batch`, ...P))
    add(day(r.sold_at), "Receipt", `Fuel coupons sold — ${r.n} (${r.batch})${r.buyer ? ` to ${r.buyer}` : ""}`, [dr(payAccount(r.method), r.v), cr("Fuel coupons (unused)", r.v)]);
  for (const r of all(`SELECT w.*, c.name FROM wallet_ledger w JOIN customers c ON c.id=w.customer_id WHERE w.tenant_id=? AND w.type IN ('deposit','refund') AND w.created_at >= ? AND w.created_at < ?`, ...P))
    add(day(r.created_at), r.type === "deposit" ? "Receipt" : "Payment", `Wallet ${r.type} — ${r.name}${r.ref ? ` (${r.ref})` : ""}`,
      r.type === "deposit" ? [dr(payAccount(r.method), r.amount), cr("Customer wallets", r.amount)] : [dr("Customer wallets", r.amount), cr(payAccount(r.method), r.amount)]);
  // staff: advances out, repayments in, shortages charged
  for (const r of all(`SELECT l.*, u.name FROM staff_ledger l JOIN users u ON u.id=l.user_id WHERE l.tenant_id=? AND l.created_at >= ? AND l.created_at < ?`, ...P)) {
    const d = day(r.created_at);
    const via = payAccount(r.method);
    if (r.type === "advance") add(d, "Payment", `Advance — ${r.name}`, [dr("Staff advances", r.amount), cr(via, r.amount)]);
    if (r.type === "repayment") add(d, "Receipt", `Advance paid back — ${r.name}`, [dr(via, r.amount), cr("Staff advances", r.amount)]);
    // kept back out of a cash salary (the salary expense was booked in full from cash) — or set off with no cash at all
    if (r.type === "deduction") add(d, "Journal", `Advance recovered — ${r.name}`, [dr(r.month ? CASH : "Salaries payable", r.amount), cr("Staff advances", r.amount)]);
    if (r.type === "shortage") add(d, "Journal", `Cash shortage charged — ${r.name}`, [dr("Staff advances", r.amount), cr("Cash short / over", r.amount)]);
    if (r.type === "bonus" && !r.month) add(d, "Payment", `Bonus — ${r.name}`, [dr("Expense: Staff bonus", r.amount), cr(via, r.amount)]);
  }
  // shift cash short / over
  for (const r of all(`SELECT sh.id, sh.attendant, sh.closed_at, sh.variance FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.status='closed' AND sh.closed_at >= ? AND sh.closed_at < ? AND ABS(COALESCE(sh.variance,0)) >= 1`, ...P))
    add(day(r.closed_at), "Journal", `Shift #${r.id} cash ${r.variance < 0 ? "short" : "over"} — ${r.attendant}`, [dr("Cash short / over", -r.variance), cr(CASH, -r.variance)]);

  // cash the cashier received from the salesman differs from what the salesman counted at closing
  for (const r of all(`SELECT sh.id, sh.attendant, sh.handed_at, sh.handed_amount, sh.cash_actual FROM shifts sh JOIN stations s ON s.id=sh.station_id
      WHERE s.tenant_id=? AND sh.handed_at IS NOT NULL AND sh.handed_at >= ? AND sh.handed_at < ? AND ABS(sh.handed_amount - COALESCE(sh.cash_actual,0)) >= 1`, ...P)) {
    const diff = round2(r.cash_actual - r.handed_amount);
    add(day(r.handed_at), "Journal", `Shift #${r.id} handover ${diff > 0 ? "short" : "over"} — ${r.attendant}`, [dr("Cash short / over", diff), cr(CASH, diff)]);
  }
  // cash counter: money in / out that belongs to no party account
  for (const r of all(`SELECT * FROM cashier_vouchers WHERE tenant_id=? AND party_type='other' AND LOWER(method)='cash' AND voided=0 AND created_at >= ? AND created_at < ?`, ...P)) {
    const other = specialSide(r.src) ?? (r.direction === "in" ? "Other income" : "Other payments");
    add(day(r.created_at), r.direction === "in" ? "Receipt" : "Payment", `${r.category ?? (r.direction === "in" ? "Other money in" : "Other payment")} — ${r.party_name}`,
      r.direction === "in" ? [dr(CASH, r.amount), cr(other, r.amount)] : [dr(other, r.amount), cr(CASH, r.amount)]);
  }

  out.sort((a, b) => a.date.localeCompare(b.date) || a.type.localeCompare(b.type));
  out.forEach((v, i) => { v.no = `PA-${v.date.replaceAll("-", "").slice(2)}-${String(i + 1).padStart(4, "0")}`; });
  const tb = new Map<string, { debit: number; credit: number }>();
  for (const v of out) for (const l of v.lines) { const x = tb.get(l.account) ?? { debit: 0, credit: 0 }; x.debit += l.debit; x.credit += l.credit; tb.set(l.account, x); }
  const trial = [...tb.entries()].map(([account, x]) => ({ account, debit: round2(x.debit), credit: round2(x.credit), balance: round2(x.debit - x.credit) })).sort((a, b) => a.account.localeCompare(b.account));
  return { from: fromDay, to: toDay, vouchers: out, trial_balance: trial, totals: { debit: round2(trial.reduce((a, x) => a + x.debit, 0)), credit: round2(trial.reduce((a, x) => a + x.credit, 0)) } };
}

function range(req: Request) {
  const to = String(req.query.to ?? pkDate()), from = String(req.query.from ?? `${to.slice(0, 7)}-01`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(from) || !/^\d{4}-\d{2}-\d{2}$/.test(to) || from > to) throw new AppError(400, "Choose a valid date range");
  if (Date.parse(to) - Date.parse(from) > 92 * 86_400_000) throw new AppError(400, "Export up to 3 months at a time");
  return { from, to };
}
ledger.get("/ledger", requirePerm("reports.view"), h((req) => {
  const r = range(req);
  const j = journal(tid(req), r.from, r.to);
  return { ...j, voucher_count: j.vouchers.length, vouchers: j.vouchers.slice(-200) };
}));

const download = (fn: (req: Request) => { name: string; type: string; body: string }) => (req: Request, res: Response, next: NextFunction) => {
  try { const f = fn(req); res.setHeader("Content-Type", f.type); res.setHeader("Content-Disposition", `attachment; filename="${f.name}"`); res.send(f.body); } catch (e) { next(e); }
};
ledger.get("/ledger.csv", requirePerm("reports.view"), download((req) => {
  const r = range(req);
  const j = journal(tid(req), r.from, r.to);
  const esc = (v: unknown) => `"${String(v ?? "").replaceAll('"', '""')}"`;
  const rows = [["Date", "Voucher", "Type", "Account", "Debit", "Credit", "Narration"],
    ...j.vouchers.flatMap((v) => v.lines.map((l) => [v.date, v.no, v.type, l.account, l.debit || "", l.credit || "", v.narration]))];
  return { name: `general-ledger-${r.from}-to-${r.to}.csv`, type: "text/csv; charset=utf-8", body: "﻿" + rows.map((x) => x.map(esc).join(",")).join("\n") };
}));
ledger.get("/ledger/tally.xml", requirePerm("reports.view"), download((req) => {
  const r = range(req);
  const j = journal(tid(req), r.from, r.to);
  const x = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" })[c]!);
  const vouchers = j.vouchers.map((v) => `<TALLYMESSAGE xmlns:UDF="TallyUDF"><VOUCHER VCHTYPE="Journal" ACTION="Create"><DATE>${v.date.replaceAll("-", "")}</DATE><VOUCHERTYPENAME>Journal</VOUCHERTYPENAME><VOUCHERNUMBER>${v.no}</VOUCHERNUMBER><NARRATION>${x(v.narration)}</NARRATION>${
    v.lines.map((l) => `<ALLLEDGERENTRIES.LIST><LEDGERNAME>${x(l.account)}</LEDGERNAME><ISDEEMEDPOSITIVE>${l.debit ? "Yes" : "No"}</ISDEEMEDPOSITIVE><AMOUNT>${(l.debit ? -l.debit : l.credit).toFixed(2)}</AMOUNT></ALLLEDGERENTRIES.LIST>`).join("")}</VOUCHER></TALLYMESSAGE>`).join("\n");
  return { name: `tally-vouchers-${r.from}-to-${r.to}.xml`, type: "application/xml; charset=utf-8",
    body: `<?xml version="1.0" encoding="UTF-8"?>\n<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC><REQUESTDATA>\n${vouchers}\n</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>` };
}));
