import { Router } from "express";
import { z } from "zod";
import { buildAuthContext, publicUser, requireUser, type UserRow } from "../auth.js";
import { decodeRtdn, verifyAndStore, verifyPubSubPush } from "../billing/googlePlay.js";
import { query } from "../db/pool.js";
import { handler, HttpError, parse } from "../http.js";
import { log } from "../log.js";
import { GOOGLE_PRODUCTS } from "../plans.js";

export const billingRouter = Router();

billingRouter.post(
  "/billing/google/verify",
  handler(async (req, res) => {
    const userId = requireUser(req);
    const { productId, purchaseToken } = parse(
      z.object({
        productId: z.string().refine((p) => p in GOOGLE_PRODUCTS, "Unknown product"),
        purchaseToken: z.string().min(10).max(4096),
      }),
      req.body,
    );
    await verifyAndStore(userId, productId, purchaseToken);
    const ctx = await buildAuthContext(userId);
    const [user] = await query<UserRow>(
      "SELECT id, email, name, granted_plan, granted_until, created_at FROM users WHERE id = $1",
      [userId],
    );
    res.json({ user: publicUser(user!, ctx), entitlements: ctx.entitlements });
  }),
);

// Google Play Real-time Developer Notifications via Cloud Pub/Sub push.
// Always 2xx for messages we can't use, so Pub/Sub doesn't redeliver them forever.
billingRouter.post(
  "/billing/google/rtdn",
  handler(async (req, res) => {
    if (!(await verifyPubSubPush(req.headers.authorization, req.query.secret))) {
      throw new HttpError(401, "unauthorized", "Bad push credentials");
    }
    const n = decodeRtdn(req.body);
    if (n) {
      try {
        await verifyAndStore(null, n.productId, n.purchaseToken);
      } catch (err) {
        log.error({ err }, "rtdn refresh failed");
        res.status(500).end(); // let Pub/Sub retry transient Play API failures
        return;
      }
    }
    res.status(204).end();
  }),
);
