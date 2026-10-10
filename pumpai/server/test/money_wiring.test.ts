import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-money-"));
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
const PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
const photo = async (who: string) => ok(await call(who, "POST", "/api/ai/read-photo", { kind: "proof", image: PNG }), "photo").photo_id as number;
const day = (n: number) => new Date(Date.now() + 5 * 3600_000 + n * 86_400_000).toISOString().slice(0, 10);

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "wholesale", "salesman", "cashier"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => server?.close());

const cash = async () => ok(await call("admin", "GET", "/api/cash"), "cash").cash_in_hand as number;
const bank = async (id: number) => ok(await call("admin", "GET", "/api/bank/accounts"), "banks").accounts.find((a: any) => a.id === id).balance as number;
const today = () => new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10);
const trial = async () => ok(await call("admin", "GET", `/api/ledger?from=${today()}&to=${today()}`), "ledger");
const tb = (l: any, acc: string) => l.trial_balance.find((x: any) => x.account === acc)?.balance ?? 0;
const dayBookAddsUp = async (msg: string) => {
  const b = ok(await call("cashier", "GET", "/api/cashier/daybook"), "daybook");
  if (b.cash.opening == null) {
    // the first cash count ever was today: the book starts at that line and adds up from there
    const start = b.rows.find((x: any) => x.start);
    assert.ok(start, `book start line (${msg})`);
    const after = b.rows.filter((x: any) => x.at > start.at && x.cash && x.dir !== "count");
    const move = after.reduce((a: number, x: any) => a + (x.dir === "in" ? x.amount : x.dir === "out" ? -x.amount : x.what === "Cash deposited in bank" ? -x.amount : 0), 0);
    near(start.amount + move + b.totals.withdrawn, b.cash.closing, `day book adds up from the count (${msg})`);
  } else {
    near(b.cash.opening + b.totals.in_cash - b.totals.out_cash - b.totals.deposited + b.totals.withdrawn + b.totals.counted, b.cash.closing, `day book adds up (${msg})`);
    near(b.rows.filter((x: any) => x.dir === "count").reduce((a: number, x: any) => a + x.signed, 0), b.totals.counted, `count line shown (${msg})`);
    // the day's opening is the book at the end of yesterday (or the count, on the day the book starts)
    if (!b.cash.starts_today) {
    const y = new Date(Date.now() + 5 * 3600_000 - 86400_000).toISOString().slice(0, 10);
    near(b.cash.opening, ok(await call("cashier", "GET", `/api/cashier/daybook?date=${y}`), "yesterday").cash.closing, `opening = yesterday's closing (${msg})`);
    }
  }
  near(b.cash.closing, await cash(), `day book closing = cash book (${msg})`);
};
let acc = 0;

test("setup: a cash count starts the book", async () => {
  acc = ok(await call("admin", "GET", "/api/bank/accounts"), "banks").accounts[0].id;
  ok(await call("admin", "POST", "/api/cash/count", { amount: 500000 }), "count");
  near(await cash(), 500000);
  await dayBookAddsUp("start");
});

test("staff advance by bank leaves the bank, not the cash; a manual deduction moves no cash", async () => {
  const u = ok(await call("cashier", "GET", "/api/cashier/parties?kind=staff"), "staff").staff.find((x: any) => x.role === "salesman");
  const c0 = await cash(), b0 = await bank(acc), l0 = await trial();
  ok(await call("cashier", "POST", "/api/cashier/pay", { party_type: "staff", party_id: u.id, amount: 1000, method: "Bank transfer", account_id: acc }), "advance by bank");
  near(await cash(), c0, "cash unchanged"); near(await bank(acc), b0 - 1000, "bank down");
  const l1 = await trial();
  near(tb(l1, "Cash in hand"), tb(l0, "Cash in hand"), "ledger cash unchanged"); near(tb(l1, "Bank"), tb(l0, "Bank") - 1000, "ledger bank down");
  ok(await call("admin", "POST", `/api/staff/${u.id}/entry`, { type: "deduction", amount: 700 }), "manual deduction");
  near(await cash(), c0, "deduction is not cash");
  await dayBookAddsUp("staff");
});

