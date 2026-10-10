/**
 * Keeping the system safe: nightly database backups (download, restore on next start),
 * push notifications to phones, and the list of hardware integrations waiting for equipment.
 */
import { Router } from "express";
import { z } from "zod";
import fs from "node:fs";
import path from "node:path";
import { db, getSetting, setSetting, closeDb } from "../db.js";
import { execFile } from "node:child_process";
import { h, parse, tid, requirePerm } from "../auth.js";
import { AppError, audit } from "../services.js";
import { config } from "../config.js";
import { vapidPublicKey, saveSubscription, removeSubscription, subscriptionCount, pushToUser } from "../push.js";

export const system = Router();

/* ================= Backups ================= */
export const backupDir = () => process.env.BACKUP_DIR ?? path.join(path.dirname(config.dbPath), "backups");
const KEEP = 14;
const NAME = /^pumpai-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.db$/;

/** A consistent copy of the whole database (VACUUM INTO), keeping the last 14. */
export function makeBackup(): { name: string; bytes: number } {
  fs.mkdirSync(backupDir(), { recursive: true });
  const name = `pumpai-${new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 19).replace(/:/g, "-")}.db`;
  const file = path.join(backupDir(), name);
  if (fs.existsSync(file)) fs.rmSync(file);
  db.exec(`VACUUM INTO '${file.replace(/'/g, "''")}'`);
  const all = listBackups();
  for (const old of all.slice(KEEP)) fs.rmSync(path.join(backupDir(), old.name), { force: true });
  copyOffsite(file);
  return { name, bytes: fs.statSync(file).size };
}

/**
 * A backup on the same server is lost with the server: BACKUP_COPY_CMD copies each new one elsewhere,
 * e.g. `rclone copy "$BACKUP_FILE" gdrive:pumpai` or `aws s3 cp "$BACKUP_FILE" s3://my-bucket/`. The result shows in Settings → Backups.
 */
function copyOffsite(file: string) {
  const cmd = process.env.BACKUP_COPY_CMD;
  if (!cmd) return;
  execFile("sh", ["-c", cmd], { env: { ...process.env, BACKUP_FILE: file }, timeout: 15 * 60_000 }, (err, _out, errOut) => {
    setSetting(0, "offsite_last", JSON.stringify({ at: new Date().toISOString(), ok: !err, message: err ? String(errOut || err.message).slice(0, 300) : null }));
    if (err) console.error("[backup] off-site copy failed:", err.message);
  });
}
export function listBackups() {
  if (!fs.existsSync(backupDir())) return [];
  return fs.readdirSync(backupDir()).filter((f) => NAME.test(f)).map((f) => ({ name: f, bytes: fs.statSync(path.join(backupDir(), f)).size }))
    .sort((a, b) => b.name.localeCompare(a.name));
}

system.get("/backups", requirePerm("settings.manage"), h(() => ({ dir: backupDir(), backups: listBackups(), restore_pending: fs.existsSync(`${config.dbPath}.restore`), can_restart: process.env.SUPERVISED === "1",
  offsite: { configured: Boolean(process.env.BACKUP_COPY_CMD), last: (() => { try { return JSON.parse(getSetting(0, "offsite_last", "null")); } catch { return null; } })() } })));
