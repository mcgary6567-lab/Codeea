/**
 * Wholesale supply: bulk fuel to dealers/businesses, each with their own per-litre rate card.
 * Every movement is a ledger entry (supply / return / payment / adjustment) so the client's
 * account — fuel out, fuel back, billed, received and due — is always reproducible from the ledger.
 */
import { Router, type Request } from "express";
import { z } from "zod";
import { all, get, run, tx, now, pkDayStart, pkDate, type Row } from "../db.js";
import { h, parse, tid, requirePerm, can } from "../auth.js";
import { AppError, createAlert, normalizePhone, round2, pkr, currentPrices } from "../services.js";
import { PRODUCTS } from "../config.js";
import { announce } from "../notifications.js";

export const wholesale = Router();
wholesale.use("/wholesale", requirePerm("wholesale.view"));

const product = z.enum(["PMG", "HOBC", "HSD"]);
const dateStr = z.string().regex(/^\d{4}-\d{2}-\d{2}/).optional();

/** Signed effect of a ledger row on the amount the client owes us. */
const DUE_SQL = `CASE type WHEN 'supply' THEN amount WHEN 'return' THEN -amount WHEN 'payment' THEN -amount ELSE amount END`;

function ownClient(tenantId: number, id: number) {
  const c = get("SELECT * FROM wholesale_clients WHERE id=? AND tenant_id=?", id, tenantId);
  if (!c) throw new AppError(404, "Wholesale client not found");
  return c;
}

export function clientDue(clientId: number, before?: string): number {
  const c = get("SELECT opening_balance FROM wholesale_clients WHERE id=?", clientId)!;
  const r = get(`SELECT COALESCE(SUM(${DUE_SQL}),0) d FROM wholesale_txns WHERE client_id=? AND voided=0 ${before ? "AND txn_date < ?" : ""}`,
    ...(before ? [clientId, before] : [clientId]))!;
  return round2(c.opening_balance + r.d);
}

/** Rate input: a plain number (fixed rate) or { mode: "fixed", rate } or { mode: "discount", discount } (Rs/L below the pump price). */
export const rateInput = z.union([
  z.number().positive(),
  z.object({ mode: z.literal("fixed"), rate: z.number().positive() }),
  z.object({ mode: z.literal("discount"), discount: z.number().min(-200).max(200) }),
]);
type RateInput = z.infer<typeof rateInput>;
const norm = (r: RateInput) => typeof r === "number" ? { mode: "fixed" as const, rate: r, discount: null } : r.mode === "fixed" ? { ...r, discount: null } : { ...r, rate: null };
const rd = (n: number) => `Rs ${Math.abs(n).toFixed(2)}`;
export const rateLabel = (mode: string, discount: number | null) =>
  mode === "discount" ? (discount! >= 0 ? `pump − ${rd(discount!)}` : `pump + ${rd(discount!)}`) : "fixed";

/** Last purchase rate per product (what a litre cost us), for the margin shown on the rate card. */
function lastCost(tenantId: number, product: string): number | null {
  return get("SELECT rate FROM supplier_txns WHERE tenant_id=? AND type='purchase' AND product=? AND rate > 0 ORDER BY txn_date DESC, id DESC LIMIT 1", tenantId, product)?.rate ?? null;
}

/** Full rate card: each product's mode, discount, today's pump price and the effective rate the client pays now. */
export function rateCard(clientId: number) {
  const tenant = get("SELECT tenant_id FROM wholesale_clients WHERE id=?", clientId)!.tenant_id;
  const pump = currentPrices(tenant);
  return Object.fromEntries(all("SELECT * FROM wholesale_rates WHERE client_id=?", clientId).map((r) => {
    const pumpRate = pump[r.product]?.price ?? null;
    const rate = r.mode === "discount" ? (pumpRate == null ? null : round2(pumpRate - r.discount)) : r.rate;
    const cost = lastCost(tenant, r.product);
    return [r.product, {
      mode: r.mode, discount: r.mode === "discount" ? r.discount : null, fixed: r.mode === "fixed" ? r.rate : null,
      rate, pump: pumpRate, vs_pump: rate != null && pumpRate != null ? round2(pumpRate - rate) : null,
      cost, margin: rate != null && cost != null ? round2(rate - cost) : null,
      label: rateLabel(r.mode, r.discount), updated_at: r.updated_at, updated_by: r.updated_by,
    }];
  })) as Record<string, { mode: string; discount: number | null; fixed: number | null; rate: number | null; pump: number | null; vs_pump: number | null; cost: number | null; margin: number | null; label: string; updated_at: string; updated_by: string }>;
}

