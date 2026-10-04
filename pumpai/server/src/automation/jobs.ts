/** Fully automated background jobs. Each returns a short human-readable result for the Automations page. */
import { all, get, getSetting, pkDate, pkDayStart, type Row } from "../db.js";
import { scoreCustomers, detectAnomalies, tankOutlook, kpis, insights } from "../ai/analytics.js";
import { createAlert, paymentLink, pkr } from "../services.js";
import { sendWhatsApp, sendToPhone } from "../whatsapp/cloud.js";
import { askBusiness, writeCampaign } from "../ai/agent.js";
import { aiEnabled } from "../config.js";
import { notify, staff, shiftUser } from "../notifications.js";

export interface Job {
  key: string;
  name: string;
  description: string;
  cron: string; // Asia/Karachi time
  run: (tenantId: number) => Promise<string>;
}

const ownerPhone = (tenantId: number) => getSetting(tenantId, "owner_phone") || get("SELECT owner_phone FROM tenants WHERE id=?", tenantId)?.owner_phone;

async function notifyOwner(tenantId: number, text: string) {
  const phone = ownerPhone(tenantId);
  if (phone) await sendToPhone(phone, text);
}

function recentlySent(customerId: number, kind: string, days: number) {
  return Boolean(get(
    `SELECT m.id FROM messages m JOIN conversations c ON c.id=m.conversation_id
     WHERE c.customer_id=? AND m.direction='out' AND m.meta LIKE ? AND m.created_at >= ? LIMIT 1`,
    customerId, `%"kind":"${kind}"%`, new Date(Date.now() - days * 86_400_000).toISOString(),
  ));
}

export const JOBS: Job[] = [
  {
    key: "ai_scoring",
    name: "AI customer scoring",
    description: "Recomputes segments, churn probability and khata credit-risk score for every customer.",
    cron: "0 2 * * *",
    run: async (t) => `Scored ${scoreCustomers(t).updated} customers`,
  },
  {
    key: "anomaly_scan",
    name: "Loss & fraud detection",
    description: "Scans dip variance, short tanker deliveries, attendant cash shortages and sales drops; raises alerts and WhatsApps the owner on critical ones.",
    cron: "*/30 * * * *",
    run: async (t) => {
      const findings = detectAnomalies(t);
      let created = 0;
      for (const f of findings) {
        const a = createAlert(t, { station_id: f.station_id, type: f.type, severity: f.severity, title: f.title, body: f.body, dedupe_key: f.key });
        if (a) {
          created++;
          if (f.severity === "critical") await notifyOwner(t, `🚨 ${f.title}\n${f.body}`);
        }
      }
      return `${findings.length} findings, ${created} new alerts`;
    },
  },
  {
    key: "stock_watch",
    name: "Tank stock-out prediction",
    description: "Forecasts days-to-reorder for every tank and alerts with a suggested tanker order before you run dry.",
    cron: "0 * * * *",
    run: async (t) => {
      let n = 0;
      for (const tank of tankOutlook(t)) {
        if (tank.days_to_reorder <= 1.5) {
          const a = createAlert(t, {
            station_id: tank.station_id, type: "low_stock", severity: tank.days_to_empty < 1 ? "critical" : "warning",
            title: `${tank.station_name} ${tank.name}: order ${tank.suggested_order_l.toLocaleString()}L ${tank.product}`,
            body: `${tank.fill_pct}% full (${Math.round(tank.current_l).toLocaleString()}L). Forecast ${tank.forecast_daily_l}L/day → empty in ${tank.days_to_empty} days.`,
            dedupe_key: `stock-${tank.id}`,
          });
          if (a) {
            n++;
            await notifyOwner(t, `⛽ Tanker order needed: ${a.title}\n${a.body}`);
          }
        }
      }
      return `${n} reorder alerts`;
    },
  },
  {
    key: "khata_reminders",
    name: "Khata payment reminders",
    description: "WhatsApps customers with dues (near limit or no payment in 15 days) their balance and a JazzCash/Easypaisa/Raast payment link. Max once every 3 days per customer.",
    cron: "0 11 * * *",
    run: async (t) => {
      const due = all(
        `SELECT c.* FROM customers c WHERE c.tenant_id=? AND c.balance > 0 AND (
           c.balance >= 0.8 * c.credit_limit OR
           COALESCE((SELECT MAX(created_at) FROM khata_ledger k WHERE k.customer_id=c.id AND k.type='credit'), '2000') < ?)`,
        t, new Date(Date.now() - 15 * 86_400_000).toISOString(),
      );
      let sent = 0;
      for (const c of due) {
        if (recentlySent(c.id, "khata_reminder", 3)) continue;
        const link = paymentLink(c, c.balance);
        await sendWhatsApp(t, c, `Assalam-o-Alaikum ${c.name}! 📒 Aap ka khata balance ${pkr(c.balance)} hai (limit ${pkr(c.credit_limit)}).\nAasani se pay karein (JazzCash / Easypaisa / Raast):\n${link}\nShukriya! 🙏`, "system", { kind: "khata_reminder" });
        sent++;
      }
      return `${sent} reminders sent (${due.length} customers with dues)`;
    },
  },
  {
    key: "churn_winback",
    name: "AI win-back campaign",
    description: "Finds regular customers likely to churn and sends them a personalised AI-written offer on WhatsApp (opt-in only, once per 14 days).",
    cron: "0 12 * * 1",
    run: async (t) => {
      const atRisk = all("SELECT * FROM customers WHERE tenant_id=? AND churn_score >= 0.6 AND opt_in=1 AND last_visit_at IS NOT NULL ORDER BY churn_score DESC LIMIT 50", t);
      if (!atRisk.length) return "No at-risk customers";
      const { message } = await writeCampaign(t, "We miss you — double loyalty points on your next fill this week", "customers who stopped visiting");
      let sent = 0;
      for (const c of atRisk) {
        if (recentlySent(c.id, "winback", 14)) continue;
        await sendWhatsApp(t, c, message.replaceAll("{name}", String(c.name).split(" ")[0]), "campaign", { kind: "winback" });
        sent++;
      }
      return `${sent} win-back messages sent`;
    },
  },
  {
    key: "shift_watch",
    name: "12-hour shift reminder",
    description: "Reminds a salesman to close their shift with meter readings and cash once it passes 12 hours, and alerts managers if a shift is still open after 13 hours.",
    cron: "*/15 * * * *",
    run: async (t) => {
      const open = all(`SELECT sh.*, s.name station_name FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.status='open'`, t);
      let reminded = 0;
      for (const sh of open) {
        const hours = (Date.now() - Date.parse(sh.opened_at)) / 3600_000;
        if (hours < 12) continue;
        const u = shiftUser(t, sh);
        if (u && !get("SELECT id FROM notifications WHERE user_id=? AND type='shift_due' AND json_extract(data,'$.shift_id')=?", u.id, sh.id)) {
          await notify(t, [u], { type: "shift_due", data: { shift_id: sh.id },
            title: "⏰ Shift time over — close your shift", body: `${Math.floor(hours)} ghante ho gaye. Meter readings aur cash darj kar ke shift close karein.` });
          reminded++;
        }
        if (hours >= 13) {
          const a = createAlert(t, { station_id: sh.station_id, type: "shift_overdue", severity: "warning", title: `Shift still open: ${sh.attendant} (${Math.floor(hours)} h)`,
            body: `${sh.station_name} — opened ${new Date(sh.opened_at).toLocaleString("en-PK")}.`, dedupe_key: `shift-overdue-${sh.id}` });
          if (a) await notify(t, staff(t, ["admin", "manager"]), { type: "shift_overdue", data: { shift_id: sh.id }, whatsapp: false, title: a.title, body: a.body });
        }
      }
      return `${open.length} open shifts, ${reminded} reminders sent`;
    },
  },
  {
    key: "daily_brief",
    name: "Owner's daily WhatsApp brief",
    description: "At 9pm sends the owner a summary: sales, cash vs digital, khata, stock outlook, alerts and tomorrow's forecast.",
    cron: "0 21 * * *",
    run: async (t) => {
      const text = await buildDailyBrief(t);
      await notifyOwner(t, text);
      createAlert(t, { type: "daily_brief", severity: "info", title: "Daily brief sent", body: text });
      return "Brief sent to owner";
    },
  },
];

