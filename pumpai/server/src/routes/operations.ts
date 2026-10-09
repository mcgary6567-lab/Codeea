import { Router, type Request } from "express";
import { z } from "zod";
import { registerDecider, requestApproval, closeApproval } from "./approvals.js";
import { walletAfterFill } from "./prepaid.js";
import { askRating } from "./feedback.js";
import { claimForDelivery } from "./claims.js";
import { all, get, run, tx, now, getSetting, pkDate, pkDayStart, METER, meterName, type Row } from "../db.js";
import { meterSales } from "./reports.js";
import { h, parse, tid, requirePerm, requireAny, scopedStation, can } from "../auth.js";
import { AppError, recordSale, undoSale, audit, UNDO_SECONDS, currentPrices, createAlert, round2, pkr, rateFmt, correctTo15 } from "../services.js";
import { productSchema } from "../products.js";
import { moneyMethods, SPECIAL_METHODS } from "./lookups.js";
import { sendWhatsApp } from "../whatsapp/cloud.js";
import { PRODUCTS } from "../config.js";
import { recordPurchase } from "./suppliers.js";
import { followPumpPrice } from "./wholesale.js";
import { khataFillReceipt, wholesaleRateMessage, receiptUrl } from "../billing.js";
import { chargeShortage } from "./staff.js";
import { closeOrderOnDelivery, litresFromCm } from "./backoffice.js";
import { linkPhotos, photosFor, proofPhotos, proofCol } from "./capture.js";
import { settleShift, shiftReadings, shiftSummary, shiftReport, shiftFuels } from "../shifts.js";
import { notify, staff, announce } from "../notifications.js";

export const operations = Router();
const product = productSchema();

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

