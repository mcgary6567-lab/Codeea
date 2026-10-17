/**
 * Each wholesale client gets their own margin: "pump − Rs 2", "pump − Rs 4" or a fixed rate.
 * Pump-linked rates follow every pump price change; past supplies keep the rate they were billed at.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-margin-"));
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
const ok = (r: { status: number; data: any }, msg: string) => { assert.equal(r.status, 200, `${msg}: ${JSON.stringify(r.data)}`); return r.data; };
const near = (a: number, b: number, msg?: string) => assert.ok(Math.abs(a - b) < 0.011, `${msg ?? ""} ${a} ≈ ${b}`);

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "wholesale"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

let A: any, B: any, C: any, pump = 0, firstSupply: any;
const card = async (id: number) => ok(await call("wholesale", "GET", `/api/wholesale/clients/${id}`), "client").rate_card;

test("admin gives each client its own margin below the pump price, or a fixed rate", async () => {
  pump = ok(await call("admin", "GET", "/api/prices"), "prices").current.HSD.price;
  const mk = async (name: string, rates: any) => ok(await call("admin", "POST", "/api/wholesale/clients", { name, credit_limit: 0, rates }), name);
  A = await mk("Two Rupee Traders", { HSD: { mode: "discount", discount: 2 } });
  B = await mk("Four Rupee Logistics", { HSD: { mode: "discount", discount: 4 }, PMG: { mode: "discount", discount: 3.5 } });
  C = await mk("Fixed Rate Farms", { HSD: 270 });
  const a = (await card(A.id)).HSD, b = (await card(B.id)).HSD, c = (await card(C.id)).HSD;
  near(a.rate, pump - 2, "A pays pump − 2"); assert.equal(a.label, "pump − Rs 2.00"); near(a.vs_pump, 2);
  near(b.rate, pump - 4, "B pays pump − 4");
  assert.equal(c.mode, "fixed"); near(c.rate, 270);
  assert.ok(a.cost > 0 && Math.abs(a.margin - (a.rate - a.cost)) < 0.011, "our margin over purchase cost is shown");
  // the new-client notice tells the wholesale officer the rate and the margin
  const n = ok(await call("wholesale", "GET", "/api/notifications"), "notifs").items.find((x: any) => x.type === "new_wholesale_client" && x.title.includes("Two Rupee"));
  assert.match(n.body, /pump − Rs 2\.00/);
});

test("wholesale officer supplies at the client's own rate and cannot change rates", async () => {
  firstSupply = ok(await call("wholesale", "POST", `/api/wholesale/clients/${A.id}/supply`, { station_id: 1, product: "HSD", litres: 100 }), "supply A");
  near(firstSupply.rate, pump - 2); near(firstSupply.amount, 100 * (pump - 2));
  const sb = ok(await call("wholesale", "POST", `/api/wholesale/clients/${B.id}/supply`, { station_id: 1, product: "HSD", litres: 100 }), "supply B");
  near(sb.amount, 100 * (pump - 4), "B billed at its bigger margin");
  assert.equal((await call("wholesale", "PUT", `/api/wholesale/clients/${A.id}/rates`, { rates: { HSD: { mode: "discount", discount: 10 } } })).status, 403);
  assert.equal((await call("wholesale", "POST", `/api/wholesale/clients/${A.id}/supply`, { station_id: 1, product: "HSD", litres: 10, rate: 200 })).status, 403);
});

test("pump price change moves pump-linked clients, keeps fixed ones, and tells the wholesale team", async () => {
  const r = ok(await call("manager", "POST", "/api/prices", { prices: { HSD: pump + 3 } }), "price change");
  assert.ok(r.wholesale_rates_updated >= 2);
  near((await card(A.id)).HSD.rate, pump + 1, "A = new pump − 2");
  near((await card(B.id)).HSD.rate, pump - 1, "B = new pump − 4");
  near((await card(C.id)).HSD.rate, 270, "fixed rate unchanged");
  const n = ok(await call("wholesale", "GET", "/api/notifications"), "notifs").items.find((x: any) => x.type === "wholesale_rates");
  assert.ok(n, "wholesale officer told");
  assert.match(n.body, new RegExp(`Two Rupee Traders: Diesel \\(HSD\\) Rs ${pump - 2} → Rs ${pump + 1}`));
  assert.match(n.body, /Fixed Rate Farms Diesel \(HSD\) Rs 270 \(fixed\)/);
  const hist = ok(await call("admin", "GET", `/api/wholesale/clients/${A.id}`), "detail").rate_history;
  assert.equal(hist[0].changed_by, "Pump price change"); assert.equal(hist[0].note, "pump − Rs 2.00");

  // next supply uses the new rate; the earlier one keeps the rate it was billed at
  const s2 = ok(await call("wholesale", "POST", `/api/wholesale/clients/${A.id}/supply`, { station_id: 1, product: "HSD", litres: 50 }), "supply 2");
  near(s2.rate, pump + 1);
  const st = ok(await call("admin", "GET", `/api/wholesale/clients/${A.id}/statement`), "statement");
  const supplies = st.lines.filter((l: any) => l.type === "supply");
  near(supplies[0].rate, pump - 2, "old supply keeps its rate");
  near(st.closing ?? supplies.at(-1).balance, 100 * (pump - 2) + 50 * (pump + 1), "ledger due");
});

test("admin can switch a client between margin and fixed", async () => {
  ok(await call("admin", "PUT", `/api/wholesale/clients/${C.id}/rates`, { rates: { HSD: { mode: "discount", discount: 1.5 } } }), "to margin");
  const c = (await card(C.id)).HSD;
  assert.equal(c.mode, "discount"); near(c.rate, pump + 3 - 1.5);
  ok(await call("admin", "PUT", `/api/wholesale/clients/${A.id}/rates`, { rates: { HSD: { mode: "fixed", rate: 268 } } }), "to fixed");
  const a = (await card(A.id)).HSD;
  assert.equal(a.mode, "fixed"); near(a.rate, 268);
  assert.equal((await call("admin", "PUT", `/api/wholesale/clients/${A.id}/rates`, { rates: { HSD: { mode: "discount", discount: 999 } } })).status, 400);
});
