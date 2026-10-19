import { Router, type Request } from "express";
import { z } from "zod";
import { registerDecider, requestApproval, closeApproval } from "./approvals.js";
import { walletAfterFill } from "./prepaid.js";
import { askRating } from "./feedback.js";
import { claimForDelivery } from "./claims.js";
import { all, get, run, tx, now, getSetting, pkDate, pkDayStart, pkStart, pkEnd, METER, meterName, type Row } from "../db.js";
import { meterSales } from "./reports.js";
import { h, parse, tid, requirePerm, requireAny, scopedStation, can } from "../auth.js";
import { AppError, recordSale, undoSale, audit, UNDO_SECONDS, currentPrices, createAlert, round2, pkr, rateFmt, correctTo15, priceOf, type SaleInput } from "../services.js";
import { productSchema } from "../products.js";
import { moneyMethods, SPECIAL_METHODS } from "./lookups.js";
import { sendWhatsApp } from "../whatsapp/cloud.js";
import { PRODUCTS } from "../config.js";
import { recordPurchase } from "./suppliers.js";
import { followPumpPrice, priceSupply, insertTxn, clientDue } from "./wholesale.js";
import { bankAccountFor } from "./banks.js";
import { isCheque } from "./capture.js";
import { khataFillReceipt, wholesaleRateMessage, receiptUrl } from "../billing.js";
import { chargeShortage } from "./staff.js";
import { closeOrderOnDelivery, litresFromCm } from "./backoffice.js";
import { linkPhotos, photosFor, proofPhotos, proofCol } from "./capture.js";
import { settleShift, shiftReadings, shiftSummary, shiftReport, shiftFuels, shiftDipRecon } from "../shifts.js";
import { notify, staff, announce } from "../notifications.js";
import { product, ownStation, ownTank, pkDay, listText, optId } from "./operationsShared.js";

/** Retail sales / POS: record, edit, undo, late offline sales, khata card-pending holds. */
export const saleRoutes = Router();

/* ---------------- Sales / POS ---------------- */
saleRoutes.get("/sales", requirePerm("sales.view"), h((req) => {
  const limit = Math.min(500, Number(req.query.limit ?? 100));
  const own = scopedStation(req);
  return all(
    `SELECT s.*, c.name customer_name, st.name station_name FROM sales s JOIN stations st ON st.id=s.station_id
     LEFT JOIN customers c ON c.id=s.customer_id WHERE st.tenant_id=? ${own ? "AND s.station_id=" + Number(own) : ""} ORDER BY s.id DESC LIMIT ?`, tid(req), limit);
}));

/**
 * A POS sale made without internet that reaches the server after its shift was closed. At close the meters booked
 * every litre not on the POS as CASH, so:
 *  - a late cash sale is already in the books (nothing to add);
 *  - a late khata / card / online sale is moved out of that meter cash entry: the customer's khata (or the bank) is
 *    charged at the rate of the time, the shift report shows it, and the cash the salesman owed goes down by the same
 *    rupees. Stock and meters do not move (the fuel already left at close). Managers are told.
 */
