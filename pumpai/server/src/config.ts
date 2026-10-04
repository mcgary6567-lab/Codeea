import "dotenv/config";
import path from "node:path";

export const config = {
  port: Number(process.env.PORT ?? 4000),
  dbPath: process.env.DB_PATH ?? path.resolve("data/pumpai.db"),
  jwtSecret: process.env.JWT_SECRET ?? "dev-secret-change-me",
  publicUrl: process.env.PUBLIC_URL ?? "http://localhost:4000",
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
};

export const aiEnabled = () => Boolean(config.anthropicKey);
export const waLive = () => Boolean(config.wa.token && config.wa.phoneNumberId);

export const PRODUCTS: Record<string, string> = {
  PMG: "Petrol (Super)",
  HOBC: "Hi-Octane",
  HSD: "Diesel (HSD)",
};
