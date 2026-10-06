/**
 * The whole business in one pass: a salesman's shift (cash, khata, shift expense, meter close),
 * a tanker delivery from a supplier and a wholesale supply must all land in the manager's
 * notifications and the admin's "today" dashboard — sales, expenses, supply, stock left, stock value,
 * receivables and payables — and agree with the Reports page and the tanks.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-circle-"));
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
const nearRs = (a: number, b: number, msg?: string) => assert.ok(Math.abs(a - b) <= 2, `${msg ?? ""} ${a} ≈ ${b}`);
const ok = (r: { status: number; data: any }, msg: string) => { assert.equal(r.status, 200, `${msg}: ${JSON.stringify(r.data)}`); return r.data; };
const day = async () => ok(await call("admin", "GET", "/api/dashboard"), "dashboard").day;
const tanksOf = async (product: string) => {
  const st = ok(await call("admin", "GET", "/api/stations"), "stations");
  return st.flatMap((s: any) => s.tanks).filter((t: any) => t.product === product);
};
const prod = (d: any, p: string) => d.stock.products.find((x: any) => x.product === p);

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

let d0: any, shiftSummary: any, shiftLitres: Record<string, number> = {}, khataAmt = 0;
let delivered = 0, purchaseCost = 0, wsLitres = 0, wsAmount = 0, supId = 0, supOwed0 = 0;
const EXPENSE = 450;

test("before: today's book matches the tanks", async () => {
  d0 = await day();
  for (const p of ["PMG", "HSD"]) {
    const tanks = await tanksOf(p);
    nearRs(prod(d0, p).closing_l, tanks.reduce((a: number, t: any) => a + t.current_l, 0), `${p} stock = tanks`);
  }
  assert.ok(d0.stock.value_at_cost > 0 && d0.stock.value_at_sale > d0.stock.value_at_cost, "stock is valued at cost and at sale price");
});

test("salesman: open shift, sell cash + khata, pay an expense, close with meters", async () => {
  const hand = ok(await call("salesman", "GET", "/api/shifts/handover"), "handover");
  const shift = ok(await call("salesman", "POST", "/api/shifts/open", {}), "open");
  const sid = hand.station_id;
  ok(await call("salesman", "POST", "/api/sales", { station_id: sid, product: "PMG", amount: 5000, payment_method: "cash" }), "cash sale");
  ok(await call("salesman", "POST", "/api/sales", { station_id: sid, product: "HSD", amount: 2000, payment_method: "easypaisa" }), "easypaisa sale");
  const acct = ok(await call("salesman", "GET", "/api/pos/khata-accounts"), "khata accounts").find((a: any) => a.status === "ok");
  const k = ok(await call("salesman", "POST", "/api/sales", { station_id: sid, product: "HSD", litres: 30, payment_method: "khata", customer_id: acct.id, slip_no: "C-1" }), "khata sale");
  khataAmt = k.amount ?? k.sale?.amount;
  assert.ok(khataAmt > 0, "khata amount recorded");
  const cat = ok(await call("salesman", "GET", "/api/shifts/expense-categories"), "categories")[0];
  ok(await call("salesman", "POST", `/api/shifts/${shift.id}/expenses`, { category: cat.name ?? cat, amount: EXPENSE, note: "chai" }), "expense");

  // closing readings: each nozzle pumped 40 L more than was entered on the POS for it
  const live = ok(await call("salesman", "GET", `/api/shifts/${shift.id}/live`), "live");
  const readings: Record<string, number> = {};
  for (const r of live.readings) readings[r.nozzle_id] = r.opening + 40 + (r.product === "HSD" ? 30 : 0);
  const closeEst = ok(await call("salesman", "GET", `/api/shifts/${shift.id}/report`), "pre-close report");
  assert.ok(closeEst.summary.expenses_total === EXPENSE, "expense counted on the shift");
  const closed = ok(await call("salesman", "POST", `/api/shifts/${shift.id}/close`, { readings, cash_actual: 0 }), "close");
  shiftSummary = closed.summary;
  nearRs(closed.cash_expected, shiftSummary.cash_sales - EXPENSE, "cash to hand over = cash sales − expenses");
  for (const p of shiftSummary.by_product) shiftLitres[p.product] = p.litres;
  const metered = live.readings.reduce((a: number, r: any) => a + (readings[r.nozzle_id] - r.opening), 0);
  nearRs(closed.litres, metered, "shift litres = meter difference");
  nearRs(shiftSummary.khata, khataAmt, "khata on the shift");
  nearRs(shiftSummary.digital, 2000, "Easypaisa on the shift");
});

test("manager is told the shift result", async () => {
  const n = ok(await call("manager", "GET", "/api/notifications"), "notifications").items;
  const sc = n.find((x: any) => x.type === "shift_closed");
  assert.ok(sc, "shift_closed notification");
  assert.match(sc.body, /Sales Rs/);
  assert.match(sc.body, /Short/); // counted 0
});

test("admin/manager: tanker from a supplier and a wholesale supply", async () => {
  const [tank] = await tanksOf("PMG");
  delivered = Math.min(2000, Math.floor(tank.capacity_l - tank.current_l - 1));
  assert.ok(delivered > 100, "room in the tank");
  const sup = ok(await call("admin", "GET", "/api/suppliers"), "suppliers")[0];
  supId = sup.id; supOwed0 = sup.owed;
  ok(await call("manager", "POST", "/api/stock/delivery", { tank_id: tank.id, invoice_l: delivered, received_l: delivered, supplier_id: sup.id, purchase_rate: 250, tanker_no: "TLR-1" }), "delivery");
  purchaseCost = delivered * 250;

  const clients = ok(await call("admin", "GET", "/api/wholesale/clients"), "clients");
  for (const c of clients) {
    const r = await call("admin", "POST", `/api/wholesale/clients/${c.id}/supply`, { station_id: tank.station_id, product: "HSD", litres: 60 });
    if (r.status === 200) { wsLitres = 60; wsAmount = r.data.amount; break; }
  }
  assert.ok(wsLitres, "a wholesale client took fuel");
});

test("admin dashboard: today's sales, expenses, supply, stock left and its value all add up", async () => {
  const d1 = await day();
  const shiftAmt = shiftSummary.amount;
  nearRs(d1.sales.retail - d0.sales.retail, shiftAmt, "retail sales = the shift's sales");
  nearRs(d1.sales.wholesale - d0.sales.wholesale, wsAmount, "wholesale billed");
  nearRs(d1.sales.revenue - d0.sales.revenue, shiftAmt + wsAmount, "revenue");
  nearRs(d1.sales.khata - d0.sales.khata, khataAmt, "khata sales");
  nearRs(d1.sales.cash - d0.sales.cash, shiftSummary.cash_sales, "cash sales");
  nearRs(d1.expenses.total - d0.expenses.total, EXPENSE, "shift expense reached the expense book");
  assert.equal(d1.supply.deliveries - d0.supply.deliveries, 1);
  nearRs(d1.supply.litres - d0.supply.litres, delivered, "supply litres");
  nearRs(d1.supply.cost - d0.supply.cost, purchaseCost, "supply cost");

  // stock: opening + received − sold = closing, and closing is what is physically in the tanks
  for (const p of ["PMG", "HSD"]) {
    const a = prod(d0, p), b = prod(d1, p);
    const sold = (shiftLitres[p] ?? 0) + (p === "HSD" ? wsLitres : 0);
    nearRs(b.sold_l - a.sold_l, sold, `${p} sold`);
    nearRs(b.received_l - a.received_l, p === "PMG" ? delivered : 0, `${p} received`);
    nearRs(b.closing_l - a.closing_l, (p === "PMG" ? delivered : 0) - sold, `${p} stock moved`);
    nearRs(b.closing_l, b.opening_l + b.received_l - b.sold_l + b.dip_adjust_l, `${p} stock equation`);
    const tanks = await tanksOf(p);
    nearRs(b.closing_l, tanks.reduce((x: number, t: any) => x + t.current_l, 0), `${p} stock = tanks`);
    nearRs(b.value_at_sale, b.closing_l * b.sale_rate, `${p} value at sale price`);
    // cost_rate is shown rounded to paisa, so allow half a paisa per litre
    assert.ok(Math.abs(b.value_at_cost - b.closing_l * b.cost_rate) <= b.closing_l * 0.005 + 2, `${p} value at cost`);
  }
  nearRs(d1.stock.value_at_cost, d1.stock.products.reduce((a: number, x: any) => a + x.value_at_cost, 0), "total stock value");

  // balances: we owe the supplier for the tanker; the khata customer and wholesale client owe us
  // the supplier's own balance goes up by the tanker. The payables total only counts suppliers we owe (a demo supplier can
  // be in advance at some hours of the day), so it moves by the part of the tanker above zero
  const owedNow = ok(await call("admin", "GET", "/api/suppliers"), "suppliers").find((x: any) => x.id === supId).owed;
  nearRs(owedNow - supOwed0, purchaseCost, "supplier owed up by the tanker");
  nearRs(d1.payables - d0.payables, Math.max(0, owedNow) - Math.max(0, supOwed0), "payables up by the tanker");
  nearRs(d1.receivables - d0.receivables, khataAmt + wsAmount, "receivables up by khata + wholesale");
  assert.ok(d1.shifts.closed - d0.shifts.closed === 1 && d1.shifts.variance - d0.shifts.variance < 0, "short cash shows on the dashboard");

  // the Reports page for the same window says the same
  const rep = ok(await call("manager", "GET", `/api/reports?from=${encodeURIComponent(d1.from)}`), "report");
  nearRs(rep.summary.revenue, d1.sales.revenue, "report revenue");
  nearRs(rep.summary.expenses, d1.expenses.total, "report expenses");
  nearRs(rep.receivables.total, d1.receivables, "report receivables");
});

test("salesman cannot see the owner's book", async () => {
  assert.equal((await call("salesman", "GET", "/api/dashboard")).status, 403);
  assert.equal((await call("salesman", "GET", "/api/reports")).status, 403);
});