/** Effective rate per product today (pump-linked rates follow the current pump price). */
function rates(clientId: number): Record<string, number> {
  return Object.fromEntries(Object.entries(rateCard(clientId)).filter(([, r]) => r.rate != null).map(([p, r]) => [p, r.rate!]));
}

function summary(clientId: number, from?: string, to?: string) {
  const where = `client_id=? AND voided=0 ${from ? "AND txn_date >= ?" : ""} ${to ? "AND txn_date < date(?, '+1 day')" : ""}`;
  const args = [clientId, ...(from ? [from] : []), ...(to ? [to] : [])];
  const byProduct = all(
    `SELECT product,
       ROUND(SUM(CASE WHEN type='supply' THEN litres ELSE 0 END),2) supplied_l,
       ROUND(SUM(CASE WHEN type='return' THEN litres ELSE 0 END),2) returned_l,
       ROUND(SUM(CASE WHEN type='supply' THEN amount WHEN type='return' THEN -amount ELSE 0 END),2) net_amount
     FROM wholesale_txns WHERE ${where} AND product IS NOT NULL GROUP BY product`, ...args);
  const t = get(
    `SELECT COALESCE(SUM(CASE WHEN type='supply' THEN amount END),0) billed,
       COALESCE(SUM(CASE WHEN type='return' THEN amount END),0) returned,
       COALESCE(SUM(CASE WHEN type='payment' THEN amount END),0) received,
       COALESCE(SUM(CASE WHEN type='adjustment' THEN amount END),0) adjustments,
       COUNT(CASE WHEN type='supply' THEN 1 END) supplies,
       MAX(CASE WHEN type='payment' THEN txn_date END) last_payment,
       MAX(CASE WHEN type='supply' THEN txn_date END) last_supply
     FROM wholesale_txns WHERE ${where}`, ...args)!;
  return { by_product: byProduct, ...t, due: clientDue(clientId) };
}

/* ---------------- Overview ---------------- */
wholesale.get("/wholesale/summary", h((req) => {
  const t = tid(req);
  const month = new Date(pkDate().slice(0, 7) + "-01T00:00:00+05:00").toISOString();
  const today = pkDayStart();
  const clients = all("SELECT id FROM wholesale_clients WHERE tenant_id=?", t);
  const dues = clients.map((c) => clientDue(c.id));
  return {
    total_due: round2(dues.reduce((a, b) => a + b, 0)),
    clients: clients.length,
    month: get(
      `SELECT COALESCE(SUM(CASE WHEN type='supply' THEN litres END),0) supplied_l, COALESCE(SUM(CASE WHEN type='supply' THEN amount END),0) billed,
         COALESCE(SUM(CASE WHEN type='payment' THEN amount END),0) received, COALESCE(SUM(CASE WHEN type='return' THEN litres END),0) returned_l
       FROM wholesale_txns WHERE tenant_id=? AND voided=0 AND txn_date >= ?`, t, month),
    today: get(`SELECT COALESCE(SUM(CASE WHEN type='supply' THEN litres END),0) supplied_l, COALESCE(SUM(CASE WHEN type='payment' THEN amount END),0) received
       FROM wholesale_txns WHERE tenant_id=? AND voided=0 AND txn_date >= ?`, t, today),
    by_product_month: all(`SELECT product, ROUND(SUM(litres)) litres, ROUND(SUM(amount)) amount FROM wholesale_txns
       WHERE tenant_id=? AND voided=0 AND type='supply' AND txn_date >= ? GROUP BY product`, t, month),
    recent: all(`SELECT x.*, c.name client_name FROM wholesale_txns x JOIN wholesale_clients c ON c.id=x.client_id
       WHERE x.tenant_id=? ORDER BY x.txn_date DESC, x.id DESC LIMIT 15`, t),
  };
}));

/* ---------------- Clients ---------------- */
wholesale.get("/wholesale/clients", h((req) => {
  const q = `%${String(req.query.q ?? "").trim()}%`;
  const month = new Date(pkDate().slice(0, 7) + "-01T00:00:00+05:00").toISOString();
  return all("SELECT * FROM wholesale_clients WHERE tenant_id=? AND (name LIKE ? OR business_name LIKE ? OR phone LIKE ?) ORDER BY active DESC, name", tid(req), q, q, q)
    .map((c) => {
      const s = get(`SELECT COALESCE(SUM(CASE WHEN type='supply' AND txn_date >= ? THEN litres END),0) month_l,
          MAX(CASE WHEN type='payment' THEN txn_date END) last_payment, MAX(CASE WHEN type='supply' THEN txn_date END) last_supply
        FROM wholesale_txns WHERE client_id=? AND voided=0`, month, c.id)!;
      return { ...c, rates: rates(c.id), rate_card: rateCard(c.id), due: clientDue(c.id), month_l: s.month_l, last_payment: s.last_payment, last_supply: s.last_supply };
    });
}));

