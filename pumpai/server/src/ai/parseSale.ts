/**
 * Turn a spoken / typed sentence into a POS sale, e.g.
 *   "police station kahna 20 litre diesel slip 7781"  →  HSD 20 L, khata, Police Station Kahna, slip 7781
 *   "do hazar ka petrol easypaisa"                     →  PMG Rs 2000, easypaisa
 *   "پیٹرول 500 روپے نقد"                                →  PMG Rs 500, cash
 * Rule-based so it works without an API key; Claude (when configured) handles freer speech.
 */
export type ParsedSale = {
  product: string | null; litres: number | null; amount: number | null; payment_method: string | null;
  customer_id: number | null; customer_name: string | null; vehicle_no: string | null; slip_no: string | null;
  candidates: { id: number; name: string }[]; heard: string; engine: "rules" | "claude";
};

const DIGITS: Record<string, string> = { "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4", "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9", "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9" };
const WORDS: Record<string, number> = {
  ek: 1, aik: 1, "ایک": 1, do: 2, "دو": 2, teen: 3, "تین": 3, char: 4, chaar: 4, "چار": 4, panch: 5, paanch: 5, "پانچ": 5,
  chay: 6, che: 6, "چھ": 6, saat: 7, "سات": 7, aath: 8, "آٹھ": 8, nau: 9, "نو": 9, das: 10, "دس": 10, bees: 20, "بیس": 20,
  tees: 30, "تیس": 30, chalees: 40, "چالیس": 40, pachas: 50, pachaas: 50, "پچاس": 50, sau: 100, "سو": 100,
};
const PRODUCT: [string, RegExp][] = [
  ["HOBC", /(hi[\s-]?octane|high[\s-]?octane|octane|hobc|آکٹین|اوکٹین|ہائی آکٹین)/i],
  ["HSD", /(diesel|deezal|dizal|hsd|ڈیزل|ڈیزل)/i],
  ["PMG", /(petrol|patrol|super|pmg|پیٹرول|پٹرول|سپر)/i],
];
const PAY: [string, RegExp][] = [
  ["khata", /(khata|khaata|udhaar|udhar|credit|کھاتہ|کھاتے|کھاتا|ادھار)/i],
  ["easypaisa", /(easy\s?paisa|easy\s?paisa|ایزی\s?پیسہ|ایزی\s?پیسا|ایزی پیسے)/i],
  ["jazzcash", /(jazz\s?cash|جاز\s?کیش)/i],
  ["card", /(card|atm|کارڈ)/i],
  ["raast", /(raast|راست)/i],
  ["cash", /(cash|naqd|naqad|کیش|نقد)/i],
];
const UNIT_L = "(?:litres?|liters?|ltrs?|l\\b|لیٹر|لٹر)";
const UNIT_RS = "(?:rupees?|rupay|rupaye|rupe|rs\\.?|روپے|روپیہ|روپئے)";
const THOUSAND = "(?:hazar|hazaar|hajar|thousand|ہزار)";
const STOP = new Set(["the", "and", "for", "station", "school", "office", "wala", "wali", "walay", "ka", "ki", "ke", "mein"]);

