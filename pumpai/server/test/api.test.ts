import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = ""; // exercise the offline rule engine
process.env.WA_TOKEN = "";

let server: Server;
let base = "";
let token = "";

async function api(method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, {
    method, headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data: any = await res.json().catch(() => null);
  return { status: res.status, data };
}

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  const r = await api("POST", "/api/auth/login", { email: "admin@pumpai.pk", password: "demo1234" });
  token = r.data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("rejects bad login and unauthenticated calls", async () => {
  const saved = token; token = "";
  assert.equal((await api("POST", "/api/auth/login", { email: "admin@pumpai.pk", password: "nope" })).status, 401);
  assert.equal((await api("GET", "/api/dashboard")).status, 401);
  token = saved;
});

test("dashboard returns KPIs, forecast, tanks and insights", async () => {
  const { status, data } = await api("GET", "/api/dashboard");
  assert.equal(status, 200);
  assert.ok(data.kpis.today.litres > 0);
  assert.equal(data.forecast.futureDates.length, 7);
  assert.ok(data.tanks.length === 5 && data.tanks.every((t: any) => t.days_to_empty >= 0));
  assert.ok(data.insights.length > 0);
});

test("WhatsApp simulator: price, order and confirm flow via rule engine", async () => {
  let r = await api("POST", "/api/whatsapp/simulate", { phone: "03214445566", name: "Farmer Test", text: "aaj diesel ka rate kya hai" });
  assert.match(r.data.reply, /Diesel/);
  r = await api("POST", "/api/whatsapp/simulate", { phone: "03214445566", text: "confirm 600 litre diesel chahiye" });
  assert.match(r.data.reply, /Order #\d+/);
  const orders = (await api("GET", "/api/orders")).data;
  assert.ok(orders.some((o: any) => o.litres === 600 && o.status === "pending"));
});

test("human handoff stops AI replies until switched back", async () => {
  await api("POST", "/api/whatsapp/simulate", { phone: "03007778899", text: "manager se baat karni hai" });
  const r = await api("POST", "/api/whatsapp/simulate", { phone: "03007778899", text: "hello?" });
  assert.equal(r.data.handled_by, "human_queue");
  const conv = (await api("GET", "/api/whatsapp/conversations")).data.find((c: any) => c.phone === "923007778899");
  assert.equal(conv.mode, "human");
  await api("PATCH", `/api/whatsapp/conversations/${conv.id}`, { mode: "ai" });
  const again = await api("POST", "/api/whatsapp/simulate", { phone: "03007778899", text: "rate?" });
  assert.ok(again.data.reply);
});

test("shift open/close books unrecorded meter litres as cash and flags shortage", async () => {
  const st = (await api("GET", "/api/stations")).data[0];
  const shift = (await api("POST", "/api/shifts/open", { station_id: st.id, attendant: "Test Attendant" })).data;
  const readings: Record<string, number> = {};
  for (const n of st.nozzles) readings[n.id] = n.totalizer + 10; // 10 L per nozzle
  const closed = await api("POST", `/api/shifts/${shift.id}/close`, { readings, cash_actual: 100 });
  assert.equal(closed.status, 200);
  assert.equal(closed.data.status, "closed");
  assert.equal(Math.round(closed.data.litres), 10 * st.nozzles.length);
  assert.ok(closed.data.variance < -500);
  const alerts = (await api("GET", "/api/alerts")).data;
  assert.ok(alerts.some((a: any) => a.type === "cash_short" && a.title.includes("Test Attendant")));
});

test("khata sale respects credit limit and payment reduces balance", async () => {
  const c = (await api("POST", "/api/customers", { name: "Khata Test", phone: "03335550001", credit_limit: 10000 })).data;
  const st = (await api("GET", "/api/stations")).data[0];
  const over = await api("POST", "/api/sales", { station_id: st.id, product: "PMG", litres: 100, payment_method: "khata", customer_id: c.id });
  assert.equal(over.status, 400);
  const ok = await api("POST", "/api/sales", { station_id: st.id, product: "PMG", amount: 5000, payment_method: "khata", customer_id: c.id });
  assert.equal(ok.status, 200);
  let detail = (await api("GET", `/api/customers/${c.id}`)).data;
  assert.equal(Math.round(detail.balance), 5000);
  await api("POST", `/api/customers/${c.id}/khata`, { type: "credit", amount: 2000, method: "JazzCash" });
  detail = (await api("GET", `/api/customers/${c.id}`)).data;
  assert.equal(Math.round(detail.balance), 3000);
  assert.ok(detail.conversation, "payment receipt was sent on WhatsApp");
});

test("dip variance and short delivery create alerts", async () => {
  const tank = (await api("GET", "/api/stations")).data.flatMap((s: any) => s.tanks).find((t: any) => t.capacity_l - t.current_l * 0.98 > 6000);
  const dip = await api("POST", "/api/stock/dip", { tank_id: tank.id, measured_l: tank.current_l * 0.98 });
  assert.ok(dip.data.variance_pct < -1.9);
  const sup = (await api("GET", "/api/suppliers")).data[0];
  const del = await api("POST", "/api/stock/delivery", { tank_id: tank.id, invoice_l: 5000, received_l: 4950, tanker_no: "TLR-TEST", supplier_id: sup.id, purchase_rate: 250 });
  assert.equal(del.status, 200);
  const alerts = (await api("GET", "/api/alerts")).data;
  assert.ok(alerts.some((a: any) => a.title.includes("TLR-TEST")));
});

test("price update with broadcast", async () => {
  const r = await api("POST", "/api/prices", { prices: { PMG: 270.5 }, broadcast: true });
  assert.equal(r.status, 200);
  assert.ok(r.data.broadcast_queued > 0);
  const p = (await api("GET", "/api/prices")).data;
  assert.equal(p.current.PMG.price, 270.5);
});

test("order delivered posts sale to khata", async () => {
  const order = (await api("GET", "/api/orders")).data.find((o: any) => o.status === "pending" && o.payment === "khata");
  const before = (await api("GET", `/api/customers/${order.customer_id}`)).data.balance;
  const r = await api("PATCH", `/api/orders/${order.id}`, { status: "delivered" });
  assert.equal(r.status, 200, JSON.stringify(r.data));
  assert.equal(r.data.status, "delivered");
  const afterBal = (await api("GET", `/api/customers/${order.customer_id}`)).data.balance;
  assert.ok(afterBal > before);
});

test("campaigns: AI write + send to segment", async () => {
  const w = await api("POST", "/api/campaigns/ai-write", { goal: "Free car wash on 40L+ fill", segment: "VIP" });
  assert.match(w.data.message, /\{name\}/);
  const c = await api("POST", "/api/campaigns", { name: "Car wash", segment: "all", message: w.data.message, send_now: true });
  assert.equal(c.data.status, "sent");
  assert.ok(c.data.sent_count > 10);
});

test("automations run on demand", async () => {
  const list = (await api("GET", "/api/automations")).data;
  assert.ok(list.length >= 6);
  for (const key of ["ai_scoring", "anomaly_scan", "stock_watch", "khata_reminders", "daily_brief"]) {
    const r = await api("POST", `/api/automations/${key}/run`);
    assert.equal(r.status, 200);
    assert.doesNotMatch(r.data.result, /^Error/);
  }
});

test("ask AI falls back to analytics summary without a key", async () => {
  const r = await api("POST", "/api/ai/ask", { question: "How are sales today?" });
  assert.equal(r.data.engine, "rules");
  assert.match(r.data.answer, /Aaj ki sale: Rs/);
});

test("Meta webhook verification and inbound message", async () => {
  const v = await fetch(`${base}/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=pumpai-verify&hub.challenge=123`);
  assert.equal(await v.text(), "123");
  const payload = { entry: [{ changes: [{ value: { contacts: [{ wa_id: "923451230000", profile: { name: "Webhook User" } }], messages: [{ from: "923451230000", id: "wamid.TEST1", type: "text", text: { body: "points kitne hain" } }] } }] }] };
  const res = await fetch(`${base}/webhooks/whatsapp`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) });
  assert.equal(res.status, 200);
  await new Promise((r) => setTimeout(r, 300));
  const conv = (await api("GET", "/api/whatsapp/conversations")).data.find((c: any) => c.phone === "923451230000");
  assert.ok(conv && conv.last_sender === "ai");
});