const clientBody = z.object({
  name: z.string().min(2), business_name: z.string().optional().nullable(), phone: z.string().optional().nullable(),
  city: z.string().optional().nullable(), address: z.string().optional().nullable(), notes: z.string().optional().nullable(),
  credit_limit: z.number().min(0).optional(), opening_balance: z.number().optional(), active: z.boolean().optional(),
  rates: z.record(product, rateInput).optional(),
});

function guardFinancials(req: Request, b: { credit_limit?: number; opening_balance?: number; rates?: unknown }) {
  if ((b.credit_limit !== undefined || b.opening_balance !== undefined || b.rates !== undefined) && !can(req.user, "wholesale.rates"))
    throw new AppError(403, "Only the admin can set rates, credit limits and opening balances");
}

function saveRates(clientId: number, newRates: Partial<Record<string, RateInput>>, by: string) {
  const tenant = get("SELECT tenant_id FROM wholesale_clients WHERE id=?", clientId)!.tenant_id;
  const pump = currentPrices(tenant);
  const old = rateCard(clientId);
  for (const [p, input] of Object.entries(newRates)) {
    const r = norm(input!);
    const o = old[p];
    if (o && o.mode === r.mode && (r.mode === "fixed" ? o.fixed === r.rate : o.discount === r.discount)) continue;
    if (r.mode === "discount" && !pump[p]) throw new AppError(400, `No pump price set for ${PRODUCTS[p]} yet, so a rate below the pump price cannot be worked out`);
    const effective = r.mode === "discount" ? round2(pump[p].price - r.discount!) : r.rate!;
    if (effective <= 0) throw new AppError(400, `${PRODUCTS[p]} rate would be Rs ${effective}`);
    run(`INSERT INTO wholesale_rates (client_id,product,rate,mode,discount,updated_at,updated_by) VALUES (?,?,?,?,?,?,?)
         ON CONFLICT(client_id,product) DO UPDATE SET rate=excluded.rate, mode=excluded.mode, discount=excluded.discount, updated_at=excluded.updated_at, updated_by=excluded.updated_by`,
      clientId, p, effective, r.mode, r.discount, now(), by);
    run("INSERT INTO wholesale_rate_history (client_id,product,old_rate,new_rate,changed_by,note,created_at) VALUES (?,?,?,?,?,?,?)",
      clientId, p, o?.rate ?? null, effective, by, r.mode === "discount" ? `Set to ${rateLabel("discount", r.discount)}` : "Fixed rate", now());
  }
}

/**
 * Pump price changed: clients on "pump − Rs X" now pay the new pump price minus their discount.
 * Records the change in each client's rate history and returns lines for the wholesale team.
 */
export function followPumpPrice(tenantId: number, changes: { product: string; old: number | null; new: number }[]) {
  const moved: string[] = [], fixed: string[] = [];
  for (const ch of changes) {
    for (const r of all(`SELECT r.*, c.name FROM wholesale_rates r JOIN wholesale_clients c ON c.id=r.client_id
        WHERE c.tenant_id=? AND c.active=1 AND r.product=? ORDER BY c.name`, tenantId, ch.product)) {
      if (r.mode !== "discount") { fixed.push(`${r.name} ${PRODUCTS[ch.product]} Rs ${r.rate} (fixed)`); continue; }
      const nr = round2(ch.new - r.discount);
      const or = ch.old == null ? r.rate : round2(ch.old - r.discount);
      run("UPDATE wholesale_rates SET rate=? WHERE client_id=? AND product=?", nr, r.client_id, r.product);
      run("INSERT INTO wholesale_rate_history (client_id,product,old_rate,new_rate,changed_by,note,created_at) VALUES (?,?,?,?,?,?,?)",
        r.client_id, r.product, or, nr, "Pump price change", rateLabel("discount", r.discount), now());
      moved.push(`${r.name}: ${PRODUCTS[ch.product]} Rs ${or} → Rs ${nr} (${rateLabel("discount", r.discount)})`);
    }
  }
  return { moved, fixed };
}

