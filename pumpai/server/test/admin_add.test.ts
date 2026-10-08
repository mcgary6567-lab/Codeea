import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-add-"));
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
const notes = async (who: string) => (await call(who, "GET", "/api/notifications")).data.items as any[];

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman", "wholesale"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

let shadbagh: any;

test("admin adds a police khata with vehicles: salesman and manager see it at once", async () => {
  const r = await call("admin", "POST", "/api/customers", { name: "Police Station Shadbagh", phone: "03004440001", type: "police", city: "Lahore", credit_limit: 300000,
    vehicles: [{ plate_no: "lej-2001 (mobile 1)", fuel: "PMG" }, { plate_no: "LEJ-2002 (Mobile 2)", fuel: "HSD" }] });
  assert.equal(r.status, 200);
  shadbagh = r.data;
  assert.equal(r.data.vehicles.length, 2);
  assert.equal(r.data.vehicles[0].plate_no, "LEJ-2001 (MOBILE 1)");
  // appears in the salesman's POS list, flagged NEW, with its vehicles
  const pos = (await call("salesman", "GET", "/api/pos/khata-accounts")).data.find((a: any) => a.id === shadbagh.id);
  assert.ok(pos && pos.is_new && pos.vehicles.length === 2 && pos.status === "ok");
  // notifications
  assert.ok((await notes("salesman")).some((n) => n.type === "new_khata" && /Shadbagh/.test(n.title)));
  assert.ok((await notes("manager")).some((n) => n.type === "new_khata"));
  assert.ok(!(await notes("admin")).some((n) => n.type === "new_khata" && /Shadbagh/.test(n.title)), "the person who added it is not notified");
});

test("manager cannot give credit but can add a customer; admin is told", async () => {
  assert.equal((await call("manager", "POST", "/api/customers", { name: "Credit Try", phone: "03004440002", credit_limit: 5000 })).status, 403);
  assert.equal((await call("manager", "POST", "/api/customers", { name: "Walk-in Regular", phone: "03004440003" })).status, 200);
  assert.ok((await notes("admin")).some((n) => n.type === "new_customer" && /Walk-in Regular/.test(n.title)));
  assert.ok(!(await notes("salesman")).some((n) => n.type === "new_customer"), "plain customers don't disturb salesmen");
});

test("new vehicle and khata closed reach the salesman; closed account leaves the POS", async () => {
  assert.equal((await call("admin", "POST", `/api/customers/${shadbagh.id}/vehicles`, { plate_no: "LEJ-2003 (Mobile 3)", fuel: "PMG" })).status, 200);
  assert.equal((await call("admin", "POST", `/api/customers/${shadbagh.id}/vehicles`, { plate_no: "lej-2003 (mobile 3)" })).status, 400, "duplicate vehicle");
  assert.ok((await notes("salesman")).some((n) => n.type === "new_vehicle" && /LEJ-2003/.test(n.body)));
  assert.equal((await call("admin", "PATCH", `/api/customers/${shadbagh.id}`, { credit_limit: 0 })).status, 200);
  assert.ok((await notes("salesman")).some((n) => n.type === "khata_closed"));
  assert.ok(!(await call("salesman", "GET", "/api/pos/khata-accounts")).data.some((a: any) => a.id === shadbagh.id));
  await call("admin", "PATCH", `/api/customers/${shadbagh.id}`, { credit_limit: 300000 });
  assert.ok((await notes("salesman")).filter((n) => n.type === "new_khata" && /Shadbagh/.test(n.title)).length >= 2);
});

