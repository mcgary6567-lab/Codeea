/**
 * Shift meter settlement.
 * Each nozzle on a shift has an opening reading and an optional checkpoint (set when the salesman
 * confirms a price change). Litres pumped since the last checkpoint that were not entered on the POS
 * are booked as cash sales at the rate valid for that segment, so a mid-shift price change is billed
 * correctly: before the change at the old rate, after it at the new rate.
 */
import { all, get, run, now, type Row } from "./db.js";
import { AppError, recordSale, round2 } from "./services.js";

export function shiftReadings(shiftId: number) {
  return all(
    `SELECT r.*, n.label, t.product, t.name tank FROM meter_readings r JOIN nozzles n ON n.id=r.nozzle_id JOIN tanks t ON t.id=n.tank_id
     WHERE r.shift_id=? ORDER BY n.id`, shiftId);
}

/**
 * Settle every nozzle of an open shift up to the given meter readings.
 * @param rateFor rate to bill unrecorded litres of this segment at (undefined = current price)
 * @returns litres settled per nozzle in this segment
 */
export function settleShift(tenantId: number, shift: Row, readings: Record<string, number>, rateFor: (product: string) => number | undefined) {
  const rows = shiftReadings(shift.id);
  const out: { product: string; litres: number; recorded_on_pos: number; unrecorded: number; rate: number | null }[] = [];
  // validate everything first so a bad reading changes nothing
  for (const r of rows) {
    const meter = readings[String(r.nozzle_id)];
    const start = r.checkpoint ?? r.opening;
    if (meter === undefined) throw new AppError(400, `Enter the meter reading for nozzle ${r.label}`);
    if (meter < start) throw new AppError(400, `Nozzle ${r.label}: reading ${meter} is below the previous reading ${start}`);
    if (meter - start > 60000) throw new AppError(400, `Nozzle ${r.label}: ${Math.round(meter - start)} L in one shift looks wrong — please re-check the reading`);
  }
  const ts = now();
  // Settle per product: POS sales may be entered with or without a nozzle, so compare the meters of all
  // nozzles of a product with all POS sales of that product in this segment.
  const products = [...new Set(rows.map((r) => r.product))];
  for (const product of products) {
    const noz = rows.filter((r) => r.product === product);
    const dispensed = noz.reduce((a, r) => a + readings[String(r.nozzle_id)] - (r.checkpoint ?? r.opening), 0);
    // The settlement sale of the previous checkpoint is stamped exactly at checkpoint_at, so a
    // checkpoint segment counts only sales strictly after it.
    const cp = noz.find((r) => r.checkpoint_at)?.checkpoint_at;
    const recorded = cp
      ? get("SELECT COALESCE(SUM(litres),0) l FROM sales WHERE shift_id=? AND product=? AND created_at > ?", shift.id, product, cp)!.l
      : get("SELECT COALESCE(SUM(litres),0) l FROM sales WHERE shift_id=? AND product=? AND created_at >= ?", shift.id, product, shift.opened_at)!.l;
    const unrecorded = round2(dispensed - recorded);
    if (unrecorded > 0.01)
      recordSale(tenantId, { station_id: shift.station_id, product, litres: unrecorded, payment_method: "cash", nozzle_id: noz[0].nozzle_id, shift_id: shift.id, rate: rateFor(product), created_at: ts });
    for (const r of noz) {
      const meter = readings[String(r.nozzle_id)];
      run("UPDATE meter_readings SET checkpoint=?, checkpoint_at=? WHERE id=?", meter, ts, r.id);
      run("UPDATE nozzles SET totalizer=? WHERE id=?", meter, r.nozzle_id);
    }
    out.push({ product, litres: round2(dispensed), recorded_on_pos: round2(recorded), unrecorded: Math.max(0, unrecorded), rate: rateFor(product) ?? null });
  }
  return out;
}

/** Totals for a shift: litres and amount by product, money by payment method, cash expected. */
export function shiftSummary(shiftId: number) {
  const byProduct = all(`SELECT product, ROUND(SUM(litres),2) litres, ROUND(SUM(amount),2) amount, COUNT(*) txns FROM sales WHERE shift_id=? GROUP BY product`, shiftId);
  const byPayment = all(`SELECT payment_method method, ROUND(SUM(amount),2) amount, COUNT(*) txns FROM sales WHERE shift_id=? GROUP BY payment_method ORDER BY amount DESC`, shiftId);
  const cash = byPayment.find((p) => p.method === "cash")?.amount ?? 0;
  return {
    by_product: byProduct, by_payment: byPayment,
    litres: round2(byProduct.reduce((a, p) => a + p.litres, 0)), amount: round2(byProduct.reduce((a, p) => a + p.amount, 0)), cash_expected: round2(cash),
  };
}