wholesale.post("/wholesale/clients", requirePerm("wholesale.manage"), h(async (req) => {
  const b = parse(clientBody, req.body);
  guardFinancials(req, b);
  const created = tx(() => {
    const { id } = run(
      "INSERT INTO wholesale_clients (tenant_id,name,business_name,phone,city,address,credit_limit,opening_balance,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
      tid(req), b.name, b.business_name ?? null, b.phone ? normalizePhone(b.phone) : null, b.city ?? null, b.address ?? null,
      b.credit_limit ?? 0, b.opening_balance ?? 0, b.notes ?? null, now(),
    );
    if (b.rates) saveRates(id, b.rates, req.user!.name);
    return { ...get("SELECT * FROM wholesale_clients WHERE id=?", id)!, rates: rates(id), due: clientDue(id) };
  });
  await announce(tid(req), req.user!.id, ["wholesale", "admin"], { type: "new_wholesale_client", data: { client_id: created.id },
    title: `🚛 New wholesale client: ${b.name}`,
    body: b.rates ? Object.entries(rateCard(created.id)).map(([p, r]) => `${PRODUCTS[p]} Rs ${r.rate}/L${r.mode === "discount" ? ` (${r.label})` : ""}`).join(" · ") : "Rate abhi set nahi — admin rate card set karein." });
  return created;
}));

wholesale.patch("/wholesale/clients/:id", requirePerm("wholesale.manage"), h((req) => {
  const c = ownClient(tid(req), Number(req.params.id));
  const b = parse(clientBody.partial(), req.body);
  guardFinancials(req, b);
  const m = { ...c, ...b };
  run(`UPDATE wholesale_clients SET name=?, business_name=?, phone=?, city=?, address=?, credit_limit=?, opening_balance=?, notes=?, active=? WHERE id=?`,
    m.name, m.business_name ?? null, b.phone ? normalizePhone(b.phone) : c.phone, m.city ?? null, m.address ?? null,
    m.credit_limit, m.opening_balance, m.notes ?? null, b.active === undefined ? c.active : b.active ? 1 : 0, c.id);
  if (b.rates) tx(() => saveRates(c.id, b.rates!, req.user!.name));
  return get("SELECT * FROM wholesale_clients WHERE id=?", c.id);
}));

wholesale.put("/wholesale/clients/:id/rates", requirePerm("wholesale.rates"), h((req) => {
  const c = ownClient(tid(req), Number(req.params.id));
  const b = parse(z.object({ rates: z.record(product, rateInput) }), req.body);
  tx(() => saveRates(c.id, b.rates, req.user!.name));
  return { rates: rates(c.id), rate_card: rateCard(c.id) };
}));

wholesale.get("/wholesale/clients/:id", h((req) => {
  const c = ownClient(tid(req), Number(req.params.id));
  return {
    ...c, rates: rates(c.id), rate_card: rateCard(c.id), summary: summary(c.id),
    month: summary(c.id, new Date(pkDate().slice(0, 7) + "-01T00:00:00+05:00").toISOString()),
    rate_history: all("SELECT * FROM wholesale_rate_history WHERE client_id=? ORDER BY id DESC LIMIT 30", c.id),
  };
}));

/* ---------------- Statement (ledger with running balance) ---------------- */
function statement(tenantId: number, clientId: number, from?: string, to?: string) {
  const c = ownClient(tenantId, clientId);
  const opening = from ? clientDue(c.id, from) : c.opening_balance;
  const rows = all(
    `SELECT x.*, s.name station_name FROM wholesale_txns x LEFT JOIN stations s ON s.id=x.station_id
     WHERE x.client_id=? ${from ? "AND x.txn_date >= ?" : ""} ${to ? "AND x.txn_date < date(?, '+1 day')" : ""} ORDER BY x.txn_date, x.id`,
    ...[c.id, ...(from ? [from] : []), ...(to ? [to] : [])],
  );
  let bal = opening;
  const lines = rows.map((r) => {
    const effect = r.voided ? 0 : r.type === "supply" || r.type === "adjustment" ? r.amount : -r.amount;
    bal = round2(bal + effect);
    return { ...r, debit: effect > 0 ? effect : 0, credit: effect < 0 ? -effect : 0, balance: bal };
  });
  return { client: c, from: from ?? null, to: to ?? null, opening_balance: opening, closing_balance: bal, lines, summary: summary(c.id, from, to) };
}

