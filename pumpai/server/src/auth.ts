import type { Request, Response, NextFunction, RequestHandler } from "express";
import jwt from "jsonwebtoken";
import bcrypt from "bcryptjs";
import { z, ZodError } from "zod";
import { config } from "./config.js";
import { get } from "./db.js";
import { AppError } from "./services.js";

export type Role = "owner" | "manager" | "accountant" | "attendant";
export interface AuthUser { id: number; tenant_id: number; name: string; email: string; role: Role; station_id: number | null }

declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace Express { interface Request { user?: AuthUser } }
}

export function signToken(u: AuthUser) {
  return jwt.sign({ sub: u.id }, config.jwtSecret, { expiresIn: "7d" });
}

export function login(email: string, password: string) {
  const u = get("SELECT * FROM users WHERE email=?", email.toLowerCase().trim());
  if (!u || !bcrypt.compareSync(password, u.password_hash)) throw new AppError(401, "Invalid email or password");
  const user: AuthUser = { id: u.id, tenant_id: u.tenant_id, name: u.name, email: u.email, role: u.role, station_id: u.station_id };
  return { token: signToken(user), user };
}

export function requireAuth(req: Request, _res: Response, next: NextFunction) {
  const h = req.headers.authorization;
  const token = h?.startsWith("Bearer ") ? h.slice(7) : (req.query.token as string | undefined);
  if (!token) return next(new AppError(401, "Not signed in"));
  try {
    const p = jwt.verify(token, config.jwtSecret) as unknown as { sub: number };
    const u = get("SELECT id, tenant_id, name, email, role, station_id FROM users WHERE id=?", p.sub);
    if (!u) return next(new AppError(401, "User not found"));
    req.user = u as AuthUser;
    next();
  } catch {
    next(new AppError(401, "Session expired"));
  }
}

export const requireRole = (...roles: Role[]): RequestHandler => (req, _res, next) =>
  req.user && roles.includes(req.user.role) ? next() : next(new AppError(403, "You don't have permission for this"));

/** Wrap async handlers so thrown errors reach the error middleware. */
export const h = (fn: (req: Request, res: Response) => unknown): RequestHandler => async (req, res, next) => {
  try {
    const out = await fn(req, res);
    if (out !== undefined && !res.headersSent) res.json(out);
  } catch (e) {
    next(e);
  }
};

export const parse = <T extends z.ZodTypeAny>(schema: T, data: unknown): z.infer<T> => schema.parse(data);

export function errorHandler(err: any, _req: Request, res: Response, _next: NextFunction) {
  if (err instanceof ZodError) return res.status(400).json({ error: err.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ") });
  if (err instanceof AppError) return res.status(err.status).json({ error: err.message });
  console.error(err);
  res.status(500).json({ error: "Internal server error" });
}

export const tid = (req: Request) => req.user!.tenant_id;
