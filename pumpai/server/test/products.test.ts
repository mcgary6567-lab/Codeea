/** Admin-managed fuel products: add / rename / hide / delete, and a sale in a brand-new product still tallies. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-products-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.DEMO_DATA = "0";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server;
let base = "";
let token = "";
async function call(method: string, url: string, body?: unknown, auth = true) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(auth && token ? { authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, m: string) => { assert.equal(r.status, 200, `${m}: ${JSON.stringify(r.data)}`); return r.data; };

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  // a real fresh install, so products are seeded through the setup route
  const r = ok(await call("POST", "/api/setup", {
    business: { name: "Products Pump", owner_name: "Owner", owner_phone: "0300 1112223" },
    admin: { name: "Owner", email: "owner@products.pk", password: "strongpass1" },
    stations: [{ name: "Main", tanks: [{ name: "Tank-1 Petrol", product: "PMG", capacity_l: 20000, current_l: 5000, nozzles: 1 }] }],
    prices: { PMG: 262 },
  }), "setup");
  token = r.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("a new pump starts with the three standard fuels", async () => {
  const d = ok(await call("GET", "/api/products"), "products");
  assert.deepEqual(d.products.map((p: any) => p.code).sort(), ["HOBC", "HSD", "PMG"]);
  assert.ok(d.products.every((p: any) => p.active && p.colour && p.name));
});

test("admin adds a fuel, it becomes usable everywhere and a sale in it keeps the whole system tallied", async () => {
  // add a premium petrol
  const xt = ok(await call("POST", "/api/products", { code: "xtron", name: "XTRON 97 Premium", short: "XTRON", colour: "#7c3aed", ur: "ایکسٹرون" }), "add");
  assert.equal(xt.code, "XTRON");
  // it appears in the live list and gets a price
  assert.ok(ok(await call("GET", "/api/products"), "list").products.some((p: any) => p.code === "XTRON"));
  ok(await call("POST", "/api/prices", { prices: { XTRON: 310 } }), "price");
  // a tank for it, a shift, and a sale — the new fuel flows through stock + the ledger
  const st = ok(await call("GET", "/api/stations"), "st")[0];
  const tank = ok(await call("POST", "/api/tanks", { station_id: st.id, name: "Tank-2 XTRON", product: "XTRON", capacity_l: 10000, current_l: 4000, nozzles: 1 }), "tank");
  assert.equal(tank.product, "XTRON");
  const noz = ok(await call("GET", "/api/stations"), "st2")[0].nozzles.find((n: any) => n.product === "XTRON");
  const sh = ok(await call("POST", "/api/shifts/open", { station_id: st.id, attendant: "Owner", readings: { [noz.id]: 0 } }), "shift");
  ok(await call("POST", "/api/sales", { station_id: st.id, product: "XTRON", litres: 10, payment_method: "cash" }), "sale");
  ok(await call("POST", `/api/shifts/${sh.id}/close`, { readings: { [noz.id]: 10 }, cash_actual: 3100 }), "close");
  // an unknown fuel is refused
  assert.equal((await call("POST", "/api/sales", { station_id: st.id, product: "NITRO", litres: 1, payment_method: "cash" })).status, 400);
  // the whole system still tallies with the new product in the books
  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("after new product");
  // the live dictionary (used for labels, reports and the TV board) carries the new fuel
  const cfg = await import("../src/config.js");
  assert.equal(cfg.PRODUCTS.XTRON, "XTRON 97 Premium");
  assert.equal(cfg.PRODUCT_META.XTRON.colour, "#7c3aed");
});

test("rename and recolour keep the code; hide needs the tank gone; only an unused fuel deletes", async () => {
  // rename HOBC (unused here)
  const list = ok(await call("GET", "/api/products?all=1"), "all").products;
  const hobc = list.find((p: any) => p.code === "HOBC");
  ok(await call("PATCH", `/api/products/${hobc.id}`, { name: "Hi-Octane 97", colour: "#ea580c" }), "rename");
  const after = ok(await call("GET", "/api/products?all=1"), "all2").products.find((p: any) => p.code === "HOBC");
  assert.equal(after.name, "Hi-Octane 97"); assert.equal(after.colour, "#ea580c");
  // XTRON has a tank + sales → cannot hide until the tank is retired, and can never be deleted
  const xt = list.find((p: any) => p.code === "XTRON");
  assert.equal((await call("PATCH", `/api/products/${xt.id}`, { active: false })).status, 400, "tank holds it");
  assert.equal((await call("DELETE", `/api/products/${xt.id}`)).status, 400, "has history → hide instead");
  // HOBC never used → can be hidden, then it leaves the pickers but history/labels still resolve
  ok(await call("PATCH", `/api/products/${hobc.id}`, { active: false }), "hide hobc");
  assert.ok(!ok(await call("GET", "/api/products"), "active").products.some((p: any) => p.code === "HOBC"), "hidden from pickers");
  assert.ok(ok(await call("GET", "/api/products?all=1"), "all3").products.some((p: any) => p.code === "HOBC"), "still listed with all=1");
  // a product no record ever used can be deleted outright
  const tmp = ok(await call("POST", "/api/products", { code: "KERO", name: "Kerosene" }), "kero");
  assert.equal((await call("DELETE", `/api/products/${tmp.id}`)).status, 200);
  // non-admin cannot manage products
  assert.equal((await call("POST", "/api/products", { code: "LPG", name: "LPG" }, false)).status, 401);
});
