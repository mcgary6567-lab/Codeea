/**
 * Fuel sales history: every POS / meter sale in a date range with filters and totals — the page the owner opens to find
 * "that 40-litre sale to the white Corolla last Tuesday". A salesman sees only his own sales.
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, pkDate, pkStart, pkEnd } from "../db.js";
import { h, parse, tid, requirePerm, scopedStation } from "../auth.js";
import { round2 } from "../services.js";

export const salesHistory = Router();

const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const q = z.object({
  from: day.optional(), to: day.optional(), station_id: z.coerce.number().int().optional(), payment_method: z.string().max(30).optional(),
  product: z.string().max(20).optional(), customer_id: z.coerce.number().int().optional(), q: z.string().trim().max(80).optional(),
  limit: z.coerce.number().int().min(1).max(1000).default(200),
});

salesHistory.get("/sales/history", requirePerm("sales.view"), h((req) => {
  const t = tid(req);
  const f = parse(q, req.query);
  const to = f.to ?? pkDate(), from = f.from ?? to;
  const where = ["st.tenant_id=?", "s.created_at >= ?", "s.created_at < ?"]; const P: (string | number)[] = [t, pkStart(from), pkEnd(to)];
  const own = scopedStation(req);
  if (own) { where.push("s.station_id=?"); P.push(Number(own)); }
  if (f.station_id) { where.push("s.station_id=?"); P.push(f.station_id); }
  if (f.payment_method) { where.push("s.payment_method=?"); P.push(f.payment_method); }
  if (f.product) { where.push("s.product=?"); P.push(f.product); }
  if (f.customer_id) { where.push("s.customer_id=?"); P.push(f.customer_id); }
  // a salesman only sees what he sold
  if (req.user!.role === "salesman") { where.push("sh.attendant=?"); P.push(req.user!.name); }
  if (f.q) for (const w of f.q.split(/\s+/).filter(Boolean)) {
    where.push("(COALESCE(c.name,'') LIKE ? OR COALESCE(s.vehicle_no,'') LIKE ? OR COALESCE(s.slip_no,'') LIKE ? OR COALESCE(sh.attendant,'') LIKE ? OR CAST(s.amount AS TEXT) LIKE ?)");
    const like = `%${w}%`; P.push(like, like, like, like, like);
  }
  const W = where.join(" AND ");
  const FROM = `FROM sales s JOIN stations st ON st.id=s.station_id LEFT JOIN customers c ON c.id=s.customer_id LEFT JOIN shifts sh ON sh.id=s.shift_id`;
  const rows = all(`SELECT s.id, s.created_at, st.name station_name, s.station_id, s.shift_id, sh.attendant, s.product, s.litres, s.rate, s.amount, s.discount, s.payment_method,
      s.customer_id, c.name customer_name, s.vehicle_no, s.slip_no, s.source, s.at_close ${FROM} WHERE ${W} ORDER BY s.id DESC LIMIT ?`, ...P, f.limit);
  const tot = get(`SELECT COUNT(*) n, COALESCE(SUM(s.litres),0) litres, COALESCE(SUM(s.amount),0) amount ${FROM} WHERE ${W}`, ...P)!;
  return {
    from, to, rows, limit: f.limit,
    totals: {
      n: tot.n, litres: round2(tot.litres), amount: round2(tot.amount),
      by_method: all(`SELECT s.payment_method method, COUNT(*) n, ROUND(SUM(s.amount),2) amount ${FROM} WHERE ${W} GROUP BY s.payment_method ORDER BY amount DESC`, ...P),
      by_product: all(`SELECT s.product, COUNT(*) n, ROUND(SUM(s.litres),2) litres, ROUND(SUM(s.amount),2) amount ${FROM} WHERE ${W} GROUP BY s.product ORDER BY amount DESC`, ...P),
    },
  };
}));
