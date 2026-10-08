/**
 * Wholesale desk: the day-to-day work around supplies and recovery.
 *  - Order book: a client books litres for a day; the supply / tanker trip that delivers it closes the order
 *  - Payment promises: "will pay Rs X on Friday" — kept or broken is worked out from the payments that came in
 *  - Cheque register: post-dated cheques in hand → deposited → cleared (becomes the payment) or bounced
 *  - Call list: who to call today for money, most urgent first
 *  - Extra suggestions for the wholesale dashboard from all of the above
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, tx, now, pkDate, pkStart, pkEnd, type Row } from "../db.js";
import { h, parse, tid, requirePerm, requireAny, requireRole } from "../auth.js";
import { AppError, createAlert, round2, pkr } from "../services.js";
import { PRODUCTS } from "../config.js";
import { linkPhotos, proofPhotos, proofCol, requireProof } from "./capture.js";
import { bankAccountFor, accountIdField } from "./banks.js";
import { sendDirect } from "../whatsapp/cloud.js";
import { notify, staff } from "../notifications.js";
import { clientDue } from "./wholesale.js";

export const wholesaleDesk = Router();
wholesaleDesk.use("/wholesale", requirePerm("wholesale.view"));
const DAY = 86_400_000;
const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const product = z.enum(["PMG", "HOBC", "HSD"]);
const own = (t: number, id: number) => {
  const c = get("SELECT * FROM wholesale_clients WHERE id=? AND tenant_id=?", id, t);
  if (!c) throw new AppError(404, "Wholesale client not found");
  return c;
};
const addDays = (d: string, n: number) => new Date(Date.parse(d + "T00:00:00Z") + n * DAY).toISOString().slice(0, 10);

/* ================= Order book ================= */
export function openOrders(t: number, clientId?: number) {
  return all(`SELECT o.*, c.name client_name, c.phone, c.city FROM wholesale_orders o JOIN wholesale_clients c ON c.id=o.client_id
    WHERE o.tenant_id=? AND o.status='open' ${clientId ? "AND o.client_id=?" : ""} ORDER BY o.needed_on, o.id`, t, ...(clientId ? [clientId] : []));
}
/** A supply that delivers an order closes it (called from the supply and trip routes, inside their transaction). */
export function deliverOrder(t: number, orderId: number | null | undefined, clientId: number, txnId: number) {
  if (!orderId) return;
  const o = get("SELECT * FROM wholesale_orders WHERE id=? AND tenant_id=?", orderId, t);
  if (!o) throw new AppError(400, "Order not found");
  if (o.client_id !== clientId) throw new AppError(400, "That order belongs to another client");
  if (o.status !== "open") throw new AppError(400, "That order is already closed");
  run("UPDATE wholesale_orders SET status='done', txn_id=?, done_at=? WHERE id=?", txnId, now(), o.id);
}

wholesaleDesk.get("/wholesale/orders", h((req) => {
  const t = tid(req), today = pkDate();
  const open = openOrders(t).map((o) => ({ ...o, late: o.needed_on < today, today: o.needed_on === today }));
  const need: Record<string, number> = {};
  for (const o of open) if (o.needed_on <= addDays(today, 2)) need[o.product] = (need[o.product] ?? 0) + o.litres;
  const stock = Object.fromEntries(Object.keys(PRODUCTS).map((p) => [p, Math.round(get("SELECT COALESCE(SUM(t.current_l),0) l FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? AND t.product=?", t, p)!.l)]));
  return {
    open, stock, need_3_days: need,
    done: all(`SELECT o.*, c.name client_name FROM wholesale_orders o JOIN wholesale_clients c ON c.id=o.client_id WHERE o.tenant_id=? AND o.status<>'open' ORDER BY COALESCE(o.done_at, o.created_at) DESC LIMIT 15`, t),
  };
}));

wholesaleDesk.post("/wholesale/clients/:id/orders", requirePerm("wholesale.manage"), h((req) => {
  const t = tid(req);
  const c = own(t, Number(req.params.id));
  const b = parse(z.object({ product, litres: z.number().positive().max(200_000), needed_on: day, location: z.string().max(120).optional().nullable(), note: z.string().max(200).optional().nullable() }), req.body);
  if (b.needed_on < addDays(pkDate(), -1)) throw new AppError(400, "The delivery date is in the past");
  const { id } = run("INSERT INTO wholesale_orders (tenant_id,client_id,product,litres,needed_on,location,note,status,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
    t, c.id, b.product, b.litres, b.needed_on, b.location ?? c.city ?? null, b.note ?? null, "open", req.user!.name, now());
  return get("SELECT * FROM wholesale_orders WHERE id=?", id);
}));

wholesaleDesk.patch("/wholesale/orders/:id", requirePerm("wholesale.manage"), h((req) => {
  const t = tid(req);
  const o = get("SELECT * FROM wholesale_orders WHERE id=? AND tenant_id=?", Number(req.params.id), t);
  if (!o) throw new AppError(404, "Order not found");
  if (o.status !== "open") throw new AppError(400, "That order is already closed");
  const b = parse(z.object({ litres: z.number().positive().max(200_000).optional(), needed_on: day.optional(), location: z.string().max(120).optional().nullable(),
    note: z.string().max(200).optional().nullable(), cancel: z.string().min(3, "Give a reason").max(200).optional() }), req.body);
  if (b.cancel) run("UPDATE wholesale_orders SET status='cancelled', note=?, done_at=? WHERE id=?", `${o.note ? `${o.note} · ` : ""}Cancelled: ${b.cancel} (${req.user!.name})`, now(), o.id);
  else run("UPDATE wholesale_orders SET litres=?, needed_on=?, location=?, note=? WHERE id=?", b.litres ?? o.litres, b.needed_on ?? o.needed_on, b.location === undefined ? o.location : b.location, b.note === undefined ? o.note : b.note, o.id);
  return get("SELECT * FROM wholesale_orders WHERE id=?", o.id);
}));

/* ================= Carriage / kiraya supply (bypass on our depot ID) ================= */
/**
 * A bypass supply where the depot loads on OUR id and invoices us, but the fuel money is the client's — we only
 * earn kiraya (carriage/commission). No stock, no fuel purchase/sale on our books: just the kiraya billed to the
 * client (receivable) and booked as income. The client settles it by cash / online / cheque like any other due.
 */
