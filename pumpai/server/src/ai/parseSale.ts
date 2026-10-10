/**
 * Turn a spoken / typed sentence into a POS sale, e.g.
 *   "police station kahna 20 litre diesel slip 7781"  →  HSD 20 L, khata, Police Station Kahna, slip 7781
 *   "do hazar ka petrol easypaisa"                     →  PMG Rs 2000, easypaisa
 *   "پیٹرول 500 روپے نقد"                                →  PMG Rs 500, cash
 * Rule-based so it works without an API key; Claude (when configured) handles freer speech.
 * Numbers and names are read the same way as wholesale and khata (shared helpers in parseWholesale).
 */
import { DIGITS, MAX_AMOUNT, MAX_LITRES, matchNamed, quantities, splitUnits, stripNameDigits } from "./parseWholesale.js";

export type ParsedSale = {
  product: string | null; litres: number | null; amount: number | null; payment_method: string | null;
  customer_id: number | null; customer_name: string | null; vehicle_no: string | null; slip_no: string | null;
  candidates: { id: number; name: string }[]; heard: string; engine: "rules" | "claude" | "gemini";
};

const PRODUCT: [string, RegExp][] = [
  ["HOBC", /(hi[\s-]?octane|high[\s-]?octane|octane|hobc|آکٹین|اوکٹین|ہائی آکٹین)/i],
  ["HSD", /(diesel|deezal|dizal|deezel|hsd|ڈیزل)/i],
  ["PMG", /(petrol|patrol|super|pmg|پیٹرول|پٹرول|سپر)/i],
];
const PAY: [string, RegExp][] = [
  ["khata", /(khata|khaata|udhaar|udhar|credit|کھاتہ|کھاتے|کھاتا|ادھار)/i],
  ["easypaisa", /(easy\s?paisa|ایزی\s?پیسہ|ایزی\s?پیسا|ایزی پیسے)/i],
  ["jazzcash", /(jazz\s?cash|جاز\s?کیش)/i],
  ["card", /(\bcard\b|\batm\b|کارڈ)/i],
  ["raast", /(raast|راست)/i],
  ["cash", /(\bcash\b|naqd|naqad|کیش|نقد)/i],
];
const LITRE = /(?:^|\s)\d+(?:\.\d+)?\s*(?:litres?|liters?|ltrs?|ltr|l|لیٹر|لٹر)(?=\s|$)|(?:litres?|liters?|لیٹر)\s+\d/i;
/** Words that make a number rupees: "100 ka petrol", "500 rupay", "Rs 750". */
const RUPEE_AFTER = /^(ka|ke|ki|ky|kaa|ka,|rupees?|rupay|rupaye|rupe|rupay,|rs\.?|pkr|روپے|روپیہ|روپئے|کا|کی|کے)$/;
const RUPEE_BEFORE = /^(rs\.?|pkr|rupees?|rupay|روپے)$/;
/** Never the letters of a number plate: Urdu particles and fuel / unit words. */
const NOT_PLATE = /^(ko|se|ka|ke|ki|ne|mein|main|me|aur|or|ya|rs|ltr|ltrs|litr|pmg|hsd|hobc|slip|card|cash|atm|pkr|no|liye|wala|wale)$/i;
const normalize = (raw: string) => ` ${raw.replace(/[۰-۹٠-٩]/g, (d) => DIGITS[d]).replace(/(\d),(\d)/g, "$1$2").toLowerCase().replace(/[،؟?!]/g, " ")} `;

