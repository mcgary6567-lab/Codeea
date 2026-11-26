import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-batcha-"));
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

test("competitor prices: add, list vs ours", async () => {
  ok(await call("POST", "/api/prices", { prices: { PMG: 260 }, broadcast: false }), "our price");
  ok(await call("POST", "/api/competitors", { name: "Shell Model Town", product: "PMG", price: 258 }), "add comp");
  const r = ok(await call("GET", "/api/competitors"), "list");
  assert.equal(r.ours.PMG, 260);
  const row = r.competitors.find((c: any) => c.name === "Shell Model Town");
  assert.ok(row && row.price === 258);
});

test("khata security: cheque + guarantor held, then marked deposited", async () => {
  const c = ok(await call("POST", "/api/customers", { name: "Secured Co", type: "fleet", phone: "03005550001", credit_limit: 500000 }), "cust");
  const g = ok(await call("POST", `/api/customers/${c.id}/guarantees`, { kind: "cheque", bank: "HBL", cheque_no: "A-123", amount: 200000, cheque_date: "2026-12-31" }), "cheque");
  assert.equal(g.status, "held");
  ok(await call("POST", `/api/customers/${c.id}/guarantees`, { kind: "guarantor", guarantor_name: "Malik Sahib", guarantor_phone: "03001234567" }), "guarantor");
  const list = ok(await call("GET", `/api/customers/${c.id}/guarantees`), "list");
  assert.equal(list.length, 2);
  const upd = ok(await call("PATCH", `/api/guarantees/${g.id}`, { status: "deposited" }), "mark deposited");
  assert.equal(upd.status, "deposited");
  // cheque needs a number; guarantor needs a name
  assert.equal((await call("POST", `/api/customers/${c.id}/guarantees`, { kind: "cheque" })).status, 400);
});

test("discounts & overrides report + books still tally", async () => {
  const { tallyBooks } = await import("./helpers/tally.js");
  const c = ok(await call("POST", "/api/customers", { name: "Disc Audit Co", type: "fleet", phone: "03006660001", credit_limit: 500000 }), "cust");
  ok(await call("POST", "/api/sales", { station_id: 1, product: "PMG", litres: 100, payment_method: "khata", customer_id: c.id, discount: 400 }), "disc sale");
  const rep = ok(await call("GET", "/api/reports/discounts"), "report");
  assert.ok(rep.discount_total >= 400, "discount counted");
  assert.ok(rep.by_person.length >= 1, "by person");
  await tallyBooks("batch-a");
});
