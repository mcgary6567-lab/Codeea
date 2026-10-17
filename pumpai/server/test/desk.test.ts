import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-desk-"));
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
const near = (a: number, b: number, msg?: string) => assert.ok(Math.abs(a - b) < 0.05, `${msg ?? ""} ${a} ≈ ${b}`);
// money from litres stored to 2 decimals can differ by a few paisa
const nearRs = (a: number, b: number, msg?: string) => assert.ok(Math.abs(a - b) < 1, `${msg ?? ""} Rs ${a} ≈ ${b}`);

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => server?.close());

test("manager desk: live shifts, staff today and ranked suggestions with links", async () => {
  await call("salesman", "POST", "/api/shifts/open", {});
  const r = await call("manager", "GET", "/api/dashboard/desk");
  assert.equal(r.status, 200, JSON.stringify(r.data));
  const d = r.data;
  assert.ok(d.shifts.some((s: any) => s.attendant === "Imran"));
  assert.ok(d.staff.length > 0 && d.staff.every((s: any) => ["present", "late", "missing", "due", "leave", "off"].includes(s.status)));
  assert.equal(d.staff_summary.total, d.staff.length);
  const order = { critical: 0, warning: 1, info: 2, good: 3 } as Record<string, number>;
  assert.ok(d.suggestions.every((s: any, i: number, a: any[]) => i === 0 || order[a[i - 1].level] <= order[s.level]), "most urgent first");
  assert.ok(d.suggestions.every((s: any) => !s.to || s.to.startsWith("/")));
  // a pending price change is flagged as urgent
  const db = await import("../src/db.js");
  db.setSetting(1, "price_approval", "1");
  const req = await call("manager", "POST", "/api/prices", { prices: { PMG: 300 } });
  assert.equal(req.data.pending, true, JSON.stringify(req.data));
  const again = (await call("manager", "GET", "/api/dashboard/desk")).data;
  assert.equal(again.pending.prices, 1);
  assert.equal(again.suggestions.find((s: any) => s.to === "/prices")?.level, "critical", "price approval is urgent");
  // a khata customer over the limit shows up
  const c = (await call("admin", "GET", "/api/customers")).data.find((x: any) => x.balance > 0);
  if (c) {
    await call("admin", "PATCH", `/api/customers/${c.id}`, { credit_limit: 1 });
    assert.ok((await call("manager", "GET", "/api/dashboard/desk")).data.suggestions.some((s: any) => /over the credit limit/.test(s.title)));
  }
  assert.equal((await call("salesman", "GET", "/api/dashboard/desk")).status, 403);
});
