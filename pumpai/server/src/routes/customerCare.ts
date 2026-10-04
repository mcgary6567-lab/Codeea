/**
 * Customers and khata control:
 *  - Customer portal: a private link where a khata customer sees balance, fills, slips and bills
 *  - Overdue khata goes on hold automatically (institutions optional); optional late-payment charge
 *  - Government bills: monthly bill per office with PO number, submission and payment tracking
 *  - Payment notice (English + Urdu) ready to print
 *  - Car wash / oil change / tyre bookings, with reminders and "oil change due" follow-ups
 */
import { Router } from "express";
import { z } from "zod";
import jwt from "jsonwebtoken";
import { all, get, run, tx, now, pkDate, getSetting } from "../db.js";
import { h, parse, tid, requirePerm, scopedStation } from "../auth.js";
import { AppError, round2, pkr, khataEntry, paymentLink, upsertCustomerByPhone } from "../services.js";
import { config, PRODUCTS } from "../config.js";
import { sendWhatsApp } from "../whatsapp/cloud.js";
import { notify, staff } from "../notifications.js";
import { khataStatement } from "./crm.js";
import { billLink } from "../billing.js";

export const care = Router();
const DAY = 86_400_000;
export const INSTITUTIONS = ["police", "school", "government", "hospital"];
const esc = (v: unknown) => String(v ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]!));
const n2 = (v: number) => Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 });

function ownCustomer(t: number, id: number) {
  const c = get("SELECT * FROM customers WHERE id=? AND tenant_id=?", id, t);
  if (!c) throw new AppError(404, "Customer not found");
  return c;
}

/* ================= Customer portal ================= */
export const portalLink = (c: { id: number; tenant_id: number; portal_v: number }) =>
  `${config.publicUrl}/portal/${jwt.sign({ portal: c.id, t: c.tenant_id, v: c.portal_v }, config.jwtSecret)}`;

care.get("/customers/:id/portal-link", requirePerm("khata.manage"), h((req) => ({ url: portalLink(ownCustomer(tid(req), Number(req.params.id))) })));
/** Shared the link with the wrong person? Make a new one; the old link stops working. */
care.post("/customers/:id/portal-link/reset", requirePerm("khata.manage"), h((req) => {
  const c = ownCustomer(tid(req), Number(req.params.id));
  run("UPDATE customers SET portal_v = portal_v + 1 WHERE id=?", c.id);
  return { url: portalLink(get("SELECT * FROM customers WHERE id=?", c.id)!) };
}));
care.post("/customers/:id/portal-link/send", requirePerm("khata.manage"), h(async (req) => {
  const c = ownCustomer(tid(req), Number(req.params.id));
  await sendWhatsApp(tid(req), c, `📒 ${c.name}, apna khata kabhi bhi dekhein (balance, har fill, slip aur bill):\n${portalLink(c)}`, "system", { kind: "portal_link" });
  return { ok: true };
}));

