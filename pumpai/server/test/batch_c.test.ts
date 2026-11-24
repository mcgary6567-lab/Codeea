import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-batchc-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server;
let base = "";
let db: typeof import("../src/db.js");
let token = "";
async function call(method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, msg: string) => { assert.equal(r.status, 200, `${msg}: ${JSON.stringify(r.data)}`); return r.data; };

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  token = (await call("POST", "/api/auth/login", { email: "admin@pumpai.pk", password: "demo1234" })).data.token;
});
after(() => server?.close());

test("scheduled price: stays pending until due, then the job applies it (new rate becomes current)", async () => {
  const { applyDuePrices } = await import("../src/routes/operations.js");
  ok(await call("POST", "/api/prices", { prices: { PMG: 250 }, broadcast: false }), "base price");
  // schedule a change for later (must be >1 min in the future)
  const at = new Date(Date.now() + 2 * 3600_000).toISOString();
  const s = ok(await call("POST", "/api/prices/schedule", { prices: { PMG: 275 }, broadcast: false, effective_at: at }), "schedule");
  assert.equal(s.status, "pending");
  // not applied yet
  let cur = ok(await call("GET", "/api/prices"), "prices").current;
  assert.equal(cur.PMG.price, 250, "still old rate before due");
  assert.equal(ok(await call("GET", "/api/prices/scheduled"), "pending").length, 1);
  // make it due, then run the job
  db.run("UPDATE scheduled_prices SET effective_at=? WHERE id=?", new Date(Date.now() - 1000).toISOString(), s.id);
  const n = await applyDuePrices(1);
  assert.ok(n >= 1, "one applied");
  cur = ok(await call("GET", "/api/prices"), "prices2").current;
  assert.equal(cur.PMG.price, 275, "new rate live after due");
  assert.equal(ok(await call("GET", "/api/prices/scheduled"), "pending2").length, 0, "no longer pending");

  // past time is rejected
  assert.equal((await call("POST", "/api/prices/schedule", { prices: { PMG: 280 }, effective_at: new Date(Date.now() - 1000).toISOString() })).status, 400);
});

test("product margins: sale rate − purchase cost", async () => {
  const month = new Date().toISOString().slice(0, 7);
  const r = ok(await call("GET", `/api/analysis/margins?month=${month}`), "margins");
  assert.ok(Array.isArray(r.products));
  if (r.products.length) {
    const p = r.products[0];
    assert.ok("avg_sale_rate" in p && "avg_cost" in p && "margin_per_l" in p && "profit" in p);
    assert.ok(Math.abs(p.margin_per_l - (p.avg_sale_rate - p.avg_cost)) <= 0.02, "margin = sale − cost");
  }
});

test("targets: set and read back with actuals", async () => {
  const month = new Date().toISOString().slice(0, 7);
  ok(await call("PUT", "/api/analysis/targets", { month, sales_litres: 100000, revenue: 25000000, net_profit: 800000 }), "set");
  const t = ok(await call("GET", `/api/analysis/targets?month=${month}`), "get");
  assert.equal(t.target.sales_litres, 100000);
  assert.ok("actual" in t && typeof t.actual.revenue === "number");
});
