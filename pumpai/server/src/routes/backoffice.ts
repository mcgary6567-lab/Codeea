/**
 * Back office without registers:
 *  - Cash book: office cash = last cash count + money in − money out (shift cash, khata/wholesale cash,
 *    bank deposits, cash expenses, supplier and staff cash), with bank deposits and cash counts
 *  - Tanker orders sent to the supplier on WhatsApp in one tap, closed by the delivery
 *  - Dip charts: dip in cm → litres for every tank
 *  - Day close: at midnight the day is locked and the owner gets the day's report
 */
import { BRAND_CSS, brandHead, brandFoot } from "../brandPrint.js";
import { Router } from "express";
import { logoTag } from "./setup.js";
import { z } from "zod";
import jwt from "jsonwebtoken";
import { all, get, run, tx, now, pkDate, pkStart, pkEnd, getSetting } from "../db.js";
import { rentalIncome } from "./property.js";
import { h, parse, tid, requirePerm, requireAny, can } from "../auth.js";
import { AppError, round2, pkr, createAlert } from "../services.js";
import { config, PRODUCTS } from "../config.js";
import { sendDirect } from "../whatsapp/cloud.js";
import { linkPhotos, proofPhotos, proofCol } from "./capture.js";
import { dayBook } from "./reports.js";
import { bankAccountFor, accountIdField, accountName } from "./banks.js";
import { notify, staff } from "../notifications.js";

export const backoffice = Router();

/* ================= Cash book ================= */
/** The pump takes the salesmen's cash at a counter (a cashier exists, or a shift was ever handed over): shift cash counts when received. */
export const handoverMode = (t: number) => Boolean(
  get("SELECT 1 x FROM users WHERE tenant_id=? AND role='cashier' AND active=1 LIMIT 1", t)
  || get("SELECT 1 x FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.handed_at IS NOT NULL LIMIT 1", t));
/** SQL: this payment method is cash (no method means cash). */
export const cashSql = (col: string) => `LOWER(TRIM(COALESCE(${col},''))) IN ('cash','')`;

