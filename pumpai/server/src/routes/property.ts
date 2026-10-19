/**
 * Property & rent:
 *  - Is the pump OWNED or RENTED? If rented, the monthly pump rent is booked as an expense by itself.
 *  - Units inside the pump (shop, hotel, tyre / service bay, ATM…) rented out to others — that rent is INCOME.
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, now, pkDate, getSetting, setSetting } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { AppError, round2 } from "../services.js";
import { bankAccountFor } from "./banks.js";
import { ensureCategories } from "./expenses.js";
import { linkPhotos } from "./capture.js";

export const property = Router();
const METHODS = ["cash", "bank", "jazzcash", "easypaisa", "raast", "cheque", "card"] as const;
const KINDS = ["shop", "hotel", "tyre", "service", "atm", "tuckshop", "office", "other"] as const;
const monthStr = () => pkDate().slice(0, 7);

const PUMP_RENT_NOTE = "Pump rent (auto)";
/** Keep the pump's own rent as a self-booking monthly expense when the pump is on rent. */
function syncPumpRent(t: number, ownership: string, rent: number, by: string) {
  const existing = get("SELECT * FROM recurring_expenses WHERE tenant_id=? AND note=?", t, PUMP_RENT_NOTE);
  if (ownership === "rented" && rent > 0) {
    ensureCategories(t);
    if (!get("SELECT id FROM expense_categories WHERE tenant_id=? AND name='Rent'", t))
      run("INSERT INTO expense_categories (tenant_id,name,monthly_budget) VALUES (?,?,0)", t, "Rent");
    if (existing) run("UPDATE recurring_expenses SET amount=?, active=1 WHERE id=?", rent, existing.id);
    else {
      const started = Number(pkDate().slice(8)) >= 1 ? monthStr() : null;
      run(`INSERT INTO recurring_expenses (tenant_id,station_id,category,amount,paid_to,method,day_of_month,note,last_month,created_by,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)`, t, null, "Rent", rent, "Pump landlord", "cash", 1, PUMP_RENT_NOTE, started, by, now());
    }
  } else if (existing) {
    run("UPDATE recurring_expenses SET active=0 WHERE id=?", existing.id);
  }
}

/** A rental unit with this month's rent status. */
function withStatus(r: any, month: string) {
  const paid = round2(get("SELECT COALESCE(SUM(amount),0) v FROM rental_payments WHERE rental_id=? AND for_month=?", r.id, month)!.v);
  return { ...r, month_paid: paid, month_due: round2(Math.max(0, r.monthly_rent - paid)), month_settled: paid >= r.monthly_rent - 0.01 };
}

property.get("/property", requirePerm("expenses.view"), h((req) => {
  const t = tid(req);
  const month = typeof req.query.month === "string" && /^\d{4}-\d{2}$/.test(req.query.month) ? req.query.month : monthStr();
  const ownership = getSetting(t, "pump_ownership", "owned");
  const pump_rent = Number(getSetting(t, "pump_rent", "0")) || 0;
  const rentals = all("SELECT r.*, s.name station_name FROM rentals r LEFT JOIN stations s ON s.id=r.station_id WHERE r.tenant_id=? ORDER BY r.active DESC, r.name", t)
    .map((r) => withStatus(r, month));
  const active = rentals.filter((r) => r.active);
  return {
    month, ownership, pump_rent,
    rentals,
    summary: {
      units: active.length,
      expected: round2(active.reduce((a, r) => a + r.monthly_rent, 0)),
      collected: round2(active.reduce((a, r) => a + r.month_paid, 0)),
      due: round2(active.reduce((a, r) => a + r.month_due, 0)),
      pump_rent: ownership === "rented" ? pump_rent : 0,
    },
  };
}));

property.post("/property/settings", requirePerm("settings.manage"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({ ownership: z.enum(["owned", "rented"]), pump_rent: z.number().min(0).max(100_000_000).default(0) }), req.body);
  setSetting(t, "pump_ownership", b.ownership);
  setSetting(t, "pump_rent", String(b.ownership === "rented" ? b.pump_rent : 0));
  syncPumpRent(t, b.ownership, b.pump_rent, req.user!.name);
  return { ownership: b.ownership, pump_rent: b.ownership === "rented" ? b.pump_rent : 0 };
}));

