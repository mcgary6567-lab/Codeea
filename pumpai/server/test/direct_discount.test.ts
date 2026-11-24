/** Direct (walk-in) discount at the POS: net cash, own ledger account, shift report line, reference, limits, report, tally. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-ddisc-"));
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
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
  db.run("UPDATE shifts SET status='closed', closed_at=? WHERE status='open'", new Date().toISOString());
});
after(() => server?.close());

let shiftId = 0, live: any = null, rate = 0, sale: any = null;

test("salesman: cash sale with a direct discount — customer pays the net, discount kind/ref stored", async () => {
  const open = ok(await call("salesman", "POST", "/api/shifts/open", {}), "open");
  shiftId = open.id;
  live = ok(await call("salesman", "GET", `/api/shifts/${shiftId}/live`), "live");
  rate = live.prices.PMG;
  sale = ok(await call("salesman", "POST", "/api/sales", { station_id: live.shift.station_id, product: "PMG", litres: 10, payment_method: "cash", discount: 50, discount_kind: "direct" }), "sale");
  near(sale.amount, 10 * rate - 50, "net amount"); assert.equal(sale.discount, 50); assert.equal(sale.discount_kind, "direct"); assert.equal(sale.discount_ref, null);
  // reference typed right after
  const r = ok(await call("salesman", "POST", `/api/sales/${sale.id}/discount-ref`, { ref: "Rickshaw union — Aslam" }), "ref");
  assert.equal(r.discount_ref, "Rickshaw union — Aslam");
  assert.equal((await call("salesman", "POST", `/api/sales/${sale.id}/discount-ref`, { ref: "x" })).status, 400, "too short");
  // the POS recent list carries it
  const t = ok(await call("salesman", "GET", "/api/pos/today"), "today");
  const rec = t.recent.find((x: any) => x.id === sale.id);
  assert.equal(rec.discount_ref, "Rickshaw union — Aslam"); assert.equal(rec.discount_kind, "direct");
});

test("limits & rules: salesman capped at direct_discount_max, manager not; not on khata / coupon; per-litre is just rupees", async () => {
  const over = await call("salesman", "POST", "/api/sales", { station_id: live.shift.station_id, product: "PMG", litres: 20, payment_method: "card", discount: 5001, discount_kind: "direct" });
  assert.equal(over.status, 403); assert.match(over.data.error, /Direct discount above Rs 5,000/);
  ok(await call("admin", "PUT", "/api/settings", { limits: { direct_discount_max: 1000 } }), "raise limit");
  const s2 = ok(await call("salesman", "POST", "/api/sales", { station_id: live.shift.station_id, product: "PMG", litres: 20, payment_method: "card", discount: 600, discount_kind: "direct", discount_ref: "Dukandar Bilal" }), "card sale with discount");
  assert.equal(s2.discount_ref, "Dukandar Bilal"); near(s2.amount, 20 * rate - 600);
  const accts = ok(await call("salesman", "GET", "/api/pos/khata-accounts"), "accounts");
  const bad = await call("salesman", "POST", "/api/sales", { station_id: live.shift.station_id, product: "PMG", litres: 5, payment_method: "khata", customer_id: accts[0].id, discount: 20, discount_kind: "direct" });
  assert.equal(bad.status, 400); assert.match(bad.data.error, /Direct discount is for cash/);
  const tooBig = await call("manager", "POST", "/api/sales", { station_id: live.shift.station_id, product: "PMG", litres: 1, payment_method: "cash", discount: 100000, discount_kind: "direct" });
  assert.equal(tooBig.status, 400); assert.match(tooBig.data.error, /more than the fuel amount/);
  // not a direct-discount sale → no reference
  const plain = ok(await call("salesman", "POST", "/api/sales", { station_id: live.shift.station_id, product: "PMG", litres: 3, payment_method: "cash" }), "plain");
  assert.equal((await call("salesman", "POST", `/api/sales/${plain.id}/discount-ref`, { ref: "nothing" })).status, 400);
});

test("shift: cash expected is net of the discount and the summary shows the direct-discount line", async () => {
  const l = ok(await call("salesman", "GET", `/api/shifts/${shiftId}/live`), "live");
  near(l.summary.direct_discount, 650, "direct discount total"); assert.equal(l.summary.direct_discount_n, 2);
  near(l.summary.cash_sales, (10 * rate - 50) + 3 * rate, "cash = net");
  near(l.summary.cash_expected, l.summary.cash_sales - l.summary.expenses_total);
});

test("ledger / P&L / report: own account 'Discount given — direct', revenue at gross, grouped by reference", async () => {
  const j = ok(await call("admin", "GET", `/api/ledger?from=${today}&to=${today}`), "journal");
  const dd = j.trial_balance.find((x: any) => x.account === "Discount given — direct");
  assert.ok(dd, "account exists"); near(dd.balance, 650);
  const pl = ok(await call("admin", "GET", `/api/analysis/pl?month=${today.slice(0, 7)}`), "pl").pl;
  near(pl.discount_direct, 650, "P&L direct discount line");
  const rep = ok(await call("admin", "GET", `/api/reports/discounts?from=${new Date(Date.now() - 86_400_000).toISOString()}&to=${new Date(Date.now() + 60_000).toISOString()}`), "report");
  near(rep.direct_total, 650); assert.equal(rep.direct_count, 2);
  assert.ok(rep.by_reference.some((x: any) => x.ref === "Rickshaw union — Aslam" && x.total === 50), "by reference");
  assert.ok(rep.by_reference.some((x: any) => x.ref === "Dukandar Bilal" && x.total === 600));
  assert.ok(rep.list.some((x: any) => x.id === sale.id && x.payment_method === "cash" && x.discount_kind === "direct"));
  // a manager may fix a reference later; audit kept
  ok(await call("manager", "POST", `/api/sales/${sale.id}/discount-ref`, { ref: "Rickshaw union (Aslam)" }), "manager edits");
  assert.ok(db.get("SELECT id FROM audit_log WHERE action='discount_ref_changed'"), "audited");
});

test("alert when a shift's direct discounts cross the alert line; void reverses everything; books tally", async () => {
  ok(await call("admin", "PUT", "/api/settings", { limits: { direct_discount_alert: 600 } }), "alert line");
  ok(await call("salesman", "POST", "/api/sales", { station_id: live.shift.station_id, product: "PMG", litres: 4, payment_method: "cash", discount: 10, discount_kind: "direct", discount_ref: "test" }), "one more");
  assert.ok(db.get("SELECT id FROM alerts WHERE type='direct_discount' AND dedupe_key=?", `direct-disc-${shiftId}`), "alert raised");
  const before = db.get("SELECT COALESCE(SUM(discount),0) v FROM sales WHERE shift_id=? AND discount_kind='direct'", shiftId).v;
  ok(await call("admin", "POST", `/api/corrections/sale/${sale.id}/void`, { reason: "test void" }), "void");
  near(db.get("SELECT COALESCE(SUM(discount),0) v FROM sales WHERE shift_id=? AND discount_kind='direct'", shiftId).v, before - 50);
  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("direct_discount");
});
