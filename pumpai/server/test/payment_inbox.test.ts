/** A khata customer WhatsApps a payment screenshot → it queues in the cashier's payment inbox → confirm posts a khata credit, books tally. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";
import { tallyBooks } from "./helpers/tally.js";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-payin-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

// a 1x1 PNG, enough to carry through the flow (no AI key in the test, so it is just saved, amount filled by the cashier)
const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";

let server: Server, base = "", admin = "";
async function call(tok: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tok ? { authorization: `Bearer ${tok}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, m: string) => { assert.equal(r.status, 200, `${m}: ${JSON.stringify(r.data)}`); return r.data; };

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  admin = (await call("", "POST", "/api/auth/login", { email: "admin@pumpai.pk", password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("payment screenshot → inbox → cashier confirms → khata credit, books tally", async () => {
  const cust = ok(await call(admin, "GET", "/api/customers"), "customers").find((c: any) => c.phone);
  assert.ok(cust, "a customer with a phone exists");
  const balBefore = cust.balance;

  // the customer WhatsApps a payment screenshot (simulated inbound image)
  const sim = ok(await call(admin, "POST", "/api/whatsapp/simulate", { phone: cust.phone, name: cust.name, image: PNG, image_caption: "paid" }), "simulate image");
  assert.equal(sim.handled_by, "payment_screenshot", "handled as a payment screenshot");
  assert.ok(sim.queued, "queued for the cashier");

  // it shows in the cashier's inbox as pending
  const inbox = ok(await call(admin, "GET", "/api/payment-inbox"), "inbox");
  const row = inbox.payments.find((p: any) => p.id === sim.payment_inbox_id);
  assert.ok(row && row.status === "pending" && row.customer_id === cust.id, "pending row for this customer");
  assert.ok(row.photo_id, "the screenshot photo was saved");

  await tallyBooks("before confirm");
  // cashier confirms with the amount they see on the screenshot
  const conf = ok(await call(admin, "POST", `/api/payment-inbox/${row.id}/confirm`, { amount: 2500, method: "Easypaisa" }), "confirm");
  assert.equal(Math.round(conf.balance_after), Math.round(balBefore - 2500), "khata balance came down by the payment");
  await tallyBooks("after confirm");

  // confirming again is refused
  assert.equal((await call(admin, "POST", `/api/payment-inbox/${row.id}/confirm`, { amount: 2500 })).status, 400);
});

test("a screenshot can be rejected without posting anything, and an unknown number is not queued", async () => {
  const cust = ok(await call(admin, "GET", "/api/customers"), "customers").find((c: any) => c.phone);
  const sim = ok(await call(admin, "POST", "/api/whatsapp/simulate", { phone: cust.phone, image: PNG }), "sim2");
  const id = sim.payment_inbox_id;
  assert.equal((await call(admin, "POST", `/api/payment-inbox/${id}/reject`, { reason: "x" })).status, 400, "reason too short");
  ok(await call(admin, "POST", `/api/payment-inbox/${id}/reject`, { reason: "duplicate screenshot" }), "reject");
  assert.ok(!ok(await call(admin, "GET", "/api/payment-inbox"), "inbox").payments.find((p: any) => p.id === id), "rejected row off the pending list");
  await tallyBooks("after reject");

  // a number that belongs to no customer is acknowledged but not queued
  const unknown = ok(await call(admin, "POST", "/api/whatsapp/simulate", { phone: "923459999999", image: PNG }), "unknown");
  assert.equal(unknown.queued, false, "unknown number not queued");
});
