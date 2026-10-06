/**
 * Stock in and stock out against the money: a supplier's tanker (short by 50 L) → stock, supplier bill, shortage claim;
 * a wholesale tanker trip with two drops, one voided → stock, client dues, trip sheet; claim settled by credit note;
 * supplier paid from the bank; a dip. Tanks, stock register, stock report, supplier balance, client dues, bank and the
 * ledger must all move together.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-stockledger-"));
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

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "wholesale"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => server?.close());

let st = 0, tank: any, S: any, W1: any, W2: any, acc = 0, invoice = 0, received = 0, rate = 250;
const dayStart = () => new Date(Date.parse(`${today()}T00:00:00+05:00`)).toISOString();
async function books() {
  const l = ok(await call("admin", "GET", `/api/ledger?from=${today()}&to=${today()}`), "ledger");
  const reg = ok(await call("manager", "GET", `/api/register?station_id=${st}&from=${today()}&to=${today()}`), "register").products.find((p: any) => p.product === "HSD");
  const rep = ok(await call("admin", "GET", `/api/reports?from=${encodeURIComponent(dayStart())}&to=${encodeURIComponent(new Date().toISOString())}`), "report").stock.products.find((p: any) => p.product === "HSD");
  const clients = ok(await call("wholesale", "GET", "/api/wholesale/clients"), "clients");
  return {
    tank: db.get("SELECT current_l FROM tanks WHERE id=?", tank.id).current_l as number,
    hsd: db.get("SELECT SUM(t.current_l) l FROM tanks t WHERE t.station_id=? AND t.product='HSD'", st).l as number,
    owed: ok(await call("admin", "GET", "/api/suppliers"), "sup").find((s: any) => s.id === S.id).owed as number,
    due1: clients.find((c: any) => c.id === W1.id).due as number, due2: clients.find((c: any) => c.id === W2.id).due as number,
    bank: ok(await call("admin", "GET", "/api/bank/accounts"), "banks").accounts.find((a: any) => a.id === acc).balance as number,
    reg: reg.days[reg.days.length - 1], rep,
    tb: (a: string) => l.trial_balance.find((x: any) => x.account === a)?.balance ?? 0, l,
  };
}
let B0: Awaited<ReturnType<typeof books>>;

test("setup", async () => {
  // the diesel tank with the most room (how full the demo tanks are depends on the time of day the seed ran)
  const ids = ok(await call("admin", "GET", "/api/stations"), "stations").map((s: any) => s.id);
  tank = db.get(`SELECT * FROM tanks WHERE station_id IN (${ids.join(",")}) AND product='HSD' ORDER BY capacity_l - current_l DESC LIMIT 1`);
  st = tank.station_id;
  invoice = Math.min(10000, Math.floor(tank.capacity_l - tank.current_l) - 100);
  assert.ok(invoice > 6000, `room in the tank for a tanker: ${JSON.stringify(tank)}`);
  received = invoice - 50;
  S = ok(await call("admin", "GET", "/api/suppliers"), "sup")[0];
  [W1, W2] = ok(await call("wholesale", "GET", "/api/wholesale/clients"), "clients");
  acc = ok(await call("admin", "GET", "/api/bank/accounts"), "banks").accounts.find((a: any) => a.active).id;
  B0 = await books();
});

let claimId = 0, trip: any;
test("a tanker cannot be received without its supplier and purchase rate (its cost would be missing from the books)", async () => {
  const before = db.get("SELECT current_l FROM tanks WHERE id=?", tank.id).current_l;
  const noSup = await call("manager", "POST", "/api/stock/delivery", { tank_id: tank.id, invoice_l: 1000, received_l: 1000, purchase_rate: rate });
  assert.equal(noSup.status, 400); assert.match(noSup.data.error, /supplier/i);
  const noRate = await call("manager", "POST", "/api/stock/delivery", { tank_id: tank.id, invoice_l: 1000, received_l: 1000, supplier_id: S.id });
  assert.equal(noRate.status, 400); assert.match(noRate.data.error, /rate/i);
  assert.equal((await call("manager", "POST", "/api/stock/delivery", { tank_id: tank.id, invoice_l: 1000, received_l: 1000, supplier_id: 999999, purchase_rate: rate })).status, 400, "unknown supplier");
  assert.equal(db.get("SELECT current_l FROM tanks WHERE id=?", tank.id).current_l, before, "nothing went into the tank");
});

test("supplier's tanker 50 L short: stock by what came, bill by the invoice, a claim for the shortage", async () => {
  const d = ok(await call("manager", "POST", "/api/stock/delivery", { tank_id: tank.id, invoice_l: invoice, received_l: received, supplier_id: S.id, purchase_rate: rate, tanker_no: "SL-1" }), "delivery");
  claimId = d.claim_id;
  assert.ok(claimId, "shortage claim opened");
  const claim = db.get("SELECT * FROM shortage_claims WHERE id=?", claimId);
  const B = await books();
  near(B.tank - B0.tank, received, "tank up by what came in");
  near(B.owed - B0.owed, invoice * rate, "supplier owed for the invoice");
  near(B.tb("Fuel purchases") - B0.tb("Fuel purchases"), invoice * rate, "ledger purchase");
  near(B.tb(`Payable — ${S.name}`) - B0.tb(`Payable — ${S.name}`), -(invoice * rate), "ledger payable = supplier balance");
  near(B.reg.receipts - B0.reg.receipts, received, "stock register receipt");
  near(B.rep.received_l - B0.rep.received_l, received, "stock report receipt");
  near(claim.amount, claim.litres * rate, "claim at the purchase rate");
  assert.ok(claim.litres > 0 && claim.litres <= 50, "claim for the shortage above the allowed loss");
});

test("wholesale tanker trip with two drops, one voided: stock, dues, trip sheet, register and ledger", async () => {
  trip = ok(await call("wholesale", "POST", "/api/wholesale/trips", { station_id: st, product: "HSD", vehicle_no: "TLR-SL",
    drops: [{ client_id: W1.id, litres: 3000, override_limit: true }, { client_id: W2.id, litres: 2000, override_limit: true }] }), "trip");
  const [d1, d2] = trip.drops;
  near(trip.delivered_l, 5000); near(trip.billed, d1.amount + d2.amount, "trip billed = drops");
  let B = await books();
  near(B.hsd - B0.hsd, received - 5000, "diesel: tanker in, trip out");
  near(B.due1 - B0.due1, d1.amount); near(B.due2 - B0.due2, d2.amount);
  near(B.tb("Wholesale receivable") - B0.tb("Wholesale receivable"), d1.amount + d2.amount, "ledger receivable = dues");
  near(B.tb("Wholesale sales") - B0.tb("Wholesale sales"), -(d1.amount + d2.amount), "ledger wholesale sales");
  near(B.reg.wholesale - B0.reg.wholesale, 5000, "register wholesale out");
  // the second drop did not happen: void it — stock comes back, the client owes nothing for it
  ok(await call("admin", "POST", `/api/wholesale/txns/${d2.id}/void`, { reason: "not delivered" }), "void");
  B = await books();
  near(B.hsd - B0.hsd, received - 3000, "voided drop back in the tank");
  near(B.due2, B0.due2, "client 2 owes nothing for it");
  near(B.tb("Wholesale receivable") - B0.tb("Wholesale receivable"), d1.amount, "ledger drops the voided drop");
  near(B.reg.wholesale - B0.reg.wholesale, 3000, "register: only the real drop");
  near(B.rep.wholesale_out_l - B0.rep.wholesale_out_l, 3000, "report: only the real drop");
  const sheet = ok(await call("wholesale", "GET", `/api/wholesale/trips/${trip.id}`), "sheet");
  near(sheet.delivered_l, 3000); near(sheet.billed, d1.amount);
});

test("one tanker carrying diesel and petrol: each drop comes out of its own fuel's tank, billed at that fuel's rate", async () => {
  const pmg = () => db.get("SELECT SUM(current_l) l FROM tanks WHERE station_id=? AND product='PMG'", st).l as number;
  const hsd = () => db.get("SELECT SUM(current_l) l FROM tanks WHERE station_id=? AND product='HSD'", st).l as number;
  const B = await books(); const p0 = pmg(), h0 = hsd();
  const mix = ok(await call("wholesale", "POST", "/api/wholesale/trips", { station_id: st, product: "HSD", vehicle_no: "TLR-MIX",
    drops: [{ client_id: W1.id, litres: 1000, override_limit: true }, { client_id: W1.id, product: "PMG", litres: 600, override_limit: true }] }), `mixed trip (W1 ${W1.name} ${JSON.stringify(W1.rates)}, petrol ${pmg()})`);
  assert.equal(mix.product, "HSD+PMG", "trip shows both fuels");
  const [dh, dp] = mix.drops;
  assert.equal(dh.product, "HSD"); assert.equal(dp.product, "PMG");
  const cards = db.all("SELECT product FROM wholesale_txns WHERE trip_id=? ORDER BY id", mix.id).map((r: any) => r.product);
  assert.deepEqual(cards, ["HSD", "PMG"]);
  near(h0 - hsd(), 1000, "diesel tank down by the diesel drop"); near(p0 - pmg(), 600, "petrol tank down by the petrol drop");
  const A = await books();
  near(A.due1 - B.due1, dh.amount + dp.amount, "client owes both drops");
  near(A.tb("Wholesale receivable") - B.tb("Wholesale receivable"), dh.amount + dp.amount, "ledger receivable");
  near(A.reg.wholesale - B.reg.wholesale, 1000, "diesel register: only the diesel drop");
  near(mix.delivered_l, 1600); near(mix.billed, dh.amount + dp.amount);
  // put it back so the later checks work on the earlier numbers
  for (const d of mix.drops) ok(await call("admin", "POST", `/api/wholesale/txns/${d.id}/void`, { reason: "test" }), "void");
  near(hsd(), h0); near(pmg(), p0);
});

test("claim settled by credit note, supplier paid from the bank, a dip: everything still agrees", async () => {
  const claim = db.get("SELECT * FROM shortage_claims WHERE id=?", claimId);
  ok(await call("admin", "POST", `/api/claims/${claimId}/settle`, { action: "recovered", method: "credit_note" }), "credit note");
  ok(await call("admin", "POST", `/api/suppliers/${S.id}/payment`, { amount: 1_000_000, method: "Bank transfer", account_id: acc }), "pay by bank");
  const book = db.get("SELECT current_l FROM tanks WHERE id=?", tank.id).current_l;
  // a dip far from the book (a typing slip like 150 for 15 cm) is held until someone confirms it
  const slip = await call("manager", "POST", "/api/stock/dip", { tank_id: tank.id, measured_l: book * 0.7 });
  assert.equal(slip.status, 409); assert.match(slip.data.error, /confirm/);
  near(db.get("SELECT current_l FROM tanks WHERE id=?", tank.id).current_l, book, "stock not touched");
  ok(await call("manager", "POST", "/api/stock/dip", { tank_id: tank.id, measured_l: book - 40 }), "dip 40 L less");
  const B = await books();
  near(B.owed - B0.owed, invoice * rate - claim.amount - 1_000_000, "supplier: bill − credit note − payment");
  near(B.tb(`Payable — ${S.name}`) - B0.tb(`Payable — ${S.name}`), -(B.owed - B0.owed), "ledger payable = supplier balance");
  near(B.tb("Shortage claims recovered") - B0.tb("Shortage claims recovered"), -claim.amount, "claim income in the ledger");
  near(B.bank - B0.bank, -1_000_000, "bank down");
  near(B.tb("Bank") - B0.tb("Bank"), -1_000_000, "ledger bank down");
  near(B.reg.gain_loss - B0.reg.gain_loss, -40, "register: dip loss");
  near(B.rep.dip_adjust_l - B0.rep.dip_adjust_l, -40, "report: dip loss");
  near(B.hsd - B0.hsd, received - 3000 - 40, "diesel stock");
  // the register's day adds up, and its closing is what the tanks hold
  const r = B.reg;
  near(r.opening + r.receipts - r.sales - r.wholesale + r.gain_loss, r.closing, "register day adds up");
  near(r.closing, B.hsd, "register closing = tanks");
  near(B.l.totals.debit, B.l.totals.credit, "journal balances");
});
