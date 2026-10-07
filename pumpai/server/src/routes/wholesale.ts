/**
 * Wholesale supply: bulk fuel to dealers/businesses, each with their own per-litre rate card.
 * Every movement is a ledger entry (supply / return / payment / adjustment) so the client's
 * account — fuel out, fuel back, billed, received and due — is always reproducible from the ledger.
 */
import { Router, type Request } from "express";
import { z } from "zod";
import { all, get, run, tx, now, pkDayStart, pkDate, pkStart, pkEnd, type Row } from "../db.js";
import { h, parse, tid, requirePerm, requireAny, can } from "../auth.js";
import { AppError, createAlert, normalizePhone, round2, pkr, currentPrices } from "../services.js";
import { PRODUCTS } from "../config.js";
import { announce } from "../notifications.js";
import { linkPhotos, proofPhotos, proofCol, requireProof, isCheque } from "./capture.js";
import { bankAccountFor, accountIdField, DEPOT_PAY } from "./banks.js";
import { wholesaleReceipt, wholesaleRateMessage, sendWholesaleStatement, billLink, prevMonth } from "../billing.js";
import { recordPurchase } from "./suppliers.js";
import { createExpense } from "./expenses.js";
import { claimForTrip } from "./claims.js";
import { guardClosedDay } from "./backoffice.js";
import { profile } from "./setup.js";
import { SOCIALS } from "../brandPrint.js";
import { deliverOrder, deskSuggestions, openOrders, promises, cheques } from "./wholesaleDesk.js";

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

/** Carriage / kiraya income from bypass-on-our-ID supplies in a period (whole amount is profit — no fuel cost on our books). */
export function carriageIncome(tenantId: number, fromIso: string, toIso?: string): number {
  const r = get(`SELECT COALESCE(SUM(amount),0) v FROM wholesale_txns WHERE tenant_id=? AND voided=0 AND type='carriage' AND txn_date >= ?${toIso ? " AND txn_date < ?" : ""}`,
    ...(toIso ? [tenantId, fromIso, toIso] : [tenantId, fromIso]))!;
  return round2(r.v as number);
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
  const where = `client_id=? AND voided=0 ${from ? "AND txn_date >= ?" : ""} ${to ? "AND txn_date < ?" : ""}`;
  const args = [clientId, ...(from ? [pkStart(from)] : []), ...(to ? [pkEnd(to)] : [])];
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
    recent: all(`SELECT x.*, ${proofCol("'wtx:'||x.id")}, c.name client_name FROM wholesale_txns x JOIN wholesale_clients c ON c.id=x.client_id
       WHERE x.tenant_id=? ORDER BY x.txn_date DESC, x.id DESC LIMIT 15`, t),
  };
}));

/* ---------------- Dashboard: KPIs, trends, ageing and suggestions ---------------- */
const DAYMS = 86_400_000;
/** How old the unpaid amount is: the due is matched to the newest supplies first (oldest bills count as paid first). */
function ageing(clientId: number, due: number, nowMs: number) {
  const buckets = { d0_15: 0, d16_30: 0, d31_60: 0, d60: 0 };
  let left = due, oldest = 0;
  if (left <= 0) return { buckets, oldest_days: 0 };
  for (const s of all("SELECT amount, txn_date FROM wholesale_txns WHERE client_id=? AND type='supply' AND voided=0 ORDER BY txn_date DESC, id DESC", clientId)) {
    const part = Math.min(left, s.amount), days = Math.floor((nowMs - Date.parse(s.txn_date)) / DAYMS);
    buckets[days <= 15 ? "d0_15" : days <= 30 ? "d16_30" : days <= 60 ? "d31_60" : "d60"] += part;
    oldest = days; left -= part;
    if (left <= 0.01) break;
  }
  if (left > 0.01) { buckets.d60 += left; oldest = Math.max(oldest, 61); } // opening balance / older than any supply
  return { buckets, oldest_days: oldest };
}

