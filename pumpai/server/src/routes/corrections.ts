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
import { AppError, round2, pkr, reverseSale, recordSale, khataEntry, audit } from "../services.js";
import { moneyMethods } from "./lookups.js";
import { productSchema } from "../products.js";
import { insertTxn, clientDue } from "./wholesale.js";

export const corrections = Router();

const reasonField = z.object({ reason: z.string().trim().min(3, "Wajah likhein (kam se kam 3 harf)") });
const product = productSchema();

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

/** CEO edit of ANY fuel sale: reverse it + re-record with the new values in one transaction, so stock, meter, khata,
 *  cash/bank and the ledger all stay balanced. Money-side fields can change on any sale; the litres/amount only while the
 *  shift is still open (otherwise the closed shift's meter↔sales tie would break — void & re-enter instead). */
corrections.post("/corrections/sale/:id/edit", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({
    reason: z.string().trim().min(3, "Wajah likhein (kam se kam 3 harf)"),
    product: product.optional(), litres: z.number().positive().max(60000).optional(), amount: z.number().positive().max(100_000_000).optional(),
    payment_method: z.string().max(40).optional(), customer_id: z.number().int().positive().nullable().optional(),
    vehicle_no: z.string().max(40).nullable().optional(), slip_no: z.string().max(40).nullable().optional(), account_id: z.number().int().positive().nullable().optional(),
    discount: z.number().min(0).optional(), discount_kind: z.enum(["khata", "direct"]).optional(), discount_ref: z.string().trim().max(120).nullable().optional(),
  }), req.body);
  const sale = get(`SELECT s.*, sh.status shift_status FROM sales s JOIN stations st ON st.id=s.station_id LEFT JOIN shifts sh ON sh.id=s.shift_id WHERE s.id=? AND st.tenant_id=?`, Number(req.params.id), t);
  if (!sale) throw new AppError(404, "Sale nahi mili");
  if (!sale.created_by) throw new AppError(400, "Meter-settlement entry edit nahi hoti — ise void karein.");
  if (["coupon", "wallet", "loyalty"].includes(sale.payment_method) || sale.pending) throw new AppError(400, `${sale.payment_method}${sale.pending ? " (card-pending)" : ""} sale edit nahi hoti — void karke dobara daalein.`);
  if (sale.payment_method === "wholesale") throw new AppError(400, "Wholesale (nozzle) sale edit nahi hoti — void karke dobara daalein.");
  const method = b.payment_method ?? sale.payment_method;
  if (![...moneyMethods(t), "khata"].includes(method)) throw new AppError(400, `Sale sirf cash / card / online / khata ho sakti hai — ${method} nahi.`);
  const litresChanges = b.litres !== undefined || b.amount !== undefined;
  if (litresChanges && (!sale.shift_id || sale.shift_status !== "open")) throw new AppError(400, "Band shift ki sale ke litres/amount edit nahi hote (meter hisaab). Sirf khula shift — warna void karein.");
  const customer_id = b.customer_id !== undefined ? b.customer_id : sale.customer_id;
  if (method === "khata" && !customer_id) throw new AppError(400, "Khata sale ke liye customer chahiye");
  const litres = b.litres ?? (b.amount ? undefined : sale.litres);
  const before = { product: sale.product, litres: sale.litres, amount: sale.amount, method: sale.payment_method, customer_id: sale.customer_id, vehicle_no: sale.vehicle_no, slip_no: sale.slip_no };
  const saved = tx(() => {
    reverseSale(t, sale);
    return recordSale(t, {
      station_id: sale.station_id, product: b.product ?? sale.product, litres, amount: b.amount,
      // same fuel → keep the sale's own rate (price may have moved since); a new fuel reprices at its current rate
      rate: (b.product && b.product !== sale.product) ? undefined : sale.rate,
      payment_method: method, customer_id: method === "khata" ? customer_id : null,
      nozzle_id: sale.nozzle_id, shift_id: sale.shift_id, created_by: sale.created_by, created_at: sale.created_at,
      vehicle_no: b.vehicle_no !== undefined ? b.vehicle_no : sale.vehicle_no, slip_no: b.slip_no !== undefined ? b.slip_no : sale.slip_no,
      account_id: b.account_id !== undefined ? b.account_id : sale.account_id,
      discount: b.discount ?? sale.discount ?? 0, discount_kind: b.discount_kind ?? sale.discount_kind ?? undefined, discount_ref: b.discount_ref ?? sale.discount_ref ?? undefined,
    });
  });
  audit(t, req.user!, "sale_edit", `sale:${sale.id}`, { before, after: { product: saved.product, litres: saved.litres, amount: saved.amount, method: saved.payment_method, customer_id: saved.customer_id }, new_sale_id: saved.id, reason: b.reason });
  return { ok: true, sale: saved };
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
      const owner = ref.startsWith("SALE-") ? "sale" : ref.startsWith("oth-disc:") ? "other" : ref === "open:" ? "opening" : k.via_cashier ? "cashier" : null;
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
  if (ref === "open:") throw new AppError(400, "Ye opening balance (purana baqaya) hai — Opening balances page se badlein.");
  if (get("SELECT 1 FROM cashier_vouchers WHERE tenant_id=? AND src=?", t, `khata:${k.id}`)) throw new AppError(400, "Ye cashier ke voucher se bani hai — Cashier desk se voucher void karein.");
  tx(() => {
    run("DELETE FROM khata_ledger WHERE id=?", k.id);
    // a debit (charge) had raised the balance → lower it back; a credit (payment) had lowered it → raise it back
    run("UPDATE customers SET balance = balance + ? WHERE id=? AND tenant_id=?", k.type === "credit" ? k.amount : -k.amount, k.customer_id, t);
  });
  audit(t, req.user!, "khata_entry_void", `khata:${k.id}`, { type: k.type, amount: k.amount, ref: k.ref, note: k.note, reason });
  return { ok: true, balance_after: round2(get("SELECT balance FROM customers WHERE id=?", k.customer_id)!.balance) };
}));

