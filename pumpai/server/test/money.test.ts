/** Tanker shortage claims, depot comparison, sales tax and withholding, bank statement reconciliation, general ledger export. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-money-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server;
let base = "";
let db: typeof import("../src/db.js");
const tokens: Record<string, string> = {};
async function call(who: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tokens[who] ? { authorization: `Bearer ${tokens[who]}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  const text = await res.text();
  let data: any = text;
  try { data = JSON.parse(text); } catch { /* csv / xml / html */ }
  return { status: res.status, data };
}
const ok = (r: { status: number; data: any }, msg: string) => { assert.equal(r.status, 200, `${msg}: ${JSON.stringify(r.data).slice(0, 300)}`); return r.data; };

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("a short tanker opens a claim; claim goes to the depot on WhatsApp; credit note lowers what we owe", async () => {
  const sup = db.get("SELECT * FROM suppliers WHERE name='Shell Machike Depot'");
  const tank = db.get("SELECT * FROM tanks WHERE station_id=1 AND product='HSD'");
  db.run("UPDATE tanks SET current_l=? WHERE id=?", 5000, tank.id);
  const d = ok(await call("manager", "POST", "/api/stock/delivery", { tank_id: tank.id, invoice_l: 10000, received_l: 9900, tanker_no: "TLR-TEST", supplier_id: sup.id, purchase_rate: 260, freight: 18000 }), "delivery");
  assert.ok(d.claim_id, "claim opened");
  const list = ok(await call("manager", "GET", "/api/claims"), "claims");
  const c = list.claims.find((x: any) => x.id === d.claim_id);
  assert.equal(c.litres, 80, "100 L short − 0.2% allowed (20 L)");
  assert.equal(c.amount, 80 * 260);
  assert.ok(list.summary.not_claimed >= c.amount);
  const sent = ok(await call("manager", "POST", `/api/claims/${c.id}/claim`, { claim_ref: "CLM-1" }), "claim");
  assert.equal(sent.sent, true);
  assert.ok(db.get("SELECT id FROM outbox WHERE kind='shortage_claim' AND to_phone=? AND text LIKE '%TLR-TEST%'", sup.phone));
  const { supplierOwed } = await import("../src/routes/suppliers.js");
  const before = supplierOwed(sup.id);
  ok(await call("manager", "POST", `/api/claims/${c.id}/settle`, { action: "recovered", amount: 10000 }), "part");
  assert.equal(db.get("SELECT status FROM shortage_claims WHERE id=?", c.id).status, "partly");
  ok(await call("manager", "POST", `/api/claims/${c.id}/settle`, { action: "recovered" }), "rest");
  assert.equal(db.get("SELECT status FROM shortage_claims WHERE id=?", c.id).status, "recovered");
  assert.equal(Math.round(before - supplierOwed(sup.id)), 80 * 260, "credit notes reduce the payable");
  assert.equal((await call("salesman", "GET", "/api/claims")).status, 403);
  // a delivery within the allowed loss makes no claim
  const d2 = ok(await call("manager", "POST", "/api/stock/delivery", { tank_id: tank.id, invoice_l: 5000, received_l: 4995, supplier_id: sup.id, purchase_rate: 260 }), "delivery 2");
  assert.equal(d2.claim_id, null);
});

test("depot comparison: landed cost per litre includes freight and shortage", async () => {
  const r = ok(await call("manager", "GET", "/api/suppliers-compare?days=120"), "compare");
  const hsd = r.rows.filter((x: any) => x.product === "HSD");
  assert.equal(hsd.length, 2);
  for (const x of hsd) assert.ok(x.landed_per_l > x.avg_rate + x.freight_per_l - 0.01, "landed ≥ rate + freight");
  assert.ok(r.best.HSD.supplier);
  assert.ok(r.best.HSD.saving_per_l >= 0);
});

