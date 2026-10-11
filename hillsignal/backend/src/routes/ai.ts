import { Router } from "express";
import rateLimit, { ipKeyGenerator } from "express-rate-limit";
import { z } from "zod";
import { requirePlan, requireUser } from "../auth.js";
import { chat } from "../ai/chat.js";
import { consumeQuota, DISCLAIMER, mapAiError, refundQuota } from "../ai/client.js";
import { politicianInsight, tradeInsight } from "../ai/insights.js";
import { planSearch } from "../ai/search.js";
import { getPolitician } from "../data/entities.js";
import { getTrade, listTrades } from "../data/trades.js";
import { handler, notFound, parse } from "../http.js";
import { config } from "../config.js";

export const aiRouter = Router();

// Burst protection on top of the daily quotas.
aiRouter.use(
  "/ai",
  rateLimit({
    windowMs: 60_000,
    limit: config.NODE_ENV === "test" ? 1000 : 20,
    keyGenerator: (req) => req.auth.userId ?? ipKeyGenerator(req.ip ?? ""),
    standardHeaders: "draft-8",
    legacyHeaders: false,
    handler: (_req, res) => {
      res.status(429).json({ error: { code: "rate_limited", message: "Slow down a little" } });
    },
  }),
);

aiRouter.post(
  "/ai/search",
  handler(async (req, res) => {
    const userId = requireUser(req);
    const { query: q } = parse(z.object({ query: z.string().trim().min(3).max(300) }), req.body);
    const ent = req.auth.entitlements;
    const remaining = await consumeQuota(userId, "search", ent.aiSearchPerDay);
    let plan;
    try {
      plan = await planSearch(q);
    } catch (err) {
      await refundQuota(userId, "search");
      mapAiError(err);
    }
    const results = await listTrades(plan.filters, ent, { page: 1, pageSize: 25, sort: plan.sort });
    res.json({ filters: plan.filters, explanation: plan.explanation, results, remaining, disclaimer: DISCLAIMER });
  }),
);

aiRouter.get(
  "/ai/trades/:id/insight",
  handler(async (req, res) => {
    requirePlan(req, "pro", "AI insights");
    const trade = await getTrade(String(req.params.id), req.auth.entitlements);
    if (!trade) throw notFound("Trade");
    const insight = await tradeInsight(trade).catch(mapAiError);
    res.json({ insight });
  }),
);

aiRouter.get(
  "/ai/politicians/:id/insight",
  handler(async (req, res) => {
    requirePlan(req, "pro", "AI insights");
    const id = String(req.params.id);
    if (!(await getPolitician(id, req.auth.entitlements))) throw notFound("Politician");
    const insight = await politicianInsight(id).catch(mapAiError);
    if (!insight) throw notFound("Politician");
    res.json({ insight });
  }),
);

const ChatBody = z.object({
  messages: z
    .array(z.object({ role: z.enum(["user", "assistant"]), content: z.string().trim().min(1).max(4000) }))
    .min(1)
    .max(20)
    .refine((m) => m[0]!.role === "user" && m.at(-1)!.role === "user", "Conversation must start and end with a user message"),
});

aiRouter.post(
  "/ai/chat",
  handler(async (req, res) => {
    const userId = requirePlan(req, "premium", "Ask HillSignal chat");
    const { messages } = parse(ChatBody, req.body);
    await consumeQuota(userId, "chat", req.auth.entitlements.aiSearchPerDay);
    try {
      res.json(await chat(messages, req.auth.entitlements));
    } catch (err) {
      await refundQuota(userId, "chat");
      mapAiError(err);
    }
  }),
);