test("salary with an advance kept back: only the net leaves the cash", async () => {
  const u = ok(await call("cashier", "GET", "/api/cashier/parties?kind=staff"), "staff").staff.find((x: any) => x.role === "wholesale");
  ok(await call("admin", "POST", `/api/staff/${u.id}/entry`, { type: "advance", amount: 5000 }), "cash advance");
  const c0 = await cash();
  const r = ok(await call("admin", "POST", `/api/staff/${u.id}/pay-salary`, { deduct: 5000, skip_loan: true, photo_ids: [await photo("admin")] }), "salary");
  near(await cash(), c0 - r.net, "cash out = net salary");
  await dayBookAddsUp("salary");
});

test("shift cash counts when the cashier receives it; a short handover is charged to the salesman", async () => {
  const open = ok(await call("salesman", "POST", "/api/shifts/open", {}), "open");
  const live = ok(await call("salesman", "GET", `/api/shifts/${open.id}/live`), "live");
  ok(await call("salesman", "POST", `/api/shifts/${open.id}/close`, { readings: Object.fromEntries(live.readings.map((r: any) => [r.nozzle_id, r.opening])), cash_actual: 20000 }), "close");
  const c0 = await cash();
  ok(await call("admin", "POST", "/api/cash/count", { amount: c0 }), "count before the handover");
  const sm = ok(await call("cashier", "GET", "/api/cashier/parties?kind=staff"), "staff").staff.find((x: any) => x.role === "salesman");
  ok(await call("cashier", "POST", `/api/cashier/handovers/${open.id}`, { amount: 19000 }), "handover short 1000");
  near(await cash(), c0 + 19000, "received cash is in the book even after a count");
  const after = ok(await call("cashier", "GET", "/api/cashier/parties?kind=staff"), "staff2").staff.find((x: any) => x.id === sm.id);
  near(after.balance, sm.balance + 1000, "shortage on the salesman's account");
  await dayBookAddsUp("handover");
});

test("a cash sale with no shift open reaches the cash book", async () => {
  db.run("UPDATE shifts SET status='closed', closed_at=?, cash_actual=0, handed_amount=0, handed_at=? WHERE status='open'", new Date().toISOString(), new Date().toISOString());
  const st = ok(await call("admin", "GET", "/api/stations"), "stations")[0];
  const c0 = await cash();
  ok(await call("manager", "POST", "/api/sales", { station_id: st.id, product: "PMG", amount: 1000, payment_method: "cash" }), "counter sale");
  near(await cash(), c0 + 1000, "counter sale in cash");
  await dayBookAddsUp("counter sale");
});

test("claims recovered in cash / bank, tax deposited, coupons by Raast — all land in the books", async () => {
  const claimIds = db.all("SELECT id FROM shortage_claims WHERE status NOT IN ('recovered','written_off') ORDER BY id LIMIT 2").map((r: any) => r.id);
  const c0 = await cash(), b0 = await bank(acc);
  if (claimIds[0]) {
    const left = db.get("SELECT amount - recovered v FROM shortage_claims WHERE id=?", claimIds[0]).v;
    ok(await call("admin", "POST", `/api/claims/${claimIds[0]}/settle`, { action: "recovered", method: "cash" }), "claim cash");
    near(await cash(), c0 + left, "claim cash in");
  }
  if (claimIds[1]) {
    const left = db.get("SELECT amount - recovered v FROM shortage_claims WHERE id=?", claimIds[1]).v;
    ok(await call("admin", "POST", `/api/claims/${claimIds[1]}/settle`, { action: "recovered", method: "bank", account_id: acc }), "claim bank");
    near(await bank(acc), b0 + left, "claim bank in");
  }
  const w = ok(await call("admin", "POST", "/api/tax/withholding", { payee: "Test depot", gross: 10000, amount: 500 }), "wht");
  const c1 = await cash();
  ok(await call("admin", "POST", "/api/tax/withholding/deposit", { ids: [w.id], cpr_no: "CPR-TEST-1", method: "cash" }), "wht deposit");
  near(await cash(), c1 - 500, "tax paid out of cash");
  const b1 = await bank(acc);
  ok(await call("admin", "POST", "/api/coupons", { count: 2, value: 1000, method: "raast", account_id: acc }), "coupons raast");
  near(await bank(acc), b1 + 2000, "coupon money in the bank");
  await dayBookAddsUp("claims/tax/coupons");
});

