import "dotenv/config";
import path from "node:path";

export const config = {
  port: Number(process.env.PORT ?? 4000),
  dbPath: process.env.DB_PATH ?? path.resolve("data/pumpai.db"),
  jwtSecret: process.env.JWT_SECRET ?? (process.env.VERCEL_PROJECT_ID ? `pumpai-demo-${process.env.VERCEL_PROJECT_ID}` : "dev-secret-change-me"),
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
  paymentLinkBase: process.env.PAYMENT_LINK_BASE ?? "https://pay.example.pk/pumpai",
  timezone: process.env.TZ_NAME ?? "Asia/Karachi",
  schedulerEnabled: process.env.SCHEDULER !== "off",
  // first-run setup: when set, the setup wizard asks for this code (printed by the installer)
  setupToken: process.env.SETUP_TOKEN ?? "",
  // load the demo pump when the database is empty (default: yes in development, no in production)
  demoData: process.env.DEMO_DATA ? process.env.DEMO_DATA === "1" : process.env.NODE_ENV !== "production",
  // who installed and supports this copy (shown in Help → About)
  vendor: { name: process.env.VENDOR_NAME ?? "", phone: process.env.VENDOR_PHONE ?? "", email: process.env.VENDOR_EMAIL ?? "" },
};

export const APP_VERSION = "1.0.0";

export const aiEnabled = () => Boolean(config.anthropicKey);
export const waLive = () => Boolean(config.wa.token && config.wa.phoneNumberId);

export const PRODUCTS: Record<string, string> = {
  PMG: "Petrol (Super)",
  HOBC: "Hi-Octane",
  HSD: "Diesel (HSD)",
};
