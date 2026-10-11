import Anthropic from "@anthropic-ai/sdk";
import { config } from "../config.js";
import { query } from "../db/pool.js";
import { HttpError } from "../http.js";

export const DISCLAIMER =
  "AI-generated from public financial disclosures. Disclosures are filed weeks after trades and report value ranges, not exact amounts. Not investment advice.";

// Refusal fallback: if the model declines, the API retries on Anthropic's recommended model.
export const FALLBACK = { betas: ["server-side-fallback-2026-07-01"], fallbacks: "default" as const };

let client: Anthropic | null = null;

/** Tests point the client at a fake server. */
export function setAiClientForTests(c: Anthropic | null) {
  client = c;
}

export function ai(): Anthropic {
  if (client) return client;
  if (!config.ANTHROPIC_API_KEY) throw new HttpError(503, "ai_unavailable", "AI features are not configured");
  client = new Anthropic({ apiKey: config.ANTHROPIC_API_KEY, maxRetries: 2, timeout: 120_000 });
  return client;
}

export function assertNotRefused(msg: { stop_reason: string | null }) {
  if (msg.stop_reason === "refusal") {
    throw new HttpError(422, "ai_declined", "The assistant couldn't help with that request. Try rephrasing.");
  }
}

/** Map SDK errors to API errors the app understands. */
export function mapAiError(err: unknown): never {
  if (err instanceof HttpError) throw err;
  if (err instanceof Anthropic.RateLimitError) throw new HttpError(503, "ai_unavailable", "AI is busy, try again shortly");
  if (err instanceof Anthropic.APIError) throw new HttpError(503, "ai_unavailable", "AI request failed");
  throw err;
}

/** Atomically count one use against today's quota; 429 when the limit is reached. */
export async function consumeQuota(userId: string, kind: string, limit: number): Promise<number> {
  if (limit <= 0) throw new HttpError(402, "upgrade_required", "Upgrade to use this feature", { requiredPlan: "pro" });
  const rows = await query<{ count: number }>(
    `INSERT INTO ai_usage (user_id, day, kind, count) VALUES ($1, current_date, $2, 1)
     ON CONFLICT (user_id, day, kind) DO UPDATE SET count = ai_usage.count + 1
     WHERE ai_usage.count < $3
     RETURNING count`,
    [userId, kind, limit],
  );
  const used = rows[0]?.count;
  if (used === undefined) throw new HttpError(429, "quota_exceeded", `Daily limit of ${limit} reached. Resets at midnight UTC.`);
  return limit - used;
}

/** Give back a quota unit when the AI call itself failed, so users aren't charged for errors. */
export async function refundQuota(userId: string, kind: string): Promise<void> {
  await query(
    "UPDATE ai_usage SET count = greatest(count - 1, 0) WHERE user_id = $1 AND day = current_date AND kind = $2",
    [userId, kind],
  );
}

export async function cached<T>(key: string, maxAgeHours: number, produce: () => Promise<T>): Promise<T> {
  const [hit] = await query<{ value: T }>(
    "SELECT value FROM ai_cache WHERE key = $1 AND created_at > now() - make_interval(hours => $2::int)",
    [key, maxAgeHours],
  );
  if (hit) return hit.value;
  const value = await produce();
  await query(
    `INSERT INTO ai_cache (key, value) VALUES ($1, $2)
     ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, created_at = now()`,
    [key, JSON.stringify(value)],
  );
  return value;
}
