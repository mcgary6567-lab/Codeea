/**
 * Machines register: dispensers, generator, compressor, fans, lights, UPS, pumps… with
 * service every N days (or N running hours for the generator), service and repair history with cost,
 * fault reports (salesmen can report from their phone), warranty dates and reminders when due.
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, now, pkDate, tx, getSetting } from "../db.js";
import { h, parse, tid, requirePerm, scopedStation } from "../auth.js";
import { AppError, round2, createAlert } from "../services.js";
import { notify, staff } from "../notifications.js";
import { sendDirect } from "../whatsapp/cloud.js";
import { linkPhotos } from "./capture.js";
import { ensureCategories } from "./expenses.js";

export const machines = Router();
const DAY = 86_400_000;
const dateStr = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
export const MACHINE_TYPES = ["dispenser", "generator", "compressor", "submersible_pump", "fan", "light", "ups", "inverter", "air_conditioner", "cctv", "water_pump", "car_wash", "other"] as const;
const addDays = (d: string, n: number) => pkDate(Date.parse(`${d}T12:00:00+05:00`) + n * DAY);
const daysTo = (d: string | null) => (d ? Math.round((Date.parse(`${d}T12:00:00+05:00`) - Date.parse(`${pkDate()}T12:00:00+05:00`)) / DAY) : null);

/** Service due by date and/or by running hours (generator). */
export function dueOf(m: any) {
  const dDays = daysTo(m.next_service_on);
  const hoursLeft = m.service_every_hours && m.hours != null ? round2((m.last_service_hours ?? 0) + m.service_every_hours - m.hours) : null;
  const warrantyDays = daysTo(m.warranty_until);
  const service = (dDays != null && dDays < 0) || (hoursLeft != null && hoursLeft < 0) ? "overdue"
    : (dDays != null && dDays <= 7) || (hoursLeft != null && hoursLeft <= 25) ? "due_soon" : dDays == null && hoursLeft == null ? "none" : "ok";
  return { service, days_to_service: dDays, hours_left: hoursLeft, warranty: warrantyDays == null ? "none" : warrantyDays < 0 ? "expired" : warrantyDays <= 30 ? "ending" : "ok", warranty_days: warrantyDays };
}
const withDue = (m: any) => ({ ...m, due: dueOf(m), open_faults: get("SELECT COUNT(*) n FROM machine_logs WHERE machine_id=? AND kind='fault' AND resolved_at IS NULL", m.id)!.n });

machines.get("/machines", requirePerm("sales.create"), h((req) => {
  const t = tid(req);
  const own = scopedStation(req);
  const rows = all(`SELECT m.*, s.name station_name FROM machines m LEFT JOIN stations s ON s.id=m.station_id WHERE m.tenant_id=? ${own ? "AND (m.station_id=? OR m.station_id IS NULL)" : ""}
    ORDER BY m.status='retired', s.id, m.type, m.name`, t, ...(own ? [own] : [])).map(withDue);
  const live = rows.filter((r) => r.status !== "retired");
  return {
    types: MACHINE_TYPES, machines: rows,
    summary: { total: live.length, faulty: live.filter((r) => ["faulty", "under_repair"].includes(r.status)).length, overdue: live.filter((r) => r.due.service === "overdue").length,
      due_soon: live.filter((r) => r.due.service === "due_soon").length, warranty_ending: live.filter((r) => r.due.warranty === "ending").length,
      spent_90d: round2(get("SELECT COALESCE(SUM(cost),0) v FROM machine_logs WHERE tenant_id=? AND day >= ?", t, pkDate(Date.now() - 90 * DAY))!.v) },
  };
}));
machines.get("/machines/:id", requirePerm("sales.create"), h((req) => {
  const m = get("SELECT m.*, s.name station_name FROM machines m LEFT JOIN stations s ON s.id=m.station_id WHERE m.id=? AND m.tenant_id=?", Number(req.params.id), tid(req));
  if (!m) throw new AppError(404, "Machine not found");
  const logs = all("SELECT * FROM machine_logs WHERE machine_id=? ORDER BY day DESC, id DESC", m.id);
  return { ...withDue(m), logs, spent_total: round2(logs.reduce((a, l) => a + (l.cost ?? 0), 0)), downtime_hours: round2(logs.reduce((a, l) => a + (l.downtime_hours ?? 0), 0)) };
}));

