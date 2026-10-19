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

/** Stations, tanks, meters (dispenser nozzles) and per-meter sales. */
export const stationRoutes = Router();

/** Stations with their tanks and meters. Closed stations, retired tanks and meters are left out unless ?all=1 (the Settings page). */
stationRoutes.get("/stations", requireAny("sales.view", "stock.manage", "wholesale.view", "users.manage"), h((req) => {
  const own = req.user!.role === "salesman" ? scopedStation(req) : null;
  const act = req.query.all === "1" ? "" : "AND active=1";
  return all(`SELECT * FROM stations WHERE tenant_id=? ${own ? "AND id=" + Number(own) : ""} ${act} ORDER BY id`, tid(req)).map((s) => ({
  ...s,
  tanks: all(`SELECT * FROM tanks WHERE station_id=? ${act} ORDER BY id`, s.id),
  nozzles: all(`SELECT n.*, ${METER} name, t.product FROM nozzles n JOIN tanks t ON t.id=n.tank_id WHERE n.station_id=? ${act.replace("active", "n.active")} ORDER BY n.meter_no, n.id`, s.id),
}));
}));

const stationBody = z.object({ name: z.string().min(2).max(80), city: z.string().max(60).optional().nullable(), address: z.string().max(200).optional().nullable(), omc: z.string().max(40).optional().nullable(),
  timings: z.string().max(60).optional().nullable(), services: z.string().max(200).optional().nullable(), lat: z.number().min(-90).max(90).optional().nullable(), lng: z.number().min(-180).max(180).optional().nullable() });
stationRoutes.post("/stations", requirePerm("stations.manage"), h(async (req) => {
  const b = parse(stationBody, req.body);
  const st = get("SELECT * FROM stations WHERE id=?", run("INSERT INTO stations (tenant_id,name,city,address,omc,timings,services,lat,lng) VALUES (?,?,?,?,?,?,?,?,?)",
    tid(req), b.name, b.city ?? null, b.address ?? null, b.omc ?? null, b.timings ?? "24 hours", b.services ?? null, b.lat ?? null, b.lng ?? null).id)!;
  await announce(tid(req), req.user!.id, ["manager", "admin"], { type: "new_station", data: { station_id: st.id }, title: `⛽ New station: ${st.name}`, body: "Add its tanks and assign salesmen." });
  return st;
}));
/** Edit a station (name, address, timings, location for the attendance distance check) or close / reopen it. */
stationRoutes.patch("/stations/:id", requirePerm("stations.manage"), h((req) => {
  const b = parse(stationBody.partial().extend({ active: z.boolean().optional() }), req.body);
  const s = ownStation(tid(req), Number(req.params.id));
  if (b.active === false && get("SELECT id FROM shifts WHERE station_id=? AND status='open'", s.id)) throw new AppError(400, "Close the open shifts at this station first");
  tx(() => {
    for (const k of ["name", "city", "address", "omc", "timings", "services", "lat", "lng"] as const) if (b[k] !== undefined) run(`UPDATE stations SET ${k}=? WHERE id=?`, b[k], s.id);
    if (b.active !== undefined) run("UPDATE stations SET active=? WHERE id=?", b.active ? 1 : 0, s.id);
  });
  audit(tid(req), req.user!, "station_edit", `station:${s.id}`, b);
  return get("SELECT * FROM stations WHERE id=?", s.id);
}));
/** Delete a station that was never used (no sales, shifts or tanks); a used one is closed instead. */
stationRoutes.delete("/stations/:id", requirePerm("stations.manage"), h((req) => {
  const s = ownStation(tid(req), Number(req.params.id));
  if (get("SELECT id FROM stations WHERE tenant_id=? AND id<>? AND active=1 LIMIT 1", tid(req), s.id) == null) throw new AppError(400, "This is the only station — it cannot be removed");
  const used = get("SELECT (SELECT COUNT(*) FROM sales WHERE station_id=?) + (SELECT COUNT(*) FROM shifts WHERE station_id=?) + (SELECT COUNT(*) FROM tanks WHERE station_id=?) n", s.id, s.id, s.id)!.n;
  if (used) throw new AppError(400, "This station has tanks or history — close it instead (it keeps its records)");
  run("DELETE FROM stations WHERE id=?", s.id);
  run("UPDATE users SET station_id=NULL WHERE station_id=?", s.id);
  audit(tid(req), req.user!, "station_delete", `station:${s.id}`, { name: s.name });
  return { ok: true };
}));

