/**
 * Bypass delivery: buy fuel from one or more suppliers at the depot and deliver it straight to wholesale clients.
 *
 *  - The fuel never enters our tanks. Each client drop is billed at the client's own rate (their wholesale khata).
 *  - Each supplier we lift from keeps its OWN "bypass" account — kept apart from the pump-stock supplier payable —
 *    with its own statement. We buy at a cost, so the margin (client rate − our cost) is our profit.
 *  - We can never deliver more litres than we purchased (per fuel).
 *  - What we owe the supplier is settled three ways: we pay them, the client pays them direct, or the client sends
 *    it to us and we forward it on.  (client-direct / through-us land in a later step.)
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, tx, now, pkDate, pkStart, pkEnd } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { AppError, round2, audit } from "../services.js";
import { productSchema } from "../products.js";
import { PRODUCTS } from "../config.js";
import { linkPhotos, proofPhotos, proofCol } from "./capture.js";
import { bankAccountFor, accountIdField } from "./banks.js";
import { insertTxn, fleet, priceSupply, clientDue } from "./wholesale.js";
import { BYPASS_PAY } from "./banks.js";
import { deliverOrder } from "./wholesaleDesk.js";
import { profile } from "./setup.js";
import { SOCIALS } from "../brandPrint.js";

export const bypass = Router();
bypass.use("/bypass", requirePerm("wholesale.view"));
const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const product = productSchema();

/** What we still owe a supplier on bypass dealings (purchases − payments), kept separate from the pump-stock payable. */
export function bypassOwed(supplierId: number, before?: string): number {
  const pur = get(`SELECT COALESCE(SUM(amount),0) v FROM bypass_purchases WHERE supplier_id=? AND voided=0 ${before ? "AND txn_date < ?" : ""}`,
    ...(before ? [supplierId, before] : [supplierId]))!.v as number;
  const pay = get(`SELECT COALESCE(SUM(amount),0) v FROM bypass_supplier_payments WHERE supplier_id=? AND voided=0 ${before ? "AND txn_date < ?" : ""}`,
    ...(before ? [supplierId, before] : [supplierId]))!.v as number;
  return round2(pur - pay);
}
/** Total bypass fuel cost DELIVERED in a period (its share of the stock) — counted against the bypass sales for profit. */
export function bypassCost(tenantId: number, fromIso: string, toIso?: string): number {
  const r = get(`SELECT COALESCE(SUM(cost_amount),0) v FROM bypass_drops WHERE tenant_id=? AND voided=0 AND tank_id IS NULL AND txn_date >= ?${toIso ? " AND txn_date < ?" : ""}`,
    ...(toIso ? [tenantId, fromIso, toIso] : [tenantId, fromIso]))!;
  return round2(r.v as number);
}
/** Bypass stock still on hand (bought but not yet delivered) — litres and value, pooled per fuel. */
export function bypassStock(tenantId: number, product?: string) {
  const w = product ? " AND product=?" : "";
  const p = product ? [tenantId, product] : [tenantId];
  const inL = get(`SELECT COALESCE(SUM(litres),0) l, COALESCE(SUM(amount),0) v FROM bypass_purchases WHERE tenant_id=? AND voided=0${w}`, ...p)!;
  const out = get(`SELECT COALESCE(SUM(litres),0) l, COALESCE(SUM(cost_amount),0) v FROM bypass_drops WHERE tenant_id=? AND voided=0${w}`, ...p)!;
  const litres = round2((inL.l as number) - (out.l as number));
  const value = round2((inL.v as number) - (out.v as number));
  return { litres, value, avg_cost: litres > 0.001 ? round2(value / litres) : 0 };
}
/** Total value of all bypass stock on hand (the "Bypass stock" asset). */
export function bypassStockValue(tenantId: number): number { return bypassStock(tenantId).value; }

/* ================= Suppliers we lift bypass fuel from ================= */
bypass.get("/bypass/suppliers", h((req) => {
  const t = tid(req);
  return all(`SELECT s.id, s.name, s.phone, s.depot_id, s.company, d.name depot_name FROM suppliers s LEFT JOIN depots d ON d.id=s.depot_id
    WHERE s.tenant_id=? AND COALESCE(s.active,1)=1 ORDER BY COALESCE(d.name,''), s.company, s.name`, t)
    .map((s) => ({ ...s, bypass_owed: bypassOwed(s.id) }));
}));

/** Move bypass stock into OUR own tank inside a tx: a tank-side bypass_drop + a stock-register delivery + fill the tank.
 *  Caller must have validated the tank, its capacity and that the stock is available. No new supplier bill — the bypass purchase already covers it. */
function unloadToTank(t: number, by: string, a: { tank: any; product: string; litres: number; unit: number; amount?: number | null; delivery_id: number | null; note: string | null; ts: string; tanker_no?: string | null }) {
  // the value (cost) of the fuel going into our tank: a typed amount wins, else litres × the bypass average cost
  const litres = round2(a.litres), cost = a.amount != null ? round2(a.amount) : round2(litres * a.unit);
  const unit = litres > 0 ? round2(cost / litres) : round2(a.unit);
  const sup = get("SELECT s.id, s.name FROM bypass_purchases p JOIN suppliers s ON s.id=p.supplier_id WHERE p.tenant_id=? AND p.product=? AND p.voided=0 ORDER BY p.id DESC LIMIT 1", t, a.product);
  const drop = run(`INSERT INTO bypass_drops (tenant_id,delivery_id,wtx_id,client_id,product,litres,unit_cost,cost_amount,created_by,txn_date,created_at,tank_id,note)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`, t, a.delivery_id ?? null, null, null, a.product, litres, unit, cost, by, a.ts, now(), a.tank.id, a.note ?? null).id;
  const del = run("INSERT INTO deliveries (tank_id,supplier,supplier_id,purchase_rate,tanker_no,invoice_l,received_l,shortage_pct,freight,created_at,bypass_drop_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
    a.tank.id, `${sup?.name ?? "Bypass"} (bypass leftover)`, sup?.id ?? null, unit, a.tanker_no ?? null, litres, litres, 0, null, a.ts, drop).id;
  run("UPDATE bypass_drops SET to_delivery_id=? WHERE id=?", del, drop);
  run("UPDATE tanks SET current_l = current_l + ? WHERE id=?", litres, a.tank.id);
  return { drop, del, cost };
}

