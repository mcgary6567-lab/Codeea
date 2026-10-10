/** CEO corrections: void a wrong sale / manual khata entry / stock delivery — everything it moved is reversed and the books still tally to the rupee. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";
import { tallyBooks } from "./helpers/tally.js";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-corr-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server, base = "", admin = "", salesman = "", manager = "";
async function call(tok: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tok ? { authorization: `Bearer ${tok}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, m: string) => { assert.equal(r.status, 200, `${m}: ${JSON.stringify(r.data)}`); return r.data; };
const bal = async (id: number) => ok(await call(admin, "GET", "/api/cashier/parties?kind=khata"), "p").khata.find((c: any) => c.id === id).balance;
const supOwed = async (id: number) => ok(await call(admin, "GET", "/api/suppliers"), "s").find((s: any) => s.id === id).owed;

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  admin = (await call("", "POST", "/api/auth/login", { email: "admin@pumpai.pk", password: "demo1234" })).data.token;
  salesman = (await call("", "POST", "/api/auth/login", { email: "salesman@pumpai.pk", password: "demo1234" })).data.token;
  manager = (await call("", "POST", "/api/auth/login", { email: "manager@pumpai.pk", password: "demo1234" })).data.token;
  ok(await call(salesman, "POST", "/api/shifts/open", {}), "open shift"); // sales need an open shift
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("void a wrong khata sale — the customer's balance comes back and the books tally", async () => {
  const kh = ok(await call(admin, "GET", "/api/cashier/parties?kind=khata"), "p").khata[0];
  const before = await bal(kh.id);
  const sale = ok(await call(salesman, "POST", "/api/sales", { station_id: 1, product: "PMG", amount: 3000, payment_method: "khata", customer_id: kh.id }), "khata sale").id;
  assert.ok(Math.abs((await bal(kh.id)) - (before + 3000)) < 1, "balance rose by the sale");
  await tallyBooks("after the khata sale");
  ok(await call(admin, "POST", `/api/corrections/sale/${sale}/void`, { reason: "galat customer" }), "void");
  assert.equal(Math.round(await bal(kh.id)), Math.round(before), "balance restored");
  await tallyBooks("after voiding the khata sale");
  // a voided (deleted) sale cannot be voided again
  assert.equal((await call(admin, "POST", `/api/corrections/sale/${sale}/void`, { reason: "again" })).status, 404);
});

test("void a wrong cash sale — books still tally, and it is CEO-only", async () => {
  const sale = ok(await call(salesman, "POST", "/api/sales", { station_id: 1, product: "HSD", amount: 2500, payment_method: "cash" }), "cash sale").id;
  await tallyBooks("after cash sale");
  assert.equal((await call(salesman, "POST", `/api/corrections/sale/${sale}/void`, { reason: "test litres" })).status, 403);
  assert.equal((await call(manager, "POST", `/api/corrections/sale/${sale}/void`, { reason: "test litres" })).status, 403);
  ok(await call(admin, "POST", `/api/corrections/sale/${sale}/void`, { reason: "test litres thi, sale nahi" }), "admin void");
  await tallyBooks("after voiding the cash sale");
});

test("void a manual khata charge — balance restored, and sale/discount/cashier entries are refused", async () => {
  const kh = ok(await call(admin, "GET", "/api/cashier/parties?kind=khata"), "p").khata[0];
  const before = await bal(kh.id);
  // a manual charge put on the customer by the manager
  ok(await call(admin, "POST", `/api/customers/${kh.id}/khata`, { type: "debit", amount: 1500, note: "Shop udhaar" }), "manual charge");
  assert.ok(Math.abs((await bal(kh.id)) - (before + 1500)) < 1, "charge raised balance");
  await tallyBooks("after manual charge");
  // find it through the corrections lookup and confirm it is voidable
  const list = ok(await call(admin, "GET", `/api/corrections/khata?customer_id=${kh.id}`), "khata list");
  const entry = list.entries.find((e: any) => e.note === "Shop udhaar" && e.voidable);
  assert.ok(entry, "the manual charge is listed and voidable");
  ok(await call(admin, "POST", `/api/corrections/khata/${entry.id}/void`, { reason: "galti se charge laga diya" }), "void manual charge");
  assert.equal(Math.round(await bal(kh.id)), Math.round(before), "balance restored");
  await tallyBooks("after voiding the manual charge");

  // a sale-linked khata row must be sent to Sale void, not voided here
  const sale = ok(await call(salesman, "POST", "/api/sales", { station_id: 1, product: "PMG", amount: 800, payment_method: "khata", customer_id: kh.id }), "khata sale").id;
  const saleRow = ok(await call(admin, "GET", `/api/corrections/khata?customer_id=${kh.id}`), "list2").entries.find((e: any) => e.ref === `SALE-${sale}`);
  assert.ok(saleRow && !saleRow.voidable && saleRow.owner === "sale", "sale-linked row flagged not voidable");
  assert.equal((await call(admin, "POST", `/api/corrections/khata/${saleRow.id}/void`, { reason: "nope" })).status, 400);
  ok(await call(admin, "POST", `/api/corrections/sale/${sale}/void`, { reason: "cleanup" }), "cleanup sale");
  await tallyBooks("after cleanup");
});

test("void a wrong stock delivery — supplier payable and stock reverse, books tally", async () => {
  const tank = ok(await call(admin, "GET", "/api/stations"), "st").flatMap((s: any) => s.tanks).find((t: any) => t.capacity_l - t.current_l > 6000);
  const sup = ok(await call(admin, "GET", "/api/suppliers"), "sup")[0];
  const owedBefore = await supOwed(sup.id);
  const stockBefore = ok(await call(admin, "GET", "/api/stations"), "st2").flatMap((s: any) => s.tanks).find((t: any) => t.id === tank.id).current_l;

  const del = ok(await call(admin, "POST", "/api/stock/delivery", { tank_id: tank.id, invoice_l: 5000, received_l: 5000, tanker_no: "TLR-VOID", supplier_id: sup.id, purchase_rate: 250 }), "delivery").id;
  assert.ok(Math.abs((await supOwed(sup.id)) - (owedBefore + 5000 * 250)) < 1, "payable rose by invoice value");
  await tallyBooks("after delivery");

  const dlist = ok(await call(admin, "GET", "/api/corrections/deliveries"), "dlist");
  assert.ok(dlist.deliveries.find((d: any) => d.id === del && d.voidable), "delivery listed voidable");
  ok(await call(admin, "POST", `/api/corrections/delivery/${del}/void`, { reason: "galat tank/ghalat rate" }), "void delivery");

  assert.equal(Math.round(await supOwed(sup.id)), Math.round(owedBefore), "supplier payable restored");
  const stockAfter = ok(await call(admin, "GET", "/api/stations"), "st3").flatMap((s: any) => s.tanks).find((t: any) => t.id === tank.id).current_l;
  assert.ok(Math.abs(stockAfter - stockBefore) < 0.01, "tank stock restored");
  await tallyBooks("after voiding the delivery");
});

test("void a direct supplier payment (with withholding) — payable + tax reverse, books tally", async () => {
  const sup = ok(await call(admin, "GET", "/api/suppliers"), "sup")[0];
  const owedBefore = await supOwed(sup.id);
  // a payment with Rs 200 income tax withheld: supplier gets 5000, FBR gets 200
  ok(await call(admin, "POST", `/api/suppliers/${sup.id}/payment`, { amount: 5000, method: "Cash", withholding: 200, wht_section: "153" }), "supplier payment");
  await tallyBooks("after supplier payment+WHT");
  const txn = ok(await call(admin, "GET", "/api/corrections/supplier-txns"), "stx list").txns.find((x: any) => x.type === "payment" && x.amount === 5000 && x.voidable);
  assert.ok(txn, "the direct payment is listed and voidable");
  const r = ok(await call(admin, "POST", `/api/corrections/supplier-txn/${txn.id}/void`, { reason: "galat depot ko chala gaya" }), "void stx");
  assert.equal(Math.round(r.withholding_reversed), 200, "withholding also reversed");
  assert.equal(Math.round(await supOwed(sup.id)), Math.round(owedBefore), "supplier payable restored");
  await tallyBooks("after voiding the supplier payment");
});

test("void a wholesale supply — client due and tank stock reverse, books tally", async () => {
  const clients = ok(await call(admin, "GET", "/api/wholesale/clients"), "wc");
  const cl = clients.find((c: any) => c.rates?.PMG > 0) ?? clients[0];
  const station = ok(await call(admin, "GET", "/api/stations"), "st")[0];
  const dueBefore = cl.due;
  // a payment is always reversible; test that first
  ok(await call(admin, "POST", `/api/wholesale/clients/${cl.id}/payment`, { amount: 1000, method: "Cash" }), "wholesale payment");
  await tallyBooks("after wholesale payment");
  const payTxn = ok(await call(admin, "GET", "/api/corrections/wholesale-txns"), "wtx list").txns.find((x: any) => x.type === "payment" && x.amount === 1000 && x.voidable);
  assert.ok(payTxn, "wholesale payment listed voidable");
  ok(await call(admin, "POST", `/api/corrections/wholesale-txn/${payTxn.id}/void`, { reason: "galat client" }), "void wholesale payment");
  await tallyBooks("after voiding wholesale payment");
  const dueAfterPay = ok(await call(admin, "GET", "/api/wholesale/clients"), "wc2").find((c: any) => c.id === cl.id).due;
  assert.equal(Math.round(dueAfterPay), Math.round(dueBefore), "due restored after payment void");

  // and a supply, if this client has a PMG rate (restores tank stock too)
  if (cl.rates?.PMG > 0) {
    const tankId = station.tanks.find((t: any) => t.product === "PMG")?.id;
    const stockBefore = ok(await call(admin, "GET", "/api/stations"), "st2")[0].tanks.find((t: any) => t.id === tankId).current_l;
    const sup = ok(await call(admin, "POST", `/api/wholesale/clients/${cl.id}/supply`, { station_id: station.id, product: "PMG", litres: 20 }), "supply");
    await tallyBooks("after wholesale supply");
    const supTxn = ok(await call(admin, "GET", "/api/corrections/wholesale-txns"), "wtx2").txns.find((x: any) => x.type === "supply" && x.voidable);
    ok(await call(admin, "POST", `/api/corrections/wholesale-txn/${supTxn.id}/void`, { reason: "galat supply entry" }), "void supply");
    const stockAfter = ok(await call(admin, "GET", "/api/stations"), "st3")[0].tanks.find((t: any) => t.id === tankId).current_l;
    assert.ok(Math.abs(stockAfter - stockBefore) < 0.01, "tank stock restored after supply void");
    await tallyBooks("after voiding wholesale supply");
  }
});

test("void a rent receipt — cash/bank + rent income reverse, books tally", async () => {
  const unit = ok(await call(admin, "POST", "/api/rentals", { name: "Tuck shop", kind: "shop", monthly_rent: 20000 }), "rental");
  await tallyBooks("after making the rental unit");
  ok(await call(admin, "POST", `/api/rentals/${unit.id}/pay`, { amount: 20000, method: "cash", for_month: "2026-01" }), "rent received");
  await tallyBooks("after rent received");
  const pay = ok(await call(admin, "GET", "/api/corrections/rent"), "rent list").payments.find((p: any) => p.amount === 20000);
  assert.ok(pay, "rent payment listed");
  ok(await call(admin, "POST", `/api/corrections/rent/${pay.id}/void`, { reason: "galti se do dafa laga diya" }), "void rent");
  await tallyBooks("after voiding the rent receipt");
});

test("a reason is required for every correction", async () => {
  const sale = ok(await call(salesman, "POST", "/api/sales", { station_id: 1, product: "PMG", amount: 100, payment_method: "cash" }), "s").id;
  assert.equal((await call(admin, "POST", `/api/corrections/sale/${sale}/void`, { reason: "x" })).status, 400);
  ok(await call(admin, "POST", `/api/corrections/sale/${sale}/void`, { reason: "proper wajah" }), "ok with reason");
});
