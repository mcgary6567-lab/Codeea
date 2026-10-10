import type { Request, Response, NextFunction, RequestHandler } from "express";
import jwt from "jsonwebtoken";
import bcrypt from "bcryptjs";
import { z, ZodError } from "zod";
import { config } from "./config.js";
import { all, get, run, type Row } from "./db.js";
import { AppError } from "./services.js";

/**
 * Roles:
 *  - admin     (CEO / owner): full system access, users, settings, credit limits, wholesale rates
 *  - manager   : runs daily operations, CRM, WhatsApp, stock, prices, automations, expenses
 *  - salesman  : POS sales, own shifts and customer lookup at their assigned station only
 *  - wholesale : wholesale officer — only the wholesale supply module (supplies, returns, payments, statements)
 *  - cashier   : the cash counter — money received and paid, cheques, banks, cash from the salesmen, day book
 */
export const ROLES = ["admin", "manager", "salesman", "wholesale", "cashier", "staff"] as const;
export type Role = (typeof ROLES)[number];
export interface AuthUser { id: number; tenant_id: number; name: string; email: string; role: Role; station_id: number | null }

const ALL: Role[] = ["admin", "manager", "salesman"];
const WHOLESALE: Role[] = ["admin", "wholesale"];
const MGMT: Role[] = ["admin", "manager"];
const ADMIN: Role[] = ["admin"];
const CASHIER: Role[] = ["admin", "cashier"];

/** Single source of truth for access control; sent to the dashboard via /api/me. */
export const PERMISSIONS = {
  "dashboard.view": MGMT,
  "ai.ask": MGMT,
  "sales.create": ALL,
  "sales.view": ALL, // salesman: own station only
  "shifts.manage": ALL, // salesman: own shifts at own station only
  "shifts.view_all": MGMT,
  "shifts.expenses": ALL, // record expenses paid from the shift's cash (tea, generator fuel...)
  "customers.view": ALL, // salesman only reads khata accounts via the POS picker (/pos/khata-accounts)
  "customers.create": MGMT, // salesman cannot add customers — only pick existing khata accounts at the POS
  "customers.edit": MGMT,
  "credit.set_limit": ADMIN,
  "khata.manage": MGMT,
  "khata.clear_pending": ["admin", "manager", "cashier"], // clear a card-pending fuel hold and bill it to the khata (owner / manager / cashier)
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
  "staff.manage": MGMT, // staff advances, salary, cash shortages
  "wholesale.view": WHOLESALE,
  "wholesale.manage": WHOLESALE, // add clients, supplies, returns, payments
  "wholesale.rates": ADMIN, // set each client's per-litre rates, credit limits
  "wholesale.void": ADMIN, // cancel a wrong entry (stock is reversed)
  "carriage.view": WHOLESALE, // carriage / kiraya (bypass on our depot ID), run with thekedars
  "carriage.manage": WHOLESALE, // add thekedars, bill kiraya, record fuel money, take payments
  "carriage.void": ADMIN, // cancel a wrong carriage / fuel entry
  "expenses.view": MGMT,
  "expenses.create": MGMT,
  "expenses.approve": ADMIN, // approve manager expenses above the approval limit
  "reports.view": MGMT,
  "suppliers.manage": MGMT, // supplier accounts, fuel purchase cost, payments to suppliers
  "audit.view": ADMIN, // who changed what (prices, undo, deletes, edits, sign-ins)
  "other_entries.manage": ADMIN, // CEO-only: record other income / expense / discount and see its report
  "photos.delete": ADMIN, // only the CEO can delete an uploaded photo; everyone else may only view
  "bank.view": CASHIER, // how much is in each bank account, statements
  "bank.manage": CASHIER, // add bank accounts, cash withdrawals, transfers, bank charges / profit
  "cashier.desk": CASHIER, // the cashier desk: what to collect, pay, deposit and clear today
  "cash.book": ["admin", "manager", "cashier"], // office cash book: count the cash, bank deposits
  "cash.receive": CASHIER, // take payments from khata customers, wholesale clients and others (receipt no.)
  "cash.pay": CASHIER, // pay suppliers, expenses, staff advances and others
  "cheques.manage": CASHIER, // cheque register: cheques received and issued — deposit, clear, bounce
  "shifts.handover": ["admin", "manager", "cashier"], // take the cash from a salesman when the shift closes
} as const satisfies Record<string, readonly Role[]>;
export type Permission = keyof typeof PERMISSIONS;