export async function buildDailyBrief(t: number) {
  if (aiEnabled()) {
    const r = await askBusiness(t, "Write today's end-of-day WhatsApp brief for the owner in Roman Urdu: sales by product, cash vs digital vs khata, stock/reorder warnings, open alerts, cash shortages, and tomorrow's demand forecast. Under 900 characters, emoji bullet points, no markdown headings.");
    if (r.engine === "claude") return r.answer;
  }
  const k = kpis(t);
  const tanks = tankOutlook(t);
  const alerts = all("SELECT title FROM alerts WHERE tenant_id=? AND acknowledged=0 AND severity!='info' ORDER BY id DESC LIMIT 4", t);
  const byProduct = all(
    `SELECT s.product, ROUND(SUM(s.litres)) l FROM sales s JOIN stations st ON st.id=s.station_id WHERE st.tenant_id=? AND s.created_at >= ? GROUP BY s.product`,
    t, pkDayStart(),
  );
  return [
    `📊 Aaj ki report — ${pkDate()}`,
    `⛽ Sales: ${byProduct.map((r: Row) => `${r.product} ${Number(r.l).toLocaleString()}L`).join(" | ") || "—"}`,
    `💰 Revenue ${pkr(k.today.amount)} (Cash ${pkr(k.today.cash)} · Digital ${pkr(k.today.digital)} · Khata ${pkr(k.today.khata)})`,
    `📒 Khata outstanding ${pkr(k.khata.outstanding)} (${k.khata.debtors} customers)`,
    ...tanks.filter((x) => x.days_to_reorder <= 2).map((x) => `🛢️ ${x.station_name} ${x.name}: ${x.fill_pct}% — order ${x.suggested_order_l.toLocaleString()}L`),
    ...alerts.map((a: Row) => `⚠️ ${a.title}`),
    ...insights(t).filter((c) => c.icon === "trend").map((c) => `📈 ${c.title}`),
    `💬 WhatsApp: ${k.whatsapp.ai_replies_today} AI replies today, ${k.pending_orders} pending orders`,
  ].join("\n");
}
