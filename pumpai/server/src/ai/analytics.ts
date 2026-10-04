/**
 * Predictive analytics: demand forecasting, stock-out prediction, churn and credit-risk scoring,
 * and anomaly detection. Pure TypeScript so it runs anywhere without a Python service.
 */
import { all, get, run, now, type Row } from "../db.js";
import { PRODUCTS } from "../config.js";

const DAY = 86_400_000;
const dayKey = (d: Date) => d.toISOString().slice(0, 10);

/** Daily litres per product for the last `days` days (zero-filled). */
export function dailySeries(tenantId: number, days = 60, stationId?: number) {
  const since = new Date(Date.now() - days * DAY);
  const rows = all(
    `SELECT substr(s.created_at,1,10) d, s.product, SUM(s.litres) litres, SUM(s.amount) amount
     FROM sales s JOIN stations st ON st.id=s.station_id
     WHERE st.tenant_id=? AND s.created_at >= ? ${stationId ? "AND s.station_id=" + Number(stationId) : ""}
     GROUP BY d, s.product ORDER BY d`,
    tenantId, since.toISOString(),
  );
  const dates: string[] = [];
  for (let i = days - 1; i >= 0; i--) dates.push(dayKey(new Date(Date.now() - i * DAY)));
  const series: Record<string, number[]> = {};
  const revenue: number[] = dates.map(() => 0);
  for (const p of Object.keys(PRODUCTS)) series[p] = dates.map(() => 0);
  for (const r of rows) {
    const i = dates.indexOf(r.d);
    if (i < 0) continue;
    (series[r.product] ??= dates.map(() => 0))[i] = r.litres;
    revenue[i] += r.amount;
  }
  return { dates, series, revenue };
}

/**
 * Holt-Winters additive smoothing with weekly seasonality.
 * Falls back to a weekday average for short histories.
 */
export function holtWinters(y: number[], horizon = 7, season = 7, alpha = 0.35, beta = 0.05, gamma = 0.3): number[] {
  const n = y.length;
  if (n < season * 2) {
    const avg = n ? y.reduce((a, b) => a + b, 0) / n : 0;
    return Array(horizon).fill(avg);
  }
  let level = y.slice(0, season).reduce((a, b) => a + b, 0) / season;
  let trend = (y.slice(season, 2 * season).reduce((a, b) => a + b, 0) - y.slice(0, season).reduce((a, b) => a + b, 0)) / (season * season);
  const seasonal = y.slice(0, season).map((v) => v - level);
  for (let t = 0; t < n; t++) {
    const s = seasonal[t % season];
    const prevLevel = level;
    level = alpha * (y[t] - s) + (1 - alpha) * (level + trend);
    trend = beta * (level - prevLevel) + (1 - beta) * trend;
    seasonal[t % season] = gamma * (y[t] - level) + (1 - gamma) * s;
  }
  return Array.from({ length: horizon }, (_, h) => Math.max(0, level + (h + 1) * trend + seasonal[(n + h) % season]));
}

export function forecast(tenantId: number, horizon = 7, stationId?: number) {
  const { dates, series } = dailySeries(tenantId, 56, stationId);
  const out: Record<string, { history: number[]; forecast: number[] }> = {};
  for (const [p, y] of Object.entries(series)) {
    // drop today's partial day from training
    const hist = y.slice(0, -1);
    out[p] = { history: y, forecast: holtWinters(hist, horizon + 1).slice(1).map(Math.round) };
  }
  const futureDates = Array.from({ length: horizon }, (_, i) => dayKey(new Date(Date.now() + (i + 1) * DAY)));
  return { dates, futureDates, products: out };
}

