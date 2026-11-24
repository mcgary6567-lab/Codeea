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

/** Stock: dip readings and tanker deliveries. */
export const stockRoutes = Router();

/* ---------------- Stock: dips & deliveries ---------------- */
const stockQuery = z.object({ limit: z.coerce.number().int().min(1).max(500).default(50), from: pkDay, to: pkDay, tank_id: optId, q: listText });
/** Dip readings and tanker deliveries, newest first. Filters: tank, PK day range, free text (supplier / tanker no / tank / product / station), limit (default 50, max 500). */
stockRoutes.get("/stock", requirePerm("stock.manage"), h((req) => {
  const f = parse(stockQuery, req.query);
  const where = ["s.tenant_id=?"]; const args: (string | number)[] = [tid(req)];
  if (f.tank_id) { where.push("d.tank_id=?"); args.push(f.tank_id); }
  if (f.from) { where.push("d.created_at >= ?"); args.push(pkStart(f.from)); }
  if (f.to) { where.push("d.created_at < ?"); args.push(pkEnd(f.to)); }
  const like = f.q ? `%${f.q}%` : null;
  const common = ["t.name LIKE ?", "t.product LIKE ?", "s.name LIKE ?"];
  const textWhere = (extra: string[]) => (like ? [`(${[...common, ...extra].join(" OR ")})`] : []);
  const textArgs = (n: number) => (like ? Array(common.length + n).fill(like) : []);
  return {
    limit: f.limit,
    dips: all(`SELECT d.*, ${proofCol("'dip:'||d.id")}, t.name tank, t.product, s.name station FROM dip_readings d JOIN tanks t ON t.id=d.tank_id JOIN stations s ON s.id=t.station_id
      WHERE ${[...where, ...textWhere([])].join(" AND ")} ORDER BY d.id DESC LIMIT ?`, ...args, ...textArgs(0), f.limit),
    deliveries: all(`SELECT d.*, t.name tank, t.product, s.name station FROM deliveries d JOIN tanks t ON t.id=d.tank_id JOIN stations s ON s.id=t.station_id
      WHERE ${[...where, ...textWhere(["d.supplier LIKE ?", "d.tanker_no LIKE ?"])].join(" AND ")} ORDER BY d.id DESC LIMIT ?`, ...args, ...textArgs(2), f.limit),
  };
}));

stockRoutes.post("/stock/dip", requirePerm("stock.manage"), h((req) => {
  const b = parse(z.object({ tank_id: z.number(), measured_l: z.number().min(0).optional(), measured_cm: z.number().min(0).optional(), temperature: z.number().min(-10).max(70).optional(), photo_ids: proofPhotos, confirm: z.boolean().optional() })
    .refine((x) => x.measured_l !== undefined || x.measured_cm !== undefined, "Enter the dip in cm or litres"), req.body);
  const t = ownTank(tid(req), b.tank_id);
  // a dip in cm is turned into litres with the tank's dip chart
  const measured = b.measured_cm !== undefined ? litresFromCm(t.id, b.measured_cm) : b.measured_l!;
  // volume corrected to 15°C if a temperature was taken (fuel expands in heat); informational, book stock stays the measured litres
  const corrected = b.temperature !== undefined ? correctTo15(measured, t.product, b.temperature) : null;
  const variance = t.current_l ? ((measured - t.current_l) / t.current_l) * 100 : 0;
  // the dip replaces the book stock, so a slip of the finger (15 typed as 150) must not go through unasked
  if (Math.abs(variance) >= 5 && !b.confirm)
    throw new AppError(409, `Dip ${Math.round(measured).toLocaleString("en-IN")} L is ${variance.toFixed(1)}% from the book stock ${Math.round(t.current_l).toLocaleString("en-IN")} L. Check the reading, then confirm · ڈپ دوبارہ چیک کریں`);
  return tx(() => {
    const { id } = run("INSERT INTO dip_readings (tank_id,measured_l,measured_cm,temperature,corrected_l,book_l,variance_pct,created_at) VALUES (?,?,?,?,?,?,?,?)", t.id, measured, b.measured_cm ?? null, b.temperature ?? null, corrected, t.current_l, round2(variance), now());
    run("UPDATE tanks SET current_l=? WHERE id=?", measured, t.id);
    linkPhotos(tid(req), b.photo_ids, `dip:${id}`); // dip-stick photo
    if (Math.abs(variance) >= 0.5)
      createAlert(tid(req), { station_id: t.station_id, type: "stock_variance", severity: Math.abs(variance) >= 1 ? "critical" : "warning",
        title: `${t.name}: stock variance ${variance.toFixed(2)}%`, body: `Dip ${Math.round(measured)}L${b.measured_cm !== undefined ? ` (${b.measured_cm} cm)` : ""} vs book ${Math.round(t.current_l)}L.${corrected != null ? ` At 15°C ≈ ${Math.round(corrected)}L (temp ${b.temperature}°C — fuel ${corrected < measured ? "phaila hua" : "sukra hua"}).` : ""}`, dedupe_key: `dip-${id}` });
    return get("SELECT * FROM dip_readings WHERE id=?", id);
  });
}));