/** "LEA-1234", "LEA1234", "LHR 5678" as typed in capitals, or after "gaari / car / number": letters + 3-4 digits. */
function findPlate(rawDigits: string, nameWords: Set<string>): { no: string; letters: string; digits: string } | null {
  const ok = (l: string) => !NOT_PLATE.test(l) && !nameWords.has(l.toLowerCase());
  const cue = rawDigits.match(/(?:gaari|gari|gadi|car|vehicle|number|nambar|گاڑی|نمبر)\s*(?:number|no\.?|nambar|نمبر)?\s*[:#]?\s*([a-z]{2,4})[\s-]?(\d{3,4})\b/i);
  if (cue && ok(cue[1])) return { no: `${cue[1].toUpperCase()}-${cue[2]}`, letters: cue[1], digits: cue[2] };
  for (const m of rawDigits.matchAll(/\b([A-Za-z]{2,4})(-|\s?)(\d{3,4})\b/g)) {
    const [, l, sep, d] = m;
    if ((l === l.toUpperCase() || sep === "-") && ok(l)) return { no: `${l.toUpperCase()}-${d}`, letters: l, digits: d };
  }
  return null;
}

export function parseSaleText(raw: string, accounts: { id: number; name: string }[]): ParsedSale {
  const heard = raw.trim();
  let t = splitUnits(normalize(heard));
  const out: ParsedSale = { product: null, litres: null, amount: null, payment_method: null, customer_id: null, customer_name: null, vehicle_no: null, slip_no: null, candidates: [], heard, engine: "rules" };
  out.product = PRODUCT.find(([, re]) => re.test(t))?.[0] ?? null;

  // slip / parchi number first, so its digits are not read as litres or rupees
  const slip = t.match(/(?:slip|parchi|parchee|indent|پرچی|سلپ)\s*(?:no\.?|number|nambar|نمبر)?\s*[:#]?\s*([a-z0-9-]{2,12})/i);
  if (slip) { out.slip_no = slip[1].toUpperCase(); t = t.replace(slip[0], " "); }

  // khata account by its name (spelling / Urdu script tolerant); fuel words are not names
  let names = t;
  for (const [, re] of PRODUCT) names = names.replace(new RegExp(re.source, "gi"), " ");
  const m = matchNamed(accounts, names);
  if (m.best) { out.customer_id = m.best.id; out.customer_name = m.best.name; }
  out.candidates = m.candidates.map((x) => ({ id: x.id, name: x.name }));
  t = stripNameDigits(t, out.customer_name, m.best ? m.spans : []);

  // vehicle number — never letters of the customer's name or an Urdu word ("KHAN-2000", "KO-3000")
  const nameWords = new Set((out.customer_name ?? "").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean));
  const rawDigits = heard.replace(/[۰-۹٠-٩]/g, (d) => DIGITS[d]);
  const plate = findPlate(rawDigits, nameWords);
  if (plate) { out.vehicle_no = plate.no; t = t.replace(new RegExp(`\\b${plate.letters}[\\s-]?${plate.digits}\\b`, "i"), " "); }

  const toks = t.split(/\s+/).filter(Boolean);
  const q = quantities(t).filter((x) => x.value > 0);
  const lit = q.find((x) => x.litres);
  if (lit) out.litres = lit.value;
  else {
    const rupee = (x: (typeof q)[number]) => x.mult >= 1000 || RUPEE_AFTER.test(toks[x.end] ?? "") || RUPEE_BEFORE.test(toks[x.at - 1] ?? "");
    const said = q.find(rupee) ?? q[0];
    // a bare small number is litres ("petrol 10"), a bigger one rupees ("diesel 1500")
    if (said) { if (rupee(said) || said.value >= 200) out.amount = said.value; else out.litres = said.value; }
  }
  if (out.litres != null && out.litres > MAX_LITRES) out.litres = null;
  if (out.amount != null && out.amount > MAX_AMOUNT) out.amount = null;

  // a named account (or several possible ones) means khata unless another payment was said
  out.payment_method = PAY.find(([, re]) => re.test(t))?.[0] ?? (out.candidates.length ? "khata" : "cash");
  return out;
}

/** Expense categories by what was said, matched to the tenant's own category names (by their start). */
const CATEGORY: [RegExp, string][] = [
  [/(bank (?:charges?|fee|fees|katoti)|bank ki katoti|بینک چارج)/, "Bank charges"],
  [/(\bchai\b|chaye|chae|\btea\b|khana|khaana|\broti\b|\bfood\b|lunch|dinner|nashta|چائے|کھانا|روٹی|ناشتہ)/, "Tea"],
  [/(generator|genny|جنریٹر)/, "Generator"],
  [/(bijli|electric|wapda|lesco|بجلی|واپڈا)/, "Electricity"],
  [/(repair|mistri|mechanic|theek|marammat|مرمت|مستری)/, "Maintenance"],
  [/(\brent\b|kiraya|kiraaya|کرایہ)/, "Rent"],
  [/(salary|salaries|tankhwah|tankhah|tankha|wages|chowkidar|chaukidar|تنخواہ|چوکیدار)/, "Salaries"],
  [/(security|\bguard\b|گارڈ|سیکیورٹی)/, "Security"],
  [/(\btax\b|taxes|\bfee\b|\bfees\b|challan|ٹیکس|فیس|چالان)/, "Taxes"],
  [/(bhara|bhaara|freight|بھاڑا)/, "Tanker freight"],
  [/(stationery|register|\bpen\b|\bcopy\b|photocopy|printer|کاپی|رجسٹر)/, "Office"],
  [/(safai|cleaning|صفائی)/, "Other"],
  [/\bbill\b|بل/, "Electricity"],
];
/** Said without "kharcha", these words alone make it an expense (when nothing in it looks like a sale). */
const STRONG = /(\bchai\b|chaye|chae|\btea\b|khana|khaana|\broti\b|nashta|چائے|کھانا|روٹی|bijli|wapda|lesco|بجلی|generator|genny|جنریٹر|mistri|marammat|مستری|مرمت|safai|صفائی|\brent\b|kiraya|kiraaya|کرایہ|salary|tankhwah|tankhah|tankha|تنخواہ|chowkidar|chaukidar|چوکیدار|bhara|bhaara|freight)/;

/** "chai ka kharcha 300", "generator mein 2000 ka diesel dala kharcha", "bijli ka bill 18000" → an expense from the shift's cash. */
export function expenseFromText(raw: string, categories: string[], accounts: { id: number; name: string }[] = []) {
  const t = splitUnits(normalize(raw));
  const said = /(kharcha|kharch|kharche|expense|خرچہ|خرچ|خرچے)/.test(t);
  if (!said) {
    if (!STRONG.test(t)) return null;
    // "2000 ka diesel jazzcash", "City Bakers generator diesel 2500", "generator ke liye 20 litre diesel" stay sales
    if (LITRE.test(t) || PAY.some(([, re]) => re.test(t))) return null;
    // ("khana" sounds like "Khan" / "Kahna": the category word itself is not a name)
    if (matchNamed(accounts, t.replace(new RegExp(STRONG.source, "g"), " ")).candidates.length) return null;
    if (findPlate(raw.replace(/[۰-۹٠-٩]/g, (d) => DIGITS[d]), new Set())) return null;
  }
  const q = quantities(t).filter((x) => !x.litres && x.value > 0 && x.value <= MAX_AMOUNT);
  const amount = (q.find((x) => x.value >= 10) ?? q[0])?.value ?? null;
  const has = (name: string) => categories.find((c) => c.toLowerCase().startsWith(name.toLowerCase()));
  const category = CATEGORY.filter(([re]) => re.test(t)).map(([, name]) => has(name)).find(Boolean)
    ?? categories.find((c) => c === "Other") ?? categories[0];
  const note = raw.replace(/\d[\d,.]*/g, " ").replace(/(kharcha|kharch|kharche|expense|\bka\b|\bki\b|\bke\b|rupay|rupees|\brs\b|hazar|خرچہ|خرچ|کا|کی|کے|روپے|ہزار)/gi, " ").replace(/\s+/g, " ").trim();
  return { intent: "expense" as const, amount, category, note: note || null, heard: raw.trim() };
}