const bankMove = (t: number, account: number, dir: "in" | "out", amount: number, party: string, ref: string, note: string, at: string, by: string) =>
  run("INSERT INTO bank_txns (tenant_id,account_id,kind,amount,party,ref,note,txn_date,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
    t, account, dir === "in" ? "other_in" : "other_out", dir === "in" ? amount : -amount, party, ref, note, at, by, now());
/** WhatsApp the client (bypass confirmations), best effort. */
const tellClient = (t: number, c: any, kind: string, ref: string, text: string) => { if (c.phone) void sendDirect(t, { phone: c.phone, name: c.name }, kind, ref, text).catch(() => {}); };
const tenantName = (t: number) => get("SELECT name FROM tenants WHERE id=?", t)!.name;

/** The optional "fuel money" leg shared by the standalone and the combined entry. Returns the new record id (or null). */
const fuelSchema = z.object({
  mode: z.enum(["direct", "through_us"]), amount: z.number().positive().max(1_000_000_000),
  in_method: z.string().max(30).optional().nullable(), in_account_id: accountIdField, in_ref: z.string().max(60).optional().nullable(),
  forward_now: z.boolean().optional(), fwd_method: z.string().max(30).optional().nullable(), fwd_account_id: accountIdField, fwd_ref: z.string().max(60).optional().nullable(),
});
function recordFuel(t: number, c: any, supplierId: number, depotName: string, f: z.infer<typeof fuelSchema>, invoiceRef: string | null, note: string | null, ts: string, by: string, carriageTxnId: number | null) {
  const amount = round2(f.amount);
  if (f.mode === "direct") {
    return run(`INSERT INTO bypass_fuel_payments (tenant_id,client_id,supplier_id,amount,mode,status,invoice_ref,note,carriage_txn_id,created_by,txn_date,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`, t, c.id, supplierId, amount, "direct", "direct", invoiceRef, note, carriageTxnId, by, ts, now()).id as number;
  }
  const inAcc = bankAccountFor(t, f.in_account_id, f.in_method ?? "bank");
  if (!inAcc) throw new AppError(400, "Choose which of our bank accounts the client sent the fuel money to");
  const forwarded = Boolean(f.forward_now);
  const fwdAcc = forwarded ? (bankAccountFor(t, f.fwd_account_id ?? f.in_account_id, f.fwd_method ?? f.in_method ?? "bank") || inAcc) : null;
  const id = run(`INSERT INTO bypass_fuel_payments (tenant_id,client_id,supplier_id,amount,mode,status,in_account_id,in_ref,fwd_account_id,fwd_ref,forwarded_at,forwarded_by,invoice_ref,note,carriage_txn_id,created_by,txn_date,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    t, c.id, supplierId, amount, "through_us", forwarded ? "forwarded" : "held", inAcc, f.in_ref ?? null,
    fwdAcc, forwarded ? (f.fwd_ref ?? null) : null, forwarded ? ts : null, forwarded ? by : null, invoiceRef, note, carriageTxnId, by, ts, now()).id as number;
  bankMove(t, inAcc, "in", amount, c.name, `bypassfuel:in:${id}`, `Fuel money from ${c.name} for ${depotName}`, ts, by);
  if (forwarded && fwdAcc) bankMove(t, fwdAcc, "out", amount, depotName, `bypassfuel:out:${id}`, `Fuel money forwarded to ${depotName}`, ts, by);
  return id;
}

wholesaleDesk.post("/wholesale/clients/:id/carriage", requirePerm("wholesale.manage"), h((req) => {
  const t = tid(req);
  const c = own(t, Number(req.params.id));
  const b = parse(z.object({
    supplier_id: z.number().int(), invoice_ref: z.string().max(60).optional().nullable(), vehicle_no: z.string().max(30).optional().nullable(),
    lines: z.array(z.object({ product, litres: z.number().positive().max(200_000) })).min(1),
    amount: z.number().positive().max(1_000_000_000), note: z.string().max(200).optional().nullable(), txn_date: day.optional(),
    photo_ids: proofPhotos, notify: z.boolean().default(true), fuel: fuelSchema.optional().nullable(), // optional fuel-money leg in the same entry
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
    const { id } = run(`INSERT INTO wholesale_txns (tenant_id,client_id,type,product,litres,rate,amount,vehicle_no,ref,note,created_by,txn_date,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`,
      t, c.id, "carriage", single, litres, null, kiraya, b.vehicle_no ?? null, b.invoice_ref ?? null, note, by, ts, now());
    if (b.photo_ids?.length) linkPhotos(t, b.photo_ids, `wtx:${id}`); // depot invoice photo
    const fuelId = b.fuel ? recordFuel(t, c, b.supplier_id, depot.name, b.fuel, b.invoice_ref ?? null, b.note ?? null, ts, by, id) : null;
    return { id, fuelId };
  });
  if (b.notify) {
    const fuelLine = b.fuel ? `\nFuel Rs ${Math.round(b.fuel.amount).toLocaleString("en-PK")} ${b.fuel.mode === "direct" ? "aap ne depot ko di" : (b.fuel.forward_now ? "hum ne depot ko bhej di" : "hamein mili")}.` : "";
    tellClient(t, c, "bypass_carriage", `wtx:${id}`, `${c.name}\nBypass supply${b.invoice_ref ? ` (inv ${b.invoice_ref})` : ""}: ${breakdown}.\nKiraya Rs ${Math.round(kiraya).toLocaleString("en-PK")} aap ke zimme.${fuelLine}\n— ${tenantName(t)}`);
  }
  return { txn: get("SELECT * FROM wholesale_txns WHERE id=?", id), kiraya, litres, due: clientDue(c.id), fuel_id: fuelId };
}));

/** Fuel payments a bypass client made — direct to the depot, or routed through us — for the statement. */
export function bypassFuelPayments(t: number, clientId: number, fromIso?: string, toIso?: string) {
  return all(`SELECT f.*, s.name depot_name, ${proofCol("'bfp:'||f.id")} FROM bypass_fuel_payments f LEFT JOIN suppliers s ON s.id=f.supplier_id
    WHERE f.tenant_id=? AND f.client_id=? AND f.voided=0 ${fromIso ? "AND f.txn_date >= ?" : ""} ${toIso ? "AND f.txn_date < ?" : ""} ORDER BY f.txn_date, f.id`,
    t, clientId, ...(fromIso ? [fromIso] : []), ...(toIso ? [toIso] : []));
}
/** Held fuel money (received from a client, not yet sent to the depot). */
wholesaleDesk.get("/wholesale/fuel-held", requirePerm("wholesale.view"), h((req) =>
  all(`SELECT f.*, c.name client_name, s.name depot_name FROM bypass_fuel_payments f JOIN wholesale_clients c ON c.id=f.client_id
    LEFT JOIN suppliers s ON s.id=f.supplier_id WHERE f.tenant_id=? AND f.status='held' AND f.voided=0 ORDER BY f.txn_date, f.id`, tid(req))));

/** Owner summary of the bypass business: kiraya earned (profit) and fuel money routed, by client and by depot. */
wholesaleDesk.get("/wholesale/bypass-summary", requirePerm("wholesale.view"), h((req) => {
  const t = tid(req);
  const month = String(req.query.month ?? pkDate().slice(0, 7));
  const from = new Date(`${month}-01T00:00:00+05:00`).toISOString(), to = new Date(`${month}-31T23:59:59+05:00`).toISOString();
  const P = [t, from, to] as const;
  return {
    month,
    kiraya_total: round2(get(`SELECT COALESCE(SUM(amount),0) v FROM wholesale_txns WHERE tenant_id=? AND type='carriage' AND voided=0 AND txn_date >= ? AND txn_date <= ?`, ...P)!.v),
    kiraya_by_client: all(`SELECT c.name, ROUND(SUM(w.amount),2) kiraya, COUNT(*) trips FROM wholesale_txns w JOIN wholesale_clients c ON c.id=w.client_id
      WHERE w.tenant_id=? AND w.type='carriage' AND w.voided=0 AND w.txn_date >= ? AND w.txn_date <= ? GROUP BY w.client_id ORDER BY kiraya DESC`, ...P),
    fuel_by_depot: all(`SELECT s.name depot, ROUND(SUM(f.amount),2) fuel, SUM(CASE WHEN f.mode='through_us' THEN f.amount ELSE 0 END) through_us, SUM(CASE WHEN f.mode='direct' THEN f.amount ELSE 0 END) direct
      FROM bypass_fuel_payments f LEFT JOIN suppliers s ON s.id=f.supplier_id WHERE f.tenant_id=? AND f.voided=0 AND f.txn_date >= ? AND f.txn_date <= ? GROUP BY f.supplier_id ORDER BY fuel DESC`, ...P),
    held_total: round2(get(`SELECT COALESCE(SUM(amount),0) v FROM bypass_fuel_payments WHERE tenant_id=? AND status='held' AND voided=0`, t)!.v),
  };
}));

wholesaleDesk.post("/wholesale/clients/:id/fuel-payment", requirePerm("wholesale.manage"), h((req) => {
  const t = tid(req);
  const c = own(t, Number(req.params.id));
  const b = parse(fuelSchema.extend({
    supplier_id: z.number().int(), invoice_ref: z.string().max(60).optional().nullable(), note: z.string().max(200).optional().nullable(),
    photo_ids: proofPhotos, txn_date: day.optional(), notify: z.boolean().default(true),
  }), req.body);
  const depot = get("SELECT name FROM suppliers WHERE id=? AND tenant_id=?", b.supplier_id, t);
  if (!depot) throw new AppError(400, "Choose the depot the fuel was lifted on");
  const ts = b.txn_date ? new Date(`${b.txn_date}T12:00:00+05:00`).toISOString() : now();
  const by = req.user!.name;
  const id = tx(() => recordFuel(t, c, b.supplier_id, depot.name, b, b.invoice_ref ?? null, b.note ?? null, ts, by, null));
  if (b.photo_ids?.length) linkPhotos(t, b.photo_ids, `bfp:${id}`);
  const f = get("SELECT * FROM bypass_fuel_payments WHERE id=?", id);
  if (b.notify) tellClient(t, c, "bypass_fuel", `bfp:${id}`, `${c.name}\nFuel Rs ${Math.round(b.amount).toLocaleString("en-PK")} ${b.mode === "direct" ? `aap ne ${depot.name} ko di (record)` : (f.status === "forwarded" ? `hum ne ${depot.name} ko bhej di` : "hamein mil gayi")}.${b.invoice_ref ? ` Inv ${b.invoice_ref}.` : ""}\n— ${tenantName(t)}`);
  return f;
}));

/** Forward held fuel money on to the depot. */
wholesaleDesk.post("/wholesale/fuel-payments/:id/forward", requirePerm("wholesale.manage"), h((req) => {
  const t = tid(req);
  const f = get("SELECT * FROM bypass_fuel_payments WHERE id=? AND tenant_id=? AND voided=0", Number(req.params.id), t);
  if (!f) throw new AppError(404, "Fuel payment not found");
  if (f.status !== "held") throw new AppError(400, "Only money still held with us can be forwarded");
  const b = parse(z.object({ fwd_method: z.string().max(30).optional().nullable(), fwd_account_id: accountIdField, fwd_ref: z.string().max(60).optional().nullable(), notify: z.boolean().default(true) }), req.body);
  const c = get("SELECT * FROM wholesale_clients WHERE id=?", f.client_id);
  const depot = get("SELECT name FROM suppliers WHERE id=?", f.supplier_id);
  const fwdAcc = bankAccountFor(t, b.fwd_account_id ?? f.in_account_id, b.fwd_method ?? "bank") || f.in_account_id;
  tx(() => {
    run("UPDATE bypass_fuel_payments SET status='forwarded', fwd_account_id=?, fwd_ref=?, forwarded_at=?, forwarded_by=? WHERE id=?", fwdAcc, b.fwd_ref ?? null, now(), req.user!.name, f.id);
    bankMove(t, fwdAcc, "out", f.amount, depot?.name ?? "Depot", `bypassfuel:out:${f.id}`, `Fuel money forwarded to ${depot?.name ?? "depot"}`, now(), req.user!.name);
  });
  if (b.notify && c) tellClient(t, c, "bypass_fuel", `bfp:${f.id}`, `${c.name}\nAap ki fuel payment Rs ${Math.round(f.amount).toLocaleString("en-PK")} ${depot?.name ?? "depot"} ko bhej di gayi.\n— ${tenantName(t)}`);
  return get("SELECT * FROM bypass_fuel_payments WHERE id=?", f.id);
}));

/** Void a fuel-money record (correction): reverse any bank movement and drop it from the statement. */
wholesaleDesk.post("/wholesale/fuel-payments/:id/void", requirePerm("wholesale.manage"), h((req) => {
  const t = tid(req);
  const f = get("SELECT * FROM bypass_fuel_payments WHERE id=? AND tenant_id=? AND voided=0", Number(req.params.id), t);
  if (!f) throw new AppError(404, "Fuel payment not found");
  tx(() => {
    // delete the pass-through bank movements so the bank and 'Depot money held' revert exactly
    run("DELETE FROM bank_txns WHERE tenant_id=? AND ref IN (?,?)", t, `bypassfuel:in:${f.id}`, `bypassfuel:out:${f.id}`);
    run("UPDATE bypass_fuel_payments SET voided=1 WHERE id=?", f.id);
  });
  return { ok: true };
}));

/** Reminder: fuel money received from clients but not yet forwarded to the depot for over ~a day. */
export async function bypassFuelHeldWatch(t: number) {
  const cutoff = new Date(Date.now() - 20 * 3600_000).toISOString();
  const held = all(`SELECT f.amount, c.name client_name, s.name depot_name FROM bypass_fuel_payments f JOIN wholesale_clients c ON c.id=f.client_id
    LEFT JOIN suppliers s ON s.id=f.supplier_id WHERE f.tenant_id=? AND f.status='held' AND f.voided=0 AND f.txn_date < ?`, t, cutoff);
  if (!held.length) return 0;
  const total = round2(held.reduce((a, f) => a + f.amount, 0));
  const a = createAlert(t, { type: "bypass_fuel_held", severity: "warning", title: `Fuel money to forward: ${pkr(total)} (${held.length})`,
    body: held.map((f) => `${f.client_name} → ${f.depot_name ?? "depot"} ${pkr(f.amount)}`).join("; "), dedupe_key: `bfp-held-${pkDate()}` });
  if (a) await notify(t, staff(t, ["admin", "manager"]), { type: "bypass_fuel_held", title: a.title, body: "Client fuel money is still with us — forward it to the depot." });
  return held.length;
}

/* ================= Client supply requests (from the portal) ================= */
/** Supply requests a client placed from their own link — pure communication, not tied to the ledger or the order book. */
export function clientRequests(t: number, clientId?: number, includePast = false) {
  return all(`SELECT r.*, c.name client_name, c.phone, c.city FROM wholesale_requests r JOIN wholesale_clients c ON c.id=r.client_id
    WHERE r.tenant_id=? ${clientId ? "AND r.client_id=?" : ""} ${includePast ? "" : "AND (r.status='pending' OR r.decided_at >= ?)"}
    ORDER BY CASE r.status WHEN 'pending' THEN 0 ELSE 1 END, r.want_date, r.id DESC`,
    t, ...(clientId ? [clientId] : []), ...(includePast ? [] : [new Date(Date.now() - 30 * DAY).toISOString()]));
}
// only the CEO (owner / admin) sees and decides client order requests — it is just an owner-to-client channel
wholesaleDesk.get("/wholesale/requests", requireRole("admin"), h((req) => {
  const t = tid(req);
  const list = clientRequests(t);
  return { pending: list.filter((r) => r.status === "pending"), recent: list.filter((r) => r.status !== "pending") };
}));
/** Approve or reject a client's supply request. Approval pings the client on WhatsApp. CEO only. */
wholesaleDesk.post("/wholesale/requests/:id/:decision(approve|reject)", requireRole("admin"), h(async (req) => {
  const t = tid(req);
  const r = get("SELECT * FROM wholesale_requests WHERE id=? AND tenant_id=?", Number(req.params.id), t);
  if (!r) throw new AppError(404, "Request not found");
  if (r.status !== "pending") throw new AppError(400, "That request is already decided");
  const b = parse(z.object({ reply: z.string().max(200).optional().nullable() }), req.body ?? {});
  const c = get("SELECT * FROM wholesale_clients WHERE id=?", r.client_id);
  const approved = req.params.decision === "approve";
  run("UPDATE wholesale_requests SET status=?, reply=?, decided_by=?, decided_at=? WHERE id=?",
    approved ? "approved" : "rejected", b.reply ?? null, req.user!.name, now(), r.id);
  const when = new Date(`${r.want_date}T00:00:00+05:00`).toLocaleDateString("en-PK", { day: "numeric", month: "short", year: "numeric" });
  const line = `${Math.round(r.litres).toLocaleString("en-PK")} L ${PRODUCTS[r.product] ?? r.product} · ${when}`;
  if (c?.phone) {
    const text = approved
      ? `${c.name}\n✅ Aap ka order approve ho gaya hai:\n${line}${b.reply ? `\n${b.reply}` : ""}\n— ${get("SELECT name FROM tenants WHERE id=?", t)!.name}`
      : `${c.name}\n❌ Maazrat, abhi aap ka order poora nahi ho saka:\n${line}${b.reply ? `\nWajah: ${b.reply}` : ""}\n— ${get("SELECT name FROM tenants WHERE id=?", t)!.name}`;
    await sendDirect(t, { phone: c.phone, name: c.name }, approved ? "order_approved" : "order_rejected", `wreq:${r.id}`, text);
  }
  return get("SELECT * FROM wholesale_requests WHERE id=?", r.id);
}));
/** The client edits their own still-pending request from the portal. Changes only the ask — never supply, stock or the ledger. */
export async function editClientRequest(t: number, clientId: number, reqId: number, b: { product: string; litres: number; want_date: string; note?: string | null }) {
  const r = get("SELECT * FROM wholesale_requests WHERE id=? AND tenant_id=? AND client_id=?", reqId, t, clientId);
  if (!r || r.status !== "pending") return null; // only a pending request the client owns can be edited
  run("UPDATE wholesale_requests SET product=?, litres=?, want_date=?, note=? WHERE id=?", b.product, b.litres, b.want_date, b.note ?? null, r.id);
  const c = get("SELECT name FROM wholesale_clients WHERE id=?", clientId);
  const when = new Date(`${b.want_date}T00:00:00+05:00`).toLocaleDateString("en-PK", { day: "numeric", month: "short" });
  createAlert(t, { type: "wholesale_request", severity: "info", title: `Order changed: ${c?.name} — now ${Math.round(b.litres).toLocaleString("en-PK")} L ${PRODUCTS[b.product] ?? b.product} for ${when}`, dedupe_key: `wreq:${r.id}` });
  await notify(t, staff(t, ["admin"]), { type: "wholesale_request", title: `Order request changed — ${c?.name}`, body: `Now ${Math.round(b.litres).toLocaleString("en-PK")} L ${PRODUCTS[b.product] ?? b.product} on ${when}. Review in Wholesale → Requests.` });
  return get("SELECT * FROM wholesale_requests WHERE id=?", r.id);
}
/** Place a supply request on a client's behalf, and the notification helper the portal uses when the client places one. */
export async function placeClientRequest(t: number, clientId: number, b: { product: string; litres: number; want_date: string; note?: string | null }) {
  const { id } = run("INSERT INTO wholesale_requests (tenant_id,client_id,product,litres,want_date,note,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
    t, clientId, b.product, b.litres, b.want_date, b.note ?? null, "pending", now());
  const c = get("SELECT name FROM wholesale_clients WHERE id=?", clientId);
  const when = new Date(`${b.want_date}T00:00:00+05:00`).toLocaleDateString("en-PK", { day: "numeric", month: "short" });
  createAlert(t, { type: "wholesale_request", severity: "info", title: `Order request: ${c?.name} — ${Math.round(b.litres).toLocaleString("en-PK")} L ${PRODUCTS[b.product] ?? b.product} for ${when}`, dedupe_key: `wreq:${id}` });
  await notify(t, staff(t, ["admin"]), { type: "wholesale_request", title: `New order request — ${c?.name}`, body: `${Math.round(b.litres).toLocaleString("en-PK")} L ${PRODUCTS[b.product] ?? b.product} wanted on ${when}. Approve in Wholesale → Requests.` });
  return get("SELECT * FROM wholesale_requests WHERE id=?", id);
}

/* ================= Payment promises ================= */
/** open → due today → kept (payments since the promise reach it) or broken (date passed, not paid). */
export function promises(t: number, clientId?: number, includeClosed = false) {
  const today = pkDate();
  return all(`SELECT p.*, c.name client_name, c.phone FROM wholesale_promises p JOIN wholesale_clients c ON c.id=p.client_id
    WHERE p.tenant_id=? AND p.status<>'cancelled' ${clientId ? "AND p.client_id=?" : ""} ORDER BY p.promised_on DESC, p.id DESC LIMIT 200`, t, ...(clientId ? [clientId] : []))
    .map((p) => {
      const paid = round2(get("SELECT COALESCE(SUM(amount),0) v FROM wholesale_txns WHERE client_id=? AND type='payment' AND voided=0 AND created_at >= ? AND txn_date <= ?",
        p.client_id, p.created_at, pkEnd(addDays(p.promised_on, 1)))!.v);
      const state = paid >= p.amount - 0.5 ? "kept" : p.promised_on < today ? "broken" : p.promised_on === today ? "today" : "open";
      return { ...p, paid, state };
    })
    .filter((p) => includeClosed || p.state !== "kept" || p.promised_on >= addDays(today, -7))
    .filter((p) => includeClosed || p.state !== "broken" || p.promised_on >= addDays(today, -30));
}

wholesaleDesk.post("/wholesale/clients/:id/promises", requirePerm("wholesale.manage"), h((req) => {
  const t = tid(req);
  const c = own(t, Number(req.params.id));
  const b = parse(z.object({ amount: z.number().positive().max(1_000_000_000), promised_on: day, note: z.string().max(200).optional().nullable() }), req.body);
  if (b.promised_on < pkDate()) throw new AppError(400, "The promised date is in the past");
  const { id } = run("INSERT INTO wholesale_promises (tenant_id,client_id,amount,promised_on,note,status,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
    t, c.id, b.amount, b.promised_on, b.note ?? null, "open", req.user!.name, now());
  return promises(t, c.id).find((p) => p.id === id);
}));

wholesaleDesk.delete("/wholesale/promises/:id", requirePerm("wholesale.manage"), h((req) => {
  const p = get("SELECT id FROM wholesale_promises WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  if (!p) throw new AppError(404, "Promise not found");
  run("UPDATE wholesale_promises SET status='cancelled' WHERE id=?", p.id);
  return { ok: true };
}));

/* ================= Cheque register ================= */
export function cheques(t: number, clientId?: number) {
  return all(`SELECT q.*, ${proofCol("'wchq:'||q.id")}, c.name client_name, c.phone FROM wholesale_cheques q JOIN wholesale_clients c ON c.id=q.client_id
    WHERE q.tenant_id=? ${clientId ? "AND q.client_id=?" : ""} AND (q.status IN ('in_hand','deposited') OR q.updated_at >= ?)
    ORDER BY CASE q.status WHEN 'in_hand' THEN 0 WHEN 'deposited' THEN 1 WHEN 'bounced' THEN 2 ELSE 3 END, q.cheque_date, q.id`,
    t, ...(clientId ? [clientId] : []), new Date(Date.now() - 60 * DAY).toISOString());
}
const ownCheque = (t: number, id: number) => {
  const q = get("SELECT * FROM wholesale_cheques WHERE id=? AND tenant_id=?", id, t);
  if (!q) throw new AppError(404, "Cheque not found");
  return q;
};

/** A cheque received (often post-dated). It is not a payment until it clears. */
wholesaleDesk.post("/wholesale/clients/:id/cheques", requireAny("wholesale.manage", "cheques.manage"), h((req) => {
  const t = tid(req);
  const c = own(t, Number(req.params.id));
  const b = parse(z.object({ amount: z.number().positive().max(1_000_000_000), bank: z.string().trim().min(2).max(80), cheque_no: z.string().trim().min(2).max(30),
    cheque_date: day, note: z.string().max(200).optional().nullable(), photo_ids: proofPhotos }), req.body);
  requireProof(t, b.photo_ids, "cheque");
  if (get("SELECT id FROM wholesale_cheques WHERE tenant_id=? AND bank=? AND cheque_no=? AND status<>'returned'", t, b.bank, b.cheque_no))
    throw new AppError(400, `Cheque ${b.cheque_no} of ${b.bank} is already in the register`);
  const { id } = run(`INSERT INTO wholesale_cheques (tenant_id,client_id,amount,bank,cheque_no,cheque_date,status,note,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)`,
    t, c.id, b.amount, b.bank, b.cheque_no, b.cheque_date, "in_hand", b.note ?? null, req.user!.name, now(), now());
  linkPhotos(t, b.photo_ids, `wchq:${id}`);
  return get("SELECT * FROM wholesale_cheques WHERE id=?", id);
}));

/** deposit → clear (the payment is entered, into the chosen bank account) / bounce / return to the client. */
wholesaleDesk.post("/wholesale/cheques/:id/:action", requireAny("wholesale.manage", "cheques.manage"), h(async (req) => {
  const t = tid(req);
  const q = ownCheque(t, Number(req.params.id));
  const action = parse(z.enum(["deposit", "clear", "bounce", "return"]), req.params.action);
  const b = parse(z.object({ account_id: z.number().int().positive().optional().nullable(), reason: z.string().max(200).optional().nullable(),
    charge: z.number().min(0).max(100_000).optional() }), req.body ?? {});
  const c = get("SELECT * FROM wholesale_clients WHERE id=?", q.client_id)!;
  const stamp = now();
  if (action === "deposit") {
    if (q.status !== "in_hand") throw new AppError(400, "Only a cheque in hand can be deposited");
    if (q.cheque_date > pkDate()) throw new AppError(400, `This cheque is dated ${q.cheque_date} — deposit it on or after that day`);
    run("UPDATE wholesale_cheques SET status='deposited', account_id=?, deposited_at=?, updated_at=? WHERE id=?", bankAccountFor(t, b.account_id ?? q.account_id, "cheque"), stamp, stamp, q.id);
  } else if (action === "clear") {
    if (!["in_hand", "deposited"].includes(q.status)) throw new AppError(400, "This cheque is already closed");
    const account = bankAccountFor(t, b.account_id ?? q.account_id, "cheque");
    tx(() => {
      const { id } = run(`INSERT INTO wholesale_txns (tenant_id,client_id,type,amount,method,ref,note,created_by,txn_date,created_at,account_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)`,
        t, q.client_id, "payment", q.amount, "Cheque", `CHQ ${q.cheque_no}`, `${q.bank} cheque ${q.cheque_no} dated ${q.cheque_date} — cleared`, req.user!.name, stamp, stamp, account);
      run("UPDATE wholesale_cheques SET status='cleared', account_id=?, payment_txn_id=?, cleared_at=?, updated_at=? WHERE id=?", account, id, stamp, stamp, q.id);
    });
  } else if (action === "bounce") {
    if (!["in_hand", "deposited", "cleared"].includes(q.status)) throw new AppError(400, "This cheque is already closed");
    tx(() => {
      // a cleared cheque that bounces takes its payment back off the account
      if (q.payment_txn_id) run("UPDATE wholesale_txns SET voided=1, void_reason=? WHERE id=?", `Cheque bounced${b.reason ? `: ${b.reason}` : ""} (by ${req.user!.name})`, q.payment_txn_id);
      if (b.charge) run(`INSERT INTO wholesale_txns (tenant_id,client_id,type,amount,note,created_by,txn_date,created_at) VALUES (?,?,?,?,?,?,?,?)`,
        t, q.client_id, "adjustment", b.charge, `Bounced cheque charges — ${q.bank} ${q.cheque_no}`, req.user!.name, stamp, stamp);
      run("UPDATE wholesale_cheques SET status='bounced', bounce_reason=?, updated_at=? WHERE id=?", b.reason ?? null, stamp, q.id);
    });
    createAlert(t, { type: "cheque_bounced", severity: "critical", title: `Cheque bounced — ${c.name} ${pkr(q.amount)}`,
      body: `${q.bank} cheque ${q.cheque_no} dated ${q.cheque_date}${b.reason ? `. Reason: ${b.reason}` : ""}. Due now ${pkr(clientDue(c.id))}.` });
    if (c.phone) await sendDirect(t, { phone: c.phone, name: c.name }, "cheque_bounced", `wchq:${q.id}`,
      `Assalam o Alaikum ${c.name}, aap ka ${q.bank} cheque # ${q.cheque_no} (${pkr(q.amount)}) bank se wapas aa gaya hai${b.reason ? ` (${b.reason})` : ""}. Meharbani kar ke payment jald ada karein. Baqaya: ${pkr(clientDue(c.id))}.`).catch(() => undefined);
  } else {
    if (q.status !== "in_hand") throw new AppError(400, "Only a cheque in hand can be given back");
    run("UPDATE wholesale_cheques SET status='returned', bounce_reason=?, updated_at=? WHERE id=?", b.reason ?? "Given back to the client", stamp, q.id);
  }
  return { cheque: get("SELECT * FROM wholesale_cheques WHERE id=?", q.id), due_after: clientDue(q.client_id) };
}));

