/**
 * CEO corrections — undo a WRONG entry made anywhere (salesman / cashier / manager) and reverse everything it moved,
 * so the whole system stays tallied to the rupee. Companion to the cashier voucher void (which covers cash
 * receive/pay) and the Other-entries undo. All CEO-only (perm `corrections.manage`), each writes an audit row,
 * and after any of them the Audit → "Hisaab check" (GET /books/check) must stay all-green (0 rupaye farq).
 *
 * The ledger is DERIVED (it re-reads the source tables), so each void = undo the source row(s) + restore the
 * paired balance column(s). Kinds:
 *   1. Sale          — any fuel sale (POS, khata, wallet, coupon, loyalty, card, or a late/online close entry).
 *   2. Khata entry   — a manual khata charge/payment made directly on a customer (not a sale, not a cashier voucher,
 *                      not an Other-discount — those have their own undo).
 *   3. Delivery      — a fuel tanker received into a tank (reverses the supplier payable + the stock).
 *   4. Supplier txn  — a direct depot payment/purchase/adjustment + its withholding-tax sibling (not a cashier
 *                      voucher payment, not a delivery purchase — those go to Cashier/Delivery void).
 *   5. Wholesale txn — a direct client supply/payment/etc. (restores tank stock on a supply; not a bulk trip or a
 *                      cashier-voucher payment).
 *   6. Rent          — a rent receipt (reverses its office voucher + bank deposit + the payment row).
 * (Carriage/thekedar entries already have their own void at POST /carriage/txns/:id/void.)
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, tx, now, type Row } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { AppError, round2, pkr, reverseSale, audit } from "../services.js";

export const corrections = Router();

const reasonField = z.object({ reason: z.string().trim().min(3, "Wajah likhein (kam se kam 3 harf)") });

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

/* ---------------- 4. Supplier (depot) payment / purchase / adjustment void ---------------- */

/** Recent direct supplier_txns the CEO can void (not the ones made through a cashier voucher or a delivery). */
corrections.get("/corrections/supplier-txns", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const limit = Math.min(100, Math.max(1, Number(req.query.limit) || 40));
  const rows = all(
    `SELECT x.id, x.type, x.amount, x.method, x.ref, x.note, x.product, x.litres, x.rate, x.delivery_id, x.txn_date, s.name supplier,
            EXISTS(SELECT 1 FROM cashier_vouchers v WHERE v.tenant_id=? AND v.src='stx:'||x.id) via_cashier
     FROM supplier_txns x JOIN suppliers s ON s.id=x.supplier_id
     WHERE x.tenant_id=? AND x.method IS NOT 'WHT' ORDER BY x.id DESC LIMIT ?`, t, t, limit)
    .map((x) => ({ ...x, via_cashier: undefined,
      // a purchase tied to a tanker delivery is undone from Delivery void; a cashier-voucher payment from Cashier void
      voidable: !x.delivery_id && !x.via_cashier,
      owner: x.delivery_id ? "delivery" : x.via_cashier ? "cashier" : null }));
  return { txns: rows };
}));

corrections.post("/corrections/supplier-txn/:id/void", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const { reason } = parse(reasonField, req.body);
  const x = get(`SELECT x.* FROM supplier_txns x JOIN suppliers s ON s.id=x.supplier_id WHERE x.id=? AND s.tenant_id=?`, Number(req.params.id), t);
  if (!x) throw new AppError(404, "Supplier entry nahi mili");
  if (x.method === "WHT") throw new AppError(400, "Ye withholding-tax ka hissa hai — asal payment void karein, ye khud-ba-khud hat jayega.");
  if (x.delivery_id) throw new AppError(400, "Ye entry ek tanker delivery se bani — 'Delivery void' se theek karein.");
  if (get("SELECT 1 FROM cashier_vouchers WHERE tenant_id=? AND src=?", t, `stx:${x.id}`)) throw new AppError(400, "Ye cashier ke voucher se bani — Cashier desk se voucher void karein.");
  // a payment may have a withholding-tax sibling (same supplier, same instant, method 'WHT') + a tax_withholdings record
  const wht = x.type === "payment" ? get("SELECT * FROM supplier_txns WHERE tenant_id=? AND supplier_id=? AND method='WHT' AND txn_date=? AND id<>?", t, x.supplier_id, x.txn_date, x.id) : null;
  const whtRec = wht ? get("SELECT * FROM tax_withholdings WHERE supplier_txn_id=?", wht.id) : null;
  if (whtRec?.cpr_no) throw new AppError(400, "Is payment ka withholding tax FBR me jama (CPR) ho chuka — pehle tax deposit undo karein.");
  tx(() => {
    run("DELETE FROM supplier_txns WHERE id=? AND tenant_id=?", x.id, t);
    if (wht) {
      run("DELETE FROM supplier_txns WHERE id=? AND tenant_id=?", wht.id, t);
      if (whtRec) run("DELETE FROM tax_withholdings WHERE id=? AND tenant_id=?", whtRec.id, t);
    }
  });
  audit(t, req.user!, "supplier_txn_void", `stx:${x.id}`, { type: x.type, amount: x.amount, method: x.method, ref: x.ref, product: x.product, withholding: wht?.amount ?? 0, reason });
  return { ok: true, reversed: round2(x.amount), withholding_reversed: round2(wht?.amount ?? 0) };
}));

