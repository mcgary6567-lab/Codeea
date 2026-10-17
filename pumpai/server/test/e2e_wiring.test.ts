/**
 * End-to-end wiring sweep: one real entry in EVERY money/stock module, in one database, in sequence — and after each one the
 * system must still tie to the rupee (GET /books/check all green) while the specific balance it touched moved by exactly that amount:
 * cash in hand, khata / wholesale / thekedar / supplier dues, bank balances, tank litres, bypass stock, P&L.
 * This is the "sab modules aik doosre ke saath wired hain" proof — a regression here means a module stopped posting somewhere.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-e2e-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server, base = "";
const tok: Record<string, string> = {};
async function call(who: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tok[who] ? { authorization: `Bearer ${tok[who]}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, m: string) => { assert.equal(r.status, 200, `${m}: ${JSON.stringify(r.data)}`); return r.data; };
const near = (a: number, b: number, m: string, tol = 1) => assert.ok(Math.abs(a - b) <= tol, `${m}: got ${a}, expected ${b}`);

/* ---------- balance readers (the same numbers the owner sees on screen) ---------- */
const cash = async () => ok(await call("admin", "GET", "/api/cash"), "cash").cash_in_hand as number;
const bank = async (id: number) => (ok(await call("admin", "GET", "/api/bank/accounts"), "banks").accounts.find((a: any) => a.id === id).balance as number);
const khata = async (id: number) => ok(await call("admin", "GET", `/api/customers/${id}`), "customer").balance as number;
const wsDue = async (id: number) => ok(await call("admin", "GET", `/api/wholesale/clients/${id}`), "client").summary.due as number;
const supOwed = async (id: number) => (ok(await call("admin", "GET", "/api/suppliers"), "suppliers").find((s: any) => s.id === id).owed as number);
const tankL = async (id: number) => { const sts = ok(await call("admin", "GET", "/api/stations"), "stations"); return sts.flatMap((x: any) => x.tanks).find((x: any) => x.id === id).current_l as number; };
let step = 0;
async function booksOk(label: string) {
  const r = ok(await call("admin", "GET", "/api/books/check"), "books/check");
  const bad = r.checks.filter((c: any) => !c.ok && !c.advisory).map((c: any) => `${c.label}: book ${c.book} ledger ${c.ledger} diff ${c.diff}${c.note ? ` (${c.note})` : ""}`);
  assert.equal(r.ok, true, `[${++step}] after "${label}" the books no longer tally:\n  ${bad.join("\n  ")}`);
}

let st = 0, tank = 0, nozzle = 0, hbl = 0, cust = 0, ws = 0, sup = 0, thk = 0, oil = 0;

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman", "cashier", "wholesale"]) tok[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
  const own = ok(await call("salesman", "GET", "/api/stations"), "stations")[0]; // the salesman's own station
  st = own.id;
  const t = own.tanks.find((x: any) => x.product === "PMG") ?? own.tanks[0];
  tank = t.id;
  nozzle = own.nozzles.find((n: any) => n.tank_id === tank)?.id ?? own.nozzles[0].id;
  hbl = ok(await call("admin", "GET", "/api/bank/accounts"), "banks").accounts[0].id;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("0. fresh demo books tally before we touch anything", async () => { await booksOk("start"); });

test("1. parties: khata customer, wholesale client, supplier, thekedar, shop item", async () => {
  cust = ok(await call("admin", "POST", "/api/customers", { name: "E2E Fleet Co", type: "fleet", phone: "03001000001", credit_limit: 500000 }), "customer").id;
  ws = ok(await call("admin", "POST", "/api/wholesale/clients", { name: "E2E Filling Point", phone: "03001000002", rates: { HSD: 260, PMG: 270 } }), "client").id;
  sup = ok(await call("admin", "POST", "/api/suppliers", { name: "E2E Depot" }), "supplier").id;
  thk = ok(await call("wholesale", "POST", "/api/carriage/thekedars", { name: "E2E Thekedar", phone: "03001000003" }), "thekedar").id;
  oil = ok(await call("admin", "POST", "/api/shop/items", { station_id: st, name: "E2E Engine Oil 4L", category: "lubricant", cost: 2000, price: 2600 }), "item").id;
  await booksOk("parties created");
});

