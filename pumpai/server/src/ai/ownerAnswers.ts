/**
 * Instant answers to the owner's everyday questions (works without an AI key), e.g.
 *   "aaj ki sale?"  "kal kitna kharcha hua"  "stock kitna hai"  "cash kitna hai"  "kis ne paise dene hain"
 * Answers come from the same day book and balances as the dashboard and reports.
 */
import { pkr } from "../services.js";
import { pkDate, pkStart, pkEnd } from "../db.js";
import { dayBook, balances } from "../routes/reports.js";
import { cashPosition } from "../routes/backoffice.js";
import { bankAccounts } from "../routes/banks.js";
import { clientDue } from "../routes/wholesale.js";
import { supplierOwed } from "../routes/suppliers.js";
import { all, get } from "../db.js";
import { matchNamed, romanize } from "./parseWholesale.js";

const has = (q: string, ...words: string[]) => words.some((w) => q.includes(w));

export function quickAnswer(t: number, question: string): string | null {
  const q = ` ${question.toLowerCase()} `;
  const yesterday = has(q, "kal ", "yesterday", "کل ");
  const day = yesterday ? pkDate(Date.now() - 86_400_000) : pkDate();
  const b = yesterday ? dayBook(t, pkStart(day), pkEnd(day)) : dayBook(t);
  const when = yesterday ? `Kal (${day})` : "Aaj";
  const parts: string[] = [];

  // one party by name: "City Bakers ka khata kitna hai", "PSO ko kitna dena hai"
  const who = partyAnswer(t, question);
  if (who) parts.push(who);
  if (has(q, "sale", "bikri", "becha", "bika", "biki", "bik ", "sold", "revenue", "kamai", "litre", "liter", "diesel", " petrol ", " petrol?", "سیل", "فروخت", "بکری", "بکا", "آمدن", "لیٹر", "ڈیزل", "پیٹرول")) {
    const s = b.sales;
    parts.push(`💰 ${when} ki sale: ${pkr(s.revenue)} — pump ${pkr(s.retail)} (${Math.round(s.retail_litres).toLocaleString()} L, ${s.txns} sales)${s.wholesale ? `, wholesale ${pkr(s.wholesale)}` : ""}.\nCash ${pkr(s.cash)} · Digital ${pkr(s.digital)} · Khata ${pkr(s.khata)}.${b.profit.net != null ? ` Munafa (andaza) ${pkr(b.profit.net)}.` : ""}`);
  }
  if (has(q, "kharch", "kharcha", "expense", "خرچ")) {
    parts.push(`🧾 ${when} ka kharcha: ${pkr(b.expenses.total)}${b.expenses.by_category.length ? ` — ${b.expenses.by_category.slice(0, 4).map((c: any) => `${c.category} ${pkr(c.amount)}`).join(", ")}` : ""}.${b.expenses.pending.count ? ` ${b.expenses.pending.count} approval ke intezar mein.` : ""}`);
  }
  if (has(q, "stock", "tank", "tel ", "bacha", "baqi", "سٹاک", "ٹینک", "تیل")) {
    parts.push(`🛢️ Stock: ${b.stock.products.map((p: any) => `${p.name} ${Math.round(p.closing_l).toLocaleString()} L`).join(", ")}. Kul ${b.stock.litres.toLocaleString()} L, maliyat ${pkr(b.stock.value_at_cost ?? 0)} (khareed rate par).`);
  }
  if (has(q, "supply", "tanker", "delivery", "aaya", "سپلائی", "ٹینکر")) {
    parts.push(`🚛 ${when} supply: ${b.supply.deliveries} tanker, ${b.supply.litres.toLocaleString()} L (${pkr(b.supply.cost)}).`);
  }
  if (has(q, "cash", "naqd", "paise kitne", "کیش", "نقد")) {
    const c = cashPosition(t);
    parts.push(`💵 Office mein cash (hisaab se): ${pkr(c.cash_in_hand)}${c.last_count ? ` — aakhri ginti ${pkr(c.last_count.amount)}` : ""}. Aaj aaya ${pkr(c.total_in)}, gaya ${pkr(c.total_out)} (bank ${pkr(c.outs.bank_deposits)}).`);
  }
  if (has(q, "bank", "بینک")) {
    const bk = bankAccounts(t);
    parts.push(`🏦 Bankon mein: ${pkr(bk.total)} — ${bk.accounts.filter((a) => a.active).map((a) => `${a.bank.replace(/\s*\(.*\)/, "")} ${pkr(a.balance)}`).join(", ") || "koi account nahi"}.`);
  }
  if (has(q, "cheque", "chek", "check", "چیک")) {
    const ci = get(`SELECT COUNT(*) n, COALESCE(SUM(amount),0) v FROM (SELECT amount FROM wholesale_cheques WHERE tenant_id=? AND status IN ('in_hand','deposited') UNION ALL SELECT amount FROM cheques WHERE tenant_id=? AND direction='in' AND status IN ('in_hand','deposited'))`, t, t)!;
    const co = get("SELECT COUNT(*) n, COALESCE(SUM(amount),0) v FROM cheques WHERE tenant_id=? AND direction='out' AND status='issued'", t)!;
    parts.push(`🧾 Cheque: ${ci.n} cheque (${pkr(ci.v)}) mile hain jo abhi clear nahi hue; ${co.n} hamare diye hue (${pkr(co.v)}) bank se abhi cash nahi hue.`);
  }
  if (!who && has(q, "dene", "dena", "lene", "lena", "owe", "udhaar", "udhar", "baqaya", "receivable", "payable", "khata", "ادھار", "بقایا", "کھاتہ")) {
    const bal = balances(t);
    parts.push(`📒 Log hamein denge: ${pkr(bal.receivables.total)} — ${bal.receivables.list.slice(0, 3).map((r: any) => `${r.name} ${pkr(r.amount)}`).join(", ") || "koi nahi"}.\nHum ne dene hain: ${pkr(bal.payables.total)}${bal.payables.list.length ? ` — ${bal.payables.list.slice(0, 2).map((r: any) => `${r.name} ${pkr(r.amount)}`).join(", ")}` : ""}.`);
  }
  if (has(q, "shift", "short", "kam ", "شفٹ")) {
    parts.push(`🕘 ${when}: ${b.shifts.closed} shift band hui, cash ${b.shifts.variance < 0 ? `short ${pkr(-b.shifts.variance)}` : `theek${b.shifts.variance ? ` (+${pkr(b.shifts.variance)})` : ""}`}.`);
  }
  return parts.length ? parts.join("\n\n") : null;
}

