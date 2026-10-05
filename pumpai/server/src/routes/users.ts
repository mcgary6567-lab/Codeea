import { Router } from "express";
import { z } from "zod";
import bcrypt from "bcryptjs";
import { all, get, run, now } from "../db.js";
import { h, parse, tid, requirePerm, ROLES, PERMISSIONS, permissionMatrix, forgetRoleOverrides, type Permission } from "../auth.js";
import crypto from "node:crypto";
import { AppError, normalizePhone, audit } from "../services.js";

/** Admin-only user management. */
export const users = Router();
users.use("/users", requirePerm("users.manage"));

const list = (tenantId: number) => all(
  `SELECT u.id, u.name, u.email, u.phone, u.role, u.station_id, u.active, u.created_at, s.name station_name, u.pin_hash IS NOT NULL has_pin,
     CASE WHEN u.pin_locked_until > strftime('%Y-%m-%dT%H:%M:%fZ','now') THEN u.pin_locked_until END pin_locked_until
   FROM users u LEFT JOIN stations s ON s.id=u.station_id WHERE u.tenant_id=? ORDER BY CASE u.role WHEN 'admin' THEN 0 WHEN 'manager' THEN 1 ELSE 2 END, u.name`,
  tenantId,
);

users.get("/users", h((req) => ({ users: list(tid(req)), roles: ROLES, permissions: permissionMatrix(tid(req)), defaults: PERMISSIONS })));

/** Tick / untick what a role may do at this pump (the owner's own rights cannot be taken away). */
users.put("/roles/permissions", requirePerm("users.manage"), h((req) => {
  const b = parse(z.object({ perm: z.string(), role: z.enum(ROLES), allowed: z.boolean() }), req.body);
  if (!(b.perm in PERMISSIONS)) throw new AppError(400, "Unknown permission");
  if (b.role === "admin") throw new AppError(400, "The admin (owner) always has every permission");
  const def = (PERMISSIONS[b.perm as Permission] as readonly string[]).includes(b.role);
  // back to the default: no override needed
  if (b.allowed === def) run("DELETE FROM role_permissions WHERE tenant_id=? AND perm=? AND role=?", tid(req), b.perm, b.role);
  else run(`INSERT INTO role_permissions (tenant_id,perm,role,allowed,updated_by,updated_at) VALUES (?,?,?,?,?,?)
    ON CONFLICT (tenant_id, perm, role) DO UPDATE SET allowed=excluded.allowed, updated_by=excluded.updated_by, updated_at=excluded.updated_at`,
    tid(req), b.perm, b.role, b.allowed ? 1 : 0, req.user!.name, now());
  forgetRoleOverrides(tid(req));
  return { permissions: permissionMatrix(tid(req)) };
}));
users.post("/roles/permissions/reset", requirePerm("users.manage"), h((req) => {
  run("DELETE FROM role_permissions WHERE tenant_id=?", tid(req));
  forgetRoleOverrides(tid(req));
  return { permissions: permissionMatrix(tid(req)) };
}));

/** 4-digit PIN for quick sign-in; null removes it. */
const pinField = z.string().regex(/^\d{4}$/, "PIN must be 4 digits").nullable().optional();

function checkStation(tenantId: number, role: string, stationId: number | null | undefined) {
  if (role === "salesman" && !stationId) throw new AppError(400, "A salesman must be assigned to a station");
  if (stationId && !get("SELECT id FROM stations WHERE id=? AND tenant_id=?", stationId, tenantId)) throw new AppError(400, "Station not found");
}

users.post("/users", h((req) => {
  const b = parse(z.object({
    name: z.string().min(2), email: z.string().email(), password: z.string().min(6, "Password must be at least 6 characters"),
    role: z.enum(ROLES), station_id: z.number().nullable().optional(), phone: z.string().optional().nullable(), pin: pinField,
  }), req.body);
  const email = b.email.toLowerCase().trim();
  if (get("SELECT id FROM users WHERE email=?", email)) throw new AppError(400, "This email is already in use");
  checkStation(tid(req), b.role, b.station_id);
  const { id } = run("INSERT INTO users (tenant_id,name,email,password_hash,role,station_id,phone,active,created_at) VALUES (?,?,?,?,?,?,?,1,?)",
    tid(req), b.name, email, bcrypt.hashSync(b.password, 10), b.role, b.role === "salesman" ? b.station_id! : b.station_id ?? null, b.phone ? normalizePhone(b.phone) : null, now());
  if (b.pin) run("UPDATE users SET pin_hash=? WHERE id=?", bcrypt.hashSync(b.pin, 10), id);
  return list(tid(req)).find((u) => u.id === id);
}));

