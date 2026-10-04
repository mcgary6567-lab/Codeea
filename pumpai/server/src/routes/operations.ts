import { Router, type Request } from "express";
import { z } from "zod";
import { all, get, run, tx, now } from "../db.js";
import { h, parse, tid, requirePerm, requireAny, scopedStation } from "../auth.js";
import { AppError, recordSale, currentPrices, createAlert, round2, pkr, rateFmt } from "../services.js";
import { sendWhatsApp } from "../whatsapp/cloud.js";
import { PRODUCTS } from "../config.js";
import { recordPurchase } from "./suppliers.js";
import { settleShift, shiftReadings, shiftSummary } from "../shifts.js";
import { notify, staff } from "../notifications.js";

export const operations = Router();
const product = z.enum(["PMG", "HOBC", "HSD"]);

function ownStation(tenantId: number, stationId: number) {
  const s = get("SELECT * FROM stations WHERE id=? AND tenant_id=?", stationId, tenantId);
  if (!s) throw new AppError(404, "Station not found");
  return s;
}
function ownTank(tenantId: number, tankId: number) {
  const t = get("SELECT t.* FROM tanks t JOIN stations s ON s.id=t.station_id WHERE t.id=? AND s.tenant_id=?", tankId, tenantId);
  if (!t) throw new AppError(404, "Tank not found");
  return t;
}

operations.get("/stations", requireAny("sales.view", "stock.manage", "wholesale.view", "users.manage"), h((req) => {
  const own = req.user!.role === "salesman" ? scopedStation(req) : null;
  return all(`SELECT * FROM stations WHERE tenant_id=? ${own ? "AND id=" + Number(own) : ""} ORDER BY id`, tid(req)).map((s) => ({
  ...s,
  tanks: all("SELECT * FROM tanks WHERE station_id=? ORDER BY id", s.id),
  nozzles: all("SELECT n.*, t.product FROM nozzles n JOIN tanks t ON t.id=n.tank_id WHERE n.station_id=? ORDER BY n.id", s.id),
}));
}));

operations.post("/stations", requirePerm("stations.manage"), h((req) => {
  const b = parse(z.object({ name: z.string().min(2), city: z.string().optional(), address: z.string().optional(), omc: z.string().optional(), timings: z.string().optional(), services: z.string().optional() }), req.body);
  return get("SELECT * FROM stations WHERE id=?", run("INSERT INTO stations (tenant_id,name,city,address,omc,timings,services) VALUES (?,?,?,?,?,?,?)",
    tid(req), b.name, b.city ?? null, b.address ?? null, b.omc ?? null, b.timings ?? "24 hours", b.services ?? null).id);
}));

operations.post("/tanks", requirePerm("stock.manage"), h((req) => {
  const b = parse(z.object({ station_id: z.number(), name: z.string(), product, capacity_l: z.number().positive(), current_l: z.number().min(0), reorder_pct: z.number().min(5).max(80).default(25), nozzles: z.number().int().min(0).max(12).default(2) }), req.body);
  ownStation(tid(req), b.station_id);
  return tx(() => {
    const { id } = run("INSERT INTO tanks (station_id,name,product,capacity_l,current_l,reorder_pct) VALUES (?,?,?,?,?,?)", b.station_id, b.name, b.product, b.capacity_l, b.current_l, b.reorder_pct);
    for (let i = 1; i <= b.nozzles; i++) run("INSERT INTO nozzles (station_id,tank_id,label,totalizer) VALUES (?,?,?,0)", b.station_id, id, `${b.product}-${id}-${i}`);
    return get("SELECT * FROM tanks WHERE id=?", id);
  });
}));

