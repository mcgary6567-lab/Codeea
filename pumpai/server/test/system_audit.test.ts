/**
 * Whole-system tally on the demo pump's full history (about three months of shifts, khata, wholesale, tankers, banks,
 * cashier, staff, coupons, wallets): every party balance must equal its account in the general ledger, the journal
 * must balance, the stock books must equal the tanks, and every closed shift's meters must equal the litres it sold.
 */
import { test, before } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-audit-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let db: typeof import("../src/db.js");
let J: ReturnType<typeof import("../src/routes/ledger.js").journal>;
let t = 1;
const tb = (a: string) => J.trial_balance.find((x) => x.account === a)?.balance ?? 0;
const near = (a: number, b: number, msg: string, tol = 1) => assert.ok(Math.abs(a - b) <= tol, `${msg}: ${a.toFixed(2)} vs ${b.toFixed(2)} (diff ${(a - b).toFixed(2)})`);
const tomorrow = () => new Date(Date.now() + 29 * 3600_000).toISOString().slice(0, 10);

before(async () => {
  await import("../src/index.js"); // seeds the demo pump
  db = await import("../src/db.js");
  t = db.get("SELECT id FROM tenants ORDER BY id LIMIT 1")!.id as number;
  const { journal } = await import("../src/routes/ledger.js");
  J = journal(t, "2000-01-01", tomorrow());
});

test("the journal balances, voucher by voucher", () => {
  near(J.totals.debit, J.totals.credit, "total debits = credits", 0.05);
  for (const v of J.vouchers) {
    const d = v.lines.reduce((a, l) => a + l.debit, 0), c = v.lines.reduce((a, l) => a + l.credit, 0);
    assert.ok(Math.abs(d - c) < 0.05, `voucher ${v.no} ${v.narration}: ${d} vs ${c}`);
  }
  assert.ok(!J.trial_balance.some((x) => x.account === "Suspense"), "nothing lands in Suspense");
});

test("wholesale clients: sum of dues = Wholesale receivable in the ledger", async () => {
  const { clientDue } = await import("../src/routes/wholesale.js");
  const cs = db.all("SELECT id, opening_balance FROM wholesale_clients WHERE tenant_id=?", t);
  near(cs.reduce((a, c) => a + clientDue(c.id) - c.opening_balance, 0), tb("Wholesale receivable"), "wholesale receivable");
});

test("suppliers: each supplier's balance = its payable in the ledger", async () => {
  const { supplierOwed } = await import("../src/routes/suppliers.js");
  for (const s of db.all("SELECT id, name, opening_balance FROM suppliers WHERE tenant_id=?", t))
    near(supplierOwed(s.id) - s.opening_balance, -tb(`Payable — ${s.name}`), `supplier ${s.name}`);
});

test("khata: each customer's balance = its own ledger; the total = Khata receivable", () => {
  let total = 0;
  for (const c of db.all("SELECT id, name, balance FROM customers WHERE tenant_id=?", t)) {
    const k = db.get("SELECT COALESCE(SUM(CASE WHEN type='debit' THEN amount ELSE -amount END),0) v FROM khata_ledger WHERE customer_id=?", c.id)!.v as number;
    near(c.balance, k, `customer ${c.name} balance vs khata entries`);
    total += k;
  }
  near(total, tb("Khata receivable"), "khata receivable");
});

test("banks: the bank accounts = Bank in the ledger", async () => {
  const { bankAccounts } = await import("../src/routes/banks.js");
  const accs = db.all("SELECT opening_balance FROM bank_accounts WHERE tenant_id=?", t);
  near(bankAccounts(t).accounts.reduce((a: number, x: any) => a + x.balance - x.opening_balance, 0), tb("Bank"), "bank");
  void accs;
});