/** Under systemd / Docker the app comes straight back after exiting — used to finish a restore from the browser. */
system.post("/system/restart", requirePerm("settings.manage"), h((req) => {
  if (process.env.SUPERVISED !== "1") throw new AppError(400, "Restart the app from the server (it is not running as a service)");
  audit(tid(req), req.user!, "app_restart", "system", {});
  setTimeout(() => { closeDb(); process.exit(0); }, 600);
  return { ok: true, message: "Restarting — the app is back in a few seconds" };
}));
system.post("/backups", requirePerm("settings.manage"), h(() => makeBackup()));
system.get("/backups/:name", requirePerm("settings.manage"), (req, res, next) => {
  try {
    if (!NAME.test(req.params.name)) throw new AppError(400, "Bad backup name");
    const file = path.join(backupDir(), req.params.name);
    if (!fs.existsSync(file)) throw new AppError(404, "Backup not found");
    res.download(file);
  } catch (e) { next(e); }
});
/** Restore is staged and swapped in at the next start, so the running database is never overwritten. */
system.post("/backups/:name/restore", requirePerm("settings.manage"), h((req) => {
  if (!NAME.test(req.params.name)) throw new AppError(400, "Bad backup name");
  const file = path.join(backupDir(), req.params.name);
  if (!fs.existsSync(file)) throw new AppError(404, "Backup not found");
  makeBackup(); // safety copy of today's data first
  fs.copyFileSync(file, `${config.dbPath}.restore`);
  return { ok: true, message: "Restart the app to finish the restore. The current data is kept as a backup." };
}));
system.delete("/backups/restore", requirePerm("settings.manage"), h(() => { fs.rmSync(`${config.dbPath}.restore`, { force: true }); return { ok: true }; }));

/* ================= Push notifications ================= */
system.get("/push/key", h((req) => ({ key: vapidPublicKey(), subscribed: subscriptionCount(req.user!.id) })));
system.post("/push/subscribe", h((req) => {
  const b = parse(z.object({ endpoint: z.string().url().max(1000), keys: z.object({ p256dh: z.string().max(200), auth: z.string().max(100) }) }), req.body);
  vapidPublicKey();
  saveSubscription(tid(req), req.user!.id, b);
  return { ok: true, subscribed: subscriptionCount(req.user!.id) };
}));
system.post("/push/unsubscribe", h((req) => {
  removeSubscription(req.user!.id, parse(z.object({ endpoint: z.string() }), req.body).endpoint);
  return { ok: true };
}));
system.post("/push/test", h(async (req) => ({ sent: await pushToUser(req.user!.id, { title: "PumpAI alerts are on ✅", body: "You will get important alerts on this device." }) })));

/* ================= Hardware (pending) & safety settings ================= */
export const HARDWARE = [
  { key: "atg", name: "Automatic tank gauge (ATG) probes", gives: "Live tank litres, water level and temperature — no manual dips; instant leak and theft alerts." },
  { key: "fcc", name: "Dispenser / forecourt controller", gives: "Every nozzle sale and totalizer straight into the POS — salesmen do not type sales or meter readings." },
  { key: "cctv", name: "CCTV with number-plate reading (ANPR)", gives: "Photo of the vehicle on every sale; khata and fleet vehicles recognised automatically." },
  { key: "gps", name: "Tanker GPS tracking", gives: "Where the tanker is, stops on the way, expected arrival — stops fuel being taken out in transit." },
  { key: "meters", name: "Generator and electricity meters", gives: "Generator hours and fuel use, electricity units — expense checks and maintenance reminders." },
];
system.get("/hardware", requirePerm("settings.manage"), h((req) => HARDWARE.map((x) => ({ ...x, status: getSetting(tid(req), `hw_${x.key}`, "pending") }))));
/** The owner marks where each piece of forecourt hardware stands: pending → planned → installed → live. */
const HW_STATUS = ["pending", "planned", "installed", "live"] as const;
system.put("/hardware/:key", requirePerm("settings.manage"), h((req) => {
  if (!HARDWARE.some((x) => x.key === req.params.key)) throw new AppError(404, "Unknown hardware");
  const b = parse(z.object({ status: z.enum(HW_STATUS) }), req.body);
  setSetting(tid(req), `hw_${req.params.key}`, b.status);
  return { ok: true, status: b.status };
}));
system.get("/safety", requirePerm("settings.manage"), h((req) => ({ price_approval: getSetting(tid(req), "price_approval", "0") === "1" })));
system.put("/safety", requirePerm("settings.manage"), h((req) => {
  const b = parse(z.object({ price_approval: z.boolean() }), req.body);
  setSetting(tid(req), "price_approval", b.price_approval ? "1" : "0");
  return { price_approval: b.price_approval };
}));
