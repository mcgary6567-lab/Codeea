/**
 * Staff khata: advances, salary, cash shortages from shifts and repayments, so the salary register
 * is kept by the app. Balance = what the staff member owes the business (advances + shortages −
 * repayments − salary deductions).
 */
import { Router } from "express";
import { z } from "zod";
import { commissionForMonth } from "./feedback.js";
import { loanDue, loansOf, takeInstalments, saveSlip, slipUrl } from "./people.js";
import { all, get, run, tx, now, pkDate, getSetting } from "../db.js";
import { linkPhotos, proofPhotos, proofCol, requireProof } from "./capture.js";
import { h, parse, tid, can } from "../auth.js";
import { bankAccountFor } from "./banks.js";
import { AppError, round2, pkr } from "../services.js";
import { notify } from "../notifications.js";
import { attendanceMonth } from "./compliance.js";

export const staffRouter = Router();
// the cashier may hand out a staff advance (cash.pay); everything else here is the manager's
staffRouter.use("/staff", (req, _res, next) =>
  can(req.user, "staff.manage") || (req.method === "POST" && /^\/\d+\/entry$/.test(req.path) && can(req.user, "cash.pay"))
    ? next() : next(new AppError(403, "You don't have permission for this")));

const OWES = "CASE WHEN type IN ('advance','shortage') THEN amount WHEN type IN ('repayment','deduction') THEN -amount ELSE 0 END";
export const staffBalance = (userId: number) => round2(get(`SELECT COALESCE(SUM(${OWES}),0) b FROM staff_ledger WHERE user_id=?`, userId)!.b);

function ledger(userId: number) {
  let bal = 0;
  return all(`SELECT l.*, ${proofCol("'staff:'||l.id")} FROM staff_ledger l WHERE l.user_id=? ORDER BY l.created_at, l.id`, userId).map((l) => {
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
  return { user: u, balance: staffBalance(u.id), lines: ledger(u.id), attendance: attendanceMonth(tid(req), u.id), commission: commissionForMonth(tid(req), u.id, pkDate().slice(0, 7)),
    loans: loansOf(u.id), loan_due: round2(loanDue(u.id).reduce((a, d) => a + d.amount, 0)),
    loans_left: round2(loansOf(u.id).reduce((a, l) => a + (l.status === "closed" ? 0 : l.remaining), 0)) };
}));

staffRouter.patch("/staff/:id", h((req) => {
  const u = ownUser(tid(req), Number(req.params.id));
  const b = parse(z.object({ salary: z.number().min(0).max(10_000_000).nullable() }), req.body);
  run("UPDATE users SET salary=? WHERE id=?", b.salary, u.id);
  return ownUser(tid(req), u.id);
}));

staffRouter.post("/staff/:id/entry", h(async (req) => {
  const u = ownUser(tid(req), Number(req.params.id));
  const b = parse(z.object({ type: z.enum(["advance", "repayment", "bonus", "shortage", "deduction"]), amount: z.number().positive().max(10_000_000), note: z.string().max(200).optional().nullable(), photo_ids: proofPhotos,
    /** how the money moved (advance / bonus / repayment); cash when not given */
    method: z.string().max(30).optional().nullable(), account_id: z.number().int().positive().optional().nullable() }), req.body);
  if (b.type !== "advance" && !can(req.user, "staff.manage")) throw new AppError(403, "The cashier can only give an advance");
  if (["repayment", "deduction"].includes(b.type) && b.amount > staffBalance(u.id) + 0.01) throw new AppError(400, `${u.name} owes only ${pkr(staffBalance(u.id))}`);
  const moves = ["advance", "repayment", "bonus"].includes(b.type);
  const method = moves ? (b.method || "Cash") : null;
  const account = moves && method ? bankAccountFor(tid(req), b.account_id, method) : null;
  if (moves && !/^cash$/i.test(method!) && !account) throw new AppError(400, "Choose the bank account");
  const lid = run("INSERT INTO staff_ledger (tenant_id,user_id,type,amount,note,created_by,created_at,method,account_id) VALUES (?,?,?,?,?,?,?,?,?)", tid(req), u.id, b.type, b.amount, b.note ?? null, req.user!.name, now(), method, account).id;
  linkPhotos(tid(req), b.photo_ids, `staff:${lid}`);
  if (b.type === "advance" || b.type === "bonus")
    await notify(tid(req), [u], { type: "staff_ledger", title: b.type === "advance" ? `Advance ${pkr(b.amount)} given` : `Bonus ${pkr(b.amount)} 🎉`, body: `Your account: ${pkr(staffBalance(u.id))} to be adjusted.` });
  return { balance: staffBalance(u.id), lines: ledger(u.id) };
}));

