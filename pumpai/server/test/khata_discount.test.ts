import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-disc-"));
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
  for (const who of ["admin", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => server?.close());

test("khata lump-sum discount: net billed, booked gross with a discount line, salesman capped, books tally", async () => {
  const { tallyBooks } = await import("./helpers/tally.js");
  ok(await call("admin", "POST", "/api/prices", { prices: { PMG: 250 }, broadcast: false }), "price 250");
  const cust = ok(await call("admin", "POST", "/api/customers", { name: "Disc Co", type: "fleet", phone: "03008880001", credit_limit: 5_000_000 }), "customer");
  // CEO-controlled limit: salesman can give up to Rs 500
  ok(await call("admin", "PUT", "/api/settings", { limits: { khata_discount_max: 500 } }), "set limit");

  const { tb: tbBefore } = await tallyBooks("before");
  const discBefore = tbBefore("Discount given — khata"); // the demo already has some seeded khata discounts

  // admin sells 100 L @ 250 = 25,000 with a Rs 1000 discount → net 24,000 charged to khata
  const sale = ok(await call("admin", "POST", "/api/sales", { station_id: 1, product: "PMG", litres: 100, payment_method: "khata", customer_id: cust.id, discount: 1000 }), "discounted sale");
  assert.equal(sale.amount, 24_000, "net amount stored");
  assert.equal(sale.discount, 1000, "discount stored");

  const c = ok(await call("admin", "GET", `/api/customers/${cust.id}`), "cust");
  assert.equal(c.balance ?? c.customer?.balance, 24_000, "khata billed the net");
  const k = db.get("SELECT amount FROM khata_ledger WHERE ref=?", `SALE-${sale.id}`)!;
  assert.equal(k.amount, 24_000, "khata ledger = net");

  // books: Fuel sales booked at GROSS 25,000, with a "Discount given — khata" of 1000; everything tallies
  const { tb } = await tallyBooks("after discount");
  assert.ok(Math.abs((tb("Discount given — khata") - discBefore) - 1000) <= 0.05, `discount account rose by 1000, got ${(tb("Discount given — khata") - discBefore).toFixed(2)}`);

  // salesman cannot give more than the Rs 500 limit
  const over = await call("salesman", "POST", "/api/sales", { station_id: 1, product: "PMG", litres: 10, payment_method: "khata", customer_id: cust.id, discount: 800 });
  assert.equal(over.status, 403, `salesman over-limit blocked: ${JSON.stringify(over.data)}`);
  assert.match(over.data.error, /manager|CEO/i);

  // a discount bigger than the fuel amount is rejected
  const silly = await call("admin", "POST", "/api/sales", { station_id: 1, product: "PMG", litres: 1, payment_method: "khata", customer_id: cust.id, discount: 9999 });
  assert.equal(silly.status, 400, "discount > amount rejected");

  // discount on a non-khata sale is rejected
  const cash = await call("admin", "POST", "/api/sales", { station_id: 1, product: "PMG", litres: 10, payment_method: "cash", discount: 100 });
  assert.equal(cash.status, 400, "discount only for khata");
});
