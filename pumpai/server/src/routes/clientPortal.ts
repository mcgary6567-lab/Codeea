/**
 * Wholesale client's own khata page: a private short link (/w/<code>) that asks for a PIN made by
 * the system. With the right PIN the client sees the balance, their rates, every supply (litres,
 * rate, tanker, driver, place) and payment, and the monthly statements. Five wrong PINs lock the
 * page for 15 minutes; a new PIN or a new link can be made at any time from the client's page.
 */
import { Router, type Request, type Response } from "express";
import express from "express";
import crypto from "node:crypto";
import bcrypt from "bcryptjs";
import jwt from "jsonwebtoken";
import { all, get, run, now, pkDate } from "../db.js";
import { h, tid, requirePerm } from "../auth.js";
import { AppError, normalizePhone } from "../services.js";
import { config, PRODUCTS } from "../config.js";
import { statement, rateCard, clientDue } from "./wholesale.js";
import { billLink, prevMonth } from "../billing.js";
import { sendDirect } from "../whatsapp/cloud.js";
import { logoTag } from "./setup.js";

export const clientPortalAdmin = Router();
export const clientPortalPublic = Router();

const esc = (v: unknown) => String(v ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]!));
const rs = (v: number) => `Rs ${Math.round(v).toLocaleString("en-IN")}`;
const n2 = (v: number) => Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 });
const MAX_FAILS = 5, LOCK_MIN = 15;

const linkOf = (code: string) => `${config.publicUrl}/w/${code}`;
const newCode = () => crypto.randomBytes(8).toString("base64url");
const newPin = () => String(crypto.randomInt(0, 1_000_000)).padStart(6, "0");

function own(t: number, id: number) {
  const c = get("SELECT * FROM wholesale_clients WHERE id=? AND tenant_id=?", id, t);
  if (!c) throw new AppError(404, "Wholesale client not found");
  return c;
}
/** A fresh 6-digit PIN (and a link if there is none yet); every older PIN and remembered phone stops working. */
function issuePin(c: any) {
  const pin = newPin();
  const code = c.portal_code ?? newCode();
  run("UPDATE wholesale_clients SET portal_code=?, portal_pin_hash=?, portal_v=COALESCE(portal_v,0)+1, portal_fails=0, portal_locked_until=NULL, portal_on=1 WHERE id=?",
    code, bcrypt.hashSync(pin, 8), c.id);
  return { url: linkOf(code), pin };
}
const status = (c: any) => ({ url: c.portal_code ? linkOf(c.portal_code) : null, has_pin: Boolean(c.portal_pin_hash), enabled: Boolean(c.portal_on), last_seen: c.portal_seen_at ?? null });

clientPortalAdmin.get("/wholesale/clients/:id/portal", requirePerm("wholesale.view"), h((req) => status(own(tid(req), Number(req.params.id)))));
clientPortalAdmin.post("/wholesale/clients/:id/portal/pin", requirePerm("wholesale.manage"), h((req) => issuePin(own(tid(req), Number(req.params.id)))));
/** Shared the link with the wrong person? A new link and PIN; the old ones stop working. */
clientPortalAdmin.post("/wholesale/clients/:id/portal/new-link", requirePerm("wholesale.manage"), h((req) => {
  const c = own(tid(req), Number(req.params.id));
  run("UPDATE wholesale_clients SET portal_code=? WHERE id=?", newCode(), c.id);
  return issuePin(get("SELECT * FROM wholesale_clients WHERE id=?", c.id));
}));
clientPortalAdmin.post("/wholesale/clients/:id/portal/off", requirePerm("wholesale.manage"), h((req) => {
  const c = own(tid(req), Number(req.params.id));
  run("UPDATE wholesale_clients SET portal_on=0 WHERE id=?", c.id);
  return status(get("SELECT * FROM wholesale_clients WHERE id=?", c.id));
}));
/** New PIN + link sent to the client on WhatsApp. */
clientPortalAdmin.post("/wholesale/clients/:id/portal/send", requirePerm("wholesale.manage"), h(async (req) => {
  const c = own(tid(req), Number(req.params.id));
  if (!c.phone) throw new AppError(400, "Add the client's WhatsApp number first");
  const p = issuePin(c);
  const tenant = get("SELECT name FROM tenants WHERE id=?", tid(req))!.name;
  await sendDirect(tid(req), { ...c, phone: normalizePhone(c.phone) }, "wholesale_portal", `client:${c.id}`,
    `${c.name}\n📒 Apna khata kabhi bhi dekhein (baqaya, har supply, payment aur statement):\n${p.url}\nPIN: ${p.pin}\nYeh PIN kisi ko na batayein.\n— ${tenant}`);
  return { ...p, sent: true };
}));