/** Days until each tank reaches its reorder level and until empty, using the station-level forecast. */
export function tankOutlook(tenantId: number) {
  const tanks = all(
    `SELECT t.*, s.name station_name FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? ORDER BY s.id, t.id`,
    tenantId,
  );
  const cache = new Map<number, ReturnType<typeof forecast>>();
  return tanks.map((t) => {
    if (!cache.has(t.station_id)) cache.set(t.station_id, forecast(tenantId, 7, t.station_id));
    const f = cache.get(t.station_id)!.products[t.product];
    const sameProduct = tanks.filter((x) => x.station_id === t.station_id && x.product === t.product);
    const share = sameProduct.length ? t.current_l / Math.max(1, sameProduct.reduce((a, x) => a + x.current_l, 0)) : 1;
    const daily = Math.max(1, ((f?.forecast ?? []).reduce((a, b) => a + b, 0) / 7) * share);
    const reorderL = (t.capacity_l * t.reorder_pct) / 100;
    return {
      ...t,
      fill_pct: Math.round((t.current_l / t.capacity_l) * 1000) / 10,
      forecast_daily_l: Math.round(daily),
      days_to_empty: Math.round((t.current_l / daily) * 10) / 10,
      days_to_reorder: Math.round((Math.max(0, t.current_l - reorderL) / daily) * 10) / 10,
      suggested_order_l: Math.max(0, Math.round((t.capacity_l * 0.95 - t.current_l) / 1000) * 1000),
    };
  });
}

/** Recompute churn score, segment and credit-risk score for every customer. */
export function scoreCustomers(tenantId: number) {
  const customers = all("SELECT * FROM customers WHERE tenant_id=?", tenantId);
  const spend = new Map<number, Row>(
    all(
      `SELECT customer_id, COUNT(*) visits, SUM(amount) spend, MIN(created_at) first, MAX(created_at) last
       FROM sales WHERE customer_id IS NOT NULL AND created_at >= ? GROUP BY customer_id`,
      new Date(Date.now() - 90 * DAY).toISOString(),
    ).map((r) => [r.customer_id, r]),
  );
  const lastPay = new Map<number, string>(
    all("SELECT customer_id, MAX(created_at) last FROM khata_ledger WHERE type='credit' GROUP BY customer_id").map((r) => [r.customer_id, r.last]),
  );
  const spends = [...spend.values()].map((s) => s.spend).sort((a, b) => a - b);
  const vipCut = spends[Math.floor(spends.length * 0.8)] ?? Infinity;
  let updated = 0;
  for (const c of customers) {
    const s = spend.get(c.id);
    let churn = 0;
    if (s && s.visits >= 2) {
      const span = (Date.parse(s.last) - Date.parse(s.first)) / DAY;
      const interval = Math.max(1, span / (s.visits - 1));
      const since = (Date.now() - Date.parse(s.last)) / DAY;
      churn = 1 / (1 + Math.exp(-(since / interval - 2.5) * 1.6)); // ~0.5 when 2.5x overdue
    } else if (c.last_visit_at) {
      churn = Math.min(1, (Date.now() - Date.parse(c.last_visit_at)) / (45 * DAY));
    } else churn = 0.5;

    let risk = 0;
    if (c.credit_limit > 0 || c.balance > 0) {
      const util = c.balance / Math.max(1, c.credit_limit);
      const lp = lastPay.get(c.id);
      const daysSincePay = lp ? (Date.now() - Date.parse(lp)) / DAY : 60;
      risk = Math.min(100, Math.round(util * 55 + Math.min(daysSincePay, 60) * 0.6 + churn * 15));
    }
    const segment =
      c.type === "fleet" ? "Fleet" : c.type === "farmer" ? "Agri" : ["police", "school", "government", "hospital"].includes(c.type) ? "Institution" :
      s && s.spend >= vipCut ? "VIP" : churn > 0.6 ? "At risk" : s ? "Regular" : "New";
    run("UPDATE customers SET churn_score=?, risk_score=?, segment=? WHERE id=?", Math.round(churn * 100) / 100, risk, segment, c.id);
    updated++;
  }
  return { updated };
}

