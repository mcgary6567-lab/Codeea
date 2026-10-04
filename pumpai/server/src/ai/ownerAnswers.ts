/**
 * Instant answers to the owner's everyday questions (works without an AI key), e.g.
 *   "aaj ki sale?"  "kal kitna kharcha hua"  "stock kitna hai"  "cash kitna hai"  "kis ne paise dene hain"
 * Answers come from the same day book and balances as the dashboard and reports.
 */
import { pkr } from "../services.js";
import { pkDate, pkStart, pkEnd } from "../db.js";
import { dayBook, balances } from "../routes/reports.js";
import { cashPosition } from "../routes/backoffice.js";

const has = (q: string, ...words: string[]) => words.some((w) => q.includes(w));

export function quickAnswer(t: number, question: string): string | null {
  const q = ` ${question.toLowerCase()} `;
  const yesterday = has(q, "kal ", "yesterday", "کل ");
  const day = yesterday ? pkDate(Date.now() - 86_400_000) : pkDate();
  const b = yesterday ? dayBook(t, pkStart(day), pkEnd(day)) : dayBook(t);
  const when = yesterday ? `Kal (${day})` : "Aaj";
  const parts: string[] = [];

  if (has(q, "sale", "bikri", "becha", "revenue", "kamai", "سیل", "فروخت", "بکری", "آمدن")) {
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
  if (has(q, "dene", "dena", "lene", "lena", "owe", "udhaar", "udhar", "baqaya", "receivable", "payable", "khata", "ادھار", "بقایا", "کھاتہ")) {
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
