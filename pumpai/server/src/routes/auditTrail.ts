/**
 * Audit log: who changed what, and when — prices, undo, deletes, edits, approvals, settings, sign-ins.
 * Every change made through the API is recorded by one middleware, with the record before and after
 * for edits, so nothing depends on each screen remembering to log.
 */
import { Router, type Request, type Response, type NextFunction } from "express";
import { z } from "zod";
import { all, get, run, now, pkStart, pkEnd } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";

export const auditRouter = Router();

/** Requests that are normal daily work, not changes worth auditing (each is kept in its own table anyway). */
const SKIP: [string, RegExp][] = [
  ["POST", /^\/sales$/], ["POST", /^\/shop\/sales$/], ["POST", /^\/notifications/], ["POST", /^\/push\//], ["POST", /^\/ai\//],
  ["POST", /^\/attendance\/check-(in|out)$/], ["POST", /^\/checklist\/\d+$/], ["POST", /^\/analysis\/reconcile$/], ["POST", /^\/bank\/reconcile$/],
  ["POST", /^\/whatsapp\/simulate/], ["POST", /^\/ai\/ask$/], ["PUT", /^\/notifications/],
];
/** Records we snapshot before an edit / delete, so the log shows old → new. */
const RESOURCES: [RegExp, string, string][] = [
  [/^\/customers\/(\d+)$/, "customers", "customer"], [/^\/users\/(\d+)$/, "users", "user"], [/^\/staff\/(\d+)$/, "users", "staff member"],
  [/^\/expenses\/(\d+)$/, "expenses", "expense"], [/^\/stations\/(\d+)$/, "stations", "station"], [/^\/tanks\/(\d+)$/, "tanks", "tank"],
  [/^\/suppliers\/(\d+)$/, "suppliers", "supplier"], [/^\/wholesale\/clients\/(\d+)$/, "wholesale_clients", "wholesale client"],
  [/^\/shop\/items\/(\d+)$/, "shop_items", "shop item"], [/^\/machines\/(\d+)$/, "machines", "machine"], [/^\/licences\/(\d+)$/, "licences", "licence"],
  [/^\/checklist\/items\/(\d+)$/, "checklist_items", "checklist item"], [/^\/recurring-expenses\/(\d+)$/, "recurring_expenses", "monthly expense"],
  [/^\/vehicles\/(\d+)$/, "vehicles", "vehicle"], [/^\/nozzles\/(\d+)$/, "nozzles", "meter"], [/^\/loans\/(\d+)$/, "staff_loans", "loan"], [/^\/bookings\/(\d+)$/, "bookings", "booking"],
];
const NAMES: Record<string, string> = {
  prices: "fuel prices", "price-requests": "price change request", sales: "sale", shifts: "shift", customers: "customer", khata: "khata", users: "user",
  staff: "staff account", expenses: "expense", "expense-categories": "expense category", stations: "station", tanks: "tank", stock: "stock", suppliers: "supplier",
  wholesale: "wholesale", shop: "shop", cash: "cash book", coupons: "coupons", wallets: "wallet", settings: "settings", business: "business profile",
  integrations: "integrations", backups: "backup", licences: "licence", checklist: "checklist", leaves: "leave", training: "training", loans: "loan",
  claims: "tanker claim", tax: "tax", machines: "machine", "recurring-expenses": "monthly expense", "utility-bills": "utility bill", commission: "commission rates",
  board: "TV board", campaigns: "campaign", orders: "order", complaints: "complaint", automations: "automation", alerts: "alert", bookings: "booking",
  "govt-bills": "government bill", vehicles: "vehicle", cards: "QR card", "day-closes": "closed day", nozzles: "meter", system: "system", coaching: "coaching message",
};
const HIDDEN = new Set(["password", "password_hash", "pin", "pin_hash", "image", "logo", "csv", "data", "token", "anthropic_key", "wa_token", "wa_app_secret", "code"]);

function redact(v: unknown, depth = 0): unknown {
  if (Array.isArray(v)) return v.slice(0, 30).map((x) => redact(x, depth + 1));
  if (v && typeof v === "object" && depth < 4)
    return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, HIDDEN.has(k) ? (x ? "•••" : x) : redact(x, depth + 1)]));
  if (typeof v === "string" && v.length > 300) return `${v.slice(0, 300)}…`;
  return v;
}
const snapshot = (table: string, id: number) => get(`SELECT * FROM ${table} WHERE id=?`, id) ?? null;

