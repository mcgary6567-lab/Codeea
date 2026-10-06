/**
 * One whole pump day through every money path, then every book is checked against every other:
 * shift (POS cash / card / JazzCash / khata + slip photo, late slip, online total from the machine) → cashier handover →
 * khata payment by bank → wholesale supply, JazzCash payment, cheque deposited and cleared → supplier paid in cash →
 * expense → cash put in the bank. Cash book, day book, bank balances, khata, wholesale, supplier, stock, ledger and
 * the owner / manager / cashier screens must all tell the same story.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-fullday-"));
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
const near = (a: number, b: number, msg?: string) => assert.ok(Math.abs(a - b) < 0.06, `${msg ?? ""} ${a} ≈ ${b}`);
const PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
const photo = async (who: string) => ok(await call(who, "POST", "/api/ai/read-photo", { kind: "proof", image: PNG }), "photo").photo_id as number;
const today = () => new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10);

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "wholesale", "salesman", "cashier"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => server?.close());

/* ---------- what every book says right now ---------- */
let acc1 = 0, acc2 = 0, K: any, W: any, S: any;
async function books() {
  const banks = ok(await call("admin", "GET", "/api/bank/accounts"), "banks");
  const l = ok(await call("admin", "GET", `/api/ledger?from=${today()}&to=${today()}`), "ledger");
  const tb = (a: string) => l.trial_balance.find((x: any) => x.account === a)?.balance ?? 0;
  return {
    cash: ok(await call("admin", "GET", "/api/cash"), "cash").cash_in_hand as number,
    b1: banks.accounts.find((a: any) => a.id === acc1).balance as number,
    b2: banks.accounts.find((a: any) => a.id === acc2).balance as number,
    banks: banks.total as number,
    khata: db.get("SELECT balance FROM customers WHERE id=?", K.id).balance as number,
    due: (ok(await call("wholesale", "GET", "/api/wholesale/clients"), "clients").find((c: any) => c.id === W.id)).due as number,
    owed: (ok(await call("admin", "GET", "/api/suppliers"), "sup").find((s: any) => s.id === S.id)).owed as number,
    tanks: Object.fromEntries(db.all("SELECT t.product, SUM(t.current_l) l FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=1 GROUP BY t.product").map((r: any) => [r.product, r.l])) as Record<string, number>,
    l, tb,
  };
}
let B0: Awaited<ReturnType<typeof books>>;
let shiftId = 0, live: any, counted = 0, sales: Record<string, number> = {};

test("setup: card and JazzCash linked to their banks, the cash counted, all shifts closed", async () => {
  db.run("UPDATE shifts SET status='closed', closed_at=?, cash_actual=0, handed_amount=0, handed_at=? WHERE status='open'", new Date().toISOString(), new Date().toISOString());
  const accs = ok(await call("admin", "GET", "/api/bank/accounts"), "banks").accounts.filter((a: any) => a.active);
  acc1 = accs[0].id; acc2 = accs[1].id;
  ok(await call("admin", "PUT", "/api/bank/pos-map", { card: acc1, jazzcash: acc2 }), "pos map");
  ok(await call("admin", "POST", "/api/cash/count", { amount: 100000 }), "count");
  K = ok(await call("salesman", "GET", "/api/pos/khata-accounts"), "k").find((a: any) => !a.khata_blocked && a.status !== "full");
  db.run("UPDATE customers SET credit_limit = balance + 1000000 WHERE id=?", K.id);
  W = ok(await call("wholesale", "GET", "/api/wholesale/clients"), "clients")[0];
  S = ok(await call("admin", "GET", "/api/suppliers"), "sup").find((s: any) => s.owed > 10000);
  assert.ok(S, "a supplier we owe");
  B0 = await books();
});

