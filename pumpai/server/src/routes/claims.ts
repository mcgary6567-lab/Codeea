/**
 * Tanker money:
 *  - Shortage claims: every tanker that arrives short beyond the allowed transit loss becomes a claim
 *    on the supplier; follow it from claimed to recovered (credit note) or written off
 *  - Supplier / depot comparison: rate, freight and shortage per litre → landed cost, cheapest depot per fuel
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, now, pkDate, pkStart, pkEnd, getSetting, setSetting, tx } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { AppError, round2, pkr, audit } from "../services.js";
import { sendDirect } from "../whatsapp/cloud.js";
import { PRODUCTS } from "../config.js";

export const claims = Router();
const tolerance = (t: number) => Number(getSetting(t, "shortage_tolerance_pct", "0.2"));

/** Make a claim for a short tanker (beyond the allowed loss). Safe to call more than once. */
export function claimForDelivery(t: number, deliveryId: number) {
  if (get("SELECT id FROM shortage_claims WHERE delivery_id=?", deliveryId)) return null;
  const d = get(`SELECT d.*, t.product FROM deliveries d JOIN tanks t ON t.id=d.tank_id JOIN stations s ON s.id=t.station_id WHERE d.id=? AND s.tenant_id=?`, deliveryId, t);
  if (!d) return null;
  const allowed = (d.invoice_l * tolerance(t)) / 100;
  const litres = round2(d.invoice_l - d.received_l - allowed);
  if (litres < 1) return null;
  const rate = d.purchase_rate ?? get("SELECT rate FROM supplier_txns WHERE tenant_id=? AND type='purchase' AND product=? ORDER BY txn_date DESC LIMIT 1", t, d.product)?.rate ?? 0;
  const { id } = run("INSERT INTO shortage_claims (tenant_id,delivery_id,supplier_id,litres,rate,amount,created_at) VALUES (?,?,?,?,?,?,?)",
    t, d.id, d.supplier_id ?? null, litres, rate, round2(litres * rate), d.created_at);
  return id;
}
/** Catch up on deliveries that have no claim yet (older data, or the allowed loss was changed). */
export function scanClaims(t: number) {
  let n = 0;
  for (const d of all(`SELECT d.id FROM deliveries d JOIN tanks t ON t.id=d.tank_id JOIN stations s ON s.id=t.station_id
    WHERE s.tenant_id=? AND d.invoice_l > d.received_l AND NOT EXISTS (SELECT 1 FROM shortage_claims c WHERE c.delivery_id=d.id)`, t))
    if (claimForDelivery(t, d.id)) n++;
  return n;
}

claims.get("/claims", requirePerm("suppliers.manage"), h((req) => {
  const t = tid(req);
  scanClaims(t);
  const status = req.query.status ? String(req.query.status) : null;
  const rows = all(`SELECT c.*, d.tanker_no, d.invoice_l, d.received_l, d.shortage_pct, d.created_at delivered_at, tk.product, tk.name tank, st.name station, sp.name supplier_name, sp.phone supplier_phone
    FROM shortage_claims c JOIN deliveries d ON d.id=c.delivery_id JOIN tanks tk ON tk.id=d.tank_id JOIN stations st ON st.id=tk.station_id LEFT JOIN suppliers sp ON sp.id=c.supplier_id
    WHERE c.tenant_id=? ${status ? "AND c.status=?" : ""} ORDER BY d.created_at DESC LIMIT 300`, t, ...(status ? [status] : []));
  const sum = (f: (r: any) => number) => round2(rows.reduce((a, r) => a + f(r), 0));
  const all_ = all("SELECT status, amount, recovered FROM shortage_claims WHERE tenant_id=?", t);
  const tot = (s: string[]) => round2(all_.filter((r) => s.includes(r.status)).reduce((a, r) => a + r.amount - r.recovered, 0));
  return {
    tolerance_pct: tolerance(t),
    summary: {
      pending: tot(["open", "claimed", "partly"]), not_claimed: tot(["open"]), claimed: tot(["claimed", "partly"]),
      recovered: round2(all_.reduce((a, r) => a + r.recovered, 0)), written_off: tot(["written_off"]),
      litres: sum((r) => r.litres), n: rows.length,
    },
    by_supplier: all(`SELECT COALESCE(sp.name,'(no supplier)') supplier, COUNT(*) n, ROUND(SUM(c.litres),1) litres, ROUND(SUM(c.amount),0) amount, ROUND(SUM(c.recovered),0) recovered
      FROM shortage_claims c LEFT JOIN suppliers sp ON sp.id=c.supplier_id WHERE c.tenant_id=? GROUP BY c.supplier_id ORDER BY amount DESC`, t),
    claims: rows,
  };
}));

