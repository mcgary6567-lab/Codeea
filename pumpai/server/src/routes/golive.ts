/**
 * Go-live checklist (CEO-only): the 13 things a brand-new pump should set before its first real day.
 * Each item is auto-detected from the data (done), or the owner marks it done / skips it (settings `golive:<key>` = done|skip).
 * The page does the quick ones inline (opening cash, first bank account, staff users, shift length, khata days, reorder, alerts,
 * letterhead, wholesale module) through this router; the bigger ones link to their own page.
 */
import { Router } from "express";
import { z } from "zod";
import bcrypt from "bcryptjs";
import { all, get, run, now, getSetting, setSetting } from "../db.js";
import { h, parse, tid, requirePerm, ROLES } from "../auth.js";
import * as authMod from "../auth.js";
import { AppError, audit, normalizePhone } from "../services.js";
import { config, aiProvider } from "../config.js";
import { SENSITIVITY_DEFAULTS } from "../ai/analytics.js";
import { gdriveCfg, dropboxCfg } from "../backupCloud.js";

export const golive = Router();

export const GOLIVE_KEYS = ["cash", "bank", "staff", "opening", "shift", "khata", "reorder", "alerts", "letterhead", "backup", "whatsapp_ai", "sample", "wholesale"] as const;
type Key = (typeof GOLIVE_KEYS)[number];

const mark = (t: number, k: Key) => getSetting(t, `golive:${k}`, "") as "" | "done" | "skip";
const has = (t: number, key: string) => get("SELECT 1 x FROM settings WHERE tenant_id=? AND key=?", t, key) != null;

/** What the data itself says about each item (independent of the owner's done/skip mark). */
function detect(t: number): Record<Key, { auto: boolean; info: Record<string, unknown> }> {
  const tanks = all("SELECT tk.id, tk.name, tk.product, tk.capacity_l, tk.reorder_pct, s.name station FROM tanks tk JOIN stations s ON s.id=tk.station_id WHERE s.tenant_id=? AND tk.active=1", t);
  const staffRows = all("SELECT id, name, role, phone FROM users WHERE tenant_id=? AND role<>'admin' AND active=1 ORDER BY role, id", t);
  const banks = all("SELECT id, bank, title, account_no, kind FROM bank_accounts WHERE tenant_id=? AND active=1", t);
  const lastCount = get("SELECT amount, created_at FROM cash_counts WHERE tenant_id=? ORDER BY id DESC LIMIT 1", t);
  const openKhata = get("SELECT COUNT(*) n FROM khata_ledger k JOIN customers c ON c.id=k.customer_id WHERE c.tenant_id=? AND k.ref='open:'", t)!.n as number;
  const openParties = get(`SELECT (SELECT COUNT(*) FROM suppliers WHERE tenant_id=? AND opening_balance<>0) + (SELECT COUNT(*) FROM wholesale_clients WHERE tenant_id=? AND opening_balance<>0)
    + (SELECT COUNT(*) FROM thekedars WHERE tenant_id=? AND opening_balance<>0) + (SELECT COUNT(*) FROM bank_accounts WHERE tenant_id=? AND opening_balance<>0) n`, t, t, t, t)!.n as number;
  const openShop = get("SELECT COUNT(*) n FROM shop_moves m JOIN shop_items i ON i.id=m.item_id WHERE i.tenant_id=? AND m.type='book'", t)!.n as number;
  const sens = Object.fromEntries((Object.keys(SENSITIVITY_DEFAULTS) as (keyof typeof SENSITIVITY_DEFAULTS)[]).map((k) => [k, Number(getSetting(t, `sens_${k}`, String(SENSITIVITY_DEFAULTS[k])))]));
  const SAAS = Boolean((config as { saas?: boolean }).saas);
  const demo = Boolean(get("SELECT id FROM users WHERE email='admin@pumpai.pk' AND password_hash IS NOT NULL"));
  const cloud = Boolean(gdriveCfg() || dropboxCfg());
  return {
    cash: { auto: Boolean(lastCount), info: { last: lastCount ?? null } },
    bank: { auto: banks.length > 0, info: { accounts: banks } },
    staff: { auto: staffRows.length > 0, info: { users: staffRows, roles: ROLES.filter((r) => r !== "admin") } },
    opening: { auto: openKhata + openParties + openShop > 0, info: { khata_rows: openKhata, parties: openParties, shop_bookings: openShop } },
    shift: { auto: has(t, "shift_hours"), info: { shift_hours: Number(getSetting(t, "shift_hours", "24")) } },
    khata: { auto: has(t, "khata_block_days"), info: { block_days: Number(getSetting(t, "khata_block_days", "60")), auto_block: getSetting(t, "khata_auto_block", "1") === "1", khata_discount_max: Number(getSetting(t, "khata_discount_max", "500")) } },
    reorder: { auto: tanks.some((k) => Number(k.reorder_pct) !== 25), info: { tanks: tanks.map((k) => ({ ...k, reorder_l: Math.round((k.capacity_l * k.reorder_pct) / 100) })) } },
    alerts: { auto: Object.keys(SENSITIVITY_DEFAULTS).some((k) => has(t, `sens_${k}`)), info: { sensitivity: sens, defaults: SENSITIVITY_DEFAULTS } },
    letterhead: { auto: Boolean(getSetting(t, "biz_address", "") && getSetting(t, "biz_phone", "")), info: { biz_phone: getSetting(t, "biz_phone", ""), biz_address: getSetting(t, "biz_address", ""), receipt_footer: getSetting(t, "receipt_footer", ""), facebook: getSetting(t, "facebook", ""), instagram: getSetting(t, "instagram", ""), whatsapp: getSetting(t, "whatsapp", "") } },
    backup: { auto: cloud, info: { cloud, saas: SAAS } },
    whatsapp_ai: { auto: Boolean(config.wa.token) || aiProvider() !== "none", info: { whatsapp: Boolean(config.wa.token), ai: aiProvider(), saas: SAAS } },
    sample: { auto: false, info: { available: demo } },
    wholesale: { auto: has(t, "module_wholesale"), info: { on: getSetting(t, "module_wholesale", "0") === "1" } },
  };
}