/* ================= Call list: who to ask for money today ================= */
export function callList(t: number) {
  const nowMs = Date.now();
  const proms = promises(t);
  const chq = all("SELECT client_id, SUM(amount) v FROM wholesale_cheques WHERE tenant_id=? AND status IN ('in_hand','deposited') GROUP BY client_id", t);
  const bounced = all("SELECT client_id, COUNT(*) n FROM wholesale_cheques WHERE tenant_id=? AND status='bounced' AND updated_at >= ? GROUP BY client_id", t, new Date(nowMs - 90 * DAY).toISOString());
  const out: Row[] = [];
  for (const c of all("SELECT * FROM wholesale_clients WHERE tenant_id=? AND active=1", t)) {
    const due = clientDue(c.id);
    if (due <= 0) continue;
    const lastPay = get("SELECT MAX(txn_date) d FROM wholesale_txns WHERE client_id=? AND type='payment' AND voided=0", c.id)!.d;
    // age of the unpaid amount: the newest supplies make up the due (older bills count as paid first)
    let left = due, oldDays = 0;
    for (const x of all("SELECT amount, txn_date FROM wholesale_txns WHERE client_id=? AND type='supply' AND voided=0 ORDER BY txn_date DESC, id DESC", c.id)) {
      oldDays = Math.floor((nowMs - Date.parse(x.txn_date)) / DAY); left -= x.amount;
      if (left <= 0.01) break;
    }
    if (left > 0.01) oldDays = Math.max(oldDays, 61);
    const payDays = lastPay ? Math.floor((nowMs - Date.parse(lastPay)) / DAY) : null;
    const mine = proms.filter((p) => p.client_id === c.id);
    const inHand = round2(chq.find((x) => x.client_id === c.id)?.v ?? 0);
    const reasons: string[] = [];
    let score = 0;
    const broken = mine.find((p) => p.state === "broken"), todayP = mine.find((p) => p.state === "today"), openP = mine.find((p) => p.state === "open");
    if (broken) { score += 100; reasons.push(`Promised ${pkr(broken.amount)} on ${broken.promised_on} — not paid`); }
    if (todayP) { score += 90; reasons.push(`Promised ${pkr(todayP.amount - todayP.paid)} today`); }
    if (c.credit_limit > 0 && due >= c.credit_limit * 0.95) { score += 70; reasons.push(`At the credit limit (${Math.round((due / c.credit_limit) * 100)}%)`); }
    if (oldDays > 60) { score += 80; reasons.push(`Bills unpaid ${oldDays} days`); } else if (oldDays > 30) { score += 60; reasons.push(`Bills unpaid ${oldDays} days`); }
    if ((payDays == null || payDays >= 15) && due > 50_000) { score += 40; reasons.push(payDays == null ? "Never paid" : `No payment for ${payDays} days`); }
    if (bounced.find((x) => x.client_id === c.id)) { score += 30; reasons.push("A cheque bounced in the last 90 days"); }
    // a promise for later or cheques in hand covering the due: no need to chase today
    if (!broken && !todayP && (openP || inHand >= due - 1)) score = Math.min(score, 10);
    if (score < 30) continue;
    out.push({ client_id: c.id, name: c.name, phone: c.phone, city: c.city, due: round2(due), cheques_in_hand: inHand, last_payment_days: payDays, oldest_days: oldDays, score, reasons,
      promise: openP ? { amount: openP.amount, on: openP.promised_on } : null });
  }
  return out.sort((a, b) => b.score - a.score || b.due - a.due);
}

