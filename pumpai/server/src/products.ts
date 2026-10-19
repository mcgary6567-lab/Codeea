/**
 * Fuel products the admin manages from the app (add / rename / recolour / hide / delete).
 *
 * The whole codebase reads `config.PRODUCTS` (code → name) and `config.PRODUCT_META` (short, colour,
 * Urdu). Those two objects are kept in sync with the `products` table by `refreshProducts()`, which
 * rebuilds them IN PLACE so every module that imported them sees the change without a restart.
 *
 * `PRODUCTS` holds EVERY product (active and hidden) so labels, reports and the double-entry tally
 * never lose a historical fuel. New tanks / sales / prices are validated against the ACTIVE list only
 * (`productSchema`), so a hidden product can't be sold again while its past records stay intact.
 */
import { z } from "zod";
import { all, get, run, tx } from "./db.js";
import { PRODUCTS, PRODUCT_META } from "./config.js";
import { AppError, audit } from "./services.js";
import { h, parse, tid, requirePerm } from "./auth.js";
import { Router } from "express";

export const productsRouter = Router();

const DEFAULTS = [
  { code: "PMG", name: "Petrol (Super)", short: "Petrol", colour: "#2a78d6", ur: "پیٹرول" },
  { code: "HOBC", name: "Hi-Octane", short: "Hi-Octane", colour: "#eb6834", ur: "ہائی آکٹین" },
  { code: "HSD", name: "Diesel (HSD)", short: "Diesel", colour: "#1baf7a", ur: "ڈیزل" },
];

const firstTenant = () => get("SELECT id FROM tenants ORDER BY id LIMIT 1")?.id as number | undefined;

/** Give a pump the standard products once (new install). */
export function ensureProducts(tenantId: number) {
  if (get("SELECT id FROM products WHERE tenant_id=? LIMIT 1", tenantId)) return;
  DEFAULTS.forEach((d, i) => run("INSERT INTO products (tenant_id,code,name,short,colour,ur,sort,active) VALUES (?,?,?,?,?,?,?,1)", tenantId, d.code, d.name, d.short, d.colour, d.ur, i));
}

/** Rebuild config.PRODUCTS and config.PRODUCT_META in place from the DB (all products, active + hidden). */
export function refreshProducts() {
  const t = firstTenant();
  if (!t) return; // pre-setup: keep the code defaults
  const rows = all("SELECT * FROM products WHERE tenant_id=? ORDER BY sort, id", t);
  if (!rows.length) return;
  for (const k of Object.keys(PRODUCTS)) delete PRODUCTS[k];
  for (const k of Object.keys(PRODUCT_META)) delete PRODUCT_META[k];
  for (const r of rows) {
    PRODUCTS[r.code] = r.name;
    PRODUCT_META[r.code] = { short: r.short || r.name, colour: r.colour || "#334155", ur: r.ur || "" };
  }
}

/** Codes a NEW tank / sale / price may use (active only). Falls back to the code defaults before setup. */
export function activeCodes(): string[] {
  const t = firstTenant();
  if (!t) return Object.keys(PRODUCTS);
  const rows = all("SELECT code FROM products WHERE tenant_id=? AND active=1 ORDER BY sort, id", t);
  return rows.length ? rows.map((r) => r.code as string) : Object.keys(PRODUCTS);
}
/** Zod schema for a fuel code on a new record — validates against the active list at parse time. */
export const productSchema = () => z.string().min(1).max(12).refine((p) => activeCodes().includes(p), "Unknown fuel product — add it in Settings → Lists");

export const listProducts = (tenantId: number, includeHidden = false) =>
  all(`SELECT * FROM products WHERE tenant_id=? ${includeHidden ? "" : "AND active=1"} ORDER BY sort, id`, tenantId).map((r) => ({ ...r, active: Boolean(r.active) }));

/** Is a product referenced anywhere (so it can be hidden but not deleted)? */
function productUsed(t: number, code: string): boolean {
  return Boolean(
    get("SELECT t.id FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? AND t.product=? LIMIT 1", t, code) ||
    get("SELECT id FROM prices WHERE tenant_id=? AND product=? LIMIT 1", t, code) ||
    get("SELECT s.id FROM sales s JOIN stations st ON st.id=s.station_id WHERE st.tenant_id=? AND s.product=? LIMIT 1", t, code) ||
    get("SELECT id FROM wholesale_txns WHERE tenant_id=? AND product=? LIMIT 1", t, code),
  );
}

