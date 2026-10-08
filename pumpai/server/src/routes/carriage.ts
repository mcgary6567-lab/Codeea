/**
 * Carriage / kiraya (bypass on our depot ID) — run with THEKEDARS, not wholesale clients.
 *
 * A bypass supply: the depot loads on OUR id and invoices us, but the fuel money is the thekedar's — we only earn the
 * fixed kiraya (carriage / commission) written on the depot invoice. No stock, no fuel purchase/sale on our books: just
 * the kiraya billed to the thekedar (receivable, booked as income) and the fuel money handled separately (the thekedar
 * paid the depot direct — record only — or sent it to us to forward on, a net-zero pass-through through our bank).
 *
 * This is its own module with its own party and its own ledger account ("Carriage receivable"), kept apart from the
 * wholesale khata so the two never mix.
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, tx, now, pkDate } from "../db.js";
import { h, parse, tid, requirePerm, requireRole } from "../auth.js";
import { AppError, createAlert, round2, pkr } from "../services.js";
import { PRODUCTS } from "../config.js";
import { linkPhotos, proofPhotos, proofCol } from "./capture.js";
import { bankAccountFor, accountIdField } from "./banks.js";
import { sendDirect } from "../whatsapp/cloud.js";
import { notify, staff } from "../notifications.js";

export const carriage = Router();
carriage.use("/carriage", requirePerm("carriage.view"));
const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const product = z.enum(["PMG", "HOBC", "HSD"]);

const own = (t: number, id: number) => {
  const k = get("SELECT * FROM thekedars WHERE id=? AND tenant_id=?", id, t);
  if (!k) throw new AppError(404, "Thekedar not found");
  return k;
};
const tenantName = (t: number) => get("SELECT name FROM tenants WHERE id=?", t)!.name;
/** WhatsApp the thekedar (carriage confirmations), best effort. */
const tell = (t: number, k: any, kind: string, ref: string, text: string) => { if (k.phone) void sendDirect(t, { phone: k.phone, name: k.name }, kind, ref, text).catch(() => {}); };
const bankMove = (t: number, account: number, dir: "in" | "out", amount: number, party: string, ref: string, note: string, at: string, by: string) =>
  run("INSERT INTO bank_txns (tenant_id,account_id,kind,amount,party,ref,note,txn_date,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
    t, account, dir === "in" ? "other_in" : "other_out", dir === "in" ? amount : -amount, party, ref, note, at, by, now());

/** What a thekedar owes us for carriage (kiraya billed − paid ± adjustments), plus his opening balance. */
const DUE_SQL = `CASE type WHEN 'payment' THEN -amount ELSE amount END`;
export function thekedarDue(thekedarId: number, before?: string): number {
  const k = get("SELECT opening_balance FROM thekedars WHERE id=?", thekedarId)!;
  const r = get(`SELECT COALESCE(SUM(${DUE_SQL}),0) d FROM carriage_txns WHERE thekedar_id=? AND voided=0 ${before ? "AND txn_date < ?" : ""}`,
    ...(before ? [thekedarId, before] : [thekedarId]))!;
  return round2(k.opening_balance + r.d);
}
/** Carriage / kiraya income (whole kiraya is profit — no fuel cost on our books) in a period. */
export function carriageIncomeThekedar(tenantId: number, fromIso: string, toIso?: string): number {
  const r = get(`SELECT COALESCE(SUM(amount),0) v FROM carriage_txns WHERE tenant_id=? AND voided=0 AND type='carriage' AND txn_date >= ?${toIso ? " AND txn_date < ?" : ""}`,
    ...(toIso ? [tenantId, fromIso, toIso] : [tenantId, fromIso]))!;
  return round2(r.v as number);
}

/** Depots (our IDs) a bypass can be lifted on — the suppliers list. */
carriage.get("/carriage/depots", h((req) =>
  all("SELECT id, name, phone FROM suppliers WHERE tenant_id=? AND COALESCE(active,1)=1 ORDER BY name", tid(req))));

