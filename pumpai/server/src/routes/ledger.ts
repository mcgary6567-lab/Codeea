/**
 * Accounting export: the whole business as a double-entry general journal (one voucher per day per kind),
 * with a trial balance. Download as CSV (Excel / QuickBooks journal import) or Tally XML.
 */
import { Router, type Request, type Response, type NextFunction } from "express";
import { all, get, pkDate, pkStart, pkEnd } from "../db.js";
import { h, tid, requirePerm } from "../auth.js";
import { AppError, round2 } from "../services.js";
import { taxSettings, splitTax } from "./tax.js";
import { PRODUCTS } from "../config.js";
import { posMap, cardFees, DEPOT_PAY, BYPASS_PAY } from "./banks.js";
import { cashPosition } from "./backoffice.js";
import { digitalMethods } from "./lookups.js";

export const ledger = Router();

interface Line { account: string; debit: number; credit: number }
interface Voucher { date: string; no: string; type: string; narration: string; lines: Line[] }

const CASH = "Cash in hand", BANK = "Bank", DIGITAL = "Digital collections (Easypaisa/JazzCash/Card/Raast)";
/** Money said to be "bank transfer" / "cheque" with no bank account chosen: it waits here until linked (Cash & bank shows it to link). */
const UNLINKED = "Bank — account not chosen";
const payAccount = (t: number, m: string | null | undefined) => {
  const x = (m ?? "").toLowerCase();
  if (x === "cash" || x === "") return CASH;
  if (digitalMethods(t).includes(x)) return DIGITAL;
  if (x === "khata") return "Khata receivable";
  if (x === "loyalty") return "Loyalty points redeemed";
  if (x === "coupon") return "Fuel coupons (unused)";
  if (x === "wallet") return "Customer wallets";
  return UNLINKED;
};

/** Money tagged by where it came from: tax paid to FBR clears the tax payable, a recovered claim, a coupon refund. */
const OTHER_INCOME_CEO = "Other income (CEO)", OTHER_EXPENSE_CEO = "Expense: Other (CEO)", OTHER_DISCOUNT_CEO = "Other discount (CEO)";
const OWN_CAP = "Owner's capital", OWN_DRAW = "Owner's drawings";
/** Go-live opening balances (khata, staff advances) are brought forward against this equity account, never P&L. */
const OPENING_EQ = "Opening balances (go-live)", OPEN_REF = "open:";
const specialSide = (ref: string | null | undefined) =>
  !ref ? null : ref.startsWith("shopbuy:") ? "Shop stock" : ref.startsWith("wht:") ? "Withholding tax payable" : ref.startsWith("claim:") ? "Shortage claims recovered" : ref.startsWith("coupon-refund:") ? "Fuel coupons (unused)" : ref.startsWith("bypassfuel:") ? "Depot money held" : null;
/** CEO-only "other" markers: income / expense / discount. Hidden from non-admin views (shown as owner capital/drawings). */
const otherKind = (ref: string | null | undefined): "inc" | "exp" | "disc" | "pty" | null =>
  !ref ? null : ref.startsWith("oth-inc:") ? "inc" : ref.startsWith("oth-exp:") ? "exp" : ref.startsWith("oth-disc:") ? "disc" : ref.startsWith("oth-pty:") ? "pty" : null;
const isOtherDisc = (ref: string | null | undefined) => otherKind(ref) === "disc";
/** CEO income/expense posted ON a party's own khata ("oth-pty:<entry>"): the cash/bank row is booked against that party's
 *  receivable (the party's own ledger row is skipped — it only moves the balance). Not private: it is on the party's statement. */
const isToParty = (ref: string | null | undefined) => otherKind(ref) === "pty";
const PARTY_RECV: Record<string, string> = { khata: "Khata receivable", wholesale: "Wholesale receivable", carriage: "Carriage receivable" };
const toPartyAccount = (ref: string) => PARTY_RECV[get("SELECT party_type FROM other_entries WHERE id=?", Number(ref.slice(8)))?.party_type ?? ""] ?? "Suspense";

/** `showPrivate` (default true) includes the CEO-only "other income/expense/discount" with their real
 *  accounts + narration. When false (non-admin views), those entries appear as owner capital/drawings
 *  with generic narration, so the manager cannot see them — the books still balance either way. */