export function renderPortal(token: string): string | null {
  let p: { portal: number; t: number; v: number };
  try { p = jwt.verify(token, config.jwtSecret) as typeof p; } catch { return null; }
  const c = get("SELECT * FROM customers WHERE id=? AND tenant_id=?", p.portal, p.t);
  if (!c || c.portal_v !== p.v) return null;
  const tenant = get("SELECT * FROM tenants WHERE id=?", p.t)!;
  const since = pkDate(Date.now() - 45 * DAY);
  const s = khataStatement(p.t, c.id, since);
  const months = Array.from({ length: 6 }, (_, i) => { const d = new Date(Date.parse(`${pkDate().slice(0, 7)}-15T00:00:00Z`)); d.setUTCMonth(d.getUTCMonth() - i); return d.toISOString().slice(0, 7); });
  const bills = all("SELECT * FROM khata_bills WHERE customer_id=? ORDER BY month DESC LIMIT 6", c.id);
  const rows = [...s.lines].reverse().slice(0, 60).map((l: any) => `<tr><td>${esc(new Date(l.created_at).toLocaleString("en-PK", { timeZone: "Asia/Karachi", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }))}</td>
    <td>${l.type === "debit" ? `${esc(PRODUCTS[l.product] ?? l.note ?? "")}${l.litres ? ` · ${n2(l.litres)} L × ${n2(l.rate)}` : ""}<div class=m>${esc([l.vehicle_no, l.slip_no && `slip ${l.slip_no}`].filter(Boolean).join(" · "))}</div>` : `<b class=g>Payment</b>`}</td>
    <td class=r>${l.type === "debit" ? n2(l.amount) : `<span class=g>−${n2(l.amount)}</span>`}</td></tr>`).join("");
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(c.name)} — khata</title>
<style>:root{color-scheme:light}body{font:15px/1.45 system-ui,sans-serif;margin:0;background:#f1f5f9;color:#0f172a}.w{max-width:640px;margin:0 auto;padding:14px}.c{background:#fff;border-radius:14px;padding:16px;margin-bottom:12px}
h1{font-size:20px;margin:0}.m{color:#64748b;font-size:12px}.big{font-size:26px;font-weight:700;word-break:break-word}.g{color:#047857}.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}.t{background:#f8fafc;border-radius:10px;padding:10px}
table{width:100%;border-collapse:collapse}td{padding:7px 4px;border-bottom:1px solid #e2e8f0;vertical-align:top}.r{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}a.b{display:block;text-align:center;background:#064e3b;color:#fff;padding:12px;border-radius:10px;text-decoration:none;font-weight:600;margin-top:10px}
.hold{background:#fee2e2;color:#991b1b;padding:10px;border-radius:10px;margin-top:10px}ul{padding-left:18px;margin:6px 0}</style></head><body><div class=w>
<div class=c><div class=m>⛽ ${esc(tenant.name)}</div><h1>${esc(c.name)}</h1>
<div class=grid><div class=t><div class=m>Balance due · بقایا</div><div class=big>Rs ${Math.round(c.balance).toLocaleString("en-IN")}</div></div><div class=t><div class=m>Credit limit · حد</div><div class=big style="font-size:22px">Rs ${Math.round(c.credit_limit).toLocaleString("en-IN")}</div><div class=m>Available Rs ${Math.round(Math.max(0, c.credit_limit - c.balance)).toLocaleString("en-IN")}</div></div></div>
${c.khata_blocked ? `<div class=hold>Khata is on hold because payment is overdue. Please pay to continue.</div>` : ""}
${c.balance > 0 ? `<a class=b href="${esc(paymentLink(c, c.balance))}">Pay now (JazzCash / Easypaisa / Raast)</a>` : ""}</div>
<div class=c><b>Monthly bills</b><ul>${months.map((m) => `<li><a href="${esc(billLink(p.t, "k", c.id, m))}">${esc(new Date(`${m}-15T00:00:00Z`).toLocaleDateString("en-PK", { month: "long", year: "numeric" }))}</a>${(() => { const b = bills.find((x) => x.month === m); return b ? ` · bill ${esc(b.bill_no)}${b.po_number ? ` · PO ${esc(b.po_number)}` : ""} · ${esc(b.status)}` : ""; })()}</li>`).join("")}</ul></div>
<div class=c><b>Last 45 days</b><table>${rows || "<tr><td class=m>No entries</td></tr>"}</table></div>
<div class=m style="text-align:center">Updated ${esc(new Date().toLocaleString("en-PK", { timeZone: "Asia/Karachi" }))}</div></div></body></html>`;
}

/* ================= Overdue hold and late charge ================= */
const lastPaymentOrFirstDebit = (customerId: number) =>
  get("SELECT MAX(created_at) d FROM khata_ledger WHERE customer_id=? AND type='credit'", customerId)?.d
  ?? get("SELECT MIN(created_at) d FROM khata_ledger WHERE customer_id=? AND type='debit'", customerId)?.d;

export async function khataOverdue(t: number) {
  if (getSetting(t, "khata_auto_block", "1") === "0") return 0;
  const days = Number(getSetting(t, "khata_block_days", "60"));
  const institutions = getSetting(t, "khata_block_institutions", "0") === "1";
  let n = 0;
  for (const c of all("SELECT * FROM customers WHERE tenant_id=? AND credit_limit > 0 AND balance > 0 AND khata_blocked=0", t)) {
    if (!institutions && INSTITUTIONS.includes(c.type)) continue;
    const since = lastPaymentOrFirstDebit(c.id);
    if (!since || Date.now() - Date.parse(since) < days * DAY) continue;
    run("UPDATE customers SET khata_blocked=1 WHERE id=?", c.id);
    n++;
    await notify(t, staff(t, ["manager", "admin"]), { type: "khata_hold", title: `Khata on hold: ${c.name}`, body: `${pkr(c.balance)} due; no payment for ${days}+ days.` });
    await sendWhatsApp(t, c, `${c.name}, aap ke khata par ${pkr(c.balance)} baqaya hai aur ${days} din se payment nahi hui. Khata abhi roka gaya hai — payment karte hi dobara chalu ho jayega.\n${paymentLink(c, c.balance)}`, "system", { kind: "khata_hold" });
  }
  return n;
}

/** 1st of the month, only if a late-charge percent is set (institutions are never charged). */
export async function khataLateFees(t: number) {
  const pct = Number(getSetting(t, "khata_late_fee_pct", "0"));
  if (!(pct > 0)) return 0;
  const month = pkDate().slice(0, 7);
  let n = 0;
  for (const c of all("SELECT * FROM customers WHERE tenant_id=? AND credit_limit > 0 AND balance > 0", t)) {
    if (INSTITUTIONS.includes(c.type)) continue;
    const since = lastPaymentOrFirstDebit(c.id);
    if (!since || Date.now() - Date.parse(since) < 30 * DAY) continue;
    if (get("SELECT id FROM khata_ledger WHERE customer_id=? AND ref=?", c.id, `LATE-${month}`)) continue;
    const fee = round2((c.balance * pct) / 100);
    if (fee < 1) continue;
    tx(() => {
      run("INSERT INTO khata_ledger (customer_id,type,amount,ref,note,created_at) VALUES (?,?,?,?,?,?)", c.id, "debit", fee, `LATE-${month}`, `Late payment charge ${pct}%`, now());
      run("UPDATE customers SET balance = balance + ? WHERE id=?", fee, c.id);
    });
    n++;
  }
  return n;
}

care.post("/customers/:id/khata-hold", requirePerm("khata.manage"), h((req) => {
  const c = ownCustomer(tid(req), Number(req.params.id));
  const b = parse(z.object({ blocked: z.boolean() }), req.body);
  run("UPDATE customers SET khata_blocked=? WHERE id=?", b.blocked ? 1 : 0, c.id);
  return { ok: true, blocked: b.blocked };
}));

/* ================= Government bills ================= */
care.get("/govt-bills", requirePerm("khata.manage"), h((req) => all(
  `SELECT b.*, c.name customer_name, c.type, c.balance FROM khata_bills b JOIN customers c ON c.id=b.customer_id WHERE b.tenant_id=?
   ORDER BY CASE b.status WHEN 'submitted' THEN 0 WHEN 'partly' THEN 1 WHEN 'draft' THEN 2 ELSE 3 END, b.month DESC LIMIT 300`, tid(req))
  .map((b) => ({ ...b, outstanding: round2(b.amount - b.paid_amount), days_waiting: b.submitted_on && b.status !== "paid" ? Math.floor((Date.parse(pkDate()) - Date.parse(b.submitted_on)) / DAY) : null }))));

care.post("/customers/:id/govt-bills", requirePerm("khata.manage"), h((req) => {
  const c = ownCustomer(tid(req), Number(req.params.id));
  const b = parse(z.object({ month: z.string().regex(/^\d{4}-\d{2}$/) }), req.body);
  if (get("SELECT id FROM khata_bills WHERE customer_id=? AND month=?", c.id, b.month)) throw new AppError(400, "A bill for this month already exists");
  const last = new Date(Date.UTC(Number(b.month.slice(0, 4)), Number(b.month.slice(5, 7)), 0)).getUTCDate();
  const s = khataStatement(tid(req), c.id, `${b.month}-01`, `${b.month}-${String(last).padStart(2, "0")}`);
  if (!(s.totals.charged > 0)) throw new AppError(400, "No fuel was taken on khata in this month");
  const { id } = run("INSERT INTO khata_bills (tenant_id,customer_id,month,bill_no,amount,created_at) VALUES (?,?,?,?,?,?)",
    tid(req), c.id, b.month, `B-${c.id}-${b.month.replace("-", "")}`, round2(s.totals.charged), now());
  return get("SELECT * FROM khata_bills WHERE id=?", id);
}));

care.patch("/govt-bills/:id", requirePerm("khata.manage"), h((req) => {
  const bill = get("SELECT * FROM khata_bills WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  if (!bill) throw new AppError(404, "Bill not found");
  const b = parse(z.object({ po_number: z.string().max(60).nullable().optional(), submitted_on: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).nullable().optional(), note: z.string().max(200).nullable().optional() }), req.body);
  const m = { ...bill, ...b };
  const status = bill.status === "draft" && m.submitted_on ? "submitted" : bill.status;
  run("UPDATE khata_bills SET po_number=?, submitted_on=?, note=?, status=? WHERE id=?", m.po_number ?? null, m.submitted_on ?? null, m.note ?? null, status, bill.id);
  return get("SELECT * FROM khata_bills WHERE id=?", bill.id);
}));

/** Cheque / transfer received against a bill: credited to the khata, bill marked paid or partly paid. */
care.post("/govt-bills/:id/paid", requirePerm("khata.manage"), h((req) => {
  const bill = get("SELECT * FROM khata_bills WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  if (!bill) throw new AppError(404, "Bill not found");
  const b = parse(z.object({ amount: z.number().positive(), method: z.string().min(2).default("cheque"), ref: z.string().max(60).optional().nullable() }), req.body);
  const paid = round2(bill.paid_amount + b.amount);
  tx(() => {
    khataEntry(bill.customer_id, "credit", b.amount, b.method, `Bill ${bill.bill_no}${b.ref ? ` · ${b.ref}` : ""}`);
    run("UPDATE khata_bills SET paid_amount=?, status=? WHERE id=?", paid, paid >= bill.amount - 0.5 ? "paid" : "partly", bill.id);
  });
  return get("SELECT * FROM khata_bills WHERE id=?", bill.id);
}));

/* ================= Payment notice ================= */
care.get("/customers/:id/notice", requirePerm("khata.manage"), (req, res, next) => {
  try {
    const c = ownCustomer(tid(req), Number(req.params.id));
    const tenant = get("SELECT * FROM tenants WHERE id=?", tid(req))!;
    const since = lastPaymentOrFirstDebit(c.id);
    const due = pkDate(Date.now() + 15 * DAY);
    res.type("html").send(`<!doctype html><html><head><meta charset="utf-8"><title>Payment notice — ${esc(c.name)}</title>
<style>body{font:15px/1.6 Georgia,serif;max-width:720px;margin:30px auto;padding:0 20px;color:#111}h1{font-size:20px;text-align:center}.ur{direction:rtl;font-family:'Noto Nastaliq Urdu',serif;line-height:2.2;border-top:1px solid #999;margin-top:24px;padding-top:12px}
button{padding:8px 14px}@media print{button{display:none}}</style><link href="https://fonts.googleapis.com/css2?family=Noto+Nastaliq+Urdu&display=swap" rel="stylesheet"></head><body>
<p style="text-align:right">${esc(pkDate())}</p><p><b>${esc(tenant.name)}</b></p>
<p>To:<br><b>${esc(c.name)}</b>${c.city ? `<br>${esc(c.city)}` : ""}<br>Phone: +${esc(c.phone)}</p>
<h1>NOTICE FOR PAYMENT OF OUTSTANDING DUES</h1>
<p>Dear Sir / Madam,</p>
<p>As per our records, an amount of <b>Rs ${n2(c.balance)}</b> is outstanding against your fuel credit (khata) account with us${since ? `; no payment has been received since <b>${esc(new Date(since).toLocaleDateString("en-PK"))}</b>` : ""}. The detailed statement with every fill, slip number and rate is attached / available on request.</p>
<p>You are requested to clear the outstanding amount by <b>${esc(due)}</b>. If the amount is not paid by this date, we may stop further credit and take steps to recover the dues as per law.</p>
<p>If you have already paid, please share the payment details so that we can update your account.</p>
<p>Yours sincerely,<br><br>__________________<br>${esc(tenant.owner_name ?? "")}<br>${esc(tenant.name)}</p>
<div class="ur"><p><b>واجب الادا رقم کی ادائیگی کا نوٹس</b></p><p>محترم ${esc(c.name)}،</p>
<p>ہمارے ریکارڈ کے مطابق آپ کے فیول کھاتے پر <b>${n2(c.balance)} روپے</b> واجب الادا ہیں۔ آپ سے گزارش ہے کہ یہ رقم <b>${esc(due)}</b> تک ادا کر دیں، ورنہ مزید ادھار بند کر کے قانون کے مطابق وصولی کی کارروائی کی جا سکتی ہے۔ اگر آپ ادائیگی کر چکے ہیں تو براہ کرم تفصیل بھیج دیں۔</p></div>
<button onclick="print()">Print</button></body></html>`);
  } catch (e) { next(e); }
});

/* ================= Service bookings ================= */
export const SERVICES: Record<string, string> = { car_wash: "Car wash", oil_change: "Oil change", tyre: "Tyre / puncture", service: "Service / tuning" };

export function createBooking(t: number, o: { customer_id: number; station_id?: number | null; service: string; at: string; vehicle_no?: string | null; note?: string | null; by: string }) {
  if (!SERVICES[o.service]) throw new AppError(400, "Unknown service");
  if (Date.parse(o.at) < Date.now() - 15 * 60_000) throw new AppError(400, "Booking time has already passed");
  const station = o.station_id ?? get("SELECT id FROM stations WHERE tenant_id=? ORDER BY id LIMIT 1", t)!.id;
  // at most 2 bookings of a service in the same half hour
  const clash = get("SELECT COUNT(*) n FROM bookings WHERE station_id=? AND service=? AND status='booked' AND ABS(strftime('%s', at) - strftime('%s', ?)) < 1800", station, o.service, o.at)!.n;
  if (clash >= 2) throw new AppError(400, "That time is full; please choose another time");
  const { id } = run("INSERT INTO bookings (tenant_id,station_id,customer_id,service,at,vehicle_no,note,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
    t, station, o.customer_id, o.service, new Date(o.at).toISOString(), o.vehicle_no ?? null, o.note ?? null, o.by, now());
  return get("SELECT b.*, s.name station_name FROM bookings b JOIN stations s ON s.id=b.station_id WHERE b.id=?", id)!;
}

care.get("/bookings", requirePerm("sales.create"), h((req) => {
  const station = scopedStation(req, req.query.station_id ? Number(req.query.station_id) : null);
  const from = new Date(Date.now() - 12 * 3600_000).toISOString();
  return all(`SELECT b.*, c.name customer_name, c.phone, s.name station_name FROM bookings b JOIN customers c ON c.id=b.customer_id JOIN stations s ON s.id=b.station_id
    WHERE b.tenant_id=? ${station ? "AND b.station_id=" + Number(station) : ""} AND (b.at >= ? OR b.status='booked') ORDER BY b.at LIMIT 200`, tid(req), from);
}));
care.post("/bookings", requirePerm("sales.create"), h(async (req) => {
  const b = parse(z.object({ customer_id: z.number().optional(), phone: z.string().optional(), name: z.string().optional(), station_id: z.number().optional().nullable(),
    service: z.enum(Object.keys(SERVICES) as [string, ...string[]]), at: z.string().datetime({ offset: true }), vehicle_no: z.string().max(20).optional().nullable(), note: z.string().max(200).optional().nullable() }), req.body);
  const c = b.customer_id ? ownCustomer(tid(req), b.customer_id) : b.phone ? upsertCustomerByPhone(tid(req), b.phone, b.name) : null;
  if (!c) throw new AppError(400, "Customer phone is needed");
  const bk = createBooking(tid(req), { ...b, customer_id: c.id, station_id: scopedStation(req, b.station_id ?? null), by: req.user!.name });
  await sendWhatsApp(tid(req), c, `✅ ${SERVICES[bk.service]} booked: ${new Date(bk.at).toLocaleString("en-PK", { timeZone: "Asia/Karachi", weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })} at ${bk.station_name}. Booking #${bk.id}.`, "system", { kind: "booking" });
  return bk;
}));
care.post("/bookings/:id/:status(done|cancelled|no_show)", requirePerm("sales.create"), h((req) => {
  const bk = get("SELECT * FROM bookings WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  if (!bk) throw new AppError(404, "Booking not found");
  run("UPDATE bookings SET status=?, done_at=? WHERE id=?", req.params.status, req.params.status === "done" ? now() : null, bk.id);
  return get("SELECT * FROM bookings WHERE id=?", bk.id);
}));

/** Every 15 minutes: reminder an hour before; daily: oil change due 90 days after the last one. */
export async function bookingReminders(t: number) {
  let n = 0;
  for (const b of all(`SELECT b.*, s.name station_name FROM bookings b JOIN stations s ON s.id=b.station_id WHERE b.tenant_id=? AND b.status='booked' AND b.reminded=0 AND b.at > ? AND b.at <= ?`,
    t, new Date().toISOString(), new Date(Date.now() + 75 * 60_000).toISOString())) {
    const c = get("SELECT * FROM customers WHERE id=?", b.customer_id);
    if (!c) continue;
    await sendWhatsApp(t, c, `⏰ Yaad-dihani: aap ki ${SERVICES[b.service]} booking ${new Date(b.at).toLocaleTimeString("en-PK", { timeZone: "Asia/Karachi", hour: "2-digit", minute: "2-digit" })} baje ${b.station_name} par hai. Na aa saken to "cancel" likh dein.`, "system", { kind: "booking_reminder", booking_id: b.id });
    run("UPDATE bookings SET reminded=1 WHERE id=?", b.id);
    n++;
  }
  return n;
}
export async function serviceDue(t: number) {
  let n = 0;
  const day = (d: number) => new Date(Date.now() - d * DAY).toISOString();
  for (const b of all(`SELECT b.customer_id, MAX(b.done_at) last FROM bookings b WHERE b.tenant_id=? AND b.service='oil_change' AND b.status='done' GROUP BY b.customer_id HAVING last <= ? AND last > ?`, t, day(90), day(91))) {
    const c = get("SELECT * FROM customers WHERE id=?", b.customer_id);
    if (!c?.opt_in) continue;
    await sendWhatsApp(t, c, `🛢️ ${String(c.name).split(" ")[0]} sahab, aap ki gaari ka oil change 3 mahine pehle hua tha. Naya oil change book karne ke liye "oil change book" likhein.`, "system", { kind: "service_due" });
    n++;
  }
  return n;
}

/* ================= Booking from WhatsApp text (rule engine) ================= */
/** "kal 5 baje car wash", "aaj shaam 6 bje oil change", "tomorrow 10am tyre" → service + time (Pakistan). */
export function parseBookingText(text: string): { service: string | null; at: string | null } {
  const t = ` ${text.toLowerCase()} `;
  const service = /wash|dhula|dhulai|دھلائی|واش/.test(t) ? "car_wash" : /oil change|oil|mobil|آئل/.test(t) ? "oil_change" : /tyre|tire|puncture|پنکچر|ٹائر/.test(t) ? "tyre" : /service|tuning|سروس/.test(t) ? "service" : null;
  const plus = /parson|day after/.test(t) ? 2 : /kal|tomorrow|کل/.test(t) ? 1 : 0;
  const m = t.match(/(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje|bje|bajay|بجے)?/);
  if (!m || !m[3] && !m[2]) return { service, at: null };
  let hour = Number(m[1]);
  const min = Number(m[2] ?? 0);
  const pm = m[3] === "pm" || /shaam|sham|raat|evening|night|شام/.test(t) || (m[3] !== "am" && hour >= 1 && hour <= 7 && !/subah|morning|صبح/.test(t));
  if (pm && hour < 12) hour += 12;
  if (hour > 23 || min > 59) return { service, at: null };
  const day = pkDate(Date.now() + plus * DAY);
  const at = new Date(`${day}T${String(hour).padStart(2, "0")}:${String(min).padStart(2, "0")}:00+05:00`);
  if (at.getTime() < Date.now()) at.setTime(at.getTime() + DAY);
  return { service, at: at.toISOString() };
}
