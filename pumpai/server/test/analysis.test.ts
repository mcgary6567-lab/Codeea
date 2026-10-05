/** Owner analysis: health score, station comparison, staff risk, P&L + balance sheet, statement reconciliation, price planner, gain/loss. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-analysis-"));
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
  return { status: res.status, data: (await res.json().catch(() => null)) as any, text: "" };
}
const ok = (r: { status: number; data: any }, msg: string) => { assert.equal(r.status, 200, `${msg}: ${JSON.stringify(r.data)}`); return r.data; };

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("health score has every part and an overall level", async () => {
  const h = ok(await call("manager", "GET", "/api/analysis/health"), "health");
  assert.ok(h.score >= 0 && h.score <= 100); assert.ok(["good", "fair", "poor"].includes(h.level));
  assert.deepEqual(h.parts.map((p: any) => p.key), ["stock", "cash", "khata", "compliance", "staff", "sales"]);
  assert.match(h.parts.find((p: any) => p.key === "compliance").note, /expired/);
  assert.equal((await call("salesman", "GET", "/api/analysis/health")).status, 403);
});

test("stations are compared on sales, shop, expenses, cash and dips", async () => {
  const r = ok(await call("manager", "GET", "/api/analysis/stations"), "stations");
  assert.equal(r.stations.length, 2);
  const total = r.stations.reduce((a: number, s: any) => a + s.fuel_sales, 0);
  const all = db.get("SELECT SUM(amount) a FROM sales WHERE created_at >= ? AND created_at < ?", r.from, r.to).a;
  assert.ok(Math.abs(total - all) < 2, "station totals add up to all sales");
  assert.ok(r.stations.every((s: any) => s.shop_sales > 0 && s.litres > 0));
});

test("staff risk: litres not entered on the POS and short shifts raise the score", async () => {
  const shift = ok(await call("salesman", "POST", "/api/shifts/open", {}), "open");
  ok(await call("salesman", "POST", "/api/sales", { station_id: 1, product: "PMG", litres: 10, payment_method: "cash" }), "pos sale");
  const live = ok(await call("salesman", "GET", `/api/shifts/${shift.id}/live`), "live");
  // 200 L pumped but only 10 L entered; cash short
  const readings = Object.fromEntries(live.readings.map((r: any, i: number) => [r.nozzle_id, r.opening + (i === 0 ? 210 : 0)]));
  ok(await call("salesman", "POST", `/api/shifts/${shift.id}/close`, { readings, cash_actual: 1000 }), "close");
  const r = ok(await call("manager", "GET", "/api/analysis/staff-risk"), "risk");
  const imran = r.staff.find((x: any) => x.name === "Imran");
  assert.ok(imran.unentered_pct > 0); assert.ok(imran.short_shifts >= 1); assert.ok(imran.score > 0);
  assert.ok(imran.reasons.some((x: string) => /not entered on the POS/.test(x)));
  const { runJob } = await import("../src/automation/scheduler.js");
  assert.match(await runJob(1, "staff_risk"), /salesmen flagged/);
});

test("monthly P&L and balance sheet, with CSV export", async () => {
  const month = db.pkDate().slice(0, 7);
  const r = ok(await call("admin", "GET", `/api/analysis/pl?month=${month}`), "pl");
  const p = r.pl;
  assert.equal(p.income.total, p.income.fuel_retail + p.income.fuel_wholesale + p.income.shop);
  assert.ok(Math.abs(p.gross_profit - (p.income.total - p.cost_of_sales.total)) <= 1);
  assert.ok(Math.abs(p.net_profit - (p.gross_profit - p.expenses.total)) <= 1);
  const b = r.balance_sheet;
  assert.equal(b.net_worth, b.total_assets - b.total_liabilities);
  assert.ok(b.assets.fuel_stock > 0 && b.assets.shop_stock > 0);
  const csv = await (await fetch(`${base}/api/analysis/pl.csv?month=${month}&token=${(await call("admin", "GET", "/api/me")).data.media_token}`)).text();
  assert.match(csv, /Net profit/); assert.match(csv, /Net worth/);
});

test("Easypaisa statement reconciliation finds money without a sale and sales without money", async () => {
  const sales = db.all("SELECT s.id, s.amount, s.created_at FROM sales s WHERE payment_method='easypaisa' ORDER BY id DESC LIMIT 3");
  const pk = (iso: string) => new Date(Date.parse(iso) + 5 * 3600_000).toISOString().slice(0, 19).replace("T", " ");
  const csv = ["Date,Transaction ID,Description,Amount",
    `${pk(sales[0].created_at)},EP1001,Received from customer,${sales[0].amount}`,
    `${pk(sales[1].created_at)},EP1002,Received from customer,${sales[1].amount}`,
    `${pk(sales[1].created_at)},EP1003,Unknown transfer,1234`].join("\n");
  const r = ok(await call("manager", "POST", "/api/analysis/reconcile", { method: "easypaisa", csv }), "reconcile");
  assert.equal(r.statement_lines, 3); assert.equal(r.matched, 2);
  assert.equal(r.money_without_sale.length, 1); assert.equal(r.money_without_sale[0].amount, 1234);
  assert.ok(Array.isArray(r.sales_without_money));
  assert.equal((await call("manager", "POST", "/api/analysis/reconcile", { method: "easypaisa", csv: "nothing here\nat all" })).status, 400);
});

test("price planner and tank gain/loss", async () => {
  const p = ok(await call("manager", "GET", "/api/analysis/price-planner"), "planner");
  assert.ok(p.days_to_next >= 1 && p.days_to_next <= 16); assert.match(p.next_revision, /^\d{4}-\d{2}-(01|16)$/);
  const pmg = p.products.find((x: any) => x.product === "PMG");
  assert.equal(pmg.scenarios.find((s: any) => s.change === 5).stock_effect, Math.round(pmg.stock_l * 5));
  assert.ok(pmg.advice.length > 10);
  const g = ok(await call("manager", "GET", "/api/analysis/gain-loss"), "gain-loss");
  assert.equal(g.tanks.length, 5); assert.ok(g.tanks.every((t: any) => ["ok", "watch", "leak_suspected"].includes(t.status)));
});