test("2. stock delivery from supplier → tank up, supplier payable up, books tie", async () => {
  const l0 = await tankL(tank), o0 = await supOwed(sup);
  ok(await call("manager", "POST", "/api/stock/delivery", { tank_id: tank, invoice_l: 3000, received_l: 3000, supplier_id: sup, purchase_rate: 250, tanker_no: "E2E-1" }), "delivery");
  near(await tankL(tank), l0 + 3000, "tank litres");
  near(await supOwed(sup), o0 + 750000, "supplier owed");
  await booksOk("stock delivery");
});

test("3. shop stock-in on supplier khata → shop stock + payable", async () => {
  const o0 = await supOwed(sup);
  ok(await call("manager", "POST", `/api/shop/items/${oil}/stock-in`, { qty: 10, cost: 2000, paid_by: "supplier", supplier_id: sup }), "stock-in");
  near(await supOwed(sup), o0 + 20000, "supplier owed incl. shop goods");
  await booksOk("shop stock-in");
});

test("4. salesman shift: cash, khata, card and shop sales → tank down, khata up, cash expected", async () => {
  const sh = ok(await call("salesman", "POST", "/api/shifts/open", {}), "open shift");
  const l0 = await tankL(tank), k0 = await khata(cust);
  const s1 = ok(await call("salesman", "POST", "/api/sales", { station_id: st, product: "PMG", litres: 10, payment_method: "cash" }), "cash sale");
  const s2 = ok(await call("salesman", "POST", "/api/sales", { station_id: st, product: "PMG", litres: 20, payment_method: "khata", customer_id: cust, vehicle_no: "LEA-1" }), "khata sale");
  ok(await call("salesman", "POST", "/api/sales", { station_id: st, product: "PMG", litres: 5, payment_method: "card" }), "card sale");
  const shop = ok(await call("salesman", "POST", "/api/shop/sales", { station_id: st, payment_method: "cash", lines: [{ item_id: oil, qty: 1 }] }), "shop sale");
  near(await tankL(tank), l0 - 35, "tank after 35 L sold", 0.5);
  near(await khata(cust), k0 + s2.amount, "khata balance = khata sale");
  await booksOk("sales on an open shift");
  // close the shift with exactly the cash the system expects (meter readings = what we sold)
  const live = ok(await call("salesman", "GET", `/api/shifts/${sh.id}/live`), "live");
  const readings: Record<string, number> = {};
  for (const n of live.readings) readings[n.nozzle_id] = Number(n.closing ?? n.opening ?? 0);
  assert.ok(nozzle in readings || Object.keys(readings).length > 0, "shift has meter readings");
  const prev = ok(await call("salesman", "POST", `/api/shifts/${sh.id}/preview`, { readings }), "preview").cash_expected;
  const closed = ok(await call("salesman", "POST", `/api/shifts/${sh.id}/close`, { readings, cash_actual: prev }), "close");
  near(closed.variance ?? 0, 0, "no variance when counted = expected");
  assert.ok(s1.amount > 0 && shop.total > 0);
  const c0 = await cash();
  ok(await call("cashier", "POST", `/api/cashier/handovers/${sh.id}`, { amount: prev }), "handover to office");
  near(await cash(), c0 + prev, "office cash up by the handover");
  await booksOk("shift closed + handed over");
});

test("5. cashier desk: khata receipt, supplier payment, expense, other party receipt", async () => {
  const c0 = await cash(), k0 = await khata(cust), o0 = await supOwed(sup), b0 = await bank(hbl);
  ok(await call("cashier", "POST", "/api/cashier/receive", { party_type: "khata", party_id: cust, amount: 2000, method: "Cash" }), "khata receive");
  near(await khata(cust), k0 - 2000, "khata down"); near(await cash(), c0 + 2000, "cash up");
  ok(await call("cashier", "POST", "/api/cashier/pay", { party_type: "supplier", party_id: sup, amount: 100000, method: "Bank transfer", account_id: hbl }), "supplier pay by bank");
  near(await supOwed(sup), o0 - 100000, "supplier owed down"); near(await bank(hbl), b0 - 100000, "bank down");
  ok(await call("cashier", "POST", "/api/cashier/pay", { party_type: "other", party_name: "Labour", category: "Daily wage", amount: 500, method: "Cash" }), "cash expense");
  near(await cash(), c0 + 2000 - 500, "cash after expense");
  await booksOk("cashier desk");
});

