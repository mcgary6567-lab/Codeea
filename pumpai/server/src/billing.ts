/**
 * Automatic customer paperwork, so no one writes a khata register by hand:
 *  - a WhatsApp receipt for every khata fill and every wholesale supply / payment
 *  - a monthly bill (khata) or statement (wholesale) as a private, printable link, sent on the 1st
 * Bill links are signed, so they open without a login but cannot be guessed.
 */
import { BRAND_CSS, brandHead, brandFoot } from "./brandPrint.js";
import jwt from "jsonwebtoken";
import { logoTag } from "./routes/setup.js";
import { shopSaleTax } from "./routes/tax.js";
import { config, PRODUCTS } from "./config.js";
import { all, get, getSetting, pkDate, type Row } from "./db.js";
import { paymentLink, pkr } from "./services.js";
import { sendWhatsApp, sendDirect } from "./whatsapp/cloud.js";
import { khataStatement } from "./routes/crm.js";
import { statement as wholesaleStatement, clientDue } from "./routes/wholesale.js";
import { portalLink } from "./routes/customerCare.js";
import { pinOf } from "./routes/pinPortal.js";

type Kind = "k" | "w"; // khata customer | wholesale client
const on = (tenantId: number, key: string) => getSetting(tenantId, key, "1") !== "0";

/* ---------------- Months ---------------- */
export const prevMonth = (ms = Date.now()) => { const d = new Date(Date.parse(pkDate(ms) + "T00:00:00Z")); d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() - 1); return d.toISOString().slice(0, 7); };
const monthRange = (m: string) => {
  const last = new Date(Date.UTC(Number(m.slice(0, 4)), Number(m.slice(5, 7)), 0)).getUTCDate();
  return { from: `${m}-01`, to: `${m}-${String(last).padStart(2, "0")}` };
};
const monthName = (m: string) => new Date(`${m}-15T00:00:00Z`).toLocaleDateString("en-PK", { month: "long", year: "numeric" });

/* ---------------- Signed links ---------------- */
export const billToken = (tenantId: number, kind: Kind, id: number, month: string) =>
  jwt.sign({ bill: kind, t: tenantId, id, m: month }, config.jwtSecret, { expiresIn: "400d" });
export const billLink = (tenantId: number, kind: Kind, id: number, month: string) => `${config.publicUrl}/bill/${billToken(tenantId, kind, id, month)}`;

/* ---------------- Receipts ---------------- */
export async function khataFillReceipt(tenantId: number, sale: Row) {
  if (!on(tenantId, "khata_receipts") || sale.payment_method !== "khata" || !sale.customer_id) return;
  const c = get("SELECT * FROM customers WHERE id=? AND tenant_id=?", sale.customer_id, tenantId);
  if (!c?.phone) return;
  const st = get("SELECT name FROM stations WHERE id=?", sale.station_id);
  const text = [
    `⛽ ${st?.name ?? "Fuel"} — ${new Date(sale.created_at).toLocaleString("en-PK", { timeZone: "Asia/Karachi", dateStyle: "medium", timeStyle: "short" })}`,
    `${PRODUCTS[sale.product]}: ${sale.litres} L × Rs ${sale.rate} = ${pkr(sale.amount)}`,
    [sale.vehicle_no && `Gaari ${sale.vehicle_no}`, sale.slip_no && `Slip ${sale.slip_no}`].filter(Boolean).join(" · "),
    `📒 Khata balance: ${pkr(c.balance)}${c.credit_limit ? ` (limit ${pkr(c.credit_limit)})` : ""}`,
    "Shukriya! 🙏",
  ].filter(Boolean).join("\n");
  await sendWhatsApp(tenantId, c, text, "system", { kind: "khata_receipt", sale_id: sale.id });
}

