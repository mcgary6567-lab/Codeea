/** Read-only analytics tools for the owner's "Ask AI" business assistant. */
import { all } from "../db.js";
import { kpis, forecast, tankOutlook, insights } from "./analytics.js";
import { currentPrices } from "../services.js";
import { clientDue } from "../routes/wholesale.js";
import { buildReport } from "../routes/reports.js";
import type { runTool } from "./tools.js";

type Tools = Parameters<typeof runTool>[0];
const obj = (properties: Record<string, unknown>, required: string[] = []) =>
  ({ type: "object" as const, properties, required, additionalProperties: false });

export const businessTools: Tools = [
  {
    name: "get_kpis",
    description: "Today's KPIs: litres, revenue, payment mix, khata outstanding, open alerts, WhatsApp stats, pending orders.",
    input_schema: obj({}),
    run: (ctx) => kpis(ctx.tenantId),
  },
  {
    name: "get_sales",
    description: "Sales aggregated by day (or by station/product/payment method/attendant) between two dates (YYYY-MM-DD, inclusive).",
    input_schema: obj({
      from: { type: "string" }, to: { type: "string" },
      group_by: { type: "string", enum: ["day", "station", "product", "payment_method", "attendant"] },
    }, ["from", "to", "group_by"]),
    run: (ctx, i) => {
      const col = { day: "substr(s.created_at,1,10)", station: "st.name", product: "s.product", payment_method: "s.payment_method", attendant: "sh.attendant" }[i.group_by as string] ?? "substr(s.created_at,1,10)";
      return all(
        `SELECT ${col} k, ROUND(SUM(s.litres)) litres, ROUND(SUM(s.amount)) amount_pkr, COUNT(*) txns
         FROM sales s JOIN stations st ON st.id=s.station_id LEFT JOIN shifts sh ON sh.id=s.shift_id
         WHERE st.tenant_id=? AND s.created_at >= ? AND s.created_at < date(?, '+1 day') GROUP BY k ORDER BY k LIMIT 120`,
        ctx.tenantId, i.from, i.to,
      );
    },
  },
  {
    name: "get_forecast",
    description: "7-day demand forecast (litres/day) per product from the Holt-Winters model.",
    input_schema: obj({}),
    run: (ctx) => {
      const f = forecast(ctx.tenantId, 7);
      return { dates: f.futureDates, forecast: Object.fromEntries(Object.entries(f.products).map(([p, v]) => [p, v.forecast])) };
    },
  },
  {
    name: "get_tank_outlook",
    description: "Every tank: stock, % full, forecast daily usage, days to reorder level and to empty, suggested order litres.",
    input_schema: obj({}),
    run: (ctx) => tankOutlook(ctx.tenantId).map((t) => ({
      station: t.station_name, tank: t.name, product: t.product, stock_l: Math.round(t.current_l), fill_pct: t.fill_pct,
      daily_l: t.forecast_daily_l, days_to_reorder: t.days_to_reorder, days_to_empty: t.days_to_empty, suggested_order_l: t.suggested_order_l,
    })),
  },
  {
    name: "get_customers",
    description: "Customers ranked by a metric: top spenders (90 days), biggest khata balances, highest churn risk or credit risk.",
    input_schema: obj({ rank_by: { type: "string", enum: ["spend", "balance", "churn", "credit_risk"] }, limit: { type: "integer" } }, ["rank_by"]),
    run: (ctx, i) => {
      const lim = Math.min(25, i.limit ?? 10);
      if (i.rank_by === "spend")
        return all(
          `SELECT c.name, c.type, c.segment, ROUND(SUM(s.amount)) spend_pkr, ROUND(SUM(s.litres)) litres, COUNT(*) visits
           FROM sales s JOIN customers c ON c.id=s.customer_id WHERE c.tenant_id=? AND s.created_at >= date('now','-90 day')
           GROUP BY c.id ORDER BY spend_pkr DESC LIMIT ?`, ctx.tenantId, lim);
      const col = { balance: "balance", churn: "churn_score", credit_risk: "risk_score" }[i.rank_by as string] ?? "balance";
      return all(`SELECT name, type, segment, balance, credit_limit, churn_score, risk_score, last_visit_at FROM customers WHERE tenant_id=? ORDER BY ${col} DESC LIMIT ?`, ctx.tenantId, lim);
    },
  },
  {
    name: "get_alerts",
    description: "Recent alerts (stock variance, short deliveries, cash shortages, complaints, handoffs).",
    input_schema: obj({ only_open: { type: "boolean" } }),
    run: (ctx, i) => all(`SELECT type, severity, title, body, acknowledged, created_at FROM alerts WHERE tenant_id=? ${i?.only_open ? "AND acknowledged=0" : ""} ORDER BY id DESC LIMIT 25`, ctx.tenantId),
  },
  {
    name: "get_shifts",
    description: "Recent closed shifts with attendant, litres, expected vs counted cash and variance.",
    input_schema: obj({ days: { type: "integer" } }),
    run: (ctx, i) => all(
      `SELECT sh.id, st.name station, sh.attendant, sh.opened_at, sh.closed_at, ROUND(sh.litres) litres, ROUND(sh.cash_expected) expected, ROUND(sh.cash_actual) counted, ROUND(sh.variance) variance
       FROM shifts sh JOIN stations st ON st.id=sh.station_id WHERE st.tenant_id=? AND sh.status='closed' AND sh.closed_at >= date('now', ?) ORDER BY sh.id DESC LIMIT 60`,
      ctx.tenantId, `-${Math.min(60, i?.days ?? 7)} day`),
  },
  {
    name: "get_prices",
    description: "Current fuel prices and the last 10 price changes.",
    input_schema: obj({}),
    run: (ctx) => ({ current: currentPrices(ctx.tenantId), history: all("SELECT product, price, effective_from FROM prices WHERE tenant_id=? ORDER BY effective_from DESC LIMIT 10", ctx.tenantId) }),
  },
  {
    name: "get_insights",
    description: "Pre-computed AI insight cards (reorder warnings, demand shifts, churn and credit risks).",
    input_schema: obj({}),
    run: (ctx) => insights(ctx.tenantId),
  },
  {
    name: "get_period_report",
    description: "Full business report for a period: revenue (retail + wholesale), expenses, estimated fuel cost, gross and net profit, money in/out, stock movement per product (opening, received, sold, closing), and current receivables (who owes us) and payables (whom we owe). Use ISO dates.",
    input_schema: obj({ from: { type: "string", description: "Start, e.g. 2026-09-01 or ISO datetime" }, to: { type: "string", description: "End (exclusive); default now" } }, ["from"]),
    run: (ctx, i) => {
      const from = new Date(i.from).toISOString();
      const to = i.to ? new Date(i.to).toISOString() : new Date().toISOString();
      const r = buildReport(ctx.tenantId, from, to);
      return {
        from, to, summary: r.summary, stock: r.stock.products, expenses_by_category: r.expenses.by_category,
        receivables: { total: r.receivables.total, khata: r.receivables.khata_total, wholesale: r.receivables.wholesale_total, aging: r.receivables.aging,
          top: r.receivables.list.slice(0, 10).map((x: any) => ({ name: x.name, kind: x.kind, amount: x.amount, aging: x.aging })) },
        payables: { total: r.payables.total, list: r.payables.list.slice(0, 10).map((x: any) => ({ name: x.name, reason: x.reason, amount: x.amount })) },
        shift_cash: r.shifts.by_attendant,
      };
    },
  },
  {
    name: "get_wholesale",
    description: "Wholesale supply accounts: each client's per-litre rates, amount due, credit limit, litres supplied this month, last supply and last payment.",
    input_schema: obj({}),
    run: (ctx) => {
      const month = new Date().toISOString().slice(0, 7) + "-01";
      return all("SELECT * FROM wholesale_clients WHERE tenant_id=?", ctx.tenantId).map((c) => ({
        name: c.name, due_pkr: clientDue(c.id), credit_limit_pkr: c.credit_limit,
        rates: Object.fromEntries(all("SELECT product, rate FROM wholesale_rates WHERE client_id=?", c.id).map((r) => [r.product, r.rate])),
        ...all(`SELECT ROUND(COALESCE(SUM(CASE WHEN type='supply' AND txn_date >= ? THEN litres END),0)) month_supplied_l,
            ROUND(COALESCE(SUM(CASE WHEN type='payment' AND txn_date >= ? THEN amount END),0)) month_received_pkr,
            MAX(CASE WHEN type='supply' THEN substr(txn_date,1,10) END) last_supply, MAX(CASE WHEN type='payment' THEN substr(txn_date,1,10) END) last_payment
          FROM wholesale_txns WHERE client_id=? AND voided=0`, month, month, c.id)[0],
      }));
    },
  },
  {
    name: "get_expenses",
    description: "Approved expenses for a month (YYYY-MM, default current) by category with budget and 3-month average, plus revenue and revenue minus expenses.",
    input_schema: obj({ month: { type: "string" } }),
    run: (ctx, i) => {
      const month = /^\d{4}-\d{2}$/.test(i?.month ?? "") ? i.month : new Date().toISOString().slice(0, 7);
      const start = month + "-01";
      const end = new Date(Date.UTC(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 1)).toISOString().slice(0, 10);
      const byCat = all(`SELECT category, ROUND(SUM(amount)) spent FROM expenses WHERE tenant_id=? AND status='approved' AND expense_date >= ? AND expense_date < ? GROUP BY category ORDER BY spent DESC`, ctx.tenantId, start, end);
      const total = byCat.reduce((a, r) => a + r.spent, 0);
      const retail = all(`SELECT COALESCE(SUM(s.amount),0) a FROM sales s JOIN stations st ON st.id=s.station_id WHERE st.tenant_id=? AND s.created_at >= ? AND s.created_at < ?`, ctx.tenantId, start, end)[0].a;
      const wholesale = all(`SELECT COALESCE(SUM(CASE WHEN type='supply' THEN amount WHEN type='return' THEN -amount END),0) a FROM wholesale_txns WHERE tenant_id=? AND voided=0 AND txn_date >= ? AND txn_date < ?`, ctx.tenantId, start, end)[0].a;
      return { month, by_category: byCat, total_expenses: total, revenue_retail: Math.round(retail), revenue_wholesale: Math.round(wholesale), revenue_minus_expenses: Math.round(retail + wholesale - total),
        pending_approval: all("SELECT category, amount, created_by, note FROM expenses WHERE tenant_id=? AND status='pending'", ctx.tenantId) };
    },
  },
  {
    name: "get_whatsapp_stats",
    description: "WhatsApp CRM stats for the last N days: inbound/outbound messages, AI replies, orders and complaints created.",
    input_schema: obj({ days: { type: "integer" } }),
    run: (ctx, i) => {
      const since = `-${Math.min(90, i?.days ?? 7)} day`;
      return {
        messages: all(`SELECT m.sender, COUNT(*) n FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.tenant_id=? AND m.created_at >= date('now', ?) GROUP BY m.sender`, ctx.tenantId, since),
        orders: all(`SELECT status, COUNT(*) n, ROUND(SUM(litres)) litres FROM orders WHERE tenant_id=? AND created_at >= date('now', ?) GROUP BY status`, ctx.tenantId, since),
        complaints: all(`SELECT category, COUNT(*) n FROM complaints WHERE tenant_id=? AND created_at >= date('now', ?) GROUP BY category`, ctx.tenantId, since),
      };
    },
  },
];