/* ================= Create a bypass delivery ================= */
bypass.post("/bypass/deliveries", requirePerm("wholesale.manage"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({
    station_id: z.number().int(), txn_date: day.optional(), note: z.string().max(200).optional().nullable(), photo_ids: proofPhotos,
    tanker_id: z.number().int().optional().nullable(), driver_id: z.number().int().optional().nullable(), vehicle_no: z.string().max(30).optional().nullable(),
    // one tanker can't be at two depots at once: when fuel is lifted from 2+ places, each tanker carries its own driver
    tankers: z.array(z.object({ tanker_id: z.number().int().optional().nullable(), driver_id: z.number().int().optional().nullable(), vehicle_no: z.string().max(30).optional().nullable() })).max(10).optional(),
    // what we bought now (optional — can buy only, to sell from stock later). Enter the full cost amount, or a per-litre rate.
    purchases: z.array(z.object({ supplier_id: z.number().int(), product, litres: z.number().positive().max(200_000), cost_rate: z.number().positive().optional(), amount: z.number().positive().optional(), ref: z.string().max(60).optional().nullable() })).default([]),
    // where it was dropped (optional — can deliver from the held stock with no fresh purchase). Enter the full sale amount, or a per-litre rate.
    drops: z.array(z.object({ client_id: z.number().int(), product: product.optional(), litres: z.number().positive(), rate: z.number().positive().optional(), amount: z.number().positive().optional(), location: z.string().max(120).optional().nullable(), ref: z.string().max(60).optional().nullable(), order_id: z.number().int().optional().nullable(), override_limit: z.boolean().optional() })).max(30).default([]),
    // some of the load emptied into OUR own tank (not sold to a client) — bypass stock → pump tank at its average cost, no client bill
    to_tank: z.array(z.object({ tank_id: z.number().int(), product: product.optional(), litres: z.number().positive().max(200_000), amount: z.number().positive().max(100_000_000).optional(), note: z.string().max(200).optional().nullable() })).max(10).default([]),
  }), req.body);
  if (!b.purchases.length && !b.drops.length && !b.to_tank.length) throw new AppError(400, "Add a purchase, a drop, or an own-tank unload");
  if (!get("SELECT id FROM stations WHERE id=? AND tenant_id=?", b.station_id, t)) throw new AppError(400, "Station not found");
  for (const p of b.purchases) if (!get("SELECT id FROM suppliers WHERE id=? AND tenant_id=?", p.supplier_id, t)) throw new AppError(400, "Supplier not found");
  // resolve each purchase's amount + rate (whichever was entered)
  const purchases = b.purchases.map((p) => {
    if (p.amount == null && p.cost_rate == null) throw new AppError(400, "Har khareed par amount (ya rate) daalein");
    const amount = p.amount != null ? round2(p.amount) : round2(p.litres * p.cost_rate!);
    const cost_rate = p.cost_rate != null ? round2(p.cost_rate) : round2(amount / p.litres);
    return { ...p, amount, cost_rate };
  });
  const defProd = b.purchases[0]?.product ?? b.drops[0]?.product ?? "HSD";

  // ---- litre cap: per fuel, total delivered (ever) can never exceed total bought (ever) ----
  // after this delivery's purchases are added, the drops must fit in the available bypass stock
  const byProd = (rows: { product?: string; litres: number }[]) => {
    const m: Record<string, number> = {};
    for (const r of rows) { const p = r.product ?? defProd; m[p] = round2((m[p] ?? 0) + r.litres); }
    return m;
  };
  const buyProd = byProd(purchases);
  const dropProd = byProd(b.drops);
  // own-tank unloads: resolve + validate each tank (exists, right fuel, capacity)
  const tankRows = b.to_tank.map((r) => {
    const prod = r.product ?? defProd;
    const tank = get("SELECT tk.*, s.tenant_id FROM tanks tk JOIN stations s ON s.id=tk.station_id WHERE tk.id=?", r.tank_id);
    if (!tank || tank.tenant_id !== t) throw new AppError(400, "Tank not found");
    if (tank.product !== prod) throw new AppError(400, `${tank.name} is a ${PRODUCTS[tank.product] ?? tank.product} tank — choose the ${PRODUCTS[prod] ?? prod} tank`);
    return { ...r, product: prod, litres: round2(r.litres), amount: r.amount != null ? round2(r.amount) : null, tank };
  });
  const tankProd = byProd(tankRows);
  const capAdd: Record<number, number> = {};
  for (const r of tankRows) capAdd[r.tank.id] = round2((capAdd[r.tank.id] ?? 0) + r.litres);
  for (const r of tankRows) if (r.tank.current_l + capAdd[r.tank.id] > r.tank.capacity_l * 1.001) throw new AppError(400, `${r.tank.name} would go over its capacity (${Math.round(r.tank.capacity_l).toLocaleString()} L)`);
  // cap: per fuel, client drops + own-tank unloads together cannot exceed held stock + what is bought now
  const need: Record<string, number> = {};
  for (const [p, l] of Object.entries(dropProd)) need[p] = round2((need[p] ?? 0) + l);
  for (const [p, l] of Object.entries(tankProd)) need[p] = round2((need[p] ?? 0) + l);
  for (const [p, l] of Object.entries(need)) {
    const available = round2(bypassStock(t, p).litres + (buyProd[p] ?? 0));
    if (l > available + 0.01) throw new AppError(400, `${PRODUCTS[p] ?? p}: only ${available} L bypass stock is available (held + bought now) but drops + own-tank add up to ${l} L — you cannot move more than you have.`);
  }
  // one or more tanker+driver pairs carried the load (2 depots → 2 tankers, each with its own driver)
  const fleetsIn = b.tankers?.length ? b.tankers : [{ tanker_id: b.tanker_id, driver_id: b.driver_id, vehicle_no: b.vehicle_no }];
  const resolved = fleetsIn.map((x) => fleet(t, x));
  const fl = resolved[0];
  const total = round2(b.drops.reduce((a, d) => a + d.litres, 0));
  // capacity check only when every tanker's size is known: the drops must fit in the tankers together
  const caps = resolved.map((r) => r.tanker?.capacity_l).filter((c): c is number => !!c);
  const totalCap = caps.reduce((a, c) => a + c, 0);
  if (caps.length === resolved.length && totalCap > 0 && total > totalCap)
    throw new AppError(400, `The tanker(s) hold ${Math.round(totalCap).toLocaleString()} L together — the drops add up to ${total.toLocaleString()} L`);
  const tankersJson = JSON.stringify(resolved.map((r) => ({ tanker_id: r.tanker_id, driver_id: r.driver_id, vehicle_no: r.vehicle_no, driver_name: r.driver_name })));
  // price every drop at the client's rate first (so a bad drop saves nothing)
  const added: Record<number, number> = {};
  const priced = b.drops.map((d) => {
    const prod = d.product ?? defProd;
    // a bypass sale can be entered as a full amount or a rate; the deal is negotiated so any rate is allowed
    const rate = d.amount != null ? round2(d.amount / d.litres) : d.rate;
    const p = priceSupply(req, d.client_id, { product: prod, litres: d.litres, rate, amount: d.amount ?? undefined, override_limit: d.override_limit, allowAnyRate: true }, added[d.client_id] ?? 0);
    added[d.client_id] = (added[d.client_id] ?? 0) + p.amount;
    return { ...d, ...p, product: prod };
  });
  const ts = b.txn_date ? new Date(`${b.txn_date}T12:00:00+05:00`).toISOString() : now();
  const by = req.user!.name;
  const out = tx(() => {
    const { id } = run(`INSERT INTO bypass_deliveries (tenant_id,station_id,tanker_id,driver_id,vehicle_no,driver_name,tankers,note,created_by,txn_date,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?)`, t, b.station_id, fl.tanker_id, fl.driver_id, fl.vehicle_no, fl.driver_name, tankersJson, b.note ?? null, by, ts, now());
    if (b.photo_ids?.length) linkPhotos(t, b.photo_ids, `byp:${id}`);
    // supplier cost lines go INTO bypass stock (asset) — we owe the supplier for all of it
    for (const p of purchases)
      run(`INSERT INTO bypass_purchases (tenant_id,delivery_id,supplier_id,product,litres,cost_rate,amount,ref,note,created_by,txn_date,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`, t, id, p.supplier_id, p.product, round2(p.litres), p.cost_rate, p.amount, p.ref ?? null, `Bypass delivery #${id}`, by, ts, now());
    // the weighted-average cost of each fuel's stock AFTER this delivery's purchases (removing at the average keeps it stable within the batch)
    const avg: Record<string, number> = {};
    for (const p of new Set([...Object.keys(dropProd), ...Object.keys(tankProd)])) avg[p] = bypassStock(t, p).avg_cost;
    // client drops — billed to each client's wholesale khata, and taken out of the bypass stock at its cost
    const drops: any[] = [];
    for (const d of priced) {
      const txn = insertTxn(req, d.c.id, { type: "supply", station_id: b.station_id, tank_id: null, product: d.product, litres: d.litres, rate: d.rate, amount: d.amount,
        ref: d.ref ?? `BYP-${id}`, note: `Bypass delivery #${id}`, location: d.location ?? null, txn_date: b.txn_date, ...fl });
      deliverOrder(t, d.order_id, d.c.id, txn.id);
      const unit = avg[d.product] ?? 0;
      run(`INSERT INTO bypass_drops (tenant_id,delivery_id,wtx_id,client_id,product,litres,unit_cost,cost_amount,created_by,txn_date,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)`, t, id, txn.id, d.c.id, d.product, round2(d.litres), unit, round2(d.litres * unit), by, ts, now());
      drops.push(txn);
    }
    // own-tank unloads (part of the same delivery) — removed from bypass stock at the same batch average cost
    for (const r of tankRows) unloadToTank(t, by, { tank: r.tank, product: r.product, litres: r.litres, unit: avg[r.product] ?? bypassStock(t, r.product).avg_cost, amount: r.amount, delivery_id: id, note: r.note ?? null, ts, tanker_no: fl.vehicle_no });
    return { id, drops };
  });
  const purCost = round2(purchases.reduce((a, p) => a + p.amount, 0));
  const soldCost = round2(get("SELECT COALESCE(SUM(cost_amount),0) v FROM bypass_drops WHERE delivery_id=? AND tank_id IS NULL", out.id)!.v as number);
  const billed = round2(priced.reduce((a, d) => a + d.amount, 0));
  const toTankL = round2(tankRows.reduce((a, r) => a + r.litres, 0));
  return { id: out.id, billed, bought: purCost, sold_cost: soldCost, cost: soldCost, margin: round2(billed - soldCost), to_tank_litres: toTankL, drops: out.drops, stock: bypassStock(t), delivery: get("SELECT * FROM bypass_deliveries WHERE id=?", out.id) };
}));

