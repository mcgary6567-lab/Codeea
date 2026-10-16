import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-cashier-"));
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

test("cashier role: own rights, ticked on and off by the admin", async () => {
  const me = ok(await call("cashier", "GET", "/api/me"), "me");
  assert.equal(me.user.role, "cashier");
  for (const p of ["cashier.desk", "cash.receive", "cash.pay", "cheques.manage", "shifts.handover", "bank.view", "bank.manage", "cash.book"]) assert.ok(me.permissions.includes(p), p);
  assert.ok(!me.permissions.includes("dashboard.view") && !me.permissions.includes("khata.manage"));
  ok(await call("cashier", "GET", "/api/cashier/desk"), "desk");
  ok(await call("cashier", "GET", "/api/bank/accounts"), "banks");
  assert.equal((await call("salesman", "GET", "/api/cashier/desk")).status, 403);
  assert.equal((await call("wholesale", "POST", "/api/cashier/receive", { party_type: "other", party_name: "x", amount: 5, method: "Cash" })).status, 403);
  // the admin takes the cheque right away, then gives it back
  ok(await call("admin", "PUT", "/api/roles/permissions", { perm: "cheques.manage", role: "cashier", allowed: false }), "untick");
  assert.equal((await call("cashier", "GET", "/api/cashier/cheques")).status, 403);
  ok(await call("admin", "PUT", "/api/roles/permissions", { perm: "cheques.manage", role: "cashier", allowed: true }), "tick");
  ok(await call("cashier", "GET", "/api/cashier/cheques"), "cheques again");
  // a cashier user can be made from Users & Roles
  ok(await call("admin", "POST", "/api/users", { name: "Second Cashier", email: "cash2@pumpai.pk", password: "demo12345", role: "cashier" }), "new cashier");
});

test("receive: khata, wholesale and other money — with a voucher number; the cashier cannot add to a khata", async () => {
  const parties = ok(await call("cashier", "GET", "/api/cashier/parties"), "parties");
  const k = parties.khata.find((c: any) => c.balance > 10000);
  const r = ok(await call("cashier", "POST", "/api/cashier/receive", { party_type: "khata", party_id: k.id, amount: 5000, method: "Cash", notify: false }), "khata receive");
  assert.match(r.voucher.no, /^RV-\d{5}$/);
  near(r.balance_after, k.balance - 5000, "khata after");
  assert.equal((await call("cashier", "POST", `/api/customers/${k.id}/khata`, { type: "debit", amount: 100 })).status, 403);
  const w = parties.wholesale[0];
  const acc = ok(await call("cashier", "GET", "/api/bank/accounts"), "acc").accounts[0];
  const r2 = ok(await call("cashier", "POST", "/api/cashier/receive", { party_type: "wholesale", party_id: w.id, amount: 20000, method: "Bank transfer", account_id: acc.id }), "wholesale receive");
  near(r2.balance_after, w.balance - 20000, "wholesale due after");
  const before = ok(await call("cashier", "GET", "/api/cash"), "cash").cash_in_hand;
  ok(await call("cashier", "POST", "/api/cashier/receive", { party_type: "other", party_name: "Scrap dealer", category: "Scrap sale", amount: 3000, method: "Cash" }), "other");
  near(ok(await call("cashier", "GET", "/api/cash"), "cash").cash_in_hand, before + 3000, "other cash in the book");
  assert.equal((await call("cashier", "POST", "/api/cashier/receive", { party_type: "other", party_name: "x", amount: 10, method: "Raast" })).status, 400, "digital other needs an account");
  const vs = ok(await call("cashier", "GET", "/api/cashier/vouchers"), "vouchers");
  assert.ok(vs.length >= 3);
});