/** Cash that came into and went out of the office between two moments. */
export function cashFlows(t: number, since: string, until: string) {
  const P = [t, since, until] as const;
  const one = (sql: string, ...args: unknown[]) => round2(get(sql, ...(args as []))!.v ?? 0);
  const ins = {
    shift_cash: handoverMode(t)
      ? one("SELECT COALESCE(SUM(sh.handed_amount),0) v FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.handed_at IS NOT NULL AND sh.handed_at > ? AND sh.handed_at <= ?", ...P)
      : one("SELECT COALESCE(SUM(sh.cash_actual),0) v FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.status='closed' AND sh.closed_at > ? AND sh.closed_at <= ?", ...P),
    // cash sales a manager / owner made with no shift open (they never pass through a salesman's bag)
    counter_sales: round2(
      one("SELECT COALESCE(SUM(x.amount),0) v FROM sales x JOIN stations s ON s.id=x.station_id WHERE s.tenant_id=? AND x.shift_id IS NULL AND x.payment_method='cash' AND x.created_at > ? AND x.created_at <= ?", ...P)
      + one("SELECT COALESCE(SUM(total),0) v FROM shop_sales WHERE tenant_id=? AND shift_id IS NULL AND payment_method='cash' AND created_at > ? AND created_at <= ?", ...P)),
    khata_cash: one(`SELECT COALESCE(SUM(k.amount),0) v FROM khata_ledger k JOIN customers c ON c.id=k.customer_id WHERE c.tenant_id=? AND k.type='credit' AND ${cashSql("k.ref")} AND k.created_at > ? AND k.created_at <= ?`, ...P),
    wholesale_cash: one("SELECT COALESCE(SUM(amount),0) v FROM wholesale_txns WHERE tenant_id=? AND type='payment' AND voided=0 AND LOWER(COALESCE(method,''))='cash' AND created_at > ? AND created_at <= ?", ...P),
    carriage_cash: one("SELECT COALESCE(SUM(amount),0) v FROM carriage_txns WHERE tenant_id=? AND type='payment' AND voided=0 AND LOWER(COALESCE(method,''))='cash' AND created_at > ? AND created_at <= ?", ...P),
    prepaid_cash: round2(one("SELECT COALESCE(SUM(value),0) v FROM fuel_coupons WHERE tenant_id=? AND method='cash' AND sold_at > ? AND sold_at <= ?", ...P)
      + one("SELECT COALESCE(SUM(CASE WHEN type='refund' THEN -amount ELSE amount END),0) v FROM wallet_ledger WHERE tenant_id=? AND type IN ('deposit','refund') AND method='cash' AND created_at > ? AND created_at <= ?", ...P)),
    bank_withdrawals: one("SELECT COALESCE(SUM(-amount),0) v FROM bank_txns WHERE tenant_id=? AND kind='withdraw' AND created_at > ? AND created_at <= ?", ...P),
    other_cash: one("SELECT COALESCE(SUM(amount),0) v FROM cashier_vouchers WHERE tenant_id=? AND direction='in' AND party_type='other' AND LOWER(method)='cash' AND voided=0 AND created_at > ? AND created_at <= ?", ...P),
    // advances paid back in cash, and advances / loan instalments kept back out of a cash salary (the salary below is booked in full)
    staff_repaid: one(`SELECT COALESCE(SUM(amount),0) v FROM staff_ledger WHERE tenant_id=? AND ((type='repayment' AND ${cashSql("method")}) OR (type='deduction' AND month IS NOT NULL)) AND created_at > ? AND created_at <= ?`, ...P),
  };
  const outs = {
    bank_deposits: one("SELECT COALESCE(SUM(amount),0) v FROM bank_deposits WHERE tenant_id=? AND created_at > ? AND created_at <= ?", ...P),
    expenses: one("SELECT COALESCE(SUM(amount),0) v FROM expenses WHERE tenant_id=? AND status='approved' AND method='cash' AND shift_id IS NULL AND created_at > ? AND created_at <= ?", ...P),
    supplier_payments: one("SELECT COALESCE(SUM(amount),0) v FROM supplier_txns WHERE tenant_id=? AND type='payment' AND LOWER(COALESCE(method,''))='cash' AND created_at > ? AND created_at <= ?", ...P),
    bypass_supplier_payments: one("SELECT COALESCE(SUM(amount),0) v FROM bypass_supplier_payments WHERE tenant_id=? AND mode='we_pay' AND voided=0 AND account_id IS NULL AND LOWER(COALESCE(method,''))='cash' AND created_at > ? AND created_at <= ?", ...P),
    other_cash: one("SELECT COALESCE(SUM(amount),0) v FROM cashier_vouchers WHERE tenant_id=? AND direction='out' AND party_type='other' AND LOWER(method)='cash' AND voided=0 AND created_at > ? AND created_at <= ?", ...P),
    staff_advances: one(`SELECT COALESCE(SUM(amount),0) v FROM staff_ledger WHERE tenant_id=? AND (type='advance' OR (type='bonus' AND month IS NULL)) AND ${cashSql("method")} AND created_at > ? AND created_at <= ?`, ...P),
  };
  const sum = (o: Record<string, number>) => round2(Object.values(o).reduce((a, b) => a + b, 0));
  return { ins, outs, total_in: sum(ins), total_out: sum(outs) };
}

export function cashPosition(t: number, until = new Date().toISOString()) {
  const last = get("SELECT * FROM cash_counts WHERE tenant_id=? AND created_at <= ? ORDER BY created_at DESC, id DESC LIMIT 1", t, until);
  // before the first cash count the book runs from the very first entry
  const since = last?.created_at ?? "1970-01-01T00:00:00.000Z";
  const { ins, outs, total_in, total_out } = cashFlows(t, since, until);
  return {
    last_count: last ? { amount: last.amount, at: last.created_at, by: last.counted_by, variance: last.variance } : null,
    since, ins, outs, total_in, total_out,
    cash_in_hand: round2((last?.amount ?? 0) + total_in - total_out),
  };
}

// a deposit with the full account (title, number, branch) for the paying-in slip
const DEPOSIT_SQL = `SELECT d.*, s.name station_name, a.bank acc_bank, a.branch acc_branch, a.title acc_title, a.account_no acc_no
  FROM bank_deposits d LEFT JOIN stations s ON s.id=d.station_id LEFT JOIN bank_accounts a ON a.id=d.account_id`;