/* ---------------- public page ---------------- */
const page = (title: string, body: string, tenantId?: number) => `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>${esc(title)}</title><style>:root{color-scheme:light}body{font:15px/1.45 system-ui,sans-serif;margin:0;background:#f1f5f9;color:#0f172a}.w{max-width:720px;margin:0 auto;padding:14px}
.c{background:#fff;border-radius:14px;padding:16px;margin-bottom:12px}h1{font-size:20px;margin:0}.m{color:#64748b;font-size:12px}.big{font-size:24px;font-weight:700;white-space:nowrap}.g{color:#047857}.red{color:#b91c1c}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-top:10px}.t{background:#f8fafc;border-radius:10px;padding:10px}table{width:100%;border-collapse:collapse}
td,th{padding:7px 4px;border-bottom:1px solid #e2e8f0;vertical-align:top;text-align:left}th{font-size:11px;color:#64748b;text-transform:uppercase}.r{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.row{display:flex;gap:10px;justify-content:space-between;padding:9px 0;border-bottom:1px solid #e2e8f0}.l{min-width:0}.rr{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
input{font-size:28px;letter-spacing:10px;text-align:center;width:100%;box-sizing:border-box;padding:12px;border:2px solid #cbd5e1;border-radius:12px}button,a.b{display:block;width:100%;box-sizing:border-box;text-align:center;background:#064e3b;color:#fff;border:0;padding:13px;border-radius:12px;font-size:16px;font-weight:600;text-decoration:none;margin-top:10px;cursor:pointer}
.err{background:#fee2e2;color:#991b1b;padding:10px;border-radius:10px;margin-top:10px}.ur{font-family:"Noto Nastaliq Urdu",serif}ul{padding-left:18px;margin:6px 0}@media print{.np{display:none}body{background:#fff}}</style></head>
<body><div class=w>${tenantId ? `<div class=c style="display:flex;gap:10px;align-items:center">${logoTag(tenantId, "height:36px;max-width:120px;object-fit:contain")}<b>${esc(get("SELECT name FROM tenants WHERE id=?", tenantId)?.name)}</b></div>` : ""}${body}</div></body></html>`;

function pinForm(c: any, error?: string) {
  return page(`${c.name} — khata`, `<div class=c><h1>${esc(c.name)}</h1><p class=m>Enter the 6-digit PIN the pump sent you · <span class=ur>اپنا پن درج کریں</span></p>
<form method=post><input name=pin inputmode=numeric autocomplete=one-time-code pattern="[0-9]{6}" maxlength=6 required autofocus placeholder="••••••">
<label class=m style="display:flex;gap:6px;align-items:center;margin-top:10px"><input type=checkbox name=remember value=1 style="width:auto;font-size:14px" checked> Remember this phone for 30 days</label>
<button>Open my khata · کھاتہ دیکھیں</button></form>${error ? `<div class=err>${esc(error)}</div>` : ""}</div>`, c.tenant_id);
}

