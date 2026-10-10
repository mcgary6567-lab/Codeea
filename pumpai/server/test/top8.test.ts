/** Stock register, coupons, wallet, ratings, TV rate board, WhatsApp approvals, commission, monthly expenses & bijli bill. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-top8-"));
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
const wait = (ms = 150) => new Promise((r) => setTimeout(r, ms));
const lastOut = (phone: string) => db.get("SELECT m.body, m.meta FROM messages m JOIN conversations c ON c.id=m.conversation_id JOIN customers cu ON cu.id=c.customer_id WHERE cu.phone=? AND m.direction='out' ORDER BY m.id DESC LIMIT 1", phone);

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
  ok(await call("salesman", "POST", "/api/shifts/open", {}), "open shift");
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("stock register: days chain together, closing = stock now, receipts and dips listed", async () => {
  const to = db.pkDate(), from = db.pkDate(Date.now() - 6 * 86_400_000);
  const r = ok(await call("manager", "GET", `/api/register?station_id=1&from=${from}&to=${to}`), "register");
  assert.equal(r.station.id, 1);
  assert.ok(r.licences.some((l: any) => /xplosive/i.test(l.name)), "explosives licence printed in the header");
  const hsd = r.products.find((p: any) => p.product === "HSD");
  assert.equal(hsd.days.length, 7);
  for (let i = 1; i < hsd.days.length; i++) assert.ok(Math.abs(hsd.days[i].opening - hsd.days[i - 1].closing) < 0.02, "opening = previous closing");
  const stock = db.get("SELECT SUM(current_l) v FROM tanks WHERE station_id=1 AND product='HSD'").v;
  assert.ok(Math.abs(hsd.days[6].closing - stock) < 0.02, "today's closing is the tank stock");
  for (const d of hsd.days) assert.ok(Math.abs(d.book_closing + d.gain_loss - d.closing) < 0.02, "book + gain/loss = closing");
  assert.ok(r.products.some((p: any) => p.days.some((d: any) => d.receipt_lines.length > 0 && d.receipt_lines[0].tanker_no)), "tanker receipts listed");
  assert.ok(hsd.month.sales > 0);
  const m = ok(await call("manager", "GET", `/api/register?station_id=1&month=${to.slice(0, 7)}`), "month");
  assert.equal(m.from, `${to.slice(0, 7)}-01`);
  assert.equal((await call("salesman", "GET", "/api/register")).status, 403);
});

test("coupons: sold in advance, scanned once at the POS, undo gives it back; cash goes in the cash book", async () => {
  const cashBefore = ok(await call("manager", "GET", "/api/cash"), "cash").ins.prepaid_cash;
  const b = ok(await call("manager", "POST", "/api/coupons", { count: 3, value: 1000, product: "PMG", buyer: "Test Traders", method: "cash" }), "sell");
  assert.equal(b.total, 3000);
  assert.equal(ok(await call("manager", "GET", "/api/cash"), "cash").ins.prepaid_cash, cashBefore + 3000);
  const book = ok(await call("manager", "GET", `/api/coupons/batch/${b.batch}`), "batch");
  const code = book.coupons[0].code;
  const look = ok(await call("salesman", "GET", `/api/pos/coupon/PUMPAI-${code}`), "lookup");
  assert.equal(look.value, 1000);
  assert.equal((await call("salesman", "POST", "/api/sales", { station_id: 1, product: "HSD", payment_method: "coupon", coupon_code: code })).status, 400, "petrol-only coupon");
  const sale = ok(await call("salesman", "POST", "/api/sales", { station_id: 1, product: "PMG", payment_method: "coupon", coupon_code: code, amount: 5 }), "sale");
  assert.equal(sale.amount, 1000, "coupon value is the sale amount");
  assert.equal((await call("salesman", "POST", "/api/sales", { station_id: 1, product: "PMG", payment_method: "coupon", coupon_code: code })).status, 400, "used twice");
  assert.match((await call("salesman", "GET", `/api/pos/coupon/${code}`)).data.error, /Already used/);
  ok(await call("salesman", "POST", `/api/sales/${sale.id}/undo`, {}), "undo");
  assert.equal(ok(await call("salesman", "GET", `/api/pos/coupon/${code}`), "again").value, 1000);
  const list = ok(await call("manager", "GET", "/api/coupons"), "list");
  assert.ok(list.summary.outstanding >= 3000);
  ok(await call("admin", "POST", "/api/coupons/void", { codes: [book.coupons[2].code], reason: "Lost by buyer" }), "void");
  assert.match((await call("salesman", "GET", `/api/pos/coupon/${book.coupons[2].code}`)).data.error, /cancelled/);
});

test("wallet: deposit is confirmed on WhatsApp, fills come out of it, low balance warned once, undo refunds", async () => {
  const c = db.get("SELECT * FROM customers WHERE name='Bismillah Goods Transport'");
  const dep = ok(await call("manager", "POST", `/api/customers/${c.id}/wallet/deposit`, { amount: 12000, method: "raast", ref: "RT-1" }), "deposit");
  assert.equal(dep.wallet_balance, 12000);
  assert.match(lastOut(c.phone).body, /12,000.*jama/s);
  ok(await call("manager", "PATCH", `/api/customers/${c.id}/wallet`, { wallet_low: 10000 }), "alert level");
  assert.ok(ok(await call("salesman", "GET", "/api/pos/wallet-accounts"), "accounts").some((a: any) => a.id === c.id));
  const sale = ok(await call("salesman", "POST", "/api/sales", { station_id: 1, product: "HSD", amount: 3000, payment_method: "wallet", customer_id: c.id, vehicle_no: "TLX-1" }), "fill");
  await wait();
  assert.equal(db.get("SELECT wallet_balance v FROM customers WHERE id=?", c.id).v, 9000);
  assert.ok(db.get("SELECT m.id FROM messages m JOIN conversations cv ON cv.id=m.conversation_id WHERE cv.customer_id=? AND m.meta LIKE '%wallet_low%'", c.id), "low balance WhatsApp");
  assert.equal((await call("salesman", "POST", "/api/sales", { station_id: 1, product: "HSD", amount: 9500, payment_method: "wallet", customer_id: c.id })).status, 400, "not enough");
  ok(await call("salesman", "POST", `/api/sales/${sale.id}/undo`, {}), "undo");
  assert.equal(db.get("SELECT wallet_balance v FROM customers WHERE id=?", c.id).v, 12000);
  const w = ok(await call("manager", "GET", "/api/wallets"), "wallets");
  assert.ok(w.wallets.find((x: any) => x.id === c.id));
  const summary = ok(await call("salesman", "GET", "/api/pos/today"), "today");
  assert.ok(summary);
});

test("rating: asked after a fill; a low score asks why, logs a complaint and alerts the manager", async () => {
  const c = db.get("SELECT * FROM customers WHERE name='Shaheen Rickshaw Union'");
  db.run("DELETE FROM ratings WHERE customer_id=?", c.id);
  ok(await call("manager", "POST", `/api/customers/${c.id}/wallet/deposit`, { amount: 5000, method: "cash" }), "deposit");
  ok(await call("salesman", "POST", "/api/sales", { station_id: 1, product: "PMG", amount: 1000, payment_method: "wallet", customer_id: c.id }), "fill");
  await wait();
  assert.match(lastOut(c.phone).body, /1 se 5/);
  const { handleInbound } = await import("../src/ai/agent.js");
  const r1: any = await handleInbound(1, { from: c.phone, text: "2" });
  assert.equal(r1.handled_by, "rating");
  assert.match(r1.reply, /kya masla/i);
  assert.ok(db.get("SELECT id FROM alerts WHERE type='low_rating' AND title LIKE ?", `%${c.name}%`));
  const r2: any = await handleInbound(1, { from: c.phone, text: "Salesman ne change kam diya" });
  assert.match(r2.reply, /Shikayat #\d+/);
  assert.ok(db.get("SELECT id FROM complaints WHERE customer_id=? AND message LIKE '%change kam%'", c.id));
  const s = ok(await call("manager", "GET", "/api/ratings?days=30"), "ratings");
  assert.ok(s.summary.rated > 0);
  assert.ok(s.by_salesman.find((x: any) => x.name === "Imran").low >= 1);
  // a normal message afterwards goes to the AI as usual
  const r3: any = await handleInbound(1, { from: c.phone, text: "petrol ka rate kya hai" });
  assert.notEqual(r3.handled_by, "rating");
});

test("commission on shop sales goes into the salary; weekly leaderboard", async () => {
  const oil = db.get("SELECT * FROM shop_items WHERE station_id=1 AND category='lubricant' AND stock > 2 ORDER BY id LIMIT 1");
  ok(await call("salesman", "POST", "/api/shop/sales", { station_id: 1, payment_method: "cash", lines: [{ item_id: oil.id, qty: 2 }] }), "shop sale");
  const month = db.pkDate().slice(0, 7);
  const c = ok(await call("manager", "GET", `/api/commission?month=${month}`), "commission");
  const imran = c.salesmen.find((x: any) => x.name === "Imran");
  assert.ok(imran.shop_commission >= Math.round(2 * oil.price * 0.03 * 100) / 100 - 0.01);
  ok(await call("admin", "PUT", "/api/commission/settings", { shop: { lubricant: 5 }, per_litre: 0.1 }), "rates");
  const c2 = ok(await call("manager", "GET", `/api/commission?month=${month}`), "commission 2").salesmen.find((x: any) => x.name === "Imran");
  assert.ok(c2.fuel_commission > 0 && c2.total > imran.total);
  const lb = ok(await call("salesman", "GET", "/api/leaderboard"), "leaderboard");
  assert.equal(lb.rows[0].rank, 1);
  const { runJob } = await import("../src/automation/scheduler.js");
  assert.doesNotMatch(await runJob(1, "leaderboard"), /^Error/);
  const u = db.get("SELECT id FROM users WHERE email='salesman@pumpai.pk'");
  db.run("DELETE FROM staff_ledger WHERE user_id=? AND type='salary' AND month=?", u.id, month);
  const paid = ok(await call("manager", "POST", `/api/staff/${u.id}/pay-salary`, { photo_ids: await salaryProof(),  month }), "pay");
  assert.equal(paid.commission, c2.total);
  assert.ok(db.get("SELECT id FROM staff_ledger WHERE user_id=? AND type='bonus' AND note=?", u.id, `Commission ${month}`));
});

test("monthly fixed expenses book themselves once; a higher bijli bill raises an alert", async () => {
  const month = db.pkDate().slice(0, 7);
  db.run("UPDATE recurring_expenses SET active=0");
  const r = ok(await call("admin", "POST", "/api/recurring-expenses", { category: "Rent", amount: 77777, paid_to: "Test landlord", day_of_month: 1 }), "add");
  db.run("UPDATE recurring_expenses SET last_month=NULL WHERE id=?", r.id);
  const { bookRecurring } = await import("../src/routes/recurring.js");
  assert.equal(await bookRecurring(1, `${month}-02`), 1);
  assert.equal(await bookRecurring(1, `${month}-03`), 0, "only once a month");
  assert.ok(db.get("SELECT id FROM expenses WHERE amount=77777 AND status='approved' AND expense_date=?", `${month}-01`));
  assert.equal((await call("manager", "POST", "/api/recurring-expenses", { category: "Rent", amount: 1 })).status, 403, "admin sets them up");
  // bijli bill: AI is off, so the amounts are typed; this month is much higher than last
  const bill = ok(await call("manager", "POST", "/api/utility-bills", { kind: "electricity", station_id: 1, month, units: 3900, amount: 236000, reference: "24 11215 0412300" }), "bill");
  assert.equal(bill.high, true);
  assert.ok(bill.change_pct > 30);
  assert.ok(db.get("SELECT id FROM alerts WHERE type='utility_high'"));
  assert.ok(db.get("SELECT id FROM outbox WHERE kind='utility_high'"), "owner told on WhatsApp");
  assert.ok(db.get("SELECT id FROM expenses WHERE id=? AND category='Electricity (bijli)'", bill.expense_id));
  assert.equal((await call("manager", "POST", "/api/utility-bills", { kind: "electricity", station_id: 1, month, amount: 1000 })).status, 400, "same month twice");
});

test("TV rate board: public signed page with prices and offers, follows a price change", async () => {
  const link = ok(await call("manager", "GET", "/api/board-link?station_id=1"), "link");
  const token = link.url.split("/board/")[1];
  ok(await call("manager", "PUT", "/api/board/settings", { offers: "Free tyre air\nTea on the house" }), "offers");
  const html = await (await fetch(`${base}/board/${token}`)).text();
  assert.match(html, /پیٹرول/);
  assert.match(html, /Free tyre air/);
  const d = await (await fetch(`${base}/board/${token}/data`)).json() as any;
  assert.deepEqual(d.offers, ["Free tyre air", "Tea on the house"]);
  assert.equal((await fetch(`${base}/board/not-a-token`)).status, 404);
  assert.equal((await fetch(`${base}/api/me`, { headers: { authorization: `Bearer ${token}` } })).status, 401, "board link is not a login");
});

test("WhatsApp approvals: owner replies 1 to a manager's price change and 2 to a big expense", async () => {
  db.setSetting(1, "price_approval", "1");
  const old = db.get("SELECT price FROM prices WHERE product='PMG' ORDER BY effective_from DESC, id DESC LIMIT 1").price;
  const req = ok(await call("manager", "POST", "/api/prices", { prices: { PMG: old + 1.5 } }), "request");
  assert.equal(req.pending, true);
  const msg = db.get("SELECT * FROM outbox WHERE kind='approval_request' ORDER BY id DESC LIMIT 1");
  assert.match(msg.text, /Reply \*1\* to approve/);
  const owner = db.get("SELECT owner_phone FROM tenants WHERE id=1").owner_phone;
  const exp = ok(await call("manager", "POST", "/api/expenses", { category: "Maintenance & repairs", amount: 95000, paid_to: "Dispenser mechanic" }), "expense");
  assert.equal(exp.status, "pending");
  const { handleInbound } = await import("../src/ai/agent.js");
  // a manager cannot approve on WhatsApp
  db.run("UPDATE users SET phone='923009998888' WHERE email='manager@pumpai.pk'");
  const m: any = await handleInbound(1, { from: "923009998888", text: "1" });
  assert.match(m.reply, /sirf owner/);
  const appr = ok(await call("manager", "GET", "/api/approvals"), "approvals");
  const priceAppr = appr.pending.find((a: any) => a.kind === "price");
  const expAppr = appr.pending.find((a: any) => a.kind === "expense");
  const r1: any = await handleInbound(1, { from: owner, text: `1 ${priceAppr.id}` });
  assert.equal(r1.handled_by, "approval");
  assert.match(r1.reply, /Approved/);
  assert.equal(db.get("SELECT price FROM prices WHERE product='PMG' ORDER BY effective_from DESC, id DESC LIMIT 1").price, old + 1.5);
  const r2: any = await handleInbound(1, { from: owner, text: "2" });
  assert.match(r2.reply, /Rejected/);
  assert.equal(db.get("SELECT status FROM expenses WHERE id=?", exp.id).status, "rejected");
  assert.equal(db.get("SELECT decided_via FROM approvals WHERE id=?", expAppr.id).decided_via, "whatsapp");
  const r3: any = await handleInbound(1, { from: owner, text: "1" });
  assert.match(r3.reply, /Koi approval baqi nahi/);
  // decided in the app: WhatsApp no longer acts on it
  const req2 = ok(await call("manager", "POST", "/api/prices", { prices: { PMG: old + 3 } }), "request 2");
  ok(await call("admin", "POST", `/api/price-requests/${req2.request_id}/reject`, {}), "reject in app");
  assert.equal(db.get("SELECT status FROM approvals WHERE kind='price' AND ref_id=?", req2.request_id).status, "rejected");
  // other owner messages still go to the business assistant
  const r4: any = await handleInbound(1, { from: owner, text: "aaj ki sale" });
  assert.equal(r4.handled_by, "owner_assistant");
});

/** Salary needs a photo of the signed salary sheet. */
async function salaryProof() {
  const r = await call("manager", "POST", "/api/ai/read-photo", { kind: "proof", image: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==" });
  return [r.data.photo_id as number];
}