test("cash: office cash + cash still with the salesmen = Cash in hand in the ledger", async () => {
  const { cashPosition } = await import("../src/routes/backoffice.js");
  const { cashWithSalesmen } = await import("../src/routes/cashier.js");
  const firstCount = db.get("SELECT MIN(created_at) v FROM cash_counts WHERE tenant_id=?", t)!.v;
  // the cash book starts from the first count; the ledger from the first entry — compare the movement since that count
  if (firstCount) {
    const { journal } = await import("../src/routes/ledger.js");
    const day = new Date(Date.parse(firstCount) + 5 * 3600_000).toISOString().slice(0, 10);
    const before = journal(t, "2000-01-01", new Date(Date.parse(day) - 86400_000).toISOString().slice(0, 10)).trial_balance.find((x) => x.account === "Cash in hand")?.balance ?? 0;
    console.log("cash", { office: cashPosition(t).cash_in_hand, salesmen: cashWithSalesmen(t).total, ledger: tb("Cash in hand"), ledger_before_first_count: before, firstCount });
  }
  near(cashPosition(t).cash_in_hand + cashWithSalesmen(t).total, tb("Cash in hand"), "cash");
});

test("coupons, wallets and staff advances = their ledger accounts", async () => {
  const unused = db.get("SELECT COALESCE(SUM(value),0) v FROM fuel_coupons WHERE tenant_id=? AND status='active' AND sold_at IS NOT NULL", t)!.v as number;
  near(unused, -tb("Fuel coupons (unused)"), "coupons unused");
  const wallets = db.get("SELECT COALESCE(SUM(CASE WHEN type IN ('deposit','bonus') THEN amount ELSE -amount END),0) v FROM wallet_ledger WHERE tenant_id=?", t)!.v as number;
  near(wallets, -tb("Customer wallets"), "wallets");
  const { staffBalance } = await import("../src/routes/staff.js");
  const staff = db.all("SELECT DISTINCT user_id FROM staff_ledger WHERE tenant_id=?", t).reduce((a, u) => a + staffBalance(u.user_id), 0);
  near(staff, tb("Staff advances"), "staff advances");
});

test("stock: register and report closing = what the tanks hold", async () => {
  const { stockRegister } = await import("../src/routes/register.js");
  const today = new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10);
  const from = new Date(Date.now() - 20 * 86400_000 + 5 * 3600_000).toISOString().slice(0, 10);
  for (const s of db.all("SELECT id, name FROM stations WHERE tenant_id=?", t)) {
    const r: any = stockRegister(t, s.id, from, today);
    for (const p of r.products) {
      const last = p.days[p.days.length - 1];
      const tanks = db.get("SELECT COALESCE(SUM(current_l),0) v FROM tanks WHERE station_id=? AND product=?", s.id, p.product)!.v as number;
      near(last.closing, tanks, `${s.name} ${p.product} register closing vs tanks`);
      for (const d of p.days) near(d.opening + d.receipts - d.sales - d.wholesale + d.gain_loss, d.closing, `${s.name} ${p.product} ${d.date} day adds up`);
    }
  }
});

test("meters: every closed shift sold what its meters ran (less test litres)", () => {
  const shifts = db.all(`SELECT sh.id FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.status='closed'`, t);
  let bad = 0; const eg: string[] = [];
  for (const sh of shifts) {
    const m = db.all(`SELECT n.tank_id, t.product, SUM(mr.closing - mr.opening - COALESCE(mr.test_l,0)) l FROM meter_readings mr JOIN nozzles n ON n.id=mr.nozzle_id JOIN tanks t ON t.id=n.tank_id
      WHERE mr.shift_id=? AND mr.closing IS NOT NULL GROUP BY t.product`, sh.id);
    for (const r of m) {
      const sold = db.get("SELECT COALESCE(SUM(litres),0) l FROM sales WHERE shift_id=? AND product=?", sh.id, r.product)!.l as number;
      if (Math.abs(sold - r.l) > 0.6) { bad++; if (eg.length < 5) eg.push(`shift ${sh.id} ${r.product}: meters ${r.l.toFixed(2)} L, sold ${sold.toFixed(2)} L`); }
    }
  }
  assert.equal(bad, 0, `${bad} shift/fuel pairs off:\n${eg.join("\n")}`);
});