/** Stations with their tanks and meters. Closed stations, retired tanks and meters are left out unless ?all=1 (the Settings page). */
operations.get("/stations", requireAny("sales.view", "stock.manage", "wholesale.view", "users.manage"), h((req) => {
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
operations.post("/stations", requirePerm("stations.manage"), h(async (req) => {
  const b = parse(stationBody, req.body);
  const st = get("SELECT * FROM stations WHERE id=?", run("INSERT INTO stations (tenant_id,name,city,address,omc,timings,services,lat,lng) VALUES (?,?,?,?,?,?,?,?,?)",
    tid(req), b.name, b.city ?? null, b.address ?? null, b.omc ?? null, b.timings ?? "24 hours", b.services ?? null, b.lat ?? null, b.lng ?? null).id)!;
  await announce(tid(req), req.user!.id, ["manager", "admin"], { type: "new_station", data: { station_id: st.id }, title: `⛽ New station: ${st.name}`, body: "Add its tanks and assign salesmen." });
  return st;
}));
/** Edit a station (name, address, timings, location for the attendance distance check) or close / reopen it. */
operations.patch("/stations/:id", requirePerm("stations.manage"), h((req) => {
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
operations.delete("/stations/:id", requirePerm("stations.manage"), h((req) => {
  const s = ownStation(tid(req), Number(req.params.id));
  if (get("SELECT id FROM stations WHERE tenant_id=? AND id<>? AND active=1 LIMIT 1", tid(req), s.id) == null) throw new AppError(400, "This is the only station — it cannot be removed");
  const used = get("SELECT (SELECT COUNT(*) FROM sales WHERE station_id=?) + (SELECT COUNT(*) FROM shifts WHERE station_id=?) + (SELECT COUNT(*) FROM tanks WHERE station_id=?) n", s.id, s.id, s.id)!.n;
  if (used) throw new AppError(400, "This station has tanks or history — close it instead (it keeps its records)");
  run("DELETE FROM stations WHERE id=?", s.id);
  run("UPDATE users SET station_id=NULL WHERE station_id=?", s.id);
  audit(tid(req), req.user!, "station_delete", `station:${s.id}`, { name: s.name });
  return { ok: true };
}));

operations.post("/tanks", requirePerm("stock.manage"), h(async (req) => {
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
operations.patch("/tanks/:id", requirePerm("stock.manage"), h((req) => {
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
operations.delete("/tanks/:id", requirePerm("stock.manage"), h((req) => {
  const tk = ownTank(tid(req), Number(req.params.id));
  const used = get(`SELECT (SELECT COUNT(*) FROM deliveries WHERE tank_id=?) + (SELECT COUNT(*) FROM dip_readings WHERE tank_id=?)
    + (SELECT COUNT(*) FROM meter_readings r JOIN nozzles n ON n.id=r.nozzle_id WHERE n.tank_id=?) + (SELECT COUNT(*) FROM sales s JOIN nozzles n ON n.id=s.nozzle_id WHERE n.tank_id=?) n`, tk.id, tk.id, tk.id, tk.id)!.n;
  if (used) throw new AppError(400, "This tank has history — retire it instead (it keeps its records)");
  tx(() => { run("DELETE FROM tank_charts WHERE tank_id=?", tk.id); run("DELETE FROM nozzles WHERE tank_id=?", tk.id); run("DELETE FROM tanks WHERE id=?", tk.id); });
  audit(tid(req), req.user!, "tank_delete", `tank:${tk.id}`, { name: tk.name });
  return { ok: true };
}));

/** Add a dispenser meter to an existing tank (a new machine on the forecourt). */
operations.post("/tanks/:id/nozzles", requirePerm("stock.manage"), h((req) => {
  const b = parse(z.object({ label: z.string().trim().max(30).optional(), totalizer: z.number().min(0).max(99_999_999).default(0), meter_no: z.number().int().min(1).max(99).optional() }), req.body);
  const tk = ownTank(tid(req), Number(req.params.id));
  const n = Number(get("SELECT COUNT(*) n FROM nozzles WHERE tank_id=?", tk.id)!.n) + 1;
  const { id } = run("INSERT INTO nozzles (station_id,tank_id,label,totalizer,meter_no) VALUES (?,?,?,?,?)", tk.station_id, tk.id, b.label || `${tk.product}-${n}`, b.totalizer, b.meter_no ?? null);
  audit(tid(req), req.user!, "meter_add", `nozzle:${id}`, b);
  return get(`SELECT n.*, ${METER} name FROM nozzles n WHERE n.id=?`, id);
}));
const nozzleBusy = (id: number) => Boolean(get("SELECT r.id FROM meter_readings r JOIN shifts sh ON sh.id=r.shift_id WHERE r.nozzle_id=? AND sh.status='open' LIMIT 1", id));
/** Renumber or rename a meter (No.1, No.2 …), or retire / bring back one. Taking a number another meter has swaps the two. */
operations.patch("/nozzles/:id", requirePerm("stock.manage"), h((req) => {
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
operations.delete("/nozzles/:id", requirePerm("stock.manage"), h((req) => {
  const n = get("SELECT n.* FROM nozzles n JOIN stations s ON s.id=n.station_id WHERE n.id=? AND s.tenant_id=?", Number(req.params.id), tid(req));
  if (!n) throw new AppError(404, "Meter not found");
  if (get("SELECT id FROM meter_readings WHERE nozzle_id=? LIMIT 1", n.id) || get("SELECT id FROM sales WHERE nozzle_id=? LIMIT 1", n.id)) throw new AppError(400, "This meter has readings — retire it instead");
  run("DELETE FROM nozzles WHERE id=?", n.id);
  audit(tid(req), req.user!, "meter_delete", `nozzle:${n.id}`, { label: n.label });
  return { ok: true };
}));

/** Litres and money per meter for a period (default: today). */
operations.get("/meters/sales", requirePerm("reports.view"), h((req) => {
  const q = parse(z.object({ from: z.string().datetime({ offset: true }).optional(), to: z.string().datetime({ offset: true }).optional(), station_id: z.coerce.number().optional() }), req.query);
  const to = q.to ? new Date(q.to).toISOString() : new Date().toISOString();
  const from = q.from ? new Date(q.from).toISOString() : pkDayStart();
  return { from, to, meters: meterSales(tid(req), from, to, q.station_id) };
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

type PriceInput = { prices: Partial<Record<"PMG" | "HOBC" | "HSD", number>>; broadcast: boolean; note?: string };
/** Put new prices into effect: stock revaluation, salesmen told to change the dispenser, wholesale rates follow. */
export async function applyPrices(t: number, by: { id: number; name: string }, b: PriceInput) {
  const old = currentPrices(t);
  const ts = now();
  for (const [p, price] of Object.entries(b.prices)) run("INSERT INTO prices (tenant_id,product,price,effective_from,created_by) VALUES (?,?,?,?,?)", t, p, price, ts, by.name);
  // Revalue stock at the moment of change (price change gain/loss)
  const stock = all("SELECT t.product, SUM(t.current_l) l FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? GROUP BY t.product", t);
  const impact = stock.reduce((a, s) => a + (b.prices[s.product as keyof typeof b.prices] && old[s.product] ? (b.prices[s.product as keyof typeof b.prices]! - old[s.product].price) * s.l : 0), 0);
  createAlert(t, {
    type: "price_change", severity: "info", title: `Prices updated by ${by.name}`,
    body: Object.entries(b.prices).map(([p, v]) => `${p}: ${old[p] ? pkr(old[p].price) + " → " : ""}${pkr(v)}`).join(", ") + `. Stock revaluation ${impact >= 0 ? "gain" : "loss"} ${pkr(Math.abs(impact))}.`,
  });
  // tell every salesman to change the dispenser rate (they must confirm, with meter readings if on shift)
  const changes = Object.entries(b.prices).filter(([p, v]) => old[p]?.price !== v)
    .map(([p, v]) => ({ product: p, old: old[p]?.price ?? null, new: v, diff: old[p] ? round2(v - old[p].price) : null }));
  audit(t, by, "price:Changed fuel prices", "prices", { changes: changes.map((c) => ({ field: PRODUCTS[c.product] ?? c.product, from: c.old, to: c.new })), note: b.note ?? null });
  const lines = changes.map((c) => `${PRODUCTS[c.product]}: ${c.old != null ? `${rateFmt(c.old)} → ` : ""}${rateFmt(c.new)}${c.diff ? ` (${c.diff > 0 ? "+" : "−"}Rs ${Math.abs(c.diff).toFixed(2)}/L ${c.diff > 0 ? "barh gaya" : "kam ho gaya"})` : ""}`);
  const salesmen = staff(t, ["salesman"]);
  if (changes.length) {
    const data = { batch: ts, changes, old: Object.fromEntries(Object.entries(old).map(([k, v]) => [k, v.price])) };
    await notify(t, salesmen, { type: "price_change", ack_required: true, data,
      title: "⛽ Fuel price changed — update the dispenser now",
      body: `${lines.join("\n")}\nDispenser par naya rate set karein aur app mein confirm karein (meter reading ke saath).` });
    await notify(t, staff(t, ["admin", "manager"], by.id), { type: "price_change_info", data, whatsapp: false,
      title: `Prices changed by ${by.name}`, body: lines.join("\n") });
  }
  // wholesale clients on "pump − Rs X" follow the new pump price; fixed-rate clients are listed for review
  const ws = changes.length ? tx(() => followPumpPrice(t, changes)) : { moved: [], fixed: [], perClient: {} as Record<number, string[]> };
  for (const [cid, ls] of Object.entries(ws.perClient)) await wholesaleRateMessage(t, Number(cid), ls);
  if (ws.moved.length || ws.fixed.length)
    await notify(t, staff(t, ["wholesale", "admin"]), { type: "wholesale_rates", whatsapp: false, data: { batch: ts },
      title: `🚛 Wholesale rates ${ws.moved.length ? `updated for ${ws.moved.length} client rate${ws.moved.length === 1 ? "" : "s"}` : "— review fixed rates"}`,
      body: [...ws.moved, ...(ws.fixed.length ? ["Fixed rate (not changed, review if needed):", ...ws.fixed] : [])].join("\n") });
  let queued = 0;
  if (b.broadcast) {
    const customers = all("SELECT * FROM customers WHERE tenant_id=? AND opt_in=1", t);
    const msg = `⛽ Nayi qeematein (${new Date().toLocaleDateString("en-PK")}):\n` +
      Object.entries(b.prices).map(([p, v]) => `• ${PRODUCTS[p]}: ${rateFmt(v)}/L`).join("\n") + (b.note ? `\n${b.note}` : "") + "\nSTOP likh kar unsubscribe karein.";
    queued = customers.length;
    void (async () => { for (const c of customers) await sendWhatsApp(t, c, msg, "campaign", { kind: "price_update" }); })();
  }
  return { ok: true, stock_revaluation: Math.round(impact), broadcast_queued: queued, salesmen_notified: changes.length ? salesmen.length : 0, wholesale_rates_updated: ws.moved.length };
}

const priceBody = z.object({ prices: z.record(product, z.number().positive()), broadcast: z.boolean().default(false), note: z.string().optional() });
operations.post("/prices", requirePerm("prices.update"), h(async (req) => {
  const b = parse(priceBody, req.body);
  const t = tid(req);
  // two-person rule: when switched on, a manager's price change waits for the admin
  if (getSetting(t, "price_approval", "0") === "1" && req.user!.role !== "admin") {
    const old = currentPrices(t);
    const { id } = run("INSERT INTO price_requests (tenant_id,prices,broadcast,note,requested_by,requested_by_id,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
      t, JSON.stringify(b.prices), b.broadcast ? 1 : 0, b.note ?? null, req.user!.name, req.user!.id, "pending", now());
    await notify(t, staff(t, ["admin"]), { type: "price_request", data: { request_id: id }, title: `Price change needs your OK — ${req.user!.name}`,
      body: Object.entries(b.prices).map(([p, v]) => `${PRODUCTS[p]}: ${old[p] ? `${rateFmt(old[p].price)} → ` : ""}${rateFmt(v!)}`).join("\n") });
    await requestApproval(t, "price", id, `⛽ Price change by ${req.user!.name}\n` + Object.entries(b.prices).map(([p, v]) => `${PRODUCTS[p]}: ${old[p] ? `${rateFmt(old[p].price)} → ` : ""}${rateFmt(v!)}`).join("\n") + (b.note ? `\n${b.note}` : ""));
    return { pending: true, request_id: id };
  }
  return applyPrices(t, req.user!, b);
}));

operations.get("/price-requests", requirePerm("prices.update"), h((req) =>
  all("SELECT * FROM price_requests WHERE tenant_id=? ORDER BY id DESC LIMIT 20", tid(req)).map((r) => ({ ...r, prices: JSON.parse(r.prices) }))));

/* ---------------- Scheduled (future) price changes ---------------- */
operations.get("/prices/scheduled", requirePerm("prices.update"), h((req) =>
  all("SELECT * FROM scheduled_prices WHERE tenant_id=? AND status='pending' ORDER BY effective_at", tid(req)).map((r) => ({ ...r, prices: JSON.parse(r.prices) }))));
operations.post("/prices/schedule", requirePerm("prices.update"), h((req) => {
  const b = parse(priceBody.extend({ effective_at: z.string().datetime({ offset: true }) }), req.body);
  if (Date.parse(b.effective_at) < Date.now() + 60_000) throw new AppError(400, "Choose a future time for the price to take effect");
  const { id } = run("INSERT INTO scheduled_prices (tenant_id,prices,broadcast,note,effective_at,status,created_by,created_at) VALUES (?,?,?,?,?,'pending',?,?)",
    tid(req), JSON.stringify(b.prices), b.broadcast ? 1 : 0, b.note ?? null, new Date(b.effective_at).toISOString(), req.user!.name, now());
  return get("SELECT * FROM scheduled_prices WHERE id=?", id);
}));
operations.delete("/prices/scheduled/:id", requirePerm("prices.update"), h((req) => {
  run("UPDATE scheduled_prices SET status='cancelled' WHERE id=? AND tenant_id=? AND status='pending'", Number(req.params.id), tid(req));
  return { ok: true };
}));
/** Apply every scheduled price change that is now due (called by the automation job). */
export async function applyDuePrices(t: number) {
  const due = all("SELECT * FROM scheduled_prices WHERE tenant_id=? AND status='pending' AND effective_at <= ? ORDER BY effective_at", t, now());
  let n = 0;
  for (const s of due) {
    await applyPrices(t, { id: 0, name: `Scheduled (${s.created_by ?? "owner"})` }, { prices: JSON.parse(s.prices), broadcast: Boolean(s.broadcast), note: s.note ?? undefined });
    run("UPDATE scheduled_prices SET status='applied', applied_at=? WHERE id=?", now(), s.id);
    n++;
  }
  return n;
}
/** Approve or reject a manager's price change (from the app, or the owner's "1" on WhatsApp). */
export async function decidePriceRequest(t: number, id: number, approve: boolean, by: string) {
  const r = get("SELECT * FROM price_requests WHERE id=? AND tenant_id=?", id, t);
  if (!r) throw new AppError(404, "Request not found");
  if (r.status !== "pending") throw new AppError(400, `Already ${r.status}`);
  run("UPDATE price_requests SET status=?, decided_by=?, decided_at=? WHERE id=?", approve ? "approved" : "rejected", by, now(), r.id);
  closeApproval("price", r.id, approve, by);
  const asker = get("SELECT id, phone FROM users WHERE id=?", r.requested_by_id);
  if (asker) await notify(t, [asker], { type: "price_request_decision", whatsapp: false, title: `Price change ${approve ? "approved" : "rejected"} by ${by}`, body: "" });
  return approve ? applyPrices(t, { id: r.requested_by_id, name: `${r.requested_by} (approved by ${by})` }, { prices: JSON.parse(r.prices), broadcast: Boolean(r.broadcast), note: r.note ?? undefined }) : { ok: true };
}
registerDecider("price", async (t, id, approve, by) => { await decidePriceRequest(t, id, approve, by); return approve ? "New prices are live; salesmen have been told." : "Prices stay the same."; });
operations.post("/price-requests/:id/:decision(approve|reject)", requirePerm("settings.manage"), h((req) =>
  decidePriceRequest(tid(req), Number(req.params.id), req.params.decision === "approve", req.user!.name)));

/* ---------------- Competitor prices (noted by hand, to compare with ours) ---------------- */
operations.get("/competitors", requirePerm("prices.view"), h((req) => {
  const t = tid(req);
  const latest = all(`SELECT cp.* FROM competitor_prices cp WHERE cp.tenant_id=? AND cp.id = (
      SELECT id FROM competitor_prices c2 WHERE c2.tenant_id=cp.tenant_id AND c2.name=cp.name AND c2.product=cp.product ORDER BY c2.noted_on DESC, c2.id DESC LIMIT 1)
    ORDER BY cp.name, cp.product`, t);
  const ours = Object.fromEntries(Object.entries(currentPrices(t)).map(([k, v]) => [k, v.price]));
  return { ours, competitors: latest };
}));
operations.post("/competitors", requirePerm("prices.update"), h((req) => {
  const b = parse(z.object({ name: z.string().min(1).max(60), product: productSchema(), price: z.number().positive(), noted_on: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional() }), req.body);
  const { id } = run("INSERT INTO competitor_prices (tenant_id,name,product,price,noted_on,created_by,created_at) VALUES (?,?,?,?,?,?,?)",
    tid(req), b.name.trim(), b.product, b.price, b.noted_on ?? pkDate(), req.user!.id, now());
  return get("SELECT * FROM competitor_prices WHERE id=?", id);
}));
operations.delete("/competitors/:id", requirePerm("prices.update"), h((req) => {
  run("DELETE FROM competitor_prices WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  return { ok: true };
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
    /** a fixed rupee discount for a khata customer (the khata is billed the net) */
    discount: z.number().min(0).optional(),
    /** when the sale was made on a tablet without internet; billed at the price in force then */
    offline_at: z.string().datetime({ offset: true }).nullable().optional(),
  }), req.body);
  if (!([...SPECIAL_METHODS] as string[]).includes(b.payment_method) && !moneyMethods(tid(req)).includes(b.payment_method))
    throw new AppError(400, `Unknown payment method: ${b.payment_method} — add it in Settings → Lists`);
  b.station_id = scopedStation(req, b.station_id)!;
  // a khata discount above the salesman's limit needs a manager / CEO (checked early, before the shift lookup)
  if (b.discount && b.discount > 0) {
    const max = Number(getSetting(tid(req), "khata_discount_max", "500"));
    if (b.discount > max && !can(req.user, "customers.edit"))
      throw new AppError(403, `Discount above ${pkr(max)} needs the manager / CEO. Ask them to enter it.`);
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
      ? get("SELECT id, status FROM shifts WHERE station_id=? AND attendant=? AND opened_at<=? ORDER BY id DESC LIMIT 1", b.station_id, req.user!.name, at)
      : get("SELECT id, status FROM shifts WHERE station_id=? AND status='open' AND attendant=? ORDER BY id DESC LIMIT 1", b.station_id, req.user!.name)
    : get("SELECT id, status FROM shifts WHERE station_id=? AND status='open' ORDER BY id DESC LIMIT 1", b.station_id);
  if (salesman && !shift) throw new AppError(400, "Start your shift first (Shifts page) before recording sales");
  if (shift && shift.status !== "open") throw new AppError(409, "This shift is already closed; its litres were counted from the meter. Tell the manager about this sale.");
  if (salesman && !at && get("SELECT id FROM notifications WHERE user_id=? AND type='price_change' AND acked_at IS NULL LIMIT 1", req.user!.id))
    throw new AppError(409, "Fuel price has changed. Update the dispenser and confirm the new price first.");
  if (b.override_limit && !can(req.user, "customers.edit")) throw new AppError(403, "Only a manager can allow more than the vehicle's daily limit");
  if (b.account_id && !get("SELECT id FROM bank_accounts WHERE id=? AND tenant_id=? AND active=1", b.account_id, tid(req))) throw new AppError(400, "Bank account not found");
  const { offline_at: _o, ...sale } = b;
  const saved = recordSale(tid(req), { ...sale, shift_id: shift?.id ?? null, created_by: req.user!.id, ...(at ? { created_at: at } : {}) });
  if (!saved.duplicate) setImmediate(() => {
    khataFillReceipt(tid(req), saved).catch((e) => console.error("[khata receipt]", e.message));
    walletAfterFill(tid(req), saved).catch((e) => console.error("[wallet]", e.message));
    askRating(tid(req), saved).catch((e) => console.error("[rating]", e.message));
  });
  return { ...saved, receipt_url: receiptUrl(tid(req), "f", saved.id) };
}));

/** Attach the khata slip photo afterwards (the salesman took it after the rush): to the sale and its khata entry. */
operations.post("/sales/:id/slip-photo", requirePerm("sales.create"), h((req) => {
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
operations.post("/sales/:id/undo", requirePerm("sales.create"), h((req) => {
  const sale = get(`SELECT s.*, sh.status shift_status FROM sales s JOIN stations st ON st.id=s.station_id LEFT JOIN shifts sh ON sh.id=s.shift_id
    WHERE s.id=? AND st.tenant_id=?`, Number(req.params.id), tid(req));
  if (!sale) throw new AppError(404, "Sale not found");
  const salesman = req.user!.role === "salesman";
  if (salesman && sale.created_by !== req.user!.id) throw new AppError(403, "You can only undo your own sales");
  if (salesman && Date.now() - Date.parse(sale.created_at) > UNDO_SECONDS * 1000) throw new AppError(400, "Undo time (2 minutes) has passed. Ask the manager.");
  if (sale.shift_id && sale.shift_status !== "open") throw new AppError(400, "The shift is closed; this sale can no longer be undone");
  if (!sale.created_by) throw new AppError(400, "Meter settlement entries cannot be undone");
  if (sale.shift_id && get("SELECT id FROM meter_readings WHERE shift_id=? AND checkpoint IS NOT NULL AND checkpoint_at > ? LIMIT 1", sale.shift_id, sale.created_at))
    throw new AppError(400, "Prices changed after this sale and the meters were settled; ask the manager to adjust instead");
  undoSale(sale);
  audit(tid(req), req.user!, "sale_undo", `SALE-${sale.id}`, { product: sale.product, litres: sale.litres, amount: sale.amount, payment: sale.payment_method, customer_id: sale.customer_id });
  return { ok: true, undone: sale.id, summary: sale.shift_id ? shiftSummary(sale.shift_id) : null };
}));

/* ---------------- Khata "card pending" holds ---------------- */
/**
 * Fuel given to a khata customer on trust: no rate is locked and no debt is raised until the card / parchi is
 * brought in (6–10 days later), when it is billed at that day's pump rate. These endpoints list what is still
 * waiting and clear one or many at once. Owner / manager / cashier only.
 */
operations.get("/sales/pending", requirePerm("khata.clear_pending"), h((req) => {
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

operations.post("/sales/clear", requirePerm("khata.clear_pending"), h((req) => {
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

/* ---------------- Shifts ---------------- */
operations.get("/shifts", requirePerm("shifts.manage"), h((req) => all(
  `SELECT sh.*, st.name station_name FROM shifts sh JOIN stations st ON st.id=sh.station_id WHERE st.tenant_id=?
   ${req.user!.role === "salesman" ? "AND sh.station_id=" + Number(scopedStation(req)) + " AND sh.attendant=?" : "AND ?=?"} ORDER BY sh.id DESC LIMIT 60`,
  tid(req), ...(req.user!.role === "salesman" ? [req.user!.name] : [1, 1]),
).map((s) => ({ ...s, readings: all(`SELECT r.*, ${METER} label, n.meter_no FROM meter_readings r JOIN nozzles n ON n.id=r.nozzle_id WHERE r.shift_id=? ORDER BY n.meter_no`, s.id) }))));

/** Nozzles that are not part of another open shift. */
const busyNozzles = (stationId: number) => new Set(all(
  `SELECT r.nozzle_id FROM meter_readings r JOIN shifts sh ON sh.id=r.shift_id WHERE sh.station_id=? AND sh.status='open'`, stationId).map((r) => r.nozzle_id));

/** Handover sheet for starting a shift: each nozzle's last closing reading and who handed it over. */
operations.get("/shifts/handover", requirePerm("shifts.manage"), h((req) => {
  const stationId = req.user!.role === "salesman" ? scopedStation(req)! : Number(req.query.station_id) || get("SELECT id FROM stations WHERE tenant_id=? ORDER BY id LIMIT 1", tid(req))!.id;
  ownStation(tid(req), stationId);
  const busy = busyNozzles(stationId);
  return {
    station_id: stationId,
    nozzles: all("SELECT n.*, t.product, t.name tank FROM nozzles n JOIN tanks t ON t.id=n.tank_id WHERE n.station_id=? AND n.active=1 ORDER BY n.meter_no, n.id", stationId).map((n) => {
      const last = get(`SELECT sh.attendant, sh.closed_at, r.closing FROM meter_readings r JOIN shifts sh ON sh.id=r.shift_id
        WHERE r.nozzle_id=? AND sh.status='closed' AND r.closing IS NOT NULL ORDER BY sh.closed_at DESC LIMIT 1`, n.id);
      return { nozzle_id: n.id, meter_no: n.meter_no, code: n.label, label: meterName(n as any), product: n.product, tank: n.tank, last_reading: n.totalizer, handed_over_by: last?.attendant ?? null, handed_over_at: last?.closed_at ?? null, busy: busy.has(n.id) };
    }),
  };
}));

/**
 * Start a shift. The salesman writes the meter reading of each nozzle they will run (the previous shift's
 * closing reading is the starting point). If a meter moved between shifts, those litres left the tank
 * without a sale: stock is reduced and managers are alerted.
 */
operations.post("/shifts/open", requirePerm("shifts.manage"), h(async (req) => {
  const b = parse(z.object({ station_id: z.number().optional(), attendant: z.string().min(2).optional(), readings: z.record(z.string(), z.number().min(0)).optional(), photo_ids: z.array(z.number()).max(20).optional() }), req.body);
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
  linkPhotos(tid(req), b.photo_ids, `shift-open:${shift.id}`);
  // attendance is NOT marked by opening a shift: it needs the salesman's own live selfie + location
  const att = get("SELECT id FROM users WHERE tenant_id=? AND name=? AND role='salesman' AND active=1", tid(req), shift.attendant);
  const notCheckedIn = Boolean(att && !get("SELECT id FROM attendance WHERE user_id=? AND day=?", att.id, pkDate()));
  return { ...shift, nozzles: chosen.length, handover_gaps: gaps, attendance_missing: notCheckedIn };
}));

/** Expenses paid from the shift's cash (tea, generator diesel, small repairs...). */
operations.get("/shifts/expense-categories", requirePerm("shifts.expenses"), h((req) =>
  all("SELECT name FROM expense_categories WHERE tenant_id=? ORDER BY name", tid(req)).map((c) => c.name)));

operations.post("/shifts/:id/expenses", requirePerm("shifts.expenses"), h(async (req) => {
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

operations.delete("/shifts/:id/expenses/:eid", requirePerm("shifts.expenses"), h((req) => {
  const shift = ownOpenShift(req, Number(req.params.id));
  if (shift.status !== "open") throw new AppError(400, "Shift is closed");
  run("DELETE FROM expenses WHERE id=? AND shift_id=?", Number(req.params.eid), shift.id);
  return { summary: shiftSummary(shift.id) };
}));

/** Full shift report: meters, litres at each rate, khata accounts, digital, expenses, cash to hand over. */
operations.get("/shifts/:id/report", requirePerm("shifts.manage"), h((req) => {
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
operations.get("/shifts/:id/live", requirePerm("shifts.manage"), h((req) => {
  const shift = ownOpenShift(req, Number(req.params.id));
  return {
    shift, hours_open: round2((Date.parse(shift.closed_at ?? now()) - Date.parse(shift.opened_at)) / 3600_000),
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
    // the full picture per fuel, from what is now saved
    const fuels = shiftFuels(shift.id, (p) => prices[p]?.price ?? null);
    const sold = all(`SELECT payment_method m, ROUND(SUM(amount),2) a FROM sales WHERE shift_id=? GROUP BY payment_method`, shift.id);
    const summary = shiftSummary(shift.id);
    return { fuels, summary, cash_expected: summary.cash_expected, digital_by_method: Object.fromEntries(DIGITAL.map((m) => [m, round2(sold.filter((x) => x.m === m).reduce((a, x) => a + x.a, 0))])) };
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

/** What closing with these readings would give — nothing is saved. */
operations.post("/shifts/:id/preview", requirePerm("shifts.manage"), h((req) => {
  const shift = ownOpenShift(req, Number(req.params.id));
  if (shift.status !== "open") throw new AppError(400, "Shift is not open");
  return closeShiftCore(req, shift, parse(closeBody, req.body), true);
}));

operations.post("/shifts/:id/close", requirePerm("shifts.manage"), h(async (req) => {
  const b = parse(closeBody.extend({ cash_actual: z.number().min(0) }), req.body);
  const t = tid(req);
  const shift = ownOpenShift(req, Number(req.params.id));
  if (shift.status !== "open") throw new AppError(400, "Shift is not open");
  const result = closeShiftCore(req, shift, b, false);
  linkPhotos(t, b.photo_ids, `shift-close:${shift.id}`);
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
  return { ...closed, summary, fuels: result.fuels, readings: shiftReadings(shift.id), checkout_missing: notCheckedOut };
}));

/* ---------------- Stock: dips & deliveries ---------------- */
operations.get("/stock", requirePerm("stock.manage"), h((req) => ({
  dips: all(`SELECT d.*, ${proofCol("'dip:'||d.id")}, t.name tank, t.product, s.name station FROM dip_readings d JOIN tanks t ON t.id=d.tank_id JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? ORDER BY d.id DESC LIMIT 50`, tid(req)),
  deliveries: all(`SELECT d.*, t.name tank, t.product, s.name station FROM deliveries d JOIN tanks t ON t.id=d.tank_id JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? ORDER BY d.id DESC LIMIT 50`, tid(req)),
})));

operations.post("/stock/dip", requirePerm("stock.manage"), h((req) => {
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

operations.post("/stock/delivery", requirePerm("stock.manage"), h((req) => {
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
