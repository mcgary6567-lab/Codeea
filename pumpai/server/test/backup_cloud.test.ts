/** Cloud backup copies (Google Drive service account, Dropbox refresh token) — HTTP mocked, real JWT signing. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import crypto from "node:crypto";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-cloud-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.BACKUP_DIR = path.join(dir, "backups");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server;
let base = "";
let admin = "", manager = "";
async function call(tok: string, method: string, url: string, body?: unknown) {
  const res = await realFetch(base + url, { method, headers: { "content-type": "application/json", ...(tok ? { authorization: `Bearer ${tok}` } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}
const ok = (r: { status: number; data: any }, msg: string) => { assert.equal(r.status, 200, `${msg}: ${JSON.stringify(r.data)}`); return r.data; };

// ---- fake Google + Dropbox: everything not on our own server is answered here ----
const realFetch = globalThis.fetch;
const calls: { url: string; method: string; headers: Record<string, string>; body?: any }[] = [];
const drive: { id: string; name: string }[] = [];
const dbx: string[] = [];
let googleDown = false;
const { privateKey, publicKey } = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
const pem = privateKey.export({ type: "pkcs8", format: "pem" }) as string;
globalThis.fetch = (async (input: any, init: any = {}) => {
  const url = String(input);
  if (url.startsWith(base)) return realFetch(input, init);
  const headers: Record<string, string> = {}; for (const [k, v] of Object.entries(init.headers ?? {})) headers[k.toLowerCase()] = String(v);
  calls.push({ url, method: init.method ?? "GET", headers, body: init.body });
  const json = (o: any, status = 200) => new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json" } });
  if (googleDown && url.includes("googleapis.com")) return json({ error: { message: "backend down" } }, 503);
  if (url === "https://oauth2.googleapis.com/token") {
    const assertion = new URLSearchParams(String(init.body)).get("assertion")!;
    const [h, c, s] = assertion.split(".");
    const okSig = crypto.createVerify("RSA-SHA256").update(`${h}.${c}`).verify(publicKey, Buffer.from(s.replace(/-/g, "+").replace(/_/g, "/"), "base64"));
    const claims = JSON.parse(Buffer.from(c, "base64").toString());
    if (!okSig || claims.iss !== "backup@demo.iam.gserviceaccount.com") return json({ error: "invalid_grant" }, 400);
    return json({ access_token: "g-token", expires_in: 3600 });
  }
  if (url.includes("googleapis.com") && headers.authorization !== "Bearer g-token") return json({ error: { message: "unauthorized" } }, 401);
  if (url.startsWith("https://www.googleapis.com/drive/v3/files/FOLDER123?")) return json({ id: "FOLDER123", name: "PumpAI backups", mimeType: "application/vnd.google-apps.folder" });
  if (url.startsWith("https://www.googleapis.com/drive/v3/files/NOTAFOLDER?")) return json({ id: "NOTAFOLDER", name: "x.pdf", mimeType: "application/pdf" });
  if (url.startsWith("https://www.googleapis.com/drive/v3/files/") && url.includes("?fields=")) return json({ error: { message: "File not found" } }, 404);
  if (url.startsWith("https://www.googleapis.com/upload/drive/v3/files")) {
    const m = String(init.body).match(/"name":"(pumpai-[^"]+)"/); drive.push({ id: `id${drive.length + 1}`, name: m![1] }); return json({ id: "new", name: m![1] });
  }
  if (url.startsWith("https://www.googleapis.com/drive/v3/files?q=")) return json({ files: drive });
  if (init.method === "DELETE" && url.startsWith("https://www.googleapis.com/drive/v3/files/")) { const id = url.split("/files/")[1].split("?")[0]; const i = drive.findIndex((f) => f.id === id); if (i >= 0) drive.splice(i, 1); return new Response(null, { status: 204 }); }
  if (url === "https://api.dropboxapi.com/oauth2/token") {
    const p = new URLSearchParams(String(init.body));
    if (headers.authorization !== `Basic ${Buffer.from("APPKEY:APPSECRET").toString("base64")}`) return json({ error: "invalid_client" }, 401);
    if (p.get("grant_type") === "authorization_code") return p.get("code") === "GOODCODE" ? json({ access_token: "d-token", refresh_token: "REFRESH1" }) : json({ error: "invalid_grant", error_description: "code expired" }, 400);
    if (p.get("grant_type") === "refresh_token" && p.get("refresh_token") === "REFRESH1") return json({ access_token: "d-token" });
    return json({ error: "invalid_grant" }, 400);
  }
  if (url.includes("dropboxapi.com") && headers.authorization !== "Bearer d-token") return json({ error_summary: "invalid_access_token" }, 401);
  if (url.endsWith("/users/get_current_account")) return json({ email: "owner@example.com", name: { display_name: "Owner" } });
  if (url.endsWith("/files/create_folder_v2")) return json({ error_summary: "path/conflict/folder" }, 409);
  if (url === "https://content.dropboxapi.com/2/files/upload") { const arg = JSON.parse(headers["dropbox-api-arg"]); dbx.push(arg.path); return json({ name: path.basename(arg.path) }); }
  if (url.endsWith("/files/list_folder")) return json({ entries: dbx.map((p) => ({ name: path.basename(p), path_lower: p.toLowerCase() })) });
  if (url.endsWith("/files/delete_v2")) { const p = JSON.parse(String(init.body)).path; const i = dbx.findIndex((x) => x.toLowerCase() === p); if (i >= 0) dbx.splice(i, 1); return json({}); }
  return json({ error: `unmocked ${url}` }, 500);
}) as any;

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  admin = (await call("", "POST", "/api/auth/login", { email: "admin@pumpai.pk", password: "demo1234" })).data.token;
  manager = (await call("", "POST", "/api/auth/login", { email: "manager@pumpai.pk", password: "demo1234" })).data.token;
});
after(() => { server?.close(); globalThis.fetch = realFetch; });

const saJson = JSON.stringify({ type: "service_account", client_email: "backup@demo.iam.gserviceaccount.com", private_key: pem, project_id: "demo" });

test("nothing connected: the backups card says so; manager cannot touch it", async () => {
  const d = ok(await call(admin, "GET", "/api/backups"), "backups");
  assert.equal(d.cloud.gdrive.configured, false); assert.equal(d.cloud.dropbox.configured, false);
  assert.equal((await call(manager, "GET", "/api/backups")).status, 403);
  assert.equal((await call(manager, "PUT", "/api/backups/cloud/gdrive", { json: saJson, folder_id: "FOLDER123" })).status, 403);
});

test("Google Drive: bad JSON / unshared folder refused; a good key + folder connects (checked live)", async () => {
  let r = await call(admin, "PUT", "/api/backups/cloud/gdrive", { json: '{"type":"user","client_email":"x@y.com","private_key":"nope"}', folder_id: "FOLDER123" });
  assert.equal(r.status, 400); assert.match(r.data.error, /service-account/);
  r = await call(admin, "PUT", "/api/backups/cloud/gdrive", { json: saJson, folder_id: "MISSINGFOLDER" });
  assert.equal(r.status, 400); assert.match(r.data.error, /Share the folder with backup@demo/);
  r = await call(admin, "PUT", "/api/backups/cloud/gdrive", { json: saJson, folder_id: "NOTAFOLDER" });
  assert.equal(r.status, 400); assert.match(r.data.error, /not a folder/);
  // a pasted folder LINK works too
  const d = ok(await call(admin, "PUT", "/api/backups/cloud/gdrive", { json: saJson, folder_id: "https://drive.google.com/drive/folders/FOLDER123?usp=sharing" }), "connect");
  assert.equal(d.folder, "PumpAI backups"); assert.equal(d.cloud.gdrive.configured, true); assert.equal(d.cloud.gdrive.folder_id, "FOLDER123");
});

test("Dropbox: connect with the one-time code (refresh token kept), bad code refused", async () => {
  assert.equal((await call(admin, "GET", "/api/backups/cloud/dropbox/auth-url?app_key=")).status, 400);
  const u = ok(await call(admin, "GET", "/api/backups/cloud/dropbox/auth-url?app_key=APPKEY"), "url");
  assert.match(u.url, /dropbox\.com\/oauth2\/authorize\?client_id=APPKEY.*token_access_type=offline/);
  const bad = await call(admin, "POST", "/api/backups/cloud/dropbox/connect", { app_key: "APPKEY", app_secret: "APPSECRET", code: "WRONGCODE" });
  assert.equal(bad.status, 400); assert.match(bad.data.error, /code expired/);
  const d = ok(await call(admin, "POST", "/api/backups/cloud/dropbox/connect", { app_key: "APPKEY", app_secret: "APPSECRET", code: "GOODCODE" }), "connect");
  assert.equal(d.account, "owner@example.com"); assert.equal(d.cloud.dropbox.configured, true);
  const chk = ok(await call(admin, "POST", "/api/backups/cloud/dropbox/check", {}), "check");
  assert.equal(chk.account, "owner@example.com");
});

test("a backup is uploaded to both clouds; 'send now' works; only the newest 14 stay in the cloud", async () => {
  const { makeBackup } = await import("../src/routes/system.js");
  // seed 15 older local backups and copies so the retention has something to trim
  fs.mkdirSync(process.env.BACKUP_DIR!, { recursive: true });
  for (let i = 1; i <= 15; i++) { const n = `pumpai-2026-09-${String(i).padStart(2, "0")}T02-30-00.db`; drive.push({ id: `old${i}`, name: n }); dbx.push(`/PumpAI backups/${n}`); }
  calls.length = 0;
  const b = makeBackup();
  // cloudPushAll runs in the background → wait for both results to be stored
  for (let i = 0; i < 50; i++) { const d = ok(await call(admin, "GET", "/api/backups"), "poll"); if (d.cloud.gdrive.last && d.cloud.dropbox.last) break; await new Promise((r) => setTimeout(r, 50)); }
  const d = ok(await call(admin, "GET", "/api/backups"), "backups");
  assert.equal(d.cloud.gdrive.last.ok, true, JSON.stringify(d.cloud.gdrive.last)); assert.equal(d.cloud.gdrive.last.file, b.name);
  assert.equal(d.cloud.dropbox.last.ok, true, JSON.stringify(d.cloud.dropbox.last)); assert.equal(d.cloud.dropbox.last.file, b.name);
  assert.ok(drive.some((f) => f.name === b.name), "on Drive"); assert.ok(dbx.some((p) => p.endsWith(b.name)), "on Dropbox");
  assert.equal(drive.length, 14, "Drive keeps 14"); assert.equal(dbx.length, 14, "Dropbox keeps 14");
  assert.ok(!drive.some((f) => f.name.includes("2026-09-01")) && !dbx.some((p) => p.includes("2026-09-01")), "oldest trimmed");
  const up = calls.find((c) => c.url.startsWith("https://www.googleapis.com/upload/"));
  assert.ok(up && String(up.body).includes(`"parents":["FOLDER123"]`), "uploaded into the shared folder");
  // send the latest again by hand
  const push = ok(await call(admin, "POST", "/api/backups/cloud/dropbox/push", {}), "push");
  assert.equal(push.result.ok, true);
});

test("a cloud outage is recorded, never breaks the backup itself", async () => {
  googleDown = true;
  const r = await call(admin, "POST", "/api/backups/cloud/gdrive/push", {});
  assert.equal(r.status, 502); assert.match(r.data.error, /backend down|HTTP 503/);
  const d = ok(await call(admin, "GET", "/api/backups"), "backups");
  assert.equal(d.cloud.gdrive.last.ok, false);
  const { makeBackup } = await import("../src/routes/system.js");
  assert.ok(makeBackup().bytes > 0, "local backup still made");
  googleDown = false;
});

test("disconnect forgets the keys", async () => {
  const d = ok(await call(admin, "DELETE", "/api/backups/cloud/gdrive"), "disconnect");
  assert.equal(d.cloud.gdrive.configured, false); assert.equal(d.cloud.gdrive.last, null);
  assert.equal((await call(admin, "POST", "/api/backups/cloud/gdrive/push", {})).status, 400);
  assert.equal((await call(admin, "DELETE", "/api/backups/cloud/icloud")).status, 400);
});