/** What kind of change this is (for filtering) and a plain-English label. */
export function describe(method: string, path: string) {
  const parts = path.split("/").filter(Boolean);
  const noun = NAMES[parts[0]] ?? parts[0];
  const last = parts[parts.length - 1];
  if (/undo$/.test(path)) return { kind: "undo", label: `Undid a ${parts[0] === "shop" ? "shop sale" : "sale"}` };
  if (parts[0] === "prices" || parts[0] === "price-requests") return { kind: "price", label: parts[0] === "prices" ? "Changed fuel prices" : `${last === "approve" ? "Approved" : "Rejected"} a price change` };
  if (["approve", "reject"].includes(last)) return { kind: "approval", label: `${last === "approve" ? "Approved" : "Rejected"} ${NAMES[parts[0]] ?? noun}` };
  if (["settings", "business", "integrations", "automations", "tax", "commission", "board", "system"].includes(parts[0]) || last === "settings") return { kind: "settings", label: `Changed ${noun}` };
  if (method === "DELETE") return { kind: "delete", label: `Deleted ${noun}` };
  if (method === "PATCH" || method === "PUT") return { kind: "edit", label: `Edited ${noun}` };
  const action = parts.length > 1 && !/^\d+$/.test(last) ? ` — ${last.replace(/-/g, " ")}` : "";
  return { kind: "create", label: `${/^\d+$/.test(last) || parts.length === 1 ? "Added" : "Updated"} ${noun}${action}` };
}

export function auditTrail(req: Request, res: Response, next: NextFunction) {
  const m = req.method;
  if (!["POST", "PUT", "PATCH", "DELETE"].includes(m) || !req.user || SKIP.some(([sm, re]) => sm === m && re.test(req.path))) return next();
  const res_ = RESOURCES.find(([re]) => re.test(req.path));
  const id = res_ ? Number(req.path.match(res_[0])![1]) : null;
  const before = res_ && id ? snapshot(res_[1], id) : null;
  const startId = get("SELECT COALESCE(MAX(id),0) v FROM audit_log")!.v as number;
  const user = req.user;
  const body = redact(req.body);
  res.on("finish", () => {
    if (res.statusCode >= 400) return;
    try {
      // a screen that already wrote its own, more detailed entry for this request wins
      if (get("SELECT id FROM audit_log WHERE id > ? AND user_id=? LIMIT 1", startId, user.id)) return;
      const after = res_ && id && m !== "DELETE" ? snapshot(res_[1], id) : null;
      const changes = before && after ? Object.keys(after).filter((k) => !HIDDEN.has(k) && !["created_at", "updated_at"].includes(k) && JSON.stringify(before[k]) !== JSON.stringify(after[k]))
        .map((k) => ({ field: k, from: before[k], to: after[k] })) : [];
      if (m !== "DELETE" && before && after && !changes.length && m !== "POST") return; // nothing really changed
      const d = describe(m, req.path);
      const label = before?.name || before?.title || before?.category ? `${d.label}: ${before.name ?? before.title ?? before.category}` : d.label;
      run("INSERT INTO audit_log (tenant_id,user_id,user_name,action,ref,data,created_at) VALUES (?,?,?,?,?,?,?)",
        user.tenant_id, user.id, user.name, `${d.kind}:${label}`, `${m} ${req.path}`,
        JSON.stringify({ body, changes, ...(m === "DELETE" && before ? { deleted: redact(before) } : {}) }), now());
    } catch (e) { console.error("[audit]", (e as Error).message); }
  });
  next();
}

