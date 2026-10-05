/** Expense management: categories with monthly budgets, approval flow, monthly summary vs revenue. */
import { Router, type Request } from "express";
import { z } from "zod";
import { all, get, run, now, pkDate, getSetting, setSetting } from "../db.js";
import { h, parse, tid, requirePerm, requireAny, can } from "../auth.js";
import { bankAccountFor, accountIdField } from "./banks.js";
import { AppError, createAlert, pkr, round2 } from "../services.js";
import { linkPhotos } from "./capture.js";
import { guardClosedDay } from "./backoffice.js";
import { registerDecider, requestApproval, closeApproval } from "./approvals.js";

export const expenses = Router();

export const DEFAULT_CATEGORIES: [string, number | null][] = [
  ["Salaries & wages", null], ["Electricity (bijli)", null], ["Generator fuel", null], ["Maintenance & repairs", null],
  ["Rent", null], ["Taxes & fees", null], ["Bank charges", null], ["Tanker freight & transport", null],
  ["Office & stationery", null], ["Tea & food", null], ["Security", null], ["Other", null],
];

export function ensureCategories(tenantId: number) {
  if (get("SELECT id FROM expense_categories WHERE tenant_id=? LIMIT 1", tenantId)) return;
  for (const [name, budget] of DEFAULT_CATEGORIES) run("INSERT INTO expense_categories (tenant_id,name,monthly_budget) VALUES (?,?,?)", tenantId, name, budget);
}

const approvalLimit = (t: number) => Number(getSetting(t, "expense_approval_limit", "10000"));
const monthOf = (d: string) => d.slice(0, 7);
const nextMonth = (m: string) => { const [y, mo] = m.split("-").map(Number); return mo === 12 ? `${y + 1}-01` : `${y}-${String(mo + 1).padStart(2, "0")}`; };
const prevMonth = (m: string) => { const [y, mo] = m.split("-").map(Number); return mo === 1 ? `${y - 1}-12` : `${y}-${String(mo - 1).padStart(2, "0")}`; };

function checkBudget(t: number, category: string, month: string) {
  const cat = get("SELECT monthly_budget FROM expense_categories WHERE tenant_id=? AND name=?", t, category);
  if (!cat?.monthly_budget) return;
  const spent = get("SELECT COALESCE(SUM(amount),0) s FROM expenses WHERE tenant_id=? AND category=? AND status='approved' AND expense_date >= ? AND expense_date < ?",
    t, category, month + "-01", nextMonth(month) + "-01")!.s;
  if (spent > cat.monthly_budget)
    createAlert(t, { type: "expense_budget", severity: "warning", title: `${category} over budget for ${month}`,
      body: `Spent ${pkr(spent)} of ${pkr(cat.monthly_budget)} budget.`, dedupe_key: `budget-${category}-${month}` });
}

