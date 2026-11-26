/**
 * Two field problems:
 *  1) a khata sale made on the tablet without internet uploads only after the shift was closed — it must still reach
 *     the customer's khata and the salesman's shift report (moved out of the meter cash), never be lost;
 *  2) a shift with no sale at all (strike / pump closed) must close with Rs 0 and hand the meters to the next salesman.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-latesale-"));
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
const near = (a: number, b: number, msg?: string) => assert.ok(Math.abs(a - b) < 0.05, `${msg ?? ""} ${a} ≈ ${b}`);

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman", "cashier"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
  db.run("UPDATE shifts SET status='closed', closed_at=? WHERE status='open'", new Date().toISOString());
});
after(() => server?.close());

const sameReadings = (live: any) => Object.fromEntries(live.readings.map((r: any) => [r.nozzle_id, r.checkpoint ?? r.opening]));

test("strike day: a shift with no sale closes with Rs 0, needs no handover, and the next salesman can start", async () => {
  const open = ok(await call("salesman", "POST", "/api/shifts/open", {}), "open");
  const live = ok(await call("salesman", "GET", `/api/shifts/${open.id}/live`), "live");
  const readings = sameReadings(live);
  const prev = ok(await call("salesman", "POST", `/api/shifts/${open.id}/preview`, { readings }), "preview");
  assert.equal(prev.cash_expected, 0);
  const closed = ok(await call("salesman", "POST", `/api/shifts/${open.id}/close`, { readings, cash_actual: 0 }), "close with zero");
  assert.equal(closed.status, "closed");
  assert.equal(closed.litres, 0); assert.equal(closed.cash_expected, 0); assert.equal(closed.variance, 0);
  assert.equal(db.get("SELECT COUNT(*) n FROM sales WHERE shift_id=?", open.id).n, 0, "no sale rows invented");
  // nothing to take at the cash counter
  const h = ok(await call("cashier", "GET", "/api/cashier/handovers"), "handovers");
  assert.ok(!h.pending.some((p: any) => p.id === open.id), "a zero shift is not waiting for handover");
  // the meters are free: the next salesman starts on them
  const next = ok(await call("manager", "POST", "/api/shifts/open", { station_id: live.shift.station_id, attendant: "Bilal (next shift)" }), "next salesman opens");
  assert.ok(next.nozzles > 0);
  db.run("UPDATE shifts SET status='closed', closed_at=? WHERE id=?", new Date().toISOString(), next.id);
});

let shiftId = 0, live: any = null, cust: any = null, rateHSD = 0, tankBefore = 0, hsdNozzle: any = null;
test("setup: a shift is closed with 50 L diesel on the meter and nothing on the POS (all booked as cash)", async () => {
  const open = ok(await call("salesman", "POST", "/api/shifts/open", {}), "open");
  shiftId = open.id;
  live = ok(await call("salesman", "GET", `/api/shifts/${shiftId}/live`), "live");
  hsdNozzle = live.readings.find((r: any) => r.product === "HSD");
  assert.ok(hsdNozzle, "diesel nozzle");
  rateHSD = live.prices.HSD;
  const accts = ok(await call("salesman", "GET", "/api/pos/khata-accounts"), "accounts");
  cust = accts.find((a: any) => !a.khata_blocked) ?? accts[0];
  db.run("UPDATE customers SET credit_limit = balance + 1000000, khata_blocked=0 WHERE id=?", cust.id);
  const readings = sameReadings(live);
  readings[hsdNozzle.nozzle_id] = (hsdNozzle.checkpoint ?? hsdNozzle.opening) + 50;
  const closed = ok(await call("salesman", "POST", `/api/shifts/${shiftId}/close`, { readings, cash_actual: Math.round(50 * rateHSD) }), "close");
  near(closed.cash_expected, 50 * rateHSD, "all 50 L counted as cash");
  const meter = db.get("SELECT * FROM sales WHERE shift_id=? AND source='meter' AND product='HSD'", shiftId);
  near(meter.litres, 50);
  tankBefore = db.get("SELECT SUM(current_l) l FROM tanks WHERE station_id=? AND product='HSD'", live.shift.station_id).l;
});

test("late offline khata sale: lands on the khata, the shift report and the salesman's cash — not lost", async () => {
  const balBefore = db.get("SELECT balance FROM customers WHERE id=?", cust.id).balance;
  const at = new Date(Date.parse(live.shift.opened_at) + 1_000).toISOString(); // made during the shift, uploaded now
  const r = ok(await call("salesman", "POST", "/api/sales", { station_id: live.shift.station_id, product: "HSD", litres: 20, payment_method: "khata", customer_id: cust.id, slip_no: "L-1", vehicle_no: "LEB-1", client_uid: "late-khata-0001", offline_at: at }), "late khata sale");
  assert.equal(r.late_rebooked, true);
  assert.equal(r.shift_id, shiftId); assert.equal(r.at_close, 1);
  near(r.amount, 20 * rateHSD);
  // customer's khata went up by the sale
  near(db.get("SELECT balance FROM customers WHERE id=?", cust.id).balance - balBefore, 20 * rateHSD, "khata balance");
  assert.ok(db.get("SELECT id FROM khata_ledger WHERE customer_id=? AND ref=?", cust.id, `SALE-${r.id}`), "khata ledger row");
  // the meter cash entry shrank by the same litres; stock did not move again
  near(db.get("SELECT litres FROM sales WHERE shift_id=? AND source='meter' AND product='HSD'", shiftId).litres, 30, "meter cash litres");
  near(db.get("SELECT SUM(current_l) l FROM tanks WHERE station_id=? AND product='HSD'", live.shift.station_id).l, tankBefore, "tank unchanged");
  near(db.get("SELECT SUM(litres) l FROM sales WHERE shift_id=? AND product='HSD'", shiftId).l, 50, "shift litres still 50");
  // the salesman owes less cash; the shift report shows the khata
  const sh = db.get("SELECT * FROM shifts WHERE id=?", shiftId);
  near(sh.cash_expected, 30 * rateHSD, "cash expected");
  near(sh.variance, sh.cash_actual - sh.cash_expected, "variance recomputed");
  const rep = ok(await call("manager", "GET", `/api/shifts/${shiftId}/report`), "report");
  near(rep.summary.khata, 20 * rateHSD); near(rep.summary.cash_sales, 30 * rateHSD);
  assert.ok(rep.khata.some((k: any) => k.id === cust.id && k.slip_nos.includes("L-1")), "khata account on the report");
  // the manager is told
  assert.ok(db.get("SELECT id FROM alerts WHERE type='late_sale' AND title LIKE '%Late offline khata%'"), "alert");
  // the tablet re-sending the same sale changes nothing
  const again = ok(await call("salesman", "POST", "/api/sales", { station_id: live.shift.station_id, product: "HSD", litres: 20, payment_method: "khata", customer_id: cust.id, slip_no: "L-1", client_uid: "late-khata-0001", offline_at: at }), "resend");
  assert.equal(again.duplicate, true);
  near(db.get("SELECT litres FROM sales WHERE shift_id=? AND source='meter' AND product='HSD'", shiftId).litres, 30);
});

test("late offline cash sale is already covered by the meter; more than the meter has left is refused", async () => {
  const at = new Date(Date.parse(live.shift.opened_at) + 2_000).toISOString();
  const cash = ok(await call("salesman", "POST", "/api/sales", { station_id: live.shift.station_id, product: "HSD", litres: 5, payment_method: "cash", client_uid: "late-cash-0001", offline_at: at }), "late cash");
  assert.equal(cash.covered_by_meter, true);
  near(db.get("SELECT litres FROM sales WHERE shift_id=? AND source='meter' AND product='HSD'", shiftId).litres, 30, "meter untouched by a cash sale");
  const tooMuch = await call("salesman", "POST", "/api/sales", { station_id: live.shift.station_id, product: "HSD", litres: 40, payment_method: "khata", customer_id: cust.id, slip_no: "L-9", client_uid: "late-khata-0009", offline_at: at });
  assert.equal(tooMuch.status, 409, JSON.stringify(tooMuch.data));
  assert.match(tooMuch.data.error ?? JSON.stringify(tooMuch.data), /already closed/);
});

test("late offline card sale moves cash to the bank side of the shift", async () => {
  const at = new Date(Date.parse(live.shift.opened_at) + 3_000).toISOString();
  const r = ok(await call("salesman", "POST", "/api/sales", { station_id: live.shift.station_id, product: "HSD", litres: 10, payment_method: "card", client_uid: "late-card-0001", offline_at: at }), "late card");
  assert.equal(r.late_rebooked, true);
  near(db.get("SELECT litres FROM sales WHERE shift_id=? AND source='meter' AND product='HSD'", shiftId).litres, 20);
  near(db.get("SELECT cash_expected FROM shifts WHERE id=?", shiftId).cash_expected, 20 * rateHSD);
  const rep = ok(await call("manager", "GET", `/api/shifts/${shiftId}/report`), "report");
  near(rep.summary.digital, 10 * rateHSD);
});

test("every book still tallies to the rupee", async () => {
  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("late_sale_zero_shift");
  const { shiftMetersTally } = await import("./helpers/shiftMeters.js");
  await shiftMetersTally("late_sale_zero_shift");
});