export function journal(t: number, fromDay: string, toDay: string, showPrivate = true) {
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
  // money that names a bank account sits in the bank (as the bank module counts it), whatever the method was called;
  // POS card / JazzCash / Easypaisa / Raast sales go to the account each method is linked to
  const posBank = posMap(t);
  const via = (m: string | null | undefined, accountId?: number | null) => (accountId ? BANK : payAccount(t, m));
  // ...from the day that account was opened (as the bank module counts them); before that they stay in digital collections
  const posFrom: Record<string, string> = Object.fromEntries(Object.entries(posBank).map(([m, id]) => [m, get("SELECT opening_date d FROM bank_accounts WHERE id=? AND tenant_id=?", id, t)?.d ?? "9999-12-31"]));
  const sold = (m: string | null | undefined, d: string) => (m && posBank[m.toLowerCase()] && d >= posFrom[m.toLowerCase()] ? BANK : payAccount(t, m));

  // fuel sales: one voucher per day, money side by payment method (card-pending khata holds are handled on their own below)
  const fuel = all(`SELECT date(datetime(s.created_at,'+5 hours')) d, s.payment_method m, SUM(s.amount) a,
      SUM(CASE WHEN s.discount_kind='direct' THEN 0 ELSE COALESCE(s.discount,0) END) dsc, SUM(CASE WHEN s.discount_kind='direct' THEN COALESCE(s.discount,0) ELSE 0 END) ddir, SUM(s.litres) l FROM sales s JOIN stations st ON st.id=s.station_id
    WHERE st.tenant_id=? AND s.created_at >= ? AND s.created_at < ? AND s.payment_method <> 'wholesale' AND NOT (COALESCE(s.pending,0)=1 OR s.cleared_at IS NOT NULL) GROUP BY d, m`, ...P);
  for (const d of [...new Set(fuel.map((r) => r.d))]) {
    const rows = fuel.filter((r) => r.d === d);
    const net = rows.reduce((a, r) => a + r.a, 0);          // what customers were actually billed / paid
    const disc = rows.reduce((a, r) => a + r.dsc, 0);        // lump-sum khata discounts given
    const ddir = rows.reduce((a, r) => a + r.ddir, 0);       // direct (walk-in) discounts given at the POS
    const split = splitTax(net + disc + ddir, tax.fuel_gst_pct, true); // revenue is booked at the gross (before discount)
    add(d, "Sales", `Fuel sales ${d} (${Math.round(rows.reduce((a, r) => a + r.l, 0)).toLocaleString()} L)`,
      [...rows.map((r) => dr(sold(r.m, d), r.a)), ...(disc > 0 ? [dr("Discount given — khata", disc)] : []), ...(ddir > 0 ? [dr("Discount given — direct", ddir)] : []), cr("Fuel sales", split.value), cr("Output sales tax", split.tax)]);
  }
  // khata "card pending": on the fill day the fuel is sold into an asset (not yet billed) at the meter rate; when the
  // card is brought in it is billed to the khata at that day's rate, and the rate difference lands on the clear day.
  const UNBILLED = "Unbilled fuel (card pending)";
  for (const r of all(`SELECT date(datetime(s.created_at,'+5 hours')) d, SUM(s.amount) a, SUM(s.litres) l FROM sales s JOIN stations st ON st.id=s.station_id
    WHERE st.tenant_id=? AND s.created_at >= ? AND s.created_at < ? AND (COALESCE(s.pending,0)=1 OR s.cleared_at IS NOT NULL) GROUP BY d`, ...P)) {
    const split = splitTax(r.a, tax.fuel_gst_pct, true);
    add(r.d, "Sales", `Fuel sales — card pending ${r.d} (${Math.round(r.l).toLocaleString()} L)`,
      [dr(UNBILLED, r.a), cr("Fuel sales", split.value), cr("Output sales tax", split.tax)]);
  }
  for (const r of all(`SELECT date(datetime(s.cleared_at,'+5 hours')) d, SUM(s.amount) prov, SUM(round(s.litres*s.clear_rate,2)) fin FROM sales s JOIN stations st ON st.id=s.station_id
    WHERE st.tenant_id=? AND s.cleared_at >= ? AND s.cleared_at < ? GROUP BY d`, ...P)) {
    const split = splitTax(round2(r.fin - r.prov), tax.fuel_gst_pct, true);
    add(r.d, "Journal", `Card pending cleared ${r.d} — billed to khata`,
      [dr("Khata receivable", round2(r.fin)), cr(UNBILLED, round2(r.prov)), cr("Fuel sales", split.value), cr("Output sales tax", split.tax)]);
  }
  // shop sales (sales tax split out)
  const shop = all(`SELECT date(datetime(ss.created_at,'+5 hours')) d, ss.payment_method m, SUM(ss.total) a FROM shop_sales ss WHERE ss.tenant_id=? AND ss.created_at >= ? AND ss.created_at < ? GROUP BY d, m`, ...P);
  const shopTax = all(`SELECT date(datetime(ss.created_at,'+5 hours')) d, i.category c, SUM(l.qty*l.price) a FROM shop_sale_lines l JOIN shop_items i ON i.id=l.item_id JOIN shop_sales ss ON ss.id=l.sale_id
    WHERE ss.tenant_id=? AND ss.created_at >= ? AND ss.created_at < ? GROUP BY d, c`, ...P);
  for (const d of [...new Set(shop.map((r) => r.d))]) {
    const rows = shop.filter((r) => r.d === d);
    const total = rows.reduce((a, r) => a + r.a, 0);
    const taxAmt = round2(shopTax.filter((r) => r.d === d && !tax.exempt.includes(r.c)).reduce((a, r) => a + splitTax(r.a, tax.gst_pct, true).tax, 0));
    add(d, "Sales", `Shop & lubricant sales ${d}`, [...rows.map((r) => dr(sold(r.m, d), r.a)), cr("Shop sales", total - taxAmt), cr("Output sales tax", taxAmt)]);
  }
  // shop inventory: goods received with no money entry here (opening / entered elsewhere) come in against the go-live equity;
  // the cost of what sold each day moves from stock to cost of sales; a count difference is a loss (expense) or a gain
  for (const r of all(`SELECT m.*, i.name FROM shop_moves m JOIN shop_items i ON i.id=m.item_id WHERE i.tenant_id=? AND m.type='purchase' AND COALESCE(m.paid_by,'none')='none' AND m.created_at >= ? AND m.created_at < ?`, ...P)) {
    const v = round2(Math.abs(r.amount ?? r.qty * (r.cost ?? 0)));
    if (v) add(day(r.created_at), "Journal", `Shop goods in (no money entry) — ${r.name} × ${r.qty}`, [dr("Shop stock", v), cr(OPENING_EQ, v)]);
  }
  // one-click go-live booking of old shelf stock (books.ts): the shelf value comes in against the go-live equity, never P&L
  for (const r of all(`SELECT m.*, i.name FROM shop_moves m JOIN shop_items i ON i.id=m.item_id WHERE i.tenant_id=? AND m.type='book' AND m.created_at >= ? AND m.created_at < ?`, ...P)) {
    const v = round2(Math.abs(r.amount ?? 0));
    if (v) add(day(r.created_at), "Opening", `Shop stock booked (go-live) — ${r.name}${r.note ? ` (${r.note})` : ""}`, (r.amount ?? 0) > 0 ? [dr("Shop stock", v), cr(OPENING_EQ, v)] : [dr(OPENING_EQ, v), cr("Shop stock", v)]);
  }
  for (const r of all(`SELECT date(datetime(created_at,'+5 hours')) d, SUM(cost_total) c FROM shop_sales WHERE tenant_id=? AND created_at >= ? AND created_at < ? GROUP BY d`, ...P))
    if (r.c) add(r.d, "Journal", `Cost of shop goods sold ${r.d}`, [dr("Cost of shop sales", r.c), cr("Shop stock", r.c)]);
  for (const r of all(`SELECT m.*, i.name FROM shop_moves m JOIN shop_items i ON i.id=m.item_id WHERE i.tenant_id=? AND m.type='adjust' AND m.created_at >= ? AND m.created_at < ?`, ...P)) {
    const v = round2(Math.abs(r.qty * (r.cost ?? 0)));
    if (v) add(day(r.created_at), "Journal", `Shop stock ${r.qty < 0 ? "loss" : "gain"} — ${r.name} ${r.qty > 0 ? "+" : ""}${r.qty}${r.note ? ` (${r.note})` : ""}`,
      r.qty < 0 ? [dr("Expense: Shop stock loss", v), cr("Shop stock", v)] : [dr("Shop stock", v), cr("Other income", v)]);
  }
  // withholding tax deducted from a payee outside the supplier flow (rent, contractor): the deducted part is still a cost
  // of that bill and sits in the payable until it is deposited with FBR (the deposit voucher, ref wht:, clears it)
  for (const r of all(`SELECT w.* FROM tax_withholdings w WHERE w.tenant_id=? AND w.supplier_txn_id IS NULL AND w.supplier_id IS NULL AND w.txn_date >= ? AND w.txn_date < ?`, ...P))
    add(day(r.txn_date), "Journal", `Income tax withheld — ${r.payee}${r.section ? ` (s.${r.section})` : ""}`, [dr("Expense: Withholding tax (on behalf)", r.amount), cr("Withholding tax payable", r.amount)]);
  // khata: payments received, and charges other than fuel (late fee, adjustments)
  for (const r of all(`SELECT k.*, c.name FROM khata_ledger k JOIN customers c ON c.id=k.customer_id WHERE c.tenant_id=? AND k.created_at >= ? AND k.created_at < ? AND COALESCE(k.ref,'') NOT LIKE 'SALE%' AND COALESCE(k.ref,'') NOT LIKE 'SHOP-%'`, ...P)) {
    // go-live opening balance: brought forward, not this period's income
    if (isToParty(r.ref)) continue; // booked from the cash/bank row against Khata receivable
    if (r.ref === OPEN_REF) { add(day(r.created_at), "Opening", `Opening balance — ${r.name}`, r.type === "debit" ? [dr("Khata receivable", r.amount), cr(OPENING_EQ, r.amount)] : [dr(OPENING_EQ, r.amount), cr("Khata receivable", r.amount)]); continue; }
    if (r.type === "credit") {
      const od = isOtherDisc(r.ref);
      const dside = od ? (showPrivate ? OTHER_DISCOUNT_CEO : OWN_DRAW) : via(r.ref, r.account_id);
      const narr = od ? (showPrivate ? `Discount given — ${r.name}${r.note ? ` (${r.note})` : ""}` : "Owner's drawings") : `Khata payment — ${r.name}${r.note ? ` (${r.note})` : ""}`;
      add(day(r.created_at), od ? "Journal" : "Receipt", narr, [dr(dside, r.amount), cr("Khata receivable", r.amount)]);
    }
    else add(day(r.created_at), "Journal", `Khata charge — ${r.name}${r.note ? ` (${r.note})` : ""}`, [dr("Khata receivable", r.amount), cr("Other income", r.amount)]);
  }
  // wholesale
  for (const r of all(`SELECT w.*, c.name FROM wholesale_txns w JOIN wholesale_clients c ON c.id=w.client_id WHERE w.tenant_id=? AND w.voided=0 AND w.txn_date >= ? AND w.txn_date < ?`, ...P)) {
    const d = day(r.txn_date);
    if (isToParty(r.ref)) continue; // booked from the cash/bank row against Wholesale receivable
    if (r.type === "supply") add(d, "Sales", `Wholesale supply — ${r.name} ${r.litres} L ${PRODUCTS[r.product] ?? r.product ?? ""}`, [dr("Wholesale receivable", r.amount), cr("Wholesale sales", r.amount)]);
    if (r.type === "return") add(d, "Credit note", `Wholesale return — ${r.name} ${r.litres} L`, [dr("Wholesale sales", r.amount), cr("Wholesale receivable", r.amount)]);
    // paid straight to our depot: what we owe the depot goes down instead of money coming in
    const depot = r.type === "payment" && r.method === DEPOT_PAY ? get("SELECT p.name FROM supplier_txns s JOIN suppliers p ON p.id=s.supplier_id WHERE s.ref=?", `wtx:${r.id}`)?.name : null;
    if (depot) add(d, "Journal", `Wholesale payment — ${r.name} paid ${depot} direct${r.ref ? ` (${r.ref})` : ""}`, [dr(`Payable — ${depot}`, r.amount), cr("Wholesale receivable", r.amount)]);
    // a client who settled a bypass supplier direct: the receivable credit is booked against the bypass payable (below), so skip it here
    else if (r.type === "payment" && r.method !== BYPASS_PAY) {
      const od = isOtherDisc(r.ref);
      const dside = od ? (showPrivate ? OTHER_DISCOUNT_CEO : OWN_DRAW) : via(r.method, r.account_id);
      const narr = od ? (showPrivate ? `Discount given — ${r.name}` : "Owner's drawings") : `Wholesale payment — ${r.name}${r.ref ? ` (${r.ref})` : ""}`;
      add(d, od ? "Journal" : "Receipt", narr, [dr(dside, r.amount), cr("Wholesale receivable", r.amount)]);
    }
    if (r.type === "adjustment") add(d, "Journal", `Wholesale adjustment — ${r.name}${r.note ? ` (${r.note})` : ""}`, [dr("Wholesale receivable", r.amount), cr("Other income", r.amount)]);
    // bypass on our depot ID: only the kiraya is ours — the client owes us the carriage, booked as income (no fuel on our books)
    if (r.type === "carriage") add(d, "Journal", `Carriage / kiraya — ${r.name}${r.litres ? ` ${r.litres} L ${PRODUCTS[r.product] ?? r.product ?? ""}` : ""}${r.ref ? ` (inv ${r.ref})` : ""}`, [dr("Wholesale receivable", r.amount), cr("Carriage income", r.amount)]);
  }
  // carriage / kiraya with thekedars (bypass on our depot ID): their own receivable, booked as income (no fuel on our books)
  for (const r of all(`SELECT c.*, k.name FROM carriage_txns c JOIN thekedars k ON k.id=c.thekedar_id WHERE c.tenant_id=? AND c.voided=0 AND c.txn_date >= ? AND c.txn_date < ?`, ...P)) {
    const d = day(r.txn_date);
    if (isToParty(r.ref)) continue; // booked from the cash/bank row against Carriage receivable
    if (r.type === "carriage") add(d, "Journal", `Carriage / kiraya — ${r.name}${r.litres ? ` ${r.litres} L ${PRODUCTS[r.product] ?? r.product ?? ""}` : ""}${r.ref ? ` (inv ${r.ref})` : ""}`, [dr("Carriage receivable", r.amount), cr("Carriage income", r.amount)]);
    if (r.type === "payment") {
      const od = isOtherDisc(r.ref);
      const dside = od ? (showPrivate ? OTHER_DISCOUNT_CEO : OWN_DRAW) : via(r.method, r.account_id);
      const narr = od ? (showPrivate ? `Discount given — ${r.name}` : "Owner's drawings") : `Carriage payment — ${r.name}${r.ref ? ` (${r.ref})` : ""}`;
      add(d, od ? "Journal" : "Receipt", narr, [dr(dside, r.amount), cr("Carriage receivable", r.amount)]);
    }
    if (r.type === "adjustment") add(d, "Journal", `Carriage adjustment — ${r.name}${r.note ? ` (${r.note})` : ""}`, r.amount >= 0 ? [dr("Carriage receivable", r.amount), cr("Other income", r.amount)] : [dr("Other income", -r.amount), cr("Carriage receivable", -r.amount)]);
  }
  // bypass delivery: fuel bought from suppliers goes into bypass stock (asset); its cost hits P&L only when delivered
  for (const r of all(`SELECT b.*, s.name FROM bypass_purchases b JOIN suppliers s ON s.id=b.supplier_id WHERE b.tenant_id=? AND b.voided=0 AND b.txn_date >= ? AND b.txn_date < ?`, ...P)) {
    add(day(r.txn_date), "Purchase", `Bypass fuel — ${r.name} ${r.litres} L ${PRODUCTS[r.product] ?? r.product ?? ""} @ ${r.cost_rate}${r.ref ? ` (${r.ref})` : ""}`, [dr("Bypass stock", r.amount), cr("Bypass suppliers payable", r.amount)]);
  }
  // delivered out of the bypass stock: its share of the cost moves from stock to cost of sales
  for (const r of all(`SELECT d.*, tk.name tank_name FROM bypass_drops d LEFT JOIN tanks tk ON tk.id=d.tank_id WHERE d.tenant_id=? AND d.voided=0 AND d.cost_amount > 0 AND d.txn_date >= ? AND d.txn_date < ?`, ...P)) {
    // leftover unloaded into our own tank: the fuel becomes pump stock (bought at the bypass average cost), not a bypass sale
    if (r.tank_id) add(day(r.txn_date), "Journal", `Bypass leftover to pump stock — ${r.litres} L ${PRODUCTS[r.product] ?? r.product ?? ""} @ ${r.unit_cost} → ${r.tank_name ?? "tank"}`, [dr("Fuel purchases", r.cost_amount), cr("Bypass stock", r.cost_amount)]);
    else add(day(r.txn_date), "Journal", `Bypass delivered — ${r.litres} L ${PRODUCTS[r.product] ?? r.product ?? ""} @ ${r.unit_cost}`, [dr("Bypass fuel cost", r.cost_amount), cr("Bypass stock", r.cost_amount)]);
  }
  for (const r of all(`SELECT b.*, s.name FROM bypass_supplier_payments b JOIN suppliers s ON s.id=b.supplier_id WHERE b.tenant_id=? AND b.voided=0 AND b.txn_date >= ? AND b.txn_date < ?`, ...P)) {
    const d = day(r.txn_date);
    if (r.mode === "we_pay") add(d, "Payment", `Bypass supplier paid — ${r.name}${r.ref ? ` (${r.ref})` : ""}`, [dr("Bypass suppliers payable", r.amount), cr(via(r.method, r.account_id), r.amount)]);
    else if (r.mode === "through_us") add(d, "Payment", `Bypass forwarded to ${r.name} (through us)${r.ref ? ` (${r.ref})` : ""}`, [dr("Bypass suppliers payable", r.amount), cr(via(r.method, r.account_id), r.amount)]);
    else if (r.mode === "client_direct") add(d, "Journal", `Bypass — client paid ${r.name} direct${r.ref ? ` (${r.ref})` : ""}`, [dr("Bypass suppliers payable", r.amount), cr("Wholesale receivable", r.amount)]);
  }
  // suppliers (purchase cost, payments, withholding, credit notes)
  for (const r of all(`SELECT s.*, p.name FROM supplier_txns s JOIN suppliers p ON p.id=s.supplier_id WHERE s.tenant_id=? AND s.txn_date >= ? AND s.txn_date < ?`, ...P)) {
    const d = day(r.txn_date), pay = `Payable — ${r.name}`;
    if (r.type === "purchase" && String(r.ref ?? "").startsWith("shopbuy:")) add(d, "Purchase", `Shop goods — ${r.name}${r.note ? ` (${r.note})` : ""}`, [dr("Shop stock", r.amount), cr(pay, r.amount)]);
    else if (r.type === "purchase") add(d, "Purchase", `Fuel purchase — ${r.name} ${r.litres} L ${PRODUCTS[r.product] ?? ""} @ ${r.rate}${r.ref ? ` (${r.ref})` : ""}`, [dr("Fuel purchases", r.amount), cr(pay, r.amount)]);
    if (r.type === "payment" && r.method === DEPOT_PAY) continue; // booked with the client's payment above
    if (r.type === "payment") add(d, "Payment", `${r.method === "WHT" ? "Income tax withheld" : "Paid"} — ${r.name}${r.ref ? ` (${r.ref})` : ""}`, [dr(pay, r.amount), cr(r.method === "WHT" ? "Withholding tax payable" : via(r.method, r.account_id), r.amount)]);
    if (r.type === "adjustment") add(d, "Journal", `Supplier adjustment — ${r.name}${r.note ? ` (${r.note})` : ""}`, [dr(r.amount < 0 ? pay : "Supplier adjustments", Math.abs(r.amount)), cr(r.amount < 0 ? "Shortage claims recovered" : pay, Math.abs(r.amount))]);
  }
  // expenses
  for (const r of all(`SELECT * FROM expenses WHERE tenant_id=? AND (status='approved' OR (status='pending' AND shift_id IS NOT NULL)) AND created_at >= ? AND created_at < ?`, ...P))
    add(day(r.created_at), "Payment", `${r.category}${r.paid_to ? ` — ${r.paid_to}` : ""}${r.note ? ` (${r.note})` : ""}`, [dr(`Expense: ${r.category}`, r.amount), cr(via(r.method, r.account_id), r.amount)]);
  // bank deposits, coupons, wallets
  for (const r of all(`SELECT * FROM bank_deposits WHERE tenant_id=? AND created_at >= ? AND created_at < ?`, ...P))
    add(day(r.created_at), "Contra", `Cash deposited — ${r.bank}${r.slip_ref ? ` slip ${r.slip_ref}` : ""}`, [dr(r.account_id ? BANK : UNLINKED, r.amount), cr(CASH, r.amount)]);
  // bank-only entries: cash taken out, charges, profit, owner money (transfers between own banks net to nil)
  const BANK_SIDE: Record<string, string> = { withdraw: CASH, charges: "Expense: Bank charges", profit: "Bank profit", owner_in: "Owner's capital", owner_out: "Owner's drawings", other_in: "Other income", other_out: "Other payments" };
  for (const r of all(`SELECT * FROM bank_txns WHERE tenant_id=? AND kind<>'transfer' AND txn_date >= ? AND txn_date < ?`, ...P)) {
    const ok = otherKind(r.ref); // CEO other income/expense into/out of a bank account
    const v = Math.abs(r.amount);
    let other: string, narr: string;
    if (ok === "inc" || ok === "exp") {
      other = showPrivate ? (ok === "inc" ? OTHER_INCOME_CEO : OTHER_EXPENSE_CEO) : (r.amount > 0 ? OWN_CAP : OWN_DRAW);
      narr = showPrivate ? `${r.note ?? r.kind}${r.party ? ` — ${r.party}` : ""}` : (r.amount > 0 ? "Owner's capital introduced" : "Owner's drawings");
    } else if (ok === "pty") {
      other = toPartyAccount(r.ref); // on the party's own account: paid to / received from them
      narr = `${r.amount > 0 ? "Received from" : "Paid to"} ${r.party ?? "party"}${r.note ? ` (${r.note})` : ""}`;
    } else {
      other = specialSide(r.ref) ?? BANK_SIDE[r.kind] ?? "Suspense";
      narr = `${r.note ?? r.kind}${r.party ? ` — ${r.party}` : ""}`;
    }
    add(day(r.txn_date), r.kind === "withdraw" ? "Contra" : r.amount > 0 ? "Receipt" : "Payment", narr,
      r.amount > 0 ? [dr(BANK, v), cr(other, v)] : [dr(other, v), cr(BANK, v)]);
  }
  // bank card/POS fee (MDR) on each day's card sales — same figures the bank module nets off the balance
  for (const f of cardFees(t)) if (f.day >= fromDay && f.day <= toDay)
    add(f.day, "Payment", `Bank card charges ${f.day} (${f.pct}% of ${Math.round(f.sales).toLocaleString()})`, [dr("Expense: Card charges", f.fee), cr(BANK, f.fee)]);
  for (const r of all(`SELECT batch, method, account_id, MIN(sold_at) sold_at, SUM(value) v, COUNT(*) n, buyer FROM fuel_coupons WHERE tenant_id=? AND sold_at >= ? AND sold_at < ? GROUP BY batch, account_id`, ...P))
    add(day(r.sold_at), "Receipt", `Fuel coupons sold — ${r.n} (${r.batch})${r.buyer ? ` to ${r.buyer}` : ""}`, [dr(via(r.method, r.account_id), r.v), cr("Fuel coupons (unused)", r.v)]);
  // coupons cancelled with no money back (lost book): the unused liability is released to income on the day of the void
  for (const r of all(`SELECT batch, MIN(voided_at) at, SUM(value) v, COUNT(*) n FROM fuel_coupons WHERE tenant_id=? AND status='void' AND void_refund=0 AND voided_at >= ? AND voided_at < ? GROUP BY batch`, ...P))
    add(day(r.at), "Journal", `Coupons cancelled, no refund — ${r.n} (${r.batch})`, [dr("Fuel coupons (unused)", r.v), cr("Other income", r.v)]);
  for (const r of all(`SELECT w.*, c.name FROM wallet_ledger w JOIN customers c ON c.id=w.customer_id WHERE w.tenant_id=? AND w.type='adjustment' AND w.created_at >= ? AND w.created_at < ?`, ...P))
    add(day(r.created_at), "Journal", `Wallet adjustment — ${r.name}${r.note ? ` (${r.note})` : ""}`, [dr("Expense: Wallet adjustments", r.amount), cr("Customer wallets", r.amount)]);
  for (const r of all(`SELECT w.*, c.name FROM wallet_ledger w JOIN customers c ON c.id=w.customer_id WHERE w.tenant_id=? AND w.type IN ('deposit','refund') AND w.created_at >= ? AND w.created_at < ?`, ...P))
    add(day(r.created_at), r.type === "deposit" ? "Receipt" : "Payment", `Wallet ${r.type} — ${r.name}${r.ref ? ` (${r.ref})` : ""}`,
      r.type === "deposit" ? [dr(via(r.method, r.account_id), r.amount), cr("Customer wallets", r.amount)] : [dr("Customer wallets", r.amount), cr(via(r.method, r.account_id), r.amount)]);
  // staff: advances out, repayments in, shortages charged
  for (const r of all(`SELECT l.*, u.name FROM staff_ledger l JOIN users u ON u.id=l.user_id WHERE l.tenant_id=? AND l.created_at >= ? AND l.created_at < ?`, ...P)) {
    const d = day(r.created_at);
    const way = via(r.method, r.account_id);
    if (r.type === "advance" && r.ref === OPEN_REF) { add(d, "Opening", `Opening advance — ${r.name}`, [dr("Staff advances", r.amount), cr(OPENING_EQ, r.amount)]); continue; }
    if (r.type === "advance") add(d, "Payment", `Advance — ${r.name}`, [dr("Staff advances", r.amount), cr(way, r.amount)]);
    if (r.type === "repayment") add(d, "Receipt", `Advance paid back — ${r.name}`, [dr(way, r.amount), cr("Staff advances", r.amount)]);
    // kept back out of a cash salary (the salary expense was booked in full from cash) — or set off with no cash at all
    // a manual set-off (no salary month) forgives the advance: it is a staff cost now, not a payable nobody ever clears
    if (r.type === "deduction") add(d, "Journal", `Advance ${r.month ? "recovered" : "adjusted (set off)"} — ${r.name}`, [dr(r.month ? CASH : "Expense: Staff advance adjusted", r.amount), cr("Staff advances", r.amount)]);
    if (r.type === "shortage") add(d, "Journal", `Cash shortage charged — ${r.name}`, [dr("Staff advances", r.amount), cr("Cash short / over", r.amount)]);
    if (r.type === "bonus" && !r.month) add(d, "Payment", `Bonus — ${r.name}`, [dr("Expense: Staff bonus", r.amount), cr(way, r.amount)]);
  }
  // shift cash short / over
  for (const r of all(`SELECT sh.id, sh.attendant, sh.closed_at, sh.variance FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.status='closed' AND sh.closed_at >= ? AND sh.closed_at < ? AND ABS(COALESCE(sh.variance,0)) >= 0.005`, ...P))
    add(day(r.closed_at), "Journal", `Shift #${r.id} cash ${r.variance < 0 ? "short" : "over"} — ${r.attendant}`, [dr("Cash short / over", -r.variance), cr(CASH, -r.variance)]);

  // cash the cashier received from the salesman differs from what the salesman counted at closing
  for (const r of all(`SELECT sh.id, sh.attendant, sh.handed_at, sh.handed_amount, sh.cash_actual FROM shifts sh JOIN stations s ON s.id=sh.station_id
      WHERE s.tenant_id=? AND sh.handed_at IS NOT NULL AND sh.handed_at >= ? AND sh.handed_at < ? AND ABS(sh.handed_amount - COALESCE(sh.cash_actual,0)) >= 0.005`, ...P)) {
    const diff = round2(r.cash_actual - r.handed_amount);
    add(day(r.handed_at), "Journal", `Shift #${r.id} handover ${diff > 0 ? "short" : "over"} — ${r.attendant}`, [dr("Cash short / over", diff), cr(CASH, diff)]);
  }
  // cash counter: money in / out that belongs to no party account
  for (const r of all(`SELECT * FROM cashier_vouchers WHERE tenant_id=? AND party_type='other' AND LOWER(method)='cash' AND voided=0 AND created_at >= ? AND created_at < ?`, ...P)) {
    const ok = otherKind(r.src); // CEO other income/expense
    let other: string, narr: string;
    if (ok === "inc" || ok === "exp") {
      other = showPrivate ? (ok === "inc" ? OTHER_INCOME_CEO : OTHER_EXPENSE_CEO) : (r.direction === "in" ? OWN_CAP : OWN_DRAW);
      narr = showPrivate ? `${r.category ?? (r.direction === "in" ? "Other money in" : "Other payment")} — ${r.party_name}` : (r.direction === "in" ? "Owner's capital introduced" : "Owner's drawings");
    } else if (ok === "pty") {
      other = toPartyAccount(r.src); // on the party's own account: paid to / received from them
      narr = `${r.direction === "in" ? "Received from" : "Paid to"} ${r.party_name}${r.category ? ` (${r.category})` : ""}`;
    } else {
      other = specialSide(r.src) ?? (r.direction === "in" ? "Other income" : "Other payments");
      narr = `${r.category ?? (r.direction === "in" ? "Other money in" : "Other payment")} — ${r.party_name}`;
    }
    add(day(r.created_at), r.direction === "in" ? "Receipt" : "Payment", narr,
      r.direction === "in" ? [dr(CASH, r.amount), cr(other, r.amount)] : [dr(other, r.amount), cr(CASH, r.amount)]);
  }

  // cash counts: the counted cash is the truth. The first count starts the cash book (its difference from what the books
  // made of the cash so far is the owner's — cash taken or put in without an entry); later counts' short / over is booked
  const first = get("SELECT MIN(created_at) v FROM cash_counts WHERE tenant_id=?", t)?.v as string | null;
  for (const r of all("SELECT * FROM cash_counts WHERE tenant_id=? AND created_at >= ? AND created_at < ? ORDER BY created_at, id", ...P)) {
    const isFirst = r.created_at === first;
    const diff = isFirst ? round2(r.amount - cashPosition(t, new Date(Date.parse(r.created_at) - 1).toISOString()).cash_in_hand) : round2(r.variance ?? 0);
    if (Math.abs(diff) < 0.005) continue;
    add(day(r.created_at), "Journal", isFirst ? `Cash counted — cash book starts (${diff > 0 ? "more" : "less"} than the books)` : `Cash counted — ${diff < 0 ? "short" : "over"}`,
      isFirst ? [dr(CASH, diff), cr("Owner's capital", diff)] : [dr(CASH, diff), cr("Cash short / over", diff)]);
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
  const j = journal(tid(req), r.from, r.to, req.user!.role === "admin");
  return { ...j, voucher_count: j.vouchers.length, vouchers: j.vouchers.slice(-200) };
}));

