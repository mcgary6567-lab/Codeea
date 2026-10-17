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
import { all, get, run, tx, now, pkStart, pkEnd, type Row } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import type { Request } from "express";
import { AppError, round2, pkr, reverseSale, recordSale, khataEntry, audit, adjustClosedShiftCash, saleCash } from "../services.js";
import { moneyMethods } from "./lookups.js";
import { productSchema } from "../products.js";
import { insertTxn, clientDue } from "./wholesale.js";
import { bankAccountFor } from "./banks.js";
import { recordPurchase } from "./suppliers.js";
import { claimForDelivery } from "./claims.js";

export const corrections = Router();

const reasonField = z.object({ reason: z.string().trim().min(3, "Wajah likhein (kam se kam 3 harf)") });
const product = productSchema();

/* ---------------- shared list filters (q / from / to / limit) for every "recent rows" endpoint below ---------------- */

const dayStr = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "YYYY-MM-DD");
const listQuery = z.object({
  q: z.string().trim().max(80).optional(),
  from: z.preprocess((v) => (v === "" ? undefined : v), dayStr.optional()),
  to: z.preprocess((v) => (v === "" ? undefined : v), dayStr.optional()),
  limit: z.preprocess((v) => (v === "" || v == null ? undefined : v), z.coerce.number().int().min(1).max(500).optional()),
});
/** Reads the optional `q` / `from` / `to` / `limit` of a list request: `q` LIKE-matches any of `cols` (every word),
 *  `from`/`to` are Pakistan calendar days on `dateCol`. Returns the extra ` AND …` SQL, its params and the row cap. */
function listFilter(req: Request, cols: string[], dateCol: string, defaultLimit: number) {
  const f = parse(listQuery, req.query);
  const sql: string[] = [], params: string[] = [];
  for (const w of (f.q ?? "").split(/\s+/).filter(Boolean)) {
    sql.push(`(${cols.map((c) => `${c} LIKE ?`).join(" OR ")})`);
    params.push(...cols.map(() => `%${w}%`));
  }
  if (f.from) { sql.push(`${dateCol} >= ?`); params.push(pkStart(f.from)); }
  if (f.to) { sql.push(`${dateCol} < ?`); params.push(pkEnd(f.to)); }
  return { where: sql.map((x) => ` AND ${x}`).join(""), params, limit: f.limit ?? defaultLimit };
}

/* ---------------- 1. Sale void ---------------- */

/** Recent fuel sales the CEO can pick from to void a wrong one. */
corrections.get("/corrections/sales", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const f = listFilter(req, ["c.name", "s.vehicle_no", "s.slip_no", "s.product", "s.payment_method", "u.name", "CAST(s.amount AS TEXT)"], "s.created_at", 60);
  const rows = all(
    `SELECT s.id, s.product, s.litres, s.rate, s.amount, s.payment_method, s.vehicle_no, s.slip_no, s.pending, s.at_close, s.source, s.created_at,
            st.name station, c.name customer, u.name by_name
     FROM sales s JOIN stations st ON st.id=s.station_id
       LEFT JOIN customers c ON c.id=s.customer_id
       LEFT JOIN users u ON u.id=s.created_by
     WHERE st.tenant_id=?${f.where}
     ORDER BY s.id DESC LIMIT ?`, t, ...f.params, f.limit);
  return { sales: rows };
}));

