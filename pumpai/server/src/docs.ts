/**
 * Documents as PDFs + "send on WhatsApp".
 * One renderer per document kind (voucher, khata statement, wholesale / carriage / supplier statement, shift report,
 * sale receipt) drawn with the in-house Pdf writer on the pump's letterhead. Every PDF has a signed public link
 * (/doc/<token>.pdf) so it can be downloaded, printed, or sent as a WhatsApp document (Meta Cloud API) — or, with no
 * API configured, opened in the phone's WhatsApp (wa.me) with the link. Every send is recorded in `sent_docs`.
 */
import jwt from "jsonwebtoken";
import { all, get, run, now, pkStart, pkEnd, type Row } from "./db.js";
import { config } from "./config.js";
import { Pdf } from "./pdf.js";
import { brandLines } from "./brandPrint.js";
import { logoJpeg } from "./routes/setup.js";
import { khataStatement } from "./routes/crm.js";
import { statement as wholesaleStatement } from "./routes/wholesale.js";
import { statement as carriageStatement } from "./routes/carriage.js";
import { supplierOwed } from "./routes/suppliers.js";
import { shiftReport } from "./shifts.js";
import { PRODUCTS } from "./config.js";
import { AppError, round2, normalizePhone } from "./services.js";
import { sendDocumentToPhone, sendWhatsApp } from "./whatsapp/cloud.js";
import { slipUrl } from "./routes/people.js";

export const DOC_KINDS = ["voucher", "khata", "wholesale", "carriage", "supplier", "shift", "receipt", "slip"] as const;
export type DocKind = (typeof DOC_KINDS)[number];
export type DocOpts = { from?: string; to?: string };
export type Doc = { title: string; filename: string; pdf?: Buffer; party: { phone: string | null; name: string; customer_id?: number } | null; caption: string; link?: string };