const rentalBody = z.object({
  name: z.string().min(1).max(60), kind: z.enum(KINDS).default("shop"), tenant_name: z.string().max(80).optional().nullable(),
  phone: z.string().max(30).optional().nullable(), monthly_rent: z.number().min(0).max(100_000_000), deposit: z.number().min(0).max(100_000_000).default(0),
  start_day: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional().nullable(), station_id: z.number().optional().nullable(), note: z.string().max(200).optional().nullable(),
});
property.post("/rentals", requirePerm("settings.manage"), h((req) => {
  const t = tid(req);
  const b = parse(rentalBody, req.body);
  const { id } = run(`INSERT INTO rentals (tenant_id,station_id,name,kind,tenant_name,phone,monthly_rent,deposit,start_day,active,note,created_by,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?)`, t, b.station_id ?? null, b.name, b.kind, b.tenant_name ?? null, b.phone ?? null, b.monthly_rent, b.deposit,
    b.start_day ?? null, b.note ?? null, req.user!.name, now());
  return get("SELECT * FROM rentals WHERE id=?", id);
}));
property.patch("/rentals/:id", requirePerm("settings.manage"), h((req) => {
  const t = tid(req);
  const r = get("SELECT * FROM rentals WHERE id=? AND tenant_id=?", Number(req.params.id), t);
  if (!r) throw new AppError(404, "Unit not found");
  const b = parse(rentalBody.partial().extend({ active: z.boolean().optional() }), req.body);
  const m = { ...r, ...b };
  run(`UPDATE rentals SET name=?, kind=?, tenant_name=?, phone=?, monthly_rent=?, deposit=?, start_day=?, station_id=?, note=?, active=? WHERE id=?`,
    m.name, m.kind, m.tenant_name ?? null, m.phone ?? null, m.monthly_rent, m.deposit, m.start_day ?? null, m.station_id ?? null, m.note ?? null,
    b.active === undefined ? r.active : b.active ? 1 : 0, r.id);
  return get("SELECT * FROM rentals WHERE id=?", r.id);
}));

/** Record rent received for a unit. The money lands in cash (or a bank account) like any other receipt. */
property.post("/rentals/:id/pay", requirePerm("cash.receive"), h((req) => {
  const t = tid(req);
  const r = get("SELECT * FROM rentals WHERE id=? AND tenant_id=?", Number(req.params.id), t);
  if (!r) throw new AppError(404, "Unit not found");
  const b = parse(z.object({
    amount: z.number().positive().max(100_000_000), for_month: z.string().regex(/^\d{4}-\d{2}$/).optional(),
    method: z.enum(METHODS).default("cash"), account_id: z.number().optional().nullable(), ref: z.string().max(60).optional().nullable(),
    note: z.string().max(200).optional().nullable(), photo_ids: z.array(z.number()).max(10).optional(),
  }), req.body);
  const forMonth = b.for_month ?? monthStr();
  const isCash = /^cash$/i.test(b.method);
  const account = bankAccountFor(t, b.account_id, b.method);
  if (!isCash && !account) throw new AppError(400, "Choose the bank account the rent came into");
  const note = `Rent — ${r.name}${r.tenant_name ? ` (${r.tenant_name})` : ""} · ${forMonth}${b.note ? ` · ${b.note}` : ""}`;
  // a voucher so the office cash book counts it (party_type 'other' = other money in); or a bank deposit line
  const { id: vid } = run(`INSERT INTO cashier_vouchers (tenant_id,direction,party_type,party_name,amount,method,account_id,category,ref,note,src,created_by,created_at)
    VALUES (?, 'in', 'other', ?, ?, ?, ?, 'Shop / hotel rent', ?, ?, ?, ?, ?)`,
    t, r.name, b.amount, b.method, account, b.ref ?? null, note, `rental:${r.id}`, req.user!.name, now());
  if (account) run("INSERT INTO bank_txns (tenant_id,account_id,kind,amount,party,ref,note,txn_date,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
    t, account, "other_in", b.amount, r.name, b.ref ?? null, note, now(), req.user!.name, now());
  const { id: pid } = run(`INSERT INTO rental_payments (tenant_id,rental_id,for_month,amount,method,account_id,ref,note,voucher_id,received_by,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)`, t, r.id, forMonth, b.amount, b.method, account, b.ref ?? null, b.note ?? null, vid, req.user!.name, now());
  if (b.photo_ids?.length) linkPhotos(t, b.photo_ids, `rentpay:${pid}`);
  return { ok: true, rental: withStatus(r, monthStr()), received: b.amount };
}));

/** Rent income collected in a period — added to reports and the day report. */
export function rentalIncome(t: number, fromIso: string, toIso: string) {
  return round2(get("SELECT COALESCE(SUM(amount),0) v FROM rental_payments WHERE tenant_id=? AND created_at >= ? AND created_at < ?", t, fromIso, toIso)!.v);
}