/* ---------------- 5. Wholesale txn void ---------------- */

corrections.get("/corrections/wholesale-txns", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const limit = Math.min(100, Math.max(1, Number(req.query.limit) || 40));
  const rows = all(
    `SELECT x.id, x.type, x.amount, x.litres, x.rate, x.product, x.method, x.ref, x.note, x.trip_id, x.tank_id, x.txn_date, c.name client,
            EXISTS(SELECT 1 FROM cashier_vouchers v WHERE v.tenant_id=? AND v.src='wtx:'||x.id) via_cashier
     FROM wholesale_txns x JOIN wholesale_clients c ON c.id=x.client_id
     WHERE x.tenant_id=? AND x.voided=0 ORDER BY x.id DESC LIMIT ?`, t, t, limit)
    .map((x) => ({ ...x, via_cashier: undefined,
      // a bulk tanker-trip entry or a cashier-voucher payment is undone from its own place
      voidable: !x.trip_id && !x.via_cashier,
      owner: x.trip_id ? "trip" : x.via_cashier ? "cashier" : null }));
  return { txns: rows };
}));

corrections.post("/corrections/wholesale-txn/:id/void", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const { reason } = parse(reasonField, req.body);
  const x = get(`SELECT x.* FROM wholesale_txns x JOIN wholesale_clients c ON c.id=x.client_id WHERE x.id=? AND c.tenant_id=? AND x.voided=0`, Number(req.params.id), t);
  if (!x) throw new AppError(404, "Wholesale entry nahi mili");
  if (x.trip_id) throw new AppError(400, "Ye ek tanker-trip (bulk) entry hai — trip ko wholesale page se void karein.");
  if (get("SELECT 1 FROM cashier_vouchers WHERE tenant_id=? AND src=?", t, `wtx:${x.id}`)) throw new AppError(400, "Ye cashier ke voucher se bani — Cashier desk se voucher void karein.");
  tx(() => {
    run("UPDATE wholesale_txns SET voided=1, void_reason=? WHERE id=?", `${req.user!.name}: ${reason}`, x.id);
    // a supply drew stock out of a tank — put it back
    if (x.type === "supply" && x.tank_id && x.litres) run("UPDATE tanks SET current_l = current_l + ? WHERE id=?", x.litres, x.tank_id);
    // a depot-direct supply booked a supplier leg against ref wtx:<id>
    run("DELETE FROM supplier_txns WHERE tenant_id=? AND ref=?", t, `wtx:${x.id}`);
  });
  audit(t, req.user!, "wholesale_txn_void", `wtx:${x.id}`, { type: x.type, amount: x.amount, litres: x.litres, product: x.product, method: x.method, reason });
  return { ok: true, reversed: round2(x.amount) };
}));

/* ---------------- 6. Rent receipt void ---------------- */

corrections.get("/corrections/rent", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const rows = all(
    `SELECT p.id, p.amount, p.method, p.account_id, p.for_month, p.ref, p.note, p.created_at, r.name unit, r.tenant_name
     FROM rental_payments p JOIN rentals r ON r.id=p.rental_id WHERE p.tenant_id=? ORDER BY p.id DESC LIMIT 40`, t);
  return { payments: rows };
}));

corrections.post("/corrections/rent/:id/void", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const { reason } = parse(reasonField, req.body);
  const p = get(`SELECT p.*, r.name unit FROM rental_payments p JOIN rentals r ON r.id=p.rental_id WHERE p.id=? AND p.tenant_id=?`, Number(req.params.id), t);
  if (!p) throw new AppError(404, "Rent payment nahi mila");
  const note = `Rent — ${p.unit}${p.tenant_name ? ` (${p.tenant_name})` : ""} · ${p.for_month}${p.note ? ` · ${p.note}` : ""}`;
  tx(() => {
    // the three rows the receipt created: the office voucher (exact id), the bank deposit (if any), and the payment row
    if (p.voucher_id) run("DELETE FROM cashier_vouchers WHERE id=? AND tenant_id=?", p.voucher_id, t);
    if (p.account_id) run("DELETE FROM bank_txns WHERE id = (SELECT id FROM bank_txns WHERE tenant_id=? AND account_id=? AND kind='other_in' AND amount=? AND note=? ORDER BY id DESC LIMIT 1)",
      t, p.account_id, p.amount, note);
    run("DELETE FROM rental_payments WHERE id=? AND tenant_id=?", p.id, t);
  });
  audit(t, req.user!, "rent_void", `rentpay:${p.id}`, { unit: p.unit, amount: p.amount, method: p.method, for_month: p.for_month, reason });
  return { ok: true, reversed: round2(p.amount) };
}));