function rebookLateSale(req: Request, shift: Row, s: Omit<SaleInput, "shift_id" | "created_by" | "created_at">, at: string): Row {
  const t = tid(req);
  if (s.payment_method === "cash") return { covered_by_meter: true, shift_id: shift.id, payment_method: "cash", product: s.product, litres: s.litres ?? null, amount: s.amount ?? null };
  const rate = priceOf(t, s.product, at);
  const litres = round2(s.litres ?? (s.amount ? s.amount / rate : 0));
  if (!(litres > 0)) throw new AppError(400, "Litres or amount required");
  // the same slip may already have been added on the close screen ("khata slips not entered yet")
  if (s.payment_method === "khata" && s.customer_id) {
    const dup = get("SELECT * FROM sales WHERE shift_id=? AND payment_method='khata' AND customer_id=? AND product=? AND at_close=1 AND ABS(litres-?)<0.01 ORDER BY id DESC LIMIT 1", shift.id, s.customer_id, s.product, litres);
    if (dup) return { ...dup, duplicate: true };
  }
  return tx(() => {
    const meter = get("SELECT * FROM sales WHERE shift_id=? AND product=? AND source='meter' AND created_by IS NULL ORDER BY litres DESC LIMIT 1", shift.id, s.product);
    if (!meter || meter.litres + 0.01 < litres)
      throw new AppError(409, `Shift #${shift.id} is already closed and its meter cash has ${meter ? round2(meter.litres) : 0} L of ${PRODUCTS[s.product] ?? s.product} left — this ${litres} L ${s.payment_method} sale cannot be moved. It may already be entered at close; tell the manager.`);
    const keepL = round2(meter.litres - litres);
    const keepA = keepL > 0.009 ? round2(keepL * meter.rate) : 0;
    const freed = round2(meter.amount - keepA);
    if (keepL > 0.009) run("UPDATE sales SET litres=?, amount=? WHERE id=?", keepL, keepA, meter.id);
    else run("DELETE FROM sales WHERE id=?", meter.id);
    // the fuel already left the tank at close: give the litres back first, recordSale takes them out again (net zero)
    const tank = get("SELECT id FROM tanks WHERE station_id=? AND product=? ORDER BY current_l DESC LIMIT 1", shift.station_id, s.product);
    if (tank) run("UPDATE tanks SET current_l = current_l + ? WHERE id=?", litres, tank.id);
    const saved = recordSale(t, { ...s, nozzle_id: null, litres, amount: undefined, shift_id: shift.id, created_by: req.user!.id, created_at: at, source: "pos", at_close: true });
    const expected = round2((shift.cash_expected ?? 0) - freed);
    run("UPDATE shifts SET cash_expected=?, variance=? WHERE id=?", expected, round2((shift.cash_actual ?? 0) - expected), shift.id);
    audit(t, req.user!, "late_sale_rebooked", `SALE-${saved.id}`, { shift_id: shift.id, product: s.product, litres, payment: s.payment_method, customer_id: s.customer_id ?? null, moved_from_meter_cash: freed, offline_at: at });
    createAlert(t, { station_id: shift.station_id, type: "late_sale", severity: "warning",
      title: `Late offline ${s.payment_method} sale — ${shift.attendant}`,
      body: `${litres} L ${PRODUCTS[s.product] ?? s.product} (${pkr(saved.amount)}) made without internet reached after shift #${shift.id} was closed. Moved out of the meter cash: cash expected ${pkr(shift.cash_expected ?? 0)} → ${pkr(expected)}.`, dedupe_key: `late-sale-${saved.id}` });
    return { ...saved, late_rebooked: true, moved_from_meter_cash: freed, cash_expected: expected };
  })!;
}

