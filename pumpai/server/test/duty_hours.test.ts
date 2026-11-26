/** 24h-duty (a day on / a day off) salesman: his off days are not counted absent and his salary is not cut; a real gap still is. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-duty-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server; let base = ""; let db: typeof import("../src/db.js");
const tokens: Record<string, string> = {};
async function call(who: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tokens[who] ? { authorization: `Bearer ${tokens[who]}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, m: string) => { assert.equal(r.status, 200, `${m}: ${JSON.stringify(r.data)}`); return r.data; };

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["manager"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server?.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("24h duty: off days of the a-day-on/a-day-off cycle are not absent and not cut; a true gap is", async () => {
  const month = db.pkDate().slice(0, 7);
  const dayOfMonth = Number(db.pkDate().slice(8, 10));
  const u = db.get("SELECT id, salary FROM users WHERE tenant_id=1 AND role='salesman' AND duty_start IS NOT NULL ORDER BY id LIMIT 1") as any;
  assert.ok(u, "a salesman with a duty time");
  // make the whole month countable: joined at month start, daily salary, no weekly off
  db.run("UPDATE users SET created_at=?, salary=30000, weekly_off=NULL WHERE id=?", `${month}-01T00:00:00+05:00`, u.id);
  db.run("DELETE FROM attendance WHERE user_id=? AND day LIKE ?", u.id, `${month}-%`);
  // set him to 24h duty (a day on / a day off)
  ok(await call("manager", "PATCH", `/api/staff/${u.id}/duty`, { duty_hours: 24 }), "set 24h duty");

  // check-ins on the odd days up to today; even days in between are his off days
  const maxDay = Math.min(dayOfMonth, 27);
  for (let d = 1; d <= maxDay; d += 2) {
    const day = `${month}-${String(d).padStart(2, "0")}`;
    db.run("INSERT INTO attendance (tenant_id,user_id,day,check_in,late_minutes,source) VALUES (1,?,?,?,0,'test')", u.id, day, `${day}T08:00:00+05:00`);
  }
  if (dayOfMonth < 4) return; // too early in the month to see the pattern — skip the asserts

  let att = ok(await call("manager", "GET", `/api/staff/${u.id}`), "staff").attendance;
  assert.equal(att.user.duty_hours, 24, "duty_hours shown");
  assert.equal(att.absent, 0, "no off day counted as absent");
  assert.ok(att.off >= 1, "the in-between days are counted as off");
  assert.equal(att.unpaid_days, 0, "nothing unpaid");
  assert.equal(att.salary_cut, 0, "no salary cut for a 24h-duty man's off days");

  // a REAL gap: he misses an on-day (remove an odd day's check-in) — both neighbours are off, so it is a genuine absence
  const missDay = `${month}-03`;
  db.run("DELETE FROM attendance WHERE user_id=? AND day=?", u.id, missDay);
  att = ok(await call("manager", "GET", `/api/staff/${u.id}`), "staff2").attendance;
  assert.equal(att.absent, 1, "a missed on-day (no worked neighbour) is a real absence");
  assert.ok(att.salary_cut > 0, "the real absence is cut");

  // the same calendar on a 12h (daily) duty would count every missing day as absent — many cuts
  ok(await call("manager", "PATCH", `/api/staff/${u.id}/duty`, { duty_hours: 12 }), "back to daily");
  const daily = ok(await call("manager", "GET", `/api/staff/${u.id}`), "staff3").attendance;
  assert.ok(daily.absent > att.absent, "daily duty counts every unworked day as absent");
});
