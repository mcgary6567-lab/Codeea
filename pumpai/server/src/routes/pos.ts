/** Endpoints for the big-button POS used by salesmen. */
import { Router } from "express";
import { all, get, pkDate, getSetting, type Row } from "../db.js";
import { h, tid, requirePerm, scopedStation, can } from "../auth.js";
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
    vehicles: all("SELECT plate_no FROM vehicles WHERE customer_id=? ORDER BY plate_no", c.id).map((v) => v.plate_no),
  };
}

/** A khata customer's / vehicle's usual fill (most common product + amount), to one-tap prefill the POS. */
pos.get("/pos/usual", h((req) => {
  const t = tid(req);
  const cid = Number(req.query.customer_id);
  const veh = String(req.query.vehicle ?? "").toUpperCase().trim();
  if (!cid || !get("SELECT id FROM customers WHERE id=? AND tenant_id=?", cid, t)) return { usual: null };
  const rows = veh
    ? all("SELECT product, amount, litres FROM sales WHERE customer_id=? AND payment_method='khata' AND COALESCE(pending,0)=0 AND UPPER(vehicle_no)=? ORDER BY id DESC LIMIT 20", cid, veh)
    : all("SELECT product, amount, litres FROM sales WHERE customer_id=? AND payment_method='khata' AND COALESCE(pending,0)=0 ORDER BY id DESC LIMIT 20", cid);
  if (rows.length < 2) return { usual: null };
  // most common (product + amount rounded to nearest 100)
  const tally = new Map<string, { product: string; amount: number; n: number; litres: number[] }>();
  for (const r of rows) {
    const amt = Math.round(r.amount / 100) * 100;
    const key = `${r.product}|${amt}`;
    const e = tally.get(key) ?? { product: r.product as string, amount: amt, n: 0, litres: [] as number[] };
    e.n++; e.litres.push(r.litres); tally.set(key, e);
  }
  const top = [...tally.values()].sort((a, b) => b.n - a.n)[0];
  if (top.n < 2) return { usual: null }; // no clear pattern
  const med = [...top.litres].sort((a, b) => a - b)[Math.floor(top.litres.length / 2)];
  return { usual: { product: top.product, amount: top.amount, litres: Math.round(med * 100) / 100, times: top.n } };
}));

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
    // khata lump-sum discount: how much this user may give per sale, and whether they can go above the salesman limit
    discount_max: can(u, "customers.edit") ? null : Number(getSetting(tid(req), "khata_discount_max", "500")),
    products: [...new Set(all("SELECT product FROM tanks WHERE station_id=?", stationId).map((t) => t.product))],
    shift: shift ? { ...shift, hours_open: (Date.now() - Date.parse(shift.opened_at)) / 3600_000, summary: shiftSummary(shift.id) } : null,
    recent: shift ? all(`SELECT s.id, s.product, s.litres, s.rate, s.amount, s.payment_method, s.vehicle_no, s.slip_no, s.photo_id, s.created_at, s.created_by, c.name customer_name, b.bank bank_name
      FROM sales s LEFT JOIN customers c ON c.id=s.customer_id LEFT JOIN bank_accounts b ON b.id=s.account_id WHERE s.shift_id=? ORDER BY s.id DESC LIMIT 12`, shift.id) : [],
  };
}));
