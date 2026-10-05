import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-wdesk-"));
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
const PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
const photo = async (who: string) => ok(await call(who, "POST", "/api/ai/read-photo", { kind: "proof", image: PNG }), "photo").photo_id as number;
const day = (n: number) => new Date(Date.now() + 5 * 3600_000 + n * 86_400_000).toISOString().slice(0, 10);

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "wholesale"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => server?.close());

test("order book: a booked order is closed by the supply or trip that delivers it", async () => {
  assert.equal((await call("manager", "GET", "/api/wholesale/orders")).status, 403);
  const [a, b] = ok(await call("wholesale", "GET", "/api/wholesale/clients"), "clients").filter((c: any) => c.rates.HSD);
  const st = ok(await call("admin", "GET", "/api/stations"), "stations")[0];
  const o1 = ok(await call("wholesale", "POST", `/api/wholesale/clients/${a.id}/orders`, { product: "HSD", litres: 500, needed_on: day(0), location: "Site A" }), "order 1");
  const o2 = ok(await call("wholesale", "POST", `/api/wholesale/clients/${b.id}/orders`, { product: "HSD", litres: 400, needed_on: day(1) }), "order 2");
  assert.equal((await call("wholesale", "POST", `/api/wholesale/clients/${a.id}/orders`, { product: "HSD", litres: 400, needed_on: day(-5) })).status, 400, "past date");
  let book = ok(await call("wholesale", "GET", "/api/wholesale/orders"), "orders");
  assert.ok(book.open.find((o: any) => o.id === o1.id).today);
  assert.ok(book.need_3_days.HSD >= 900);

  // the wrong client's order cannot be closed
  assert.equal((await call("wholesale", "POST", `/api/wholesale/clients/${b.id}/supply`, { station_id: st.id, product: "HSD", litres: 100, order_id: o1.id })).status, 400);
  ok(await call("wholesale", "POST", `/api/wholesale/clients/${a.id}/supply`, { station_id: st.id, product: "HSD", litres: 500, order_id: o1.id }), "supply");
  ok(await call("wholesale", "POST", "/api/wholesale/trips", { station_id: st.id, product: "HSD", drops: [{ client_id: b.id, litres: 400, order_id: o2.id }] }), "trip");
  book = ok(await call("wholesale", "GET", "/api/wholesale/orders"), "orders");
  assert.ok(!book.open.some((o: any) => [o1.id, o2.id].includes(o.id)));
  assert.equal(db.get("SELECT status FROM wholesale_orders WHERE id=?", o1.id)!.status, "done");
  assert.ok(db.get("SELECT txn_id FROM wholesale_orders WHERE id=?", o2.id)!.txn_id);

  const o3 = ok(await call("wholesale", "POST", `/api/wholesale/clients/${a.id}/orders`, { product: "HSD", litres: 300, needed_on: day(2) }), "order 3");
  ok(await call("wholesale", "PATCH", `/api/wholesale/orders/${o3.id}`, { cancel: "Client bought elsewhere" }), "cancel");
  assert.equal((await call("wholesale", "PATCH", `/api/wholesale/orders/${o3.id}`, { litres: 10 })).status, 400, "closed order");
});

test("promises: kept when the money comes, broken when the date passes; call list and suggestions", async () => {
  const [a, b] = ok(await call("wholesale", "GET", "/api/wholesale/clients"), "clients").filter((c: any) => c.due > 100_000);
  const p = ok(await call("wholesale", "POST", `/api/wholesale/clients/${a.id}/promises`, { amount: 50_000, promised_on: day(0) }), "promise");
  assert.equal(p.state, "today");
  ok(await call("wholesale", "POST", `/api/wholesale/clients/${a.id}/payment`, { amount: 50_000, method: "Bank transfer" }), "pay");
  assert.equal(ok(await call("wholesale", "GET", `/api/wholesale/clients/${a.id}/desk`), "desk").promises.find((x: any) => x.id === p.id).state, "kept");
  // a promise made earlier for yesterday and not paid
  db.run("INSERT INTO wholesale_promises (tenant_id,client_id,amount,promised_on,status,created_by,created_at) VALUES (1,?,?,?,?,?,?)", b.id, 99_000_000, day(-1), "open", "test", new Date(Date.now() - 3 * 86_400_000).toISOString());
  const col = ok(await call("wholesale", "GET", "/api/wholesale/collect"), "collect");
  const row = col.calls.find((c: any) => c.client_id === b.id);
  assert.ok(row && row.reasons.some((r: string) => /not paid/.test(r)), JSON.stringify(row));
  assert.equal(col.calls[0].score >= row.score, true);
  const dash = ok(await call("wholesale", "GET", "/api/wholesale/dashboard"), "dashboard");
  assert.ok(dash.suggestions.some((s: any) => /broke a promise/.test(s.title)));
  assert.ok(dash.kpi.broken_promises >= 1);
});

