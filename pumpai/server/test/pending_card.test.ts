import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-pending-"));
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

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  tokens.admin = (await call("", "POST", "/api/auth/login", { email: "admin@pumpai.pk", password: "demo1234" })).data.token;
});
after(() => server?.close());

test("khata card-pending hold: no debt at fill, billed at clear-day rate, books tally throughout", async () => {
  const { tallyBooks } = await import("./helpers/tally.js");

  // a khata customer who will fill now and bring the card later
  const cust = ok(await call("admin", "POST", "/api/customers", { name: "Thana 112", type: "fleet", phone: "03009990112", credit_limit: 1_000_000 }), "customer");
  // fix the pump price at the fill day
  ok(await call("admin", "POST", "/api/prices", { prices: { PMG: 250 }, broadcast: false }), "price 250");

  await tallyBooks("before");

  // fill 100 L on trust — pending hold, no rate locked, no debt
  const fill = ok(await call("admin", "POST", "/api/sales", { station_id: 1, product: "PMG", litres: 100, payment_method: "khata", customer_id: cust.id, pending: true }), "pending fill");
  assert.equal(fill.pending, 1, "sale is held pending");
  assert.equal(fill.payment_method, "khata");
  const afterFill = ok(await call("admin", "GET", `/api/customers/${cust.id}`), "cust after fill");
  assert.equal(afterFill.balance ?? afterFill.customer?.balance ?? 0, 0, "no khata debt raised at fill");
  // nothing on the khata ledger yet
  assert.equal(db.get("SELECT COUNT(*) n FROM khata_ledger WHERE customer_id=?", cust.id)!.n, 0, "no khata entry yet");
  // but the fuel left the tank and the books still balance (Unbilled fuel asset holds it)
  await tallyBooks("pending-held");

  // the pending list shows it at today's rate
  let pend = ok(await call("admin", "GET", "/api/sales/pending"), "pending list");
  assert.equal(pend.length, 1, "one hold listed");
  assert.equal(pend[0].id, fill.id);
  assert.equal(pend[0].current_rate, 250, "current rate shown");

  // days later the rate goes UP to 270
  ok(await call("admin", "POST", "/api/prices", { prices: { PMG: 270 }, broadcast: false }), "price 270");
  pend = ok(await call("admin", "GET", "/api/sales/pending"), "pending list 2");
  assert.equal(pend[0].current_rate, 270, "clear-day rate");
  assert.equal(pend[0].projected_amount, 27_000, "100L × 270");

  // the card arrives → clear at today's rate (default), billed to the khata
  const cleared = ok(await call("admin", "POST", "/api/sales/clear", { ids: [fill.id] }), "clear");
  assert.equal(cleared.total, 27_000, "billed at 270, not 250");

  const afterClear = ok(await call("admin", "GET", `/api/customers/${cust.id}`), "cust after clear");
  const bal = afterClear.balance ?? afterClear.customer?.balance;
  assert.equal(bal, 27_000, "khata now owes 100L × 270");
  // khata ledger carries the debit, tied to the sale
  const k = db.get("SELECT * FROM khata_ledger WHERE customer_id=? AND ref=?", cust.id, `SALE-${fill.id}`)!;
  assert.equal(k.amount, 27_000);
  assert.equal(k.rate, 270);
  // sale row now cleared and no longer pending
  const srow = db.get("SELECT pending, clear_rate, cleared_at FROM sales WHERE id=?", fill.id)!;
  assert.equal(srow.pending, 0);
  assert.equal(srow.clear_rate, 270);
  assert.ok(srow.cleared_at, "cleared_at stamped");

  // no longer in the pending list
  pend = ok(await call("admin", "GET", "/api/sales/pending"), "pending list 3");
  assert.equal(pend.length, 0, "hold cleared");

  // the whole system still tallies: Khata receivable == customer balance, journal balances, Unbilled fuel nets to zero
  const { tb } = await tallyBooks("cleared");
  assert.ok(Math.abs(tb("Unbilled fuel (card pending)")) <= 0.05, "unbilled asset cleared to zero");
});
