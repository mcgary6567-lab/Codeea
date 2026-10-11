import { Router } from "express";
import { z } from "zod";
import { requireUser } from "../auth.js";
import { getIssuer, getPolitician, listIssuers, listPoliticians } from "../data/entities.js";
import { feedTrades } from "../data/trades.js";
import { query } from "../db/pool.js";
import { handler, notFound, pageQuery, parse, upgradeRequired } from "../http.js";

export const watchlistRouter = Router();

const Kind = z.enum(["politician", "issuer"]);

watchlistRouter.get(
  "/watchlist",
  handler(async (req, res) => {
    const userId = requireUser(req);
    const rows = await query<{ kind: "politician" | "issuer"; target_id: string }>(
      "SELECT kind, target_id FROM watchlist WHERE user_id = $1 ORDER BY created_at DESC",
      [userId],
    );
    const ids = (k: string) => rows.filter((r) => r.kind === k).map((r) => r.target_id);
    const ent = req.auth.entitlements;
    const [politicians, issuers] = await Promise.all([
      ids("politician").length ? listPoliticians({ ids: ids("politician") }, ent, { page: 1, pageSize: 100, sort: "name" }) : null,
      ids("issuer").length ? listIssuers({ ids: ids("issuer") }, ent, { page: 1, pageSize: 100 }) : null,
    ]);
    res.json({ politicians: politicians?.data ?? [], issuers: issuers?.data ?? [] });
  }),
);

watchlistRouter.post(
  "/watchlist",
  handler(async (req, res) => {
    const userId = requireUser(req);
    const { kind, targetId } = parse(z.object({ kind: Kind, targetId: z.string().min(1).max(64) }), req.body);
    const ent = req.auth.entitlements;
    const exists = kind === "politician" ? await getPolitician(targetId, ent) : await getIssuer(targetId, ent);
    if (!exists) throw notFound(kind === "politician" ? "Politician" : "Issuer");

    const [{ n } = { n: 0 }] = await query<{ n: number }>(
      "SELECT count(*)::int AS n FROM watchlist WHERE user_id = $1",
      [userId],
    );
    const [already] = await query("SELECT 1 FROM watchlist WHERE user_id = $1 AND kind = $2 AND target_id = $3", [userId, kind, targetId]);
    if (!already && n >= ent.watchlistLimit) {
      throw upgradeRequired(ent.plan === "free" ? "pro" : "premium", `Following more than ${ent.watchlistLimit}`);
    }
    await query(
      "INSERT INTO watchlist (user_id, kind, target_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
      [userId, kind, targetId],
    );
    res.status(201).json({ ok: true });
  }),
);

watchlistRouter.delete(
  "/watchlist/:kind/:targetId",
  handler(async (req, res) => {
    const userId = requireUser(req);
    const kind = parse(Kind, req.params.kind);
    await query("DELETE FROM watchlist WHERE user_id = $1 AND kind = $2 AND target_id = $3", [userId, kind, req.params.targetId]);
    res.status(204).end();
  }),
);

watchlistRouter.get(
  "/feed",
  handler(async (req, res) => {
    const userId = requireUser(req);
    const { page, pageSize } = parse(z.object(pageQuery), req.query);
    res.json(await feedTrades(userId, req.auth.entitlements, { page, pageSize }));
  }),
);

watchlistRouter.post(
  "/devices",
  handler(async (req, res) => {
    const userId = requireUser(req);
    const body = parse(
      z.object({
        expoPushToken: z.string().regex(/^Expo(nent)?PushToken\[.+\]$/, "Not an Expo push token"),
        platform: z.enum(["android", "ios"]),
      }),
      req.body,
    );
    // A device belongs to whoever signed in on it last.
    await query(
      `INSERT INTO devices (push_token, user_id, platform) VALUES ($1, $2, $3)
       ON CONFLICT (push_token) DO UPDATE SET user_id = EXCLUDED.user_id, platform = EXCLUDED.platform, updated_at = now()`,
      [body.expoPushToken, userId, body.platform],
    );
    res.status(201).json({ ok: true });
  }),
);
