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

/** Shifts: open, live view, handover, shift expenses, report, dip reconciliation, close. */
export const shiftRoutes = Router();

/* ---------------- Shifts ---------------- */
/** A PK calendar day (YYYY-MM-DD) in a list filter; "" means not set. */
const shiftsQuery = z.object({
  station_id: optId, attendant: listText, q: listText, from: pkDay, to: pkDay,
  status: z.enum(["open", "closed", "short"]).or(z.literal("")).optional(), limit: z.coerce.number().int().min(1).max(500).default(60),
});
/** Shift list, newest first. Filters: station, attendant / free text, PK day range (opened_at), status (short = closed with cash short), limit (max 500). */
shiftRoutes.get("/shifts", requirePerm("shifts.manage"), h((req) => {
  const f = parse(shiftsQuery, req.query);
  const where = ["st.tenant_id=?"]; const args: (string | number)[] = [tid(req)];
  if (req.user!.role === "salesman") { where.push("sh.station_id=?", "sh.attendant=?"); args.push(Number(scopedStation(req)), req.user!.name); }
  else if (f.station_id) { where.push("sh.station_id=?"); args.push(f.station_id); }
  if (f.attendant) { where.push("sh.attendant LIKE ?"); args.push(`%${f.attendant}%`); }
  if (f.q) { where.push("(sh.attendant LIKE ? OR st.name LIKE ? OR sh.id = ?)"); args.push(`%${f.q}%`, `%${f.q}%`, Number(f.q) || 0); }
  if (f.from) { where.push("sh.opened_at >= ?"); args.push(pkStart(f.from)); }
  if (f.to) { where.push("sh.opened_at < ?"); args.push(pkEnd(f.to)); }
  if (f.status === "open" || f.status === "closed") { where.push("sh.status=?"); args.push(f.status); }
  else if (f.status === "short") where.push("sh.status='closed' AND sh.variance < 0");
  return all(`SELECT sh.*, st.name station_name FROM shifts sh JOIN stations st ON st.id=sh.station_id WHERE ${where.join(" AND ")} ORDER BY sh.id DESC LIMIT ?`, ...args, f.limit)
    .map((s) => ({ ...s, readings: all(`SELECT r.*, ${METER} label, n.meter_no FROM meter_readings r JOIN nozzles n ON n.id=r.nozzle_id WHERE r.shift_id=? ORDER BY n.meter_no`, s.id) }));
}));

/** Nozzles that are not part of another open shift. */
const busyNozzles = (stationId: number) => new Set(all(
  `SELECT r.nozzle_id FROM meter_readings r JOIN shifts sh ON sh.id=r.shift_id WHERE sh.station_id=? AND sh.status='open'`, stationId).map((r) => r.nozzle_id));

/** Handover sheet for starting a shift: each nozzle's last closing reading and who handed it over. */
shiftRoutes.get("/shifts/handover", requirePerm("shifts.manage"), h((req) => {
  const stationId = req.user!.role === "salesman" ? scopedStation(req)! : Number(req.query.station_id) || get("SELECT id FROM stations WHERE tenant_id=? ORDER BY id LIMIT 1", tid(req))!.id;
  ownStation(tid(req), stationId);
  const busy = busyNozzles(stationId);
  return {
    station_id: stationId,
    nozzles: all("SELECT n.*, t.product, t.name tank FROM nozzles n JOIN tanks t ON t.id=n.tank_id WHERE n.station_id=? AND n.active=1 ORDER BY n.meter_no, n.id", stationId).map((n) => {
      const last = get(`SELECT sh.attendant, sh.closed_at, r.closing FROM meter_readings r JOIN shifts sh ON sh.id=r.shift_id
        WHERE r.nozzle_id=? AND sh.status='closed' AND r.closing IS NOT NULL ORDER BY sh.closed_at DESC LIMIT 1`, n.id);
      return { nozzle_id: n.id, meter_no: n.meter_no, code: n.label, label: meterName(n as any), product: n.product, tank: n.tank, tank_id: n.tank_id, last_reading: n.totalizer, handed_over_by: last?.attendant ?? null, handed_over_at: last?.closed_at ?? null, busy: busy.has(n.id) };
    }),
  };
}));

/**
 * Start a shift. The salesman writes the meter reading of each nozzle they will run (the previous shift's
 * closing reading is the starting point). If a meter moved between shifts, those litres left the tank
 * without a sale: stock is reduced and managers are alerted.
 */