stationRoutes.post("/tanks", requirePerm("stock.manage"), h(async (req) => {
  const b = parse(z.object({ station_id: z.number(), name: z.string(), product, capacity_l: z.number().positive(), current_l: z.number().min(0), reorder_pct: z.number().min(5).max(80).default(25), nozzles: z.number().int().min(0).max(12).default(2) }), req.body);
  const st = ownStation(tid(req), b.station_id);
  if (b.current_l > b.capacity_l) throw new AppError(400, "Current stock cannot be more than the capacity");
  const tank = tx(() => {
    const { id } = run("INSERT INTO tanks (station_id,name,product,capacity_l,current_l,reorder_pct) VALUES (?,?,?,?,?,?)", b.station_id, b.name, b.product, b.capacity_l, b.current_l, b.reorder_pct);
    for (let i = 1; i <= b.nozzles; i++) run("INSERT INTO nozzles (station_id,tank_id,label,totalizer) VALUES (?,?,?,0)", b.station_id, id, `${b.product}-${id}-${i}`);
    return get("SELECT * FROM tanks WHERE id=?", id)!;
  });
  await announce(tid(req), req.user!.id, ["manager", "admin", "salesman"], { type: "new_tank", data: { tank_id: tank.id }, stationId: st.id,
    title: `🛢️ New tank at ${st.name}: ${b.name}`, body: `${PRODUCTS[b.product]} · ${b.capacity_l.toLocaleString()} L · ${b.nozzles} nozzles. Nayi shift se meter readings mein shamil hoga.` });
  return tank;
}));

/** Edit a tank (name, capacity, reorder level) or retire / bring back an empty one. Stock itself moves only through dips and deliveries. */
stationRoutes.patch("/tanks/:id", requirePerm("stock.manage"), h((req) => {
  const b = parse(z.object({ name: z.string().trim().min(1).max(60).optional(), capacity_l: z.number().positive().max(500_000).optional(), reorder_pct: z.number().min(5).max(80).optional(), active: z.boolean().optional() }), req.body);
  const tk = ownTank(tid(req), Number(req.params.id));
  if (b.capacity_l !== undefined && b.capacity_l < tk.current_l) throw new AppError(400, `Capacity cannot be less than the ${Math.round(tk.current_l).toLocaleString()} L in the tank now`);
  if (b.active === false) {
    if (tk.current_l > 0.5) throw new AppError(400, `Empty the tank first — it still shows ${Math.round(tk.current_l).toLocaleString()} L (record a dip)`);
    if (get("SELECT r.id FROM meter_readings r JOIN shifts sh ON sh.id=r.shift_id JOIN nozzles n ON n.id=r.nozzle_id WHERE n.tank_id=? AND sh.status='open' LIMIT 1", tk.id)) throw new AppError(400, "A meter on this tank is running in an open shift");
  }
  tx(() => {
    for (const k of ["name", "capacity_l", "reorder_pct"] as const) if (b[k] !== undefined) run(`UPDATE tanks SET ${k}=? WHERE id=?`, b[k], tk.id);
    if (b.active !== undefined) { run("UPDATE tanks SET active=? WHERE id=?", b.active ? 1 : 0, tk.id); run("UPDATE nozzles SET active=? WHERE tank_id=?", b.active ? 1 : 0, tk.id); }
  });
  audit(tid(req), req.user!, "tank_edit", `tank:${tk.id}`, b);
  return get("SELECT * FROM tanks WHERE id=?", tk.id);
}));
/** Delete a tank that was never used (no deliveries, dips or meter readings); a used one is retired instead. */
stationRoutes.delete("/tanks/:id", requirePerm("stock.manage"), h((req) => {
  const tk = ownTank(tid(req), Number(req.params.id));
  const used = get(`SELECT (SELECT COUNT(*) FROM deliveries WHERE tank_id=?) + (SELECT COUNT(*) FROM dip_readings WHERE tank_id=?)
    + (SELECT COUNT(*) FROM meter_readings r JOIN nozzles n ON n.id=r.nozzle_id WHERE n.tank_id=?) + (SELECT COUNT(*) FROM sales s JOIN nozzles n ON n.id=s.nozzle_id WHERE n.tank_id=?) n`, tk.id, tk.id, tk.id, tk.id)!.n;
  if (used) throw new AppError(400, "This tank has history — retire it instead (it keeps its records)");
  tx(() => { run("DELETE FROM tank_charts WHERE tank_id=?", tk.id); run("DELETE FROM nozzles WHERE tank_id=?", tk.id); run("DELETE FROM tanks WHERE id=?", tk.id); });
  audit(tid(req), req.user!, "tank_delete", `tank:${tk.id}`, { name: tk.name });
  return { ok: true };
}));

