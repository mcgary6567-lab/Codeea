/**
 * Manager's desk: what is happening right now (live shifts, who is on duty) and a ranked list of
 * suggestions for today — each one says what to do and links to the screen that does it.
 */
import { Router } from "express";
import { all, get, pkDate, pkDayStart, getSetting } from "../db.js";
import { h, tid, requirePerm } from "../auth.js";
import { pkr } from "../services.js";
import { PRODUCTS } from "../config.js";
import { tankOutlook } from "../ai/analytics.js";
import { shiftSummary } from "../shifts.js";
import { meterSales } from "./reports.js";
import { onlineToday } from "./cashier.js";

export const managerDesk = Router();
const DAY = 86_400_000;
type Sug = { level: "critical" | "warning" | "info" | "good"; title: string; detail: string; to?: string; label?: string };

managerDesk.get("/dashboard/desk", requirePerm("dashboard.view"), h((req) => {
  const t = tid(req), nowMs = Date.now(), today = pkDate(), dayStart = pkDayStart();
  const pkHour = new Date(nowMs + 5 * 3600_000).getUTCHours();
  // a forecourt shift usually runs a full 24 hours (e.g. 8am→8am); the owner can tune it
  const shiftHours = Number(getSetting(t, "shift_hours", "24")) || 24;
  const sug: Sug[] = [];

  /* ---- live shifts ---- */
  const shifts = all(`SELECT sh.*, s.name station_name FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE s.tenant_id=? AND sh.status='open' ORDER BY sh.opened_at`, t).map((sh) => {
    const sum = shiftSummary(sh.id);
    return { id: sh.id, attendant: sh.attendant, station: sh.station_name, opened_at: sh.opened_at, hours: Math.round(((nowMs - Date.parse(sh.opened_at)) / 3600_000) * 10) / 10,
      litres: sum.litres, amount: sum.amount, cash_expected: sum.cash_expected };
  });
  for (const s of shifts) if (s.hours >= shiftHours)
    sug.push({ level: "warning", title: `${s.attendant}'s shift has been open ${Math.floor(s.hours)} hours`, detail: `${s.station}. The ${shiftHours}-hour shift is over — close it with the meter readings and cash count, then start the next one.`, to: "/shifts", label: "Shifts" });

  /* ---- staff on duty today ---- */
  const weekday = new Date(Date.parse(today + "T12:00:00+05:00")).getUTCDay();
  const staff = all("SELECT id, name, role, duty_start, weekly_off FROM users WHERE tenant_id=? AND active=1 AND role<>'admin' AND duty_start IS NOT NULL ORDER BY name", t).map((u) => {
    const a = get("SELECT check_in, check_out, late_minutes, away_m FROM attendance WHERE user_id=? AND day=?", u.id, today);
    const leave = get("SELECT type FROM leaves WHERE user_id=? AND status='approved' AND from_day<=? AND to_day>=?", u.id, today, today);
    const due = Date.parse(`${today}T${u.duty_start}:00+05:00`);
    const status = a ? (a.late_minutes > 15 ? "late" : "present") : leave ? "leave" : u.weekly_off === weekday ? "off" : nowMs > due + 30 * 60_000 ? "missing" : "due";
    return { id: u.id, name: u.name, role: u.role, duty_start: u.duty_start, status, check_in: a?.check_in ?? null, check_out: a?.check_out ?? null, late_minutes: a?.late_minutes ?? 0, away_m: a?.away_m ?? null };
  });
  const missing = staff.filter((s) => s.status === "missing");
  if (missing.length)
    sug.push({ level: "warning", title: `${missing.map((s) => s.name).join(", ")} not checked in yet`, detail: `Duty started at ${missing.map((s) => s.duty_start).join(" / ")}. Call them, or record leave so the salary is right.`, to: "/staff", label: "Attendance" });
  for (const s of staff) if (s.away_m != null && s.away_m > 300)
    sug.push({ level: "warning", title: `${s.name} checked in ${Math.round(s.away_m).toLocaleString()} m away from the pump`, detail: "Check the selfie and map pin on the attendance page.", to: "/staff", label: "Attendance" });

  /* ---- cash and POS discipline ---- */
  for (const r of all(`SELECT sh.attendant, SUM(sh.variance) v, COUNT(*) n FROM shifts sh JOIN stations s ON s.id=sh.station_id
      WHERE s.tenant_id=? AND sh.status='closed' AND sh.closed_at >= ? GROUP BY sh.attendant HAVING SUM(sh.variance) < -1000 ORDER BY v`, t, new Date(nowMs - 3 * DAY).toISOString()))
    sug.push({ level: r.v < -5000 ? "critical" : "warning", title: `${r.attendant} is short ${pkr(-r.v)} in the last 3 days`, detail: `${r.n} shift(s). Talk to them today; the shortage is charged to their account.`, to: "/staff", label: "Staff accounts" });
  for (const r of all(`SELECT sh.attendant, SUM(CASE WHEN x.source='meter' THEN x.litres ELSE 0 END) m, SUM(x.litres) l FROM sales x JOIN shifts sh ON sh.id=x.shift_id
      JOIN stations s ON s.id=x.station_id WHERE s.tenant_id=? AND x.created_at >= ? GROUP BY sh.attendant`, t, new Date(nowMs - 2 * DAY).toISOString()))
    if (r.l > 500 && r.m / r.l > 0.15)
      sug.push({ level: "info", title: `${r.attendant} left ${Math.round((r.m / r.l) * 100)}% of litres off the POS`, detail: `${Math.round(r.m).toLocaleString()} L were only found from the meters at shift end. Ask them to enter every sale — it keeps khata, receipts and loyalty right.`, to: "/team", label: "Coaching" });

  /* ---- stock ---- */
  for (const tk of tankOutlook(t)) if (tk.days_to_reorder <= 1.5)
    sug.push({ level: tk.days_to_empty <= 1 ? "critical" : "warning", title: `${tk.name} (${tk.station_name}): order ${Math.round(tk.suggested_order_l).toLocaleString()} L`, detail: `${Math.round(tk.current_l).toLocaleString()} L left — about ${tk.days_to_empty} days at ${Math.round(tk.forecast_daily_l).toLocaleString()} L a day.`, to: "/stock", label: "Order fuel" });
  if (pkHour >= 9) {
    const noDip = all(`SELECT t.name, s.name station FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? AND NOT EXISTS (SELECT 1 FROM dip_readings d WHERE d.tank_id=t.id AND d.created_at >= ?)`, t, dayStart);
    if (noDip.length) sug.push({ level: "info", title: `No dip reading today for ${noDip.length} tank${noDip.length > 1 ? "s" : ""}`, detail: noDip.map((x) => `${x.name} (${x.station})`).join(", ") + ". A daily dip catches leaks and short deliveries early.", to: "/stock", label: "Enter dip" });
  }
  // a meter that sold nothing while its twin sold plenty may be faulty or blocked
  const ms = meterSales(t, new Date(nowMs - 3 * DAY).toISOString(), new Date(nowMs).toISOString());
  for (const m of ms) {
    const twin = ms.filter((x) => x.station_id === m.station_id && x.product === m.product && x.nozzle_id !== m.nozzle_id);
    if (m.litres < 1 && twin.some((x) => x.litres > 500))
      sug.push({ level: "info", title: `Meter ${m.meter} sold nothing in 3 days`, detail: `${m.station} — the other ${PRODUCTS[m.product]} meter is busy. Check the dispenser for a fault.`, to: "/machines", label: "Machines" });
  }

  /* ---- money owed ---- */
  const old = all(`SELECT c.id, c.name, c.balance, c.credit_limit, (SELECT MAX(created_at) FROM khata_ledger k WHERE k.customer_id=c.id AND k.type='credit') lp
    FROM customers c WHERE c.tenant_id=? AND c.balance > 0 ORDER BY c.balance DESC`, t)
    .filter((c) => !c.lp || nowMs - Date.parse(c.lp) > 30 * DAY);
  if (old.length) {
    const sum = old.reduce((a, c) => a + c.balance, 0);
    sug.push({ level: sum > 500_000 ? "critical" : "warning", title: `${old.length} khata customers have not paid for 30+ days`, detail: `${pkr(sum)} in total — biggest: ${old.slice(0, 3).map((c) => `${c.name} ${pkr(c.balance)}`).join(", ")}. Send reminders today.`, to: "/khata", label: "Khata" });
  }
  const over = get("SELECT COUNT(*) n FROM customers WHERE tenant_id=? AND credit_limit > 0 AND balance > credit_limit", t)!.n;
  if (over) sug.push({ level: "warning", title: `${over} khata customer${over > 1 ? "s are" : " is"} over the credit limit`, detail: "Collect before giving more fuel on credit.", to: "/khata", label: "Khata" });

  /* ---- approvals waiting ---- */
  const pend = {
    expenses: get("SELECT COUNT(*) n, COALESCE(SUM(amount),0) a FROM expenses WHERE tenant_id=? AND status='pending'", t)!,
    leaves: get("SELECT COUNT(*) n FROM leaves WHERE tenant_id=? AND status='pending'", t)!.n,
    prices: get("SELECT COUNT(*) n FROM price_requests WHERE tenant_id=? AND status='pending'", t)!.n,
    orders: get("SELECT COUNT(*) n FROM orders WHERE tenant_id=? AND status='pending'", t)!.n,
    complaints: get("SELECT COUNT(*) n FROM complaints WHERE tenant_id=? AND status='open'", t)!.n,
    chats: get("SELECT COUNT(*) n FROM conversations WHERE tenant_id=? AND mode='human' AND unread > 0", t)!.n,
  };
  if (pend.prices) sug.push({ level: "critical", title: `${pend.prices} price change waiting for approval`, detail: "Pumps keep selling at the old rate until it is approved.", to: "/prices", label: "Prices" });
  if (pend.expenses.n) sug.push({ level: "info", title: `${pend.expenses.n} expense${pend.expenses.n > 1 ? "s" : ""} waiting for approval`, detail: `${pkr(pend.expenses.a)} in total.`, to: "/expenses", label: "Expenses" });
  if (pend.leaves) sug.push({ level: "info", title: `${pend.leaves} leave request${pend.leaves > 1 ? "s" : ""} to decide`, detail: "Approve or reject so the duty roster and salary stay right.", to: "/staff", label: "Leave" });
  if (pend.orders) sug.push({ level: "warning", title: `${pend.orders} customer order${pend.orders > 1 ? "s" : ""} pending`, detail: "Confirm or schedule delivery.", to: "/orders", label: "Orders" });
  if (pend.chats) sug.push({ level: "warning", title: `${pend.chats} WhatsApp chat${pend.chats > 1 ? "s" : ""} waiting for a person`, detail: "The AI handed these over — reply from the inbox.", to: "/inbox", label: "Inbox" });
  if (pend.complaints) sug.push({ level: "info", title: `${pend.complaints} open complaint${pend.complaints > 1 ? "s" : ""}`, detail: "Call the customer and close each one.", to: "/complaints", label: "Complaints" });

  /* ---- compliance and machines ---- */
  if (pkHour >= 10) {
    const items = get("SELECT COUNT(*) n FROM checklist_items WHERE tenant_id=? AND active=1 AND frequency='daily'", t)!.n;
    if (items) for (const s of all("SELECT id, name FROM stations WHERE tenant_id=?", t)) {
      const done = get("SELECT COUNT(DISTINCT item_id) n FROM checklist_entries WHERE station_id=? AND day=?", s.id, today)!.n;
      if (done < items) sug.push({ level: "info", title: `Daily checks: ${done} of ${items} done at ${s.name}`, detail: "Fire extinguishers, leaks, cleanliness and the 5-litre measure.", to: "/checklist", label: "Daily checks" });
    }
  }
  const in30 = new Date(nowMs + 30 * DAY + 5 * 3600_000).toISOString().slice(0, 10);
  for (const l of all("SELECT name, expires_on FROM licences WHERE tenant_id=? AND expires_on <= ? ORDER BY expires_on", t, in30))
    sug.push({ level: l.expires_on < today ? "critical" : "warning", title: `Licence ${l.expires_on < today ? "expired" : "expires"}: ${l.name} (${l.expires_on})`, detail: "Start the renewal now — inspections can stop sales.", to: "/compliance", label: "Licences" });
  const in3 = new Date(nowMs + 3 * DAY + 5 * 3600_000).toISOString().slice(0, 10);
  const svc = all("SELECT name, next_service_on FROM machines WHERE tenant_id=? AND next_service_on IS NOT NULL AND next_service_on <= ? ORDER BY next_service_on", t, in3);
  if (svc.length) sug.push({ level: svc.some((m) => m.next_service_on < today) ? "warning" : "info", title: `${svc.length} machine${svc.length > 1 ? "s" : ""} due for service`, detail: svc.slice(0, 4).map((m) => `${m.name} (${m.next_service_on})`).join(", "), to: "/machines", label: "Machines" });

  /* ---- a word of praise ---- */
  const best = get(`SELECT sh.attendant, SUM(x.litres) l FROM sales x JOIN shifts sh ON sh.id=x.shift_id JOIN stations s ON s.id=x.station_id
    WHERE s.tenant_id=? AND x.created_at >= ? GROUP BY sh.attendant ORDER BY l DESC LIMIT 1`, t, new Date(nowMs - 7 * DAY).toISOString());
  if (best?.l) sug.push({ level: "good", title: `Best salesman this week: ${best.attendant}`, detail: `${Math.round(best.l).toLocaleString()} L sold. A word of praise in front of the team goes a long way.`, to: "/team", label: "Leaderboard" });

  const order = { critical: 0, warning: 1, info: 2, good: 3 };
  sug.sort((a, b) => order[a.level] - order[b.level]);
  return {
    shifts, staff, pending: pend, shift_hours: shiftHours,
    // today's online money (card / JazzCash / Easypaisa / Raast): same figures as the owner's and the cashier's view
    online: onlineToday(tid(req)),
    staff_summary: { on_duty: staff.filter((s) => s.status === "present" || s.status === "late").length, late: staff.filter((s) => s.status === "late").length, missing: missing.length, total: staff.length },
    cash_in_shifts: Math.round(shifts.reduce((a, s) => a + s.cash_expected, 0)),
    khata_overdue: Math.round(old.reduce((a, c) => a + c.balance, 0)),
    suggestions: sug.slice(0, 14),
  };
}));
