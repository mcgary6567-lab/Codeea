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

/** Fuel prices: set, schedule, approve, and competitor prices noted by hand. */
export const priceRoutes = Router();

/* ---------------- Prices ---------------- */
priceRoutes.get("/prices", requirePerm("prices.view"), h((req) => {
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
priceRoutes.post("/prices", requirePerm("prices.update"), h(async (req) => {
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

priceRoutes.get("/price-requests", requirePerm("prices.update"), h((req) =>
  all("SELECT * FROM price_requests WHERE tenant_id=? ORDER BY id DESC LIMIT 20", tid(req)).map((r) => ({ ...r, prices: JSON.parse(r.prices) }))));

/* ---------------- Scheduled (future) price changes ---------------- */
priceRoutes.get("/prices/scheduled", requirePerm("prices.update"), h((req) =>
  all("SELECT * FROM scheduled_prices WHERE tenant_id=? AND status='pending' ORDER BY effective_at", tid(req)).map((r) => ({ ...r, prices: JSON.parse(r.prices) }))));
priceRoutes.post("/prices/schedule", requirePerm("prices.update"), h((req) => {
  const b = parse(priceBody.extend({ effective_at: z.string().datetime({ offset: true }) }), req.body);
  if (Date.parse(b.effective_at) < Date.now() + 60_000) throw new AppError(400, "Choose a future time for the price to take effect");
  const { id } = run("INSERT INTO scheduled_prices (tenant_id,prices,broadcast,note,effective_at,status,created_by,created_at) VALUES (?,?,?,?,?,'pending',?,?)",
    tid(req), JSON.stringify(b.prices), b.broadcast ? 1 : 0, b.note ?? null, new Date(b.effective_at).toISOString(), req.user!.name, now());
  return get("SELECT * FROM scheduled_prices WHERE id=?", id);
}));
priceRoutes.delete("/prices/scheduled/:id", requirePerm("prices.update"), h((req) => {
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
priceRoutes.post("/price-requests/:id/:decision(approve|reject)", requirePerm("settings.manage"), h((req) =>
  decidePriceRequest(tid(req), Number(req.params.id), req.params.decision === "approve", req.user!.name)));

/* ---------------- Competitor prices (noted by hand, to compare with ours) ---------------- */
priceRoutes.get("/competitors", requirePerm("prices.view"), h((req) => {
  const t = tid(req);
  const latest = all(`SELECT cp.* FROM competitor_prices cp WHERE cp.tenant_id=? AND cp.id = (
      SELECT id FROM competitor_prices c2 WHERE c2.tenant_id=cp.tenant_id AND c2.name=cp.name AND c2.product=cp.product ORDER BY c2.noted_on DESC, c2.id DESC LIMIT 1)
    ORDER BY cp.name, cp.product`, t);
  const ours = Object.fromEntries(Object.entries(currentPrices(t)).map(([k, v]) => [k, v.price]));
  return { ours, competitors: latest };
}));
priceRoutes.post("/competitors", requirePerm("prices.update"), h((req) => {
  const b = parse(z.object({ name: z.string().min(1).max(60), product: productSchema(), price: z.number().positive(), noted_on: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional() }), req.body);
  const { id } = run("INSERT INTO competitor_prices (tenant_id,name,product,price,noted_on,created_by,created_at) VALUES (?,?,?,?,?,?,?)",
    tid(req), b.name.trim(), b.product, b.price, b.noted_on ?? pkDate(), req.user!.id, now());
  return get("SELECT * FROM competitor_prices WHERE id=?", id);
}));
priceRoutes.delete("/competitors/:id", requirePerm("prices.update"), h((req) => {
  run("DELETE FROM competitor_prices WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  return { ok: true };
}));