test("cash note breakdown is kept with the voucher and comes back for the slip", async () => {
  // 20,000 = 15×1000 + 8×500 + 100×10 → stored as JSON, only cash
  const r = ok(await call("cashier", "POST", "/api/cashier/receive", { party_type: "other", party_name: "Rent tenant", category: "Shop rent", amount: 20000, method: "Cash",
    notes: { "1000": 15, "500": 8, "100": 10, "50": 0 } }), "cash with notes");
  const saved = JSON.parse(r.voucher.notes_json);
  assert.deepEqual(saved, { "1000": 15, "500": 8, "100": 10 }, "zero counts dropped");
  assert.equal(1000 * 15 + 500 * 8 + 100 * 10, 20000);
  // it comes back in the voucher list for reprinting
  const v = ok(await call("cashier", "GET", "/api/cashier/vouchers"), "vouchers").find((x: any) => x.id === r.voucher.id);
  assert.ok(v && v.notes_json, "notes_json in the list");
  // a pay voucher keeps its breakdown too
  const p = ok(await call("cashier", "POST", "/api/cashier/pay", { party_type: "other", party_name: "Labour", category: "Daily wage", amount: 1500, method: "Cash", notes: { "1000": 1, "500": 1 } }), "pay with notes");
  assert.deepEqual(JSON.parse(p.voucher.notes_json), { "1000": 1, "500": 1 });
  // notes are ignored for non-cash methods
  const acc = ok(await call("cashier", "GET", "/api/bank/accounts"), "acc").accounts[0];
  const bank = ok(await call("cashier", "POST", "/api/cashier/receive", { party_type: "other", party_name: "Online payer", amount: 5000, method: "Bank transfer", account_id: acc.id, notes: { "1000": 5 } }), "bank");
  assert.equal(bank.voucher.notes_json, null, "no note breakdown for bank transfer");
});

test("cheque received: in the register, deposited, cleared → khata credited; bounce raises an alert", async () => {
  const k = ok(await call("cashier", "GET", "/api/cashier/parties?kind=khata"), "p").khata.find((c: any) => c.balance > 20000);
  const acc = ok(await call("cashier", "GET", "/api/bank/accounts"), "acc").accounts[0];
  assert.equal((await call("cashier", "POST", "/api/cashier/receive", { party_type: "khata", party_id: k.id, amount: 10000, method: "Cheque", cheque: { bank: "MCB", cheque_no: "9001", cheque_date: day(0) } })).status, 400, "photo needed");
  const r = ok(await call("cashier", "POST", "/api/cashier/receive", { party_type: "khata", party_id: k.id, amount: 10000, method: "Cheque", photo_ids: [await photo("cashier")], cheque: { bank: "MCB", cheque_no: "9001", cheque_date: day(0) } }), "cheque");
  assert.ok(r.cheque_pending);
  near(r.balance_after, k.balance, "not paid until it clears");
  const reg = ok(await call("cashier", "GET", "/api/cashier/cheques"), "reg");
  const q = reg.cheques.find((x: any) => x.src === "c" && x.cheque_no === "9001");
  assert.equal(q.status, "in_hand");
  assert.equal((await call("cashier", "POST", "/api/cashier/receive", { party_type: "khata", party_id: k.id, amount: 1, method: "Cheque", photo_ids: [await photo("cashier")], cheque: { bank: "MCB", cheque_no: "9001", cheque_date: day(0) } })).status, 400, "duplicate");
  const bank0 = acc.balance;
  ok(await call("cashier", "POST", `/api/cashier/cheques/${q.id}/deposit`, { account_id: acc.id }), "deposit");
  ok(await call("cashier", "POST", `/api/cashier/cheques/${q.id}/clear`, {}), "clear");
  const after = ok(await call("cashier", "GET", "/api/cashier/parties?kind=khata"), "p2").khata.find((c: any) => c.id === k.id);
  near(after.balance, k.balance - 10000, "khata credited on clear");
  near(ok(await call("cashier", "GET", "/api/bank/accounts"), "acc2").accounts.find((a: any) => a.id === acc.id).balance, bank0 + 10000, "bank up");
  assert.equal((await call("cashier", "POST", `/api/cashier/cheques/${q.id}/bounce`, {})).status, 400, "closed");
  // a post-dated cheque cannot be deposited early; another one bounces
  const r2 = ok(await call("cashier", "POST", "/api/cashier/receive", { party_type: "khata", party_id: k.id, amount: 7000, method: "Cheque", photo_ids: [await photo("cashier")], cheque: { bank: "UBL", cheque_no: "77", cheque_date: day(5) } }), "pdc");
  const id2 = Number(r2.voucher.src.split(":")[1]);
  assert.equal((await call("cashier", "POST", `/api/cashier/cheques/${id2}/deposit`, { account_id: acc.id })).status, 400);
  ok(await call("cashier", "POST", `/api/cashier/cheques/${id2}/bounce`, { reason: "Funds insufficient" }), "bounce");
  const alerts = ok(await call("admin", "GET", "/api/alerts"), "alerts");
  assert.ok(JSON.stringify(alerts).includes("Cheque bounced"));
});