/* ================= Leftover bypass fuel unloaded into our own tank ================= */
/**
 * The tanker had more than the clients took: the rest is emptied into OUR tank. The litres leave the bypass stock at its
 * average cost (like a drop, but to a tank instead of a client) and enter the pump's stock through a normal stock-register
 * delivery at that cost — no new supplier bill (the bypass purchase already covers it). Ledger: Dr Fuel purchases / Cr Bypass stock.
 */
bypass.post("/bypass/to-tank", requirePerm("wholesale.manage"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({ product, litres: z.number().positive().max(200_000), amount: z.number().positive().max(100_000_000).optional(), tank_id: z.number().int(), delivery_id: z.number().int().optional().nullable(), txn_date: day.optional(), note: z.string().max(200).optional().nullable(), tanker_no: z.string().max(30).optional().nullable() }), req.body);
  const tank = get("SELECT tk.*, s.tenant_id FROM tanks tk JOIN stations s ON s.id=tk.station_id WHERE tk.id=?", b.tank_id);
  if (!tank || tank.tenant_id !== t) throw new AppError(404, "Tank not found");
  if (tank.product !== b.product) throw new AppError(400, `${tank.name} is a ${PRODUCTS[tank.product] ?? tank.product} tank — choose the ${PRODUCTS[b.product] ?? b.product} tank`);
  const st = bypassStock(t, b.product);
  if (b.litres > st.litres + 0.01) throw new AppError(400, `Only ${st.litres} L ${PRODUCTS[b.product] ?? b.product} bypass stock is on hand — cannot unload ${b.litres} L`);
  if (tank.current_l + b.litres > tank.capacity_l * 1.001) throw new AppError(400, `${tank.name} would go over its capacity (${Math.round(tank.capacity_l)} L)`);
  if (b.delivery_id && !get("SELECT id FROM bypass_deliveries WHERE id=? AND tenant_id=?", b.delivery_id, t)) throw new AppError(400, "Bypass delivery not found");
  const ts = b.txn_date ? new Date(`${b.txn_date}T12:00:00+05:00`).toISOString() : now();
  // the value of the fuel put in the tank: a typed amount wins, else litres × the bypass average cost
  const cost = b.amount != null ? round2(b.amount) : round2(b.litres * st.avg_cost);
  const unit = round2(cost / b.litres);
  const out = tx(() => unloadToTank(t, req.user!.name, { tank, product: b.product, litres: b.litres, unit: st.avg_cost, amount: b.amount, delivery_id: b.delivery_id ?? null, note: b.note ?? null, ts, tanker_no: b.tanker_no ?? null }));
  audit(t, req.user!, "bypass_to_tank", `bypdrop:${out.drop}`, { product: b.product, litres: b.litres, tank: tank.name, unit_cost: unit, cost, delivery_id: b.delivery_id ?? null });
  return { ok: true, id: out.drop, delivery_row: out.del, litres: round2(b.litres), unit_cost: unit, cost, tank: get("SELECT id, name, current_l, capacity_l FROM tanks WHERE id=?", tank.id), stock: bypassStock(t, b.product) };
}));
/** Transfers so far (newest first). */
bypass.get("/bypass/to-tank", h((req) => all(`SELECT d.id, d.product, d.litres, d.unit_cost, d.cost_amount, d.voided, d.created_by, d.txn_date, d.note, d.delivery_id, tk.name tank, s.name station
    FROM bypass_drops d JOIN tanks tk ON tk.id=d.tank_id JOIN stations s ON s.id=tk.station_id WHERE d.tenant_id=? AND d.tank_id IS NOT NULL ORDER BY d.id DESC LIMIT 100`, tid(req))));
