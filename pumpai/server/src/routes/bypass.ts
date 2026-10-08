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
import { all, get, run, tx, now, pkDate } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { AppError, round2 } from "../services.js";
import { PRODUCTS } from "../config.js";
import { linkPhotos, proofPhotos, proofCol } from "./capture.js";
import { bankAccountFor, accountIdField } from "./banks.js";
import { insertTxn, fleet, priceSupply, clientDue } from "./wholesale.js";
import { BYPASS_PAY } from "./banks.js";
import { deliverOrder } from "./wholesaleDesk.js";

export const bypass = Router();
bypass.use("/bypass", requirePerm("wholesale.view"));
const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const product = z.enum(["PMG", "HOBC", "HSD"]);

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
  const r = get(`SELECT COALESCE(SUM(cost_amount),0) v FROM bypass_drops WHERE tenant_id=? AND voided=0 AND txn_date >= ?${toIso ? " AND txn_date < ?" : ""}`,
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

/* ================= Create a bypass delivery ================= */
bypass.post("/bypass/deliveries", requirePerm("wholesale.manage"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({
    station_id: z.number().int(), txn_date: day.optional(), note: z.string().max(200).optional().nullable(), photo_ids: proofPhotos,
    tanker_id: z.number().int().optional().nullable(), driver_id: z.number().int().optional().nullable(), vehicle_no: z.string().max(30).optional().nullable(),
    // what we bought now (optional — can buy only, to sell from stock later). Enter the full cost amount, or a per-litre rate.
    purchases: z.array(z.object({ supplier_id: z.number().int(), product, litres: z.number().positive().max(200_000), cost_rate: z.number().positive().optional(), amount: z.number().positive().optional(), ref: z.string().max(60).optional().nullable() })).default([]),
    // where it was dropped (optional — can deliver from the held stock with no fresh purchase). Enter the full sale amount, or a per-litre rate.
    drops: z.array(z.object({ client_id: z.number().int(), product: product.optional(), litres: z.number().positive(), rate: z.number().positive().optional(), amount: z.number().positive().optional(), location: z.string().max(120).optional().nullable(), ref: z.string().max(60).optional().nullable(), order_id: z.number().int().optional().nullable(), override_limit: z.boolean().optional() })).max(30).default([]),
  }), req.body);
  if (!b.purchases.length && !b.drops.length) throw new AppError(400, "Add a purchase or a drop (or both)");
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
  for (const [p, l] of Object.entries(dropProd)) {
    const available = round2(bypassStock(t, p).litres + (buyProd[p] ?? 0));
    if (l > available + 0.01) throw new AppError(400, `${PRODUCTS[p] ?? p}: only ${available} L bypass stock is available (held + bought now) but the drops add up to ${l} L — you cannot deliver more than you have.`);
  }
  const fl = fleet(t, b);
  const total = round2(b.drops.reduce((a, d) => a + d.litres, 0));
  if (fl.tanker?.capacity_l && total > fl.tanker.capacity_l) throw new AppError(400, `Tanker ${fl.tanker.number} holds ${Math.round(fl.tanker.capacity_l).toLocaleString()} L — the drops add up to ${total.toLocaleString()} L`);
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
    const { id } = run(`INSERT INTO bypass_deliveries (tenant_id,station_id,tanker_id,driver_id,vehicle_no,driver_name,note,created_by,txn_date,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?)`, t, b.station_id, fl.tanker_id, fl.driver_id, fl.vehicle_no, fl.driver_name, b.note ?? null, by, ts, now());
    if (b.photo_ids?.length) linkPhotos(t, b.photo_ids, `byp:${id}`);
    // supplier cost lines go INTO bypass stock (asset) — we owe the supplier for all of it
    for (const p of purchases)
      run(`INSERT INTO bypass_purchases (tenant_id,delivery_id,supplier_id,product,litres,cost_rate,amount,ref,note,created_by,txn_date,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`, t, id, p.supplier_id, p.product, round2(p.litres), p.cost_rate, p.amount, p.ref ?? null, `Bypass delivery #${id}`, by, ts, now());
    // the weighted-average cost of each fuel's stock AFTER this delivery's purchases (removing at the average keeps it stable within the batch)
    const avg: Record<string, number> = {};
    for (const p of Object.keys(dropProd)) avg[p] = bypassStock(t, p).avg_cost;
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
    return { id, drops };
  });
  const purCost = round2(purchases.reduce((a, p) => a + p.amount, 0));
  const soldCost = round2(get("SELECT COALESCE(SUM(cost_amount),0) v FROM bypass_drops WHERE delivery_id=?", out.id)!.v as number);
  const billed = round2(priced.reduce((a, d) => a + d.amount, 0));
  return { id: out.id, billed, bought: purCost, sold_cost: soldCost, cost: soldCost, margin: round2(billed - soldCost), drops: out.drops, stock: bypassStock(t), delivery: get("SELECT * FROM bypass_deliveries WHERE id=?", out.id) };
}));