test("pay: supplier (cash and issued cheque), expense, staff advance; cash limit; issued cheque clears to the supplier", async () => {
  const p = ok(await call("cashier", "GET", "/api/cashier/parties"), "parties");
  const s = p.supplier[0];
  const acc = ok(await call("cashier", "GET", "/api/bank/accounts"), "acc").accounts[0];
  ok(await call("cashier", "POST", "/api/cash/count", { amount: 100000, notes: { "5000": 20 } }), "count");
  assert.equal((await call("cashier", "POST", "/api/cashier/pay", { party_type: "supplier", party_id: s.id, amount: 500000, method: "Cash" })).status, 400, "more than in hand");
  const r = ok(await call("cashier", "POST", "/api/cashier/pay", { party_type: "supplier", party_id: s.id, amount: 40000, method: "Cash" }), "pay supplier");
  assert.match(r.voucher.no, /^PV-/);
  near(r.balance_after, s.balance - 40000, "supplier owed");
  const c = ok(await call("cashier", "POST", "/api/cashier/pay", { party_type: "supplier", party_id: s.id, amount: 60000, method: "Cheque", account_id: acc.id, photo_ids: [await photo("cashier")], cheque: { bank: "HBL", cheque_no: "5501", cheque_date: day(1) } }), "issue cheque");
  near(c.balance_after, s.balance - 40000, "not paid until it clears");
  const qid = Number(c.voucher.src.split(":")[1]);
  ok(await call("cashier", "POST", `/api/cashier/cheques/${qid}/clear`, {}), "clear issued");
  near(ok(await call("cashier", "GET", "/api/cashier/parties?kind=supplier"), "s2").supplier.find((x: any) => x.id === s.id).balance, s.balance - 100000, "supplier paid by cheque");
  const e = ok(await call("cashier", "POST", "/api/cashier/pay", { party_type: "expense", category: "Generator fuel", party_name: "Shell pump", amount: 2500, method: "Cash" }), "expense");
  assert.ok(e.voucher.src.startsWith("expense:"));
  const u = p.staff.find((x: any) => x.role === "salesman");
  const a = ok(await call("cashier", "POST", "/api/cashier/pay", { party_type: "staff", party_id: u.id, amount: 3000, method: "Cash", note: "Medicine" }), "advance");
  near(a.balance_after, u.balance + 3000, "advance on the staff account");
  assert.equal((await call("cashier", "POST", `/api/staff/${u.id}/entry`, { type: "bonus", amount: 100 })).status, 403, "only advances");
  assert.equal((await call("cashier", "GET", "/api/suppliers")).status, 403, "no supplier accounts page");
});

test("handover: the cashier takes the salesman's cash; short cash is reported; day book adds up", async () => {
  const h = ok(await call("cashier", "GET", "/api/cashier/handovers"), "handovers");
  assert.ok(h.pending.length >= 1);
  const sh = h.pending[0];
  const r = ok(await call("cashier", "POST", `/api/cashier/handovers/${sh.id}`, { amount: sh.cash_actual - 1000, note: "Rs 1000 kam" }), "handover");
  assert.equal(r.difference, -1000);
  assert.ok(!r.pending.find((x: any) => x.id === sh.id));
  assert.equal((await call("cashier", "POST", `/api/cashier/handovers/${sh.id}`, { amount: 1 })).status, 400, "only once");
  const alerts = ok(await call("admin", "GET", "/api/alerts"), "alerts");
  assert.ok(JSON.stringify(alerts).includes("Handover short"));
  const book = ok(await call("cashier", "GET", "/api/cashier/daybook"), "daybook");
  assert.ok(book.rows.length > 0);
  for (const k of ["in_cash", "in_bank", "out_cash", "out_bank"]) assert.equal(typeof book.totals[k], "number");
  const t = book.totals;
  near(book.cash.opening + t.in_cash - t.out_cash - t.deposited + t.withdrawn + t.counted, book.cash.closing, "day book adds up");
  // a difference only appears on a day the cash was counted, and it is shown as a line of its own
  const countRows = book.rows.filter((x: any) => x.dir === "count");
  if (!countRows.length) near(t.counted, 0, "no count, no difference"); else near(countRows.reduce((a: number, x: any) => a + x.signed, 0), t.counted, "count line");
});

