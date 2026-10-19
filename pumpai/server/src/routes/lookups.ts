/**
 * Lists the admin manages from the app instead of the code: customer types, machine types, shop categories,
 * utility bill kinds, booking services, training topics, job titles, complaint categories.
 *
 * Each pump starts with the standard list (the same values the code used before), then adds, renames,
 * switches off or deletes entries from Settings → Lists. Everything else reads these lists, so a new
 * entry shows up at once in every dropdown, filter and report.
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, tx } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { AppError, audit } from "../services.js";

export const lookupsRouter = Router();

export type Lookup = { id: number; kind: string; key: string; label: string; extra: Record<string, any>; sort: number; active: boolean };

/** The kinds, what the owner sees them called, the fields each entry may carry, and the standard entries. */
export const KINDS: Record<string, { label: string; hint: string; fields: { key: string; label: string; type: "text" | "number" | "boolean" }[]; defaults: { key: string; label: string; extra?: Record<string, any> }[]; used_in?: [table: string, column: string] }> = {
  customer_type: {
    label: "Customer types", hint: "Khata / customer kinds. Tick 'institution' for police, schools, govt offices: never auto-held, no late fee, listed first at the POS.",
    fields: [{ key: "icon", label: "Icon (emoji)", type: "text" }, { key: "institution", label: "Institution", type: "boolean" }],
    used_in: ["customers", "type"],
    defaults: [
      { key: "retail", label: "Retail customer", extra: { icon: "🚗" } }, { key: "fleet", label: "Fleet / transport", extra: { icon: "🚚" } },
      { key: "farmer", label: "Farmer", extra: { icon: "🚜" } }, { key: "business", label: "Business", extra: { icon: "🏢" } },
      { key: "police", label: "Police station", extra: { icon: "🚓", institution: true } }, { key: "school", label: "School / college", extra: { icon: "🏫", institution: true } },
      { key: "government", label: "Government office", extra: { icon: "🏛️", institution: true } }, { key: "hospital", label: "Hospital / health", extra: { icon: "🚑", institution: true } },
    ],
  },
  machine_type: {
    label: "Machine types", hint: "Kinds of equipment in the machines register.", fields: [{ key: "icon", label: "Icon (emoji)", type: "text" }], used_in: ["machines", "type"],
    defaults: [
      { key: "dispenser", label: "Dispenser", extra: { icon: "⛽" } }, { key: "generator", label: "Generator", extra: { icon: "⚡" } }, { key: "compressor", label: "Air compressor", extra: { icon: "🛞" } },
      { key: "submersible_pump", label: "Submersible pump", extra: { icon: "🛢️" } }, { key: "fan", label: "Fan", extra: { icon: "🌀" } }, { key: "light", label: "Lights", extra: { icon: "💡" } },
      { key: "ups", label: "UPS", extra: { icon: "🔋" } }, { key: "inverter", label: "Inverter", extra: { icon: "🔋" } }, { key: "air_conditioner", label: "AC", extra: { icon: "❄️" } },
      { key: "cctv", label: "CCTV", extra: { icon: "📹" } }, { key: "water_pump", label: "Water pump", extra: { icon: "💧" } }, { key: "car_wash", label: "Car wash", extra: { icon: "🚿" } }, { key: "other", label: "Other", extra: { icon: "🔧" } },
    ],
  },
  shop_category: {
    label: "Shop categories", hint: "Shop item categories — also used for GST exemption and salesman commission rates.", fields: [], used_in: ["shop_items", "category"],
    defaults: [
      { key: "lubricant", label: "Engine oil / lubricants" }, { key: "filter", label: "Filters" }, { key: "coolant", label: "Coolant" }, { key: "tyre", label: "Tyres" },
      { key: "battery", label: "Batteries" }, { key: "tuck", label: "Tuck shop" }, { key: "service", label: "Services" }, { key: "other", label: "Other" },
    ],
  },
  utility_kind: {
    label: "Utility bills", hint: "Kinds of monthly bills. 'Expense category' is where the bill is booked.", fields: [{ key: "icon", label: "Icon (emoji)", type: "text" }, { key: "category", label: "Expense category", type: "text" }], used_in: ["utility_bills", "kind"],
    defaults: [
      { key: "electricity", label: "Bijli (electricity)", extra: { icon: "⚡", category: "Electricity (bijli)" } }, { key: "gas", label: "Gas", extra: { icon: "🔥", category: "Other" } },
      { key: "water", label: "Water", extra: { icon: "💧", category: "Other" } }, { key: "phone", label: "Phone / internet", extra: { icon: "☎️", category: "Office & stationery" } },
    ],
  },
  booking_service: {
    label: "Booking services", hint: "What customers can book (on WhatsApp too). 'Keywords' are words a customer may type, comma-separated.", fields: [{ key: "icon", label: "Icon (emoji)", type: "text" }, { key: "keywords", label: "Keywords", type: "text" }], used_in: ["bookings", "service"],
    defaults: [
      { key: "car_wash", label: "Car wash", extra: { icon: "🚿", keywords: "wash, dhula, dhulai, دھلائی, واش" } }, { key: "oil_change", label: "Oil change", extra: { icon: "🛢️", keywords: "oil change, oil, mobil, آئل" } },
      { key: "tyre", label: "Tyre / puncture", extra: { icon: "🛞", keywords: "tyre, tire, puncture, پنکچر, ٹائر" } }, { key: "service", label: "Service / tuning", extra: { icon: "🔧", keywords: "service, tuning, سروس" } },
    ],
  },
  training_topic: {
    label: "Training topics", hint: "Staff training with how long it stays valid; 'required' topics show as missing for salesmen.", fields: [{ key: "months", label: "Valid for (months)", type: "number" }, { key: "required", label: "Required", type: "boolean" }], used_in: ["trainings", "topic"],
    defaults: [
      { key: "Fire safety & extinguisher use", label: "Fire safety & extinguisher use", extra: { months: 12, required: true } },
      { key: "Emergency shutdown & spill handling", label: "Emergency shutdown & spill handling", extra: { months: 12, required: true } },
      { key: "POS & cash handling", label: "POS & cash handling", extra: { months: 12, required: true } },
      { key: "Fuel quality: density & water check", label: "Fuel quality: density & water check", extra: { months: 12, required: false } },
      { key: "Customer service", label: "Customer service", extra: { months: 24, required: false } }, { key: "First aid", label: "First aid", extra: { months: 24, required: false } },
    ],
  },
  job_title: {
    label: "Job titles", hint: "Designations for staff who do not log in (guard, cleaner, electrician…).", fields: [], used_in: ["users", "job_title"],
    defaults: ["Chowkidar (day)", "Chowkidar (night)", "Cleaner / sweeper", "Gardener (mali)", "Electrician", "Helper", "Pump operator", "Manager", "Cashier", "Accountant", "Driver", "Cook"].map((k) => ({ key: k, label: k })),
  },
  payment_method: {
    label: "Payment methods", hint: "The money ways shown at the POS and shop (besides khata, wallet, coupon, points). 'Goes to' — Cash stays in the drawer; Bank/digital settles into a bank account (set which in Cash & bank → POS machines).",
    fields: [{ key: "icon", label: "Icon (emoji)", type: "text" }, { key: "digital", label: "Goes to bank (not cash)", type: "boolean" }], used_in: ["sales", "payment_method"],
    defaults: [
      { key: "cash", label: "Cash", extra: { icon: "💵", digital: false } }, { key: "card", label: "Card machine", extra: { icon: "💳", digital: true } },
      { key: "jazzcash", label: "JazzCash", extra: { icon: "📱", digital: true } }, { key: "easypaisa", label: "Easypaisa", extra: { icon: "📱", digital: true } },
      { key: "raast", label: "Raast", extra: { icon: "⚡", digital: true } },
    ],
  },
  complaint_category: {
    label: "Complaint categories", hint: "How complaints are classified (the WhatsApp assistant picks one of these).", fields: [], used_in: ["complaints", "category"],
    defaults: [
      { key: "short_measure", label: "Short measure" }, { key: "fuel_quality", label: "Fuel quality" }, { key: "staff_behaviour", label: "Staff behaviour" },
      { key: "payment", label: "Payment / card" }, { key: "billing", label: "Billing" }, { key: "facility", label: "Facility (toilet, air, lights)" }, { key: "other", label: "Other" },
    ],
  },
};