/* ================= Thekedars (the carriage party) ================= */
carriage.get("/carriage/thekedars", h((req) => {
  const t = tid(req);
  const list = all("SELECT * FROM thekedars WHERE tenant_id=? ORDER BY active DESC, name", t);
  return list.map((k) => ({ ...k, due: thekedarDue(k.id) }));
}));

carriage.post("/carriage/thekedars", requirePerm("carriage.manage"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({
    name: z.string().min(1).max(80), phone: z.string().max(20).optional().nullable(), cnic: z.string().max(20).optional().nullable(),
    city: z.string().max(60).optional().nullable(), address: z.string().max(200).optional().nullable(),
    opening_balance: z.number().optional(), notes: z.string().max(300).optional().nullable(),
  }), req.body);
  const { id } = run(`INSERT INTO thekedars (tenant_id,name,phone,cnic,city,address,opening_balance,notes,created_at)
    VALUES (?,?,?,?,?,?,?,?,?)`, t, b.name, b.phone ?? null, b.cnic ?? null, b.city ?? null, b.address ?? null, round2(b.opening_balance ?? 0), b.notes ?? null, now());
  return get("SELECT * FROM thekedars WHERE id=?", id);
}));

carriage.patch("/carriage/thekedars/:id", requirePerm("carriage.manage"), h((req) => {
  const t = tid(req);
  const k = own(t, Number(req.params.id));
  const b = parse(z.object({
    name: z.string().min(1).max(80).optional(), phone: z.string().max(20).optional().nullable(), cnic: z.string().max(20).optional().nullable(),
    city: z.string().max(60).optional().nullable(), address: z.string().max(200).optional().nullable(),
    active: z.boolean().optional(), notes: z.string().max(300).optional().nullable(),
  }), req.body);
  const set: string[] = [], vals: any[] = [];
  for (const [col, v] of Object.entries(b)) { if (v !== undefined) { set.push(`${col}=?`); vals.push(col === "active" ? (v ? 1 : 0) : v); } }
  if (set.length) run(`UPDATE thekedars SET ${set.join(",")} WHERE id=?`, ...vals, k.id);
  return { ...get("SELECT * FROM thekedars WHERE id=?", k.id)!, due: thekedarDue(k.id) };
}));

carriage.get("/carriage/thekedars/:id", h((req) => {
  const t = tid(req);
  const k = own(t, Number(req.params.id));
  return statement(t, k.id, req.query.from as string | undefined, req.query.to as string | undefined);
}));

