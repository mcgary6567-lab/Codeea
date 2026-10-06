/**
 * A customer's own khata page behind a PIN — for wholesale clients (/w/…) and khata customers (/k/…).
 *
 * The link and the 6-digit PIN are both signed with the server secret from (kind, id, version), so
 * any copy of the server can check them without looking anything up (this also works on serverless
 * hosting where each copy has its own database). "New link + PIN" raises the version, so the old
 * link and PIN stop working; the page can be turned off. Five wrong PINs lock the page for 15 minutes.
 */
import { Router, type Request, type Response } from "express";
import express from "express";
import crypto from "node:crypto";
import jwt from "jsonwebtoken";
import { get, run, now, pkDate } from "../db.js";
import { h, tid, requirePerm } from "../auth.js";
import { AppError, normalizePhone, currentPrices, paymentLink } from "../services.js";
import { config, PRODUCTS } from "../config.js";
import { statement, rateCard, clientDue } from "./wholesale.js";
import { khataStatement } from "./crm.js";
import { billLink } from "../billing.js";
import { sendDirect, sendWhatsApp } from "../whatsapp/cloud.js";
import { logoTag } from "./setup.js";

export const pinPortalAdmin = Router();
export const pinPortalPublic = Router();

type Kind = "w" | "k";
const TABLE: Record<Kind, string> = { w: "wholesale_clients", k: "customers" };
const esc = (v: unknown) => String(v ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]!));
const rs = (v: number) => `Rs ${Math.round(v).toLocaleString("en-IN")}`;
const n2 = (v: number) => Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 });
const MAX_FAILS = 5, LOCK_MIN = 15;

const hmac = (s: string) => crypto.createHmac("sha256", config.jwtSecret).update(s).digest();
const codeOf = (kind: Kind, id: number, v: number) => `${id}-${v}-${hmac(`${kind}:link:${id}:${v}`).toString("base64url").slice(0, 10)}`;
export const pinOf = (kind: Kind, id: number, v: number) => String(hmac(`${kind}:pin:${id}:${v}`).readUIntBE(0, 6) % 1_000_000).padStart(6, "0");
export const pinLink = (kind: Kind, c: { id: number; portal_v?: number | null }) => `${config.publicUrl}/${kind}/${codeOf(kind, c.id, c.portal_v ?? 0)}`;

/** The record behind a link, or null when the link is forged, replaced by a newer one, or turned off. */
function fromCode(kind: Kind, code: string) {
  const m = /^(\d+)-(\d+)-([\w-]{10})$/.exec(code);
  if (!m) return null;
  const id = Number(m[1]), v = Number(m[2]);
  if (!crypto.timingSafeEqual(Buffer.from(codeOf(kind, id, v)), Buffer.from(code))) return null;
  const c = get(`SELECT * FROM ${TABLE[kind]} WHERE id=?`, id);
  // a newer link was made (version went up) or the page is off
  if (!c || (c.portal_v ?? 0) > v || c.portal_off || c.active === 0) return null;
  return { c, v };
}

/* ---------------- staff side ---------------- */
function ownRow(kind: Kind, t: number, id: number) {
  const c = get(`SELECT * FROM ${TABLE[kind]} WHERE id=? AND tenant_id=?`, id, t);
  if (!c) throw new AppError(404, kind === "w" ? "Wholesale client not found" : "Customer not found");
  return c;
}
const info = (kind: Kind, c: any) => ({ url: pinLink(kind, c), pin: pinOf(kind, c.id, c.portal_v ?? 0), enabled: !c.portal_off, last_seen: c.portal_seen_at ?? null });
const message = (kind: Kind, c: any, tenant: string) => {
  const p = info(kind, c);
  return `${c.name}\n📒 Apna khata kabhi bhi dekhein (baqaya, har ${kind === "w" ? "supply" : "fill"}, payment aur bill):\n${p.url}\nPIN: ${p.pin}\nYeh PIN kisi ko na batayein.\n— ${tenant}`;
};
for (const [kind, base, view, manage] of [["w", "/wholesale/clients/:id/portal", "wholesale.view", "wholesale.manage"], ["k", "/customers/:id/portal", "khata.manage", "khata.manage"]] as const) {
  pinPortalAdmin.get(base, requirePerm(view), h((req) => info(kind, ownRow(kind, tid(req), Number(req.params.id)))));
  /** Shared with the wrong person? A new link and PIN; the old ones stop working. */
  pinPortalAdmin.post(`${base}/new`, requirePerm(manage), h((req) => {
    const c = ownRow(kind, tid(req), Number(req.params.id));
    run(`UPDATE ${TABLE[kind]} SET portal_v=COALESCE(portal_v,0)+1, portal_off=0, portal_fails=0, portal_locked_until=NULL WHERE id=?`, c.id);
    return info(kind, get(`SELECT * FROM ${TABLE[kind]} WHERE id=?`, c.id));
  }));
  pinPortalAdmin.post(`${base}/:state(on|off)`, requirePerm(manage), h((req) => {
    const c = ownRow(kind, tid(req), Number(req.params.id));
    run(`UPDATE ${TABLE[kind]} SET portal_off=? WHERE id=?`, req.params.state === "off" ? 1 : 0, c.id);
    return info(kind, get(`SELECT * FROM ${TABLE[kind]} WHERE id=?`, c.id));
  }));
  pinPortalAdmin.post(`${base}/send`, requirePerm(manage), h(async (req) => {
    const c = ownRow(kind, tid(req), Number(req.params.id));
    if (!c.phone) throw new AppError(400, "Add a WhatsApp number first");
    if (c.portal_off) run(`UPDATE ${TABLE[kind]} SET portal_off=0 WHERE id=?`, c.id);
    const text = message(kind, c, get("SELECT name FROM tenants WHERE id=?", tid(req))!.name);
    // khata customers chat with the pump in the WhatsApp inbox; wholesale clients get a direct message
    if (kind === "k") await sendWhatsApp(tid(req), c, text, "system", { kind: "portal_link" });
    else await sendDirect(tid(req), { ...c, phone: normalizePhone(c.phone) }, "portal_link", `${kind}:${c.id}`, text);
    return { ...info(kind, c), sent: true };
  }));
}