stockRoutes.post("/stock/delivery", requirePerm("stock.manage"), h((req) => {
  const b = parse(z.object({
    tank_id: z.number(), invoice_l: z.number().positive(), received_l: z.number().positive(), tanker_no: z.string().optional(),
    // every tanker comes from a supplier at a price: that is what puts its cost in the books (bill, payable, ledger, profit)
    supplier_id: z.number({ required_error: "Choose the supplier (depot) · سپلائر منتخب کریں", invalid_type_error: "Choose the supplier (depot) · سپلائر منتخب کریں" }),
    purchase_rate: z.number({ required_error: "Enter the purchase rate per litre from the supplier invoice · ریٹ لکھیں", invalid_type_error: "Enter the purchase rate per litre from the supplier invoice · ریٹ لکھیں" }).positive("Enter the purchase rate per litre from the supplier invoice"),
    photo_id: z.number().optional().nullable(),
    freight: z.number().min(0).optional().nullable(),
  }), req.body);
  const t = ownTank(tid(req), b.tank_id);
  const supplier = get("SELECT * FROM suppliers WHERE id=? AND tenant_id=?", b.supplier_id, tid(req));
  if (!supplier) throw new AppError(400, "Supplier not found");
  if (t.current_l + b.received_l > t.capacity_l * 1.001) throw new AppError(400, `Exceeds tank capacity (${t.capacity_l}L)`);
  const shortage = ((b.invoice_l - b.received_l) / b.invoice_l) * 100;
  return tx(() => {
    const { id } = run("INSERT INTO deliveries (tank_id,supplier,supplier_id,purchase_rate,tanker_no,invoice_l,received_l,shortage_pct,freight,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
      t.id, supplier.name, supplier.id, b.purchase_rate, b.tanker_no ?? null, b.invoice_l, b.received_l, round2(shortage), b.freight ?? null, now());
    // we pay the supplier for the invoiced litres; any shortage is claimed separately
    recordPurchase(tid(req), { supplier_id: supplier.id, delivery_id: id, product: t.product, litres: b.invoice_l, rate: b.purchase_rate, ref: b.tanker_no, by: req.user!.name });
    run("UPDATE tanks SET current_l = current_l + ? WHERE id=?", b.received_l, t.id);
    closeOrderOnDelivery(tid(req), supplier.id, t.id, id);
    if (b.photo_id && linkPhotos(tid(req), [b.photo_id], `delivery:${id}`)) { run("UPDATE deliveries SET photo_id=? WHERE id=?", b.photo_id, id); }
    const claim = claimForDelivery(tid(req), id);
    if (shortage >= 0.3)
      createAlert(tid(req), { station_id: t.station_id, type: "short_delivery", severity: shortage >= 0.8 ? "critical" : "warning",
        title: `Tanker ${b.tanker_no ?? ""} short by ${shortage.toFixed(2)}%`, body: `${t.name}: invoice ${b.invoice_l}L, received ${b.received_l}L.${claim ? " A shortage claim was opened (Suppliers → Claims)." : ""}`, dedupe_key: `delivery-${id}` });
    return { ...get("SELECT * FROM deliveries WHERE id=?", id)!, claim_id: claim };
  });
}));