saleRoutes.post("/sales", requirePerm("sales.create"), h((req) => {
  const b = parse(z.object({
    station_id: z.number(), product, litres: z.number().positive().optional(), amount: z.number().positive().optional(),
    payment_method: z.string().max(40), // a money method (Settings → Lists) or khata / loyalty / coupon / wallet — checked below
    coupon_code: z.string().max(40).nullable().optional(),
    customer_id: z.number().nullable().optional(), nozzle_id: z.number().nullable().optional(), vehicle_no: z.string().max(40).nullable().optional(),
    override_limit: z.boolean().optional(),
    slip_no: z.string().max(40).nullable().optional(),
    photo_id: z.number().int().positive().nullable().optional(),
    /** which bank's POS machine a card / digital sale went to */
    account_id: z.number().int().positive().nullable().optional(),
    client_uid: z.string().min(8).max(64).nullable().optional(),
    /** khata "card pending": fuel out now at the meter rate, billed to the khata only when the card is brought in (at that day's rate) */
    pending: z.boolean().optional(),
    /** a fixed rupee discount: khata (lump-sum on a khata fill) or direct (walk-in on cash / card / online) */
    discount: z.number().min(0).optional(),
    discount_kind: z.enum(["khata", "direct"]).optional(),
    /** direct discount: who / why — free text; can also be added right after with POST /sales/:id/discount-ref */
    discount_ref: z.string().trim().max(120).optional().nullable(),
    /** when the sale was made on a tablet without internet; billed at the price in force then */
    offline_at: z.string().datetime({ offset: true }).nullable().optional(),
    /** a wholesale client fuelled from the nozzle: billed at their wholesale rate. payment_method is then the settlement
     * (khata = on credit, or cash / a money method / cheque = paid now). */
    wholesale_client_id: z.number().int().positive().nullable().optional(),
    cheque_bank: z.string().max(80).nullable().optional(),
    cheque_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).nullable().optional(),
  }), req.body);
  // ---- Wholesale client fuelled straight from the pump nozzle (billed at their wholesale rate) ----
  if (b.wholesale_client_id) {
    b.station_id = scopedStation(req, b.station_id)!;
    if (!(b.litres && b.litres > 0)) throw new AppError(400, "Enter litres for a wholesale fill (not amount)");
    const settle = b.payment_method; // "khata" = on credit; otherwise a money method / "cheque" = paid now
    const payNow = settle !== "khata";
    // rate comes from the client's own wholesale rate card; paid-now sales never add to the outstanding, so skip the limit
    const { c, rate, amount } = priceSupply(req, b.wholesale_client_id, { product: b.product, litres: b.litres }, 0, payNow);
    const salesman = req.user!.role === "salesman";
    const shift = salesman
      ? get("SELECT * FROM shifts WHERE station_id=? AND status='open' AND attendant=? ORDER BY id DESC LIMIT 1", b.station_id, req.user!.name)
      : get("SELECT * FROM shifts WHERE station_id=? AND status='open' ORDER BY id DESC LIMIT 1", b.station_id);
    if (salesman && !shift) throw new AppError(400, "Start your shift first (Shifts page) before recording sales");
    // the litres/stock/meter go on a sales row (method 'wholesale'); the money is a wholesale supply (tank_id NULL = not a 2nd stock-out)
    const saved = recordSale(tid(req), { station_id: b.station_id, product: b.product, litres: b.litres, payment_method: "wholesale",
      wholesale_client_id: c.id, rate, nozzle_id: b.nozzle_id ?? null, vehicle_no: b.vehicle_no ?? null, slip_no: b.slip_no ?? null,
      shift_id: shift?.id ?? null, created_by: req.user!.id, client_uid: b.client_uid ?? null });
    if (saved.duplicate) return saved;
    const supply = insertTxn(req, c.id, { type: "supply", station_id: b.station_id, tank_id: null, product: b.product, litres: b.litres, rate, amount,
      ref: `POS-${saved.id}`, note: `Pump nozzle sale (POS #${saved.id})${b.slip_no ? ` · slip ${b.slip_no}` : ""}`, vehicle_no: b.vehicle_no ?? null });
    run("UPDATE wholesale_txns SET sale_id=? WHERE id=?", saved.id, supply.id);
    let cheque_pending = false;
    if (payNow) {
      if (isCheque(settle) && !b.account_id) {
        // a cheque still in hand goes into the wholesale cheque register — the due comes down when it clears
        const bank = b.cheque_bank?.trim() || "Bank not given", no = b.slip_no?.trim() || "—";
        if (no !== "—" && get("SELECT id FROM wholesale_cheques WHERE tenant_id=? AND bank=? AND cheque_no=? AND status<>'returned'", tid(req), bank, no))
          throw new AppError(400, `Cheque ${no} of ${bank} is already in the register`);
        run(`INSERT INTO wholesale_cheques (tenant_id,client_id,amount,bank,cheque_no,cheque_date,status,note,created_by,created_at,updated_at,sale_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
          tid(req), c.id, amount, bank, no, b.cheque_date || pkDate(), "in_hand", `POS nozzle sale #${saved.id}`, req.user!.name, now(), now(), saved.id);
        cheque_pending = true;
      } else {
        const pay = insertTxn(req, c.id, { type: "payment", amount, method: settle, account_id: bankAccountFor(tid(req), b.account_id, settle),
          ref: `POS-${saved.id}`, note: `Pump nozzle sale #${saved.id} (paid ${settle})` });
        run("UPDATE wholesale_txns SET sale_id=? WHERE id=?", saved.id, pay.id);
      }
    }
    return { ...saved, wholesale: { client: c.name, rate, amount, settled: payNow && !cheque_pending, cheque_pending, due_after: clientDue(c.id) },
      receipt_url: receiptUrl(tid(req), "f", saved.id) };
  }
  // ---- normal retail sale ----
  if (!([...SPECIAL_METHODS] as string[]).includes(b.payment_method) && !moneyMethods(tid(req)).includes(b.payment_method))
    throw new AppError(400, `Unknown payment method: ${b.payment_method} — add it in Settings → Lists`);
  b.station_id = scopedStation(req, b.station_id)!;
  // a discount above the salesman's limit needs a manager / CEO (checked early, before the shift lookup)
  if (b.discount && b.discount > 0) {
    const direct = (b.discount_kind ?? (b.payment_method === "khata" ? "khata" : "direct")) === "direct";
    const max = Number(getSetting(tid(req), direct ? "direct_discount_max" : "khata_discount_max", direct ? "5000" : "500"));
    if (b.discount > max && !can(req.user, "customers.edit"))
      throw new AppError(403, `${direct ? "Direct discount" : "Discount"} above ${pkr(max)} needs the manager / CEO. Ask them to enter it.`);
  }
  const at = b.offline_at ? new Date(b.offline_at).toISOString() : null;
  if (at && (Date.parse(at) > Date.now() + 60_000 || Date.parse(at) < Date.now() - 48 * 3600_000))
    throw new AppError(400, "Offline sale time is not valid (must be within the last 48 hours)");
  if (b.client_uid) {
    const dup = get("SELECT * FROM sales WHERE client_uid=? AND station_id=?", b.client_uid, b.station_id);
    if (dup) return { ...dup, duplicate: true };
  }
  const salesman = req.user!.role === "salesman";
  // a salesman's sale goes on their own shift (the one open when an offline sale was made); others use the station's open shift
  const shift = salesman
    ? at
      ? get("SELECT * FROM shifts WHERE station_id=? AND attendant=? AND opened_at<=? ORDER BY id DESC LIMIT 1", b.station_id, req.user!.name, at)
      : get("SELECT * FROM shifts WHERE station_id=? AND status='open' AND attendant=? ORDER BY id DESC LIMIT 1", b.station_id, req.user!.name)
    : get("SELECT * FROM shifts WHERE station_id=? AND status='open' ORDER BY id DESC LIMIT 1", b.station_id);
  if (salesman && !shift) throw new AppError(400, "Start your shift first (Shifts page) before recording sales");
  if (shift && shift.status !== "open") {
    // a sale made on the tablet without internet that only uploads after the shift was closed: the meters already
    // settled those litres as cash, so move them to the right book instead of losing the entry
    if (!(salesman && at)) throw new AppError(409, "This shift is already closed; its litres were counted from the meter. Tell the manager about this sale.");
    if (b.account_id && !get("SELECT id FROM bank_accounts WHERE id=? AND tenant_id=? AND active=1", b.account_id, tid(req))) throw new AppError(400, "Bank account not found");
    const { offline_at: _o, ...sale } = b;
    const late = rebookLateSale(req, shift, sale, at);
    if (!late.duplicate && !late.covered_by_meter) setImmediate(() => { khataFillReceipt(tid(req), late).catch((e) => console.error("[khata receipt]", e.message)); });
    return late.covered_by_meter ? late : { ...late, receipt_url: receiptUrl(tid(req), "f", late.id) };
  }
  if (salesman && !at && get("SELECT id FROM notifications WHERE user_id=? AND type='price_change' AND acked_at IS NULL LIMIT 1", req.user!.id))
    throw new AppError(409, "Fuel price has changed. Update the dispenser and confirm the new price first.");
  if (b.override_limit && !can(req.user, "customers.edit")) throw new AppError(403, "Only a manager can allow more than the vehicle's daily limit");
  if (b.account_id && !get("SELECT id FROM bank_accounts WHERE id=? AND tenant_id=? AND active=1", b.account_id, tid(req))) throw new AppError(400, "Bank account not found");
  const { offline_at: _o, ...sale } = b;
  const saved = recordSale(tid(req), { ...sale, shift_id: shift?.id ?? null, created_by: req.user!.id, ...(at ? { created_at: at } : {}) });
  if (!saved.duplicate && saved.discount_kind === "direct") directDiscountWatch(tid(req), saved);
  if (!saved.duplicate) setImmediate(() => {
    khataFillReceipt(tid(req), saved).catch((e) => console.error("[khata receipt]", e.message));
    walletAfterFill(tid(req), saved).catch((e) => console.error("[wallet]", e.message));
    askRating(tid(req), saved).catch((e) => console.error("[rating]", e.message));
  });
  return { ...saved, receipt_url: receiptUrl(tid(req), "f", saved.id) };
}));