function khataPage(c: any) {
  const t = c.tenant_id;
  const since = pkDate(Date.now() - 60 * 86_400_000);
  const s = statement(t, c.id, since);
  const due = clientDue(c.id);
  const card = rateCard(c.id);
  const monthStart = new Date(pkDate().slice(0, 7) + "-01T00:00:00+05:00").toISOString();
  const m = get(`SELECT COALESCE(SUM(CASE WHEN type='supply' THEN litres END),0) l, COALESCE(SUM(CASE WHEN type='supply' THEN amount END),0) billed,
    COALESCE(SUM(CASE WHEN type='payment' THEN amount END),0) paid FROM wholesale_txns WHERE client_id=? AND voided=0 AND txn_date >= ?`, c.id, monthStart)!;
  const months = Array.from({ length: 6 }, (_, i) => { const d = new Date(Date.parse(`${pkDate().slice(0, 7)}-15T00:00:00Z`)); d.setUTCMonth(d.getUTCMonth() - i); return d.toISOString().slice(0, 7); });
  const rows = [...s.lines].reverse().filter((l: any) => !l.voided).slice(0, 80).map((l: any) => {
    const what = l.type === "supply" ? `<b>${esc(PRODUCTS[l.product] ?? l.product)}</b> · ${n2(l.litres)} L × ${n2(l.rate)}`
      : l.type === "payment" ? `<b class=g>Payment received</b>` : l.type === "return" ? `Fuel returned · ${n2(l.litres)} L` : `Adjustment`;
    const sub = l.type === "supply" ? [l.vehicle_no && `🚛 ${l.vehicle_no}`, l.driver_name && `👤 ${l.driver_name}`, l.location && `📍 ${l.location}`, l.ref]
      : l.type === "payment" ? [l.method, l.ref] : [l.note];
    const amt = l.debit ? `<b>${n2(l.debit)}</b>` : `<b class=g>−${n2(l.credit)}</b>`;
    return `<div class=row><div class=l><div class=m>${esc(new Date(l.txn_date).toLocaleDateString("en-PK", { timeZone: "Asia/Karachi", day: "2-digit", month: "short", year: "numeric" }))}</div>${what}<div class=m>${esc(sub.filter(Boolean).join(" · "))}</div></div>
      <div class=rr>${amt}<div class=m>bal ${n2(l.balance)}</div></div></div>`;
  }).join("");
  return page(`${c.name} — khata`, `<div class=c><h1>${esc(c.name)}</h1><div class=m>${esc([c.business_name, c.city].filter(Boolean).join(" · "))}</div>
<div class=grid><div class=t><div class=m>Balance due · بقایا</div><div class="big ${due > 0 ? "red" : "g"}">${rs(due)}</div></div>
<div class=t><div class=m>Credit limit</div><div class=big style="font-size:20px">${c.credit_limit ? rs(c.credit_limit) : "—"}</div>${c.credit_limit ? `<div class=m>Available ${rs(Math.max(0, c.credit_limit - due))}</div>` : ""}</div>
<div class=t><div class=m>This month</div><div class=big style="font-size:20px">${Math.round(m.l).toLocaleString("en-IN")} L</div><div class=m>Billed ${rs(m.billed)} · paid ${rs(m.paid)}</div></div></div></div>
<div class=c><b>Your rates today · آج کا ریٹ</b><table>${Object.entries(card).map(([p, r]) => `<tr><td>${esc(PRODUCTS[p] ?? p)}</td><td class=r><b>${r.rate != null ? `Rs ${r.rate.toFixed(2)} / L` : "—"}</b></td></tr>`).join("") || "<tr><td class=m>No rate set</td></tr>"}</table></div>
<div class=c><b>Last 60 days</b><div class=m>Opening balance ${rs(s.opening_balance)} · supplies in black, payments in green (−)</div>${rows || "<p class=m>No entries</p>"}</div>
<div class=c><b>Monthly statements</b><ul>${months.map((mm) => `<li><a href="${esc(billLink(t, "w", c.id, mm))}">${esc(new Date(`${mm}-15T00:00:00Z`).toLocaleDateString("en-PK", { month: "long", year: "numeric" }))}</a></li>`).join("")}</ul></div>
<div class=np><a class=b href="javascript:print()">Print / save as PDF</a><form method=post action="${esc(c.portal_code)}/logout"><button style="background:#e2e8f0;color:#0f172a">Lock this page</button></form></div>
<div class=m style="text-align:center;margin-top:8px">Updated ${esc(new Date().toLocaleString("en-PK", { timeZone: "Asia/Karachi" }))}</div>`, t);
}