/** This month's bypass munafa so far: client revenue − delivered cost for drops dated this month. */
export function bypassMonthProfit(tenantId: number) {
  const monthStart = new Date(pkDate().slice(0, 7) + "-01T00:00:00+05:00").toISOString();
  const billed = get(`SELECT COALESCE(SUM(w.amount),0) v FROM bypass_drops bd JOIN wholesale_txns w ON w.id=bd.wtx_id
    WHERE bd.tenant_id=? AND bd.voided=0 AND w.voided=0 AND bd.txn_date >= ?`, tenantId, monthStart)!.v as number;
  const cost = bypassCost(tenantId, monthStart);
  return { billed: round2(billed), cost, profit: round2(billed - cost) };
}

/** Bypass stock on hand (bought but not yet delivered), per fuel, with its value and average cost. */
bypass.get("/bypass/stock", h((req) => {
  const t = tid(req);
  const prods = all("SELECT DISTINCT product FROM bypass_purchases WHERE tenant_id=? AND voided=0", t).map((r) => r.product as string);
  const byProduct = prods.map((p) => ({ product: p, ...bypassStock(t, p) })).filter((x) => x.litres > 0.01 || x.value > 0.5);
  return { total: bypassStock(t), by_product: byProduct, month: bypassMonthProfit(t) };
}));

bypass.get("/bypass/deliveries", h((req) => {
  const t = tid(req);
  return all(`SELECT d.*, (SELECT COALESCE(SUM(amount),0) FROM bypass_purchases WHERE delivery_id=d.id AND voided=0) cost,
      (SELECT COALESCE(SUM(litres),0) FROM bypass_purchases WHERE delivery_id=d.id AND voided=0) litres,
      (SELECT COALESCE(SUM(litres),0) FROM bypass_drops WHERE delivery_id=d.id AND voided=0) drop_litres,
      (SELECT COALESCE(SUM(cost_amount),0) FROM bypass_drops WHERE delivery_id=d.id AND voided=0) sold_cost,
      (SELECT COALESCE(SUM(w.amount),0) FROM bypass_drops bd JOIN wholesale_txns w ON w.id=bd.wtx_id WHERE bd.delivery_id=d.id AND bd.voided=0 AND w.voided=0) billed
     FROM bypass_deliveries d WHERE d.tenant_id=? AND d.voided=0 ORDER BY d.txn_date DESC, d.id DESC LIMIT 60`, t)
    // munafa = client ne jo diya (billed) − us delivery me bike maal ki cost (COGS); stock hold ho to sirf delivered hissa counts
    .map((d) => ({ ...d, margin: round2((d.billed as number) - (d.sold_cost as number)) }));
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
  const payments = all(`SELECT p.id, 'payment' kind, p.txn_date, p.amount, p.mode, p.method, p.ref, p.note, c.name client_name FROM bypass_supplier_payments p
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
