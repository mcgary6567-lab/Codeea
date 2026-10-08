/**
 * Carriage / kiraya (bypass on our depot ID) — run with THEKEDARS, its own module and its own ledger account.
 * Kiraya billed = thekedar's due and our income (whole amount is profit). Fuel money is separate: paid to the depot
 * direct (record only) or routed through us (net-zero pass-through). Every book must still tally with the ledger.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-carriage-"));
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

let S: any, acc = 0, K: any;
const today = () => new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10);
function ledger() {
  return call("admin", "GET", `/api/ledger?from=${today()}&to=${today()}`);
}
const tb = (l: any, a: string) => l.trial_balance.find((x: any) => x.account === a)?.balance ?? 0;
async function bankBal() {
  return ok(await call("admin", "GET", "/api/bank/accounts"), "banks").accounts.find((a: any) => a.id === acc).balance as number;
}

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "wholesale"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
  S = ok(await call("admin", "GET", "/api/suppliers"), "sup")[0];
  acc = ok(await call("admin", "GET", "/api/bank/accounts"), "banks").accounts.find((a: any) => a.active).id;
});
after(() => server?.close());

test("add a thekedar (the carriage party, separate from wholesale clients)", async () => {
  K = ok(await call("wholesale", "POST", "/api/carriage/thekedars", { name: "Test Carriage Contractor", phone: "923009990000", city: "Multan", opening_balance: 0 }), "add thekedar");
  assert.ok(K.id, "thekedar created");
  const list = ok(await call("wholesale", "GET", "/api/carriage/thekedars"), "list");
  assert.ok(list.some((x: any) => x.id === K.id), "thekedar in list");
});

test("bill kiraya (kiraya only): thekedar billed carriage as income, no fuel/stock on our books", async () => {
  const L0 = ok(await ledger(), "l0");
  const r = ok(await call("wholesale", "POST", `/api/carriage/thekedars/${K.id}/carriage`,
    { supplier_id: S.id, invoice_ref: "DEP-9", amount: 10000, lines: [{ product: "HSD", litres: 5000 }] }), "carriage");
  assert.equal(r.kiraya, 10000);
  near(r.due, 10000, "thekedar due = kiraya");
  const L1 = ok(await ledger(), "l1");
  near(tb(L1, "Carriage receivable") - tb(L0, "Carriage receivable"), 10000, "receivable up by kiraya");
  near(tb(L1, "Carriage income") - tb(L0, "Carriage income"), -10000, "income (credit) up by kiraya"); // income is a credit balance (negative)
});

test("fuel money — direct to depot is record only (no bank, no due change)", async () => {
  const bank0 = await bankBal();
  const st0 = ok(await call("wholesale", "GET", `/api/carriage/thekedars/${K.id}`), "st0");
  ok(await call("wholesale", "POST", `/api/carriage/thekedars/${K.id}/fuel-payment`,
    { supplier_id: S.id, mode: "direct", amount: 1_300_000, invoice_ref: "DEP-9" }), "fuel direct");
  near(await bankBal(), bank0, "bank unchanged (direct)");
  const st1 = ok(await call("wholesale", "GET", `/api/carriage/thekedars/${K.id}`), "st1");
  near(st1.closing_balance, st0.closing_balance, "due unchanged by fuel");
  assert.ok(st1.lines.some((x: any) => x.type === "fuel_note" && x.fuel_mode === "direct"), "fuel note on statement");
});

test("fuel money — through us is a net-zero pass-through (held → forwarded), Depot money held nets to zero", async () => {
  const L0 = ok(await ledger(), "l0");
  const held = ok(await call("wholesale", "POST", `/api/carriage/thekedars/${K.id}/fuel-payment`,
    { supplier_id: S.id, mode: "through_us", amount: 800_000, in_method: "Bank transfer", in_account_id: acc }), "fuel in");
  assert.equal(held.status, "held");
  const L1 = ok(await ledger(), "l1");
  near(tb(L1, "Depot money held"), tb(L0, "Depot money held") - 800_000, "held is a liability (credit)");
  // forward it on — liability back to zero
  ok(await call("wholesale", "POST", `/api/carriage/fuel-payments/${held.id}/forward`, { fwd_method: "Bank transfer", fwd_account_id: acc }), "forward");
  const L2 = ok(await ledger(), "l2");
  near(tb(L2, "Depot money held"), tb(L0, "Depot money held"), "held back to zero after forward");
});

test("combined entry (kiraya + fuel in one call), then void it reverses both", async () => {
  const L0 = ok(await ledger(), "l0");
  const bank0 = await bankBal();
  const r = ok(await call("wholesale", "POST", `/api/carriage/thekedars/${K.id}/carriage`,
    { supplier_id: S.id, invoice_ref: "DEP-C1", amount: 12000, lines: [{ product: "HSD", litres: 6000 }],
      fuel: { mode: "through_us", amount: 480_000, in_method: "Bank transfer", in_account_id: acc, forward_now: false } }), "combined");
  assert.equal(r.kiraya, 12000);
  assert.ok(r.fuel_id, "fuel recorded in the same entry");
  // void the carriage txn → kiraya reversed AND the linked held fuel money reversed
  const v = ok(await call("admin", "POST", `/api/carriage/txns/${r.txn.id}/void`, { reason: "wrong invoice" }), "void");
  const L1 = ok(await ledger(), "l1");
  near(tb(L1, "Carriage receivable"), tb(L0, "Carriage receivable"), "receivable back after void");
  near(tb(L1, "Depot money held"), tb(L0, "Depot money held"), "held fuel reversed by void");
  near(await bankBal(), bank0, "bank back after void");
});

test("thekedar pays kiraya: due down, money in", async () => {
  const st0 = ok(await call("wholesale", "GET", `/api/carriage/thekedars/${K.id}`), "st0");
  const p = ok(await call("wholesale", "POST", `/api/carriage/thekedars/${K.id}/payment`,
    { amount: 5000, method: "cash" }), "pay");
  near(p.due, st0.closing_balance - 5000, "due down by payment");
});

test("owner summary: kiraya earned + fuel routed + held", async () => {
  const month = new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 7);
  const sum = ok(await call("admin", "GET", `/api/carriage/summary?month=${month}`), "summary");
  assert.ok(sum.kiraya_total >= 10000, "kiraya earned");
  assert.ok(sum.kiraya_by_thekedar.some((c: any) => c.name === "Test Carriage Contractor"), "kiraya by thekedar");
  assert.ok(sum.fuel_by_depot.length >= 1, "fuel by depot");
});

test("carriage is NOT reachable on the wholesale client route any more (fully moved)", async () => {
  const W = ok(await call("wholesale", "GET", "/api/wholesale/clients"), "clients")[0];
  const r = await call("wholesale", "POST", `/api/wholesale/clients/${W.id}/carriage`,
    { supplier_id: S.id, amount: 1000, lines: [{ product: "HSD", litres: 100 }] });
  assert.equal(r.status, 404, "old wholesale carriage route is gone");
});

test("govt % is deducted from the kiraya: net is billed and booked as income", async () => {
  const L0 = ok(await ledger(), "l0");
  const st0 = ok(await call("wholesale", "GET", `/api/carriage/thekedars/${K.id}`), "st0");
  // kiraya 8000, govt 10% → net 7200 billed
  const r = ok(await call("wholesale", "POST", `/api/carriage/thekedars/${K.id}/carriage`,
    { supplier_id: S.id, invoice_ref: "DEP-GOV", amount: 8000, govt_pct: 10, lines: [{ product: "HSD", litres: 3000 }] }), "carriage govt");
  assert.equal(r.kiraya, 7200, "net kiraya after 10% cut");
  assert.equal(r.gross, 8000, "gross kept");
  assert.equal(r.govt_cut, 800, "govt cut");
  const st1 = ok(await call("wholesale", "GET", `/api/carriage/thekedars/${K.id}`), "st1");
  near(st1.closing_balance - st0.closing_balance, 7200, "due up by the net only");
  const L1 = ok(await ledger(), "l1");
  near(tb(L1, "Carriage income") - tb(L0, "Carriage income"), -7200, "income up by the net only (govt cut is not our income)");
});

test("after all of the above: every book still tallies with the ledger", async () => {
  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("carriage");
});