test("supplier, wholesale client, station and tank notify the right people", async () => {
  assert.equal((await call("admin", "POST", "/api/suppliers", { name: "Shell Depot Machike" })).status, 200);
  assert.ok((await notes("manager")).some((n) => n.type === "new_supplier"));
  assert.ok(!(await notes("salesman")).some((n) => n.type === "new_supplier"));

  assert.equal((await call("admin", "POST", "/api/wholesale/clients", { name: "Raza Filling Point", rates: { HSD: 262 } })).status, 200);
  assert.ok((await notes("wholesale")).some((n) => n.type === "new_wholesale_client" && /Rs 262/.test(n.body)));

  const st = (await call("admin", "POST", "/api/stations", { name: "Al-Madina Sheikhupura Road", city: "Lahore" })).data;
  assert.ok((await notes("manager")).some((n) => n.type === "new_station"));
  assert.equal((await call("salesman", "POST", "/api/stations", { name: "X Station" })).status, 403);
  // tank at the new station: the salesman (assigned to station 1) is not told; a tank at station 1 is announced to them
  assert.equal((await call("admin", "POST", "/api/tanks", { station_id: st.id, name: "Tank-1 Diesel", product: "HSD", capacity_l: 30000, current_l: 50000 })).status, 400, "stock above capacity");
  assert.equal((await call("admin", "POST", "/api/tanks", { station_id: st.id, name: "Tank-1 Diesel", product: "HSD", capacity_l: 30000, current_l: 12000, nozzles: 2 })).status, 200);
  assert.ok(!(await notes("salesman")).some((n) => n.type === "new_tank"));
  assert.equal((await call("manager", "POST", "/api/tanks", { station_id: 1, name: "Tank-4 Diesel", product: "HSD", capacity_l: 20000, current_l: 5000 })).status, 200);
  assert.ok((await notes("salesman")).some((n) => n.type === "new_tank" && /Tank-4 Diesel/.test(n.title)));
  const stations = (await call("admin", "GET", "/api/stations")).data;
  assert.equal(stations.find((s: any) => s.id === st.id).nozzles.length, 2);
});