/** Rule + statistics based anomaly detection; returns findings (caller decides on alerts). */
export function detectAnomalies(tenantId: number) {
  const findings: { type: string; severity: "info" | "warning" | "critical"; title: string; body: string; station_id?: number; key: string }[] = [];

  // 1. Stock variance between physical dip and book stock
  for (const d of all(
    `SELECT d.*, t.name tank, t.station_id FROM dip_readings d JOIN tanks t ON t.id=d.tank_id JOIN stations s ON s.id=t.station_id
     WHERE s.tenant_id=? AND d.created_at >= ? ORDER BY d.id DESC`,
    tenantId, new Date(Date.now() - 2 * DAY).toISOString(),
  )) {
    if (Math.abs(d.variance_pct) >= 0.5) {
      findings.push({
        type: "stock_variance", severity: Math.abs(d.variance_pct) >= 1 ? "critical" : "warning", station_id: d.station_id,
        title: `${d.tank}: stock variance ${d.variance_pct.toFixed(2)}%`,
        body: `Dip ${Math.round(d.measured_l)}L vs book ${Math.round(d.book_l)}L. Check for leakage, meter calibration or pilferage.`,
        key: `dip-${d.id}`,
      });
    }
  }
  // 2. Tanker short delivery
  for (const d of all(
    `SELECT d.*, t.name tank, t.station_id FROM deliveries d JOIN tanks t ON t.id=d.tank_id JOIN stations s ON s.id=t.station_id
     WHERE s.tenant_id=? AND d.created_at >= ?`,
    tenantId, new Date(Date.now() - 3 * DAY).toISOString(),
  )) {
    if (d.shortage_pct >= 0.3) {
      findings.push({
        type: "short_delivery", severity: d.shortage_pct >= 0.8 ? "critical" : "warning", station_id: d.station_id,
        title: `Tanker ${d.tanker_no ?? ""} short by ${d.shortage_pct.toFixed(2)}%`,
        body: `${d.tank}: invoice ${d.invoice_l}L, received ${d.received_l}L (${Math.round(d.invoice_l - d.received_l)}L short). Raise claim with ${d.supplier ?? "supplier"}.`,
        key: `delivery-${d.id}`,
      });
    }
  }
  // 3. Cash shortage per attendant (z-score over their own history)
  const shifts = all(
    `SELECT sh.* FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.status='closed' AND sh.closed_at >= ?`,
    tenantId, new Date(Date.now() - 30 * DAY).toISOString(),
  );
  const byAtt = new Map<string, Row[]>();
  for (const s of shifts) (byAtt.get(s.attendant) ?? byAtt.set(s.attendant, []).get(s.attendant)!).push(s);
  for (const [att, list] of byAtt) {
    const recent = list.filter((s) => Date.parse(s.closed_at) > Date.now() - 2 * DAY);
    const v = list.map((s) => s.variance ?? 0);
    const mean = v.reduce((a, b) => a + b, 0) / v.length;
    const sd = Math.sqrt(v.reduce((a, b) => a + (b - mean) ** 2, 0) / v.length) || 1;
    for (const s of recent) {
      if (s.variance < -2000 || (s.variance < -500 && (s.variance - mean) / sd < -2)) {
        findings.push({
          type: "cash_short", severity: s.variance < -5000 ? "critical" : "warning", station_id: s.station_id,
          title: `Cash short Rs ${Math.abs(Math.round(s.variance)).toLocaleString()} — ${att}`,
          body: `Shift #${s.id}: expected Rs ${Math.round(s.cash_expected).toLocaleString()}, counted Rs ${Math.round(s.cash_actual).toLocaleString()}.`,
          key: `shift-${s.id}`,
        });
      }
    }
  }
  // 4. Sales drop vs forecast (yesterday)
  const { series } = dailySeries(tenantId, 29);
  for (const [p, y] of Object.entries(series)) {
    const yesterday = y[y.length - 2];
    const base = y.slice(0, -2);
    const avg = base.reduce((a, b) => a + b, 0) / Math.max(1, base.length);
    if (avg > 100 && yesterday < avg * 0.7) {
      findings.push({
        type: "sales_drop", severity: "warning",
        title: `${PRODUCTS[p] ?? p} sales down ${Math.round((1 - yesterday / avg) * 100)}% yesterday`,
        body: `Yesterday ${Math.round(yesterday)}L vs 4-week average ${Math.round(avg)}L. Check dispenser downtime, competitor pricing or supply.`,
        key: `drop-${p}-${new Date().toISOString().slice(0, 10)}`,
      });
    }
  }
  return findings;
}

