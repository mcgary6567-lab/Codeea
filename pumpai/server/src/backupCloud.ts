/**
 * Cloud copies of the nightly database backup — a backup on the same server dies with the server.
 * Two providers, both set up from Settings → Backups (no shell access needed):
 *  - Google Drive: a service-account key (JSON) + the ID of a Drive folder shared with that account.
 *  - Dropbox: an app key/secret + a one-time code from Dropbox's "connect" page → refresh token kept here.
 * Each new backup is uploaded to every connected provider; only the last KEEP_CLOUD copies are kept there.
 * Results (ok / error, when) are stored per provider and shown on the Backups card. Plain fetch, no SDKs.
 */
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { getSetting, setSetting } from "./db.js";

export const PROVIDERS = ["gdrive", "dropbox"] as const;
export type Provider = (typeof PROVIDERS)[number];
const KEEP_CLOUD = 14;
const FOLDER_NAME = "PumpAI backups";
const NAME = /^pumpai-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.db$/;

const readJson = <T,>(key: string): T | null => { try { return JSON.parse(getSetting(0, key, "null")); } catch { return null; } };
type GdriveCfg = { client_email: string; private_key: string; folder_id: string };
type DropboxCfg = { app_key: string; app_secret: string; refresh_token: string; account?: string | null };
export const gdriveCfg = () => readJson<GdriveCfg>("cloud_gdrive");
export const dropboxCfg = () => readJson<DropboxCfg>("cloud_dropbox");
export type CloudResult = { at: string; ok: boolean; message: string | null; file?: string };
const lastOf = (p: Provider) => readJson<CloudResult>(`cloud_last_${p}`);
const remember = (p: Provider, r: CloudResult) => setSetting(0, `cloud_last_${p}`, JSON.stringify(r));

const fetchJson = async (url: string, init: RequestInit, what: string) => {
  const res = await fetch(url, init);
  const text = await res.text();
  let data: any = null; try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text.slice(0, 200) }; }
  if (!res.ok) throw new Error(`${what}: HTTP ${res.status} ${data?.error?.message ?? data?.error_summary ?? data?.error_description ?? data?.error ?? data?.raw ?? ""}`.trim());
  return data;
};

