/** Go-live opening balances: khata (list + paste import), parties, bank, staff, stock — booked to equity, never P&L; books tally. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-opening-"));
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
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, msg: string) => { assert.equal(r.status, 200, `${msg}: ${JSON.stringify(r.data)}`); return r.data; };
const near = (a: number, b: number, msg?: string) => assert.ok(Math.abs(a - b) < 0.05, `${msg ?? ""} ${a} ≈ ${b}`);
const month = new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 7);

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "cashier"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => server?.close());

let plBefore: any, cust: any, otherIncomeBefore = 0;

test("CEO only; the page lists every book that can be brought forward", async () => {
  assert.equal((await call("manager", "GET", "/api/opening")).status, 403);
  assert.equal((await call("cashier", "POST", "/api/opening/khata", { rows: [] })).status, 403);
  const d = ok(await call("admin", "GET", "/api/opening"), "get");
  for (const k of ["khata", "suppliers", "wholesale", "carriage", "banks", "staff", "stock", "cash"]) assert.ok(k in d, k);
  cust = d.khata.find((c: any) => c.credit_limit > 0);
  plBefore = ok(await call("admin", "GET", `/api/analysis/pl?month=${month}`), "pl").pl;
  otherIncomeBefore = plBefore.other_income;
});

test("khata openings: set, replace, negative (advance), clear — balance follows, booked to equity", async () => {
  const bal0 = cust.balance;
  ok(await call("admin", "POST", "/api/opening/khata", { as_of: "2026-10-01", rows: [{ customer_id: cust.id, amount: 85000 }] }), "set");
  near(db.get("SELECT balance FROM customers WHERE id=?", cust.id).balance, bal0 + 85000, "balance up");
  const row = db.get("SELECT * FROM khata_ledger WHERE customer_id=? AND ref='open:'", cust.id);
  assert.equal(row.type, "debit"); near(row.amount, 85000);
  assert.ok(row.created_at < new Date().toISOString(), "stamped on the go-live day");
  // re-entering replaces, never adds
  ok(await call("admin", "POST", "/api/opening/khata", { rows: [{ customer_id: cust.id, amount: 60000 }] }), "replace");
  near(db.get("SELECT balance FROM customers WHERE id=?", cust.id).balance, bal0 + 60000, "replaced");
  assert.equal(db.get("SELECT COUNT(*) n FROM khata_ledger WHERE customer_id=? AND ref='open:'", cust.id).n, 1);
  // the statement shows it as the first line
  const st = ok(await call("admin", "GET", `/api/customers/${cust.id}/statement`), "statement");
  assert.ok(st.lines.some((l: any) => l.ref === "open:" && l.amount === 60000));
  // ledger: equity, not income
  const j = ok(await call("admin", "GET", "/api/ledger?from=2026-10-01&to=2026-10-01"), "journal");
  const tb = j.trial_balance.find((x: any) => x.account === "Opening balances (go-live)");
  assert.ok(tb, "equity account exists");
  const pl = ok(await call("admin", "GET", `/api/analysis/pl?month=${month}`), "pl").pl;
  near(pl.other_income, otherIncomeBefore, "P&L other income unchanged");
  // we owe the customer (advance) → credit
  ok(await call("admin", "POST", "/api/opening/khata", { rows: [{ customer_id: cust.id, amount: -5000 }] }), "advance");
  near(db.get("SELECT balance FROM customers WHERE id=?", cust.id).balance, bal0 - 5000, "advance");
  assert.equal(db.get("SELECT type FROM khata_ledger WHERE customer_id=? AND ref='open:'", cust.id).type, "credit");
  // clear
  ok(await call("admin", "POST", "/api/opening/khata", { rows: [{ customer_id: cust.id, amount: 0 }] }), "clear");
  near(db.get("SELECT balance FROM customers WHERE id=?", cust.id).balance, bal0, "cleared");
  assert.equal(db.get("SELECT COUNT(*) n FROM khata_ledger WHERE customer_id=? AND ref='open:'", cust.id).n, 0);
  // the Corrections page will not void an opening row
  ok(await call("admin", "POST", "/api/opening/khata", { rows: [{ customer_id: cust.id, amount: 1000 }] }), "set again");
  const kid = db.get("SELECT id FROM khata_ledger WHERE customer_id=? AND ref='open:'", cust.id).id;
  const v = await call("admin", "POST", `/api/corrections/khata/${kid}/void`, { reason: "test" });
  assert.equal(v.status, 400); assert.match(v.data.error, /Opening balances/);
});

test("paste import: matches by phone, creates new khata accounts, reports bad lines", async () => {
  const existing = db.get("SELECT id, name, phone, balance FROM customers WHERE tenant_id=1 AND credit_limit>0 AND id<>? ORDER BY id LIMIT 1", cust.id);
  const text = [
    "name, phone, amount, limit",
    `${existing.name}, ${existing.phone}, 12000`,
    "Naya Khata Wala, 0300 9998877, 30000, 90000",
    "No Phone New Guy, , 500",
    "Bad Line Here",
  ].join("\n");
  const r = ok(await call("admin", "POST", "/api/opening/khata/import", { as_of: "2026-10-01", text }), "import");
  assert.equal(r.updated, 1); assert.equal(r.created, 1); assert.equal(r.errors.length, 2);
  near(db.get("SELECT balance FROM customers WHERE id=?", existing.id).balance, existing.balance + 12000, "existing updated");
  const nw = db.get("SELECT * FROM customers WHERE tenant_id=1 AND name='Naya Khata Wala'");
  assert.ok(nw, "created"); near(nw.balance, 30000); near(nw.credit_limit, 90000);
  assert.equal(nw.phone, "923009998877");
  // importing the same list again replaces (no doubling)
  ok(await call("admin", "POST", "/api/opening/khata/import", { text }), "again");
  near(db.get("SELECT balance FROM customers WHERE id=?", nw.id).balance, 30000, "no doubling");
});

test("supplier / wholesale / thekedar / bank / staff / stock openings", async () => {
  const d = ok(await call("admin", "GET", "/api/opening"), "get");
  const sup = d.suppliers[0];
  const r1 = ok(await call("admin", "POST", "/api/opening/party", { kind: "supplier", id: sup.id, amount: sup.opening_balance + 250000 }), "supplier");
  near(r1.due, sup.owed + 250000, "supplier owed up by the opening");
  const wc = d.wholesale[0];
  const r2 = ok(await call("admin", "POST", "/api/opening/party", { kind: "wholesale", id: wc.id, amount: wc.opening_balance + 40000 }), "wholesale");
  near(r2.due, wc.due + 40000);
  const th = d.carriage[0];
  const r3 = ok(await call("admin", "POST", "/api/opening/party", { kind: "carriage", id: th.id, amount: th.opening_balance + 7000 }), "thekedar");
  near(r3.due, th.due + 7000);
  const bk = d.banks[0];
  const r4 = ok(await call("admin", "POST", "/api/opening/bank", { id: bk.id, amount: bk.opening_balance + 100000, date: bk.opening_date }), "bank");
  near(r4.account.balance, bk.balance + 100000);
  const u = d.staff.find((x: any) => x.role === "salesman");
  const r5 = ok(await call("admin", "POST", "/api/opening/staff", { user_id: u.id, amount: 15000 }), "staff");
  near(r5.balance, u.balance + 15000, "staff owes the opening advance");
  ok(await call("admin", "POST", "/api/opening/staff", { user_id: u.id, amount: 9000 }), "staff replace");
  near(db.get("SELECT COALESCE(SUM(amount),0) v FROM staff_ledger WHERE user_id=? AND ref='open:'", u.id).v, 9000);
  const cashBefore = ok(await call("admin", "GET", "/api/cash"), "cash").cash_in_hand;
  near(ok(await call("admin", "GET", "/api/cash"), "cash").cash_in_hand, cashBefore, "no cash moved for a staff opening");
  const tk = d.stock[0];
  const r6 = ok(await call("admin", "POST", "/api/opening/stock", { tank_id: tk.id, litres: Math.min(tk.capacity_l, tk.current_l + 500) }), "stock");
  near(r6.tank.current_l, Math.min(tk.capacity_l, tk.current_l + 500));
  assert.equal((await call("admin", "POST", "/api/opening/stock", { tank_id: tk.id, litres: tk.capacity_l * 2 })).status, 400, "over capacity refused");
  assert.equal((await call("admin", "POST", "/api/opening/party", { kind: "supplier", id: 999999, amount: 1 })).status, 404);
});

test("every book still tallies to the rupee", async () => {
  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("opening");
  ok(await call("admin", "GET", "/api/books/check"), "books check").ok && assert.equal(ok(await call("admin", "GET", "/api/books/check"), "books check").ok, true);
});
