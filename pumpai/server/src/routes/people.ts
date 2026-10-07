/**
 * Staff:
 *  - Salary slip as a PDF, sent on WhatsApp when the salary is paid
 *  - Loans paid back in monthly instalments, taken from the salary by themselves
 *  - Training records (fire safety, POS, quality…) with re-training due dates
 *  - Daily coaching message to each salesman from yesterday's numbers (AI when available)
 */
import { Router } from "express";
import jwt from "jsonwebtoken";
import { z } from "zod";
import Anthropic from "@anthropic-ai/sdk";
import { config, aiEnabled } from "../config.js";
import { all, get, run, now, pkDate, pkStart, pkEnd, tx } from "../db.js";
import { h, parse, tid, requirePerm, can } from "../auth.js";
import { AppError, round2, pkr, audit } from "../services.js";
import { notify, staff } from "../notifications.js";
import { linkPhotos, proofPhotos } from "./capture.js";
import { Pdf } from "../pdf.js";
import { brandLines } from "../brandPrint.js";
import { logoJpeg } from "./setup.js";

export const people = Router();
const DAY = 86_400_000;
const dateStr = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);

function ownUser(t: number, id: number) {
  const u = get("SELECT id, name, role, phone, salary, station_id, active, created_at FROM users WHERE id=? AND tenant_id=?", id, t);
  if (!u) throw new AppError(404, "Staff member not found");
  return u;
}

/* ================= Loans ================= */
const loanPaid = (loanId: number) => round2(get("SELECT COALESCE(SUM(amount),0) v FROM staff_ledger WHERE ref=? AND type IN ('deduction','repayment')", `loan:${loanId}`)!.v);
export function loansOf(userId: number) {
  return all("SELECT * FROM staff_loans WHERE user_id=? ORDER BY id DESC", userId).map((l) => {
    const paid = loanPaid(l.id);
    const left = round2(Math.max(0, l.amount - paid));
    return { ...l, paid, remaining: left, months_left: l.status === "active" && l.instalment ? Math.ceil(left / l.instalment) : 0 };
  });
}
/** Instalments due this month on active loans. */
export const loanDue = (userId: number) =>
  loansOf(userId).filter((l) => l.status === "active" && l.remaining > 0).map((l) => ({ loan_id: l.id, amount: round2(Math.min(l.instalment, l.remaining)) }));

/** Book the instalments inside a salary payment (call inside the salary transaction). */
export function takeInstalments(t: number, userId: number, month: string, max: number, by: string) {
  let left = max, total = 0;
  for (const d of loanDue(userId)) {
    const amt = round2(Math.min(d.amount, left));
    if (amt <= 0) break;
    run("INSERT INTO staff_ledger (tenant_id,user_id,type,amount,note,ref,month,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
      t, userId, "deduction", amt, `Loan #${d.loan_id} instalment ${month}`, `loan:${d.loan_id}`, month, by, now());
    left -= amt; total += amt;
    if (loanPaid(d.loan_id) >= get("SELECT amount FROM staff_loans WHERE id=?", d.loan_id)!.amount - 0.01)
      run("UPDATE staff_loans SET status='closed', closed_at=? WHERE id=?", now(), d.loan_id);
  }
  return round2(total);
}

