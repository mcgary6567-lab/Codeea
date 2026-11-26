import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-batchf-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server;
let base = "";
let db: typeof import("../src/db.js");
let token = "";
async function call(method: string, url: string, body?: unknown, auth = "") {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(auth ? { authorization: `Bearer ${auth}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, msg: string) => { assert.equal(r.status, 200, `${msg}: ${JSON.stringify(r.data)}`); return r.data; };

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  token = (await call("POST", "/api/auth/login", { email: "admin@pumpai.pk", password: "demo1234" })).data.token;
});
after(() => server?.close());

test("admin 2FA: off = direct token; on = password returns a code step, then verify issues the token", async () => {
  // off by default → normal login
  const r1 = ok(await call("POST", "/api/auth/login", { email: "admin@pumpai.pk", password: "demo1234" }), "login off");
  assert.ok(r1.token && !r1.twofa, "direct token when 2FA off");

  // give the admin a phone and turn 2FA on
  db.run("UPDATE users SET phone='923009990000' WHERE email='admin@pumpai.pk'");
  ok(await call("PUT", "/api/settings", { admin_2fa: true }, token), "enable 2fa");

  // now password alone returns a 2FA challenge, no token
  const r2 = await call("POST", "/api/auth/login", { email: "admin@pumpai.pk", password: "demo1234" });
  assert.equal(r2.status, 200, JSON.stringify(r2.data));
  assert.ok(r2.data.twofa && r2.data.user_id && !r2.data.token, "challenge, no token");

  // wrong code is rejected; the real code (read from the DB store) verifies
  const bad = await call("POST", "/api/auth/verify-2fa", { user_id: r2.data.user_id, code: "000000" });
  assert.equal(bad.status, 401, "wrong code rejected");
  // the stored code is hashed; re-issue a known one by logging in again and brute-checking is not possible,
  // so verify the stored row exists and that a fresh login replaces it
  assert.ok(db.get("SELECT user_id FROM login_otps WHERE user_id=?", r2.data.user_id), "otp stored");

  // happy path: set a known code via the helper, then verify issues a real session token
  const { create2fa } = await import("../src/auth.js");
  const code = create2fa(r2.data.user_id);
  const good = ok(await call("POST", "/api/auth/verify-2fa", { user_id: r2.data.user_id, code }), "verify");
  assert.ok(good.token, "token after correct code");
  assert.ok(!db.get("SELECT user_id FROM login_otps WHERE user_id=?", r2.data.user_id), "otp cleared after use");

  // salesman (not admin) is unaffected even with 2FA on
  const sm = await call("POST", "/api/auth/login", { email: "salesman@pumpai.pk", password: "demo1234" });
  assert.ok(sm.data.token && !sm.data.twofa, "non-admin unaffected");
});