test("admin edits, closes and deletes stations; edits, retires and deletes tanks; adds, retires and deletes meters", async () => {
  const st = (await call("admin", "POST", "/api/stations", { name: "Temp Site", city: "Okara" })).data;
  // edit (typo fix + location for the attendance distance check)
  const e = await call("admin", "PATCH", `/api/stations/${st.id}`, { name: "Temp Site Okara", lat: 30.81, lng: 73.45, timings: "6am–11pm" });
  assert.equal(e.status, 200); assert.equal(e.data.name, "Temp Site Okara"); assert.equal(e.data.lat, 30.81);
  assert.equal((await call("manager", "PATCH", `/api/stations/${st.id}`, { name: "x" })).status, 403);
  // a tank, its meters, then more meters later
  const tk = (await call("admin", "POST", "/api/tanks", { station_id: st.id, name: "T1", product: "PMG", capacity_l: 10000, current_l: 0, nozzles: 1 })).data;
  const m2 = (await call("admin", "POST", `/api/tanks/${tk.id}/nozzles`, { label: "Machine 2 right", totalizer: 500.5 })).data;
  assert.equal(m2.label, "Machine 2 right"); assert.equal(m2.totalizer, 500.5); assert.equal(m2.meter_no, 2);
  let s = (await call("admin", "GET", "/api/stations")).data.find((x: any) => x.id === st.id);
  assert.equal(s.nozzles.length, 2);
  // tank edit: capacity cannot drop below the stock; reorder level and name change
  assert.equal((await call("admin", "PATCH", `/api/tanks/${tk.id}`, { capacity_l: 5000, name: "T1 Petrol", reorder_pct: 30 })).status, 200);
  assert.equal((await call("admin", "GET", "/api/stations")).data.find((x: any) => x.id === st.id).tanks[0].reorder_pct, 30);
  // retire a meter: it leaves the handover sheet and the shift; restore brings it back
  assert.equal((await call("admin", "PATCH", `/api/nozzles/${m2.id}`, { active: false })).status, 200);
  const sheet = (await call("admin", "GET", `/api/shifts/handover?station_id=${st.id}`)).data;
  assert.deepEqual(sheet.nozzles.map((n: any) => n.nozzle_id), [s.nozzles[0].id]);
  assert.equal((await call("admin", "GET", "/api/stations")).data.find((x: any) => x.id === st.id).nozzles.length, 1, "retired meter hidden");
  assert.equal((await call("admin", "GET", "/api/stations?all=1")).data.find((x: any) => x.id === st.id).nozzles.length, 2, "…but listed for the admin");
  assert.equal((await call("admin", "DELETE", `/api/nozzles/${m2.id}`)).status, 200, "never ran a shift → can be deleted");
  // a meter that ran a shift cannot be deleted, only retired
  const first = s.nozzles[0];
  const sh = (await call("admin", "POST", "/api/shifts/open", { station_id: st.id, attendant: "Tester", readings: { [first.id]: 0 } })).data;
  assert.equal((await call("admin", "PATCH", `/api/nozzles/${first.id}`, { active: false })).status, 400, "running in an open shift");
  assert.equal((await call("admin", "PATCH", `/api/tanks/${tk.id}`, { active: false })).status, 400, "meter running");
  assert.equal((await call("admin", "PATCH", `/api/stations/${st.id}`, { active: false })).status, 400, "open shift");
  assert.equal((await call("admin", "POST", `/api/shifts/${sh.id}/close`, { readings: { [first.id]: 0 }, cash_actual: 0 })).status, 200);
  assert.equal((await call("admin", "DELETE", `/api/nozzles/${first.id}`)).status, 400, "has readings → retire instead");
  assert.equal((await call("admin", "DELETE", `/api/tanks/${tk.id}`)).status, 400, "has history → retire instead");
  assert.equal((await call("admin", "PATCH", `/api/tanks/${tk.id}`, { active: false })).status, 200, "empty tank retires with its meters");
  s = (await call("admin", "GET", "/api/stations")).data.find((x: any) => x.id === st.id);
  assert.equal(s.tanks.length, 0); assert.equal(s.nozzles.length, 0);
  // close the station: it leaves every list; reopen brings it back; delete only when unused
  assert.equal((await call("admin", "DELETE", `/api/stations/${st.id}`)).status, 400, "has a tank → close instead");
  assert.equal((await call("admin", "PATCH", `/api/stations/${st.id}`, { active: false })).status, 200);
  assert.ok(!(await call("admin", "GET", "/api/stations")).data.some((x: any) => x.id === st.id));
  assert.ok((await call("admin", "GET", "/api/stations?all=1")).data.some((x: any) => x.id === st.id && !x.active));
  const empty = (await call("admin", "POST", "/api/stations", { name: "Never Used" })).data;
  assert.equal((await call("admin", "DELETE", `/api/stations/${empty.id}`)).status, 200);
  assert.equal((await call("admin", "DELETE", `/api/stations/1`)).status, 400, "station 1 has history");
});

test("a customer can be archived only when settled, then restored", async () => {
  const c = (await call("admin", "POST", "/api/customers", { name: "Dup Customer", phone: "03005550001", type: "retail" })).data;
  // archive a plain customer → gone from the list, back with ?all=1
  assert.equal((await call("admin", "POST", `/api/customers/${c.id}/archive`, { archived: true })).status, 200);
  assert.ok(!(await call("manager", "GET", "/api/customers?q=Dup")).data.some((x: any) => x.id === c.id));
  assert.ok((await call("manager", "GET", "/api/customers?q=Dup&all=1")).data.some((x: any) => x.id === c.id && !x.active));
  assert.equal((await call("admin", "POST", `/api/customers/${c.id}/archive`, { archived: false })).status, 200);
  assert.ok((await call("manager", "GET", "/api/customers?q=Dup")).data.some((x: any) => x.id === c.id));
  // a khata account with a balance cannot be archived until it is settled
  const k = (await call("admin", "POST", "/api/customers", { name: "Owing Khata", phone: "03005550002", type: "business", credit_limit: 50000 })).data;
  assert.equal((await call("admin", "POST", `/api/customers/${k.id}/khata`, { type: "debit", amount: 3000, note: "manual charge", notify: false })).status, 200);
  assert.equal((await call("admin", "POST", `/api/customers/${k.id}/archive`, { archived: true })).status, 400, "owes money → refused");
  // settle it, then archiving works and it leaves the POS khata list
  assert.equal((await call("admin", "POST", `/api/customers/${k.id}/khata`, { type: "credit", amount: 3000, note: "paid", notify: false })).status, 200);
  assert.equal((await call("admin", "POST", `/api/customers/${k.id}/archive`, { archived: true })).status, 200);
  assert.ok(!(await call("salesman", "GET", "/api/pos/khata-accounts")).data.some((a: any) => a.id === k.id), "archived khata leaves the POS");
});