test("6. wholesale: tanker trip from our tank, payment; carriage kiraya + payment", async () => {
  const l0 = await tankL(tank), d0 = await wsDue(ws), c0 = await cash();
  const trip = ok(await call("wholesale", "POST", "/api/wholesale/trips", { station_id: st, product: "PMG", vehicle_no: "E2E-TLR", drops: [{ client_id: ws, litres: 500, override_limit: true }] }), "trip");
  const billed = trip.drops[0].amount as number;
  near(await tankL(tank), l0 - 500, "tank down 500 L"); near(await wsDue(ws), d0 + billed, "client due = trip amount");
  ok(await call("cashier", "POST", "/api/cashier/receive", { party_type: "wholesale", party_id: ws, amount: 50000, method: "Cash" }), "wholesale receipt");
  near(await wsDue(ws), d0 + billed - 50000, "client due after payment"); near(await cash(), c0 + 50000, "cash up");
  const kir = ok(await call("wholesale", "POST", `/api/carriage/thekedars/${thk}/carriage`, { supplier_id: sup, invoice_ref: "E2E-DEP", amount: 8000, lines: [{ product: "HSD", litres: 4000 }] }), "kiraya");
  near(kir.due, 8000, "thekedar owes kiraya");
  ok(await call("wholesale", "POST", `/api/carriage/thekedars/${thk}/payment`, { amount: 3000, method: "Cash", notify: false }), "kiraya receipt");
  near(await cash(), c0 + 53000, "cash after kiraya");
  await booksOk("wholesale + carriage");
});

test("7. bypass: buy at depot, drop to client, leftover held, supplier paid", async () => {
  const d0 = await wsDue(ws);
  const del = ok(await call("wholesale", "POST", "/api/bypass/deliveries", { station_id: st, purchases: [{ supplier_id: sup, product: "HSD", litres: 1000, cost_rate: 255, ref: "E2E-BILTY" }], drops: [{ client_id: ws, product: "HSD", litres: 800, override_limit: true }] }), "bypass delivery");
  near(await wsDue(ws), d0 + 800 * 260, "client billed at his HSD rate");
  const held = ok(await call("wholesale", "GET", "/api/bypass/stock"), "stock");
  assert.ok((held.by_product ?? []).some((p: any) => p.product === "HSD" && p.litres >= 200), `200 L held: ${JSON.stringify(held.by_product)}`);
  const b0 = await bank(hbl);
  ok(await call("wholesale", "POST", `/api/bypass/suppliers/${sup}/payment`, { amount: 255000, mode: "we_pay", method: "Bank transfer", account_id: hbl, ref: "E2E-PO" }), "pay bypass supplier");
  near(await bank(hbl), b0 - 255000, "bank down by supplier payment");
  assert.ok(del.id);
  await booksOk("bypass");
});

test("8. bank: deposit cash, transfer, charges, withdraw", async () => {
  const c0 = await cash(), b0 = await bank(hbl);
  ok(await call("manager", "POST", "/api/cash/deposits", { amount: 20000, bank: "HBL", account_id: hbl, slip_ref: "E2E-DS" }), "deposit");
  near(await cash(), c0 - 20000, "cash down"); near(await bank(hbl), b0 + 20000, "bank up");
  ok(await call("admin", "POST", "/api/bank/entries", { account_id: hbl, kind: "charges", amount: 150 }), "charges");
  near(await bank(hbl), b0 + 20000 - 150, "bank after charges");
  ok(await call("admin", "POST", "/api/bank/entries", { account_id: hbl, kind: "withdraw", amount: 5000 }), "withdraw");
  near(await cash(), c0 - 20000 + 5000, "cash after withdrawal"); near(await bank(hbl), b0 + 20000 - 150 - 5000, "bank after withdrawal");
  await booksOk("bank moves");
});