/** Everything at a glance, when the question did not match a topic. */
export function summaryAnswer(t: number): string {
  return quickAnswer(t, "sale kharch stock cash dene") ?? "";
}

/** A question about one customer / wholesale client / supplier, matched by name (Urdu or Roman). */
function partyAnswer(t: number, question: string): string | null {
  const text = romanize(question.toLowerCase());
  const cust = all("SELECT id, name, balance FROM customers WHERE tenant_id=? AND (balance <> 0 OR credit_limit > 0)", t);
  const ws = all("SELECT id, name, business_name alt FROM wholesale_clients WHERE tenant_id=? AND active=1", t);
  const sup = all("SELECT id, name FROM suppliers WHERE tenant_id=?", t);
  const words = new Set(text.split(/[^a-z0-9]+/).filter(Boolean));
  // matched words of the name, counting common ones too ("Petroleum", "Services") as a tie-breaker
  const score = (n: any) => `${n.name} ${n.alt ?? ""}`.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length >= 3 && words.has(w)).length;
  const pick = (list: any[]) => { const m = matchNamed(list, text); return { best: m.best as any, n: m.best ? m.spans.length * 10 + score(m.best) : 0 }; };
  const found = [pick(cust), pick(ws), pick(sup)];
  // when several match, keep the one(s) with the most matched words ("Malik Petroleum" → the client, not "Malik Sarwar")
  const top = Math.max(...found.map((f) => (f.best ? f.n : 0)));
  const [c, w, s] = found.map((f) => (f.best && f.n === top ? f.best : null));
  const out: string[] = [];
  if (c) out.push(`📒 ${c.name} ka khata: ${pkr(c.balance)}${c.balance > 0 ? " (hamein dene hain)" : c.balance < 0 ? " (advance jama hai)" : " (saaf)"}.`);
  if (w) { const due = clientDue(w.id); out.push(`🚛 ${w.name} (wholesale) ke zimme: ${pkr(due)}.`); }
  if (s) out.push(`🏭 ${s.name} ko hum ne dene hain: ${pkr(supplierOwed(s.id))}.`);
  return out.length ? out.join("\n") : null;
}
