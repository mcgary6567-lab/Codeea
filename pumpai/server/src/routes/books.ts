/**
 * Live whole-system reconciliation ("Hisaab check") for the owner. Runs the SAME ties the audit test
 * enforces — the journal balances, nothing sits in Suspense, and every party / cash / bank / coupon /
 * wallet / staff balance equals its ledger account — but against live data and WITHOUT throwing, so the
 * CEO can press one button (especially after fixing a wrong entry) and see "all tallied, 0 rupee
 * difference" or exactly where a difference is. Mirrors test/helpers/tally.ts.
 */
import { Router } from "express";
import { all, get, run, tx, now } from "../db.js";
import { h, tid, requirePerm } from "../auth.js";
import { round2, AppError, audit } from "../services.js";
import { journal } from "./ledger.js";
import { clientDue } from "./wholesale.js";
import { thekedarDue } from "./carriage.js";
import { bypassOwed, bypassStockValue } from "./bypass.js";
import { supplierOwed } from "./suppliers.js";
import { bankAccounts } from "./banks.js";
import { cashPosition } from "./backoffice.js";
import { cashWithSalesmen } from "./cashier.js";
import { staffBalance } from "./staff.js";

export const books = Router();

type Check = { label: string; book: number; ledger: number; diff: number; ok: boolean; note?: string; advisory?: boolean };

