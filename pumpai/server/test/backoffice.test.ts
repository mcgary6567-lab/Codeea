/** Cash book & bank deposits, one-tap tanker orders, dip charts, midnight day close, owner questions on WhatsApp. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-backoffice-"));
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
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("cash book: counted cash + shift cash + cash received − bank deposit − cash expenses", async () => {
  const first = ok(await call("manager", "POST", "/api/cash/count", { amount: 50000 }), "first count");
  assert.equal(first.variance, 0, "first count sets the starting cash"); near(first.cash_in_hand, 50000);
  const shift = ok(await call("salesman", "POST", "/api/shifts/open", {}), "open");
  const live = ok(await call("salesman", "GET", `/api/shifts/${shift.id}/live`), "live");
  ok(await call("salesman", "POST", `/api/shifts/${shift.id}/close`, { readings: Object.fromEntries(live.readings.map((r: any) => [r.nozzle_id, r.opening])), cash_actual: 10000 }), "close");
  const acct = ok(await call("manager", "GET", "/api/pos/khata-accounts"), "accts").find((a: any) => a.balance > 2000);
  ok(await call("manager", "POST", `/api/customers/${acct.id}/khata`, { type: "credit", amount: 2000, method: "cash", notify: false }), "khata cash");
  near(ok(await call("manager", "GET", "/api/cash"), "cash").cash_in_hand, 62000, "after shift + khata cash");
  const dep = ok(await call("manager", "POST", "/api/cash/deposits", { amount: 30000, bank: "HBL", slip_ref: "DS-1" }), "deposit");
  near(dep.cash_in_hand, 32000);
  assert.equal((await call("manager", "POST", "/api/cash/deposits", { amount: 999999, bank: "HBL" })).status, 400, "cannot deposit more than in hand");
  ok(await call("manager", "POST", "/api/expenses", { category: "Tea & food", amount: 500, method: "cash" }), "cash expense");
  near(ok(await call("manager", "GET", "/api/cash"), "cash").cash_in_hand, 31500);
  const count = ok(await call("manager", "POST", "/api/cash/count", { amount: 30000 }), "count");
  near(count.variance, -1500, "short against the book");
  assert.ok(db.get("SELECT id FROM alerts WHERE type='cash_count'"), "alert raised");
  assert.equal((await call("salesman", "GET", "/api/cash")).status, 403);
});

test("tanker order in one tap goes to the supplier on WhatsApp and closes when the tanker arrives", async () => {
  const tank = ok(await call("manager", "GET", "/api/stations"), "stations")[0].tanks[0];
  // demo stock depends on the time of day the data was made; leave room for a 5,000 L tanker
  db.run("UPDATE tanks SET current_l = capacity_l - 12000 WHERE id=?", tank.id);
  const sug = ok(await call("manager", "GET", `/api/stock/order-suggestion/${tank.id}`), "suggestion");
  assert.ok(sug.supplier_id, "last supplier suggested");
  assert.equal((await call("manager", "POST", "/api/stock/orders", { tank_id: tank.id, supplier_id: sug.supplier_id, litres: sug.room + 5000 })).status, 400, "no space");
  const o = ok(await call("manager", "POST", "/api/stock/orders", { tank_id: tank.id, supplier_id: sug.supplier_id, litres: 5000 }), "order");
  assert.equal(o.order.status, "ordered");
  const msg = db.get("SELECT * FROM outbox WHERE kind='tanker_order' ORDER BY id DESC LIMIT 1");
  assert.match(msg.text, /5,000 L/); assert.equal(msg.ref, `po:${o.order.id}`);
  ok(await call("manager", "POST", "/api/stock/delivery", { tank_id: tank.id, invoice_l: 5000, received_l: 4990, supplier_id: sug.supplier_id, purchase_rate: 250 }), "delivery");
  const after = ok(await call("manager", "GET", "/api/stock/orders"), "orders").find((x: any) => x.id === o.order.id);
  assert.equal(after.status, "delivered"); assert.ok(after.delivery_id);
});

test("dip chart: dip in cm becomes litres", async () => {
  const tank = ok(await call("manager", "GET", "/api/stations"), "stations")[0].tanks[1];
  const chart = ok(await call("manager", "GET", `/api/tanks/${tank.id}/chart`), "chart");
  assert.ok(chart.rows.length > 20, "demo tanks have a chart");
  const top = chart.rows.at(-1);
  near(ok(await call("manager", "GET", `/api/tanks/${tank.id}/dip-litres?cm=0`), "0 cm").litres, 0);
  near(ok(await call("manager", "GET", `/api/tanks/${tank.id}/dip-litres?cm=${top.cm}`), "full").litres, tank.capacity_l);
  const mid = (chart.rows[10].litres + chart.rows[11].litres) / 2;
  const cm = (chart.rows[10].cm + chart.rows[11].cm) / 2;
  near(ok(await call("manager", "GET", `/api/tanks/${tank.id}/dip-litres?cm=${cm}`), "mid").litres, mid, "interpolated");
  ok(await call("manager", "POST", "/api/stock/dip", { tank_id: tank.id, measured_cm: chart.rows[20].cm }), "dip");
  near(ok(await call("manager", "GET", "/api/stations"), "stations")[0].tanks[1].current_l, chart.rows[20].litres, "tank set from chart");
  assert.equal((await call("manager", "PUT", `/api/tanks/${tank.id}/chart`, { rows: [{ cm: 0, litres: 0 }, { cm: 10, litres: 500 }, { cm: 20, litres: 400 }] })).status, 400, "litres must not go down");
  const gen = ok(await call("manager", "PUT", `/api/tanks/${tank.id}/chart`, { diameter_cm: 250 }), "generate");
  near(gen.max_litres, tank.capacity_l);
});

test("midnight day close: owner gets the report link and the day is locked for non-admins", async () => {
  const yesterday = db.pkDate(Date.now() - 86_400_000);
  const e = ok(await call("manager", "POST", "/api/expenses", { category: "Other", amount: 15000, expense_date: yesterday }), "pending expense yesterday");
  assert.equal(e.status, "pending");
  const { runJob } = await import("../src/automation/scheduler.js");
  assert.match(await runJob(1, "day_close"), new RegExp(`${yesterday} closed`));
  assert.match(await runJob(1, "day_close"), /already closed/);
  const msg = db.get("SELECT * FROM outbox WHERE kind='day_close' ORDER BY id DESC LIMIT 1");
  assert.match(msg.text, /Din band/); const link = msg.text.match(/\/day\/(\S+)/)[1];
  const html = await (await fetch(`${base}/day/${link}`)).text();
  assert.match(html, /day report/); assert.match(html, /Office cash/);
  const r = await call("manager", "PATCH", `/api/expenses/${e.id}`, { amount: 900 });
  assert.equal(r.status, 400); assert.match(r.data.error, /closed/);
  assert.equal((await call("manager", "POST", "/api/expenses", { category: "Other", amount: 100, expense_date: yesterday })).status, 400);
  ok(await call("admin", "PATCH", `/api/expenses/${e.id}`, { amount: 900 }), "admin can still fix it");
  const list = ok(await call("manager", "GET", "/api/day-closes"), "closes");
  assert.equal(list[0].day, yesterday); assert.match(list[0].url, /\/day\//);
});

test("owner asks on WhatsApp and gets the numbers back", async () => {
  const owner = db.get("SELECT owner_phone FROM tenants WHERE id=1").owner_phone;
  const r = ok(await call("manager", "POST", "/api/whatsapp/simulate", { phone: owner, text: "aaj ki sale kitni hai aur stock kitna bacha?" }), "owner q");
  assert.equal(r.handled_by, "owner_assistant");
  assert.match(r.reply, /Aaj ki sale: Rs/); assert.match(r.reply, /Stock:/);
  assert.equal(db.get("SELECT * FROM outbox WHERE kind='owner_answer' ORDER BY id DESC LIMIT 1").text, r.reply);
  const k = ok(await call("manager", "POST", "/api/whatsapp/simulate", { phone: owner, text: "kis ne paise dene hain?" }), "owe");
  assert.match(k.reply, /Log hamein denge/);
  const other = ok(await call("manager", "POST", "/api/whatsapp/simulate", { phone: "03001112233", text: "petrol ka rate kya hai?" }), "customer");
  assert.notEqual(other.handled_by, "owner_assistant");
});
