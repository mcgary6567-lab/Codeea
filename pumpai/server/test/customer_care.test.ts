/** Khata portal, overdue hold, late charge, government bills with PO, payment notice, service bookings via WhatsApp. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-care-"));
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
const local = (u: string) => u.replace(/^https?:\/\/[^/]+/, base);

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("customer portal: private link shows balance and fills; resetting the link kills the old one", async () => {
  const c = db.get("SELECT * FROM customers WHERE credit_limit > 0 AND balance > 0 ORDER BY id LIMIT 1");
  const { url, pin } = ok(await call("manager", "GET", `/api/customers/${c.id}/portal`), "link");
  assert.match(await (await fetch(local(url))).text(), /Enter the 6-digit PIN/, "PIN asked first");
  const html = await (await fetch(local(url), { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body: `pin=${pin}` })).text();
  assert.match(html, new RegExp(c.name)); assert.match(html, /You have to pay|All paid/); assert.match(html, /Entries/); assert.doesNotMatch(html, /Pay now/, "no payment link unless the pump has set one");
  ok(await call("manager", "POST", `/api/customers/${c.id}/portal/send`, {}), "send");
  const sent = db.get("SELECT m.* FROM messages m JOIN conversations v ON v.id=m.conversation_id WHERE v.customer_id=? AND m.meta LIKE '%portal_link%' ORDER BY m.id DESC", c.id);
  assert.ok(sent && sent.body.includes(pin), "link and PIN sent on WhatsApp");
  const fresh = ok(await call("manager", "POST", `/api/customers/${c.id}/portal/new`, {}), "reset");
  assert.equal((await fetch(local(url))).status, 404, "old link dead");
  assert.equal((await fetch(local(fresh.url))).status, 200);
});

test("overdue khata goes on hold (not institutions), POS refuses it, a payment lifts the hold", async () => {
  const c = db.get("SELECT * FROM customers WHERE credit_limit > 0 AND balance > 0 AND type NOT IN ('police','school','government','hospital') ORDER BY id LIMIT 1");
  const inst = db.get("SELECT * FROM customers WHERE credit_limit > 0 AND balance > 0 AND type='police' ORDER BY id LIMIT 1");
  const old = new Date(Date.now() - 80 * 86_400_000).toISOString();
  for (const x of [c, inst]) { db.run("UPDATE khata_ledger SET created_at=? WHERE customer_id=? AND type='credit'", old, x.id); db.run("UPDATE khata_ledger SET created_at=? WHERE customer_id=?", old, x.id); }
  const { runJob } = await import("../src/automation/scheduler.js");
  assert.match(await runJob(1, "khata_overdue"), /put on hold/);
  assert.equal(db.get("SELECT khata_blocked b FROM customers WHERE id=?", c.id).b, 1);
  assert.equal(db.get("SELECT khata_blocked b FROM customers WHERE id=?", inst.id).b, 0, "institutions left out by default");
  ok(await call("salesman", "POST", "/api/shifts/open", {}), "open");
  const r = await call("salesman", "POST", "/api/sales", { station_id: 1, product: "HSD", litres: 5, payment_method: "khata", customer_id: c.id });
  assert.equal(r.status, 400); assert.match(r.data.error, /on hold/);
  assert.equal(ok(await call("salesman", "GET", "/api/pos/khata-accounts"), "accts").find((a: any) => a.id === c.id).blocked, true);
  ok(await call("manager", "POST", `/api/customers/${c.id}/khata`, { type: "credit", amount: 1000, method: "cash", notify: false }), "payment");
  assert.equal(db.get("SELECT khata_blocked b FROM customers WHERE id=?", c.id).b, 0, "payment lifts hold");
});

test("late charge only when switched on, never for institutions", async () => {
  const { runJob } = await import("../src/automation/scheduler.js");
  assert.match(await runJob(1, "khata_late_fee"), /^0 charges/);
  ok(await call("admin", "PUT", "/api/settings", { khata_rules: { late_fee_pct: 1 } }), "on");
  assert.equal(ok(await call("admin", "GET", "/api/settings"), "s").khata_rules.late_fee_pct, 1);
  const r = await runJob(1, "khata_late_fee");
  const n = Number(r.split(" ")[0]);
  const charged = db.all("SELECT c.type FROM khata_ledger k JOIN customers c ON c.id=k.customer_id WHERE k.ref LIKE 'LATE-%'");
  assert.equal(charged.length, n); assert.ok(charged.every((x: any) => !["police", "school", "government", "hospital"].includes(x.type)));
  assert.match(await runJob(1, "khata_late_fee"), /^0 charges/, "once a month");
});

test("government bill: made from the month's fills, PO and submission recorded, cheque marks it paid", async () => {
  const prev = (() => { const d = new Date(Date.parse(db.pkDate().slice(0, 7) + "-15T00:00:00Z")); d.setUTCMonth(d.getUTCMonth() - 1); return d.toISOString().slice(0, 7); })();
  // an office with fuel taken on khata last month
  const police = db.get(`SELECT c.* FROM customers c WHERE c.type IN ('police','government','school') AND c.credit_limit > 0
    AND EXISTS (SELECT 1 FROM khata_ledger k WHERE k.customer_id=c.id AND k.type='debit' AND k.created_at LIKE ?) ORDER BY c.id DESC LIMIT 1`, `${prev}-1%`);
  const bill = ok(await call("manager", "POST", `/api/customers/${police.id}/govt-bills`, { month: prev }), "bill");
  assert.ok(bill.amount > 0); assert.equal(bill.status, "draft");
  assert.equal((await call("manager", "POST", `/api/customers/${police.id}/govt-bills`, { month: prev })).status, 400, "one per month");
  const sub = ok(await call("manager", "PATCH", `/api/govt-bills/${bill.id}`, { po_number: "PO-2231", submitted_on: db.pkDate() }), "submit");
  assert.equal(sub.status, "submitted");
  const bal0 = db.get("SELECT balance FROM customers WHERE id=?", police.id).balance;
  const half = ok(await call("manager", "POST", `/api/govt-bills/${bill.id}/paid`, { amount: 1000, method: "cheque", ref: "CHQ-1" }), "part");
  assert.equal(half.status, "partly");
  ok(await call("manager", "POST", `/api/govt-bills/${bill.id}/paid`, { amount: bill.amount - 1000, method: "cheque" }), "rest");
  assert.ok(Math.abs(db.get("SELECT balance FROM customers WHERE id=?", police.id).balance - (bal0 - bill.amount)) < 0.02);
  const list = ok(await call("manager", "GET", "/api/govt-bills"), "list");
  assert.equal(list.find((b: any) => b.id === bill.id).status, "paid");
  const notice = await (await fetch(`${base}/api/customers/${police.id}/notice?token=${(await call("manager", "GET", "/api/me")).data.media_token}`)).text();
  assert.match(notice, /NOTICE FOR PAYMENT/); assert.match(notice, /واجب الادا/);
});

test("bookings: customer books on WhatsApp, gets a reminder; staff mark it done", async () => {
  const phone = "923331112244";
  const r = ok(await call("manager", "POST", "/api/whatsapp/simulate", { phone, text: "kal 5 baje car wash book kar dein" }), "wa");
  assert.match(r.reply, /Booking #\d+: Car wash/);
  const c = db.get("SELECT id FROM customers WHERE phone=?", phone);
  const b = db.get("SELECT * FROM bookings WHERE customer_id=?", c.id);
  assert.equal(b.service, "car_wash");
  assert.equal(new Date(Date.parse(b.at) + 5 * 3600_000).toISOString().slice(11, 16), "17:00");
  const ask = ok(await call("manager", "POST", "/api/whatsapp/simulate", { phone, text: "oil change karwana hai" }), "ask");
  assert.match(ask.reply, /Kis din/);
  // staff booking for soon → reminder
  const soon = new Date(Date.now() + 40 * 60_000).toISOString();
  const bk = ok(await call("salesman", "POST", "/api/bookings", { phone: "923001119999", name: "Walk-in", service: "oil_change", at: soon }), "staff booking");
  const { runJob } = await import("../src/automation/scheduler.js");
  assert.match(await runJob(1, "booking_reminders"), /^1 reminders/);
  ok(await call("salesman", "POST", `/api/bookings/${bk.id}/done`, {}), "done");
  const list = ok(await call("salesman", "GET", "/api/bookings"), "list");
  assert.ok(list.some((x: any) => x.id === bk.id && x.status === "done"));
  const cancel = ok(await call("manager", "POST", "/api/whatsapp/simulate", { phone, text: "booking cancel kar dein" }), "cancel");
  assert.match(cancel.reply, /cancel kar di/);
});