export function parseSaleText(raw: string, accounts: { id: number; name: string }[]): ParsedSale {
  const heard = raw.trim();
  let t = ` ${heard.replace(/[۰-۹٠-٩]/g, (d) => DIGITS[d]).replace(/(\d),(\d)/g, "$1$2").toLowerCase()} `;
  // number words → digits ("do hazar" → "2 hazar", "bees litre" → "20 litre")
  t = t.replace(/(?<=\s)([a-z]+|[؀-ۿ]+)(?=\s)/g, (w) => (w in WORDS ? String(WORDS[w]) : w));

  const out: ParsedSale = { product: null, litres: null, amount: null, payment_method: null, customer_id: null, customer_name: null, vehicle_no: null, slip_no: null, candidates: [], heard, engine: "rules" };
  out.product = PRODUCT.find(([, re]) => re.test(t))?.[0] ?? null;

  // slip / parchi number first, so its digits are not read as litres or rupees
  const slip = t.match(/(?:slip|parchi|parchee|indent|پرچی|سلپ)\s*(?:no\.?|number|nambar|نمبر)?\s*[:#]?\s*([a-z0-9-]{2,12})/i);
  if (slip) { out.slip_no = slip[1].toUpperCase(); t = t.replace(slip[0], " "); }
  // vehicle number like "LEA-1234", "lhr 5678", "gari number abc 123"
  const veh = t.toUpperCase().match(/\b([A-Z]{2,4})[\s-]?(\d{2,4})\b/);
  if (veh && !/^(RS|LTR|LTRS|PMG|HSD|HOBC|SLIP|CARD|CASH|KA|KI|KE|LITR)$/.test(veh[1])) { out.vehicle_no = `${veh[1]}-${veh[2]}`; t = t.replace(new RegExp(`${veh[1]}[\\s-]?${veh[2]}`, "i"), " "); }

  const n = (s: string) => Number(s);
  const k = t.match(new RegExp(`(\\d+(?:\\.\\d+)?)\\s*${THOUSAND}`, "i"));
  const l = t.match(new RegExp(`(\\d+(?:\\.\\d+)?)\\s*${UNIT_L}`, "i"));
  const r = t.match(new RegExp(`(\\d+(?:\\.\\d+)?)\\s*${UNIT_RS}`, "i")) ?? t.match(new RegExp(`${UNIT_RS}\\s*(\\d+(?:\\.\\d+)?)`, "i"));
  if (l) out.litres = n(l[1]);
  else if (k) out.amount = n(k[1]) * 1000;
  else if (r) out.amount = n(r[1]);
  else {
    const bare = t.match(/(\d+(?:\.\d+)?)/);
    if (bare) { const v = n(bare[1]); if (v >= 200) out.amount = v; else out.litres = v; }
  }

  // khata account by the words of its name
  const words = new Set(t.split(/[^a-z0-9؀-ۿ]+/).filter(Boolean));
  const scored = accounts.map((a) => {
    const toks = a.name.toLowerCase().split(/[^a-z0-9؀-ۿ]+/).filter((w) => w.length >= 3 && !STOP.has(w));
    return { a, hit: toks.filter((w) => words.has(w)).length, of: toks.length };
  }).filter((x) => x.hit > 0).sort((x, y) => y.hit - x.hit || y.hit / y.of - x.hit / x.of);
  if (scored.length) {
    const best = scored.filter((x) => x.hit === scored[0].hit);
    if (best.length === 1) { out.customer_id = best[0].a.id; out.customer_name = best[0].a.name; }
    out.candidates = scored.slice(0, 5).map((x) => ({ id: x.a.id, name: x.a.name }));
  }

  // a named account (or several possible ones) means khata unless another payment was said
  out.payment_method = PAY.find(([, re]) => re.test(t))?.[0] ?? (out.candidates.length ? "khata" : "cash");
  return out;
}

/** "chai ka kharcha 300", "generator mein 2000 ka diesel dala kharcha" → an expense from the shift's cash. */
export function expenseFromText(raw: string, categories: string[]) {
  const t = ` ${raw.replace(/[۰-۹٠-٩]/g, (d) => DIGITS[d]).replace(/(\d),(\d)/g, "$1$2").toLowerCase()} `;
  if (!/(kharcha|kharch|kharche|expense|خرچہ|خرچ|خرچے)/.test(t)) return null;
  const k = t.match(new RegExp(`(\\d+(?:\\.\\d+)?)\\s*${THOUSAND}`, "i"));
  const n = t.match(/(\d+(?:\.\d+)?)/);
  const amount = k ? Number(k[1]) * 1000 : n ? Number(n[1]) : null;
  const pick = (re: RegExp, name: string) => (re.test(t) ? categories.find((c) => c.toLowerCase().startsWith(name.toLowerCase())) : undefined);
  const category = pick(/(chai|chaye|tea|khana|roti|food|چائے|کھانا|روٹی)/, "Tea")
    ?? pick(/(generator|genny|جنریٹر)/, "Generator") ?? pick(/(bijli|bill|electric|بجلی)/, "Electricity")
    ?? pick(/(repair|mistri|mechanic|theek|مرمت|مستری)/, "Maintenance") ?? pick(/(safai|cleaning|صفائی)/, "Other")
    ?? pick(/(stationery|register|pen|کاپی)/, "Office") ?? categories.find((c) => c === "Other") ?? categories[0];
  const note = raw.replace(/\d[\d,.]*/g, " ").replace(/(kharcha|kharch|kharche|expense|ka|ki|ke|rupay|rupees|rs|hazar|خرچہ|خرچ|کا|کی|کے|روپے|ہزار)/gi, " ").replace(/\s+/g, " ").trim();
  return { intent: "expense" as const, amount, category, note: note || null, heard: raw.trim() };
}
