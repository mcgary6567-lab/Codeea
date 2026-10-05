/** Audit log of every change (with before → after) and the machines register. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-audit-"));
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
  const text = await res.text();
  let data: any = text; try { data = JSON.parse(text); } catch { /* csv */ }
  return { status: res.status, data };
}
const ok = (r: { status: number; data: any }, msg: string) => { assert.equal(r.status, 200, `${msg}: ${JSON.stringify(r.data).slice(0, 300)}`); return r.data; };

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("audit log: price change old → new, edit with before/after, delete, undo, sign-ins; admin only", async () => {
  await call("", "POST", "/api/auth/login", { email: "manager@pumpai.pk", password: "wrong-password" });
  ok(await call("salesman", "POST", "/api/shifts/open", {}), "shift");
  const sale = ok(await call("salesman", "POST", "/api/sales", { station_id: 1, product: "PMG", amount: 500, payment_method: "cash" }), "sale");
  ok(await call("salesman", "POST", `/api/sales/${sale.id}/undo`, {}), "undo");
  const old = db.get("SELECT price FROM prices WHERE product='HSD' ORDER BY effective_from DESC, id DESC LIMIT 1").price;
  ok(await call("manager", "POST", "/api/prices", { prices: { HSD: old + 2 } }), "price");
  const cust = db.get("SELECT * FROM customers WHERE credit_limit > 0 LIMIT 1");
  ok(await call("admin", "PATCH", `/api/customers/${cust.id}`, { credit_limit: cust.credit_limit + 50000 }), "edit");
  const e = ok(await call("manager", "POST", "/api/expenses", { category: "Tea & food", amount: 300, paid_to: "Chai wala" }), "expense");
  ok(await call("admin", "DELETE", `/api/expenses/${e.id}`), "delete");

  const all = ok(await call("admin", "GET", "/api/audit"), "audit");
  const price = all.rows.find((r: any) => r.kind === "price" && r.label === "Changed fuel prices");
  assert.equal(price.user, "Kamran Shah");
  assert.deepEqual(price.data.changes[0], { field: "Diesel (HSD)", from: old, to: old + 2 });
  const edit = all.rows.find((r: any) => r.kind === "edit" && r.ref === `PATCH /customers/${cust.id}`);
  assert.ok(edit.label.includes(cust.name));
  assert.deepEqual(edit.data.changes.find((c: any) => c.field === "credit_limit"), { field: "credit_limit", from: cust.credit_limit, to: cust.credit_limit + 50000 });
  const del = all.rows.find((r: any) => r.kind === "delete" && r.ref === `DELETE /expenses/${e.id}`);
  assert.equal(del.data.deleted.amount, 300);
  assert.ok(all.rows.some((r: any) => r.kind === "undo" && r.user === "Imran"));
  assert.ok(!all.rows.some((r: any) => r.ref === "POST /sales"), "normal sales are not audit noise");
  const logins = ok(await call("admin", "GET", "/api/audit?kind=login"), "logins").rows;
  assert.ok(logins.some((r: any) => r.label === "Wrong password" && r.user === "Kamran Shah"));
  assert.ok(logins.some((r: any) => r.label === "Signed in (password)"));
  assert.ok(ok(await call("admin", "GET", "/api/audit?kind=price"), "prices").rows.every((r: any) => r.kind === "price"));
  assert.ok(ok(await call("admin", "GET", "/api/audit?q=Chai"), "search").rows.length >= 1);
  const csv = await call("admin", "GET", "/api/audit.csv?kind=edit");
  assert.match(csv.data, /credit_limit: \d+ → \d+/);
  assert.equal((await call("manager", "GET", "/api/audit")).status, 403);
  // secrets never stored
  ok(await call("admin", "PUT", "/api/integrations", { anthropic_key: "sk-ant-secret-9999" }), "key");
  ok(await call("admin", "PUT", "/api/integrations", { anthropic_key: "" }), "key off");
  assert.equal(db.get("SELECT COUNT(*) n FROM audit_log WHERE data LIKE '%sk-ant-secret%'").n, 0);
});