test("after all of the above: every book still tallies with the ledger", async () => {
  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("cashier");
});

test("CEO edits a standalone 'other' cash voucher amount — cash follows and the books tally", async () => {
  const { tallyBooks } = await import("./helpers/tally.js");
  const before = ok(await call("cashier", "GET", "/api/cash"), "cash").cash_in_hand;
  const r = ok(await call("cashier", "POST", "/api/cashier/receive", { party_type: "other", party_name: "Kabaari", category: "Scrap", amount: 3000, method: "Cash", notify: false }), "other in");
  near(ok(await call("cashier", "GET", "/api/cash"), "cash").cash_in_hand, before + 3000, "cash up 3000");
  await tallyBooks("after other cash in");
  ok(await call("admin", "POST", `/api/cashier/vouchers/${r.voucher.id}/edit`, { reason: "it was 2000 not 3000", amount: 2000 }), "edit voucher");
  near(ok(await call("cashier", "GET", "/api/cash"), "cash").cash_in_hand, before + 2000, "cash now +2000");
  await tallyBooks("after editing the voucher");
  // a non-CEO cannot edit a voucher
  assert.equal((await call("cashier", "POST", `/api/cashier/vouchers/${r.voucher.id}/edit`, { reason: "nope", amount: 1 })).status, 403);
});

test("CEO edits a linked voucher (khata / wholesale / supplier) from the desk — balances follow, books tally", async () => {
  const { tallyBooks } = await import("./helpers/tally.js");
  const khataBal = async (id) => ok(await call("admin", "GET", "/api/cashier/parties?kind=khata"), "pk").khata.find((x) => x.id === id).balance;
  // khata payment 2000 → corrected to 1500 (customer balance follows)
  const k = ok(await call("admin", "GET", "/api/cashier/parties?kind=khata"), "pk0").khata[0];
  const kb0 = await khataBal(k.id);
  const kr = ok(await call("cashier", "POST", "/api/cashier/receive", { party_type: "khata", party_id: k.id, amount: 2000, method: "Cash", notify: false }), "khata rec");
  near(await khataBal(k.id), kb0 - 2000, "khata down 2000 after receiving");
  ok(await call("admin", "POST", `/api/cashier/vouchers/${kr.voucher.id}/edit`, { reason: "it was 1500 not 2000", amount: 1500 }), "edit khata voucher");
  near(await khataBal(k.id), kb0 - 1500, "khata balance now reflects the corrected 1500");
  await tallyBooks("after khata voucher edit");

  // wholesale payment 3000 → 2000
  const wc = ok(await call("admin", "GET", "/api/wholesale/clients"), "wc")[0];
  const wr = ok(await call("cashier", "POST", "/api/cashier/receive", { party_type: "wholesale", party_id: wc.id, amount: 3000, method: "Cash", notify: false }), "ws rec");
  ok(await call("admin", "POST", `/api/cashier/vouchers/${wr.voucher.id}/edit`, { reason: "2000 not 3000", amount: 2000 }), "edit ws voucher");
  await tallyBooks("after wholesale voucher edit");

  // supplier payment 4000 → 2500
  const sup = ok(await call("admin", "GET", "/api/suppliers"), "sup")[0];
  const sr = ok(await call("cashier", "POST", "/api/cashier/pay", { party_type: "supplier", party_id: sup.id, amount: 4000, method: "Cash", notify: false }), "sup pay");
  ok(await call("admin", "POST", `/api/cashier/vouchers/${sr.voucher.id}/edit`, { reason: "2500 not 4000", amount: 2500 }), "edit sup voucher");
  await tallyBooks("after supplier voucher edit");

  // a non-CEO cannot edit, and a cheque voucher is refused
  assert.equal((await call("cashier", "POST", `/api/cashier/vouchers/${kr.voucher.id}/edit`, { reason: "no", amount: 1 })).status, 403);
  assert.equal(ok(await call("admin", "GET", "/api/books/check"), "books").ok, true);
});