shiftRoutes.post("/shifts/open", requirePerm("shifts.manage"), h(async (req) => {
  const dipRow = z.object({ tank_id: z.number().int(), measured_l: z.number().min(0).optional(), measured_cm: z.number().min(0).optional() }).refine((x) => x.measured_l !== undefined || x.measured_cm !== undefined, "Enter the dip in litres or cm");
  const b = parse(z.object({ station_id: z.number().optional(), attendant: z.string().min(2).optional(), readings: z.record(z.string(), z.number().min(0)).optional(), photo_ids: z.array(z.number()).max(20).optional(), dips: z.array(dipRow).max(20).optional() }), req.body);
  const isSalesman = req.user!.role === "salesman";
  const stationId = isSalesman ? scopedStation(req, b.station_id)! : b.station_id;
  const attendant = isSalesman ? req.user!.name : b.attendant;
  if (!stationId || !attendant) throw new AppError(400, "Station and attendant are required");
  const st = ownStation(tid(req), stationId);
  if (get("SELECT id FROM shifts WHERE station_id=? AND status='open' AND attendant=?", stationId, attendant)) throw new AppError(400, "This attendant already has an open shift");
  const busy = busyNozzles(stationId);
  const stationNozzles = all("SELECT n.*, t.product, t.id tank_id FROM nozzles n JOIN tanks t ON t.id=n.tank_id WHERE n.station_id=? AND n.active=1 ORDER BY n.meter_no", stationId)
    .map((n) => ({ ...n, label: meterName(n as any) }));
  const chosen = b.readings ? stationNozzles.filter((n) => String(n.id) in b.readings!) : stationNozzles.filter((n) => !busy.has(n.id));
  if (b.readings && chosen.length !== Object.keys(b.readings).length) throw new AppError(400, "Unknown nozzle in readings");
  for (const n of chosen) if (busy.has(n.id)) throw new AppError(400, `Nozzle ${n.label} is already running in another open shift`);
  if (!chosen.length) throw new AppError(400, "All nozzles at this station are already in use by open shifts");
  for (const n of chosen) {
    const r = b.readings?.[String(n.id)];
    if (r !== undefined && r < n.totalizer) throw new AppError(400, `Nozzle ${n.label}: reading ${r} is below the last closing reading ${n.totalizer}. Meters cannot go back — please check.`);
  }
  const gaps: { label: string; product: string; litres: number }[] = [];
  const shift = tx(() => {
    const { id } = run("INSERT INTO shifts (station_id,attendant,opened_at,status) VALUES (?,?,?, 'open')", stationId, attendant, now());
    for (const n of chosen) {
      const opening = b.readings?.[String(n.id)] ?? n.totalizer;
      const gap = round2(opening - n.totalizer);
      run("INSERT INTO meter_readings (shift_id,nozzle_id,opening,handover_prev,handover_gap) VALUES (?,?,?,?,?)", id, n.id, opening, n.totalizer, gap);
      if (gap > 0.01) {
        // fuel left the tank between shifts without being billed: keep book stock with the meters
        run("UPDATE tanks SET current_l = MAX(0, current_l - ?) WHERE id=?", gap, n.tank_id);
        run("UPDATE nozzles SET totalizer=? WHERE id=?", opening, n.id);
        gaps.push({ label: n.label, product: n.product, litres: gap });
      }
    }
    return get("SELECT * FROM shifts WHERE id=?", id)!;
  });
  if (gaps.length) {
    const body = `${gaps.map((g) => `${g.label} (${PRODUCTS[g.product]}): ${g.litres} L`).join(", ")} moved on the meter between shifts and were not billed. Started by ${attendant} at ${st.name}.`;
    const a = createAlert(tid(req), { station_id: stationId, type: "handover_gap", severity: "critical", title: `⚠️ Meter gap at shift handover — ${round2(gaps.reduce((x, g) => x + g.litres, 0))} L`, body, dedupe_key: `gap-${shift.id}` });
    if (a) await notify(tid(req), staff(tid(req), ["admin", "manager"]), { type: "handover_gap", data: { shift_id: shift.id }, title: a.title, body });
  }
  // opening dip per tank (optional) — the salesman's physical stock at shift start, for wet-stock reconciliation at close
  for (const d of b.dips ?? []) {
    const tk = get("SELECT t.id, t.station_id FROM tanks t WHERE t.id=? AND t.station_id=?", d.tank_id, stationId);
    if (!tk) throw new AppError(400, "Dip tank is not on this station");
    const litres = d.measured_cm !== undefined ? litresFromCm(d.tank_id, d.measured_cm) : d.measured_l!;
    run("INSERT INTO shift_dips (shift_id,tank_id,phase,measured_l,measured_cm,created_at) VALUES (?,?,?,?,?,?)", shift.id, d.tank_id, "open", round2(litres), d.measured_cm ?? null, now());
  }
  linkPhotos(tid(req), b.photo_ids, `shift-open:${shift.id}`);
  // attendance is NOT marked by opening a shift: it needs the salesman's own live selfie + location
  const att = get("SELECT id FROM users WHERE tenant_id=? AND name=? AND role='salesman' AND active=1", tid(req), shift.attendant);
  const notCheckedIn = Boolean(att && !get("SELECT id FROM attendance WHERE user_id=? AND day=?", att.id, pkDate()));
  return { ...shift, nozzles: chosen.length, handover_gaps: gaps, attendance_missing: notCheckedIn };
}));