test("P&L and reports agree with the ledger for this month", async () => {
  const { profitAndLoss } = await import("../src/routes/analysis.js");
  const month = new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 7);
  const { journal } = await import("../src/routes/ledger.js");
  const M = journal(t, `${month}-01`, tomorrow());
  const mt = (a: string) => M.trial_balance.find((x) => x.account === a)?.balance ?? 0;
  const pl = profitAndLoss(t, month);
  // pump and shop sales as billed (the ledger splits the sales tax out of both). The ledger books fuel revenue at the
  // gross and the khata discount as a contra ("Discount given — khata"); the P&L shows the net, so net + discount = gross.
  near(pl.income.fuel_retail + pl.income.shop + mt("Discount given — khata"), -(mt("Fuel sales") + mt("Shop sales") + mt("Output sales tax")), "pump + shop sales (with sales tax) vs ledger", 5);
  near(pl.income.fuel_wholesale, -mt("Wholesale sales"), "wholesale sales vs ledger", 5);
  const ledgerExp = M.trial_balance.filter((x) => x.account.startsWith("Expense: ")).reduce((a, x) => a + x.balance, 0);
  near(pl.expenses.total, ledgerExp, "expenses vs ledger", 5);
});

test("statements end on the account balance (wholesale, khata)", async () => {
  const { statement, clientDue } = await import("../src/routes/wholesale.js");
  for (const c of db.all("SELECT id, name FROM wholesale_clients WHERE tenant_id=?", t)) near(statement(t, c.id).closing_balance, clientDue(c.id), `wholesale statement ${c.name}`);
  const { khataStatement } = await import("../src/routes/crm.js");
  for (const c of db.all("SELECT id, name, balance FROM customers WHERE tenant_id=?", t)) near((khataStatement(t, c.id) as any).closing_balance, c.balance, `khata statement ${c.name}`);
});

test("cashier's day book closes on the cash book; owner sees the same cash", async () => {
  const { cashierDayBook } = await import("../src/routes/cashier.js");
  const { cashPosition } = await import("../src/routes/backoffice.js");
  const d: any = cashierDayBook(t, new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10));
  if (d.cash.closing != null) near(d.cash.closing, cashPosition(t).cash_in_hand, "day book closing");
  if (d.cash.opening != null && d.cash.closing != null)
    near(d.cash.opening + d.totals.in_cash - d.totals.out_cash - d.totals.deposited + d.totals.withdrawn + d.totals.counted, d.cash.closing, "day book adds up");
});

test("every sale: litres × rate = amount; no tank below zero; each meter = its last closing reading", () => {
  // amount is the net billed: litres × rate − any lump-sum khata discount
  const bad = db.get("SELECT COUNT(*) n FROM sales s JOIN stations st ON st.id=s.station_id WHERE st.tenant_id=? AND ABS(s.litres * s.rate - COALESCE(s.discount,0) - s.amount) > 1", t)!.n;
  assert.equal(bad, 0, "sales where litres × rate − discount ≠ amount");
  assert.equal(db.get("SELECT COUNT(*) n FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? AND t.current_l < 0", t)!.n, 0, "tanks below zero");
  const off = db.all(`SELECT n.id, n.label, n.totalizer, (SELECT mr.closing FROM meter_readings mr JOIN shifts sh ON sh.id=mr.shift_id WHERE mr.nozzle_id=n.id AND mr.closing IS NOT NULL ORDER BY sh.closed_at DESC LIMIT 1) last
    FROM nozzles n JOIN stations s ON s.id=n.station_id WHERE s.tenant_id=?`, t).filter((n) => n.last != null && Math.abs(n.last - n.totalizer) > 0.01);
  assert.equal(off.length, 0, `nozzles whose meter is not the last closing reading: ${JSON.stringify(off.slice(0, 3))}`);
});