test("sales tax on shop receipts, withholding on supplier payment, monthly tax report and CSV", async () => {
  ok(await call("admin", "PUT", "/api/tax/settings", { gst_pct: 18, prices_include_tax: true, exempt: ["tuck"], ntn: "1234567-8", strn: "17-00-1234-567-89", wht_section: "153(1)(a)" }), "settings");
  ok(await call("salesman", "POST", "/api/shifts/open", {}), "shift");
  const oil = db.get("SELECT * FROM shop_items WHERE station_id=1 AND category='lubricant' AND stock > 1 LIMIT 1");
  const sale = ok(await call("salesman", "POST", "/api/shop/sales", { station_id: 1, payment_method: "cash", lines: [{ item_id: oil.id, qty: 1 }] }), "shop sale");
  const html = (await call("", "GET", new URL(sale.receipt_url).pathname)).data as string;
  assert.match(html, /Includes sales tax 18%/);
  assert.match(html, /NTN 1234567-8/);
  const sup = db.get("SELECT * FROM suppliers WHERE name='PSO Mehmoodkot Depot'");
  const { supplierOwed } = await import("../src/routes/suppliers.js");
  const owed = supplierOwed(sup.id);
  ok(await call("manager", "POST", `/api/suppliers/${sup.id}/payment`, { amount: 99000, method: "Bank transfer", ref: "PO-WHT", withholding: 1000 }), "payment");
  assert.equal(Math.round(owed - supplierOwed(sup.id)), 100000, "gross paid off the payable");
  const month = db.pkDate().slice(0, 7);
  const r = ok(await call("manager", "GET", `/api/tax/report?month=${month}`), "report");
  const lub = r.shop.find((x: any) => x.category === "lubricant");
  assert.ok(Math.abs(lub.tax - (lub.sales - lub.sales / 1.18)) < 0.05);
  assert.ok(r.withholding.some((w: any) => w.amount === 1000 && w.payee === sup.name && w.section === "153(1)(a)"));
  assert.ok(r.wht_total.pending >= 1000);
  const w = r.withholding.find((x: any) => x.amount === 1000);
  ok(await call("manager", "POST", "/api/tax/withholding/deposit", { ids: [w.id], cpr_no: "IT-20261005-001" }), "deposit");
  assert.equal(db.get("SELECT cpr_no FROM tax_withholdings WHERE id=?", w.id).cpr_no, "IT-20261005-001");
  ok(await call("manager", "POST", "/api/tax/withholding", { payee: "Malik Property", gross: 150000, rate: 10, section: "155 (rent)" }), "rent wht");
  const csv = await call("manager", "GET", `/api/tax/report.csv?month=${month}`);
  assert.match(csv.data, /Withholding tax/);
  assert.match(csv.data, /Malik Property/);
  // the cash book is not touched by the WHT part
  assert.equal(db.get("SELECT COUNT(*) n FROM supplier_txns WHERE method='WHT'").n >= 1, true);
});

