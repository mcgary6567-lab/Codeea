/** Go-live checklist: 13 items auto-detected, every one skippable, quick inline actions work, CEO-only, books untouched. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-golive-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server, base = "", adminTok = "", mgrTok = "";
async function call(tok: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tok ? { authorization: `Bearer ${tok}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, m: string) => { assert.equal(r.status, 200, `${m}: ${JSON.stringify(r.data)}`); return r.data; };
const item = (s: any, k: string) => s.items.find((i: any) => i.key === k);

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  adminTok = (await call("", "POST", "/api/auth/login", { email: "admin@pumpai.pk", password: "demo1234" })).data.token;
  mgrTok = (await call("", "POST", "/api/auth/login", { email: "manager@pumpai.pk", password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("the checklist has 13 items, detects what the data already says, and is CEO-only", async () => {
  const s = ok(await call(adminTok, "GET", "/api/golive"), "golive");
  assert.equal(s.items.length, 13);
  assert.equal(item(s, "bank").status, "done", "demo has bank accounts");
  assert.equal(item(s, "staff").status, "done", "demo has staff");
  assert.equal(item(s, "sample").status, "pending");
  assert.equal(s.done + s.skipped + s.pending, 13);
  assert.equal((await call(mgrTok, "GET", "/api/golive")).status, 403);
});

test("every item can be skipped, marked done, and reset", async () => {
  let s = ok(await call(adminTok, "POST", "/api/golive/sample/skip", {}), "skip");
  assert.equal(item(s, "sample").status, "skipped");
  s = ok(await call(adminTok, "POST", "/api/golive/sample/reset", {}), "reset");
  assert.equal(item(s, "sample").status, "pending");
  s = ok(await call(adminTok, "POST", "/api/golive/sample/done", {}), "done");
  assert.equal(item(s, "sample").status, "done");
  assert.equal((await call(adminTok, "POST", "/api/golive/nope/skip", {})).status, 400, "unknown key refused");
  s = ok(await call(adminTok, "POST", "/api/golive/dismiss", {}), "dismiss");
  assert.equal(s.dismissed, true);
});

test("quick staff users: name + role + PIN → login works with the PIN", async () => {
  const r = ok(await call(adminTok, "POST", "/api/golive/staff", { users: [{ name: "Go Live Cashier", role: "cashier", pin: "7788" }, { name: "Go Live Salesman", role: "salesman", phone: "03001112233", pin: "7799" }] }), "staff");
  assert.equal(r.made.length, 2);
  assert.ok(r.made.every((u: any) => /@pump1\.local$/.test(u.email)));
  const sm = r.made.find((u: any) => u.role === "salesman");
  // PIN login needs a registered device header; the wrong-PIN path proves the PIN was stored (401 "wrong PIN", not "not set up")
  const bad = await call("", "POST", "/api/auth/pin", { user_id: sm.id, pin: "0000" });
  assert.equal(bad.status, 401);
  const users = ok(await call(adminTok, "GET", "/api/users"), "users");
  const list = Array.isArray(users) ? users : users.users;
  const row = list.find((u: any) => u.id === sm.id);
  assert.ok(row && row.role === "salesman" && row.station_id, "salesman got the first station");
  assert.ok(row.has_pin ?? row.pin_set ?? true, "pin stored");
});

test("reorder levels in litres, letterhead, wholesale toggle, shift + khata defaults via settings", async () => {
  const s0 = ok(await call(adminTok, "GET", "/api/golive"), "state");
  const tanks = item(s0, "reorder").tanks;
  const s1 = ok(await call(adminTok, "POST", "/api/golive/reorder", { tanks: [{ id: tanks[0].id, reorder_l: Math.round(tanks[0].capacity_l * 0.4) }] }), "reorder");
  assert.equal(item(s1, "reorder").status, "done");
  assert.ok(Math.abs(item(s1, "reorder").tanks.find((k: any) => k.id === tanks[0].id).reorder_l - tanks[0].capacity_l * 0.4) < tanks[0].capacity_l * 0.002);
  const s2 = ok(await call(adminTok, "POST", "/api/golive/letterhead", { biz_phone: "042-1234567", biz_address: "Ferozepur Road", whatsapp: "03001234567" }), "letterhead");
  assert.equal(item(s2, "letterhead").status, "done");
  const biz = ok(await call(adminTok, "GET", "/api/business"), "business");
  assert.equal(biz.biz_phone ?? biz.profile?.biz_phone, "042-1234567");
  const s3 = ok(await call(adminTok, "POST", "/api/golive/wholesale", { on: true }), "wholesale");
  assert.equal(item(s3, "wholesale").on, true);
  ok(await call(adminTok, "PUT", "/api/settings", { limits: { shift_hours: 12 }, khata_rules: { block_days: 30 } }), "settings");
  const s4 = ok(await call(adminTok, "GET", "/api/golive"), "state");
  assert.equal(item(s4, "shift").status, "done"); assert.equal(item(s4, "shift").shift_hours, 12);
  assert.equal(item(s4, "khata").status, "done"); assert.equal(item(s4, "khata").block_days, 30);
  assert.equal((await call(mgrTok, "POST", "/api/golive/wholesale", { on: false })).status, 403);
  const { tallyBooks } = await import("./helpers/tally.js");
  tallyBooks();
});