/** Direct discounts given in one shift add up past the owner's alert line → tell the managers once per shift. */
function directDiscountWatch(t: number, sale: Row) {
  if (!sale.shift_id) return;
  const limit = Number(getSetting(t, "direct_discount_alert", "10000"));
  const tot = get("SELECT COALESCE(SUM(discount),0) v, COUNT(*) n FROM sales WHERE shift_id=? AND discount_kind='direct'", sale.shift_id)!;
  if (tot.v < limit) return;
  const sh = get("SELECT attendant FROM shifts WHERE id=?", sale.shift_id);
  createAlert(t, { station_id: sale.station_id, type: "direct_discount", severity: "warning", title: `Direct discounts ${pkr(tot.v)} this shift — ${sh?.attendant ?? ""}`,
    body: `${tot.n} sales with a direct discount on shift #${sale.shift_id} (alert line ${pkr(limit)}). See Reports → Discounts & overrides.`, dedupe_key: `direct-disc-${sale.shift_id}` });
}

/** Direct discount: who / why — the salesman types it right after saving the sale; a manager may fix it later. */
saleRoutes.post("/sales/:id/discount-ref", requirePerm("sales.create"), h((req) => {
  const b = parse(z.object({ ref: z.string().trim().min(2, "Reference likhein — kis ko / kyun").max(120) }), req.body);
  const sale = get(`SELECT s.*, sh.status shift_status FROM sales s JOIN stations st ON st.id=s.station_id LEFT JOIN shifts sh ON sh.id=s.shift_id WHERE s.id=? AND st.tenant_id=?`, Number(req.params.id), tid(req));
  if (!sale) throw new AppError(404, "Sale not found");
  if (sale.discount_kind !== "direct") throw new AppError(400, "This sale has no direct discount");
  if (!can(req.user, "customers.edit") && (sale.created_by !== req.user!.id || sale.shift_status !== "open")) throw new AppError(403, "Only your own sales of the open shift. Ask the manager.");
  run("UPDATE sales SET discount_ref=? WHERE id=?", b.ref, sale.id);
  if (sale.discount_ref && sale.discount_ref !== b.ref) audit(tid(req), req.user!, "discount_ref_changed", `SALE-${sale.id}`, { from: sale.discount_ref, to: b.ref });
  return { ok: true, id: sale.id, discount_ref: b.ref };
}));

