/**
 * Wholesale by voice: one spoken / typed sentence → the entry, filled in and checked, for the officer to
 * confirm (nothing is saved here). Questions ("Malik ka baqaya kitna hai?") get the answer in Urdu and English.
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, pkDate, pkDayStart } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { round2, pkr } from "../services.js";
import { PRODUCTS } from "../config.js";
import { parseWholesale } from "../ai/vision.js";
import { missingFor } from "../ai/parseWholesale.js";
import { clientDue, rateCard } from "./wholesale.js";
import { openOrders, promises, cheques } from "./wholesaleDesk.js";

export const wholesaleVoice = Router();

const PROD_UR: Record<string, string> = { PMG: "پیٹرول", HOBC: "ہائی آکٹین", HSD: "ڈیزل" };
const n = (v: number) => Math.round(v).toLocaleString("en-PK");

wholesaleVoice.post("/wholesale/ai/command", requirePerm("wholesale.view"), h(async (req) => {
  const t = tid(req);
  const b = parse(z.object({ text: z.string().trim().min(2).max(500), client_id: z.number().int().optional().nullable() }), req.body);
  const ctx = {
    clients: all("SELECT id, name, business_name alt FROM wholesale_clients WHERE tenant_id=? AND active=1", t) as { id: number; name: string; alt: string | null }[],
    tankers: all("SELECT id, number FROM tankers WHERE tenant_id=? AND active=1", t) as { id: number; number: string }[],
    drivers: all("SELECT id, name FROM drivers WHERE tenant_id=? AND active=1", t) as { id: number; name: string }[],
    clientId: b.client_id ?? null,
  };
  const p = await parseWholesale(b.text, ctx);
  p.missing = missingFor(p);
  const today = pkDate();

  // where the fuel comes from: the station holding the most of it
  const station = p.product ? get(`SELECT s.id, s.name, t.current_l FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? AND t.product=? ORDER BY t.current_l DESC LIMIT 1`, t, p.product) : null;
  let preview: Record<string, unknown> | null = null;
  let answer: { en: string; ur: string } | null = null;

  if (p.client_id) {
    const c = get("SELECT * FROM wholesale_clients WHERE id=?", p.client_id)!;
    const due = clientDue(c.id);
    const card = rateCard(c.id);
    const rate = p.rate ?? (p.product ? card[p.product]?.rate ?? null : null);
    const amount = p.intent === "supply" || p.intent === "return" ? (rate && p.litres ? round2(rate * p.litres) : null) : p.amount;
    const after = p.intent === "supply" && amount ? due + amount : p.intent === "return" && amount ? due - amount : p.intent === "payment" && amount ? due - amount : null;
    preview = {
      due: round2(due), rate, amount, due_after: after == null ? null : round2(after), credit_limit: c.credit_limit,
      over_limit: p.intent === "supply" && c.credit_limit > 0 && after != null && after > c.credit_limit,
      no_rate: (p.intent === "supply" || p.intent === "order") && !!p.product && !card[p.product]?.rate,
      stock_short: p.intent === "supply" && station && p.litres ? station.current_l < p.litres : false,
    };
    if (p.intent === "balance") {
      const last = get("SELECT MAX(txn_date) d FROM wholesale_txns WHERE client_id=? AND type='payment' AND voided=0", c.id)!.d;
      const ch = cheques(t, c.id).filter((q) => q.status === "in_hand" || q.status === "deposited");
      const pr = promises(t, c.id).find((x) => x.state === "open" || x.state === "today" || x.state === "broken");
      const ord = openOrders(t, c.id);
      const lastDays = last ? Math.floor((Date.now() - Date.parse(last)) / 86_400_000) : null;
      answer = {
        en: `${c.name} owes ${pkr(due)}${c.credit_limit ? ` (limit ${pkr(c.credit_limit)})` : ""}. Last payment ${lastDays == null ? "never" : lastDays === 0 ? "today" : `${lastDays} days ago`}.` +
          (ch.length ? ` Cheques not cleared: ${pkr(ch.reduce((a, q) => a + q.amount, 0))}.` : "") + (pr ? ` Promise: ${pkr(pr.amount)} on ${pr.promised_on} (${pr.state}).` : "") +
          (ord.length ? ` Open orders: ${ord.map((o) => `${n(o.litres)} L ${PRODUCTS[o.product]} for ${o.needed_on}`).join(", ")}.` : ""),
        ur: `${c.name} کے ذمے ${n(due)} روپے ہیں۔ آخری ادائیگی ${lastDays == null ? "کبھی نہیں" : lastDays === 0 ? "آج" : `${lastDays} دن پہلے`}۔` +
          (ch.length ? ` ${n(ch.reduce((a, q) => a + q.amount, 0))} روپے کے چیک ابھی کلیئر نہیں ہوئے۔` : "") + (pr ? ` ${n(pr.amount)} روپے کا وعدہ ${pr.promised_on} کا ہے۔` : "") +
          (ord.length ? ` ${ord.length} آرڈر باقی ہیں۔` : ""),
      };
    }
  }
  if (p.intent === "today") {
    const d = get(`SELECT COALESCE(SUM(CASE WHEN type='supply' THEN litres END),0) l, COALESCE(SUM(CASE WHEN type='supply' THEN amount END),0) billed, COALESCE(SUM(CASE WHEN type='payment' THEN amount END),0) got, COUNT(CASE WHEN type='supply' THEN 1 END) n
      FROM wholesale_txns WHERE tenant_id=? AND voided=0 AND txn_date >= ?`, t, pkDayStart())!;
    const ord = openOrders(t).filter((o) => o.needed_on <= today);
    const due = round2(all("SELECT id FROM wholesale_clients WHERE tenant_id=? AND active=1", t).reduce((a, c) => a + clientDue(c.id), 0));
    answer = {
      en: `Today: ${n(d.l)} L supplied in ${d.n} entries (billed ${pkr(d.billed)}), ${pkr(d.got)} received. ${ord.length ? `${ord.length} order(s) still to deliver today. ` : ""}Total due with all clients ${pkr(due)}.`,
      ur: `آج ${n(d.l)} لیٹر سپلائی ہوا (${n(d.billed)} روپے کا بل)، ${n(d.got)} روپے وصول ہوئے۔ ${ord.length ? `${ord.length} آرڈر آج دینے باقی ہیں۔ ` : ""}سب کلائنٹس کے ذمے کل ${n(due)} روپے ہیں۔`,
    };
  }
  if (p.intent === "trip") {
    preview = { drops: p.drops.map((d) => { const r = p.product ? rateCard(d.client_id)[p.product]?.rate ?? null : null; return { ...d, rate: r, amount: r ? round2(r * d.litres) : null }; }) };
  }
  // what was understood, said back in Urdu (read aloud by the phone)
  const who = p.client_name ?? "";
  const say: Record<string, string> = {
    supply: `${who} کو ${n(p.litres ?? 0)} لیٹر ${PROD_UR[p.product ?? ""] ?? ""} سپلائی`,
    return: `${who} سے ${n(p.litres ?? 0)} لیٹر ${PROD_UR[p.product ?? ""] ?? ""} واپس`,
    payment: `${who} سے ${n(p.amount ?? 0)} روپے وصول`,
    order: `${who} کا ${p.date ?? ""} کو ${n(p.litres ?? 0)} لیٹر ${PROD_UR[p.product ?? ""] ?? ""} کا آرڈر`,
    promise: `${who} ${p.date ?? ""} کو ${n(p.amount ?? 0)} روپے دیں گے`,
    cheque: `${who} کا ${n(p.amount ?? 0)} روپے کا چیک`,
    trip: `ایک ٹینکر، ${p.drops.length} جگہ ڈراپ، کل ${n(p.litres ?? 0)} لیٹر`,
  };
  return { ...p, station_id: station?.id ?? null, station_name: station?.name ?? null, preview, answer, confirm_ur: say[p.intent] ? `${say[p.intent]}۔ ٹھیک ہے؟` : null };
}));
