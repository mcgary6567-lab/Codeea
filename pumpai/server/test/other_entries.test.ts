/** CEO-only "other income / expense / discount": the money/balance moves, the books still tally, and the P&L reflects it. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";
import { tallyBooks } from "./helpers/tally.js";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-other-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server, base = "", adminTok = "", salesTok = "", mgrTok = "";
async function call(tok: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tok ? { authorization: `Bearer ${tok}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, m: string) => { assert.equal(r.status, 200, `${m}: ${JSON.stringify(r.data)}`); return r.data; };
const month = new Date().toISOString().slice(0, 7);

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  adminTok = (await call("", "POST", "/api/auth/login", { email: "admin@pumpai.pk", password: "demo1234" })).data.token;
  salesTok = (await call("", "POST", "/api/auth/login", { email: "salesman@pumpai.pk", password: "demo1234" })).data.token;
  mgrTok = (await call("", "POST", "/api/auth/login", { email: "manager@pumpai.pk", password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("only the CEO can record other entries", async () => {
  assert.equal((await call(salesTok, "POST", "/api/other-entries", { kind: "income", party_type: "other", party_name: "X", reason: "test", amount: 100 })).status, 403);
  assert.equal((await call(salesTok, "GET", "/api/other-entries?kind=income")).status, 403);
});

test("income, expense and discount move the money/balance, tally, and hit the P&L", async () => {
  const parties = ok(await call(adminTok, "GET", "/api/other-entries/parties"), "parties");
  const khata = parties.khata[0]; const whole = parties.wholesale[0];
  assert.ok(khata && whole, "demo has a khata customer and a wholesale client");
  const acct = ok(await call(adminTok, "GET", "/api/bank/accounts/pick"), "accounts");
  const bankId = (Array.isArray(acct) ? acct : acct.accounts ?? [])[0]?.id;
  assert.ok(bankId, "demo has a bank account");

  const pl0 = ok(await call(adminTok, "GET", `/api/analysis/pl?month=${month}`), "pl0").pl;
  const custBefore = ok(await call(adminTok, "GET", "/api/other-entries/parties"), "p2"); // name only; balance checked via tally

  // 1) misc income in cash, attributed to a khata customer (name only — does NOT touch their khata)
  const inc1 = ok(await call(adminTok, "POST", "/api/other-entries", { kind: "income", party_type: "khata", party_id: khata.id, reason: "Generator kiraya", amount: 5000, method: "cash" }), "inc cash");
  assert.equal(inc1.kind, "income"); assert.match(String(inc1.src), /^voucher:/);
  // 2) misc income into a bank account
  const inc2 = ok(await call(adminTok, "POST", "/api/other-entries", { kind: "income", party_type: "other", party_name: "Scrap wala", reason: "Purana saman becha", amount: 3000, method: "bank", account_id: bankId }), "inc bank");
  assert.match(String(inc2.src), /^bank:/);
  // 3) an expense in cash
  ok(await call(adminTok, "POST", "/api/other-entries", { kind: "expense", party_type: "other", party_name: "Mistri", reason: "Gate welding", amount: 2000, method: "cash" }), "exp cash");
  // 4) discount to the khata customer (their balance must drop by 1000)
  ok(await call(adminTok, "POST", "/api/other-entries", { kind: "discount", party_type: "khata", party_id: khata.id, reason: "Purani adjustment", amount: 1000 }), "disc khata");
  // 5) discount to a wholesale client
  ok(await call(adminTok, "POST", "/api/other-entries", { kind: "discount", party_type: "wholesale", party_id: whole.id, reason: "Bonus rebate", amount: 500 }), "disc wholesale");
  // discount cannot apply to a free-text party
  assert.equal((await call(adminTok, "POST", "/api/other-entries", { kind: "discount", party_type: "other", party_name: "ZZ", reason: "x", amount: 10 })).status, 400);

  // the whole system still ties to the rupee
  await tallyBooks("after other entries");

  // P&L: +8000 income, −1500 discount, and a "Other (CEO)" expense of 2000
  const pl = ok(await call(adminTok, "GET", `/api/analysis/pl?month=${month}`), "pl").pl;
  assert.equal(pl.other_income_ceo, 8000);
  assert.equal(pl.other_discount_ceo, 1500);
  assert.ok(pl.expenses.by_category.some((c: any) => c.category === "Other (CEO)" && c.amount === 2000), "expense shows as Other (CEO)");
  // net profit = old + 8000 income − 2000 expense − 1500 discount
  assert.equal(pl.net_profit, Math.round((pl0.net_profit + 8000 - 2000 - 1500) * 100) / 100);

  // report groups by party
  const incRep = ok(await call(adminTok, "GET", `/api/other-entries?kind=income&from=${month}-01&to=${month}-28`), "inc report");
  assert.equal(incRep.total, 8000);
  assert.ok(incRep.by_party.some((p: any) => p.party_name === khata.name && p.total === 5000));
  const discRep = ok(await call(adminTok, "GET", `/api/other-entries?kind=discount`), "disc report");
  assert.equal(discRep.total, 1500);

  // undo the wholesale discount → tally still holds and the report drops it
  const discList = discRep.list; const wtxEntry = discList.find((e: any) => e.party_type === "wholesale");
  ok(await call(adminTok, "DELETE", `/api/other-entries/${wtxEntry.id}`), "undo");
  await tallyBooks("after undo");
  const discRep2 = ok(await call(adminTok, "GET", "/api/other-entries?kind=discount"), "disc report 2");
  assert.equal(discRep2.total, 1000);
  void custBefore;
});

test("the CEO entries are private — a manager cannot see them, but both books still balance", async () => {
  // state from the previous test: income 8000, expense 2000, discount (khata) 1000
  const adminPl = ok(await call(adminTok, "GET", `/api/analysis/pl?month=${month}`), "admin pl").pl;
  const mgrPl = ok(await call(mgrTok, "GET", `/api/analysis/pl?month=${month}`), "mgr pl").pl;

  // the owner sees the CEO lines; the manager sees none of them
  assert.equal(adminPl.other_income_ceo, 8000);
  assert.equal(mgrPl.other_income_ceo, 0);
  assert.equal(mgrPl.other_discount_ceo, 0);
  assert.ok(adminPl.expenses.by_category.some((c: any) => c.category === "Other (CEO)"), "owner sees the Other (CEO) expense");
  assert.ok(!mgrPl.expenses.by_category.some((c: any) => c.category === "Other (CEO)"), "manager does NOT see the Other (CEO) expense");
  // the manager's net profit excludes all three (8000 income − 2000 expense − 1000 discount = 5000)
  assert.equal(Math.round((adminPl.net_profit - mgrPl.net_profit) * 100) / 100, 5000);

  const g = (tb: any[], a: string) => tb.find((x) => x.account === a);
  const adminLed = ok(await call(adminTok, "GET", `/api/ledger?from=${month}-01&to=${month}-28`), "admin ledger");
  const mgrLed = ok(await call(mgrTok, "GET", `/api/ledger?from=${month}-01&to=${month}-28`), "mgr ledger");
  // both balance, nothing in Suspense
  assert.equal(adminLed.totals.debit, adminLed.totals.credit);
  assert.equal(mgrLed.totals.debit, mgrLed.totals.credit);
  assert.ok(!g(adminLed.trial_balance, "Suspense") && !g(mgrLed.trial_balance, "Suspense"));
  // the owner's ledger names the CEO accounts; the manager's does not (they appear as owner capital/drawings)
  assert.ok(g(adminLed.trial_balance, "Other income (CEO)") && g(adminLed.trial_balance, "Other discount (CEO)"));
  assert.ok(!g(mgrLed.trial_balance, "Other income (CEO)"), "manager ledger hides Other income (CEO)");
  assert.ok(!g(mgrLed.trial_balance, "Expense: Other (CEO)"), "manager ledger hides the Other (CEO) expense");
  assert.ok(!g(mgrLed.trial_balance, "Other discount (CEO)"), "manager ledger hides Other discount (CEO)");
  assert.ok(g(mgrLed.trial_balance, "Owner's capital") && g(mgrLed.trial_balance, "Owner's drawings"), "manager sees them only as owner capital/drawings");
});
