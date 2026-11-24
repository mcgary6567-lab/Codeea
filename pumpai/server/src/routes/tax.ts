/**
 * Tax:
 *  - Sales tax (GST) on shop / lubricant sales, shown on the receipt with the NTN / STRN
 *  - Withholding tax deducted when paying suppliers (and other payees), with the CPR when deposited
 *  - Monthly tax report with Excel export
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, now, pkDate, pkStart, getSetting, setSetting } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { otherMoney, cardFees } from "./banks.js";
import { AppError, round2 } from "../services.js";
import { lookupKeys } from "./lookups.js";
import { PRODUCTS } from "../config.js";

export const tax = Router();

export function taxSettings(t: number) {
  let exempt: string[] = [];
  try { exempt = JSON.parse(getSetting(t, "gst_exempt", "[]")); } catch { /* none */ }
  return {
    gst_pct: Number(getSetting(t, "gst_pct", "18")), prices_include_tax: getSetting(t, "gst_inclusive", "1") === "1", exempt,
    fuel_gst_pct: Number(getSetting(t, "fuel_gst_pct", "0")), ntn: getSetting(t, "ntn"), strn: getSetting(t, "strn"),
    wht_pct: Number(getSetting(t, "wht_pct", "0")), wht_section: getSetting(t, "wht_section", "153(1)(a)"),
  };
}
tax.get("/tax/settings", requirePerm("reports.view"), h((req) => ({ ...taxSettings(tid(req)), categories: lookupKeys(tid(req), "shop_category") })));
tax.put("/tax/settings", requirePerm("settings.manage"), h((req) => {
  const b = parse(z.object({
    gst_pct: z.number().min(0).max(30).optional(), prices_include_tax: z.boolean().optional(), exempt: z.array(z.string().max(40)).optional(),
    fuel_gst_pct: z.number().min(0).max(30).optional(), ntn: z.string().max(20).optional(), strn: z.string().max(20).optional(),
    wht_pct: z.number().min(0).max(20).optional(), wht_section: z.string().max(30).optional(),
  }), req.body);
  const t = tid(req);
  if (b.gst_pct !== undefined) setSetting(t, "gst_pct", String(b.gst_pct));
  if (b.prices_include_tax !== undefined) setSetting(t, "gst_inclusive", b.prices_include_tax ? "1" : "0");
  if (b.exempt) setSetting(t, "gst_exempt", JSON.stringify(b.exempt));
  if (b.fuel_gst_pct !== undefined) setSetting(t, "fuel_gst_pct", String(b.fuel_gst_pct));
  if (b.ntn !== undefined) setSetting(t, "ntn", b.ntn);
  if (b.strn !== undefined) setSetting(t, "strn", b.strn);
  if (b.wht_pct !== undefined) setSetting(t, "wht_pct", String(b.wht_pct));
  if (b.wht_section !== undefined) setSetting(t, "wht_section", b.wht_section);
  return taxSettings(t);
}));

/** Tax inside (or on top of) an amount. */
export function splitTax(amount: number, pct: number, inclusive: boolean) {
  if (!pct) return { value: round2(amount), tax: 0, total: round2(amount) };
  return inclusive ? { value: round2(amount / (1 + pct / 100)), tax: round2(amount - amount / (1 + pct / 100)), total: round2(amount) }
    : { value: round2(amount), tax: round2((amount * pct) / 100), total: round2(amount * (1 + pct / 100)) };
}

/** Lines for a shop receipt: taxable value and sales tax per sale. */
export function shopSaleTax(t: number, saleId: number) {
  const s = taxSettings(t);
  const lines = all("SELECT l.qty, l.price, i.category FROM shop_sale_lines l JOIN shop_items i ON i.id=l.item_id WHERE l.sale_id=?", saleId);
  const taxed = lines.filter((l) => !s.exempt.includes(l.category)).reduce((a, l) => a + l.qty * l.price, 0);
  return { ...splitTax(taxed, s.gst_pct, s.prices_include_tax), pct: s.gst_pct, inclusive: s.prices_include_tax, ntn: s.ntn, strn: s.strn };
}