export function reconcileBooks(t: number) {
  const J = journal(t, "2000-01-01", new Date(Date.now() + 29 * 3600_000).toISOString().slice(0, 10));
  const tb = (a: string) => J.trial_balance.find((x) => x.account === a)?.balance ?? 0;
  const rows: Check[] = [];
  const TOL = 1; // one rupee of rounding across the whole business
  const push = (label: string, book: number, ledger: number, note?: string) =>
    rows.push({ label, book: round2(book), ledger: round2(ledger), diff: round2(book - ledger), ok: Math.abs(book - ledger) <= TOL, note });
  const safe = (label: string, fn: () => void) => { try { fn(); } catch (e) { rows.push({ label, book: 0, ledger: 0, diff: 0, ok: false, note: `check failed: ${(e as Error).message}` }); } };

  // 1. the journal itself balances, and nothing fell into Suspense
  rows.push({ label: "General ledger balanced (debit = credit)", book: round2(J.totals.debit), ledger: round2(J.totals.credit), diff: round2(J.totals.debit - J.totals.credit), ok: Math.abs(J.totals.debit - J.totals.credit) <= 0.05 });
  const suspense = J.trial_balance.find((x) => x.account === "Suspense");
  rows.push({ label: "Nothing stuck in Suspense", book: round2(suspense?.balance ?? 0), ledger: 0, diff: round2(suspense?.balance ?? 0), ok: !suspense });

  // 2. every balance ties to its ledger account
  safe("Cash in hand (office + with salesmen)", () => push("Cash in hand (office + with salesmen)", cashPosition(t).cash_in_hand + cashWithSalesmen(t).total, tb("Cash in hand")));
  safe("Bank accounts", () => push("Bank accounts", bankAccounts(t).accounts.reduce((a: number, x: any) => a + x.balance - x.opening_balance, 0), tb("Bank")));
  safe("Khata receivable (customers)", () => {
    let khata = 0, allMatch = true;
    for (const c of all("SELECT id, name, balance FROM customers WHERE tenant_id=?", t)) {
      const k = get("SELECT COALESCE(SUM(CASE WHEN type='debit' THEN amount ELSE -amount END),0) v FROM khata_ledger WHERE customer_id=?", c.id)!.v as number;
      if (Math.abs(c.balance - k) > TOL) allMatch = false;
      khata += k;
    }
    push("Khata receivable (customers)", khata, tb("Khata receivable"), allMatch ? undefined : "a customer balance differs from its own khata ledger");
  });
  safe("Wholesale receivable", () => push("Wholesale receivable", all("SELECT id, opening_balance FROM wholesale_clients WHERE tenant_id=?", t).reduce((a, c) => a + clientDue(c.id) - c.opening_balance, 0), tb("Wholesale receivable")));
  safe("Carriage receivable (thekedars)", () => push("Carriage receivable (thekedars)", all("SELECT id, opening_balance FROM thekedars WHERE tenant_id=?", t).reduce((a, k) => a + thekedarDue(k.id) - k.opening_balance, 0), tb("Carriage receivable")));
  safe("Suppliers payable", () => push("Suppliers payable", all("SELECT id, name, opening_balance FROM suppliers WHERE tenant_id=?", t).reduce((a, s) => a + supplierOwed(s.id) - s.opening_balance, 0), all("SELECT name FROM suppliers WHERE tenant_id=?", t).reduce((a, s) => a - tb(`Payable — ${s.name}`), 0)));
  safe("Shop stock (items × avg cost)", () => {
    const book = get("SELECT COALESCE(SUM(stock * cost),0) v FROM shop_items WHERE tenant_id=? AND active=1", t)!.v as number;
    const ledger = tb("Shop stock");
    const tol = Math.max(TOL, Math.abs(book) * 0.02); // avg cost moves with every purchase, so 2% is the honest tolerance here
    // advisory: stock that was on the shelves before stock-in carried a money leg was never booked, so an old pump sees a gap here until it is counted in
    rows.push({ label: "Shop stock (items × avg cost) — advisory", book: round2(book), ledger: round2(ledger), diff: round2(book - ledger), ok: Math.abs(book - ledger) <= tol, advisory: true,
      note: Math.abs(book - ledger) > tol ? "purana shop stock bina money entry ke tha, ya count ka farq — neeche 'Purana shop stock book karein' ek click me book kar deta hai" : undefined });
  });
  safe("Withholding tax payable (undeposited)", () => push("Withholding tax payable", get("SELECT COALESCE(SUM(amount),0) v FROM tax_withholdings WHERE tenant_id=? AND cpr_no IS NULL", t)!.v as number, -tb("Withholding tax payable")));
  safe("Fuel coupons (unused)", () => push("Fuel coupons (unused)", get("SELECT COALESCE(SUM(value),0) v FROM fuel_coupons WHERE tenant_id=? AND status='active' AND sold_at IS NOT NULL", t)!.v as number, -tb("Fuel coupons (unused)")));
  safe("Customer wallets", () => push("Customer wallets", get("SELECT COALESCE(SUM(CASE WHEN type IN ('deposit','bonus','adjustment') THEN amount ELSE -amount END),0) v FROM wallet_ledger WHERE tenant_id=?", t)!.v as number, -tb("Customer wallets")));
  safe("Staff advances", () => push("Staff advances", all("SELECT DISTINCT user_id FROM staff_ledger WHERE tenant_id=?", t).reduce((a, u) => a + staffBalance(u.user_id), 0), tb("Staff advances")));
  safe("Bypass stock", () => push("Bypass stock", bypassStockValue(t), tb("Bypass stock")));
  safe("Bypass suppliers payable", () => push("Bypass suppliers payable", all("SELECT DISTINCT supplier_id FROM bypass_purchases WHERE tenant_id=?", t).reduce((a, s) => a + bypassOwed(s.supplier_id), 0), -tb("Bypass suppliers payable")));

  const failed = rows.filter((r) => !r.ok && !r.advisory); // advisory rows inform, they never fail the check
  const maxDiff = rows.filter((r) => !r.advisory).reduce((m, r) => Math.max(m, Math.abs(r.diff)), 0);
  return { ok: failed.length === 0, checked_at: new Date().toISOString(), max_diff: round2(maxDiff), checks: rows, failed: failed.map((r) => r.label) };
}

books.get("/books/check", requirePerm("audit.view"), h((req) => reconcileBooks(tid(req))));