function ownUser(tenantId: number, id: number) {
  const u = get("SELECT * FROM users WHERE id=? AND tenant_id=?", id, tenantId);
  if (!u) throw new AppError(404, "User not found");
  return u;
}
const activeAdmins = (tenantId: number) => get("SELECT COUNT(*) n FROM users WHERE tenant_id=? AND role='admin' AND active=1", tenantId)!.n as number;

users.patch("/users/:id", h((req) => {
  const u = ownUser(tid(req), Number(req.params.id));
  const b = parse(z.object({
    name: z.string().min(2).optional(), email: z.string().email().optional(), role: z.enum(ROLES).optional(),
    station_id: z.number().nullable().optional(), active: z.boolean().optional(), password: z.string().min(6).optional(),
    phone: z.string().optional().nullable(), pin: pinField,
  }), req.body);
  const role = b.role ?? u.role;
  const active = b.active ?? Boolean(u.active);
  const stationId = b.station_id === undefined ? u.station_id : b.station_id;
  if (u.role === "admin" && u.active && (role !== "admin" || !active) && activeAdmins(tid(req)) <= 1)
    throw new AppError(400, "There must always be at least one active admin");
  if (u.id === req.user!.id && !active) throw new AppError(400, "You cannot disable your own account");
  checkStation(tid(req), role, stationId);
  const email = b.email?.toLowerCase().trim() ?? u.email;
  if (email !== u.email && get("SELECT id FROM users WHERE email=?", email)) throw new AppError(400, "This email is already in use");
  run("UPDATE users SET name=?, email=?, role=?, station_id=?, active=? WHERE id=?", b.name ?? u.name, email, role, stationId ?? null, active ? 1 : 0, u.id);
  if (b.phone !== undefined) run("UPDATE users SET phone=? WHERE id=?", b.phone ? normalizePhone(b.phone) : null, u.id);
  if (b.password) run("UPDATE users SET password_hash=? WHERE id=?", bcrypt.hashSync(b.password, 10), u.id);
  if (b.pin !== undefined) run("UPDATE users SET pin_hash=?, pin_fails=0, pin_locked_until=NULL WHERE id=?", b.pin ? bcrypt.hashSync(b.pin, 10) : null, u.id);
  return list(tid(req)).find((x) => x.id === u.id);
}));

/** Forgotten PIN: the admin sets a new one (or lets the app pick one) and the lock is lifted. */
users.post("/users/:id/reset-pin", h((req) => {
  const u = ownUser(tid(req), Number(req.params.id));
  const b = parse(z.object({ pin: z.string().regex(/^\d{4}$/, "PIN must be 4 digits").optional() }), req.body);
  // the admin's own choice is used as is; a PIN made by the app avoids easy ones like 0000 or 1234
  let pin = b.pin;
  while (!pin) { const p = String(crypto.randomInt(0, 10_000)).padStart(4, "0"); if (!/^(\d)\1{3}$/.test(p) && p !== "1234") pin = p; }
  run("UPDATE users SET pin_hash=?, pin_fails=0, pin_locked_until=NULL WHERE id=?", bcrypt.hashSync(pin, 10), u.id);
  audit(tid(req), req.user!, "pin_reset", `user:${u.id}`, { name: u.name });
  return { pin, user: list(tid(req)).find((x) => x.id === u.id) };
}));

users.delete("/users/:id", h((req) => {
  const u = ownUser(tid(req), Number(req.params.id));
  if (u.id === req.user!.id) throw new AppError(400, "You cannot delete your own account");
  if (u.role === "admin" && u.active && activeAdmins(tid(req)) <= 1) throw new AppError(400, "There must always be at least one active admin");
  run("DELETE FROM users WHERE id=?", u.id);
  return { ok: true };
}));
