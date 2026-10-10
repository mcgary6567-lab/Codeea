/** Thekedar (carriage) collection: a thekedar who owes kiraya shows on the call-list and gets a throttled WhatsApp reminder. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-carr-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server, base = "", admin = "";
async function call(tok: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tok ? { authorization: `Bearer ${tok}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, m: string) => { assert.equal(r.status, 200, `${m}: ${JSON.stringify(r.data)}`); return r.data; };

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  admin = (await call("", "POST", "/api/auth/login", { email: "admin@pumpai.pk", password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("an owing thekedar lands on the call-list and gets one throttled reminder", async () => {
  const depot = ok(await call(admin, "GET", "/api/suppliers"), "sup")[0];
  const k = ok(await call(admin, "POST", "/api/carriage/thekedars", { name: "Collection Test Thekedar", phone: "03009998877" }), "new thekedar");
  // bill kiraya, no payment → he owes
  ok(await call(admin, "POST", `/api/carriage/thekedars/${k.id}/carriage`, { supplier_id: depot.id, lines: [{ product: "PMG", litres: 1000 }], amount: 5000, notify: false }), "bill kiraya");

  const list = ok(await call(admin, "GET", "/api/carriage/call-list"), "call list");
  const row = list.thekedars.find((x: any) => x.id === k.id);
  assert.ok(row, "the thekedar is on the call-list");
  assert.equal(Math.round(row.due), 5000, "due equals the kiraya billed");
  assert.equal(row.overdue, true, "no payment ever → overdue");
  assert.ok(list.totals.due >= 5000, "totals include his due");

  // the reminder job messages him once, then is throttled for 5 days
  const { carriageReminders } = await import("../src/routes/carriage.js");
  const t = k.tenant_id ?? 1;
  const first = await carriageReminders(t);
  assert.ok(first >= 1, "at least one reminder sent");
  const second = await carriageReminders(t);
  assert.equal(second, 0, "a second run within 5 days sends nothing (throttled)");

  // once he pays, he drops off the call-list
  ok(await call(admin, "POST", `/api/carriage/thekedars/${k.id}/payment`, { amount: 5000, method: "Cash", notify: false }), "pay kiraya");
  const list2 = ok(await call(admin, "GET", "/api/carriage/call-list"), "call list 2");
  assert.ok(!list2.thekedars.find((x: any) => x.id === k.id), "paid thekedar is off the list");
});