/* ================= Admin routes ================= */
productsRouter.get("/products", h((req) => {
  const t = tid(req);
  ensureProducts(t);
  return { products: listProducts(t, req.query.all === "1") };
}));

const codeRe = /^[A-Za-z0-9][A-Za-z0-9_-]{0,11}$/;
const bodyFields = z.object({ name: z.string().trim().min(2).max(40), short: z.string().trim().max(20).optional(), colour: z.string().regex(/^#[0-9a-fA-F]{6}$/).optional(), ur: z.string().max(40).optional() });

productsRouter.post("/products", requirePerm("settings.manage"), h((req) => {
  const b = parse(bodyFields.extend({ code: z.string().trim().regex(codeRe, "Code: letters/numbers, e.g. XTRON or PMG97") }), req.body);
  const t = tid(req);
  ensureProducts(t);
  const code = b.code.toUpperCase();
  const dup = get("SELECT id, active FROM products WHERE tenant_id=? AND code=?", t, code);
  if (dup?.active) throw new AppError(400, "A product with this code already exists");
  if (dup) { run("UPDATE products SET active=1, name=?, short=?, colour=?, ur=? WHERE id=?", b.name, b.short ?? b.name, b.colour ?? "#334155", b.ur ?? "", dup.id); refreshProducts(); return listProducts(t, true).find((p) => p.id === dup.id); }
  const sort = Number(get("SELECT COALESCE(MAX(sort),-1)+1 s FROM products WHERE tenant_id=?", t)!.s);
  const { id } = run("INSERT INTO products (tenant_id,code,name,short,colour,ur,sort,active) VALUES (?,?,?,?,?,?,?,1)", t, code, b.name, b.short ?? b.name, b.colour ?? "#334155", b.ur ?? "", sort);
  audit(t, req.user!, "product_add", `product:${code}`, b);
  refreshProducts();
  return listProducts(t, true).find((p) => p.id === id);
}));

/** Rename / recolour a product, or hide / show it (the code never changes — records point to it). */
productsRouter.patch("/products/:id", requirePerm("settings.manage"), h((req) => {
  const b = parse(bodyFields.partial().extend({ active: z.boolean().optional(), sort: z.number().int().min(0).optional() }), req.body);
  const t = tid(req);
  const p = get("SELECT * FROM products WHERE id=? AND tenant_id=?", Number(req.params.id), t);
  if (!p) throw new AppError(404, "Product not found");
  if (b.active === false && get("SELECT t.id FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? AND t.product=? AND t.active=1 LIMIT 1", t, p.code))
    throw new AppError(400, "A tank still holds this fuel — retire the tank first, then hide the product");
  if (b.active === false && activeCodes().length <= 1) throw new AppError(400, "At least one fuel must stay active");
  tx(() => {
    for (const k of ["name", "short", "colour", "ur"] as const) if (b[k] !== undefined) run(`UPDATE products SET ${k}=? WHERE id=?`, b[k], p.id);
    if (b.sort !== undefined) run("UPDATE products SET sort=? WHERE id=?", b.sort, p.id);
    if (b.active !== undefined) run("UPDATE products SET active=? WHERE id=?", b.active ? 1 : 0, p.id);
  });
  audit(t, req.user!, "product_edit", `product:${p.code}`, b);
  refreshProducts();
  return listProducts(t, true).find((x) => x.id === p.id);
}));

/** Delete a product that was never used anywhere; a used one is hidden instead. */
productsRouter.delete("/products/:id", requirePerm("settings.manage"), h((req) => {
  const t = tid(req);
  const p = get("SELECT * FROM products WHERE id=? AND tenant_id=?", Number(req.params.id), t);
  if (!p) throw new AppError(404, "Product not found");
  if (productUsed(t, p.code)) throw new AppError(400, "This fuel has tanks, prices or sales — hide it instead (history stays)");
  run("DELETE FROM products WHERE id=?", p.id);
  audit(t, req.user!, "product_delete", `product:${p.code}`, { name: p.name });
  refreshProducts();
  return { ok: true };
}));
