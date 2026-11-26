/**
 * Payment inbox — WhatsApp payment screenshots from khata customers, waiting for the cashier to confirm.
 *
 * When a khata customer WhatsApps a JazzCash/Easypaisa/Raast/bank screenshot, the AI reads the amount + method +
 * reference (when a key is set) and a PENDING row lands here. The cashier checks it against the bank/wallet and, with
 * one tap, posts it as a khata payment (credit) — exactly the same `khataEntry` the manual screen uses, so every book
 * stays tallied. Nothing is credited automatically: a screenshot is a claim, not proof of cleared money.
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, now, type Row } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { AppError, round2, khataEntry, audit } from "../services.js";
import { bankAccountFor, accountIdField } from "./banks.js";
import { linkPhotos } from "./capture.js";
import { notify, staff } from "../notifications.js";

export const paymentInbox = Router();

/**
 * A khata customer sent a payment screenshot. Match them by phone to a REAL customer, save a pending row the cashier
 * confirms, and tell the cashiers. `amount` may be null when there is no AI key (the cashier fills it in). Returns the
 * row, or null if the number belongs to no customer.
 */
export async function recordPaymentScreenshot(t: number, p: { phone: string; sender_name?: string | null; amount?: number | null; method?: string | null; reference?: string | null; confidence?: string | null; photo_id?: number | null; caption?: string | null }): Promise<Row | null> {
  // only a real khata account (has a credit limit, a running balance, or past ledger activity) — a random new
  // number that just messaged in is not someone whose payment we would ever post to a khata
  const customer = get(
    `SELECT id, name FROM customers c WHERE tenant_id=? AND phone=? AND (credit_limit > 0 OR balance <> 0 OR EXISTS(SELECT 1 FROM khata_ledger k WHERE k.customer_id=c.id))`,
    t, p.phone);
  if (!customer) return null; // not a khata customer we can credit
  const { id } = run(
    `INSERT INTO payment_inbox (tenant_id,customer_id,phone,sender_name,amount,method,reference,confidence,photo_id,caption,status,created_at)
     VALUES (?,?,?,?,?,?,?,?,?,?, 'pending', ?)`,
    t, customer.id, p.phone, p.sender_name ?? null, p.amount ?? null, p.method ?? null, p.reference ?? null, p.confidence ?? null, p.photo_id ?? null, p.caption ?? null, now());
  if (p.photo_id) run("UPDATE photos SET ref=? WHERE id=? AND tenant_id=? AND ref IS NULL", `payment-inbox:${id}`, p.photo_id, t);
  await notify(t, staff(t, ["cashier", "admin", "manager"]), { type: "payment_screenshot",
    title: `💸 ${customer.name} ne payment screenshot bheji${p.amount ? ` (Rs ${Math.round(p.amount).toLocaleString("en-PK")})` : ""}`,
    body: "Cashier desk → Payment inbox me check kar ke confirm karein." }).catch(() => {});
  return get("SELECT * FROM payment_inbox WHERE id=?", id);
}

/** Pending (and recent) screenshots for the cashier. */
paymentInbox.get("/payment-inbox", requirePerm("cash.receive"), h((req) => {
  const t = tid(req);
  const status = req.query.status ? String(req.query.status) : "pending";
  const rows = all(
    `SELECT p.*, c.name customer_name, c.balance customer_balance FROM payment_inbox p LEFT JOIN customers c ON c.id=p.customer_id
     WHERE p.tenant_id=? AND p.status=? ORDER BY p.id DESC LIMIT 100`, t, status);
  return { payments: rows, pending: get("SELECT COUNT(*) n FROM payment_inbox WHERE tenant_id=? AND status='pending'", t)!.n };
}));

/** Confirm a screenshot → post it as a khata payment (credit). The cashier sets/fixes the amount & method here. */
paymentInbox.post("/payment-inbox/:id/confirm", requirePerm("cash.receive"), h((req) => {
  const t = tid(req);
  const p = get("SELECT * FROM payment_inbox WHERE id=? AND tenant_id=?", Number(req.params.id), t);
  if (!p) throw new AppError(404, "Payment nahi mili");
  if (p.status !== "pending") throw new AppError(400, `Ye pehle hi ${p.status === "confirmed" ? "confirm" : "reject"} ho chuki hai`);
  if (!p.customer_id) throw new AppError(400, "Is number ka koi khata customer nahi — pehle customer banayein.");
  const b = parse(z.object({ amount: z.number().positive(), method: z.string().min(2).default("Easypaisa"), account_id: accountIdField, note: z.string().max(200).optional().nullable() }),
    { amount: req.body.amount ?? p.amount, method: req.body.method ?? (p.method ? p.method[0].toUpperCase() + p.method.slice(1) : "Easypaisa"), account_id: req.body.account_id, note: req.body.note });
  const updated = khataEntry(p.customer_id, "credit", b.amount, b.method, b.note ?? `WhatsApp payment${p.reference ? ` · ${p.reference}` : ""}`, bankAccountFor(t, b.account_id, b.method));
  // move the screenshot photo onto the khata entry so it shows on the statement as proof
  if (p.photo_id) { const k = get("SELECT MAX(id) id FROM khata_ledger WHERE customer_id=?", p.customer_id)!.id; run("UPDATE photos SET ref=? WHERE id=? AND tenant_id=?", `khata:${k}`, p.photo_id, t); }
  run("UPDATE payment_inbox SET status='confirmed', amount=?, method=?, decided_by=?, decided_at=? WHERE id=?", b.amount, b.method, req.user!.name, now(), p.id);
  audit(t, req.user!, "payment_inbox_confirm", `payinbox:${p.id}`, { customer_id: p.customer_id, amount: b.amount, method: b.method, reference: p.reference });
  return { ok: true, balance_after: round2(updated.balance) };
}));

/** Reject a screenshot (duplicate, wrong, not received). Nothing is posted. */
paymentInbox.post("/payment-inbox/:id/reject", requirePerm("cash.receive"), h((req) => {
  const t = tid(req);
  const { reason } = parse(z.object({ reason: z.string().trim().min(3, "Wajah likhein") }), req.body);
  const p = get("SELECT * FROM payment_inbox WHERE id=? AND tenant_id=?", Number(req.params.id), t);
  if (!p) throw new AppError(404, "Payment nahi mili");
  if (p.status !== "pending") throw new AppError(400, "Ye pehle hi decide ho chuki hai");
  run("UPDATE payment_inbox SET status='rejected', note=?, decided_by=?, decided_at=? WHERE id=?", reason, req.user!.name, now(), p.id);
  audit(t, req.user!, "payment_inbox_reject", `payinbox:${p.id}`, { customer_id: p.customer_id, amount: p.amount, reason });
  return { ok: true };
}));