/** Expenses paid from the shift's cash (tea, generator diesel, small repairs...). */
shiftRoutes.get("/shifts/expense-categories", requirePerm("shifts.expenses"), h((req) =>
  all("SELECT name FROM expense_categories WHERE tenant_id=? ORDER BY name", tid(req)).map((c) => c.name)));

shiftRoutes.post("/shifts/:id/expenses", requirePerm("shifts.expenses"), h(async (req) => {
  const shift = ownOpenShift(req, Number(req.params.id));
  if (shift.status !== "open") throw new AppError(400, "Shift is closed");
  const b = parse(z.object({ category: z.string().min(2), amount: z.number().positive().max(1_000_000), paid_to: z.string().max(80).optional().nullable(), note: z.string().max(200).optional().nullable(), photo_id: z.number().optional().nullable() }), req.body);
  if (!get("SELECT id FROM expense_categories WHERE tenant_id=? AND name=? AND active=1", tid(req), b.category)) throw new AppError(400, "Unknown expense category");
  const limit = Number(getSetting(tid(req), "expense_approval_limit", "10000"));
  const status = req.user!.role === "admin" || b.amount <= limit ? "approved" : "pending";
  const { id } = run(`INSERT INTO expenses (tenant_id,station_id,shift_id,category,amount,paid_to,method,note,status,created_by,approved_by,expense_date,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`, tid(req), shift.station_id, shift.id, b.category, b.amount, b.paid_to ?? null, "cash", b.note ?? null, status,
    req.user!.name, status === "approved" ? req.user!.name : null, new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10), now());
  if (status === "pending")
    createAlert(tid(req), { station_id: shift.station_id, type: "expense_approval", severity: "warning", title: `Shift expense needs approval: ${pkr(b.amount)} ${b.category}`,
      body: `Paid from ${shift.attendant}'s shift cash${b.note ? ` · ${b.note}` : ""}` });
  if (b.photo_id && linkPhotos(tid(req), [b.photo_id], `expense:${id}`)) { run("UPDATE expenses SET photo_id=? WHERE id=?", b.photo_id, id); }
  return { expense: get("SELECT * FROM expenses WHERE id=?", id), summary: shiftSummary(shift.id) };
}));

shiftRoutes.delete("/shifts/:id/expenses/:eid", requirePerm("shifts.expenses"), h((req) => {
  const shift = ownOpenShift(req, Number(req.params.id));
  if (shift.status !== "open") throw new AppError(400, "Shift is closed");
  run("DELETE FROM expenses WHERE id=? AND shift_id=?", Number(req.params.eid), shift.id);
  return { summary: shiftSummary(shift.id) };
}));

/** Full shift report: meters, litres at each rate, khata accounts, digital, expenses, cash to hand over. */
shiftRoutes.get("/shifts/:id/report", requirePerm("shifts.manage"), h((req) => {
  const shift = ownOpenShift(req, Number(req.params.id));
  return { ...shiftReport(shift.id), photos: photosFor(tid(req), [`shift-open:${shift.id}`, `shift-close:${shift.id}`]) };
}));

/**
 * Close a shift with closing totalizer readings and counted cash.
 * Litres on the meter not already recorded as sales are booked as cash sales, so stock follows the meters.
 * Expected cash = all cash sales in the shift; variance = counted - expected.
 */