export function goliveState(t: number) {
  const d = detect(t);
  const items = GOLIVE_KEYS.map((key) => {
    const m = mark(t, key);
    const status = m === "skip" ? "skipped" : m === "done" || d[key].auto ? "done" : "pending";
    return { key, status, auto: d[key].auto, mark: m, ...d[key].info };
  });
  const pending = items.filter((i) => i.status === "pending").length;
  return { items, pending, done: items.filter((i) => i.status === "done").length, skipped: items.filter((i) => i.status === "skipped").length, dismissed: getSetting(t, "golive:dismissed", "") === "1" };
}

golive.get("/golive", requirePerm("settings.manage"), h((req) => goliveState(tid(req))));

const keyParam = z.enum(GOLIVE_KEYS);
golive.post("/golive/:key/skip", requirePerm("settings.manage"), h((req) => { const k = keyParam.parse(req.params.key); setSetting(tid(req), `golive:${k}`, "skip"); audit(tid(req), req.user!, "golive_skip", `golive:${k}`, {}); return goliveState(tid(req)); }));
golive.post("/golive/:key/done", requirePerm("settings.manage"), h((req) => { const k = keyParam.parse(req.params.key); setSetting(tid(req), `golive:${k}`, "done"); return goliveState(tid(req)); }));
golive.post("/golive/:key/reset", requirePerm("settings.manage"), h((req) => { const k = keyParam.parse(req.params.key); setSetting(tid(req), `golive:${k}`, ""); return goliveState(tid(req)); }));
golive.post("/golive/dismiss", requirePerm("settings.manage"), h((req) => { setSetting(tid(req), "golive:dismissed", req.body?.on === false ? "" : "1"); return goliveState(tid(req)); }));