/* ---------------- customer side ---------------- */
const FUEL_UR: Record<string, string> = { PMG: "پیٹرول", HOBC: "ہائی آکٹین", HSD: "ڈیزل" };
const DAYMS = 86_400_000;
const CSS = `:root{color-scheme:light}*{box-sizing:border-box}body{font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;background:#eef2f6;color:#0f172a}
.ur{font-family:"Noto Nastaliq Urdu","Jameel Noori Nastaleeq","Noto Naskh Arabic",serif;direction:rtl;unicode-bidi:isolate}
.w{max-width:760px;margin:0 auto;padding:12px}.c{background:#fff;border-radius:16px;padding:16px;margin-bottom:12px;box-shadow:0 1px 2px rgba(15,23,42,.06)}
.top{position:sticky;top:0;z-index:5;background:#064e3b;color:#fff;border-radius:0 0 16px 16px;padding:10px 12px;margin:-12px -12px 12px}
.brand{display:flex;align-items:center;gap:10px;font-weight:700}.brand img{background:#fff;border-radius:8px;padding:2px}
.acts{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:10px}.acts a,.acts button{display:flex;flex-direction:column;align-items:center;gap:2px;background:rgba(255,255,255,.12);color:#fff;border:0;border-radius:12px;padding:8px 4px;font:600 13px system-ui;text-decoration:none;cursor:pointer}
.acts .i{font-size:22px;line-height:1}.acts .ur{font-size:12px;font-weight:400}
h1{font-size:22px;margin:0}.m{color:#64748b;font-size:13px}.g{color:#047857}.red{color:#b91c1c}
.hero{border-radius:16px;padding:16px;text-align:center}.hero.owe{background:#fef2f2;border:2px solid #fecaca}.hero.ok{background:#ecfdf5;border:2px solid #a7f3d0}
.hero .amt{font-size:38px;font-weight:800;letter-spacing:.5px;white-space:nowrap}.hero .lbl{font-size:16px;font-weight:600}.hero .ur{font-size:20px}
.bar{height:10px;border-radius:99px;background:#e2e8f0;overflow:hidden;margin-top:6px}.bar>i{display:block;height:100%;border-radius:99px}
.tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:12px}.tile{background:#f8fafc;border-radius:12px;padding:10px;text-align:center}.tile .i{font-size:22px}.tile{min-width:0}.tile b{display:block;font-size:clamp(12px,3.4vw,19px);white-space:nowrap}
.rates{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}.rate{border-radius:12px;padding:12px;color:#fff;text-align:center}.rate b{display:block;font-size:24px}
.chips{display:flex;gap:6px;overflow-x:auto;padding-bottom:2px}.chips a{flex:none;padding:7px 12px;border-radius:99px;background:#f1f5f9;color:#0f172a;text-decoration:none;font-size:14px;border:1px solid #e2e8f0}.chips a.on{background:#064e3b;color:#fff;border-color:#064e3b}
.e{display:flex;gap:10px;align-items:flex-start;padding:12px 0;border-bottom:1px solid #e2e8f0}.e .ic{flex:none;width:42px;height:42px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:22px}
.e .l{min-width:0;flex:1}.et{font-weight:700;font-size:16px}.e .r{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}.e .r b{font-size:18px}
.slips{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}.slips a{display:block;color:#475569;font-size:12px;text-align:center}.slips img{display:block;width:96px;height:72px;object-fit:cover;border-radius:10px;border:1px solid #cbd5e1;margin-bottom:2px}
.sup .ic{background:#eff6ff}.pay .ic{background:#ecfdf5}.ret .ic{background:#fefce8}.adj .ic{background:#f5f3ff}
.sum{display:flex;justify-content:space-between;padding:10px 0;font-weight:700;border-top:2px solid #0f172a}
.row{display:flex;gap:10px;justify-content:space-between;padding:9px 0;border-bottom:1px solid #e2e8f0}.row .l{min-width:0}.rr{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-top:10px}.t{background:#f8fafc;border-radius:10px;padding:10px}.big{font-size:24px;font-weight:700;white-space:nowrap}
a.b,button.b{display:block;width:100%;text-align:center;background:#064e3b;color:#fff;border:0;padding:14px;border-radius:12px;font-size:17px;font-weight:600;text-decoration:none;margin-top:10px;cursor:pointer}
input[name=pin]{font-size:30px;letter-spacing:10px;text-align:center;width:100%;padding:12px;border:2px solid #cbd5e1;border-radius:12px}
.err{background:#fee2e2;color:#991b1b;padding:10px;border-radius:10px;margin-top:10px}ul{padding-left:18px;margin:6px 0}.po{display:none}
@media print{body{background:#fff;font-size:12px}.np,.top{display:none!important}.po{display:block}.c{box-shadow:none;border:1px solid #cbd5e1;break-inside:avoid;padding:10px;margin-bottom:8px}
.e{break-inside:avoid;padding:6px 0}.rate{background:#fff!important;color:#000;border:1px solid #94a3b8}.tile{border:1px solid #e2e8f0}.e .ic{width:28px;height:28px;font-size:15px}.hero .amt{font-size:26px}.w{max-width:none;padding:0}a{color:inherit;text-decoration:none}}`;

