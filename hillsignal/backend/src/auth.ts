import bcrypt from "bcryptjs";
import jwt from "jsonwebtoken";
import type { NextFunction, Request, Response } from "express";
import { config } from "./config.js";
import { query } from "./db/pool.js";
import { HttpError, upgradeRequired } from "./http.js";
import { entitlementsFor, higherPlan, planAtLeast, type Entitlements, type Plan } from "./plans.js";

export interface UserRow {
  id: string;
  email: string;
  name: string | null;
  granted_plan: Plan;
  granted_until: Date | null;
  created_at: Date;
}

export interface AuthContext {
  userId: string | null;
  plan: Plan;
  planExpiresAt: Date | null;
  entitlements: Entitlements;
}

declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace Express {
    interface Request {
      auth: AuthContext;
    }
  }
}

export const hashPassword = (pw: string) => bcrypt.hash(pw, 12);
export const checkPassword = (pw: string, hash: string) => bcrypt.compare(pw, hash);

export function signToken(userId: string): string {
  return jwt.sign({}, config.JWT_SECRET, { subject: userId, expiresIn: "30d", algorithm: "HS256" });
}

function verifyToken(token: string): string | null {
  try {
    const payload = jwt.verify(token, config.JWT_SECRET, { algorithms: ["HS256"] });
    return typeof payload === "object" && payload.sub ? payload.sub : null;
  } catch {
    return null;
  }
}

/** Effective plan = best of an unexpired manual grant and any live store subscription. */
export async function resolvePlan(userId: string): Promise<{ plan: Plan; expiresAt: Date | null }> {
  const [user] = await query<{ granted_plan: Plan; granted_until: Date | null }>(
    "SELECT granted_plan, granted_until FROM users WHERE id = $1",
    [userId],
  );
  if (!user) return { plan: "free", expiresAt: null };

  let plan: Plan = "free";
  let expiresAt: Date | null = null;
  if (user.granted_plan !== "free" && (!user.granted_until || user.granted_until > new Date())) {
    plan = user.granted_plan;
    expiresAt = user.granted_until;
  }

  // Canceled subscriptions stay entitled until they expire; on-hold/paused/expired don't.
  const subs = await query<{ plan: Plan; expires_at: Date | null }>(
    `SELECT plan, expires_at FROM subscriptions
     WHERE user_id = $1
       AND state IN ('SUBSCRIPTION_STATE_ACTIVE','SUBSCRIPTION_STATE_IN_GRACE_PERIOD','SUBSCRIPTION_STATE_CANCELED')
       AND expires_at > now()`,
    [userId],
  );
  for (const s of subs) {
    if (higherPlan(s.plan, plan) !== plan) {
      plan = s.plan;
      expiresAt = s.expires_at;
    } else if (s.plan === plan && s.expires_at && expiresAt && s.expires_at > expiresAt) {
      expiresAt = s.expires_at;
    }
  }
  return { plan, expiresAt };
}

export async function buildAuthContext(userId: string | null): Promise<AuthContext> {
  if (!userId) return { userId: null, plan: "free", planExpiresAt: null, entitlements: entitlementsFor("free") };
  const { plan, expiresAt } = await resolvePlan(userId);
  return { userId, plan, planExpiresAt: expiresAt, entitlements: entitlementsFor(plan) };
}

/** Attaches req.auth. Anonymous requests get free entitlements; a bad token is a 401. */
export async function authenticate(req: Request, _res: Response, next: NextFunction) {
  try {
    const header = req.headers.authorization;
    let userId: string | null = null;
    if (header?.startsWith("Bearer ")) {
      userId = verifyToken(header.slice(7));
      if (!userId) throw new HttpError(401, "unauthorized", "Invalid or expired token");
      const [exists] = await query("SELECT 1 FROM users WHERE id = $1", [userId]);
      if (!exists) throw new HttpError(401, "unauthorized", "Account no longer exists");
    }
    req.auth = await buildAuthContext(userId);
    next();
  } catch (err) {
    next(err);
  }
}

export function requireUser(req: Request): string {
  if (!req.auth.userId) throw new HttpError(401, "unauthorized", "Sign in required");
  return req.auth.userId;
}

export function requirePlan(req: Request, plan: Plan, feature: string): string {
  const userId = requireUser(req);
  if (!planAtLeast(req.auth.plan, plan)) throw upgradeRequired(plan, feature);
  return userId;
}

export function publicUser(u: UserRow, ctx: AuthContext) {
  return {
    id: u.id,
    email: u.email,
    name: u.name,
    plan: ctx.plan,
    planExpiresAt: ctx.planExpiresAt?.toISOString() ?? null,
    createdAt: u.created_at.toISOString(),
  };
}
