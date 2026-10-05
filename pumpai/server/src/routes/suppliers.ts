/** Supplier accounts (payables): fuel purchases from depots and our payments to them. */
import { Router } from "express";
import { z } from "zod";
import { recordWithholding, taxSettings } from "./tax.js";
import { all, get, run, now, tx } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { AppError, normalizePhone, round2 } from "../services.js";
import { announce } from "../notifications.js";

export const suppliers = Router();
suppliers.use("/suppliers", requirePerm("suppliers.manage"));

const OWED_SQL = `CASE type WHEN 'purchase' THEN amount WHEN 'payment' THEN -amount ELSE amount END`;

/** What we currently owe this supplier (positive = we owe them). */
export function supplierOwed(supplierId: number, before?: string): number {
  const s = get("SELECT opening_balance FROM suppliers WHERE id=?", supplierId)!;
  const r = get(`SELECT COALESCE(SUM(${OWED_SQL}),0) o FROM supplier_txns WHERE supplier_id=? ${before ? "AND txn_date < ?" : ""}`,
    ...(before ? [supplierId, before] : [supplierId]))!;
  return round2(s.opening_balance + r.o);
}

export function recordPurchase(tenantId: number, f: { supplier_id: number; delivery_id: number; product: string; litres: number; rate: number; ref?: string | null; by: string }) {
  run(`INSERT INTO supplier_txns (tenant_id,supplier_id,type,delivery_id,product,litres,rate,amount,ref,created_by,txn_date,created_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
    tenantId, f.supplier_id, "purchase", f.delivery_id, f.product, f.litres, f.rate, round2(f.litres * f.rate), f.ref ?? null, f.by, now(), now());
}

function own(tenantId: number, id: number) {
  const s = get("SELECT * FROM suppliers WHERE id=? AND tenant_id=?", id, tenantId);
  if (!s) throw new AppError(404, "Supplier not found");
  return s;
}

suppliers.get("/suppliers", h((req) => all("SELECT * FROM suppliers WHERE tenant_id=? ORDER BY active DESC, name", tid(req)).map((s) => ({
  ...s, owed: supplierOwed(s.id),
  ...get(`SELECT MAX(CASE WHEN type='purchase' THEN txn_date END) last_purchase, MAX(CASE WHEN type='payment' THEN txn_date END) last_payment,
      COALESCE(SUM(CASE WHEN type='purchase' AND txn_date >= ? THEN litres END),0) month_l FROM supplier_txns WHERE supplier_id=?`,
    new Date().toISOString().slice(0, 7) + "-01", s.id)!,
}))));

suppliers.post("/suppliers", h(async (req) => {
  const b = parse(z.object({ name: z.string().min(2), phone: z.string().optional().nullable(), opening_balance: z.number().optional(), notes: z.string().optional().nullable() }), req.body);
  const { id } = run("INSERT INTO suppliers (tenant_id,name,phone,opening_balance,notes,created_at) VALUES (?,?,?,?,?,?)",
    tid(req), b.name, b.phone ? normalizePhone(b.phone) : null, b.opening_balance ?? 0, b.notes ?? null, now());
  await announce(tid(req), req.user!.id, ["manager", "admin"], { type: "new_supplier", data: { supplier_id: id },
    title: `🏭 New supplier: ${b.name}`, body: `Tanker delivery par ab ye supplier chuna ja sakta hai.` });
  return { ...get("SELECT * FROM suppliers WHERE id=?", id)!, owed: supplierOwed(id) };
}));

suppliers.get("/suppliers/:id", h((req) => {
  const s = own(tid(req), Number(req.params.id));
  let bal = s.opening_balance;
  const lines = all("SELECT * FROM supplier_txns WHERE supplier_id=? ORDER BY txn_date, id", s.id).map((t) => {
    const effect = t.type === "payment" ? -t.amount : t.amount;
    bal = round2(bal + effect);
    return { ...t, debit: effect > 0 ? effect : 0, credit: effect < 0 ? -effect : 0, balance: bal };
  });
  return { ...s, owed: supplierOwed(s.id), lines: lines.reverse() };
}));

suppliers.post("/suppliers/:id/payment", h((req) => {
  const s = own(tid(req), Number(req.params.id));
  const b = parse(z.object({ amount: z.number().positive(), method: z.string().min(2), ref: z.string().optional().nullable(), note: z.string().optional().nullable(),
    txn_date: z.string().regex(/^\d{4}-\d{2}-\d{2}/).optional(),
    /** income tax withheld from this payment (paid to FBR instead of the supplier) */
    withholding: z.number().min(0).optional(), wht_section: z.string().max(30).optional().nullable() }), req.body);
  const ts = b.txn_date ? new Date(b.txn_date).toISOString() : now();
  const t = tid(req);
  tx(() => {
    run("INSERT INTO supplier_txns (tenant_id,supplier_id,type,amount,method,ref,note,created_by,txn_date,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
      t, s.id, "payment", b.amount, b.method, b.ref ?? null, b.note ?? null, req.user!.name, ts, now());
    if (b.withholding) {
      const w = run("INSERT INTO supplier_txns (tenant_id,supplier_id,type,amount,method,ref,note,created_by,txn_date,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        t, s.id, "payment", b.withholding, "WHT", b.ref ?? null, "Income tax withheld", req.user!.name, ts, now()).id;
      recordWithholding(t, { payee: s.name, supplier_id: s.id, supplier_txn_id: w, section: b.wht_section ?? taxSettings(t).wht_section, gross: round2(b.amount + b.withholding),
        rate: round2((b.withholding / (b.amount + b.withholding)) * 100), amount: b.withholding, note: b.ref ? `Payment ${b.ref}` : null, by: req.user!.name, date: ts });
    }
  });
  return { owed: supplierOwed(s.id) };
}));

suppliers.post("/suppliers/:id/adjustment", h((req) => {
  const s = own(tid(req), Number(req.params.id));
  const b = parse(z.object({ amount: z.number().refine((v) => v !== 0), note: z.string().min(3, "Give a reason") }), req.body);
  run("INSERT INTO supplier_txns (tenant_id,supplier_id,type,amount,note,created_by,txn_date,created_at) VALUES (?,?,?,?,?,?,?,?)",
    tid(req), s.id, "adjustment", b.amount, b.note, req.user!.name, now(), now());
  return { owed: supplierOwed(s.id) };
}));