wholesale.get("/wholesale/clients/:id/statement", h((req) => {
  const q = parse(z.object({ from: dateStr, to: dateStr }), req.query);
  return statement(tid(req), Number(req.params.id), q.from, q.to);
}));

wholesale.get("/wholesale/clients/:id/statement.csv", (req, res, next) => {
  try {
    const q = parse(z.object({ from: dateStr, to: dateStr }), req.query);
    const s = statement(tid(req), Number(req.params.id), q.from, q.to);
    const esc = (v: unknown) => `"${String(v ?? "").replaceAll('"', '""')}"`;
    const out = [
      ["Statement", s.client.name, s.client.business_name ?? ""].map(esc).join(","),
      ["Period", s.from ?? "start", s.to ?? "today"].map(esc).join(","),
      ["Opening balance", s.opening_balance].map(esc).join(","),
      ["Date", "Type", "Product", "Litres", "Rate", "Debit (billed)", "Credit (received/returned)", "Balance", "Method", "Vehicle", "Ref", "Note", "Status"].map(esc).join(","),
      ...s.lines.map((l) => [l.txn_date.slice(0, 10), l.type, l.product ?? "", l.litres ?? "", l.rate ?? "", l.debit || "", l.credit || "", l.balance,
        l.method ?? "", l.vehicle_no ?? "", l.ref ?? "", l.note ?? "", l.voided ? "VOID" : ""].map(esc).join(",")),
      ["Closing balance", s.closing_balance].map(esc).join(","),
    ].join("\n");
    res.setHeader("Content-Type", "text/csv; charset=utf-8");
    res.setHeader("Content-Disposition", `attachment; filename="statement-${s.client.name.replace(/[^\w-]+/g, "_")}.csv"`);
    res.send("﻿" + out);
  } catch (e) { next(e); }
});

/* ---------------- Ledger entries ---------------- */
function pickTank(tenantId: number, stationId: number, prod: string) {
  const t = get(`SELECT t.* FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? AND t.station_id=? AND t.product=? ORDER BY t.current_l DESC LIMIT 1`,
    tenantId, stationId, prod);
  if (!t) throw new AppError(400, `No ${PRODUCTS[prod] ?? prod} tank at this station`);
  return t;
}

