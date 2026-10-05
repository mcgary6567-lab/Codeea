/**
 * Khata by voice / typing — Urdu, Roman Urdu or English:
 *   "Police Station Kahna se 20 hazar naqd mile"           → payment received
 *   "Rescue 1122 ke khate mein 5000 likh do tyre repair"     → charge (udhaar)
 *   "City Bakers ka khata kitna hai"                         → balance
 *   "City Bakers ko reminder bhejo"                          → WhatsApp reminder
 *   "City Bakers ko bill bhejo"                              → last month's bill on WhatsApp
 *   "sab se zyada udhaar kis ka hai"                         → top debtors
 */
import { DIGITS, MAX_AMOUNT, MAX_LITRES, METHOD, PRODUCT, matchNamed, quantities, splitUnits, stripNameDigits, type Named } from "./parseWholesale.js";

export type KhataIntent = "payment" | "charge" | "balance" | "reminder" | "bill" | "top" | "unknown";
export type ParsedKhata = {
  intent: KhataIntent; heard: string; engine: "rules" | "claude";
  customer_id: number | null; customer_name: string | null; candidates: Named[];
  amount: number | null; method: string | null; note: string | null; litres: number | null; product: string | null; missing: string[];
};

const NEED: Record<KhataIntent, (keyof ParsedKhata)[]> = {
  payment: ["customer_id", "amount"], charge: ["customer_id", "amount"], balance: ["customer_id"], reminder: ["customer_id"], bill: ["customer_id"], top: [], unknown: [],
};
export const khataMissing = (p: ParsedKhata) => NEED[p.intent].filter((k) => p[k] == null) as string[];

export function parseKhataText(raw: string, ctx: { customers: Named[]; customerId?: number | null; prices?: Record<string, number> }): ParsedKhata {
  const heard = raw.trim();
  let t = ` ${heard.replace(/[۰-۹٠-٩]/g, (d) => DIGITS[d]).replace(/(\d),(\d)/g, "$1$2").toLowerCase().replace(/[،,؟?!]/g, " ")} `;
  t = splitUnits(t);
  const out: ParsedKhata = { intent: "unknown", heard, engine: "rules", customer_id: null, customer_name: null, candidates: [], amount: null, method: null, note: null, litres: null, product: null, missing: [] };
  const m = matchNamed(ctx.customers, t);
  if (m.best) { out.customer_id = m.best.id; out.customer_name = m.best.name; }
  out.candidates = m.candidates;
  if (!out.customer_id && ctx.customerId) { const c = ctx.customers.find((x) => x.id === ctx.customerId); if (c) { out.customer_id = c.id; out.customer_name = c.name; } }
  // numbers that are part of the customer's name ("Rescue 1122") are not amounts — where they stand by the name
  t = stripNameDigits(t, out.customer_name, m.best ? m.spans : []);
  const q = quantities(t).filter((x) => x.value <= (x.litres ? MAX_LITRES : MAX_AMOUNT));
  const lit = q.find((x) => x.litres);
  out.product = PRODUCT.find(([, re]) => re.test(t))?.[0] ?? null;
  if (lit) out.litres = lit.value;
  out.amount = q.find((x) => !x.litres && x.value >= 10)?.value ?? null;
  // "20 litre diesel udhaar" with no rupees: worth litres × today's pump price
  if (!out.amount && out.litres && out.product && ctx.prices?.[out.product]) out.amount = Math.round(out.litres * ctx.prices[out.product]);

  const kw = {
    top: /(sab se zyada|sabse zyada|kis ka|kis kis|\btop\b|sab se ziada|\bmost\b|highest|biggest|largest|who owes|سب سے زیادہ|کس کا|کس کس)/.test(t) && !out.customer_id,
    balance: /(kitna|kitne|kitni|baqaya|baqi|balance|hisab|hisaab|کتنا|کتنے|کتنی|بقایا|باقی|حساب|بیلنس)/.test(t),
    reminder: /(reminder|yaad|yad dahani|یاد|ریمائنڈر)/.test(t),
    bill: /(bill|statement|بل|اسٹیٹمنٹ)/.test(t) && /(bhej|send|بھیج)/.test(t),
    charge: /(likh|likho|daal|dal do|udhaar|udhar|charge|naam|khate mein|کھاتے میں|لکھ|ڈال|ادھار|چارج)/.test(t),
    pay: /(mila|mile|mili|diye|diya|de gaya|de gaye|de gayi|jama|payment|received|wasool|aaye|aaya|chuka|lauta|ملا|ملے|ملی|دیے|دیا|جمع|وصول|آئے|چکا|لوٹا)/.test(t),
    // "udhaar wapas mila", "udhaar chuka diya": the credit is being paid back
    repay: /(udhaar|udhar|qarz|ادھار|قرض)/.test(t) && /(wapas|wapis|chuka|chukaya|lauta|lautaya|واپس|چکا|لوٹا)/.test(t),
  };
  if (kw.top && !out.amount) out.intent = "top";
  else if (kw.reminder && out.customer_id) out.intent = "reminder";
  else if (kw.bill && out.customer_id) out.intent = "bill";
  else if (kw.balance && !out.amount) out.intent = "balance";
  else if (out.amount && kw.pay && (!kw.charge || kw.repay)) out.intent = "payment";
  else if (out.amount && kw.charge) out.intent = "charge";
  else if (out.amount && out.litres) out.intent = "charge";
  else if (out.amount) out.intent = "payment";
  if (out.intent === "payment") out.method = METHOD.find(([, re]) => re.test(t))?.[0] ?? "Cash";
  if (out.intent === "charge") {
    // what the charge is for: the words after "for / ka / ki" that are not names or numbers
    const note = heard.replace(/\d[\d,.]*/g, " ").split(/\s+/).filter((w) => w.length > 2 && !/^(ke|ka|ki|ko|se|mein|likh|likho|daal|do|udhaar|udhar|khate|khata|charge|rupay|rupees|rs|hazar|lakh|litre)$/i.test(w));
    const name = new Set((out.customer_name ?? "").toLowerCase().split(/\s+/));
    const rest = note.filter((w) => !name.has(w.toLowerCase())).join(" ").trim();
    out.note = out.litres && out.product ? `${out.litres} L ${out.product === "HSD" ? "Diesel" : out.product === "PMG" ? "Petrol" : "Hi-Octane"}` : rest || null;
  }
  out.missing = khataMissing(out);
  return out;
}
