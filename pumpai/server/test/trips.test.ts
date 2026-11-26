import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-trips-"));
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
  for (const who of ["admin", "manager", "wholesale"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => server?.close());

test("wholesale fleet: tankers and drivers on file; one tanker trip drops fuel at several clients, each at their own rate", async () => {
  // register a driver and a tanker
  const dr = await call("wholesale", "POST", "/api/wholesale/drivers", { name: "Test Driver", phone: "03001112223", cnic: "35202-1111111-1", licence_no: "L-1", licence_expiry: "2020-01-01" });
  assert.equal(dr.status, 200, JSON.stringify(dr.data));
  const tk = await call("wholesale", "POST", "/api/wholesale/tankers", { number: "tst-100", capacity_l: 1500, driver_id: dr.data.id });
  assert.equal(tk.status, 200); assert.equal(tk.data.number, "TST-100");
  assert.equal((await call("wholesale", "POST", "/api/wholesale/tankers", { number: "TST-100" })).status, 400, "duplicate number");
  const fleet = (await call("wholesale", "GET", "/api/wholesale/fleet")).data;
  assert.ok(fleet.drivers.find((x: any) => x.id === dr.data.id).licence_expired);
  assert.equal((await call("manager", "GET", "/api/wholesale/fleet")).status, 403);

  const clients = (await call("wholesale", "GET", "/api/wholesale/clients")).data.filter((c: any) => c.rates.HSD);
  const [a, b] = clients;
  assert.ok(a && b && a.rates.HSD !== b.rates.HSD, "two clients with different diesel rates");
  const st = (await call("admin", "GET", "/api/stations")).data[0];
  const tankBefore = st.tanks.find((t: any) => t.product === "HSD");
  const dueA = a.due, dueB = b.due;

  // more than the tanker holds is refused
  const tooMuch = await call("wholesale", "POST", "/api/wholesale/trips", { station_id: st.id, product: "HSD", tanker_id: tk.data.id, drops: [{ client_id: a.id, litres: 1000 }, { client_id: b.id, litres: 600 }] });
  assert.equal(tooMuch.status, 400); assert.match(tooMuch.data.error, /holds 1,500/);
  // the wholesale officer cannot change a client's rate
  assert.equal((await call("wholesale", "POST", "/api/wholesale/trips", { station_id: st.id, product: "HSD", tanker_id: tk.data.id, drops: [{ client_id: a.id, litres: 100, rate: 1 }] })).status, 403);

  // 1,000 L in one tanker: 800 L to A at two places, 200 L to B
  const trip = await call("wholesale", "POST", "/api/wholesale/trips", { station_id: st.id, product: "HSD", tanker_id: tk.data.id, note: "morning round",
    drops: [{ client_id: a.id, litres: 500, location: "Pattoki depot" }, { client_id: b.id, litres: 200, location: "Shah yard" }, { client_id: a.id, litres: 300, location: "Pattoki farm" }] });
  assert.equal(trip.status, 200, JSON.stringify(trip.data));
  const t = trip.data;
  assert.equal(t.litres, 1000); assert.equal(t.drops.length, 3); assert.equal(t.vehicle_no, "TST-100"); assert.equal(t.driver_name, "Test Driver");
  assert.equal(t.drops[0].rate, a.rates.HSD); assert.equal(t.drops[1].rate, b.rates.HSD, "each client at their own rate");
  assert.ok(Math.abs(t.billed - (800 * a.rates.HSD + 200 * b.rates.HSD)) < 0.05);
  const after1 = (await call("admin", "GET", "/api/stations")).data[0].tanks.find((x: any) => x.id === tankBefore.id);
  assert.ok(Math.abs(after1.current_l - (tankBefore.current_l - 1000)) < 0.01, "stock taken once for the whole trip");
  const ca = (await call("wholesale", "GET", `/api/wholesale/clients/${a.id}`)).data;
  assert.ok(Math.abs(ca.summary.due - (dueA + 800 * a.rates.HSD)) < 0.05);
  const stmt = (await call("wholesale", "GET", `/api/wholesale/clients/${b.id}/statement`)).data.lines.at(-1);
  assert.equal(stmt.trip_id, t.id); assert.equal(stmt.location, "Shah yard"); assert.equal(stmt.driver_name, "Test Driver"); assert.equal(stmt.vehicle_no, "TST-100");
  assert.ok(Math.abs((await call("wholesale", "GET", `/api/wholesale/clients/${b.id}`)).data.summary.due - (dueB + 200 * b.rates.HSD)) < 0.05);
  // trip list and sheet
  assert.ok((await call("wholesale", "GET", "/api/wholesale/trips")).data.some((x: any) => x.id === t.id));
  // voiding one drop puts its fuel back and the sheet shows the rest
  ok(await call("admin", "POST", `/api/wholesale/txns/${t.drops[1].id}/void`, { reason: "client refused" }));
  const sheet = (await call("wholesale", "GET", `/api/wholesale/trips/${t.id}`)).data;
  assert.equal(sheet.delivered_l, 800);
  // a single supply can also pick the tanker; its usual driver comes with it
  const one = await call("wholesale", "POST", `/api/wholesale/clients/${a.id}/supply`, { station_id: st.id, product: "HSD", litres: 100, tanker_id: tk.data.id, location: "Pattoki" });
  assert.equal(one.status, 200, JSON.stringify(one.data));
  assert.equal(one.data.vehicle_no, "TST-100"); assert.equal(one.data.driver_name, "Test Driver"); assert.equal(one.data.location, "Pattoki");
});
function ok(r: { status: number; data: any }) { assert.equal(r.status, 200, JSON.stringify(r.data)); return r.data; }

test("wholesale dashboard: KPIs, 30-day trend, ageing, client health and suggestions", async () => {
  const r = await call("wholesale", "GET", "/api/wholesale/dashboard");
  assert.equal(r.status, 200, JSON.stringify(r.data));
  const dsh = r.data;
  assert.equal(dsh.daily.length, 30); assert.equal(dsh.weekly.length, 8);
  assert.ok(dsh.kpi.month_litres >= 0 && typeof dsh.kpi.total_due === "number");
  const ageSum = Object.values(dsh.ageing).reduce((a: number, b: any) => a + b, 0) as number;
  const dueSum = dsh.clients.reduce((a: number, c: any) => a + Math.max(0, c.due), 0);
  assert.ok(Math.abs(ageSum - dueSum) < 5, `ageing ${ageSum} = due ${dueSum}`);
  assert.ok(dsh.clients.every((c: any) => ["green", "amber", "red"].includes(c.health)));
  // the driver with the expired licence from the first test is flagged
  assert.ok(dsh.suggestions.some((x: any) => /Test Driver: licence expired/.test(x.title)));
  // a client at the limit gets a "collect payment" suggestion
  const c = dsh.clients[0];
  await call("admin", "PATCH", `/api/wholesale/clients/${c.id}`, { credit_limit: Math.max(1, Math.round(c.due / 0.9)) });
  const again = (await call("wholesale", "GET", "/api/wholesale/dashboard")).data;
  assert.ok(again.suggestions.some((x: any) => x.action?.kind === "payment" && x.action.client_id === c.id));
  assert.equal((await call("manager", "GET", "/api/wholesale/dashboard")).status, 403);
});

test("after all of the above: every book still tallies with the ledger", async () => {
  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("trips");
});