/** Attach the khata slip photo afterwards (the salesman took it after the rush): to the sale and its khata entry. */
saleRoutes.post("/sales/:id/slip-photo", requirePerm("sales.create"), h((req) => {
  const b = parse(z.object({ photo_id: z.number().int().positive() }), req.body);
  const sale = get(`SELECT s.*, sh.status shift_status FROM sales s JOIN stations st ON st.id=s.station_id LEFT JOIN shifts sh ON sh.id=s.shift_id
    WHERE s.id=? AND st.tenant_id=?`, Number(req.params.id), tid(req));
  if (!sale) throw new AppError(404, "Sale not found");
  if (!get("SELECT id FROM photos WHERE id=? AND tenant_id=?", b.photo_id, tid(req))) throw new AppError(404, "Photo not found");
  // a salesman only on their own sales of a shift that is still open; a manager any time
  if (!can(req.user, "shifts.view_all") && (sale.created_by !== req.user!.id || sale.shift_status !== "open"))
    throw new AppError(403, "Only your own sales of the open shift. Ask the manager to add the photo.");
  run("UPDATE sales SET photo_id=? WHERE id=?", b.photo_id, sale.id);
  const k = get("SELECT id FROM khata_ledger WHERE ref=? AND customer_id=?", `SALE-${sale.id}`, sale.customer_id);
  if (k) run("UPDATE photos SET ref=? WHERE id=? AND ref IS NULL", `khata:${k.id}`, b.photo_id);
  return { ok: true, photo_id: b.photo_id };
}));