export async function wholesaleReceipt(tenantId: number, clientId: number, txn: Row) {
  if (!on(tenantId, "wholesale_messages")) return;
  const c = get("SELECT * FROM wholesale_clients WHERE id=? AND tenant_id=?", clientId, tenantId);
  if (!c?.phone) return;
  const due = clientDue(c.id);
  const head: Record<string, string> = {
    supply: `🚛 Supply: ${txn.litres} L ${PRODUCTS[txn.product]} × Rs ${txn.rate} = ${pkr(txn.amount)}${txn.vehicle_no ? ` · gaari ${txn.vehicle_no}` : ""}${txn.driver_name ? ` · driver ${txn.driver_name}` : ""}${txn.location ? ` · ${txn.location}` : ""}`,
    payment: `✅ Payment received: ${pkr(txn.amount)}${txn.method ? ` (${txn.method})` : ""}${txn.ref ? ` · ref ${txn.ref}` : ""}`,
    return: `↩️ Fuel return: ${txn.litres} L ${PRODUCTS[txn.product]} = ${pkr(txn.amount)} credited`,
  };
  if (!head[txn.type]) return;
  await sendDirect(tenantId, c, `wholesale_${txn.type}`, `wtxn:${txn.id}`,
    `${c.name}\n${head[txn.type]}\nBalance due now: ${pkr(due)}\n— ${get("SELECT name FROM tenants WHERE id=?", tenantId)!.name}`);
}

export async function wholesaleRateMessage(tenantId: number, clientId: number, lines: string[]) {
  if (!lines.length || !on(tenantId, "wholesale_messages")) return;
  const c = get("SELECT * FROM wholesale_clients WHERE id=? AND tenant_id=?", clientId, tenantId);
  if (!c?.phone || !c.active) return;
  await sendDirect(tenantId, c, "wholesale_rate", `client:${c.id}`, `${c.name}\n⛽ Naya rate (${pkDate()}):\n${lines.join("\n")}\n— ${get("SELECT name FROM tenants WHERE id=?", tenantId)!.name}`);
}

/* ---------------- Monthly bills ---------------- */
export async function sendKhataBill(tenantId: number, customerId: number, month = prevMonth()) {
  const c = get("SELECT * FROM customers WHERE id=? AND tenant_id=?", customerId, tenantId)!;
  const { from, to } = monthRange(month);
  const s = khataStatement(tenantId, c.id, from, to);
  const fuel = s.totals.by_product.map((p: any) => `${PRODUCTS[p.product] ?? p.product} ${Math.round(p.litres)} L = ${pkr(p.amount)}`).join("\n");
  const text = `📄 ${c.name} — ${monthName(month)} ka bill\n${fuel || "Is mahine koi fuel nahi liya"}\nIs mahine: ${pkr(s.totals.charged)} · Payment: ${pkr(s.totals.paid)}\n` +
    `Kul baqaya: ${pkr(s.closing_balance)}\nPoora bill (slips ke saath, print ke liye): ${billLink(tenantId, "k", c.id, month)}\nApna khata kabhi bhi dekhein: ${portalLink(c)} (PIN: ${pinOf("k", c.id, c.portal_v ?? 0)})` +
    (s.closing_balance > 0 && paymentLink(c, s.closing_balance) ? `\nPay karein: ${paymentLink(c, s.closing_balance)}` : "");
  await sendWhatsApp(tenantId, c, text, "system", { kind: "monthly_bill", month });
  return { month, charged: s.totals.charged, closing: s.closing_balance };
}

export async function sendWholesaleStatement(tenantId: number, clientId: number, month = prevMonth()) {
  const c = get("SELECT * FROM wholesale_clients WHERE id=? AND tenant_id=?", clientId, tenantId)!;
  const { from, to } = monthRange(month);
  const s = wholesaleStatement(tenantId, c.id, from, to);
  const sup = s.lines.filter((l: any) => l.type === "supply" && !l.voided);
  await sendDirect(tenantId, c, "wholesale_statement", `client:${c.id}:${month}`,
    `📄 ${c.name} — ${monthName(month)} statement\nSupply: ${sup.reduce((a: number, l: any) => a + l.litres, 0).toLocaleString()} L = ${pkr(sup.reduce((a: number, l: any) => a + l.amount, 0))}\n` +
    `Opening ${pkr(s.opening_balance)} · Closing ${pkr(s.closing_balance)}\nPoori statement: ${billLink(tenantId, "w", c.id, month)}`);
  return { month, closing: s.closing_balance };
}