const page = (title: string, body: string, tenantId?: number, top = "") => `<!doctype html><html lang="ur"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Noto+Nastaliq+Urdu:wght@400;600&display=swap" rel="stylesheet">
<title>${esc(title)}</title><style>${CSS}</style></head>
<body><div class=w>${top || (tenantId ? `<div class=c><div class=brand>${logoTag(tenantId, "height:34px;max-width:110px;object-fit:contain")}<span>${esc(get("SELECT name FROM tenants WHERE id=?", tenantId)?.name)}</span></div></div>` : "")}${body}</div></body></html>`;

function pinForm(c: any, error?: string) {
  return page(`${c.name} — khata`, `<div class=c style="text-align:center"><div style="font-size:44px">🔒</div><h1>${esc(c.name)}</h1>
<p style="margin:6px 0 2px">Enter the 6-digit PIN</p><p class=ur style="margin:0 0 12px;font-size:20px">اپنا ۶ ہندسوں کا پن لکھیں</p>
<form method=post><input name=pin inputmode=numeric autocomplete=one-time-code pattern="[0-9]{6}" maxlength=6 required autofocus placeholder="••••••">
<label class=m style="display:flex;gap:6px;align-items:center;justify-content:center;margin-top:10px"><input type=checkbox name=remember value=1 checked> Remember this phone 30 days · <span class=ur>یہ فون یاد رکھیں</span></label>
<button class=b>Open my khata · <span class=ur>کھاتہ کھولیں</span></button></form>${error ? `<div class=err>${esc(error)}</div>` : ""}</div>`, c.tenant_id);
}

/** Top bar with the pump's name and the main buttons (print, monthly bill, call, WhatsApp, lock). */
function topBar(c: any, code: string, billUrl: string) {
  const t = c.tenant_id;
  const tenant = get("SELECT * FROM tenants WHERE id=?", t)!;
  const phone = String(get("SELECT value FROM settings WHERE tenant_id=? AND key='biz_phone'", t)?.value || tenant.owner_phone || "").replace(/[^\d+]/g, "");
  const wa = phone.replace(/^\+/, "").replace(/^0/, "92");
  return `<div class="top np"><div class=brand>${logoTag(t, "height:30px;max-width:90px;object-fit:contain")}<span>${esc(tenant.name)}</span></div>
<div class=acts>
<button onclick="print()"><span class=i>🖨️</span>Print<span class=ur>پرنٹ</span></button>
<a href="${esc(billUrl)}"><span class=i>🧾</span>Bill<span class=ur>ماہانہ بل</span></a>
${phone ? `<a href="tel:${esc(phone)}"><span class=i>📞</span>Call<span class=ur>کال کریں</span></a>` : `<a href="#"><span class=i>📞</span>Call<span class=ur>کال</span></a>`}
<form method=post action="${esc(code)}/logout" style="display:contents"><button><span class=i>🔒</span>Lock<span class=ur>بند کریں</span></button></form>
</div>${wa ? `<a href="https://wa.me/${esc(wa)}?text=${encodeURIComponent(`${c.name}: khata ke baare mein`)}" style="display:block;margin-top:8px;background:#25d366;color:#fff;text-align:center;padding:9px;border-radius:12px;text-decoration:none;font-weight:600">💬 WhatsApp the pump · <span class=ur>واٹس ایپ کریں</span></a>` : ""}</div>`;
}