test("machines: register, service resets the schedule and books the expense, salesman reports a fault, repair closes it", async () => {
  const list = ok(await call("manager", "GET", "/api/machines"), "list");
  assert.ok(list.summary.total >= 8);
  const comp = list.machines.find((m: any) => m.type === "compressor");
  assert.equal(comp.status, "faulty"); assert.equal(comp.open_faults, 1);
  const gen = list.machines.find((m: any) => m.name === "Generator 30 kVA");
  assert.equal(gen.due.hours_left, 250 - (4268 - 4050));
  const m = ok(await call("manager", "POST", "/api/machines", { name: "Test fan", type: "fan", station_id: 1, service_every_days: 30, last_service_on: db.pkDate(Date.now() - 31 * 86_400_000), warranty_until: db.pkDate(Date.now() + 7 * 86_400_000) }), "add");
  assert.equal(m.due.service, "overdue"); assert.equal(m.due.warranty, "ending");
  const { runJob } = await import("../src/automation/scheduler.js");
  assert.match(await runJob(1, "machine_watch"), /[1-9]\d* machines need attention/);
  assert.ok(db.get("SELECT id FROM alerts WHERE type='machine_service' AND title LIKE '%Test fan%'"));
  assert.ok(db.get("SELECT id FROM alerts WHERE type='machine_fault' AND title LIKE '%Air compressor%still not repaired%'"), "fault open 3 days");
  ok(await call("manager", "POST", `/api/machines/${m.id}/logs`, { kind: "service", description: "Cleaned and oiled", cost: 800, done_by: "Electrician Akram" }), "service");
  const after1 = ok(await call("manager", "GET", `/api/machines/${m.id}`), "detail");
  assert.equal(after1.due.service, "ok"); assert.equal(after1.next_service_on, db.pkDate(Date.now() + 30 * 86_400_000));
  assert.ok(db.get("SELECT id FROM expenses WHERE id=? AND category='Maintenance & repairs' AND amount=800", after1.logs[0].expense_id));
  // salesman: can report a fault, cannot record a service
  assert.equal((await call("salesman", "POST", `/api/machines/${m.id}/logs`, { kind: "service" })).status, 403);
  ok(await call("salesman", "POST", `/api/machines/${m.id}/logs`, { kind: "fault", description: "Fan making noise and slow" }), "fault");
  assert.equal(ok(await call("manager", "GET", `/api/machines/${m.id}`), "d").status, "faulty");
  assert.ok(db.get("SELECT id FROM notifications WHERE type='machine_fault' AND title LIKE '%Test fan%'"));
  ok(await call("manager", "POST", `/api/machines/${m.id}/logs`, { kind: "repair", description: "Capacitor replaced", cost: 450, downtime_hours: 5 }), "repair");
  const d2 = ok(await call("manager", "GET", `/api/machines/${m.id}`), "d2");
  assert.equal(d2.status, "working"); assert.equal(d2.open_faults, 0); assert.equal(d2.spent_total, 1250); assert.equal(d2.downtime_hours, 5);
  // generator hours: service resets the hours counter
  ok(await call("manager", "POST", `/api/machines/${gen.id}/logs`, { kind: "service", hours: 4300, cost: 15000 }), "gen service");
  assert.equal(ok(await call("manager", "GET", `/api/machines/${gen.id}`), "gen").due.hours_left, 250);
  ok(await call("manager", "PATCH", `/api/machines/${m.id}`, { location: "Tuck shop" }), "edit");
  assert.ok(ok(await call("admin", "GET", "/api/audit?kind=edit"), "audit").rows.some((r: any) => r.data.changes.some((c: any) => c.field === "location" && c.to === "Tuck shop")));
  assert.equal((await call("manager", "DELETE", `/api/machines/${m.id}`)).status, 403);
  ok(await call("admin", "DELETE", `/api/machines/${m.id}`), "delete");
});