wholesale.get("/wholesale/dashboard", h((req) => {
  const t = tid(req), nowMs = Date.now(), today = pkDate();
  const monthStart = new Date(today.slice(0, 7) + "-01T00:00:00+05:00");
  const dayOfMonth = Number(today.slice(8, 10));
  const lastMonthStart = new Date(Date.UTC(monthStart.getUTCFullYear(), monthStart.getUTCMonth() - 1, 1) - 5 * 3600_000);
  const lastMonthSameDay = new Date(lastMonthStart.getTime() + dayOfMonth * DAYMS);
  const period = (from: Date, to: Date) => get(`SELECT COALESCE(SUM(CASE WHEN type='supply' THEN litres END),0) litres, COALESCE(SUM(CASE WHEN type='supply' THEN amount END),0) billed,
      COALESCE(SUM(CASE WHEN type='payment' THEN amount END),0) received, COUNT(CASE WHEN type='supply' THEN 1 END) supplies
    FROM wholesale_txns WHERE tenant_id=? AND voided=0 AND txn_date >= ? AND txn_date < ?`, t, from.toISOString(), to.toISOString())!;
  const mtd = period(monthStart, new Date(nowMs + 1000)), lastMtd = period(lastMonthStart, lastMonthSameDay);
  const pct = (a: number, b: number) => (b > 0 ? Math.round(((a - b) / b) * 100) : null);
  const cost: Record<string, number | null> = Object.fromEntries(Object.keys(PRODUCTS).map((p) => [p, lastCost(t, p)]));
  const monthSupplies = all("SELECT client_id, product, litres, rate, amount FROM wholesale_txns WHERE tenant_id=? AND voided=0 AND type='supply' AND txn_date >= ?", t, monthStart.toISOString());
  const withCost = monthSupplies.filter((x) => cost[x.product] != null);
  const profit = withCost.reduce((a, x) => a + x.litres * (x.rate - cost[x.product]!), 0);
  const costedL = withCost.reduce((a, x) => a + x.litres, 0);

  // clients: due, limit, ageing, ordering rhythm, margin
  const clients = all("SELECT * FROM wholesale_clients WHERE tenant_id=? AND active=1 ORDER BY name", t).map((c) => {
    const due = clientDue(c.id);
    const ag = ageing(c.id, due, nowMs);
    const sup = all("SELECT txn_date FROM wholesale_txns WHERE client_id=? AND type='supply' AND voided=0 AND txn_date >= ? ORDER BY txn_date", c.id, new Date(nowMs - 60 * DAYMS).toISOString()).map((x) => Date.parse(x.txn_date));
    const gaps = sup.slice(1).map((x, i) => (x - sup[i]) / DAYMS);
    const usual = gaps.length >= 2 ? Math.round((gaps.reduce((a, b) => a + b, 0) / gaps.length) * 10) / 10 : null;
    const last = get("SELECT MAX(CASE WHEN type='supply' THEN txn_date END) ls, MAX(CASE WHEN type='payment' THEN txn_date END) lp FROM wholesale_txns WHERE client_id=? AND voided=0", c.id)!;
    const mine = monthSupplies.filter((x) => x.client_id === c.id);
    const card = rateCard(c.id);
    const margins = Object.entries(card).filter(([, r]) => r.margin != null).map(([p, r]) => ({ product: p, margin: r.margin!, rate: r.rate }));
    const days = (iso: string | null) => (iso ? Math.floor((nowMs - Date.parse(iso)) / DAYMS) : null);
    const limitPct = c.credit_limit > 0 ? Math.round((due / c.credit_limit) * 100) : null;
    const health = (limitPct ?? 0) >= 90 || ag.oldest_days > 60 ? "red" : (limitPct ?? 0) >= 75 || ag.oldest_days > 30 ? "amber" : "green";
    return {
      id: c.id, name: c.name, city: c.city, phone: c.phone, credit_limit: c.credit_limit, due: round2(due), limit_pct: limitPct, ageing: ag.buckets, oldest_days: ag.oldest_days,
      month_l: Math.round(mine.reduce((a, x) => a + x.litres, 0)), month_billed: Math.round(mine.reduce((a, x) => a + x.amount, 0)),
      last_supply_days: days(last.ls), last_payment_days: days(last.lp), usual_gap_days: usual, margins, health,
    };
  });
  const ageTotals = clients.reduce((a, c) => { for (const k of Object.keys(a) as (keyof typeof a)[]) a[k] += c.ageing[k]; return a; }, { d0_15: 0, d16_30: 0, d31_60: 0, d60: 0 });

  // trends: daily litres by product (30 days) and weekly billed vs received (8 weeks)
  const from30 = new Date(Date.parse(today + "T00:00:00+05:00") - 29 * DAYMS);
  const dayRows = all(`SELECT strftime('%Y-%m-%d', txn_date, '+5 hours') d, product, SUM(litres) l FROM wholesale_txns WHERE tenant_id=? AND voided=0 AND type='supply' AND txn_date >= ? GROUP BY d, product`, t, from30.toISOString());
  const daily = Array.from({ length: 30 }, (_, i) => {
    const d = new Date(from30.getTime() + i * DAYMS + 5 * 3600_000).toISOString().slice(0, 10);
    return { day: d, ...Object.fromEntries(Object.keys(PRODUCTS).map((p) => [p, Math.round(dayRows.find((r) => r.d === d && r.product === p)?.l ?? 0)])) };
  });
  const weekStart = (ms: number) => { const dd = new Date(ms + 5 * 3600_000); const back = (dd.getUTCDay() + 6) % 7; return Date.parse(dd.toISOString().slice(0, 10) + "T00:00:00+05:00") - back * DAYMS; };
  const w0 = weekStart(nowMs) - 7 * 7 * DAYMS;
  const wkRows = all(`SELECT txn_date, type, amount FROM wholesale_txns WHERE tenant_id=? AND voided=0 AND type IN ('supply','payment') AND txn_date >= ?`, t, new Date(w0).toISOString());
  const weekly = Array.from({ length: 8 }, (_, i) => {
    const a = w0 + i * 7 * DAYMS, b = a + 7 * DAYMS;
    const rows = wkRows.filter((r) => { const x = Date.parse(r.txn_date); return x >= a && x < b; });
    return { week: new Date(a + 5 * 3600_000).toISOString().slice(5, 10), billed: Math.round(rows.filter((r) => r.type === "supply").reduce((s, r) => s + r.amount, 0)), received: Math.round(rows.filter((r) => r.type === "payment").reduce((s, r) => s + r.amount, 0)) };
  });

  /* ---------- suggestions: what to do today, most urgent first ---------- */
  type Sug = { level: "critical" | "warning" | "info" | "good"; title: string; ur?: string; detail: string; action?: { kind: string; client_id?: number; label: string } };
  const sug: Sug[] = [];
  for (const c of clients) {
    if (c.limit_pct != null && c.limit_pct >= 80)
      sug.push({ level: c.limit_pct >= 95 ? "critical" : "warning", title: `${c.name}: ${c.limit_pct}% of credit limit used`, ur: `${c.name} کی ادھار حد ${c.limit_pct}% بھر گئی — اگلی سپلائی سے پہلے رقم لیں`, detail: `Due ${pkr(c.due)} of ${pkr(c.credit_limit)}. Collect a payment before the next supply.`, action: { kind: "payment", client_id: c.id, label: "Receive payment" } });
    if (c.oldest_days > 30 && c.due > 0)
      sug.push({ level: c.oldest_days > 60 ? "critical" : "warning", title: `${c.name}: bills unpaid for ${c.oldest_days} days`, ur: `${c.name} کے بل ${c.oldest_days} دن سے باقی ہیں — حساب بھیجیں اور فون کریں`, detail: `${pkr(c.ageing.d31_60 + c.ageing.d60)} is older than 30 days. Send the statement and call.`, action: { kind: "statement", client_id: c.id, label: "WhatsApp statement" } });
    else if (c.due > 50_000 && (c.last_payment_days == null || c.last_payment_days >= 15))
      sug.push({ level: "warning", title: `${c.name}: no payment for ${c.last_payment_days ?? "many"} days`, ur: `${c.name} نے ${c.last_payment_days ?? "کئی"} دن سے ادائیگی نہیں کی`, detail: `Due ${pkr(c.due)}. A reminder now keeps it from getting old.`, action: { kind: "statement", client_id: c.id, label: "WhatsApp statement" } });
    if (c.usual_gap_days && c.last_supply_days != null && c.last_supply_days > Math.max(3, c.usual_gap_days * 2))
      sug.push({ level: "info", title: `${c.name} has not ordered for ${c.last_supply_days} days`, ur: `${c.name} نے ${c.last_supply_days} دن سے آرڈر نہیں دیا — فون کر کے پوچھیں`, detail: `Usually orders every ${c.usual_gap_days} days. Call — they may be buying elsewhere.`, action: { kind: "open", client_id: c.id, label: "Open client" } });
    for (const m of c.margins) if (m.margin < 1)
      sug.push({ level: m.margin < 0 ? "critical" : "warning", title: `${c.name}: ${PRODUCTS[m.product]} margin only Rs ${m.margin.toFixed(2)}/L`, ur: `${c.name}: منافع صرف ${m.margin.toFixed(2)} روپے فی لیٹر — ریٹ دیکھیں`, detail: `Their rate Rs ${m.rate} vs our last purchase cost. Review the rate.`, action: { kind: "rates", client_id: c.id, label: "Review rate" } });
  }
  for (const c of clients) {
    const fixed = Object.entries(rateCard(c.id)).filter(([, r]) => r.mode === "fixed").map(([p]) => PRODUCTS[p]);
    if (fixed.length && c.month_l > 0)
      sug.push({ level: "info", title: `${c.name}: fixed rate for ${fixed.join(", ")}`, ur: "پمپ ریٹ کے ساتھ چلنے والا ریٹ لگائیں تاکہ منافع ایک جیسا رہے", detail: "A pump-linked rate (pump price − Rs X) changes by itself with every OGRA price change, so your margin stays the same.", action: { kind: "rates", client_id: c.id, label: "Change rate" } });
  }
  const top = [...clients].sort((a, b) => b.month_l - a.month_l)[0];
  if (top?.month_l) sug.push({ level: "good", title: `Top client this month: ${top.name}`, ur: `اس مہینے سب سے بڑا کلائنٹ: ${top.name}`, detail: `${top.month_l.toLocaleString()} L so far. Keep them happy — a thank-you call or a small discount on big loads.`, action: { kind: "open", client_id: top.id, label: "Open client" } });
  // stock for the next 3 days of wholesale
  for (const p of Object.keys(PRODUCTS)) {
    const avg = (get("SELECT COALESCE(SUM(litres),0) l FROM wholesale_txns WHERE tenant_id=? AND voided=0 AND type='supply' AND product=? AND txn_date >= ?", t, p, new Date(nowMs - 14 * DAYMS).toISOString())!.l as number) / 14;
    const stock = get("SELECT COALESCE(SUM(t.current_l),0) l FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? AND t.product=?", t, p)!.l as number;
    if (avg > 0 && stock < avg * 3)
      sug.push({ level: "warning", title: `${PRODUCTS[p]} stock covers only ${Math.max(0, Math.floor(stock / avg))} days of wholesale`, ur: `${PRODUCTS[p]} کا اسٹاک صرف ${Math.max(0, Math.floor(stock / avg))} دن کا ہے — ٹینکر منگوائیں`, detail: `${Math.round(stock).toLocaleString()} L in tanks; wholesale takes about ${Math.round(avg).toLocaleString()} L a day (plus pump sales). Order a tanker.` });
  }
  // fleet
  for (const d of all("SELECT name, licence_expiry FROM drivers WHERE tenant_id=? AND active=1 AND licence_expiry IS NOT NULL AND licence_expiry <= ?", t, new Date(nowMs + 30 * DAYMS).toISOString().slice(0, 10)))
    sug.push({ level: d.licence_expiry < today ? "critical" : "warning", title: `Driver ${d.name}: licence ${d.licence_expiry < today ? "expired" : "expires"} ${d.licence_expiry}`, ur: `ڈرائیور ${d.name} کا لائسنس ${d.licence_expiry < today ? "ختم ہو گیا" : "ختم ہونے والا ہے"}`, detail: "Do not send this driver on a trip until the licence is renewed.", action: { kind: "fleet", label: "Drivers" } });
  const fill = get(`SELECT AVG(tr.litres / tk.capacity_l) f, COUNT(*) n FROM wholesale_trips tr JOIN tankers tk ON tk.id=tr.tanker_id
    WHERE tr.tenant_id=? AND tk.capacity_l > 0 AND tr.trip_date >= ?`, t, new Date(nowMs - 30 * DAYMS).toISOString())!;
  if (fill.n >= 3 && fill.f < 0.6)
    sug.push({ level: "info", title: `Tankers leave only ${Math.round(fill.f * 100)}% full on average`, ur: `ٹینکر صرف ${Math.round(fill.f * 100)}% بھر کر جاتے ہیں — قریب کے کلائنٹس ایک ٹرپ میں ملائیں`, detail: "Combine nearby clients into one tanker trip to save diesel and driver time.", action: { kind: "trip", label: "Plan a trip" } });
  sug.push(...deskSuggestions(t, clients));
  const order = { critical: 0, warning: 1, info: 2, good: 3 };
  sug.sort((a, b) => order[a.level] - order[b.level]);

  const totalDue = round2(clients.reduce((a, c) => a + c.due, 0));
  return {
    kpi: {
      total_due: totalDue, overdue_30: Math.round(ageTotals.d31_60 + ageTotals.d60),
      month_litres: Math.round(mtd.litres), month_litres_change: pct(mtd.litres, lastMtd.litres),
      month_billed: Math.round(mtd.billed), month_received: Math.round(mtd.received), month_received_change: pct(mtd.received, lastMtd.received),
      collection_pct: mtd.billed > 0 ? Math.round((mtd.received / mtd.billed) * 100) : null,
      profit_estimate: costedL ? Math.round(profit) : null, margin_per_l: costedL ? round2(profit / costedL) : null,
      today: get(`SELECT COALESCE(SUM(CASE WHEN type='supply' THEN litres END),0) litres, COALESCE(SUM(CASE WHEN type='payment' THEN amount END),0) received FROM wholesale_txns WHERE tenant_id=? AND voided=0 AND txn_date >= ?`, t, pkDayStart()),
      trips_month: get("SELECT COUNT(*) n FROM wholesale_trips WHERE tenant_id=? AND trip_date >= ?", t, monthStart.toISOString())!.n,
      ...(() => {
        const o = openOrders(t), p = promises(t), q = cheques(t);
        return {
          open_orders: o.length, open_orders_l: Math.round(o.reduce((a, x) => a + x.litres, 0)), orders_today: o.filter((x) => x.needed_on <= today).length,
          promised_today: round2(p.filter((x) => x.state === "today").reduce((a, x) => a + x.amount - x.paid, 0)),
          broken_promises: p.filter((x) => x.state === "broken").length,
          cheques_in_hand: round2(q.filter((x) => x.status === "in_hand" || x.status === "deposited").reduce((a, x) => a + x.amount, 0)),
        };
      })(),
    },
    ageing: Object.fromEntries(Object.entries(ageTotals).map(([k, v]) => [k, Math.round(v)])),
    daily, weekly, clients, suggestions: sug.slice(0, 20),
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
  const perClient: Record<number, string[]> = {};
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
      (perClient[r.client_id] ??= []).push(`${PRODUCTS[ch.product]}: Rs ${nr}/L (pehle Rs ${or})`);
    }
  }
  return { moved, fixed, perClient };
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

wholesale.put("/wholesale/clients/:id/rates", requirePerm("wholesale.rates"), h(async (req) => {
  const c = ownClient(tid(req), Number(req.params.id));
  const b = parse(z.object({ rates: z.record(product, rateInput) }), req.body);
  const before = rates(c.id);
  tx(() => saveRates(c.id, b.rates, req.user!.name));
  const after = rates(c.id);
  await wholesaleRateMessage(tid(req), c.id, Object.keys(b.rates).filter((p) => after[p] !== before[p]).map((p) => `${PRODUCTS[p]}: Rs ${after[p]}/L${before[p] ? ` (pehle Rs ${before[p]})` : ""}`));
  return { rates: after, rate_card: rateCard(c.id) };
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
export function statement(tenantId: number, clientId: number, from?: string, to?: string) {
  const c = ownClient(tenantId, clientId);
  const opening = from ? clientDue(c.id, pkStart(from)) : c.opening_balance;
  const rows = all(
    `SELECT x.*, ${proofCol("'wtx:'||x.id")}, s.name station_name,
       (SELECT GROUP_CONCAT(p.id) FROM photos p WHERE x.trip_id IS NOT NULL AND p.ref = 'trip:' || x.trip_id) trip_proof_ids
     FROM wholesale_txns x LEFT JOIN stations s ON s.id=x.station_id
     WHERE x.client_id=? ${from ? "AND x.txn_date >= ?" : ""} ${to ? "AND x.txn_date < ?" : ""} ORDER BY x.txn_date, x.id`,
    ...[c.id, ...(from ? [pkStart(from)] : []), ...(to ? [pkEnd(to)] : [])],
  );
  let bal = opening;
  const lines = rows.map((r) => {
    const effect = r.voided ? 0 : r.type === "payment" || r.type === "return" ? -r.amount : r.amount;
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
      ["Date", "Type", "Product", "Litres", "Rate", "Debit (billed)", "Credit (received/returned)", "Balance", "Method", "Vehicle", "Driver", "Drop location", "Trip", "Ref", "Note", "Status"].map(esc).join(","),
      ...s.lines.map((l) => [l.txn_date.slice(0, 10), l.type, l.product ?? "", l.litres ?? "", l.rate ?? "", l.debit || "", l.credit || "", l.balance,
        l.method ?? "", l.vehicle_no ?? "", l.driver_name ?? "", l.location ?? "", l.trip_id ?? "", l.ref ?? "", l.note ?? "", l.voided ? "VOID" : ""].map(esc).join(",")),
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

export function insertTxn(req: Request, clientId: number, f: Row) {
  const ts = f.txn_date ? new Date(f.txn_date).toISOString() : now();
  const { id } = run(
    `INSERT INTO wholesale_txns (tenant_id,client_id,type,station_id,tank_id,product,litres,rate,amount,method,vehicle_no,ref,note,created_by,txn_date,created_at,
       trip_id,tanker_id,driver_id,driver_name,location,account_id)
     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    tid(req), clientId, f.type, f.station_id ?? null, f.tank_id ?? null, f.product ?? null, f.litres ?? null, f.rate ?? null,
    f.amount, f.method ?? null, f.vehicle_no ?? null, f.ref ?? null, f.note ?? null, req.user!.name, ts, now(),
    f.trip_id ?? null, f.tanker_id ?? null, f.driver_id ?? null, f.driver_name ?? null, f.location ?? null, f.account_id ?? null,
  );
  linkPhotos(tid(req), f.photo_ids, `wtx:${id}`);
  // WhatsApp receipt to the client once the entry is committed
  setImmediate(() => { if (get("SELECT id FROM wholesale_txns WHERE id=?", id)) wholesaleReceipt(tid(req), clientId, get("SELECT * FROM wholesale_txns WHERE id=?", id)!).catch((e) => console.error("[wholesale receipt]", e.message)); });
  return { ...get("SELECT * FROM wholesale_txns WHERE id=?", id), due_after: clientDue(clientId) };
}

const fuelBody = z.object({
  station_id: z.number(), product, litres: z.number().positive(), rate: z.number().positive().optional(),
  vehicle_no: z.string().optional().nullable(), ref: z.string().optional().nullable(), note: z.string().optional().nullable(),
  tanker_id: z.number().int().optional().nullable(), driver_id: z.number().int().optional().nullable(), location: z.string().max(120).optional().nullable(),
  txn_date: dateStr, override_limit: z.boolean().optional(), photo_ids: proofPhotos, order_id: z.number().int().optional().nullable(),
});

/** Tanker number and driver name from the fleet register (a typed vehicle number still works). */
function fleet(t: number, b: { tanker_id?: number | null; driver_id?: number | null; vehicle_no?: string | null }) {
  const tanker = b.tanker_id ? get("SELECT * FROM tankers WHERE id=? AND tenant_id=?", b.tanker_id, t) : null;
  if (b.tanker_id && !tanker) throw new AppError(400, "Tanker not found");
  const driverId = b.driver_id ?? tanker?.driver_id ?? null;
  const driver = driverId ? get("SELECT * FROM drivers WHERE id=? AND tenant_id=?", driverId, t) : null;
  if (b.driver_id && !driver) throw new AppError(400, "Driver not found");
  return { tanker, driver, tanker_id: tanker?.id ?? null, driver_id: driver?.id ?? null, vehicle_no: tanker?.number ?? b.vehicle_no ?? null, driver_name: driver?.name ?? null };
}

/** Rate and amount for one supply to one client, after the rate-permission and credit-limit checks. */
function priceSupply(req: Request, clientId: number, p: { product: string; litres: number; rate?: number; override_limit?: boolean }, alreadyAdded = 0) {
  const c = ownClient(tid(req), clientId);
  if (!c.active) throw new AppError(400, `${c.name} is inactive`);
  const card = rates(c.id)[p.product];
  if (p.rate !== undefined && p.rate !== card && !can(req.user, "wholesale.rates")) throw new AppError(403, "Only the admin can change the rate on a supply");
  const rate = p.rate ?? card;
  if (!rate) throw new AppError(400, `No ${PRODUCTS[p.product]} rate set for ${c.name}. Ask the admin to set the rate first.`);
  const amount = round2(p.litres * rate);
  const due = clientDue(c.id) + alreadyAdded;
  if (c.credit_limit > 0 && due + amount > c.credit_limit && !(p.override_limit && can(req.user, "wholesale.rates")))
    throw new AppError(400, `Credit limit exceeded for ${c.name}: due ${pkr(due)} + this supply ${pkr(amount)} > limit ${pkr(c.credit_limit)}`);
  return { c, rate, amount };
}
function limitAlert(req: Request, c: Row, dueAfter: number) {
  if (c.credit_limit > 0 && dueAfter >= 0.9 * c.credit_limit)
    createAlert(tid(req), { type: "wholesale_limit", severity: "warning", title: `${c.name} is at ${Math.round((dueAfter / c.credit_limit) * 100)}% of wholesale credit limit`,
      body: `Due ${pkr(dueAfter)} of ${pkr(c.credit_limit)}.`, dedupe_key: `wlimit-${c.id}` });
}

wholesale.post("/wholesale/clients/:id/supply", requirePerm("wholesale.manage"), h((req) => {
  const b = parse(fuelBody, req.body);
  const { c, rate, amount } = priceSupply(req, Number(req.params.id), b);
  const fl = fleet(tid(req), b);
  const tank = pickTank(tid(req), b.station_id, b.product);
  if (tank.current_l < b.litres) throw new AppError(400, `Not enough stock in ${tank.name} (${Math.round(tank.current_l)} L available)`);
  return tx(() => {
    run("UPDATE tanks SET current_l = current_l - ? WHERE id=?", b.litres, tank.id);
    const t = insertTxn(req, c.id, { ...b, ...fl, type: "supply", tank_id: tank.id, rate, amount });
    deliverOrder(tid(req), b.order_id, c.id, t.id);
    limitAlert(req, c, t.due_after);
    return t;
  });
}));

/* ---------------- Tanker trips: one tanker, several clients / places, each at their own rate ---------------- */
const tripBody = z.object({
  station_id: z.number(), product, tanker_id: z.number().int().optional().nullable(), driver_id: z.number().int().optional().nullable(),
  vehicle_no: z.string().optional().nullable(), txn_date: dateStr, note: z.string().max(200).optional().nullable(), photo_ids: proofPhotos,
  drops: z.array(z.object({
    client_id: z.number().int(), litres: z.number().positive(), rate: z.number().positive().optional(),
    // a tanker with chambers can carry petrol and diesel on one trip: each drop says its fuel (default: the trip's)
    product: product.optional(),
    location: z.string().max(120).optional().nullable(), ref: z.string().max(60).optional().nullable(), override_limit: z.boolean().optional(),
    order_id: z.number().int().optional().nullable(),
  })).min(1, "Add at least one drop").max(30),
  // bypass: loaded at the supplier's depot and taken straight to the clients — never in our tanks
  source: z.enum(["pump", "depot"]).default("pump"),
  supplier_id: z.number().int().optional().nullable(),
  cost_rates: z.record(product, z.number().positive()).optional(), // purchase rate per fuel (Rs/L)
  invoice_l: z.record(product, z.number().positive()).optional(), // litres the depot billed per fuel (default: what was dropped)
  depot_ref: z.string().max(60).optional().nullable(),
  freight: z.number().min(0).optional().nullable(),
  freight_by: z.enum(["rate", "supplier", "cash"]).default("rate"),
});
const FREIGHT_CAT = "Tanker freight & transport";
wholesale.get("/wholesale/depots", h((req) => {
  // suppliers to buy a depot-direct trip from, each with the last rate they charged per fuel (any supplier's when they have none)
  const t = tid(req);
  return all("SELECT id, name, phone FROM suppliers WHERE tenant_id=? AND COALESCE(active,1)=1 ORDER BY name", t).map((s) => ({
    ...s, rates: Object.fromEntries(Object.keys(PRODUCTS).map((p) => [p,
      get("SELECT rate FROM supplier_txns WHERE supplier_id=? AND type='purchase' AND product=? AND rate > 0 ORDER BY txn_date DESC, id DESC LIMIT 1", s.id, p)?.rate ?? lastCost(t, p)])),
  }));
}));
wholesale.post("/wholesale/trips", requirePerm("wholesale.manage"), h(async (req) => {
  const b = parse(tripBody, req.body);
  const depot = b.source === "depot";
  const supplier = depot ? get("SELECT * FROM suppliers WHERE id=? AND tenant_id=?", b.supplier_id ?? 0, tid(req)) : null;
  if (depot && !supplier) throw new AppError(400, "Choose the supplier (depot) the tanker was loaded from · سپلائر منتخب کریں");
  if (!get("SELECT id FROM stations WHERE id=? AND tenant_id=?", b.station_id, tid(req))) throw new AppError(400, "Station not found");
  const fl = fleet(tid(req), b);
  const total = round2(b.drops.reduce((a, d) => a + d.litres, 0));
  if (fl.tanker?.capacity_l && total > fl.tanker.capacity_l)
    throw new AppError(400, `Tanker ${fl.tanker.number} holds ${Math.round(fl.tanker.capacity_l).toLocaleString()} L — the drops add up to ${total.toLocaleString()} L`);
  // price every drop first (same client twice counts both against the limit) so a bad drop saves nothing
  const added: Record<number, number> = {};
  const priced = b.drops.map((d) => {
    const prod = d.product ?? b.product;
    const p = priceSupply(req, d.client_id, { ...d, product: prod }, added[d.client_id] ?? 0);
    added[d.client_id] = (added[d.client_id] ?? 0) + p.amount;
    return { ...d, ...p, product: prod };
  });
  const products = [...new Set(priced.map((d) => d.product))];
  const litresOf = (p: string) => round2(priced.filter((d) => d.product === p).reduce((a, d) => a + d.litres, 0));
  // from our pump: each fuel comes out of its own tank. From the depot: nothing touches the tanks, the supplier bills us instead
  const tanks: Record<string, Row> = depot ? {} : Object.fromEntries(products.map((p) => [p, pickTank(tid(req), b.station_id, p)]));
  if (!depot) for (const p of products) {
    if (tanks[p].current_l < litresOf(p)) throw new AppError(400, `Not enough stock in ${tanks[p].name} (${Math.round(tanks[p].current_l)} L ${PRODUCTS[p] ?? p} available, trip needs ${litresOf(p)} L)`);
  }
  const buy = depot ? products.map((p) => {
    const rate = b.cost_rates?.[p];
    if (!rate) throw new AppError(400, `Enter the purchase rate for ${PRODUCTS[p] ?? p} from the depot invoice · خریداری ریٹ لکھیں`);
    const litres = b.invoice_l?.[p] ?? litresOf(p);
    if (litres < litresOf(p) - 0.01) throw new AppError(400, `${PRODUCTS[p] ?? p}: the depot billed ${litres} L but ${litresOf(p)} L were dropped — check the litres`);
    return { p, rate, litres, amount: round2(litres * rate) };
  }) : [];
  const freight = depot && b.freight_by !== "rate" ? round2(b.freight ?? 0) : 0;
  if (depot && b.freight_by !== "rate" && !freight) throw new AppError(400, "Enter the freight amount, or choose \"included in the rate\"");
  // the freight expense must be bookable on that day (a closed day would refuse it after the trip was saved)
  const freightDay = new Date(Date.parse(b.txn_date ?? now()) + 5 * 3600_000).toISOString().slice(0, 10);
  if (freight && b.freight_by === "cash") guardClosedDay(req, tid(req), freightDay);
  const mixed = products.length > 1;
  const ts = b.txn_date ? new Date(b.txn_date).toISOString() : now();
  const trip = tx(() => {
    for (const p of Object.keys(tanks)) run("UPDATE tanks SET current_l = current_l - ? WHERE id=?", litresOf(p), tanks[p].id);
    const tank = depot ? null : tanks[products[0]];
    const amount = round2(priced.reduce((a, d) => a + d.amount, 0));
    const { id } = run(`INSERT INTO wholesale_trips (tenant_id,station_id,tank_id,product,tanker_id,driver_id,vehicle_no,driver_name,litres,amount,drops,note,created_by,trip_date,created_at,
        source,supplier_id,depot_ref,invoice_l,cost,freight,freight_by)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`, tid(req), b.station_id, mixed || !tank ? null : tank.id, products.join("+"), fl.tanker_id, fl.driver_id, fl.vehicle_no, fl.driver_name,
      total, amount, priced.length, b.note ?? null, req.user!.name, ts, now(),
      b.source, supplier?.id ?? null, depot ? b.depot_ref ?? null : null, depot ? round2(buy.reduce((a, x) => a + x.litres, 0)) : null,
      depot ? round2(buy.reduce((a, x) => a + x.amount, 0)) : null, depot ? freight : null, depot ? b.freight_by : null);
    linkPhotos(tid(req), b.photo_ids, `trip:${id}`);
    for (const d of priced) {
      const t = insertTxn(req, d.c.id, { type: "supply", station_id: b.station_id, tank_id: depot ? null : tanks[d.product].id, product: d.product, litres: d.litres, rate: d.rate, amount: d.amount,
        ref: d.ref ?? `TRIP-${id}`, note: b.note ?? null, location: d.location ?? null, txn_date: b.txn_date, trip_id: id, ...fl });
      deliverOrder(tid(req), d.order_id, d.c.id, t.id);
      limitAlert(req, d.c, t.due_after);
    }
    // the supplier's bill for a depot-direct trip: the fuel, and the freight when it is on their bill
    const ref = b.depot_ref || `TRIP-${id}`;
    for (const x of buy) recordPurchase(tid(req), { supplier_id: supplier!.id, trip_id: id, product: x.p, litres: x.litres, rate: x.rate, ref, note: `Depot direct — trip #${id}`, by: req.user!.name, at: ts });
    // the depot billed more than the clients got (beyond the allowed loss): claim it from the supplier, like a short tanker
    for (const x of buy) {
      const claim = claimForTrip(tid(req), { trip_id: id, supplier_id: supplier!.id, product: x.p, invoice_l: x.litres, received_l: litresOf(x.p), rate: x.rate, at: ts });
      if (claim) createAlert(tid(req), { station_id: b.station_id, type: "short_delivery", severity: "warning", dedupe_key: `trip-claim-${claim}`,
        title: `Depot ${supplier!.name} short by ${round2(x.litres - litresOf(x.p))} L ${PRODUCTS[x.p] ?? x.p} (trip #${id})`,
        body: `Billed ${x.litres} L, clients got ${litresOf(x.p)} L. A shortage claim was opened (Suppliers → Claims).` });
    }
    if (freight && b.freight_by === "supplier")
      recordPurchase(tid(req), { supplier_id: supplier!.id, trip_id: id, product: null, litres: null, rate: null, amount: freight, ref, note: `Freight — trip #${id}`, by: req.user!.name, at: ts });
    return id;
  });
  // freight paid in cash is an expense (it goes to the owner for approval above the limit, like any other)
  if (freight && b.freight_by === "cash") {
    if (!get("SELECT id FROM expense_categories WHERE tenant_id=? AND name=?", tid(req), FREIGHT_CAT)) run("INSERT INTO expense_categories (tenant_id,name) VALUES (?,?)", tid(req), FREIGHT_CAT);
    await createExpense(req, { category: FREIGHT_CAT, amount: freight, method: "cash", station_id: b.station_id, paid_to: fl.vehicle_no ?? fl.driver_name ?? null,
      note: `Trip #${trip} — depot ${supplier!.name}`, expense_date: freightDay } as any);
  }
  return tripSheet(tid(req), trip);
}));
export function tripSheet(t: number, id: number) {
  const trip = get(`SELECT tr.*, ${proofCol("'trip:'||tr.id")}, s.name station_name, tk.name tank_name, d.phone driver_phone, d.cnic driver_cnic, d.licence_no driver_licence, sp.name supplier_name
    FROM wholesale_trips tr JOIN stations s ON s.id=tr.station_id LEFT JOIN tanks tk ON tk.id=tr.tank_id LEFT JOIN drivers d ON d.id=tr.driver_id LEFT JOIN suppliers sp ON sp.id=tr.supplier_id
    WHERE tr.id=? AND tr.tenant_id=?`, id, t);
  if (!trip) throw new AppError(404, "Trip not found");
  const drops = all(`SELECT w.id, ${proofCol("'wtx:'||w.id")}, w.client_id, c.name client_name, c.business_name, c.phone, c.address, w.product, w.litres, w.rate, w.amount, w.location, w.ref, w.voided
    FROM wholesale_txns w JOIN wholesale_clients c ON c.id=w.client_id WHERE w.trip_id=? ORDER BY w.id`, id);
  const live = drops.filter((d) => !d.voided);
  const delivered = round2(live.reduce((a, d) => a + d.litres, 0)), billed = round2(live.reduce((a, d) => a + d.amount, 0));
  // depot-direct: what the trip earned after the supplier's bill and the freight
  const depot = trip.source === "depot" ? {
    purchases: all("SELECT product, litres, rate, amount, ref FROM supplier_txns WHERE trip_id=? AND type='purchase' ORDER BY id", id),
    short_l: round2(Math.max(0, (trip.invoice_l ?? 0) - drops.reduce((a, d) => a + d.litres, 0))),
    profit: round2(billed - (trip.cost ?? 0) - (trip.freight ?? 0)),
    claims: all("SELECT id, product, litres, amount, recovered, status FROM shortage_claims WHERE trip_id=? ORDER BY id", id),
  } : null;
  // the header of the printed challans: who is delivering
  const biz = profile(t);
  const st = get("SELECT name, address, city FROM stations WHERE id=?", trip.station_id)!;
  const business = { name: biz.name, phone: biz.biz_phone || biz.owner_phone, address: biz.place || [st.address, st.city].filter(Boolean).join(", "),
    city: biz.biz_city || st.city, ntn: biz.ntn, strn: biz.strn, logo_url: biz.logo_url, footer: biz.receipt_footer, station: st.name,
    email: biz.biz_email, website: biz.website, omc: biz.omc, color: biz.brand_color, social: Object.fromEntries(SOCIALS.map((x) => [x.key, biz[x.key]]).filter(([, v]) => v)) };
  return { ...trip, drops, delivered_l: delivered, billed, depot, business };
}
wholesale.get("/wholesale/trips", h((req) => all(`SELECT tr.*, s.name station_name, sp.name supplier_name FROM wholesale_trips tr JOIN stations s ON s.id=tr.station_id LEFT JOIN suppliers sp ON sp.id=tr.supplier_id
  WHERE tr.tenant_id=? ORDER BY tr.trip_date DESC, tr.id DESC LIMIT 100`, tid(req))));
wholesale.get("/wholesale/trips/:id", h((req) => tripSheet(tid(req), Number(req.params.id))));

/* ---------------- Fleet register: tankers and drivers ---------------- */
const tankerBody = z.object({
  number: z.string().trim().min(2).max(30).transform((v) => v.toUpperCase()), capacity_l: z.number().positive().max(100_000).optional().nullable(),
  chambers: z.number().int().min(1).max(10).optional().nullable(), ownership: z.enum(["own", "hired"]).default("own"),
  owner_name: z.string().max(80).optional().nullable(), owner_phone: z.string().max(20).optional().nullable(),
  driver_id: z.number().int().optional().nullable(), notes: z.string().max(200).optional().nullable(), active: z.boolean().optional(),
});
const driverBody = z.object({
  name: z.string().trim().min(2).max(80), phone: z.string().max(20).optional().nullable(), cnic: z.string().max(20).optional().nullable(),
  licence_no: z.string().max(30).optional().nullable(), licence_expiry: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional().nullable().or(z.literal("")),
  address: z.string().max(160).optional().nullable(), notes: z.string().max(200).optional().nullable(), active: z.boolean().optional(),
});
const clean = (o: Record<string, unknown>) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, v === "" ? null : typeof v === "boolean" ? Number(v) : v]));
wholesale.get("/wholesale/fleet", h((req) => {
  const today = pkDate();
  return {
    tankers: all(`SELECT tk.*, d.name driver_name, d.phone driver_phone, (SELECT MAX(txn_date) FROM wholesale_txns w WHERE w.tanker_id=tk.id AND w.voided=0) last_trip
      FROM tankers tk LEFT JOIN drivers d ON d.id=tk.driver_id WHERE tk.tenant_id=? ORDER BY tk.active DESC, tk.number`, tid(req)),
    drivers: all(`SELECT d.*, (SELECT MAX(txn_date) FROM wholesale_txns w WHERE w.driver_id=d.id AND w.voided=0) last_trip FROM drivers d WHERE d.tenant_id=? ORDER BY d.active DESC, d.name`, tid(req))
      .map((d) => ({ ...d, licence_expired: Boolean(d.licence_expiry && d.licence_expiry < today) })),
  };
}));
function saveFleet(table: "tankers" | "drivers", req: Request, data: Record<string, unknown>, id?: number) {
  const t = tid(req);
  if (data.driver_id && !get("SELECT id FROM drivers WHERE id=? AND tenant_id=?", data.driver_id as number, t)) throw new AppError(400, "Driver not found");
  if (table === "tankers" && data.number && get("SELECT id FROM tankers WHERE tenant_id=? AND number=? AND id<>?", t, data.number as string, id ?? 0)) throw new AppError(400, `Tanker ${data.number} is already on file`);
  const v = clean(data);
  if (id) {
    if (!get(`SELECT id FROM ${table} WHERE id=? AND tenant_id=?`, id, t)) throw new AppError(404, "Not found");
    const keys = Object.keys(v);
    if (keys.length) run(`UPDATE ${table} SET ${keys.map((k) => `${k}=?`).join(",")} WHERE id=?`, ...(Object.values(v) as any[]), id);
    return get(`SELECT * FROM ${table} WHERE id=?`, id);
  }
  const keys = Object.keys(v);
  const r = run(`INSERT INTO ${table} (tenant_id,${keys.join(",")},created_at) VALUES (?,${keys.map(() => "?").join(",")},?)`, t, ...(Object.values(v) as any[]), now());
  return get(`SELECT * FROM ${table} WHERE id=?`, r.id);
}
wholesale.post("/wholesale/tankers", requirePerm("wholesale.manage"), h((req) => saveFleet("tankers", req, parse(tankerBody, req.body))));
wholesale.patch("/wholesale/tankers/:id", requirePerm("wholesale.manage"), h((req) => saveFleet("tankers", req, parse(tankerBody.partial(), req.body), Number(req.params.id))));
wholesale.post("/wholesale/drivers", requirePerm("wholesale.manage"), h((req) => saveFleet("drivers", req, parse(driverBody, req.body))));
wholesale.patch("/wholesale/drivers/:id", requirePerm("wholesale.manage"), h((req) => saveFleet("drivers", req, parse(driverBody.partial(), req.body), Number(req.params.id))));

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

wholesale.post("/wholesale/clients/:id/payment", requireAny("wholesale.manage", "cash.receive"), h((req) => {
  const c = ownClient(tid(req), Number(req.params.id));
  const b = parse(z.object({ amount: z.number().positive(), method: z.string().min(2), ref: z.string().optional().nullable(), note: z.string().optional().nullable(), txn_date: dateStr, photo_ids: proofPhotos, account_id: accountIdField,
    cheque_bank: z.string().max(80).optional().nullable(), cheque_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional().nullable() }), req.body);
  if (isCheque(b.method)) requireProof(tid(req), b.photo_ids, "cheque");
  // a cheque still in hand goes into the cheque register — the due comes down when it clears
  if (isCheque(b.method) && !b.account_id) {
    const t = tid(req), no = b.ref?.trim() || "—", bank = b.cheque_bank?.trim() || "Bank not given";
    if (no !== "—" && get("SELECT id FROM wholesale_cheques WHERE tenant_id=? AND bank=? AND cheque_no=? AND status<>'returned'", t, bank, no))
      throw new AppError(400, `Cheque ${no} of ${bank} is already in the register`);
    const { id } = run(`INSERT INTO wholesale_cheques (tenant_id,client_id,amount,bank,cheque_no,cheque_date,status,note,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)`,
      t, c.id, b.amount, bank, no, b.cheque_date || b.txn_date?.slice(0, 10) || pkDate(), "in_hand", b.note ?? null, req.user!.name, now(), now());
    linkPhotos(t, b.photo_ids, `wchq:${id}`);
    return { cheque_pending: true, cheque: get("SELECT * FROM wholesale_cheques WHERE id=?", id), due_after: clientDue(c.id),
      message: "Cheque is in the register — it counts as paid when it clears" };
  }
  return insertTxn(req, c.id, { ...b, account_id: bankAccountFor(tid(req), b.account_id, b.method), type: "payment" });
}));

wholesale.post("/wholesale/clients/:id/adjustment", requirePerm("wholesale.rates"), h((req) => {
  const c = ownClient(tid(req), Number(req.params.id));
  const b = parse(z.object({ amount: z.number().refine((v) => v !== 0, "Amount cannot be zero"), note: z.string().min(3, "Give a reason"), txn_date: dateStr, photo_ids: proofPhotos }), req.body);
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
    // a payment made straight to our depot: the depot's side goes too
    if (t.type === "payment" && t.method === DEPOT_PAY) run("DELETE FROM supplier_txns WHERE tenant_id=? AND ref=? AND method=?", tid(req), `wtx:${t.id}`, DEPOT_PAY);
    const c = get("SELECT name FROM wholesale_clients WHERE id=?", t.client_id)!;
    createAlert(tid(req), { type: "wholesale_void", severity: "info", title: `Wholesale ${t.type} #${t.id} voided — ${c.name}`, body: `${pkr(t.amount)}. Reason: ${b.reason}. By ${req.user!.name}.` });
    return { ...get("SELECT * FROM wholesale_txns WHERE id=?", t.id)!, due_after: clientDue(t.client_id) };
  });
}));

/* ---------------- Monthly statement on WhatsApp ---------------- */
const monthQ = z.object({ month: z.string().regex(/^\d{4}-\d{2}$/).optional() });
wholesale.get("/wholesale/clients/:id/statement-link", h((req) => {
  const c = ownClient(tid(req), Number(req.params.id));
  const m = parse(monthQ, req.query).month ?? pkDate().slice(0, 7);
  return { month: m, url: billLink(tid(req), "w", c.id, m) };
}));
wholesale.post("/wholesale/clients/:id/send-statement", requirePerm("wholesale.manage"), h(async (req) => {
  const c = ownClient(tid(req), Number(req.params.id));
  if (!c.phone) throw new AppError(400, "Add the client's WhatsApp number first");
  return sendWholesaleStatement(tid(req), c.id, parse(monthQ, req.body).month ?? prevMonth());
}));
