import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-rep-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server;
let base = "";
const tokens: Record<string, string> = {};
async function call(who: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tokens[who] ? { authorization: `Bearer ${tokens[who]}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const DAY = 86_400_000;
const range = (days: number, endAgo = 0) => `from=${new Date(Date.now() - (days + endAgo) * DAY).toISOString()}&to=${new Date(Date.now() - endAgo * DAY).toISOString()}`;

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman", "wholesale"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("reports are for admin and manager only", async () => {
  assert.equal((await call("admin", "GET", "/api/reports")).status, 200);
  assert.equal((await call("manager", "GET", "/api/reports")).status, 200);
  assert.equal((await call("salesman", "GET", "/api/reports")).status, 403);
  assert.equal((await call("wholesale", "GET", "/api/reports")).status, 403);
});

test("every preset period returns all sections with the right grouping", async () => {
  for (const [days, grain] of [[1, "hour"], [7, "day"], [30, "day"], [182, "month"], [365, "month"]] as const) {
    const r = (await call("manager", "GET", `/api/reports?${range(days)}`)).data;
    assert.equal(r.grain, grain, `${days}d`);
    for (const k of ["summary", "trend", "sales", "stock", "expenses", "khata", "wholesale", "shifts", "receivables", "payables"]) assert.ok(k in r, k);
    assert.ok(r.summary.revenue > 0);
  }
  assert.equal((await call("admin", "GET", `/api/reports?from=${new Date().toISOString()}&to=${new Date(Date.now() - DAY).toISOString()}`)).status, 400);
  assert.equal((await call("admin", "GET", "/api/reports?from=yesterday")).status, 400);
});

test("stock movement reconciles and periods chain together", async () => {
  const r = (await call("admin", "GET", `/api/reports?${range(30)}`)).data;
  for (const p of r.stock.products) {
    assert.equal(Math.round(p.opening_l + p.received_l + p.returns_in_l - p.retail_sold_l - p.wholesale_out_l + p.dip_adjust_l - p.closing_l), 0, p.product);
    assert.ok(p.opening_l >= 0 && p.closing_l >= 0);
  }
  const a = (await call("admin", "GET", `/api/reports?${range(10, 10)}`)).data;
  const b = (await call("admin", "GET", `/api/reports?${range(10)}`)).data;
  a.stock.products.forEach((p: any, i: number) => assert.equal(p.closing_l, b.stock.products[i].opening_l));
});

test("a sale shows up in the 24h report: revenue, stock and money in", async () => {
  const before = (await call("admin", "GET", `/api/reports?${range(1)}`)).data;
  const st = (await call("admin", "GET", "/api/stations")).data[0];
  await call("admin", "POST", "/api/sales", { station_id: st.id, product: "PMG", litres: 100, payment_method: "cash" });
  const after = (await call("admin", "GET", `/api/reports?${range(1)}`)).data;
  const pmg = (r: any) => r.stock.products.find((p: any) => p.product === "PMG");
  assert.equal(Math.round(pmg(after).retail_sold_l - pmg(before).retail_sold_l), 100);
  assert.ok(after.summary.money_in.cash_sales > before.summary.money_in.cash_sales);
});

test("receivables and payables (as of now)", async () => {
  const r = (await call("manager", "GET", "/api/reports")).data;
  assert.ok(r.receivables.total > 0);
  assert.equal(Math.round(r.receivables.khata_total + r.receivables.wholesale_total + (r.receivables.carriage_total ?? 0)), Math.round(r.receivables.total));
  assert.equal(Math.round(r.receivables.aging.reduce((a: number, b: any) => a + b.amount, 0)), Math.round(r.receivables.total));
  assert.ok(r.receivables.list.every((x: any) => x.amount > 0));
  assert.ok(r.payables.list.some((x: any) => x.kind === "Supplier"));
  assert.ok(r.payables.pending_expenses.count >= 1);
});

test("supplier delivery creates a payable; paying it reduces what we owe", async () => {
  assert.equal((await call("salesman", "GET", "/api/suppliers")).status, 403);
  const sup = (await call("manager", "GET", "/api/suppliers")).data[0];
  const st = (await call("admin", "GET", "/api/stations")).data[0];
  const tank = st.tanks.find((t: any) => t.capacity_l - t.current_l > 2000);
  assert.ok(tank, "a tank with room");
  assert.equal((await call("manager", "POST", "/api/stock/delivery", { tank_id: tank.id, invoice_l: 1000, received_l: 998, supplier_id: sup.id })).status, 400, "rate required");
  assert.equal((await call("manager", "POST", "/api/stock/delivery", { tank_id: tank.id, invoice_l: 1000, received_l: 998, supplier_id: sup.id, purchase_rate: 250 })).status, 200);
  const afterDelivery = (await call("manager", "GET", `/api/suppliers/${sup.id}`)).data;
  assert.equal(Math.round(afterDelivery.owed - sup.owed), 250000);
  const paid = (await call("manager", "POST", `/api/suppliers/${sup.id}/payment`, { amount: 100000, method: "Bank transfer" })).data;
  assert.equal(Math.round(afterDelivery.owed - paid.owed), 100000);
  const added = (await call("manager", "POST", "/api/suppliers", { name: "Shell Depot Machike", opening_balance: 50000 })).data;
  assert.equal(added.owed, 50000);
});
