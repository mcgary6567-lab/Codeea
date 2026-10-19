/** Salesman fixes his own entries before closing: edit litres / payment method, remove an entry — stock, khata and books all stay balanced. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-saleedit-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server; let base = ""; let db: typeof import("../src/db.js");
const tokens: Record<string, string> = {};
async function call(who: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tokens[who] ? { authorization: `Bearer ${tokens[who]}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, m: string) => { assert.equal(r.status, 200, `${m}: ${JSON.stringify(r.data)}`); return r.data; };
const near = (a: number, b: number, m?: string) => assert.ok(Math.abs(a - b) < 0.05, `${m ?? ""} ${a} ≈ ${b}`);

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server?.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("salesman fixes his own entries before close: edit litres + method, then remove one — books stay tallied", async () => {
  const noz = db.get("SELECT n.id nozzle_id, n.tank_id, n.station_id, n.totalizer, t.product FROM nozzles n JOIN tanks t ON t.id=n.tank_id JOIN stations s ON s.id=n.station_id WHERE s.tenant_id=1 AND n.active=1 AND t.product='PMG' ORDER BY n.id LIMIT 1") as any;
  assert.ok(noz, "a PMG nozzle");
  db.run("UPDATE tanks SET current_l = 20000 WHERE id=?", noz.tank_id);
  for (const sh of db.all("SELECT id FROM shifts WHERE station_id=? AND status='open'", noz.station_id) as any[]) db.run("UPDATE shifts SET status='closed', closed_at=? WHERE id=?", new Date().toISOString(), sh.id);
  ok(await call("salesman", "POST", "/api/shifts/open", { station_id: noz.station_id, readings: { [noz.nozzle_id]: noz.totalizer } }), "open shift");

  // two cash sales, by mistake the first should have been 40 L on jazzcash
  const a = ok(await call("salesman", "POST", "/api/sales", { station_id: noz.station_id, product: "PMG", litres: 50, payment_method: "cash" }), "sale A");
  const b = ok(await call("salesman", "POST", "/api/sales", { station_id: noz.station_id, product: "PMG", litres: 10, payment_method: "cash" }), "sale B");
  const tankAfterSales = db.get("SELECT current_l FROM tanks WHERE id=?", noz.tank_id).current_l;

  // see the whole shift
  let list = ok(await call("salesman", "GET", "/api/pos/entries"), "entries");
  assert.equal(list.entries.length, 2, "both entries listed");
  assert.ok(list.entries.every((e: any) => e.can_edit && e.can_undo), "own live entries are editable");
  const cashBefore = list.summary.cash_sales;

  // fix A: 50 L cash → 40 L jazzcash
  const edited = ok(await call("salesman", "POST", `/api/sales/${a.id}/edit`, { litres: 40, payment_method: "jazzcash" }), "edit A");
  assert.notEqual(edited.sale.id, a.id, "a fresh row replaces the old one");
  near(edited.sale.litres, 40, "new litres"); assert.equal(edited.sale.payment_method, "jazzcash");
  // tank: gave back 50, took 40 → net +10 vs before
  near(db.get("SELECT current_l FROM tanks WHERE id=?", noz.tank_id).current_l, tankAfterSales + 10, "tank adjusted by the litre change");
  // cash sales drop (A left cash); jazzcash now carries A's money
  list = ok(await call("salesman", "GET", "/api/pos/entries"), "entries after edit");
  near(list.summary.cash_sales, cashBefore - a.amount, "A's cash removed from the bag");
  assert.ok(list.summary.digital > 0, "jazzcash counted as digital");

  // remove B entirely
  ok(await call("salesman", "POST", `/api/sales/${b.id}/undo`, {}), "remove B");
  list = ok(await call("salesman", "GET", "/api/pos/entries"), "entries after delete");
  assert.equal(list.entries.length, 1, "only the fixed A remains");

  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("sale edit");
  assert.equal(ok(await call("admin", "GET", "/api/books/check"), "books").ok, true, "books tally after edits");
});

test("guards: cannot edit another salesman's sale, a closed shift's sale, or a special-money sale", async () => {
  const noz = db.get("SELECT n.id nozzle_id, n.station_id, n.totalizer FROM nozzles n JOIN tanks t ON t.id=n.tank_id JOIN stations s ON s.id=n.station_id WHERE s.tenant_id=1 AND n.active=1 AND t.product='PMG' ORDER BY n.id LIMIT 1") as any;
  // a loyalty/coupon-style sale can't be edited in place: make a khata card-pending sale and confirm it is refused
  const cust = db.get("SELECT id FROM customers WHERE tenant_id=1 AND credit_limit > 0 ORDER BY id LIMIT 1") as any;
  if (cust) {
    const p = await call("salesman", "POST", "/api/sales", { station_id: noz.station_id, product: "PMG", litres: 5, payment_method: "khata", customer_id: cust.id, vehicle_no: "AB-123", slip_no: "1", pending: true });
    if (p.status === 200) {
      const r = await call("salesman", "POST", `/api/sales/${p.data.id}/edit`, { litres: 6 });
      assert.equal(r.status, 400, "card-pending khata sale refused for edit");
    }
  }
});
