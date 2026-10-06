/**
 * Shift meter settlement.
 * Each nozzle on a shift has an opening reading and an optional checkpoint (set when the salesman
 * confirms a price change). Litres pumped since the last checkpoint that were not entered on the POS
 * are booked as cash sales at the rate valid for that segment, so a mid-shift price change is billed
 * correctly: before the change at the old rate, after it at the new rate.
 */
import { all, get, run, now, METER, type Row } from "./db.js";
import { AppError, recordSale, round2 } from "./services.js";

export function shiftReadings(shiftId: number) {
  return all(
    `SELECT r.*, ${METER} label, n.meter_no, t.product, t.name tank FROM meter_readings r JOIN nozzles n ON n.id=r.nozzle_id JOIN tanks t ON t.id=n.tank_id
     WHERE r.shift_id=? ORDER BY n.meter_no, n.id`, shiftId);
}

/**
 * Settle every nozzle of an open shift up to the given meter readings.
 * @param rateFor rate to bill unrecorded litres of this segment at (undefined = current price)
 * @returns litres settled per nozzle in this segment
 */
export function settleShift(tenantId: number, shift: Row, readings: Record<string, number>, rateFor: (product: string) => number | undefined,
  /** litres per product that went back into the tank (nozzle test / calibration): pumped but not sold */
  backToTank: Record<string, number> = {}) {
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
    const dispensed = noz.reduce((a, r) => a + readings[String(r.nozzle_id)] - (r.checkpoint ?? r.opening), 0) - (backToTank[product] ?? 0);
    // The settlement sale of the previous checkpoint is stamped exactly at checkpoint_at, so a
    // checkpoint segment counts only sales strictly after it.
    const cp = noz.find((r) => r.checkpoint_at)?.checkpoint_at;
    const recorded = cp
      ? get("SELECT COALESCE(SUM(litres),0) l FROM sales WHERE shift_id=? AND product=? AND created_at > ?", shift.id, product, cp)!.l
      : get("SELECT COALESCE(SUM(litres),0) l FROM sales WHERE shift_id=? AND product=? AND created_at >= ?", shift.id, product, shift.opened_at)!.l;
    const unrecorded = round2(dispensed - recorded);
    if (unrecorded > 0.01)
      recordSale(tenantId, { station_id: shift.station_id, product, litres: unrecorded, payment_method: "cash", nozzle_id: noz[0].nozzle_id, shift_id: shift.id, rate: rateFor(product), created_at: ts, source: "meter" });
    for (const r of noz) {
      const meter = readings[String(r.nozzle_id)];
      run("UPDATE meter_readings SET checkpoint=?, checkpoint_at=? WHERE id=?", meter, ts, r.id);
      run("UPDATE nozzles SET totalizer=? WHERE id=?", meter, r.nozzle_id);
    }
    out.push({ product, litres: round2(dispensed), recorded_on_pos: round2(recorded), unrecorded: Math.max(0, unrecorded), rate: rateFor(product) ?? null });
  }
  return out;
}

/**
 * Totals for a shift: litres and amount by product, money by payment method, khata accounts,
 * expenses paid from the shift's cash, and the cash the salesman must hand over.
 */
export function shiftSummary(shiftId: number) {
  const byProduct = all(`SELECT product, ROUND(SUM(litres),2) litres, ROUND(SUM(amount),2) amount, COUNT(*) txns FROM sales WHERE shift_id=? GROUP BY product`, shiftId);
  const byPayment = all(`SELECT payment_method method, ROUND(SUM(amount),2) amount, ROUND(SUM(litres),2) litres, COUNT(*) txns FROM sales WHERE shift_id=? GROUP BY payment_method ORDER BY amount DESC`, shiftId);
  const expenses = all(`SELECT id, category, amount, paid_to, note, status, created_by, created_at FROM expenses WHERE shift_id=? AND status<>'rejected' ORDER BY id`, shiftId);
  const sum = (m: string[]) => round2(byPayment.filter((p) => m.includes(p.method)).reduce((a, p) => a + p.amount, 0));
  // shop (lubricants / tuck shop) sales on the same shift: their cash goes in the same bag
  const shopBy = all("SELECT payment_method method, ROUND(SUM(total),2) amount, COUNT(*) n FROM shop_sales WHERE shift_id=? GROUP BY payment_method", shiftId);
  const shopSum = (m: string[]) => round2(shopBy.filter((p) => m.includes(p.method)).reduce((a, p) => a + p.amount, 0));
  const shop = { total: shopSum(["cash", "easypaisa", "jazzcash", "raast", "card", "khata"]), cash: shopSum(["cash"]), digital: shopSum(["easypaisa", "jazzcash", "raast", "card"]), khata: shopSum(["khata"]), sales: shopBy.reduce((a, p) => a + p.n, 0) };
  const cashSales = round2(sum(["cash"]) + shop.cash);
  const expensesTotal = round2(expenses.reduce((a, e) => a + e.amount, 0));
  return {
    by_product: byProduct, by_payment: byPayment,
    litres: round2(byProduct.reduce((a, p) => a + p.litres, 0)), amount: round2(byProduct.reduce((a, p) => a + p.amount, 0)),
    cash_sales: cashSales, digital: sum(["easypaisa", "jazzcash", "raast", "card"]), khata: sum(["khata"]), points: sum(["loyalty"]), prepaid: sum(["coupon", "wallet"]), shop,
    expenses, expenses_total: expensesTotal,
    // what must be in the cash bag at the end: cash sales minus expenses paid from that cash
    cash_expected: round2(cashSales - expensesTotal),
  };
}