/** Shelf value of the shop (Σ stock × avg cost) vs what the ledger carries on "Shop stock" — the go-live gap of an old pump. */
export function shopStockGap(t: number) {
  const J = journal(t, "2000-01-01", new Date(Date.now() + 29 * 3600_000).toISOString().slice(0, 10));
  const ledger = J.trial_balance.find((x) => x.account === "Shop stock")?.balance ?? 0;
  const book = get("SELECT COALESCE(SUM(stock * cost),0) v FROM shop_items WHERE tenant_id=? AND active=1", t)!.v as number;
  return { book: round2(book), ledger: round2(ledger), gap: round2(book - ledger) };
}

/**
 * One click: book the whole shop-stock gap against "Opening balances (go-live)" — nothing in P&L, no cash moves.
 * Itemised: each active item gets a `book` move (qty 0, amount = its own unbooked value, computed from its moves at cost);
 * the rounding remainder lands on the biggest item so the total equals the ledger gap to the rupee. Pressing it again books nothing.
 */
books.post("/books/shop-stock/book", requirePerm("opening.manage"), h((req) => {
  const t = tid(req);
  const before = shopStockGap(t);
  if (Math.abs(before.gap) <= 1) return { booked: 0, items: [], before, after: before };
  const items = all("SELECT id, name, stock, cost FROM shop_items WHERE tenant_id=? AND active=1", t).map((i) => {
    // what this item's own moves have already put on the ledger: goods in at cost (any money leg), earlier bookings, minus what sold / was lost, plus count gains
    const m = get(`SELECT
        COALESCE(SUM(CASE WHEN type='purchase' THEN COALESCE(amount, qty*COALESCE(cost,0)) ELSE 0 END),0) p,
        COALESCE(SUM(CASE WHEN type='book' THEN COALESCE(amount,0) ELSE 0 END),0) b,
        COALESCE(SUM(CASE WHEN type IN ('sale','undo','adjust') THEN qty*COALESCE(cost,0) ELSE 0 END),0) s
      FROM shop_moves WHERE item_id=?`, i.id)!;
    const booked = (m.p as number) + (m.b as number) + (m.s as number);
    return { id: i.id as number, name: i.name as string, shelf: round2(i.stock * i.cost), gap: round2(i.stock * i.cost - booked) };
  });
  // scale the itemised gaps onto the real ledger gap (they agree except for sale-edit / rounding noise), remainder on the biggest line
  let rows = items.filter((x) => Math.abs(x.gap) > 0.5);
  const sum = rows.reduce((a, x) => a + x.gap, 0);
  if (!rows.length || Math.abs(sum) < 0.5) rows = [{ ...(items.sort((a, b) => b.shelf - a.shelf)[0] ?? { id: 0, name: "Shop", shelf: 0, gap: 0 }), gap: before.gap }];
  else if (Math.abs(sum - before.gap) > 0.5) { const k = before.gap / sum; rows = rows.map((x) => ({ ...x, gap: round2(x.gap * k) })); }
  const drift = round2(before.gap - rows.reduce((a, x) => a + x.gap, 0));
  if (drift) { const big = rows.reduce((a, x) => (Math.abs(x.gap) > Math.abs(a.gap) ? x : a)); big.gap = round2(big.gap + drift); }
  if (!rows[0].id) throw new AppError(400, "Shop me koi item nahi — pehle items banayein");
  const note = String(req.body?.note ?? "").trim() || "Purana shop stock book kiya (one click)";
  const ts = now();
  tx(() => {
    for (const r of rows) if (r.gap) run("INSERT INTO shop_moves (item_id,type,qty,cost,note,created_by,created_at,paid_by,amount) VALUES (?,?,?,?,?,?,?,?,?)",
      r.id, "book", 0, null, note, req.user!.name, ts, "none", r.gap);
  });
  const after = shopStockGap(t);
  audit(t, req.user!, "shop_stock_booked", `shopstock:${ts}`, { gap: before.gap, items: rows.filter((r) => r.gap).map((r) => ({ id: r.id, name: r.name, amount: r.gap })), after_gap: after.gap });
  return { booked: round2(before.gap), items: rows.filter((r) => r.gap).map((r) => ({ id: r.id, name: r.name, amount: r.gap })), before, after };
}));