corrections.post("/corrections/sale/:id/void", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const { reason } = parse(reasonField, req.body);
  const sale = get(`SELECT s.* FROM sales s JOIN stations st ON st.id=s.station_id WHERE s.id=? AND st.tenant_id=?`, Number(req.params.id), t);
  if (!sale) throw new AppError(404, "Sale nahi mili (shayad pehle hi void ho chuki)");
  const customer = sale.customer_id ? get("SELECT name FROM customers WHERE id=?", sale.customer_id) : null;
  const shift = sale.shift_id ? get("SELECT * FROM shifts WHERE id=?", sale.shift_id) : null;
  const { did, shiftCash } = tx(() => {
    const did = reverseSale(t, sale);
    // a cash sale on a CLOSED shift: that cash was counted in the bag, so the shift's expected cash drops and the rest shows as "over"
    const shiftCash = adjustClosedShiftCash(shift, saleCash(sale.payment_method, sale.amount), "sale void");
    if (shiftCash) did.push(`shift #${shiftCash.shift_id} cash expected ${pkr(shiftCash.cash_expected_before)} → ${pkr(shiftCash.cash_expected)}`);
    return { did, shiftCash };
  });
  audit(t, req.user!, "sale_void", `sale:${sale.id}`, { product: sale.product, litres: sale.litres, amount: sale.amount, method: sale.payment_method, customer: customer?.name ?? null, reason, reversed: did, shift_cash: shiftCash });
  return { ok: true, reversed: did, amount: round2(sale.amount), shift_cash: shiftCash };
}));

/** CEO edit of ANY fuel sale: reverse it + re-record with the new values in one transaction, so stock, meter, khata,
 *  cash/bank and the ledger all stay balanced. Money-side fields can change on any sale; the litres/amount only while the
 *  shift is still open (otherwise the closed shift's meter↔sales tie would break — void & re-enter instead). */
corrections.post("/corrections/sale/:id/edit", requirePerm("entries.edit"), h((req) => {
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
  const shift = sale.shift_id ? get("SELECT * FROM shifts WHERE id=?", sale.shift_id) : null;
  const { saved, shiftCash } = tx(() => {
    reverseSale(t, sale);
    const saved = recordSale(t, {
      station_id: sale.station_id, product: b.product ?? sale.product, litres, amount: b.amount,
      // same fuel → keep the sale's own rate (price may have moved since); a new fuel reprices at its current rate
      rate: (b.product && b.product !== sale.product) ? undefined : sale.rate,
      payment_method: method, customer_id: method === "khata" ? customer_id : null,
      nozzle_id: sale.nozzle_id, shift_id: sale.shift_id, created_by: sale.created_by, created_at: sale.created_at,
      vehicle_no: b.vehicle_no !== undefined ? b.vehicle_no : sale.vehicle_no, slip_no: b.slip_no !== undefined ? b.slip_no : sale.slip_no,
      account_id: b.account_id !== undefined ? b.account_id : sale.account_id,
      discount: b.discount ?? sale.discount ?? 0, discount_kind: b.discount_kind ?? sale.discount_kind ?? undefined, discount_ref: b.discount_ref ?? sale.discount_ref ?? undefined,
    });
    // on a CLOSED shift the bag's cash follows: cash → khata/card (or a smaller cash amount) frees counted cash → expected drops;
    // khata/card → cash would ADD cash that was never counted → refused (the tx rolls back)
    const shiftCash = adjustClosedShiftCash(shift, saleCash(sale.payment_method, sale.amount) - saleCash(saved.payment_method, saved.amount), `${sale.payment_method} → ${saved.payment_method} edit`);
    return { saved, shiftCash };
  });
  audit(t, req.user!, "sale_edit", `sale:${sale.id}`, { before, after: { product: saved.product, litres: saved.litres, amount: saved.amount, method: saved.payment_method, customer_id: saved.customer_id }, new_sale_id: saved.id, reason: b.reason, shift_cash: shiftCash });
  return { ok: true, sale: saved, shift_cash: shiftCash };
}));

/* ---------------- 2. Manual khata entry void ---------------- */

