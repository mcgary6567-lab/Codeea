import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-hand-"));
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
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

let hand: any, shift: any, pmg: any[], rate = 0;

test("handover: salesman starts from the last closing reading; a meter gap is alerted and taken from stock", async () => {
  hand = (await call("salesman", "GET", "/api/shifts/handover")).data;
  assert.ok(hand.nozzles.length >= 4 && hand.nozzles.every((n: any) => !n.busy && n.last_reading > 0));
  pmg = hand.nozzles.filter((n: any) => n.product === "PMG");
  // a reading below the last closing is impossible
  const bad = await call("salesman", "POST", "/api/shifts/open", { readings: { [pmg[0].nozzle_id]: pmg[0].last_reading - 1 } });
  assert.equal(bad.status, 400);
  assert.match(bad.data.error, /below the last closing/);
  const tankBefore = (await call("admin", "GET", "/api/stations")).data[0].tanks.find((t: any) => t.product === "PMG").current_l;
  // salesman takes the two petrol nozzles; nozzle 1 shows 12.5 L more than the last closing
  const r = await call("salesman", "POST", "/api/shifts/open", { readings: { [pmg[0].nozzle_id]: pmg[0].last_reading + 12.5, [pmg[1].nozzle_id]: pmg[1].last_reading } });
  assert.equal(r.status, 200);
  shift = r.data;
  assert.equal(shift.nozzles, 2);
  near(shift.handover_gaps[0].litres, 12.5);
  const tankAfter = (await call("admin", "GET", "/api/stations")).data[0].tanks.find((t: any) => t.product === "PMG").current_l;
  near(tankBefore - tankAfter, 12.5, "stock follows the meter");
  const m = (await call("manager", "GET", "/api/notifications")).data.items;
  assert.ok(m.some((n: any) => n.type === "handover_gap" && /12\.5 L/.test(n.body)));
});

test("one nozzle, one open shift: the next salesman gets the remaining nozzles", async () => {
  const busy = await call("manager", "POST", "/api/shifts/open", { station_id: hand.station_id, attendant: "Asghar", readings: { [pmg[0].nozzle_id]: pmg[0].last_reading + 20 } });
  assert.equal(busy.status, 400);
  assert.match(busy.data.error, /already running/);
  const asghar = (await call("manager", "POST", "/api/shifts/open", { station_id: hand.station_id, attendant: "Asghar" })).data;
  assert.equal(asghar.nozzles, hand.nozzles.length - 2);
  const h2 = (await call("salesman", "GET", "/api/shifts/handover")).data;
  assert.ok(h2.nozzles.every((n: any) => n.busy));
});

test("sales, khata, digital and cash expenses add up to the meter at shift close", async () => {
  const today = (await call("salesman", "GET", "/api/pos/today")).data;
  rate = today.prices.PMG;
  const police = (await call("salesman", "GET", "/api/pos/khata-accounts")).data.find((a: any) => a.type === "police");
  await call("salesman", "POST", "/api/sales", { station_id: hand.station_id, product: "PMG", litres: 20, payment_method: "khata", customer_id: police.id, vehicle_no: "LEJ-1", slip_no: "PS-77" });
  await call("salesman", "POST", "/api/sales", { station_id: hand.station_id, product: "PMG", amount: 1000, payment_method: "easypaisa" });
  await call("salesman", "POST", "/api/sales", { station_id: hand.station_id, product: "PMG", litres: 10, payment_method: "cash" });
  // expenses paid from shift cash
  const cats = (await call("salesman", "GET", "/api/shifts/expense-categories")).data;
  assert.ok(cats.includes("Tea & food"));
  assert.equal((await call("salesman", "POST", `/api/shifts/${shift.id}/expenses`, { category: "Nope", amount: 10 })).status, 400);
  const tea = (await call("salesman", "POST", `/api/shifts/${shift.id}/expenses`, { category: "Tea & food", amount: 500, note: "Chai for staff" })).data;
  assert.equal(tea.expense.status, "approved");
  const big = (await call("salesman", "POST", `/api/shifts/${shift.id}/expenses`, { category: "Maintenance & repairs", amount: 15000, note: "Nozzle hose" })).data;
  assert.equal(big.expense.status, "pending", "above the approval limit");
  near(big.summary.expenses_total, 15500);
  // remove a wrong one and add it again
  const del = (await call("salesman", "DELETE", `/api/shifts/${shift.id}/expenses/${big.expense.id}`)).data;
  near(del.summary.expenses_total, 500);
  await call("salesman", "POST", `/api/shifts/${shift.id}/expenses`, { category: "Maintenance & repairs", amount: 15000, note: "Nozzle hose" });

  // close: nozzle 1 moved 100 L since opening, nozzle 2 moved 0
  const live = (await call("salesman", "GET", `/api/shifts/${shift.id}/live`)).data;
  const readings = Object.fromEntries(live.readings.map((r: any) => [r.nozzle_id, r.opening + (r.nozzle_id === pmg[0].nozzle_id ? 100 : 0)]));
  const meterAmount = 100 * rate;
  const khata = 20 * rate;
  const expectedCash = meterAmount - khata - 1000 - 15500;
  const closed = await call("salesman", "POST", `/api/shifts/${shift.id}/close`, { readings, cash_actual: Math.round(expectedCash) });
  assert.equal(closed.status, 200);
  const s = closed.data.summary;
  near(s.litres, 100);
  nearRs(s.amount, meterAmount);
  near(s.khata, khata);
  near(s.digital, 1000);
  nearRs(s.khata + s.digital + s.cash_sales, s.amount, "every rupee of the meter is accounted for");
  nearRs(s.cash_expected, expectedCash);
  nearRs(closed.data.cash_expected, expectedCash);
  assert.ok(Math.abs(closed.data.variance) < 1);
});

test("shift report shows meters, rates, khata slips, expenses and the handover gap; next handover continues from here", async () => {
  const r = (await call("salesman", "GET", `/api/shifts/${shift.id}/report`)).data;
  const n1 = r.readings.find((x: any) => x.nozzle_id === pmg[0].nozzle_id);
  near(n1.litres, 100);
  near(n1.handover_prev, pmg[0].last_reading);
  assert.equal(r.handover_gaps.length, 1);
  assert.equal(r.by_rate[0].rate, rate);
  assert.equal(r.khata[0].slip_nos[0], "PS-77");
  near(r.khata[0].litres, 20);
  assert.equal(r.summary.expenses.length, 2);
  // manager can open it too; another salesman's report is not visible to this salesman
  assert.equal((await call("manager", "GET", `/api/shifts/${shift.id}/report`)).status, 200);
  const h = (await call("salesman", "GET", "/api/shifts/handover")).data;
  const next = h.nozzles.find((n: any) => n.nozzle_id === pmg[0].nozzle_id);
  near(next.last_reading, n1.closing);
  assert.equal(next.handed_over_by, "Imran");
  assert.equal(next.busy, false);
  // shift expenses show up in the expenses module
  const exp = (await call("admin", "GET", "/api/expenses?status=pending")).data.expenses;
  assert.ok(exp.some((e: any) => e.shift_id === shift.id && e.category === "Maintenance & repairs"));
});
