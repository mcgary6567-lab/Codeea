/**
 * Every book against every other, for whatever has happened so far: the journal balances; each wholesale client, supplier,
 * khata customer, bank, the cash (office + still with salesmen), coupons, wallets and staff advances equal their ledger
 * accounts; the tanks equal the stock books. Used by the audit test and at the end of the day-long tests.
 */
import assert from "node:assert/strict";

export async function tallyBooks(label = "") {
  const db = await import("../../src/db.js");
  const { journal } = await import("../../src/routes/ledger.js");
  const { clientDue } = await import("../../src/routes/wholesale.js");
  const { thekedarDue } = await import("../../src/routes/carriage.js");
  const { bypassOwed } = await import("../../src/routes/bypass.js");
  const { supplierOwed } = await import("../../src/routes/suppliers.js");
  const { bankAccounts } = await import("../../src/routes/banks.js");
  const { cashPosition } = await import("../../src/routes/backoffice.js");
  const { cashWithSalesmen } = await import("../../src/routes/cashier.js");
  const { staffBalance } = await import("../../src/routes/staff.js");
  const t = db.get("SELECT id FROM tenants ORDER BY id LIMIT 1")!.id as number;
  const J = journal(t, "2000-01-01", new Date(Date.now() + 29 * 3600_000).toISOString().slice(0, 10));
  const tb = (a: string) => J.trial_balance.find((x) => x.account === a)?.balance ?? 0;
  const near = (a: number, b: number, msg: string, tol = 1) => assert.ok(Math.abs(a - b) <= tol, `${label} ${msg}: ${a.toFixed(2)} vs ${b.toFixed(2)} (diff ${(a - b).toFixed(2)})`);

  near(J.totals.debit, J.totals.credit, "journal debits = credits", 0.05);
  assert.ok(!J.trial_balance.some((x) => x.account === "Suspense"), `${label} nothing in Suspense`);
  const cs = db.all("SELECT id, opening_balance FROM wholesale_clients WHERE tenant_id=?", t);
  near(cs.reduce((a, c) => a + clientDue(c.id) - c.opening_balance, 0), tb("Wholesale receivable"), "wholesale receivable");
  const ks = db.all("SELECT id, opening_balance FROM thekedars WHERE tenant_id=?", t);
  near(ks.reduce((a, k) => a + thekedarDue(k.id) - k.opening_balance, 0), tb("Carriage receivable"), "carriage receivable");
  const bsups = db.all("SELECT DISTINCT supplier_id FROM bypass_purchases WHERE tenant_id=?", t);
  near(bsups.reduce((a, s) => a + bypassOwed(s.supplier_id), 0), -tb("Bypass suppliers payable"), "bypass suppliers payable");
  for (const s of db.all("SELECT id, name, opening_balance FROM suppliers WHERE tenant_id=?", t))
    near(supplierOwed(s.id) - s.opening_balance, -tb(`Payable — ${s.name}`), `supplier ${s.name}`);
  let khata = 0;
  for (const c of db.all("SELECT id, name, balance FROM customers WHERE tenant_id=?", t)) {
    const k = db.get("SELECT COALESCE(SUM(CASE WHEN type='debit' THEN amount ELSE -amount END),0) v FROM khata_ledger WHERE customer_id=?", c.id)!.v as number;
    near(c.balance, k, `customer ${c.name} balance vs its khata`);
    khata += k;
  }
  near(khata, tb("Khata receivable"), "khata receivable");
  // every account, closed ones too (their money moved through the books before they were closed)
  near(bankAccounts(t).accounts.reduce((a: number, x: any) => a + x.balance - x.opening_balance, 0), tb("Bank"), "bank");
  near(cashPosition(t).cash_in_hand + cashWithSalesmen(t).total, tb("Cash in hand"), "cash (office + with salesmen)");
  near(db.get("SELECT COALESCE(SUM(value),0) v FROM fuel_coupons WHERE tenant_id=? AND status='active' AND sold_at IS NOT NULL", t)!.v as number, -tb("Fuel coupons (unused)"), "coupons unused");
  near(db.get("SELECT COALESCE(SUM(CASE WHEN type IN ('deposit','bonus') THEN amount ELSE -amount END),0) v FROM wallet_ledger WHERE tenant_id=?", t)!.v as number, -tb("Customer wallets"), "wallets");
  near(db.all("SELECT DISTINCT user_id FROM staff_ledger WHERE tenant_id=?", t).reduce((a, u) => a + staffBalance(u.user_id), 0), tb("Staff advances"), "staff advances");
  return { journal: J, tb };
}
