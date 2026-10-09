import "dotenv/config";
import path from "node:path";
import fs from "node:fs";
import crypto from "node:crypto";

/** A real install (not development, not the Vercel showroom). */
export const isProduction = process.env.NODE_ENV === "production" && !process.env.VERCEL;
const dbPath = process.env.DB_PATH ?? path.resolve("data/pumpai.db");

/** A secret kept next to the database (made once, mode 600), so an install is safe even if nobody set it. */
function keptSecret(name: string, make: () => string): string {
  const file = path.join(path.dirname(dbPath), `.${name}`);
  try { const v = fs.readFileSync(file, "utf8").trim(); if (v) return v; } catch { /* first start */ }
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const v = make();
  fs.writeFileSync(file, v + "\n", { mode: 0o600 });
  return v;
}
const WEAK = /change|put-a-long|dev-secret|secret-here|example/i;
function jwtSecret(): string {
  const env = process.env.JWT_SECRET?.trim();
  if (env) {
    if (isProduction && (env.length < 32 || WEAK.test(env))) {
      console.error("[config] JWT_SECRET is too short or still the example value — refusing to start. Make one with: openssl rand -hex 32");
      process.exit(1);
    }
    return env;
  }
  if (process.env.VERCEL_PROJECT_ID) return `pumpai-demo-${process.env.VERCEL_PROJECT_ID}`;
  return isProduction ? keptSecret("jwt_secret", () => crypto.randomBytes(32).toString("hex")) : "dev-secret-change-me";
}
function setupCode(): string {
  const env = process.env.SETUP_TOKEN?.trim();
  if (env && !(isProduction && /^change/i.test(env))) return env;
  // a fresh real install always asks for a code, so nobody else can claim it first
  return isProduction ? keptSecret("setup_code", () => crypto.randomBytes(4).toString("hex").toUpperCase()) : "";
}

export const config = {
  port: Number(process.env.PORT ?? 4000),
  dbPath,
  jwtSecret: jwtSecret(),
  publicUrl: process.env.PUBLIC_URL ?? (process.env.VERCEL_PROJECT_PRODUCTION_URL ? `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL}` : "http://localhost:4000"),
  // Claude AI. Without a key the app falls back to the built-in rule-based Roman Urdu engine.
  anthropicKey: process.env.ANTHROPIC_API_KEY ?? "",
  aiModel: process.env.AI_MODEL ?? "claude-opus-5-5",
  aiEffort: (process.env.AI_EFFORT ?? "low") as "low" | "medium" | "high",
  // WhatsApp Cloud API (Meta). Without these, outbound messages are stored as "simulated".
  wa: {
    token: process.env.WA_TOKEN ?? "",
    phoneNumberId: process.env.WA_PHONE_NUMBER_ID ?? "",
    verifyToken: process.env.WA_VERIFY_TOKEN ?? "pumpai-verify",
    appSecret: process.env.WA_APP_SECRET ?? "",
    templateName: process.env.WA_TEMPLATE_NAME ?? "general_update",
    templateLang: process.env.WA_TEMPLATE_LANG ?? "en",
    graphVersion: process.env.WA_GRAPH_VERSION ?? "v21.0",
  },
  // the pump's own online payment page (e.g. a bank / JazzCash merchant link); empty = no "pay online" line in messages
  paymentLinkBase: process.env.PAYMENT_LINK_BASE ?? "",
  timezone: process.env.TZ_NAME ?? "Asia/Karachi",
  schedulerEnabled: process.env.SCHEDULER !== "off",
  // first-run setup: when set, the setup wizard asks for this code (printed by the installer)
  setupToken: setupCode(),
  // load the demo pump when the database is empty (default: yes in development, no in production)
  demoData: process.env.DEMO_DATA ? process.env.DEMO_DATA === "1" : process.env.NODE_ENV !== "production",
  // who installed and supports this copy (shown in Help → About)
  vendor: { name: process.env.VENDOR_NAME ?? "", phone: process.env.VENDOR_PHONE ?? "", email: process.env.VENDOR_EMAIL ?? "" },
};

export const APP_VERSION = "1.0.0";

export const aiEnabled = () => Boolean(config.anthropicKey);
export const waLive = () => Boolean(config.wa.token && config.wa.phoneNumberId);

/**
 * Fuel products. Code → full name. This object is the single dictionary the whole app reads for
 * labels, iteration and reports; it holds EVERY product ever set up (active or hidden) so history and
 * the tally never lose one. The admin manages the list in Settings → Lists → Fuel products, and
 * products.ts rebuilds this object (and PRODUCT_META) in place whenever it changes. New records are
 * validated against the ACTIVE products only (see productSchema in products.ts).
 */
export const PRODUCTS: Record<string, string> = {
  PMG: "Petrol (Super)",
  HOBC: "Hi-Octane",
  HSD: "Diesel (HSD)",
};
/** Per-product short label, board colour and Urdu name — mutated in place alongside PRODUCTS. */
export const PRODUCT_META: Record<string, { short: string; colour: string; ur: string }> = {
  PMG: { short: "Petrol", colour: "#2a78d6", ur: "پیٹرول" },
  HOBC: { short: "Hi-Octane", colour: "#eb6834", ur: "ہائی آکٹین" },
  HSD: { short: "Diesel", colour: "#1baf7a", ur: "ڈیزل" },
};
