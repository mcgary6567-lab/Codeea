import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-pos-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server;
let base = "";
const tokens: Record<string, string> = {};
async function call(who: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tokens[who] ? { authorization: `Bearer ${tokens[who]}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  const text = await res.text();
  let data: any = text; try { data = JSON.parse(text); } catch { /* csv */ }
  return { status: res.status, data };
}

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("POS khata list: institutions first, no balances for salesmen", async () => {
  const s = (await call("salesman", "GET", "/api/pos/khata-accounts")).data;
  assert.ok(["police", "school", "government", "hospital"].includes(s[0].type));
  assert.ok(s.some((a: any) => a.type === "police" && a.vehicles.length >= 1));
  assert.ok(s.every((a: any) => a.balance === undefined && a.available === undefined && ["ok", "near", "full"].includes(a.status)));
  const m = (await call("manager", "GET", "/api/pos/khata-accounts")).data;
  assert.ok(m.every((a: any) => typeof a.balance === "number"));
});

test("salesman sells to a police station on khata: litres, that day's rate, vehicle and slip are kept", async () => {
  await call("salesman", "POST", "/api/shifts/open", {});
  const today = (await call("salesman", "GET", "/api/pos/today")).data;
  assert.ok(today.shift && today.prices.PMG > 0 && today.products.includes("PMG"));
  const police = (await call("salesman", "GET", "/api/pos/khata-accounts")).data.find((a: any) => a.name === "Police Station Kahna");
  const sale = await call("salesman", "POST", "/api/sales", { station_id: today.station.id, product: "PMG", litres: 30, payment_method: "khata",
    customer_id: police.id, vehicle_no: "lej-1234 (mobile 1)", slip_no: "PS-9001" });
  assert.equal(sale.status, 200);
  assert.equal(sale.data.slip_no, "PS-9001");
  assert.equal(sale.data.vehicle_no, "LEJ-1234 (MOBILE 1)");
  const rateThen = sale.data.rate;
  // price changes later: the khata entry keeps the old rate
  await call("admin", "POST", "/api/prices", { prices: { PMG: rateThen + 5 } });
  const st = (await call("manager", "GET", `/api/customers/${police.id}/statement?from=${new Date().toISOString().slice(0, 10)}`)).data;
  const line = st.lines.find((l: any) => l.slip_no === "PS-9001");
  assert.equal(line.litres, 30);
  assert.equal(line.rate, rateThen);
  assert.equal(Math.round(line.amount * 100), Math.round(30 * rateThen * 100));
  assert.equal(line.vehicle_no, "LEJ-1234 (MOBILE 1)");
  const pmg = st.totals.by_product.find((p: any) => p.product === "PMG");
  assert.ok(pmg.litres >= 30);
  assert.equal(Math.round(st.closing_balance - st.opening_balance), Math.round(st.totals.charged - st.totals.paid));
  // salesman's shift panel shows it
  const after = (await call("salesman", "GET", "/api/pos/today")).data;
  assert.equal(after.recent[0].slip_no, "PS-9001");
  assert.equal(after.recent[0].customer_name, "Police Station Kahna");
});

test("khata bill: full history balance matches the account, CSV export, salesman blocked", async () => {
  const police = (await call("manager", "GET", "/api/khata")).data.find((c: any) => c.name === "Police Station Kahna");
  const all = (await call("manager", "GET", `/api/customers/${police.id}/statement`)).data;
  assert.equal(Math.round(all.closing_balance), Math.round(police.balance));
  assert.ok(all.lines.filter((l: any) => l.type === "debit").every((l: any) => l.litres > 0 && l.rate > 0 && l.slip_no));
  const csv = await call("manager", "GET", `/api/customers/${police.id}/statement.csv`);
  assert.equal(csv.status, 200);
  assert.match(csv.data, /Slip no\./);
  assert.match(csv.data, /PS-9001/);
  assert.equal((await call("salesman", "GET", `/api/customers/${police.id}/statement`)).status, 403);
});

test("institution account types", async () => {
  const c = await call("admin", "POST", "/api/customers", { name: "Govt Girls College Kahna", phone: "03211230000", type: "school", credit_limit: 100000 });
  assert.equal(c.status, 200);
  assert.equal(c.data.type, "school");
  assert.equal((await call("admin", "POST", "/api/customers", { name: "Bad Type", phone: "03211230001", type: "alien" })).status, 400);
});