/** Recent manual khata entries (charges/payments) for one customer, each flagged whether this tool may void it. */
corrections.get("/corrections/khata", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const customerId = Number(req.query.customer_id);
  const c = get("SELECT id, name, balance FROM customers WHERE id=? AND tenant_id=?", customerId, t);
  if (!c) throw new AppError(404, "Customer nahi mila");
  const f = listFilter(req, ["k.ref", "k.note", "k.type", "CAST(k.amount AS TEXT)"], "k.created_at", 60);
  const rows = all(
    `SELECT k.id, k.type, k.amount, k.ref, k.note, k.created_at,
            EXISTS(SELECT 1 FROM cashier_vouchers v WHERE v.tenant_id=? AND v.src='khata:'||k.id) via_cashier
     FROM khata_ledger k WHERE k.customer_id=?${f.where} ORDER BY k.id DESC LIMIT ?`, t, c.id, ...f.params, f.limit)
    .map((k) => {
      // what owns this row decides where it must be undone
      const ref = String(k.ref ?? "");
      const owner = ref.startsWith("SALE-") ? "sale" : ref.startsWith("SHOP-") ? "shop" : ref.startsWith("oth-") ? "other" : ref === "open:" ? "opening" : k.via_cashier ? "cashier" : null;
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
  if (ref.startsWith("SHOP-")) throw new AppError(400, "Ye shop sale ki khata entry hai — Shop page se sale undo/edit karein.");
  if (ref.startsWith("oth-")) throw new AppError(400, "Ye Other-entries (CEO) ki entry hai — Other entries page se undo karein.");
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
corrections.post("/corrections/khata/:id/edit", requirePerm("entries.edit"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({ reason: z.string().trim().min(3, "Wajah likhein"), type: z.enum(["debit", "credit"]).optional(), amount: z.number().positive().max(100_000_000).optional(), note: z.string().max(200).nullable().optional() }), req.body);
  const k = get(`SELECT k.* FROM khata_ledger k JOIN customers c ON c.id=k.customer_id WHERE k.id=? AND c.tenant_id=?`, Number(req.params.id), t);
  if (!k) throw new AppError(404, "Khata entry nahi mili");
  const ref = String(k.ref ?? "");
  if (ref.startsWith("SALE-")) throw new AppError(400, "Ye ek sale ki entry hai — 'Sale edit' se badlein.");
  if (ref.startsWith("SHOP-")) throw new AppError(400, "Ye shop sale ki khata entry hai — Shop page se sale edit karein.");
  if (ref.startsWith("oth-")) throw new AppError(400, "Ye Other-entries (CEO) ki entry hai — Other entries page se badlein.");
  if (ref === "open:") throw new AppError(400, "Ye opening balance hai — Opening balances page se badlein.");
  if (get("SELECT 1 FROM cashier_vouchers WHERE tenant_id=? AND src=?", t, `khata:${k.id}`)) throw new AppError(400, "Ye cashier ke voucher se bani — Cashier desk se badlein.");
  const type = b.type ?? k.type, amount = round2(b.amount ?? k.amount), note = b.note !== undefined ? b.note : k.note;
  tx(() => {
    // reverse the old row's balance move, drop it, then re-post the corrected one (khataEntry also moves the balance)
    run("UPDATE customers SET balance = balance + ? WHERE id=?", k.type === "credit" ? k.amount : -k.amount, k.customer_id);
    run("DELETE FROM khata_ledger WHERE id=?", k.id);
    khataEntry(k.customer_id, type, amount, k.ref ?? null, note ?? null, k.account_id ?? null);
    // the corrected row keeps the original day (cash book / bank / ledger stay in the right period)
    run("UPDATE khata_ledger SET created_at=? WHERE id=(SELECT MAX(id) FROM khata_ledger WHERE customer_id=?)", k.created_at, k.customer_id);
  });
  audit(t, req.user!, "khata_entry_edit", `khata:${k.id}`, { before: { type: k.type, amount: k.amount, note: k.note }, after: { type, amount, note }, reason: b.reason });
  return { ok: true, balance_after: round2(get("SELECT balance FROM customers WHERE id=?", k.customer_id)!.balance) };
}));

/* ---------------- 3. Stock delivery void ---------------- */

corrections.get("/corrections/deliveries", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const f = listFilter(req, ["d.supplier", "d.tanker_no", "tk.name", "tk.product", "CAST(ROUND(d.invoice_l*d.purchase_rate) AS INTEGER)"], "d.created_at", 40);
  const rows = all(
    `SELECT d.id, d.supplier, d.tanker_no, d.invoice_l, d.received_l, d.purchase_rate, d.shortage_pct, d.created_at,
            tk.name tank, tk.product,
            (SELECT status FROM shortage_claims c WHERE c.delivery_id=d.id) claim_status
     FROM deliveries d JOIN tanks tk ON tk.id=d.tank_id JOIN stations st ON st.id=tk.station_id
     WHERE st.tenant_id=?${f.where} ORDER BY d.id DESC LIMIT ?`, t, ...f.params, f.limit)
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
  const f = listFilter(req, ["s.name", "x.type", "x.method", "x.ref", "x.note", "x.product", "CAST(x.amount AS TEXT)"], "x.txn_date", 40);
  const rows = all(
    `SELECT x.id, x.type, x.amount, x.method, x.ref, x.note, x.product, x.litres, x.rate, x.delivery_id, x.txn_date, s.name supplier,
            EXISTS(SELECT 1 FROM cashier_vouchers v WHERE v.tenant_id=? AND v.src='stx:'||x.id) via_cashier
     FROM supplier_txns x JOIN suppliers s ON s.id=x.supplier_id
     WHERE x.tenant_id=? AND x.method IS NOT 'WHT'${f.where} ORDER BY x.id DESC LIMIT ?`, t, t, ...f.params, f.limit)
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
  if (String(x.ref ?? "").startsWith("wtx:")) throw new AppError(400, "Ye wholesale client ki depot-direct payment ka hissa hai — wholesale payment void karein.");
  if (String(x.ref ?? "").startsWith("claim:")) throw new AppError(400, "Ye shortage claim ki credit note hai — Claims page se badlein.");
  if (String(x.ref ?? "").startsWith("shopbuy:")) throw new AppError(400, "Ye shop ke maal ki kharidari hai — Shop page (stock history) se dekhein.");
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
corrections.post("/corrections/supplier-txn/:id/edit", requirePerm("entries.edit"), h((req) => {
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
  if (String(x.ref ?? "").startsWith("wtx:")) throw new AppError(400, "Ye wholesale client ki depot-direct payment ka hissa hai — wholesale payment se badlein.");
  if (String(x.ref ?? "").startsWith("claim:")) throw new AppError(400, "Ye shortage claim ki credit note hai — Claims page se badlein.");
  if (String(x.ref ?? "").startsWith("shopbuy:")) throw new AppError(400, "Ye shop ke maal ki kharidari hai — Shop page (stock history) se dekhein.");
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
      t, x.supplier_id, type, amount, x.method ?? null, ref ?? null, note ?? null, x.product ?? null, litres, rate, x.created_by ?? req.user!.name, x.txn_date, x.created_at ?? now(), x.account_id ?? null);
  });
  audit(t, req.user!, "supplier_txn_edit", `stx:${x.id}`, { before, after: { type, amount, method: x.method, ref, note, litres, rate }, reason: b.reason });
  return { ok: true, amount: round2(amount) };
}));

