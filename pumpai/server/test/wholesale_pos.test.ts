/**
 * Wholesale client fuelled straight from the pump nozzle (POS), billed at their wholesale rate.
 * The litres/stock/meter live on a sales row (method 'wholesale'); the money is a wholesale_txns supply
 * (tank_id NULL, so it is not a 2nd stock-out) plus, when paid on the spot, a payment. Books stay tallied.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-wspos-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server, base = "";
let db: typeof import("../src/db.js");
const tokens: Record<string, string> = {};
async function call(who: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tokens[who] ? { authorization: `Bearer ${tokens[who]}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, msg: string) => { assert.equal(r.status, 200, `${msg}: ${JSON.stringify(r.data)}`); return r.data; };
const near = (a: number, b: number, msg?: string) => assert.ok(Math.abs(a - b) < 0.05, `${msg ?? ""} ${a} ≈ ${b}`);

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "wholesale", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => server?.close());

const RATE = 250; // HSD wholesale rate we set for the client
let W: any, st = 0, shiftId = 0, nozzleId = 0, saleId = 0, dueBefore = 0, tankId = 0, tankBefore = 0;
const dueOf = async () => {
  const list = ok(await call("wholesale", "GET", "/api/wholesale/clients"), "clients list");
  return (list.find((x: any) => x.id === W.id) ?? {}).due as number;
};

test("setup: a dedicated wholesale client with an HSD rate + credit limit, and a fresh salesman shift", async () => {
  db.run("UPDATE shifts SET status='closed', closed_at=?, cash_actual=0, handed_amount=0, handed_at=? WHERE status='open'", new Date().toISOString(), new Date().toISOString());
  // a client only this test uses, so the shared demo clients (used by other test files) are never touched
  W = ok(await call("admin", "POST", "/api/wholesale/clients", { name: "WS POS Test Traders", rates: { HSD: RATE } }), "new client");
  ok(await call("admin", "PATCH", `/api/wholesale/clients/${W.id}`, { credit_limit: 10_000_000 }), "limit");
  const live = ok(await call("salesman", "POST", "/api/shifts/open", {}), "open");
  shiftId = live.id;
  const l = ok(await call("salesman", "GET", `/api/shifts/${shiftId}/live`), "live");
  st = l.shift.station_id;
  const noz = l.readings.find((r: any) => r.product === "HSD");
  assert.ok(noz, "an HSD nozzle on this shift");
  nozzleId = noz.nozzle_id;
  const tank = db.get("SELECT id, current_l FROM tanks WHERE station_id=? AND product='HSD' ORDER BY current_l DESC LIMIT 1", st)!;
  tankId = tank.id; tankBefore = tank.current_l;
});

test("udhaar: wholesale client fills 50 L HSD at the pump → client khata up, tank down, a wholesale supply (tank NULL), Fuel sales untouched", async () => {
  dueBefore = (await dueOf());
  const r = ok(await call("salesman", "POST", "/api/sales", { station_id: st, product: "HSD", litres: 50, payment_method: "khata", wholesale_client_id: W.id, nozzle_id: nozzleId }), "wholesale pos sale");
  saleId = r.id;
  assert.equal(r.payment_method, "wholesale", "sales row marked wholesale");
  near(r.amount, 50 * RATE, "sale amount = litres × wholesale rate");
  near(r.wholesale.amount, 50 * RATE); assert.equal(r.wholesale.settled, false, "udhaar: not settled");
  near(r.wholesale.due_after, dueBefore + 50 * RATE, "client khata up by the fill");
  near(db.get("SELECT current_l FROM tanks WHERE id=?", tankId)!.current_l, tankBefore - 50, "tank down by 50 L (once)");
  const sup = db.get("SELECT * FROM wholesale_txns WHERE sale_id=? AND type='supply'", saleId);
  assert.ok(sup && sup.tank_id == null, "a wholesale supply linked to the sale, tank_id NULL (not a 2nd stock-out)");
  assert.equal(db.get("SELECT COUNT(*) n FROM wholesale_txns WHERE sale_id=? AND type='payment'", saleId)!.n, 0, "udhaar raises no payment");
  // ledger: this is wholesale revenue, not retail Fuel sales
  const today = new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10);
  const j = ok(await call("admin", "GET", `/api/ledger?from=${today}&to=${today}`), "journal");
  assert.ok(j.vouchers.some((v: any) => /Wholesale supply/.test(JSON.stringify(v)) && JSON.stringify(v).includes(String(50 * RATE))), "booked as Wholesale sales");
  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("wholesale pos udhaar");
  assert.equal(ok(await call("admin", "GET", "/api/books/check"), "books").ok, true);
});

test("pay-now cash: a second fill paid at the pump → supply + payment net to zero on the khata, cash in the books up", async () => {
  const dueBefore2 = (await dueOf());
  const r = ok(await call("salesman", "POST", "/api/sales", { station_id: st, product: "HSD", litres: 20, payment_method: "cash", wholesale_client_id: W.id, nozzle_id: nozzleId }), "pay-now cash");
  assert.equal(r.payment_method, "wholesale");
  assert.equal(r.wholesale.settled, true, "paid now");
  near(r.wholesale.due_after, dueBefore2, "khata unchanged (supply then payment)");
  assert.equal(db.get("SELECT COUNT(*) n FROM wholesale_txns WHERE sale_id=? AND type='payment' AND method='cash'", r.id)!.n, 1, "a cash payment booked");
  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("wholesale pos cash");
  assert.equal(ok(await call("admin", "GET", "/api/books/check"), "books").ok, true);
});

test("undo reverses both sides: stock back, wholesale supply voided, khata back", async () => {
  const dueBeforeUndo = (await dueOf());
  ok(await call("salesman", "POST", `/api/sales/${saleId}/undo`, {}), "undo");
  assert.equal(db.get("SELECT COUNT(*) n FROM sales WHERE id=?", saleId)!.n, 0, "sales row gone");
  assert.equal(db.get("SELECT voided FROM wholesale_txns WHERE sale_id=? AND type='supply'", saleId)!.voided, 1, "supply voided");
  near((await dueOf()), dueBeforeUndo - 50 * RATE, "khata back down");
  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("wholesale pos undo");
  assert.equal(ok(await call("admin", "GET", "/api/books/check"), "books").ok, true);
});


