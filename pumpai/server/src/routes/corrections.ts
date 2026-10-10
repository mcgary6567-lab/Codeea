/**
 * CEO corrections — undo a WRONG entry made anywhere (salesman / cashier / manager) and reverse everything it moved,
 * so the whole system stays tallied to the rupee. Companion to the cashier voucher void (which covers cash
 * receive/pay) and the Other-entries undo. All CEO-only (perm `corrections.manage`), each writes an audit row,
 * and after any of them the Audit → "Hisaab check" (GET /books/check) must stay all-green (0 rupaye farq).
 *
 * The ledger is DERIVED (it re-reads the source tables), so each void = undo the source row(s) + restore the
 * paired balance column(s). Three kinds:
 *   1. Sale        — any fuel sale (POS, khata, wallet, coupon, loyalty, card, or a late/online close entry).
 *   2. Khata entry — a manual khata charge/payment made directly on a customer (not a sale, not a cashier voucher,
 *                    not an Other-discount — those have their own undo).
 *   3. Delivery    — a fuel tanker received into a tank (reverses the supplier payable + the stock).
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, tx, now, type Row } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { AppError, round2, pkr, reverseSale, audit } from "../services.js";

export const corrections = Router();

const reasonField = z.object({ reason: z.string().trim().min(3, "Wajah likhein (kam se kam 3 harf)") });
const ownStationIds = (t: number) => all("SELECT id FROM stations WHERE tenant_id=?", t).map((s) => s.id);

/* ---------------- 1. Sale void ---------------- */

/** Recent fuel sales the CEO can pick from to void a wrong one. */
corrections.get("/corrections/sales", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const limit = Math.min(200, Math.max(1, Number(req.query.limit) || 60));
  const q = String(req.query.q ?? "").trim();
  const rows = all(
    `SELECT s.id, s.product, s.litres, s.rate, s.amount, s.payment_method, s.vehicle_no, s.slip_no, s.pending, s.at_close, s.source, s.created_at,
            st.name station, c.name customer, u.name by_name
     FROM sales s JOIN stations st ON st.id=s.station_id
       LEFT JOIN customers c ON c.id=s.customer_id
       LEFT JOIN users u ON u.id=s.created_by
     WHERE st.tenant_id=? ${q ? "AND (c.name LIKE ? OR s.vehicle_no LIKE ? OR s.slip_no LIKE ?)" : ""}
     ORDER BY s.id DESC LIMIT ?`,
    ...(q ? [t, `%${q}%`, `%${q}%`, `%${q}%`, limit] : [t, limit]));
  return { sales: rows };
}));

corrections.post("/corrections/sale/:id/void", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const { reason } = parse(reasonField, req.body);
  const sale = get(`SELECT s.* FROM sales s JOIN stations st ON st.id=s.station_id WHERE s.id=? AND st.tenant_id=?`, Number(req.params.id), t);
  if (!sale) throw new AppError(404, "Sale nahi mili (shayad pehle hi void ho chuki)");
  const customer = sale.customer_id ? get("SELECT name FROM customers WHERE id=?", sale.customer_id) : null;
  const did = tx(() => reverseSale(t, sale));
  audit(t, req.user!, "sale_void", `sale:${sale.id}`, { product: sale.product, litres: sale.litres, amount: sale.amount, method: sale.payment_method, customer: customer?.name ?? null, reason, reversed: did });
  return { ok: true, reversed: did, amount: round2(sale.amount) };
}));

/* ---------------- 2. Manual khata entry void ---------------- */

/** Recent manual khata entries (charges/payments) for one customer, each flagged whether this tool may void it. */
corrections.get("/corrections/khata", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const customerId = Number(req.query.customer_id);
  const c = get("SELECT id, name, balance FROM customers WHERE id=? AND tenant_id=?", customerId, t);
  if (!c) throw new AppError(404, "Customer nahi mila");
  const rows = all(
    `SELECT k.id, k.type, k.amount, k.ref, k.note, k.created_at,
            EXISTS(SELECT 1 FROM cashier_vouchers v WHERE v.tenant_id=? AND v.src='khata:'||k.id) via_cashier
     FROM khata_ledger k WHERE k.customer_id=? ORDER BY k.id DESC LIMIT 60`, t, c.id)
    .map((k) => {
      // what owns this row decides where it must be undone
      const ref = String(k.ref ?? "");
      const owner = ref.startsWith("SALE-") ? "sale" : ref.startsWith("oth-disc:") ? "other" : k.via_cashier ? "cashier" : null;
      return { ...k, via_cashier: undefined, owner, voidable: owner === null };
    });
  return { customer: { id: c.id, name: c.name, balance: round2(c.balance) }, entries: rows };
}));