backoffice.get("/cash", requireAny("expenses.view", "cash.book"), h((req) => ({
  ...cashPosition(tid(req)),
  deposits: all(`${DEPOSIT_SQL} WHERE d.tenant_id=? ORDER BY d.id DESC LIMIT 30`, tid(req)),
  counts: all(`SELECT c.*, ${proofCol("'cashcount:'||c.id")} FROM cash_counts c WHERE c.tenant_id=? ORDER BY c.id DESC LIMIT 15`, tid(req)),
})));

backoffice.post("/cash/deposits", requireAny("expenses.create", "cash.book"), h((req) => {
  const b = parse(z.object({ amount: z.number().positive().max(100_000_000), bank: z.string().min(2).max(60).optional().nullable(), account_id: accountIdField, slip_ref: z.string().max(60).optional().nullable(),
    station_id: z.number().optional().nullable(), photo_id: z.number().optional().nullable(), note: z.string().max(200).optional().nullable() }), req.body);
  const accountId = bankAccountFor(tid(req), b.account_id, "bank");
  const bank = accountId ? accountName(get("SELECT * FROM bank_accounts WHERE id=?", accountId)!) : b.bank;
  if (!bank) throw new AppError(400, "Choose the bank");
  const pos = cashPosition(tid(req));
  if (b.amount > pos.cash_in_hand + 0.01 && pos.last_count) throw new AppError(400, `Only ${pkr(pos.cash_in_hand)} cash should be in hand`);
  const { id } = run("INSERT INTO bank_deposits (tenant_id,station_id,amount,bank,slip_ref,photo_id,note,deposited_by,created_at,account_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
    tid(req), b.station_id ?? null, b.amount, bank, b.slip_ref ?? null, null, b.note ?? null, req.user!.name, now(), accountId);
  if (b.photo_id && linkPhotos(tid(req), [b.photo_id], `deposit:${id}`)) run("UPDATE bank_deposits SET photo_id=? WHERE id=?", b.photo_id, id);
  return { deposit: get(`${DEPOSIT_SQL} WHERE d.id=?`, id), ...cashPosition(tid(req)) };
}));

/** Count the office cash: the difference from what the book says is reported. */
backoffice.post("/cash/count", requireAny("expenses.create", "cash.book"), h(async (req) => {
  const b = parse(z.object({ amount: z.number().min(0).max(100_000_000), note: z.string().max(200).optional().nullable(), photo_ids: proofPhotos,
    /** notes counted by denomination: { "5000": 12, "1000": 30, ... } */
    notes: z.record(z.string().regex(/^\d+$/), z.number().int().min(0).max(100_000)).optional().nullable() }), req.body);
  const t = tid(req);
  const expected = cashPosition(t);
  // the very first count just sets the starting cash
  const variance = expected.last_count ? round2(b.amount - expected.cash_in_hand) : 0;
  const cid = run("INSERT INTO cash_counts (tenant_id,amount,expected,variance,note,counted_by,created_at,notes_json) VALUES (?,?,?,?,?,?,?,?)",
    t, b.amount, expected.cash_in_hand, variance, b.note ?? null, req.user!.name, now(), b.notes ? JSON.stringify(b.notes) : null).id;
  linkPhotos(t, b.photo_ids, `cashcount:${cid}`);
  if (Math.abs(variance) >= 500) {
    const a = createAlert(t, { type: "cash_count", severity: Math.abs(variance) >= 5000 ? "critical" : "warning",
      title: `Office cash ${variance < 0 ? "short" : "over"} ${pkr(Math.abs(variance))}`, body: `Counted ${pkr(b.amount)} by ${req.user!.name}; book said ${pkr(expected.cash_in_hand)}.` });
    if (a) await notify(t, staff(t, ["admin"], req.user!.id), { type: "cash_count", title: a.title, body: a.body });
  }
  return { variance, ...cashPosition(t) };
}));

/* ================= Tanker orders ================= */
backoffice.get("/stock/orders", requirePerm("stock.manage"), h((req) => all(
  `SELECT o.*, s.name supplier_name, s.phone supplier_phone, st.name station_name FROM purchase_orders o JOIN suppliers s ON s.id=o.supplier_id
   JOIN stations st ON st.id=o.station_id WHERE o.tenant_id=? ORDER BY o.id DESC LIMIT 30`, tid(req))));

