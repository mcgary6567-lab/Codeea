/**
 * How customers and staff are doing:
 *  - Rating after each fill on WhatsApp (reply 1-5); a bad rating asks what went wrong, logs a complaint
 *    and alerts the manager. Every salesman gets a customer score.
 *  - Salesman commission on lubricants / shop sales (and optionally per litre), added to salary
 *  - Weekly leaderboard sent to the staff
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, now, getSetting, setSetting, pkStart, pkEnd, pkDate, type Row } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { round2, pkr, createAlert } from "../services.js";
import { sendWhatsApp } from "../whatsapp/cloud.js";
import { notify, staff } from "../notifications.js";
import { SHOP_CATEGORIES } from "./shop.js";

export const feedback = Router();
const DAY = 86_400_000;

/* ================= Ratings ================= */
/** WhatsApp the customer after a fill (at most once a day each), if switched on. */
export async function askRating(t: number, sale: Row) {
  if (!sale.customer_id || getSetting(t, "ask_rating", "1") === "0") return;
  const c = get("SELECT * FROM customers WHERE id=?", sale.customer_id);
  if (!c?.phone) return;
  if (get("SELECT id FROM ratings WHERE customer_id=? AND asked_at >= ?", c.id, new Date(Date.now() - 20 * 3600_000).toISOString())) return;
  run("INSERT INTO ratings (tenant_id,customer_id,sale_id,station_id,salesman_id,asked_at) VALUES (?,?,?,?,?,?)",
    t, c.id, sale.id, sale.station_id, sale.created_by ?? null, now());
  await sendWhatsApp(t, c, `Shukriya ${c.name.split(" ")[0]}! ⛽\nAaj ki service kaisi thi? *1 se 5* tak number bhejein (5 = behtareen, 1 = kharab).`, "system", { kind: "rating_ask" });
}

/** A customer's WhatsApp that answers the rating question. Returns the reply, or null if it is not one. */
export async function handleRatingReply(t: number, customer: Row, text: string): Promise<string | null> {
  const open = get("SELECT * FROM ratings WHERE customer_id=? AND asked_at >= ? ORDER BY id DESC LIMIT 1", customer.id, new Date(Date.now() - 24 * 3600_000).toISOString());
  if (!open) return null;
  const score = text.trim().match(/^([1-5])\s*(⭐|\*|star|stars)?\s*[.!]?$/i);
  if (open.status === "asked" && score) {
    const n = Number(score[1]);
    run("UPDATE ratings SET score=?, status=?, rated_at=? WHERE id=?", n, n <= 2 ? "low" : "rated", now(), open.id);
    if (n >= 4) {
      const review = getSetting(t, "google_review_url");
      return `Bohat shukriya! 🙏 ${n === 5 ? "Aap ki 5 star rating hamari team tak pohncha di gayi hai." : ""}${review ? `\nGoogle par review dein: ${review}` : ""}`;
    }
    if (n === 3) return "Shukriya! Hum aur behtar karne ki koshish karenge. Koi mashwara ho to zaroor likhein.";
    const st = get("SELECT name FROM stations WHERE id=?", open.station_id)?.name ?? "";
    const salesman = open.salesman_id ? get("SELECT name FROM users WHERE id=?", open.salesman_id)?.name : null;
    const title = `⭐ ${n}/5 rating from ${customer.name}`;
    const body = `${st}${salesman ? ` · salesman ${salesman}` : ""} · sale #${open.sale_id ?? "-"}. Asked the customer what went wrong.`;
    createAlert(t, { station_id: open.station_id, type: "low_rating", severity: "warning", title, body, dedupe_key: `rating-${open.id}` });
    await notify(t, staff(t, ["admin", "manager"]), { type: "low_rating", title, body });
    return "Maazrat chahte hain 😔 Bataiye kya masla hua? (Naap, rawaiya, intezar, safai…) Hum zaroor theek karenge.";
  }
  // the reason after a low rating becomes a complaint
  if (open.status === "low" && !open.comment && open.rated_at && Date.now() - Date.parse(open.rated_at) < 6 * 3600_000 && text.trim().length >= 3) {
    const { id } = run("INSERT INTO complaints (tenant_id,customer_id,station_id,category,message,sentiment,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
      t, customer.id, open.station_id, "service", `Rating ${open.score}/5: ${text.trim()}`, "negative", "open", now());
    run("UPDATE ratings SET comment=?, complaint_id=? WHERE id=?", text.trim().slice(0, 500), id, open.id);
    await notify(t, staff(t, ["admin", "manager"]), { type: "low_rating", title: `Complaint #${id} from ${customer.name}`, body: text.trim().slice(0, 200) });
    return `Shikayat #${id} darj ho gayi hai. Manager jald aap se raabta karenge. Shukriya.`;
  }
  return null;
}

