/** Leftover bypass fuel unloaded into our own tank: bypass stock down at avg cost, pump stock up, stock-register row, ledger, void, tally. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-byptank-"));
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
const today = new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10);

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "wholesale", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => server?.close());

let tank: any, supplierId = 0, clientId = 0, transferId = 0, stockBefore = 0, tankBefore = 0, avg = 0;

test("setup: a bypass delivery buys 4,000 L HSD and drops 3,000 L to a client — 1,000 L stay as bypass stock", async () => {
  tank = db.get("SELECT tk.* FROM tanks tk JOIN stations s ON s.id=tk.station_id WHERE s.tenant_id=1 AND tk.product='HSD' ORDER BY tk.id LIMIT 1");
  db.run("UPDATE tanks SET current_l = MIN(current_l, capacity_l - 2000) WHERE id=?", tank.id);
  supplierId = db.get("SELECT id FROM suppliers WHERE tenant_id=1 ORDER BY id LIMIT 1").id;
  clientId = db.get("SELECT id FROM wholesale_clients WHERE tenant_id=1 ORDER BY id LIMIT 1").id;
  db.run("UPDATE wholesale_clients SET credit_limit = credit_limit + 5000000 WHERE id=?", clientId);
  const stock0 = ok(await call("admin", "GET", "/api/bypass/stock"), "stock").total.litres;
  const r = ok(await call("admin", "POST", "/api/bypass/deliveries", { station_id: tank.station_id, purchases: [{ supplier_id: supplierId, product: "HSD", litres: 4000, cost_rate: 250 }], drops: [{ client_id: clientId, product: "HSD", litres: 3000, rate: 262 }] }), "bypass delivery");
  assert.ok(r.id);
  const st = ok(await call("admin", "GET", "/api/bypass/stock"), "stock");
  const hsd = st.by_product.find((x: any) => x.product === "HSD");
  near(hsd.litres, stock0 + 1000, "1,000 L left in bypass stock");
  stockBefore = hsd.litres; avg = hsd.avg_cost;
  tankBefore = db.get("SELECT current_l FROM tanks WHERE id=?", tank.id).current_l;
});

test("unload the leftover into our tank: bypass stock −1,000 at avg cost, tank +1,000, stock-register delivery, no new supplier bill", async () => {
  const supBefore = db.get("SELECT COUNT(*) n FROM supplier_txns WHERE supplier_id=?", supplierId).n;
  const bad = await call("admin", "POST", "/api/bypass/to-tank", { product: "HSD", litres: stockBefore + 500, tank_id: tank.id });
  assert.equal(bad.status, 400); assert.match(bad.data.error, /bypass stock is on hand/);
  const wrongTank = db.get("SELECT tk.id FROM tanks tk JOIN stations s ON s.id=tk.station_id WHERE s.tenant_id=1 AND tk.product<>'HSD' LIMIT 1");
  if (wrongTank) assert.equal((await call("admin", "POST", "/api/bypass/to-tank", { product: "HSD", litres: 100, tank_id: wrongTank.id })).status, 400, "wrong fuel tank");
  const r = ok(await call("admin", "POST", "/api/bypass/to-tank", { product: "HSD", litres: 1000, tank_id: tank.id, note: "Tanker had 1,000 L left", txn_date: today }), "to tank");
  transferId = r.id;
  near(r.unit_cost, avg, "at the bypass average cost"); near(r.cost, 1000 * avg);
  near(db.get("SELECT current_l FROM tanks WHERE id=?", tank.id).current_l, tankBefore + 1000, "tank up");
  near(ok(await call("admin", "GET", "/api/bypass/stock"), "stock").by_product.find((x: any) => x.product === "HSD")?.litres ?? 0, stockBefore - 1000, "bypass stock down");
  const del = db.get("SELECT * FROM deliveries WHERE bypass_drop_id=?", transferId);
  assert.ok(del, "stock register row"); near(del.received_l, 1000); near(del.purchase_rate, avg); assert.equal(del.tank_id, tank.id); assert.match(del.supplier, /bypass leftover/);
  assert.equal(db.get("SELECT COUNT(*) n FROM supplier_txns WHERE supplier_id=?", supplierId).n, supBefore, "no second supplier bill");
  // the stock page lists it like any delivery; the transfers list shows it
  const stock = ok(await call("admin", "GET", "/api/stock"), "stock page");
  assert.ok(stock.deliveries.some((d: any) => d.id === del.id));
  const list = ok(await call("admin", "GET", "/api/bypass/to-tank"), "list");
  assert.equal(list[0].id, transferId); assert.equal(list[0].tank, tank.name);
});

test("ledger: Dr Fuel purchases / Cr Bypass stock; bypass profit untouched; books tally", async () => {
  const j = ok(await call("admin", "GET", `/api/ledger?from=${today}&to=${today}`), "journal");
  const v = j.vouchers.find((x: any) => /Bypass leftover to pump stock/.test(x.narration ?? x.memo ?? JSON.stringify(x)));
  assert.ok(v, "journal voucher");
  const bs = j.trial_balance.find((x: any) => x.account === "Bypass stock");
  assert.ok(bs, "bypass stock account");
  // the transfer is not a bypass sale: bypass fuel cost unchanged by it
  const costRows = db.get("SELECT COALESCE(SUM(cost_amount),0) v FROM bypass_drops WHERE tenant_id=1 AND voided=0 AND tank_id IS NULL AND txn_date >= ?", new Date(`${today}T00:00:00+05:00`).toISOString()).v;
  near(costRows, 3000 * avg, "only the client drop is bypass cost of sales");
  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("bypass_to_tank");
  assert.equal(ok(await call("admin", "GET", "/api/books/check"), "books").ok, true);
});

test("permissions and undo: salesman refused; Corrections refuses the delivery row; void from the bypass page reverses it", async () => {
  assert.equal((await call("salesman", "POST", "/api/bypass/to-tank", { product: "HSD", litres: 10, tank_id: tank.id })).status, 403);
  const del = db.get("SELECT id FROM deliveries WHERE bypass_drop_id=?", transferId);
  const c = await call("admin", "POST", `/api/corrections/delivery/${del.id}/void`, { reason: "test void" });
  assert.equal(c.status, 400); assert.match(c.data.error, /Bypass page/);
  assert.equal((await call("admin", "POST", `/api/bypass/to-tank/${transferId}/void`, { reason: "" })).status, 400, "reason needed");
  ok(await call("admin", "POST", `/api/bypass/to-tank/${transferId}/void`, { reason: "entered twice" }), "void");
  near(db.get("SELECT current_l FROM tanks WHERE id=?", tank.id).current_l, tankBefore, "tank back");
  near(ok(await call("admin", "GET", "/api/bypass/stock"), "stock").by_product.find((x: any) => x.product === "HSD").litres, stockBefore, "bypass stock back");
  assert.equal(db.get("SELECT COUNT(*) n FROM deliveries WHERE bypass_drop_id=?", transferId).n, 0, "stock register row removed");
  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("bypass_to_tank_void");
});

test("one delivery can buy, drop to a client AND unload into our own tank — margin counts only the client drop, tally stays", async () => {
  const tk = db.get("SELECT tk.* FROM tanks tk JOIN stations s ON s.id=tk.station_id WHERE s.tenant_id=1 AND tk.product='HSD' ORDER BY tk.id LIMIT 1");
  db.run("UPDATE tanks SET current_l = 1000 WHERE id=?", tk.id);
  const tankL0 = 1000;
  const cl = db.get("SELECT id FROM wholesale_clients WHERE tenant_id=1 ORDER BY id LIMIT 1").id;
  // buy 1000 L @ 250 = 250,000; drop 600 L to client @ 262; unload 400 L into our own tank
  const r = ok(await call("admin", "POST", "/api/bypass/deliveries", {
    station_id: tk.station_id,
    purchases: [{ supplier_id: supplierId, product: "HSD", litres: 1000, cost_rate: 250 }],
    drops: [{ client_id: cl, product: "HSD", litres: 600, rate: 262 }],
    to_tank: [{ tank_id: tk.id, product: "HSD", litres: 400 }],
  }), "combined delivery");
  near(r.billed, 600 * 262, "client billed = dropped litres × rate");
  near(r.sold_cost, 600 * 250, "COGS = only the 600 L dropped to the client");
  near(r.margin, 600 * 262 - 600 * 250, "margin excludes the own-tank 400 L (that is stock, not a loss)");
  near(r.to_tank_litres, 400, "own-tank litres reported");
  near(db.get("SELECT current_l FROM tanks WHERE id=?", tk.id).current_l, tankL0 + 400, "our tank got the 400 L");
  assert.equal(db.get("SELECT COUNT(*) n FROM deliveries WHERE bypass_drop_id IN (SELECT id FROM bypass_drops WHERE delivery_id=? AND tank_id IS NOT NULL)", r.id).n, 1, "a stock-register row for the own-tank unload");
  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("bypass combined delivery");
  assert.equal(ok(await call("admin", "GET", "/api/books/check"), "books").ok, true);
  // over-stock guard: cannot move (drop + own-tank) more than held + bought
  const over = await call("admin", "POST", "/api/bypass/deliveries", { station_id: tk.station_id, purchases: [{ supplier_id: supplierId, product: "HSD", litres: 100, cost_rate: 250 }], drops: [{ client_id: cl, product: "HSD", litres: 80, rate: 262 }], to_tank: [{ tank_id: tk.id, product: "HSD", litres: 5000 }] });
  assert.equal(over.status, 400); assert.match(over.data.error, /own-tank|available|capacity/);
  // wrong-fuel tank refused
  const petrolTank = db.get("SELECT id FROM tanks WHERE station_id=? AND product='PMG' LIMIT 1", tk.station_id);
  if (petrolTank) { const wf = await call("admin", "POST", "/api/bypass/deliveries", { station_id: tk.station_id, purchases: [{ supplier_id: supplierId, product: "HSD", litres: 50, cost_rate: 250 }], to_tank: [{ tank_id: petrolTank.id, product: "HSD", litres: 50 }] }); assert.equal(wf.status, 400); }
});

test("own-tank unload with a typed amount: that value flows to the stock register, ledger and journal; books tally", async () => {
  const tk = db.get("SELECT tk.* FROM tanks tk JOIN stations s ON s.id=tk.station_id WHERE s.tenant_id=1 AND tk.product='HSD' ORDER BY tk.id LIMIT 1");
  db.run("UPDATE tanks SET current_l = 1000 WHERE id=?", tk.id);
  // buy 500 L @ 250 and unload 500 L into our own tank at a TYPED amount of 130,000 (not 500×250=125,000)
  const r = ok(await call("admin", "POST", "/api/bypass/deliveries", {
    station_id: tk.station_id, txn_date: today,
    purchases: [{ supplier_id: supplierId, product: "HSD", litres: 500, cost_rate: 250 }],
    to_tank: [{ tank_id: tk.id, product: "HSD", litres: 500, amount: 130000 }],
  }), "delivery with typed own-tank amount");
  // the bypass_drop for the tank carries exactly the typed amount at amount/litres
  const drop = db.get("SELECT * FROM bypass_drops WHERE delivery_id=? AND tank_id IS NOT NULL ORDER BY id DESC LIMIT 1", r.id);
  near(drop.cost_amount, 130000, "drop cost = typed amount");
  near(drop.unit_cost, 260, "unit = amount / litres");
  // the stock-register delivery row is valued at the same per-litre
  const del = db.get("SELECT * FROM deliveries WHERE bypass_drop_id=?", drop.id);
  near(del.purchase_rate, 260, "stock register rate = amount / litres");
  // ledger journal: Dr Fuel purchases / Cr Bypass stock at the typed amount
  const j = ok(await call("admin", "GET", `/api/ledger?from=${today}&to=${today}`), "journal");
  const v = j.vouchers.find((x: any) => /Bypass leftover to pump stock/.test(x.narration ?? x.memo ?? JSON.stringify(x)) && JSON.stringify(x).includes("130000"));
  assert.ok(v, "journal voucher carries the typed amount");
  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("bypass own-tank typed amount");
  assert.equal(ok(await call("admin", "GET", "/api/books/check"), "books").ok, true, "books tally with a typed own-tank amount");
});
