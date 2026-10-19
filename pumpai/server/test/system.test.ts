/** Nightly backups with staged restore, two-person price approval, phone push subscription, hardware list. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-system-"));
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

before(async () => {
  const { app } = await import("../src/index.js");
  db = await import("../src/db.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  for (const who of ["admin", "manager", "salesman"]) tokens[who] = (await call("", "POST", "/api/auth/login", { email: `${who}@pumpai.pk`, password: "demo1234" })).data.token;
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("backups: nightly job and on demand, downloadable, restore is staged for the next start", async () => {
  const { runJob } = await import("../src/automation/scheduler.js");
  assert.match(await runJob(1, "backup"), /pumpai-.*\.db \(\d+ KB\)/);
  const made = ok(await call("admin", "POST", "/api/backups", {}), "backup");
  const list = ok(await call("admin", "GET", "/api/backups"), "list");
  assert.ok(list.backups.some((b: any) => b.name === made.name)); assert.equal(list.restore_pending, false);
  const file = await fetch(`${base}/api/backups/${made.name}?token=${(await call("admin", "GET", "/api/me")).data.media_token}`);
  assert.equal(file.status, 200);
  const bytes = Buffer.from(await file.arrayBuffer());
  assert.equal(bytes.subarray(0, 15).toString(), "SQLite format 3", "a real SQLite file");
  const r = ok(await call("admin", "POST", `/api/backups/${made.name}/restore`, {}), "restore");
  assert.match(r.message, /Restart/);
  assert.equal(ok(await call("admin", "GET", "/api/backups"), "list").restore_pending, true);
  assert.ok(fs.existsSync(`${process.env.DB_PATH}.restore`));
  ok(await call("admin", "DELETE", "/api/backups/restore"), "cancel restore");
  assert.equal(fs.existsSync(`${process.env.DB_PATH}.restore`), false);
  assert.equal((await call("manager", "GET", "/api/backups")).status, 403);
  assert.equal((await call("admin", "GET", "/api/backups/..%2Ftest.db")).status, 400);
});

test("two-person rule: a manager's price change waits for the admin, then takes effect", async () => {
  ok(await call("admin", "PUT", "/api/safety", { price_approval: true }), "on");
  const before = ok(await call("manager", "GET", "/api/prices"), "prices").current.PMG.price;
  const r = ok(await call("manager", "POST", "/api/prices", { prices: { PMG: before + 3 } }), "request");
  assert.equal(r.pending, true);
  assert.equal(ok(await call("manager", "GET", "/api/prices"), "prices").current.PMG.price, before, "not applied yet");
  assert.ok(db.get("SELECT id FROM notifications WHERE type='price_request'"), "admin told");
  assert.equal((await call("manager", "POST", `/api/price-requests/${r.request_id}/approve`, {})).status, 403, "manager cannot approve");
  const done = ok(await call("admin", "POST", `/api/price-requests/${r.request_id}/approve`, {}), "approve");
  assert.ok(done.salesmen_notified >= 1);
  assert.equal(ok(await call("manager", "GET", "/api/prices"), "prices").current.PMG.price, before + 3);
  assert.equal((await call("admin", "POST", `/api/price-requests/${r.request_id}/approve`, {})).status, 400, "only once");
  const direct = ok(await call("admin", "POST", "/api/prices", { prices: { PMG: before } }), "admin direct");
  assert.equal(direct.pending, undefined);
  ok(await call("admin", "PUT", "/api/safety", { price_approval: false }), "off");
  assert.equal(ok(await call("manager", "POST", "/api/prices", { prices: { PMG: before + 1 } }), "direct").pending, undefined);
});

test("push: key, subscribe/unsubscribe; hardware integrations are listed as pending", async () => {
  const k = ok(await call("salesman", "GET", "/api/push/key"), "key");
  assert.ok(k.key.length > 40); assert.equal(k.subscribed, 0);
  const sub = { endpoint: "https://push.example.com/sub/abc123", keys: { p256dh: "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM", auth: "tBHItJI5svbpez7KI4CCXg" } };
  assert.equal(ok(await call("salesman", "POST", "/api/push/subscribe", sub), "sub").subscribed, 1);
  ok(await call("salesman", "POST", "/api/push/unsubscribe", { endpoint: sub.endpoint }), "unsub");
  assert.equal(ok(await call("salesman", "GET", "/api/push/key"), "key").subscribed, 0);
  const hw = ok(await call("admin", "GET", "/api/hardware"), "hw");
  assert.equal(hw.length, 5); assert.ok(hw.every((x: any) => x.status === "pending"));
});