/** Undo a transfer: litres back to the bypass stock, out of the tank, stock-register row removed. */
bypass.post("/bypass/to-tank/:id/void", requirePerm("wholesale.void"), h((req) => {
  const t = tid(req);
  const { reason } = parse(z.object({ reason: z.string().trim().min(3).max(200) }), req.body);
  const d = get("SELECT * FROM bypass_drops WHERE id=? AND tenant_id=? AND tank_id IS NOT NULL AND voided=0", Number(req.params.id), t);
  if (!d) throw new AppError(404, "Transfer not found");
  const tank = get("SELECT * FROM tanks WHERE id=?", d.tank_id)!;
  if (tank.current_l < d.litres - 0.01) throw new AppError(400, `${tank.name} now holds only ${Math.round(tank.current_l)} L — the ${d.litres} L have already been sold, this cannot be undone`);
  tx(() => {
    run("UPDATE bypass_drops SET voided=1, note=COALESCE(note,'') || ? WHERE id=?", ` [void: ${reason}]`, d.id);
    if (d.to_delivery_id) run("DELETE FROM deliveries WHERE id=?", d.to_delivery_id);
    run("UPDATE tanks SET current_l = current_l - ? WHERE id=?", d.litres, tank.id);
  });
  audit(t, req.user!, "bypass_to_tank_void", `bypdrop:${d.id}`, { reason, litres: d.litres, tank: tank.name });
  return { ok: true, stock: bypassStock(t, d.product) };
}));

