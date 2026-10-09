/** Fully automated background jobs. Each returns a short human-readable result for the Automations page. */
import { all, get, getSetting, pkDate, pkDayStart, type Row } from "../db.js";
import { scoreCustomers, detectAnomalies, tankOutlook, kpis, insights, sensitivity } from "../ai/analytics.js";
import { createAlert, paymentLink, pkr, round2 } from "../services.js";
import { PRODUCTS } from "../config.js";
import { sendWhatsApp, sendToPhone } from "../whatsapp/cloud.js";
import { askBusiness, writeCampaign } from "../ai/agent.js";
import { aiEnabled } from "../config.js";
import { notify, staff, shiftUser } from "../notifications.js";
import { monthlyBills } from "../billing.js";
import { closeDay } from "../routes/backoffice.js";
import { licenceWatch, checklistWatch, attendanceWatch } from "../routes/compliance.js";
import { weeklyStaffRisk } from "../routes/analysis.js";
import { khataOverdue, khataLateFees, bookingReminders, serviceDue } from "../routes/customerCare.js";
import { makeBackup } from "../routes/system.js";
import { bookRecurring } from "../routes/recurring.js";
import { weeklyLeaderboard } from "../routes/feedback.js";
import { trainingWatch, dailyCoaching } from "../routes/people.js";
import { machineWatch } from "../routes/machines.js";
import { carriageFuelHeldWatch } from "../routes/carriage.js";

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
        if (tank.days_to_reorder <= sensitivity(t).low_stock_days) {
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
        await sendWhatsApp(t, c, `Assalam-o-Alaikum ${c.name}! 📒 Aap ka khata balance ${pkr(c.balance)} hai (limit ${pkr(c.credit_limit)}).\n${link ? `Aasani se pay karein (JazzCash / Easypaisa / Raast):\n${link}` : "Meharbani kar ke pump par ya bank transfer se ada kar dein."}\nShukriya! 🙏`, "system", { kind: "khata_reminder" });
        sent++;
      }
      return `${sent} reminders sent (${due.length} customers with dues)`;
    },
  },
  {
    key: "pending_card_watch",
    name: "Card-pending fuel holds",
    description: "Reminds the owner / cashier about khata fuel given on trust where the card / parchi has not come in within 3 days, so it can be cleared and billed at today's rate.",
    cron: "0 10 * * *",
    run: async (t) => {
      const cutoff = new Date(Date.now() - 3 * 86_400_000).toISOString();
      const rows = all(`SELECT s.id, s.product, s.litres, s.created_at, s.station_id, c.name customer_name, st.name station_name
        FROM sales s JOIN stations st ON st.id=s.station_id LEFT JOIN customers c ON c.id=s.customer_id
        WHERE st.tenant_id=? AND s.pending=1 AND s.created_at <= ? ORDER BY s.created_at`, t, cutoff);
      let n = 0;
      for (const r of rows) {
        const days = Math.floor((Date.now() - Date.parse(r.created_at)) / 86_400_000);
        const a = createAlert(t, {
          station_id: r.station_id, type: "card_pending", severity: days >= 7 ? "critical" : "warning",
          title: `Card pending ${days} days: ${r.customer_name ?? "khata"} — ${round2(r.litres)}L ${PRODUCTS[r.product] ?? r.product}`,
          body: `Fuel given on ${String(r.created_at).slice(0, 10)} at ${r.station_name}. Card/parchi not brought in yet — clear it to bill at today's rate.`,
          dedupe_key: `card-pending-${r.id}`,
        });
        if (a) n++;
      }
      return `${rows.length} holds past 3 days, ${n} new alerts`;
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
    name: "Shift-over reminder",
    description: "Reminds a salesman to close their shift with meter readings and cash once it reaches the shift length (default 24 hours, e.g. 8am→8am), and alerts managers if it is still open an hour past that.",
    cron: "*/15 * * * *",
    run: async (t) => {
      const shiftHours = Number(getSetting(t, "shift_hours", "24")) || 24;
      const open = all(`SELECT sh.*, s.name station_name FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.status='open'`, t);
      let reminded = 0;
      for (const sh of open) {
        const hours = (Date.now() - Date.parse(sh.opened_at)) / 3600_000;
        if (hours < shiftHours) continue;
        const u = shiftUser(t, sh);
        if (u && !get("SELECT id FROM notifications WHERE user_id=? AND type='shift_due' AND json_extract(data,'$.shift_id')=?", u.id, sh.id)) {
          await notify(t, [u], { type: "shift_due", data: { shift_id: sh.id },
            title: "⏰ Shift time over — close your shift", body: `${Math.floor(hours)} ghante ho gaye. Meter readings aur cash darj kar ke shift close karein.` });
          reminded++;
        }
        if (hours >= shiftHours + 1) {
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
  {
    key: "day_close",
    name: "Close the day at midnight",
    description: "Just after midnight the previous day is locked (only the admin can change it) and the owner gets the day's sales, expenses, supply, stock, cash and a printable report link on WhatsApp.",
    cron: "5 0 * * *",
    run: async (t) => {
      const r = await closeDay(t);
      return "already" in r ? `${r.day} was already closed` : `${r.day} closed · sales ${pkr(r.revenue)}`;
    },
  },
  {
    key: "licence_watch",
    name: "Licence & certificate expiry",
    description: "Every morning: explosives licence, OGRA, fire NOC, nap-tol (calibration) and other certificates — reminders 30, 7 and 1 day before expiry, on the day and weekly after, to the owner on WhatsApp.",
    cron: "0 9 * * *",
    run: async (t) => `${await licenceWatch(t)} reminders`,
  },
  {
    key: "checklist_watch",
    name: "Daily checklist follow-up",
    description: "At noon and 8pm, tells the managers which daily checks (cleaning, water in tank, density, 5-litre measure, fire extinguishers…) are not done yet.",
    cron: "0 12,20 * * *",
    run: async (t) => `${await checklistWatch(t)} stations with missing checks`,
  },
  {
    key: "attendance_watch",
    name: "Attendance follow-up",
    description: "Every hour: staff who have not checked in an hour after their duty time (not on weekly off or leave) are reported to the manager.",
    cron: "15 * * * *",
    run: async (t) => `${await attendanceWatch(t)} not checked in`,
  },
  {
    key: "staff_risk",
    name: "Weekly staff risk report",
    description: "Monday 10am: the owner gets each salesman with a risk score — short shifts, litres not entered on the POS, undone sales, late days and failed checks.",
    cron: "0 10 * * 1",
    run: async (t) => `${await weeklyStaffRisk(t)} salesmen flagged`,
  },
  {
    key: "khata_overdue",
    name: "Overdue khata hold",
    description: "Every morning: khata accounts with no payment for the set number of days (default 60) are put on hold and told on WhatsApp; a payment lifts the hold. Police / government / schools are left out unless switched on.",
    cron: "0 8 * * *",
    run: async (t) => `${await khataOverdue(t)} accounts put on hold`,
  },
  {
    key: "khata_late_fee",
    name: "Late-payment charge",
    description: "On the 1st, if a late-charge percent is set in Settings, adds it to khata balances unpaid for 30+ days (never to institutions). Off by default.",
    cron: "0 8 1 * *",
    run: async (t) => `${await khataLateFees(t)} charges added`,
  },
  {
    key: "carriage_fuel_held",
    name: "Fuel money to forward",
    description: "Every morning: if a thekedar's carriage fuel money is still with us (received but not sent to the depot) for over a day, the owner and managers are reminded to forward it.",
    cron: "0 9 * * *",
    run: async (t) => `${await carriageFuelHeldWatch(t)} fuel payments to forward`,
  },
  {
    key: "booking_reminders",
    name: "Service booking reminders",
    description: "Every 15 minutes: WhatsApp reminder about an hour before each car wash / oil change / tyre booking.",
    cron: "*/15 * * * *",
    run: async (t) => `${await bookingReminders(t)} reminders`,
  },
  {
    key: "service_due",
    name: "Oil change due",
    description: "Daily: customers whose last oil change was 3 months ago get a reminder to book the next one (opted-in only).",
    cron: "30 10 * * *",
    run: async (t) => `${await serviceDue(t)} reminders`,
  },
  {
    key: "backup",
    name: "Nightly backup",
    description: "At 2:30am a full copy of the database is saved (the last 14 are kept). Download or restore them in Settings → Backups.",
    cron: "30 2 * * *",
    run: async () => { const b = makeBackup(); return `${b.name} (${Math.round(b.bytes / 1024)} KB)`; },
  },
  {
    key: "recurring_expenses",
    name: "Monthly fixed expenses",
    description: "Every morning: rent, security, internet and other fixed costs set in Expenses → Monthly are booked by themselves on their day of the month.",
    cron: "0 7 * * *",
    run: async (t) => `${await bookRecurring(t)} booked`,
  },
  {
    key: "leaderboard",
    name: "Weekly salesman leaderboard",
    description: "Monday 9am: every salesman gets last week's top 3 (litres, shop sales, customer rating) and their own place and commission.",
    cron: "0 9 * * 1",
    run: async (t) => `${await weeklyLeaderboard(t)} salesmen ranked`,
  },
  {
    key: "staff_coaching",
    name: "Daily coaching for salesmen",
    description: "8:30am: each salesman who worked yesterday gets a short message (app + WhatsApp) with what went well and one or two tips — cash short, litres not entered, late, ratings, shop sales. Written by AI when the key is set.",
    cron: "30 8 * * *",
    run: async (t) => `${await dailyCoaching(t)} messages sent`,
  },
  {
    key: "training_due",
    name: "Training due",
    description: "Monday 9:30am: staff whose fire safety, POS or other training is overdue, due in 30 days or never done — to the managers and to each person.",
    cron: "30 9 * * 1",
    run: async (t) => `${await trainingWatch(t)} staff need training`,
  },
  {
    key: "machine_watch",
    name: "Machine service & warranty",
    description: "Every morning: dispensers, generator, compressor and other machines whose service is due (by date or generator hours), warranty ending, or a fault not repaired for 2 days — to the managers and the owner.",
    cron: "15 9 * * *",
    run: async (t) => `${await machineWatch(t)} machines need attention`,
  },
  {
    key: "monthly_bills",
    name: "Monthly khata bills & wholesale statements",
    description: "On the 1st at 9am, every khata account and wholesale client with activity or dues gets last month's bill on WhatsApp with a printable link (every fill, slip and payment).",
    cron: "0 9 1 * *",
    run: async (t) => {
      const r = await monthlyBills(t);
      return `${r.month}: ${r.khata} khata bills, ${r.wholesale} wholesale statements sent`;
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