test("tanker trips add up to their drops; the balance sheet's parties = the books", async () => {
  for (const tr of db.all("SELECT id, litres, amount FROM wholesale_trips WHERE tenant_id=?", t)) {
    const d = db.get("SELECT COALESCE(SUM(litres),0) l, COALESCE(SUM(amount),0) a FROM wholesale_txns WHERE trip_id=?", tr.id)!;
    near(d.l, tr.litres, `trip ${tr.id} litres`); near(d.a, tr.amount, `trip ${tr.id} amount`);
  }
  const { balanceSheet } = await import("../src/routes/analysis.js");
  const { clientDue } = await import("../src/routes/wholesale.js");
  const { supplierOwed } = await import("../src/routes/suppliers.js");
  const b: any = balanceSheet(t);
  near(b.assets.wholesale_receivable, db.all("SELECT id FROM wholesale_clients WHERE tenant_id=?", t).reduce((a, c) => a + Math.max(0, clientDue(c.id)), 0), "balance sheet wholesale", 2);
  near(b.liabilities.suppliers, db.all("SELECT id FROM suppliers WHERE tenant_id=?", t).reduce((a, s) => a + Math.max(0, supplierOwed(s.id)), 0), "balance sheet suppliers", 2);
  near(b.assets.khata_receivable, db.get("SELECT COALESCE(SUM(balance),0) v FROM customers WHERE tenant_id=? AND balance > 0", t)!.v as number, "balance sheet khata", 2);
  near(b.total_assets - b.total_liabilities, b.net_worth, "net worth", 1);
});

test("the shared tally (as run after every day-long test) passes on the demo too", async () => {
  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("demo");
});

test("shift reports: meter lines less test litres = litres sold, for every closed shift", async () => {
  const { shiftMetersTally } = await import("./helpers/shiftMeters.js");
  await shiftMetersTally("system_audit");
});

test("the owner's money levers tie to the ledger: khata discount, bank card commission (MDR), shop/unit rent", async () => {
  // 1) lump-sum khata discounts: every rupee of discount is booked to "Discount given — khata"
  const discGiven = db.get("SELECT COALESCE(SUM(discount),0) v FROM sales s JOIN stations st ON st.id=s.station_id WHERE st.tenant_id=?", t)!.v as number;
  assert.ok(discGiven > 0, "demo has at least one lump-sum khata discount to check");
  near(discGiven, tb("Discount given — khata"), "khata discount vs ledger");

  // 2) bank card/POS commission (MDR): the ledger expense equals the fee derived from card sales, to the rupee
  const { cardFees } = await import("../src/routes/banks.js");
  const feeTotal = cardFees(t).reduce((a: number, f: any) => a + f.fee, 0);
  assert.ok(feeTotal > 0, "demo has a card fee % set so commission is exercised");
  near(feeTotal, tb("Expense: Card charges"), "card commission (MDR) vs ledger");

  // 3) shop/unit rent: every rent payment is income in the ledger ("Other income" covers it); the helper agrees with the rows
  const { rentalIncome } = await import("../src/routes/property.js");
  const rentRows = db.get("SELECT COALESCE(SUM(amount),0) v, COUNT(*) n FROM rental_payments WHERE tenant_id=?", t)!;
  assert.ok((rentRows.n as number) > 0, "demo has rent payments to check");
  near(rentalIncome(t, "2000-01-01T00:00:00.000Z", tomorrow() + "T23:59:59.999Z"), rentRows.v as number, "rental income helper vs rows");
  // income accounts carry credit (negative) balances in the trial balance, so flip the sign
  assert.ok(-tb("Other income") >= (rentRows.v as number) - 1, `ledger "Other income" (${-tb("Other income")}) covers the rent (${rentRows.v})`);
});

test("P&L surfaces commission, rent and discount so the owner sees them", async () => {
  const { profitAndLoss } = await import("../src/routes/analysis.js");
  const month = new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 7);
  const pl: any = profitAndLoss(t, month);
  // card commission shows as its own expense line
  assert.ok(pl.expenses.by_category.some((e: any) => /card/i.test(e.category) && e.amount > 0), "P&L shows a card-charges expense line");
  // rent (and carriage) is added back as other income
  const { rentalIncome } = await import("../src/routes/property.js");
  const rentThisMonth = rentalIncome(t, `${month}-01T00:00:00.000Z`, tomorrow() + "T23:59:59.999Z");
  if (rentThisMonth > 0) assert.ok(pl.other_income >= rentThisMonth - 1, `P&L other_income (${pl.other_income}) includes this month's rent (${rentThisMonth})`);
});