const seeded = new Set<number>();
/** Give a pump the standard lists once (new pump, or a kind added in an update). */
export function ensureLookups(t: number) {
  if (seeded.has(t)) return;
  tx(() => {
    for (const [kind, k] of Object.entries(KINDS)) {
      if (get("SELECT id FROM lookups WHERE tenant_id=? AND kind=? LIMIT 1", t, kind)) continue;
      k.defaults.forEach((d, i) => run("INSERT INTO lookups (tenant_id,kind,key,label,extra,sort,active) VALUES (?,?,?,?,?,?,1)", t, kind, d.key, d.label, JSON.stringify(d.extra ?? {}), i));
    }
  });
  seeded.add(t);
}
const row = (r: any): Lookup => { let extra = {}; try { extra = JSON.parse(r.extra || "{}"); } catch { /* none */ } return { id: r.id, kind: r.kind, key: r.key, label: r.label, extra, sort: r.sort, active: Boolean(r.active) }; };

/** A pump's list of one kind (active entries unless `includeOff`), in the admin's order. */
export function lookups(t: number, kind: string, includeOff = false): Lookup[] {
  ensureLookups(t);
  return all(`SELECT * FROM lookups WHERE tenant_id=? AND kind=? ${includeOff ? "" : "AND active=1"} ORDER BY sort, id`, t, kind).map(row);
}
export const lookupKeys = (t: number, kind: string) => lookups(t, kind).map((x) => x.key);
export function lookupLabel(t: number, kind: string, key: string | null | undefined) {
  if (!key) return "";
  return get("SELECT label FROM lookups WHERE tenant_id=? AND kind=? AND key=?", t, kind, key)?.label ?? key;
}
export function lookupExtra(t: number, kind: string, key: string): Record<string, any> {
  const r = get("SELECT extra FROM lookups WHERE tenant_id=? AND kind=? AND key=?", t, kind, key);
  try { return JSON.parse(r?.extra || "{}"); } catch { return {}; }
}
/** Refuse a value that is not an active entry of the list (so a switched-off type can't be used for new records). */
export function assertLookup(t: number, kind: string, key: string | null | undefined, what = KINDS[kind]?.label ?? kind) {
  if (key == null) return;
  ensureLookups(t);
  if (!get("SELECT id FROM lookups WHERE tenant_id=? AND kind=? AND key=? AND active=1", t, kind, key)) throw new AppError(400, `Unknown ${what.toLowerCase().replace(/s$/, "")}: ${key} — add it in Settings → Lists`);
}
/** Customer types that are institutions (police, school, govt…): never auto-held, no late fee. */
export const institutionTypes = (t: number) => lookups(t, "customer_type").filter((x) => x.extra.institution).map((x) => x.key);
/** Training topics with validity, in the shape the training matrix uses. */
export const trainingTopics = (t: number) => lookups(t, "training_topic").map((x) => ({ topic: x.key, months: Number(x.extra.months ?? 12), required: Boolean(x.extra.required) }));
/** Non-khata money methods the admin set up (cash + card/digital brands). */
export const paymentMethods = (t: number) => lookups(t, "payment_method");
/** Keys of money methods that settle into a bank account (card, JazzCash, SadaPay…), not the cash drawer.
 *  This one list drives the POS, the bank module AND the ledger, so adding a brand never breaks the tally. */