function ownOpenShift(req: Request, id: number) {
  const shift = get("SELECT sh.*, s.name station_name FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE sh.id=? AND s.tenant_id=?", id, tid(req));
  if (!shift) throw new AppError(404, "Shift not found");
  if (req.user!.role === "salesman" && (shift.station_id !== scopedStation(req) || shift.attendant !== req.user!.name))
    throw new AppError(403, "You can only work on your own shift");
  return shift;
}

/** Live view of a shift: elapsed time, meter readings so far, sales so far, current prices. */
shiftRoutes.get("/shifts/:id/live", requirePerm("shifts.manage"), h((req) => {
  const shift = ownOpenShift(req, Number(req.params.id));
  return {
    shift, hours_open: round2((Date.parse(shift.closed_at ?? now()) - Date.parse(shift.opened_at)) / 3600_000),
    // the salesman's own duty length (12 or 24h) — the "shift over" banner uses it, falling back to the pump-wide default
    duty_hours: Number(get("SELECT duty_hours FROM users WHERE tenant_id=? AND role='salesman' AND name=? AND station_id=?", tid(req), shift.attendant, shift.station_id)?.duty_hours) || Number(getSetting(tid(req), "shift_hours", "24")) || 24,
    readings: shiftReadings(shift.id), summary: shiftSummary(shift.id),
    prices: Object.fromEntries(Object.entries(currentPrices(tid(req))).map(([k, v]) => [k, v.price])),
    // what was already entered on the POS this shift: the close screen starts from these, nothing is typed twice
    recorded: {
      online: Object.fromEntries(DIGITAL.map((m) => [m, round2(get("SELECT COALESCE(SUM(amount),0) v FROM sales WHERE shift_id=? AND payment_method=?", shift.id, m)!.v)])),
      khata: all(`SELECT s.id, s.product, s.litres, s.amount, s.vehicle_no, s.slip_no, s.photo_id, s.created_at, c.name customer_name FROM sales s
        JOIN customers c ON c.id=s.customer_id WHERE s.shift_id=? AND s.payment_method='khata' ORDER BY s.id`, shift.id),
    },
  };
}));

/**
 * Close a shift with closing meter readings and counted cash — built for a rush day:
 * the salesman only enters khata on the POS during the day; at the end he adds
 *   - the online money taken (card / JazzCash / Easypaisa / Raast totals, e.g. the card machine's settlement slip),
 *   - any khata slips he could not enter during the rush (slip no. and vehicle no. required),
 *   - litres put back in the tank after a nozzle test.
 * Everything else the meters show is cash at the rate of the time. Expected cash = cash sales − expenses from the bag;
 * variance = counted − expected. The preview runs exactly the same steps and then rolls them back, so what the
 * salesman sees before pressing "close" is what gets saved.
 */
const DIGITAL = ["card", "jazzcash", "easypaisa", "raast"] as const;
const ONLINE_NAME: Record<string, string> = { card: "Card machine", jazzcash: "JazzCash", easypaisa: "Easypaisa", raast: "Raast" };
const closeBody = z.object({
  readings: z.record(z.string(), z.number().min(0)),
  cash_actual: z.number().min(0).optional(),
  notes: z.string().max(500).optional(),
  photo_ids: z.array(z.number()).max(20).optional(),
  digital: z.object(Object.fromEntries(DIGITAL.map((m) => [m, z.number().min(0).max(100_000_000).optional()])) as Record<(typeof DIGITAL)[number], z.ZodOptional<z.ZodNumber>>).optional(),
  test: z.record(z.string(), z.number().min(0).max(5000)).optional(),
  khata: z.array(z.object({
    customer_id: z.number().int(), product: z.string(), litres: z.number().positive().max(60000).optional(), amount: z.number().positive().max(100_000_000).optional(),
    vehicle_no: z.string().trim().min(2, "Vehicle no. is needed on every khata slip").max(40), slip_no: z.string().trim().min(1, "Slip no. is needed on every khata slip").max(40),
    photo_id: z.number().int().positive().nullable().optional(),
  }).refine((k) => k.litres || k.amount, "Litres or amount on each khata slip")).max(200).optional(),
  cash_notes: z.record(z.string().regex(/^\d+$/), z.number().int().min(0).max(100_000)).optional(),
  // closing dip per tank (optional) — wet-stock reconciliation: dip replaces the tank's physical stock, meter sale is compared to it
  dips: z.array(z.object({ tank_id: z.number().int(), measured_l: z.number().min(0).optional(), measured_cm: z.number().min(0).optional() }).refine((x) => x.measured_l !== undefined || x.measured_cm !== undefined, "Enter the dip in litres or cm")).max(20).optional(),
});
type CloseInput = z.infer<typeof closeBody>;
class DryRun { constructor(public result: unknown) {} }

