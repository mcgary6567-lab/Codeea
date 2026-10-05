/**
 * Legal stock register (Explosives / OGRA daily stock register and monthly return), made by itself
 * from sales, tanker receipts, wholesale supplies and dips — ready to print for inspection.
 *
 * Book stock is worked back from today's tank stock: closing(day) = stock now − everything that moved after that day.
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, pkDate, pkStart, pkEnd, type Row } from "../db.js";
import { h, parse, tid, requirePerm, scopedStation } from "../auth.js";
import { AppError, round2 } from "../services.js";
import { PRODUCTS } from "../config.js";

export const register = Router();
const DAY = 86_400_000;
const dateStr = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);

/** Litres in and out of a station's tanks for one product in [from, to). */
function moves(stationId: number, product: string, from: string, to: string) {
  const tanks = all("SELECT id FROM tanks WHERE station_id=? AND product=?", stationId, product).map((t) => t.id);
  if (!tanks.length) return { receipts: 0, sales: 0, wholesale: 0, dip_adj: 0 };
  const inT = `(${tanks.join(",")})`;
  const v = (sql: string, ...a: unknown[]) => round2(get(sql, ...(a as []))!.v ?? 0);
  return {
    receipts: v(`SELECT COALESCE(SUM(received_l),0) v FROM deliveries WHERE tank_id IN ${inT} AND created_at >= ? AND created_at < ?`, from, to),
    sales: v("SELECT COALESCE(SUM(litres),0) v FROM sales WHERE station_id=? AND product=? AND created_at >= ? AND created_at < ?", stationId, product, from, to),
    wholesale: v(`SELECT COALESCE(SUM(CASE WHEN type='supply' THEN litres WHEN type='return' THEN -litres END),0) v FROM wholesale_txns WHERE tank_id IN ${inT} AND voided=0 AND created_at >= ? AND created_at < ?`, from, to),
    dip_adj: v(`SELECT COALESCE(SUM(measured_l - book_l),0) v FROM dip_readings WHERE tank_id IN ${inT} AND created_at >= ? AND created_at < ?`, from, to),
  };
}
const net = (m: ReturnType<typeof moves>) => m.receipts - m.sales - m.wholesale + m.dip_adj;

export function stockRegister(t: number, stationId: number, from: string, to: string) {
  const station = get("SELECT * FROM stations WHERE id=? AND tenant_id=?", stationId, t);
  if (!station) throw new AppError(404, "Station not found");
  const days: string[] = [];
  for (let d = Date.parse(`${from}T12:00:00+05:00`); d <= Date.parse(`${to}T12:00:00+05:00`); d += DAY) days.push(pkDate(d));
  if (days.length > 62) throw new AppError(400, "Choose up to 62 days");
  const nowIso = new Date().toISOString();
  const products = all("SELECT product, SUM(current_l) stock, SUM(capacity_l) capacity, COUNT(*) tanks FROM tanks WHERE station_id=? GROUP BY product ORDER BY product", stationId).map((p) => {
    const tankIds = all("SELECT id FROM tanks WHERE station_id=? AND product=?", stationId, p.product).map((x) => x.id);
    let closing = round2(p.stock - net(moves(stationId, p.product, pkEnd(to), nowIso)));
    const rows: Row[] = [];
    for (const day of [...days].reverse()) {
      const m = moves(stationId, p.product, pkStart(day), pkEnd(day));
      const opening = round2(closing - net(m));
      const receipts = all(`SELECT d.tanker_no, d.supplier, d.invoice_l, d.received_l, d.shortage_pct, d.created_at, s.name supplier_name
        FROM deliveries d LEFT JOIN suppliers s ON s.id=d.supplier_id WHERE d.tank_id IN (${tankIds.join(",")}) AND d.created_at >= ? AND d.created_at < ? ORDER BY d.id`, pkStart(day), pkEnd(day));
      // physical stock: the last dip of the day in each tank (only when every tank was dipped)
      const dips = tankIds.map((id) => get("SELECT measured_l, measured_cm FROM dip_readings WHERE tank_id=? AND created_at >= ? AND created_at < ? ORDER BY id DESC LIMIT 1", id, pkStart(day), pkEnd(day)));
      const dip = dips.every(Boolean) ? round2(dips.reduce((a, x) => a + x!.measured_l, 0)) : null;
      rows.push({
        day, opening, receipts: m.receipts, receipt_lines: receipts, total: round2(opening + m.receipts), sales: m.sales, wholesale: m.wholesale,
        book_closing: round2(opening + m.receipts - m.sales - m.wholesale), dip_closing: dip, gain_loss: m.dip_adj,
        closing,
      });
      closing = opening;
    }
    rows.reverse();
    const sum = (k: "receipts" | "sales" | "wholesale" | "gain_loss") => round2(rows.reduce((a, r) => a + r[k], 0));
    const sold = sum("sales") + sum("wholesale");
    return {
      product: p.product, name: PRODUCTS[p.product] ?? p.product, tanks: p.tanks, capacity: p.capacity, days: rows,
      month: { opening: rows[0]?.opening ?? 0, receipts: sum("receipts"), sales: sum("sales"), wholesale: sum("wholesale"), gain_loss: sum("gain_loss"),
        closing: rows[rows.length - 1]?.closing ?? 0, variation_pct: sold ? round2((sum("gain_loss") / sold) * 100) : 0 },
    };
  });
  const licences = all(`SELECT name, number, authority, expires_on FROM licences WHERE tenant_id=? AND (station_id=? OR station_id IS NULL)
    AND (name LIKE '%xplosive%' OR name LIKE '%OGRA%' OR name LIKE '%DPC%' OR authority LIKE '%xplosive%' OR authority LIKE '%OGRA%') ORDER BY name`, t, stationId);
  return {
    tenant: get("SELECT name, owner_name FROM tenants WHERE id=?", t), station: { id: station.id, name: station.name, address: station.address, city: station.city },
    from, to, licences, products,
  };
}

register.get("/register", requirePerm("stock.manage"), h((req) => {
  const q = parse(z.object({ station_id: z.coerce.number().optional(), from: dateStr.optional(), to: dateStr.optional(), month: z.string().regex(/^\d{4}-\d{2}$/).optional() }), req.query);
  const station = scopedStation(req, q.station_id ?? null) ?? get("SELECT id FROM stations WHERE tenant_id=? ORDER BY id LIMIT 1", tid(req))!.id;
  let from = q.from, to = q.to;
  if (q.month) {
    from = `${q.month}-01`;
    const last = pkDate(Date.parse(`${q.month}-01T12:00:00+05:00`) + 31 * DAY).slice(0, 7);
    to = pkDate(Date.parse(`${last}-01T12:00:00+05:00`) - DAY);
  }
  to = to ?? pkDate();
  if (to > pkDate()) to = pkDate();
  from = from ?? pkDate(Date.parse(`${to}T12:00:00+05:00`) - 6 * DAY);
  if (from > to) throw new AppError(400, "From date is after the to date");
  return stockRegister(tid(req), station, from, to);
}));
