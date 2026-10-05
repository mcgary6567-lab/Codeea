/**
 * Running the pump by the rules, without registers:
 *  - Licences & certificates (explosives, OGRA, fire NOC, nap-tol/calibration…) with expiry reminders
 *  - Daily / weekly checklist with photos and readings (water in tank, density, 5-litre measure test…)
 *  - Attendance: check-in/out with selfie and location, lateness, weekly off and leave
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, now, pkDate } from "../db.js";
import { h, parse, tid, requirePerm, can, scopedStation } from "../auth.js";
import { AppError, round2, createAlert } from "../services.js";
import { notify, staff } from "../notifications.js";
import { linkPhotos } from "./capture.js";
import { sendDirect } from "../whatsapp/cloud.js";
import { getSetting } from "../db.js";

export const compliance = Router();
const DAY = 86_400_000;
const daysLeft = (d: string) => Math.ceil((Date.parse(`${d}T00:00:00+05:00`) - Date.parse(`${pkDate()}T00:00:00+05:00`)) / DAY);
const dateStr = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);

/* ================= Licences ================= */
compliance.get("/licences", requirePerm("alerts.view"), h((req) =>
  all("SELECT l.*, s.name station_name FROM licences l LEFT JOIN stations s ON s.id=l.station_id WHERE l.tenant_id=? ORDER BY l.expires_on", tid(req))
    .map((l) => ({ ...l, days_left: daysLeft(l.expires_on) }))));

const licBody = z.object({ name: z.string().min(2).max(80), number: z.string().max(60).optional().nullable(), authority: z.string().max(80).optional().nullable(),
  station_id: z.number().optional().nullable(), issued_on: dateStr.optional().nullable(), expires_on: dateStr, photo_id: z.number().optional().nullable(), note: z.string().max(200).optional().nullable() });