const cookieName = (code: string) => `wp_${code}`;
function remembered(req: Request, c: any) {
  const raw = String(req.headers.cookie ?? "").split(/;\s*/).find((x) => x.startsWith(cookieName(c.portal_code) + "="));
  if (!raw) return false;
  try { const p = jwt.verify(decodeURIComponent(raw.split("=")[1]), config.jwtSecret) as any; return p.w === c.id && p.v === c.portal_v; } catch { return false; }
}
const byCode = (code: string) => get("SELECT * FROM wholesale_clients WHERE portal_code=? AND portal_on=1 AND active=1", code);
const gone = (res: Response) => res.status(404).type("html").send(page("Not found", "<div class=c>This link is not valid any more. Ask the pump for a new one.</div>"));

clientPortalPublic.get("/w/:code", (req, res) => {
  const c = byCode(req.params.code);
  if (!c || !c.portal_pin_hash) return gone(res);
  res.setHeader("cache-control", "no-store");
  if (remembered(req, c)) { run("UPDATE wholesale_clients SET portal_seen_at=? WHERE id=?", now(), c.id); return res.type("html").send(khataPage(c)); }
  res.type("html").send(pinForm(c));
});
clientPortalPublic.post("/w/:code", express.urlencoded({ extended: false, limit: "2kb" }), (req, res) => {
  const c = byCode(req.params.code);
  if (!c || !c.portal_pin_hash) return gone(res);
  res.setHeader("cache-control", "no-store");
  if (c.portal_locked_until && Date.parse(c.portal_locked_until) > Date.now())
    return res.status(429).type("html").send(pinForm(c, `Too many wrong PINs. Try again after ${Math.ceil((Date.parse(c.portal_locked_until) - Date.now()) / 60_000)} minutes.`));
  const pin = String(req.body?.pin ?? "").trim();
  if (!/^\d{6}$/.test(pin) || !bcrypt.compareSync(pin, c.portal_pin_hash)) {
    const fails = (c.portal_fails ?? 0) + 1;
    const lock = fails >= MAX_FAILS ? new Date(Date.now() + LOCK_MIN * 60_000).toISOString() : null;
    run("UPDATE wholesale_clients SET portal_fails=?, portal_locked_until=? WHERE id=?", lock ? 0 : fails, lock, c.id);
    return res.status(401).type("html").send(pinForm(c, lock ? `Wrong PIN. The page is locked for ${LOCK_MIN} minutes.` : `Wrong PIN · غلط پن (${MAX_FAILS - fails} tries left)`));
  }
  run("UPDATE wholesale_clients SET portal_fails=0, portal_locked_until=NULL, portal_seen_at=? WHERE id=?", now(), c.id);
  if (req.body?.remember) {
    const tok = jwt.sign({ w: c.id, v: c.portal_v }, config.jwtSecret, { expiresIn: "30d" });
    res.setHeader("set-cookie", `${cookieName(c.portal_code)}=${encodeURIComponent(tok)}; Path=/w/${c.portal_code}; Max-Age=${30 * 86400}; HttpOnly; SameSite=Lax${config.publicUrl.startsWith("https") ? "; Secure" : ""}`);
  }
  res.type("html").send(khataPage(c));
});
clientPortalPublic.post("/w/:code/logout", (req, res) => {
  res.setHeader("set-cookie", `${cookieName(req.params.code)}=; Path=/w/${req.params.code}; Max-Age=0`);
  res.redirect(303, `/w/${req.params.code}`);
});
void prevMonth; void all;