/** Void a client drop of a bypass delivery: the litres go back to the bypass stock, the client's bill is voided.
 *  Refused when money already hangs off the client's row (cashier voucher / bypass supplier payment). */
bypass.post("/bypass/drops/:wtxId/void", requirePerm("wholesale.void"), h((req) => {
  const t = tid(req);
  const { reason } = parse(z.object({ reason: z.string().trim().min(3).max(200) }), req.body);
  const d = get("SELECT * FROM bypass_drops WHERE wtx_id=? AND tenant_id=? AND tank_id IS NULL", Number(req.params.wtxId), t);
  if (!d) throw new AppError(404, "Drop nahi mila");
  if (d.voided) throw new AppError(400, "Ye drop pehle hi void hai");
  if (get("SELECT 1 FROM cashier_vouchers WHERE tenant_id=? AND src=? AND voided=0", t, `wtx:${d.wtx_id}`)) throw new AppError(400, "Is drop par cashier voucher bana hai — pehle woh void karein");
  if (get("SELECT 1 FROM bypass_supplier_payments WHERE tenant_id=? AND wtx_id=? AND voided=0", t, d.wtx_id)) throw new AppError(400, "Is drop par bypass supplier payment judi hai — pehle woh void karein");
  tx(() => {
    run("UPDATE bypass_drops SET voided=1, note=COALESCE(note,'') || ? WHERE id=?", ` [void: ${reason}]`, d.id);
    run("UPDATE wholesale_txns SET voided=1, void_reason=? WHERE id=? AND tenant_id=?", `${req.user!.name} (drop void): ${reason}`, d.wtx_id, t);
  });
  audit(t, req.user!, "bypass_drop_void", `bypdrop:${d.id}`, { reason, litres: d.litres, cost: d.cost_amount, client_id: d.client_id, wtx_id: d.wtx_id });
  return { ok: true, stock: bypassStock(t, d.product) };
}));