/** Suggested supplier for a product: the one we bought it from last. */
backoffice.get("/stock/order-suggestion/:tankId", requirePerm("stock.manage"), h((req) => {
  const tank = get("SELECT t.*, s.name station_name FROM tanks t JOIN stations s ON s.id=t.station_id WHERE t.id=? AND s.tenant_id=?", Number(req.params.tankId), tid(req));
  if (!tank) throw new AppError(404, "Tank not found");
  const last = get(`SELECT d.supplier_id FROM deliveries d JOIN tanks t ON t.id=d.tank_id WHERE t.product=? AND d.supplier_id IS NOT NULL ORDER BY d.id DESC LIMIT 1`, tank.product);
  const room = Math.floor((tank.capacity_l - tank.current_l) / 1000) * 1000;
  return { tank, supplier_id: last?.supplier_id ?? null, litres: Math.max(0, Math.min(room, 40_000)), room: Math.round(tank.capacity_l - tank.current_l) };
}));

backoffice.post("/stock/orders", requirePerm("stock.manage"), h(async (req) => {
  const t = tid(req);
  const b = parse(z.object({ tank_id: z.number(), supplier_id: z.number(), litres: z.number().positive().max(100_000), note: z.string().max(200).optional().nullable() }), req.body);
  const tank = get("SELECT t.*, s.name station_name, s.address FROM tanks t JOIN stations s ON s.id=t.station_id WHERE t.id=? AND s.tenant_id=?", b.tank_id, t);
  if (!tank) throw new AppError(404, "Tank not found");
  const sup = get("SELECT * FROM suppliers WHERE id=? AND tenant_id=?", b.supplier_id, t);
  if (!sup) throw new AppError(404, "Supplier not found");
  if (b.litres > tank.capacity_l - tank.current_l + 1) throw new AppError(400, `${tank.name} has space for ${Math.round(tank.capacity_l - tank.current_l).toLocaleString()} L only`);
  const { id } = run("INSERT INTO purchase_orders (tenant_id,supplier_id,station_id,tank_id,product,litres,status,note,ordered_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
    t, sup.id, tank.station_id, tank.id, tank.product, b.litres, "ordered", b.note ?? null, req.user!.name, now());
  const biz = get("SELECT name FROM tenants WHERE id=?", t)!.name;
  const sent = await sendDirect(t, sup, "tanker_order", `po:${id}`,
    `🛢️ Tanker order #${id} — ${biz}\n${PRODUCTS[tank.product]}: ${b.litres.toLocaleString()} L\nDeliver to: ${tank.station_name}${tank.address ? `, ${tank.address}` : ""}\n` +
    `${b.note ? `${b.note}\n` : ""}Ordered by ${req.user!.name}. Please confirm the dispatch time and tanker number.`);
  return { order: get("SELECT * FROM purchase_orders WHERE id=?", id), whatsapp: sent ? "sent" : "no phone" };
}));

backoffice.post("/stock/orders/:id/cancel", requirePerm("stock.manage"), h((req) => {
  run("UPDATE purchase_orders SET status='cancelled' WHERE id=? AND tenant_id=? AND status='ordered'", Number(req.params.id), tid(req));
  return { ok: true };
}));

/** Called when a tanker is received: closes the oldest open order for that supplier and tank. */
export function closeOrderOnDelivery(t: number, supplierId: number | null, tankId: number, deliveryId: number) {
  if (!supplierId) return;
  const o = get("SELECT id FROM purchase_orders WHERE tenant_id=? AND supplier_id=? AND tank_id=? AND status='ordered' ORDER BY id LIMIT 1", t, supplierId, tankId);
  if (o) run("UPDATE purchase_orders SET status='delivered', delivery_id=?, delivered_at=? WHERE id=?", deliveryId, now(), o.id);
}

/* ================= Dip charts ================= */
export function litresFromCm(tankId: number, cm: number): number {
  const rows = all("SELECT cm, litres FROM tank_charts WHERE tank_id=? ORDER BY cm", tankId) as { cm: number; litres: number }[];
  if (rows.length < 2) throw new AppError(400, "This tank has no dip chart yet. Add it on the Tanks & Stock page.");
  if (cm < rows[0].cm || cm > rows[rows.length - 1].cm) throw new AppError(400, `Dip must be between ${rows[0].cm} and ${rows[rows.length - 1].cm} cm`);
  const i = rows.findIndex((r) => r.cm >= cm);
  if (rows[i].cm === cm) return rows[i].litres;
  const a = rows[i - 1], b = rows[i];
  return round2(a.litres + ((cm - a.cm) / (b.cm - a.cm)) * (b.litres - a.litres));
}