/* ================= Withholding ================= */
export function recordWithholding(t: number, w: { payee: string; supplier_id?: number | null; supplier_txn_id?: number | null; section?: string | null; gross: number; rate?: number | null; amount: number; note?: string | null; by: string; date?: string }) {
  return run(`INSERT INTO tax_withholdings (tenant_id,payee,supplier_id,supplier_txn_id,section,gross,rate,amount,note,created_by,txn_date,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
    t, w.payee, w.supplier_id ?? null, w.supplier_txn_id ?? null, w.section ?? null, w.gross, w.rate ?? null, w.amount, w.note ?? null, w.by, w.date ?? now(), now()).id;
}
tax.post("/tax/withholding", requirePerm("expenses.create"), h((req) => {
  const b = parse(z.object({ payee: z.string().min(2).max(80), gross: z.number().positive(), rate: z.number().min(0).max(30).optional().nullable(), amount: z.number().positive().optional(),
    section: z.string().max(30).optional().nullable(), note: z.string().max(200).optional().nullable(), date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional() }), req.body);
  const amount = b.amount ?? (b.rate ? round2((b.gross * b.rate) / 100) : 0);
  if (!amount) throw new AppError(400, "Enter the tax amount or rate");
  const id = recordWithholding(tid(req), { ...b, amount, by: req.user!.name, date: b.date ? pkStart(b.date) : undefined });
  return get("SELECT * FROM tax_withholdings WHERE id=?", id);
}));
tax.post("/tax/withholding/deposit", requirePerm("expenses.create"), h((req) => {
  const b = parse(z.object({ ids: z.array(z.number()).min(1), cpr_no: z.string().min(3).max(40), deposited_on: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
    /** how the tax was paid to FBR (cash at the bank counter, or from an account) */
    method: z.string().max(30).optional().nullable(), account_id: z.number().int().positive().optional().nullable() }), req.body);
  const t = tid(req);
  const open = all(`SELECT * FROM tax_withholdings WHERE tenant_id=? AND cpr_no IS NULL AND id IN (${b.ids.map(() => "?").join(",")})`, t, ...b.ids);
  const total = round2(open.reduce((a, w) => a + w.amount, 0));
  const method = b.method || (b.account_id ? "bank" : "cash");
  for (const w of open) run("UPDATE tax_withholdings SET cpr_no=?, deposited_on=?, paid_method=?, paid_account_id=? WHERE id=?", b.cpr_no, b.deposited_on ?? pkDate(), method, b.account_id ?? null, w.id);
  // the tax leaves the cash / bank: the books show it and the "withholding tax payable" is cleared
  if (total > 0) otherMoney(t, { dir: "out", amount: total, method, account_id: b.account_id, party: "FBR", category: "Withholding tax deposited", note: `CPR ${b.cpr_no}`, ref: `wht:${b.cpr_no}`, by: req.user!.name });
  return { updated: open.length, amount: total };
}));

/* ================= Monthly report ================= */
const monthRange = (m: string) => {
  const [y, mo] = m.split("-").map(Number);
  const next = mo === 12 ? `${y + 1}-01` : `${y}-${String(mo + 1).padStart(2, "0")}`;
  return { from: pkStart(`${m}-01`), to: pkStart(`${next}-01`) };
};
export function taxReport(t: number, month: string) {
  const s = taxSettings(t);
  const { from, to } = monthRange(month);
  const shop = all(`SELECT i.category, SUM(l.qty * l.price) amount FROM shop_sale_lines l JOIN shop_items i ON i.id=l.item_id JOIN shop_sales ss ON ss.id=l.sale_id
    WHERE ss.tenant_id=? AND ss.created_at >= ? AND ss.created_at < ? GROUP BY i.category ORDER BY amount DESC`, t, from, to)
    .map((r) => { const ex = s.exempt.includes(r.category); return { category: r.category, sales: round2(r.amount), exempt: ex, ...(ex ? { value: round2(r.amount), tax: 0 } : splitTax(r.amount, s.gst_pct, s.prices_include_tax)) }; });
  const fuel = all(`SELECT s.product, SUM(s.litres) litres, SUM(s.amount) amount FROM sales s JOIN stations st ON st.id=s.station_id WHERE st.tenant_id=? AND s.created_at >= ? AND s.created_at < ? GROUP BY s.product`, t, from, to)
    .map((r) => ({ product: r.product, name: PRODUCTS[r.product] ?? r.product, litres: round2(r.litres), sales: round2(r.amount), ...splitTax(r.amount, s.fuel_gst_pct, true) }));
  const wht = all("SELECT * FROM tax_withholdings WHERE tenant_id=? AND txn_date >= ? AND txn_date < ? ORDER BY txn_date", t, from, to);
  const sum = <T,>(a: T[], f: (x: T) => number) => round2(a.reduce((x, y) => x + f(y), 0));
  // Bank card/POS merchant fee (MDR) the bank kept on this month's card sales
  const feeRows = cardFees(t).filter((f) => f.day.slice(0, 7) === month);
  const card_fee = { sales: sum(feeRows, (x) => x.sales), fee: sum(feeRows, (x) => x.fee), pct: feeRows[0]?.pct ?? 0 };
  return {
    month, settings: s, card_fee,
    shop, shop_total: { sales: sum(shop, (x) => x.sales), value: sum(shop, (x) => x.value), tax: sum(shop, (x) => x.tax) },
    fuel, fuel_total: { sales: sum(fuel, (x) => x.sales), tax: sum(fuel, (x) => x.tax) },
    withholding: wht, wht_total: { amount: sum(wht, (x) => x.amount), deposited: sum(wht.filter((x) => x.cpr_no), (x) => x.amount), pending: sum(wht.filter((x) => !x.cpr_no), (x) => x.amount) },
    // all withholding not yet deposited, any month
    wht_pending_all: get("SELECT COALESCE(SUM(amount),0) v, COUNT(*) n FROM tax_withholdings WHERE tenant_id=? AND cpr_no IS NULL", t),
  };
}
tax.get("/tax/report", requirePerm("reports.view"), h((req) => taxReport(tid(req), String(req.query.month ?? pkDate().slice(0, 7)))));
tax.get("/tax/report.csv", requirePerm("reports.view"), (req, res, next) => {
  try {
    const r = taxReport(tid(req), String(req.query.month ?? pkDate().slice(0, 7)));
    const esc = (v: unknown) => `"${String(v ?? "").replaceAll('"', '""')}"`;
    const rows: unknown[][] = [[`Tax report ${r.month}`], [], ["Sales tax on shop sales", `GST ${r.settings.gst_pct}%`, r.settings.prices_include_tax ? "prices include tax" : "tax added on price"],
      ["Category", "Sales", "Taxable value", "Sales tax", "Exempt"], ...r.shop.map((x) => [x.category, x.sales, x.value, x.tax, x.exempt ? "yes" : ""]),
      ["Total", r.shop_total.sales, r.shop_total.value, r.shop_total.tax], [], ["Fuel sales", "Litres", "Sales", "Tax inside price"],
      ...r.fuel.map((x) => [x.name, x.litres, x.sales, x.tax]), [], ["Withholding tax", "Section", "Gross paid", "Rate %", "Tax", "CPR", "Deposited on", "Date"],
      ...r.withholding.map((x) => [x.payee, x.section, x.gross, x.rate, x.amount, x.cpr_no, x.deposited_on, String(x.txn_date).slice(0, 10)]),
      ["Total", "", "", "", r.wht_total.amount, `pending ${r.wht_total.pending}`]];
    res.setHeader("Content-Type", "text/csv; charset=utf-8");
    res.setHeader("Content-Disposition", `attachment; filename="tax-${r.month}.csv"`);
    res.send("﻿" + rows.map((row) => row.map(esc).join(",")).join("\n"));
  } catch (e) { next(e); }
});
