import "dotenv/config";
import { z } from "zod";

const Env = z.object({
  NODE_ENV: z.enum(["development", "test", "production"]).default("development"),
  PORT: z.coerce.number().default(4000),
  APP_NAME: z.string().default("HillSignal"),
  DATABASE_URL: z.string().default("postgres://hillsignal:hillsignal@localhost:5432/hillsignal"),
  JWT_SECRET: z.string().min(32).default("dev-only-secret-change-me-dev-only-secret"),
  CORS_ORIGINS: z.string().default("*"),

  // AI (Claude). Leave ANTHROPIC_API_KEY unset to disable AI routes (they return 503).
  ANTHROPIC_API_KEY: z.string().optional(),
  AI_MODEL: z.string().default("claude-opus-5-5"),

  // Free plan limits
  FREE_DELAY_DAYS: z.coerce.number().default(3),
  FREE_HISTORY_DAYS: z.coerce.number().default(365),

  // Google Play Billing
  GOOGLE_PLAY_PACKAGE_NAME: z.string().default("com.hillsignal.app"),
  GOOGLE_SERVICE_ACCOUNT_JSON: z.string().optional(), // the JSON key itself, not a path
  RTDN_PUSH_AUDIENCE: z.string().optional(), // Pub/Sub push OIDC audience (recommended)
  RTDN_SHARED_SECRET: z.string().optional(), // fallback: ?secret=... on the push URL

  // Run ingestion inside the API process every N minutes (0 = off; use `npm run ingest` from cron instead).
  INGEST_INTERVAL_MINUTES: z.coerce.number().default(0),

  // Push alerts via Expo
  EXPO_ACCESS_TOKEN: z.string().optional(),
});

const parsed = Env.safeParse(process.env);
if (!parsed.success) {
  console.error("Invalid environment:", z.prettifyError(parsed.error));
  process.exit(1);
}

export const config = parsed.data;

if (config.NODE_ENV === "production" && config.JWT_SECRET.startsWith("dev-only")) {
  console.error("JWT_SECRET must be set in production");
  process.exit(1);
}