test("cheques: in hand → deposit → clear makes the payment into the bank; a bounce takes it back", async () => {
  const [c] = ok(await call("wholesale", "GET", "/api/wholesale/clients"), "clients").filter((x: any) => x.due > 100_000);
  const acc = ok(await call("admin", "POST", "/api/bank/accounts", { bank: "Meezan Bank", branch: "Test" }), "account");
  const dueBefore = c.due;
  assert.equal((await call("wholesale", "POST", `/api/wholesale/clients/${c.id}/cheques`, { amount: 90_000, bank: "MCB Bank", cheque_no: "111222", cheque_date: day(0) })).status, 400, "photo needed");
  const q = ok(await call("wholesale", "POST", `/api/wholesale/clients/${c.id}/cheques`, { amount: 90_000, bank: "MCB Bank", cheque_no: "111222", cheque_date: day(0), photo_ids: [await photo("wholesale")] }), "cheque");
  assert.equal((await call("wholesale", "POST", `/api/wholesale/clients/${c.id}/cheques`, { amount: 1, bank: "MCB Bank", cheque_no: "111222", cheque_date: day(0), photo_ids: [await photo("wholesale")] })).status, 400, "same cheque twice");
  // a post-dated cheque cannot be deposited early
  const pd = ok(await call("wholesale", "POST", `/api/wholesale/clients/${c.id}/cheques`, { amount: 40_000, bank: "HBL", cheque_no: "999", cheque_date: day(5), photo_ids: [await photo("wholesale")] }), "pdc");
  assert.equal((await call("wholesale", "POST", `/api/wholesale/cheques/${pd.id}/deposit`, {})).status, 400);
  near(ok(await call("wholesale", "GET", `/api/wholesale/clients/${c.id}`), "client").summary.due, dueBefore, "a cheque in hand is not a payment yet");

  ok(await call("wholesale", "POST", `/api/wholesale/cheques/${q.id}/deposit`, { account_id: acc.id }), "deposit");
  const cl = ok(await call("wholesale", "POST", `/api/wholesale/cheques/${q.id}/clear`, {}), "clear");
  near(cl.due_after, dueBefore - 90_000, "cleared cheque is paid");
  const pay = db.get("SELECT * FROM wholesale_txns WHERE id=?", cl.cheque.payment_txn_id)!;
  assert.equal(pay.account_id, acc.id);
  assert.equal(ok(await call("admin", "GET", "/api/bank/accounts"), "banks").accounts.find((a: any) => a.id === acc.id).balance, 90_000);

  const bo = ok(await call("wholesale", "POST", `/api/wholesale/cheques/${q.id}/bounce`, { reason: "Insufficient funds", charge: 1500 }), "bounce");
  near(bo.due_after, dueBefore + 1500, "payment taken back plus charges");
  assert.equal(db.get("SELECT voided FROM wholesale_txns WHERE id=?", pay.id)!.voided, 1);
  assert.equal(ok(await call("admin", "GET", "/api/bank/accounts"), "banks").accounts.find((a: any) => a.id === acc.id).balance, 0);
  assert.ok(db.get("SELECT id FROM alerts WHERE type='cheque_bounced' ORDER BY id DESC LIMIT 1"));
  const dash = ok(await call("wholesale", "GET", "/api/wholesale/dashboard"), "dashboard");
  assert.ok(dash.suggestions.some((s: any) => /bounced in 90 days/.test(s.title)));

  ok(await call("wholesale", "POST", `/api/wholesale/cheques/${pd.id}/return`, { reason: "Paid in cash instead" }), "return");
  assert.equal((await call("wholesale", "POST", `/api/wholesale/cheques/${pd.id}/clear`, {})).status, 400);
  const col = ok(await call("wholesale", "GET", "/api/wholesale/collect"), "collect");
  assert.ok(col.cheques.some((x: any) => x.id === q.id && x.status === "bounced"));
});

test("voice command: a sentence becomes the entry to confirm, questions get an answer", async () => {
  const cmd = async (text: string, extra: object = {}) => ok(await call("wholesale", "POST", "/api/wholesale/ai/command", { text, ...extra }), text);
  const clients = ok(await call("wholesale", "GET", "/api/wholesale/clients"), "clients").filter((c: any) => c.rates.HSD);
  const [a, b] = clients;
  const first = (name: string) => name.split(" ")[0];
  const s = await cmd(`${first(a.name)} ko 2000 litre diesel bheja`);
  assert.equal(s.intent, "supply"); assert.equal(s.client_id, a.id); assert.equal(s.litres, 2000); assert.equal(s.product, "HSD");
  assert.equal(s.preview.rate, a.rates.HSD); near(s.preview.amount, a.rates.HSD * 2000);
  assert.ok(s.station_id); assert.match(s.confirm_ur, /لیٹر/);
  const p = await cmd(`${a.name} se dedh lakh bank transfer mila`);
  assert.equal(p.intent, "payment"); assert.equal(p.amount, 150000); assert.equal(p.method, "Bank transfer"); assert.equal(p.product, null);
  const o = await cmd(`${first(b.name)} ka kal 6000 litre diesel ka order`);
  assert.equal(o.intent, "order"); assert.equal(o.date, day(1));
  const bal = await cmd(`${first(a.name)} ka baqaya kitna hai`);
  assert.equal(bal.intent, "balance"); assert.match(bal.answer.en, /owes Rs/); assert.match(bal.answer.ur, /روپے/);
  const tr = await cmd(`${first(a.name)} ko 3000 aur ${first(b.name)} ko 2000 litre diesel`);
  assert.equal(tr.intent, "trip"); assert.equal(tr.drops.length, 2); assert.equal(tr.preview.drops[0].rate, a.rates.HSD);
  // on a client's page the client is already known
  const pg = await cmd("5 lakh ka cheque HBL number 556677", { client_id: b.id });
  assert.equal(pg.intent, "cheque"); assert.equal(pg.client_id, b.id); assert.equal(pg.bank, "Habib Bank (HBL)"); assert.equal(pg.cheque_no, "556677");
  assert.ok((await cmd("aaj kitni supply hui")).answer.ur);
  assert.equal((await call("manager", "POST", "/api/wholesale/ai/command", { text: "test" })).status, 403);
  // suggestions carry an Urdu line too
  const dash = ok(await call("wholesale", "GET", "/api/wholesale/dashboard"), "dashboard");
  assert.ok(dash.suggestions.every((x: any) => x.ur), JSON.stringify(dash.suggestions.filter((x: any) => !x.ur)));
});