/** Pay a month's salary: optional bonus, recover advances/shortages, book the salary expense. */
staffRouter.post("/staff/:id/pay-salary", h(async (req) => {
  const u = ownUser(tid(req), Number(req.params.id));
  const b = parse(z.object({ month: z.string().regex(/^\d{4}-\d{2}$/).optional(), deduct: z.number().min(0).default(0), bonus: z.number().min(0).default(0), note: z.string().max(200).optional(),
    absence_cut: z.number().min(0).optional(), commission: z.number().min(0).optional(), skip_loan: z.boolean().optional(), photo_ids: proofPhotos }), req.body);
  const month = b.month ?? pkDate().slice(0, 7);
  requireProof(tid(req), b.photo_ids, "signed salary sheet");
  if (!u.salary) throw new AppError(400, `Set ${u.name}'s monthly salary first`);
  if (get("SELECT id FROM staff_ledger WHERE user_id=? AND type='salary' AND month=?", u.id, month)) throw new AppError(400, `${u.name}'s salary for ${month} is already paid`);
  // loans are recovered by their own instalments; the manual cut is for advances and shortages only
  const loansLeft = round2(loansOf(u.id).reduce((a, l) => a + (l.status === "closed" ? 0 : l.remaining), 0));
  const cuttable = round2(staffBalance(u.id) - loansLeft);
  if (b.deduct > cuttable + 0.01) throw new AppError(400, `Deduction is more than ${u.name} owes in advances / shortages (${pkr(Math.max(0, cuttable))}); loans are cut by their instalment`);
  // unpaid absences (staff with a duty time) are cut at salary / 30 per day unless the manager changes it
  const absenceCut = round2(Math.min(u.salary, b.absence_cut ?? attendanceMonth(tid(req), u.id, b.month ?? pkDate().slice(0, 7)).salary_cut));
  // commission on lubricants / shop sales (and per litre if set) is added by itself
  const com = round2(b.commission ?? commissionForMonth(tid(req), u.id, month));
  const gross = round2(u.salary - absenceCut + b.bonus + com);
  if (b.deduct > gross) throw new AppError(400, "Deduction is more than the salary");
  const t = tid(req);
  // loan instalments come off by themselves (as much as the salary allows)
  const loanWanted = b.skip_loan ? 0 : round2(loanDue(u.id).reduce((a, d) => a + d.amount, 0));
  let loan = 0;
  tx(() => {
    loan = loanWanted ? takeInstalments(t, u.id, month, gross - b.deduct, req.user!.name) : 0;
    const ins = (type: string, amount: number, note: string) =>
      run("INSERT INTO staff_ledger (tenant_id,user_id,type,amount,note,month,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)", t, u.id, type, amount, note, month, req.user!.name, now()).id;
    if (b.bonus) ins("bonus", b.bonus, b.note ?? `Bonus ${month}`);
    if (com) ins("bonus", com, `Commission ${month}`);
    if (b.deduct) ins("deduction", b.deduct, `Recovered from ${month} salary`);
    const salaryId = ins("salary", round2(gross - b.deduct - loan), `Salary ${month}: ${pkr(u.salary)}${absenceCut ? ` − ${pkr(absenceCut)} absences` : ""}${b.bonus ? ` + bonus ${pkr(b.bonus)}` : ""}${com ? ` + commission ${pkr(com)}` : ""}${b.deduct ? ` − ${pkr(b.deduct)} advance/shortage` : ""}${loan ? ` − ${pkr(loan)} loan` : ""}`);
    linkPhotos(t, b.photo_ids, `staff:${salaryId}`); // signed salary sheet / receipt
    // the expense book shows the full salary cost; the recovered part was paid out earlier as an advance
    // (the category is made if the owner deleted it — otherwise the salary would never leave the cash book)
    if (!get("SELECT id FROM expense_categories WHERE tenant_id=? AND name='Salaries & wages'", t))
      run("INSERT INTO expense_categories (tenant_id,name,monthly_budget) VALUES (?,?,?)", t, "Salaries & wages", 0);
    run(`INSERT INTO expenses (tenant_id,station_id,category,amount,paid_to,method,note,status,created_by,approved_by,expense_date,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`, t, u.station_id ?? null, "Salaries & wages", gross, u.name, "cash", `Salary ${month}`, "approved", req.user!.name, req.user!.name, pkDate(), now());
  });
  const net = round2(gross - b.deduct - loan);
  // salary slip (PDF) with every line, linked in the WhatsApp message
  const att = attendanceMonth(t, u.id, month);
  const slipId = saveSlip(t, u.id, month, {
    salary: u.salary, bonus: b.bonus, commission: com, absence_cut: absenceCut, deduct: b.deduct, loan, net, balance_after: staffBalance(u.id),
    loans_left: round2(loansOf(u.id).reduce((a, l) => a + (l.status === "closed" ? 0 : l.remaining), 0)),
    attendance: att.tracked ? { present: att.present, late: att.late, absent: att.absent, leave: att.paid_leave + att.unpaid_leave, unpaid_days: att.unpaid_days } : null,
  }, req.user!.name);
  const url = slipUrl(t, slipId);
  await notify(t, [u], { type: "staff_ledger", title: `Salary paid: ${pkr(net)}`, data: { slip_url: url },
    body: `${month}${b.deduct ? ` · ${pkr(b.deduct)} adjusted from advance/shortage` : ""}${loan ? ` · loan instalment ${pkr(loan)}` : ""}. Remaining to adjust: ${pkr(staffBalance(u.id))}.
Salary slip (PDF): ${url}` });
  return { net, absence_cut: absenceCut, commission: com, loan, slip_url: url, balance: staffBalance(u.id), lines: ledger(u.id) };
}));

/** Every staff member can see their own account. */
staffRouter.get("/me/account", h((req) => {
  const u = ownUser(tid(req), req.user!.id);
  return { user: u, balance: staffBalance(u.id), lines: ledger(u.id).slice(0, 60), loans: loansOf(u.id).filter((l) => l.status !== "closed"),
    slips: all("SELECT id, month, data FROM salary_slips WHERE user_id=? ORDER BY month DESC LIMIT 12", u.id).map((x) => ({ id: x.id, month: x.month, net: JSON.parse(x.data).net, url: slipUrl(tid(req), x.id) })) };
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
