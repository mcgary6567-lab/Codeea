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
import { insertTxn, fleet, priceSupply } from "./wholesale.js";
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
/** Total bypass fuel cost in a period — counted against the bypass sales when working out profit. */
export function bypassCost(tenantId: number, fromIso: string, toIso?: string): number {
  const r = get(`SELECT COALESCE(SUM(amount),0) v FROM bypass_purchases WHERE tenant_id=? AND voided=0 AND txn_date >= ?${toIso ? " AND txn_date < ?" : ""}`,
    ...(toIso ? [tenantId, fromIso, toIso] : [tenantId, fromIso]))!;
  return round2(r.v as number);
}

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
    // what we bought, per supplier line
    purchases: z.array(z.object({ supplier_id: z.number().int(), product, litres: z.number().positive().max(200_000), cost_rate: z.number().positive(), ref: z.string().max(60).optional().nullable() })).min(1, "Add at least one supplier line"),
    // where it was dropped, per client
    drops: z.array(z.object({ client_id: z.number().int(), product: product.optional(), litres: z.number().positive(), rate: z.number().positive().optional(), location: z.string().max(120).optional().nullable(), ref: z.string().max(60).optional().nullable(), order_id: z.number().int().optional().nullable(), override_limit: z.boolean().optional() })).min(1, "Add at least one drop").max(30),
  }), req.body);
  if (!get("SELECT id FROM stations WHERE id=? AND tenant_id=?", b.station_id, t)) throw new AppError(400, "Station not found");
  for (const p of b.purchases) if (!get("SELECT id FROM suppliers WHERE id=? AND tenant_id=?", p.supplier_id, t)) throw new AppError(400, "Supplier not found");

  // ---- litre cap: per fuel, the clients can never get more than we purchased ----
  const byProd = (rows: { product?: string; litres: number }[], def?: string) => {
    const m: Record<string, number> = {};
    for (const r of rows) { const p = r.product ?? def ?? "HSD"; m[p] = round2((m[p] ?? 0) + r.litres); }
    return m;
  };
  const bought = byProd(b.purchases);
  const dropped = byProd(b.drops, b.purchases[0].product);
  for (const [p, l] of Object.entries(dropped)) {
    const have = bought[p] ?? 0;
    if (l > have + 0.01) throw new AppError(400, `${PRODUCTS[p] ?? p}: you purchased ${have} L from the supplier(s) but the drops add up to ${l} L — you cannot deliver more than you bought.`);
  }
  const fl = fleet(t, b);
  const total = round2(b.drops.reduce((a, d) => a + d.litres, 0));
  if (fl.tanker?.capacity_l && total > fl.tanker.capacity_l) throw new AppError(400, `Tanker ${fl.tanker.number} holds ${Math.round(fl.tanker.capacity_l).toLocaleString()} L — the drops add up to ${total.toLocaleString()} L`);
  // price every drop at the client's rate first (so a bad drop saves nothing)
  const added: Record<number, number> = {};
  const priced = b.drops.map((d) => {
    const prod = d.product ?? b.purchases[0].product;
    const p = priceSupply(req, d.client_id, { ...d, product: prod }, added[d.client_id] ?? 0);
    added[d.client_id] = (added[d.client_id] ?? 0) + p.amount;
    return { ...d, ...p, product: prod };
  });
  const ts = b.txn_date ? new Date(`${b.txn_date}T12:00:00+05:00`).toISOString() : now();
  const by = req.user!.name;
  const out = tx(() => {
    const { id } = run(`INSERT INTO bypass_deliveries (tenant_id,station_id,tanker_id,driver_id,vehicle_no,driver_name,note,created_by,txn_date,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?)`, t, b.station_id, fl.tanker_id, fl.driver_id, fl.vehicle_no, fl.driver_name, b.note ?? null, by, ts, now());
    if (b.photo_ids?.length) linkPhotos(t, b.photo_ids, `byp:${id}`);
    // supplier cost lines (each its own bypass-account entry)
    for (const p of b.purchases)
      run(`INSERT INTO bypass_purchases (tenant_id,delivery_id,supplier_id,product,litres,cost_rate,amount,ref,note,created_by,txn_date,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`, t, id, p.supplier_id, p.product, round2(p.litres), round2(p.cost_rate), round2(p.litres * p.cost_rate), p.ref ?? null, `Bypass delivery #${id}`, by, ts, now());
    // client drops — billed to each client's wholesale khata (no stock moves)
    const drops: any[] = [];
    for (const d of priced) {
      const txn = insertTxn(req, d.c.id, { type: "supply", station_id: b.station_id, tank_id: null, product: d.product, litres: d.litres, rate: d.rate, amount: d.amount,
        ref: d.ref ?? `BYP-${id}`, note: `Bypass delivery #${id}`, location: d.location ?? null, txn_date: b.txn_date, ...fl });
      deliverOrder(t, d.order_id, d.c.id, txn.id);
      drops.push(txn);
    }
    return { id, drops };
  });
  const purCost = round2(b.purchases.reduce((a, p) => a + p.litres * p.cost_rate, 0));
  const billed = round2(priced.reduce((a, d) => a + d.amount, 0));
  return { id: out.id, billed, cost: purCost, margin: round2(billed - purCost), drops: out.drops, delivery: get("SELECT * FROM bypass_deliveries WHERE id=?", out.id) };
}));

bypass.get("/bypass/deliveries", h((req) => {
  const t = tid(req);
  return all(`SELECT d.*, (SELECT COALESCE(SUM(amount),0) FROM bypass_purchases WHERE delivery_id=d.id AND voided=0) cost,
      (SELECT COALESCE(SUM(litres),0) FROM bypass_purchases WHERE delivery_id=d.id AND voided=0) litres
     FROM bypass_deliveries d WHERE d.tenant_id=? AND d.voided=0 ORDER BY d.txn_date DESC, d.id DESC LIMIT 60`, t);
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
  if (b.mode !== "we_pay") throw new AppError(400, "Client-direct and through-us settlement are coming in the next step; use 'we pay' for now.");
  const ts = b.txn_date ? new Date(`${b.txn_date}T12:00:00+05:00`).toISOString() : now();
  const acc = bankAccountFor(t, b.account_id, b.method ?? "bank");
  const { id } = run(`INSERT INTO bypass_supplier_payments (tenant_id,supplier_id,amount,mode,method,account_id,ref,note,created_by,txn_date,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)`, t, s.id, round2(b.amount), "we_pay", b.method ?? null, acc ?? null, b.ref ?? null, b.note ?? null, req.user!.name, ts, now());
  if (b.photo_ids?.length) linkPhotos(t, b.photo_ids, `byp:pay:${id}`);
  return { payment: get("SELECT * FROM bypass_supplier_payments WHERE id=?", id), bypass_owed: bypassOwed(s.id) };
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
  run("UPDATE bypass_supplier_payments SET voided=1 WHERE id=?", r.id);
  return { ok: true, bypass_owed: bypassOwed(r.supplier_id) };
}));
