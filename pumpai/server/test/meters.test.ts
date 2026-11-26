import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-meters-"));
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

test("meters are numbered No.1, No.2 … per station; number shows on shifts; sales per meter", async () => {
  const hand = (await call("salesman", "GET", "/api/shifts/handover")).data;
  const nums = hand.nozzles.map((n: any) => n.meter_no);
  assert.deepEqual(nums, nums.map((_: any, i: number) => i + 1), "1..N in order");
  assert.match(hand.nozzles[0].label, /^No\.1 · /);
  // a new tank's nozzles get the next numbers
  const st = hand.station_id;
  await call("admin", "POST", "/api/tanks", { station_id: st, name: "Extra", product: "HSD", capacity_l: 1000, current_l: 0, nozzles: 2 });
  const after2 = (await call("salesman", "GET", "/api/shifts/handover")).data.nozzles.map((n: any) => n.meter_no);
  assert.deepEqual(after2.slice(-2), [nums.length + 1, nums.length + 2]);
  // renumber: taking an existing number swaps
  const first = hand.nozzles[0], second = hand.nozzles[1];
  const r = await call("manager", "PATCH", `/api/nozzles/${first.nozzle_id}`, { meter_no: 2, label: "PMG-A" });
  assert.equal(r.status, 200); assert.equal(r.data.name, "No.2 · PMG-A");
  const swapped = (await call("salesman", "GET", "/api/shifts/handover")).data.nozzles;
  assert.equal(swapped.find((n: any) => n.nozzle_id === second.nozzle_id).meter_no, 1);
  assert.equal((await call("salesman", "PATCH", `/api/nozzles/${first.nozzle_id}`, { meter_no: 3 })).status, 403);
  // run a shift on two meters and read sales per meter
  const readings = { [first.nozzle_id]: first.last_reading, [second.nozzle_id]: second.last_reading };
  const shift = (await call("salesman", "POST", "/api/shifts/open", { readings })).data;
  assert.ok(shift.id, JSON.stringify(shift));
  const live = (await call("salesman", "GET", `/api/shifts/${shift.id}/live`)).data;
  assert.ok(live.readings.every((x: any) => /^No\.\d+ · /.test(x.label)));
  const closed = await call("salesman", "POST", `/api/shifts/${shift.id}/close`, { readings: { [first.nozzle_id]: first.last_reading + 100, [second.nozzle_id]: second.last_reading + 40 }, cash_actual: 0 });
  assert.equal(closed.status, 200, JSON.stringify(closed.data));
  const rep = (await call("manager", "GET", `/api/shifts/${shift.id}/report`)).data;
  const m1 = rep.readings.find((x: any) => x.nozzle_id === first.nozzle_id);
  assert.equal(m1.litres, 100); assert.ok(m1.amount > 100 * 200, "money at the fuel rate");
  const ms = (await call("manager", "GET", "/api/meters/sales")).data.meters;
  const row = ms.find((x: any) => x.nozzle_id === first.nozzle_id);
  assert.equal(row.meter, "No.2 · PMG-A"); assert.ok(row.litres >= 100); assert.ok(row.amount > 0); assert.ok(row.salesmen.length);
  assert.equal((await call("salesman", "GET", "/api/meters/sales")).status, 403);
  const report = (await call("manager", "GET", "/api/reports")).data;
  assert.ok(report.sales.by_meter.some((x: any) => x.nozzle_id === first.nozzle_id));
});

test("shift reports: meter lines less test litres = litres sold, for every closed shift", async () => {
  const { shiftMetersTally } = await import("./helpers/shiftMeters.js");
  await shiftMetersTally("meters");
});