/* ================= The fuel-money leg (direct to the depot, or routed through us) ================= */
const fuelSchema = z.object({
  mode: z.enum(["direct", "through_us"]), amount: z.number().positive().max(1_000_000_000),
  in_method: z.string().max(30).optional().nullable(), in_account_id: accountIdField, in_ref: z.string().max(60).optional().nullable(),
  forward_now: z.boolean().optional(), fwd_method: z.string().max(30).optional().nullable(), fwd_account_id: accountIdField, fwd_ref: z.string().max(60).optional().nullable(),
});
/** Record fuel money for a bypass. Returns the new record id. */
function recordFuel(t: number, k: any, supplierId: number, depotName: string, f: z.infer<typeof fuelSchema>, invoiceRef: string | null, note: string | null, ts: string, by: string, carriageTxnId: number | null) {
  const amount = round2(f.amount);
  if (f.mode === "direct") {
    return run(`INSERT INTO bypass_fuel_payments (tenant_id,thekedar_id,supplier_id,amount,mode,status,invoice_ref,note,carriage_txn_id,created_by,txn_date,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`, t, k.id, supplierId, amount, "direct", "direct", invoiceRef, note, carriageTxnId, by, ts, now()).id as number;
  }
  const inAcc = bankAccountFor(t, f.in_account_id, f.in_method ?? "bank");
  if (!inAcc) throw new AppError(400, "Choose which of our bank accounts the thekedar sent the fuel money to");
  const forwarded = Boolean(f.forward_now);
  const fwdAcc = forwarded ? (bankAccountFor(t, f.fwd_account_id ?? f.in_account_id, f.fwd_method ?? f.in_method ?? "bank") || inAcc) : null;
  const id = run(`INSERT INTO bypass_fuel_payments (tenant_id,thekedar_id,supplier_id,amount,mode,status,in_account_id,in_ref,fwd_account_id,fwd_ref,forwarded_at,forwarded_by,invoice_ref,note,carriage_txn_id,created_by,txn_date,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    t, k.id, supplierId, amount, "through_us", forwarded ? "forwarded" : "held", inAcc, f.in_ref ?? null,
    fwdAcc, forwarded ? (f.fwd_ref ?? null) : null, forwarded ? ts : null, forwarded ? by : null, invoiceRef, note, carriageTxnId, by, ts, now()).id as number;
  bankMove(t, inAcc, "in", amount, k.name, `bypassfuel:in:${id}`, `Fuel money from ${k.name} for ${depotName}`, ts, by);
  if (forwarded && fwdAcc) bankMove(t, fwdAcc, "out", amount, depotName, `bypassfuel:out:${id}`, `Fuel money forwarded to ${depotName}`, ts, by);
  return id;
}

/* ================= Bill the kiraya (+ optional fuel money in the same entry) ================= */
carriage.post("/carriage/thekedars/:id/carriage", requirePerm("carriage.manage"), h((req) => {
  const t = tid(req);
  const k = own(t, Number(req.params.id));
  const b = parse(z.object({
    supplier_id: z.number().int(), invoice_ref: z.string().max(60).optional().nullable(), vehicle_no: z.string().max(30).optional().nullable(),
    lines: z.array(z.object({ product, litres: z.number().positive().max(200_000) })).min(1),
    amount: z.number().positive().max(1_000_000_000), note: z.string().max(200).optional().nullable(), txn_date: day.optional(),
    photo_ids: proofPhotos, notify: z.boolean().default(true), fuel: fuelSchema.optional().nullable(),
  }), req.body);
  const depot = get("SELECT name FROM suppliers WHERE id=? AND tenant_id=?", b.supplier_id, t);
  if (!depot) throw new AppError(400, "Choose the depot (our ID) the fuel was lifted on");
  const litres = round2(b.lines.reduce((a, l) => a + l.litres, 0));
  const kiraya = round2(b.amount);
  const ts = b.txn_date ? new Date(`${b.txn_date}T12:00:00+05:00`).toISOString() : now();
  const breakdown = b.lines.map((l) => `${round2(l.litres)} L ${PRODUCTS[l.product] ?? l.product}`).join(" + ");
  const single = b.lines.length === 1 ? b.lines[0].product : null;
  const note = [`Bypass on our ID · ${depot.name}`, breakdown, "fixed kiraya", b.note].filter(Boolean).join(" · ");
  const by = req.user!.name;
  const { id, fuelId } = tx(() => {
    const { id } = run(`INSERT INTO carriage_txns (tenant_id,thekedar_id,type,supplier_id,product,litres,amount,vehicle_no,ref,note,created_by,txn_date,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`,
      t, k.id, "carriage", b.supplier_id, single, litres, kiraya, b.vehicle_no ?? null, b.invoice_ref ?? null, note, by, ts, now());
    if (b.photo_ids?.length) linkPhotos(t, b.photo_ids, `carr:${id}`); // depot invoice photo
    const fuelId = b.fuel ? recordFuel(t, k, b.supplier_id, depot.name, b.fuel, b.invoice_ref ?? null, b.note ?? null, ts, by, id) : null;
    return { id, fuelId };
  });
  if (b.notify) {
    const fuelLine = b.fuel ? `\nFuel Rs ${Math.round(b.fuel.amount).toLocaleString("en-PK")} ${b.fuel.mode === "direct" ? "aap ne depot ko di" : (b.fuel.forward_now ? "hum ne depot ko bhej di" : "hamein mili")}.` : "";
    tell(t, k, "carriage", `carr:${id}`, `${k.name}\nBypass supply${b.invoice_ref ? ` (inv ${b.invoice_ref})` : ""}: ${breakdown}.\nKiraya Rs ${Math.round(kiraya).toLocaleString("en-PK")} aap ke zimme.${fuelLine}\n— ${tenantName(t)}`);
  }
  return { txn: get("SELECT * FROM carriage_txns WHERE id=?", id), kiraya, litres, due: thekedarDue(k.id), fuel_id: fuelId };
}));

/* ================= Thekedar pays his kiraya / an adjustment ================= */
carriage.post("/carriage/thekedars/:id/payment", requirePerm("carriage.manage"), h((req) => {
  const t = tid(req);
  const k = own(t, Number(req.params.id));
  const b = parse(z.object({
    amount: z.number().positive().max(1_000_000_000), method: z.string().max(30).optional().nullable(), account_id: accountIdField,
    ref: z.string().max(60).optional().nullable(), note: z.string().max(200).optional().nullable(), txn_date: day.optional(),
    photo_ids: proofPhotos, notify: z.boolean().default(true),
  }), req.body);
  const ts = b.txn_date ? new Date(`${b.txn_date}T12:00:00+05:00`).toISOString() : now();
  const acc = bankAccountFor(t, b.account_id, b.method ?? "cash");
  const { id } = run(`INSERT INTO carriage_txns (tenant_id,thekedar_id,type,amount,method,account_id,ref,note,created_by,txn_date,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)`, t, k.id, "payment", round2(b.amount), b.method ?? null, acc ?? null, b.ref ?? null, b.note ?? null, req.user!.name, ts, now());
  if (b.photo_ids?.length) linkPhotos(t, b.photo_ids, `carr:${id}`);
  if (b.notify) tell(t, k, "carriage", `carr:${id}`, `${k.name}\nKiraya payment Rs ${Math.round(b.amount).toLocaleString("en-PK")} mil gayi. Baqaya Rs ${Math.round(thekedarDue(k.id)).toLocaleString("en-PK")}.\n— ${tenantName(t)}`);
  return { txn: get("SELECT * FROM carriage_txns WHERE id=?", id), due: thekedarDue(k.id) };
}));

carriage.post("/carriage/thekedars/:id/adjustment", requirePerm("carriage.manage"), h((req) => {
  const t = tid(req);
  const k = own(t, Number(req.params.id));
  const b = parse(z.object({ amount: z.number().refine((n) => n !== 0, "non-zero"), note: z.string().max(200), txn_date: day.optional() }), req.body);
  const ts = b.txn_date ? new Date(`${b.txn_date}T12:00:00+05:00`).toISOString() : now();
  const { id } = run(`INSERT INTO carriage_txns (tenant_id,thekedar_id,type,amount,note,created_by,txn_date,created_at)
    VALUES (?,?,?,?,?,?,?,?)`, t, k.id, "adjustment", round2(b.amount), b.note, req.user!.name, ts, now());
  return { txn: get("SELECT * FROM carriage_txns WHERE id=?", id), due: thekedarDue(k.id) };
}));

/** Void a carriage / payment / adjustment entry. Its linked fuel-money records are voided too. */
carriage.post("/carriage/txns/:id/void", requirePerm("carriage.void"), h((req) => {
  const t = tid(req);
  const x = get("SELECT * FROM carriage_txns WHERE id=? AND tenant_id=? AND voided=0", Number(req.params.id), t);
  if (!x) throw new AppError(404, "Entry not found");
  const b = parse(z.object({ reason: z.string().max(200).optional().nullable() }), req.body);
  tx(() => {
    run("UPDATE carriage_txns SET voided=1, void_reason=? WHERE id=?", b.reason ?? null, x.id);
    // void any fuel money booked in the same entry (reverse its bank movements too)
    for (const f of all("SELECT id FROM bypass_fuel_payments WHERE carriage_txn_id=? AND voided=0", x.id)) {
      run("DELETE FROM bank_txns WHERE tenant_id=? AND ref IN (?,?)", t, `bypassfuel:in:${f.id}`, `bypassfuel:out:${f.id}`);
      run("UPDATE bypass_fuel_payments SET voided=1 WHERE id=?", f.id);
    }
  });
  return { ok: true, due: thekedarDue(x.thekedar_id) };
}));

/* ================= Standalone fuel-money entry ================= */
carriage.post("/carriage/thekedars/:id/fuel-payment", requirePerm("carriage.manage"), h((req) => {
  const t = tid(req);
  const k = own(t, Number(req.params.id));
  const b = parse(fuelSchema.extend({
    supplier_id: z.number().int(), invoice_ref: z.string().max(60).optional().nullable(), note: z.string().max(200).optional().nullable(),
    photo_ids: proofPhotos, txn_date: day.optional(), notify: z.boolean().default(true),
  }), req.body);
  const depot = get("SELECT name FROM suppliers WHERE id=? AND tenant_id=?", b.supplier_id, t);
  if (!depot) throw new AppError(400, "Choose the depot the fuel was lifted on");
  const ts = b.txn_date ? new Date(`${b.txn_date}T12:00:00+05:00`).toISOString() : now();
  const by = req.user!.name;
  const id = tx(() => recordFuel(t, k, b.supplier_id, depot.name, b, b.invoice_ref ?? null, b.note ?? null, ts, by, null));
  if (b.photo_ids?.length) linkPhotos(t, b.photo_ids, `bfp:${id}`);
  const f = get("SELECT * FROM bypass_fuel_payments WHERE id=?", id);
  if (b.notify) tell(t, k, "carriage_fuel", `bfp:${id}`, `${k.name}\nFuel Rs ${Math.round(b.amount).toLocaleString("en-PK")} ${b.mode === "direct" ? `aap ne ${depot.name} ko di (record)` : (f.status === "forwarded" ? `hum ne ${depot.name} ko bhej di` : "hamein mil gayi")}.${b.invoice_ref ? ` Inv ${b.invoice_ref}.` : ""}\n— ${tenantName(t)}`);
  return f;
}));

/** Forward held fuel money on to the depot. */
carriage.post("/carriage/fuel-payments/:id/forward", requirePerm("carriage.manage"), h((req) => {
  const t = tid(req);
  const f = get("SELECT * FROM bypass_fuel_payments WHERE id=? AND tenant_id=? AND voided=0", Number(req.params.id), t);
  if (!f) throw new AppError(404, "Fuel payment not found");
  if (f.status !== "held") throw new AppError(400, "Only money still held with us can be forwarded");
  const b = parse(z.object({ fwd_method: z.string().max(30).optional().nullable(), fwd_account_id: accountIdField, fwd_ref: z.string().max(60).optional().nullable(), notify: z.boolean().default(true) }), req.body);
  const k = get("SELECT * FROM thekedars WHERE id=?", f.thekedar_id);
  const depot = get("SELECT name FROM suppliers WHERE id=?", f.supplier_id);
  const fwdAcc = bankAccountFor(t, b.fwd_account_id ?? f.in_account_id, b.fwd_method ?? "bank") || f.in_account_id;
  tx(() => {
    run("UPDATE bypass_fuel_payments SET status='forwarded', fwd_account_id=?, fwd_ref=?, forwarded_at=?, forwarded_by=? WHERE id=?", fwdAcc, b.fwd_ref ?? null, now(), req.user!.name, f.id);
    bankMove(t, fwdAcc, "out", f.amount, depot?.name ?? "Depot", `bypassfuel:out:${f.id}`, `Fuel money forwarded to ${depot?.name ?? "depot"}`, now(), req.user!.name);
  });
  if (b.notify && k) tell(t, k, "carriage_fuel", `bfp:${f.id}`, `${k.name}\nAap ki fuel payment Rs ${Math.round(f.amount).toLocaleString("en-PK")} ${depot?.name ?? "depot"} ko bhej di gayi.\n— ${tenantName(t)}`);
  return get("SELECT * FROM bypass_fuel_payments WHERE id=?", f.id);
}));

/** Void a fuel-money record (correction): reverse any bank movement and drop it from the statement. */
carriage.post("/carriage/fuel-payments/:id/void", requirePerm("carriage.manage"), h((req) => {
  const t = tid(req);
  const f = get("SELECT * FROM bypass_fuel_payments WHERE id=? AND tenant_id=? AND voided=0", Number(req.params.id), t);
  if (!f) throw new AppError(404, "Fuel payment not found");
  tx(() => {
    run("DELETE FROM bank_txns WHERE tenant_id=? AND ref IN (?,?)", t, `bypassfuel:in:${f.id}`, `bypassfuel:out:${f.id}`);
    run("UPDATE bypass_fuel_payments SET voided=1 WHERE id=?", f.id);
  });
  return { ok: true };
}));

/** Held fuel money (received from a thekedar, not yet sent to the depot). */
carriage.get("/carriage/fuel-held", h((req) =>
  all(`SELECT f.*, k.name thekedar_name, s.name depot_name FROM bypass_fuel_payments f JOIN thekedars k ON k.id=f.thekedar_id
    LEFT JOIN suppliers s ON s.id=f.supplier_id WHERE f.tenant_id=? AND f.status='held' AND f.voided=0 ORDER BY f.txn_date, f.id`, tid(req))));

/** Owner summary of the carriage business: kiraya earned (profit) and fuel money routed, by thekedar and by depot. */
carriage.get("/carriage/summary", h((req) => {
  const t = tid(req);
  const month = String(req.query.month ?? pkDate().slice(0, 7));
  const from = new Date(`${month}-01T00:00:00+05:00`).toISOString(), to = new Date(`${month}-31T23:59:59+05:00`).toISOString();
  const P = [t, from, to] as const;
  return {
    month,
    kiraya_total: round2(get(`SELECT COALESCE(SUM(amount),0) v FROM carriage_txns WHERE tenant_id=? AND type='carriage' AND voided=0 AND txn_date >= ? AND txn_date <= ?`, ...P)!.v),
    kiraya_by_thekedar: all(`SELECT k.name, ROUND(SUM(c.amount),2) kiraya, COUNT(*) trips FROM carriage_txns c JOIN thekedars k ON k.id=c.thekedar_id
      WHERE c.tenant_id=? AND c.type='carriage' AND c.voided=0 AND c.txn_date >= ? AND c.txn_date <= ? GROUP BY c.thekedar_id ORDER BY kiraya DESC`, ...P),
    fuel_by_depot: all(`SELECT s.name depot, ROUND(SUM(f.amount),2) fuel, SUM(CASE WHEN f.mode='through_us' THEN f.amount ELSE 0 END) through_us, SUM(CASE WHEN f.mode='direct' THEN f.amount ELSE 0 END) direct
      FROM bypass_fuel_payments f LEFT JOIN suppliers s ON s.id=f.supplier_id WHERE f.tenant_id=? AND f.thekedar_id IS NOT NULL AND f.voided=0 AND f.txn_date >= ? AND f.txn_date <= ? GROUP BY f.supplier_id ORDER BY fuel DESC`, ...P),
    held_total: round2(get(`SELECT COALESCE(SUM(amount),0) v FROM bypass_fuel_payments WHERE tenant_id=? AND thekedar_id IS NOT NULL AND status='held' AND voided=0`, t)!.v),
  };
}));

/* ---------------- Statement (ledger with running balance) ---------------- */
export function statement(tenantId: number, thekedarId: number, from?: string, to?: string) {
  const k = own(tenantId, thekedarId);
  const fromIso = from ? new Date(`${from}T00:00:00+05:00`).toISOString() : undefined;
  const toIso = to ? new Date(`${to}T23:59:59+05:00`).toISOString() : undefined;
  const opening = fromIso ? thekedarDue(k.id, fromIso) : k.opening_balance;
  const rows = all(
    `SELECT x.*, ${proofCol("'carr:'||x.id")}, s.name depot_name FROM carriage_txns x LEFT JOIN suppliers s ON s.id=x.supplier_id
     WHERE x.thekedar_id=? ${fromIso ? "AND x.txn_date >= ?" : ""} ${toIso ? "AND x.txn_date < ?" : ""} ORDER BY x.txn_date, x.id`,
    ...[k.id, ...(fromIso ? [fromIso] : []), ...(toIso ? [toIso] : [])],
  );
  let bal = opening;
  const real = rows.map((r) => {
    const effect = r.voided ? 0 : r.type === "payment" ? -r.amount : r.amount;
    bal = round2(bal + effect);
    return { ...r, debit: effect > 0 ? effect : 0, credit: effect < 0 ? -effect : 0, balance: bal };
  });
  // fuel-money records — shown for the record only; no effect on the kiraya due
  const fuels = all(
    `SELECT f.*, s.name depot_name, ${proofCol("'bfp:'||f.id")} FROM bypass_fuel_payments f LEFT JOIN suppliers s ON s.id=f.supplier_id
     WHERE f.thekedar_id=? AND f.voided=0 ${fromIso ? "AND f.txn_date >= ?" : ""} ${toIso ? "AND f.txn_date < ?" : ""}`,
    ...[k.id, ...(fromIso ? [fromIso] : []), ...(toIso ? [toIso] : [])],
  ).map((f) => ({ id: `bfp${f.id}`, type: "fuel_note", txn_date: f.txn_date, amount: f.amount, fuel_mode: f.mode, fuel_status: f.status, depot_name: f.depot_name, ref: f.invoice_ref, note: f.note, proof_ids: f.proof_ids, debit: 0, credit: 0, balance: 0 }));
  const lines = [...real, ...fuels].sort((a, b) => (a.txn_date < b.txn_date ? -1 : a.txn_date > b.txn_date ? 1 : 0));
  let rb = opening;
  for (const l of lines) { if (l.type !== "fuel_note") rb = l.balance; else l.balance = rb; }
  return { thekedar: { ...k, due: thekedarDue(k.id) }, from: from ?? null, to: to ?? null, opening_balance: opening, closing_balance: bal, lines };
}

/** Reminder: fuel money received from thekedars but not yet forwarded to the depot for over ~a day. */
export async function carriageFuelHeldWatch(t: number) {
  const cutoff = new Date(Date.now() - 20 * 3600_000).toISOString();
  const held = all(`SELECT f.amount, k.name thekedar_name, s.name depot_name FROM bypass_fuel_payments f JOIN thekedars k ON k.id=f.thekedar_id
    LEFT JOIN suppliers s ON s.id=f.supplier_id WHERE f.tenant_id=? AND f.status='held' AND f.voided=0 AND f.txn_date < ?`, t, cutoff);
  if (!held.length) return 0;
  const total = round2(held.reduce((a, f) => a + f.amount, 0));
  const a = createAlert(t, { type: "carriage_fuel_held", severity: "warning", title: `Fuel money to forward: ${pkr(total)} (${held.length})`,
    body: held.map((f) => `${f.thekedar_name} → ${f.depot_name ?? "depot"} ${pkr(f.amount)}`).join("; "), dedupe_key: `carr-fuel-held-${pkDate()}` });
  if (a) await notify(t, staff(t, ["admin", "manager"]), { type: "carriage_fuel_held", title: a.title, body: "Thekedar fuel money is still with us — forward it to the depot." });
  return held.length;
}
