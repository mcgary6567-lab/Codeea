import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-cardfee-"));
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

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => server?.close());

test("bank card fee (1.8%) is deducted on card sales — books tally and the statement shows it", async () => {
  const { tallyBooks } = await import("./helpers/tally.js");
  // a bank whose POS machine takes card money, with a 1.8% merchant fee
  const mzn = ok(await call("admin", "POST", "/api/bank/accounts", { bank: "Meezan Bank", opening_balance: 0, card_fee_pct: 1.8 }), "account");
  assert.equal(mzn.card_fee_pct, 1.8);
  ok(await call("admin", "PUT", "/api/bank/pos-map", { card: mzn.id }), "map card");
  // a card sale (inserted directly so the test does not need an open shift)
  db.run("INSERT INTO sales (station_id,product,litres,rate,amount,payment_method,created_at) VALUES (1,'PMG',400,250,100000,'card',?)", new Date().toISOString());

  const since = db.get("SELECT opening_date d FROM bank_accounts WHERE id=?", mzn.id)!.d as string;
  const start = new Date(`${since}T00:00:00+05:00`).toISOString();
  const cardTotal = (db.get("SELECT COALESCE(SUM(s.amount),0) v FROM sales s JOIN stations st ON st.id=s.station_id WHERE st.tenant_id=1 AND s.payment_method='card' AND s.created_at >= ?", start)!.v as number)
    + (db.get("SELECT COALESCE(SUM(total),0) v FROM shop_sales WHERE tenant_id=1 AND payment_method='card' AND created_at >= ?", start)!.v as number);
  assert.ok(cardTotal >= 100000, "card sales counted");

  const a = ok(await call("admin", "GET", "/api/bank/accounts"), "accounts").accounts.find((x: any) => x.id === mzn.id);
  // the bank only got the NET: gross card sales minus the 1.8% fee
  const fee = Math.round(cardTotal * 0.018);
  assert.ok(Math.abs((cardTotal - a.balance) - fee) <= 2, `net balance: got ${a.balance}, expected ${cardTotal - fee}`);

  // the statement shows the card-fee line(s)
  const st = ok(await call("admin", "GET", `/api/bank/accounts/${mzn.id}/statement`), "statement");
  const feeLines = (st.lines as any[]).filter((l) => l.kind === "card_fee");
  assert.ok(feeLines.length >= 1, "a card-fee line exists");
  assert.ok(/card charges/i.test(feeLines[0].text), "labelled as card charges");
  const feeSum = Math.round(-feeLines.reduce((s, l) => s + l.amount, 0));
  assert.ok(Math.abs(feeSum - fee) <= 2, `fee total ${feeSum} ≈ ${fee}`);

  // the whole system still tallies (ledger has Expense: Card charges against the bank)
  await tallyBooks("card-fee");

  // turn the fee off → the bank gets the full gross again, still tallies
  ok(await call("admin", "PATCH", `/api/bank/accounts/${mzn.id}`, { card_fee_pct: 0 }), "fee off");
  const b = ok(await call("admin", "GET", "/api/bank/accounts"), "accounts").accounts.find((x: any) => x.id === mzn.id);
  assert.ok(Math.abs(b.balance - cardTotal) <= 2, `full gross after fee off: ${b.balance} ≈ ${cardTotal}`);
  await tallyBooks("card-fee-off");
});
