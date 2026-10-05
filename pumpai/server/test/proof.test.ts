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
  (globalThis as any).base = base;
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
  // a cheque already in the bank (account given) is posted at once; one still in hand waits in the cheque register
  const acc = (await call("admin", "GET", "/api/bank/accounts")).data.accounts[0];
  const pay = await call("wholesale", "POST", `/api/wholesale/clients/${client.id}/payment`, { amount: 1000, method: "Cheque", ref: "CHQ-1", photo_ids: [a, b], account_id: acc.id });
  assert.equal(pay.status, 200, JSON.stringify(pay.data));
  const line = (await call("wholesale", "GET", `/api/wholesale/clients/${client.id}/statement`)).data.lines.find((l: any) => l.id === pay.data.id);
  assert.ok(has(line.proof_ids, a) && has(line.proof_ids, b), "both cheque photos on the statement line");
  // a photo already used cannot be moved to another entry
  const again = await call("wholesale", "POST", `/api/wholesale/clients/${client.id}/payment`, { amount: 10, method: "Cash", photo_ids: [a] });
  assert.ok(!(await call("wholesale", "GET", `/api/wholesale/clients/${client.id}/statement`)).data.lines.find((l: any) => l.id === again.data.id).proof_ids);

  // khata payment
  const cust = (await call("manager", "GET", "/api/customers")).data.find((c: any) => c.balance > 0);
  const kp = await photo("manager");
  assert.equal((await call("manager", "POST", `/api/customers/${cust.id}/khata`, { type: "credit", amount: 500, method: "Cheque", notify: false, photo_ids: [kp], account_id: acc.id })).status, 200);
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

test("cheque payments and salary cannot be saved without a photo", async () => {
  const client = (await call("wholesale", "GET", "/api/wholesale/clients")).data[0];
  const no = await call("wholesale", "POST", `/api/wholesale/clients/${client.id}/payment`, { amount: 100, method: "Cheque" });
  assert.equal(no.status, 400); assert.match(no.data.error, /Photo of the cheque is required/);
  assert.equal((await call("wholesale", "POST", `/api/wholesale/clients/${client.id}/payment`, { amount: 100, method: "Cash" })).status, 200, "cash without photo is fine");
  assert.equal((await call("wholesale", "POST", `/api/wholesale/clients/${client.id}/payment`, { amount: 100, method: "Cheque", photo_ids: [await photo("wholesale")] })).status, 200);
  // a photo already used for another entry does not count
  const used = await photo("wholesale");
  await call("wholesale", "POST", `/api/wholesale/clients/${client.id}/payment`, { amount: 100, method: "Cash", photo_ids: [used] });
  assert.equal((await call("wholesale", "POST", `/api/wholesale/clients/${client.id}/payment`, { amount: 100, method: "Cheque", photo_ids: [used] })).status, 400);
  const cust = (await call("manager", "GET", "/api/customers")).data.find((c: any) => c.balance > 0);
  assert.equal((await call("manager", "POST", `/api/customers/${cust.id}/khata`, { type: "credit", amount: 100, method: "Cheque", notify: false })).status, 400);
  const sup = (await call("admin", "GET", "/api/suppliers")).data[0];
  assert.equal((await call("admin", "POST", `/api/suppliers/${sup.id}/payment`, { amount: 100, method: "Cheque" })).status, 400);
  const staff = (await call("manager", "GET", "/api/staff")).data;
  const sid = (staff.staff ?? staff)[0].id ?? (staff.staff ?? staff)[0].user?.id;
  const sal = await call("manager", "POST", `/api/staff/${sid}/pay-salary`, {});
  assert.equal(sal.status, 400); assert.match(sal.data.error, /salary sheet is required/);
});