/** Approximate chart for a horizontal cylindrical tank (until the OMC chart is entered). */
export function cylinderChart(diameterCm: number, capacityL: number, stepCm = 5) {
  const r = diameterCm / 2, full = Math.PI * r * r;
  const area = (h: number) => r * r * Math.acos((r - h) / r) - (r - h) * Math.sqrt(Math.max(0, 2 * r * h - h * h));
  const rows: { cm: number; litres: number }[] = [];
  for (let h = 0; h < diameterCm; h += stepCm) rows.push({ cm: h, litres: Math.round((area(h) / full) * capacityL) });
  rows.push({ cm: diameterCm, litres: Math.round(capacityL) });
  return rows;
}

const ownTank = (t: number, id: number) => {
  const tank = get("SELECT t.* FROM tanks t JOIN stations s ON s.id=t.station_id WHERE t.id=? AND s.tenant_id=?", id, t);
  if (!tank) throw new AppError(404, "Tank not found");
  return tank;
};
backoffice.get("/tanks/:id/chart", requirePerm("stock.manage"), h((req) => {
  const tank = ownTank(tid(req), Number(req.params.id));
  return { tank, rows: all("SELECT cm, litres FROM tank_charts WHERE tank_id=? ORDER BY cm", tank.id) };
}));
backoffice.put("/tanks/:id/chart", requirePerm("stock.manage"), h((req) => {
  const tank = ownTank(tid(req), Number(req.params.id));
  const b = parse(z.union([
    z.object({ rows: z.array(z.object({ cm: z.number().min(0).max(1000), litres: z.number().min(0) })).min(2).max(2000) }),
    z.object({ diameter_cm: z.number().min(50).max(600) }),
  ]), req.body);
  const rows = "rows" in b ? [...b.rows].sort((x, y) => x.cm - y.cm) : cylinderChart(b.diameter_cm, tank.capacity_l);
  for (let i = 1; i < rows.length; i++)
    if (rows[i].cm === rows[i - 1].cm || rows[i].litres < rows[i - 1].litres) throw new AppError(400, `Chart row ${i + 1}: cm must go up and litres must not go down`);
  tx(() => {
    run("DELETE FROM tank_charts WHERE tank_id=?", tank.id);
    for (const r of rows) run("INSERT INTO tank_charts (tank_id,cm,litres) VALUES (?,?,?)", tank.id, r.cm, r.litres);
  });
  return { rows: rows.length, max_litres: rows[rows.length - 1].litres };
}));
backoffice.get("/tanks/:id/dip-litres", requirePerm("stock.manage"), h((req) => {
  const tank = ownTank(tid(req), Number(req.params.id));
  return { litres: litresFromCm(tank.id, Number(req.query.cm)) };
}));

/* ================= Day close ================= */
export const lastClosedDay = (t: number): string | null => get("SELECT MAX(day) d FROM day_closes WHERE tenant_id=?", t)?.d ?? null;
/** Entries for a closed day can only be changed by the admin. */
export function guardClosedDay(req: { user?: { role: string } }, t: number, date: string) {
  const closed = lastClosedDay(t);
  if (closed && date.slice(0, 10) <= closed && req.user?.role !== "admin") throw new AppError(400, `${date.slice(0, 10)} is closed. Only the admin can change it.`);
}
const dayToken = (t: number, day: string) => jwt.sign({ day, t }, config.jwtSecret, { expiresIn: "400d" });

