import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-proof-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server;
let base = "";
const tokens: Record<string, string> = {};
async function call(who: string, method: string, url: string, body?: unknown) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tokens[who] ? { authorization: `Bearer ${tokens[who]}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const near = (a: number, b: number, msg?: string) => assert.ok(Math.abs(a - b) < 0.05, `${msg ?? ""} ${a} ≈ ${b}`);
// money from litres stored to 2 decimals can differ by a few paisa
const nearRs = (a: number, b: number, msg?: string) => assert.ok(Math.abs(a - b) < 1, `${msg ?? ""} Rs ${a} ≈ ${b}`);

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "wholesale"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => server?.close());
const PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
const photo = async (who: string) => { const r = await call(who, "POST", "/api/ai/read-photo", { kind: "proof", image: PNG }); assert.equal(r.status, 200, JSON.stringify(r.data)); return r.data.photo_id as number; };
const has = (ids: string | null | undefined, id: number) => String(ids ?? "").split(",").map(Number).includes(id);

test("photo proof is kept with every money and stock entry and comes back in the lists", async () => {
  // wholesale payment (cheque front + back) and supply
  const client = (await call("wholesale", "GET", "/api/wholesale/clients")).data[0];
  const [a, b] = [await photo("wholesale"), await photo("wholesale")];
  const pay = await call("wholesale", "POST", `/api/wholesale/clients/${client.id}/payment`, { amount: 1000, method: "Cheque", ref: "CHQ-1", photo_ids: [a, b] });
  assert.equal(pay.status, 200, JSON.stringify(pay.data));
  const line = (await call("wholesale", "GET", `/api/wholesale/clients/${client.id}/statement`)).data.lines.find((l: any) => l.id === pay.data.id);
  assert.ok(has(line.proof_ids, a) && has(line.proof_ids, b), "both cheque photos on the statement line");
  // a photo already used cannot be moved to another entry
  const again = await call("wholesale", "POST", `/api/wholesale/clients/${client.id}/payment`, { amount: 10, method: "Cash", photo_ids: [a] });
  assert.ok(!(await call("wholesale", "GET", `/api/wholesale/clients/${client.id}/statement`)).data.lines.find((l: any) => l.id === again.data.id).proof_ids);

  // khata payment
  const cust = (await call("manager", "GET", "/api/customers")).data.find((c: any) => c.balance > 0);
  const kp = await photo("manager");
  assert.equal((await call("manager", "POST", `/api/customers/${cust.id}/khata`, { type: "credit", amount: 500, method: "Cheque", notify: false, photo_ids: [kp] })).status, 200);
  assert.ok(has((await call("manager", "GET", `/api/customers/${cust.id}`)).data.ledger[0].proof_ids, kp));

  // supplier payment
  const sup = (await call("admin", "GET", "/api/suppliers")).data[0];
  const sp = await photo("admin");
  assert.equal((await call("admin", "POST", `/api/suppliers/${sup.id}/payment`, { amount: 1000, method: "Pay order", photo_ids: [sp] })).status, 200);
  assert.ok((await call("admin", "GET", `/api/suppliers/${sup.id}`)).data.lines.some((l: any) => has(l.proof_ids, sp)));

  // dip reading
  const tank = (await call("manager", "GET", "/api/stations")).data[0].tanks[0];
  const dp = await photo("manager");
  assert.equal((await call("manager", "POST", "/api/stock/dip", { tank_id: tank.id, measured_l: tank.current_l, photo_ids: [dp] })).status, 200);
  assert.ok((await call("manager", "GET", "/api/stock")).data.dips.some((x: any) => has(x.proof_ids, dp)));

  // staff advance
  const staff = (await call("manager", "GET", "/api/staff")).data;
  const sid = (staff.staff ?? staff)[0].id ?? (staff.staff ?? staff)[0].user?.id;
  const st = await photo("manager");
  const adv = await call("manager", "POST", `/api/staff/${sid}/entry`, { type: "advance", amount: 500, photo_ids: [st] });
  assert.equal(adv.status, 200, JSON.stringify(adv.data));
  assert.ok(adv.data.lines.some((l: any) => has(l.proof_ids, st)));

  // office cash count
  const cc = await photo("manager");
  assert.equal((await call("manager", "POST", "/api/cash/count", { amount: 1000, photo_ids: [cc] })).status, 200);
  assert.ok((await call("manager", "GET", "/api/cash")).data.counts.some((x: any) => has(x.proof_ids, cc)));

  // shop stock received with the supplier bill
  const items = (await call("manager", "GET", "/api/shop/items")).data;
  const item = (items.items ?? items)[0];
  const sh = await photo("manager");
  assert.equal((await call("manager", "POST", `/api/shop/items/${item.id}/stock-in`, { qty: 2, photo_ids: [sh] })).status, 200);
  assert.ok((await call("manager", "GET", `/api/shop/items/${item.id}/moves`)).data.some((m: any) => has(m.proof_ids, sh)));
});
