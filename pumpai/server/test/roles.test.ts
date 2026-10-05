import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-roles-"));
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
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
async function loginAs(who: string, email: string, password = "demo1234") {
  const r = await call("", "POST", "/api/auth/login", { email, password });
  if (r.status === 200) tokens[who] = r.data.token;
  return r;
}

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const [who, email] of [["admin", "admin@pumpai.pk"], ["manager", "manager@pumpai.pk"], ["salesman", "salesman@pumpai.pk"]])
    assert.equal((await loginAs(who, email)).status, 200, who);
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("/me returns role-specific permissions", async () => {
  const a = (await call("admin", "GET", "/api/me")).data;
  const m = (await call("manager", "GET", "/api/me")).data;
  const s = (await call("salesman", "GET", "/api/me")).data;
  assert.equal(a.user.role, "admin");
  assert.ok(a.permissions.includes("users.manage") && a.permissions.includes("settings.manage"));
  assert.ok(m.permissions.includes("whatsapp.inbox") && !m.permissions.includes("users.manage"));
  assert.deepEqual(s.permissions.sort(), ["customers.create", "customers.view", "prices.view", "sales.create", "sales.view", "shifts.expenses", "shifts.manage"].sort());
  assert.ok(s.user.station_name);
});

const MGMT_ONLY = ["/api/dashboard", "/api/khata", "/api/orders", "/api/complaints", "/api/campaigns", "/api/stock", "/api/alerts", "/api/automations", "/api/whatsapp/conversations"];

test("salesman is blocked from management screens", async () => {
  for (const url of [...MGMT_ONLY, "/api/users", "/api/settings"]) assert.equal((await call("salesman", "GET", url)).status, 403, url);
  assert.equal((await call("salesman", "POST", "/api/prices", { prices: { PMG: 1 } })).status, 403);
  assert.equal((await call("salesman", "POST", "/api/ai/ask", { question: "sales today?" })).status, 403);
  assert.equal((await call("salesman", "POST", "/api/whatsapp/simulate", { phone: "03001231234", text: "hi" })).status, 403);
});

test("manager runs operations but cannot manage users, settings or credit limits", async () => {
  for (const url of MGMT_ONLY) assert.equal((await call("manager", "GET", url)).status, 200, url);
  assert.equal((await call("manager", "GET", "/api/users")).status, 403);
  assert.equal((await call("manager", "GET", "/api/settings")).status, 403);
  const c = (await call("manager", "GET", "/api/khata")).data[0];
  assert.equal((await call("manager", "PATCH", `/api/customers/${c.id}`, { credit_limit: c.credit_limit + 100000 })).status, 403);
  assert.equal((await call("admin", "PATCH", `/api/customers/${c.id}`, { credit_limit: c.credit_limit + 100000 })).status, 200);
});

test("salesman is locked to their own station and shift", async () => {
  const stations = (await call("salesman", "GET", "/api/stations")).data;
  assert.equal(stations.length, 1);
  const own = stations[0].id;
  const all = (await call("admin", "GET", "/api/stations")).data;
  const other = all.find((s: any) => s.id !== own).id;
  assert.equal((await call("salesman", "POST", "/api/sales", { station_id: other, product: "PMG", amount: 1000, payment_method: "cash" })).status, 403);
  assert.equal((await call("salesman", "POST", "/api/sales", { station_id: own, product: "PMG", amount: 1000, payment_method: "cash" })).status, 400, "needs open shift");
  // shift: attendant name forced to the salesman
  const shift = await call("salesman", "POST", "/api/shifts/open", { station_id: own, attendant: "Someone Else" });
  assert.equal(shift.status, 200);
  assert.equal(shift.data.attendant, "Imran");
  const sale = await call("salesman", "POST", "/api/sales", { station_id: own, product: "PMG", amount: 1000, payment_method: "cash" });
  assert.equal(sale.status, 200);
  assert.equal(sale.data.shift_id, shift.data.id);
  const sales = (await call("salesman", "GET", "/api/sales")).data;
  assert.ok(sales.every((s: any) => s.station_id === own));
  const shifts = (await call("salesman", "GET", "/api/shifts")).data;
  assert.ok(shifts.every((s: any) => s.attendant === "Imran" && s.station_id === own));
  // cannot close a shift belonging to someone else
  const mgrShift = (await call("manager", "POST", "/api/shifts/open", { station_id: other, attendant: "Asghar" })).data;
  assert.equal((await call("salesman", "POST", `/api/shifts/${mgrShift.id}/close`, { readings: {}, cash_actual: 0 })).status, 403);
});