/** Everything about one shift, for the shift report / receipt. */
const DIGITAL_METHODS = ["card", "jazzcash", "easypaisa", "raast"];
const OTHER_METHODS = ["coupon", "wallet", "loyalty"];
/**
 * Per fuel, how the meters were settled: litres pumped − put back in the tank − khata − online − coupons/wallet = cash.
 * Reads the saved closing readings, so it works for the close preview (inside its rolled-back transaction) and the report.
 */
export function shiftFuels(shiftId: number, rateOf: (product: string) => number | null = () => null) {
  const rows = shiftReadings(shiftId);
  const sold = all(`SELECT product, payment_method m, ROUND(SUM(litres),2) l, ROUND(SUM(amount),2) a FROM sales WHERE shift_id=? GROUP BY product, payment_method`, shiftId);
  return [...new Set(rows.map((r) => r.product as string))].map((product) => {
    const by = (f: (m: string) => boolean) => sold.filter((x) => x.product === product && f(x.m));
    const L = (f: (m: string) => boolean) => round2(by(f).reduce((a, x) => a + x.l, 0)), A = (f: (m: string) => boolean) => round2(by(f).reduce((a, x) => a + x.a, 0));
    const mine = rows.filter((r) => r.product === product);
    return {
      product,
      meter_l: round2(mine.reduce((a, r) => a + (r.closing != null ? r.closing - r.opening : 0), 0)),
      test_l: round2(mine.reduce((a, r) => a + (r.test_l ?? 0), 0)),
      khata_l: L((m) => m === "khata"), khata: A((m) => m === "khata"),
      digital_l: L((m) => DIGITAL_METHODS.includes(m)), digital: A((m) => DIGITAL_METHODS.includes(m)),
      other_l: L((m) => OTHER_METHODS.includes(m)), other: A((m) => OTHER_METHODS.includes(m)),
      cash_l: L((m) => m === "cash"), cash: A((m) => m === "cash"), rate: rateOf(product),
    };
  });
}

export function shiftReport(shiftId: number) {
  const shift = get("SELECT sh.*, s.name station_name FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE sh.id=?", shiftId)!;
  // money per meter: its litres at the average rate this shift sold that fuel at
  const rate = Object.fromEntries(all("SELECT product, SUM(amount)/SUM(litres) r FROM sales WHERE shift_id=? AND litres > 0 GROUP BY product", shiftId).map((x) => [x.product, x.r]));
  const readings = shiftReadings(shiftId).map((r) => {
    const litres = r.closing != null ? round2(r.closing - r.opening) : null;
    return { ...r, litres, amount: litres != null && rate[r.product] ? Math.round(litres * rate[r.product]) : null };
  });
  return {
    shift, readings, summary: shiftSummary(shiftId),
    fuels: shift.status === "closed" ? shiftFuels(shiftId, (p) => (rate[p] ? round2(rate[p]) : null)) : [],
    // litres and amount at each rate (two lines if the price changed during the shift)
    by_rate: all(`SELECT product, rate, ROUND(SUM(litres),2) litres, ROUND(SUM(amount),2) amount FROM sales WHERE shift_id=? GROUP BY product, rate ORDER BY product, rate`, shiftId),
    khata: all(`SELECT c.id, c.name, c.type, ROUND(SUM(s.litres),2) litres, ROUND(SUM(s.amount),2) amount, COUNT(*) slips,
        GROUP_CONCAT(COALESCE(s.slip_no, ''), ', ') slip_nos FROM sales s JOIN customers c ON c.id=s.customer_id
      WHERE s.shift_id=? AND s.payment_method='khata' GROUP BY c.id ORDER BY amount DESC`, shiftId)
      .map((k) => ({ ...k, slip_nos: String(k.slip_nos ?? "").split(", ").filter(Boolean) })),
    handover_gaps: readings.filter((r) => (r.handover_gap ?? 0) > 0.01).map((r) => ({ label: r.label, product: r.product, litres: r.handover_gap, previous: r.handover_prev, opening: r.opening })),
  };
}
