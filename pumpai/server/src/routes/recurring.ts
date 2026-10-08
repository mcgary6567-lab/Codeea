/**
 * Fixed monthly costs and utility bills:
 *  - Recurring expenses (rent, security, internet, salaries of guards…) are booked by themselves each month
 *  - Electricity / gas bill: take a photo, AI reads the units and amount; alert when it is higher than last month
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, now, pkDate, getSetting } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { AppError, round2, pkr, createAlert } from "../services.js";
import { notify, staff } from "../notifications.js";
import { sendDirect } from "../whatsapp/cloud.js";
import { linkPhotos } from "./capture.js";
import { ensureCategories } from "./expenses.js";

export const recurring = Router();
const METHODS = ["cash", "bank", "jazzcash", "easypaisa", "raast", "cheque", "card"] as const;

/* ================= Recurring expenses ================= */
recurring.get("/recurring-expenses", requirePerm("expenses.view"), h((req) =>
  all("SELECT r.*, s.name station_name FROM recurring_expenses r LEFT JOIN stations s ON s.id=r.station_id WHERE r.tenant_id=? ORDER BY r.active DESC, r.day_of_month, r.id", tid(req))));

const body = z.object({
  category: z.string().min(2), amount: z.number().positive().max(100_000_000), paid_to: z.string().max(80).optional().nullable(),
  method: z.enum(METHODS).default("cash"), day_of_month: z.number().int().min(1).max(31).default(1), note: z.string().max(200).optional().nullable(),
  station_id: z.number().optional().nullable(), active: z.boolean().optional(),
});
recurring.post("/recurring-expenses", requirePerm("expenses.approve"), h((req) => {
  const t = tid(req);
  const b = parse(body, req.body);
  ensureCategories(t);
  if (!get("SELECT id FROM expense_categories WHERE tenant_id=? AND name=? AND active=1", t, b.category)) throw new AppError(400, "Unknown category");
  // starts next time the day comes round; if that day already passed this month, it starts next month
  const started = Number(pkDate().slice(8)) >= b.day_of_month ? pkDate().slice(0, 7) : null;
  const { id } = run(`INSERT INTO recurring_expenses (tenant_id,station_id,category,amount,paid_to,method,day_of_month,note,last_month,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)`,
    t, b.station_id ?? null, b.category, b.amount, b.paid_to ?? null, b.method, b.day_of_month, b.note ?? null, started, req.user!.name, now());
  return get("SELECT * FROM recurring_expenses WHERE id=?", id);
}));
recurring.patch("/recurring-expenses/:id", requirePerm("expenses.approve"), h((req) => {
  const r = get("SELECT * FROM recurring_expenses WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  if (!r) throw new AppError(404, "Not found");
  const b = parse(body.partial(), req.body);
  const m = { ...r, ...b };
  run("UPDATE recurring_expenses SET category=?, amount=?, paid_to=?, method=?, day_of_month=?, note=?, station_id=?, active=? WHERE id=?",
    m.category, m.amount, m.paid_to ?? null, m.method, m.day_of_month, m.note ?? null, m.station_id ?? null, b.active === undefined ? r.active : b.active ? 1 : 0, r.id);
  return get("SELECT * FROM recurring_expenses WHERE id=?", r.id);
}));
recurring.delete("/recurring-expenses/:id", requirePerm("expenses.approve"), h((req) => {
  run("DELETE FROM recurring_expenses WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  return { ok: true };
}));

/** Daily: book every recurring expense whose day has come this month (once a month each). */
export async function bookRecurring(t: number, today = pkDate()) {
  const month = today.slice(0, 7), dom = Number(today.slice(8));
  const daysInMonth = new Date(Date.UTC(Number(month.slice(0, 4)), Number(month.slice(5)), 0)).getUTCDate();
  const booked: string[] = [];
  for (const r of all("SELECT * FROM recurring_expenses WHERE tenant_id=? AND active=1 AND (last_month IS NULL OR last_month < ?)", t, month)) {
    const due = Math.min(r.day_of_month, daysInMonth);
    if (dom < due) continue;
    const date = `${month}-${String(due).padStart(2, "0")}`;
    run(`INSERT INTO expenses (tenant_id,station_id,category,amount,paid_to,method,note,status,created_by,approved_by,expense_date,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`, t, r.station_id, r.category, r.amount, r.paid_to, r.method, `${r.note ? `${r.note} · ` : ""}monthly (auto)`, "approved", "Auto (monthly)", "Auto (monthly)", date, now());
    run("UPDATE recurring_expenses SET last_month=? WHERE id=?", month, r.id);
    booked.push(`${r.category} ${pkr(r.amount)}${r.paid_to ? ` → ${r.paid_to}` : ""}`);
  }
  if (booked.length)
    await notify(t, staff(t, ["admin", "manager"]), { type: "recurring_expenses", whatsapp: false, title: `${booked.length} monthly expense${booked.length > 1 ? "s" : ""} booked`, body: booked.join("\n") });
  return booked.length;
}

/* ================= Utility bills ================= */
const KIND_CATEGORY: Record<string, string> = { electricity: "Electricity (bijli)", gas: "Other", water: "Other", phone: "Office & stationery" };

recurring.get("/utility-bills", requirePerm("expenses.view"), h((req) =>
  all("SELECT b.*, s.name station_name FROM utility_bills b LEFT JOIN stations s ON s.id=b.station_id WHERE b.tenant_id=? ORDER BY b.month DESC, b.id DESC LIMIT 60", tid(req))));

recurring.post("/utility-bills", requirePerm("expenses.create"), h(async (req) => {
  const t = tid(req);
  const b = parse(z.object({
    kind: z.enum(["electricity", "gas", "water", "phone"]).default("electricity"), month: z.string().regex(/^\d{4}-\d{2}$/).optional(),
    units: z.number().min(0).optional().nullable(), amount: z.number().positive().optional().nullable(), due_date: z.string().max(20).optional().nullable(),
    reference: z.string().max(40).optional().nullable(), photo_id: z.number().optional().nullable(), station_id: z.number().optional().nullable(),
    add_expense: z.boolean().default(true), method: z.enum(METHODS).default("bank"),
  }), req.body);
  // fill in anything the AI read from the bill photo
  const ai = b.photo_id ? JSON.parse(get("SELECT ai_result FROM photos WHERE id=? AND tenant_id=?", b.photo_id, t)?.ai_result ?? "null") : null;
  const amount = b.amount ?? (ai?.amount ? Number(ai.amount) : null);
  const units = b.units ?? (ai?.units != null ? Number(ai.units) : null);
  if (!amount) throw new AppError(400, "Enter the bill amount (the photo could not be read)");
  const month = b.month ?? (typeof ai?.month === "string" && /^\d{4}-\d{2}$/.test(ai.month) ? ai.month : pkDate().slice(0, 7));
  if (get("SELECT id FROM utility_bills WHERE tenant_id=? AND kind=? AND month=? AND COALESCE(station_id,0)=?", t, b.kind, month, b.station_id ?? 0))
    throw new AppError(400, `The ${b.kind} bill for ${month} is already entered`);
  const prev = get("SELECT * FROM utility_bills WHERE tenant_id=? AND kind=? AND COALESCE(station_id,0)=? AND month < ? ORDER BY month DESC LIMIT 1", t, b.kind, b.station_id ?? 0, month);
  const change = prev?.amount ? round2(((amount - prev.amount) / prev.amount) * 100) : null;
  const unitChange = prev?.units && units != null ? round2(((units - prev.units) / prev.units) * 100) : null;
  let expenseId: number | null = null;
  if (b.add_expense) {
    ensureCategories(t);
    const cat = get("SELECT name FROM expense_categories WHERE tenant_id=? AND name=?", t, KIND_CATEGORY[b.kind]) ? KIND_CATEGORY[b.kind] : "Other";
    expenseId = run(`INSERT INTO expenses (tenant_id,station_id,category,amount,paid_to,method,note,receipt_ref,status,created_by,approved_by,expense_date,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`, t, b.station_id ?? null, cat, amount, `${b.kind} bill`, b.method, `${b.kind} ${month}${units != null ? ` · ${units} units` : ""}`,
      b.reference ?? ai?.reference ?? null, "approved", req.user!.name, req.user!.name, pkDate(), now()).id;
  }
  const { id } = run(`INSERT INTO utility_bills (tenant_id,station_id,kind,month,units,amount,due_date,reference,photo_id,expense_id,prev_amount,prev_units,change_pct,created_by,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`, t, b.station_id ?? null, b.kind, month, units, amount, b.due_date ?? ai?.due_date ?? null, b.reference ?? ai?.reference ?? null,
  null, expenseId, prev?.amount ?? null, prev?.units ?? null, change, req.user!.name, now());
  if (b.photo_id && linkPhotos(t, [b.photo_id], `bill:${id}`)) {
    run("UPDATE utility_bills SET photo_id=? WHERE id=?", b.photo_id, id);
    if (expenseId) run("UPDATE expenses SET photo_id=? WHERE id=?", b.photo_id, expenseId);
  }
  const limit = Number(getSetting(t, "utility_alert_pct", "15"));
  const high = (change ?? 0) > limit || (unitChange ?? 0) > limit;
  if (high) {
    const title = `⚡ ${b.kind[0].toUpperCase() + b.kind.slice(1)} bill up ${Math.max(change ?? 0, unitChange ?? 0).toFixed(0)}% — ${pkr(amount)}`;
    const text = `${month}: ${pkr(amount)}${units != null ? `, ${units} units` : ""}. Last bill (${prev.month}): ${pkr(prev.amount)}${prev.units != null ? `, ${prev.units} units` : ""}. Check for a fault, wrong reading or extra load.`;
    createAlert(t, { station_id: b.station_id ?? null, type: "utility_high", severity: "warning", title, body: text, dedupe_key: `bill-${id}` });
    const owner = getSetting(t, "owner_phone") || get("SELECT owner_phone FROM tenants WHERE id=?", t)?.owner_phone;
    if (owner) await sendDirect(t, { phone: owner, name: "Owner" }, "utility_high", `bill:${id}`, `${title}\n${text}`);
  }
  return { ...get("SELECT * FROM utility_bills WHERE id=?", id)!, unit_change_pct: unitChange, high, read_by_ai: Boolean(ai) };
}));