/* ================= Page ================= */
const KINDS: Record<string, string> = {
  price: "price", undo: "undo|sale_undo|shop_sale_undo", delete: "delete", edit: "edit", create: "create", approval: "approval", settings: "settings|integrations_changed|setup|app_restart",
  login: "login", money: "coupons|wallet|claim|staff_loan",
};
function query(t: number, q: Record<string, unknown>) {
  const where = ["tenant_id=?"], args: (string | number)[] = [t];
  if (q.from) { where.push("created_at >= ?"); args.push(pkStart(String(q.from))); }
  if (q.to) { where.push("created_at < ?"); args.push(pkEnd(String(q.to))); }
  if (q.user_id) { where.push("user_id=?"); args.push(Number(q.user_id)); }
  if (q.kind && KINDS[String(q.kind)]) { where.push(`(${KINDS[String(q.kind)].split("|").map(() => "action LIKE ?").join(" OR ")})`); args.push(...KINDS[String(q.kind)].split("|").map((k) => `${k}%`)); }
  if (q.q) { where.push("(action LIKE ? OR ref LIKE ? OR data LIKE ? OR user_name LIKE ?)"); const s = `%${String(q.q)}%`; args.push(s, s, s, s); }
  return { where: where.join(" AND "), args };
}
/** Older entries (written by a screen directly) have plain action names; give them the same shape. */
function shape(r: any) {
  let data: any = null;
  try { data = JSON.parse(r.data ?? "null"); } catch { data = r.data; }
  const [kind, ...rest] = String(r.action).includes(":") ? String(r.action).split(":") : [r.action.includes("undo") ? "undo" : r.action.startsWith("login") ? "login" : "other", r.action.replace(/_/g, " ")];
  return { id: r.id, at: r.created_at, user_id: r.user_id, user: r.user_name, kind, label: rest.join(":"), ref: r.ref, data };
}
const filters = z.object({ from: z.string().optional(), to: z.string().optional(), user_id: z.coerce.number().optional(), kind: z.string().optional(), q: z.string().max(80).optional(), page: z.coerce.number().min(1).default(1) });
auditRouter.get("/audit", requirePerm("audit.view"), h((req) => {
  const f = parse(filters, req.query);
  const { where, args } = query(tid(req), f);
  const total = get(`SELECT COUNT(*) n FROM audit_log WHERE ${where}`, ...args)!.n;
  const rows = all(`SELECT * FROM audit_log WHERE ${where} ORDER BY id DESC LIMIT 100 OFFSET ?`, ...args, (f.page - 1) * 100).map(shape);
  return { total, page: f.page, pages: Math.max(1, Math.ceil(total / 100)), rows, users: all("SELECT DISTINCT user_id id, user_name name FROM audit_log WHERE tenant_id=? AND user_id IS NOT NULL ORDER BY user_name", tid(req)) };
}));
auditRouter.get("/audit.csv", requirePerm("audit.view"), (req, res, next) => {
  try {
    const f = parse(filters, req.query);
    const { where, args } = query(tid(req), f);
    const esc = (v: unknown) => `"${String(v ?? "").replaceAll('"', '""')}"`;
    const rows = all(`SELECT * FROM audit_log WHERE ${where} ORDER BY id DESC LIMIT 20000`, ...args).map(shape);
    const out = [["Time", "User", "Type", "What", "Changes", "Request"], ...rows.map((r) => [r.at, r.user, r.kind, r.label,
      (r.data?.changes ?? []).map((c: any) => `${c.field}: ${c.from} → ${c.to}`).join("; "), r.ref])].map((x) => x.map(esc).join(",")).join("\n");
    res.setHeader("Content-Type", "text/csv; charset=utf-8");
    res.setHeader("Content-Disposition", 'attachment; filename="audit-log.csv"');
    res.send("﻿" + out);
  } catch (e) { next(e); }
});

