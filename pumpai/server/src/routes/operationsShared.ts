/** Helpers shared by the operations sub-routers (stations, prices, sales, shifts, stock).
 * Kept in one place so the split route files all see the same ownership checks and query parsers. */
import { z } from "zod";
import { get } from "../db.js";
import { AppError } from "../services.js";
import { productSchema } from "../products.js";

/** The admin-managed fuel-product enum (PMG / HSD / HOBC …). */
export const product = productSchema();

/** A station of this tenant, or 404. */
export function ownStation(tenantId: number, stationId: number) {
  const s = get("SELECT * FROM stations WHERE id=? AND tenant_id=?", stationId, tenantId);
  if (!s) throw new AppError(404, "Station not found");
  return s;
}
/** A tank of this tenant (via its station), or 404. */
export function ownTank(tenantId: number, tankId: number) {
  const t = get("SELECT t.* FROM tanks t JOIN stations s ON s.id=t.station_id WHERE t.id=? AND s.tenant_id=?", tankId, tenantId);
  if (!t) throw new AppError(404, "Tank not found");
  return t;
}

/** A PK calendar day (YYYY-MM-DD) in a list filter; "" means not set. */
export const pkDay = z.string().regex(/^\d{4}-\d{2}-\d{2}$/).or(z.literal("")).optional();
export const listText = z.string().trim().max(80).optional();
export const optId = z.preprocess((v) => (v === "" || v == null ? undefined : v), z.coerce.number().int().positive().optional());
