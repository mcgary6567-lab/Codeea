/** Quick PIN sign-in on the pump tablet, 2-minute undo, and offline POS sales synced once. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-easy-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server;
let base = "";
const tokens: Record<string, string> = {};
async function call(who: string, method: string, url: string, body?: unknown, headers: Record<string, string> = {}) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tokens[who] ? { authorization: `Bearer ${tokens[who]}` } : {}), ...headers }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, msg: string) => { assert.equal(r.status, 200, `${msg}: ${JSON.stringify(r.data)}`); return r.data; };
const near = (a: number, b: number, msg?: string) => assert.ok(Math.abs(a - b) < 0.05, `${msg ?? ""} ${a} ≈ ${b}`);

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

let device = "";
test("PIN login: the tablet is linked by one email sign-in, then staff tap their name and PIN", async () => {
  assert.equal((await call("", "GET", "/api/auth/pin-users")).status, 401, "unlinked device sees nobody");
  const first = ok(await call("", "POST", "/api/auth/login", { email: "manager@pumpai.pk", password: "demo1234" }), "email login");
  device = first.device_token;
  assert.ok(device);
  assert.equal((await call("", "GET", "/api/me", undefined, { authorization: `Bearer ${device}` })).status, 401, "device token is not a login");
  const people = ok(await call("", "GET", "/api/auth/pin-users", undefined, { "x-device": device }), "pin users");
  const imran = people.find((p: any) => p.name === "Imran");
  assert.equal(imran.role, "salesman");
  assert.ok(!("email" in imran) && !("pin_hash" in imran), "no private fields");
  const r = ok(await call("", "POST", "/api/auth/pin", { user_id: imran.id, pin: "3333" }, { "x-device": device }), "pin login");
  tokens.salesman = r.token;
  assert.equal(ok(await call("salesman", "GET", "/api/me"), "me").user.name, "Imran");
  tokens.admin = ok(await call("", "POST", "/api/auth/pin", { user_id: people.find((p: any) => p.role === "admin").id, pin: "1111" }, { "x-device": device }), "admin pin").token;
  tokens.manager = first.token;
});

test("PIN: wrong PINs lock the account for a while; admin can change or remove a PIN", async () => {
  const wh = ok(await call("", "GET", "/api/auth/pin-users", undefined, { "x-device": device }), "users").find((p: any) => p.role === "wholesale");
  for (let i = 1; i <= 4; i++) {
    const r = await call("", "POST", "/api/auth/pin", { user_id: wh.id, pin: "0000" }, { "x-device": device });
    assert.equal(r.status, 401); assert.match(r.data.error, new RegExp(`${5 - i} tries left`));
  }
  assert.match((await call("", "POST", "/api/auth/pin", { user_id: wh.id, pin: "0000" }, { "x-device": device })).data.error, /Locked/);
  assert.equal((await call("", "POST", "/api/auth/pin", { user_id: wh.id, pin: "4444" }, { "x-device": device })).status, 429, "right PIN still locked");
  ok(await call("admin", "PATCH", `/api/users/${wh.id}`, { pin: "5678" }), "admin resets PIN");
  ok(await call("", "POST", "/api/auth/pin", { user_id: wh.id, pin: "5678" }, { "x-device": device }), "new PIN works");
  assert.equal((await call("admin", "PATCH", `/api/users/${wh.id}`, { pin: "12a4" })).status, 400);
  ok(await call("admin", "PATCH", `/api/users/${wh.id}`, { pin: null }), "remove");
  const list = ok(await call("admin", "GET", "/api/users"), "users").users;
  assert.equal(list.find((u: any) => u.id === wh.id).has_pin, 0);
  assert.ok(!ok(await call("", "GET", "/api/auth/pin-users", undefined, { "x-device": device }), "users").some((p: any) => p.id === wh.id));
});

let shift: any;
const tankL = async (p: string) => ok(await call("admin", "GET", "/api/stations"), "stations")[0].tanks.filter((t: any) => t.product === p).reduce((a: number, t: any) => a + t.current_l, 0);

test("undo: a wrong khata sale is fully reversed — stock, balance and ledger", async () => {
  shift = ok(await call("salesman", "POST", "/api/shifts/open", {}), "open");
  const acct = ok(await call("salesman", "GET", "/api/pos/khata-accounts"), "accts").find((a: any) => a.status === "ok");
  const bal0 = ok(await call("admin", "GET", `/api/customers/${acct.id}`), "cust").balance;
  const tank0 = await tankL("HSD");
  const sale = ok(await call("salesman", "POST", "/api/sales", { station_id: 1, product: "HSD", litres: 25, payment_method: "khata", customer_id: acct.id, slip_no: "U-1" }), "sale");
  near(await tankL("HSD"), tank0 - 25);
  const today = ok(await call("salesman", "GET", "/api/pos/today"), "today");
  assert.equal(today.recent[0].created_by, sale.created_by); assert.equal(today.undo_seconds, 120);
  const u = ok(await call("salesman", "POST", `/api/sales/${sale.id}/undo`, {}), "undo");
  assert.equal(u.summary.khata, 0);
  near(await tankL("HSD"), tank0, "stock back");
  near(ok(await call("admin", "GET", `/api/customers/${acct.id}`), "cust").balance, bal0, "balance back");
  const st = ok(await call("admin", "GET", `/api/customers/${acct.id}/statement`), "statement");
  assert.ok(!JSON.stringify(st).includes(`SALE-${sale.id}"`), "ledger line removed");
  assert.equal((await call("salesman", "POST", `/api/sales/${sale.id}/undo`, {})).status, 404, "only once");
});

test("undo: salesman only own sale within 2 minutes; manager can while the shift is open", async () => {
  const mine = ok(await call("salesman", "POST", "/api/sales", { station_id: 1, product: "PMG", amount: 1000, payment_method: "cash" }), "sale");
  const byMgr = ok(await call("manager", "POST", "/api/sales", { station_id: 1, product: "PMG", amount: 500, payment_method: "cash" }), "mgr sale");
  assert.equal((await call("salesman", "POST", `/api/sales/${byMgr.id}/undo`, {})).status, 403);
  // age the salesman's sale past the window
  const { run } = await import("../src/db.js");
  run("UPDATE sales SET created_at=? WHERE id=?", new Date(Date.now() - 3 * 60_000).toISOString(), mine.id);
  const late = await call("salesman", "POST", `/api/sales/${mine.id}/undo`, {});
  assert.equal(late.status, 400); assert.match(late.data.error, /2 minutes/);
  ok(await call("manager", "POST", `/api/sales/${mine.id}/undo`, {}), "manager undo");
  const log = (await import("../src/db.js")).all("SELECT * FROM audit_log WHERE action='sale_undo'");
  assert.equal(log.length, 2);
});

test("offline: a queued sale synced twice is saved once, at the price of the time it was made", async () => {
  const before = ok(await call("salesman", "GET", "/api/pos/today"), "today");
  const oldRate = before.prices.PMG;
  const at = new Date(Date.parse(shift.opened_at) + 1).toISOString(); // during the shift, before the price change below
  // price goes up after the sale was made offline
  ok(await call("admin", "POST", "/api/prices", { prices: { PMG: oldRate + 5 } }), "price up");
  const body = { station_id: 1, product: "PMG", litres: 10, payment_method: "cash", client_uid: "tablet-abc-12345", offline_at: at };
  const a = ok(await call("salesman", "POST", "/api/sales", body), "sync 1");
  near(a.rate, oldRate, "billed at the old price");
  assert.equal(a.created_at, at);
  assert.equal(a.shift_id, shift.id);
  const b = ok(await call("salesman", "POST", "/api/sales", body), "sync 2");
  assert.equal(b.id, a.id); assert.equal(b.duplicate, true);
  const n = (await import("../src/db.js")).get("SELECT COUNT(*) n FROM sales WHERE client_uid='tablet-abc-12345'")!.n;
  assert.equal(n, 1);
  assert.equal((await call("salesman", "POST", "/api/sales", { ...body, client_uid: "tablet-old-99999", offline_at: new Date(Date.now() - 50 * 3600_000).toISOString() })).status, 400, "too old");
});
