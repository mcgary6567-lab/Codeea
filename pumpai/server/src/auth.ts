import type { Request, Response, NextFunction, RequestHandler } from "express";
import jwt from "jsonwebtoken";
import bcrypt from "bcryptjs";
import { z, ZodError } from "zod";
import { config } from "./config.js";
import { all, get, run } from "./db.js";
import { AppError } from "./services.js";

/**
 * Roles:
 *  - admin     (CEO / owner): full system access, users, settings, credit limits, wholesale rates
 *  - manager   : runs daily operations, CRM, WhatsApp, stock, prices, automations, expenses
 *  - salesman  : POS sales, own shifts and customer lookup at their assigned station only
 *  - wholesale : wholesale officer — only the wholesale supply module (supplies, returns, payments, statements)
 */
export const ROLES = ["admin", "manager", "salesman", "wholesale"] as const;
export type Role = (typeof ROLES)[number];
export interface AuthUser { id: number; tenant_id: number; name: string; email: string; role: Role; station_id: number | null }

const ALL: Role[] = ["admin", "manager", "salesman"];
const WHOLESALE: Role[] = ["admin", "wholesale"];
const MGMT: Role[] = ["admin", "manager"];
const ADMIN: Role[] = ["admin"];

/** Single source of truth for access control; sent to the dashboard via /api/me. */
export const PERMISSIONS = {
  "dashboard.view": MGMT,
  "ai.ask": MGMT,
  "sales.create": ALL,
  "sales.view": ALL, // salesman: own station only
  "shifts.manage": ALL, // salesman: own shifts at own station only
  "shifts.view_all": MGMT,
  "shifts.expenses": ALL, // record expenses paid from the shift's cash (tea, generator fuel...)
  "customers.view": ALL,
  "customers.create": ALL,
  "customers.edit": MGMT,
  "credit.set_limit": ADMIN,
  "khata.manage": MGMT,
  "whatsapp.inbox": MGMT,
  "orders.manage": MGMT,
  "complaints.manage": MGMT,
  "campaigns.manage": MGMT,
  "stock.manage": MGMT,
  "prices.view": ALL,
  "prices.update": MGMT,
  "alerts.view": MGMT,
  "automations.manage": MGMT,
  "stations.manage": ADMIN,
  "settings.manage": ADMIN,
  "users.manage": ADMIN,
  "wholesale.view": WHOLESALE,
  "wholesale.manage": WHOLESALE, // add clients, supplies, returns, payments
  "wholesale.rates": ADMIN, // set each client's per-litre rates, credit limits
  "wholesale.void": ADMIN, // cancel a wrong entry (stock is reversed)
  "expenses.view": MGMT,
  "expenses.create": MGMT,
  "expenses.approve": ADMIN, // approve manager expenses above the approval limit
  "reports.view": MGMT,
  "suppliers.manage": MGMT, // supplier accounts, fuel purchase cost, payments to suppliers
} as const satisfies Record<string, readonly Role[]>;
export type Permission = keyof typeof PERMISSIONS;

export const can = (user: AuthUser | undefined, perm: Permission) =>
  Boolean(user && (PERMISSIONS[perm] as readonly Role[]).includes(user.role));
export const permissionsOf = (role: Role) =>
  (Object.keys(PERMISSIONS) as Permission[]).filter((p) => (PERMISSIONS[p] as readonly Role[]).includes(role));

declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace Express { interface Request { user?: AuthUser } }
}

export function signToken(u: AuthUser) {
  return jwt.sign({ sub: u.id }, config.jwtSecret, { expiresIn: "7d" });
}

/** A device token links a pump tablet to the business so staff can sign in by tapping their name + PIN. */
export const deviceToken = (tenantId: number) => jwt.sign({ dev: tenantId }, config.jwtSecret, { expiresIn: "365d" });
function deviceTenant(token: string | undefined): number {
  try {
    const p = jwt.verify(token ?? "", config.jwtSecret) as { dev?: number };
    if (typeof p.dev === "number") return p.dev;
  } catch { /* fall through */ }
  throw new AppError(401, "This device is not linked. Sign in once with email and password.");
}

