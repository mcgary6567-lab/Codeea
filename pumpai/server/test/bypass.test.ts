/**
 * Bypass delivery: buy fuel from suppliers at the depot, deliver straight to wholesale clients.
 * The supplier's bypass account is separate from the pump-stock payable; clients are billed in their own khata;
 * we can't deliver more than we bought; we pay the supplier. Every book must still tally with the ledger.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-bypass-"));
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
const today = () => new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10);
function ledger() { return call("admin", "GET", `/api/ledger?from=${today()}&to=${today()}`); }
const tb = (l: any, a: string) => l.trial_balance.find((x: any) => x.account === a)?.balance ?? 0;

let S: any, S2: any, W1: any, W2: any, st = 0, acc = 0;
const clientDueOf = async (id: number) => ok(await call("wholesale", "GET", "/api/wholesale/clients"), "clients").find((c: any) => c.id === id).due as number;
const supOwed = async (id: number) => ok(await call("admin", "GET", "/api/suppliers"), "sup").find((s: any) => s.id === id).owed as number;

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "wholesale", "manager"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
  const sups = ok(await call("admin", "GET", "/api/suppliers"), "sup");
  S = sups[0]; S2 = sups[1];
  [W1, W2] = ok(await call("wholesale", "GET", "/api/wholesale/clients"), "clients");
  st = ok(await call("admin", "GET", "/api/stations"), "stations")[0].id;
  acc = ok(await call("admin", "GET", "/api/bank/accounts"), "banks").accounts.find((a: any) => a.active).id;
});
after(() => { server?.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("bypass suppliers list carries a separate bypass balance", async () => {
  const list = ok(await call("wholesale", "GET", "/api/bypass/suppliers"), "bypass suppliers");
  assert.ok(list.find((s: any) => s.id === S.id), "supplier listed");
  assert.equal(list.find((s: any) => s.id === S.id).bypass_owed, 0, "starts at zero");
});

test("can't deliver more litres than purchased", async () => {
  const bad = await call("wholesale", "POST", "/api/bypass/deliveries", { station_id: st,
    purchases: [{ supplier_id: S.id, product: "HSD", litres: 1000, cost_rate: 255 }],
    drops: [{ client_id: W1.id, product: "HSD", litres: 1500, override_limit: true }] });
  assert.equal(bad.status, 400, "refused");
  assert.match(bad.data.error, /cannot deliver more than you bought|purchased/i);
});

let DEL: any;
test("bypass delivery: multi-supplier purchase, clients billed, supplier bypass account up, no stock/pump-payable move", async () => {
  const L0 = ok(await ledger(), "l0");
  const tanks0 = db.get("SELECT COALESCE(SUM(current_l),0) l FROM tanks WHERE station_id=?", st)!.l as number;
  const pumpOwed0 = await supOwed(S.id);
  const due1_0 = await clientDueOf(W1.id), due2_0 = await clientDueOf(W2.id);
  // buy 2000 L HSD from S @255 and 1000 L HSD from S2 @256; drop 2000 to W1 and 800 to W2
  DEL = ok(await call("wholesale", "POST", "/api/bypass/deliveries", { station_id: st,
    purchases: [{ supplier_id: S.id, product: "HSD", litres: 2000, cost_rate: 255, ref: "BILTY-1" }, { supplier_id: S2.id, product: "HSD", litres: 1000, cost_rate: 256, ref: "BILTY-2" }],
    drops: [{ client_id: W1.id, product: "HSD", litres: 2000, override_limit: true }, { client_id: W2.id, product: "HSD", litres: 800, override_limit: true }] }), "delivery");
  const cost = 2000 * 255 + 1000 * 256;
  const billed = DEL.billed;
  const w1Amt = DEL.drops.find((d: any) => d.client_id === W1.id).amount;
  const w2Amt = DEL.drops.find((d: any) => d.client_id === W2.id).amount;
  near(DEL.cost, cost, "purchase cost"); near(DEL.margin, billed - cost, "margin");
  // our tanks and the pump-stock supplier payable did not move
  near(db.get("SELECT COALESCE(SUM(current_l),0) l FROM tanks WHERE station_id=?", st)!.l as number, tanks0, "no stock moved");
  near(await supOwed(S.id), pumpOwed0, "pump-stock payable unchanged (separate account)");
  // clients owe their drops
  near(await clientDueOf(W1.id) - due1_0, w1Amt, "W1 billed");
  near(await clientDueOf(W2.id) - due2_0, w2Amt, "W2 billed");
  // supplier bypass accounts
  const bl = ok(await call("wholesale", "GET", "/api/bypass/suppliers"), "bl");
  near(bl.find((s: any) => s.id === S.id).bypass_owed, 2000 * 255, "S bypass owed");
  near(bl.find((s: any) => s.id === S2.id).bypass_owed, 1000 * 256, "S2 bypass owed");
  const L1 = ok(await ledger(), "l1");
  near(tb(L1, "Bypass suppliers payable") - tb(L0, "Bypass suppliers payable"), -cost, "ledger bypass payable (liability)");
  near(tb(L1, "Bypass fuel cost") - tb(L0, "Bypass fuel cost"), cost, "ledger bypass cost");
  near(tb(L1, "Wholesale receivable") - tb(L0, "Wholesale receivable"), billed, "ledger receivable up by drops");
});

test("bypass delivery adds its cost to profit (direct cost, not stock purchases)", async () => {
  const dayStart = new Date(Date.parse(`${today()}T00:00:00+05:00`)).toISOString();
  const rep = ok(await call("admin", "GET", `/api/reports?from=${encodeURIComponent(dayStart)}&to=${encodeURIComponent(new Date().toISOString())}`), "report");
  assert.ok(rep.stock.direct.cost >= 2000 * 255 + 1000 * 256, "bypass cost in direct cost");
});

test("we pay the bypass supplier from the bank: bypass account down, bank down", async () => {
  const L0 = ok(await ledger(), "l0");
  const bankOf = async () => ok(await call("admin", "GET", "/api/bank/accounts"), "banks").accounts.find((a: any) => a.id === acc).balance as number;
  const bank0 = await bankOf();
  const r = ok(await call("wholesale", "POST", `/api/bypass/suppliers/${S.id}/payment`, { amount: 300000, mode: "we_pay", method: "Bank transfer", account_id: acc, ref: "PO-1" }), "pay");
  near(r.bypass_owed, 2000 * 255 - 300000, "bypass owed down");
  near(await bankOf(), bank0 - 300000, "bank down");
  const L1 = ok(await ledger(), "l1");
  near(tb(L1, "Bypass suppliers payable") - tb(L0, "Bypass suppliers payable"), 300000, "payable reduced");
});

test("client pays the bypass supplier direct: client's due and our bypass payable both down, no money through us", async () => {
  const L0 = ok(await ledger(), "l0");
  const bankOf = async () => ok(await call("admin", "GET", "/api/bank/accounts"), "banks").accounts.find((a: any) => a.id === acc).balance as number;
  const bank0 = await bankOf();
  const owed0 = ok(await call("wholesale", "GET", "/api/bypass/suppliers"), "bl").find((s: any) => s.id === S2.id).bypass_owed;
  const due0 = await clientDueOf(W1.id);
  const r = ok(await call("wholesale", "POST", `/api/bypass/suppliers/${S2.id}/payment`, { amount: 50000, mode: "client_direct", client_id: W1.id, ref: "slip-9" }), "client direct");
  near(r.bypass_owed, owed0 - 50000, "bypass owed down");
  near(r.client_due, due0 - 50000, "client due down");
  near(await bankOf(), bank0, "no money through our bank");
  const L1 = ok(await ledger(), "l1");
  near(tb(L1, "Bypass suppliers payable") - tb(L0, "Bypass suppliers payable"), 50000, "payable reduced");
  near(tb(L1, "Wholesale receivable") - tb(L0, "Wholesale receivable"), -50000, "receivable reduced");
});

test("through us: client sends money to us, we forward to the supplier (net-zero through our bank)", async () => {
  const L0 = ok(await ledger(), "l0");
  const bankOf = async () => ok(await call("admin", "GET", "/api/bank/accounts"), "banks").accounts.find((a: any) => a.id === acc).balance as number;
  const bank0 = await bankOf();
  const owed0 = ok(await call("wholesale", "GET", "/api/bypass/suppliers"), "bl").find((s: any) => s.id === S2.id).bypass_owed;
  const due0 = await clientDueOf(W1.id);
  const r = ok(await call("wholesale", "POST", `/api/bypass/suppliers/${S2.id}/payment`, { amount: 40000, mode: "through_us", client_id: W1.id, method: "Bank transfer", account_id: acc, ref: "thru-1" }), "through us");
  near(r.bypass_owed, owed0 - 40000, "bypass owed down");
  near(r.client_due, due0 - 40000, "client due down");
  near(await bankOf(), bank0, "bank net zero (in then out)");
  const L1 = ok(await ledger(), "l1");
  near(tb(L1, "Bypass suppliers payable") - tb(L0, "Bypass suppliers payable"), 40000, "payable reduced");
  near(tb(L1, "Wholesale receivable") - tb(L0, "Wholesale receivable"), -40000, "receivable reduced");
});

test("bypass supplier statement (separate from the pump-stock statement)", async () => {
  const stmt = ok(await call("wholesale", "GET", `/api/bypass/suppliers/${S.id}/statement`), "statement");
  assert.ok(stmt.lines.some((l: any) => l.kind === "purchase"), "has a purchase line");
  assert.ok(stmt.lines.some((l: any) => l.kind === "payment"), "has a payment line");
  near(stmt.closing_balance, 2000 * 255 - 300000, "closing balance");
  const csv = await call("admin", "GET", `/api/bypass/suppliers/${S.id}/statement.csv`);
  assert.equal(csv.status, 200);
});

test("after all of the above: every book still tallies with the ledger", async () => {
  const { tallyBooks } = await import("./helpers/tally.js");
  await tallyBooks("bypass");
});