claims.put("/claims/settings", requirePerm("settings.manage"), h((req) => {
  const b = parse(z.object({ tolerance_pct: z.number().min(0).max(2) }), req.body);
  setSetting(tid(req), "shortage_tolerance_pct", String(b.tolerance_pct));
  return { tolerance_pct: b.tolerance_pct };
}));

/** Send the claim to the supplier (WhatsApp) and mark it claimed. */
claims.post("/claims/:id/claim", requirePerm("suppliers.manage"), h(async (req) => {
  const t = tid(req);
  const c = get(`SELECT c.*, d.tanker_no, d.invoice_l, d.received_l, d.created_at delivered_at, tk.product, sp.name supplier_name, sp.phone supplier_phone
    FROM shortage_claims c JOIN deliveries d ON d.id=c.delivery_id JOIN tanks tk ON tk.id=d.tank_id LEFT JOIN suppliers sp ON sp.id=c.supplier_id WHERE c.id=? AND c.tenant_id=?`, Number(req.params.id), t);
  if (!c) throw new AppError(404, "Claim not found");
  if (c.status !== "open") throw new AppError(400, `Claim is already ${c.status}`);
  const b = parse(z.object({ claim_ref: z.string().max(60).optional().nullable(), send: z.boolean().default(true) }), req.body);
  run("UPDATE shortage_claims SET status='claimed', claim_ref=?, claimed_on=?, updated_at=? WHERE id=?", b.claim_ref ?? null, pkDate(), now(), c.id);
  const tenant = get("SELECT name FROM tenants WHERE id=?", t)!.name;
  const text = `Shortage claim — ${tenant}\nTanker ${c.tanker_no ?? "-"} (${PRODUCTS[c.product] ?? c.product}) received ${c.delivered_at.slice(0, 10)}\nInvoice ${c.invoice_l.toLocaleString()} L, received ${c.received_l.toLocaleString()} L.\nClaim: ${c.litres} L × Rs ${c.rate} = *${pkr(c.amount)}*${b.claim_ref ? `\nRef ${b.claim_ref}` : ""}\nPlease issue a credit note.`;
  const sent = b.send && c.supplier_phone ? await sendDirect(t, { phone: c.supplier_phone, name: c.supplier_name }, "shortage_claim", `claim:${c.id}`, text) : null;
  audit(t, req.user!, "claim_sent", `claim:${c.id}`, { amount: c.amount });
  return { ok: true, sent: Boolean(sent), text };
}));

