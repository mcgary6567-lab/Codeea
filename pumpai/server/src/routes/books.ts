/**
 * Live whole-system reconciliation ("Hisaab check") for the owner. Runs the SAME ties the audit test
 * enforces — the journal balances, nothing sits in Suspense, and every party / cash / bank / coupon /
 * wallet / staff balance equals its ledger account — but against live data and WITHOUT throwing, so the
 * CEO can press one button (especially after fixing a wrong entry) and see "all tallied, 0 rupee
 * difference" or exactly where a difference is. Mirrors test/helpers/tally.ts.
 */
import { Router } from "express";
import { all, get } from "../db.js";
import { h, tid, requirePerm } from "../auth.js";
import { round2 } from "../services.js";
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

type Check = { label: string; book: number; ledger: number; diff: number; ok: boolean; note?: string };

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
  safe("Fuel coupons (unused)", () => push("Fuel coupons (unused)", get("SELECT COALESCE(SUM(value),0) v FROM fuel_coupons WHERE tenant_id=? AND status='active' AND sold_at IS NOT NULL", t)!.v as number, -tb("Fuel coupons (unused)")));
  safe("Customer wallets", () => push("Customer wallets", get("SELECT COALESCE(SUM(CASE WHEN type IN ('deposit','bonus') THEN amount ELSE -amount END),0) v FROM wallet_ledger WHERE tenant_id=?", t)!.v as number, -tb("Customer wallets")));
  safe("Staff advances", () => push("Staff advances", all("SELECT DISTINCT user_id FROM staff_ledger WHERE tenant_id=?", t).reduce((a, u) => a + staffBalance(u.user_id), 0), tb("Staff advances")));
  safe("Bypass stock", () => push("Bypass stock", bypassStockValue(t), tb("Bypass stock")));
  safe("Bypass suppliers payable", () => push("Bypass suppliers payable", all("SELECT DISTINCT supplier_id FROM bypass_purchases WHERE tenant_id=?", t).reduce((a, s) => a + bypassOwed(s.supplier_id), 0), -tb("Bypass suppliers payable")));

  const failed = rows.filter((r) => !r.ok);
  const maxDiff = rows.reduce((m, r) => Math.max(m, Math.abs(r.diff)), 0);
  return { ok: failed.length === 0, checked_at: new Date().toISOString(), max_diff: round2(maxDiff), checks: rows, failed: failed.map((r) => r.label) };
}

books.get("/books/check", requirePerm("audit.view"), h((req) => reconcileBooks(tid(req))));