/*
 * Each pump can change what a role may do (Users & Roles → tick / untick). Changes are kept in
 * role_permissions and laid over the defaults above. The admin (owner) always has everything,
 * so nobody can lock the owner out.
 */
const overrides = new Map<number, Map<string, boolean>>(); // tenant → "perm|role" → allowed
function tenantOverrides(t: number) {
  let m = overrides.get(t);
  if (!m) {
    m = new Map(all("SELECT perm, role, allowed FROM role_permissions WHERE tenant_id=?", t).map((r) => [`${r.perm}|${r.role}`, Boolean(r.allowed)]));
    overrides.set(t, m);
  }
  return m;
}
export const forgetRoleOverrides = (t: number) => overrides.delete(t);
export function allowed(tenantId: number, role: Role, perm: Permission): boolean {
  if (role === "admin") return true;
  const o = tenantOverrides(tenantId).get(`${perm}|${role}`);
  return o ?? (PERMISSIONS[perm] as readonly Role[]).includes(role);
}
/** Who may do what at this pump: every permission → the roles allowed. */
export const permissionMatrix = (tenantId: number) =>
  Object.fromEntries((Object.keys(PERMISSIONS) as Permission[]).map((p) => [p, ROLES.filter((r) => allowed(tenantId, r, p))])) as Record<Permission, Role[]>;

export const can = (user: AuthUser | undefined, perm: Permission) => Boolean(user && allowed(user.tenant_id, user.role, perm));
export const permissionsOf = (role: Role, tenantId: number) => (Object.keys(PERMISSIONS) as Permission[]).filter((p) => allowed(tenantId, role, p));

declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace Express { interface Request { user?: AuthUser } }
}

export function signToken(u: AuthUser) {
  const tv = get("SELECT token_version v FROM users WHERE id=?", u.id)?.v ?? 0;
  return jwt.sign({ sub: u.id, tv }, config.jwtSecret, { expiresIn: "7d" });
}
/** A short-lived, read-only token for links the browser opens by itself (photos, downloads, the live feed). */
export function mediaToken(userId: number) {
  const tv = get("SELECT token_version v FROM users WHERE id=?", userId)?.v ?? 0;
  return jwt.sign({ sub: userId, tv, media: 1 }, config.jwtSecret, { expiresIn: "12h" });
}
/** Sign this person out everywhere (password / PIN change, disabled, "sign out all"). */
export const revokeSessions = (userId: number) => run("UPDATE users SET token_version = token_version + 1 WHERE id=?", userId);

/** A device token links a pump tablet to the business so staff can sign in by tapping their name + PIN. */
// linked tablets carry the pump's device "epoch": the owner can unlink every tablet at once by moving it on
const deviceEpoch = (tenantId: number) => Number(get("SELECT value FROM settings WHERE tenant_id=? AND key='device_epoch'", tenantId)?.value ?? 0);
export const deviceToken = (tenantId: number) => jwt.sign({ dev: tenantId, ep: deviceEpoch(tenantId) }, config.jwtSecret, { expiresIn: "365d" });
export const unlinkDevices = (tenantId: number) => run(
  "INSERT INTO settings (tenant_id,key,value) VALUES (?,'device_epoch',?) ON CONFLICT(tenant_id,key) DO UPDATE SET value=excluded.value", tenantId, String(deviceEpoch(tenantId) + 1));
function deviceTenant(token: string | undefined): number {
  try {
    const p = jwt.verify(token ?? "", config.jwtSecret) as { dev?: number; ep?: number };
    if (typeof p.dev === "number" && (p.ep ?? 0) === deviceEpoch(p.dev)) return p.dev;
  } catch { /* fall through */ }
  throw new AppError(401, "This device is not linked. Sign in once with email and password.");
}
/** The owner signs in with a password; PIN sign-in is for staff on a shared tablet (setting "pin_admin" allows it). */
const pinAllowed = (u: { role: string; tenant_id: number }) => u.role !== "admin" || get("SELECT value FROM settings WHERE tenant_id=? AND key='pin_admin'", u.tenant_id)?.value === "1";

/** Staff who can sign in with a PIN on this device. */
export function pinUsers(device: string | undefined) {
  const t = deviceTenant(device);
  return all(`SELECT u.id, u.name, u.role, u.tenant_id, s.name station_name FROM users u LEFT JOIN stations s ON s.id=u.station_id
    WHERE u.tenant_id=? AND u.active=1 AND u.pin_hash IS NOT NULL
    ORDER BY CASE u.role WHEN 'salesman' THEN 0 WHEN 'manager' THEN 1 WHEN 'wholesale' THEN 2 WHEN 'cashier' THEN 3 ELSE 4 END, u.name`, t)
    .filter(pinAllowed).map(({ tenant_id: _t, ...u }) => u);
}

