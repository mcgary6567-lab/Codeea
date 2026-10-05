/**
 * Wholesale by voice / typing — Urdu, Roman Urdu or English. One sentence becomes one entry to confirm:
 *   "Shah Transport ko 5000 litre diesel bheja tanker 3412"          → supply
 *   "Malik Petroleum se 2 lakh bank transfer mila"                     → payment
 *   "Green Fields ka kal 8000 litre diesel ka order"                    → order
 *   "Shah Transport jumma ko 3 lakh dega"                               → promise
 *   "Malik ka cheque mila 3 lakh HBL number 1004 date 10 tareekh"       → cheque
 *   "Green Fields ne 300 litre diesel wapas kiya"                       → return
 *   "tanker 3412 Shah ko 5000 aur Malik ko 3000 litre diesel"           → trip (several drops)
 *   "Malik ka baqaya kitna hai" / "aaj kitni supply hui"                → answer
 * Rule-based so it works without an API key; Claude (when configured) reads freer speech.
 */
import { pkDate } from "../db.js";

export type Intent = "supply" | "payment" | "order" | "promise" | "cheque" | "return" | "trip" | "balance" | "today" | "unknown";
export type Named = { id: number; name: string; alt?: string | null };
export type ParsedWholesale = {
  intent: Intent; heard: string; engine: "rules" | "claude";
  client_id: number | null; client_name: string | null; candidates: Named[];
  product: string | null; litres: number | null; amount: number | null; rate: number | null;
  method: string | null; date: string | null; bank: string | null; cheque_no: string | null; ref: string | null;
  tanker_id: number | null; tanker: string | null; driver_id: number | null; driver: string | null; location: string | null;
  drops: { client_id: number; client_name: string; litres: number }[];
  missing: string[];
};

export const DIGITS: Record<string, string> = { "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4", "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9", "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9" };
export const WORDS: Record<string, number> = {
  ek: 1, aik: 1, "ایک": 1, do: 2, "دو": 2, teen: 3, "تین": 3, char: 4, chaar: 4, "چار": 4, panch: 5, paanch: 5, "پانچ": 5,
  chay: 6, che: 6, chhe: 6, "چھ": 6, saat: 7, "سات": 7, aath: 8, "آٹھ": 8, nau: 9, "نو": 9, das: 10, "دس": 10, bara: 12, "بارہ": 12,
  pandra: 15, "پندرہ": 15, bees: 20, "بیس": 20, pachees: 25, "پچیس": 25, tees: 30, "تیس": 30, chalees: 40, "چالیس": 40,
  pachas: 50, pachaas: 50, "پچاس": 50, sau: 100, "سو": 100,
  dedh: 1.5, derh: 1.5, "ڈیڑھ": 1.5, dhai: 2.5, dhaai: 2.5, "ڈھائی": 2.5,
};
const MULT: [RegExp, number][] = [
  [/^(crore|karor|کروڑ)$/, 10_000_000], [/^(lakh|lac|lacs|lakhs|laakh|لاکھ)$/, 100_000], [/^(hazar|hazaar|hajar|thousand|k|ہزار)$/, 1000], [/^(sau|سو|hundred)$/, 100],
];
export const PRODUCT: [string, RegExp][] = [
  ["HOBC", /(hi[\s-]?octane|high[\s-]?octane|octane|hobc|آکٹین|اوکٹین)/i],
  ["HSD", /(diesel|deezal|dizal|deezel|hsd|ڈیزل)/i],
  ["PMG", /(\bpetrol\b|\bpatrol\b|\bsuper\b|\bpmg\b|پیٹرول(?!یم)|پٹرول(?!یم)|سپر)/i],
];
export const METHOD: [string, RegExp][] = [
  ["Cheque", /(cheque|check|chek|چیک)/i],
  ["Raast", /(raast|راست)/i],
  ["JazzCash", /(jazz\s?cash|جاز\s?کیش)/i],
  ["Easypaisa", /(easy\s?paisa|ایزی\s?پیس)/i],
  ["Bank transfer", /(bank|transfer|ibft|online|aan\s?line|بینک|ٹرانسفر|آن\s?لائن)/i],
  ["Cash", /(cash|naqd|naqad|کیش|نقد)/i],
];
const BANKS: [string, RegExp][] = [
  ["Habib Bank (HBL)", /\b(hbl|habib bank)\b|حبیب|ایچ بی ایل/i], ["United Bank (UBL)", /\b(ubl|united bank)\b|یو بی ایل/i], ["MCB Bank", /\bmcb\b|ایم سی بی/i],
  ["Allied Bank (ABL)", /\b(abl|allied)\b|الائیڈ/i], ["National Bank of Pakistan (NBP)", /\b(nbp|national bank)\b|نیشنل بینک/i], ["Bank Alfalah", /alfalah|الفلاح/i],
  ["Meezan Bank", /meezan|میزان/i], ["Bank of Punjab (BOP)", /\b(bop|bank of punjab|punjab bank)\b|پنجاب بینک/i], ["Askari Bank", /askari|عسکری/i],
  ["Faysal Bank", /faysal|faisal|فیصل/i], ["Bank AL Habib", /al habib|الحبیب/i], ["Habib Metropolitan Bank", /metro/i], ["BankIslami", /bank ?islami|بینک اسلامی/i],
  ["JS Bank", /\bjs bank\b/i], ["Soneri Bank", /soneri|سونیری/i], ["Standard Chartered Pakistan", /standard chartered|\bscb\b/i], ["Dubai Islamic Bank Pakistan", /dubai islamic|\bdib\b/i],
];
const DAYS: [number, RegExp][] = [
  [1, /\b(monday|peer|pir)\b|پیر/i], [2, /\b(tuesday|mangal)\b|منگل/i], [3, /\b(wednesday|budh|budhwar)\b|بدھ/i], [4, /\b(thursday|jumeraat|jumerat|jumay?raat)\b|جمعرات/i],
  [5, /\b(friday|juma|jumma|jumah)\b|جمعہ|جمعے/i], [6, /\b(saturday|hafta|hafte)\b|ہفتہ|ہفتے/i], [0, /\b(sunday|itwar|itwaar)\b|اتوار/i],
];