/** Bypass munafa for a date window: client revenue − delivered cost for drops in [fromIso, toIso). */
export function bypassProfit(tenantId: number, fromIso: string, toIso?: string) {
  const billed = get(`SELECT COALESCE(SUM(w.amount),0) v FROM bypass_drops bd JOIN wholesale_txns w ON w.id=bd.wtx_id
    WHERE bd.tenant_id=? AND bd.voided=0 AND w.voided=0 AND bd.txn_date >= ?${toIso ? " AND bd.txn_date < ?" : ""}`,
    ...(toIso ? [tenantId, fromIso, toIso] : [tenantId, fromIso]))!.v as number;
  const cost = bypassCost(tenantId, fromIso, toIso);
  return { billed: round2(billed), cost, profit: round2(billed - cost) };
}
/** This month's and last month's bypass munafa (client revenue − delivered cost). */
export function bypassMonthProfit(tenantId: number) {
  const ym = pkDate().slice(0, 7); // YYYY-MM in Pakistan time
  const [y, m] = ym.split("-").map(Number);
  const monthStart = new Date(`${ym}-01T00:00:00+05:00`).toISOString();
  const lastYm = m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, "0")}`;
  const lastStart = new Date(`${lastYm}-01T00:00:00+05:00`).toISOString();
  const yearStart = new Date(`${y}-01-01T00:00:00+05:00`).toISOString();
  return {
    ...bypassProfit(tenantId, monthStart),
    last: { ...bypassProfit(tenantId, lastStart, monthStart), month: lastYm },
    year: { ...bypassProfit(tenantId, yearStart), year: y },
  };
}

/** Bypass stock on hand (bought but not yet delivered), per fuel, with its value and average cost. */
bypass.get("/bypass/stock", h((req) => {
  const t = tid(req);
  const prods = all("SELECT DISTINCT product FROM bypass_purchases WHERE tenant_id=? AND voided=0", t).map((r) => r.product as string);
  const byProduct = prods.map((p) => ({ product: p, ...bypassStock(t, p) })).filter((x) => x.litres > 0.01 || x.value > 0.5);
  return { total: bypassStock(t), by_product: byProduct, month: bypassMonthProfit(t) };
}));

/** Recent bypass deliveries. Optional `q` (tanker, client, supplier or product), `from`/`to` (PK days) and `limit` (default 60, max 500). */
const deliveriesQuery = z.object({
  q: z.string().trim().max(80).optional(), from: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(), to: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  limit: z.coerce.number().int().min(1).max(500).default(60),
});
bypass.get("/bypass/deliveries", h((req) => {
  const t = tid(req);
  const qp = parse(deliveriesQuery, { q: req.query.q || undefined, from: req.query.from || undefined, to: req.query.to || undefined, limit: req.query.limit || undefined });
  const where = ["d.tenant_id=?", "d.voided=0"]; const args: (string | number)[] = [t];
  if (qp.from) { where.push("d.txn_date>=?"); args.push(pkStart(qp.from)); }
  if (qp.to) { where.push("d.txn_date<?"); args.push(pkEnd(qp.to)); }
  if (qp.q) {
    const like = `%${qp.q}%`; const prodKeys = Object.entries(PRODUCTS).filter(([k, v]) => `${k} ${v}`.toLowerCase().includes(qp.q!.toLowerCase())).map(([k]) => k);
    where.push(`(d.vehicle_no LIKE ? OR d.driver_name LIKE ? OR COALESCE(d.tankers,'') LIKE ? OR CAST(d.id AS TEXT)=?
      OR EXISTS (SELECT 1 FROM bypass_drops bd JOIN wholesale_clients c ON c.id=bd.client_id WHERE bd.delivery_id=d.id AND (c.name LIKE ? OR c.business_name LIKE ?))
      OR EXISTS (SELECT 1 FROM bypass_purchases bp JOIN suppliers sp ON sp.id=bp.supplier_id WHERE bp.delivery_id=d.id AND sp.name LIKE ?)
      ${prodKeys.length ? `OR EXISTS (SELECT 1 FROM bypass_purchases bp WHERE bp.delivery_id=d.id AND bp.product IN (${prodKeys.map(() => "?").join(",")}))` : ""})`);
    args.push(like, like, like, qp.q.replace(/^#/, ""), like, like, like, ...prodKeys);
  }
  return all(`SELECT d.*, ${proofCol("'byp:'||d.id")}, (SELECT COALESCE(SUM(amount),0) FROM bypass_purchases WHERE delivery_id=d.id AND voided=0) cost,
      (SELECT COALESCE(SUM(litres),0) FROM bypass_purchases WHERE delivery_id=d.id AND voided=0) litres,
      (SELECT COALESCE(SUM(litres),0) FROM bypass_drops WHERE delivery_id=d.id AND voided=0) drop_litres,
      (SELECT COALESCE(SUM(cost_amount),0) FROM bypass_drops WHERE delivery_id=d.id AND voided=0 AND tank_id IS NULL) sold_cost,
      (SELECT COALESCE(SUM(litres),0) FROM bypass_drops WHERE delivery_id=d.id AND voided=0 AND tank_id IS NOT NULL) to_tank_litres,
      (SELECT COALESCE(SUM(w.amount),0) FROM bypass_drops bd JOIN wholesale_txns w ON w.id=bd.wtx_id WHERE bd.delivery_id=d.id AND bd.voided=0 AND w.voided=0) billed
     FROM bypass_deliveries d WHERE ${where.join(" AND ")} ORDER BY d.txn_date DESC, d.id DESC LIMIT ?`, ...args, qp.limit)
    // munafa = client ne jo diya (billed) − us delivery me bike maal ki cost (COGS); stock hold ho to sirf delivered hissa counts
    .map((d) => ({ ...d, margin: round2((d.billed as number) - (d.sold_cost as number)) }));
}));

/**
 * One bypass delivery with everything the printed voucher/challan needs — same shape the pump
 * trip sheet uses, so the shared challan/trip-sheet printer renders it unchanged. Each client's
 * drop carries the per-litre rate from THAT client's own rate card (the rate the CEO set), the
 * amount it made, and the client's balance after the delivery.
 */
bypass.get("/bypass/deliveries/:id", h((req) => {
  const t = tid(req);
  const d = get(`SELECT d.*, ${proofCol("'byp:'||d.id")}, dr.phone driver_phone
    FROM bypass_deliveries d LEFT JOIN drivers dr ON dr.id=d.driver_id WHERE d.id=? AND d.tenant_id=?`, Number(req.params.id), t);
  if (!d) throw new AppError(404, "Delivery not found");
  const drops = all(`SELECT w.id, w.client_id, c.name client_name, c.business_name, c.phone, c.address, bd.product, bd.litres, w.rate, w.amount, w.location, w.ref, bd.voided
    FROM bypass_drops bd JOIN wholesale_txns w ON w.id=bd.wtx_id JOIN wholesale_clients c ON c.id=bd.client_id
    WHERE bd.delivery_id=? ORDER BY bd.id`, d.id);
  // balance owed by each client right now (shown on the voucher so the client sees the running khata)
  for (const x of drops) (x as any).balance = clientDue(x.client_id as number);
  const purchases = all(`SELECT bp.product, bp.litres, bp.cost_rate rate, bp.amount, bp.ref, s.name supplier_name
    FROM bypass_purchases bp JOIN suppliers s ON s.id=bp.supplier_id WHERE bp.delivery_id=? AND bp.voided=0 ORDER BY bp.id`, d.id);
  // the tanker(s) + driver(s) that carried this load (multi-tanker: 2 depots → 2 tankers)
  let tankers: { tanker_no: string | null; driver_name: string | null }[] = [];
  try { tankers = (JSON.parse(d.tankers || "[]") as any[]).map((x) => ({ tanker_no: x.vehicle_no ?? null, driver_name: x.driver_name ?? null })); } catch { /* bad json */ }
  if (!tankers.length && (d.vehicle_no || d.driver_name)) tankers = [{ tanker_no: d.vehicle_no ?? null, driver_name: d.driver_name ?? null }];
  const live = drops.filter((x) => !x.voided);
  const delivered_l = round2(live.reduce((a, x) => a + (x.litres as number), 0));
  const billed = round2(live.reduce((a, x) => a + (x.amount as number), 0));
  // letterhead for the printed papers (same source as the pump trip sheet)
  const biz = profile(t);
  const st = get("SELECT name, address, city FROM stations WHERE id=?", d.station_id)!;
  const business = { name: biz.name, phone: biz.biz_phone || biz.owner_phone, address: biz.place || [st.address, st.city].filter(Boolean).join(", "),
    city: biz.biz_city || st.city, ntn: biz.ntn, strn: biz.strn, logo_url: biz.logo_url, footer: biz.receipt_footer, station: st.name,
    email: biz.biz_email, website: biz.website, omc: biz.omc, color: biz.brand_color, social: Object.fromEntries(SOCIALS.map((x) => [x.key, biz[x.key]]).filter(([, v]) => v)) };
  return { id: d.id, trip_date: d.txn_date, source: "depot", depot_ref: purchases.map((p) => p.ref).filter(Boolean).join(", ") || null,
    supplier_name: [...new Set(purchases.map((p) => p.supplier_name))].join(", ") || null, bypass: true,
    vehicle_no: d.vehicle_no, driver_name: d.driver_name, driver_phone: d.driver_phone, tankers, note: d.note, station_name: st.name,
    drops, purchases, delivered_l, billed, business };
}));

/** Monthly bypass munafa series for the last N months (oldest first) — for the panel chart. */
bypass.get("/bypass/monthly", h((req) => {
  const t = tid(req);
  const months = Math.min(24, Math.max(1, Number(req.query.months) || 6));
  const ym = pkDate().slice(0, 7);
  let [y, m] = ym.split("-").map(Number);
  const out: { month: string; billed: number; cost: number; profit: number }[] = [];
  for (let i = 0; i < months; i++) {
    const mm = `${y}-${String(m).padStart(2, "0")}`;
    const start = new Date(`${mm}-01T00:00:00+05:00`).toISOString();
    const [ny, nm] = m === 12 ? [y + 1, 1] : [y, m + 1];
    const end = new Date(`${ny}-${String(nm).padStart(2, "0")}-01T00:00:00+05:00`).toISOString();
    const billed = get(`SELECT COALESCE(SUM(w.amount),0) v FROM bypass_drops bd JOIN wholesale_txns w ON w.id=bd.wtx_id
      WHERE bd.tenant_id=? AND bd.voided=0 AND w.voided=0 AND bd.txn_date >= ? AND bd.txn_date < ?`, t, start, end)!.v as number;
    const cost = bypassCost(t, start, end);
    out.unshift({ month: mm, billed: round2(billed), cost, profit: round2(billed - cost) });
    if (m === 1) { y -= 1; m = 12; } else m -= 1;
  }
  return out;
}));

/* ================= A supplier's bypass statement ================= */
export function bypassStatement(tenantId: number, supplierId: number, from?: string, to?: string) {
  const s = get("SELECT s.*, d.name depot_name FROM suppliers s LEFT JOIN depots d ON d.id=s.depot_id WHERE s.id=? AND s.tenant_id=?", supplierId, tenantId);
  if (!s) throw new AppError(404, "Supplier not found");
  const fromIso = from ? new Date(`${from}T00:00:00+05:00`).toISOString() : undefined;
  const toIso = to ? new Date(`${to}T23:59:59+05:00`).toISOString() : undefined;
  const opening = fromIso ? bypassOwed(s.id, fromIso) : 0;
  const purchases = all(`SELECT id, 'purchase' kind, txn_date, product, litres, cost_rate, amount, ref, note FROM bypass_purchases
    WHERE supplier_id=? AND voided=0 ${fromIso ? "AND txn_date >= ?" : ""} ${toIso ? "AND txn_date < ?" : ""}`,
    ...[s.id, ...(fromIso ? [fromIso] : []), ...(toIso ? [toIso] : [])]);
  const payments = all(`SELECT p.id, 'payment' kind, p.txn_date, p.amount, p.mode, p.method, p.ref, p.note, c.name client_name, ${proofCol("'byp:pay:'||p.id")} FROM bypass_supplier_payments p
    LEFT JOIN wholesale_clients c ON c.id=p.client_id WHERE p.supplier_id=? AND p.voided=0 ${fromIso ? "AND p.txn_date >= ?" : ""} ${toIso ? "AND p.txn_date < ?" : ""}`,
    ...[s.id, ...(fromIso ? [fromIso] : []), ...(toIso ? [toIso] : [])]);
  const rows = [...purchases, ...payments].sort((a, b) => (a.txn_date < b.txn_date ? -1 : a.txn_date > b.txn_date ? 1 : a.id - b.id));
  let bal = opening;
  const lines = rows.map((r: any) => {
    const debit = r.kind === "purchase" ? r.amount : 0; // we owe more
    const credit = r.kind === "payment" ? r.amount : 0; // we owe less
    bal = round2(bal + debit - credit);
    return { ...r, debit, credit, balance: bal };
  });
  return { supplier: { ...s, bypass_owed: bypassOwed(s.id) }, from: from ?? null, to: to ?? null, opening_balance: opening, closing_balance: bal, lines };
}
bypass.get("/bypass/suppliers/:id/statement", h((req) =>
  bypassStatement(tid(req), Number(req.params.id), req.query.from as string | undefined, req.query.to as string | undefined)));

bypass.get("/bypass/suppliers/:id/statement.csv", (req, res, next) => {
  try {
    const s = bypassStatement(tid(req), Number(req.params.id), req.query.from as string | undefined, req.query.to as string | undefined);
    const name = [s.supplier.company, s.supplier.name].filter(Boolean).join(" — ");
    const esc = (v: unknown) => `"${String(v ?? "").replaceAll('"', '""')}"`;
    const out = [
      ["Bypass supplier statement", name].map(esc).join(","),
      ["Opening balance (we owe)", s.opening_balance].map(esc).join(","),
      ["Date", "Entry", "Product", "Litres", "Cost rate", "Purchased (we owe)", "Paid", "Mode", "Ref", "Note", "Balance"].map(esc).join(","),
      ...s.lines.map((l: any) => [String(l.txn_date).slice(0, 10), l.kind, l.product ?? "", l.litres ?? "", l.cost_rate ?? "", l.debit || "", l.credit || "", l.mode ?? "", l.ref ?? "", l.note ?? "", l.balance].map(esc).join(",")),
      ["Closing balance (we owe)", s.closing_balance].map(esc).join(","),
    ].join("\n");
    res.setHeader("Content-Type", "text/csv; charset=utf-8");
    res.setHeader("Content-Disposition", `attachment; filename="bypass-${String(name).replace(/[^\w-]+/g, "_")}.csv"`);
    res.send("﻿" + out);
  } catch (e) { next(e); }
});