const PIN_TRIES = 5, PIN_LOCK_MIN = 10;
/** Sign-ins and wrong passwords / PINs go in the audit log. */
function auditLogin(u: { id: number; tenant_id: number; name: string }, ok: boolean, how: string) {
  run("INSERT INTO audit_log (tenant_id,user_id,user_name,action,ref,created_at) VALUES (?,?,?,?,?,?)",
    u.tenant_id, u.id, u.name, `login:${ok ? `Signed in (${how})` : `Wrong ${how}`}`, how, new Date().toISOString());
}

export function pinLogin(device: string | undefined, userId: number, pin: string) {
  const u = get("SELECT * FROM users WHERE id=? AND tenant_id=?", userId, deviceTenant(device));
  if (!u || !u.pin_hash || !pinAllowed(u)) throw new AppError(401, "PIN login is not set up for this person");
  if (!u.active) throw new AppError(403, "This account is disabled. Contact your admin.");
  if (u.pin_locked_until && Date.parse(u.pin_locked_until) > Date.now())
    throw new AppError(429, `Too many wrong PINs. Try again after ${Math.ceil((Date.parse(u.pin_locked_until) - Date.now()) / 60000)} minutes, or ask the admin to reset your PIN.`);
  if (!bcrypt.compareSync(pin, u.pin_hash)) {
    const fails = u.pin_fails + 1;
    run("UPDATE users SET pin_fails=?, pin_locked_until=? WHERE id=?", fails >= PIN_TRIES ? 0 : fails,
      fails >= PIN_TRIES ? new Date(Date.now() + PIN_LOCK_MIN * 60000).toISOString() : null, u.id);
    auditLogin(u, false, fails >= PIN_TRIES ? "PIN — locked for 10 minutes" : "PIN");
    throw new AppError(401, fails >= PIN_TRIES ? `Wrong PIN. Locked for ${PIN_LOCK_MIN} minutes — forgot it? Ask the admin to reset it.` : `Wrong PIN (${PIN_TRIES - fails} tries left)`);
  }
  run("UPDATE users SET pin_fails=0, pin_locked_until=NULL WHERE id=?", u.id);
  auditLogin(u, true, "PIN");
  const user: AuthUser = { id: u.id, tenant_id: u.tenant_id, name: u.name, email: u.email, role: u.role, station_id: u.station_id };
  return { token: signToken(user), user, permissions: permissionsOf(user.role, user.tenant_id) };
}

const PW_TRIES = 8, PW_LOCK_MIN = 15;
const DUMMY_HASH = bcrypt.hashSync("not-a-real-password", 10);
/** Check email + password (with lockout). Returns the user row on success; throws on failure. Does NOT issue a session. */
export function authenticate(email: string, password: string): Row {
  const u = get("SELECT * FROM users WHERE email=?", email.toLowerCase().trim());
  if (u?.pw_locked_until && Date.parse(u.pw_locked_until) > Date.now())
    throw new AppError(429, `Too many wrong passwords. Try again after ${Math.ceil((Date.parse(u.pw_locked_until) - Date.now()) / 60000)} minutes.`);
  // the same work is done whether or not the email exists, so the answer time doesn't give it away
  const good = bcrypt.compareSync(password, u?.password_hash ?? DUMMY_HASH);
  if (!u || !good) {
    if (u) {
      const fails = (u.pw_fails ?? 0) + 1;
      run("UPDATE users SET pw_fails=?, pw_locked_until=? WHERE id=?", fails >= PW_TRIES ? 0 : fails, fails >= PW_TRIES ? new Date(Date.now() + PW_LOCK_MIN * 60000).toISOString() : null, u.id);
      auditLogin(u, false, fails >= PW_TRIES ? "password — locked for 15 minutes" : "password");
    }
    throw new AppError(401, "Invalid email or password");
  }
  if (!u.active) throw new AppError(403, "This account is disabled. Contact your admin.");
  run("UPDATE users SET pw_fails=0, pw_locked_until=NULL WHERE id=?", u.id);
  return u;
}
/** Issue a signed session for an already-authenticated user row. */
export function issueSession(u: Row, how = "password") {
  auditLogin(u, true, how);
  const user: AuthUser = { id: u.id, tenant_id: u.tenant_id, name: u.name, email: u.email, role: u.role, station_id: u.station_id };
  // only the owner / a manager links a shared tablet for PIN sign-in
  const links = ["admin", "manager"].includes(u.role);
  return { token: signToken(user), user, permissions: permissionsOf(user.role, user.tenant_id), ...(links ? { device_token: deviceToken(u.tenant_id) } : {}) };
}
export function login(email: string, password: string) {
  return issueSession(authenticate(email, password));
}