function closeShiftCore(req: Request, shift: Row, b: CloseInput, dryRun: boolean) {
  const t = tid(req);
  const prices = currentPrices(t);
  const rows = shiftReadings(shift.id);
  const testLimit = Number(getSetting(t, "test_limit_l", "10"));
  for (const r of rows) if (b.readings[String(r.nozzle_id)] === undefined) throw new AppError(400, `Enter the meter reading for nozzle ${r.label}`);
  // litres back to the tank: never more than the nozzle pumped; above the limit only a manager may close
  const backToTank: Record<string, number> = {};
  const tests: { id: number; label: string; litres: number }[] = [];
  for (const r of rows) {
    const l = round2(b.test?.[String(r.nozzle_id)] ?? 0);
    if (!l) continue;
    const pumped = b.readings[String(r.nozzle_id)] - (r.checkpoint ?? r.opening);
    if (l > pumped + 0.001) throw new AppError(400, `${r.label}: ${l} L test is more than the ${round2(pumped)} L this nozzle pumped`);
    if (l > testLimit && !can(req.user, "shifts.view_all")) throw new AppError(403, `${r.label}: ${l} L test / back-to-tank is more than ${testLimit} L — ask the manager to close this shift`);
    backToTank[r.product] = round2((backToTank[r.product] ?? 0) + l);
    tests.push({ id: r.id, label: r.label, litres: l });
  }
  const stamp = now();
  const run_ = () => {
    // 1) khata slips not entered during the rush
    for (const k of b.khata ?? []) {
      if (!rows.some((r) => r.product === k.product)) throw new AppError(400, `This shift has no ${PRODUCTS[k.product] ?? k.product} nozzle`);
      recordSale(t, { station_id: shift.station_id, product: k.product, litres: k.litres, amount: k.litres ? undefined : k.amount, payment_method: "khata", customer_id: k.customer_id,
        vehicle_no: k.vehicle_no, slip_no: k.slip_no, photo_id: k.photo_id ?? null, shift_id: shift.id, created_by: req.user!.id, created_at: stamp, source: "pos", at_close: true });
    }
    // 2) online money: shared over the fuels by the value still not entered, each at its own rate
    const remaining = [...new Set(rows.map((r) => r.product))].map((product) => {
      const noz = rows.filter((r) => r.product === product);
      const pumped = noz.reduce((a, r) => a + b.readings[String(r.nozzle_id)] - (r.checkpoint ?? r.opening), 0) - (backToTank[product] ?? 0);
      const cp = noz.find((r) => r.checkpoint_at)?.checkpoint_at;
      const recorded = get(`SELECT COALESCE(SUM(litres),0) l FROM sales WHERE shift_id=? AND product=? AND created_at ${cp ? ">" : ">="} ?`, shift.id, product, cp ?? shift.opened_at)!.l;
      const rate = prices[product]?.price ?? 0;
      return { product, litres: Math.max(0, round2(pumped - recorded)), rate, value: Math.max(0, round2((pumped - recorded) * rate)) };
    });
    const openValue = round2(remaining.reduce((a, r) => a + r.value, 0));
    // each online total is for the WHOLE shift (as on the card machine's slip); what the POS already has is taken off
    const extra: Record<string, number> = {};
    for (const m of DIGITAL) {
      if (b.digital?.[m] == null) { extra[m] = 0; continue; }
      const onPos = round2(get("SELECT COALESCE(SUM(amount),0) v FROM sales WHERE shift_id=? AND payment_method=?", shift.id, m)!.v);
      if (b.digital[m]! < onPos - 1) throw new AppError(400, `${ONLINE_NAME[m]}: ${pkr(onPos)} was already entered on the POS this shift — the shift total cannot be less (${pkr(b.digital[m]!)}).`);
      extra[m] = Math.max(0, round2(b.digital[m]! - onPos));
    }
    const digitalTotal = round2(DIGITAL.reduce((a, m) => a + extra[m], 0));
    if (digitalTotal > openValue + 1)
      throw new AppError(400, `Online money not on the POS (${pkr(digitalTotal)}) is more than the meter sale not yet entered (${pkr(openValue)}). Check the amounts, or that khata / card sales were not entered twice.`);
    for (const m of DIGITAL) {
      const amt = extra[m];
      if (!amt) continue;
      const parts = remaining.filter((r) => r.value > 0);
      let left = amt;
      parts.forEach((r, i) => {
        const share = i === parts.length - 1 ? left : round2((amt * r.value) / openValue);
        left = round2(left - share);
        if (share > 0) recordSale(t, { station_id: shift.station_id, product: r.product, amount: share, payment_method: m, shift_id: shift.id, created_by: req.user!.id, created_at: stamp, source: "pos", at_close: true });
      });
    }
    // 3) the rest of the meters is cash
    settleShift(t, shift, b.readings, () => undefined, backToTank);
    for (const r of shiftReadings(shift.id)) run("UPDATE meter_readings SET closing=?, test_l=? WHERE id=?", b.readings[String(r.nozzle_id)], tests.find((x) => x.id === r.id)?.litres ?? null, r.id);
    // closing dips (optional): the dip is the tank's real stock, so it replaces the book stock; the meter sale is reconciled against it
    const shiftTankIds = new Set(all(`SELECT DISTINCT n.tank_id id FROM meter_readings r JOIN nozzles n ON n.id=r.nozzle_id WHERE r.shift_id=?`, shift.id).map((x) => x.id));
    for (const d of b.dips ?? []) {
      if (!shiftTankIds.has(d.tank_id)) throw new AppError(400, "Closing dip is for a tank that is not on this shift");
      const tk = get("SELECT * FROM tanks WHERE id=?", d.tank_id)!;
      const measured = round2(d.measured_cm !== undefined ? litresFromCm(d.tank_id, d.measured_cm) : d.measured_l!);
      run("INSERT INTO shift_dips (shift_id,tank_id,phase,measured_l,measured_cm,created_at) VALUES (?,?,?,?,?,?)", shift.id, d.tank_id, "close", measured, d.measured_cm ?? null, stamp);
      run("INSERT INTO dip_readings (tank_id,measured_l,measured_cm,temperature,corrected_l,book_l,variance_pct,created_at) VALUES (?,?,?,?,?,?,?,?)", d.tank_id, measured, d.measured_cm ?? null, null, null, tk.current_l, tk.current_l ? round2(((measured - tk.current_l) / tk.current_l) * 100) : 0, stamp);
      run("UPDATE tanks SET current_l=? WHERE id=?", measured, d.tank_id);
    }
    // the full picture per fuel, from what is now saved
    const fuels = shiftFuels(shift.id, (p) => prices[p]?.price ?? null);
    const sold = all(`SELECT payment_method m, ROUND(SUM(amount),2) a FROM sales WHERE shift_id=? GROUP BY payment_method`, shift.id);
    const summary = shiftSummary(shift.id);
    return { fuels, summary, cash_expected: summary.cash_expected, dip_recon: shiftDipRecon(shift.id), digital_by_method: Object.fromEntries(DIGITAL.map((m) => [m, round2(sold.filter((x) => x.m === m).reduce((a, x) => a + x.a, 0))])) };
  };
  if (dryRun) {
    try { tx(() => { throw new DryRun(run_()); }); } catch (e) { if (e instanceof DryRun) return e.result as ReturnType<typeof run_>; throw e; }
  }
  return tx(() => {
    const r = run_();
    const totalLitres = shiftReadings(shift.id).reduce((a, x) => a + (x.closing - x.opening), 0);
    const counted = b.cash_actual ?? 0;
    run("UPDATE shifts SET status='closed', closed_at=?, litres=?, cash_expected=?, cash_actual=?, variance=?, notes=?, cash_notes=? WHERE id=?",
      now(), round2(totalLitres), r.cash_expected, counted, round2(counted - r.cash_expected), b.notes ?? null, b.cash_notes ? JSON.stringify(b.cash_notes) : null, shift.id);
    return r;
  })!;
}