/** CEO edit of a manual khata charge/payment: reverse the old balance move, then re-post with the new type/amount/note.
 *  Rows owned by another flow (a sale, Other-discount, opening, a cashier voucher) are sent to their own editor. */
corrections.post("/corrections/khata/:id/edit", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({ reason: z.string().trim().min(3, "Wajah likhein"), type: z.enum(["debit", "credit"]).optional(), amount: z.number().positive().max(100_000_000).optional(), note: z.string().max(200).nullable().optional() }), req.body);
  const k = get(`SELECT k.* FROM khata_ledger k JOIN customers c ON c.id=k.customer_id WHERE k.id=? AND c.tenant_id=?`, Number(req.params.id), t);
  if (!k) throw new AppError(404, "Khata entry nahi mili");
  const ref = String(k.ref ?? "");
  if (ref.startsWith("SALE-")) throw new AppError(400, "Ye ek sale ki entry hai — 'Sale edit' se badlein.");
  if (ref.startsWith("oth-disc:")) throw new AppError(400, "Ye Other-discount ki entry hai — Other entries page se badlein.");
  if (ref === "open:") throw new AppError(400, "Ye opening balance hai — Opening balances page se badlein.");
  if (get("SELECT 1 FROM cashier_vouchers WHERE tenant_id=? AND src=?", t, `khata:${k.id}`)) throw new AppError(400, "Ye cashier ke voucher se bani — Cashier desk se badlein.");
  const type = b.type ?? k.type, amount = round2(b.amount ?? k.amount), note = b.note !== undefined ? b.note : k.note;
  tx(() => {
    // reverse the old row's balance move, drop it, then re-post the corrected one (khataEntry also moves the balance)
    run("UPDATE customers SET balance = balance + ? WHERE id=?", k.type === "credit" ? k.amount : -k.amount, k.customer_id);
    run("DELETE FROM khata_ledger WHERE id=?", k.id);
    khataEntry(k.customer_id, type, amount, k.ref ?? null, note ?? null, null);
  });
  audit(t, req.user!, "khata_entry_edit", `khata:${k.id}`, { before: { type: k.type, amount: k.amount, note: k.note }, after: { type, amount, note }, reason: b.reason });
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
  if (d.bypass_drop_id) throw new AppError(400, "Ye bypass ka bacha hua tel hai — Bypass page se 'Undo' karein (bypass stock wapas jayega).");
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