/** Money recovered (credit note reduces what we owe the supplier) or written off. */
claims.post("/claims/:id/settle", requirePerm("suppliers.manage"), h((req) => {
  const t = tid(req);
  const c = get("SELECT c.*, d.tanker_no FROM shortage_claims c JOIN deliveries d ON d.id=c.delivery_id WHERE c.id=? AND c.tenant_id=?", Number(req.params.id), t);
  if (!c) throw new AppError(404, "Claim not found");
  if (["recovered", "written_off"].includes(c.status)) throw new AppError(400, `Claim is already ${c.status.replace("_", " ")}`);
  const b = parse(z.object({ action: z.enum(["recovered", "written_off"]), amount: z.number().positive().optional(), method: z.enum(["credit_note", "cash", "bank"]).default("credit_note"), note: z.string().max(200).optional().nullable() }), req.body);
  if (b.action === "written_off") {
    run("UPDATE shortage_claims SET status='written_off', note=?, updated_at=? WHERE id=?", b.note ?? null, now(), c.id);
    return get("SELECT * FROM shortage_claims WHERE id=?", c.id);
  }
  const left = round2(c.amount - c.recovered);
  const amount = round2(Math.min(b.amount ?? left, left));
  tx(() => {
    run("UPDATE shortage_claims SET recovered=recovered+?, status=?, recovered_by=?, note=?, updated_at=? WHERE id=?",
      amount, amount >= left - 0.5 ? "recovered" : "partly", b.method, b.note ?? c.note, now(), c.id);
    // a credit note lowers what we owe the depot
    if (b.method === "credit_note" && c.supplier_id)
      run("INSERT INTO supplier_txns (tenant_id,supplier_id,type,amount,note,ref,created_by,txn_date,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        t, c.supplier_id, "adjustment", -amount, `Shortage credit note — tanker ${c.tanker_no ?? ""}`, `claim:${c.id}`, req.user!.name, now(), now());
  });
  return get("SELECT * FROM shortage_claims WHERE id=?", c.id);
}));

/* ================= Supplier / depot comparison ================= */
export function supplierComparison(t: number, from: string, to: string) {
  const rows = all(`SELECT sp.id, sp.name, tk.product, COUNT(d.id) tankers, SUM(d.invoice_l) invoice_l, SUM(d.received_l) received_l,
      SUM(d.invoice_l * d.purchase_rate) cost, SUM(COALESCE(d.freight,0)) freight, SUM(CASE WHEN d.freight IS NOT NULL THEN d.received_l ELSE 0 END) freight_l,
      COALESCE((SELECT SUM(c.recovered) FROM shortage_claims c JOIN deliveries d2 ON d2.id=c.delivery_id JOIN tanks t2 ON t2.id=d2.tank_id
        WHERE c.supplier_id=sp.id AND t2.product=tk.product AND d2.created_at >= ? AND d2.created_at < ?),0) recovered
    FROM deliveries d JOIN tanks tk ON tk.id=d.tank_id JOIN suppliers sp ON sp.id=d.supplier_id
    WHERE sp.tenant_id=? AND d.purchase_rate IS NOT NULL AND d.created_at >= ? AND d.created_at < ? GROUP BY sp.id, tk.product ORDER BY tk.product, sp.name`, from, to, t, from, to);
  const out = rows.map((r) => {
    const rate = r.cost / r.invoice_l;
    const freightPerL = r.freight_l ? r.freight / r.freight_l : 0;
    const shortPct = ((r.invoice_l - r.received_l) / r.invoice_l) * 100;
    // landed cost per litre actually in the tank: (paid − recovered + freight) / received
    const landed = (r.cost - r.recovered + r.freight) / r.received_l;
    return { supplier_id: r.id, supplier: r.name, product: r.product, tankers: r.tankers, litres: Math.round(r.received_l), avg_rate: round2(rate), freight_per_l: round2(freightPerL),
      shortage_pct: round2(shortPct), shortage_cost_per_l: round2(landed - rate - freightPerL), landed_per_l: round2(landed) };
  });
  const best = Object.fromEntries(Object.keys(PRODUCTS).map((p) => {
    const list = out.filter((o) => o.product === p).sort((a, b) => a.landed_per_l - b.landed_per_l);
    return [p, list.length > 1 ? { supplier: list[0].supplier, saving_per_l: round2(list[list.length - 1].landed_per_l - list[0].landed_per_l) } : null];
  }));
  return { rows: out, best };
}
claims.get("/suppliers-compare", requirePerm("suppliers.manage"), h((req) => {
  const days = Math.min(365, Number(req.query.days ?? 90) || 90);
  const from = req.query.from ? pkStart(String(req.query.from)) : new Date(Date.now() - days * 86_400_000).toISOString();
  const to = req.query.to ? pkEnd(String(req.query.to)) : new Date().toISOString();
  return { from, to, ...supplierComparison(tid(req), from, to) };
}));
