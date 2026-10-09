import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-khrate-"));
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
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => server?.close());

test("khata account gets its own CEO rate; a bulk +/- moves every special rate; books stay correct", async () => {
  const { recordSale } = await import("../src/services.js");
  const { tallyBooks } = await import("./helpers/tally.js");
  const pump = ok(await call("admin", "GET", "/api/prices"), "prices").current.PMG.price as number;

  // a police khata account
  const cust = ok(await call("admin", "POST", "/api/customers", { name: "Police 112", phone: "03007778888", type: "police", credit_limit: 1_000_000 }), "customer");

  // salesman cannot set rates; the CEO (admin) can
  assert.equal((await call("salesman", "PUT", `/api/customers/${cust.id}/rates`, { rates: { PMG: 240 } })).status, 403);
  ok(await call("admin", "PUT", `/api/customers/${cust.id}/rates`, { rates: { PMG: 240 } }), "set rate");
  assert.equal(ok(await call("admin", "GET", `/api/customers/${cust.id}`), "detail").rates.PMG, 240);

  // a khata fill now bills at 240, not the pump price
  const s1 = recordSale(1, { station_id: 1, product: "PMG", litres: 10, payment_method: "khata", customer_id: cust.id } as any);
  assert.equal(s1.rate, 240, "special rate applied");
  assert.equal(s1.amount, 2400, "amount at special rate");
  assert.notEqual(240, pump, "special rate differs from pump (demo)");

  // the POS khata list carries the special rate so the tablet shows the right price
  const acc = ok(await call("salesman", "GET", "/api/pos/khata-accounts"), "khata list").find((a: any) => a.id === cust.id);
  assert.equal(acc.rates.PMG, 240, "POS sees the special rate");

  // the CEO enters +2 when the pump rate goes up — every special rate moves
  const shift = ok(await call("admin", "POST", "/api/customers/rates/shift", { delta: 2 }), "shift up");
  assert.ok(shift.shifted >= 1);
  assert.equal(ok(await call("admin", "GET", `/api/customers/${cust.id}`), "detail2").rates.PMG, 242, "rate +2");
  const s2 = recordSale(1, { station_id: 1, product: "PMG", litres: 10, payment_method: "khata", customer_id: cust.id } as any);
  assert.equal(s2.rate, 242, "new rate billed");

  // −2 brings it back
  ok(await call("admin", "POST", "/api/customers/rates/shift", { delta: -2 }), "shift down");
  assert.equal(ok(await call("admin", "GET", `/api/customers/${cust.id}`), "detail3").rates.PMG, 240);

  // the khata balance is exactly the two fills at their billed rates, and the whole system tallies
  const bal = ok(await call("admin", "GET", `/api/customers/${cust.id}`), "bal").balance;
  assert.ok(Math.abs(bal - (2400 + 2420)) < 0.5, `khata balance ${bal}`);
  await tallyBooks("khata-rate");

  // clearing the rate falls back to the pump price
  ok(await call("admin", "PUT", `/api/customers/${cust.id}/rates`, { rates: { PMG: null } }), "clear");
  const s3 = recordSale(1, { station_id: 1, product: "PMG", litres: 5, payment_method: "khata", customer_id: cust.id } as any);
  assert.equal(s3.rate, pump, "back to pump price");
});