/** 1st of the month: bills to every khata account and wholesale client that had activity or owes money. */
export async function monthlyBills(tenantId: number, month = prevMonth()) {
  const { from, to } = monthRange(month);
  const fromIso = new Date(`${from}T00:00:00+05:00`).toISOString(), toIso = new Date(Date.parse(`${to}T00:00:00+05:00`) + 86_400_000).toISOString();
  let k = 0, w = 0;
  for (const c of all(`SELECT c.id FROM customers c WHERE c.tenant_id=? AND c.credit_limit > 0 AND (c.balance > 0 OR EXISTS
      (SELECT 1 FROM khata_ledger l WHERE l.customer_id=c.id AND l.created_at >= ? AND l.created_at < ?))`, tenantId, fromIso, toIso)) {
    await sendKhataBill(tenantId, c.id, month); k++;
  }
  for (const c of all("SELECT id, phone FROM wholesale_clients WHERE tenant_id=? AND active=1 AND phone IS NOT NULL", tenantId)) {
    const active = get("SELECT 1 x FROM wholesale_txns WHERE client_id=? AND txn_date >= ? AND txn_date < ? LIMIT 1", c.id, fromIso, toIso);
    if (active || clientDue(c.id) > 0) { await sendWholesaleStatement(tenantId, c.id, month); w++; }
  }
  return { month, khata: k, wholesale: w };
}

/* ---------------- Printable bill page ---------------- */
const esc = (v: unknown) => String(v ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]!));
const dts = (iso: string) => new Date(iso).toLocaleString("en-PK", { timeZone: "Asia/Karachi", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
const n2 = (v: number) => Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 });