/** Urdu script → rough Roman letters, so "شاہ ٹرانسپورٹ" can be matched to "Shah Transport". */
const UR: Record<string, string> = {
  "ا": "a", "آ": "a", "ب": "b", "پ": "p", "ت": "t", "ٹ": "t", "ث": "s", "ج": "j", "چ": "ch", "ح": "h", "خ": "kh", "د": "d", "ڈ": "d", "ذ": "z", "ر": "r", "ڑ": "r",
  "ز": "z", "ژ": "zh", "س": "s", "ش": "sh", "ص": "s", "ض": "z", "ط": "t", "ظ": "z", "ع": "a", "غ": "gh", "ف": "f", "ق": "q", "ک": "k", "گ": "g", "ل": "l", "م": "m",
  "ن": "n", "ں": "n", "و": "o", "ہ": "h", "ھ": "h", "ۂ": "h", "ء": "", "ی": "i", "ے": "e", "ئ": "i", "ؤ": "o", "ة": "h",
};
export const romanize = (s: string) => s.replace(/[؀-ۿ]/g, (c) => UR[c] ?? "");
/** Consonant skeleton: survives the vowel differences between spoken Urdu and English spelling. */
const skel = (w: string) => romanize(w.toLowerCase()).replace(/[^a-z0-9]/g, "")
  .replace(/tion/g, "shn").replace(/ce/g, "s").replace(/c/g, "k").replace(/q/g, "k").replace(/ph/g, "f")
  .replace(/^a(?=s[^aeiou])/, "") // Urdu writes "station" as اسٹیشن (a-station)
  .replace(/(.)h$/, "$1") // final ہ is a vowel ("kahna" / کاہنہ)
  .replace(/(.)\1+/g, "$1").replace(/(?!^)[aeiouyw]/g, "");
const STOP = new Set(["the", "and", "ltd", "pvt", "company", "co", "services", "service", "traders", "trading", "petroleum", "filling", "station", "point", "ka", "ki", "ke", "ko", "se", "ne"]);

const addDays = (d: string, n: number) => new Date(Date.parse(d + "T00:00:00Z") + n * 86_400_000).toISOString().slice(0, 10);