export const digitalMethods = (t: number): string[] => lookups(t, "payment_method").filter((m) => m.extra.digital).map((m) => m.key);
/** Payment-method keys that are NOT a credit/prepaid flow — the plain money ones (cash + digital). */
export const moneyMethods = (t: number): string[] => lookups(t, "payment_method").map((m) => m.key);
/** The fixed non-money POS methods handled by their own flows (never freely added). */
export const SPECIAL_METHODS = ["khata", "loyalty", "coupon", "wallet"] as const;

/* ================= Admin routes ================= */
lookupsRouter.get("/lookups", h((req) => {
  const t = tid(req);
  ensureLookups(t);
  const allOf = req.query.all === "1";
  return {
    kinds: Object.fromEntries(Object.entries(KINDS).map(([k, v]) => [k, { label: v.label, hint: v.hint, fields: v.fields }])),
    lists: Object.fromEntries(Object.keys(KINDS).map((k) => [k, lookups(t, k, allOf)])),
  };
}));
const keyRe = /^[a-z0-9_][a-z0-9_ &/:().'+-]{0,60}$/i;
lookupsRouter.post("/lookups", requirePerm("settings.manage"), h((req) => {
  const b = parse(z.object({ kind: z.enum(Object.keys(KINDS) as [string, ...string[]]), key: z.string().trim().regex(keyRe).optional(), label: z.string().trim().min(1).max(80), extra: z.record(z.string(), z.any()).optional() }), req.body);
  const t = tid(req);
  ensureLookups(t);
  // the key is what records store; default to a slug of the label (training topics and job titles use the label itself)
  const key = b.key ?? (["training_topic", "job_title"].includes(b.kind) ? b.label : b.label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, ""));
  const dup = get("SELECT id, active FROM lookups WHERE tenant_id=? AND kind=? AND key=?", t, b.kind, key);
  if (dup?.active) throw new AppError(400, "That entry already exists");
  if (dup) { run("UPDATE lookups SET active=1, label=?, extra=? WHERE id=?", b.label, JSON.stringify(b.extra ?? {}), dup.id); return row(get("SELECT * FROM lookups WHERE id=?", dup.id)); }
  const sort = Number(get("SELECT COALESCE(MAX(sort),-1)+1 s FROM lookups WHERE tenant_id=? AND kind=?", t, b.kind)!.s);
  const { id } = run("INSERT INTO lookups (tenant_id,kind,key,label,extra,sort,active) VALUES (?,?,?,?,?,?,1)", t, b.kind, key, b.label, JSON.stringify(b.extra ?? {}), sort);
  audit(t, req.user!, "list_add", `lookup:${id}`, { kind: b.kind, key, label: b.label });
  return row(get("SELECT * FROM lookups WHERE id=?", id));
}));
lookupsRouter.patch("/lookups/:id", requirePerm("settings.manage"), h((req) => {
  const b = parse(z.object({ label: z.string().trim().min(1).max(80).optional(), extra: z.record(z.string(), z.any()).optional(), sort: z.number().int().min(0).optional(), active: z.boolean().optional() }), req.body);
  const t = tid(req);
  const r = get("SELECT * FROM lookups WHERE id=? AND tenant_id=?", Number(req.params.id), t);
  if (!r) throw new AppError(404, "Entry not found");
  tx(() => {
    if (b.label !== undefined) run("UPDATE lookups SET label=? WHERE id=?", b.label, r.id);
    if (b.extra !== undefined) run("UPDATE lookups SET extra=? WHERE id=?", JSON.stringify({ ...JSON.parse(r.extra || "{}"), ...b.extra }), r.id);
    if (b.sort !== undefined) run("UPDATE lookups SET sort=? WHERE id=?", b.sort, r.id);
    if (b.active !== undefined) run("UPDATE lookups SET active=? WHERE id=?", b.active ? 1 : 0, r.id);
  });
  audit(t, req.user!, "list_edit", `lookup:${r.id}`, b);
  return row(get("SELECT * FROM lookups WHERE id=?", r.id));
}));
/** Move an entry up or down in its list. */
lookupsRouter.post("/lookups/:id/move", requirePerm("settings.manage"), h((req) => {
  const b = parse(z.object({ dir: z.enum(["up", "down"]) }), req.body);
  const t = tid(req);
  const r = get("SELECT * FROM lookups WHERE id=? AND tenant_id=?", Number(req.params.id), t);
  if (!r) throw new AppError(404, "Entry not found");
  const list = all("SELECT id FROM lookups WHERE tenant_id=? AND kind=? ORDER BY sort, id", t, r.kind).map((x) => x.id as number);
  const i = list.indexOf(r.id), j = b.dir === "up" ? i - 1 : i + 1;
  if (j >= 0 && j < list.length) { [list[i], list[j]] = [list[j], list[i]]; tx(() => list.forEach((id, k) => run("UPDATE lookups SET sort=? WHERE id=?", k, id))); }
  return { ok: true };
}));
/** Delete an entry nothing uses; one in use is switched off instead. */
lookupsRouter.delete("/lookups/:id", requirePerm("settings.manage"), h((req) => {
  const t = tid(req);
  const r = get("SELECT * FROM lookups WHERE id=? AND tenant_id=?", Number(req.params.id), t);
  if (!r) throw new AppError(404, "Entry not found");
  const used = KINDS[r.kind]?.used_in;
  if (used && get(`SELECT 1 FROM ${used[0]} WHERE tenant_id=? AND ${used[1]}=? LIMIT 1`, t, r.key)) throw new AppError(400, "This entry is in use — switch it off instead (records keep it)");
  if (get("SELECT COUNT(*) n FROM lookups WHERE tenant_id=? AND kind=? AND active=1", t, r.kind)!.n <= 1) throw new AppError(400, "A list needs at least one entry");
  run("DELETE FROM lookups WHERE id=?", r.id);
  audit(t, req.user!, "list_delete", `lookup:${r.id}`, { kind: r.kind, key: r.key });
  return { ok: true };
}));