/* ---------------- Prices ---------------- */
operations.get("/prices", requirePerm("prices.view"), h((req) => {
  const last = get("SELECT data, created_at FROM notifications WHERE tenant_id=? AND type='price_change' ORDER BY id DESC LIMIT 1", tid(req));
  const batch = last ? JSON.parse(last.data).batch : null;
  return {
    current: currentPrices(tid(req)),
    history: all("SELECT * FROM prices WHERE tenant_id=? ORDER BY effective_from DESC, id DESC LIMIT 60", tid(req)),
    products: PRODUCTS,
    // who has confirmed the latest price change on their dispenser
    last_change: batch ? {
      at: batch, changes: JSON.parse(last!.data).changes,
      acks: all(`SELECT u.name, s.name station, n.acked_at, n.data FROM notifications n JOIN users u ON u.id=n.user_id LEFT JOIN stations s ON s.id=u.station_id
        WHERE n.tenant_id=? AND n.type='price_change' AND json_extract(n.data,'$.batch')=? ORDER BY u.name`, tid(req), batch)
        .map((a) => ({ name: a.name, station: a.station, acked_at: a.acked_at, with_readings: Boolean(JSON.parse(a.data).settled) })),
    } : null,
  };
}));

operations.post("/prices", requirePerm("prices.update"), h(async (req) => {
  const b = parse(z.object({ prices: z.record(product, z.number().positive()), broadcast: z.boolean().default(false), note: z.string().optional() }), req.body);
  const t = tid(req);
  const old = currentPrices(t);
  const ts = now();
  for (const [p, price] of Object.entries(b.prices)) run("INSERT INTO prices (tenant_id,product,price,effective_from,created_by) VALUES (?,?,?,?,?)", t, p, price, ts, req.user!.name);
  // Revalue stock at the moment of change (price change gain/loss)
  const stock = all("SELECT t.product, SUM(t.current_l) l FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? GROUP BY t.product", t);
  const impact = stock.reduce((a, s) => a + (b.prices[s.product as keyof typeof b.prices] && old[s.product] ? (b.prices[s.product as keyof typeof b.prices]! - old[s.product].price) * s.l : 0), 0);
  createAlert(t, {
    type: "price_change", severity: "info", title: `Prices updated by ${req.user!.name}`,
    body: Object.entries(b.prices).map(([p, v]) => `${p}: ${old[p] ? pkr(old[p].price) + " → " : ""}${pkr(v)}`).join(", ") + `. Stock revaluation ${impact >= 0 ? "gain" : "loss"} ${pkr(Math.abs(impact))}.`,
  });
  // tell every salesman to change the dispenser rate (they must confirm, with meter readings if on shift)
  const changes = Object.entries(b.prices).filter(([p, v]) => old[p]?.price !== v)
    .map(([p, v]) => ({ product: p, old: old[p]?.price ?? null, new: v, diff: old[p] ? round2(v - old[p].price) : null }));
  const lines = changes.map((c) => `${PRODUCTS[c.product]}: ${c.old != null ? `${rateFmt(c.old)} → ` : ""}${rateFmt(c.new)}${c.diff ? ` (${c.diff > 0 ? "+" : "−"}Rs ${Math.abs(c.diff).toFixed(2)}/L ${c.diff > 0 ? "barh gaya" : "kam ho gaya"})` : ""}`);
  const salesmen = staff(t, ["salesman"]);
  if (changes.length) {
    const data = { batch: ts, changes, old: Object.fromEntries(Object.entries(old).map(([k, v]) => [k, v.price])) };
    await notify(t, salesmen, { type: "price_change", ack_required: true, data,
      title: "⛽ Fuel price changed — update the dispenser now",
      body: `${lines.join("\n")}\nDispenser par naya rate set karein aur app mein confirm karein (meter reading ke saath).` });
    await notify(t, staff(t, ["admin", "manager"], req.user!.id), { type: "price_change_info", data, whatsapp: false,
      title: `Prices changed by ${req.user!.name}`, body: lines.join("\n") });
  }
  let queued = 0;
  if (b.broadcast) {
    const customers = all("SELECT * FROM customers WHERE tenant_id=? AND opt_in=1", t);
    const msg = `⛽ Nayi qeematein (${new Date().toLocaleDateString("en-PK")}):\n` +
      Object.entries(b.prices).map(([p, v]) => `• ${PRODUCTS[p]}: ${rateFmt(v)}/L`).join("\n") + (b.note ? `\n${b.note}` : "") + "\nSTOP likh kar unsubscribe karein.";
    queued = customers.length;
    void (async () => { for (const c of customers) await sendWhatsApp(t, c, msg, "campaign", { kind: "price_update" }); })();
  }
  return { ok: true, stock_revaluation: Math.round(impact), broadcast_queued: queued, salesmen_notified: changes.length ? salesmen.length : 0 };
}));