test("admin renames, switches off and deletes expense categories; limits are settings", async () => {
  const c = (await call("admin", "POST", "/api/expense-categories", { name: "Chai & biskut" })).data;
  const exp = (await call("manager", "POST", "/api/expenses", { category: "Chai & biskut", amount: 150 })).data;
  assert.equal((await call("admin", "PATCH", `/api/expense-categories/${c.id}`, { name: "Tea & biscuits", monthly_budget: 5000 })).status, 200);
  const list = (await call("manager", "GET", "/api/expenses")).data.expenses;
  assert.equal(list.find((x: any) => x.id === exp.id).category, "Tea & biscuits", "past expenses follow the new name");
  assert.equal((await call("admin", "DELETE", `/api/expense-categories/${c.id}`)).status, 400, "in use → switch off instead");
  assert.equal((await call("admin", "PATCH", `/api/expense-categories/${c.id}`, { active: false })).status, 200);
  assert.ok(!(await call("manager", "GET", "/api/expense-categories")).data.categories.some((x: any) => x.id === c.id), "off → not offered");
  assert.ok((await call("admin", "GET", "/api/expense-categories?all=1")).data.categories.some((x: any) => x.id === c.id && !x.active));
  assert.equal((await call("manager", "POST", "/api/expenses", { category: "Tea & biscuits", amount: 10 })).status, 400, "switched-off category refused");
  const unused = (await call("admin", "POST", "/api/expense-categories", { name: "Typo catgory" })).data;
  assert.equal((await call("admin", "DELETE", `/api/expense-categories/${unused.id}`)).status, 200);
  // limits
  assert.equal((await call("admin", "PUT", "/api/settings", { limits: { test_limit_l: 25, shortage_min: 250, shortage_tolerance_pct: 0.5, utility_alert_pct: 20, pin_admin: true } })).status, 200);
  const l = (await call("admin", "GET", "/api/settings")).data.limits;
  assert.deepEqual(l, { test_limit_l: 25, shortage_min: 250, utility_alert_pct: 20, shortage_tolerance_pct: 0.5, pin_admin: true });
  assert.equal((await call("admin", "GET", "/api/claims")).data.tolerance_pct, 0.5, "claims read the same setting");
});

test("admin sets hardware status and alert sensitivity", async () => {
  // hardware status: no longer always 'pending'
  assert.equal((await call("admin", "PUT", "/api/hardware/cctv", { status: "live" })).status, 200);
  assert.equal((await call("admin", "GET", "/api/hardware")).data.find((x: any) => x.key === "cctv").status, "live");
  assert.equal((await call("admin", "PUT", "/api/hardware/nope", { status: "live" })).status, 404);
  assert.equal((await call("manager", "PUT", "/api/hardware/cctv", { status: "pending" })).status, 403);
  // alert sensitivity round-trips and reaches the detector defaults
  const d = (await call("admin", "GET", "/api/settings")).data.sensitivity;
  assert.equal(d.dip_var_warn, 0.5); assert.equal(d.low_stock_days, 1.5);
  assert.equal((await call("admin", "PUT", "/api/settings", { sensitivity: { dip_var_warn: 0.8, cash_short_warn: 3000, sales_drop_pct: 40, low_stock_days: 2 } })).status, 200);
  const d2 = (await call("admin", "GET", "/api/settings")).data.sensitivity;
  assert.equal(d2.dip_var_warn, 0.8); assert.equal(d2.cash_short_warn, 3000); assert.equal(d2.sales_drop_pct, 40); assert.equal(d2.low_stock_days, 2);
});
