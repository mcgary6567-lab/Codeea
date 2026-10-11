import { Router } from "express";
import rateLimit from "express-rate-limit";
import { z } from "zod";
import { buildAuthContext, checkPassword, hashPassword, publicUser, requireUser, signToken, type UserRow } from "../auth.js";
import { query } from "../db/pool.js";
import { handler, HttpError, parse } from "../http.js";
import { config } from "../config.js";

export const authRouter = Router();

const limiter = rateLimit({
  windowMs: 15 * 60_000,
  limit: config.NODE_ENV === "test" ? 1000 : 20,
  standardHeaders: "draft-8",
  legacyHeaders: false,
  handler: (_req, res) => {
    res.status(429).json({ error: { code: "rate_limited", message: "Too many attempts, try again later" } });
  },
});

const Credentials = z.object({
  email: z.email().transform((e) => e.trim().toLowerCase()),
  password: z.string().min(8).max(200),
});

const USER_COLS = "id, email, name, granted_plan, granted_until, created_at";

authRouter.post(
  "/auth/register",
  limiter,
  handler(async (req, res) => {
    const body = parse(Credentials.extend({ name: z.string().trim().max(80).optional() }), req.body);
    const [user] = await query<UserRow>(
      `INSERT INTO users (email, password_hash, name) VALUES ($1, $2, $3)
       ON CONFLICT (email) DO NOTHING RETURNING ${USER_COLS}`,
      [body.email, await hashPassword(body.password), body.name ?? null],
    );
    if (!user) throw new HttpError(409, "conflict", "An account with this email already exists");
    const ctx = await buildAuthContext(user.id);
    res.status(201).json({ token: signToken(user.id), user: publicUser(user, ctx), entitlements: ctx.entitlements });
  }),
);

authRouter.post(
  "/auth/login",
  limiter,
  handler(async (req, res) => {
    const body = parse(Credentials, req.body);
    const [row] = await query<UserRow & { password_hash: string }>(
      `SELECT ${USER_COLS}, password_hash FROM users WHERE email = $1`,
      [body.email],
    );
    if (!row || !(await checkPassword(body.password, row.password_hash))) {
      throw new HttpError(401, "unauthorized", "Wrong email or password");
    }
    const ctx = await buildAuthContext(row.id);
    res.json({ token: signToken(row.id), user: publicUser(row, ctx), entitlements: ctx.entitlements });
  }),
);

authRouter.get(
  "/me",
  handler(async (req, res) => {
    const userId = requireUser(req);
    const [user] = await query<UserRow>(`SELECT ${USER_COLS} FROM users WHERE id = $1`, [userId]);
    res.json({ user: publicUser(user!, req.auth), entitlements: req.auth.entitlements });
  }),
);

// Google Play requires in-app account deletion. Subscriptions must still be
// cancelled in Play; the app links the user there before deleting.
authRouter.delete(
  "/me",
  handler(async (req, res) => {
    const userId = requireUser(req);
    await query("DELETE FROM users WHERE id = $1", [userId]);
    res.status(204).end();
  }),
);