test("cheques not yet in the bank wait in the register; a khata payment with no method is cash everywhere", async () => {
  const sup = ok(await call("admin", "GET", "/api/suppliers"), "sup")[0];
  const b0 = await bank(acc);
  const r = ok(await call("admin", "POST", `/api/suppliers/${sup.id}/payment`, { amount: 3000, method: "Cheque", ref: "777001", account_id: acc, photo_ids: [await photo("admin")] }), "supplier cheque");
  assert.ok(r.cheque_pending); near(await bank(acc), b0, "bank unchanged until the cheque is paid");
  const reg = ok(await call("cashier", "GET", "/api/cashier/cheques"), "reg").cheques.find((q: any) => q.src === "c" && q.cheque_no === "777001");
  assert.equal(reg.status, "issued");
  ok(await call("cashier", "POST", `/api/cashier/cheques/${reg.id}/clear`, {}), "clear");
  near(await bank(acc), b0 - 3000, "bank down when paid");
  const client = ok(await call("wholesale", "GET", "/api/wholesale/clients"), "clients")[0];
  const w = ok(await call("wholesale", "POST", `/api/wholesale/clients/${client.id}/payment`, { amount: 7000, method: "Cheque", ref: "88001", photo_ids: [await photo("wholesale")] }), "wholesale cheque in hand");
  assert.ok(w.cheque_pending); near(w.due_after, client.due, "due unchanged until it clears");
  const k = ok(await call("manager", "GET", "/api/pos/khata-accounts"), "k").find((a: any) => a.balance > 1000);
  const c0 = await cash();
  ok(await call("manager", "POST", `/api/customers/${k.id}/khata`, { type: "credit", amount: 500, notify: false }), "khata no method");
  near(await cash(), c0 + 500, "counted as cash");
  await dayBookAddsUp("cheques/khata");
});

test("backdated bank entry stays on its own day; cash counter vouchers reach the journal; journal balances", async () => {
  const y = new Date(Date.now() + 5 * 3600_000 - 86_400_000).toISOString().slice(0, 10);
  ok(await call("admin", "POST", "/api/bank/entries", { account_id: acc, kind: "charges", amount: 123, txn_date: y }), "backdated charge");
  const st = ok(await call("admin", "GET", `/api/bank/accounts/${acc}/statement?from=${y}&to=${y}`), "st y");
  assert.ok(st.lines.some((l: any) => l.amount === -123), "on yesterday's statement");
  const st2 = ok(await call("admin", "GET", `/api/bank/accounts/${acc}/statement?from=${today()}&to=${today()}`), "st t");
  assert.ok(!st2.lines.some((l: any) => l.amount === -123), "not again today");
  near(st2.opening, st.closing, "today opens where yesterday closed");
  const l0 = await trial();
  ok(await call("cashier", "POST", "/api/cashier/receive", { party_type: "other", party_name: "Scrap", category: "Scrap sale", amount: 300, method: "Cash" }), "other in");
  const l1 = await trial();
  near(tb(l1, "Cash in hand"), tb(l0, "Cash in hand") + 300, "voucher in the journal");
  near(l1.totals.debit, l1.totals.credit, "journal balances");
});

test("owner overview adds up from the same books; quick answers find the right party", async () => {
  const o = ok(await call("admin", "GET", "/api/owner/overview"), "overview");
  near(o.money.office_cash, await cash(), "office cash = cash book");
  near(o.money.banks, ok(await call("admin", "GET", "/api/bank/accounts"), "banks").total, "banks = bank total");
  near(o.money.total, o.money.office_cash + o.money.with_salesmen + o.money.banks, "money total");
  const s = (x: any) => Object.entries(x).filter(([k, v]) => typeof v === "number" && k !== "total").reduce((a, [, v]) => a + (v as number), 0);
  near(o.owed_to_us.total, s(o.owed_to_us), "owed to us total"); near(o.we_owe.total, s(o.we_owe), "we owe total");
  near(o.net_position, o.money.total + o.owed_to_us.total - o.we_owe.total, "net position");
  assert.ok(o.activity.length >= 10 && o.month.month);
  assert.equal((await call("salesman", "GET", "/api/owner/overview")).status, 403);
  const ask = async (q: string) => ok(await call("admin", "POST", "/api/ai/ask", { question: q }), q).answer as string;
  assert.match(await ask("Malik Petroleum ke zimme kitna hai"), /Malik Petroleum Services \(wholesale\)/);
  assert.doesNotMatch(await ask("Malik Petroleum ke zimme kitna hai"), /ki sale/);
  assert.match(await ask("kal kitna diesel bika"), /Kal \(/);
  assert.match(await ask("bank mein kitna paisa hai"), /Bankon mein/);
  assert.match(await ask("cheque kitne pending hain"), /Cheque:/);
});

test("after all of the above: every book still tallies with the ledger", async () => {
  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("money_wiring");
});