/* ---------------- 5. Wholesale txn void ---------------- */

/** A wholesale row other flows hang off (CEO other-entry legs, a POS wholesale sale, bypass payments/drops) is not voidable here. */
const wtxLinked = (t: number, x: Row): string | null =>
  String(x.ref ?? "").startsWith("oth-") ? "Ye Other-entries (CEO) ki entry hai — wahin se badlein/undo karein."
  : x.sale_id ? "Ye POS par bechi gayi wholesale sale hai — Sale void/edit se theek karein."
  : get("SELECT 1 FROM bypass_supplier_payments WHERE tenant_id=? AND wtx_id=? AND voided=0", t, x.id) ? "Is par bypass supplier payment judi hai — Bypass page se void karein."
  : get("SELECT 1 FROM bypass_drops WHERE tenant_id=? AND wtx_id=? AND voided=0", t, x.id) ? "Ye bypass delivery (drop) ki entry hai — Bypass page se void karein."
  : null;
corrections.get("/corrections/wholesale-txns", requirePerm("corrections.manage"), h((req) => {
  const t = tid(req);
  const f = listFilter(req, ["c.name", "x.type", "x.method", "x.ref", "x.note", "x.product", "x.vehicle_no", "CAST(x.amount AS TEXT)"], "x.txn_date", 40);
  const rows = all(
    `SELECT x.id, x.type, x.amount, x.litres, x.rate, x.product, x.method, x.ref, x.note, x.trip_id, x.tank_id, x.txn_date, c.name client,
            EXISTS(SELECT 1 FROM cashier_vouchers v WHERE v.tenant_id=? AND v.src='wtx:'||x.id) via_cashier
     FROM wholesale_txns x JOIN wholesale_clients c ON c.id=x.client_id
     WHERE x.tenant_id=? AND x.voided=0${f.where} ORDER BY x.id DESC LIMIT ?`, t, t, ...f.params, f.limit)
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
  { const why = wtxLinked(t, x); if (why) throw new AppError(400, why); }
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
corrections.post("/corrections/wholesale-txn/:id/edit", requirePerm("entries.edit"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({
    reason: z.string().trim().min(3, "Wajah likhein (kam se kam 3 harf)"),
    litres: z.number().positive().max(10_000_000).optional(), rate: z.number().positive().max(100000).optional(), amount: z.number().positive().max(100_000_000).optional(),
    method: z.string().max(40).optional(), vehicle_no: z.string().max(40).nullable().optional(), note: z.string().max(200).nullable().optional(),
  }), req.body);
  const x = get(`SELECT x.* FROM wholesale_txns x JOIN wholesale_clients c ON c.id=x.client_id WHERE x.id=? AND c.tenant_id=? AND x.voided=0`, Number(req.params.id), t);
  if (!x) throw new AppError(404, "Wholesale entry nahi mili");
  if (x.trip_id) throw new AppError(400, "Ye ek tanker-trip (bulk) entry hai — trip ko wholesale page se badlein.");
  { const why = wtxLinked(t, x); if (why) throw new AppError(400, why); }
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
  const f = listFilter(req, ["r.name", "r.tenant_name", "p.method", "p.ref", "p.note", "p.for_month", "CAST(p.amount AS TEXT)"], "p.created_at", 40);
  const rows = all(
    `SELECT p.id, p.amount, p.method, p.account_id, p.for_month, p.ref, p.note, p.created_at, r.name unit, r.tenant_name
     FROM rental_payments p JOIN rentals r ON r.id=p.rental_id WHERE p.tenant_id=?${f.where} ORDER BY p.id DESC LIMIT ?`, t, ...f.params, f.limit);
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

/** CEO edit of a rent receipt: drop the old three rows (office voucher + bank deposit + payment) and re-post the
 *  corrected ones, so the office cash book / bank and the rent income all move to the new amount, month or method. */
corrections.post("/corrections/rent/:id/edit", requirePerm("entries.edit"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({
    reason: z.string().trim().min(3, "Wajah likhein (kam se kam 3 harf)"),
    amount: z.number().positive().max(100_000_000).optional(), for_month: z.string().regex(/^\d{4}-\d{2}$/).optional(),
    method: z.string().max(40).optional(), account_id: z.number().int().positive().nullable().optional(),
    ref: z.string().max(60).nullable().optional(), note: z.string().max(200).nullable().optional(),
  }), req.body);
  const p = get(`SELECT p.*, r.name unit, r.tenant_name FROM rental_payments p JOIN rentals r ON r.id=p.rental_id WHERE p.id=? AND p.tenant_id=?`, Number(req.params.id), t);
  if (!p) throw new AppError(404, "Rent payment nahi mila");
  const amount = round2(b.amount ?? p.amount), forMonth = b.for_month ?? p.for_month, method = b.method ?? p.method;
  const ref = b.ref !== undefined ? b.ref : p.ref, userNote = b.note !== undefined ? b.note : p.note;
  const isCash = /^cash$/i.test(method);
  const account = isCash ? null : bankAccountFor(t, b.account_id !== undefined ? b.account_id : p.account_id, method);
  if (!isCash && !account) throw new AppError(400, "Bank account chunein jahan rent aaya");
  const oldNote = `Rent — ${p.unit}${p.tenant_name ? ` (${p.tenant_name})` : ""} · ${p.for_month}${p.note ? ` · ${p.note}` : ""}`;
  const newNote = `Rent — ${p.unit}${p.tenant_name ? ` (${p.tenant_name})` : ""} · ${forMonth}${userNote ? ` · ${userNote}` : ""}`;
  const before = { amount: p.amount, for_month: p.for_month, method: p.method, note: p.note };
  tx(() => {
    // reverse the old three rows (same as the rent void)
    if (p.voucher_id) run("DELETE FROM cashier_vouchers WHERE id=? AND tenant_id=?", p.voucher_id, t);
    if (p.account_id) run("DELETE FROM bank_txns WHERE id = (SELECT id FROM bank_txns WHERE tenant_id=? AND account_id=? AND kind='other_in' AND amount=? AND note=? ORDER BY id DESC LIMIT 1)", t, p.account_id, p.amount, oldNote);
    // re-post with the corrected numbers (voucher so the office cash book counts it; bank line if it came to a bank)
    const { id: vid } = run(`INSERT INTO cashier_vouchers (tenant_id,direction,party_type,party_name,amount,method,account_id,category,ref,note,src,created_by,created_at)
      VALUES (?, 'in', 'other', ?, ?, ?, ?, 'Shop / hotel rent', ?, ?, ?, ?, ?)`,
      t, p.unit, amount, method, account, ref ?? null, newNote, `rental:${p.rental_id}`, p.received_by ?? req.user!.name, p.created_at);
    if (account) run("INSERT INTO bank_txns (tenant_id,account_id,kind,amount,party,ref,note,txn_date,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
      t, account, "other_in", amount, p.unit, ref ?? null, newNote, p.created_at, p.received_by ?? req.user!.name, now());
    run(`UPDATE rental_payments SET for_month=?, amount=?, method=?, account_id=?, ref=?, note=?, voucher_id=? WHERE id=? AND tenant_id=?`,
      forMonth, amount, method, account, ref ?? null, userNote ?? null, vid, p.id, t);
  });
  audit(t, req.user!, "rent_edit", `rentpay:${p.id}`, { before, after: { amount, for_month: forMonth, method, note: userNote }, reason: b.reason });
  return { ok: true, amount };
}));

/* ---------------- 7. Stock delivery edit ---------------- */

/** CEO edit of a tanker delivery: reverse the purchase + stock, then re-book with the corrected litres / rate / supplier,
 *  so the supplier payable, the tank stock and the Fuel-purchases cost all follow. A delivery whose shortage claim is past
 *  'open' (being chased / recovered) is sent back to handle the claim first, and a bypass leftover edits from Bypass. */
corrections.post("/corrections/delivery/:id/edit", requirePerm("entries.edit"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({
    reason: z.string().trim().min(3, "Wajah likhein (kam se kam 3 harf)"),
    invoice_l: z.number().positive().max(200000).optional(), received_l: z.number().positive().max(200000).optional(),
    purchase_rate: z.number().positive().max(100000).optional(), tanker_no: z.string().max(40).nullable().optional(),
    supplier_id: z.number().int().positive().optional(), freight: z.number().min(0).nullable().optional(),
  }), req.body);
  const d = get(`SELECT d.*, tk.id tank_id2, tk.name tank, tk.product, tk.current_l, tk.capacity_l, tk.station_id FROM deliveries d
     JOIN tanks tk ON tk.id=d.tank_id JOIN stations st ON st.id=tk.station_id WHERE d.id=? AND st.tenant_id=?`, Number(req.params.id), t);
  if (!d) throw new AppError(404, "Delivery nahi mili");
  if (d.bypass_drop_id) throw new AppError(400, "Ye bypass ka bacha hua tel hai — Bypass page se badlein.");
  const claim = get("SELECT id, status FROM shortage_claims WHERE delivery_id=?", d.id);
  if (claim && claim.status !== "open") throw new AppError(400, `Is delivery ka shortage claim pehle hi '${claim.status}' hai — pehle claim handle karein.`);
  const supplier = b.supplier_id ? get("SELECT * FROM suppliers WHERE id=? AND tenant_id=?", b.supplier_id, t) : get("SELECT * FROM suppliers WHERE id=? AND tenant_id=?", d.supplier_id, t);
  if (!supplier) throw new AppError(400, "Supplier nahi mila");
  const invoice_l = b.invoice_l ?? d.invoice_l, received_l = b.received_l ?? d.received_l, rate = b.purchase_rate ?? d.purchase_rate;
  const tanker_no = b.tanker_no !== undefined ? b.tanker_no : d.tanker_no, freight = b.freight !== undefined ? b.freight : d.freight;
  // the tank must still hold the corrected litres once the old received litres are handed back
  const tankAfterReverse = round2((d.current_l ?? 0) - (d.received_l ?? 0));
  if (tankAfterReverse + received_l > d.capacity_l * 1.001) throw new AppError(400, `Tank capacity se zyada (${d.capacity_l}L)`);
  const shortage = round2(((invoice_l - received_l) / invoice_l) * 100);
  const before = { supplier: d.supplier, invoice_l: d.invoice_l, received_l: d.received_l, purchase_rate: d.purchase_rate, tanker_no: d.tanker_no };
  tx(() => {
    // reverse: drop the purchase leg(s), take the old received litres back out, drop a still-open claim
    for (const s of all("SELECT id FROM supplier_txns WHERE tenant_id=? AND delivery_id=?", t, d.id)) run("DELETE FROM supplier_txns WHERE id=? AND tenant_id=?", s.id, t);
    run("UPDATE tanks SET current_l = MAX(0, current_l - ?) WHERE id=?", d.received_l, d.tank_id);
    if (claim) run("DELETE FROM shortage_claims WHERE id=?", claim.id);
    // re-book with the corrected values
    run("UPDATE deliveries SET supplier=?, supplier_id=?, purchase_rate=?, tanker_no=?, invoice_l=?, received_l=?, shortage_pct=?, freight=? WHERE id=?",
      supplier.name, supplier.id, rate, tanker_no ?? null, invoice_l, received_l, shortage, freight ?? null, d.id);
    recordPurchase(t, { supplier_id: supplier.id, delivery_id: d.id, product: d.product, litres: invoice_l, rate, ref: tanker_no, by: req.user!.name, at: d.created_at });
    run("UPDATE tanks SET current_l = current_l + ? WHERE id=?", received_l, d.tank_id);
    if (shortage >= 0.3) claimForDelivery(t, d.id);
  });
  audit(t, req.user!, "delivery_edit", `delivery:${d.id}`, { before, after: { supplier: supplier.name, invoice_l, received_l, purchase_rate: rate, tanker_no }, reason: b.reason });
  return { ok: true, amount: round2(invoice_l * rate), stock_now_l: round2(tankAfterReverse + received_l) };
}));