/** CEO edit of a direct supplier payment / purchase / adjustment. The supplier balance (owed) and the bank leg are both
 *  DERIVED from this row, so edit = drop the old row + insert the corrected one (same type/supplier/date/method/account).
 *  Rows that belong to another flow (a delivery purchase, a cashier voucher, a WHT sibling, a payment whose tax is
 *  already deposited to FBR) are sent to their own editor so nothing is counted twice. */
corrections.post("/corrections/supplier-txn/:id/edit", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({
    reason: z.string().trim().min(3, "Wajah likhein (kam se kam 3 harf)"),
    amount: z.number().max(100_000_000).optional(), ref: z.string().max(120).nullable().optional(), note: z.string().max(200).nullable().optional(),
    litres: z.number().positive().max(10_000_000).nullable().optional(), rate: z.number().positive().max(100000).nullable().optional(),
  }), req.body);
  const x = get(`SELECT x.* FROM supplier_txns x JOIN suppliers s ON s.id=x.supplier_id WHERE x.id=? AND s.tenant_id=?`, Number(req.params.id), t);
  if (!x) throw new AppError(404, "Supplier entry nahi mili");
  if (x.method === "WHT") throw new AppError(400, "Ye withholding-tax ka hissa hai — asal payment edit karein.");
  if (x.delivery_id) throw new AppError(400, "Ye entry ek tanker delivery se bani — 'Delivery' se badlein.");
  if (get("SELECT 1 FROM cashier_vouchers WHERE tenant_id=? AND src=?", t, `stx:${x.id}`)) throw new AppError(400, "Ye cashier ke voucher se bani — Cashier desk se badlein.");
  // a payment that carried withholding tax touches FBR records — too much to re-wire here; void & re-enter
  if (x.type === "payment" && get("SELECT 1 FROM supplier_txns WHERE tenant_id=? AND supplier_id=? AND method='WHT' AND txn_date=? AND id<>?", t, x.supplier_id, x.txn_date, x.id))
    throw new AppError(400, "Is payment par withholding tax laga hua hai — ise void karke dobara daalein.");
  const type = x.type as string;
  const litres = type === "purchase" ? (b.litres !== undefined ? b.litres : x.litres) : null;
  const rate = type === "purchase" ? (b.rate !== undefined ? b.rate : x.rate) : null;
  // a purchase's amount follows litres×rate unless given; adjustments keep their sign
  const amount = b.amount != null ? round2(b.amount) : (type === "purchase" && litres && rate ? round2(litres * rate) : round2(x.amount));
  if (type === "adjustment" ? amount === 0 : amount <= 0) throw new AppError(400, "Amount theek nahi");
  const ref = b.ref !== undefined ? b.ref : x.ref, note = b.note !== undefined ? b.note : x.note;
  const before = { type, amount: x.amount, method: x.method, ref: x.ref, note: x.note, litres: x.litres, rate: x.rate };
  tx(() => {
    run("DELETE FROM supplier_txns WHERE id=? AND tenant_id=?", x.id, t);
    run(`INSERT INTO supplier_txns (tenant_id,supplier_id,type,amount,method,ref,note,product,litres,rate,created_by,txn_date,created_at,account_id)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
      t, x.supplier_id, type, amount, x.method ?? null, ref ?? null, note ?? null, x.product ?? null, litres, rate, x.created_by ?? req.user!.name, x.txn_date, now(), x.account_id ?? null);
  });
  audit(t, req.user!, "supplier_txn_edit", `stx:${x.id}`, { before, after: { type, amount, method: x.method, ref, note, litres, rate }, reason: b.reason });
  return { ok: true, amount: round2(amount) };
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

/** CEO edit of a direct wholesale supply or payment. Old row is voided (stock put back, supplier leg cleared) and a fresh
 *  corrected row is posted — the client's due (and the tank, on a supply) land on the new numbers. Bulk trips, cashier
 *  vouchers, depot-direct (bypass) supplies and POS pay-now sales belong to their own flow and are refused here. */
corrections.post("/corrections/wholesale-txn/:id/edit", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({
    reason: z.string().trim().min(3, "Wajah likhein (kam se kam 3 harf)"),
    litres: z.number().positive().max(10_000_000).optional(), rate: z.number().positive().max(100000).optional(), amount: z.number().positive().max(100_000_000).optional(),
    method: z.string().max(40).optional(), vehicle_no: z.string().max(40).nullable().optional(), note: z.string().max(200).nullable().optional(),
  }), req.body);
  const x = get(`SELECT x.* FROM wholesale_txns x JOIN wholesale_clients c ON c.id=x.client_id WHERE x.id=? AND c.tenant_id=? AND x.voided=0`, Number(req.params.id), t);
  if (!x) throw new AppError(404, "Wholesale entry nahi mili");
  if (x.trip_id) throw new AppError(400, "Ye ek tanker-trip (bulk) entry hai — trip ko wholesale page se badlein.");
  if (x.sale_id) throw new AppError(400, "Ye POS par bechi gayi wholesale sale hai — ise void karke dobara daalein.");
  if (get("SELECT 1 FROM cashier_vouchers WHERE tenant_id=? AND src=?", t, `wtx:${x.id}`)) throw new AppError(400, "Ye cashier ke voucher se bani — Cashier desk se badlein.");
  if (get("SELECT 1 FROM supplier_txns WHERE tenant_id=? AND ref=?", t, `wtx:${x.id}`)) throw new AppError(400, "Ye depot-direct (bypass) supply hai — ise void karke dobara daalein.");
  const isSupply = x.type === "supply";
  const litres = isSupply ? (b.litres ?? x.litres) : null;
  const rate = isSupply ? (b.rate ?? x.rate) : null;
  const amount = b.amount != null ? round2(b.amount) : (isSupply && litres && rate ? round2(litres * rate) : round2(x.amount));
  const method = isSupply ? x.method : (b.method ?? x.method);
  const before = { type: x.type, litres: x.litres, rate: x.rate, amount: x.amount, method: x.method, vehicle_no: x.vehicle_no, note: x.note };
  // a tank supply must still fit after we hand back the old litres
  if (isSupply && x.tank_id) {
    const avail = round2((get("SELECT current_l FROM tanks WHERE id=?", x.tank_id)?.current_l ?? 0) + (x.litres ?? 0));
    if (litres! > avail) throw new AppError(400, `Tank me itna stock nahi (${Math.round(avail)} L mojood)`);
  }
  const saved = tx(() => {
    run("UPDATE wholesale_txns SET voided=1, void_reason=? WHERE id=?", `${req.user!.name} (edit): ${b.reason}`, x.id);
    if (isSupply && x.tank_id && x.litres) run("UPDATE tanks SET current_l = current_l + ? WHERE id=?", x.litres, x.tank_id);
    if (isSupply && x.tank_id && litres) run("UPDATE tanks SET current_l = current_l - ? WHERE id=?", litres, x.tank_id);
    return insertTxn(req, x.client_id, {
      type: x.type, station_id: x.station_id, tank_id: x.tank_id, product: x.product, litres, rate, amount, method,
      vehicle_no: b.vehicle_no !== undefined ? b.vehicle_no : x.vehicle_no, ref: x.ref, note: b.note !== undefined ? b.note : x.note,
      txn_date: x.txn_date, account_id: x.account_id,
    });
  });
  audit(t, req.user!, "wholesale_txn_edit", `wtx:${x.id}`, { before, after: { type: x.type, litres, rate, amount, method }, new_txn_id: saved.id, reason: b.reason });
  return { ok: true, txn_id: saved.id, amount: round2(amount), due_after: clientDue(x.client_id) };
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