export function parseDate(t: string, past: boolean): string | null {
  const today = pkDate();
  if (/\b(parson|parsoon)\b|پرسوں/.test(t)) return addDays(today, past ? -2 : 2);
  if (/\b(kal|tomorrow|yesterday)\b|کل/.test(t)) return addDays(today, past || /yesterday/.test(t) ? -1 : 1);
  if (/\b(aaj|aj|today)\b|آج/.test(t)) return today;
  for (const [dow, re] of DAYS) if (re.test(t)) {
    const now = new Date(today + "T00:00:00Z").getUTCDay();
    const diff = past ? -((now - dow + 7) % 7 || 7) : ((dow - now + 7) % 7 || 7);
    return addDays(today, diff);
  }
  const iso = t.match(/\b(20\d\d)-(\d\d)-(\d\d)\b/);
  if (iso) return iso[0];
  const dm = t.match(/\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b/);
  if (dm) { const y = dm[3] ? (dm[3].length === 2 ? `20${dm[3]}` : dm[3]) : today.slice(0, 4); return `${y}-${dm[2].padStart(2, "0")}-${dm[1].padStart(2, "0")}`; }
  const tar = t.match(/\b(\d{1,2})\s*(?:tareekh|tarikh|tarik|date)\b|(\d{1,2})\s*تاریخ/);
  if (tar) {
    const n = Number(tar[1] ?? tar[2]);
    let d = `${today.slice(0, 8)}${String(n).padStart(2, "0")}`;
    if (!past && d < today) { const m = new Date(today + "T00:00:00Z"); m.setUTCMonth(m.getUTCMonth() + 1, n); d = m.toISOString().slice(0, 10); }
    return d;
  }
  return null;
}

/** Numbers in the sentence with their multiplier ("2 lakh", "dedh lakh", "5 hazar") and what follows (litres?). */
export function quantities(t: string) {
  const toks = t.split(/\s+/).filter(Boolean);
  const out: { value: number; litres: boolean; big: boolean; at: number }[] = [];
  for (let i = 0; i < toks.length; i++) {
    let v = /^\d+(\.\d+)?$/.test(toks[i]) ? Number(toks[i]) : WORDS[toks[i]];
    if (v == null) continue;
    let j = i + 1, big = false;
    while (j < toks.length) {
      const m = MULT.find(([re]) => re.test(toks[j]));
      if (!m) break;
      v *= m[1]; big ||= m[1] >= 100_000; j++;
    }
    const litres = j < toks.length && /^(litres?|liters?|ltrs?|l|لیٹر|لٹر)$/.test(toks[j]);
    out.push({ value: v, litres, big, at: i });
    i = j - 1;
  }
  return out;
}

export function matchNamed(list: Named[], t: string): { best: Named | null; candidates: Named[]; spans: string[] } {
  const words = t.split(/[^a-z0-9؀-ۿ]+/).filter((w) => w.length >= 3);
  const sk = new Map(words.map((w) => [skel(w), w]));
  const scored = list.map((n) => {
    const toks = `${n.name} ${n.alt ?? ""}`.toLowerCase().split(/[^a-z0-9؀-ۿ]+/).filter((w) => w.length >= 3 && !STOP.has(w));
    const hits = toks.filter((w) => sk.has(skel(w)) && skel(w).length >= 2);
    return { n, hit: new Set(hits.map(skel)).size, of: toks.length, spans: hits.map((h) => sk.get(skel(h))!) };
  }).filter((x) => x.hit > 0).sort((a, b) => b.hit - a.hit || b.hit / b.of - a.hit / a.of);
  if (!scored.length) return { best: null, candidates: [], spans: [] };
  const top = scored.filter((x) => x.hit === scored[0].hit && x.hit / x.of === scored[0].hit / scored[0].of);
  return { best: top.length === 1 ? top[0].n : null, candidates: scored.slice(0, 5).map((x) => x.n), spans: scored[0].spans };
}