export function kpis(tenantId: number) {
  const today = new Date().toISOString().slice(0, 10);
  const t = get(
    `SELECT COALESCE(SUM(s.litres),0) litres, COALESCE(SUM(s.amount),0) amount, COUNT(*) txns,
       COALESCE(SUM(CASE WHEN payment_method='cash' THEN s.amount END),0) cash,
       COALESCE(SUM(CASE WHEN payment_method IN ('jazzcash','easypaisa','raast','card') THEN s.amount END),0) digital,
       COALESCE(SUM(CASE WHEN payment_method='khata' THEN s.amount END),0) khata
     FROM sales s JOIN stations st ON st.id=s.station_id WHERE st.tenant_id=? AND s.created_at >= ?`,
    tenantId, today,
  )!;
  const y = get(
    `SELECT COALESCE(SUM(s.amount),0) amount FROM sales s JOIN stations st ON st.id=s.station_id
     WHERE st.tenant_id=? AND s.created_at >= ? AND s.created_at < ?`,
    tenantId, new Date(Date.now() - DAY).toISOString().slice(0, 10), new Date(Date.now() - DAY).toISOString(),
  )!;
  const k = get("SELECT COALESCE(SUM(balance),0) outstanding, COUNT(CASE WHEN balance>0 THEN 1 END) debtors FROM customers WHERE tenant_id=?", tenantId)!;
  const a = get("SELECT COUNT(*) open FROM alerts WHERE tenant_id=? AND acknowledged=0", tenantId)!;
  const w = get(
    `SELECT COUNT(*) total, COUNT(CASE WHEN mode='human' THEN 1 END) human, COALESCE(SUM(unread),0) unread
     FROM conversations WHERE tenant_id=? AND status='open'`, tenantId,
  )!;
  const ai = get(
    `SELECT COUNT(*) n FROM messages m JOIN conversations c ON c.id=m.conversation_id
     WHERE c.tenant_id=? AND m.sender='ai' AND m.created_at >= ?`, tenantId, today,
  )!;
  return {
    today: { ...t, vs_yesterday_pct: y.amount ? Math.round(((t.amount - y.amount) / y.amount) * 1000) / 10 : null },
    khata: k, open_alerts: a.open, whatsapp: { ...w, ai_replies_today: ai.n },
    customers: get("SELECT COUNT(*) n FROM customers WHERE tenant_id=?", tenantId)!.n,
    pending_orders: get("SELECT COUNT(*) n FROM orders WHERE tenant_id=? AND status IN ('pending','confirmed')", tenantId)!.n,
    generated_at: now(),
  };
}

