/** Endpoints for the big-button POS used by salesmen. */
import { Router } from "express";
import { all, get, pkDate, type Row } from "../db.js";
import { h, tid, requirePerm, scopedStation } from "../auth.js";
import { AppError, currentPrices, UNDO_SECONDS } from "../services.js";
import { institutionTypes } from "./lookups.js";
import { shiftSummary } from "../shifts.js";

export const pos = Router();
pos.use("/pos", requirePerm("sales.create"));



/**
 * Khata (credit) accounts the salesman can charge: institutions first, then fleets, farmers, businesses.
 * Exact balances are not exposed to salesmen — only whether the account can still take credit.
 */
/** A khata account as the POS sees it (salesmen do not see balances). */
function khataItem(c: Row, showBalance: boolean) {
  const used = c.balance / c.credit_limit;
  return {
    id: c.id, name: c.name, type: c.type, city: c.city,
    is_new: Date.now() - Date.parse(c.created_at) < 3 * 86_400_000,
    status: c.khata_blocked ? "full" : used >= 1 ? "full" : used >= 0.9 ? "near" : "ok", blocked: Boolean(c.khata_blocked),
    ...(showBalance ? { balance: c.balance, credit_limit: c.credit_limit, available: Math.max(0, c.credit_limit - c.balance) } : {}),
    // the CEO's own fuel rate for this account (112, police, govt…): the POS bills at this, not the pump rate
    rates: Object.fromEntries(all("SELECT product, rate FROM customer_rates WHERE customer_id=?", c.id).map((r) => [r.product, r.rate])),
    vehicles: all("SELECT plate_no FROM vehicles WHERE customer_id=? ORDER BY plate_no", c.id).map((v) => v.plate_no),
  };
}

/** Bank POS machines (active bank accounts) a salesman can attribute a card / digital sale to. */
pos.get("/pos/bank-pos", h(async (req) => {
  const { accountName } = await import("./banks.js");
  return { accounts: all("SELECT * FROM bank_accounts WHERE tenant_id=? AND active=1 ORDER BY bank, id", tid(req)).map((a) => ({ id: a.id, name: accountName(a), bank: a.bank })) };
}));

/** Scan a QR card (account card or vehicle sticker) to pick the khata account and vehicle in one go. */
pos.get("/pos/card/:code", h((req) => {
  const code = String(req.params.code).toUpperCase().replace(/^PUMPAI-/, "").trim();
  const v = get(`SELECT v.plate_no, c.* FROM vehicles v JOIN customers c ON c.id=v.customer_id WHERE v.card_code=? AND c.tenant_id=?`, code, tid(req));
  const c = v ?? get("SELECT * FROM customers WHERE card_code=? AND tenant_id=?", code, tid(req));
  if (!c) throw new AppError(404, "Card not found. Ask the manager.");
  if (!(c.credit_limit > 0)) throw new AppError(400, `${c.name} has no khata account`);
  return { account: khataItem(c, req.user!.role !== "salesman"), vehicle: v?.plate_no ?? null };
}));

pos.get("/pos/khata-accounts", h((req) => {
  const rows = all("SELECT id, name, type, city, balance, credit_limit, khata_blocked, created_at FROM customers WHERE tenant_id=? AND credit_limit > 0 AND active=1 ORDER BY name", tid(req));
  const inst = new Set(institutionTypes(tid(req))); // institutions first (Settings → Lists → Customer types)
  const order = (t: string) => (inst.has(t) ? 0 : t === "fleet" ? 1 : t === "farmer" ? 2 : 3);
  const showBalance = req.user!.role !== "salesman";
  return rows
    .map((c) => khataItem(c, showBalance))
    .sort((a, b) => order(a.type) - order(b.type) || a.name.localeCompare(b.name));
}));

/** The salesman's current shift at a glance (or the latest open shift at a station for managers). */
pos.get("/pos/today", h((req) => {
  const u = req.user!;
  const stationId = u.role === "salesman" ? scopedStation(req) : Number(req.query.station_id) || get("SELECT id FROM stations WHERE tenant_id=? ORDER BY id LIMIT 1", tid(req))!.id;
  const shift = u.role === "salesman"
    ? get("SELECT * FROM shifts WHERE station_id=? AND attendant=? AND status='open' ORDER BY id DESC LIMIT 1", stationId, u.name)
    : get("SELECT * FROM shifts WHERE station_id=? AND status='open' ORDER BY id DESC LIMIT 1", stationId);
  return {
    server_time: new Date().toISOString(), undo_seconds: UNDO_SECONDS,
    // salesman must mark attendance before the shift can be started
    attendance: u.role === "salesman" ? (get("SELECT id, check_in FROM attendance WHERE user_id=? AND day=?", u.id, pkDate()) ?? null) : null,
    station: get("SELECT id, name FROM stations WHERE id=?", stationId),
    prices: Object.fromEntries(Object.entries(currentPrices(tid(req))).map(([k, v]) => [k, v.price])),
    products: [...new Set(all("SELECT product FROM tanks WHERE station_id=?", stationId).map((t) => t.product))],
    shift: shift ? { ...shift, hours_open: (Date.now() - Date.parse(shift.opened_at)) / 3600_000, summary: shiftSummary(shift.id) } : null,
    recent: shift ? all(`SELECT s.id, s.product, s.litres, s.rate, s.amount, s.payment_method, s.vehicle_no, s.slip_no, s.photo_id, s.created_at, s.created_by, c.name customer_name, b.bank bank_name
      FROM sales s LEFT JOIN customers c ON c.id=s.customer_id LEFT JOIN bank_accounts b ON b.id=s.account_id WHERE s.shift_id=? ORDER BY s.id DESC LIMIT 12`, shift.id) : [],
  };
}));