wholesaleDesk.get("/wholesale/collect", h((req) => {
  const t = tid(req), today = pkDate();
  const list = callList(t);
  const ch = cheques(t);
  return {
    calls: list, promises: promises(t), cheques: ch,
    totals: {
      to_call: list.length, to_collect: round2(list.reduce((a, c) => a + c.due, 0)),
      promised_today: round2(promises(t).filter((p) => p.state === "today").reduce((a, p) => a + p.amount - p.paid, 0)),
      cheques_in_hand: round2(ch.filter((q) => q.status === "in_hand").reduce((a, q) => a + q.amount, 0)),
      cheques_to_deposit: ch.filter((q) => q.status === "in_hand" && q.cheque_date <= today).length,
      cheques_deposited: round2(ch.filter((q) => q.status === "deposited").reduce((a, q) => a + q.amount, 0)),
    },
  };
}));

/** One client's open orders, promises and cheques (client page). */
wholesaleDesk.get("/wholesale/clients/:id/desk", h((req) => {
  const t = tid(req);
  const c = own(t, Number(req.params.id));
  return { orders: openOrders(t, c.id), promises: promises(t, c.id), cheques: cheques(t, c.id) };
}));

/* ================= Suggestions for the wholesale dashboard ================= */
type Sug = { level: "critical" | "warning" | "info" | "good"; title: string; ur?: string; detail: string; action?: { kind: string; client_id?: number; label: string } };
export function deskSuggestions(t: number, clients: { id: number; name: string; phone: string | null; due: number; credit_limit: number; limit_pct: number | null; oldest_days: number; month_l: number }[]): Sug[] {
  const sug: Sug[] = [];
  const today = pkDate(), nowMs = Date.now();
  // orders
  const open = openOrders(t);
  const late = open.filter((o) => o.needed_on < today), dueToday = open.filter((o) => o.needed_on === today);
  if (late.length) sug.push({ level: "critical", title: `${late.length} order${late.length > 1 ? "s" : ""} late — not delivered yet`, ur: `${late.length} آرڈر لیٹ ہیں — ابھی تک نہیں دیے`, detail: late.slice(0, 3).map((o) => `${o.client_name} ${Math.round(o.litres).toLocaleString()} L ${PRODUCTS[o.product]} (for ${o.needed_on})`).join(" · "), action: { kind: "orders", label: "Order book" } });
  if (dueToday.length) {
    const l = dueToday.reduce((a, o) => a + o.litres, 0);
    sug.push({ level: "warning", title: `Deliver today: ${dueToday.length} order${dueToday.length > 1 ? "s" : ""}, ${Math.round(l).toLocaleString()} L`, ur: `آج ${dueToday.length} آرڈر دینے ہیں، ${Math.round(l).toLocaleString()} لیٹر`, detail: `${dueToday.map((o) => o.client_name).slice(0, 4).join(", ")}. Put nearby drops on one tanker trip.`, action: { kind: "trip", label: "Plan a trip" } });
  }
  for (const p of Object.keys(PRODUCTS)) {
    const need = open.filter((o) => o.product === p && o.needed_on <= addDays(today, 2)).reduce((a, o) => a + o.litres, 0);
    const stock = get("SELECT COALESCE(SUM(t.current_l),0) l FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? AND t.product=?", t, p)!.l as number;
    if (need > 0 && need > stock * 0.7)
      sug.push({ level: need > stock ? "critical" : "warning", title: `${PRODUCTS[p]}: orders for 3 days need ${Math.round(need).toLocaleString()} L`, ur: `اگلے 3 دن کے آرڈر ${Math.round(need).toLocaleString()} لیٹر، ٹینک میں کم ہے — ڈپو سے ٹینکر منگوائیں`, detail: `Only ${Math.round(stock).toLocaleString()} L in tanks (pump sales need fuel too). Order a tanker from the depot now.`, action: { kind: "orders", label: "Order book" } });
  }
  // promises
  for (const p of promises(t)) {
    if (p.state === "broken" && p.promised_on >= addDays(today, -14))
      sug.push({ level: "critical", title: `${p.client_name} broke a promise: ${pkr(p.amount)} on ${p.promised_on}`, ur: `${p.client_name} نے ${p.promised_on} کو رقم دینے کا وعدہ پورا نہیں کیا — آج فون کریں`, detail: `Received ${pkr(p.paid)} since. Call today; hold the next supply until paid.`, action: { kind: "collect", label: "Call list" } });
    if (p.state === "today")
      sug.push({ level: "warning", title: `${p.client_name} promised ${pkr(p.amount - p.paid)} today`, ur: `${p.client_name} نے آج ${Math.round(p.amount - p.paid).toLocaleString()} روپے دینے کا وعدہ کیا ہے`, detail: "A friendly reminder in the morning gets the money the same day.", action: { kind: "statement", client_id: p.client_id, label: "WhatsApp statement" } });
  }
  // cheques
  const ch = cheques(t);
  const toDeposit = ch.filter((q) => q.status === "in_hand" && q.cheque_date <= today);
  if (toDeposit.length) sug.push({ level: "warning", title: `${toDeposit.length} cheque${toDeposit.length > 1 ? "s" : ""} ready to deposit — ${pkr(toDeposit.reduce((a, q) => a + q.amount, 0))}`, ur: `${toDeposit.length} چیک آج بینک میں جمع کروانے ہیں`, detail: toDeposit.slice(0, 3).map((q) => `${q.client_name} ${q.bank} #${q.cheque_no}`).join(" · ") + ". Deposit today so they clear by tomorrow.", action: { kind: "collect", label: "Cheques" } });
  const slow = ch.filter((q) => q.status === "deposited" && q.deposited_at && nowMs - Date.parse(q.deposited_at) > 3 * DAY);
  if (slow.length) sug.push({ level: "info", title: `${slow.length} deposited cheque${slow.length > 1 ? "s" : ""} not marked cleared after 3 days`, ur: "جمع کروائے چیک 3 دن سے کلیئر نہیں لکھے — بینک اسٹیٹمنٹ دیکھیں", detail: "Check the bank statement and mark each cleared or bounced, so the client's due is right.", action: { kind: "collect", label: "Cheques" } });
  const soon = ch.filter((q) => q.status === "in_hand" && q.cheque_date > today && q.cheque_date <= addDays(today, 3));
  if (soon.length) sug.push({ level: "info", title: `${soon.length} post-dated cheque${soon.length > 1 ? "s" : ""} due in the next 3 days`, ur: `${soon.length} چیک اگلے 3 دن میں جمع ہونے والے ہیں`, detail: soon.map((q) => `${q.client_name} ${pkr(q.amount)} on ${q.cheque_date}`).slice(0, 3).join(" · "), action: { kind: "collect", label: "Cheques" } });
  for (const r of all(`SELECT q.client_id, c.name, COUNT(*) n, SUM(q.amount) v FROM wholesale_cheques q JOIN wholesale_clients c ON c.id=q.client_id
      WHERE q.tenant_id=? AND q.status='bounced' AND q.updated_at >= ? GROUP BY q.client_id`, t, new Date(nowMs - 90 * DAY).toISOString()))
    sug.push({ level: "warning", title: `${r.name}: ${r.n} cheque${r.n > 1 ? "s" : ""} bounced in 90 days (${pkr(r.v)})`, ur: `${r.name} کا چیک باؤنس ہوا — صرف نقد یا آن لائن لیں`, detail: "Take cash / online transfer only, and keep the credit limit low until they are regular again.", action: { kind: "edit", client_id: r.client_id, label: "Credit limit" } });
  // volume drop vs the same days last month
  const monthStart = pkStart(today.slice(0, 7) + "-01");
  const lmStart = new Date(Date.parse(monthStart) - 1).toISOString().slice(0, 7);
  const lmFrom = pkStart(lmStart + "-01"), lmTo = new Date(Date.parse(lmFrom) + (nowMs - Date.parse(monthStart))).toISOString();
  for (const c of clients) {
    const last = get("SELECT COALESCE(SUM(litres),0) l FROM wholesale_txns WHERE client_id=? AND type='supply' AND voided=0 AND txn_date >= ? AND txn_date < ?", c.id, lmFrom, lmTo)!.l as number;
    if (last >= 5000 && c.month_l < last * 0.6)
      sug.push({ level: "info", title: `${c.name} is buying ${Math.round((1 - c.month_l / last) * 100)}% less than last month`, ur: `${c.name} پچھلے مہینے سے ${Math.round((1 - c.month_l / last) * 100)}% کم لے رہا ہے — وجہ پوچھیں`, detail: `${c.month_l.toLocaleString()} L so far vs ${Math.round(last).toLocaleString()} L by this date last month. Ask why — rate, service or a new supplier?`, action: { kind: "open", client_id: c.id, label: "Open client" } });
    // a regular payer pressing on the limit: room to grow
    if (c.limit_pct != null && c.limit_pct >= 75 && c.limit_pct < 95 && c.oldest_days <= 15) {
      const pays = get("SELECT COUNT(*) n FROM wholesale_txns WHERE client_id=? AND type='payment' AND voided=0 AND txn_date >= ?", c.id, new Date(nowMs - 60 * DAY).toISOString())!.n;
      if (pays >= 3) sug.push({ level: "good", title: `${c.name} pays on time and is at ${c.limit_pct}% of the limit`, ur: `${c.name} وقت پر ادائیگی کرتا ہے — ادھار حد بڑھا سکتے ہیں`, detail: `${pays} payments in 60 days, nothing older than 15 days. A higher limit (${pkr(Math.round((c.credit_limit * 1.25) / 10000) * 10000)}) can bring more orders.`, action: { kind: "edit", client_id: c.id, label: "Raise limit" } });
    }
    if (!c.phone && c.due > 0)
      sug.push({ level: "info", title: `${c.name}: no WhatsApp number`, ur: `${c.name} کا واٹس ایپ نمبر نہیں — نمبر ڈالیں`, detail: `Due ${pkr(c.due)}. Add the number so receipts, statements and reminders go by themselves.`, action: { kind: "edit", client_id: c.id, label: "Add number" } });
  }
  return sug;
}
