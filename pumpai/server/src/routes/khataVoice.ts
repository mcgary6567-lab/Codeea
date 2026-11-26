/**
 * Khata by voice: one sentence → payment / charge / reminder / bill to confirm, or an answer
 * ("City Bakers ka khata kitna hai?", "sab se zyada udhaar kis ka hai?") in Urdu and English.
 * Nothing is saved here — the screen saves through the normal khata routes after the user confirms.
 */
import { Router } from "express";
import { z } from "zod";
import { all, get } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { round2, pkr, currentPrices } from "../services.js";
import { parseKhata } from "../ai/vision.js";
import { khataMissing } from "../ai/parseKhata.js";

export const khataVoice = Router();
const n = (v: number) => Math.round(v).toLocaleString("en-PK");

khataVoice.post("/khata/ai/command", requirePerm("khata.manage"), h(async (req) => {
  const t = tid(req);
  const b = parse(z.object({ text: z.string().trim().min(2).max(500), customer_id: z.number().int().optional().nullable() }), req.body);
  const customers = all("SELECT id, name FROM customers WHERE tenant_id=? AND (credit_limit > 0 OR balance <> 0)", t) as { id: number; name: string }[];
  const prices = Object.fromEntries(Object.entries(currentPrices(t)).map(([k, v]) => [k, v.price]));
  const p = await parseKhata(b.text, { customers, customerId: b.customer_id ?? null, prices });
  p.missing = khataMissing(p);
  let preview: Record<string, unknown> | null = null;
  let answer: { en: string; ur: string } | null = null;
  if (p.customer_id) {
    const c = get("SELECT * FROM customers WHERE id=? AND tenant_id=?", p.customer_id, t)!;
    const last = get("SELECT MAX(created_at) d FROM khata_ledger WHERE customer_id=? AND type='credit'", c.id)!.d;
    const lastDays = last ? Math.floor((Date.now() - Date.parse(last)) / 86_400_000) : null;
    const after = p.amount ? (p.intent === "payment" ? c.balance - p.amount : p.intent === "charge" ? c.balance + p.amount : null) : null;
    preview = { balance: round2(c.balance), credit_limit: c.credit_limit, after: after == null ? null : round2(after), blocked: Boolean(c.khata_blocked), phone: c.phone,
      over_limit: p.intent === "charge" && c.credit_limit > 0 && after != null && after > c.credit_limit };
    if (p.intent === "balance") answer = {
      en: `${c.name} owes ${pkr(c.balance)}${c.credit_limit ? ` of a ${pkr(c.credit_limit)} limit` : ""}. Last payment ${lastDays == null ? "never" : lastDays === 0 ? "today" : `${lastDays} days ago`}.${c.khata_blocked ? " Khata is on hold." : ""}`,
      ur: `${c.name} کے ذمے ${n(c.balance)} روپے ہیں${c.credit_limit ? `، حد ${n(c.credit_limit)} روپے` : ""}۔ آخری ادائیگی ${lastDays == null ? "کبھی نہیں" : lastDays === 0 ? "آج" : `${lastDays} دن پہلے`}۔${c.khata_blocked ? " کھاتہ روکا ہوا ہے۔" : ""}`,
    };
  }
  if (p.intent === "top") {
    const top = all("SELECT name, balance FROM customers WHERE tenant_id=? AND balance > 0 ORDER BY balance DESC LIMIT 5", t);
    const total = get("SELECT COALESCE(SUM(balance),0) v FROM customers WHERE tenant_id=? AND balance > 0", t)!.v;
    answer = {
      en: `Total khata due ${pkr(total)}. Most: ${top.map((x, i) => `${i + 1}. ${x.name} ${pkr(x.balance)}`).join(", ")}.`,
      ur: `کل کھاتہ ${n(total)} روپے۔ سب سے زیادہ: ${top.map((x, i) => `${i + 1}۔ ${x.name} ${n(x.balance)} روپے`).join("، ")}۔`,
    };
  }
  const who = p.customer_name ?? "";
  const say: Record<string, string> = {
    payment: `${who} سے ${n(p.amount ?? 0)} روپے وصول`, charge: `${who} کے کھاتے میں ${n(p.amount ?? 0)} روپے`,
    reminder: `${who} کو واٹس ایپ پر یاد دہانی`, bill: `${who} کو پچھلے مہینے کا بل`,
  };
  return { ...p, preview, answer, confirm_ur: say[p.intent] ? `${say[p.intent]}۔ ٹھیک ہے؟` : null };
}));