export function parseWholesaleText(raw: string, ctx: { clients: Named[]; tankers: { id: number; number: string }[]; drivers: Named[]; clientId?: number | null }): ParsedWholesale {
  const heard = raw.trim();
  let t = ` ${heard.replace(/[۰-۹٠-٩]/g, (d) => DIGITS[d]).replace(/(\d),(\d)/g, "$1$2").toLowerCase().replace(/[،,؟?!]/g, " ")} `;
  t = t.replace(/(\d)(lakh|lac|hazar|k|l|ltr|litre)\b/g, "$1 $2");
  const out: ParsedWholesale = {
    intent: "unknown", heard, engine: "rules", client_id: null, client_name: null, candidates: [], product: null, litres: null, amount: null, rate: null,
    method: null, date: null, bank: null, cheque_no: null, ref: null, tanker_id: null, tanker: null, driver_id: null, driver: null, location: null, drops: [], missing: [],
  };
  out.product = PRODUCT.find(([, re]) => re.test(t))?.[0] ?? null;

  // tanker: "tanker 3412", "TLR-3412", "gari 3412"
  // a registered tanker's digits ("3412", "TLR-3412"), or "tanker 1234" for one not in the register
  for (const x of ctx.tankers) {
    const digits = x.number.replace(/\D/g, "");
    const re = new RegExp(`(?:\\b[a-z]{2,4}[\\s-]?)?\\b${digits}\\b`);
    if (digits.length >= 3 && re.test(t)) { out.tanker_id = x.id; out.tanker = x.number; t = t.replace(re, " "); break; }
  }
  const tk = !out.tanker && t.match(/(?:tanker|gari|gaari|truck|ٹینکر|گاڑی)\s*(?:number|no|نمبر)?\s*([a-z]{2,4}[\s-]?)?(\d{3,4})\b/);
  if (tk) { out.tanker = `${tk[1] ? tk[1].replace(/[\s-]/g, "").toUpperCase() + "-" : ""}${tk[2]}`; t = t.replace(tk[0], " "); }
  // cheque number before amounts so its digits are not read as money
  const cn = t.match(/(?:cheque|check|chek|چیک)\s*(?:number|no\.?|nambar|نمبر|#)\s*(\d{4,12})/) ?? t.match(/(?:number|no\.?|nambar|نمبر|#)\s*(\d{5,12})/);
  if (cn) { out.cheque_no = cn[1]; t = t.replace(cn[0], " cheque "); }
  const isPast = /(bheja|bheji|bhej diya|diya|di |dia|dala|daala|supply ki|supply hui|deliver|mila|mile|mili|aaya|aya|aayi|jama|kiya|kia|wapas|بھیج|دیا|دی |ڈالا|ملا|ملی|ملے|آیا|جمع|واپس|کیا)/.test(t);
  out.date = parseDate(t, isPast && !/(order|booking|book|wada|waada|promise|dega|dega|denge|dein ge|de ga|دے گا|دیں گے|وعدہ|آرڈر)/.test(t));
  out.bank = BANKS.find(([, re]) => re.test(t))?.[0] ?? null;
  out.method = METHOD.find(([, re]) => re.test(t))?.[0] ?? null;
  if (out.method === "Bank transfer" && out.bank && !/(transfer|ibft|online|آن لائن|ٹرانسفر)/.test(t) && /(cheque|chek|چیک)/.test(t)) out.method = "Cheque";
  const rate = t.match(/(?:rate|ریٹ)\s*(\d+(?:\.\d+)?)/) ?? t.match(/(\d+(?:\.\d+)?)\s*(?:ka|ke|per|fi)\s*(?:litre|liter|لیٹر)/);
  if (rate) { out.rate = Number(rate[1]); t = t.replace(rate[0], " "); }
  const loc = t.match(/(?:location|jagah|site|پر|par)\s+([a-z؀-ۿ]{3,20})\b/);
  if (loc && !/^(diesel|petrol)$/.test(loc[1])) out.location = loc[1][0].toUpperCase() + loc[1].slice(1);

  const q = quantities(t);
  const moneyWord = /(lakh|lac|rupe|rupay|rs\b|paise|payment|raqam|cash|naqd|transfer|cheque|chek|لاکھ|روپے|رقم|پیسے|نقد|چیک)/.test(t);
  // "Shah ko 5000 aur Malik ko 3000 litre diesel": with a fuel named and no money words, plain numbers are litres
  const litQ = q.filter((x) => x.litres || (out.product && !moneyWord && !x.big && x.value >= 50)), moneyQ = q.filter((x) => !x.litres && x.value >= 500);
  const kw = {
    balance: /(kitna|kitne|kitni|baqaya|baqi|balance|due|hisab|hisaab|کتنا|کتنے|کتنی|بقایا|باقی|حساب|بیلنس)/.test(t),
    today: /(aaj|today|آج)/.test(t),
    order: /(order|booking|book|chahiye|chaiye|mangwa|bhejna hai|bhejni hai|آرڈر|بکنگ|چاہیے|منگوا)/.test(t),
    promise: /(wada|waada|promise|dega|denge|dein ge|de ga|de dega|de den ge|dain gay|وعدہ|دے گا|دیں گے|دے دے گا)/.test(t),
    cheque: /(cheque|check|chek|چیک)/.test(t),
    ret: /(wapas|wapis|return|واپس)/.test(t),
    pay: /(payment|paise|paisay|paisa|raqam|mila|mile|mili|diye|jama|received|aaye|aaya|ادائیگی|رقم|پیسے|ملے|ملی|ملا|دیے|جمع|آئے)/.test(t),
    supply: /(supply|bheja|bheji|bhej|diya|dala|daala|deliver|ڈالا|بھیج|دیا|سپلائی)/.test(t),
  };

  // several clients each with litres → one tanker trip
  const names = ctx.clients.map((c) => ({ c, m: matchNamed([c], t) })).filter((x) => x.m.best);
  if (litQ.length >= 2 && names.length >= 2 && !kw.order) {
    const toks = t.split(/\s+/).filter(Boolean);
    const pos = (w: string) => toks.findIndex((x) => x === w);
    const placed = names.map((x) => ({ c: x.c, at: Math.min(...x.m.spans.map(pos).filter((i) => i >= 0)) })).sort((a, b) => a.at - b.at);
    for (const p of placed) {
      const after = litQ.find((l) => l.at > p.at && !out.drops.some((d) => d.litres === l.value && false)) ?? null;
      const next = placed.find((x) => x.at > p.at);
      const l = litQ.filter((x) => x.at > p.at && (!next || x.at < next.at))[0] ?? (after && (!next || after.at < next.at) ? after : null);
      if (l) out.drops.push({ client_id: p.c.id, client_name: p.c.name, litres: l.value });
    }
    if (out.drops.length >= 2) { out.intent = "trip"; out.litres = out.drops.reduce((a, d) => a + d.litres, 0); }
  }

  if (out.intent === "unknown") {
    const m = matchNamed(ctx.clients, t);
    if (m.best) { out.client_id = m.best.id; out.client_name = m.best.name; }
    out.candidates = m.candidates;
    if (!out.client_id && ctx.clientId) { const c = ctx.clients.find((x) => x.id === ctx.clientId); if (c) { out.client_id = c.id; out.client_name = c.name; } }
    out.litres = litQ[0]?.value ?? null;
    out.amount = moneyQ.find((x) => x.value !== out.litres)?.value ?? null;
    // a bare number with a fuel word and no money word is litres ("Shah ko 5000 diesel")
    if (!out.litres && out.product && !kw.pay && !kw.promise && !kw.cheque && q.length) { out.litres = q[0].value; out.amount = null; }

    if (kw.balance && !out.litres && !out.amount) out.intent = out.client_id || out.candidates.length ? "balance" : kw.today ? "today" : "balance";
    else if (kw.cheque && (out.cheque_no || out.bank) && out.amount && !kw.promise) out.intent = "cheque";
    else if (kw.promise && out.amount) out.intent = "promise";
    else if (kw.ret && out.litres) out.intent = "return";
    else if (kw.order && out.litres) out.intent = "order";
    else if (out.litres && (kw.supply || out.product)) out.intent = out.date && out.date > pkDate() ? "order" : "supply";
    else if (out.amount && (kw.pay || out.method)) out.intent = "payment";
    else if (out.amount) out.intent = "payment";
    else if (out.litres) out.intent = "supply";
  }
  if (out.intent === "trip" || out.intent === "supply" || out.intent === "return" || out.intent === "order") out.amount = null;
  if (["payment", "promise", "cheque", "balance", "today"].includes(out.intent)) { out.product = null; out.litres = null; }
  if (out.intent === "payment" && !out.method) out.method = "Cash";
  if (out.intent === "cheque") out.method = "Cheque";

  // driver by name
  const d = matchNamed(ctx.drivers, t);
  if (d.best && d.best.id !== out.client_id) { out.driver_id = d.best.id; out.driver = d.best.name; }

  if (out.intent === "cheque" && !out.date) out.date = pkDate();
  out.missing = missingFor(out);
  return out;
}

const NEED: Record<Intent, (keyof ParsedWholesale)[]> = {
  supply: ["client_id", "product", "litres"], return: ["client_id", "product", "litres"], order: ["client_id", "product", "litres", "date"],
  payment: ["client_id", "amount"], promise: ["client_id", "amount", "date"], cheque: ["client_id", "amount", "bank", "cheque_no", "date"],
  trip: ["product"], balance: ["client_id"], today: [], unknown: [],
};
/** What still has to be filled in before this entry can be saved. */
export const missingFor = (p: ParsedWholesale) => NEED[p.intent].filter((k) => p[k] == null) as string[];