/* ================= Pay a bypass supplier ================= */
bypass.post("/bypass/suppliers/:id/payment", requirePerm("wholesale.manage"), h((req) => {
  const t = tid(req);
  const s = get("SELECT * FROM suppliers WHERE id=? AND tenant_id=?", Number(req.params.id), t);
  if (!s) throw new AppError(404, "Supplier not found");
  const b = parse(z.object({
    amount: z.number().positive().max(1_000_000_000), mode: z.enum(["we_pay", "client_direct", "through_us"]).default("we_pay"),
    method: z.string().max(30).optional().nullable(), account_id: accountIdField, client_id: z.number().int().optional().nullable(),
    ref: z.string().max(60).optional().nullable(), note: z.string().max(200).optional().nullable(), txn_date: day.optional(), photo_ids: proofPhotos,
  }), req.body);
  const amount = round2(b.amount);
  const ts = b.txn_date ? new Date(`${b.txn_date}T12:00:00+05:00`).toISOString() : now();
  // the client legs lower that client's due to us as well — pick the client who paid
  let client: any = null;
  if (b.mode !== "we_pay") {
    if (!b.client_id) throw new AppError(400, "Choose the client who paid");
    client = get("SELECT * FROM wholesale_clients WHERE id=? AND tenant_id=?", b.client_id, t);
    if (!client) throw new AppError(400, "Client not found");
    const due = clientDue(client.id);
    if (amount > due + 0.01) throw new AppError(400, `${client.name} only owes ${Math.round(due).toLocaleString("en-PK")} — a bypass settlement cannot be more than the client's due.`);
  }
  const acc = b.mode === "client_direct" ? null : bankAccountFor(t, b.account_id, b.method ?? "bank");
  const out = tx(() => {
    let wtxId: number | null = null;
    if (b.mode === "client_direct") {
      // client paid the supplier direct: no money through us — just lowers the client's due (BYPASS_PAY is booked against the bypass payable)
      wtxId = insertTxn(req, client.id, { type: "payment", amount, method: BYPASS_PAY, ref: b.ref ?? null, note: b.note || `Paid ${s.name} direct (bypass)`, txn_date: b.txn_date }).id as number;
    } else if (b.mode === "through_us") {
      // client sent it to us (a normal receipt into our account) and we forward it on to the supplier (the payment below)
      wtxId = insertTxn(req, client.id, { type: "payment", amount, method: b.method ?? "Bank transfer", account_id: acc, ref: b.ref ?? null, note: b.note || `For ${s.name} (bypass, through us)`, txn_date: b.txn_date }).id as number;
    }
    const { id } = run(`INSERT INTO bypass_supplier_payments (tenant_id,supplier_id,amount,mode,client_id,wtx_id,method,account_id,ref,note,created_by,txn_date,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`, t, s.id, amount, b.mode, client?.id ?? null, wtxId, b.method ?? null, acc ?? null, b.ref ?? null, b.note ?? null, req.user!.name, ts, now());
    if (b.photo_ids?.length) linkPhotos(t, b.photo_ids, `byp:pay:${id}`);
    return id;
  });
  return { payment: get("SELECT * FROM bypass_supplier_payments WHERE id=?", out), bypass_owed: bypassOwed(s.id), client_due: client ? clientDue(client.id) : undefined };
}));