test("bank statement: deposits, cheques and payments matched; charges and uncleared items listed", async () => {
  const t0 = Date.now() - 2 * 86_400_000;
  const iso = (ms: number) => new Date(ms).toISOString();
  db.run("INSERT INTO bank_deposits (tenant_id,amount,bank,slip_ref,deposited_by,created_at) VALUES (1,?,?,?,?,?)", 345000, "HBL Ferozepur Road", "DS-77", "Test", iso(t0));
  db.run("INSERT INTO expenses (tenant_id,category,amount,paid_to,method,status,created_by,approved_by,expense_date,created_at,receipt_ref) VALUES (1,'Rent',?,?,?,?,?,?,?,?,?)", 150000, "Malik Property", "cheque", "approved", "Test", "Test", db.pkDate(t0), iso(t0), "CHQ-5512");
  db.run("INSERT INTO expenses (tenant_id,category,amount,paid_to,method,status,created_by,approved_by,expense_date,created_at) VALUES (1,'Maintenance & repairs',?,?,?,?,?,?,?,?)", 42000, "Not yet cleared", "cheque", "approved", "Test", "Test", db.pkDate(t0), iso(t0));
  const d = (ms: number) => new Date(ms + 5 * 3600_000).toISOString().slice(0, 10).split("-").reverse().join("/");
  const csv = [
    "HBL Account 0123-456789,,,,,",
    "Date,Description,Cheque No,Debit,Credit,Balance",
    `${d(t0)},CASH DEPOSIT DS-77,,,"345,000.00","1,845,000.00"`,
    `${d(t0 + 86_400_000)},CHQ PAID 5512 MALIK,CHQ-5512,"150,000.00",,"1,695,000.00"`,
    `${d(t0 + 86_400_000)},SMS ALERT CHARGES,,250.00,,"1,694,750.00"`,
    `${d(t0 + 86_400_000)},IBFT FROM UNKNOWN,,,"12,345.00","1,707,095.00"`,
  ].join("\n");
  const r = ok(await call("admin", "POST", "/api/bank/reconcile", { csv, bank: "HBL" }), "reconcile");
  assert.equal(r.statement_lines, 4);
  assert.equal(r.matched, 2);
  assert.equal(r.statement_closing, 1707095);
  assert.ok(r.bank_only.some((l: any) => l.debit === 250 && /Bank charges/.test(l.hint)));
  assert.ok(r.bank_only.some((l: any) => l.credit === 12345));
  assert.ok(r.books_only.some((e: any) => e.amount === 42000 && e.side === "out"), "cheque not cleared yet");
  assert.equal(ok(await call("admin", "GET", "/api/bank/reconciliations"), "history")[0].matched, 2);
  assert.equal((await call("manager", "POST", "/api/bank/reconcile", { csv })).status, 403, "admin only");
  assert.equal((await call("admin", "POST", "/api/bank/reconcile", { csv: "hello,world\n1,2" })).status, 400);
});

test("general ledger: every voucher balances, trial balance totals agree; CSV and Tally XML", async () => {
  const to = db.pkDate(), from = db.pkDate(Date.now() - 20 * 86_400_000);
  const r = ok(await call("manager", "GET", `/api/ledger?from=${from}&to=${to}`), "ledger");
  assert.ok(r.voucher_count > 20);
  for (const v of r.vouchers) {
    const dr = v.lines.reduce((a: number, l: any) => a + l.debit, 0), cr = v.lines.reduce((a: number, l: any) => a + l.credit, 0);
    assert.ok(Math.abs(dr - cr) < 0.05, `voucher ${v.no} ${v.narration} balances (${dr} vs ${cr})`);
  }
  assert.ok(Math.abs(r.totals.debit - r.totals.credit) < 1);
  const accounts = r.trial_balance.map((a: any) => a.account);
  for (const a of ["Fuel sales", "Shop sales", "Output sales tax", "Fuel purchases", "Khata receivable", "Bank", "Cash in hand"]) assert.ok(accounts.includes(a), a);
  // fuel sales in the ledger = sales in the books (no sales tax on fuel set)
  const fuel = db.get("SELECT SUM(s.amount) v FROM sales s WHERE s.created_at >= ? AND s.created_at < ?", db.pkStart(from), db.pkEnd(to)).v;
  assert.ok(Math.abs(r.trial_balance.find((a: any) => a.account === "Fuel sales").credit - fuel) < 1);
  const csv = await call("manager", "GET", `/api/ledger.csv?from=${from}&to=${to}`);
  assert.match(csv.data, /^\ufeff?"Date","Voucher","Type","Account","Debit","Credit","Narration"/);
  const xml = await call("manager", "GET", `/api/ledger/tally.xml?from=${from}&to=${to}`);
  assert.match(xml.data, /<TALLYREQUEST>Import Data<\/TALLYREQUEST>/);
  assert.match(xml.data, /<LEDGERNAME>Fuel sales<\/LEDGERNAME>/);
  assert.equal((await call("manager", "GET", "/api/ledger?from=2026-01-01&to=2026-09-30")).status, 400, "3 months at most");
  assert.equal((await call("salesman", "GET", "/api/ledger")).status, 403);
});
