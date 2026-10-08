/**
 * Photo and voice entry: staff photograph a meter, tanker invoice or receipt and the numbers are
 * filled in for them; the photo is kept as proof. A spoken sentence becomes a POS sale.
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, now } from "../db.js";
import { h, parse, tid, requirePerm, requireAny } from "../auth.js";
import { AppError, audit } from "../services.js";
import { aiEnabled } from "../config.js";
import { readPhoto, parseSale, type PhotoKind } from "../ai/vision.js";
import { expenseFromText } from "../ai/parseSale.js";

export const capture = Router();

const MAX_BYTES = 4 * 1024 * 1024;

capture.post("/ai/read-photo", requireAny("sales.create", "shifts.manage", "stock.manage", "expenses.create", "shifts.expenses", "wholesale.manage", "khata.manage", "staff.manage", "suppliers.manage", "cash.receive", "cash.pay", "cheques.manage", "cash.book", "bank.manage"), h(async (req) => {
  const b = parse(z.object({
    kind: z.enum(["meter", "invoice", "receipt", "bill", "slip", "selfie", "proof"]),
    image: z.string().regex(/^data:image\/(jpeg|png|webp);base64,/, "Send a JPEG, PNG or WebP photo"),
    hint: z.string().max(300).optional(),
  }), req.body);
  const [, mime, data] = b.image.match(/^data:(image\/[a-z]+);base64,(.*)$/s)!;
  const bytes = Buffer.from(data, "base64");
  if (bytes.length > MAX_BYTES) throw new AppError(400, "Photo is too large");
  // the bytes must really be a picture of the type claimed (not a web page dressed up as one)
  const real = bytes.subarray(0, 3).equals(Buffer.from([0xff, 0xd8, 0xff])) ? "image/jpeg"
    : bytes.subarray(0, 4).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47])) ? "image/png"
    : bytes.subarray(0, 4).toString("latin1") === "RIFF" && bytes.subarray(8, 12).toString("latin1") === "WEBP" ? "image/webp" : null;
  if (!real) throw new AppError(400, "Send a JPEG, PNG or WebP photo");
  let extra = b.hint ?? "";
  if (b.kind === "receipt") extra += `\nCategories: ${all("SELECT name FROM expense_categories WHERE tenant_id=?", tid(req)).map((c) => c.name).join(", ")}`;
  // selfies (attendance) and proof photos (checklist, licence) are only stored, not read
  const result = ["selfie", "proof"].includes(b.kind) ? null : await readPhoto(b.kind as PhotoKind, data, mime, extra);
  const { id } = run("INSERT INTO photos (tenant_id,kind,mime,data,ai_result,created_by,created_at) VALUES (?,?,?,?,?,?,?)",
    tid(req), b.kind, real, bytes, result ? JSON.stringify(result) : null, req.user!.id, now());
  return {
    photo_id: id, ai: Boolean(result), result,
    // a khata slip photo is first of all a record: without the AI key it is simply saved, no warning
    message: result || ["selfie", "proof"].includes(b.kind) || (b.kind === "slip" && !aiEnabled()) ? null
      : aiEnabled() ? (b.kind === "slip" ? "Photo saved. Could not read the slip — please type the slip number." : "Could not read the photo clearly. Please type the numbers.")
      : "Photo saved as proof. Automatic reading needs the AI key — please type the numbers.",
  };
}));

capture.get("/photos/:id", h((req, res) => {
  const p = get("SELECT mime, data, created_by FROM photos WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  if (!p) throw new AppError(404, "Photo not found");
  // a salesman sees only the photos they took (cheques, salary sheets and selfies of others stay private)
  if (req.user!.role === "salesman" && p.created_by !== req.user!.id) throw new AppError(404, "Photo not found");
  res.setHeader("content-type", /^image\/(jpeg|png|webp)$/.test(p.mime) ? p.mime : "application/octet-stream");
  res.setHeader("content-disposition", "inline");
  res.setHeader("cache-control", "private, max-age=86400");
  res.end(Buffer.from(p.data as Uint8Array));
  return undefined;
}));

/** Only the CEO/admin may delete an uploaded photo; everyone else can only view it. */
capture.delete("/photos/:id", requirePerm("photos.delete"), h((req) => {
  const id = Number(req.params.id);
  const p = get("SELECT id, kind, ref FROM photos WHERE id=? AND tenant_id=?", id, tid(req));
  if (!p) throw new AppError(404, "Photo not found");
  run("DELETE FROM photos WHERE id=? AND tenant_id=?", id, tid(req));
  audit(tid(req), req.user!, "photo_delete", `photo:${id}`, { kind: p.kind, ref: p.ref });
  return { ok: true };
}));

capture.post("/ai/parse-sale", requirePerm("sales.create"), h(async (req) => {
  const b = parse(z.object({ text: z.string().min(2).max(400) }), req.body);
  const accounts = all("SELECT id, name FROM customers WHERE tenant_id=? AND credit_limit > 0", tid(req)) as { id: number; name: string }[];
  // "chai ka kharcha 300" / "bijli ka bill 18000" is an expense from the shift's cash, not a sale
  // (the accounts are passed so "City Bakers generator diesel 2500" stays a khata sale)
  const exp = expenseFromText(b.text, all("SELECT name FROM expense_categories WHERE tenant_id=? ORDER BY name", tid(req)).map((c) => c.name as string), accounts);
  if (exp?.amount) return exp;
  return parseSale(b.text, accounts);
}));

/** Photo proof sent with an entry: up to 10 photos (cheque, receipt, slip, invoice, signed chalan…). */
export const proofPhotos = z.array(z.number().int().positive()).max(10).optional();
/** The photo ids attached to a ledger row, as a comma list, for list / statement queries ("proof_ids"). */
export const proofCol = (refSql: string) => `(SELECT GROUP_CONCAT(p.id) FROM photos p WHERE p.ref = ${refSql}) proof_ids`;

/** For entries where a photo is compulsory (cheques, salary): at least one new, unused photo of this pump. */
export function requireProof(tenantId: number, ids: number[] | undefined | null, what: string) {
  const ok = (ids ?? []).filter((id) => get("SELECT id FROM photos WHERE id=? AND tenant_id=? AND ref IS NULL", id, tenantId));
  if (!ok.length) throw new AppError(400, `Photo of the ${what} is required · ${what === "cheque" ? "چیک" : "رسید"} کی تصویر لازمی ہے`);
}
export const isCheque = (method?: string | null) => /cheque|check/i.test(method ?? "");

/** Attach uploaded photos to what they prove (a shift's meters, a delivery, an expense). */
export function linkPhotos(tenantId: number, ids: number[] | undefined | null, ref: string): number {
  let n = 0;
  for (const id of ids ?? []) n += Number(run("UPDATE photos SET ref=? WHERE id=? AND tenant_id=? AND ref IS NULL", ref, id, tenantId).changes ?? 0);
  return n;
}
export const photosFor = (tenantId: number, refs: string[]) =>
  refs.length ? all(`SELECT id, kind, ref, created_at FROM photos WHERE tenant_id=? AND ref IN (${refs.map(() => "?").join(",")}) ORDER BY id`, tenantId, ...refs) : [];
