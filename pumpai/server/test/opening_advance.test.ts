/** Opening balance as DUE or ADVANCE for khata customers and wholesale clients; sales eat the advance first. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-adv-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server; let base = "";
const tokens: Record<string, string> = {};
async function call(who: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tokens[who] ? { authorization: `Bearer ${tokens[who]}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, msg: string) => { assert.equal(r.status, 200, `${msg}: ${JSON.stringify(r.data)}`); return r.data; };
const near = (a: number, b: number, msg?: string) => assert.ok(Math.abs(a - b) < 0.05, `${msg ?? ""} ${a} ≈ ${b}`);

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server?.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("khata customer opened with an advance: minus balance, visible at POS, sale eats it, then limit applies", async () => {
  const c = ok(await call("admin", "POST", "/api/customers", { name: "Advance Wala", phone: "03001234999", type: "retail", credit_limit: 0, opening_balance: 5000, opening_kind: "advance" }), "create");
  const got = ok(await call("admin", "GET", `/api/customers/${c.id}`), "read");
  near(got.balance ?? got.customer?.balance, -5000, "balance");
  const st = ok(await call("admin", "GET", `/api/customers/${c.id}/statement`), "statement");
  near(st.closing_balance, -5000);
  // the POS lists the account although it has no credit limit, with the advance shown
  const list = ok(await call("salesman", "GET", "/api/pos/khata-accounts"), "pos list");
  const acc = (list.accounts ?? list).find((a: any) => a.id === c.id);
  assert.ok(acc, "account at POS"); assert.equal(acc.status, "ok"); near(acc.advance, 5000);
  const open = ok(await call("salesman", "POST", "/api/shifts/open", {}), "open shift");
  const st1 = open.station_id;
  const sale = ok(await call("salesman", "POST", "/api/sales", { station_id: st1, product: "PMG", litres: 5, payment_method: "khata", customer_id: c.id }), "khata sale");
  const after1 = ok(await call("admin", "GET", `/api/customers/${c.id}`), "after sale");
  near(after1.balance ?? after1.customer?.balance, -5000 + sale.amount, "advance reduced by the sale");
  // a sale bigger than what is left of the advance (no credit limit) is refused with an advance message
  const big = await call("salesman", "POST", "/api/sales", { station_id: st1, product: "PMG", litres: 200, payment_method: "khata", customer_id: c.id });
  assert.equal(big.status, 400); assert.match(big.data.error ?? "", /Advance khatam/);
  // non-admin cannot bring an opening balance
  const no = await call("manager", "POST", "/api/customers", { name: "Nope", phone: "03001234998", type: "retail", opening_balance: 100, opening_kind: "due" });
  assert.equal(no.status, 403);
  // due works as before
  const d = ok(await call("admin", "POST", "/api/customers", { name: "Due Wala", phone: "03001234997", type: "retail", credit_limit: 50000, opening_balance: 1200, opening_kind: "due" }), "due");
  near((ok(await call("admin", "GET", `/api/customers/${d.id}`), "read")).balance ?? 0, 1200);
});

test("wholesale client opened with an advance: due is negative, supply reduces it, statement shows the advance", async () => {
  const c = ok(await call("admin", "POST", "/api/wholesale/clients", { name: "Adv Client", phone: "03007778888", opening_balance: 100000, opening_kind: "advance", rates: { HSD: { mode: "fixed", rate: 250 } } }), "client");
  near(c.opening_balance, -100000); near(c.due, -100000);
  const st = ok(await call("admin", "GET", "/api/stations"), "stations")[0].id;
  const sup = ok(await call("admin", "POST", `/api/wholesale/clients/${c.id}/supply`, { station_id: st, product: "HSD", litres: 100 }), "supply");
  near(sup.due_after, -100000 + 25000, "advance after supply");
  const stmt = ok(await call("admin", "GET", `/api/wholesale/clients/${c.id}/statement`), "statement");
  near(stmt.opening_balance, -100000); near(stmt.closing_balance, -75000);
  // switching the kind on edit flips the sign; a plain signed number still works
  const e = ok(await call("admin", "PATCH", `/api/wholesale/clients/${c.id}`, { opening_balance: 40000, opening_kind: "due" }), "edit");
  near(e.opening_balance, 40000);
});