export function renderBill(token: string): string | null {
  let p: { bill: Kind; t: number; id: number; m: string };
  try { p = jwt.verify(token, config.jwtSecret) as typeof p; } catch { return null; }
  if (!p?.bill || !/^\d{4}-\d{2}$/.test(p.m)) return null;
  const tenant = get("SELECT * FROM tenants WHERE id=?", p.t);
  if (!tenant) return null;
  const { from, to } = monthRange(p.m);
  let title: string, who: Row, rows: string, totals: string, opening: number, closing: number;
  if (p.bill === "k") {
    const s = khataStatement(p.t, p.id, from, to);
    who = s.customer; opening = s.opening_balance; closing = s.closing_balance; title = "Khata bill";
    rows = s.lines.map((l: any) => `<tr><td>${dts(l.created_at)}</td><td>${esc(l.vehicle_no)}</td><td>${esc(l.slip_no)}</td><td>${l.type === "debit" ? esc(PRODUCTS[l.product] ?? l.note) : `Payment${l.note ? ` — ${esc(l.note)}` : ""}`}</td>` +
      `<td class=r>${l.litres ? n2(l.litres) : ""}</td><td class=r>${l.rate ? n2(l.rate) : ""}</td><td class=r>${l.type === "debit" ? n2(l.amount) : ""}</td><td class=r>${l.type === "credit" ? n2(l.amount) : ""}</td><td class=r>${n2(l.balance)}</td></tr>`).join("");
    totals = s.totals.by_product.map((t: any) => `<div>${esc(PRODUCTS[t.product] ?? t.product)}: <b>${n2(t.litres)} L</b> · ${t.entries} fills · <b>Rs ${n2(t.amount)}</b></div>`).join("") +
      `<div>Charged this month: <b>Rs ${n2(s.totals.charged)}</b> · Paid: <b>Rs ${n2(s.totals.paid)}</b></div>`;
  } else {
    const s = wholesaleStatement(p.t, p.id, from, to);
    who = s.client; opening = s.opening_balance; closing = s.closing_balance; title = "Wholesale statement";
    rows = s.lines.map((l: any) => `<tr${l.voided ? " class=void" : ""}><td>${dts(l.txn_date)}</td><td>${esc(l.vehicle_no)}</td><td>${esc(l.ref)}</td><td>${esc(l.type)}${l.product ? ` — ${esc(PRODUCTS[l.product])}` : ""}${l.voided ? " (void)" : ""}</td>` +
      `<td class=r>${l.litres ? n2(l.litres) : ""}</td><td class=r>${l.rate ? n2(l.rate) : ""}</td><td class=r>${l.debit ? n2(l.debit) : ""}</td><td class=r>${l.credit ? n2(l.credit) : ""}</td><td class=r>${n2(l.balance)}</td></tr>`).join("");
    totals = `<div>Supplied: <b>${n2(s.summary.by_product.reduce((a: number, x: any) => a + x.supplied_l, 0))} L</b> · Billed <b>Rs ${n2(s.summary.billed)}</b> · Received <b>Rs ${n2(s.summary.received)}</b></div>`;
  }
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(title)} — ${esc(who.name)} — ${esc(monthName(p.m))}</title>
<style>
:root{color-scheme:light}body{font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f1f5f9;color:#0f172a}
.page{max-width:900px;margin:16px auto;background:#fff;padding:24px;border-radius:12px;box-shadow:0 1px 3px #0002}
h1{margin:0;font-size:22px}.muted{color:#64748b}.top{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;border-bottom:2px solid #064e3b;padding-bottom:12px;margin-bottom:12px}
.wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:13px;min-width:640px}th,td{padding:6px 8px;border-bottom:1px solid #e2e8f0;text-align:left}th{background:#f8fafc;font-size:12px;text-transform:uppercase;color:#475569}
.r{text-align:right;font-variant-numeric:tabular-nums}.void td{color:#94a3b8;text-decoration:line-through}.sum{margin-top:12px;display:grid;gap:4px}
.big{font-size:20px;margin-top:8px}button{margin-top:16px;padding:10px 18px;border:0;border-radius:8px;background:#064e3b;color:#fff;font-size:15px;cursor:pointer}
tbody tr:nth-child(even) td{background:#f8fafc}th{background:#0f172a;color:#fff}
.acct{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:4px 0 12px}.box{border:1px solid #cbd5e1;border-radius:8px;padding:8px 10px}.box b{font-size:15px}
.box span{display:block;font-size:10.5px;text-transform:uppercase;letter-spacing:.04em;color:#64748b}.doc{font-size:16px;font-weight:800;letter-spacing:.02em;margin:2px 0 8px}
@media print{body{background:#fff}.page{box-shadow:none;margin:0;max-width:none;padding:10mm}button{display:none}table{min-width:0}}
@media (max-width:600px){.page{margin:0;border-radius:0;padding:16px}.acct{grid-template-columns:1fr}}
${BRAND_CSS}
</style></head><body><div class="page">
${brandHead(p.t, title)}
<div class="doc">${esc(title.toUpperCase())} · ${esc(monthName(p.m))}</div>
<div class="acct"><div class="box"><span>Account of</span><b>${esc(who.name)}</b>${who.business_name ? `<div class=muted>${esc(who.business_name)}</div>` : ""}${who.phone ? `<div class=muted>+${esc(who.phone)}</div>` : ""}${who.city ? `<div class=muted>${esc(who.city)}</div>` : ""}</div>
<div class="box"><span>Period</span><b>${esc(monthName(p.m))}</b><div class=muted>Opening balance: <b>Rs ${n2(opening)}</b></div><div class=muted>Closing balance: <b>Rs ${n2(closing)}</b></div></div></div>
<div class="wrap"><table><thead><tr><th>Date</th><th>Vehicle</th><th>Slip / ref</th><th>Entry</th><th class=r>Litres</th><th class=r>Rate</th><th class=r>Charged</th><th class=r>Paid</th><th class=r>Balance</th></tr></thead>
<tbody>${rows || `<tr><td colspan=9 class=muted>No entries this month</td></tr>`}</tbody></table></div>
<div class="sum">${totals}<div class="big">Balance due: <b>Rs ${n2(closing)}</b></div></div>
<div class="muted" style="margin-top:8px">Generated ${esc(new Date().toLocaleString("en-PK", { timeZone: "Asia/Karachi" }))} · computer statement, no signature needed</div>
${brandFoot(p.t)}
<button onclick="print()">Print / Save as PDF</button></div></body></html>`;
}

/* ---------------- Walk-in digital receipt (QR on the POS) ---------------- */
export const receiptUrl = (tenantId: number, kind: "f" | "s", id: number) =>
  `${config.publicUrl}/r/${jwt.sign({ r: kind, t: tenantId, id }, config.jwtSecret, { expiresIn: "400d" })}`;

export function renderReceipt(token: string): string | null {
  let p: { r: "f" | "s"; t: number; id: number };
  try { p = jwt.verify(token, config.jwtSecret) as typeof p; } catch { return null; }
  if (p.r !== "f" && p.r !== "s") return null; // only receipt links, never another kind of signed link
  const tenant = get("SELECT * FROM tenants WHERE id=?", p.t);
  if (!tenant) return null;
  let rows: string, total: number, when: string, pay: string, station: string, taxLine = "";
  if (p.r === "f") {
    const s = get("SELECT s.*, st.name station FROM sales s JOIN stations st ON st.id=s.station_id WHERE s.id=? AND st.tenant_id=?", p.id, p.t);
    if (!s) return null;
    rows = `<tr><td>${esc(PRODUCTS[s.product])}<div class=muted>${n2(s.litres)} L × Rs ${n2(s.rate)}</div></td><td class=r>Rs ${n2(s.amount)}</td></tr>`;
    total = s.amount; when = s.created_at; pay = s.payment_method; station = s.station;
  } else {
    const s = get("SELECT s.*, st.name station FROM shop_sales s JOIN stations st ON st.id=s.station_id WHERE s.id=? AND s.tenant_id=?", p.id, p.t);
    if (!s) return null;
    rows = all("SELECT l.*, i.name FROM shop_sale_lines l JOIN shop_items i ON i.id=l.item_id WHERE l.sale_id=?", s.id)
      .map((l) => `<tr><td>${esc(l.name)}<div class=muted>${n2(l.qty)} × Rs ${n2(l.price)}</div></td><td class=r>Rs ${n2(l.qty * l.price)}</td></tr>`).join("");
    total = s.total; when = s.created_at; pay = s.payment_method; station = s.station;
    const tx = shopSaleTax(p.t, s.id);
    if (tx.tax > 0) taxLine = `<tr><td class=muted>${tx.inclusive ? "Includes" : "Plus"} sales tax ${tx.pct}% on Rs ${n2(tx.value)}</td><td class="r muted">Rs ${n2(tx.tax)}</td></tr>`;
    if (tx.ntn || tx.strn) taxLine += `<tr><td colspan=2 class=muted>${tx.ntn ? `NTN ${esc(tx.ntn)}` : ""}${tx.ntn && tx.strn ? " · " : ""}${tx.strn ? `STRN ${esc(tx.strn)}` : ""}</td></tr>`;
  }
  const review = getSetting(p.t, "google_review_url", "");
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Receipt — ${esc(tenant.name)}</title>
<style>:root{color-scheme:light}body{font:15px/1.45 system-ui,sans-serif;margin:0;background:#f1f5f9;color:#0f172a}.card{max-width:420px;margin:16px auto;background:#fff;border-radius:14px;padding:20px}
h1{font-size:19px;margin:0}.muted{color:#64748b;font-size:13px}table{width:100%;border-collapse:collapse;margin-top:12px}td{padding:8px 0;border-bottom:1px dashed #cbd5e1}.r{text-align:right;font-variant-numeric:tabular-nums}
.total{font-size:22px;font-weight:700}.btn{display:block;text-align:center;margin-top:10px;padding:11px;border-radius:10px;text-decoration:none;font-weight:600}.g{background:#064e3b;color:#fff}.w{background:#dcfce7;color:#14532d}
${BRAND_CSS}.lh{flex-direction:column;align-items:flex-start}.lh .ct{text-align:left}@media print{.btn{display:none}body{background:#fff}.card{margin:0}}</style></head>
<body><div class=card>${brandHead(p.t, "Receipt")}<div class=muted>${esc(station)} · ${esc(new Date(when).toLocaleString("en-PK", { timeZone: "Asia/Karachi", dateStyle: "medium", timeStyle: "short" }))}</div>
<table>${rows}${taxLine}<tr><td class=total>Total</td><td class="r total">Rs ${n2(total)}</td></tr></table>
<div class=muted style="margin-top:6px">Paid: ${esc(pay)} · Receipt ${p.r === "f" ? "F" : "S"}-${p.id}</div>
${review ? `<a class="btn g" href="${esc(review)}">⭐ Rate us on Google</a>` : ""}
${tenant.owner_phone ? `<a class="btn w" href="https://wa.me/${esc(tenant.owner_phone)}">WhatsApp us</a>` : ""}
${getSetting(p.t, "receipt_footer", "") ? "" : `<div class=muted style="text-align:center;margin-top:12px">Shukriya! Phir tashreef layein 🙏</div>`}${brandFoot(p.t)}</div></body></html>`;
}