function range(q: Record<string, unknown>) {
  const days = Math.min(365, Number(q.days ?? 30) || 30);
  const from = q.from ? pkStart(String(q.from)) : new Date(Date.now() - days * DAY).toISOString();
  const to = q.to ? pkEnd(String(q.to)) : new Date().toISOString();
  return { from, to };
}

export function ratingsBySalesman(t: number, from: string, to: string) {
  return all(`SELECT u.id, u.name, COUNT(r.id) n, ROUND(AVG(r.score),2) avg, SUM(CASE WHEN r.score <= 2 THEN 1 ELSE 0 END) low
    FROM ratings r JOIN users u ON u.id=r.salesman_id WHERE r.tenant_id=? AND r.score IS NOT NULL AND r.rated_at >= ? AND r.rated_at < ? GROUP BY u.id ORDER BY avg DESC`, t, from, to);
}

feedback.get("/ratings", requirePerm("complaints.manage"), h((req) => {
  const t = tid(req);
  const { from, to } = range(req.query);
  const s = get(`SELECT COUNT(*) asked, COUNT(score) rated, ROUND(AVG(score),2) avg, SUM(CASE WHEN score<=2 THEN 1 ELSE 0 END) low FROM ratings WHERE tenant_id=? AND asked_at >= ? AND asked_at < ?`, t, from, to)!;
  return {
    from, to, enabled: getSetting(t, "ask_rating", "1") === "1",
    summary: { ...s, response_pct: s.asked ? Math.round((s.rated / s.asked) * 100) : 0 },
    stars: [5, 4, 3, 2, 1].map((n) => ({ score: n, n: get("SELECT COUNT(*) n FROM ratings WHERE tenant_id=? AND score=? AND rated_at >= ? AND rated_at < ?", t, n, from, to)!.n })),
    by_salesman: ratingsBySalesman(t, from, to),
    by_station: all(`SELECT s.name, COUNT(r.score) n, ROUND(AVG(r.score),2) avg FROM ratings r JOIN stations s ON s.id=r.station_id WHERE r.tenant_id=? AND r.score IS NOT NULL AND r.rated_at >= ? AND r.rated_at < ? GROUP BY s.id`, t, from, to),
    recent: all(`SELECT r.*, c.name customer_name, u.name salesman_name FROM ratings r JOIN customers c ON c.id=r.customer_id LEFT JOIN users u ON u.id=r.salesman_id
      WHERE r.tenant_id=? AND r.score IS NOT NULL ORDER BY r.rated_at DESC LIMIT 30`, t),
  };
}));

/* ================= Commission ================= */
const DEFAULT_RATES: Record<string, number> = { lubricant: 3, filter: 2, coolant: 2, battery: 1, tyre: 1, tuck: 0, service: 0, other: 0 };
export function commissionRates(t: number) {
  let r: Record<string, number> = {};
  try { r = JSON.parse(getSetting(t, "commission_rates", "{}")); } catch { /* default */ }
  return { shop: { ...DEFAULT_RATES, ...r }, per_litre: Number(getSetting(t, "commission_per_litre", "0")) };
}

/** Commission each salesman earned in a period: % of their shop sales by category, plus Rs per litre if set. */
export function commission(t: number, from: string, to: string, userId?: number) {
  const rates = commissionRates(t);
  const shop = all(`SELECT ss.created_by uid, i.category, SUM(l.qty * l.price) amount FROM shop_sales ss JOIN shop_sale_lines l ON l.sale_id=ss.id JOIN shop_items i ON i.id=l.item_id
    WHERE ss.tenant_id=? AND ss.created_at >= ? AND ss.created_at < ? ${userId ? "AND ss.created_by=?" : ""} GROUP BY ss.created_by, i.category`, t, from, to, ...(userId ? [userId] : []));
  const fuel = all(`SELECT s.created_by uid, SUM(s.litres) litres FROM sales s JOIN stations st ON st.id=s.station_id
    WHERE st.tenant_id=? AND s.source='pos' AND s.created_at >= ? AND s.created_at < ? ${userId ? "AND s.created_by=?" : ""} GROUP BY s.created_by`, t, from, to, ...(userId ? [userId] : []));
  const users = all(`SELECT id, name FROM users WHERE tenant_id=? AND role='salesman' ${userId ? "AND id=?" : "AND active=1"} ORDER BY name`, t, ...(userId ? [userId] : []));
  return users.map((u) => {
    const lines = shop.filter((s) => s.uid === u.id).map((s) => ({ category: s.category, sales: round2(s.amount), pct: rates.shop[s.category] ?? 0, commission: round2((s.amount * (rates.shop[s.category] ?? 0)) / 100) }));
    const litres = round2(fuel.find((f) => f.uid === u.id)?.litres ?? 0);
    const shopC = round2(lines.reduce((a, l) => a + l.commission, 0));
    const fuelC = round2(litres * rates.per_litre);
    return { user_id: u.id, name: u.name, shop_sales: round2(lines.reduce((a, l) => a + l.sales, 0)), lines, litres, shop_commission: shopC, fuel_commission: fuelC, total: round2(shopC + fuelC) };
  });
}
const monthRange = (m: string) => {
  const [y, mo] = m.split("-").map(Number);
  const next = mo === 12 ? `${y + 1}-01` : `${y}-${String(mo + 1).padStart(2, "0")}`;
  return { from: pkStart(`${m}-01`), to: pkStart(`${next}-01`) };
};
export const commissionForMonth = (t: number, userId: number, month: string) => {
  const r = monthRange(month);
  return commission(t, r.from, r.to, userId)[0]?.total ?? 0;
};