const download = (fn: (req: Request) => { name: string; type: string; body: string }) => (req: Request, res: Response, next: NextFunction) => {
  try { const f = fn(req); res.setHeader("Content-Type", f.type); res.setHeader("Content-Disposition", `attachment; filename="${f.name}"`); res.send(f.body); } catch (e) { next(e); }
};
ledger.get("/ledger.csv", requirePerm("reports.view"), download((req) => {
  const r = range(req);
  const j = journal(tid(req), r.from, r.to, req.user!.role === "admin");
  const esc = (v: unknown) => `"${String(v ?? "").replaceAll('"', '""')}"`;
  const rows = [["Date", "Voucher", "Type", "Account", "Debit", "Credit", "Narration"],
    ...j.vouchers.flatMap((v) => v.lines.map((l) => [v.date, v.no, v.type, l.account, l.debit || "", l.credit || "", v.narration]))];
  return { name: `general-ledger-${r.from}-to-${r.to}.csv`, type: "text/csv; charset=utf-8", body: "﻿" + rows.map((x) => x.map(esc).join(",")).join("\n") };
}));
ledger.get("/ledger/tally.xml", requirePerm("reports.view"), download((req) => {
  const r = range(req);
  const j = journal(tid(req), r.from, r.to, req.user!.role === "admin");
  const x = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" })[c]!);
  const vouchers = j.vouchers.map((v) => `<TALLYMESSAGE xmlns:UDF="TallyUDF"><VOUCHER VCHTYPE="Journal" ACTION="Create"><DATE>${v.date.replaceAll("-", "")}</DATE><VOUCHERTYPENAME>Journal</VOUCHERTYPENAME><VOUCHERNUMBER>${v.no}</VOUCHERNUMBER><NARRATION>${x(v.narration)}</NARRATION>${
    v.lines.map((l) => `<ALLLEDGERENTRIES.LIST><LEDGERNAME>${x(l.account)}</LEDGERNAME><ISDEEMEDPOSITIVE>${l.debit ? "Yes" : "No"}</ISDEEMEDPOSITIVE><AMOUNT>${(l.debit ? -l.debit : l.credit).toFixed(2)}</AMOUNT></ALLLEDGERENTRIES.LIST>`).join("")}</VOUCHER></TALLYMESSAGE>`).join("\n");
  return { name: `tally-vouchers-${r.from}-to-${r.to}.xml`, type: "application/xml; charset=utf-8",
    body: `<?xml version="1.0" encoding="UTF-8"?>\n<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC><REQUESTDATA>\n${vouchers}\n</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>` };
}));
