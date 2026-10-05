/** Licences with expiry reminders, daily checklist with readings and photos, attendance, leave and payroll. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-compliance-"));
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
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, msg: string) => { assert.equal(r.status, 200, `${msg}: ${JSON.stringify(r.data)}`); return r.data; };
const PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("licences: reminders before expiry go to managers and the owner; renewing moves the date", async () => {
  const add = (days: number) => db.pkDate(Date.now() + days * 86_400_000);
  const l = ok(await call("manager", "POST", "/api/licences", { name: "Test calibration certificate", number: "WM-1", authority: "Weights & Measures", expires_on: add(7) }), "add");
  const list = ok(await call("manager", "GET", "/api/licences"), "list");
  assert.equal(list.find((x: any) => x.id === l.id).days_left, 7);
  const { runJob } = await import("../src/automation/scheduler.js");
  assert.match(await runJob(1, "licence_watch"), /\d+ reminders/);
  assert.ok(db.get("SELECT id FROM alerts WHERE type='licence_expiry' AND title LIKE '%Test calibration certificate expires in 7 days%'"));
  assert.ok(db.get("SELECT id FROM outbox WHERE kind='licence_expiry' AND text LIKE '%Test calibration%'"), "owner WhatsApp");
  ok(await call("manager", "PATCH", `/api/licences/${l.id}`, { expires_on: add(365) }), "renew");
  assert.equal(ok(await call("manager", "GET", "/api/licences"), "list").find((x: any) => x.id === l.id).days_left, 365);
  assert.equal((await call("salesman", "GET", "/api/licences")).status, 403);
});

test("checklist: salesman ticks checks; a reading out of range or a missing photo is handled; missing checks are reported", async () => {
  const c = ok(await call("salesman", "GET", "/api/checklist"), "checklist");
  assert.ok(c.total >= 8); assert.equal(c.done, 0);
  const water = c.items.find((i: any) => i.title.startsWith("Water in tanks"));
  assert.equal((await call("salesman", "POST", `/api/checklist/${water.id}`, { value: 2 })).status, 400, "photo needed");
  const photo = ok(await call("salesman", "POST", "/api/ai/read-photo", { kind: "proof", image: PNG }), "photo");
  assert.equal(photo.message, null);
  assert.equal(ok(await call("salesman", "POST", `/api/checklist/${water.id}`, { value: 2, photo_id: photo.photo_id }), "water").ok, true);
  const measure = c.items.find((i: any) => i.title.startsWith("5-litre"));
  const bad = ok(await call("salesman", "POST", `/api/checklist/${measure.id}`, { value: -60, note: "nozzle PMG-11" }), "measure");
  assert.equal(bad.ok, false);
  assert.ok(db.get("SELECT id FROM alerts WHERE type='checklist_fail' AND title LIKE '%5-litre%'"), "calibration alert");
  const after = ok(await call("salesman", "GET", "/api/checklist"), "after");
  assert.equal(after.done, 2);
  const { runJob } = await import("../src/automation/scheduler.js");
  assert.match(await runJob(1, "checklist_watch"), /stations with missing checks/);
  assert.ok(db.get("SELECT id FROM alerts WHERE type='checklist_missing'"));
});

test("attendance: live selfie + live location are mandatory; lateness; leave; salary cut for absences", async () => {
  const imran = db.get("SELECT * FROM users WHERE email='salesman@pumpai.pk'");
  db.run("DELETE FROM attendance WHERE user_id=? AND day=?", imran.id, db.pkDate());
  // opening a shift no longer marks attendance by itself
  const shift = ok(await call("salesman", "POST", "/api/shifts/open", {}), "open");
  assert.equal(shift.attendance_missing, true);
  assert.equal(ok(await call("salesman", "GET", "/api/attendance/me"), "me").today, null);
  const selfie = async (who: string) => ok(await call(who, "POST", "/api/ai/read-photo", { kind: "selfie", image: PNG }), "selfie").photo_id as number;
  // no selfie, no location, someone else's selfie, an old selfie, a non-selfie photo: all refused
  assert.match((await call("salesman", "POST", "/api/attendance/check-in", { lat: 31.6, lng: 74.4 })).data.error, /selfie/i);
  assert.match((await call("salesman", "POST", "/api/attendance/check-in", { photo_id: await selfie("salesman") })).data.error, /location/i);
  assert.equal((await call("salesman", "POST", "/api/attendance/check-in", { photo_id: await selfie("manager"), lat: 31.6, lng: 74.4 })).status, 400);
  const old = await selfie("salesman");
  db.run("UPDATE photos SET created_at=? WHERE id=?", new Date(Date.now() - 10 * 60_000).toISOString(), old);
  assert.match((await call("salesman", "POST", "/api/attendance/check-in", { photo_id: old, lat: 31.6, lng: 74.4 })).data.error, /too old/);
  const meter = ok(await call("salesman", "POST", "/api/ai/read-photo", { kind: "proof", image: PNG }), "proof").photo_id;
  assert.equal((await call("salesman", "POST", "/api/attendance/check-in", { photo_id: meter, lat: 31.6, lng: 74.4 })).status, 400);
  // live selfie + location: checked in
  const live = await selfie("salesman");
  const ci = ok(await call("salesman", "POST", "/api/attendance/check-in", { photo_id: live, lat: 31.6, lng: 74.4, accuracy: 12 }), "check in");
  assert.equal(ci.in_photo_id, live); assert.equal(ci.in_lat, 31.6); assert.equal(ci.in_acc, 12); assert.equal(ci.source, "app");
  // the same selfie cannot be used again
  assert.match((await call("salesman", "POST", "/api/attendance/check-out", { photo_id: live, lat: 31.6, lng: 74.4 })).data.error, /already used/);
  const me = ok(await call("salesman", "GET", "/api/attendance/me"), "me");
  assert.ok(me.today); assert.equal(me.month.tracked, true);
  assert.ok(me.month.present > 0, "past days present");
  const sl = ok(await call("salesman", "GET", `/api/shifts/${shift.id}/live`), "live");
  const closed = ok(await call("salesman", "POST", `/api/shifts/${shift.id}/close`, { readings: Object.fromEntries(sl.readings.map((r: any) => [r.nozzle_id, r.opening])), cash_actual: 0 }), "close");
  // closing the shift does not check out: that also needs a live selfie + location
  assert.equal(closed.checkout_missing, true);
  assert.equal(ok(await call("salesman", "GET", "/api/attendance/me"), "me").today.check_out, null);
  assert.match((await call("salesman", "POST", "/api/attendance/check-out", { photo_id: await selfie("salesman") })).data.error, /location/i);
  assert.match((await call("salesman", "POST", "/api/attendance/check-out", { lat: 31.6, lng: 74.4 })).data.error, /selfie/i);
  const out = ok(await call("salesman", "POST", "/api/attendance/check-out", { photo_id: await selfie("salesman"), lat: 31.6, lng: 74.4 }), "check out");
  assert.ok(out.check_out); assert.ok(out.out_photo_id); assert.equal(out.out_lat, 31.6);
  // manager checks in and out with selfies and location
  ok(await call("manager", "POST", "/api/attendance/check-in", { photo_id: await selfie("manager"), lat: 31.60, lng: 74.40 }), "manager in");
  const co = ok(await call("manager", "POST", "/api/attendance/check-out", { photo_id: await selfie("manager"), lat: 31.61, lng: 74.41 }), "manager out");
  assert.ok(co.out_photo_id); assert.equal(co.out_lat, 31.61);
  // leave request and approval
  const tomorrow = db.pkDate(Date.now() + 86_400_000);
  const lv = ok(await call("salesman", "POST", "/api/leaves", { from_day: tomorrow, to_day: tomorrow, type: "unpaid", reason: "sick child" }), "leave");
  assert.equal(lv.status, "pending");
  ok(await call("manager", "POST", `/api/leaves/${lv.id}/approve`, {}), "approve");
  const board = ok(await call("manager", "GET", "/api/attendance"), "board");
  assert.ok(board.present_today.length >= 2);
  assert.equal((await call("salesman", "GET", "/api/attendance")).status, 403);
  // an absent day is cut from the salary
  const yesterday = db.pkDate(Date.now() - 86_400_000);
  db.run("DELETE FROM attendance WHERE user_id=? AND day=?", imran.id, yesterday);
  db.run("UPDATE users SET weekly_off=NULL WHERE id=?", imran.id);
  const month = db.pkDate().slice(0, 7);
  const sum = ok(await call("manager", "GET", `/api/staff/${imran.id}`), "staff").attendance;
  if (yesterday.slice(0, 7) === month) {
    assert.ok(sum.absent >= 1);
    const paid = ok(await call("manager", "POST", `/api/staff/${imran.id}/pay-salary`, { photo_ids: await salaryProof() }), "pay");
    assert.ok(Math.abs(paid.absence_cut - (imran.salary / 30) * sum.unpaid_days) < 0.02, "salary / 30 per unpaid day");
  }
});

/** Salary needs a photo of the signed salary sheet. */
async function salaryProof() {
  const r = await call("manager", "POST", "/api/ai/read-photo", { kind: "proof", image: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==" });
  return [r.data.photo_id as number];
}
