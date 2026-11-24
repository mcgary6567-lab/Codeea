import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-batchd-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server;
let base = "";
let db: typeof import("../src/db.js");
const tok: Record<string, string> = {};
async function call(who: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tok[who] ? { authorization: `Bearer ${tok[who]}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, msg: string) => { assert.equal(r.status, 200, `${msg}: ${JSON.stringify(r.data)}`); return r.data; };

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "salesman"]) tok[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => server?.close());

test("duty roster: assign, read grid, today view, remove", async () => {
  const sm = db.get("SELECT id FROM users WHERE role='salesman' AND tenant_id=1 ORDER BY id LIMIT 1")!;
  const today = new Date(Date.now() + 5 * 3600_000).getUTCDay();
  ok(await call("admin", "PUT", "/api/roster", { user_id: sm.id, weekday: today, slot: "Day 8am–8pm" }), "assign");
  const grid = ok(await call("admin", "GET", "/api/roster"), "grid");
  assert.ok(grid.roster.some((r: any) => r.user_id === sm.id && r.weekday === today && r.slot === "Day 8am–8pm"));
  const td = ok(await call("admin", "GET", "/api/roster/today"), "today");
  assert.ok(td.rostered.some((r: any) => r.user_id === sm.id), "salesman rostered today");
  ok(await call("admin", "DELETE", `/api/roster/${sm.id}/${today}`), "remove");
  const grid2 = ok(await call("admin", "GET", "/api/roster"), "grid2");
  assert.ok(!grid2.roster.some((r: any) => r.user_id === sm.id && r.weekday === today));
});

test("overtime hours and paid-leave balance in the month summary", async () => {
  const sm = db.get("SELECT id FROM users WHERE role='salesman' AND tenant_id=1 ORDER BY id LIMIT 1")!;
  // a 15-hour attendance day → 3 hours overtime (threshold 12)
  const day = db.pkDate();
  db.run("DELETE FROM attendance WHERE user_id=? AND day=?", sm.id, day);
  const inAt = new Date(Date.now() - 15 * 3600_000).toISOString(), outAt = new Date().toISOString();
  db.run("INSERT INTO attendance (tenant_id,user_id,day,check_in,check_out,late_minutes,source) VALUES (1,?,?,?,?,0,'manual')", sm.id, day, inAt, outAt);
  // set a leave quota and record an approved 2-day paid leave this year
  ok(await call("admin", "PATCH", `/api/staff/${sm.id}/duty`, { leave_quota: 10 }), "quota");
  const y = day.slice(0, 4);
  db.run("INSERT INTO leaves (tenant_id,user_id,from_day,to_day,type,status,created_at) VALUES (1,?,?,?,'paid','approved',?)", sm.id, `${y}-01-02`, `${y}-01-03`, new Date().toISOString());

  const m = ok(await call("admin", "GET", `/api/staff/${sm.id}`), "staff").attendance;
  assert.ok(m.overtime_hours >= 2.9 && m.overtime_hours <= 3.1, `overtime ~3h, got ${m.overtime_hours}`);
  assert.equal(m.leave_balance.quota, 10);
  assert.ok(m.leave_balance.taken >= 2, "at least the 2 paid-leave days counted");
  assert.equal(m.leave_balance.left, Math.max(0, 10 - m.leave_balance.taken), "left = quota − taken");
});