export async function closeDay(t: number, day = pkDate(Date.now() - 86_400_000)) {
  if (get("SELECT id FROM day_closes WHERE tenant_id=? AND day=?", t, day)) return { day, already: true };
  const book = dayBook(t, pkStart(day), pkEnd(day));
  const cash = cashPosition(t, pkEnd(day));
  // the day's own ins and outs; cash.ins / cash.outs run from the last cash count, which may be days back or after the day's shifts
  const flows = cashFlows(t, pkStart(day), pkEnd(day));
  // shift cash the salesmen still hold: closed shifts not yet handed to the cashier
  const unhanded = handoverMode(t) ? round2(get("SELECT COALESCE(SUM(sh.cash_actual),0) v FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.status='closed' AND sh.handed_at IS NULL AND sh.closed_at <= ?", t, pkEnd(day))!.v) : 0;
  run("INSERT INTO day_closes (tenant_id,day,data,closed_at) VALUES (?,?,?,?)", t, day, JSON.stringify({ book, cash, flows, unhanded }), now());
  const owner = getSetting(t, "owner_phone") || get("SELECT owner_phone FROM tenants WHERE id=?", t)?.owner_phone;
  const s = book.sales;
  const text = `📒 Din band — ${day}\n💰 Sale ${pkr(s.revenue)} (${Math.round(s.retail_litres).toLocaleString()} L pump${s.wholesale ? ` + ${pkr(s.wholesale)} wholesale` : ""})\n` +
    `   Cash ${pkr(s.cash)} · Digital ${pkr(s.digital)} · Khata ${pkr(s.khata)}\n🧾 Kharcha ${pkr(book.expenses.total)}\n🚛 Supply ${book.supply.litres.toLocaleString()} L (${pkr(book.supply.cost)})\n` +
    `🛢️ Stock ${book.stock.litres.toLocaleString()} L · value ${pkr(book.stock.value_at_cost ?? 0)}\n💵 Office cash ${pkr(cash.cash_in_hand)}\n` +
    `${book.shifts.variance < 0 ? `⚠️ Shift cash short ${pkr(-book.shifts.variance)}\n` : ""}Report: ${config.publicUrl}/day/${dayToken(t, day)}`;
  if (owner) await sendDirect(t, { phone: owner, name: "Owner" }, "day_close", `day:${day}`, text);
  return { day, revenue: s.revenue, text };
}

backoffice.get("/day-closes", requirePerm("reports.view"), h((req) =>
  all("SELECT id, day, closed_at FROM day_closes WHERE tenant_id=? ORDER BY day DESC LIMIT 60", tid(req))
    .map((d) => ({ ...d, url: `${config.publicUrl}/day/${dayToken(tid(req), d.day)}` }))));
backoffice.post("/day-closes", requirePerm("reports.view"), h(async (req) => {
  if (!can(req.user, "settings.manage")) throw new AppError(403, "Only the admin can close a day by hand");
  const b = parse(z.object({ day: z.string().regex(/^\d{4}-\d{2}-\d{2}$/) }), req.body);
  if (b.day >= pkDate()) throw new AppError(400, "Only a finished day can be closed");
  return closeDay(tid(req), b.day);
}));