/** Staff who can sign in with a PIN on this device. */
export function pinUsers(device: string | undefined) {
  return all(`SELECT u.id, u.name, u.role, s.name station_name FROM users u LEFT JOIN stations s ON s.id=u.station_id
    WHERE u.tenant_id=? AND u.active=1 AND u.pin_hash IS NOT NULL
    ORDER BY CASE u.role WHEN 'salesman' THEN 0 WHEN 'manager' THEN 1 WHEN 'wholesale' THEN 2 ELSE 3 END, u.name`, deviceTenant(device));
}

const PIN_TRIES = 5, PIN_LOCK_MIN = 10;
export function pinLogin(device: string | undefined, userId: number, pin: string) {
  const u = get("SELECT * FROM users WHERE id=? AND tenant_id=?", userId, deviceTenant(device));
  if (!u || !u.pin_hash) throw new AppError(401, "PIN login is not set up for this person");
  if (!u.active) throw new AppError(403, "This account is disabled. Contact your admin.");
  if (u.pin_locked_until && Date.parse(u.pin_locked_until) > Date.now())
    throw new AppError(429, `Too many wrong PINs. Try again after ${Math.ceil((Date.parse(u.pin_locked_until) - Date.now()) / 60000)} minutes or ask the manager.`);
  if (!bcrypt.compareSync(pin, u.pin_hash)) {
    const fails = u.pin_fails + 1;
    run("UPDATE users SET pin_fails=?, pin_locked_until=? WHERE id=?", fails >= PIN_TRIES ? 0 : fails,
      fails >= PIN_TRIES ? new Date(Date.now() + PIN_LOCK_MIN * 60000).toISOString() : null, u.id);
    throw new AppError(401, fails >= PIN_TRIES ? `Wrong PIN. Locked for ${PIN_LOCK_MIN} minutes.` : `Wrong PIN (${PIN_TRIES - fails} tries left)`);
  }
  run("UPDATE users SET pin_fails=0, pin_locked_until=NULL WHERE id=?", u.id);
  const user: AuthUser = { id: u.id, tenant_id: u.tenant_id, name: u.name, email: u.email, role: u.role, station_id: u.station_id };
  return { token: signToken(user), user, permissions: permissionsOf(user.role), device_token: deviceToken(u.tenant_id) };
}

export function login(email: string, password: string) {
  const u = get("SELECT * FROM users WHERE email=?", email.toLowerCase().trim());
  if (!u || !bcrypt.compareSync(password, u.password_hash)) throw new AppError(401, "Invalid email or password");
  if (!u.active) throw new AppError(403, "This account is disabled. Contact your admin.");
  const user: AuthUser = { id: u.id, tenant_id: u.tenant_id, name: u.name, email: u.email, role: u.role, station_id: u.station_id };
  return { token: signToken(user), user, permissions: permissionsOf(user.role), device_token: deviceToken(u.tenant_id) };
}

export function requireAuth(req: Request, _res: Response, next: NextFunction) {
  const h = req.headers.authorization;
  const token = h?.startsWith("Bearer ") ? h.slice(7) : (req.query.token as string | undefined);
  if (!token) return next(new AppError(401, "Not signed in"));
  try {
    const p = jwt.verify(token, config.jwtSecret) as unknown as { sub: number };
    if (typeof p.sub !== "number") return next(new AppError(401, "Not signed in"));
    const u = get("SELECT id, tenant_id, name, email, role, station_id, active FROM users WHERE id=?", p.sub);
    if (!u || !u.active) return next(new AppError(401, "User not found or disabled"));
    delete u.active;
    req.user = u as AuthUser;
    next();
  } catch {
    next(new AppError(401, "Session expired"));
  }
}

export const requirePerm = (perm: Permission): RequestHandler => (req, _res, next) =>
  can(req.user, perm) ? next() : next(new AppError(403, "You don't have permission for this"));

/** Allow if the user has any of the permissions. */
export const requireAny = (...perms: Permission[]): RequestHandler => (req, _res, next) =>
  perms.some((p) => can(req.user, p)) ? next() : next(new AppError(403, "You don't have permission for this"));

/** Salesmen are locked to their assigned station; returns the station they may act on. */
export function scopedStation(req: Request, requested?: number | null): number | null {
  const u = req.user!;
  if (u.role !== "salesman") return requested ?? null;
  if (!u.station_id) throw new AppError(403, "No station assigned to your account. Ask your admin.");
  if (requested && requested !== u.station_id) throw new AppError(403, "You can only work on your own station");
  return u.station_id;
}

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
