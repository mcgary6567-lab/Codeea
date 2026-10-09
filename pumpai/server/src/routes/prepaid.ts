/**
 * Prepaid fuel:
 *  - Coupons / vouchers with a QR (Rs 1,000, Rs 5,000…) sold in advance and used once at the POS
 *  - Company wallet: a fleet or company deposits money first (Raast / bank / cash); every fill is taken
 *    from it and they get a WhatsApp when it runs low
 */
import { Router } from "express";
import crypto from "node:crypto";
import { z } from "zod";
import { all, get, run, now, pkDate, tx, type Row } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { bankAccountFor, accountIdField, otherMoney } from "./banks.js";
import { AppError, round2, pkr, audit } from "../services.js";
import { productSchema } from "../products.js";
import { sendWhatsApp } from "../whatsapp/cloud.js";
import { PRODUCTS } from "../config.js";

export const prepaid = Router();
const dateStr = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const METHODS = ["cash", "bank", "raast", "easypaisa", "jazzcash", "cheque"] as const;

/* ================= Coupons ================= */
const newCode = () => `C${crypto.randomBytes(5).toString("hex").toUpperCase().slice(0, 9)}`;

export function couponSummary(t: number) {
  const s = get(`SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN status='active' THEN value END),0) outstanding, COALESCE(SUM(CASE WHEN status='active' THEN 1 END),0) active,
    COALESCE(SUM(CASE WHEN status='used' THEN value END),0) used_value, COALESCE(SUM(CASE WHEN status='used' THEN 1 END),0) used FROM fuel_coupons WHERE tenant_id=?`, t)!;
  return { ...s, outstanding: round2(s.outstanding), used_value: round2(s.used_value) };
}

prepaid.get("/coupons", requirePerm("khata.manage"), h((req) => {
  const t = tid(req);
  const batches = all(`SELECT batch, value, product, buyer, method, expires_on, sold_by, MIN(sold_at) sold_at, COUNT(*) n,
      SUM(CASE WHEN status='used' THEN 1 ELSE 0 END) used, SUM(CASE WHEN status='void' THEN 1 ELSE 0 END) void
    FROM fuel_coupons WHERE tenant_id=? GROUP BY batch ORDER BY MIN(id) DESC LIMIT 60`, t);
  return { summary: couponSummary(t), batches };
}));

prepaid.get("/coupons/batch/:batch", requirePerm("khata.manage"), h((req) => {
  const rows = all(`SELECT c.*, s.vehicle_no, st.name station_name FROM fuel_coupons c LEFT JOIN sales s ON s.id=c.sale_id LEFT JOIN stations st ON st.id=s.station_id
    WHERE c.tenant_id=? AND c.batch=? ORDER BY c.id`, tid(req), req.params.batch);
  if (!rows.length) throw new AppError(404, "Batch not found");
  return { batch: req.params.batch, tenant: get("SELECT name FROM tenants WHERE id=?", tid(req))?.name, coupons: rows };
}));