/* ---------------- Categories & settings ---------------- */
expenses.get("/expense-categories", requireAny("expenses.view", "cash.pay"), h((req) => {
  ensureCategories(tid(req));
  return { categories: all("SELECT * FROM expense_categories WHERE tenant_id=? ORDER BY name", tid(req)), approval_limit: approvalLimit(tid(req)) };
}));
expenses.post("/expense-categories", requirePerm("expenses.approve"), h((req) => {
  const b = parse(z.object({ name: z.string().min(2), monthly_budget: z.number().min(0).nullable().optional() }), req.body);
  if (get("SELECT id FROM expense_categories WHERE tenant_id=? AND name=?", tid(req), b.name)) throw new AppError(400, "Category already exists");
  return get("SELECT * FROM expense_categories WHERE id=?", run("INSERT INTO expense_categories (tenant_id,name,monthly_budget) VALUES (?,?,?)", tid(req), b.name, b.monthly_budget ?? null).id);
}));
expenses.patch("/expense-categories/:id", requirePerm("expenses.approve"), h((req) => {
  const b = parse(z.object({ monthly_budget: z.number().min(0).nullable() }), req.body);
  run("UPDATE expense_categories SET monthly_budget=? WHERE id=? AND tenant_id=?", b.monthly_budget, Number(req.params.id), tid(req));
  return get("SELECT * FROM expense_categories WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
}));
expenses.put("/expenses/settings", requirePerm("expenses.approve"), h((req) => {
  const b = parse(z.object({ approval_limit: z.number().min(0) }), req.body);
  setSetting(tid(req), "expense_approval_limit", String(b.approval_limit));
  return { approval_limit: b.approval_limit };
}));

/* ---------------- List & summary ---------------- */
function filters(req: Request) {
  const q = parse(z.object({
    month: z.string().regex(/^\d{4}-\d{2}$/).optional(), category: z.string().optional(),
    station_id: z.coerce.number().optional(), status: z.enum(["pending", "approved", "rejected"]).optional(),
  }), req.query);
  const month = q.month ?? new Date().toISOString().slice(0, 7);
  return { ...q, month };
}

function listExpenses(t: number, f: ReturnType<typeof filters>) {
  const where = ["e.tenant_id=?", "e.expense_date >= ?", "e.expense_date < ?"];
  const args: (string | number)[] = [t, f.month + "-01", nextMonth(f.month) + "-01"];
  if (f.category) { where.push("e.category=?"); args.push(f.category); }
  if (f.station_id) { where.push("e.station_id=?"); args.push(f.station_id); }
  if (f.status) { where.push("e.status=?"); args.push(f.status); }
  return all(`SELECT e.*, s.name station_name FROM expenses e LEFT JOIN stations s ON s.id=e.station_id WHERE ${where.join(" AND ")} ORDER BY e.expense_date DESC, e.id DESC`, ...args);
}

expenses.get("/expenses", requirePerm("expenses.view"), h((req) => {
  const t = tid(req);
  ensureCategories(t);
  const f = filters(req);
  const start = f.month + "-01", end = nextMonth(f.month) + "-01";
  const pm = prevMonth(f.month);
  const total = (from: string, to: string) => get("SELECT COALESCE(SUM(amount),0) s FROM expenses WHERE tenant_id=? AND status='approved' AND expense_date >= ? AND expense_date < ?", t, from, to)!.s;
  const byCategory = all(
    `SELECT c.name category, c.monthly_budget budget,
       COALESCE((SELECT SUM(amount) FROM expenses e WHERE e.tenant_id=c.tenant_id AND e.category=c.name AND e.status='approved' AND e.expense_date >= ? AND e.expense_date < ?),0) spent,
       COALESCE((SELECT SUM(amount) FROM expenses e WHERE e.tenant_id=c.tenant_id AND e.category=c.name AND e.status='approved' AND e.expense_date >= ? AND e.expense_date < ?),0) / 3.0 avg_3m
     FROM expense_categories c WHERE c.tenant_id=? ORDER BY spent DESC`,
    start, end, prevMonth(prevMonth(pm)) + "-01", start, t);
  const retail = get(`SELECT COALESCE(SUM(s.amount),0) a FROM sales s JOIN stations st ON st.id=s.station_id WHERE st.tenant_id=? AND s.created_at >= ? AND s.created_at < ?`, t, start, end)!.a;
  const wholesaleBilled = get(`SELECT COALESCE(SUM(CASE WHEN type='supply' THEN amount WHEN type='return' THEN -amount END),0) a FROM wholesale_txns WHERE tenant_id=? AND voided=0 AND txn_date >= ? AND txn_date < ?`, t, start, end)!.a;
  const monthTotal = total(start, end);
  return {
    month: f.month,
    expenses: listExpenses(t, f),
    summary: {
      total: round2(monthTotal), previous_month: round2(total(pm + "-01", f.month + "-01")),
      pending: get("SELECT COUNT(*) n, COALESCE(SUM(amount),0) s FROM expenses WHERE tenant_id=? AND status='pending'", t),
      by_category: byCategory.map((c) => ({ ...c, avg_3m: round2(c.avg_3m), over_budget: c.budget ? c.spent > c.budget : false })),
      by_method: all("SELECT method, ROUND(SUM(amount)) total FROM expenses WHERE tenant_id=? AND status='approved' AND expense_date >= ? AND expense_date < ? GROUP BY method", t, start, end),
      revenue: { retail: round2(retail), wholesale: round2(wholesaleBilled), total: round2(retail + wholesaleBilled) },
      revenue_minus_expenses: round2(retail + wholesaleBilled - monthTotal),
    },
    approval_limit: approvalLimit(t),
  };
}));

expenses.get("/expenses.csv", requirePerm("expenses.view"), (req, res, next) => {
  try {
    const f = filters(req);
    const rows = listExpenses(tid(req), f);
    const esc = (v: unknown) => `"${String(v ?? "").replaceAll('"', '""')}"`;
    const out = [["Date", "Category", "Amount", "Paid to", "Method", "Station", "Status", "Note", "Receipt", "Entered by", "Approved by"].map(esc).join(","),
      ...rows.map((e) => [e.expense_date.slice(0, 10), e.category, e.amount, e.paid_to, e.method, e.station_name ?? "All", e.status, e.note, e.receipt_ref, e.created_by, e.approved_by].map(esc).join(","))].join("\n");
    res.setHeader("Content-Type", "text/csv; charset=utf-8");
    res.setHeader("Content-Disposition", `attachment; filename="expenses-${f.month}.csv"`);
    res.send("﻿" + out);
  } catch (e) { next(e); }
});

/* ---------------- Create / edit / approve ---------------- */
export const expenseBody = z.object({
  category: z.string().min(2), amount: z.number().positive(), paid_to: z.string().optional().nullable(),
  method: z.enum(["cash", "bank", "jazzcash", "easypaisa", "raast", "cheque", "card"]).default("cash"),
  note: z.string().optional().nullable(), receipt_ref: z.string().optional().nullable(),
  station_id: z.number().nullable().optional(), expense_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  photo_id: z.number().optional().nullable(), account_id: accountIdField,
});

expenses.post("/expenses", requireAny("expenses.create", "cash.pay"), h((req) => createExpense(req, parse(expenseBody, req.body))));

/** Book an expense (approved at once up to the approval limit, else sent to the owner). Also used by the cashier desk. */
export async function createExpense(req: Request, b: z.infer<typeof expenseBody>) {
  const t = tid(req);
  if (!get("SELECT id FROM expense_categories WHERE tenant_id=? AND name=?", t, b.category)) throw new AppError(400, "Unknown category");
  if (b.station_id && !get("SELECT id FROM stations WHERE id=? AND tenant_id=?", b.station_id, t)) throw new AppError(400, "Station not found");
  const autoApprove = can(req.user, "expenses.approve") || b.amount <= approvalLimit(t);
  const date = b.expense_date ?? pkDate();
  guardClosedDay(req, t, date);
  const { id } = run(
    `INSERT INTO expenses (tenant_id,station_id,category,amount,paid_to,method,note,receipt_ref,status,created_by,approved_by,expense_date,created_at,account_id)
     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    t, b.station_id ?? null, b.category, b.amount, b.paid_to ?? null, b.method, b.note ?? null, b.receipt_ref ?? null,
    autoApprove ? "approved" : "pending", req.user!.name, autoApprove ? req.user!.name : null, date, now(), bankAccountFor(t, b.account_id, b.method),
  );
  if (b.photo_id && linkPhotos(t, [b.photo_id], `expense:${id}`)) { run("UPDATE expenses SET photo_id=? WHERE id=?", b.photo_id, id); }
  if (autoApprove) checkBudget(t, b.category, monthOf(date));
  else {
    createAlert(t, { type: "expense_approval", severity: "warning", title: `Expense needs approval: ${pkr(b.amount)} ${b.category}`,
      body: `Entered by ${req.user!.name}${b.paid_to ? ` · paid to ${b.paid_to}` : ""}${b.note ? ` · ${b.note}` : ""}` });
    await requestApproval(t, "expense", id, `💸 Expense ${pkr(b.amount)} — ${b.category}\nBy ${req.user!.name}${b.paid_to ? ` · paid to ${b.paid_to}` : ""}${b.note ? ` · ${b.note}` : ""}`);
  }
  return get("SELECT * FROM expenses WHERE id=?", id)!;
}

function ownExpense(req: Request) {
  const e = get("SELECT * FROM expenses WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  if (!e) throw new AppError(404, "Expense not found");
  if (!can(req.user, "expenses.approve") && (e.created_by !== req.user!.name || e.status !== "pending"))
    throw new AppError(403, "You can only change your own pending expenses");
  return e;
}

expenses.patch("/expenses/:id", requirePerm("expenses.create"), h((req) => {
  const e = ownExpense(req);
  const b = parse(expenseBody.partial(), req.body);
  const m = { ...e, ...b };
  guardClosedDay(req, tid(req), e.expense_date);
  guardClosedDay(req, tid(req), m.expense_date);
  run("UPDATE expenses SET category=?, amount=?, paid_to=?, method=?, note=?, receipt_ref=?, station_id=?, expense_date=?, account_id=? WHERE id=?",
    m.category, m.amount, m.paid_to ?? null, m.method, m.note ?? null, m.receipt_ref ?? null, m.station_id ?? null, m.expense_date, bankAccountFor(tid(req), m.account_id, m.method), e.id);
  return get("SELECT * FROM expenses WHERE id=?", e.id);
}));

expenses.delete("/expenses/:id", requirePerm("expenses.create"), h((req) => {
  const e = ownExpense(req);
  guardClosedDay(req, tid(req), e.expense_date);
  run("DELETE FROM expenses WHERE id=?", e.id);
  closeApproval("expense", e.id, false, `${req.user!.name} (deleted)`);
  return { ok: true };
}));

/** Approve or reject a pending expense (from the app, or the owner's "1" on WhatsApp). */
export function decideExpense(t: number, id: number, approve: boolean, by: string) {
  const e = get("SELECT * FROM expenses WHERE id=? AND tenant_id=?", id, t);
  if (!e) throw new AppError(404, "Expense not found");
  if (e.status !== "pending") throw new AppError(400, `Expense is already ${e.status}`);
  const status = approve ? "approved" : "rejected";
  run("UPDATE expenses SET status=?, approved_by=? WHERE id=?", status, by, e.id);
  closeApproval("expense", e.id, approve, by);
  if (approve) checkBudget(t, e.category, monthOf(e.expense_date));
  return get("SELECT * FROM expenses WHERE id=?", e.id);
}
registerDecider("expense", async (t, id, approve, by) => { const e = decideExpense(t, id, approve, by); return `${pkr(e.amount)} ${e.category} is now ${e.status}.`; });

expenses.post("/expenses/:id/:decision(approve|reject)", requirePerm("expenses.approve"), h((req) =>
  decideExpense(tid(req), Number(req.params.id), req.params.decision === "approve", req.user!.name)));