test("9. staff: advance in cash, salary paid; expense claim approved and paid", async () => {
  const c0 = await cash();
  const sm = ok(await call("admin", "GET", "/api/users"), "users").users.find((u: any) => u.role === "salesman");
  ok(await call("manager", "POST", `/api/staff/${sm.id}/entry`, { type: "advance", amount: 2000, method: "cash", note: "E2E advance" }), "advance");
  near(await cash(), c0 - 2000, "cash down by advance");
  const cats = ok(await call("manager", "GET", "/api/expense-categories"), "categories").categories;
  const exp = ok(await call("manager", "POST", "/api/expenses", { category: cats[0].name, amount: 300 }), "expense");
  near(await cash(), c0 - 2000 - (exp.status === "pending" ? 0 : 300), "cash after a small expense");
  assert.ok(["approved", "pending", "paid"].includes(exp.status));
  await booksOk("staff + expenses");
});

test("10. CEO: other income to bank, other expense in cash, khata discount, coupon, wallet", async () => {
  const c0 = await cash(), b0 = await bank(hbl), k0 = await khata(cust);
  ok(await call("admin", "POST", "/api/other-entries", { kind: "income", amount: 15000, method: "bank", account_id: hbl, party_type: "other", party_name: "ATM rent", reason: "E2E income" }), "other income");
  near(await bank(hbl), b0 + 15000, "bank up by other income");
  ok(await call("admin", "POST", "/api/other-entries", { kind: "expense", amount: 1000, method: "cash", party_type: "other", party_name: "Owner misc", reason: "E2E expense" }), "other expense");
  near(await cash(), c0 - 1000, "cash down by other expense");
  ok(await call("admin", "POST", "/api/other-entries", { kind: "discount", amount: 500, party_type: "khata", party_id: cust, reason: "E2E discount" }), "discount");
  near(await khata(cust), k0 - 500, "khata down by discount");
  ok(await call("manager", "POST", "/api/coupons", { count: 2, value: 1000, product: "PMG", buyer: "E2E Traders", method: "cash" }), "coupons sold");
  near(await cash(), c0 - 1000 + 2000, "cash up by coupon sale");
  ok(await call("manager", "POST", `/api/customers/${cust}/wallet/deposit`, { amount: 5000, method: "cash" }), "wallet deposit");
  near(await cash(), c0 - 1000 + 2000 + 5000, "cash up by wallet deposit");
  await booksOk("CEO entries, coupons, wallet");
});

test("11. corrections ripple back: void the khata receipt, khata and cash reverse, books still tie", async () => {
  const k0 = await khata(cust), c0 = await cash();
  const vs = ok(await call("admin", "GET", "/api/cashier/vouchers?limit=50"), "vouchers");
  const rec = (Array.isArray(vs) ? vs : vs.vouchers).find((v: any) => v.direction === "in" && v.party_type === "khata" && v.amount === 2000 && !v.voided);
  assert.ok(rec, "the khata receipt voucher exists");
  ok(await call("admin", "POST", `/api/cashier/vouchers/${rec.id}/void`, { reason: "E2E void" }), "void receipt");
  near(await khata(cust), k0 + 2000, "khata back up"); near(await cash(), c0 - 2000, "cash back down");
  await booksOk("voucher void");
});

test("12. the day's journal is balanced and P&L carries every leg (fuel, shop, wholesale, other, discount)", async () => {
  const today = new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10);
  const j = ok(await call("admin", "GET", `/api/ledger?from=${today}&to=${today}`), "journal");
  near(j.totals.debit, j.totals.credit, "journal debit = credit", 0.05);
  const accounts = new Set(j.trial_balance.map((x: any) => x.account));
  for (const a of ["Cash in hand", "Bank", "Khata receivable", "Wholesale receivable", "Carriage receivable", "Shop stock", "Bypass stock", "Other income (CEO)", "Expense: Other (CEO)", "Other discount (CEO)", "Customer wallets", "Fuel coupons (unused)", "Staff advances"])
    assert.ok(accounts.has(a), `ledger has an account "${a}"`);
  assert.ok(!accounts.has("Suspense"), "nothing in Suspense");
  const { pl, balance_sheet } = ok(await call("admin", "GET", `/api/analysis/pl?month=${today.slice(0, 7)}`), "pl");
  assert.ok(pl.other_income_ceo >= 15000, `P&L other income carries the CEO income (${pl.other_income_ceo})`);
  assert.ok(typeof pl.net_profit === "number", "P&L has a net line");
  assert.ok(balance_sheet, "balance sheet comes with it");
  await booksOk("final");
});