/** Undo a sale entered by mistake: the salesman within 2 minutes, a manager any time while the shift is open. */
saleRoutes.post("/sales/:id/undo", requirePerm("sales.create"), h((req) => {
  const sale = get(`SELECT s.*, sh.status shift_status FROM sales s JOIN stations st ON st.id=s.station_id LEFT JOIN shifts sh ON sh.id=s.shift_id
    WHERE s.id=? AND st.tenant_id=?`, Number(req.params.id), tid(req));
  if (!sale) throw new AppError(404, "Sale not found");
  const salesman = req.user!.role === "salesman";
  if (salesman && sale.created_by !== req.user!.id) throw new AppError(403, "You can only undo your own sales");
  // a salesman may fix his own mistakes any time while his shift is still open; a sale not tied to an open shift keeps the 2-minute window
  if (salesman && !(sale.shift_id && sale.shift_status === "open") && Date.now() - Date.parse(sale.created_at) > UNDO_SECONDS * 1000) throw new AppError(400, "Undo time (2 minutes) has passed. Ask the manager.");
  if (sale.shift_id && sale.shift_status !== "open") throw new AppError(400, "The shift is closed; this sale can no longer be undone");
  if (!sale.created_by) throw new AppError(400, "Meter settlement entries cannot be undone");
  if (sale.shift_id && get("SELECT id FROM meter_readings WHERE shift_id=? AND checkpoint IS NOT NULL AND checkpoint_at > ? LIMIT 1", sale.shift_id, sale.created_at))
    throw new AppError(400, "Prices changed after this sale and the meters were settled; ask the manager to adjust instead");
  undoSale(sale);
  audit(tid(req), req.user!, "sale_undo", `SALE-${sale.id}`, { product: sale.product, litres: sale.litres, amount: sale.amount, payment: sale.payment_method, customer_id: sale.customer_id });
  return { ok: true, undone: sale.id, summary: sale.shift_id ? shiftSummary(sale.shift_id) : null };
}));

/**
 * Fix a mistake in a sale before the shift is closed. A salesman may correct his OWN sale any time while his
 * shift is open; a manager may correct any sale on an open shift. The fix is done safely as undo + re-record,
 * so stock, the nozzle meter, the khata and the ledger all stay in balance (tally never moves). The corrected
 * entry keeps the original time and who entered it, and the change is written to the audit log.
 * Special-money sales (coupon / wallet / loyalty / card-pending) can't be edited — undo them and enter afresh.
 */
