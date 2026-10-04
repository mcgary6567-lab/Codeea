/** Staff notifications: stored in-app and, when the user has a phone number, sent on WhatsApp too. */
import { all, get, run, now, type Row } from "./db.js";
import { sendToPhone } from "./whatsapp/cloud.js";

export interface NotifyInput {
  type: string;
  title: string;
  body?: string;
  data?: Record<string, unknown>;
  ack_required?: boolean;
  whatsapp?: boolean;
}

export async function notify(tenantId: number, users: Row[], n: NotifyInput) {
  for (const u of users) {
    run("INSERT INTO notifications (tenant_id,user_id,type,title,body,data,ack_required,created_at) VALUES (?,?,?,?,?,?,?,?)",
      tenantId, u.id, n.type, n.title, n.body ?? null, n.data ? JSON.stringify(n.data) : null, n.ack_required ? 1 : 0, now());
    if (n.whatsapp !== false && u.phone) await sendToPhone(u.phone, `${n.title}\n${n.body ?? ""}`.trim());
  }
}

export const staff = (tenantId: number, roles: string[], excludeUserId?: number) =>
  all(`SELECT id, name, role, phone, station_id FROM users WHERE tenant_id=? AND active=1 AND role IN (${roles.map(() => "?").join(",")}) ${excludeUserId ? "AND id<>?" : ""}`,
    tenantId, ...roles, ...(excludeUserId ? [excludeUserId] : []));

/** The salesman user account behind a shift (shifts store the attendant's name). */
export const shiftUser = (tenantId: number, shift: Row) =>
  get("SELECT id, name, role, phone, station_id FROM users WHERE tenant_id=? AND active=1 AND role='salesman' AND name=? AND station_id=?", tenantId, shift.attendant, shift.station_id);

/**
 * Tell other staff that something new was added (khata account, supplier, station...).
 * The person who added it is not notified. In-app only (no WhatsApp) to avoid noise.
 */
export async function announce(tenantId: number, byUserId: number, roles: string[], n: { type: string; title: string; body?: string; data?: Record<string, unknown>; stationId?: number | null }) {
  const users = staff(tenantId, roles, byUserId).filter((u) => !n.stationId || u.role !== "salesman" || !u.station_id || u.station_id === n.stationId);
  await notify(tenantId, users, { type: n.type, title: n.title, body: n.body, data: n.data, whatsapp: false });
  return users.length;
}
