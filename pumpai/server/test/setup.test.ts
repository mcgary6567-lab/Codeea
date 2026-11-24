/** A fresh install: setup wizard, branding, business profile, integrations from the app. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-setup-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.DEMO_DATA = "0";
process.env.SETUP_TOKEN = "AB12CD34";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server;
let base = "";
let token = "";
async function call(method: string, url: string, body?: unknown, auth = true) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(auth && token ? { authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, msg: string) => { assert.equal(r.status, 200, `${msg}: ${JSON.stringify(r.data)}`); return r.data; };
const PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
const setup = (over: any = {}) => ({
  code: "ab12cd34",
  business: { name: "Bismillah Filling Station", owner_name: "Chaudhry Aslam", owner_phone: "0300 7654321", biz_city: "Multan", omc: "Shell", ntn: "1234567-8", brand_color: "#2563eb", receipt_footer: "Allah Hafiz!", logo: PNG },
  admin: { name: "Chaudhry Aslam", email: "aslam@bismillah.pk", password: "strongpass1", pin: "2468" },
  stations: [{ name: "Bismillah Bosan Road", tanks: [
    // meters given one by one, with the reading on each today
    { name: "Tank-1 Petrol", product: "PMG", capacity_l: 25000, current_l: 12000, nozzles: [{ meter_no: 1, label: "Machine 1 left", totalizer: 1234567.5 }, { meter_no: 2, label: "Machine 1 right", totalizer: 98765 }, { label: "Machine 2 left" }, {}] },
    // or just a count
    { name: "Tank-2 Diesel", product: "HSD", capacity_l: 30000, current_l: 18000, nozzles: 2 }] }],
  prices: { PMG: 262.5, HSD: 268.9 },
  ...over,
});

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("a new install has no demo data and asks for setup", async () => {
  const s = ok(await call("GET", "/api/setup/status"), "status");
  assert.equal(s.needed, true); assert.equal(s.needs_code, true);
  assert.equal(ok(await call("GET", "/api/branding"), "branding").setup_needed, true);
  assert.equal((await call("POST", "/api/auth/login", { email: "admin@pumpai.pk", password: "demo1234" })).status, 401, "no demo users");
});

test("setup wizard: wrong code refused, missing price refused, then the pump is created", async () => {
  assert.equal((await call("POST", "/api/setup", setup({ code: "nope" }))).status, 403);
  assert.equal((await call("POST", "/api/setup", setup({ prices: { PMG: 262.5 } }))).status, 400, "diesel price missing");
  const r = ok(await call("POST", "/api/setup", setup()), "setup");
  token = r.token;
  const me = ok(await call("GET", "/api/me"), "me");
  assert.equal(me.user.role, "admin"); assert.equal(me.tenant.name, "Bismillah Filling Station");
  const st = ok(await call("GET", "/api/stations"), "stations");
  assert.equal(st.length, 1); assert.equal(st[0].tanks.length, 2); assert.equal(st[0].nozzles.length, 6);
  // each meter keeps its number, name and today's reading; unnamed ones get a default name and the next number
  const byNo = Object.fromEntries(st[0].nozzles.map((n: any) => [n.meter_no, n]));
  assert.equal(byNo[1].label, "Machine 1 left"); assert.equal(byNo[1].totalizer, 1234567.5);
  assert.equal(byNo[2].label, "Machine 1 right"); assert.equal(byNo[2].totalizer, 98765);
  assert.equal(byNo[3].label, "Machine 2 left"); assert.equal(byNo[4].label, "PMG-4"); assert.equal(byNo[4].totalizer, 0);
  assert.equal(byNo[5].label, "HSD-1"); assert.equal(byNo[6].label, "HSD-2");
  assert.deepEqual(st[0].nozzles.map((n: any) => n.meter_no).sort((a: number, b: number) => a - b), [1, 2, 3, 4, 5, 6]);
  // the first shift must open from today's reading, not from 0
  assert.equal((await call("POST", "/api/shifts/open", { station_id: st[0].id, attendant: "x", readings: { [byNo[1].id]: 1000 } })).status, 400, "reading below the meter is refused");
  const prices = ok(await call("GET", "/api/prices"), "prices");
  assert.equal(prices.current.PMG.price, 262.5);
  assert.equal(prices.current.HOBC, undefined, "no hi-octane tank, no price");
  assert.ok(ok(await call("GET", "/api/checklist/items"), "checklist").length >= 8, "default daily checks");
  assert.ok(ok(await call("GET", "/api/automations"), "automations").length >= 20);
  assert.equal((await call("POST", "/api/setup", setup())).status, 409, "only once");
  assert.equal(ok(await call("POST", "/api/auth/login", { email: "aslam@bismillah.pk", password: "strongpass1" }, false), "login").user.name, "Chaudhry Aslam");
});

test("branding: logo, colour and name everywhere; profile can be edited", async () => {
  const b = ok(await call("GET", "/api/branding", undefined, false), "branding");
  assert.equal(b.setup_needed, false); assert.equal(b.color, "#2563eb"); assert.ok(b.logo_url);
  const logo = await fetch(`${base}/branding/logo`);
  assert.equal(logo.headers.get("content-type"), "image/png");
  const m = await (await fetch(`${base}/manifest.webmanifest`)).json() as any;
  assert.match(m.name, /Bismillah Filling Station/); assert.equal(m.theme_color, "#2563eb");
  const p = ok(await call("PUT", "/api/business", { biz_phone: "061-1234567", website: "fb.com/bismillahfs", brand_color: "#dc2626" }), "profile");
  assert.equal(p.biz_phone, "061-1234567"); assert.equal(p.brand_color, "#dc2626"); assert.equal(p.ntn, "1234567-8");
  // a receipt carries the logo and the receipt line
  const pumpUser = ok(await call("GET", "/api/stations"), "st")[0];
  ok(await call("POST", "/api/shifts/open", { station_id: pumpUser.id, attendant: "Chaudhry Aslam" }), "shift");
  const sale = ok(await call("POST", "/api/sales", { station_id: pumpUser.id, product: "PMG", amount: 1000, payment_method: "cash" }), "sale");
  const html = await (await fetch(base + new URL(sale.receipt_url).pathname)).text();
  assert.match(html, /branding\/logo/); assert.match(html, /Allah Hafiz!/);
});

test("integrations are set from the app and work without a restart", async () => {
  const { aiEnabled, config } = await import("../src/config.js");
  assert.equal(aiEnabled(), false);
  ok(await call("PUT", "/api/integrations", { anthropic_key: "sk-ant-test-1234", wa_verify_token: "my-verify", public_url: "https://pump.bismillah.pk" }), "save");
  assert.equal(aiEnabled(), true); assert.equal(config.publicUrl, "https://pump.bismillah.pk");
  const v = ok(await call("GET", "/api/integrations"), "get").values;
  assert.match(v.anthropic_key, /^•+1234$/, "secret is masked");
  ok(await call("PUT", "/api/integrations", { anthropic_key: v.anthropic_key }), "masked value kept");
  assert.equal(config.anthropicKey, "sk-ant-test-1234");
  const verify = await fetch(`${base}/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=my-verify&hub.challenge=42`);
  assert.equal(await verify.text(), "42");
  assert.equal((await call("PUT", "/api/integrations", { public_url: "pump.pk" })).status, 400);
  ok(await call("PUT", "/api/integrations", { anthropic_key: "" }), "clear key");
  assert.equal(aiEnabled(), false);
});
