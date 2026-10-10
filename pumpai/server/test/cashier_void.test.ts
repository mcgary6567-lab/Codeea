/** CEO voids a wrong cash receive/pay voucher: the money/balance it moved is reversed and the books still tally. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";
import { tallyBooks } from "./helpers/tally.js";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-cvoid-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server, base = "", adminTok = "", cashTok = "";
async function call(tok: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tok ? { authorization: `Bearer ${tok}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, m: string) => { assert.equal(r.status, 200, `${m}: ${JSON.stringify(r.data)}`); return r.data; };

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  adminTok = (await call("", "POST", "/api/auth/login", { email: "admin@pumpai.pk", password: "demo1234" })).data.token;
  cashTok = (await call("", "POST", "/api/auth/login", { email: "cashier@pumpai.pk", password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("only the CEO can void a voucher", async () => {
  const kh = ok(await call(adminTok, "GET", "/api/cashier/parties?kind=khata"), "parties").khata[0];
  const v = ok(await call(cashTok, "POST", "/api/cashier/receive", { party_type: "khata", party_id: kh.id, amount: 300, method: "Cash" }), "receive").voucher;
  assert.equal((await call(cashTok, "POST", `/api/cashier/vouchers/${v.id}/void`, { reason: "galti" })).status, 403);
  // tidy up so it doesn't pollute later balances
  ok(await call(adminTok, "POST", `/api/cashier/vouchers/${v.id}/void`, { reason: "test cleanup" }), "admin void");
});

test("voiding each kind of voucher reverses the money and keeps the books tallied", async () => {
  const kh = ok(await call(adminTok, "GET", "/api/cashier/parties?kind=khata"), "p").khata.find((c: any) => c.balance > 2000) ?? ok(await call(adminTok, "GET", "/api/cashier/parties?kind=khata"), "p").khata[0];
  const sup = ok(await call(adminTok, "GET", "/api/cashier/parties?kind=supplier"), "p").supplier[0];
  const acc = ok(await call(adminTok, "GET", "/api/bank/accounts/pick"), "acc").accounts[0];

  const khBefore = ok(await call(adminTok, "GET", "/api/cashier/parties?kind=khata"), "p").khata.find((c: any) => c.id === kh.id).balance;
  const supBefore = ok(await call(adminTok, "GET", "/api/cashier/parties?kind=supplier"), "p").supplier.find((s: any) => s.id === sup.id).balance;
  const bankBefore = ok(await call(adminTok, "GET", "/api/bank/accounts"), "b").accounts.find((a: any) => a.id === acc.id).balance;

  // 1) khata payment in cash, 2) a cash "other" receive, 3) an "other" pay from the bank, 4) a supplier payment in cash
  const v1 = ok(await call(adminTok, "POST", "/api/cashier/receive", { party_type: "khata", party_id: kh.id, amount: 1000, method: "Cash" }), "khata pay").voucher;
  const v2 = ok(await call(adminTok, "POST", "/api/cashier/receive", { party_type: "other", party_name: "Scrap", amount: 500, method: "Cash", category: "Scrap sale" }), "other in").voucher;
  const v3 = ok(await call(adminTok, "POST", "/api/cashier/pay", { party_type: "other", party_name: "Misc", amount: 700, method: "Bank transfer", account_id: acc.id, category: "Misc" }), "other out").voucher;
  const v4 = ok(await call(adminTok, "POST", "/api/cashier/pay", { party_type: "supplier", party_id: sup.id, amount: 2000, method: "Cash" }), "supplier pay").voucher;
  await tallyBooks("after the 4 vouchers");

  // void all four
  for (const [v, why] of [[v1, "wrong customer"], [v2, "duplicate"], [v3, "wrong amount"], [v4, "wrong supplier"]] as const)
    ok(await call(adminTok, "POST", `/api/cashier/vouchers/${v.id}/void`, { reason: why }), `void ${v.no}`);

  // books still tally, and every balance is back to where it started
  await tallyBooks("after voiding all four");
  const khAfter = ok(await call(adminTok, "GET", "/api/cashier/parties?kind=khata"), "p").khata.find((c: any) => c.id === kh.id).balance;
  const supAfter = ok(await call(adminTok, "GET", "/api/cashier/parties?kind=supplier"), "p").supplier.find((s: any) => s.id === sup.id).balance;
  const bankAfter = ok(await call(adminTok, "GET", "/api/bank/accounts"), "b").accounts.find((a: any) => a.id === acc.id).balance;
  assert.equal(Math.round(khAfter * 100) / 100, Math.round(khBefore * 100) / 100, "khata balance restored");
  assert.equal(Math.round(supAfter * 100) / 100, Math.round(supBefore * 100) / 100, "supplier owed restored");
  assert.equal(Math.round(bankAfter * 100) / 100, Math.round(bankBefore * 100) / 100, "bank balance restored");

  // a voided voucher can't be voided again, and a reason is required
  assert.equal((await call(adminTok, "POST", `/api/cashier/vouchers/${v1.id}/void`, { reason: "again" })).status, 400);
  const v5 = ok(await call(adminTok, "POST", "/api/cashier/receive", { party_type: "other", party_name: "Z", amount: 10, method: "Cash" }), "v5").voucher;
  assert.equal((await call(adminTok, "POST", `/api/cashier/vouchers/${v5.id}/void`, { reason: "x" })).status, 400); // reason too short
});