/** Quick staff users in one go: [{name, role, phone?, pin?}] → email auto (<slug>@<tenant>.pump) + a random password; they sign in with the PIN. */
golive.post("/golive/staff", requirePerm("users.manage"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({ users: z.array(z.object({ name: z.string().trim().min(2).max(80), role: z.enum(ROLES.filter((r) => r !== "admin") as [string, ...string[]]),
    phone: z.string().max(16).optional().nullable(), pin: z.string().regex(/^\d{4}$/, "PIN 4 hindsay").optional().or(z.literal("")), station_id: z.number().int().optional().nullable() })).min(1).max(20) }), req.body);
  const station = get("SELECT id FROM stations WHERE tenant_id=? ORDER BY id LIMIT 1", t)?.id as number | undefined;
  const made: { id: number; name: string; role: string; email: string }[] = [];
  for (const u of b.users) {
    const slug = u.name.toLowerCase().replace(/[^a-z0-9]+/g, ".").replace(/^\.|\.$/g, "") || "staff";
    let email = `${slug}@pump${t}.local`, n = 1;
    while (get("SELECT 1 x FROM users WHERE email=?", email)) email = `${slug}${++n}@pump${t}.local`;
    const pwd = Math.random().toString(36).slice(2, 10) + Math.random().toString(36).slice(2, 6);
    const sid = u.role === "salesman" ? (u.station_id ?? station ?? null) : (u.station_id ?? null);
    const id = run("INSERT INTO users (tenant_id,name,email,password_hash,role,station_id,phone,pin_hash,duty_start) VALUES (?,?,?,?,?,?,?,?,?)",
      t, u.name, email, bcrypt.hashSync(pwd, 10), u.role, sid, u.phone ? normalizePhone(u.phone) : null, u.pin ? bcrypt.hashSync(u.pin, 10) : null, u.role === "salesman" ? "08:00" : null).id;
    made.push({ id, name: u.name, role: u.role, email });
  }
  audit(t, req.user!, "golive_staff", "users", { made });
  return { made, state: goliveState(t) };
}));

/** Reorder level per tank, typed in litres (stored as % of capacity, which every alert already reads). */
golive.post("/golive/reorder", requirePerm("stock.manage"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({ tanks: z.array(z.object({ id: z.number().int(), reorder_l: z.number().min(0) })).min(1) }), req.body);
  for (const x of b.tanks) {
    const tk = get("SELECT tk.id, tk.capacity_l FROM tanks tk JOIN stations s ON s.id=tk.station_id WHERE tk.id=? AND s.tenant_id=?", x.id, t);
    if (!tk) throw new AppError(404, "Tank nahi mila");
    const pct = Math.min(80, Math.max(5, Math.round((x.reorder_l / tk.capacity_l) * 1000) / 10));
    run("UPDATE tanks SET reorder_pct=? WHERE id=?", pct, tk.id);
  }
  setSetting(t, "golive:reorder", "done");
  return goliveState(t);
}));

/** Wholesale & carriage module on/off — the same choice the signup wizard asked. */
golive.post("/golive/wholesale", requirePerm("settings.manage"), h((req) => {
  const t = tid(req);
  const { on } = parse(z.object({ on: z.boolean() }), req.body);
  setSetting(t, "module_wholesale", on ? "1" : "0");
  (authMod as { forgetModules?: (t: number) => void }).forgetModules?.(t); // SaaS caches the module flag per tenant
  audit(t, req.user!, "module_wholesale", "settings", { on });
  return goliveState(t);
}));

/** Letterhead / footer bits in one call (the full form lives in Settings → Business). */
golive.post("/golive/letterhead", requirePerm("settings.manage"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({ biz_phone: z.string().max(20).optional(), biz_address: z.string().max(200).optional(), receipt_footer: z.string().max(160).optional(),
    facebook: z.string().max(120).optional(), instagram: z.string().max(120).optional(), whatsapp: z.string().max(120).optional() }), req.body);
  for (const [k, v] of Object.entries(b)) if (v !== undefined) setSetting(t, k, v.trim());
  setSetting(t, "golive:letterhead", "done");
  return goliveState(t);
}));

/** Defaults a brand-new pump starts with (called from provisionTenant). Explicit rows, so the owner sees them as "set" and can change them. */
export function newPumpDefaults(t: number, o: { shift_hours?: number } = {}) {
  setSetting(t, "shift_hours", String(o.shift_hours ?? 12)); // most pumps run two 12-hour shifts
  setSetting(t, "khata_block_days", "30"); // hold a khata after 30 days without payment (60 was too soft for a new pump)
  setSetting(t, "khata_auto_block", "1");
  setSetting(t, "opening_date", now().slice(0, 10));
}