test("salesman can add a walk-in customer but not give credit", async () => {
  assert.equal((await call("salesman", "POST", "/api/customers", { name: "Walk In", phone: "03451112222" })).status, 200);
  assert.equal((await call("salesman", "POST", "/api/customers", { name: "Credit Try", phone: "03451113333", credit_limit: 50000 })).status, 403);
  const c = (await call("salesman", "GET", "/api/customers?q=Walk")).data[0];
  assert.equal((await call("salesman", "PATCH", `/api/customers/${c.id}`, { name: "Changed" })).status, 403);
  assert.equal((await call("salesman", "POST", `/api/customers/${c.id}/khata`, { type: "credit", amount: 100 })).status, 403);
});

test("admin creates, edits, disables and deletes users", async () => {
  const stations = (await call("admin", "GET", "/api/stations")).data;
  assert.equal((await call("admin", "POST", "/api/users", { name: "No Station", email: "x@pumpai.pk", password: "secret123", role: "salesman" })).status, 400);
  const created = await call("admin", "POST", "/api/users", { name: "Naveed", email: "Naveed@PumpAI.pk", password: "secret123", role: "salesman", station_id: stations[1].id });
  assert.equal(created.status, 200);
  assert.equal(created.data.email, "naveed@pumpai.pk");
  assert.equal((await loginAs("naveed", "naveed@pumpai.pk", "secret123")).status, 200);
  const st = (await call("naveed", "GET", "/api/stations")).data;
  assert.equal(st[0].id, stations[1].id);
  // promote to manager
  const promoted = await call("admin", "PATCH", `/api/users/${created.data.id}`, { role: "manager" });
  assert.equal(promoted.data.role, "manager");
  // a role change signs the person out; the next sign-in has the new rights
  assert.equal((await call("naveed", "GET", "/api/dashboard")).status, 401);
  assert.equal((await loginAs("naveed", "naveed@pumpai.pk", "secret123")).status, 200);
  assert.equal((await call("naveed", "GET", "/api/dashboard")).status, 200);
  // disable: existing token and new logins stop working
  await call("admin", "PATCH", `/api/users/${created.data.id}`, { active: false });
  assert.equal((await call("naveed", "GET", "/api/dashboard")).status, 401);
  assert.equal((await loginAs("naveed2", "naveed@pumpai.pk", "secret123")).status, 403);
  // reset password + re-enable
  await call("admin", "PATCH", `/api/users/${created.data.id}`, { active: true, password: "newpass123" });
  assert.equal((await loginAs("naveed3", "naveed@pumpai.pk", "newpass123")).status, 200);
  assert.equal((await call("admin", "DELETE", `/api/users/${created.data.id}`)).status, 200);
  assert.equal((await call("admin", "POST", "/api/users", { name: "Dup", email: "manager@pumpai.pk", password: "secret123", role: "manager" })).status, 400);
});

test("the last admin cannot be demoted, disabled or delete themselves", async () => {
  const me = (await call("admin", "GET", "/api/me")).data.user;
  assert.equal((await call("admin", "PATCH", `/api/users/${me.id}`, { role: "manager" })).status, 400);
  assert.equal((await call("admin", "PATCH", `/api/users/${me.id}`, { active: false })).status, 400);
  assert.equal((await call("admin", "DELETE", `/api/users/${me.id}`)).status, 400);
});
