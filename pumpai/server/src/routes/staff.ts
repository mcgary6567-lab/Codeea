/**
 * Staff khata: advances, salary, cash shortages from shifts and repayments, so the salary register
 * is kept by the app. Balance = what the staff member owes the business (advances + shortages −
 * repayments − salary deductions).
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, tx, now, pkDate, getSetting } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { AppError, round2, pkr } from "../services.js";
import { notify } from "../notifications.js";
import { attendanceMonth } from "./compliance.js";

export const staffRouter = Router();
staffRouter.use("/staff", requirePerm("staff.manage"));

const OWES = "CASE WHEN type IN ('advance','shortage') THEN amount WHEN type IN ('repayment','deduction') THEN -amount ELSE 0 END";
export const staffBalance = (userId: number) => round2(get(`SELECT COALESCE(SUM(${OWES}),0) b FROM staff_ledger WHERE user_id=?`, userId)!.b);

function ledger(userId: number) {
  let bal = 0;
  return all("SELECT * FROM staff_ledger WHERE user_id=? ORDER BY created_at, id", userId).map((l) => {
    bal = round2(bal + (["advance", "shortage"].includes(l.type) ? l.amount : ["repayment", "deduction"].includes(l.type) ? -l.amount : 0));
    return { ...l, balance: bal };
  }).reverse();
}

function ownUser(tenantId: number, id: number) {
  const u = get("SELECT id, name, role, phone, salary, station_id, active FROM users WHERE id=? AND tenant_id=?", id, tenantId);
  if (!u) throw new AppError(404, "Staff member not found");
  return u;
}

staffRouter.get("/staff", h((req) => {
  const month = pkDate().slice(0, 7);
  return all(`SELECT u.id, u.name, u.role, u.phone, u.salary, u.active, s.name station_name FROM users u LEFT JOIN stations s ON s.id=u.station_id
    WHERE u.tenant_id=? ORDER BY u.active DESC, CASE u.role WHEN 'salesman' THEN 0 WHEN 'manager' THEN 1 ELSE 2 END, u.name`, tid(req)).map((u) => ({
    ...u, balance: staffBalance(u.id),
    shortages_this_month: round2(get("SELECT COALESCE(SUM(amount),0) s FROM staff_ledger WHERE user_id=? AND type='shortage' AND created_at >= ?", u.id, new Date(`${month}-01T00:00:00+05:00`).toISOString())!.s),
    salary_paid_this_month: Boolean(get("SELECT id FROM staff_ledger WHERE user_id=? AND type='salary' AND month=?", u.id, month)),
  }));
}));

staffRouter.get("/staff/:id", h((req) => {
  const u = ownUser(tid(req), Number(req.params.id));
  return { user: u, balance: staffBalance(u.id), lines: ledger(u.id), attendance: attendanceMonth(tid(req), u.id) };
}));

staffRouter.patch("/staff/:id", h((req) => {
  const u = ownUser(tid(req), Number(req.params.id));
  const b = parse(z.object({ salary: z.number().min(0).max(10_000_000).nullable() }), req.body);
  run("UPDATE users SET salary=? WHERE id=?", b.salary, u.id);
  return ownUser(tid(req), u.id);
}));

staffRouter.post("/staff/:id/entry", h(async (req) => {
  const u = ownUser(tid(req), Number(req.params.id));
  const b = parse(z.object({ type: z.enum(["advance", "repayment", "bonus", "shortage", "deduction"]), amount: z.number().positive().max(10_000_000), note: z.string().max(200).optional().nullable() }), req.body);
  if (["repayment", "deduction"].includes(b.type) && b.amount > staffBalance(u.id) + 0.01) throw new AppError(400, `${u.name} owes only ${pkr(staffBalance(u.id))}`);
  run("INSERT INTO staff_ledger (tenant_id,user_id,type,amount,note,created_by,created_at) VALUES (?,?,?,?,?,?,?)", tid(req), u.id, b.type, b.amount, b.note ?? null, req.user!.name, now());
  if (b.type === "advance" || b.type === "bonus")
    await notify(tid(req), [u], { type: "staff_ledger", title: b.type === "advance" ? `Advance ${pkr(b.amount)} given` : `Bonus ${pkr(b.amount)} 🎉`, body: `Your account: ${pkr(staffBalance(u.id))} to be adjusted.` });
  return { balance: staffBalance(u.id), lines: ledger(u.id) };
}));

/** Pay a month's salary: optional bonus, recover advances/shortages, book the salary expense. */
staffRouter.post("/staff/:id/pay-salary", h(async (req) => {
  const u = ownUser(tid(req), Number(req.params.id));
  const b = parse(z.object({ month: z.string().regex(/^\d{4}-\d{2}$/).optional(), deduct: z.number().min(0).default(0), bonus: z.number().min(0).default(0), note: z.string().max(200).optional(),
    absence_cut: z.number().min(0).optional() }), req.body);
  const month = b.month ?? pkDate().slice(0, 7);
  if (!u.salary) throw new AppError(400, `Set ${u.name}'s monthly salary first`);
  if (get("SELECT id FROM staff_ledger WHERE user_id=? AND type='salary' AND month=?", u.id, month)) throw new AppError(400, `${u.name}'s salary for ${month} is already paid`);
  if (b.deduct > staffBalance(u.id) + 0.01) throw new AppError(400, `Deduction is more than ${u.name} owes (${pkr(staffBalance(u.id))})`);
  // unpaid absences (staff with a duty time) are cut at salary / 30 per day unless the manager changes it
  const absenceCut = round2(Math.min(u.salary, b.absence_cut ?? attendanceMonth(tid(req), u.id, b.month ?? pkDate().slice(0, 7)).salary_cut));
  const gross = round2(u.salary - absenceCut + b.bonus);
  if (b.deduct > gross) throw new AppError(400, "Deduction is more than the salary");
  const net = round2(gross - b.deduct);
  const t = tid(req);
  tx(() => {
    const ins = (type: string, amount: number, note: string) =>
      run("INSERT INTO staff_ledger (tenant_id,user_id,type,amount,note,month,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)", t, u.id, type, amount, note, month, req.user!.name, now());
    if (b.bonus) ins("bonus", b.bonus, b.note ?? `Bonus ${month}`);
    if (b.deduct) ins("deduction", b.deduct, `Recovered from ${month} salary`);
    ins("salary", net, `Salary ${month}: ${pkr(u.salary)}${absenceCut ? ` − ${pkr(absenceCut)} absences` : ""}${b.bonus ? ` + bonus ${pkr(b.bonus)}` : ""}${b.deduct ? ` − ${pkr(b.deduct)} advance/shortage` : ""}`);
    // the expense book shows the full salary cost; the recovered part was paid out earlier as an advance
    if (get("SELECT id FROM expense_categories WHERE tenant_id=? AND name='Salaries & wages'", t))
      run(`INSERT INTO expenses (tenant_id,station_id,category,amount,paid_to,method,note,status,created_by,approved_by,expense_date,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`, t, u.station_id ?? null, "Salaries & wages", gross, u.name, "cash", `Salary ${month}`, "approved", req.user!.name, req.user!.name, pkDate(), now());
  });
  await notify(t, [u], { type: "staff_ledger", title: `Salary paid: ${pkr(net)}`, body: `${month}${b.deduct ? ` · ${pkr(b.deduct)} adjusted from advance/shortage` : ""}. Remaining to adjust: ${pkr(staffBalance(u.id))}.` });
  return { net, absence_cut: absenceCut, balance: staffBalance(u.id), lines: ledger(u.id) };
}));