/** Wet-stock reconciliation for a shift: per tank, meter sale vs dip-measured drop (for the shift report). */
shiftRoutes.get("/shifts/:id/dip-recon", requirePerm("shifts.manage"), h((req) => {
  const shift = get("SELECT sh.* FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE sh.id=? AND s.tenant_id=?", Number(req.params.id), tid(req));
  if (!shift) throw new AppError(404, "Shift not found");
  return { shift_id: shift.id, rows: shiftDipRecon(shift.id) };
}));

/** What closing with these readings would give — nothing is saved. */
shiftRoutes.post("/shifts/:id/preview", requirePerm("shifts.manage"), h((req) => {
  const shift = ownOpenShift(req, Number(req.params.id));
  if (shift.status !== "open") throw new AppError(400, "Shift is not open");
  return closeShiftCore(req, shift, parse(closeBody, req.body), true);
}));

shiftRoutes.post("/shifts/:id/close", requirePerm("shifts.manage"), h(async (req) => {
  const b = parse(closeBody.extend({ cash_actual: z.number().min(0) }), req.body);
  const t = tid(req);
  const shift = ownOpenShift(req, Number(req.params.id));
  if (shift.status !== "open") throw new AppError(400, "Shift is not open");
  const result = closeShiftCore(req, shift, b, false);
  linkPhotos(t, b.photo_ids, `shift-close:${shift.id}`);
  // wet-stock: dip vs meter — alert the manager/owner when a tank's variance is over the limit (litres)
  const dipAlertL = Number(getSetting(t, "sens_shift_dip_alert_l", "40"));
  const bigVar = (result.dip_recon ?? []).filter((d: any) => d.variance_l != null && Math.abs(d.variance_l) >= dipAlertL);
  if (bigVar.length) {
    const body = bigVar.map((d: any) => `${d.tank} (${PRODUCTS[d.product] ?? d.product}): meter ${Math.round(d.meter_l)}L vs dip ${Math.round(d.dip_sold_l)}L → ${d.variance_l > 0 ? "meter zyada" : "tank mein kami"} ${Math.round(Math.abs(d.variance_l))}L`).join(" · ");
    const a = createAlert(t, { station_id: shift.station_id, type: "dip_variance", severity: bigVar.some((d: any) => Math.abs(d.variance_l) >= dipAlertL * 3) ? "critical" : "warning",
      title: `🛢️ Dip vs meter farq — ${shift.attendant} (${shift.station_name})`, body, dedupe_key: `dipvar-${shift.id}` });
    if (a) await notify(t, staff(t, ["admin", "manager"], req.user!.id), { type: "dip_variance", data: { shift_id: shift.id }, title: a.title, body });
  }
  const closed = get("SELECT * FROM shifts WHERE id=?", shift.id)!;
  const summary = shiftSummary(shift.id);
  const v = closed.variance;
  if (v < -500)
    createAlert(t, { station_id: shift.station_id, type: "cash_short", severity: v < -5000 ? "critical" : "warning",
      title: `Cash short ${pkr(-v)} — ${shift.attendant}`, body: `Shift #${shift.id}: expected ${pkr(closed.cash_expected)}, counted ${pkr(b.cash_actual)}.`, dedupe_key: `shift-${shift.id}` });
  // shift report to managers/admin (WhatsApp only when cash is short)
  await notify(t, staff(t, ["admin", "manager"], req.user!.id), {
    type: "shift_closed", data: { shift_id: shift.id }, whatsapp: v < -500,
    title: `${v < -500 ? "⚠️" : "✅"} Shift closed — ${shift.attendant} (${shift.station_name})`,
    body: `${summary.by_product.map((p) => `${PRODUCTS[p.product]} ${Math.round(p.litres).toLocaleString()} L`).join(" · ")}\n` +
      `Sales ${pkr(summary.amount)} · Cash expected ${pkr(closed.cash_expected)} · Counted ${pkr(b.cash_actual)} · ${v < 0 ? "Short" : "Over"} ${pkr(Math.abs(v))}`,
  });
  await chargeShortage(t, shift, v);
  // check-out is NOT marked by closing the shift: it needs the salesman's own live selfie + location
  const att = get("SELECT id FROM users WHERE tenant_id=? AND name=? AND role='salesman'", t, shift.attendant);
  const notCheckedOut = Boolean(att && get("SELECT id FROM attendance WHERE user_id=? AND check_out IS NULL", att.id));
  return { ...closed, summary, fuels: result.fuels, readings: shiftReadings(shift.id), dip_recon: result.dip_recon, checkout_missing: notCheckedOut };
}));

