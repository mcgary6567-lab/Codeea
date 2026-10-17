/**
 * Phone / desktop push notifications (Web Push) for staff, on top of in-app and WhatsApp.
 * VAPID keys come from VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY, or are made once and kept in the database.
 */
import webpush from "web-push";
import { all, get, run, now, getSetting, setSetting, db } from "./db.js";
import { config } from "./config.js";

let ready = false;
export function vapidPublicKey(): string {
  let pub = process.env.VAPID_PUBLIC_KEY ?? getSetting(0, "vapid_public");
  let priv = process.env.VAPID_PRIVATE_KEY ?? getSetting(0, "vapid_private");
  if (!pub || !priv) {
    const k = webpush.generateVAPIDKeys();
    setSetting(0, "vapid_public", k.publicKey); setSetting(0, "vapid_private", k.privateKey);
    pub = k.publicKey; priv = k.privateKey;
  }
  if (!ready) { webpush.setVapidDetails(`mailto:alerts@${new URL(config.publicUrl).hostname || "pumpai.pk"}`, pub, priv); ready = true; }
  return pub;
}

export function saveSubscription(tenantId: number, userId: number, sub: { endpoint: string; keys: { p256dh: string; auth: string } }) {
  db.exec(`CREATE TABLE IF NOT EXISTS push_subs (id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, user_id INTEGER NOT NULL, endpoint TEXT NOT NULL UNIQUE, p256dh TEXT NOT NULL, auth TEXT NOT NULL, created_at TEXT NOT NULL)`);
  run(`INSERT INTO push_subs (tenant_id,user_id,endpoint,p256dh,auth,created_at) VALUES (?,?,?,?,?,?)
       ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id, p256dh=excluded.p256dh, auth=excluded.auth`, tenantId, userId, sub.endpoint, sub.keys.p256dh, sub.keys.auth, now());
}
export function removeSubscription(userId: number, endpoint: string) {
  if (get("SELECT name FROM sqlite_master WHERE name='push_subs'")) run("DELETE FROM push_subs WHERE user_id=? AND endpoint=?", userId, endpoint);
}
export const subscriptionCount = (userId: number) =>
  get("SELECT name FROM sqlite_master WHERE name='push_subs'") ? get("SELECT COUNT(*) n FROM push_subs WHERE user_id=?", userId)!.n as number : 0;

/** Fire-and-forget push to every device of a user; dead subscriptions are removed. */
export async function pushToUser(userId: number, msg: { title: string; body?: string; url?: string; tag?: string }) {
  if (!get("SELECT name FROM sqlite_master WHERE name='push_subs'")) return 0;
  const subs = all("SELECT * FROM push_subs WHERE user_id=?", userId);
  if (!subs.length) return 0;
  vapidPublicKey();
  let sent = 0;
  await Promise.all(subs.map(async (s) => {
    try {
      await webpush.sendNotification({ endpoint: s.endpoint, keys: { p256dh: s.p256dh, auth: s.auth } }, JSON.stringify(msg), { TTL: 3600 });
      sent++;
    } catch (e: any) {
      if (e?.statusCode === 404 || e?.statusCode === 410) run("DELETE FROM push_subs WHERE id=?", s.id);
    }
  }));
  return sent;
}