function insertTxn(req: Request, clientId: number, f: Row) {
  const ts = f.txn_date ? new Date(f.txn_date).toISOString() : now();
  const { id } = run(
    `INSERT INTO wholesale_txns (tenant_id,client_id,type,station_id,tank_id,product,litres,rate,amount,method,vehicle_no,ref,note,created_by,txn_date,created_at)
     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    tid(req), clientId, f.type, f.station_id ?? null, f.tank_id ?? null, f.product ?? null, f.litres ?? null, f.rate ?? null,
    f.amount, f.method ?? null, f.vehicle_no ?? null, f.ref ?? null, f.note ?? null, req.user!.name, ts, now(),
  );
  return { ...get("SELECT * FROM wholesale_txns WHERE id=?", id), due_after: clientDue(clientId) };
}

const fuelBody = z.object({
  station_id: z.number(), product, litres: z.number().positive(), rate: z.number().positive().optional(),
  vehicle_no: z.string().optional().nullable(), ref: z.string().optional().nullable(), note: z.string().optional().nullable(),
  txn_date: dateStr, override_limit: z.boolean().optional(),
});

wholesale.post("/wholesale/clients/:id/supply", requirePerm("wholesale.manage"), h((req) => {
  const c = ownClient(tid(req), Number(req.params.id));
  if (!c.active) throw new AppError(400, "This client is inactive");
  const b = parse(fuelBody, req.body);
  const card = rates(c.id)[b.product];
  if (b.rate !== undefined && b.rate !== card && !can(req.user, "wholesale.rates")) throw new AppError(403, "Only the admin can change the rate on a supply");
  const rate = b.rate ?? card;
  if (!rate) throw new AppError(400, `No ${PRODUCTS[b.product]} rate set for ${c.name}. Ask the admin to set the rate first.`);
  const amount = round2(b.litres * rate);
  const due = clientDue(c.id);
  if (c.credit_limit > 0 && due + amount > c.credit_limit && !(b.override_limit && can(req.user, "wholesale.rates")))
    throw new AppError(400, `Credit limit exceeded: due ${pkr(due)} + this supply ${pkr(amount)} > limit ${pkr(c.credit_limit)}`);
  const tank = pickTank(tid(req), b.station_id, b.product);
  if (tank.current_l < b.litres) throw new AppError(400, `Not enough stock in ${tank.name} (${Math.round(tank.current_l)} L available)`);
  return tx(() => {
    run("UPDATE tanks SET current_l = current_l - ? WHERE id=?", b.litres, tank.id);
    const t = insertTxn(req, c.id, { ...b, type: "supply", tank_id: tank.id, rate, amount });
    if (c.credit_limit > 0 && t.due_after >= 0.9 * c.credit_limit)
      createAlert(tid(req), { type: "wholesale_limit", severity: "warning", title: `${c.name} is at ${Math.round((t.due_after / c.credit_limit) * 100)}% of wholesale credit limit`,
        body: `Due ${pkr(t.due_after)} of ${pkr(c.credit_limit)}.`, dedupe_key: `wlimit-${c.id}` });
    return t;
  });
}));

wholesale.post("/wholesale/clients/:id/return", requirePerm("wholesale.manage"), h((req) => {
  const c = ownClient(tid(req), Number(req.params.id));
  const b = parse(fuelBody, req.body);
  // credit the return at the rate of their latest supply of that product (or the card rate)
  const last = get("SELECT rate FROM wholesale_txns WHERE client_id=? AND type='supply' AND product=? AND voided=0 ORDER BY txn_date DESC, id DESC LIMIT 1", c.id, b.product);
  const def = last?.rate ?? rates(c.id)[b.product];
  if (b.rate !== undefined && b.rate !== def && !can(req.user, "wholesale.rates")) throw new AppError(403, "Only the admin can change the return rate");
  const rate = b.rate ?? def;
  if (!rate) throw new AppError(400, "No rate found for this product");
  const tank = pickTank(tid(req), b.station_id, b.product);
  if (tank.current_l + b.litres > tank.capacity_l) throw new AppError(400, `${tank.name} does not have space for ${b.litres} L`);
  return tx(() => {
    run("UPDATE tanks SET current_l = current_l + ? WHERE id=?", b.litres, tank.id);
    return insertTxn(req, c.id, { ...b, type: "return", tank_id: tank.id, rate, amount: round2(b.litres * rate) });
  });
}));

wholesale.post("/wholesale/clients/:id/payment", requirePerm("wholesale.manage"), h((req) => {
  const c = ownClient(tid(req), Number(req.params.id));
  const b = parse(z.object({ amount: z.number().positive(), method: z.string().min(2), ref: z.string().optional().nullable(), note: z.string().optional().nullable(), txn_date: dateStr }), req.body);
  return insertTxn(req, c.id, { ...b, type: "payment" });
}));

wholesale.post("/wholesale/clients/:id/adjustment", requirePerm("wholesale.rates"), h((req) => {
  const c = ownClient(tid(req), Number(req.params.id));
  const b = parse(z.object({ amount: z.number().refine((v) => v !== 0, "Amount cannot be zero"), note: z.string().min(3, "Give a reason"), txn_date: dateStr }), req.body);
  return insertTxn(req, c.id, { ...b, type: "adjustment" });
}));

/** Cancel a wrong entry: it stays in the ledger marked VOID and any stock movement is reversed. */
wholesale.post("/wholesale/txns/:id/void", requirePerm("wholesale.void"), h((req) => {
  const b = parse(z.object({ reason: z.string().min(3, "Give a reason") }), req.body);
  const t = get("SELECT * FROM wholesale_txns WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  if (!t) throw new AppError(404, "Entry not found");
  if (t.voided) throw new AppError(400, "Already voided");
  return tx(() => {
    if (t.tank_id && t.litres) {
      if (t.type === "supply") run("UPDATE tanks SET current_l = current_l + ? WHERE id=?", t.litres, t.tank_id);
      if (t.type === "return") run("UPDATE tanks SET current_l = MAX(0, current_l - ?) WHERE id=?", t.litres, t.tank_id);
    }
    run("UPDATE wholesale_txns SET voided=1, void_reason=? WHERE id=?", `${b.reason} (by ${req.user!.name})`, t.id);
    const c = get("SELECT name FROM wholesale_clients WHERE id=?", t.client_id)!;
    createAlert(tid(req), { type: "wholesale_void", severity: "info", title: `Wholesale ${t.type} #${t.id} voided — ${c.name}`, body: `${pkr(t.amount)}. Reason: ${b.reason}. By ${req.user!.name}.` });
    return { ...get("SELECT * FROM wholesale_txns WHERE id=?", t.id)!, due_after: clientDue(t.client_id) };
  });
}));