corrections.post("/corrections/khata/:id/void", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const { reason } = parse(reasonField, req.body);
  const k = get(`SELECT k.* FROM khata_ledger k JOIN customers c ON c.id=k.customer_id WHERE k.id=? AND c.tenant_id=?`, Number(req.params.id), t);
  if (!k) throw new AppError(404, "Khata entry nahi mili");
  const ref = String(k.ref ?? "");
  // these belong to another undo path — send the CEO there so nothing is reversed twice
  if (ref.startsWith("SALE-")) throw new AppError(400, "Ye ek sale ki entry hai — ise 'Sale void' se theek karein.");
  if (ref.startsWith("oth-disc:")) throw new AppError(400, "Ye Other-discount ki entry hai — Other entries page se undo karein.");
  if (get("SELECT 1 FROM cashier_vouchers WHERE tenant_id=? AND src=?", t, `khata:${k.id}`)) throw new AppError(400, "Ye cashier ke voucher se bani hai — Cashier desk se voucher void karein.");
  tx(() => {
    run("DELETE FROM khata_ledger WHERE id=?", k.id);
    // a debit (charge) had raised the balance → lower it back; a credit (payment) had lowered it → raise it back
    run("UPDATE customers SET balance = balance + ? WHERE id=? AND tenant_id=?", k.type === "credit" ? k.amount : -k.amount, k.customer_id, t);
  });
  audit(t, req.user!, "khata_entry_void", `khata:${k.id}`, { type: k.type, amount: k.amount, ref: k.ref, note: k.note, reason });
  return { ok: true, balance_after: round2(get("SELECT balance FROM customers WHERE id=?", k.customer_id)!.balance) };
}));

/* ---------------- 3. Stock delivery void ---------------- */

corrections.get("/corrections/deliveries", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const limit = Math.min(100, Math.max(1, Number(req.query.limit) || 40));
  const rows = all(
    `SELECT d.id, d.supplier, d.tanker_no, d.invoice_l, d.received_l, d.purchase_rate, d.shortage_pct, d.created_at,
            tk.name tank, tk.product,
            (SELECT status FROM shortage_claims c WHERE c.delivery_id=d.id) claim_status
     FROM deliveries d JOIN tanks tk ON tk.id=d.tank_id JOIN stations st ON st.id=tk.station_id
     WHERE st.tenant_id=? ORDER BY d.id DESC LIMIT ?`, t, limit)
    .map((d) => ({ ...d, amount: round2((d.invoice_l ?? 0) * (d.purchase_rate ?? 0)),
      // a delivery whose shortage claim is already being chased / recovered can't be voided here
      voidable: !d.claim_status || d.claim_status === "open" }));
  return { deliveries: rows };
}));

corrections.post("/corrections/delivery/:id/void", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const { reason } = parse(reasonField, req.body);
  const d = get(`SELECT d.*, tk.id tank_id2, tk.name tank, tk.product FROM deliveries d
     JOIN tanks tk ON tk.id=d.tank_id JOIN stations st ON st.id=tk.station_id WHERE d.id=? AND st.tenant_id=?`, Number(req.params.id), t);
  if (!d) throw new AppError(404, "Delivery nahi mili");
  const claim = get("SELECT id, status FROM shortage_claims WHERE delivery_id=?", d.id);
  if (claim && claim.status !== "open") throw new AppError(400, `Is delivery ka shortage claim pehle hi '${claim.status}' hai — pehle claim handle karein, phir delivery void karein.`);
  const sup: Row[] = all("SELECT * FROM supplier_txns WHERE tenant_id=? AND delivery_id=?", t, d.id);
  tx(() => {
    // the supplier payable + the Fuel-purchases expense both derive from this purchase row
    for (const s of sup) run("DELETE FROM supplier_txns WHERE id=? AND tenant_id=?", s.id, t);
    // take the received litres back out of the tank
    run("UPDATE tanks SET current_l = MAX(0, current_l - ?) WHERE id=?", d.received_l, d.tank_id);
    // drop a still-open shortage claim that this delivery had opened
    if (claim) run("DELETE FROM shortage_claims WHERE id=?", claim.id);
    run("DELETE FROM deliveries WHERE id=?", d.id);
  });
  const amount = round2(sup.reduce((a, s) => a + (s.amount ?? 0), 0));
  audit(t, req.user!, "delivery_void", `delivery:${d.id}`, { supplier: d.supplier, tanker_no: d.tanker_no, tank: d.tank, product: d.product, invoice_l: d.invoice_l, received_l: d.received_l, amount, reason });
  return { ok: true, reversed_amount: amount, stock_removed_l: round2(d.received_l) };
}));