/** Every staff member can see their own account. */
staffRouter.get("/me/account", h((req) => {
  const u = ownUser(tid(req), req.user!.id);
  return { user: u, balance: staffBalance(u.id), lines: ledger(u.id).slice(0, 60) };
}));

/** Shift closed short: the shortage goes on the salesman's account (setting "shortage_to_staff"). */
export async function chargeShortage(tenantId: number, shift: { id: number; attendant: string; station_id: number }, variance: number) {
  if (variance >= 0 || getSetting(tenantId, "shortage_to_staff", "1") === "0") return;
  if (-variance < Number(getSetting(tenantId, "shortage_min", "100"))) return; // ignore small change differences
  const u = get("SELECT id, phone FROM users WHERE tenant_id=? AND name=? AND role='salesman' AND active=1", tenantId, shift.attendant);
  if (!u) return;
  run("INSERT INTO staff_ledger (tenant_id,user_id,type,amount,note,ref,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
    tenantId, u.id, "shortage", round2(-variance), `Cash short in shift #${shift.id}`, `shift:${shift.id}`, "Auto", now());
  await notify(tenantId, [u], { type: "staff_ledger", title: `Cash short ${pkr(-variance)} added to your account`, body: `Shift #${shift.id}. Total to adjust: ${pkr(staffBalance(u.id))}.` });
}