const esc = (v: unknown) => String(v ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]!));
const rs = (v: number | null | undefined) => (v == null ? "—" : `Rs ${Math.round(v).toLocaleString("en-IN")}`);
export function renderDay(token: string): string | null {
  let p: { day: string; t: number };
  try { p = jwt.verify(token, config.jwtSecret) as typeof p; } catch { return null; }
  const row = get("SELECT * FROM day_closes WHERE tenant_id=? AND day=?", p.t, p.day);
  if (!row) return null;
  const { book: b, cash: c, flows, unhanded } = JSON.parse(row.data);
  const f = flows ?? c; // days closed before flows were kept
  const tenant = get("SELECT name FROM tenants WHERE id=?", p.t)!.name;
  const kv = (k: string, v: string) => `<tr><td>${esc(k)}</td><td class=r>${esc(v)}</td></tr>`;
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Day report ${esc(p.day)} — ${esc(tenant)}</title>
<style>:root{color-scheme:light}body{font:14px/1.45 system-ui,sans-serif;margin:0;background:#f1f5f9;color:#0f172a}.page{max-width:820px;margin:16px auto;background:#fff;padding:22px;border-radius:12px}
h1{margin:0 0 4px;font-size:21px}h2{font-size:15px;margin:18px 0 6px;border-bottom:2px solid #064e3b;padding-bottom:3px}.muted{color:#64748b}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px}.tile{background:#f8fafc;border-radius:8px;padding:10px}.tile b{display:block;font-size:19px}
table{width:100%;border-collapse:collapse}td,th{padding:5px 8px;border-bottom:1px solid #e2e8f0;text-align:left}.r{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
button{margin-top:14px;padding:9px 16px;border:0;border-radius:8px;background:#064e3b;color:#fff;font-size:15px}@media print{button{display:none}body{background:#fff}.page{margin:0;padding:0}h2{margin:12px 0 4px}td,th{padding:3px 8px}.tile{padding:7px 10px}}@media(max-width:600px){.page{margin:0;border-radius:0;padding:14px}td{padding:6px 8px}.stock tr:first-child{display:none}.stock tr{display:grid;grid-template-columns:1fr 1fr;padding:6px 0;border-bottom:1px solid #e2e8f0}.stock td{border:0;background:none!important;padding:2px 8px}.stock td:first-child{grid-column:1/-1;font-weight:600}.stock td.r{text-align:left}.stock td[data-l]:before{content:attr(data-l)" ";color:#64748b}}
${BRAND_CSS}tr:nth-child(even) td{background:#f8fafc}th{background:#0f172a;color:#fff}</style></head><body><div class=page>
${brandHead(p.t, "Day report")}<h1>Day report · ${esc(p.day)}</h1><div class=muted>${esc(new Date(`${p.day}T12:00:00+05:00`).toLocaleDateString("en-PK", { weekday: "long", day: "numeric", month: "long", year: "numeric" }))} · closed ${esc(new Date(row.closed_at).toLocaleString("en-PK", { timeZone: "Asia/Karachi" }))}</div>
<div class=grid style="margin-top:12px"><div class=tile>Sales<b>${rs(b.sales.revenue)}</b></div><div class=tile>Expenses<b>${rs(b.expenses.total)}</b></div><div class=tile>Supply received<b>${Number(b.supply.litres).toLocaleString()} L</b></div><div class=tile>Office cash<b>${rs(c.cash_in_hand)}</b></div></div>
<h2>Sales</h2><table>${kv("Pump sales", `${rs(b.sales.retail)} · ${Math.round(b.sales.retail_litres).toLocaleString()} L · ${b.sales.txns} sales`)}${kv("Wholesale", rs(b.sales.wholesale))}${kv("Cash", rs(b.sales.cash))}${kv("Digital", rs(b.sales.digital))}${kv("Khata", rs(b.sales.khata))}${kv("Profit (est.)", rs(b.profit.net))}</table>
${(() => { const rent = rentalIncome(p.t, pkStart(p.day), pkEnd(p.day)); return rent > 0 ? `<h2>Other income</h2><table>${kv("Shop / hotel rent received", rs(rent))}</table>` : ""; })()}
<h2>Stock</h2><table class=stock><tr><th>Fuel</th><th class=r>Opening</th><th class=r>Received</th><th class=r>Sold</th><th class=r>Closing</th><th class=r>Value (cost)</th></tr>
${b.stock.products.map((x: any) => `<tr><td>${esc(x.name)}</td><td class=r data-l="Opening">${x.opening_l.toLocaleString()}</td><td class=r data-l="Received">${x.received_l.toLocaleString()}</td><td class=r data-l="Sold">${x.sold_l.toLocaleString()}</td><td class=r data-l="Closing">${x.closing_l.toLocaleString()}</td><td class=r data-l="Value">${rs(x.value_at_cost)}</td></tr>`).join("")}</table>
<h2>Expenses</h2><table>${b.expenses.by_category.map((e: any) => kv(e.category, rs(e.amount))).join("") || "<tr><td class=muted>None</td></tr>"}</table>
<h2>Cash</h2><table>${kv("Shift cash handed over", rs(f.ins.shift_cash))}${unhanded ? kv("Shift cash not yet handed over", rs(unhanded)) : ""}${kv("Khata / wholesale cash received", rs(f.ins.khata_cash + f.ins.wholesale_cash))}${kv("Other cash received", rs(f.total_in - f.ins.shift_cash - f.ins.khata_cash - f.ins.wholesale_cash))}${kv("Deposited in bank", rs(f.outs.bank_deposits))}${kv("Cash expenses & payments", rs(f.total_out - f.outs.bank_deposits))}${kv("Shift cash short / over", rs(b.shifts.variance))}${kv("Office cash at day end", rs(c.cash_in_hand))}</table>
<h2>Balances</h2><table>${kv("People owe us", rs(b.receivables))}${kv("We owe", rs(b.payables))}</table>
${brandFoot(p.t)}
<button onclick="print()">Print / Save as PDF</button></div></body></html>`;
}
