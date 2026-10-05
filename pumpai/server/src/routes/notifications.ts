import { Router } from "express";
import { z } from "zod";
import { all, get, run, tx, now, METER } from "../db.js";
import { h, parse, tid } from "../auth.js";
import { AppError } from "../services.js";
import { settleShift } from "../shifts.js";

/** Every signed-in user reads and acknowledges their own notifications. */
export const notifications = Router();

const shape = (n: any) => ({ ...n, data: n.data ? JSON.parse(n.data) : null, ack_required: Boolean(n.ack_required) });

notifications.get("/notifications", h((req) => {
  const u = req.user!;
  const items = all("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 50", u.id).map(shape);
  const pending = all("SELECT * FROM notifications WHERE user_id=? AND ack_required=1 AND acked_at IS NULL ORDER BY id", u.id).map(shape);
  // a salesman on shift must enter meter readings when confirming a price change
  const open = u.role === "salesman" ? get("SELECT id FROM shifts WHERE station_id=? AND attendant=? AND status='open' ORDER BY id DESC LIMIT 1", u.station_id, u.name) : null;
  return {
    items, unread: items.filter((n) => !n.read_at).length, pending_ack: pending,
    open_shift: open ? {
      id: open.id,
      readings: all(`SELECT r.nozzle_id, ${METER} label, n.meter_no, t.product, COALESCE(r.checkpoint, r.opening) last_reading FROM meter_readings r JOIN nozzles n ON n.id=r.nozzle_id
        JOIN tanks t ON t.id=n.tank_id WHERE r.shift_id=? ORDER BY n.meter_no`, open.id),
    } : null,
  };
}));

notifications.post("/notifications/read-all", h((req) => {
  run("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL", now(), req.user!.id);
  return { ok: true };
}));

notifications.post("/notifications/:id/read", h((req) => {
  run("UPDATE notifications SET read_at=COALESCE(read_at, ?) WHERE id=? AND user_id=?", now(), Number(req.params.id), req.user!.id);
  return { ok: true };
}));

/**
 * Confirm a notification. For a price change the salesman confirms the dispenser was updated;
 * if they are on shift, the meter readings at that moment settle all litres pumped so far at the OLD price.
 */
notifications.post("/notifications/:id/ack", h((req) => {
  const n = get("SELECT * FROM notifications WHERE id=? AND user_id=?", Number(req.params.id), req.user!.id);
  if (!n) throw new AppError(404, "Notification not found");
  if (n.acked_at) return { ok: true, already: true };
  const b = parse(z.object({ readings: z.record(z.string(), z.number().min(0)).optional() }), req.body);
  const data = n.data ? JSON.parse(n.data) : {};
  let settled: ReturnType<typeof settleShift> | null = null;
  return tx(() => {
    if (n.type === "price_change") {
      const u = req.user!;
      const shift = u.role === "salesman" ? get("SELECT * FROM shifts WHERE station_id=? AND attendant=? AND status='open' ORDER BY id DESC LIMIT 1", u.station_id, u.name) : null;
      if (shift) {
        if (!b.readings) throw new AppError(400, "You are on shift: enter the current meter reading of every nozzle");
        // only for litres pumped BEFORE this change; old prices come from the notification
        if (Date.parse(shift.opened_at) < Date.parse(data.batch))
          settled = settleShift(tid(req), shift, b.readings, (p) => data.old?.[p]);
      }
    }
    run("UPDATE notifications SET acked_at=?, read_at=COALESCE(read_at, ?), data=? WHERE id=?", now(), now(), JSON.stringify({ ...data, settled }), n.id);
    return { ok: true, settled };
  });
}));