test("the shift: POS cash, card, JazzCash and khata with a slip photo; closed with the machine total and a late slip", async () => {
  shiftId = ok(await call("salesman", "POST", "/api/shifts/open", {}), "open").id;
  live = ok(await call("salesman", "GET", `/api/shifts/${shiftId}/live`), "live");
  const st = live.shift.station_id;
  const sale = async (b: any) => ok(await call("salesman", "POST", "/api/sales", { station_id: st, ...b }), "sale");
  const s1 = await sale({ product: "PMG", amount: 2000, payment_method: "cash" });
  const s2 = await sale({ product: "PMG", amount: 3000, payment_method: "card" });
  const s3 = await sale({ product: "HSD", amount: 1500, payment_method: "jazzcash" });
  const s4 = await sale({ product: "HSD", litres: 20, payment_method: "khata", customer_id: K.id, slip_no: "FD-1", photo_id: await photo("salesman") });
  sales = { pmgPos: s1.litres + s2.litres, hsdPos: s3.litres + s4.litres, khataPos: s4.amount };
  // meters: 100 L petrol and 80 L diesel pumped in all
  const readings: Record<string, number> = Object.fromEntries(live.readings.map((r: any) => [r.nozzle_id, r.opening]));
  const first = (p: string) => live.readings.find((r: any) => r.product === p);
  readings[first("PMG").nozzle_id] += 100; readings[first("HSD").nozzle_id] += 80;
  const body = { readings, digital: { card: 5000, jazzcash: 1500 }, khata: [{ customer_id: K.id, product: "HSD", litres: 10, vehicle_no: "LEA-1", slip_no: "FD-2", photo_id: await photo("salesman") }] };
  const p = ok(await call("salesman", "POST", `/api/shifts/${shiftId}/preview`, body), "preview");
  counted = Math.round(p.cash_expected);
  const c = ok(await call("salesman", "POST", `/api/shifts/${shiftId}/close`, { ...body, cash_actual: counted }), "close");
  near(c.cash_expected, p.cash_expected, "closed = preview");
  const rep = ok(await call("manager", "GET", `/api/shifts/${shiftId}/report`), "report");
  // every litre the meters show is in exactly one place
  for (const f of rep.fuels) near(f.meter_l - f.test_l, f.khata_l + f.digital_l + f.other_l + f.cash_l, `${f.product} meter = khata + online + cash`);
  near(rep.online.find((o: any) => o.method === "card").total, 5000, "card = the machine total");
  near(rep.online.find((o: any) => o.method === "card").at_close, 2000, "2000 added at close");
  near(rep.online.find((o: any) => o.method === "jazzcash").total, 1500, "JazzCash, nothing added");

  const B = await books();
  near(B.tanks.PMG, B0.tanks.PMG - 100, "petrol stock down by the meter");
  near(B.tanks.HSD, B0.tanks.HSD - 80, "diesel stock down by the meter");
  near(B.b1 - B0.b1, 5000, "card money in its bank");
  near(B.b2 - B0.b2, 1500, "JazzCash money in its bank");
  const khataNow = db.get("SELECT COALESCE(SUM(amount),0) v FROM sales WHERE shift_id=? AND payment_method='khata'", shiftId).v;
  near(B.khata - B0.khata, khataNow, "both slips on the khata");
  near(B.cash, B0.cash, "the cash is still with the salesman");
  const o = ok(await call("admin", "GET", "/api/owner/overview"), "overview");
  assert.ok(o.money.with_salesmen >= counted - 1, "owner sees the cash with the salesman");
  // the khata statement and the customer's own page agree with the balance
  const stmt = ok(await call("manager", "GET", `/api/customers/${K.id}/statement`), "statement");
  near(stmt.closing_balance, B.khata, "statement = balance");
  assert.ok(stmt.lines.filter((l: any) => ["FD-1", "FD-2"].includes(l.slip_no)).every((l: any) => l.proof_ids), "both slips carry their photo");
});

test("the rest of the day: handover, khata by bank, wholesale, cheque, supplier, expense, bank deposit", async () => {
  // handing the bag to the cashier moves money from "with the salesman" to the office — the pump is not richer or poorer
  const bs0 = ok(await call("admin", "GET", "/api/analysis/pl"), "bs").balance_sheet, ov0 = ok(await call("admin", "GET", "/api/owner/overview"), "ov");
  assert.ok(bs0.assets.cash_with_salesmen >= counted - 1, "balance sheet counts the cash with the salesman");
  ok(await call("cashier", "POST", `/api/cashier/handovers/${shiftId}`, { amount: counted }), "handover");
  const bs1 = ok(await call("admin", "GET", "/api/analysis/pl"), "bs").balance_sheet, ov1 = ok(await call("admin", "GET", "/api/owner/overview"), "ov");
  const r = (a: number, b: number, m: string) => assert.ok(Math.abs(a - b) <= 2, `${m}: ${a} ≈ ${b}`); // whole rupees
  r(bs1.assets.cash_in_hand - bs0.assets.cash_in_hand, counted, "office cash up");
  r(bs1.assets.cash_with_salesmen - bs0.assets.cash_with_salesmen, -counted, "salesman cash down");
  r(bs1.net_worth, bs0.net_worth, "net worth unchanged by the handover");
  near(ov1.net_position, ov0.net_position, "owner's net position unchanged by the handover");
  ok(await call("cashier", "POST", `/api/customers/${K.id}/khata`, { type: "credit", amount: 5000, method: "Bank transfer", account_id: acc1, notify: false }), "khata by bank");
  const st = live.shift.station_id;
  const sup = ok(await call("wholesale", "POST", `/api/wholesale/clients/${W.id}/supply`, { station_id: st, product: "HSD", litres: 200, override_limit: true }), "supply");
  sales.supply = sup.amount;
  ok(await call("wholesale", "POST", `/api/wholesale/clients/${W.id}/payment`, { amount: 10000, method: "JazzCash", account_id: acc2 }), "wholesale JazzCash");
  const chq = ok(await call("wholesale", "POST", `/api/wholesale/clients/${W.id}/payment`, { amount: 7000, method: "Cheque", ref: "FD-CHQ", photo_ids: [await photo("wholesale")] }), "cheque in hand");
  assert.ok(chq.cheque_pending);
  const q = ok(await call("cashier", "GET", "/api/cashier/cheques"), "register").cheques.find((x: any) => x.src === "w" && x.amount === 7000 && x.status === "in_hand");
  ok(await call("wholesale", "POST", `/api/wholesale/cheques/${q.id}/deposit`, { account_id: acc1 }), "deposit");
  ok(await call("wholesale", "POST", `/api/wholesale/cheques/${q.id}/clear`, {}), "clear");
  ok(await call("cashier", "POST", "/api/cashier/pay", { party_type: "supplier", party_id: S.id, amount: 4000, method: "Cash" }), "supplier in cash");
  ok(await call("cashier", "POST", "/api/expenses", { category: "Tea & food", amount: 1200, method: "cash" }), "expense");
  ok(await call("cashier", "POST", "/api/cash/deposits", { amount: 20000, account_id: acc1 }), "cash to bank");
  // a shop sale by card goes to the card's bank; a khata payment by JazzCash into a named account goes to that bank
  const item = db.get("SELECT id, price FROM shop_items WHERE station_id=? AND active=1 AND stock > 5 ORDER BY id LIMIT 1", st);
  const shop = ok(await call("manager", "POST", "/api/shop/sales", { station_id: st, lines: [{ item_id: item.id, qty: 1 }], payment_method: "card" }), "shop card");
  sales.shopCard = shop.total ?? shop.sale?.total ?? item.price;
  ok(await call("cashier", "POST", `/api/customers/${K.id}/khata`, { type: "credit", amount: 2500, method: "JazzCash", account_id: acc2, notify: false }), "khata JazzCash");
});

