import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-banks-"));
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

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman", "wholesale"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => server?.close());

test("each bank account's balance tallies every payment, deposit, sale and bank entry", async () => {
  // only the owner sees balances; staff can still pick an account on a payment
  assert.equal((await call("manager", "GET", "/api/bank/accounts")).status, 403);
  assert.equal((await call("manager", "POST", "/api/bank/accounts", { bank: "HBL" })).status, 403);
  const hbl = ok(await call("admin", "POST", "/api/bank/accounts", { bank: "Habib Bank (HBL)", branch: "Ferozepur Road", account_no: "0123 4567 8901", opening_balance: 500000 }), "hbl");
  const mzn = ok(await call("admin", "POST", "/api/bank/accounts", { bank: "Meezan Bank", opening_balance: 0 }), "meezan");
  assert.match(hbl.name, /HBL.*Ferozepur Road ··8901/);
  assert.equal(hbl.balance, 500000);
  const pick = ok(await call("manager", "GET", "/api/bank/accounts/pick"), "pick").accounts;
  assert.ok([hbl.id, mzn.id].every((id) => pick.some((a: any) => a.id === id)));
  assert.ok(pick.every((a: any) => a.balance === undefined), "picker never shows balances");

  // cash deposited by the manager into HBL
  ok(await call("admin", "POST", "/api/cash/count", { amount: 200000 }), "start the cash book");
  ok(await call("manager", "POST", "/api/cash/deposits", { amount: 150000, account_id: hbl.id, slip_ref: "D-1" }), "deposit");
  assert.match(db.get("SELECT bank FROM bank_deposits ORDER BY id DESC LIMIT 1")!.bank, /HBL/);

  // wholesale payment by bank transfer into Meezan; a cash payment never goes to a bank
  const client = db.get("SELECT id FROM wholesale_clients WHERE tenant_id=1 LIMIT 1")!;
  const wp = ok(await call("wholesale", "POST", `/api/wholesale/clients/${client.id}/payment`, { amount: 80000, method: "Bank transfer", account_id: mzn.id }), "wholesale payment");
  ok(await call("wholesale", "POST", `/api/wholesale/clients/${client.id}/payment`, { amount: 5000, method: "Cash", account_id: mzn.id }), "cash payment");
  // khata payment by Raast into HBL
  const cust = db.get("SELECT id FROM customers WHERE tenant_id=1 AND type<>'retail' ORDER BY balance DESC LIMIT 1")!;
  ok(await call("manager", "POST", `/api/customers/${cust.id}/khata`, { type: "credit", amount: 20000, method: "Raast", account_id: hbl.id, notify: false }), "khata payment");
  // supplier paid from HBL, an expense from Meezan
  const sup = db.get("SELECT id FROM suppliers WHERE tenant_id=1 LIMIT 1")!;
  ok(await call("manager", "POST", `/api/suppliers/${sup.id}/payment`, { amount: 100000, method: "Bank transfer", account_id: hbl.id }), "supplier payment");
  ok(await call("admin", "POST", "/api/expenses", { category: "Other", amount: 3000, method: "bank", account_id: mzn.id }), "expense");

  // card sales settle into Meezan
  ok(await call("admin", "PUT", "/api/bank/pos-map", { card: mzn.id }), "map card");
  ok(await call("salesman", "POST", "/api/shifts/open", {}), "open shift");
  const sale = ok(await call("salesman", "POST", "/api/sales", { station_id: 1, product: "PMG", amount: 2000, payment_method: "card" }), "card sale");

  // bank-only entries: cash taken out of HBL comes into the office cash book; transfer HBL → Meezan; charges
  const cashBefore = ok(await call("admin", "GET", "/api/cash"), "cash").cash_in_hand;
  ok(await call("admin", "POST", "/api/bank/entries", { account_id: hbl.id, kind: "withdraw", amount: 30000 }), "withdraw");
  near(ok(await call("admin", "GET", "/api/cash"), "cash").cash_in_hand, cashBefore + 30000, "withdrawal adds to cash in hand");
  ok(await call("admin", "POST", "/api/bank/transfers", { from_id: hbl.id, to_id: mzn.id, amount: 50000, ref: "IBFT-9" }), "transfer");
  ok(await call("admin", "POST", "/api/bank/entries", { account_id: mzn.id, kind: "charges", amount: 250 }), "charges");

  // every card sale since the account's opening day (the demo data already has some today)
  const since = db.pkStart(db.pkDate());
  const card = db.get("SELECT COALESCE(SUM(s.amount),0) v FROM sales s JOIN stations st ON st.id=s.station_id WHERE st.tenant_id=1 AND s.payment_method='card' AND s.created_at >= ?", since)!.v
    + db.get("SELECT COALESCE(SUM(total),0) v FROM shop_sales WHERE tenant_id=1 AND payment_method='card' AND created_at >= ?", since)!.v;
  assert.ok(card >= sale.amount);
  let r = ok(await call("admin", "GET", "/api/bank/accounts"), "accounts");
  const bal = (id: number) => r.accounts.find((a: any) => a.id === id).balance;
  near(bal(hbl.id), 500000 + 150000 + 20000 - 100000 - 30000 - 50000, "HBL");
  near(bal(mzn.id), 80000 - 3000 + card + 50000 - 250, "Meezan");
  assert.equal(typeof r.cash_in_hand, "number");
  assert.ok(r.accounts.every((a: any) => typeof a.today_in === "number" && typeof a.today_out === "number"));
  near(r.total, r.accounts.filter((a: any) => a.active).reduce((t: number, a: any) => t + a.balance, 0), "total in banks");

  // statement runs from the opening to the closing balance
  const st = ok(await call("admin", "GET", `/api/bank/accounts/${mzn.id}/statement`), "statement");
  near(st.opening + st.money_in - st.money_out, st.closing);
  near(st.closing, bal(mzn.id));
  assert.ok(st.lines.some((l: any) => l.kind === "pos" && /card/.test(l.text)));
  assert.ok(st.lines.some((l: any) => /Wholesale payment/.test(l.text)));

  // voiding the wholesale payment takes it out of the bank; deleting a transfer removes both halves
  ok(await call("admin", "POST", `/api/wholesale/txns/${wp.id}/void`, { reason: "bounced cheque" }), "void");
  const tr = db.get("SELECT id FROM bank_txns WHERE kind='transfer' LIMIT 1")!;
  ok(await call("admin", "DELETE", `/api/bank/txns/${tr.id}`), "delete transfer");
  assert.equal(db.get("SELECT COUNT(*) n FROM bank_txns WHERE kind='transfer'")!.n, 0);
  r = ok(await call("admin", "GET", "/api/bank/accounts"), "accounts");
  near(bal(hbl.id), 500000 + 150000 + 20000 - 100000 - 30000, "HBL after");
  near(bal(mzn.id), -3000 + card - 250, "Meezan after");

  // money by bank without an account is flagged so the tally can be fixed
  ok(await call("manager", "POST", `/api/customers/${cust.id}/khata`, { type: "credit", amount: 7000, method: "Bank transfer", notify: false }), "unlinked");
  assert.ok(ok(await call("admin", "GET", "/api/bank/accounts"), "accounts").unlinked.received >= 7000);

  // a closed account cannot take new entries; another pump's account cannot be used
  ok(await call("admin", "PATCH", `/api/bank/accounts/${mzn.id}`, { active: false }), "close");
  assert.equal((await call("manager", "POST", `/api/customers/${cust.id}/khata`, { type: "credit", amount: 10, method: "Raast", account_id: mzn.id, notify: false })).status, 400);
  assert.equal((await call("manager", "POST", `/api/customers/${cust.id}/khata`, { type: "credit", amount: 10, method: "Raast", account_id: 99999, notify: false })).status, 400);

  // the general ledger carries the withdrawal as a contra entry
  const j = ok(await call("admin", "GET", `/api/ledger?from=${db.pkDate()}&to=${db.pkDate()}`), "journal");
  assert.ok(JSON.stringify(j).includes("Cash taken out of bank"));
});

test("a card sale can name its own bank's POS machine (credits that account, not the pos-map default)", async () => {
  const hblId = db.get("SELECT id FROM bank_accounts WHERE bank LIKE 'Habib%' AND tenant_id=1")!.id as number;
  const posList = ok(await call("salesman", "GET", "/api/pos/bank-pos"), "bank-pos").accounts;
  assert.ok(posList.some((a: any) => a.id === hblId), "HBL is offered as a POS machine");
  const bankBal = async (id: number) => ok(await call("admin", "GET", "/api/bank/accounts"), "accts").accounts.find((a: any) => a.id === id).balance;
  const before = await bankBal(hblId);
  const sale = ok(await call("salesman", "POST", "/api/sales", { station_id: 1, product: "PMG", amount: 1500, payment_method: "card", account_id: hblId }), "tagged card sale");
  assert.equal(sale.account_id, hblId);
  near((await bankBal(hblId)) - before, 1500, "the chosen bank POS was credited");
});

test("after all of the above: every book still tallies with the ledger", async () => {
  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("banks");
});
