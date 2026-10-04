/** Endpoints for the big-button POS used by salesmen. */
import { Router } from "express";
import { all, get } from "../db.js";
import { h, tid, requirePerm, scopedStation } from "../auth.js";
import { currentPrices } from "../services.js";
import { shiftSummary } from "../shifts.js";

export const pos = Router();
pos.use("/pos", requirePerm("sales.create"));

export const INSTITUTION_TYPES = ["police", "school", "government", "hospital"];

/**
 * Khata (credit) accounts the salesman can charge: institutions first, then fleets, farmers, businesses.
 * Exact balances are not exposed to salesmen — only whether the account can still take credit.
 */
pos.get("/pos/khata-accounts", h((req) => {
  const rows = all("SELECT id, name, type, city, balance, credit_limit, created_at FROM customers WHERE tenant_id=? AND credit_limit > 0 ORDER BY name", tid(req));
  const order = (t: string) => (INSTITUTION_TYPES.includes(t) ? 0 : t === "fleet" ? 1 : t === "farmer" ? 2 : 3);
  const showBalance = req.user!.role !== "salesman";
  return rows
    .map((c) => {
      const used = c.balance / c.credit_limit;
      return {
        id: c.id, name: c.name, type: c.type, city: c.city,
        is_new: Date.now() - Date.parse(c.created_at) < 3 * 86_400_000,
        status: used >= 1 ? "full" : used >= 0.9 ? "near" : "ok",
        ...(showBalance ? { balance: c.balance, credit_limit: c.credit_limit, available: Math.max(0, c.credit_limit - c.balance) } : {}),
        vehicles: all("SELECT plate_no FROM vehicles WHERE customer_id=? ORDER BY plate_no", c.id).map((v) => v.plate_no),
      };
    })
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
    station: get("SELECT id, name FROM stations WHERE id=?", stationId),
    prices: Object.fromEntries(Object.entries(currentPrices(tid(req))).map(([k, v]) => [k, v.price])),
    products: [...new Set(all("SELECT product FROM tanks WHERE station_id=?", stationId).map((t) => t.product))],
    shift: shift ? { ...shift, hours_open: (Date.now() - Date.parse(shift.opened_at)) / 3600_000, summary: shiftSummary(shift.id) } : null,
    recent: shift ? all(`SELECT s.id, s.product, s.litres, s.rate, s.amount, s.payment_method, s.vehicle_no, s.slip_no, s.created_at, c.name customer_name
      FROM sales s LEFT JOIN customers c ON c.id=s.customer_id WHERE s.shift_id=? ORDER BY s.id DESC LIMIT 12`, shift.id) : [],
  };
}));
