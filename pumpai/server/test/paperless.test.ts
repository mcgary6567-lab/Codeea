/** No manual khata: automatic receipts, monthly bill links, wholesale messages, QR cards and the staff account. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-paperless-"));
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
const tick = () => new Promise((r) => setTimeout(r, 80));
const lastMsg = (customerId: number, kind: string) => db.get(`SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id
  WHERE c.customer_id=? AND m.meta LIKE ? ORDER BY m.id DESC LIMIT 1`, customerId, `%"kind":"${kind}"%`);
const outbox = (kind: string) => db.all("SELECT * FROM outbox WHERE kind=? ORDER BY id DESC", kind);

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman", "wholesale"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

let acct: any, shift: any;
test("every khata fill sends the customer a WhatsApp receipt with the new balance", async () => {
  shift = ok(await call("salesman", "POST", "/api/shifts/open", {}), "open");
  acct = ok(await call("salesman", "GET", "/api/pos/khata-accounts"), "accts").find((a: any) => a.type === "police" && a.status === "ok");
  const s = ok(await call("salesman", "POST", "/api/sales", { station_id: 1, product: "HSD", litres: 15, payment_method: "khata", customer_id: acct.id, vehicle_no: "LEB-1122", slip_no: "P-55" }), "sale");
  await tick();
  const m = lastMsg(acct.id, "khata_receipt");
  assert.ok(m, "receipt sent");
  assert.match(m.body, /15 L × Rs/); assert.match(m.body, /Slip P-55/); assert.match(m.body, /Khata balance/);
  assert.equal(JSON.parse(m.meta).sale_id, s.id);
  ok(await call("admin", "PUT", "/api/settings", { automation: { khata_receipts: false } }), "off");
  assert.equal(ok(await call("admin", "GET", "/api/settings"), "settings").automation.khata_receipts, false);
  ok(await call("salesman", "POST", "/api/sales", { station_id: 1, product: "HSD", litres: 5, payment_method: "khata", customer_id: acct.id }), "sale 2");
  await tick();
  assert.equal(JSON.parse(lastMsg(acct.id, "khata_receipt").meta).sale_id, s.id, "no receipt when switched off");
  ok(await call("admin", "PUT", "/api/settings", { automation: { khata_receipts: true } }), "on");
});

test("monthly bill: a private printable link with every fill and slip, sent on WhatsApp", async () => {
  const month = db.pkDate().slice(0, 7);
  const link = ok(await call("manager", "GET", `/api/customers/${acct.id}/bill-link?month=${month}`), "link");
  const html = await (await fetch(link.url.replace(/^https?:\/\/[^/]+/, base))).text();
  assert.match(html, new RegExp(acct.name)); assert.match(html, /P-55/); assert.match(html, /LEB-1122/); assert.match(html, /Balance due/);
  assert.equal((await fetch(`${base}/bill/not-a-token`)).status, 404);
  const sent = ok(await call("manager", "POST", `/api/customers/${acct.id}/send-bill`, { month }), "send");
  assert.ok(sent.charged > 0);
  assert.match(lastMsg(acct.id, "monthly_bill").body, /\/bill\//);
  assert.equal((await call("salesman", "GET", `/api/customers/${acct.id}/bill-link`)).status, 403);
});

test("wholesale clients get a message for every supply, payment and rate change, and a monthly statement", async () => {
  const clients = ok(await call("wholesale", "GET", "/api/wholesale/clients"), "clients");
  const malik = clients.find((c: any) => c.name.startsWith("Malik"));
  const sup = ok(await call("wholesale", "POST", `/api/wholesale/clients/${malik.id}/supply`, { station_id: 1, product: "HSD", litres: 500, vehicle_no: "TLR-9" }), "supply");
  await tick();
  const o = outbox("wholesale_supply")[0];
  assert.equal(o.ref, `wtxn:${sup.id}`); assert.match(o.text, /500 L/); assert.match(o.text, /Balance due now/); assert.equal(o.to_phone, malik.phone);
  ok(await call("wholesale", "POST", `/api/wholesale/clients/${malik.id}/payment`, { amount: 100000, method: "cash" }), "payment");
  await tick();
  assert.match(outbox("wholesale_payment")[0].text, /Payment received: Rs 1(,00|00),000 \(cash\)/);
  const pump = ok(await call("admin", "GET", "/api/prices"), "prices").current.HSD.price;
  ok(await call("admin", "POST", "/api/prices", { prices: { HSD: pump + 2 } }), "price");
  const rateMsg = outbox("wholesale_rate").find((x: any) => x.to_phone === malik.phone);
  assert.match(rateMsg.text, new RegExp(`Rs ${pump + 2 - 4}/L`)); // Malik is on pump − Rs 4
  ok(await call("wholesale", "POST", `/api/wholesale/clients/${malik.id}/send-statement`, { month: db.pkDate().slice(0, 7) }), "statement");
  assert.match(outbox("wholesale_statement")[0].text, /\/bill\//);
  const { runJob } = await import("../src/automation/scheduler.js");
  assert.match(await runJob(1, "monthly_bills"), /khata bills, \d+ wholesale statements sent/);
});

test("QR cards: scanning the vehicle sticker picks the khata account and vehicle", async () => {
  const withCar = ok(await call("admin", "GET", "/api/customers?limit=500"), "customers");
  const list = Array.isArray(withCar) ? withCar : withCar.customers ?? withCar.items;
  const c = list.find((x: any) => x.credit_limit > 0 && x.id === acct.id) ?? list.find((x: any) => x.credit_limit > 0);
  ok(await call("manager", "POST", `/api/customers/${c.id}/vehicles`, { plate_no: "QRT-4455" }), "vehicle");
  const cards = ok(await call("manager", "GET", `/api/customers/${c.id}/cards`), "cards");
  assert.match(cards.account_code, /^[A-Z2-9]{8}$/);
  const v = cards.vehicles.find((x: any) => x.plate_no === "QRT-4455");
  const scan = ok(await call("salesman", "GET", `/api/pos/card/PUMPAI-${v.code}`), "scan");
  assert.equal(scan.account.id, c.id); assert.equal(scan.vehicle, "QRT-4455"); assert.equal(scan.account.balance, undefined, "salesman sees no balance");
  const acc = ok(await call("salesman", "GET", `/api/pos/card/${cards.account_code}`), "account card");
  assert.equal(acc.vehicle, null);
  ok(await call("manager", "POST", `/api/customers/${c.id}/cards/reissue`, { vehicle_id: v.id }), "reissue");
  assert.equal((await call("salesman", "GET", `/api/pos/card/${v.code}`)).status, 404, "old sticker stops working");
  assert.equal((await call("salesman", "GET", `/api/customers/${c.id}/cards`)).status, 403);
});

test("staff account: cash short goes on the salesman's account; salary pays it back", async () => {
  const live = ok(await call("salesman", "GET", `/api/shifts/${shift.id}/live`), "live");
  const readings = Object.fromEntries(live.readings.map((r: any) => [r.nozzle_id, r.opening + 10]));
  const base = ok(await call("salesman", "GET", "/api/me/account"), "me before").balance; // demo data has an earlier advance
  const closed = ok(await call("salesman", "POST", `/api/shifts/${shift.id}/close`, { readings, cash_actual: 0 }), "close");
  const short = -closed.variance;
  assert.ok(short > 100);
  const mine = ok(await call("salesman", "GET", "/api/me/account"), "me");
  assert.ok(Math.abs(mine.balance - base - short) < 0.02); assert.equal(mine.lines[0].type, "shortage"); assert.equal(mine.lines[0].ref, `shift:${shift.id}`);
  const staff = ok(await call("manager", "GET", "/api/staff"), "staff");
  const imran = staff.find((u: any) => u.name === "Imran");
  assert.ok(Math.abs(imran.shortages_this_month - short) < 0.02);
  ok(await call("manager", "POST", `/api/staff/${imran.id}/entry`, { type: "advance", amount: 2000, note: "family" }), "advance");
  ok(await call("admin", "PATCH", `/api/staff/${imran.id}`, { salary: null }), "clear salary");
  assert.equal((await call("manager", "POST", `/api/staff/${imran.id}/pay-salary`, {})).status, 400, "salary not set");
  ok(await call("admin", "PATCH", `/api/staff/${imran.id}`, { salary: 30000 }), "salary");
  const owed = base + short + 2000;
  assert.equal((await call("manager", "POST", `/api/staff/${imran.id}/pay-salary`, { deduct: owed + 10 })).status, 400, "cannot deduct more than owed");
  const paid = ok(await call("manager", "POST", `/api/staff/${imran.id}/pay-salary`, { deduct: owed, bonus: 1000 }), "pay");
  assert.ok(Math.abs(paid.net - (31000 - owed)) < 0.02); assert.ok(Math.abs(paid.balance) < 0.02);
  const exp = db.get("SELECT * FROM expenses WHERE category='Salaries & wages' AND paid_to='Imran' ORDER BY id DESC LIMIT 1");
  assert.equal(exp.amount, 31000);
  assert.equal((await call("manager", "POST", `/api/staff/${imran.id}/pay-salary`, {})).status, 400, "paid once a month");
  assert.equal((await call("salesman", "GET", "/api/staff")).status, 403);
});
