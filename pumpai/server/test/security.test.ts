import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-security-"));
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

test("security headers, no Express banner, bad JSON is a 400", async () => {
  const r = await fetch(base + "/api/health");
  assert.equal(r.headers.get("x-powered-by"), null);
  assert.equal(r.headers.get("x-content-type-options"), "nosniff");
  assert.match(r.headers.get("content-security-policy") ?? "", /default-src 'self'/);
  assert.equal(r.headers.get("access-control-allow-origin"), null, "no open CORS");
  assert.equal((await r.json()).db, true);
  const bad = await fetch(base + "/api/auth/login", { method: "POST", headers: { "content-type": "application/json" }, body: "{bad" });
  assert.equal(bad.status, 400);
});

test("wrong passwords lock the account for a while; the right one is then refused too", async () => {
  for (let i = 0; i < 8; i++) assert.equal((await call("", "POST", "/api/auth/login", { email: "wholesale@pumpai.pk", password: "wrong" })).status, 401);
  assert.equal((await call("", "POST", "/api/auth/login", { email: "wholesale@pumpai.pk", password: "demo1234" })).status, 429);
  db.run("UPDATE users SET pw_locked_until=NULL, pw_fails=0 WHERE email='wholesale@pumpai.pk'");
  assert.equal((await call("", "POST", "/api/auth/login", { email: "wholesale@pumpai.pk", password: "demo1234" })).status, 200);
});

test("a session token never works in a link; a media token works only there and only for reading", async () => {
  const p = ok(await call("manager", "POST", "/api/ai/read-photo", { kind: "proof", image: PNG }), "photo");
  assert.equal((await fetch(`${base}/api/photos/${p.photo_id}?token=${tokens.manager}`)).status, 401, "session token in URL refused");
  const media = ok(await call("manager", "GET", "/api/me"), "me").media_token;
  const img = await fetch(`${base}/api/photos/${p.photo_id}?token=${media}`);
  assert.equal(img.status, 200); assert.equal(img.headers.get("content-type"), "image/png");
  assert.equal((await fetch(`${base}/api/me`, { headers: { authorization: `Bearer ${media}` } })).status, 401, "media token is not a session");
  assert.equal((await fetch(`${base}/api/users?token=${media}`, { method: "POST" })).status, 401, "media token cannot write");
  // a salesman can't open other people's photos
  assert.equal((await fetch(`${base}/api/photos/${p.photo_id}?token=${ok(await call("salesman", "GET", "/api/me"), "me2").media_token}`)).status, 404);
});

test("only real pictures are stored", async () => {
  const fake = "data:image/png;base64," + Buffer.from("<html><script>alert(1)</script></html>").toString("base64");
  assert.equal((await call("manager", "POST", "/api/ai/read-photo", { kind: "proof", image: fake })).status, 400);
});

test("changing your password signs out the old sessions; the admin can sign a person out", async () => {
  const old = tokens.cashier;
  const r = ok(await call("cashier", "POST", "/api/me/password", { old_password: "demo1234", new_password: "cash-new-2026" }), "change");
  assert.equal((await fetch(`${base}/api/me`, { headers: { authorization: `Bearer ${old}` } })).status, 401, "old session gone");
  tokens.cashier = r.token;
  ok(await call("cashier", "GET", "/api/me"), "new token works");
  assert.equal((await call("cashier", "POST", "/api/me/password", { old_password: "wrong", new_password: "x".repeat(10) })).status, 400);
  const me = ok(await call("cashier", "GET", "/api/me"), "me").user;
  ok(await call("admin", "POST", `/api/users/${me.id}/sign-out`, {}), "sign out");
  assert.equal((await call("cashier", "GET", "/api/me")).status, 401);
});

test("tablets: only owner / manager sign-ins link one; the owner can unlink all", async () => {
  assert.ok(!(await call("", "POST", "/api/auth/login", { email: "salesman@pumpai.pk", password: "demo1234" })).data.device_token, "salesman gets no device token");
  const dev = ok(await call("", "POST", "/api/auth/login", { email: "manager@pumpai.pk", password: "demo1234" }), "mgr").device_token;
  const list = await fetch(base + "/api/auth/pin-users", { headers: { "x-device": dev } });
  assert.equal(list.status, 200);
  assert.ok(!(await list.json()).some((p: any) => p.role === "admin"), "no admin on the PIN list");
  ok(await call("admin", "POST", "/api/security/unlink-devices", {}), "unlink");
  assert.equal((await fetch(base + "/api/auth/pin-users", { headers: { "x-device": dev } })).status, 401);
});

test("customer messages carry no payment link unless the pump set one", async () => {
  const k = ok(await call("manager", "GET", "/api/pos/khata-accounts"), "k").find((a: any) => a.balance > 0);
  const r = ok(await call("manager", "POST", `/api/customers/${k.id}/remind`, {}), "remind");
  assert.equal(r.link, "");
  assert.doesNotMatch(db.get("SELECT body FROM messages ORDER BY id DESC LIMIT 1")?.body ?? "", /example\.pk/);
});