/** Sell a book of coupons: money is received now, fuel is given later against each coupon. */
prepaid.post("/coupons", requirePerm("khata.manage"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({
    count: z.number().int().min(1).max(500), value: z.number().min(100).max(1_000_000), product: productSchema().optional().nullable(),
    buyer: z.string().max(80).optional().nullable(), customer_id: z.number().optional().nullable(), method: z.enum(METHODS).default("cash"),
    expires_on: dateStr.optional().nullable(), account_id: accountIdField,
  }), req.body);
  const account = bankAccountFor(t, b.account_id, b.method);
  if (b.customer_id && !get("SELECT id FROM customers WHERE id=? AND tenant_id=?", b.customer_id, t)) throw new AppError(400, "Customer not found");
  const batch = `B${pkDate().replaceAll("-", "").slice(2)}-${crypto.randomBytes(2).toString("hex").toUpperCase()}`;
  const ts = now();
  tx(() => {
    for (let i = 0; i < b.count; i++) {
      let code = newCode();
      while (get("SELECT id FROM fuel_coupons WHERE code=?", code)) code = newCode();
      run(`INSERT INTO fuel_coupons (tenant_id,code,batch,value,product,buyer,customer_id,method,expires_on,sold_by,sold_at,account_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
        t, code, batch, b.value, b.product ?? null, b.buyer ?? null, b.customer_id ?? null, b.method, b.expires_on ?? null, req.user!.name, ts, account);
    }
  });
  audit(t, req.user!, "coupons_sold", batch, { count: b.count, value: b.value, buyer: b.buyer, method: b.method });
  return { batch, count: b.count, total: round2(b.count * b.value) };
}));

/** Cancel unused coupons (lost book, refund given). */
prepaid.post("/coupons/void", requirePerm("settings.manage"), h((req) => {
  const b = parse(z.object({ codes: z.array(z.string()).optional(), batch: z.string().optional(), reason: z.string().min(3).max(120),
    /** money given back for the cancelled coupons (none for a lost book) */
    refund: z.boolean().default(false), refund_method: z.string().max(30).optional().nullable(), account_id: accountIdField }), req.body);
  if (!b.codes?.length && !b.batch) throw new AppError(400, "Choose coupons or a batch");
  const where = b.batch ? "batch=?" : `code IN (${b.codes!.map(() => "?").join(",")})`;
  const keys = b.batch ? [b.batch] : b.codes!.map((c) => c.toUpperCase());
  const value = round2(get(`SELECT COALESCE(SUM(value),0) v FROM fuel_coupons WHERE tenant_id=? AND status='active' AND ${where}`, tid(req), ...keys)!.v);
  const r = run(`UPDATE fuel_coupons SET status='void', void_reason=? WHERE tenant_id=? AND status='active' AND ${where}`, b.reason, tid(req), ...keys);
  if (b.refund && value > 0)
    otherMoney(tid(req), { dir: "out", amount: value, method: b.refund_method || "cash", account_id: b.account_id, party: "Coupon refund", category: "Coupon refund", note: `${r.changes} coupons — ${b.reason}`, ref: `coupon-refund:${b.batch ?? keys[0]}`, by: req.user!.name });
  audit(tid(req), req.user!, "coupons_void", b.batch ?? b.codes!.join(","), { reason: b.reason, n: r.changes });
  return { voided: r.changes };
}));

/** POS: what is this coupon worth? */
prepaid.get("/pos/coupon/:code", requirePerm("sales.create"), h((req) => {
  const code = String(req.params.code).toUpperCase().replace(/^PUMPAI-/, "").trim();
  const c = get("SELECT code, value, product, status, expires_on, used_at FROM fuel_coupons WHERE tenant_id=? AND code=?", tid(req), code);
  if (!c) throw new AppError(404, "Coupon not found · کوپن نہیں ملا");
  if (c.status === "used") throw new AppError(400, `Already used on ${c.used_at.slice(0, 10)} · یہ کوپن استعمال ہو چکا ہے`);
  if (c.status === "void") throw new AppError(400, "This coupon was cancelled · یہ کوپن منسوخ ہے");
  if (c.expires_on && c.expires_on < pkDate()) throw new AppError(400, `Expired on ${c.expires_on} · میعاد ختم`);
  return c;
}));

/* ================= Wallet ================= */
const walletLow = (c: Row) => c.wallet_low ?? 10_000;

export function walletStatement(t: number, customerId: number, limit = 100) {
  return all("SELECT * FROM wallet_ledger WHERE tenant_id=? AND customer_id=? ORDER BY id DESC LIMIT ?", t, customerId, limit);
}

prepaid.get("/wallets", requirePerm("khata.manage"), h((req) => {
  const t = tid(req);
  const rows = all(`SELECT c.id, c.name, c.phone, c.type, c.wallet_balance, c.wallet_low,
      (SELECT MAX(created_at) FROM wallet_ledger w WHERE w.customer_id=c.id AND w.type='deposit') last_deposit,
      (SELECT COALESCE(SUM(amount),0) FROM wallet_ledger w WHERE w.customer_id=c.id AND w.type='fill' AND w.created_at >= ?) used_30d
    FROM customers c WHERE c.tenant_id=? AND (c.wallet_balance <> 0 OR EXISTS (SELECT 1 FROM wallet_ledger w WHERE w.customer_id=c.id)) ORDER BY c.name`,
  new Date(Date.now() - 30 * 86_400_000).toISOString(), t);
  return {
    total: round2(rows.reduce((a, r) => a + r.wallet_balance, 0)),
    wallets: rows.map((r) => ({ ...r, wallet_low: walletLow(r), low: r.wallet_balance < walletLow(r), days_left: r.used_30d > 0 ? Math.floor(r.wallet_balance / (r.used_30d / 30)) : null })),
  };
}));

prepaid.get("/customers/:id/wallet", requirePerm("khata.manage"), h((req) => {
  const c = get("SELECT id, name, phone, wallet_balance, wallet_low FROM customers WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  if (!c) throw new AppError(404, "Customer not found");
  return { ...c, wallet_low: walletLow(c), lines: walletStatement(tid(req), c.id) };
}));

/** Money received into the wallet; the company gets a WhatsApp with the new balance. */
prepaid.post("/customers/:id/wallet/deposit", requirePerm("khata.manage"), h(async (req) => {
  const t = tid(req);
  const c = get("SELECT * FROM customers WHERE id=? AND tenant_id=?", Number(req.params.id), t);
  if (!c) throw new AppError(404, "Customer not found");
  const b = parse(z.object({ amount: z.number().positive().max(100_000_000), method: z.enum(METHODS).default("raast"), ref: z.string().max(60).optional().nullable(),
    note: z.string().max(200).optional().nullable(), type: z.enum(["deposit", "refund", "adjustment"]).default("deposit"), account_id: accountIdField }), req.body);
  const accountId = bankAccountFor(t, b.account_id, b.method);
  // refund = money given back (balance goes down); adjustment can go either way via the sign of a deposit/refund
  const delta = b.type === "refund" ? -b.amount : b.amount;
  if (c.wallet_balance + delta < -0.005) throw new AppError(400, `Wallet has only ${pkr(c.wallet_balance)}`);
  tx(() => {
    run("INSERT INTO wallet_ledger (tenant_id,customer_id,type,amount,method,ref,note,created_by,created_at,account_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
      t, c.id, b.type, b.amount, b.method, b.ref ?? null, b.note ?? null, req.user!.name, now(), b.type === "adjustment" ? null : accountId);
    run("UPDATE customers SET wallet_balance = wallet_balance + ?, wallet_low_sent = CASE WHEN wallet_balance + ? >= COALESCE(wallet_low, 10000) THEN 0 ELSE wallet_low_sent END WHERE id=?", delta, delta, c.id);
  });
  const fresh = get("SELECT * FROM customers WHERE id=?", c.id)!;
  if (b.type === "deposit")
    await sendWhatsApp(t, fresh, `✅ ${pkr(b.amount)} aap ke fuel wallet mein jama ho gaye${b.ref ? ` (Ref ${b.ref})` : ""}.\nNaya balance: *${pkr(fresh.wallet_balance)}*\nShukriya!`, "system", { kind: "wallet_deposit" });
  audit(t, req.user!, `wallet_${b.type}`, `customer:${c.id}`, { amount: b.amount, method: b.method, ref: b.ref });
  return { wallet_balance: fresh.wallet_balance, lines: walletStatement(t, c.id, 20) };
}));

prepaid.patch("/customers/:id/wallet", requirePerm("khata.manage"), h((req) => {
  const b = parse(z.object({ wallet_low: z.number().min(0).max(10_000_000) }), req.body);
  run("UPDATE customers SET wallet_low=?, wallet_low_sent=0 WHERE id=? AND tenant_id=?", b.wallet_low, Number(req.params.id), tid(req));
  return get("SELECT id, wallet_balance, wallet_low FROM customers WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
}));

/** After a wallet fill: one WhatsApp when the balance first drops below the alert level. */
export async function walletAfterFill(t: number, sale: Row) {
  if (sale.payment_method !== "wallet" || !sale.customer_id) return;
  const c = get("SELECT * FROM customers WHERE id=?", sale.customer_id)!;
  const msg = `⛽ ${round2(sale.litres)} L ${sale.product}${sale.vehicle_no ? ` (${sale.vehicle_no})` : ""} — ${pkr(sale.amount)} wallet se.\nBaqi balance: *${pkr(c.wallet_balance)}*`;
  if (c.wallet_balance < walletLow(c) && !c.wallet_low_sent) {
    run("UPDATE customers SET wallet_low_sent=1 WHERE id=?", c.id);
    await sendWhatsApp(t, c, `${msg}\n\n⚠️ Wallet balance kam hai. Raast / bank transfer se top-up karein taa ke gaariyon ko fuel milta rahe.`, "system", { kind: "wallet_low" });
  }
}

/** POS: companies that pay from a wallet. */
prepaid.get("/pos/wallet-accounts", requirePerm("sales.create"), h((req) =>
  all("SELECT id, name, type, wallet_balance FROM customers WHERE tenant_id=? AND (wallet_balance > 0 OR EXISTS (SELECT 1 FROM wallet_ledger w WHERE w.customer_id=customers.id)) ORDER BY name", tid(req))));
