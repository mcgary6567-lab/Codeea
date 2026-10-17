import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-aging-"));
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
const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString();

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  token = (await call("POST", "/api/auth/login", { email: "admin@pumpai.pk", password: "demo1234" })).data.token;
});
after(() => server?.close());

test("khata aging: oldest charges clear first (FIFO), balance split into 0-30/31-60/61-90/90+ buckets", async () => {
  const c = ok(await call("POST", "/api/customers", { name: "Aging Co", type: "fleet", phone: "03004440001", credit_limit: 1_000_000 }), "customer");
  // three charges of 10,000 at 100, 50 and 10 days ago, then a 10,000 payment (clears the OLDEST = 100-day one)
  const debit = (amt: number, at: string) => db.run("INSERT INTO khata_ledger (customer_id,type,amount,ref,created_at) VALUES (?,?,?,?,?)", c.id, "debit", amt, "x", at);
  debit(10_000, daysAgo(100));
  debit(10_000, daysAgo(50));
  debit(10_000, daysAgo(10));
  db.run("INSERT INTO khata_ledger (customer_id,type,amount,created_at) VALUES (?,?,?,?)", c.id, "credit", 10_000, daysAgo(2));
  db.run("UPDATE customers SET balance=20000 WHERE id=?", c.id);

  const a = ok(await call("GET", "/api/khata/aging"), "aging");
  const row = a.list.find((x: any) => x.id === c.id);
  assert.ok(row, "customer listed");
  // oldest (100d) paid off → gone; left: 50d in 31-60, 10d in 0-30
  assert.equal(row.buckets[0], 10_000, "0-30 bucket = the 10-day charge");
  assert.equal(row.buckets[1], 10_000, "31-60 bucket = the 50-day charge");
  assert.equal(row.buckets[2], 0, "61-90 empty");
  assert.equal(row.buckets[3], 0, "90+ empty (100-day charge was paid off first)");
  assert.equal(row.balance, 20_000);
  assert.equal(row.overdue, 10_000, "overdue = 31+ days");
});