feedback.get("/commission", requirePerm("staff.manage"), h((req) => {
  const month = String(req.query.month ?? pkDate().slice(0, 7));
  const r = monthRange(month);
  const rows = commission(tid(req), r.from, r.to);
  return { month, rates: commissionRates(tid(req)), categories: SHOP_CATEGORIES, salesmen: rows, total: round2(rows.reduce((a, x) => a + x.total, 0)) };
}));
feedback.put("/commission/settings", requirePerm("settings.manage"), h((req) => {
  const b = parse(z.object({ shop: z.record(z.enum(SHOP_CATEGORIES), z.number().min(0).max(50)), per_litre: z.number().min(0).max(10).default(0) }), req.body);
  setSetting(tid(req), "commission_rates", JSON.stringify(b.shop));
  setSetting(tid(req), "commission_per_litre", String(b.per_litre));
  return commissionRates(tid(req));
}));

/* ================= Leaderboard ================= */
export function leaderboard(t: number, from: string, to: string) {
  const com = commission(t, from, to);
  const ratings = ratingsBySalesman(t, from, to);
  return com.map((c) => {
    const fuel = get(`SELECT COALESCE(SUM(s.amount),0) amount, COUNT(*) n FROM sales s WHERE s.created_by=? AND s.source='pos' AND s.created_at >= ? AND s.created_at < ?`, c.user_id, from, to)!;
    const short = get("SELECT COALESCE(SUM(amount),0) v FROM staff_ledger WHERE user_id=? AND type='shortage' AND created_at >= ? AND created_at < ?", c.user_id, from, to)!.v;
    const r = ratings.find((x) => x.id === c.user_id);
    // points: 1 per 100 L, 1 per Rs 500 shop sales, rating bonus, shortage penalty
    const points = Math.round(c.litres / 100 + c.shop_sales / 500 + (r?.avg ? (r.avg - 3) * 10 : 0) - short / 200);
    return { ...c, fuel_amount: round2(fuel.amount), fills: fuel.n, rating: r?.avg ?? null, ratings: r?.n ?? 0, shortage: round2(short), points };
  }).sort((a, b) => b.points - a.points).map((x, i) => ({ ...x, rank: i + 1 }));
}
feedback.get("/leaderboard", requirePerm("sales.create"), h((req) => {
  const { from, to } = range({ days: 7, ...req.query });
  return { from, to, rows: leaderboard(tid(req), from, to) };
}));

/** Monday morning: everyone sees the week's top 3 and their own place. */
export async function weeklyLeaderboard(t: number) {
  const to = pkStart(pkDate()), from = new Date(Date.parse(to) - 7 * DAY).toISOString();
  const rows = leaderboard(t, from, to).filter((r) => r.litres > 0 || r.shop_sales > 0);
  if (!rows.length) return 0;
  const medal = ["🥇", "🥈", "🥉"];
  const top = rows.slice(0, 3).map((r, i) => `${medal[i]} ${r.name} — ${Math.round(r.litres).toLocaleString()} L, shop ${pkr(r.shop_sales)}${r.rating ? `, ⭐${r.rating}` : ""}`).join("\n");
  for (const r of rows) {
    const u = get("SELECT * FROM users WHERE id=?", r.user_id);
    if (u) await notify(t, [u], { type: "leaderboard", title: `🏆 Last week you are #${r.rank} of ${rows.length}`, body: `${top}\n\nYou: ${Math.round(r.litres).toLocaleString()} L · shop ${pkr(r.shop_sales)} · commission ${pkr(r.total)}` });
  }
  await notify(t, staff(t, ["admin", "manager"]), { type: "leaderboard", whatsapp: false, title: "🏆 Weekly salesman leaderboard", body: top });
  return rows.length;
}
