import crypto from "node:crypto";
/**
 * Selling and installing PumpAI on a pump owner's own server:
 *  - First-run setup wizard (only while the database is empty): business details and logo, the owner's admin
 *    account, stations with tanks and nozzles, today's prices — then the pump is ready to use
 *  - Business profile and branding (logo, colour) used on the app, receipts, bills, the TV board and slips
 *  - Integrations (Claude AI key, WhatsApp Cloud API, public address) set from the app, no file editing
 */
import { Router } from "express";
import bcrypt from "bcryptjs";
import { z } from "zod";
import { config, APP_VERSION, PRODUCTS } from "../config.js";
import { get, run, now, tx, getSetting, setSetting } from "../db.js";
import { h, parse, tid, requirePerm, signToken } from "../auth.js";
import { AppError, normalizePhone, audit } from "../services.js";
import { ensureCategories } from "./expenses.js";
import { ensureAutomations } from "../automation/scheduler.js";
import { addDefaultChecklist } from "./compliance.js";

export const setupPublic = Router(); // no login
export const business = Router(); // logged in

const firstTenant = () => get("SELECT * FROM tenants ORDER BY id LIMIT 1");
const dataUrl = z.string().regex(/^data:image\/(jpeg|png|webp);base64,/, "Logo must be a JPEG, PNG or WebP image").max(1_500_000);
const hex = z.string().regex(/^#[0-9a-fA-F]{6}$/, "Colour like #059669");

/* ================= Branding ================= */
const PROFILE_KEYS = ["biz_phone", "biz_email", "biz_address", "biz_city", "website", "ntn", "strn", "brand_color", "receipt_footer", "omc",
  "facebook", "instagram", "whatsapp", "tiktok", "youtube", "twitter"] as const;
export function profile(t: number) {
  const tenant = get("SELECT * FROM tenants WHERE id=?", t)!;
  const first = get("SELECT address, city FROM stations WHERE tenant_id=? ORDER BY id LIMIT 1", t);
  return {
    name: tenant.name, owner_name: tenant.owner_name, owner_phone: getSetting(t, "owner_phone", tenant.owner_phone ?? ""),
    ...Object.fromEntries(PROFILE_KEYS.map((k) => [k, getSetting(t, k)])),
    logo_url: getSetting(t, "logo_photo_id") ? `/branding/logo?v=${getSetting(t, "logo_photo_id")}` : null,
    // where the pump is, for letterheads: the business address, else the first station's
    place: [getSetting(t, "biz_address"), getSetting(t, "biz_city")].filter(Boolean).join(", ")
      || [first?.address, first?.city].filter(Boolean).join(", ") || null,
  } as Record<string, string | null>;
}
function saveLogo(t: number, image: string) {
  const [, mime, data] = image.match(/^data:(image\/[a-z]+);base64,(.*)$/s)!;
  const id = run("INSERT INTO photos (tenant_id,kind,ref,mime,data,created_at) VALUES (?,?,?,?,?,?)", t, "logo", "logo", mime, Buffer.from(data, "base64"), now()).id;
  setSetting(t, "logo_photo_id", String(id));
}
/** <img> for the HTML pages we send to customers (bill, receipt, portal, board). */
export function logoTag(t: number, style = "height:44px;max-width:160px;object-fit:contain") {
  return getSetting(t, "logo_photo_id") ? `<img src="${config.publicUrl}/branding/logo" alt="" style="${style}">` : "";
}
/** The logo as a JPEG (for the PDF slip), when the logo is a JPEG. */
export function logoJpeg(t: number): Buffer | null {
  const id = Number(getSetting(t, "logo_photo_id"));
  const p = id ? get("SELECT mime, data FROM photos WHERE id=?", id) : null;
  return p && p.mime === "image/jpeg" ? Buffer.from(p.data as Uint8Array) : null;
}

setupPublic.get("/branding", h(() => {
  const t = firstTenant();
  return {
    setup_needed: !t, version: APP_VERSION, vendor: config.vendor,
    // the demo pump (sample logins shown on the sign-in page); never true on a real installation
    demo: Boolean(get("SELECT id FROM users WHERE email='admin@pumpai.pk' AND password_hash IS NOT NULL")),
    name: t?.name ?? "PumpAI", color: t ? getSetting(t.id, "brand_color") || null : null,
    logo_url: t && getSetting(t.id, "logo_photo_id") ? `/branding/logo?v=${getSetting(t.id, "logo_photo_id")}` : null,
  };
}));

/* ================= Setup wizard ================= */
setupPublic.get("/setup/status", h(() => ({ needed: !firstTenant(), needs_code: Boolean(config.setupToken), version: APP_VERSION, vendor: config.vendor, products: PRODUCTS })));

const product = z.enum(Object.keys(PRODUCTS) as [string, ...string[]]);
/** One dispenser meter (nozzle) on a tank: its number on the forecourt, a name, and the reading on it right now. */
const meter = z.object({
  meter_no: z.number().int().min(1).max(99).optional(), label: z.string().trim().max(30).optional(),
  totalizer: z.number().min(0).max(99_999_999).default(0), // the totalizer reading today — the first shift opens from here
});
const setupBody = z.object({
  code: z.string().optional(),
  business: z.object({
    name: z.string().min(2).max(80), owner_name: z.string().min(2).max(80), owner_phone: z.string().min(10).max(16),
    biz_phone: z.string().max(20).optional(), biz_email: z.string().email().optional().or(z.literal("")), biz_address: z.string().max(200).optional(), biz_city: z.string().max(60).optional(),
    website: z.string().max(100).optional(), ntn: z.string().max(20).optional(), strn: z.string().max(20).optional(), omc: z.string().max(40).optional(),
    brand_color: hex.optional(), receipt_footer: z.string().max(160).optional(), logo: dataUrl.optional().nullable(),
    // social pages printed at the bottom of statements, bills and receipts (a link or just the handle)
    facebook: z.string().max(120).optional(), instagram: z.string().max(120).optional(), whatsapp: z.string().max(120).optional(),
    tiktok: z.string().max(120).optional(), youtube: z.string().max(120).optional(), twitter: z.string().max(120).optional(),
  }),
  admin: z.object({ name: z.string().min(2).max(80), email: z.string().email(), password: z.string().min(8, "Password must be at least 8 characters"), pin: z.string().regex(/^\d{4}$/).optional().or(z.literal("")), phone: z.string().max(16).optional() }),
  stations: z.array(z.object({
    name: z.string().min(2).max(80), city: z.string().max(60).optional(), address: z.string().max(200).optional(), omc: z.string().max(40).optional(),
    timings: z.string().max(60).optional(), services: z.string().max(200).optional(), lat: z.number().optional().nullable(), lng: z.number().optional().nullable(),
    tanks: z.array(z.object({
      name: z.string().min(1).max(60), product, capacity_l: z.number().positive().max(500_000), current_l: z.number().min(0),
      // how many meters draw from this tank — a plain count, or one entry per meter with its number, name and today's reading
      nozzles: z.union([z.number().int().min(0).max(12), z.array(meter).max(12)]).default(2),
    })).min(1, "Add at least one tank"),
  })).min(1, "Add at least one station").max(20),
  prices: z.record(product, z.number().positive().max(2000)),
});

setupPublic.post("/setup", h((req) => {
  if (firstTenant()) throw new AppError(409, "This pump is already set up. Sign in instead.");
  const b = parse(setupBody, req.body);
  if (config.setupToken) {
    const want = Buffer.from(config.setupToken.trim().toUpperCase()), got = Buffer.from((b.code ?? "").trim().toUpperCase());
    if (want.length !== got.length || !crypto.timingSafeEqual(want, got)) throw new AppError(403, "Setup code is not correct (the installer printed it)");
  }
  const used = new Set(b.stations.flatMap((s) => s.tanks.map((t) => t.product)));
  for (const p of used) if (!b.prices[p]) throw new AppError(400, `Enter today's price for ${PRODUCTS[p]}`);
  const ts = now();
  const { tenantId, adminId } = tx(() => {
    const tenantId = run("INSERT INTO tenants (name, owner_name, owner_phone) VALUES (?,?,?)", b.business.name, b.business.owner_name, normalizePhone(b.business.owner_phone)).id;
    setSetting(tenantId, "owner_phone", normalizePhone(b.business.owner_phone));
    for (const k of PROFILE_KEYS) if (b.business[k]) setSetting(tenantId, k, String(b.business[k]));
    if (b.business.ntn) setSetting(tenantId, "ntn", b.business.ntn);
    if (b.business.logo) saveLogo(tenantId, b.business.logo);
    const adminId = run("INSERT INTO users (tenant_id,name,email,password_hash,role,phone,pin_hash) VALUES (?,?,?,?,?,?,?)",
      tenantId, b.admin.name, b.admin.email.toLowerCase(), bcrypt.hashSync(b.admin.password, 10), "admin", b.admin.phone ? normalizePhone(b.admin.phone) : null,
      b.admin.pin ? bcrypt.hashSync(b.admin.pin, 10) : null).id;
    for (const s of b.stations) {
      const sid = run("INSERT INTO stations (tenant_id,name,city,address,omc,lat,lng,timings,services) VALUES (?,?,?,?,?,?,?,?,?)",
        tenantId, s.name, s.city ?? b.business.biz_city ?? null, s.address ?? null, s.omc ?? b.business.omc ?? null, s.lat ?? null, s.lng ?? null, s.timings ?? "24 hours", s.services ?? null).id;
      s.tanks.forEach((tk) => {
        if (tk.current_l > tk.capacity_l) throw new AppError(400, `${s.name} ${tk.name}: stock is more than the capacity`);
        const tankId = run("INSERT INTO tanks (station_id,name,product,capacity_l,current_l,reorder_pct) VALUES (?,?,?,?,?,25)", sid, tk.name, tk.product, tk.capacity_l, tk.current_l).id;
        const meters = typeof tk.nozzles === "number" ? Array.from({ length: tk.nozzles }, () => ({} as z.infer<typeof meter>)) : tk.nozzles;
        meters.forEach((m, i) => run("INSERT INTO nozzles (station_id,tank_id,label,totalizer,meter_no) VALUES (?,?,?,?,?)",
          sid, tankId, m.label || `${tk.product}-${i + 1}`, m.totalizer ?? 0, m.meter_no ?? null)); // meter_no left null → numbered in order by the trigger
      });
    }
    for (const [p, price] of Object.entries(b.prices)) if (used.has(p)) run("INSERT INTO prices (tenant_id,product,price,effective_from,created_by) VALUES (?,?,?,?,?)", tenantId, p, price, ts, b.admin.name);
    ensureCategories(tenantId);
    ensureAutomations(tenantId);
    addDefaultChecklist(tenantId);
    setSetting(tenantId, "installed_at", ts);
    return { tenantId, adminId };
  });
  audit(tenantId, { id: adminId, name: b.admin.name }, "setup", "tenant", { business: b.business.name, stations: b.stations.length });
  const user = get("SELECT id, tenant_id, name, email, role, station_id FROM users WHERE id=?", adminId)!;
  return { token: signToken(user), user };
}));

/* ================= Business profile (after setup) ================= */
business.get("/business", h((req) => profile(tid(req))));
business.put("/business", requirePerm("settings.manage"), h((req) => {
  const t = tid(req);
  const b = parse(setupBody.shape.business.partial().extend({ logo: dataUrl.optional().nullable(), remove_logo: z.boolean().optional() }), req.body);
  if (b.name) run("UPDATE tenants SET name=? WHERE id=?", b.name, t);
  if (b.owner_name) run("UPDATE tenants SET owner_name=? WHERE id=?", b.owner_name, t);
  if (b.owner_phone) { run("UPDATE tenants SET owner_phone=? WHERE id=?", normalizePhone(b.owner_phone), t); setSetting(t, "owner_phone", normalizePhone(b.owner_phone)); }
  for (const k of PROFILE_KEYS) if (b[k] !== undefined) setSetting(t, k, String(b[k] ?? ""));
  if (b.logo) saveLogo(t, b.logo);
  if (b.remove_logo) setSetting(t, "logo_photo_id", "");
  return profile(t);
}));

/* ================= Integrations stored in the app ================= */
const CFG: Record<string, { set: (v: string) => void; secret?: boolean }> = {
  anthropic_key: { set: (v) => { config.anthropicKey = v; }, secret: true },
  ai_model: { set: (v) => { config.aiModel = v || "claude-opus-5-5"; } },
  wa_token: { set: (v) => { config.wa.token = v; }, secret: true },
  wa_phone_number_id: { set: (v) => { config.wa.phoneNumberId = v; } },
  wa_verify_token: { set: (v) => { config.wa.verifyToken = v || "pumpai-verify"; } },
  wa_app_secret: { set: (v) => { config.wa.appSecret = v; }, secret: true },
  wa_template: { set: (v) => { config.wa.templateName = v || "general_update"; } },
  public_url: { set: (v) => { config.publicUrl = v.replace(/\/$/, ""); } },
  payment_link_base: { set: (v) => { config.paymentLinkBase = v; } },
};
/** Values saved from Settings → Integrations win over the .env file (tenant 0 = whole install). */
export function applyStoredConfig() {
  for (const [k, c] of Object.entries(CFG)) {
    const v = getSetting(0, `cfg:${k}`, "\u0000");
    if (v !== "\u0000") c.set(v);
  }
}
const mask = (v: string) => (v ? `${"•".repeat(8)}${v.slice(-4)}` : "");
business.get("/integrations", requirePerm("settings.manage"), h(() => ({
  values: {
    anthropic_key: mask(config.anthropicKey), ai_model: config.aiModel, wa_token: mask(config.wa.token), wa_phone_number_id: config.wa.phoneNumberId,
    wa_verify_token: config.wa.verifyToken, wa_app_secret: mask(config.wa.appSecret), wa_template: config.wa.templateName, public_url: config.publicUrl, payment_link_base: config.paymentLinkBase,
  },
  webhook_url: `${config.publicUrl}/webhooks/whatsapp`,
  version: APP_VERSION, vendor: config.vendor, installed_at: null as string | null,
})));
business.put("/integrations", requirePerm("settings.manage"), h((req) => {
  const b = parse(z.record(z.enum(Object.keys(CFG) as [string, ...string[]]), z.string().max(400)), req.body);
  // a live WhatsApp number must have its app secret, or anyone could post fake messages to the webhook
  const token = b.wa_token !== undefined && !b.wa_token.startsWith("•") ? b.wa_token.trim() : config.wa.token;
  const secret = b.wa_app_secret !== undefined && !b.wa_app_secret.startsWith("•") ? b.wa_app_secret.trim() : config.wa.appSecret;
  if (token && !secret) throw new AppError(400, "Add the WhatsApp App Secret too (Meta app → Settings → Basic) — it proves messages really come from WhatsApp");
  const changed: string[] = [];
  for (const [k, v] of Object.entries(b)) {
    if (CFG[k].secret && v.startsWith("•")) continue; // unchanged masked value
    if (k === "public_url" && v && !/^https?:\/\/[^\s/]+/.test(v)) throw new AppError(400, "Public address must start with https://");
    setSetting(0, `cfg:${k}`, v.trim());
    CFG[k].set(v.trim());
    changed.push(k);
  }
  audit(tid(req), req.user!, "integrations_changed", "settings", { changed });
  return { ok: true, changed };
}));