test("own khata page with link + PIN for wholesale clients and khata customers; lock, new link, off, old links", async () => {
  for (const [who, base] of [["wholesale", `/api/wholesale/clients/${(await call("wholesale", "GET", "/api/wholesale/clients")).data[0].id}/portal`],
    ["manager", `/api/customers/${(await call("manager", "GET", "/api/customers")).data.find((c: any) => c.balance > 0).id}/portal`]] as const) {
    const p = (await call(who, "GET", base)).data;
    assert.match(p.pin, /^\d{6}$/); assert.match(p.url, /\/(w|k)\/\d+-\d+-[\w-]{10}$/); assert.equal(p.enabled, true);
    const path = new URL(p.url).pathname;
    const open = await fetch((globalThis as any).base + path); assert.equal(open.status, 200); assert.match(await open.text(), /Enter the 6-digit PIN/);
    // a forged link does not open
    assert.equal((await fetch((globalThis as any).base + path.replace(/-[\w-]{10}$/, "-AAAAAAAAAA"))).status, 404);
    const post = (pin: string, extra = "") => fetch((globalThis as any).base + path, { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body: `pin=${pin}${extra}` });
    const wrong = p.pin === "000000" ? "111111" : "000000";
    assert.equal((await post(wrong)).status, 401);
    const good = await post(p.pin, "&remember=1"); assert.equal(good.status, 200);
    assert.match(await good.text(), /Balance due|You have to pay|All paid/);
    const cookie = good.headers.get("set-cookie")!.split(";")[0];
    assert.match(await (await fetch((globalThis as any).base + path, { headers: { cookie } })).text(), /Balance due|You have to pay|All paid/, "remembered phone");
    for (let i = 0; i < 5; i++) await post(wrong);
    assert.equal((await post(p.pin)).status, 429, "locked after 5 wrong PINs");
    // new link + PIN: the old link is gone, the new one opens with the new PIN
    const n = (await call(who, "POST", `${base}/new`, {})).data;
    assert.notEqual(n.url, p.url);
    assert.equal((await fetch((globalThis as any).base + path)).status, 404);
    const npath = new URL(n.url).pathname;
    assert.equal((await fetch((globalThis as any).base + npath, { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body: `pin=${n.pin}` })).status, 200);
    // turned off / on
    await call(who, "POST", `${base}/off`, {});
    assert.equal((await fetch((globalThis as any).base + npath)).status, 404);
    await call(who, "POST", `${base}/on`, {});
    assert.equal((await fetch((globalThis as any).base + npath)).status, 200);
  }
  // a copy of the server whose database never saw the "new link" (serverless) still accepts the newer link
  const db = await import("../src/db.js");
  const c = (await call("wholesale", "GET", "/api/wholesale/clients")).data[1];
  const fresh = (await call("wholesale", "POST", `/api/wholesale/clients/${c.id}/portal/new`, {})).data;
  db.run("UPDATE wholesale_clients SET portal_v=0 WHERE id=?", c.id);
  assert.equal((await fetch((globalThis as any).base + new URL(fresh.url).pathname, { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body: `pin=${fresh.pin}` })).status, 200);
  // old khata links sent before PINs lead to the PIN page
  const jwt = (await import("jsonwebtoken")).default;
  const { config } = await import("../src/config.js");
  const cust = db.get("SELECT * FROM customers WHERE balance > 0 LIMIT 1");
  const old = await fetch((globalThis as any).base + `/portal/${jwt.sign({ portal: cust.id, t: cust.tenant_id, v: cust.portal_v }, config.jwtSecret)}`, { redirect: "manual" });
  assert.equal(old.status, 302); assert.match(old.headers.get("location")!, /^\/k\//);
  assert.equal((await call("manager", "GET", `/api/wholesale/clients/${c.id}/portal`)).status, 403);
});

test("admin ticks / unticks what a role can do; it takes effect at once; owner cannot be locked out", async () => {
  const before = (await call("admin", "GET", "/api/users")).data;
  assert.ok(before.permissions["expenses.view"].includes("manager"));
  // take away expenses from the manager
  const r = await call("admin", "PUT", "/api/roles/permissions", { perm: "expenses.view", role: "manager", allowed: false });
  assert.equal(r.status, 200, JSON.stringify(r.data));
  assert.ok(!r.data.permissions["expenses.view"].includes("manager"));
  assert.equal((await call("manager", "GET", "/api/expenses")).status, 403, "blocked at once");
  assert.ok(!(await call("manager", "GET", "/api/me")).data.permissions.includes("expenses.view"));
  // give the salesman the dashboard
  await call("admin", "PUT", "/api/roles/permissions", { perm: "dashboard.view", role: "salesman", allowed: true });
  assert.ok((await call("admin", "GET", "/api/users")).data.permissions["dashboard.view"].includes("salesman"));
  // the owner's own rights and unknown rights are refused; only the admin can change rights
  assert.equal((await call("admin", "PUT", "/api/roles/permissions", { perm: "users.manage", role: "admin", allowed: false })).status, 400);
  assert.equal((await call("admin", "PUT", "/api/roles/permissions", { perm: "nope", role: "manager", allowed: true })).status, 400);
  assert.equal((await call("manager", "PUT", "/api/roles/permissions", { perm: "expenses.view", role: "manager", allowed: true })).status, 403);
  // back to standard
  const reset = await call("admin", "POST", "/api/roles/permissions/reset", {});
  assert.ok(reset.data.permissions["expenses.view"].includes("manager") && !reset.data.permissions["dashboard.view"].includes("salesman"));
  assert.equal((await call("manager", "GET", "/api/expenses")).status, 200);
});
