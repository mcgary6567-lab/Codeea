import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-batche-"));
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

test("usual fill: picks the most common product + amount for a khata customer", async () => {
  const c = ok(await call("POST", "/api/customers", { name: "Usual Co", type: "fleet", phone: "03002220001", credit_limit: 1_000_000 }), "cust");
  const st = db.get("SELECT id FROM stations WHERE tenant_id=1 ORDER BY id LIMIT 1")!.id;
  // three fills of 20L PMG @250 (=5000) + one odd one
  for (let i = 0; i < 3; i++) db.run("INSERT INTO sales (station_id,customer_id,product,litres,rate,amount,payment_method,vehicle_no,created_at) VALUES (?,?,?,?,?,?,?,?,?)", st, c.id, "PMG", 20, 250, 5000, "khata", "LEA-1", new Date(Date.now() - i * 86400000).toISOString());
  db.run("INSERT INTO sales (station_id,customer_id,product,litres,rate,amount,payment_method,vehicle_no,created_at) VALUES (?,?,?,?,?,?,?,?,?)", st, c.id, "HSD", 10, 270, 2700, "khata", "LEA-1", new Date().toISOString());
  const u = ok(await call("GET", `/api/pos/usual?customer_id=${c.id}`), "usual");
  assert.ok(u.usual, "has a usual");
  assert.equal(u.usual.product, "PMG");
  assert.equal(u.usual.amount, 5000);
  assert.ok(u.usual.times >= 3);
});

test("khata limit review suggests raise for a near-limit good payer", async () => {
  const c = ok(await call("POST", "/api/customers", { name: "Good Payer", type: "fleet", phone: "03002220002", credit_limit: 100000 }), "cust");
  // near limit, low risk, recent payment
  db.run("UPDATE customers SET balance=90000, risk_score=10 WHERE id=?", c.id);
  db.run("INSERT INTO khata_ledger (customer_id,type,amount,created_at) VALUES (?,?,?,?)", c.id, "credit", 1, new Date().toISOString());
  const rev = ok(await call("GET", "/api/khata/limit-review"), "review");
  const row = rev.find((x: any) => x.id === c.id);
  assert.ok(row, "good payer suggested");
  assert.equal(row.direction, "raise");
  assert.ok(row.suggested_limit > 100000);
});

test("payment is a valid AI photo kind (route accepts it)", async () => {
  // AI is off in tests, so it saves the photo and returns null result without error
  const r = await call("POST", "/api/ai/read-photo", { kind: "payment", image: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==" });
  assert.equal(r.status, 200, JSON.stringify(r.data));
  assert.ok(r.data.photo_id, "photo saved");
});