people.get("/staff/:id/loans", requirePerm("staff.manage"), h((req) => loansOf(ownUser(tid(req), Number(req.params.id)).id)));
people.post("/staff/:id/loans", requirePerm("staff.manage"), h(async (req) => {
  const t = tid(req);
  const u = ownUser(t, Number(req.params.id));
  const b = parse(z.object({ amount: z.number().positive().max(10_000_000), instalment: z.number().positive(), note: z.string().max(200).optional().nullable(), photo_ids: proofPhotos }), req.body);
  if (b.instalment > b.amount) throw new AppError(400, "Monthly instalment is more than the loan");
  if (u.salary && b.instalment > u.salary) throw new AppError(400, `Instalment is more than ${u.name}'s salary (${pkr(u.salary)})`);
  const id = tx(() => {
    const { id } = run("INSERT INTO staff_loans (tenant_id,user_id,amount,instalment,note,created_by,created_at) VALUES (?,?,?,?,?,?,?)", t, u.id, b.amount, b.instalment, b.note ?? null, req.user!.name, now());
    // the money goes out of the office cash like an advance
    run("INSERT INTO staff_ledger (tenant_id,user_id,type,amount,note,ref,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
      t, u.id, "advance", b.amount, `Loan #${id}${b.note ? `: ${b.note}` : ""} — ${pkr(b.instalment)} a month`, `loan:${id}`, req.user!.name, now());
    linkPhotos(t, b.photo_ids, `loan:${id}`); // signed loan paper
    return id;
  });
  audit(t, req.user!, "staff_loan", `loan:${id}`, b);
  await notify(t, [u], { type: "staff_ledger", title: `Loan given: ${pkr(b.amount)}`, body: `${pkr(b.instalment)} will be cut from each salary (${Math.ceil(b.amount / b.instalment)} months).` });
  return loansOf(u.id);
}));
/** Change the instalment (e.g. a hard month) or close a loan that was paid back in cash. */
people.patch("/loans/:id", requirePerm("staff.manage"), h((req) => {
  const l = get("SELECT * FROM staff_loans WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  if (!l) throw new AppError(404, "Loan not found");
  const b = parse(z.object({ instalment: z.number().positive().optional(), status: z.enum(["active", "paused"]).optional() }), req.body);
  run("UPDATE staff_loans SET instalment=?, status=? WHERE id=?", b.instalment ?? l.instalment, b.status ?? l.status, l.id);
  return loansOf(l.user_id);
}));

/* ================= Salary slip ================= */
export function saveSlip(t: number, userId: number, month: string, data: Record<string, unknown>, by: string) {
  run("INSERT INTO salary_slips (tenant_id,user_id,month,data,created_by,created_at) VALUES (?,?,?,?,?,?) ON CONFLICT(user_id,month) DO UPDATE SET data=excluded.data, created_at=excluded.created_at",
    t, userId, month, JSON.stringify(data), by, now());
  return get("SELECT id FROM salary_slips WHERE user_id=? AND month=?", userId, month)!.id as number;
}
export const slipUrl = (t: number, slipId: number) => `${config.publicUrl}/slip/${jwt.sign({ slip: slipId, t }, config.jwtSecret, { expiresIn: "730d" })}.pdf`;

export function slipPdf(token: string): Buffer | null {
  let p: { slip?: number; t: number };
  try { p = jwt.verify(token.replace(/\.pdf$/, ""), config.jwtSecret) as typeof p; } catch { return null; }
  if (typeof p.slip !== "number") return null;
  const s = get("SELECT s.*, u.name, u.role, st.name station FROM salary_slips s JOIN users u ON u.id=s.user_id LEFT JOIN stations st ON st.id=u.station_id WHERE s.id=? AND s.tenant_id=?", p.slip, p.t);
  if (!s) return null;
  const d = JSON.parse(s.data);
  const tenant = get("SELECT name FROM tenants WHERE id=?", s.tenant_id)!.name;
  const money = (n: number) => `Rs ${Math.round(n).toLocaleString("en-PK")}`;
  const monthName = new Date(`${s.month}-15T00:00:00Z`).toLocaleString("en-GB", { month: "long", year: "numeric" });
  const brand = brandLines(s.tenant_id);
  const logo = logoJpeg(s.tenant_id);
  const role = s.role === "salesman" ? "Salesman" : s.role[0].toUpperCase() + s.role.slice(1);
  const paidOn = new Date(s.created_at).toLocaleDateString("en-PK", { timeZone: "Asia/Karachi", day: "numeric", month: "short", year: "numeric" });
  const pdf = new Pdf();
  const L = 40, R = Pdf.W - 40, HALF = Pdf.H / 2;
  // two copies on one A4 — the staff member keeps the top, the office keeps the bottom
  const copy = (oy: number, label: string) => {
    pdf.rect(0, oy, Pdf.W, 54, 0.16);
    const lx = logo ? L + 50 : L;
    if (logo) { pdf.rect(L - 3, oy + 7, 44, 40, 1); pdf.image(logo, L, oy + 9, 38, 36); }
    pdf.text(lx, oy + 25, tenant, 15, { bold: true, gray: 1 }).text(lx, oy + 41, `Salary slip · ${label}`, 9, { gray: 0.85 })
      .text(R, oy + 25, monthName, 12, { bold: true, align: "right", gray: 1 }).text(R, oy + 41, `Slip no. SS-${s.id}`, 8.5, { align: "right", gray: 0.85 });
    // letterhead strip: phone, address, tax numbers — as on every other print
    if (brand.contact) { pdf.rect(0, oy + 54, Pdf.W, 15, 0.93); pdf.text(Pdf.W / 2, oy + 64.5, brand.contact, 7.5, { align: "center", gray: 0.25 }); }
    let y = oy + 86;
    const kv = (k: string, v: string, x: number) => { pdf.text(x, y, k, 7.5, { gray: 0.4 }).text(x, y + 12, v, 10, { bold: true }); };
    kv("Name", s.name, L); kv("Role", role, L + 190); kv("Station", s.station ?? "All", L + 330);
    y += 24;
    if (d.attendance) {
      pdf.rect(L, y, R - L, 17, 0.95);
      pdf.text(L + 6, y + 11.5, `Attendance: present ${d.attendance.present} · late ${d.attendance.late} · absent ${d.attendance.absent} · leave ${d.attendance.leave}`, 8.5);
      y += 22;
    }
    y += 12;
    const row = (k: string, v: number, opts: { bold?: boolean; minus?: boolean } = {}) => {
      pdf.text(L, y, k, 9.5, { bold: opts.bold }).text(R, y, `${opts.minus && v ? "- " : ""}${money(v)}`, 9.5, { bold: opts.bold, align: "right" });
      pdf.line(L, y + 5, R, y + 5, 0.4, 0.85);
      y += 16;
    };
    pdf.text(L, y, "EARNINGS", 7.5, { bold: true, gray: 0.4 }); y += 13;
    row("Monthly salary", d.salary);
    if (d.bonus) row("Bonus", d.bonus);
    if (d.commission) row("Commission (shop / fuel)", d.commission);
    row("Gross earnings", d.salary + (d.bonus ?? 0) + (d.commission ?? 0), { bold: true });
    y += 4;
    pdf.text(L, y, "DEDUCTIONS", 7.5, { bold: true, gray: 0.4 }); y += 13;
    if (d.absence_cut) row(`Unpaid absences${d.attendance?.unpaid_days ? ` (${d.attendance.unpaid_days} day${d.attendance.unpaid_days === 1 ? "" : "s"})` : ""}`, d.absence_cut, { minus: true });
    if (d.loan) row("Loan instalment", d.loan, { minus: true });
    if (d.deduct) row("Advance / cash shortage recovered", d.deduct, { minus: true });
    if (!d.absence_cut && !d.loan && !d.deduct) row("None", 0);
    y += 2;
    pdf.rect(L, y, R - L, 24, 0.9);
    pdf.text(L + 8, y + 16, "NET PAID", 11, { bold: true }).text(R - 8, y + 16.5, money(d.net), 13, { bold: true, align: "right" });
    y += 38;
    const notes = [`Still to adjust (advances / shortages / loans): ${money(d.balance_after ?? 0)}`, d.loans_left ? `Loan remaining: ${money(d.loans_left)}` : "", `Paid on ${paidOn} by ${s.created_by ?? ""}`].filter(Boolean);
    for (const n of notes) { pdf.text(L, y, n, 8.5, { gray: 0.3 }); y += 11; }
    const sy = oy + 366;
    pdf.line(L, sy, L + 170, sy).text(L, sy + 11, "Received by (signature)", 8, { gray: 0.4 });
    pdf.line(R - 170, sy, R, sy).text(R - 170, sy + 11, "Authorised by", 8, { gray: 0.4 });
    let fy = oy + 392;
    for (const [txt, size] of [[brand.foot, 7], [brand.social, 6.5], [brand.note, 6.5]] as const)
      if (txt) { pdf.text(Pdf.W / 2, fy, txt, size, { align: "center", gray: 0.3 }); fy += 8.5; }
  };
  copy(0, "Employee copy");
  // dashed cut line between the copies
  for (let x = 14; x < Pdf.W - 14; x += 10) pdf.line(x, HALF, x + 5, HALF, 0.5, 0.55);
  pdf.text(Pdf.W - 16, HALF - 3, "cut here", 6.5, { align: "right", gray: 0.55 });
  copy(HALF + 4, "Office copy");
  return pdf.build(`Salary slip ${s.name} ${s.month}`);
}

people.get("/staff/:id/slips", h((req) => {
  const u = ownUser(tid(req), Number(req.params.id));
  if (u.id !== req.user!.id && !can(req.user, "staff.manage")) throw new AppError(403, "Not allowed");
  return all("SELECT id, month, data, created_at FROM salary_slips WHERE user_id=? ORDER BY month DESC LIMIT 24", u.id)
    .map((s) => ({ id: s.id, month: s.month, net: JSON.parse(s.data).net, created_at: s.created_at, url: slipUrl(tid(req), s.id) }));
}));

/* ================= Training ================= */
export const TRAINING_TOPICS: { topic: string; months: number; required: boolean }[] = [
  { topic: "Fire safety & extinguisher use", months: 12, required: true },
  { topic: "Emergency shutdown & spill handling", months: 12, required: true },
  { topic: "POS & cash handling", months: 12, required: true },
  { topic: "Fuel quality: density & water check", months: 12, required: false },
  { topic: "Customer service", months: 24, required: false },
  { topic: "First aid", months: 24, required: false },
];
const addMonths = (d: string, m: number) => { const x = new Date(`${d}T12:00:00Z`); x.setUTCMonth(x.getUTCMonth() + m); return x.toISOString().slice(0, 10); };
const statusOf = (next: string | null, today = pkDate()) => {
  if (!next) return "done";
  const days = Math.round((Date.parse(next) - Date.parse(today)) / DAY);
  return days < 0 ? "overdue" : days <= 30 ? "due_soon" : "done";
};

export function trainingMatrix(t: number, userId?: number) {
  const users = all(`SELECT id, name, role, station_id FROM users WHERE tenant_id=? AND active=1 AND role IN ('salesman','manager') ${userId ? "AND id=?" : ""} ORDER BY role DESC, name`, t, ...(userId ? [userId] : []));
  const custom = all("SELECT DISTINCT topic FROM trainings WHERE tenant_id=?", t).map((r) => r.topic).filter((x) => !TRAINING_TOPICS.some((d) => d.topic === x));
  const topics = [...TRAINING_TOPICS, ...custom.map((topic) => ({ topic, months: 12, required: false }))];
  return {
    topics,
    staff: users.map((u) => ({
      ...u,
      records: Object.fromEntries(topics.map((tp) => {
        const r = get("SELECT * FROM trainings WHERE user_id=? AND topic=? ORDER BY done_on DESC, id DESC LIMIT 1", u.id, tp.topic);
        return [tp.topic, r ? { id: r.id, done_on: r.done_on, next_due: r.next_due, trainer: r.trainer, photo_id: r.photo_id, status: statusOf(r.next_due) }
          : { status: tp.required && u.role === "salesman" ? "missing" : "none" }];
      })),
    })),
  };
}
people.get("/training", requirePerm("staff.manage"), h((req) => trainingMatrix(tid(req))));
people.get("/me/training", h((req) => trainingMatrix(tid(req), req.user!.id)));
people.post("/training", requirePerm("staff.manage"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({ user_ids: z.array(z.number()).min(1), topic: z.string().min(3).max(80), done_on: dateStr.optional(), valid_months: z.number().int().min(0).max(60).optional(),
    trainer: z.string().max(80).optional().nullable(), note: z.string().max(200).optional().nullable(), photo_id: z.number().optional().nullable() }), req.body);
  const done = b.done_on ?? pkDate();
  const months = b.valid_months ?? TRAINING_TOPICS.find((x) => x.topic === b.topic)?.months ?? 12;
  const next = months ? addMonths(done, months) : null;
  const ids: number[] = [];
  for (const uid of b.user_ids) {
    ownUser(t, uid);
    ids.push(run("INSERT INTO trainings (tenant_id,user_id,topic,done_on,next_due,trainer,note,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
      t, uid, b.topic, done, next, b.trainer ?? null, b.note ?? null, req.user!.name, now()).id);
  }
  if (b.photo_id && linkPhotos(t, [b.photo_id], `training:${ids[0]}`)) for (const id of ids) run("UPDATE trainings SET photo_id=? WHERE id=?", b.photo_id, id);
  return { added: ids.length, next_due: next };
}));
people.delete("/training/:id", requirePerm("staff.manage"), h((req) => {
  run("DELETE FROM trainings WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  return { ok: true };
}));

/** Weekly: who needs (re)training — to the managers, and each person's own list to them. */
export async function trainingWatch(t: number) {
  const m = trainingMatrix(t);
  const due: string[] = [];
  for (const u of m.staff) {
    const mine = Object.entries(u.records).filter(([, r]: [string, any]) => ["overdue", "due_soon", "missing"].includes(r.status))
      .map(([topic, r]: [string, any]) => `${topic}${r.status === "missing" ? " (never done)" : r.status === "overdue" ? ` (was due ${r.next_due})` : ` (due ${r.next_due})`}`);
    if (!mine.length) continue;
    due.push(`${u.name}: ${mine.join(", ")}`);
    const user = get("SELECT id, phone FROM users WHERE id=?", u.id);
    if (user) await notify(t, [user], { type: "training_due", whatsapp: false, title: "Training due", body: mine.join("\n") });
  }
  if (due.length) await notify(t, staff(t, ["admin", "manager"]), { type: "training_due", whatsapp: false, title: `${due.length} staff need training`, body: due.join("\n") });
  return due.length;
}

/* ================= Daily coaching ================= */
export function coachStats(t: number, userId: number, day = pkDate(Date.now() - DAY)) {
  const u = ownUser(t, userId);
  const from = pkStart(day), to = pkEnd(day);
  const shifts = all(`SELECT sh.* FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.attendant=? AND sh.closed_at >= ? AND sh.closed_at < ?`, t, u.name, from, to);
  const ids = shifts.map((s) => s.id);
  const inShifts = ids.length ? `shift_id IN (${ids.join(",")})` : "0";
  let sales = get(`SELECT COALESCE(SUM(litres),0) l, COALESCE(SUM(amount),0) a, COUNT(*) n, COALESCE(SUM(CASE WHEN source='meter' THEN litres END),0) meter_l FROM sales WHERE ${inShifts}`)!;
  // sales not linked to the shift (older data): count the station's sales during the shift
  if (!sales.n && shifts.length)
    sales = shifts.map((sh) => get(`SELECT COALESCE(SUM(litres),0) l, COALESCE(SUM(amount),0) a, COUNT(*) n, 0 meter_l FROM sales WHERE station_id=? AND created_at >= ? AND created_at < ?`, sh.station_id, sh.opened_at, sh.closed_at)!)
      .reduce((a, x) => ({ l: a.l + x.l, a: a.a + x.a, n: a.n + x.n, meter_l: 0 }), { l: 0, a: 0, n: 0, meter_l: 0 });
  const shop = get(`SELECT COALESCE(SUM(total),0) v, COUNT(*) n FROM shop_sales WHERE ${inShifts}`)!;
  const att = get("SELECT late_minutes FROM attendance WHERE user_id=? AND day=?", u.id, day);
  const rating = get("SELECT ROUND(AVG(score),1) avg, COUNT(score) n, SUM(CASE WHEN score<=2 THEN 1 ELSE 0 END) low FROM ratings WHERE salesman_id=? AND rated_at >= ? AND rated_at < ?", u.id, from, to)!;
  return {
    user: { id: u.id, name: u.name, phone: u.phone }, day, shifts: shifts.length,
    litres: round2(sales.l), amount: round2(sales.a), fills: sales.n,
    not_entered_l: round2(sales.meter_l), not_entered_pct: sales.l ? round2((sales.meter_l / sales.l) * 100) : 0,
    cash_variance: round2(shifts.reduce((a, s) => a + (s.variance ?? 0), 0)),
    shop_sales: round2(shop.v), shop_items: shop.n,
    undone: get("SELECT COUNT(*) n FROM audit_log WHERE user_id=? AND action='sale_undo' AND created_at >= ? AND created_at < ?", u.id, from, to)!.n,
    late_minutes: att?.late_minutes ?? 0, rating: rating.avg, ratings: rating.n, low_ratings: rating.low ?? 0,
    failed_checks: get("SELECT COUNT(*) n FROM checklist_entries WHERE tenant_id=? AND done_by=? AND ok=0 AND day=?", t, u.name, day)!.n,
  };
}
type Stats = ReturnType<typeof coachStats>;

/** Message from the numbers, without AI: one thing done well, up to two things to improve, each with a tip. */
export function coachRules(s: Stats) {
  const first = s.user.name.split(" ")[0];
  const good: string[] = [], fix: string[] = [];
  if (s.litres) good.push(`kal aap ne ${Math.round(s.litres).toLocaleString()} litre bechay (${s.fills} gaariyan)`);
  if (Math.abs(s.cash_variance) < 100 && s.shifts) good.push("cash bilkul theek tha 👏");
  if (s.rating && s.rating >= 4.5) good.push(`customers ne ${s.rating}⭐ rating di`);
  if (s.shop_sales >= 2000) good.push(`shop se ${pkr(s.shop_sales)} ki sale ki`);
  if (s.cash_variance <= -100) fix.push(`cash ${pkr(-s.cash_variance)} kam tha — har sale ke foran baad paise gin kar bag mein rakhein, aur change pehle se tayyar rakhein`);
  if (s.not_entered_pct >= 3) fix.push(`${Math.round(s.not_entered_l)} litre POS par darj nahi huay — har gaari ke baad Save zaroor dabayein`);
  if (s.low_ratings) fix.push("ek customer ne kam rating di — muskura kar salam karein, meter zero dikhayein aur jaldi service dein");
  if (s.late_minutes > 15) fix.push(`aap ${s.late_minutes} minute late aaye — duty se 10 minute pehle pohnchein`);
  if (s.undone >= 3) fix.push(`${s.undone} sale undo hui — rate aur raqam Save se pehle check karein`);
  if (s.failed_checks) fix.push("daily check mein masla aaya — manager ko foran batayein");
  if (!s.shop_sales && s.shifts) fix.push("har customer ko engine oil / filter check ki offer karein — is par commission milta hai");
  return `Assalam-o-Alaikum ${first}! ☀️\n${good.length ? `Shabash — ${good.slice(0, 2).join(", ")}.` : "Naya din, nayi koshish!"}${fix.length ? `\n\nAaj ke liye:\n${fix.slice(0, 2).map((f) => `• ${f}`).join("\n")}` : "\n\nIsi tarah kaam jaari rakhein! 💪"}`;
}

let client: Anthropic | null = null;
export async function coachMessage(s: Stats): Promise<{ text: string; engine: string }> {
  if (aiEnabled()) {
    try {
      if (!client || client.apiKey !== config.anthropicKey) client = new Anthropic({ apiKey: config.anthropicKey, maxRetries: 2, timeout: 60_000 });
      const r = await client.messages.create({
        model: config.aiModel, max_tokens: 1500, output_config: { effort: "low" },
        system: "You coach petrol pump salesmen in Pakistan. Write one short WhatsApp message in simple Roman Urdu (under 550 characters): greet by first name, praise one specific thing with its number, then one or two things to improve today with a concrete, practical tip. Warm and respectful, never insulting, 1-3 emojis, no markdown headings. If there is little data, give one useful general tip. Output only the message.",
        messages: [{ role: "user", content: `Yesterday's numbers: ${JSON.stringify({ ...s, user: { name: s.user.name } })}` }],
      });
      const text = r.content.filter((b): b is Anthropic.TextBlock => b.type === "text").map((b) => b.text).join("").trim();
      if (text) return { text, engine: "claude" };
    } catch (e) { console.error("[coach]", (e as Error).message); }
  }
  return { text: coachRules(s), engine: "rules" };
}

/** Every morning: each salesman who worked yesterday gets their message (app + WhatsApp). */
export async function dailyCoaching(t: number) {
  let n = 0;
  for (const u of all("SELECT id, name, phone FROM users WHERE tenant_id=? AND active=1 AND role='salesman'", t)) {
    const s = coachStats(t, u.id);
    if (!s.shifts) continue;
    const m = await coachMessage(s);
    await notify(t, [u], { type: "coaching", title: "Aaj ki tips · آج کی ٹپس", body: m.text });
    n++;
  }
  return n;
}
people.get("/coaching/:userId", requirePerm("staff.manage"), h(async (req) => {
  const s = coachStats(tid(req), Number(req.params.userId), req.query.day ? String(req.query.day) : undefined);
  return { stats: s, message: await coachMessage(s) };
}));
people.post("/coaching/:userId/send", requirePerm("staff.manage"), h(async (req) => {
  const t = tid(req);
  const b = parse(z.object({ text: z.string().min(5).max(1000) }), req.body);
  const u = get("SELECT id, phone FROM users WHERE id=? AND tenant_id=?", Number(req.params.userId), t);
  if (!u) throw new AppError(404, "Staff member not found");
  await notify(t, [u], { type: "coaching", title: "Aaj ki tips · آج کی ٹپس", body: b.text });
  return { ok: true };
}));