/* ---------------- Google Drive (service account) ---------------- */
const b64url = (s: Buffer | string) => Buffer.from(s).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
/** Parse the key file Google gives for a service account; only what we need is kept. */
export function parseServiceAccount(json: string): { client_email: string; private_key: string } {
  let o: any; try { o = JSON.parse(json); } catch { throw new Error("This is not the service-account JSON key file"); }
  if (o?.type !== "service_account" || !o.client_email || !o.private_key) throw new Error("The JSON must be a Google service-account key (type service_account, client_email, private_key)");
  try { crypto.createPrivateKey(o.private_key); } catch { throw new Error("The private_key in the JSON is not valid"); }
  return { client_email: o.client_email, private_key: o.private_key };
}
async function gdriveToken(cfg: { client_email: string; private_key: string }) {
  const now = Math.floor(Date.now() / 1000);
  const header = b64url(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const claims = b64url(JSON.stringify({ iss: cfg.client_email, scope: "https://www.googleapis.com/auth/drive", aud: "https://oauth2.googleapis.com/token", iat: now, exp: now + 3600 }));
  const sig = crypto.createSign("RSA-SHA256").update(`${header}.${claims}`).sign(cfg.private_key);
  const body = new URLSearchParams({ grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer", assertion: `${header}.${claims}.${b64url(sig)}` });
  const d = await fetchJson("https://oauth2.googleapis.com/token", { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body }, "Google sign-in");
  return d.access_token as string;
}
const G = "https://www.googleapis.com/drive/v3";
/** Check the key works and the folder is reachable (shared with the service account). */
export async function gdriveCheck(cfg: GdriveCfg) {
  const tok = await gdriveToken(cfg);
  const f = await fetchJson(`${G}/files/${encodeURIComponent(cfg.folder_id)}?fields=id,name,mimeType&supportsAllDrives=true`, { headers: { authorization: `Bearer ${tok}` } }, "Drive folder");
  if (f.mimeType !== "application/vnd.google-apps.folder") throw new Error("That ID is not a folder");
  return { folder: f.name as string };
}
async function gdriveUpload(cfg: GdriveCfg, file: string) {
  const tok = await gdriveToken(cfg);
  const auth = { authorization: `Bearer ${tok}` };
  const name = path.basename(file);
  const boundary = `pumpai${crypto.randomBytes(8).toString("hex")}`;
  const meta = Buffer.from(`--${boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n${JSON.stringify({ name, parents: [cfg.folder_id] })}\r\n--${boundary}\r\nContent-Type: application/octet-stream\r\n\r\n`);
  const body = Buffer.concat([meta, fs.readFileSync(file), Buffer.from(`\r\n--${boundary}--`)]);
  await fetchJson(`https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true&fields=id,name`,
    { method: "POST", headers: { ...auth, "content-type": `multipart/related; boundary=${boundary}`, "content-length": String(body.length) }, body }, "Drive upload");
  // keep only the newest copies on Drive
  const q = encodeURIComponent(`'${cfg.folder_id}' in parents and name contains 'pumpai-' and trashed=false`);
  const list = await fetchJson(`${G}/files?q=${q}&fields=files(id,name)&pageSize=200&supportsAllDrives=true&includeItemsFromAllDrives=true`, { headers: auth }, "Drive list");
  const old = (list.files as { id: string; name: string }[]).filter((x) => NAME.test(x.name)).sort((a, b) => b.name.localeCompare(a.name)).slice(KEEP_CLOUD);
  for (const o of old) await fetch(`${G}/files/${o.id}?supportsAllDrives=true`, { method: "DELETE", headers: auth }).catch(() => undefined);
  return `${name} → Google Drive`;
}

/* ---------------- Dropbox (OAuth refresh token) ---------------- */
export const dropboxAuthUrl = (appKey: string) => `https://www.dropbox.com/oauth2/authorize?client_id=${encodeURIComponent(appKey)}&response_type=code&token_access_type=offline`;
/** One-time: swap the code from Dropbox's connect page for a refresh token. */
export async function dropboxConnect(appKey: string, appSecret: string, code: string): Promise<DropboxCfg> {
  const body = new URLSearchParams({ code: code.trim(), grant_type: "authorization_code" });
  const d = await fetchJson("https://api.dropboxapi.com/oauth2/token", { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded", authorization: `Basic ${Buffer.from(`${appKey}:${appSecret}`).toString("base64")}` }, body }, "Dropbox connect");
  if (!d.refresh_token) throw new Error("Dropbox did not return a refresh token — use the link from this page (it asks for offline access)");
  const cfg: DropboxCfg = { app_key: appKey, app_secret: appSecret, refresh_token: d.refresh_token, account: null };
  try {
    const acc = await fetchJson("https://api.dropboxapi.com/2/users/get_current_account", { method: "POST", headers: { authorization: `Bearer ${d.access_token}` } }, "Dropbox account");
    cfg.account = acc?.email ?? acc?.name?.display_name ?? null;
  } catch { /* cosmetic */ }
  return cfg;
}
async function dropboxToken(cfg: DropboxCfg) {
  const body = new URLSearchParams({ grant_type: "refresh_token", refresh_token: cfg.refresh_token });
  const d = await fetchJson("https://api.dropboxapi.com/oauth2/token", { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded", authorization: `Basic ${Buffer.from(`${cfg.app_key}:${cfg.app_secret}`).toString("base64")}` }, body }, "Dropbox sign-in");
  return d.access_token as string;
}
export async function dropboxCheck(cfg: DropboxCfg) {
  const tok = await dropboxToken(cfg);
  const acc = await fetchJson("https://api.dropboxapi.com/2/users/get_current_account", { method: "POST", headers: { authorization: `Bearer ${tok}` } }, "Dropbox account");
  return { account: (acc?.email ?? acc?.name?.display_name ?? null) as string | null };
}
async function dropboxUpload(cfg: DropboxCfg, file: string) {
  const tok = await dropboxToken(cfg);
  const auth = { authorization: `Bearer ${tok}` };
  const name = path.basename(file);
  await fetch("https://api.dropboxapi.com/2/files/create_folder_v2", { method: "POST", headers: { ...auth, "content-type": "application/json" }, body: JSON.stringify({ path: `/${FOLDER_NAME}`, autorename: false }) }).catch(() => undefined); // exists → 409, fine
  await fetchJson("https://content.dropboxapi.com/2/files/upload", { method: "POST", headers: { ...auth, "content-type": "application/octet-stream",
    "Dropbox-API-Arg": JSON.stringify({ path: `/${FOLDER_NAME}/${name}`, mode: "overwrite", mute: true }) }, body: fs.readFileSync(file) }, "Dropbox upload");
  const list = await fetchJson("https://api.dropboxapi.com/2/files/list_folder", { method: "POST", headers: { ...auth, "content-type": "application/json" }, body: JSON.stringify({ path: `/${FOLDER_NAME}` }) }, "Dropbox list");
  const old = ((list.entries ?? []) as { name: string; path_lower: string }[]).filter((x) => NAME.test(x.name)).sort((a, b) => b.name.localeCompare(a.name)).slice(KEEP_CLOUD);
  for (const o of old) await fetch("https://api.dropboxapi.com/2/files/delete_v2", { method: "POST", headers: { ...auth, "content-type": "application/json" }, body: JSON.stringify({ path: o.path_lower }) }).catch(() => undefined);
  return `${name} → Dropbox (/${FOLDER_NAME})`;
}

/* ---------------- common ---------------- */
export const cloudConfigured = (p: Provider) => (p === "gdrive" ? Boolean(gdriveCfg()?.folder_id) : Boolean(dropboxCfg()?.refresh_token));
/** Upload one backup file to one provider; the outcome is remembered for the Backups card. */
export async function cloudPush(p: Provider, file: string): Promise<CloudResult> {
  const at = new Date().toISOString();
  try {
    const message = p === "gdrive" ? await gdriveUpload(gdriveCfg()!, file) : await dropboxUpload(dropboxCfg()!, file);
    const r = { at, ok: true, message, file: path.basename(file) }; remember(p, r); return r;
  } catch (e: any) {
    const r = { at, ok: false, message: String(e?.message ?? e).slice(0, 300), file: path.basename(file) }; remember(p, r);
    console.error(`[backup] cloud copy (${p}) failed:`, r.message);
    return r;
  }
}
/** After every backup: copy to each connected provider, one after the other (never throws). */
export async function cloudPushAll(file: string) {
  for (const p of PROVIDERS) if (cloudConfigured(p)) await cloudPush(p, file);
}
export function cloudStatus() {
  const g = gdriveCfg(), d = dropboxCfg();
  return {
    gdrive: { configured: Boolean(g?.folder_id), email: g?.client_email ?? null, folder_id: g?.folder_id ?? null, last: lastOf("gdrive") },
    dropbox: { configured: Boolean(d?.refresh_token), app_key: d?.app_key ?? null, account: d?.account ?? null, last: lastOf("dropbox") },
  };
}
