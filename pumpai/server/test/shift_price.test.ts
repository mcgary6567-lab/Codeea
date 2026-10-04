import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-shift-"));
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
const near = (a: number, b: number) => assert.ok(Math.abs(a - b) < 0.05, `${a} ≈ ${b}`);

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

let shift: any;
let pmgNozzle: any;
let oldRate = 0;

test("mid-shift price change: salesman notified, POS blocked until confirmed with meter readings", async () => {
  shift = (await call("salesman", "POST", "/api/shifts/open", {})).data;
  const live = (await call("salesman", "GET", `/api/shifts/${shift.id}/live`)).data;
  pmgNozzle = live.readings.find((r: any) => r.product === "PMG");
  oldRate = live.prices.PMG;
  // 10 L entered on POS before the change
  assert.equal((await call("salesman", "POST", "/api/sales", { station_id: shift.station_id, product: "PMG", litres: 10, payment_method: "cash", nozzle_id: pmgNozzle.nozzle_id })).status, 200);

  // admin raises petrol by Rs 3
  const upd = (await call("admin", "POST", "/api/prices", { prices: { PMG: oldRate + 3 } })).data;
  assert.equal(upd.salesmen_notified, 1);
  const n = (await call("salesman", "GET", "/api/notifications")).data;
  assert.equal(n.pending_ack.length, 1);
  const pc = n.pending_ack[0];
  assert.equal(pc.type, "price_change");
  assert.match(pc.body, /\+Rs 3\.00\/L barh gaya/);
  assert.equal(n.open_shift.id, shift.id);
  assert.ok(n.open_shift.readings.length >= 2);
  // managers get an info notification, no ack needed
  const m = (await call("manager", "GET", "/api/notifications")).data;
  assert.ok(m.items.some((x: any) => x.type === "price_change_info"));
  assert.equal(m.pending_ack.length, 0);
  // admin sees the salesman has not confirmed yet
  let acks = (await call("admin", "GET", "/api/prices")).data.last_change.acks;
  assert.equal(acks[0].acked_at, null);

  // POS blocked
  const blocked = await call("salesman", "POST", "/api/sales", { station_id: shift.station_id, product: "PMG", litres: 5, payment_method: "cash" });
  assert.equal(blocked.status, 409);
  // confirm needs meter readings while on shift
  assert.equal((await call("salesman", "POST", `/api/notifications/${pc.id}/ack`, {})).status, 400);
  const readings = Object.fromEntries(n.open_shift.readings.map((r: any) => [r.nozzle_id, r.last_reading + (r.nozzle_id === pmgNozzle.nozzle_id ? 50 : 0)]));
  const ack = await call("salesman", "POST", `/api/notifications/${pc.id}/ack`, { readings });
  assert.equal(ack.status, 200);
  const pm = ack.data.settled.find((s: any) => s.product === "PMG");
  near(pm.unrecorded, 40); // 50 on the meter − 10 entered on POS
  near(pm.rate, oldRate);
  acks = (await call("admin", "GET", "/api/prices")).data.last_change.acks;
  assert.ok(acks[0].acked_at && acks[0].with_readings);
});

test("litres before the change billed at the old rate, after at the new rate; shift close settles the rest", async () => {
  // POS works again, now at the new rate
  const s = (await call("salesman", "POST", "/api/sales", { station_id: shift.station_id, product: "PMG", litres: 5, payment_method: "cash" })).data; // no nozzle picked
  near(s.rate, oldRate + 3);
  const live = (await call("salesman", "GET", `/api/shifts/${shift.id}/live`)).data;
  const pm = live.readings.find((r: any) => r.nozzle_id === pmgNozzle.nozzle_id);
  near(pm.checkpoint, pmgNozzle.opening + 50);
  // close: meter moved 30 more after the checkpoint, 5 of it entered on POS -> 25 booked at the new price
  const readings = Object.fromEntries(live.readings.map((r: any) => [r.nozzle_id, (r.checkpoint ?? r.opening) + (r.nozzle_id === pmgNozzle.nozzle_id ? 30 : 0)]));
  const expected = 50 * oldRate + 30 * (oldRate + 3);
  const closed = await call("salesman", "POST", `/api/shifts/${shift.id}/close`, { readings, cash_actual: Math.round(expected) - 1000 });
  assert.equal(closed.status, 200);
  near(closed.data.litres, 80);
  const pmg = closed.data.summary.by_product.find((p: any) => p.product === "PMG");
  near(pmg.litres, 80);
  near(pmg.amount, expected);
  near(closed.data.cash_expected, expected);
  assert.ok(closed.data.variance < -500);
  // managers get the shift report
  const m = (await call("manager", "GET", "/api/notifications")).data;
  assert.ok(m.items.some((x: any) => x.type === "shift_closed" && /Short/.test(x.body)));
});

test("bad meter readings are rejected without changing anything", async () => {
  const sh = (await call("salesman", "POST", "/api/shifts/open", {})).data;
  const live = (await call("salesman", "GET", `/api/shifts/${sh.id}/live`)).data;
  const readings = Object.fromEntries(live.readings.map((r: any, i: number) => [r.nozzle_id, i === 0 ? r.opening - 5 : r.opening + 10]));
  const r = await call("salesman", "POST", `/api/shifts/${sh.id}/close`, { readings, cash_actual: 0 });
  assert.equal(r.status, 400);
  assert.match(r.data.error, /below the previous reading/);
  const after = (await call("salesman", "GET", `/api/shifts/${sh.id}/live`)).data;
  assert.equal(after.summary.litres, 0, "no sales were booked");
  assert.equal(after.shift.status, "open");
});

test("12-hour reminder to the salesman, overdue alert to managers", async () => {
  const { run } = await import("../src/db.js");
  const sh = (await call("salesman", "GET", "/api/shifts")).data.find((s: any) => s.status === "open");
  run("UPDATE shifts SET opened_at=? WHERE id=?", new Date(Date.now() - 13.5 * 3600_000).toISOString(), sh.id);
  const r = (await call("admin", "POST", "/api/automations/shift_watch/run")).data;
  assert.match(r.result, /1 reminders sent/);
  const n = (await call("salesman", "GET", "/api/notifications")).data;
  assert.ok(n.items.some((x: any) => x.type === "shift_due"));
  assert.ok((await call("manager", "GET", "/api/notifications")).data.items.some((x: any) => x.type === "shift_overdue"));
  // no duplicate reminder on the next run
  assert.match((await call("admin", "POST", "/api/automations/shift_watch/run")).data.result, /0 reminders sent/);
  await call("salesman", "POST", "/api/notifications/read-all");
  assert.equal((await call("salesman", "GET", "/api/notifications")).data.unread, 0);
});