/** Add a dispenser meter to an existing tank (a new machine on the forecourt). */
stationRoutes.post("/tanks/:id/nozzles", requirePerm("stock.manage"), h((req) => {
  const b = parse(z.object({ label: z.string().trim().max(30).optional(), totalizer: z.number().min(0).max(99_999_999).default(0), meter_no: z.number().int().min(1).max(99).optional() }), req.body);
  const tk = ownTank(tid(req), Number(req.params.id));
  const n = Number(get("SELECT COUNT(*) n FROM nozzles WHERE tank_id=?", tk.id)!.n) + 1;
  const { id } = run("INSERT INTO nozzles (station_id,tank_id,label,totalizer,meter_no) VALUES (?,?,?,?,?)", tk.station_id, tk.id, b.label || `${tk.product}-${n}`, b.totalizer, b.meter_no ?? null);
  audit(tid(req), req.user!, "meter_add", `nozzle:${id}`, b);
  return get(`SELECT n.*, ${METER} name FROM nozzles n WHERE n.id=?`, id);
}));
const nozzleBusy = (id: number) => Boolean(get("SELECT r.id FROM meter_readings r JOIN shifts sh ON sh.id=r.shift_id WHERE r.nozzle_id=? AND sh.status='open' LIMIT 1", id));
/** Renumber or rename a meter (No.1, No.2 …), or retire / bring back one. Taking a number another meter has swaps the two. */
stationRoutes.patch("/nozzles/:id", requirePerm("stock.manage"), h((req) => {
  const b = parse(z.object({ meter_no: z.number().int().min(1).max(99).optional(), label: z.string().trim().min(1).max(30).optional(), active: z.boolean().optional() }), req.body);
  const n = get("SELECT n.* FROM nozzles n JOIN stations s ON s.id=n.station_id WHERE n.id=? AND s.tenant_id=?", Number(req.params.id), tid(req));
  if (!n) throw new AppError(404, "Meter not found");
  if (b.active === false && nozzleBusy(n.id)) throw new AppError(400, "This meter is running in an open shift — close the shift first");
  tx(() => {
    if (b.meter_no && b.meter_no !== n.meter_no) {
      run("UPDATE nozzles SET meter_no=? WHERE station_id=? AND meter_no=?", n.meter_no, n.station_id, b.meter_no);
      run("UPDATE nozzles SET meter_no=? WHERE id=?", b.meter_no, n.id);
    }
    if (b.label) run("UPDATE nozzles SET label=? WHERE id=?", b.label, n.id);
    if (b.active !== undefined) run("UPDATE nozzles SET active=? WHERE id=?", b.active ? 1 : 0, n.id);
  });
  return get(`SELECT n.*, ${METER} name FROM nozzles n WHERE n.id=?`, n.id);
}));
/** Delete a meter that never ran a shift; one with readings is retired instead. */
stationRoutes.delete("/nozzles/:id", requirePerm("stock.manage"), h((req) => {
  const n = get("SELECT n.* FROM nozzles n JOIN stations s ON s.id=n.station_id WHERE n.id=? AND s.tenant_id=?", Number(req.params.id), tid(req));
  if (!n) throw new AppError(404, "Meter not found");
  if (get("SELECT id FROM meter_readings WHERE nozzle_id=? LIMIT 1", n.id) || get("SELECT id FROM sales WHERE nozzle_id=? LIMIT 1", n.id)) throw new AppError(400, "This meter has readings — retire it instead");
  run("DELETE FROM nozzles WHERE id=?", n.id);
  audit(tid(req), req.user!, "meter_delete", `nozzle:${n.id}`, { label: n.label });
  return { ok: true };
}));

/** Litres and money per meter for a period (default: today). */
stationRoutes.get("/meters/sales", requirePerm("reports.view"), h((req) => {
  const q = parse(z.object({ from: z.string().datetime({ offset: true }).optional(), to: z.string().datetime({ offset: true }).optional(), station_id: z.coerce.number().optional() }), req.query);
  const to = q.to ? new Date(q.to).toISOString() : new Date().toISOString();
  const from = q.from ? new Date(q.from).toISOString() : pkDayStart();
  return { from, to, meters: meterSales(tid(req), from, to, q.station_id) };
}));

