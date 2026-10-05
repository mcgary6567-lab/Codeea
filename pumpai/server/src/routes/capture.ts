/**
 * Photo and voice entry: staff photograph a meter, tanker invoice or receipt and the numbers are
 * filled in for them; the photo is kept as proof. A spoken sentence becomes a POS sale.
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, now } from "../db.js";
import { h, parse, tid, requirePerm, requireAny } from "../auth.js";
import { AppError } from "../services.js";
import { aiEnabled } from "../config.js";
import { readPhoto, parseSale, type PhotoKind } from "../ai/vision.js";

export const capture = Router();

const MAX_BYTES = 4 * 1024 * 1024;

capture.post("/ai/read-photo", requireAny("sales.create", "shifts.manage", "stock.manage", "expenses.create", "shifts.expenses", "wholesale.manage"), h(async (req) => {
  const b = parse(z.object({
    kind: z.enum(["meter", "invoice", "receipt", "bill", "selfie", "proof"]),
    image: z.string().regex(/^data:image\/(jpeg|png|webp);base64,/, "Send a JPEG, PNG or WebP photo"),
    hint: z.string().max(300).optional(),
  }), req.body);
  const [, mime, data] = b.image.match(/^data:(image\/[a-z]+);base64,(.*)$/s)!;
  const bytes = Buffer.from(data, "base64");
  if (bytes.length > MAX_BYTES) throw new AppError(400, "Photo is too large");
  let extra = b.hint ?? "";
  if (b.kind === "receipt") extra += `\nCategories: ${all("SELECT name FROM expense_categories WHERE tenant_id=?", tid(req)).map((c) => c.name).join(", ")}`;
  // selfies (attendance) and proof photos (checklist, licence) are only stored, not read
  const result = ["selfie", "proof"].includes(b.kind) ? null : await readPhoto(b.kind as PhotoKind, data, mime, extra);
  const { id } = run("INSERT INTO photos (tenant_id,kind,mime,data,ai_result,created_by,created_at) VALUES (?,?,?,?,?,?,?)",
    tid(req), b.kind, mime, bytes, result ? JSON.stringify(result) : null, req.user!.id, now());
  return {
    photo_id: id, ai: Boolean(result), result,
    message: result || ["selfie", "proof"].includes(b.kind) ? null : aiEnabled() ? "Could not read the photo clearly. Please type the numbers." : "Photo saved as proof. Automatic reading needs the AI key — please type the numbers.",
  };
}));

capture.get("/photos/:id", h((req, res) => {
  const p = get("SELECT mime, data FROM photos WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  if (!p) throw new AppError(404, "Photo not found");
  res.setHeader("content-type", p.mime);
  res.setHeader("cache-control", "private, max-age=86400");
  res.end(Buffer.from(p.data as Uint8Array));
  return undefined;
}));

capture.post("/ai/parse-sale", requirePerm("sales.create"), h(async (req) => {
  const b = parse(z.object({ text: z.string().min(2).max(400) }), req.body);
  const accounts = all("SELECT id, name FROM customers WHERE tenant_id=? AND credit_limit > 0", tid(req)) as { id: number; name: string }[];
  return parseSale(b.text, accounts);
}));

/** Attach uploaded photos to what they prove (a shift's meters, a delivery, an expense). */
export function linkPhotos(tenantId: number, ids: number[] | undefined | null, ref: string): number {
  let n = 0;
  for (const id of ids ?? []) n += Number(run("UPDATE photos SET ref=? WHERE id=? AND tenant_id=? AND ref IS NULL", ref, id, tenantId).changes ?? 0);
  return n;
}
export const photosFor = (tenantId: number, refs: string[]) =>
  refs.length ? all(`SELECT id, kind, ref, created_at FROM photos WHERE tenant_id=? AND ref IN (${refs.map(() => "?").join(",")}) ORDER BY id`, tenantId, ...refs) : [];