/** Plain-language insight cards built from the analytics above (no LLM needed). */
export function insights(tenantId: number) {
  const cards: { icon: string; title: string; body: string; tone: "good" | "warn" | "bad" | "info" }[] = [];
  for (const t of tankOutlook(tenantId)) {
    if (t.days_to_reorder <= 2)
      cards.push({
        icon: "fuel", tone: t.days_to_empty < 1.5 ? "bad" : "warn",
        title: `${t.station_name} · ${t.name} reorder now`,
        body: `${t.fill_pct}% full, ~${t.forecast_daily_l}L/day → empty in ${t.days_to_empty} days. Suggested order ${t.suggested_order_l.toLocaleString()}L.`,
      });
  }
  const f = forecast(tenantId, 7);
  for (const [p, d] of Object.entries(f.products)) {
    const last7 = d.history.slice(-8, -1).reduce((a, b) => a + b, 0);
    const next7 = d.forecast.reduce((a, b) => a + b, 0);
    if (last7 > 0 && Math.abs(next7 / last7 - 1) > 0.05)
      cards.push({
        icon: "trend", tone: next7 > last7 ? "good" : "warn",
        title: `${PRODUCTS[p]} demand ${next7 > last7 ? "up" : "down"} ${Math.abs(Math.round((next7 / last7 - 1) * 100))}% next week`,
        body: `Forecast ${Math.round(next7).toLocaleString()}L vs ${Math.round(last7).toLocaleString()}L last 7 days.`,
      });
  }
  const atRisk = all("SELECT name, churn_score FROM customers WHERE tenant_id=? AND churn_score>=0.6 ORDER BY churn_score DESC LIMIT 5", tenantId);
  if (atRisk.length)
    cards.push({
      icon: "users", tone: "warn", title: `${atRisk.length}+ regular customers going quiet`,
      body: `${atRisk.map((c) => c.name).join(", ")}. Win-back automation can send them an offer on WhatsApp.`,
    });
  const risky = all("SELECT name, balance, risk_score FROM customers WHERE tenant_id=? AND risk_score>=60 ORDER BY risk_score DESC LIMIT 3", tenantId);
  if (risky.length)
    cards.push({
      icon: "credit", tone: "bad", title: `High credit risk: ${risky.map((r) => r.name).join(", ")}`,
      body: `Combined khata Rs ${Math.round(risky.reduce((a, r) => a + r.balance, 0)).toLocaleString()}. Consider pausing credit until payment.`,
    });
  // Expense categories running well above their 3-month average
  const month = new Date().toISOString().slice(0, 7);
  const threeAgo = new Date(Date.UTC(Number(month.slice(0, 4)), Number(month.slice(5, 7)) - 4, 1)).toISOString().slice(0, 10);
  for (const e of all(
    `SELECT category, SUM(CASE WHEN expense_date >= ? THEN amount ELSE 0 END) cur, SUM(CASE WHEN expense_date < ? THEN amount ELSE 0 END) / 3.0 avg
     FROM expenses WHERE tenant_id=? AND status='approved' AND expense_date >= ? GROUP BY category`, month + "-01", month + "-01", tenantId, threeAgo,
  )) {
    if (e.avg > 5000 && e.cur > e.avg * 1.25)
      cards.push({ icon: "credit", tone: "warn", title: `${e.category} expense up ${Math.round((e.cur / e.avg - 1) * 100)}% this month`,
        body: `Rs ${Math.round(e.cur).toLocaleString()} so far vs Rs ${Math.round(e.avg).toLocaleString()} monthly average.` });
  }
  const pendingExp = get("SELECT COUNT(*) n, COALESCE(SUM(amount),0) s FROM expenses WHERE tenant_id=? AND status='pending'", tenantId)!;
  if (pendingExp.n) cards.push({ icon: "credit", tone: "info", title: `${pendingExp.n} expense${pendingExp.n > 1 ? "s" : ""} waiting for approval`, body: `Rs ${Math.round(pendingExp.s).toLocaleString()} total. Approve on the Expenses page.` });

  const k = kpis(tenantId);
  if (k.today.vs_yesterday_pct !== null)
    cards.push({
      icon: "chart", tone: k.today.vs_yesterday_pct >= 0 ? "good" : "info",
      title: `Today's revenue ${k.today.vs_yesterday_pct >= 0 ? "ahead of" : "behind"} yesterday`,
      body: `Rs ${Math.round(k.today.amount).toLocaleString()} so far (${k.today.vs_yesterday_pct}% vs yesterday at the same time).`,
    });
  return cards;
}