/** Two-factor login for the owner: is it switched on and usable (admin with a phone)? */
export const twoFaRequired = (u: Row) =>
  u.role === "admin" && get("SELECT value FROM settings WHERE tenant_id=? AND key='admin_2fa'", u.tenant_id)?.value === "1" && Boolean(u.phone);
const OTP_MIN = 10, OTP_TRIES = 5;
/** Make and store a 6-digit one-time code for this user; returns the plain code to send on WhatsApp. */
export function create2fa(userId: number): string {
  const code = String(Math.floor(100000 + Math.random() * 900000));
  run("INSERT INTO login_otps (user_id,code_hash,expires_at,attempts,created_at) VALUES (?,?,?,0,?) ON CONFLICT(user_id) DO UPDATE SET code_hash=excluded.code_hash, expires_at=excluded.expires_at, attempts=0, created_at=excluded.created_at",
    userId, bcrypt.hashSync(code, 8), new Date(Date.now() + OTP_MIN * 60000).toISOString(), new Date().toISOString());
  return code;
}
/** Verify a login OTP and issue the session. Throws on wrong / expired / too many tries. */
export function verify2fa(userId: number, code: string) {
  const u = get("SELECT * FROM users WHERE id=? AND active=1", userId);
  const o = get("SELECT * FROM login_otps WHERE user_id=?", userId);
  if (!u || !o) throw new AppError(401, "Sign in again");
  if (Date.parse(o.expires_at) < Date.now()) { run("DELETE FROM login_otps WHERE user_id=?", userId); throw new AppError(401, "Code expired — sign in again"); }
  if (o.attempts >= OTP_TRIES) { run("DELETE FROM login_otps WHERE user_id=?", userId); throw new AppError(429, "Too many wrong codes — sign in again"); }
  if (!bcrypt.compareSync(code, o.code_hash)) {
    run("UPDATE login_otps SET attempts=attempts+1 WHERE user_id=?", userId);
    throw new AppError(401, `Wrong code (${OTP_TRIES - o.attempts - 1} tries left)`);
  }
  run("DELETE FROM login_otps WHERE user_id=?", userId);
  return issueSession(u, "password + code");
}

export function requireAuth(req: Request, _res: Response, next: NextFunction) {
  const h = req.headers.authorization;
  const inUrl = !h?.startsWith("Bearer ");
  const token = inUrl ? (req.query.token as string | undefined) : h!.slice(7);
  if (!token) return next(new AppError(401, "Not signed in"));
  try {
    const p = jwt.verify(token, config.jwtSecret) as unknown as { sub: number; tv?: number; media?: number };
    if (typeof p.sub !== "number") return next(new AppError(401, "Not signed in"));
    // a token in a link (photo, download, live feed) must be a short read-only media token, never a full session
    if (inUrl && (!p.media || req.method !== "GET")) return next(new AppError(401, "Not signed in"));
    if (!inUrl && p.media) return next(new AppError(401, "Not signed in"));
    const u = get("SELECT id, tenant_id, name, email, role, station_id, active, token_version FROM users WHERE id=?", p.sub);
    if (!u || !u.active) return next(new AppError(401, "User not found or disabled"));
    if ((p.tv ?? 0) !== (u.token_version ?? 0)) return next(new AppError(401, "Signed out — please sign in again"));
    delete u.active; delete u.token_version;
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

/** Allow only these roles (e.g. CEO / owner = "admin"). */
export const requireRole = (...roles: Role[]): RequestHandler => (req, _res, next) =>
  req.user && roles.includes(req.user.role) ? next() : next(new AppError(403, "Only the owner can do this"));

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
  if (err?.type === "entity.parse.failed") return res.status(400).json({ error: "The request was not valid JSON" });
  if (err?.type === "entity.too.large") return res.status(413).json({ error: "Too much data in one go (photo too large?)" });
  console.error(err);
  res.status(500).json({ error: "Internal server error" });
}

export const tid = (req: Request) => req.user!.tenant_id;