const body = z.object({
  name: z.string().min(2).max(80), type: z.enum(MACHINE_TYPES).default("other"), station_id: z.number().optional().nullable(),
  make: z.string().max(60).optional().nullable(), model: z.string().max(60).optional().nullable(), serial_no: z.string().max(60).optional().nullable(),
  location: z.string().max(80).optional().nullable(), installed_on: dateStr.optional().nullable(), cost: z.number().min(0).optional().nullable(),
  vendor: z.string().max(80).optional().nullable(), vendor_phone: z.string().max(20).optional().nullable(), warranty_until: dateStr.optional().nullable(),
  service_every_days: z.number().int().min(1).max(3650).optional().nullable(), service_every_hours: z.number().min(1).max(100000).optional().nullable(),
  last_service_on: dateStr.optional().nullable(), hours: z.number().min(0).optional().nullable(), notes: z.string().max(300).optional().nullable(),
  status: z.enum(["working", "faulty", "under_repair", "retired"]).optional(), photo_id: z.number().optional().nullable(),
});
const nextService = (last: string | null | undefined, every: number | null | undefined) => (every ? addDays(last ?? pkDate(), every) : null);

machines.post("/machines", requirePerm("alerts.view"), h((req) => {
  const t = tid(req);
  const b = parse(body, req.body);
  if (b.station_id && !get("SELECT id FROM stations WHERE id=? AND tenant_id=?", b.station_id, t)) throw new AppError(400, "Station not found");
  const { id } = run(`INSERT INTO machines (tenant_id,station_id,name,type,make,model,serial_no,location,installed_on,cost,vendor,vendor_phone,warranty_until,service_every_days,service_every_hours,
    last_service_on,next_service_on,hours,last_service_hours,status,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
  t, b.station_id ?? null, b.name, b.type, b.make ?? null, b.model ?? null, b.serial_no ?? null, b.location ?? null, b.installed_on ?? null, b.cost ?? null, b.vendor ?? null,
  b.vendor_phone ?? null, b.warranty_until ?? null, b.service_every_days ?? null, b.service_every_hours ?? null, b.last_service_on ?? null,
  nextService(b.last_service_on ?? b.installed_on, b.service_every_days), b.hours ?? null, b.hours ?? null, b.status ?? "working", b.notes ?? null, now());
  if (b.photo_id && linkPhotos(t, [b.photo_id], `machine:${id}`)) run("UPDATE machines SET photo_id=? WHERE id=?", b.photo_id, id);
  return withDue(get("SELECT * FROM machines WHERE id=?", id));
}));
machines.patch("/machines/:id", requirePerm("alerts.view"), h((req) => {
  const m = get("SELECT * FROM machines WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  if (!m) throw new AppError(404, "Machine not found");
  const b = parse(body.partial(), req.body);
  const x = { ...m, ...b };
  const scheduleChanged = b.service_every_days !== undefined || b.last_service_on !== undefined;
  run(`UPDATE machines SET station_id=?, name=?, type=?, make=?, model=?, serial_no=?, location=?, installed_on=?, cost=?, vendor=?, vendor_phone=?, warranty_until=?,
    service_every_days=?, service_every_hours=?, last_service_on=?, next_service_on=?, hours=?, status=?, notes=? WHERE id=?`,
  x.station_id ?? null, x.name, x.type, x.make ?? null, x.model ?? null, x.serial_no ?? null, x.location ?? null, x.installed_on ?? null, x.cost ?? null, x.vendor ?? null,
  x.vendor_phone ?? null, x.warranty_until ?? null, x.service_every_days ?? null, x.service_every_hours ?? null, x.last_service_on ?? null,
  scheduleChanged ? nextService(x.last_service_on ?? x.installed_on, x.service_every_days) : m.next_service_on, x.hours ?? null, x.status, x.notes ?? null, m.id);
  return withDue(get("SELECT * FROM machines WHERE id=?", m.id));
}));
machines.delete("/machines/:id", requirePerm("settings.manage"), h((req) => {
  const m = get("SELECT id FROM machines WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  if (!m) throw new AppError(404, "Machine not found");
  tx(() => { run("DELETE FROM machine_logs WHERE machine_id=?", m.id); run("DELETE FROM machines WHERE id=?", m.id); });
  return { ok: true };
}));

/**
 * Add to the machine's history:
 *  - service: resets the schedule (and generator hours); cost can go straight into expenses
 *  - fault:   anyone (salesmen too) can report; the machine is marked faulty and managers are alerted
 *  - repair:  closes open faults and the machine works again
 *  - reading: generator hours meter
 *  - note
 */
machines.post("/machines/:id/logs", requirePerm("sales.create"), h(async (req) => {
  const t = tid(req);
  const m = get("SELECT m.*, s.name station_name FROM machines m LEFT JOIN stations s ON s.id=m.station_id WHERE m.id=? AND m.tenant_id=?", Number(req.params.id), t);
  if (!m) throw new AppError(404, "Machine not found");
  const b = parse(z.object({
    kind: z.enum(["service", "fault", "repair", "reading", "note"]), description: z.string().max(400).optional(), day: dateStr.optional(),
    cost: z.number().min(0).optional().nullable(), done_by: z.string().max(80).optional().nullable(), hours: z.number().min(0).optional().nullable(),
    downtime_hours: z.number().min(0).max(2000).optional().nullable(), photo_id: z.number().optional().nullable(), add_expense: z.boolean().default(true),
  }), req.body);
  const manager = ["admin", "manager"].includes(req.user!.role);
  if (!manager && b.kind !== "fault" && b.kind !== "reading") throw new AppError(403, "Salesmen can report a fault or a meter reading; a manager records services and repairs");
  if (b.kind === "fault" && !b.description?.trim()) throw new AppError(400, "Write what is wrong");
  const day = b.day ?? pkDate();
  const desc = b.description?.trim() || { service: "Service", repair: "Repaired", reading: "Hours meter reading", note: "Note", fault: "" }[b.kind];
  const logId = tx(() => {
    let expenseId: number | null = null;
    if (b.cost && manager && b.add_expense && ["service", "repair"].includes(b.kind)) {
      ensureCategories(t);
      expenseId = run(`INSERT INTO expenses (tenant_id,station_id,category,amount,paid_to,method,note,status,created_by,approved_by,expense_date,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
        t, m.station_id ?? null, "Maintenance & repairs", b.cost, b.done_by ?? m.vendor ?? null, "cash", `${m.name}: ${desc}`, "approved", req.user!.name, req.user!.name, day, now()).id;
    }
    const { id } = run(`INSERT INTO machine_logs (tenant_id,machine_id,kind,day,description,cost,done_by,hours,downtime_hours,expense_id,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
      t, m.id, b.kind, day, desc, b.cost ?? null, b.done_by ?? null, b.hours ?? null, b.downtime_hours ?? null, expenseId, req.user!.name, now());
    if (b.hours != null && b.hours >= (m.hours ?? 0)) run("UPDATE machines SET hours=? WHERE id=?", b.hours, m.id);
    if (b.kind === "service") run("UPDATE machines SET last_service_on=?, next_service_on=?, last_service_hours=COALESCE(?, hours, last_service_hours), status=CASE WHEN status='retired' THEN status ELSE 'working' END WHERE id=?",
      day, nextService(day, m.service_every_days), b.hours ?? null, m.id);
    if (b.kind === "fault") run("UPDATE machines SET status='faulty' WHERE id=? AND status<>'retired'", m.id);
    if (b.kind === "repair") {
      run("UPDATE machine_logs SET resolved_at=? WHERE machine_id=? AND kind='fault' AND resolved_at IS NULL", now(), m.id);
      run("UPDATE machines SET status='working' WHERE id=? AND status<>'retired'", m.id);
    }
    return id;
  });
  if (b.photo_id && linkPhotos(t, [b.photo_id], `machine_log:${logId}`)) run("UPDATE machine_logs SET photo_id=? WHERE id=?", b.photo_id, logId);
  if (b.kind === "fault") {
    const title = `🔧 ${m.name} not working${m.station_name ? ` — ${m.station_name}` : ""}`;
    createAlert(t, { station_id: m.station_id, type: "machine_fault", severity: m.type === "dispenser" || m.type === "generator" ? "critical" : "warning", title, body: `${desc} (reported by ${req.user!.name})` });
    await notify(t, staff(t, ["admin", "manager"]).filter((u) => u.id !== req.user!.id), { type: "machine_fault", title, body: `${desc}\nReported by ${req.user!.name}${m.vendor ? `\nMechanic: ${m.vendor}${m.vendor_phone ? ` ${m.vendor_phone}` : ""}` : ""}` });
  }
  return get("SELECT * FROM machine_logs WHERE id=?", logId);
}));

/** Daily: service due / overdue, warranty ending, faults left open for 2+ days. */
export async function machineWatch(t: number) {
  const owner = getSetting(t, "owner_phone") || get("SELECT owner_phone FROM tenants WHERE id=?", t)?.owner_phone;
  const lines: string[] = [];
  for (const m of all("SELECT m.*, s.name station_name FROM machines m LEFT JOIN stations s ON s.id=m.station_id WHERE m.tenant_id=? AND m.status<>'retired'", t)) {
    const d = dueOf(m);
    const where = m.station_name ? ` (${m.station_name})` : "";
    if (d.service === "overdue" || (d.service === "due_soon" && (d.days_to_service === 7 || d.days_to_service === 1 || d.days_to_service === 0 || (d.hours_left != null && d.hours_left <= 25)))) {
      const what = d.hours_left != null && d.hours_left <= 25 ? `${d.hours_left < 0 ? `${-d.hours_left} hours past` : `${d.hours_left} hours left to`} service` : d.days_to_service! < 0 ? `service ${-d.days_to_service!} days late` : d.days_to_service === 0 ? "service due today" : `service in ${d.days_to_service} days`;
      if (createAlert(t, { station_id: m.station_id, type: "machine_service", severity: d.service === "overdue" ? "warning" : "info", title: `🛠️ ${m.name}${where}: ${what}`,
        body: `${m.vendor ? `Mechanic: ${m.vendor}${m.vendor_phone ? ` ${m.vendor_phone}` : ""}. ` : ""}Record the service in Machines when done.`, dedupe_key: `machine-svc-${m.id}-${pkDate().slice(0, 10)}-${d.service}` })) lines.push(`${m.name}${where}: ${what}`);
    }
    if (d.warranty === "ending" && [30, 7, 1].includes(d.warranty_days!))
      if (createAlert(t, { station_id: m.station_id, type: "machine_warranty", severity: "info", title: `📄 ${m.name}${where}: warranty ends in ${d.warranty_days} days`, body: `Get any fault fixed free before ${m.warranty_until}.`, dedupe_key: `machine-war-${m.id}-${d.warranty_days}` }))
        lines.push(`${m.name}${where}: warranty ends ${m.warranty_until}`);
    const old = get("SELECT * FROM machine_logs WHERE machine_id=? AND kind='fault' AND resolved_at IS NULL AND day <= ? ORDER BY day LIMIT 1", m.id, pkDate(Date.now() - 2 * DAY));
    if (old && createAlert(t, { station_id: m.station_id, type: "machine_fault", severity: "warning", title: `⏳ ${m.name}${where} still not repaired`, body: `Reported ${old.day}: ${old.description}`, dedupe_key: `machine-open-${old.id}` }))
      lines.push(`${m.name}${where}: not repaired since ${old.day}`);
  }
  if (lines.length) {
    await notify(t, staff(t, ["admin", "manager"]), { type: "machine_service", whatsapp: false, title: `Machines: ${lines.length} need attention`, body: lines.join("\n") });
    if (owner) await sendDirect(t, { phone: owner, name: "Owner" }, "machine_service", `machines:${pkDate()}`, `🛠️ Machines\n${lines.map((l) => `• ${l}`).join("\n")}`);
  }
  return lines.length;
}