test("every book agrees", async () => {
  const B = await books();
  // cash: shift cash in, supplier, expense and the deposit out
  near(B.cash - B0.cash, counted - 4000 - 1200 - 20000, "cash book");
  // banks: card + khata transfer + cheque + deposit / JazzCash + wholesale JazzCash
  near(B.b1 - B0.b1, 5000 + 5000 + 7000 + 20000 + sales.shopCard, "bank 1");
  near(B.b2 - B0.b2, 1500 + 10000 + 2500, "bank 2");
  near(B.banks - B0.banks, (B.b1 - B0.b1) + (B.b2 - B0.b2), "bank total moved only by these two");
  // parties
  const khataFills = db.get("SELECT COALESCE(SUM(amount),0) v FROM sales WHERE shift_id=? AND payment_method='khata'", shiftId).v;
  near(B.khata - B0.khata, khataFills - 5000 - 2500, "khata");
  near(B.due - B0.due, sales.supply - 10000 - 7000, "wholesale due");
  near(B.owed - B0.owed, -4000, "supplier");
  near(B.tanks.HSD, B0.tanks.HSD - 80 - 200, "diesel stock: meter + wholesale");

  // the ledger moves exactly like the books (today's trial balance, before vs after)
  const d = (a: string) => B.tb(a) - B0.tb(a);
  near(d("Cash in hand"), B.cash - B0.cash, "ledger cash = cash book");
  near(d("Bank"), B.banks - B0.banks, "ledger bank = bank accounts");
  near(d("Khata receivable"), B.khata - B0.khata, "ledger khata = khata balances");
  near(d("Wholesale receivable"), B.due - B0.due, "ledger wholesale = wholesale due");
  near(d(`Payable — ${S.name}`), -(B.owed - B0.owed), "ledger supplier = supplier balance");
  near(d("Digital collections (Easypaisa/JazzCash/Card/Raast)"), 0, "linked online money is not left in 'digital collections'");
  near(B.l.totals.debit, B.l.totals.credit, "journal balances");

  // day book
  const book = ok(await call("cashier", "GET", "/api/cashier/daybook"), "daybook");
  const t = book.totals;
  near(book.cash.opening + t.in_cash - t.out_cash - t.deposited + t.withdrawn + t.counted, book.cash.closing, "day book adds up");
  near(book.cash.closing, B.cash, "day book closing = cash book");
  assert.ok(book.rows.some((r: any) => r.what === "Shift cash received"), "handover on the day book");

  // the three dashboards show the same money
  const o = ok(await call("admin", "GET", "/api/owner/overview"), "overview");
  near(o.money.office_cash, B.cash, "owner: office cash");
  near(o.money.banks, B.banks, "owner: banks");
  near(o.money.total, o.money.office_cash + o.money.with_salesmen + o.money.banks, "owner: money total");
  near(o.net_position, o.money.total + o.owed_to_us.total - o.we_owe.total, "owner: net position");
  const bs = ok(await call("admin", "GET", "/api/analysis/pl"), "bs").balance_sheet;
  // the balance sheet is in whole rupees
  assert.ok(Math.abs(bs.assets.cash_in_hand + bs.assets.cash_with_salesmen + bs.assets.banks - o.money.total) <= 3, "balance sheet money = owner's money");
  const desk = ok(await call("cashier", "GET", "/api/cashier/desk"), "desk");
  near(desk.cash.in_hand, B.cash, "cashier: cash in hand");
  near(desk.online.total, o.online.total, "cashier online = owner online");
  near(ok(await call("manager", "GET", "/api/dashboard/desk"), "manager").online.total, o.online.total, "manager online = owner online");
  const card = o.online.methods.find((m: any) => m.method === "card");
  assert.equal(card.account_id, acc1, "card shown going to its bank");
});