/* ---------------- Sales / POS ---------------- */
operations.get("/sales", requirePerm("sales.view"), h((req) => {
  const limit = Math.min(500, Number(req.query.limit ?? 100));
  const own = scopedStation(req);
  return all(
    `SELECT s.*, c.name customer_name, st.name station_name FROM sales s JOIN stations st ON st.id=s.station_id
     LEFT JOIN customers c ON c.id=s.customer_id WHERE st.tenant_id=? ${own ? "AND s.station_id=" + Number(own) : ""} ORDER BY s.id DESC LIMIT ?`, tid(req), limit);
}));

operations.post("/sales", requirePerm("sales.create"), h((req) => {
  const b = parse(z.object({
    station_id: z.number(), product, litres: z.number().positive().optional(), amount: z.number().positive().optional(),
    payment_method: z.enum(["cash", "card", "jazzcash", "easypaisa", "raast", "khata"]),
    customer_id: z.number().nullable().optional(), nozzle_id: z.number().nullable().optional(), vehicle_no: z.string().nullable().optional(),
  }), req.body);
  b.station_id = scopedStation(req, b.station_id)!;
  // a salesman's sale goes on their own open shift; others use the station's latest open shift
  const shift = req.user!.role === "salesman"
    ? get("SELECT id FROM shifts WHERE station_id=? AND status='open' AND attendant=? ORDER BY id DESC LIMIT 1", b.station_id, req.user!.name)
    : get("SELECT id FROM shifts WHERE station_id=? AND status='open' ORDER BY id DESC LIMIT 1", b.station_id);
  if (req.user!.role === "salesman" && !shift) throw new AppError(400, "Start your shift first (Shifts page) before recording sales");
  if (req.user!.role === "salesman" && get("SELECT id FROM notifications WHERE user_id=? AND type='price_change' AND acked_at IS NULL LIMIT 1", req.user!.id))
    throw new AppError(409, "Fuel price has changed. Update the dispenser and confirm the new price first.");
  return recordSale(tid(req), { ...b, shift_id: shift?.id ?? null });
}));

/* ---------------- Shifts ---------------- */
operations.get("/shifts", requirePerm("shifts.manage"), h((req) => all(
  `SELECT sh.*, st.name station_name FROM shifts sh JOIN stations st ON st.id=sh.station_id WHERE st.tenant_id=?
   ${req.user!.role === "salesman" ? "AND sh.station_id=" + Number(scopedStation(req)) + " AND sh.attendant=?" : "AND ?=?"} ORDER BY sh.id DESC LIMIT 60`,
  tid(req), ...(req.user!.role === "salesman" ? [req.user!.name] : [1, 1]),
).map((s) => ({ ...s, readings: all("SELECT r.*, n.label FROM meter_readings r JOIN nozzles n ON n.id=r.nozzle_id WHERE r.shift_id=?", s.id) }))));