const monthsBack = (n: number) => Array.from({ length: n }, (_, i) => { const d = new Date(Date.parse(`${pkDate().slice(0, 7)}-15T00:00:00Z`)); d.setUTCMonth(d.getUTCMonth() - i); return d.toISOString().slice(0, 7); });
const monthName = (m: string) => new Date(`${m}-15T00:00:00Z`).toLocaleDateString("en-PK", { month: "short", year: "numeric" });
const dateStr = (iso: string) => new Date(iso).toLocaleDateString("en-PK", { timeZone: "Asia/Karachi", day: "2-digit", month: "short", year: "numeric" });

function wholesaleBody(c: any, month: string | null, code: string): string {
  const t = c.tenant_id;
  const from = month ? `${month}-01` : pkDate(Date.now() - 60 * DAYMS);
  const to = month ? new Date(Date.UTC(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 0)).toISOString().slice(0, 10) : undefined;
  const s = statement(t, c.id, from, to);
  const due = clientDue(c.id);
  const card = rateCard(c.id);
  const live = s.lines.filter((l: any) => !l.voided);
  const litres = live.filter((l: any) => l.type === "supply").reduce((a: number, l: any) => a + l.litres, 0);
  const billed = live.filter((l: any) => l.type === "supply").reduce((a: number, l: any) => a + l.amount, 0);
  const paid = live.filter((l: any) => l.type === "payment").reduce((a: number, l: any) => a + l.amount, 0);
  const used = c.credit_limit > 0 ? Math.min(100, Math.max(0, (due / c.credit_limit) * 100)) : 0;
  const periodLabel = month ? monthName(month) : "Last 60 days";
  const COLORS: Record<string, string> = { PMG: "#2a78d6", HOBC: "#eb6834", HSD: "#1baf7a" };
  const rows = [...live].reverse().map((l: any) => {
    const d = `<div class=m>${esc(dateStr(l.txn_date))}</div>`;
    const bal = `<div class=m>Baqi · <span class=ur>باقی</span> ${n2(l.balance)}</div>`;
    if (l.type === "supply") return `<div class="e sup"><div class=ic>⛽</div><div class=l>${d}<div class=et>${esc(PRODUCTS[l.product] ?? l.product)} · <span class=ur>${FUEL_UR[l.product] ?? ""}</span></div>
      <div>${n2(l.litres)} L × Rs ${n2(l.rate)}</div><div class=m>${esc([l.vehicle_no && `🚛 ${l.vehicle_no}`, l.driver_name && `👤 ${l.driver_name}`, l.location && `📍 ${l.location}`, l.ref && `🧾 ${l.ref}`].filter(Boolean).join("  "))}</div>${photoThumbs(code, l.proof_ids, "Delivery", "ڈیلیوری")}${photoThumbs(code, l.trip_proof_ids, "Tanker", "ٹینکر")}</div>
      <div class=r><b>+${n2(l.amount)}</b>${bal}</div></div>`;
    if (l.type === "payment") return `<div class="e pay"><div class=ic>💵</div><div class=l>${d}<div class="et g">Payment received · <span class=ur>رقم وصول</span></div><div class=m>${esc([l.method, l.ref].filter(Boolean).join(" · "))}</div>${photoThumbs(code, l.proof_ids, "Receipt", "رسید")}</div>
      <div class=r><b class=g>−${n2(l.amount)}</b>${bal}</div></div>`;
    if (l.type === "return") return `<div class="e ret"><div class=ic>↩️</div><div class=l>${d}<div class=et>Fuel returned · <span class=ur>تیل واپس</span></div><div>${n2(l.litres)} L ${esc(PRODUCTS[l.product] ?? "")}</div>${photoThumbs(code, l.proof_ids, "Return", "واپسی")}</div>
      <div class=r><b class=g>−${n2(l.amount)}</b>${bal}</div></div>`;
    return `<div class="e adj"><div class=ic>✏️</div><div class=l>${d}<div class=et>Adjustment · <span class=ur>ایڈجسٹمنٹ</span></div><div class=m>${esc(l.note ?? "")}</div></div>
      <div class=r><b class="${l.amount < 0 ? "g" : ""}">${l.amount < 0 ? "−" : "+"}${n2(Math.abs(l.amount))}</b>${bal}</div></div>`;
  }).join("");
  const chips = [`<a href="${esc(code)}" class="${month ? "" : "on"}">60 days · <span class=ur>۶۰ دن</span></a>`, ...monthsBack(6).map((m) => `<a href="${esc(code)}?m=${m}" class="${month === m ? "on" : ""}">${esc(monthName(m))}</a>`)].join("");
  return `<div class=po><b>${esc(get("SELECT name FROM tenants WHERE id=?", t)?.name)}</b> — Account statement · <span class=ur>کھاتہ</span><br>${esc(c.name)} · ${esc(periodLabel)} · printed ${esc(new Date().toLocaleString("en-PK", { timeZone: "Asia/Karachi" }))}</div>
<div class=c><h1>${esc(c.name)}</h1><div class=m>${esc([c.business_name, c.city].filter(Boolean).join(" · "))}</div>
<div class="hero ${due > 0 ? "owe" : "ok"}" style="margin-top:12px">
${due > 0 ? `<div class=lbl>You have to pay · <span class=ur>آپ کے ذمے</span></div><div class="amt red">${rs(due)}</div>`
  : `<div class=lbl>${due < 0 ? "Advance with the pump · <span class=ur>پمپ پر ایڈوانس</span>" : "All paid · <span class=ur>حساب صاف</span>"} ✅</div><div class="amt g">${rs(Math.abs(due))}</div>`}
${c.credit_limit > 0 ? `<div class=m style="margin-top:6px">Credit limit · <span class=ur>حد</span> ${rs(c.credit_limit)} — left · <span class=ur>باقی حد</span> <b>${rs(Math.max(0, c.credit_limit - due))}</b></div>
<div class=bar><i style="width:${used}%;background:${used >= 90 ? "#dc2626" : used >= 75 ? "#d97706" : "#059669"}"></i></div>` : ""}</div>
<div class=tiles><div class=tile><div class=i>⛽</div><b>${Math.round(litres).toLocaleString("en-IN")} L</b><div class=m>Fuel taken · <span class=ur>تیل لیا</span></div></div>
<div class=tile><div class=i>🧾</div><b>${rs(billed)}</b><div class=m>Bill · <span class=ur>بل</span></div></div>
<div class=tile><div class=i>💵</div><b class=g>${rs(paid)}</b><div class=m>Paid · <span class=ur>ادا کیا</span></div></div></div>
<div class=m style="text-align:center;margin-top:6px">${esc(periodLabel)}</div></div>
<div class=c><b>Your rate today · <span class=ur>آج آپ کا ریٹ</span></b><div class=rates style="margin-top:10px">${Object.entries(card).map(([p, r]) => `<div class=rate style="background:${COLORS[p] ?? "#475569"}">${esc(PRODUCTS[p] ?? p)} · <span class=ur>${FUEL_UR[p] ?? ""}</span><b>Rs ${r.rate != null ? r.rate.toFixed(2) : "—"}</b><span style="font-size:12px">per litre · <span class=ur>فی لیٹر</span></span></div>`).join("") || "<p class=m>No rate set</p>"}</div></div>
<div class=c><div class=np style="margin-bottom:8px"><div class=chips>${chips}</div></div>
<b>Entries · <span class=ur>تفصیل</span> — ${esc(periodLabel)}</b>
<div class=m>⛽ fuel taken (+) · 💵 payment (−) · <span class=ur>باقی</span> = balance after each entry</div>
<div class=sum style="border-top:0;border-bottom:1px solid #e2e8f0;font-weight:600"><span>Opening · <span class=ur>شروع کا باقی</span></span><span>${n2(s.opening_balance)}</span></div>
${rows || `<p class=m style="text-align:center;padding:16px">No entries in this period · <span class=ur>اس دوران کوئی اندراج نہیں</span></p>`}
<div class=sum><span>Closing · <span class=ur>آخری باقی</span></span><span>${n2(s.closing_balance)}</span></div></div>`;
}

/** Khata customer's page: same look as the wholesale page — balance, pay, rate, every fill and payment. */
function khataBody(c: any, month: string | null, code: string): string {
  const t = c.tenant_id;
  const from = month ? `${month}-01` : pkDate(Date.now() - 60 * DAYMS);
  const to = month ? new Date(Date.UTC(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 0)).toISOString().slice(0, 10) : undefined;
  const s = khataStatement(t, c.id, from, to);
  const due = c.balance;
  const fills = s.lines.filter((l: any) => l.type === "debit");
  const litres = fills.reduce((a: number, l: any) => a + (l.litres ?? 0), 0);
  const billed = fills.reduce((a: number, l: any) => a + l.amount, 0);
  const paid = s.lines.filter((l: any) => l.type === "credit").reduce((a: number, l: any) => a + l.amount, 0);
  const used = c.credit_limit > 0 ? Math.min(100, Math.max(0, (due / c.credit_limit) * 100)) : 0;
  const periodLabel = month ? monthName(month) : "Last 60 days";
  const COLORS: Record<string, string> = { PMG: "#2a78d6", HOBC: "#eb6834", HSD: "#1baf7a" };
  const prices = currentPrices(t);
  // fuel per vehicle in this period (schools, police and fleets have several)
  const byVehicle = Object.values(fills.reduce((a: Record<string, any>, l: any) => {
    const k = l.vehicle_no || "—"; a[k] ??= { v: k, litres: 0, amount: 0, n: 0 }; a[k].litres += l.litres ?? 0; a[k].amount += l.amount; a[k].n++; return a;
  }, {})) as { v: string; litres: number; amount: number; n: number }[];
  const rows = [...s.lines].reverse().map((l: any) => {
    const d = `<div class=m>${esc(new Date(l.created_at).toLocaleString("en-PK", { timeZone: "Asia/Karachi", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }))}</div>`;
    const bal = `<div class=m>Baqi · <span class=ur>باقی</span> ${n2(l.balance)}</div>`;
    if (l.type === "credit") return `<div class="e pay"><div class=ic>💵</div><div class=l>${d}<div class="et g">Payment received · <span class=ur>رقم وصول</span></div><div class=m>${esc([l.ref, l.note !== "Payment received" ? l.note : null].filter(Boolean).join(" · "))}</div></div>
      <div class=r><b class=g>−${n2(l.amount)}</b>${bal}</div></div>`;
    if (l.product) return `<div class="e sup"><div class=ic>⛽</div><div class=l>${d}<div class=et>${esc(PRODUCTS[l.product] ?? l.product)} · <span class=ur>${FUEL_UR[l.product] ?? ""}</span></div>
      <div>${n2(l.litres ?? 0)} L × Rs ${n2(l.rate ?? 0)}</div><div class=m>${esc([l.vehicle_no && `🚗 ${l.vehicle_no}`, l.slip_no && `🧾 slip ${l.slip_no}`, l.station_name && `📍 ${String(l.station_name)}`].filter(Boolean).join("  "))}</div>${photoThumbs(code, l.proof_ids, "Slip", "پرچی")}</div>
      <div class=r><b>+${n2(l.amount)}</b>${bal}</div></div>`;
    return `<div class="e adj"><div class=ic>✏️</div><div class=l>${d}<div class=et>Charge · <span class=ur>چارج</span></div><div class=m>${esc(l.note ?? l.ref ?? "")}</div></div>
      <div class=r><b>+${n2(l.amount)}</b>${bal}</div></div>`;
  }).join("");
  const chips = [`<a href="${esc(code)}" class="${month ? "" : "on"}">60 days · <span class=ur>۶۰ دن</span></a>`, ...monthsBack(6).map((m) => `<a href="${esc(code)}?m=${m}" class="${month === m ? "on" : ""}">${esc(monthName(m))}</a>`)].join("");
  return `<div class=po><b>${esc(get("SELECT name FROM tenants WHERE id=?", t)?.name)}</b> — Khata statement · <span class=ur>کھاتہ</span><br>${esc(c.name)} · ${esc(periodLabel)} · printed ${esc(new Date().toLocaleString("en-PK", { timeZone: "Asia/Karachi" }))}</div>
<div class=c><h1>${esc(c.name)}</h1><div class=m>${esc([c.city, c.phone].filter(Boolean).join(" · "))}</div>
<div class="hero ${due > 0 ? "owe" : "ok"}" style="margin-top:12px">
${due > 0 ? `<div class=lbl>You have to pay · <span class=ur>آپ کے ذمے</span></div><div class="amt red">${rs(due)}</div>`
  : `<div class=lbl>${due < 0 ? "Advance with the pump · <span class=ur>پمپ پر ایڈوانس</span>" : "All paid · <span class=ur>حساب صاف</span>"} ✅</div><div class="amt g">${rs(Math.abs(due))}</div>`}
${c.credit_limit > 0 ? `<div class=m style="margin-top:6px">Credit limit · <span class=ur>حد</span> ${rs(c.credit_limit)} — left · <span class=ur>باقی حد</span> <b>${rs(Math.max(0, c.credit_limit - due))}</b></div>
<div class=bar><i style="width:${used}%;background:${used >= 90 ? "#dc2626" : used >= 75 ? "#d97706" : "#059669"}"></i></div>` : ""}
${c.khata_blocked ? `<div class=err>⛔ Khata is on hold — payment is overdue. Please pay to fill again · <span class=ur>ادائیگی کے بعد کھاتہ دوبارہ چلے گا</span></div>` : ""}
${due > 0 && paymentLink(c, due) ? `<a class="b np" href="${esc(paymentLink(c, due))}">💳 Pay now · <span class=ur>ابھی ادائیگی کریں</span> <span style="font-weight:400;font-size:14px">(JazzCash / Easypaisa / Raast)</span></a>` : ""}</div>
<div class=tiles><div class=tile><div class=i>⛽</div><b>${Math.round(litres).toLocaleString("en-IN")} L</b><div class=m>Fuel taken · <span class=ur>تیل لیا</span></div></div>
<div class=tile><div class=i>🧾</div><b>${rs(billed)}</b><div class=m>Bill · <span class=ur>بل</span></div></div>
<div class=tile><div class=i>💵</div><b class=g>${rs(paid)}</b><div class=m>Paid · <span class=ur>ادا کیا</span></div></div></div>
<div class=m style="text-align:center;margin-top:6px">${esc(periodLabel)}</div></div>
<div class=c><b>Today's rate · <span class=ur>آج کا ریٹ</span></b><div class=rates style="margin-top:10px">${Object.entries(prices).map(([p, r]) => `<div class=rate style="background:${COLORS[p] ?? "#475569"}">${esc(PRODUCTS[p] ?? p)} · <span class=ur>${FUEL_UR[p] ?? ""}</span><b>Rs ${r.price.toFixed(2)}</b><span style="font-size:12px">per litre · <span class=ur>فی لیٹر</span></span></div>`).join("")}</div></div>
${byVehicle.length > 1 ? `<div class=c><b>By vehicle · <span class=ur>گاڑی وار</span> — ${esc(periodLabel)}</b>${byVehicle.sort((a, b) => b.amount - a.amount).map((v) => `<div class=sum style="border-top:0;border-bottom:1px solid #e2e8f0;font-weight:400"><span>🚗 <b>${esc(v.v)}</b> <span class=m>${v.n} fills</span></span><span>${n2(v.litres)} L · <b>${rs(v.amount)}</b></span></div>`).join("")}</div>` : ""}
<div class=c><div class=np style="margin-bottom:8px"><div class=chips>${chips}</div></div>
<b>Entries · <span class=ur>تفصیل</span> — ${esc(periodLabel)}</b>
<div class=m>⛽ fuel taken (+) · 💵 payment (−) · <span class=ur>باقی</span> = balance after each entry</div>
<div class=sum style="border-top:0;border-bottom:1px solid #e2e8f0;font-weight:600"><span>Opening · <span class=ur>شروع کا باقی</span></span><span>${n2(s.opening_balance)}</span></div>
${rows || `<p class=m style="text-align:center;padding:16px">No entries in this period · <span class=ur>اس دوران کوئی اندراج نہیں</span></p>`}
<div class=sum><span>Closing · <span class=ur>آخری باقی</span></span><span>${n2(s.closing_balance)}</span></div></div>`;
}

/** Photos kept with an entry (khata slip / parchi, delivery challan, cheque or receipt), shown under it; they open full size from this page only. */
function photoThumbs(code: string, ids: unknown, en: string, ur: string) {
  const list = String(ids ?? "").split(",").map(Number).filter((n) => n > 0);
  if (!list.length) return "";
  return `<div class="slips np">${list.map((id) => `<a href="${esc(code)}/slip/${id}" target="_blank" rel="noopener"><img src="${esc(code)}/slip/${id}" alt="${esc(en)} photo" loading="lazy">${esc(en)} · <span class=ur>${ur}</span></a>`).join("")}</div>`;
}

function khataPage(kind: Kind, c: any, code: string, month: string | null) {
  const bill = billLink(c.tenant_id, kind, c.id, month ?? pkDate().slice(0, 7));
  return page(`${c.name} — khata`, (kind === "w" ? wholesaleBody(c, month, code) : khataBody(c, month, code)) +
    `<div class=m style="text-align:center;margin:8px 0 20px">Updated · <span class=ur>تازہ ترین</span> ${esc(new Date().toLocaleString("en-PK", { timeZone: "Asia/Karachi" }))}</div>`, c.tenant_id, topBar(c, code, bill));
}

const cookieName = (kind: Kind, id: number) => `pp_${kind}${id}`;
const monthQ = (req: Request) => (/^\d{4}-\d{2}$/.test(String(req.query.m ?? "")) ? String(req.query.m) : null);
function remembered(req: Request, kind: Kind, c: any, v: number) {
  const raw = String(req.headers.cookie ?? "").split(/;\s*/).find((x) => x.startsWith(cookieName(kind, c.id) + "="));
  if (!raw) return false;
  try { const p = jwt.verify(decodeURIComponent(raw.split("=")[1]), config.jwtSecret) as any; return p.k === kind && p.id === c.id && p.v === v; } catch { return false; }
}
const gone = (res: Response) => res.status(404).type("html").send(page("Not found", "<div class=c>This link is not valid any more. Ask the pump for a new one.</div>"));
const seen = (kind: Kind, id: number) => run(`UPDATE ${TABLE[kind]} SET portal_seen_at=? WHERE id=?`, now(), id);

for (const kind of ["w", "k"] as const) {
  pinPortalPublic.get(`/${kind}/:code`, (req, res) => {
    const r = fromCode(kind, req.params.code);
    if (!r) return gone(res);
    res.setHeader("cache-control", "no-store");
    if (remembered(req, kind, r.c, r.v)) { seen(kind, r.c.id); return res.type("html").send(khataPage(kind, r.c, req.params.code, monthQ(req))); }
    res.type("html").send(pinForm(r.c));
  });
  pinPortalPublic.post(`/${kind}/:code`, express.urlencoded({ extended: false, limit: "2kb" }), (req, res) => {
    const r = fromCode(kind, req.params.code);
    if (!r) return gone(res);
    const c = r.c;
    res.setHeader("cache-control", "no-store");
    if (c.portal_locked_until && Date.parse(c.portal_locked_until) > Date.now())
      return res.status(429).type("html").send(pinForm(c, `Too many wrong PINs. Try again after ${Math.ceil((Date.parse(c.portal_locked_until) - Date.now()) / 60_000)} minutes.`));
    const pin = String(req.body?.pin ?? "").trim();
    if (!/^\d{6}$/.test(pin) || !crypto.timingSafeEqual(Buffer.from(pin.padEnd(6)), Buffer.from(pinOf(kind, c.id, r.v)))) {
      const fails = (c.portal_fails ?? 0) + 1;
      const lock = fails >= MAX_FAILS ? new Date(Date.now() + LOCK_MIN * 60_000).toISOString() : null;
      run(`UPDATE ${TABLE[kind]} SET portal_fails=?, portal_locked_until=? WHERE id=?`, lock ? 0 : fails, lock, c.id);
      return res.status(401).type("html").send(pinForm(c, lock ? `Wrong PIN. The page is locked for ${LOCK_MIN} minutes.` : `Wrong PIN · غلط پن (${MAX_FAILS - fails} tries left)`));
    }
    run(`UPDATE ${TABLE[kind]} SET portal_fails=0, portal_locked_until=NULL, portal_seen_at=? WHERE id=?`, now(), c.id);
    // remembered phones keep the cookie 30 days; otherwise it lasts until the browser closes (so the month buttons work)
    const keep = Boolean(req.body?.remember);
    const tok = jwt.sign({ k: kind, id: c.id, v: r.v }, config.jwtSecret, { expiresIn: keep ? "30d" : "12h" });
    res.setHeader("set-cookie", `${cookieName(kind, c.id)}=${encodeURIComponent(tok)}; Path=/${kind}/; ${keep ? `Max-Age=${30 * 86400}; ` : ""}HttpOnly; SameSite=Lax${config.publicUrl.startsWith("https") ? "; Secure" : ""}`);
    res.type("html").send(khataPage(kind, c, req.params.code, null));
  });
  pinPortalPublic.get(`/${kind}/:code/slip/:photo`, (req, res) => {
    const r = fromCode(kind, req.params.code);
    // only after the PIN, and only a photo of one of this customer's / client's own entries
    if (!r || !remembered(req, kind, r.c, r.v)) return res.status(404).end();
    const p = kind === "k"
      ? get(`SELECT p.mime, p.data FROM photos p JOIN khata_ledger k ON p.ref = 'khata:' || k.id
          WHERE p.id=? AND p.tenant_id=? AND k.customer_id=? AND k.type='debit'`, Number(req.params.photo), r.c.tenant_id, r.c.id)
      // the client's own entry, or the tanker trip one of their (not voided) deliveries came on
      : get(`SELECT p.mime, p.data FROM photos p JOIN wholesale_txns x ON (p.ref = 'wtx:' || x.id OR (x.trip_id IS NOT NULL AND p.ref = 'trip:' || x.trip_id))
          WHERE p.id=? AND p.tenant_id=? AND x.client_id=? AND x.voided=0 LIMIT 1`, Number(req.params.photo), r.c.tenant_id, r.c.id);
    if (!p) return res.status(404).end();
    res.setHeader("content-type", /^image\/(jpeg|png|webp)$/.test(p.mime) ? p.mime : "application/octet-stream");
    res.setHeader("x-content-type-options", "nosniff");
    res.setHeader("content-disposition", "inline");
    res.setHeader("cache-control", "private, max-age=3600");
    res.end(Buffer.from(p.data as Uint8Array));
  });
  pinPortalPublic.post(`/${kind}/:code/logout`, (req, res) => {
    const r = fromCode(kind, req.params.code);
    if (r) res.setHeader("set-cookie", `${cookieName(kind, r.c.id)}=; Path=/${kind}/; Max-Age=0`);
    res.redirect(303, `/${kind}/${req.params.code}`);
  });
}
void pkDate;
