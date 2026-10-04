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
