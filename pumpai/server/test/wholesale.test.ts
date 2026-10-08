import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-ws-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server;
let base = "";
const tokens: Record<string, string> = {};
async function call(who: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, {
    method, headers: { "content-type": "application/json", ...(tokens[who] ? { authorization: `Bearer ${tokens[who]}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data: any = text;
  try { data = JSON.parse(text); } catch { /* csv */ }
  return { status: res.status, data };
}

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman", "wholesale"]) {
    const r = await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" });
    assert.equal(r.status, 200, who);
    tokens[who] = r.data.token;
  }
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("wholesale module access is separate", async () => {
  assert.equal((await call("wholesale", "GET", "/api/wholesale/clients")).status, 200);
  assert.equal((await call("admin", "GET", "/api/wholesale/clients")).status, 200);
  assert.equal((await call("manager", "GET", "/api/wholesale/clients")).status, 403);
  assert.equal((await call("salesman", "GET", "/api/wholesale/summary")).status, 403);
  // wholesale officer sees nothing else
  for (const url of ["/api/dashboard", "/api/customers", "/api/expenses", "/api/users", "/api/sales", "/api/shifts", "/api/prices", "/api/orders"]) assert.equal((await call("wholesale", "GET", url)).status, 403, url);
  const me = (await call("wholesale", "GET", "/api/me")).data;
  assert.deepEqual(me.permissions.sort(), ["carriage.manage", "carriage.view", "wholesale.manage", "wholesale.view"]);
});

test("only admin sets per-client rates and credit limits", async () => {
  assert.equal((await call("wholesale", "POST", "/api/wholesale/clients", { name: "XY Traders", rates: { HSD: 250 } })).status, 403);
  const c = (await call("wholesale", "POST", "/api/wholesale/clients", { name: "Khan Filling Point", phone: "03001119999" })).data;
  assert.equal((await call("wholesale", "PUT", `/api/wholesale/clients/${c.id}/rates`, { rates: { HSD: 250 } })).status, 403);
  // no rate yet -> supply refused
  const st = (await call("admin", "GET", "/api/stations")).data[0];
  const noRate = await call("wholesale", "POST", `/api/wholesale/clients/${c.id}/supply`, { station_id: st.id, product: "HSD", litres: 100 });
  assert.equal(noRate.status, 400);
  assert.match(noRate.data.error, /rate/i);
  assert.equal((await call("admin", "PUT", `/api/wholesale/clients/${c.id}/rates`, { rates: { HSD: 250, PMG: 245 } })).status, 200);
  assert.equal((await call("admin", "PATCH", `/api/wholesale/clients/${c.id}`, { credit_limit: 100000 })).status, 200);
  const detail = (await call("wholesale", "GET", `/api/wholesale/clients/${c.id}`)).data;
  assert.deepEqual(detail.rates, { HSD: 250, PMG: 245 });
  assert.equal(detail.rate_history.length, 2);
});

test("supply / return / payment keep stock and dues in sync, statement balances", async () => {
  const c = (await call("wholesale", "GET", "/api/wholesale/clients?q=Khan")).data[0];
  const st = (await call("admin", "GET", "/api/stations")).data[0];
  const tankBefore = st.tanks.find((t: any) => t.product === "HSD").current_l;

  // officer cannot override the rate
  assert.equal((await call("wholesale", "POST", `/api/wholesale/clients/${c.id}/supply`, { station_id: st.id, product: "HSD", litres: 100, rate: 200 })).status, 403);
  const s1 = await call("wholesale", "POST", `/api/wholesale/clients/${c.id}/supply`, { station_id: st.id, product: "HSD", litres: 300, vehicle_no: "TLR-1" });
  assert.equal(s1.status, 200);
  assert.equal(s1.data.amount, 75000);
  assert.equal(s1.data.due_after, 75000);
  // credit limit 100,000: next 200 L (50,000) would exceed
  const over = await call("wholesale", "POST", `/api/wholesale/clients/${c.id}/supply`, { station_id: st.id, product: "HSD", litres: 200 });
  assert.equal(over.status, 400);
  assert.match(over.data.error, /Credit limit/);
  // return 40 L at the supply rate
  const r = await call("wholesale", "POST", `/api/wholesale/clients/${c.id}/return`, { station_id: st.id, product: "HSD", litres: 40 });
  assert.equal(r.data.amount, 10000);
  assert.equal(r.data.due_after, 65000);
  const p = await call("wholesale", "POST", `/api/wholesale/clients/${c.id}/payment`, { amount: 50000, method: "Bank transfer", ref: "TRX-9" });
  assert.equal(p.data.due_after, 15000);
  // stock moved by net 260 L
  const tankAfter = (await call("admin", "GET", "/api/stations")).data[0].tanks.find((t: any) => t.product === "HSD").current_l;
  assert.equal(Math.round(tankBefore - tankAfter), 260);

  const stmt = (await call("wholesale", "GET", `/api/wholesale/clients/${c.id}/statement`)).data;
  assert.equal(stmt.closing_balance, 15000);
  assert.equal(stmt.lines.at(-1).balance, 15000);
  const hsd = stmt.summary.by_product.find((x: any) => x.product === "HSD");
  assert.equal(hsd.supplied_l, 300);
  assert.equal(hsd.returned_l, 40);
  assert.equal(stmt.summary.received, 50000);

  const csv = await call("wholesale", "GET", `/api/wholesale/clients/${c.id}/statement.csv`);
  assert.equal(csv.status, 200);
  assert.match(csv.data, /Closing balance/);
});

test("admin voids a wrong supply: due and stock are reversed", async () => {
  const c = (await call("wholesale", "GET", "/api/wholesale/clients?q=Khan")).data[0];
  const st = (await call("admin", "GET", "/api/stations")).data[0];
  const s = (await call("wholesale", "POST", `/api/wholesale/clients/${c.id}/supply`, { station_id: st.id, product: "PMG", litres: 100 })).data;
  assert.equal(s.due_after, 15000 + 24500);
  assert.equal((await call("wholesale", "POST", `/api/wholesale/txns/${s.id}/void`, { reason: "wrong client" })).status, 403);
  const tankMid = (await call("admin", "GET", "/api/stations")).data[0].tanks.find((t: any) => t.id === s.tank_id).current_l;
  const v = await call("admin", "POST", `/api/wholesale/txns/${s.id}/void`, { reason: "wrong client" });
  assert.equal(v.data.due_after, 15000);
  const tankAfter = (await call("admin", "GET", "/api/stations")).data[0].tanks.find((t: any) => t.id === s.tank_id).current_l;
  assert.equal(Math.round(tankAfter - tankMid), 100);
  // adjustment needs admin + reason
  assert.equal((await call("wholesale", "POST", `/api/wholesale/clients/${c.id}/adjustment`, { amount: -500, note: "discount" })).status, 403);
  assert.equal((await call("admin", "POST", `/api/wholesale/clients/${c.id}/adjustment`, { amount: -500, note: "Rounding discount" })).data.due_after, 14500);
});

test("wholesale summary totals", async () => {
  const s = (await call("wholesale", "GET", "/api/wholesale/summary")).data;
  assert.ok(s.clients >= 4);
  assert.ok(s.total_due > 0);
  assert.ok(s.recent.length > 0);
});

test("expenses: manager entries over the limit need admin approval", async () => {
  assert.equal((await call("salesman", "GET", "/api/expenses")).status, 403);
  const cats = (await call("manager", "GET", "/api/expense-categories")).data;
  assert.ok(cats.categories.length >= 10);
  assert.equal(cats.approval_limit, 10000);
  const small = (await call("manager", "POST", "/api/expenses", { category: "Tea & food", amount: 1500, paid_to: "Hotel" })).data;
  assert.equal(small.status, "approved");
  const big = (await call("manager", "POST", "/api/expenses", { category: "Maintenance & repairs", amount: 45000, note: "Compressor" })).data;
  assert.equal(big.status, "pending");
  assert.equal((await call("manager", "POST", `/api/expenses/${big.id}/approve`)).status, 403);
  const before = (await call("admin", "GET", "/api/expenses")).data.summary.total;
  assert.equal((await call("admin", "POST", `/api/expenses/${big.id}/approve`)).data.status, "approved");
  const after = (await call("admin", "GET", "/api/expenses")).data.summary.total;
  assert.equal(Math.round(after - before), 45000);
  // admin entries are approved immediately; manager cannot delete approved
  const adm = (await call("admin", "POST", "/api/expenses", { category: "Rent", amount: 250000 })).data;
  assert.equal(adm.status, "approved");
  assert.equal((await call("manager", "DELETE", `/api/expenses/${small.id}`)).status, 403);
  assert.equal((await call("admin", "DELETE", `/api/expenses/${small.id}`)).status, 200);
  assert.equal((await call("manager", "POST", "/api/expenses", { category: "Nope", amount: 10 })).status, 400);
});

test("expense summary, budgets, categories and CSV", async () => {
  const r = (await call("admin", "GET", "/api/expenses")).data;
  assert.ok(r.summary.revenue.total > 0);
  assert.equal(r.summary.revenue_minus_expenses, Math.round((r.summary.revenue.total - r.summary.total) * 100) / 100);
  assert.ok(r.summary.by_category.some((c: any) => c.budget));
  assert.equal((await call("manager", "POST", "/api/expense-categories", { name: "Fuel testing" })).status, 403);
  assert.equal((await call("admin", "POST", "/api/expense-categories", { name: "Fuel testing", monthly_budget: 5000 })).status, 200);
  assert.equal((await call("admin", "PUT", "/api/expenses/settings", { approval_limit: 20000 })).status, 200);
  const csv = await call("manager", "GET", "/api/expenses.csv");
  assert.equal(csv.status, 200);
  assert.match(csv.data, /Category/);
  const ask = await call("admin", "POST", "/api/ai/ask", { question: "expenses?" });
  assert.equal(ask.status, 200);
});

test("after all of the above: every book still tallies with the ledger", async () => {
  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("wholesale");
});

// runs last: a bulk change touches every client's rate, so keep it after the order-dependent tests
test("bulk rate change: +Rs applies to every client's fixed rate at once (admin only)", async () => {
  const a = (await call("admin", "POST", "/api/wholesale/clients", { name: "Bulk A", phone: "03007778888", rates: { HSD: 250, PMG: 245 } })).data;
  const b = (await call("admin", "POST", "/api/wholesale/clients", { name: "Bulk B", rates: { HSD: 252 } })).data;
  // a wholesale officer cannot bulk-change rates
  assert.equal((await call("wholesale", "POST", "/api/wholesale/rates/bulk", { deltas: { HSD: 2 } })).status, 403);
  // diesel up Rs 2 across all clients, and the client with a phone is notified on WhatsApp
  const r = await call("admin", "POST", "/api/wholesale/rates/bulk", { deltas: { HSD: 2 }, note: "price up" });
  assert.equal(r.status, 200, JSON.stringify(r.data));
  assert.ok(r.data.changed.HSD.updated >= 2, "at least both clients updated");
  assert.ok(r.data.notified >= 1, "at least the client with a phone was notified");
  assert.equal((await call("admin", "GET", `/api/wholesale/clients/${a.id}`)).data.rates.HSD, 252);
  assert.equal((await call("admin", "GET", `/api/wholesale/clients/${b.id}`)).data.rates.HSD, 254);
  // A's petrol untouched, and the meter/retail price is not part of this
  assert.equal((await call("admin", "GET", `/api/wholesale/clients/${a.id}`)).data.rates.PMG, 245);
  // a drop of Rs 1.5 works too
  assert.equal((await call("admin", "POST", "/api/wholesale/rates/bulk", { deltas: { HSD: -1.5 } })).status, 200);
  assert.equal((await call("admin", "GET", `/api/wholesale/clients/${a.id}`)).data.rates.HSD, 250.5);
  // the consolidated rate-change log lists these changes across clients (admin only)
  assert.equal((await call("wholesale", "GET", "/api/wholesale/rate-history")).status, 403);
  const hist = (await call("admin", "GET", "/api/wholesale/rate-history?limit=50")).data;
  assert.ok(hist.some((h: any) => h.client_name === "Bulk A" && h.product === "HSD" && h.new_rate === 250.5), "log has A's latest HSD change");
  assert.ok(hist.some((h: any) => h.client_name === "Bulk B"), "log includes other clients' changes too");
});