const EDITABLE_METHODS = (t: number) => [...moneyMethods(t), "khata"];
saleRoutes.post("/sales/:id/edit", requirePerm("sales.create"), h((req) => {
  const b = parse(z.object({
    product: product.optional(), litres: z.number().positive().max(60000).optional(), amount: z.number().positive().max(100_000_000).optional(),
    payment_method: z.string().max(40).optional(), customer_id: z.number().int().positive().nullable().optional(),
    vehicle_no: z.string().max(40).nullable().optional(), slip_no: z.string().max(40).nullable().optional(),
    account_id: z.number().int().positive().nullable().optional(),
    discount: z.number().min(0).optional(), discount_kind: z.enum(["khata", "direct"]).optional(), discount_ref: z.string().trim().max(120).nullable().optional(),
  }), req.body);
  const t = tid(req);
  const sale = get(`SELECT s.*, sh.status shift_status FROM sales s JOIN stations st ON st.id=s.station_id LEFT JOIN shifts sh ON sh.id=s.shift_id
    WHERE s.id=? AND st.tenant_id=?`, Number(req.params.id), t);
  if (!sale) throw new AppError(404, "Sale not found");
  const salesman = req.user!.role === "salesman";
  if (salesman && sale.created_by !== req.user!.id) throw new AppError(403, "You can only fix your own sales");
  if (!sale.created_by) throw new AppError(400, "Meter settlement entries cannot be edited");
  if (!sale.shift_id || sale.shift_status !== "open") throw new AppError(400, "This sale's shift is closed; ask the manager to adjust instead");
  // special-money sales are too entangled (coupon value, wallet balance, loyalty points, a pending card hold) to edit in place
  const special = (m: string) => (["coupon", "wallet", "loyalty"] as string[]).includes(m) || (m === "khata" && sale.pending);
  if (special(sale.payment_method)) throw new AppError(400, `A ${sale.payment_method}${sale.pending ? " (card-pending)" : ""} sale can't be edited — undo it and enter it again`);
  if (get("SELECT id FROM meter_readings WHERE shift_id=? AND checkpoint IS NOT NULL AND checkpoint_at > ? LIMIT 1", sale.shift_id, sale.created_at))
    throw new AppError(400, "Prices changed after this sale and the meters were settled; ask the manager to adjust instead");
  // merged new values: anything not sent keeps the original
  const method = b.payment_method ?? sale.payment_method;
  if (!EDITABLE_METHODS(t).includes(method)) throw new AppError(400, `A sale can only be changed to cash / card / online / khata — not ${method}. For a coupon / wallet / loyalty sale, undo and enter afresh.`);
  const litres = b.litres ?? (b.amount ? undefined : sale.litres);
  const customer_id = b.customer_id !== undefined ? b.customer_id : sale.customer_id;
  if (method === "khata" && !customer_id) throw new AppError(400, "A khata sale needs a customer");
  const discount = b.discount ?? sale.discount ?? 0;
  if (discount > 0) {
    const direct = (b.discount_kind ?? sale.discount_kind ?? (method === "khata" ? "khata" : "direct")) === "direct";
    const max = Number(getSetting(t, direct ? "direct_discount_max" : "khata_discount_max", direct ? "5000" : "500"));
    if (discount > max && !can(req.user, "customers.edit")) throw new AppError(403, `${direct ? "Direct discount" : "Discount"} above ${pkr(max)} needs the manager / CEO.`);
  }
  if (account_idInvalid(t, b.account_id)) throw new AppError(400, "Bank account not found");
  const before = { product: sale.product, litres: sale.litres, amount: sale.amount, payment: sale.payment_method, customer_id: sale.customer_id, vehicle_no: sale.vehicle_no, slip_no: sale.slip_no };
  // undo + re-record in the same (single-threaded, synchronous) request: stock / meter / khata / ledger stay balanced
  undoSale(sale);
  const saved = recordSale(t, {
    station_id: sale.station_id, product: b.product ?? sale.product, litres, amount: b.amount, payment_method: method,
    customer_id: method === "khata" ? customer_id : null, nozzle_id: sale.nozzle_id, shift_id: sale.shift_id,
    vehicle_no: b.vehicle_no !== undefined ? b.vehicle_no : sale.vehicle_no, slip_no: b.slip_no !== undefined ? b.slip_no : sale.slip_no,
    account_id: b.account_id !== undefined ? b.account_id : sale.account_id, photo_id: sale.photo_id,
    created_at: sale.created_at, created_by: sale.created_by,
    ...(discount > 0 ? { discount, discount_kind: b.discount_kind ?? sale.discount_kind ?? (method === "khata" ? "khata" : "direct"), discount_ref: b.discount_ref !== undefined ? b.discount_ref : sale.discount_ref } : {}),
  });
  audit(t, req.user!, "sale_edit", `SALE-${sale.id}`, { before, after: { product: saved.product, litres: saved.litres, amount: saved.amount, payment: saved.payment_method, customer_id: saved.customer_id, vehicle_no: saved.vehicle_no, slip_no: saved.slip_no }, new_sale_id: saved.id });
  return { ok: true, edited: sale.id, sale: saved, summary: shiftSummary(sale.shift_id) };
}));
const account_idInvalid = (t: number, id?: number | null) => id != null && !get("SELECT id FROM bank_accounts WHERE id=? AND tenant_id=? AND active=1", id, t);

/* ---------------- Khata "card pending" holds ---------------- */
/**
 * Fuel given to a khata customer on trust: no rate is locked and no debt is raised until the card / parchi is
 * brought in (6–10 days later), when it is billed at that day's pump rate. These endpoints list what is still
 * waiting and clear one or many at once. Owner / manager / cashier only.
 */
