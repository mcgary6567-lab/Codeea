/** Lists the admin manages from the app: customer types, machine types, shop categories, utility bills, booking services, training topics, job titles, complaint categories. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-lookups-"));
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
const ok = (r: { status: number; data: any }, m: string) => { assert.equal(r.status, 200, `${m}: ${JSON.stringify(r.data)}`); return r.data; };

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("every pump starts with the standard lists", async () => {
  const d = ok(await call("admin", "GET", "/api/lookups"), "lookups");
  assert.ok(d.lists.customer_type.some((x: any) => x.key === "police" && x.extra.institution));
  assert.ok(d.lists.customer_type.some((x: any) => x.key === "retail" && !x.extra.institution));
  assert.equal(d.lists.machine_type.length, 13);
  assert.ok(d.lists.shop_category.some((x: any) => x.key === "lubricant"));
  assert.ok(d.lists.booking_service.some((x: any) => x.key === "car_wash"));
  assert.ok(d.lists.training_topic.some((x: any) => x.extra.required === true));
  assert.ok(d.kinds.customer_type.fields.some((f: any) => f.key === "institution"));
});

test("admin adds a customer type and uses it at once; a switched-off type is refused", async () => {
  const t = ok(await call("admin", "POST", "/api/lookups", { kind: "customer_type", label: "NGO / charity", extra: { icon: "🤝", institution: true } }), "add type");
  assert.equal(t.key, "ngo_charity"); assert.equal(t.extra.institution, true);
  // a customer can be created with the new type immediately
  const c = ok(await call("admin", "POST", "/api/customers", { name: "Edhi Foundation", phone: "03009990001", type: "ngo_charity", credit_limit: 50000 }), "customer");
  assert.equal(c.type, "ngo_charity");
  // being an institution, it is first in the POS order and never auto-held
  const acct = ok(await call("salesman", "GET", "/api/pos/khata-accounts"), "pos").find((a: any) => a.id === c.id);
  assert.ok(acct);
  // an unknown type is refused
  assert.equal((await call("admin", "POST", "/api/customers", { name: "x", phone: "03009990009", type: "martian" })).status, 400);
  // switch the type off → new customers can't use it, the old one keeps it
  assert.equal((await call("admin", "PATCH", `/api/lookups/${t.id}`, { active: false })).status, 200);
  assert.equal((await call("admin", "POST", "/api/customers", { name: "y", phone: "03009990008", type: "ngo_charity" })).status, 400, "off type refused");
  assert.ok(!ok(await call("admin", "GET", "/api/lookups"), "list").lists.customer_type.some((x: any) => x.key === "ngo_charity"), "hidden when off");
  assert.ok(ok(await call("admin", "GET", "/api/lookups?all=1"), "all").lists.customer_type.some((x: any) => x.key === "ngo_charity"), "shown with all=1");
  // in use → cannot delete, only switch off
  await call("admin", "PATCH", `/api/lookups/${t.id}`, { active: true });
  assert.equal((await call("admin", "DELETE", `/api/lookups/${t.id}`)).status, 400, "in use → switch off instead");
});

test("new machine type, shop category and utility bill kind flow through", async () => {
  const mt = ok(await call("admin", "POST", "/api/lookups", { kind: "machine_type", label: "Solar inverter", extra: { icon: "☀️" } }), "machine type");
  const m = ok(await call("admin", "POST", "/api/machines", { name: "Roof solar", type: mt.key }), "machine");
  assert.equal(m.type, mt.key);
  assert.ok(ok(await call("admin", "GET", "/api/machines"), "machines").types.includes(mt.key));
  assert.equal((await call("admin", "POST", "/api/machines", { name: "x", type: "nope" })).status, 400);

  const sc = ok(await call("admin", "POST", "/api/lookups", { kind: "shop_category", label: "Car wash products" }), "shop cat");
  const st = ok(await call("admin", "GET", "/api/stations"), "st")[0];
  const item = ok(await call("admin", "POST", "/api/shop/items", { station_id: st.id, name: "Shampoo 1L", category: sc.key, price: 800 }), "item");
  assert.equal(item.category, sc.key);
  // commission settings now offer the new category
  assert.ok(ok(await call("admin", "GET", "/api/commission"), "comm").categories.some((x: any) => x.key === sc.key));

  const uk = ok(await call("admin", "POST", "/api/lookups", { kind: "utility_kind", label: "Internet (fibre)", extra: { icon: "🌐", category: "Office & stationery" } }), "utility kind");
  const bill = ok(await call("manager", "POST", "/api/utility-bills", { kind: uk.key, amount: 4500 }), "bill");
  assert.equal(bill.kind, uk.key);
  // it was booked to the expense category named in the list
  assert.equal((await call("manager", "GET", "/api/expenses")).data.expenses.find((x: any) => x.id === bill.expense_id).category, "Office & stationery");
});

test("admin adds a booking service with keywords; the WhatsApp parser understands it", async () => {
  const s = ok(await call("admin", "POST", "/api/lookups", { kind: "booking_service", label: "Window tinting", extra: { icon: "🪟", keywords: "tint, tinting, sheeshا" } }), "service");
  const st = ok(await call("admin", "GET", "/api/stations"), "st")[0];
  const c = ok(await call("admin", "POST", "/api/customers", { name: "Tint Cust", phone: "03009990777" }), "cust");
  // booking with the new service key is accepted
  const bk = ok(await call("manager", "POST", "/api/bookings", { customer_id: c.id, station_id: st.id, service: s.key, at: new Date(Date.now() + 2 * 3600_000).toISOString() }), "booking");
  assert.equal(bk.service, s.key);
  assert.equal((await call("manager", "POST", "/api/bookings", { customer_id: c.id, station_id: st.id, service: "flying", at: new Date(Date.now() + 2 * 3600_000).toISOString() })).status, 400, "unknown service refused");
});

test("admin adds a digital payment brand; the bank module and pos-map accept it, cash does not", async () => {
  const sp = ok(await call("admin", "POST", "/api/lookups", { kind: "payment_method", label: "SadaPay", extra: { icon: "📱", digital: true } }), "sadapay");
  assert.equal(sp.key, "sadapay");
  const acct = ok(await call("admin", "POST", "/api/bank/accounts", { bank: "Meezan", title: "Main", kind: "current", opening_date: "2024-01-01" }), "bank");
  // the new digital method can be mapped to a bank POS machine (proves digitalMethods() includes it everywhere)
  assert.equal((await call("admin", "PUT", "/api/bank/pos-map", { sadapay: acct.id })).status, 200);
  // cash is not a bank/digital method, so it cannot be mapped
  assert.equal((await call("admin", "PUT", "/api/bank/pos-map", { cash: acct.id })).status, 400);
  // the POS refuses a method that is neither a money method nor a special flow
  assert.equal((await call("salesman", "POST", "/api/sales", { station_id: 1, product: "PMG", amount: 500, payment_method: "bitcoin" })).status, 400);
});

test("lists can be reordered and only an unused entry deleted", async () => {
  const before = ok(await call("admin", "GET", "/api/lookups?all=1"), "l").lists.job_title;
  const second = before[1];
  ok(await call("admin", "POST", `/api/lookups/${second.id}/move`, { dir: "up" }), "move up");
  const after = ok(await call("admin", "GET", "/api/lookups?all=1"), "l").lists.job_title;
  assert.equal(after[0].id, second.id, "moved to the top");
  // a brand-new unused entry can be deleted outright
  const j = ok(await call("admin", "POST", "/api/lookups", { kind: "job_title", label: "Night supervisor" }), "job");
  assert.equal((await call("admin", "DELETE", `/api/lookups/${j.id}`)).status, 200);
  // non-admins cannot change lists
  assert.equal((await call("manager", "POST", "/api/lookups", { kind: "job_title", label: "x" })).status, 403);
});