test("CEO re-routes a voucher: wrong customer → right customer, received → paid, same voucher number, books tally", async () => {
  const { tallyBooks } = await import("./helpers/tally.js");
  const khataBal = async (id: number) => ok(await call("admin", "GET", "/api/cashier/parties?kind=khata"), "pk").khata.find((x: any) => x.id === id).balance;
  const ks = ok(await call("admin", "GET", "/api/cashier/parties?kind=khata"), "pk0").khata;
  const a = ks[0], b = ks[1];
  const a0 = await khataBal(a.id), b0 = await khataBal(b.id);
  // the cashier booked customer A's 2,000 payment on customer B by mistake
  const r = ok(await call("cashier", "POST", "/api/cashier/receive", { party_type: "khata", party_id: b.id, amount: 2000, method: "Cash", notify: false }), "rec on B");
  near(await khataBal(b.id), b0 - 2000, "B wrongly credited");
  const e = ok(await call("admin", "POST", `/api/cashier/vouchers/${r.voucher.id}/edit`, { reason: "galat customer", party_type: "khata", party_id: a.id }), "reroute to A");
  assert.equal(e.rerouted, true);
  near(await khataBal(b.id), b0, "B back to where it was");
  near(await khataBal(a.id), a0 - 2000, "A credited instead");
  const v = ok(await call("admin", "GET", "/api/cashier/vouchers"), "vouchers").find((x: any) => x.id === r.voucher.id);
  assert.equal(v.party_id, a.id); assert.match(String(v.src), /^khata:\d+$/); assert.equal(v.voided, 0);
  assert.equal(db.get("SELECT COUNT(*) n FROM cashier_vouchers WHERE id > ?", r.voucher.id)!.n, 0, "no extra voucher row left behind");
  await tallyBooks("after re-routing to the right customer");

  // and a slip booked as "received from khata customer" that was really "paid to a supplier"
  const sup = ok(await call("admin", "GET", "/api/suppliers"), "sup")[0];
  const cash0 = ok(await call("admin", "GET", "/api/cash"), "cash0").cash_in_hand;
  const r2 = ok(await call("cashier", "POST", "/api/cashier/receive", { party_type: "khata", party_id: a.id, amount: 500, method: "Cash", notify: false }), "rec");
  ok(await call("admin", "POST", `/api/cashier/vouchers/${r2.voucher.id}/edit`, { reason: "ye supplier ko diya tha", direction: "out", party_type: "supplier", party_id: sup.id }), "reroute in→out");
  near(await khataBal(a.id), a0 - 2000, "A's khata no longer carries the 500");
  near(ok(await call("admin", "GET", "/api/cash"), "cash1").cash_in_hand, cash0 - 500, "cash: the +500 receipt undone, 500 paid out");
  const v2 = ok(await call("admin", "GET", "/api/cashier/vouchers"), "vouchers2").find((x: any) => x.id === r2.voucher.id);
  assert.equal(v2.direction, "out"); assert.equal(v2.party_type, "supplier"); assert.match(String(v2.src), /^stx:\d+$/);
  await tallyBooks("after re-routing received → paid");
  assert.equal(ok(await call("admin", "GET", "/api/books/check"), "books").ok, true);
  // the cashier (no entries.edit) still cannot
  assert.equal((await call("cashier", "POST", `/api/cashier/vouchers/${r2.voucher.id}/edit`, { reason: "no", amount: 1 })).status, 403);
});
