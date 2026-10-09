/** Shop (lubricants / tuck shop), vehicle daily limits, paying with loyalty points, digital receipts. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-shop-"));
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

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

let shift: any;
test("shop: scan a barcode, sell, stock goes down, cash goes in the shift bag, low stock alerts", async () => {
  shift = ok(await call("salesman", "POST", "/api/shifts/open", {}), "open");
  const [oil] = ok(await call("salesman", "GET", "/api/shop/items?barcode=8901234500011"), "scan");
  assert.equal(oil.cost, undefined, "salesman does not see cost");
  const water = ok(await call("salesman", "GET", "/api/shop/items?barcode=8901234500080"), "water")[0];
  const before = ok(await call("salesman", "GET", "/api/pos/today"), "today").shift.summary;
  const sale = ok(await call("salesman", "POST", "/api/shop/sales", { station_id: 1, payment_method: "cash", lines: [{ item_id: oil.id, qty: 1 }, { item_id: water.id, qty: 3 }] }), "sale");
  near(sale.total, oil.price + 3 * water.price);
  assert.match(sale.receipt_url, /\/r\//);
  near(sale.shift_summary.cash_expected - before.cash_expected, sale.total, "shop cash in the bag");
  near(sale.shift_summary.shop.total, sale.total);
  const after = ok(await call("manager", "GET", "/api/shop/items?station_id=1"), "items").find((i: any) => i.id === oil.id);
  near(after.stock, oil.stock - 1);
  assert.equal((await call("salesman", "POST", "/api/shop/sales", { station_id: 1, payment_method: "cash", lines: [{ item_id: oil.id, qty: 999 }] })).status, 400, "not enough stock");
  // sell down to the reorder level → alert
  ok(await call("manager", "POST", `/api/shop/items/${oil.id}/adjust`, { counted: 7, reason: "count" }), "adjust");
  ok(await call("salesman", "POST", "/api/shop/sales", { station_id: 1, payment_method: "easypaisa", lines: [{ item_id: oil.id, qty: 1 }] }), "sale 2");
  assert.ok(db.get("SELECT id FROM alerts WHERE type='shop_low_stock'"), "low-stock alert");
  const path = sale.receipt_url.replace(/^https?:\/\/[^/]+/, base);
  const html = await (await fetch(path)).text();
  assert.match(html, /Shell Helix/); assert.match(html, /Total/);
  // thermal print: narrow 58 mm roll + auto-open the printer
  const thermal = await (await fetch(`${path}?print=1&w=58`)).text();
  assert.match(thermal, /58mm auto/); assert.match(thermal, /window\.print\(\)/);
});

test("shop: stock-in updates average cost; undo returns stock; khata sale goes on the ledger; profit in reports", async () => {
  const [filter] = ok(await call("manager", "GET", "/api/shop/items?barcode=8901234500059&station_id=1"), "filter");
  const r = ok(await call("manager", "POST", `/api/shop/items/${filter.id}/stock-in`, { qty: filter.stock, cost: filter.cost + 100, supplier: "Auto parts" }), "stock in");
  near(r.cost, filter.cost + 50, "average cost");
  const acct = ok(await call("salesman", "GET", "/api/pos/khata-accounts"), "accts").find((a: any) => a.status === "ok");
  const bal0 = ok(await call("admin", "GET", `/api/customers/${acct.id}`), "c").balance;
  const s = ok(await call("salesman", "POST", "/api/shop/sales", { station_id: 1, payment_method: "khata", customer_id: acct.id, lines: [{ item_id: filter.id, qty: 2 }] }), "khata");
  near(ok(await call("admin", "GET", `/api/customers/${acct.id}`), "c").balance, bal0 + s.total);
  ok(await call("salesman", "POST", `/api/shop/sales/${s.id}/undo`, {}), "undo");
  near(ok(await call("admin", "GET", `/api/customers/${acct.id}`), "c").balance, bal0, "khata reversed");
  near(ok(await call("manager", "GET", "/api/shop/items?station_id=1"), "items").find((i: any) => i.id === filter.id).stock, r.stock, "stock back");
  const sum = ok(await call("manager", "GET", "/api/shop/summary"), "summary");
  assert.ok(sum.sales > 0 && sum.profit > 0 && sum.stock_value > 0);
  const day = ok(await call("admin", "GET", "/api/dashboard"), "dash").day;
  near(day.sales.shop, sum.sales, "shop sales in today's book");
});

test("vehicle daily limit and registered fuel are enforced; manager can allow more", async () => {
  const acct = ok(await call("salesman", "GET", "/api/pos/khata-accounts"), "accts").find((a: any) => a.status === "ok");
  ok(await call("manager", "POST", `/api/customers/${acct.id}/vehicles`, { plate_no: "LIM-100", fuel: "HSD", daily_limit_l: 40 }), "vehicle");
  const sale = (who: string, litres: number, extra: any = {}) => call(who, "POST", "/api/sales", { station_id: 1, product: "HSD", litres, payment_method: "khata", customer_id: acct.id, vehicle_no: "LIM-100", ...extra });
  ok(await sale("salesman", 30), "30 L");
  const over = await sale("salesman", 15);
  assert.equal(over.status, 400); assert.match(over.data.error, /Daily limit for LIM-100 is 40 L; 30 L already given today \(10 L left\)/);
  assert.equal((await sale("salesman", 15, { override_limit: true })).status, 403, "salesman cannot override");
  ok(await sale("manager", 15, { override_limit: true }), "manager allows");
  const wrong = await call("salesman", "POST", "/api/sales", { station_id: 1, product: "PMG", litres: 5, payment_method: "khata", customer_id: acct.id, vehicle_no: "LIM-100" });
  assert.equal(wrong.status, 400); assert.match(wrong.data.error, /registered for HSD/);
});

test("loyalty: a customer pays with points; not cash in the bag; undo gives the points back", async () => {
  const c = db.get("SELECT * FROM customers WHERE loyalty_points >= 600 ORDER BY loyalty_points DESC LIMIT 1");
  const before = ok(await call("salesman", "GET", "/api/pos/today"), "today").shift.summary;
  const s = ok(await call("salesman", "POST", "/api/sales", { station_id: 1, product: "PMG", amount: 500, payment_method: "loyalty", customer_id: c.id }), "points sale");
  assert.equal(db.get("SELECT loyalty_points p FROM customers WHERE id=?", c.id).p, c.loyalty_points - 500);
  const after = ok(await call("salesman", "GET", "/api/pos/today"), "today").shift.summary;
  near(after.cash_expected, before.cash_expected, "points are not cash"); near(after.points - before.points, 500);
  assert.equal((await call("salesman", "POST", "/api/sales", { station_id: 1, product: "PMG", amount: c.loyalty_points + 1000, payment_method: "loyalty", customer_id: c.id })).status, 400, "not enough points");
  ok(await call("salesman", "POST", `/api/sales/${s.id}/undo`, {}), "undo");
  assert.equal(db.get("SELECT loyalty_points p FROM customers WHERE id=?", c.id).p, c.loyalty_points, "points back");
});