compliance.post("/licences", requirePerm("alerts.view"), h((req) => {
  const b = parse(licBody, req.body);
  const { id } = run("INSERT INTO licences (tenant_id,station_id,name,number,authority,issued_on,expires_on,photo_id,note,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
    tid(req), b.station_id ?? null, b.name, b.number ?? null, b.authority ?? null, b.issued_on ?? null, b.expires_on, null, b.note ?? null, now());
  if (b.photo_id && linkPhotos(tid(req), [b.photo_id], `licence:${id}`)) run("UPDATE licences SET photo_id=? WHERE id=?", b.photo_id, id);
  return get("SELECT * FROM licences WHERE id=?", id);
}));
/** Renewed: new expiry date (and a photo of the new certificate). */
compliance.patch("/licences/:id", requirePerm("alerts.view"), h((req) => {
  const l = get("SELECT * FROM licences WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  if (!l) throw new AppError(404, "Licence not found");
  const b = parse(licBody.partial(), req.body);
  const m = { ...l, ...b };
  run("UPDATE licences SET name=?, number=?, authority=?, station_id=?, issued_on=?, expires_on=?, note=? WHERE id=?", m.name, m.number ?? null, m.authority ?? null, m.station_id ?? null, m.issued_on ?? null, m.expires_on, m.note ?? null, l.id);
  if (b.photo_id && linkPhotos(tid(req), [b.photo_id], `licence:${l.id}:${Date.now()}`)) run("UPDATE licences SET photo_id=? WHERE id=?", b.photo_id, l.id);
  return get("SELECT * FROM licences WHERE id=?", l.id);
}));
compliance.delete("/licences/:id", requirePerm("settings.manage"), h((req) => {
  run("DELETE FROM licences WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  return { ok: true };
}));

/** Daily: reminders 30, 7 and 1 day before expiry, on the day, and weekly once expired. */
export async function licenceWatch(t: number) {
  const owner = getSetting(t, "owner_phone") || get("SELECT owner_phone FROM tenants WHERE id=?", t)?.owner_phone;
  let n = 0;
  for (const l of all("SELECT l.*, s.name station_name FROM licences l LEFT JOIN stations s ON s.id=l.station_id WHERE l.tenant_id=?", t)) {
    const d = daysLeft(l.expires_on);
    const due = [30, 7, 1, 0].includes(d) || (d < 0 && -d % 7 === 0);
    if (!due) continue;
    const title = d < 0 ? `⛔ ${l.name} expired ${-d} days ago` : d === 0 ? `⛔ ${l.name} expires TODAY` : `⏰ ${l.name} expires in ${d} day${d === 1 ? "" : "s"}`;
    const a = createAlert(t, { station_id: l.station_id, type: "licence_expiry", severity: d <= 7 ? "critical" : "warning", title,
      body: `${l.station_name ? `${l.station_name} · ` : ""}${l.number ? `No. ${l.number} · ` : ""}${l.authority ?? ""} — expiry ${l.expires_on}. Renew it and update the date.`, dedupe_key: `lic-${l.id}-${d}` });
    if (!a) continue;
    n++;
    await notify(t, staff(t, ["admin", "manager"]), { type: "licence_expiry", title, body: a.body ?? "" });
    if (owner) await sendDirect(t, { phone: owner, name: "Owner" }, "licence_expiry", `lic:${l.id}:${d}`, `${title}\n${a.body}`);
  }
  return n;
}

/* ================= Checklist ================= */
compliance.get("/checklist/items", requirePerm("sales.create"), h((req) =>
  all("SELECT * FROM checklist_items WHERE tenant_id=? ORDER BY active DESC, frequency, sort, id", tid(req))));

const itemBody = z.object({ title: z.string().min(3).max(100), urdu: z.string().max(100).optional().nullable(), frequency: z.enum(["daily", "weekly"]).default("daily"),
  kind: z.enum(["check", "number"]).default("check"), unit: z.string().max(12).optional().nullable(), min_ok: z.number().optional().nullable(), max_ok: z.number().optional().nullable(),
  needs_photo: z.boolean().default(false), active: z.boolean().optional() });
compliance.post("/checklist/items", requirePerm("alerts.view"), h((req) => {
  const b = parse(itemBody, req.body);
  const { id } = run("INSERT INTO checklist_items (tenant_id,title,urdu,frequency,kind,unit,min_ok,max_ok,needs_photo,sort) VALUES (?,?,?,?,?,?,?,?,?,?)",
    tid(req), b.title, b.urdu ?? null, b.frequency, b.kind, b.unit ?? null, b.min_ok ?? null, b.max_ok ?? null, b.needs_photo ? 1 : 0, 99);
  return get("SELECT * FROM checklist_items WHERE id=?", id);
}));
compliance.patch("/checklist/items/:id", requirePerm("alerts.view"), h((req) => {
  const i = get("SELECT * FROM checklist_items WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  if (!i) throw new AppError(404, "Item not found");
  const b = parse(itemBody.partial(), req.body);
  const m = { ...i, ...b };
  run("UPDATE checklist_items SET title=?, urdu=?, frequency=?, kind=?, unit=?, min_ok=?, max_ok=?, needs_photo=?, active=? WHERE id=?",
    m.title, m.urdu ?? null, m.frequency, m.kind, m.unit ?? null, m.min_ok ?? null, m.max_ok ?? null, m.needs_photo ? 1 : 0, b.active === undefined ? i.active : b.active ? 1 : 0, i.id);
  return get("SELECT * FROM checklist_items WHERE id=?", i.id);
}));

/** Today's checklist for a station: each item with whether it is done (weekly items: done in the last 7 days). */
export function checklistToday(t: number, stationId: number, day = pkDate()) {
  const weekAgo = pkDate(Date.parse(`${day}T12:00:00+05:00`) - 6 * DAY);
  return all("SELECT * FROM checklist_items WHERE tenant_id=? AND active=1 ORDER BY frequency, sort, id", t).map((i) => {
    const e = get(`SELECT * FROM checklist_entries WHERE station_id=? AND item_id=? AND day ${i.frequency === "weekly" ? ">= ? AND day <= ?" : "= ?"} ORDER BY id DESC LIMIT 1`,
      stationId, i.id, ...(i.frequency === "weekly" ? [weekAgo, day] : [day]));
    return { ...i, entry: e ?? null, done: Boolean(e) };
  });
}
compliance.get("/checklist", requirePerm("sales.create"), h((req) => {
  const station = scopedStation(req, req.query.station_id ? Number(req.query.station_id) : null) ?? get("SELECT id FROM stations WHERE tenant_id=? ORDER BY id LIMIT 1", tid(req))!.id;
  const day = req.query.day ? String(req.query.day) : pkDate();
  const items = checklistToday(tid(req), station, day);
  return { station_id: station, day, items, done: items.filter((i) => i.done).length, total: items.length };
}));

compliance.post("/checklist/:itemId", requirePerm("sales.create"), h(async (req) => {
  const t = tid(req);
  const i = get("SELECT * FROM checklist_items WHERE id=? AND tenant_id=? AND active=1", Number(req.params.itemId), t);
  if (!i) throw new AppError(404, "Checklist item not found");
  const b = parse(z.object({ station_id: z.number().optional(), ok: z.boolean().optional(), value: z.number().optional().nullable(), note: z.string().max(200).optional().nullable(), photo_id: z.number().optional().nullable() }), req.body);
  const station = scopedStation(req, b.station_id ?? null) ?? get("SELECT id FROM stations WHERE tenant_id=? ORDER BY id LIMIT 1", t)!.id;
  if (i.needs_photo && !b.photo_id) throw new AppError(400, "Take a photo for this check");
  if (i.kind === "number" && (b.value == null || isNaN(b.value))) throw new AppError(400, `Enter the reading${i.unit ? ` (${i.unit})` : ""}`);
  // a reading outside the allowed range is a failed check
  const inRange = i.kind !== "number" || ((i.min_ok == null || b.value! >= i.min_ok) && (i.max_ok == null || b.value! <= i.max_ok));
  const ok = (b.ok ?? true) && inRange;
  const { id } = run("INSERT INTO checklist_entries (tenant_id,station_id,item_id,day,ok,value,note,photo_id,done_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
    t, station, i.id, pkDate(), ok ? 1 : 0, b.value ?? null, b.note ?? null, null, req.user!.name, now());
  if (b.photo_id && linkPhotos(t, [b.photo_id], `check:${id}`)) run("UPDATE checklist_entries SET photo_id=? WHERE id=?", b.photo_id, id);
  if (!ok) {
    const a = createAlert(t, { station_id: station, type: "checklist_fail", severity: "warning",
      title: `Check failed: ${i.title}`, body: `${b.value != null ? `Reading ${b.value}${i.unit ? ` ${i.unit}` : ""}${i.min_ok != null || i.max_ok != null ? ` (allowed ${i.min_ok ?? "…"} to ${i.max_ok ?? "…"})` : ""}. ` : ""}${b.note ?? ""} — by ${req.user!.name}` });
    if (a) await notify(t, staff(t, ["manager", "admin"]), { type: "checklist_fail", title: a.title, body: a.body ?? "" });
  }
  return { entry: get("SELECT * FROM checklist_entries WHERE id=?", id), ok };
}));

/** Midday and evening: any daily check not done yet is reported to the managers. */
export async function checklistWatch(t: number) {
  let n = 0;
  for (const st of all("SELECT id, name FROM stations WHERE tenant_id=?", t)) {
    const missing = checklistToday(t, st.id).filter((i) => !i.done && i.frequency === "daily");
    if (!missing.length) continue;
    const a = createAlert(t, { station_id: st.id, type: "checklist_missing", severity: "warning", title: `${st.name}: ${missing.length} daily check${missing.length === 1 ? "" : "s"} not done`,
      body: missing.map((m) => m.title).join(", "), dedupe_key: `chk-${st.id}-${pkDate()}-${new Date().getUTCHours() < 10 ? "am" : "pm"}` });
    if (a) { n++; await notify(t, staff(t, ["manager", "admin"]), { type: "checklist_missing", title: a.title, body: a.body ?? "" }); }
  }
  return n;
}

/* ================= Attendance & leave ================= */
const metres = (a: { lat: number; lng: number }, b: { lat: number; lng: number }) => {
  const R = 6371000, r = Math.PI / 180, dLat = (b.lat - a.lat) * r, dLng = (b.lng - a.lng) * r;
  return Math.round(2 * R * Math.asin(Math.sqrt(Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLng / 2) ** 2)));
};
/** Minutes late against the duty start time (Pakistan time). */
const lateBy = (dutyStart: string | null, at: number) => {
  if (!dutyStart) return 0;
  const start = Date.parse(`${pkDate(at)}T${dutyStart}:00+05:00`);
  return Math.max(0, Math.round((at - start) / 60_000));
};

export function checkIn(t: number, user: { id: number; name: string }, o: { station_id?: number | null; photo_id?: number | null; lat?: number | null; lng?: number | null; accuracy?: number | null; source: string }) {
  const day = pkDate();
  const existing = get("SELECT * FROM attendance WHERE user_id=? AND day=?", user.id, day);
  if (existing) return existing;
  const u = get("SELECT * FROM users WHERE id=?", user.id)!;
  const st = get("SELECT * FROM stations WHERE id=?", o.station_id ?? u.station_id ?? 0);
  const away = st?.lat != null && o.lat != null && o.lng != null ? metres({ lat: st.lat, lng: st.lng }, { lat: o.lat, lng: o.lng }) : null;
  const late = lateBy(u.duty_start, Date.now());
  const { id } = run("INSERT INTO attendance (tenant_id,user_id,station_id,day,check_in,in_lat,in_lng,in_acc,away_m,late_minutes,source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
    t, user.id, st?.id ?? null, day, now(), o.lat ?? null, o.lng ?? null, o.accuracy ?? null, away, late, o.source);
  if (o.photo_id && linkPhotos(t, [o.photo_id], `attend-in:${id}`)) run("UPDATE attendance SET in_photo_id=? WHERE id=?", o.photo_id, id);
  if (away != null && away > 300)
    createAlert(t, { station_id: st?.id, type: "attendance_away", severity: "warning", title: `${user.name} checked in ${away.toLocaleString()} m away from ${st!.name}`, dedupe_key: `away-${id}` });
  return get("SELECT * FROM attendance WHERE id=?", id);
}
export function checkOut(userId: number, photoId?: number | null, t?: number, pos?: { lat?: number | null; lng?: number | null; accuracy?: number | null }) {
  const a = get("SELECT * FROM attendance WHERE user_id=? AND check_out IS NULL ORDER BY id DESC LIMIT 1", userId);
  if (!a) return null;
  const st = a.station_id ? get("SELECT * FROM stations WHERE id=?", a.station_id) : null;
  const away = st?.lat != null && pos?.lat != null && pos?.lng != null ? metres({ lat: st.lat, lng: st.lng }, { lat: pos.lat, lng: pos.lng }) : null;
  run("UPDATE attendance SET check_out=?, out_lat=?, out_lng=?, out_away_m=? WHERE id=?", now(), pos?.lat ?? null, pos?.lng ?? null, away, a.id);
  if (photoId && t && linkPhotos(t, [photoId], `attend-out:${a.id}`)) run("UPDATE attendance SET out_photo_id=? WHERE id=?", photoId, a.id);
  return get("SELECT * FROM attendance WHERE id=?", a.id);
}

/**
 * Check-in / out from the app needs BOTH a live selfie and the phone's live location:
 * the selfie must be taken by this person in the last few minutes (camera, not an old photo)
 * and not already used, and the location must come with the request.
 */
const SELFIE_MAX_AGE_MS = 5 * 60_000;
const proof = z.object({
  photo_id: z.number({ required_error: "Take a live selfie first · پہلے سیلفی لیں", invalid_type_error: "Take a live selfie first · پہلے سیلفی لیں" }).int(),
  lat: z.number({ required_error: "Turn on location · لوکیشن آن کریں", invalid_type_error: "Turn on location · لوکیشن آن کریں" }).min(-90).max(90),
  lng: z.number({ required_error: "Turn on location · لوکیشن آن کریں", invalid_type_error: "Turn on location · لوکیشن آن کریں" }).min(-180).max(180),
  accuracy: z.number().min(0).optional().nullable(),
});
function liveSelfie(t: number, userId: number, photoId: number) {
  const p = get("SELECT * FROM photos WHERE id=? AND tenant_id=?", photoId, t);
  if (!p || p.kind !== "selfie" || p.created_by !== userId) throw new AppError(400, "Take a live selfie from your own phone · اپنی سیلفی لیں");
  if (p.ref) throw new AppError(400, "This selfie was already used — take a new one · نئی سیلفی لیں");
  if (Date.now() - Date.parse(p.created_at) > SELFIE_MAX_AGE_MS) throw new AppError(400, "Selfie is too old — take a new one · نئی سیلفی لیں");
}
compliance.post("/attendance/check-in", h((req) => {
  const b = parse(proof, req.body);
  liveSelfie(tid(req), req.user!.id, b.photo_id);
  return checkIn(tid(req), req.user!, { ...b, station_id: req.user!.station_id, source: "app" });
}));
compliance.post("/attendance/check-out", h((req) => {
  const b = parse(proof, req.body);
  liveSelfie(tid(req), req.user!.id, b.photo_id);
  const r = checkOut(req.user!.id, b.photo_id, tid(req), b);
  if (!r) throw new AppError(400, "You have not checked in");
  return r;
}));

/** Month summary for one person: present, late, leave, weekly off and unpaid absent days so far. */
export function attendanceMonth(t: number, userId: number, month = pkDate().slice(0, 7)) {
  const u = get("SELECT id, name, duty_start, weekly_off, salary, created_at FROM users WHERE id=? AND tenant_id=?", userId, t)!;
  const rows = all("SELECT * FROM attendance WHERE user_id=? AND day LIKE ? ORDER BY day", userId, `${month}-%`);
  const leaves = all("SELECT * FROM leaves WHERE user_id=? AND status='approved' AND from_day <= ? AND to_day >= ?", userId, `${month}-31`, `${month}-01`);
  const today = pkDate();
  const last = new Date(Date.UTC(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 0)).getUTCDate();
  // count from the month start, or from when the person joined (or their first check-in, if earlier)
  const joined = [u.created_at.slice(0, 10), rows[0]?.day].filter(Boolean).sort()[0] as string;
  const start = joined > `${month}-01` ? joined : `${month}-01`;
  const days: { day: string; status: string; late: number }[] = [];
  // attendance is only counted for staff with a duty time set
  for (let d = 1; d <= last && u.duty_start; d++) {
    const day = `${month}-${String(d).padStart(2, "0")}`;
    if (day > today || day < start) continue;
    const a = rows.find((r) => r.day === day);
    const lv = leaves.find((l) => l.from_day <= day && l.to_day >= day);
    const dow = new Date(`${day}T12:00:00+05:00`).getUTCDay();
    const status = a ? (a.late_minutes > 15 ? "late" : "present") : lv ? `leave_${lv.type}` : u.weekly_off === dow ? "off" : day === today ? "not_yet" : "absent";
    days.push({ day, status, late: a?.late_minutes ?? 0 });
  }
  const count = (s: string) => days.filter((d) => d.status === s).length;
  const unpaid = count("absent") + count("leave_unpaid");
  return {
    user: { id: u.id, name: u.name, duty_start: u.duty_start, weekly_off: u.weekly_off }, month, days, tracked: Boolean(u.duty_start),
    present: count("present") + count("late"), late: count("late"), absent: count("absent"), off: count("off"),
    paid_leave: count("leave_paid") + count("leave_sick"), unpaid_leave: count("leave_unpaid"),
    unpaid_days: unpaid, salary_cut: u.salary ? round2((u.salary / 30) * unpaid) : 0,
  };
}

compliance.get("/attendance/me", h((req) => ({
  today: get("SELECT * FROM attendance WHERE user_id=? AND day=?", req.user!.id, pkDate()) ?? null,
  month: attendanceMonth(tid(req), req.user!.id),
  leaves: all("SELECT * FROM leaves WHERE user_id=? ORDER BY id DESC LIMIT 10", req.user!.id),
})));
compliance.get("/attendance", requirePerm("staff.manage"), h((req) => {
  const month = String(req.query.month ?? pkDate().slice(0, 7));
  return {
    month, today: pkDate(),
    staff: all("SELECT id FROM users WHERE tenant_id=? AND active=1 AND role<>'admin' ORDER BY name", tid(req)).map((u) => attendanceMonth(tid(req), u.id, month)),
    present_today: all(`SELECT a.*, u.name FROM attendance a JOIN users u ON u.id=a.user_id WHERE a.tenant_id=? AND a.day=? ORDER BY a.check_in`, tid(req), pkDate()),
    leaves: all("SELECT l.*, u.name FROM leaves l JOIN users u ON u.id=l.user_id WHERE l.tenant_id=? ORDER BY CASE l.status WHEN 'pending' THEN 0 ELSE 1 END, l.id DESC LIMIT 40", tid(req)),
  };
}));
compliance.patch("/staff/:id/duty", requirePerm("staff.manage"), h((req) => {
  const b = parse(z.object({ duty_start: z.string().regex(/^\d{2}:\d{2}$/).nullable().optional(), weekly_off: z.number().int().min(0).max(6).nullable().optional() }), req.body);
  const u = get("SELECT id FROM users WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  if (!u) throw new AppError(404, "Staff member not found");
  if (b.duty_start !== undefined) run("UPDATE users SET duty_start=? WHERE id=?", b.duty_start, u.id);
  if (b.weekly_off !== undefined) run("UPDATE users SET weekly_off=? WHERE id=?", b.weekly_off, u.id);
  return attendanceMonth(tid(req), u.id);
}));

compliance.post("/leaves", h(async (req) => {
  const b = parse(z.object({ user_id: z.number().optional(), from_day: dateStr, to_day: dateStr, type: z.enum(["paid", "unpaid", "sick"]).default("paid"), reason: z.string().max(200).optional().nullable() }), req.body);
  const forOther = b.user_id && b.user_id !== req.user!.id;
  if (forOther && !can(req.user, "staff.manage")) throw new AppError(403, "You can only ask leave for yourself");
  if (b.to_day < b.from_day) throw new AppError(400, "'To' must be on or after 'From'");
  const userId = b.user_id ?? req.user!.id;
  if (!get("SELECT id FROM users WHERE id=? AND tenant_id=?", userId, tid(req))) throw new AppError(404, "Staff member not found");
  const status = forOther ? "approved" : "pending"; // a manager entering leave for someone approves it
  const { id } = run("INSERT INTO leaves (tenant_id,user_id,from_day,to_day,type,reason,status,decided_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
    tid(req), userId, b.from_day, b.to_day, b.type, b.reason ?? null, status, forOther ? req.user!.name : null, now());
  if (status === "pending") await notify(tid(req), staff(tid(req), ["manager", "admin"], req.user!.id), { type: "leave_request", title: `Leave request: ${req.user!.name}`, body: `${b.from_day} to ${b.to_day} (${b.type})${b.reason ? ` — ${b.reason}` : ""}` });
  return get("SELECT * FROM leaves WHERE id=?", id);
}));
compliance.post("/leaves/:id/:decision(approve|reject)", requirePerm("staff.manage"), h(async (req) => {
  const l = get("SELECT * FROM leaves WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  if (!l) throw new AppError(404, "Leave not found");
  const status = req.params.decision === "approve" ? "approved" : "rejected";
  run("UPDATE leaves SET status=?, decided_by=? WHERE id=?", status, req.user!.name, l.id);
  const u = get("SELECT id, phone FROM users WHERE id=?", l.user_id);
  if (u) await notify(tid(req), [u], { type: "leave_decision", title: `Leave ${status}`, body: `${l.from_day} to ${l.to_day}` });
  return get("SELECT * FROM leaves WHERE id=?", l.id);
}));

/** Day starts: who has not checked in by an hour after duty start. */
export async function attendanceWatch(t: number) {
  const nowMs = Date.now();
  const missing = all("SELECT id, name, duty_start, weekly_off FROM users WHERE tenant_id=? AND active=1 AND duty_start IS NOT NULL AND role<>'admin'", t)
    .filter((u) => lateBy(u.duty_start, nowMs) >= 60 && u.weekly_off !== new Date(nowMs + 5 * 3600_000).getUTCDay()
      && !get("SELECT id FROM attendance WHERE user_id=? AND day=?", u.id, pkDate())
      && !get("SELECT id FROM leaves WHERE user_id=? AND status='approved' AND from_day <= ? AND to_day >= ?", u.id, pkDate(), pkDate()));
  if (!missing.length) return 0;
  const a = createAlert(t, { type: "attendance_missing", severity: "warning", title: `Not checked in: ${missing.map((u) => u.name).join(", ")}`, dedupe_key: `att-${pkDate()}-${missing.map((u) => u.id).join(",")}` });
  if (a) await notify(t, staff(t, ["manager"]), { type: "attendance_missing", title: a.title, body: "More than an hour after duty start." });
  return missing.length;
}

/** The standard daily / weekly checks every new pump starts with (can be changed later). */
const DEFAULT_CHECKLIST: [string, string, string, string, string | null, number | null, number | null, number][] = [
    // title, urdu, frequency, kind, unit, min_ok, max_ok, needs_photo
    ["Forecourt and canopy clean", "فورکورٹ صاف", "daily", "check", null, null, null, 1],
    ["Water in tanks (water-finding paste)", "ٹینک میں پانی", "daily", "number", "mm", 0, 10, 1],
    ["Petrol density at 15°C", "پیٹرول ڈینسٹی", "daily", "number", "kg/m³", 720, 775, 0],
    ["Diesel density at 15°C", "ڈیزل ڈینسٹی", "daily", "number", "kg/m³", 815, 870, 0],
    ["5-litre measure test (difference)", "5 لیٹر ناپ", "daily", "number", "ml", -25, 25, 0],
    ["Fire extinguishers & sand buckets in place", "آگ بجھانے کا سامان", "daily", "check", null, null, null, 0],
    ["Washrooms clean", "واش روم صاف", "daily", "check", null, null, null, 1],
    ["Air & water machine working", "ہوا اور پانی", "daily", "check", null, null, null, 0],
    ["Generator oil & fuel check", "جنریٹر", "weekly", "check", null, null, null, 0],
    ["Emergency shut-off & earthing check", "ایمرجنسی بند", "weekly", "check", null, null, null, 0],
  ];
export function addDefaultChecklist(tenantId: number) {
  if (get("SELECT id FROM checklist_items WHERE tenant_id=? LIMIT 1", tenantId)) return;
  DEFAULT_CHECKLIST.forEach(([title, urdu, freq, kind, unit, min, max, photo], i) =>
    run("INSERT INTO checklist_items (tenant_id,title,urdu,frequency,kind,unit,min_ok,max_ok,needs_photo,sort) VALUES (?,?,?,?,?,?,?,?,?,?)", tenantId, title, urdu, freq, kind, unit, min, max, photo, i));
}