saleRoutes.get("/sales/pending", requirePerm("khata.clear_pending"), h((req) => {
  const t = tid(req);
  const prices = currentPrices(t);
  const rows = all(`SELECT s.id, s.customer_id, s.product, s.litres, s.rate, s.amount, s.vehicle_no, s.slip_no, s.photo_id, s.station_id, s.created_at,
      c.name customer_name, c.phone customer_phone, st.name station_name
    FROM sales s JOIN stations st ON st.id=s.station_id LEFT JOIN customers c ON c.id=s.customer_id
    WHERE st.tenant_id=? AND s.pending=1 ORDER BY s.created_at`, t);
  return rows.map((r) => {
    const cur = prices[r.product]?.price ?? r.rate; // the rate it would be billed at if cleared today
    return { ...r, current_rate: cur, projected_amount: round2(r.litres * cur),
      days_pending: Math.floor((Date.now() - Date.parse(r.created_at)) / 86_400_000) };
  });
}));

saleRoutes.post("/sales/clear", requirePerm("khata.clear_pending"), h((req) => {
  const b = parse(z.object({
    ids: z.array(z.number().int().positive()).min(1).max(100),
    /** override rate applied to every selected slip; when omitted each is billed at the current pump price of its product */
    rate: z.number().positive().optional(),
    /** photo of the card / parchi the customer handed over (proof on the khata statement) */
    photo_id: z.number().int().positive().nullable().optional(),
  }), req.body);
  const t = tid(req);
  if (b.photo_id && !get("SELECT id FROM photos WHERE id=? AND tenant_id=?", b.photo_id, t)) throw new AppError(404, "Photo not found");
  const prices = currentPrices(t);
  const ts = now();
  const cleared: { id: number; customer_id: number; customer_name: string; product: string; litres: number; rate: number; amount: number }[] = [];
  tx(() => {
    for (const id of b.ids) {
      const s = get(`SELECT s.* FROM sales s JOIN stations st ON st.id=s.station_id WHERE s.id=? AND st.tenant_id=? AND s.pending=1`, id, t);
      if (!s) throw new AppError(404, `Pending hold #${id} not found — it may already be cleared`);
      if (!s.customer_id) throw new AppError(400, `Hold #${id} has no khata customer`);
      const rate = b.rate ?? prices[s.product]?.price;
      if (!(rate > 0)) throw new AppError(400, `No current price set for ${PRODUCTS[s.product] ?? s.product}`);
      const amount = round2(s.litres * rate);
      const c = get("SELECT * FROM customers WHERE id=?", s.customer_id)!;
      // the fuel is already out, so this must be billed — lock the clear-day rate on the sale and raise the khata debit
      run("UPDATE sales SET pending=0, clear_rate=?, cleared_at=? WHERE id=?", rate, ts, id);
      const k = run(`INSERT INTO khata_ledger (customer_id,type,amount,ref,note,product,litres,rate,vehicle_no,slip_no,station_id,created_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
        c.id, "debit", amount, `SALE-${id}`, `${round2(s.litres)}L ${s.product} @ Rs ${rate} (card pending from ${String(s.created_at).slice(0, 10)})`,
        s.product, round2(s.litres), rate, s.vehicle_no ?? null, s.slip_no ?? null, s.station_id, ts);
      run("UPDATE customers SET balance = balance + ?, loyalty_points = loyalty_points + ? WHERE id=?", amount, Math.floor(amount / 100), c.id);
      // the card / parchi photo rides the first slip's khata entry as proof
      if (b.photo_id) run("UPDATE photos SET ref=? WHERE id=? AND ref IS NULL", `khata:${k.id}`, b.photo_id);
      cleared.push({ id, customer_id: c.id, customer_name: c.name, product: s.product, litres: round2(s.litres), rate, amount });
    }
  });
  audit(t, req.user!, "sale_clear_pending", b.ids.map((i) => `SALE-${i}`).join(","), { cleared });
  return { ok: true, cleared, total: round2(cleared.reduce((a, x) => a + x.amount, 0)) };
}));