operations.post("/shifts/open", requirePerm("shifts.manage"), h((req) => {
  const b = parse(z.object({ station_id: z.number().optional(), attendant: z.string().min(2).optional() }), req.body);
  const isSalesman = req.user!.role === "salesman";
  const stationId = isSalesman ? scopedStation(req, b.station_id)! : b.station_id;
  const attendant = isSalesman ? req.user!.name : b.attendant;
  if (!stationId || !attendant) throw new AppError(400, "Station and attendant are required");
  ownStation(tid(req), stationId);
  if (get("SELECT id FROM shifts WHERE station_id=? AND status='open' AND attendant=?", stationId, attendant)) throw new AppError(400, "This attendant already has an open shift");
  return tx(() => {
    const { id } = run("INSERT INTO shifts (station_id,attendant,opened_at,status) VALUES (?,?,?, 'open')", stationId, attendant, now());
    for (const n of all("SELECT * FROM nozzles WHERE station_id=?", stationId)) run("INSERT INTO meter_readings (shift_id,nozzle_id,opening) VALUES (?,?,?)", id, n.id, n.totalizer);
    return get("SELECT * FROM shifts WHERE id=?", id);
  });
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
operations.get("/shifts/:id/live", requirePerm("shifts.manage"), h((req) => {
  const shift = ownOpenShift(req, Number(req.params.id));
  return {
    shift, hours_open: round2((Date.parse(shift.closed_at ?? now()) - Date.parse(shift.opened_at)) / 3600_000),
    readings: shiftReadings(shift.id), summary: shiftSummary(shift.id),
    prices: Object.fromEntries(Object.entries(currentPrices(tid(req))).map(([k, v]) => [k, v.price])),
  };
}));

/**
 * Close a shift with closing meter readings and counted cash.
 * Litres on the meter not entered on the POS are booked as cash sales (stock follows the meters).
 * Expected cash = all cash sales in the shift; variance = counted - expected.
 */
operations.post("/shifts/:id/close", requirePerm("shifts.manage"), h(async (req) => {
  const b = parse(z.object({ readings: z.record(z.string(), z.number().min(0)), cash_actual: z.number().min(0), notes: z.string().optional() }), req.body);
  const t = tid(req);
  const shift = ownOpenShift(req, Number(req.params.id));
  if (shift.status !== "open") throw new AppError(400, "Shift is not open");
  tx(() => {
    settleShift(t, shift, b.readings, () => undefined);
    for (const r of shiftReadings(shift.id)) run("UPDATE meter_readings SET closing=? WHERE id=?", b.readings[String(r.nozzle_id)], r.id);
    const totalLitres = shiftReadings(shift.id).reduce((a, r) => a + (r.closing - r.opening), 0);
    const expected = shiftSummary(shift.id).cash_expected;
    run("UPDATE shifts SET status='closed', closed_at=?, litres=?, cash_expected=?, cash_actual=?, variance=?, notes=? WHERE id=?",
      now(), round2(totalLitres), expected, b.cash_actual, round2(b.cash_actual - expected), b.notes ?? null, shift.id);
  });
  const closed = get("SELECT * FROM shifts WHERE id=?", shift.id)!;
  const summary = shiftSummary(shift.id);
  const v = closed.variance;
  if (v < -500)
    createAlert(t, { station_id: shift.station_id, type: "cash_short", severity: v < -5000 ? "critical" : "warning",
      title: `Cash short ${pkr(-v)} — ${shift.attendant}`, body: `Shift #${shift.id}: expected ${pkr(closed.cash_expected)}, counted ${pkr(b.cash_actual)}.`, dedupe_key: `shift-${shift.id}` });
  // shift report to managers/admin (WhatsApp only when cash is short)
  await notify(t, staff(t, ["admin", "manager"], req.user!.id), {
    type: "shift_closed", data: { shift_id: shift.id }, whatsapp: v < -500,
    title: `${v < -500 ? "⚠️" : "✅"} Shift closed — ${shift.attendant} (${shift.station_name.replace("Al-Madina ", "")})`,
    body: `${summary.by_product.map((p) => `${PRODUCTS[p.product]} ${Math.round(p.litres).toLocaleString()} L`).join(" · ")}\n` +
      `Sales ${pkr(summary.amount)} · Cash expected ${pkr(closed.cash_expected)} · Counted ${pkr(b.cash_actual)} · ${v < 0 ? "Short" : "Over"} ${pkr(Math.abs(v))}`,
  });
  return { ...closed, summary, readings: shiftReadings(shift.id) };
}));

/* ---------------- Stock: dips & deliveries ---------------- */
operations.get("/stock", requirePerm("stock.manage"), h((req) => ({
  dips: all(`SELECT d.*, t.name tank, t.product, s.name station FROM dip_readings d JOIN tanks t ON t.id=d.tank_id JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? ORDER BY d.id DESC LIMIT 50`, tid(req)),
  deliveries: all(`SELECT d.*, t.name tank, t.product, s.name station FROM deliveries d JOIN tanks t ON t.id=d.tank_id JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? ORDER BY d.id DESC LIMIT 50`, tid(req)),
})));

operations.post("/stock/dip", requirePerm("stock.manage"), h((req) => {
  const b = parse(z.object({ tank_id: z.number(), measured_l: z.number().min(0) }), req.body);
  const t = ownTank(tid(req), b.tank_id);
  const variance = t.current_l ? ((b.measured_l - t.current_l) / t.current_l) * 100 : 0;
  return tx(() => {
    const { id } = run("INSERT INTO dip_readings (tank_id,measured_l,book_l,variance_pct,created_at) VALUES (?,?,?,?,?)", t.id, b.measured_l, t.current_l, round2(variance), now());
    run("UPDATE tanks SET current_l=? WHERE id=?", b.measured_l, t.id);
    if (Math.abs(variance) >= 0.5)
      createAlert(tid(req), { station_id: t.station_id, type: "stock_variance", severity: Math.abs(variance) >= 1 ? "critical" : "warning",
        title: `${t.name}: stock variance ${variance.toFixed(2)}%`, body: `Dip ${Math.round(b.measured_l)}L vs book ${Math.round(t.current_l)}L.`, dedupe_key: `dip-${id}` });
    return get("SELECT * FROM dip_readings WHERE id=?", id);
  });
}));

operations.post("/stock/delivery", requirePerm("stock.manage"), h((req) => {
  const b = parse(z.object({
    tank_id: z.number(), invoice_l: z.number().positive(), received_l: z.number().positive(), tanker_no: z.string().optional(), supplier: z.string().optional(),
    supplier_id: z.number().optional().nullable(), purchase_rate: z.number().positive().optional().nullable(),
  }), req.body);
  const t = ownTank(tid(req), b.tank_id);
  const supplier = b.supplier_id ? get("SELECT * FROM suppliers WHERE id=? AND tenant_id=?", b.supplier_id, tid(req)) : null;
  if (b.supplier_id && !supplier) throw new AppError(400, "Supplier not found");
  if (supplier && !b.purchase_rate) throw new AppError(400, "Enter the purchase rate per litre from the supplier invoice");
  if (t.current_l + b.received_l > t.capacity_l * 1.001) throw new AppError(400, `Exceeds tank capacity (${t.capacity_l}L)`);
  const shortage = ((b.invoice_l - b.received_l) / b.invoice_l) * 100;
  return tx(() => {
    const { id } = run("INSERT INTO deliveries (tank_id,supplier,supplier_id,purchase_rate,tanker_no,invoice_l,received_l,shortage_pct,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
      t.id, supplier?.name ?? b.supplier ?? null, supplier?.id ?? null, b.purchase_rate ?? null, b.tanker_no ?? null, b.invoice_l, b.received_l, round2(shortage), now());
    // we pay the supplier for the invoiced litres; any shortage is claimed separately
    if (supplier && b.purchase_rate) recordPurchase(tid(req), { supplier_id: supplier.id, delivery_id: id, product: t.product, litres: b.invoice_l, rate: b.purchase_rate, ref: b.tanker_no, by: req.user!.name });
    run("UPDATE tanks SET current_l = current_l + ? WHERE id=?", b.received_l, t.id);
    if (shortage >= 0.3)
      createAlert(tid(req), { station_id: t.station_id, type: "short_delivery", severity: shortage >= 0.8 ? "critical" : "warning",
        title: `Tanker ${b.tanker_no ?? ""} short by ${shortage.toFixed(2)}%`, body: `${t.name}: invoice ${b.invoice_l}L, received ${b.received_l}L.`, dedupe_key: `delivery-${id}` });
    return get("SELECT * FROM deliveries WHERE id=?", id);
  });
}));