/** Void a bypass purchase or payment (correction). */
bypass.post("/bypass/purchases/:id/void", requirePerm("wholesale.void"), h((req) => {
  const t = tid(req);
  const r = get("SELECT * FROM bypass_purchases WHERE id=? AND tenant_id=? AND voided=0", Number(req.params.id), t);
  if (!r) throw new AppError(404, "Entry not found");
  run("UPDATE bypass_purchases SET voided=1 WHERE id=?", r.id);
  return { ok: true, bypass_owed: bypassOwed(r.supplier_id) };
}));
bypass.post("/bypass/payments/:id/void", requirePerm("wholesale.void"), h((req) => {
  const t = tid(req);
  const r = get("SELECT * FROM bypass_supplier_payments WHERE id=? AND tenant_id=? AND voided=0", Number(req.params.id), t);
  if (!r) throw new AppError(404, "Payment not found");
  tx(() => {
    run("UPDATE bypass_supplier_payments SET voided=1 WHERE id=?", r.id);
    if (r.wtx_id) run("UPDATE wholesale_txns SET voided=1, void_reason=? WHERE id=? AND tenant_id=?", "bypass payment voided", r.wtx_id, t); // reverse the client leg too
  });
  return { ok: true, bypass_owed: bypassOwed(r.supplier_id) };
}));