const money = (n: number) => `Rs ${Math.round(n ?? 0).toLocaleString("en-PK")}`;
const dateOf = (iso: string) => new Date(iso).toLocaleDateString("en-PK", { timeZone: "Asia/Karachi", day: "2-digit", month: "short", year: "numeric" });
const dtOf = (iso: string) => new Date(iso).toLocaleString("en-PK", { timeZone: "Asia/Karachi", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
const L = 40, R = Pdf.W - 40;

/** A4 page with the letterhead strip; returns the y where content starts and a helper to start a new page. */
function page(t: number, title: string, sub: string, right: string, right2 = "") {
  const tenant = get("SELECT name FROM tenants WHERE id=?", t)!.name as string;
  const brand = brandLines(t), logo = logoJpeg(t);
  const pdf = new Pdf();
  const head = () => {
    pdf.rect(0, 0, Pdf.W, 54, 0.16);
    const lx = logo ? L + 50 : L;
    if (logo) { pdf.rect(L - 3, 7, 44, 40, 1); pdf.image(logo, L, 9, 38, 36); }
    pdf.text(lx, 25, tenant, 15, { bold: true, gray: 1 }).text(lx, 41, sub, 9, { gray: 0.85 })
      .text(R, 25, title, 12, { bold: true, align: "right", gray: 1 });
    if (right) pdf.text(R, 41, right, 8.5, { align: "right", gray: 0.85 });
    if (brand.contact) { pdf.rect(0, 54, Pdf.W, 15, 0.93); pdf.text(Pdf.W / 2, 64.5, brand.contact, 7.5, { align: "center", gray: 0.25 }); }
    if (right2) pdf.text(R, 84, right2, 8.5, { align: "right", gray: 0.4 });
    return 86;
  };
  const foot = (n: number) => {
    const f = [brand.foot, brand.social].filter(Boolean).join("  ·  ");
    pdf.line(L, Pdf.H - 34, R, Pdf.H - 34, 0.5, 0.7);
    if (f) pdf.text(Pdf.W / 2, Pdf.H - 22, f, 7.5, { align: "center", gray: 0.4 });
    pdf.text(R, Pdf.H - 12, `Page ${n}`, 7, { align: "right", gray: 0.5 });
    if (brand.note) pdf.text(L, Pdf.H - 12, brand.note, 7, { gray: 0.4 });
  };
  let y = head(), n = 1;
  const api = {
    pdf, get y() { return y; }, set y(v: number) { y = v; },
    /** make room for h points, page-breaking if needed */
    need(h: number) { if (y + h > Pdf.H - 44) { foot(n); pdf.newPage(); n++; y = head(); return true; } return false; },
    done(name: string) { foot(n); return pdf.build(name); },
  };
  return api;
}
type Col = { k: string; w: number; align?: "left" | "right"; label: string };
/** A table that flows over pages: header row repeated, zebra rows, numbers right-aligned. */
function table(pg: ReturnType<typeof page>, cols: Col[], rows: Record<string, unknown>[], opts: { size?: number } = {}) {
  const size = opts.size ?? 8.5, rh = size + 6;
  const total = cols.reduce((a, c) => a + c.w, 0), scale = (R - L) / total;
  const xs: number[] = []; let x = L; for (const c of cols) { xs.push(x); x += c.w * scale; }
  const header = () => {
    pg.pdf.rect(L, pg.y, R - L, rh, 0.88);
    cols.forEach((c, i) => pg.pdf.text(c.align === "right" ? xs[i] + c.w * scale - 3 : xs[i] + 3, pg.y + rh - 4.5, c.label, size, { bold: true, align: c.align === "right" ? "right" : "left" }));
    pg.y += rh;
  };
  header();
  rows.forEach((r, ri) => {
    if (pg.need(rh)) header();
    if (ri % 2) pg.pdf.rect(L, pg.y, R - L, rh, 0.965);
    if (r.__bold) pg.pdf.line(L, pg.y, R, pg.y, 0.8, 0.2);
    cols.forEach((c, i) => {
      const v = r[c.k]; if (v == null || v === "") return;
      const maxW = c.w * scale - 6; let s = String(v);
      while (s.length > 2 && Pdf.width(s, size, Boolean(r.__bold)) > maxW) s = s.slice(0, -2);
      pg.pdf.text(c.align === "right" ? xs[i] + c.w * scale - 3 : xs[i] + 3, pg.y + rh - 4.5, s, size, { bold: Boolean(r.__bold), align: c.align === "right" ? "right" : "left" });
    });
    pg.y += rh;
  });
  pg.pdf.line(L, pg.y, R, pg.y, 0.6, 0.5);
  pg.y += 6;
}
function kv(pg: ReturnType<typeof page>, pairs: [string, string][], colW = 3) {
  const w = (R - L) / colW;
  pairs.forEach((p, i) => {
    const col = i % colW; if (col === 0) { pg.need(28); }
    const x = L + col * w, y = pg.y;
    pg.pdf.text(x, y + 9, p[0], 7.5, { gray: 0.45 }).text(x, y + 21, p[1], 10, { bold: true });
    if (col === colW - 1 || i === pairs.length - 1) pg.y += 30;
  });
}
const sig = (pg: ReturnType<typeof page>, labels: string[]) => {
  pg.need(50); pg.y += 28;
  const w = (R - L) / labels.length;
  labels.forEach((l, i) => { const x = L + i * w + 14; pg.pdf.line(x, pg.y, x + w - 28, pg.y, 0.6, 0.3).text(x + (w - 28) / 2, pg.y + 11, l, 8, { align: "center", gray: 0.4 }); });
  pg.y += 24;
};
const period = (o: DocOpts) => o.from || o.to ? `${o.from ? dateOf(pkStart(o.from)) : "start"} – ${o.to ? dateOf(pkStart(o.to)) : "today"}` : "All entries";
const dirLabel = (t: string) => t === "supply" ? "Fuel supplied" : t === "return" ? "Returned" : t === "payment" ? "Payment received" : t === "adjustment" ? "Adjustment" : t === "carriage" ? "Kiraya billed" : t === "purchase" ? "Purchase" : t === "fuel_note" ? "Fuel note" : t;

/* ---------------- renderers ---------------- */
function voucherDoc(t: number, id: number): Doc {
  const v = get("SELECT * FROM cashier_vouchers WHERE id=? AND tenant_id=?", id, t);
  if (!v) throw new AppError(404, "Voucher not found");
  const isIn = v.direction === "in";
  const no = `${isIn ? "RV" : "PV"}-${String(v.id).padStart(5, "0")}`;
  const acc = v.account_id ? get("SELECT bank, title, account_no FROM bank_accounts WHERE id=?", v.account_id) : null;
  const party = partyOf(t, v.party_type, v.party_id, v.party_name);
  const pg = page(t, isIn ? "Receipt voucher" : "Payment voucher", `${no} · ${dtOf(v.created_at)}`, v.voided ? "VOID" : "");
  pg.pdf.rect(L, pg.y, R - L, 44, isIn ? 0.9 : 0.93);
  pg.pdf.text(Pdf.W / 2, pg.y + 20, money(v.amount), 20, { bold: true, align: "center" }).text(Pdf.W / 2, pg.y + 36, `${isIn ? "Received from" : "Paid to"}: ${v.party_name}`, 10, { align: "center" });
  pg.y += 56;
  const pairs: [string, string][] = [["Method", `${v.method}${acc ? ` · ${acc.bank}${acc.title ? ` ${acc.title}` : ""}` : ""}`], ["Voucher no.", no], ["Date", dtOf(v.created_at)]];
  if (v.category) pairs.push(["For", v.category]);
  if (v.ref) pairs.push(["Reference", v.ref]);
  if (v.note) pairs.push(["Note", v.note]);
  pairs.push(["Prepared by", v.created_by ?? "—"]);
  if (party?.balance != null) pairs.push([v.party_type === "supplier" ? "We still owe" : v.party_type === "staff" ? "Total advance" : "Balance after", money(party.balance)]);
  kv(pg, pairs);
  let notes: Record<string, number> | null = null; try { notes = v.notes_json ? JSON.parse(v.notes_json) : null; } catch { notes = null; }
  if (notes && Object.values(notes).some((n) => n > 0)) {
    pg.need(20); pg.pdf.text(L, pg.y + 10, "Cash notes", 9, { bold: true }); pg.y += 14;
    table(pg, [{ k: "d", w: 2, label: "Note" }, { k: "n", w: 1, label: "×", align: "right" }, { k: "v", w: 2, label: "Amount", align: "right" }],
      Object.entries(notes).filter(([, n]) => n > 0).sort((a, b) => Number(b[0]) - Number(a[0])).map(([d, n]) => ({ d: `Rs ${Number(d).toLocaleString("en-PK")}`, n, v: money(Number(d) * n) })));
  }
  if (v.voided) { pg.need(20); pg.pdf.text(L, pg.y + 10, `VOIDED${v.void_reason ? ` — ${v.void_reason}` : ""}`, 10, { bold: true }); pg.y += 16; }
  sig(pg, ["Cashier", isIn ? "Paid by" : "Received by"]);
  return { title: `${isIn ? "Receipt" : "Payment"} voucher ${no}`, filename: `${no}.pdf`, pdf: pg.done(no), party: party ? { phone: party.phone, name: v.party_name, customer_id: party.customer_id } : null,
    caption: `${isIn ? "Receipt" : "Payment"} voucher ${no} — ${money(v.amount)} ${isIn ? "received from" : "paid to"} ${v.party_name}` };
}
function partyOf(t: number, type: string, id: number | null, name: string): { phone: string | null; balance?: number | null; customer_id?: number } | null {
  if (!id) return null;
  if (type === "khata") { const c = get("SELECT phone, balance FROM customers WHERE id=? AND tenant_id=?", id, t); return c ? { phone: c.phone, balance: c.balance, customer_id: id } : null; }
  if (type === "wholesale") { const c = get("SELECT phone FROM wholesale_clients WHERE id=? AND tenant_id=?", id, t); return c ? { phone: c.phone } : null; }
  if (type === "carriage" || type === "thekedar") { const k = get("SELECT phone FROM thekedars WHERE id=? AND tenant_id=?", id, t); return k ? { phone: k.phone } : null; }
  if (type === "supplier") { const s = get("SELECT phone FROM suppliers WHERE id=? AND tenant_id=?", id, t); return s ? { phone: s.phone, balance: supplierOwed(id) } : null; }
  if (type === "staff") { const u = get("SELECT phone FROM users WHERE id=? AND tenant_id=?", id, t); return u ? { phone: u.phone } : null; }
  return { phone: null };
}

function khataDoc(t: number, id: number, o: DocOpts): Doc {
  const s = khataStatement(t, id, o.from, o.to);
  const c = s.customer;
  const pg = page(t, "Khata statement", `${c.name}${c.phone ? ` · ${c.phone}` : ""}${c.city ? ` · ${c.city}` : ""}`, period(o), `Credit limit ${money(c.credit_limit)}`);
  kv(pg, [["Opening balance", money(s.opening_balance)], ["Fuel / charges", money(s.totals.charged)], ["Payments", money(s.totals.paid)], [s.closing_balance < -0.5 ? "Closing — advance with us" : "Closing balance (owes)", money(Math.abs(s.closing_balance))]], 4);
  table(pg, [{ k: "d", w: 2.2, label: "Date" }, { k: "p", w: 4.2, label: "Particulars" }, { k: "l", w: 1.3, label: "Litres", align: "right" }, { k: "r", w: 1.2, label: "Rate", align: "right" }, { k: "dr", w: 1.6, label: "Debit", align: "right" }, { k: "cr", w: 1.6, label: "Credit", align: "right" }, { k: "b", w: 1.8, label: "Balance", align: "right" }],
    s.lines.map((l: any) => ({ d: dateOf(l.created_at), p: l.type === "debit" ? `${l.product ? (PRODUCTS[l.product] ?? l.product) : "Charge"}${l.vehicle_no ? ` · ${l.vehicle_no}` : ""}${l.slip_no ? ` · slip ${l.slip_no}` : ""}${!l.product && l.note ? ` · ${l.note}` : ""}` : `Payment${l.ref ? ` · ${l.ref}` : ""}${l.note ? ` · ${l.note}` : ""}`,
      l: l.litres ? l.litres.toFixed(2) : "", r: l.rate ? l.rate.toFixed(2) : "", dr: l.type === "debit" ? money(l.amount) : "", cr: l.type === "credit" ? money(l.amount) : "", b: money(l.balance) }))
      .concat([{ __bold: true, p: "Total", dr: money(s.totals.charged), cr: money(s.totals.paid), b: money(s.closing_balance) } as any]));
  if (s.totals.by_product.length) {
    pg.need(16); pg.pdf.text(L, pg.y + 10, "By fuel", 9, { bold: true }); pg.y += 14;
    table(pg, [{ k: "p", w: 3, label: "Fuel" }, { k: "n", w: 1, label: "Fills", align: "right" }, { k: "l", w: 2, label: "Litres", align: "right" }, { k: "a", w: 2, label: "Amount", align: "right" }], s.totals.by_product.map((p: any) => ({ p: PRODUCTS[p.product] ?? p.product, n: p.entries, l: p.litres.toFixed(2), a: money(p.amount) })));
  }
  sig(pg, ["Prepared by", "Customer"]);
  return { title: `Khata statement — ${c.name}`, filename: `khata-${c.name.replace(/[^\w-]+/g, "_")}.pdf`, pdf: pg.done(`Khata statement ${c.name}`), party: { phone: c.phone, name: c.name, customer_id: c.id },
    caption: `${c.name}: khata statement (${period(o)}). Balance ${money(s.closing_balance)}.` };
}
function wholesaleDoc(t: number, id: number, o: DocOpts): Doc {
  const s = wholesaleStatement(t, id, o.from, o.to);
  const c = s.client;
  const pg = page(t, "Account statement", `${c.name}${c.business_name ? ` · ${c.business_name}` : ""}${c.phone ? ` · ${c.phone}` : ""}`, period(o));
  kv(pg, [["Opening balance", money(s.opening_balance)], ["Billed", money(s.lines.reduce((a: number, l: any) => a + l.debit, 0))], ["Received / returned", money(s.lines.reduce((a: number, l: any) => a + l.credit, 0))], [s.closing_balance < -0.5 ? "Closing — advance with us" : "Closing balance (due)", money(Math.abs(s.closing_balance))]], 4);
  table(pg, [{ k: "d", w: 2.2, label: "Date" }, { k: "p", w: 4.4, label: "Particulars" }, { k: "l", w: 1.4, label: "Litres", align: "right" }, { k: "r", w: 1.2, label: "Rate", align: "right" }, { k: "dr", w: 1.6, label: "Debit", align: "right" }, { k: "cr", w: 1.6, label: "Credit", align: "right" }, { k: "b", w: 1.8, label: "Balance", align: "right" }],
    s.lines.filter((l: any) => l.type !== "fuel_note").map((l: any) => ({ d: dateOf(l.txn_date), p: `${l.voided ? "VOID · " : ""}${dirLabel(l.type)}${l.product ? ` ${PRODUCTS[l.product] ?? l.product}` : ""}${l.vehicle_no ? ` · ${l.vehicle_no}` : ""}${l.method ? ` · ${l.method}` : ""}${l.ref ? ` · ${l.ref}` : ""}`,
      l: l.litres ? Number(l.litres).toFixed(0) : "", r: l.rate ? Number(l.rate).toFixed(2) : "", dr: l.debit ? money(l.debit) : "", cr: l.credit ? money(l.credit) : "", b: money(l.balance) }))
      .concat([{ __bold: true, p: "Total", dr: money(s.lines.reduce((a: number, l: any) => a + l.debit, 0)), cr: money(s.lines.reduce((a: number, l: any) => a + l.credit, 0)), b: money(s.closing_balance) } as any]));
  sig(pg, ["Prepared by", "Client"]);
  return { title: `Statement — ${c.name}`, filename: `statement-${c.name.replace(/[^\w-]+/g, "_")}.pdf`, pdf: pg.done(`Statement ${c.name}`), party: { phone: c.phone, name: c.name },
    caption: `${c.name}: account statement (${period(o)}). Due ${money(s.closing_balance)}.` };
}
function carriageDoc(t: number, id: number, o: DocOpts): Doc {
  const s = carriageStatement(t, id, o.from, o.to);
  const k = s.thekedar;
  const pg = page(t, "Carriage statement", `${k.name}${k.phone ? ` · ${k.phone}` : ""}${k.city ? ` · ${k.city}` : ""}`, period(o));
  kv(pg, [["Opening", money(s.opening_balance)], ["Kiraya billed", money(s.lines.reduce((a: number, l: any) => a + l.debit, 0))], ["Paid", money(s.lines.reduce((a: number, l: any) => a + l.credit, 0))], ["Closing (due)", money(s.closing_balance)]], 4);
  table(pg, [{ k: "d", w: 2.2, label: "Date" }, { k: "p", w: 5, label: "Particulars" }, { k: "l", w: 1.4, label: "Litres", align: "right" }, { k: "dr", w: 1.7, label: "Debit", align: "right" }, { k: "cr", w: 1.7, label: "Credit", align: "right" }, { k: "b", w: 1.8, label: "Balance", align: "right" }],
    s.lines.filter((l: any) => l.type !== "fuel_note").map((l: any) => ({ d: dateOf(l.txn_date), p: `${l.voided ? "VOID · " : ""}${dirLabel(l.type)}${l.product ? ` ${PRODUCTS[l.product] ?? l.product}` : ""}${l.depot_name ? ` · ${l.depot_name}` : ""}${l.vehicle_no ? ` · ${l.vehicle_no}` : ""}${l.method ? ` · ${l.method}` : ""}${l.ref ? ` · ${l.ref}` : ""}`,
      l: l.litres ? Number(l.litres).toFixed(0) : "", dr: l.debit ? money(l.debit) : "", cr: l.credit ? money(l.credit) : "", b: money(l.balance) }))
      .concat([{ __bold: true, p: "Total", dr: money(s.lines.reduce((a: number, l: any) => a + l.debit, 0)), cr: money(s.lines.reduce((a: number, l: any) => a + l.credit, 0)), b: money(s.closing_balance) } as any]));
  sig(pg, ["Prepared by", "Thekedar"]);
  return { title: `Carriage statement — ${k.name}`, filename: `carriage-${k.name.replace(/[^\w-]+/g, "_")}.pdf`, pdf: pg.done(`Carriage ${k.name}`), party: { phone: k.phone, name: k.name },
    caption: `${k.name}: kiraya statement (${period(o)}). Due ${money(s.closing_balance)}.` };
}
function supplierDoc(t: number, id: number, o: DocOpts): Doc {
  const s = get("SELECT * FROM suppliers WHERE id=? AND tenant_id=?", id, t);
  if (!s) throw new AppError(404, "Supplier not found");
  let bal = s.opening_balance as number;
  const rows = all(`SELECT * FROM supplier_txns WHERE supplier_id=? ${o.from ? "AND txn_date >= ?" : ""} ${o.to ? "AND txn_date < ?" : ""} ORDER BY txn_date, id`, ...[s.id, ...(o.from ? [pkStart(o.from)] : []), ...(o.to ? [pkEnd(o.to)] : [])]);
  if (o.from) bal = round2(s.opening_balance + (get(`SELECT COALESCE(SUM(CASE type WHEN 'payment' THEN -amount ELSE amount END),0) v FROM supplier_txns WHERE supplier_id=? AND txn_date < ?`, s.id, pkStart(o.from))!.v as number));
  const opening = bal;
  const lines = rows.map((x) => { const e = x.type === "payment" ? -x.amount : x.amount; bal = round2(bal + e); return { ...x, debit: e > 0 ? e : 0, credit: e < 0 ? -e : 0, balance: bal }; });
  const pg = page(t, "Supplier statement", `${s.name}${s.company ? ` · ${s.company}` : ""}${s.phone ? ` · ${s.phone}` : ""}`, period(o));
  kv(pg, [["Opening (we owe)", money(opening)], ["Purchases", money(lines.reduce((a, l) => a + l.debit, 0))], ["Paid", money(lines.reduce((a, l) => a + l.credit, 0))], ["Closing (we owe)", money(o.to ? bal : supplierOwed(s.id))]], 4);
  table(pg, [{ k: "d", w: 2.2, label: "Date" }, { k: "p", w: 4.6, label: "Particulars" }, { k: "l", w: 1.4, label: "Litres", align: "right" }, { k: "r", w: 1.2, label: "Rate", align: "right" }, { k: "dr", w: 1.6, label: "Purchase", align: "right" }, { k: "cr", w: 1.6, label: "Paid", align: "right" }, { k: "b", w: 1.8, label: "Balance", align: "right" }],
    lines.map((l) => ({ d: dateOf(l.txn_date), p: `${dirLabel(l.type)}${l.product ? ` ${PRODUCTS[l.product] ?? l.product}` : ""}${l.method ? ` · ${l.method}` : ""}${l.ref ? ` · ${l.ref}` : ""}${l.note ? ` · ${l.note}` : ""}`,
      l: l.litres ? Number(l.litres).toFixed(0) : "", r: l.rate ? Number(l.rate).toFixed(2) : "", dr: l.debit ? money(l.debit) : "", cr: l.credit ? money(l.credit) : "", b: money(l.balance) }))
      .concat([{ __bold: true, p: "Total", dr: money(lines.reduce((a, l) => a + l.debit, 0)), cr: money(lines.reduce((a, l) => a + l.credit, 0)), b: money(o.to ? bal : supplierOwed(s.id)) } as any]));
  sig(pg, ["Prepared by", "Supplier (confirmed)"]);
  return { title: `Supplier statement — ${s.name}`, filename: `supplier-${s.name.replace(/[^\w-]+/g, "_")}.pdf`, pdf: pg.done(`Supplier ${s.name}`), party: { phone: s.phone, name: s.name },
    caption: `${s.name}: account statement (${period(o)}). We owe ${money(supplierOwed(s.id))}.` };
}
function shiftDoc(t: number, id: number): Doc {
  const sh = get("SELECT sh.*, s.name station_name, s.tenant_id FROM shifts sh JOIN stations s ON s.id=sh.station_id WHERE sh.id=?", id);
  if (!sh || sh.tenant_id !== t) throw new AppError(404, "Shift not found");
  const r = shiftReport(id);
  const sm = r.summary;
  const pg = page(t, `Shift report #${id}`, `${sh.attendant} · ${sh.station_name}`, `${dtOf(sh.opened_at)}${sh.closed_at ? ` → ${dtOf(sh.closed_at)}` : " (open)"}`);
  kv(pg, [["Sales", money(sm.amount)], ["Litres", `${Math.round(sm.litres).toLocaleString("en-PK")} L`], ["Cash expected", money(sh.cash_expected ?? sm.cash_expected)], ["Cash counted", sh.cash_actual != null ? money(sh.cash_actual) : "—"], ["Short / over", sh.variance != null ? money(sh.variance) : "—"], ["Khata", money(sm.khata)], ["Online / card", money(sm.digital)], ["Expenses from bag", money(sm.expenses_total)], ["Direct discounts", money(sm.direct_discount ?? 0)]], 3);
  pg.pdf.text(L, pg.y + 10, "Meters", 9, { bold: true }); pg.y += 14;
  table(pg, [{ k: "m", w: 3, label: "Nozzle" }, { k: "p", w: 2, label: "Fuel" }, { k: "o", w: 2, label: "Opening", align: "right" }, { k: "c", w: 2, label: "Closing", align: "right" }, { k: "t", w: 1.5, label: "Test L", align: "right" }, { k: "l", w: 2, label: "Litres", align: "right" }, { k: "a", w: 2, label: "Amount", align: "right" }],
    r.readings.map((x: any) => ({ m: x.label, p: PRODUCTS[x.product] ?? x.product, o: x.opening?.toFixed(2), c: x.closing != null ? x.closing.toFixed(2) : "—", t: x.test_l ? x.test_l : "", l: x.litres != null ? x.litres.toFixed(2) : "—", a: x.amount != null ? money(x.amount) : "" })));
  pg.pdf.text(L, pg.y + 10, "By payment", 9, { bold: true }); pg.y += 14;
  table(pg, [{ k: "m", w: 3, label: "Method" }, { k: "n", w: 1, label: "Sales", align: "right" }, { k: "l", w: 2, label: "Litres", align: "right" }, { k: "a", w: 2, label: "Amount", align: "right" }], sm.by_payment.map((p: any) => ({ m: p.method, n: p.txns, l: p.litres?.toFixed(2), a: money(p.amount) })));
  if (r.khata.length) { pg.pdf.text(L, pg.y + 10, "Khata slips", 9, { bold: true }); pg.y += 14; table(pg, [{ k: "c", w: 4, label: "Customer" }, { k: "n", w: 1, label: "Slips", align: "right" }, { k: "l", w: 2, label: "Litres", align: "right" }, { k: "a", w: 2, label: "Amount", align: "right" }, { k: "s", w: 3, label: "Slip nos." }], r.khata.map((k: any) => ({ c: k.name, n: k.slips, l: k.litres?.toFixed(2), a: money(k.amount), s: k.slip_nos.join(", ") }))); }
  if (sm.expenses.length) { pg.pdf.text(L, pg.y + 10, "Expenses from the bag", 9, { bold: true }); pg.y += 14; table(pg, [{ k: "c", w: 3, label: "Category" }, { k: "n", w: 5, label: "Note" }, { k: "a", w: 2, label: "Amount", align: "right" }], sm.expenses.map((e: any) => ({ c: e.category, n: [e.paid_to, e.note].filter(Boolean).join(" · "), a: money(e.amount) }))); }
  sig(pg, ["Salesman", "Cashier / Manager"]);
  const att = get("SELECT phone FROM users WHERE tenant_id=? AND name=? AND role='salesman'", t, sh.attendant);
  return { title: `Shift report #${id} — ${sh.attendant}`, filename: `shift-${id}.pdf`, pdf: pg.done(`Shift ${id}`), party: { phone: att?.phone ?? null, name: sh.attendant },
    caption: `Shift #${id} (${sh.attendant}, ${sh.station_name}): sales ${money(sm.amount)}, cash expected ${money(sh.cash_expected ?? sm.cash_expected)}${sh.cash_actual != null ? `, counted ${money(sh.cash_actual)}` : ""}.` };
}
function receiptDoc(t: number, id: number): Doc {
  const s = get("SELECT s.*, st.name station_name, st.tenant_id, c.name customer_name, c.phone customer_phone FROM sales s JOIN stations st ON st.id=s.station_id LEFT JOIN customers c ON c.id=s.customer_id WHERE s.id=?", id);
  if (!s || s.tenant_id !== t) throw new AppError(404, "Sale not found");
  const gross = round2(s.litres * s.rate);
  const pg = page(t, "Fuel receipt", `${s.station_name} · ${dtOf(s.created_at)}`, `Receipt #F-${s.id}`);
  table(pg, [{ k: "p", w: 5, label: "Item" }, { k: "q", w: 2, label: "Litres", align: "right" }, { k: "r", w: 2, label: "Rate", align: "right" }, { k: "a", w: 2, label: "Amount", align: "right" }],
    [{ p: PRODUCTS[s.product] ?? s.product, q: s.litres.toFixed(2), r: s.rate.toFixed(2), a: money(gross) }, ...(s.discount > 0 ? [{ p: `${s.discount_kind === "direct" ? "Direct discount" : "Discount"}${s.discount_ref ? ` (${s.discount_ref})` : ""}`, a: `- ${money(s.discount)}` }] : []), { p: "TOTAL", a: money(s.amount), __bold: true }]);
  kv(pg, [["Paid by", `${s.payment_method}${s.customer_name ? ` — ${s.customer_name}` : ""}`], ["Vehicle", s.vehicle_no ?? "—"], ["Slip", s.slip_no ?? "—"]]);
  pg.pdf.text(Pdf.W / 2, pg.y + 12, "Shukriya! Thank you for fuelling with us.", 9, { align: "center", gray: 0.4 });
  return { title: `Receipt #F-${s.id}`, filename: `receipt-F${s.id}.pdf`, pdf: pg.done(`Receipt F-${s.id}`), party: s.customer_id ? { phone: s.customer_phone, name: s.customer_name, customer_id: s.customer_id } : null,
    caption: `Fuel receipt #F-${s.id}: ${s.litres.toFixed(2)} L ${PRODUCTS[s.product] ?? s.product} = ${money(s.amount)} (${s.payment_method}).` };
}
function slipDoc(t: number, id: number): Doc {
  const s = get("SELECT s.id, s.month, u.name, u.phone FROM salary_slips s JOIN users u ON u.id=s.user_id WHERE s.id=? AND s.tenant_id=?", id, t);
  if (!s) throw new AppError(404, "Salary slip not found");
  return { title: `Salary slip ${s.month} — ${s.name}`, filename: `salary-slip-${s.month}.pdf`, link: slipUrl(t, s.id), party: { phone: s.phone, name: s.name }, caption: `${s.name}: salary slip for ${s.month}.` };
}

export function renderDoc(t: number, kind: DocKind, id: number, o: DocOpts = {}): Doc {
  switch (kind) {
    case "voucher": return voucherDoc(t, id);
    case "khata": return khataDoc(t, id, o);
    case "wholesale": return wholesaleDoc(t, id, o);
    case "carriage": return carriageDoc(t, id, o);
    case "supplier": return supplierDoc(t, id, o);
    case "shift": return shiftDoc(t, id);
    case "receipt": return receiptDoc(t, id);
    case "slip": return slipDoc(t, id);
  }
}
/** Signed public link to the PDF (valid a year) — what WhatsApp sends and what "Download PDF" opens. */
export function docLink(t: number, kind: DocKind, id: number, o: DocOpts = {}) {
  if (kind === "slip") return renderDoc(t, kind, id).link!;
  const days = Number(get("SELECT value FROM settings WHERE tenant_id=? AND key='doc_link_days'", t)?.value ?? 365) || 365;
  return `${config.publicUrl}/doc/${jwt.sign({ d: kind, t, id, f: o.from ?? null, to: o.to ?? null }, config.jwtSecret, { expiresIn: `${days}d` })}.pdf`;
}
export function docFromToken(token: string): Doc | null {
  let p: { d: DocKind; t: number; id: number; f?: string | null; to?: string | null };
  try { p = jwt.verify(token.replace(/\.pdf$/, ""), config.jwtSecret) as typeof p; } catch { return null; }
  if (!(DOC_KINDS as readonly string[]).includes(p.d) || typeof p.id !== "number") return null;
  try { return renderDoc(p.t, p.d, p.id, { from: p.f ?? undefined, to: p.to ?? undefined }); } catch { return null; }
}

/** Who this document would usually go to: the party, the pump's own people (owner, managers, cashiers…), recent numbers. */
export function docContacts(t: number, doc: Doc, kind: DocKind, id: number) {
  const staff = all("SELECT id, name, role, phone FROM users WHERE tenant_id=? AND active=1 AND phone IS NOT NULL AND phone<>'' ORDER BY CASE role WHEN 'admin' THEN 0 WHEN 'manager' THEN 1 WHEN 'cashier' THEN 2 ELSE 3 END, name", t)
    .map((u) => ({ phone: u.phone, name: u.name, role: u.role }));
  const owner = get("SELECT owner_name, owner_phone FROM tenants WHERE id=?", t);
  if (owner?.owner_phone && !staff.some((s) => s.phone === owner.owner_phone)) staff.unshift({ phone: owner.owner_phone, name: owner.owner_name ?? "Owner", role: "owner" });
  const recent = all("SELECT to_phone phone, MAX(to_name) name, MAX(created_at) at FROM sent_docs WHERE tenant_id=? GROUP BY to_phone ORDER BY at DESC LIMIT 6", t).map((r) => ({ phone: r.phone, name: r.name }));
  const sent = all("SELECT to_phone, to_name, channel, ok, sent_by, created_at FROM sent_docs WHERE tenant_id=? AND kind=? AND ref_id=? ORDER BY id DESC LIMIT 10", t, kind, String(id));
  return { title: doc.title, caption: doc.caption, party: doc.party?.phone ? { phone: doc.party.phone, name: doc.party.name } : null, party_name: doc.party?.name ?? null, staff, recent, sent, api: Boolean(config.wa.token && config.wa.phoneNumberId) };
}

/** Send to one or more numbers. With the Cloud API the PDF goes as a document; without it we return wa.me links to open. */
export async function sendDoc(t: number, kind: DocKind, id: number, doc: Doc, link: string, phones: string[], note: string | undefined, by: { name: string }) {
  const caption = `${doc.caption}${note ? `\n${note}` : ""}`;
  const live = Boolean(config.wa.token && config.wa.phoneNumberId);
  const results: { phone: string; name: string | null; ok: boolean; channel: "api" | "phone"; error?: string; wa_link?: string }[] = [];
  for (const raw of phones) {
    let phone: string; try { phone = normalizePhone(raw); } catch { phone = ""; }
    if (!/^92\d{10}$/.test(phone)) { results.push({ phone: raw, name: null, ok: false, channel: live ? "api" : "phone", error: "Number is not valid" }); continue; }
    const known = get("SELECT name FROM users WHERE tenant_id=? AND phone=?", t, phone)?.name ?? (doc.party?.phone === phone ? doc.party.name : null);
    if (live) {
      const r = await sendDocumentToPhone(phone, link, doc.filename, caption);
      // a khata customer's own conversation keeps a copy
      if (r.ok && doc.party?.customer_id && doc.party.phone === phone) await sendWhatsApp(t, get("SELECT * FROM customers WHERE id=?", doc.party.customer_id)!, `📄 ${doc.title}\n${link}`, "system", { kind: "document", doc: kind, id }).catch(() => undefined);
      run("INSERT INTO sent_docs (tenant_id,kind,ref_id,title,to_phone,to_name,link,channel,ok,error,sent_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", t, kind, String(id), doc.title, phone, known, link, "api", r.ok ? 1 : 0, r.error ?? null, by.name, now());
      results.push({ phone, name: known, ok: r.ok, channel: "api", error: r.error });
    } else {
      const wa = `https://wa.me/${phone}?text=${encodeURIComponent(`${caption}\n${link}`)}`;
      run("INSERT INTO sent_docs (tenant_id,kind,ref_id,title,to_phone,to_name,link,channel,ok,error,sent_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", t, kind, String(id), doc.title, phone, known, link, "phone", 1, null, by.name, now());
      results.push({ phone, name: known, ok: true, channel: "phone", wa_link: wa });
    }
  }
  return { results, link, mode: live ? "api" : "phone" as const };
}
