/**
 * Offline (no AI key) voice / typed command parsers: POS sale + expense, khata, wholesale.
 * Calls the rule parsers directly with fixtures that mirror the demo seed (same ids and names),
 * so every case from the voice audit is a regression guard.
 */
import { test, before } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";

// parseWholesale imports the db module (for today's date), which opens a database: keep it in a temp dir
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-voice-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";

let S: typeof import("../src/ai/parseSale.js");
let K: typeof import("../src/ai/parseKhata.js");
let W: typeof import("../src/ai/parseWholesale.js");
before(async () => {
  S = await import("../src/ai/parseSale.js");
  K = await import("../src/ai/parseKhata.js");
  W = await import("../src/ai/parseWholesale.js");
});

const CUSTOMERS = [
  [1, "Muhammad Asif"], [6, "Ayesha Khan"], [11, "Imtiaz Ali"], [16, "Faisal Chaudhry"], [21, "Rana Arshad (Tube-well)"], [22, "Chaudhry Nazir Farms"],
  [23, "Malik Sarwar Agri"], [24, "Haji Bashir Zamindar"], [25, "Daewoo Cargo Lahore"], [26, "Bismillah Goods Transport"], [27, "Al-Karam Rent a Car"],
  [28, "Shaheen Rickshaw Union"], [29, "Lahore School Vans"], [30, "Punjab Builders (Pvt) Ltd"], [31, "City Bakers Generators"], [32, "Green Valley Hospital"],
  [33, "Police Station Kahna"], [34, "Police Station Model Town"], [35, "Rescue 1122 Lahore"], [36, "Govt. High School No. 1 Kahna"], [37, "District Health Office Lahore"],
].map(([id, name]) => ({ id: id as number, name: name as string }));
const CATEGORIES = ["Bank charges", "Electricity (bijli)", "Generator fuel", "Maintenance & repairs", "Office & stationery", "Other", "Rent", "Salaries & wages",
  "Security", "Tanker freight & transport", "Taxes & fees", "Tea & food"];
const WCTX = {
  clients: [
    { id: 1, name: "Malik Petroleum Services", alt: "Sub-dealer, Pattoki" }, { id: 2, name: "Shah Transport Company", alt: "Goods transport fleet" },
    { id: 3, name: "Green Fields Agri Farms", alt: "Tube-wells & tractors" },
  ],
  tankers: [{ id: 3, number: "LES-0981" }, { id: 1, number: "TLR-3412" }, { id: 2, number: "TLR-5520" }],
  drivers: [{ id: 1, name: "Ghulam Rasool" }, { id: 2, name: "Muhammad Akram" }, { id: 3, name: "Zafar Iqbal" }],
};
const PRICES = { PMG: 265, HSD: 275, HOBC: 300 };

// dates as the parsers see them (Pakistan time)
const pk = (d: number) => new Date(Date.now() + 5 * 3600e3 + d * 86400e3).toISOString().slice(0, 10);
const T = pk(0), Y = pk(-1), TM = pk(1), F2 = pk(2);
const dow = new Date(T + "T00:00:00Z").getUTCDay();
const next = (d: number) => pk(((d - dow + 7) % 7) || 7);
const ym = T.slice(0, 8);
const nextMonth = (() => { const d = new Date(T + "T00:00:00Z"); d.setUTCMonth(d.getUTCMonth() + 1); return d.toISOString().slice(0, 10); })();

/** What the /ai/parse-sale route does: an expense when the sentence is one, otherwise a sale. */
const pos = (text: string) => {
  const e = S.expenseFromText(text, CATEGORIES, CUSTOMERS);
  return (e?.amount ? e : S.parseSaleText(text, CUSTOMERS)) as Record<string, unknown>;
};
const khata = (text: string) => K.parseKhataText(text, { customers: CUSTOMERS, prices: PRICES }) as unknown as Record<string, unknown>;
const ws = (text: string) => W.parseWholesaleText(text, WCTX) as unknown as Record<string, unknown>;

const eq = (a: unknown, b: unknown) => (typeof b === "number" ? typeof a === "number" && Math.abs(a - b) < 0.01 : a === b);
function check(got: Record<string, unknown>, exp: Record<string, unknown>) {
  const bad: string[] = [];
  for (const [k, v] of Object.entries(exp)) {
    if (k === "drops") {
      const g = ((got.drops ?? []) as { client_id: number; litres: number }[]).map((d) => [d.client_id, d.litres]);
      if (JSON.stringify(g) !== JSON.stringify(v)) bad.push(`drops=${JSON.stringify(g)} want ${JSON.stringify(v)}`);
    } else if (k === "missing_has") {
      if (!((got.missing ?? []) as string[]).includes(v as string)) bad.push(`missing=${JSON.stringify(got.missing)} want it to include ${v}`);
    } else if (!eq(got[k], v)) bad.push(`${k}=${JSON.stringify(got[k])} want ${JSON.stringify(v)}`);
  }
  assert.ok(!bad.length, `${bad.join("; ")}\n  got ${JSON.stringify(got)}`);
}

// ---------------- POS sale / expense (the voice audit battery) ----------------
const POS: [string, Record<string, unknown>][] = [
  ["50 litre petrol cash", { product: "PMG", litres: 50, payment_method: "cash" }],
  ["2000 ka diesel jazzcash", { product: "HSD", amount: 2000, payment_method: "jazzcash" }],
  ["gaari LEA-1234 khata City Bakers 40 litre diesel", { product: "HSD", litres: 40, payment_method: "khata", customer_id: 31, vehicle_no: "LEA-1234" }],
  ["gaari LEA-1234 khata City Bakers", { payment_method: "khata", customer_id: 31, vehicle_no: "LEA-1234" }],
  ["do hazar ka petrol easypaisa", { product: "PMG", amount: 2000, payment_method: "easypaisa" }],
  ["dhai hazar ka petrol", { product: "PMG", amount: 2500, payment_method: "cash" }],
  ["sawa hazar ka diesel", { product: "HSD", amount: 1250 }],
  ["5k ka petrol", { product: "PMG", amount: 5000 }],
  ["1.5 hazar petrol", { product: "PMG", amount: 1500 }],
  ["۵۰۰۰ روپے کا ڈیزل", { product: "HSD", amount: 5000, payment_method: "cash" }],
  ["پیٹرول 500 روپے نقد", { product: "PMG", amount: 500, payment_method: "cash" }],
  ["بیس لیٹر ڈیزل", { product: "HSD", litres: 20 }],
  ["پانچ ہزار کا پیٹرول جاز کیش", { product: "PMG", amount: 5000, payment_method: "jazzcash" }],
  ["20 litre HOBC card", { product: "HOBC", litres: 20, payment_method: "card" }],
  ["high octane 10 litre", { product: "HOBC", litres: 10 }],
  ["super 3000 raast", { product: "PMG", amount: 3000, payment_method: "raast" }],
  ["HSD 100 litre Rescue 1122", { product: "HSD", litres: 100, customer_id: 35, payment_method: "khata" }],
  ["Rescue 1122 ko 3000 ka diesel", { product: "HSD", amount: 3000, customer_id: 35, payment_method: "khata" }],
  ["police station kahna 20 litre diesel slip 7781", { product: "HSD", litres: 20, customer_id: 33, payment_method: "khata", slip_no: "7781" }],
  ["Police Station Model Town 30 litre diesel", { product: "HSD", litres: 30, customer_id: 34 }],
  ["Daewoo cargo 200 litre diesel udhaar", { product: "HSD", litres: 200, customer_id: 25, payment_method: "khata" }],
  ["Deewoo 200 litre diesel", { product: "HSD", litres: 200, customer_id: 25 }],
  ["City Bakers 5000 ka diesel", { product: "HSD", amount: 5000, customer_id: 31, payment_method: "khata" }],
  ["petrol 150 rupay", { product: "PMG", amount: 150 }],
  ["petrol 10", { product: "PMG", litres: 10 }],
  ["diesel 1500", { product: "HSD", amount: 1500 }],
  ["300 ka petrol", { product: "PMG", amount: 300 }],
  ["100 ka petrol", { product: "PMG", amount: 100, litres: null }],
  ["1,500 ka petrol cash", { product: "PMG", amount: 1500, payment_method: "cash" }],
  ["LHR 5678 petrol 2000 cash", { vehicle_no: "LHR-5678", product: "PMG", amount: 2000, payment_method: "cash" }],
  ["Rs 750 petrol", { product: "PMG", amount: 750 }],
  ["teen hazar paanch sau ka diesel", { product: "HSD", amount: 3500 }],
  ["das litre petrol", { product: "PMG", litres: 10 }],
  ["pachees litre diesel", { product: "HSD", litres: 25 }],
  ["Ayesha Khan 2000 petrol", { product: "PMG", amount: 2000, customer_id: 6, payment_method: "khata" }],
  ["Imtiaz 1000 ka petrol", { product: "PMG", amount: 1000, customer_id: 11 }],
  ["Faisal 5000 diesel", { product: "HSD", amount: 5000, customer_id: 16 }],
  ["LEA 1234 40 litre petrol", { vehicle_no: "LEA-1234", product: "PMG", litres: 40 }],
  ["ڈیزل 40 لیٹر سٹی بیکرز کھاتہ", { product: "HSD", litres: 40, payment_method: "khata", customer_id: 31 }],
  ["12 litre petrol jazz cash", { product: "PMG", litres: 12, payment_method: "jazzcash" }],
  ["easypaisa se 1000 ka super", { product: "PMG", amount: 1000, payment_method: "easypaisa" }],
  ["1500 ka patrol", { product: "PMG", amount: 1500 }],
  ["diesel 3 hazar 500", { product: "HSD", amount: 3500 }],
  ["Rana Arshad tubewell 60 litre diesel", { product: "HSD", litres: 60, customer_id: 21, payment_method: "khata" }],
  ["Govt High School Kahna 25 litre diesel", { product: "HSD", litres: 25, customer_id: 36 }],
  ["petrol 2.5 litre", { product: "PMG", litres: 2.5 }],
  // expenses
  ["chai ka kharcha 300", { intent: "expense", amount: 300, category: "Tea & food" }],
  ["chai 300", { intent: "expense", amount: 300, category: "Tea & food" }],
  ["generator diesel 2500", { intent: "expense", amount: 2500, category: "Generator fuel" }],
  ["bijli ka bill 18000", { intent: "expense", amount: 18000, category: "Electricity (bijli)" }],
  ["bijli ka bill 18000 kharcha", { intent: "expense", amount: 18000, category: "Electricity (bijli)" }],
  ["generator mein 2000 ka diesel dala kharcha", { intent: "expense", amount: 2000, category: "Generator fuel" }],
  ["mistri ko 1500 diye kharcha repair", { intent: "expense", amount: 1500, category: "Maintenance & repairs" }],
  ["kharcha do hazar khana", { intent: "expense", amount: 2000, category: "Tea & food" }],
  ["چائے کا خرچہ 300", { intent: "expense", amount: 300, category: "Tea & food" }],
  ["خرچہ ۵۰۰ روٹی", { intent: "expense", amount: 500, category: "Tea & food" }],
  ["register pen kharcha 250", { intent: "expense", amount: 250, category: "Office & stationery" }],
  ["safai wale ko 800 kharcha", { intent: "expense", amount: 800, category: "Other" }],
  ["kharcha 1.5 hazar generator", { intent: "expense", amount: 1500, category: "Generator fuel" }],
  ["rent kharcha 50000", { intent: "expense", amount: 50000, category: "Rent" }],
  ["courier expense 500", { intent: "expense", amount: 500, category: "Other" }],
  ["chowkidar salary kharcha 25000", { intent: "expense", amount: 25000, category: "Salaries & wages" }],
  ["generator ka kharcha dhai hazar", { intent: "expense", amount: 2500, category: "Generator fuel" }],
  ["chai pani 2 hazar 500 kharcha", { intent: "expense", amount: 2500, category: "Tea & food" }],
  ["bijli ka bill 18,500 kharcha", { intent: "expense", amount: 18500, category: "Electricity (bijli)" }],
];
// more POS guards: plates, cues, compounds, expenses vs real sales
POS.push(
  ["LEA1234 petrol 2000", { vehicle_no: "LEA-1234", product: "PMG", amount: 2000 }],
  ["gari number abc 123 petrol 1000", { vehicle_no: "ABC-123", product: "PMG", amount: 1000 }],
  ["car lhr 5678 20 litre diesel", { vehicle_no: "LHR-5678", product: "HSD", litres: 20 }],
  ["Rescue 1122 ko 3000 ka diesel", { vehicle_no: null }],
  ["Police Station Model Town 30 litre diesel", { vehicle_no: null }],
  ["Ayesha Khan 2000 petrol", { vehicle_no: null }],
  ["easypaisa se 1000 ka super", { vehicle_no: null }],
  ["200 ka petrol", { product: "PMG", amount: 200, litres: null }],
  ["50 ka petrol", { product: "PMG", amount: 50, litres: null }],
  ["dedh hazar ka diesel", { product: "HSD", amount: 1500 }],
  ["2k ka petrol cash", { product: "PMG", amount: 2000, payment_method: "cash" }],
  ["generator diesel 2500 kharcha", { intent: "expense", amount: 2500, category: "Generator fuel" }],
  ["generator ke liye 20 litre diesel", { product: "HSD", litres: 20 }],
  ["City Bakers generator diesel 2500", { customer_id: 31, product: "HSD", amount: 2500, payment_method: "khata" }],
  ["generator 2000 ka diesel jazzcash", { product: "HSD", amount: 2000, payment_method: "jazzcash" }],
  ["kiraya 40000", { intent: "expense", amount: 40000, category: "Rent" }],
  ["chowkidar ki tankhwah 20 hazar", { intent: "expense", amount: 20000, category: "Salaries & wages" }],
  ["tanker ka bhara 15000 kharcha", { intent: "expense", amount: 15000, category: "Tanker freight & transport" }],
  ["bank charges 300 kharcha", { intent: "expense", amount: 300, category: "Bank charges" }],
  ["safai 500", { intent: "expense", amount: 500, category: "Other" }],
  ["mistri 1500", { intent: "expense", amount: 1500, category: "Maintenance & repairs" }],
  ["khana 1200", { intent: "expense", amount: 1200, category: "Tea & food" }],
  ["high octane 10 litre", { product: "HOBC", litres: 10, payment_method: "cash", customer_id: null }],
);
for (const [text, exp] of POS) test(`pos: ${text}`, () => check(pos(text), exp));

// ---------------- Khata ----------------
const KH: [string, Record<string, unknown>][] = [
  ["Police Station Kahna se 20 hazar naqd mile", { intent: "payment", customer_id: 33, amount: 20000, method: "Cash" }],
  ["City Bakers se 20 hazar mile", { intent: "payment", customer_id: 31, amount: 20000, method: "Cash" }],
  ["Rescue 1122 ke khate mein 5000 likh do tyre repair", { intent: "charge", customer_id: 35, amount: 5000 }],
  ["City Bakers ka khata kitna hai", { intent: "balance", customer_id: 31 }],
  ["City Bakers ko reminder bhejo", { intent: "reminder", customer_id: 31 }],
  ["City Bakers ko bill bhejo", { intent: "bill", customer_id: 31 }],
  ["sab se zyada udhaar kis ka hai", { intent: "top" }],
  ["پولیس اسٹیشن کاہنہ سے بیس ہزار ملے", { intent: "payment", customer_id: 33, amount: 20000 }],
  ["Faisal Chaudhry ne 2 lakh bank se bheje", { intent: "payment", customer_id: 16, amount: 200000, method: "Bank transfer" }],
  ["Faisal se do lakh pachas hazar mile", { intent: "payment", customer_id: 16, amount: 250000 }],
  ["Haji Bashir se sawa lakh mile", { intent: "payment", customer_id: 24, amount: 125000 }],
  ["Haji Bashir se dhai lakh jazzcash pe aaye", { intent: "payment", customer_id: 24, amount: 250000, method: "JazzCash" }],
  ["Malik Sarwar se 1.5 lakh cheque mila", { intent: "payment", customer_id: 23, amount: 150000, method: "Cheque" }],
  ["Malik Sarwar se 1.5lakh mila", { intent: "payment", customer_id: 23, amount: 150000 }],
  ["Ayesha Khan se 5k easypaisa", { intent: "payment", customer_id: 6, amount: 5000, method: "Easypaisa" }],
  ["Imtiaz Ali ne ۵۰۰۰ raast kiye", { intent: "payment", customer_id: 11, amount: 5000, method: "Raast" }],
  ["Rescue 1122 ka baqaya kitna hai?", { intent: "balance", customer_id: 35 }],
  ["rescue walon ka hisaab batao", { intent: "balance", customer_id: 35 }],
  ["Daewoo Cargo ke khate mein 40 litre diesel likh do", { intent: "charge", customer_id: 25, litres: 40, product: "HSD" }],
  ["Lahore School Vans ko 3000 udhaar", { intent: "charge", customer_id: 29, amount: 3000 }],
  ["Green Valley Hospital ka bill bhej do", { intent: "bill", customer_id: 32 }],
  ["Green Valley Hospital ko yaad dilao", { intent: "reminder", customer_id: 32 }],
  ["سٹی بیکرز کا کھاتہ کتنا ہے", { intent: "balance", customer_id: 31 }],
  ["رانا ارشد سے پچاس ہزار ملے", { intent: "payment", customer_id: 21, amount: 50000 }],
  ["Rana Arshad tubewell wale se 50 hazar cash", { intent: "payment", customer_id: 21, amount: 50000, method: "Cash" }],
  ["Shaheen rickshaw union ko 2000 likh do petrol", { intent: "charge", customer_id: 28, amount: 2000 }],
  ["Bismillah goods se kal 30 hazar mile the", { intent: "payment", customer_id: 26, amount: 30000 }],
  ["Al Karam rent a car ka balance", { intent: "balance", customer_id: 27 }],
  ["Alkaram se 10000 mile", { intent: "payment", customer_id: 27, amount: 10000 }],
  ["District Health Office se 46741 cheque se aaye", { intent: "payment", customer_id: 37, amount: 46741, method: "Cheque" }],
  ["Muhammad Asif ka kitna baqaya hai", { intent: "balance", customer_id: 1 }],
  ["Asif 2000 jama karwa gaya", { intent: "payment", customer_id: 1, amount: 2000 }],
  ["Model Town thane wale se 15000 mile online", { intent: "payment", customer_id: 34, amount: 15000, method: "Bank transfer" }],
  ["Govt High School No 1 Kahna se 10 hazar mile", { intent: "payment", customer_id: 36, amount: 10000 }],
  ["Govt school ka khata kitna hai", { intent: "balance", customer_id: 36 }],
  ["Chaudhry Nazir se 3 lakh 20 hazar mile", { intent: "payment", customer_id: 22, amount: 320000 }],
  ["who owes the most", { intent: "top" }],
  ["top 5 udhaar wale", { intent: "top" }],
  ["City Bakers ka statement bhejo", { intent: "bill", customer_id: 31 }],
  ["City Bakers se 20000 ka cheque mila meezan bank", { intent: "payment", customer_id: 31, amount: 20000, method: "Cheque" }],
  ["Punjab Builders ne 2 crore diye", { intent: "payment", customer_id: 30, amount: 20000000 }],
  ["punjab builders ke naam 25000 likho cement truck", { intent: "charge", customer_id: 30, amount: 25000 }],
  ["Ayesha Khan ka hisaab", { intent: "balance", customer_id: 6 }],
  ["Imtiaz ko 500 ka petrol udhaar", { intent: "charge", customer_id: 11, amount: 500, product: "PMG" }],
  ["Rana Arshad 20 litre diesel udhaar", { intent: "charge", customer_id: 21, litres: 20, product: "HSD" }],
  ["Haji Bashir se ڈیڑھ لاکھ ملے", { intent: "payment", customer_id: 24, amount: 150000 }],
  ["رسکیو 1122 سے پانچ ہزار ملے", { intent: "payment", customer_id: 35, amount: 5000 }],
  ["Faisal Chaudhry ko 10 hazar ka diesel diya udhaar", { intent: "charge", customer_id: 16, amount: 10000, product: "HSD" }],
  ["Daewoo walay 5 hazaar de gaye", { intent: "payment", customer_id: 25, amount: 5000 }],
  ["Rescue 1122 se 1122 rupay mile", { intent: "payment", customer_id: 35, amount: 1122 }],
  ["Green Valley Hospital ne 2.5 lakh easy paisa kiye", { intent: "payment", customer_id: 32, amount: 250000, method: "Easypaisa" }],
  ["Shaheen Rickshaw se 12000 udhaar wapas mile", { intent: "payment", customer_id: 28, amount: 12000 }],
  ["City Bakers ke khate mein 18000 daal do generator diesel", { intent: "charge", customer_id: 31, amount: 18000 }],
  ["سٹی بیکرز جنریٹرز سے دس ہزار ملے", { intent: "payment", customer_id: 31, amount: 10000 }],
  ["کس کا ادھار سب سے زیادہ ہے", { intent: "top" }],
];
KH.push(
  ["Rescue 1122 ka khata kitna hai", { intent: "balance", customer_id: 35 }],
  ["Imtiaz se udhaar wapas mila 3000", { intent: "payment", customer_id: 11, amount: 3000 }],
  ["Asif ka udhaar chuka diya 2000", { intent: "payment", customer_id: 1, amount: 2000 }],
  ["top up City Bakers ke khate mein 5000 likh do", { intent: "charge", customer_id: 31, amount: 5000 }],
  ["highest udhaar kis ka hai", { intent: "top" }],
);
for (const [text, exp] of KH) test(`khata: ${text}`, () => check(khata(text), exp));

// ---------------- Wholesale ----------------
const WS: [string, Record<string, unknown>][] = [
  ["Malik ko 5000 litre diesel bhej diya", { intent: "supply", client_id: 1, product: "HSD", litres: 5000 }],
  ["Shah Transport ko 5000 litre diesel bheja tanker 3412", { intent: "supply", client_id: 2, product: "HSD", litres: 5000, tanker_id: 1 }],
  ["Malik Petroleum se 2 lakh bank transfer mila", { intent: "payment", client_id: 1, amount: 200000, method: "Bank transfer" }],
  ["Green Fields ka kal 8000 litre diesel ka order", { intent: "order", client_id: 3, product: "HSD", litres: 8000, date: TM }],
  ["Shah Transport jumma ko 3 lakh dega", { intent: "promise", client_id: 2, amount: 300000, date: next(5) }],
  ["Malik ka cheque mila 3 lakh HBL number 1004 date 10 tareekh", { intent: "cheque", client_id: 1, amount: 300000, bank: "Habib Bank (HBL)", cheque_no: "1004", date: ym + "10" }],
  ["Green Fields ne 300 litre diesel wapas kiya", { intent: "return", client_id: 3, product: "HSD", litres: 300 }],
  ["tanker 3412 Shah ko 5000 aur Malik ko 3000 litre diesel", { intent: "trip", tanker_id: 1, product: "HSD", litres: 8000, drops: [[2, 5000], [1, 3000]] }],
  ["Malik ka baqaya kitna hai", { intent: "balance", client_id: 1 }],
  ["aaj kitni supply hui", { intent: "today" }],
  ["ملک کو پانچ ہزار لیٹر ڈیزل", { intent: "supply", client_id: 1, product: "HSD", litres: 5000 }],
  ["شاہ ٹرانسپورٹ سے دو لاکھ ملے", { intent: "payment", client_id: 2, amount: 200000 }],
  ["Shah se do lakh pachas hazar cash mile", { intent: "payment", client_id: 2, amount: 250000, method: "Cash" }],
  ["Malik se sawa lakh mile", { intent: "payment", client_id: 1, amount: 125000 }],
  ["Green Fields se dhai lakh online aaye", { intent: "payment", client_id: 3, amount: 250000, method: "Bank transfer" }],
  ["Malik se 1.5 lakh raast pe aaye", { intent: "payment", client_id: 1, amount: 150000, method: "Raast" }],
  ["Shah Transport se 500k mile", { intent: "payment", client_id: 2, amount: 500000 }],
  ["Malik ko 6000 liter HSD bheja TLR-5520 driver Zafar", { intent: "supply", client_id: 1, product: "HSD", litres: 6000, tanker_id: 2, driver_id: 3 }],
  ["Green Fields ko 4000 litre petrol diya", { intent: "supply", client_id: 3, product: "PMG", litres: 4000 }],
  ["Shah ko 2000 litre super bheja", { intent: "supply", client_id: 2, product: "PMG", litres: 2000 }],
  ["Malik ko 3000 litre high octane bheja", { intent: "supply", client_id: 1, product: "HOBC", litres: 3000 }],
  ["Green Fields ko 5000 litre HOBC", { intent: "supply", client_id: 3, product: "HOBC", litres: 5000 }],
  ["Malik Petroleum ko 5000 litre PMG supply ki", { intent: "supply", client_id: 1, product: "PMG", litres: 5000 }],
  ["گرین فیلڈز کو تین ہزار لیٹر پیٹرول بھیجا", { intent: "supply", client_id: 3, product: "PMG", litres: 3000 }],
  ["Shah Transport parson 10000 litre diesel mangwa raha hai", { intent: "order", client_id: 2, product: "HSD", litres: 10000, date: F2 }],
  ["Malik ka agle hafte 5000 litre diesel ka order", { intent: "order", client_id: 1, product: "HSD", litres: 5000, date: pk(7) }],
  ["Green Fields monday ko 20 hazar dega", { intent: "promise", client_id: 3, amount: 20000, date: next(1) }],
  ["Malik 15 tareekh ko 2 lakh dega", { intent: "promise", client_id: 1, amount: 200000, date: ym + "15" }],
  ["Shah Transport ka UBL cheque 150000 number 556677 mila", { intent: "cheque", client_id: 2, amount: 150000, bank: "United Bank (UBL)", cheque_no: "556677" }],
  ["Malik se Meezan bank ka cheque mila 2 lakh chek no 123456", { intent: "cheque", client_id: 1, amount: 200000, bank: "Meezan Bank", cheque_no: "123456" }],
  ["Green Fields ka 1 lakh ka cheque aaya MCB 778899", { intent: "cheque", client_id: 3, amount: 100000, bank: "MCB Bank", cheque_no: "778899" }],
  ["Green Fields ka hisaab batao", { intent: "balance", client_id: 3 }],
  ["Shah Transport ka kitna due hai", { intent: "balance", client_id: 2 }],
  ["aaj kitne paise aaye", { intent: "today" }],
  ["today's supply total", { intent: "today" }],
  ["Malik ko 5000 diesel", { intent: "supply", client_id: 1, product: "HSD", litres: 5000 }],
  ["Shah Transport ko kal 4000 litre diesel bheja tha", { intent: "supply", client_id: 2, litres: 4000, date: Y }],
  ["Green fields walon ko 8 hazar litre diesel", { intent: "supply", client_id: 3, product: "HSD", litres: 8000 }],
  ["Malik ko ۵۰۰۰ لیٹر ڈیزل بھیجا", { intent: "supply", client_id: 1, product: "HSD", litres: 5000 }],
  ["Pattoki wale sub dealer ko 3000 litre diesel", { intent: "supply", client_id: 1, product: "HSD", litres: 3000 }],
  ["Shah Transport ne 200 litre diesel wapis kar diya", { intent: "return", client_id: 2, product: "HSD", litres: 200 }],
  ["Malik se 50 hazar easypaisa", { intent: "payment", client_id: 1, amount: 50000, method: "Easypaisa" }],
  ["Green Fields se 75000 jazzcash mile", { intent: "payment", client_id: 3, amount: 75000, method: "JazzCash" }],
  ["tanker 5520 se Green Fields ko 3000 aur Shah ko 2000 litre diesel", { intent: "trip", tanker_id: 2, litres: 5000, drops: [[3, 3000], [2, 2000]] }],
  ["Malik ko 5000 litre diesel 280 rate pe bheja", { intent: "supply", client_id: 1, litres: 5000, rate: 280 }],
  ["Malik ko 5000 litre diesel rate 281 bheja", { intent: "supply", client_id: 1, litres: 5000, rate: 281 }],
  ["Malik Petroleum Services se 3 lakh 20 hazar mile", { intent: "payment", client_id: 1, amount: 320000 }],
  ["شاہ ٹرانسپورٹ کا بقایا کتنا ہے", { intent: "balance", client_id: 2 }],
  ["Green Fields kal 2 lakh dega", { intent: "promise", client_id: 3, amount: 200000, date: TM }],
  ["Shah se 2 lakh mile kal", { intent: "payment", client_id: 2, amount: 200000, date: Y }],
  ["Malik ko 5000 litre diesel bheja driver Ghulam Rasool", { intent: "supply", client_id: 1, litres: 5000, driver_id: 1 }],
  ["Akram tanker 3412 Malik ko 4000 litre diesel", { intent: "supply", client_id: 1, litres: 4000, tanker_id: 1, driver_id: 2 }],
  ["LES 0981 Shah ko 3000 litre diesel", { intent: "supply", client_id: 2, litres: 3000, tanker_id: 3 }],
  ["Malik se 20 hazar mile chek se HBL", { intent: "cheque", client_id: 1, amount: 20000, bank: "Habib Bank (HBL)" }],
  ["Malik ka 15 tareekh ka order 6000 litre diesel", { intent: "order", client_id: 1, litres: 6000, date: ym + "15" }],
  ["Shah Transport ko 10 hazar litre diesel chahiye jumeraat ko", { intent: "order", client_id: 2, litres: 10000, date: next(4) }],
  ["Malik ko 5,000 liter diesel", { intent: "supply", client_id: 1, litres: 5000 }],
  ["Malik ne 2000 litre diesel return kiya", { intent: "return", client_id: 1, litres: 2000 }],
  ["Shah ki taraf se 50000 cash mila", { intent: "payment", client_id: 2, amount: 50000, method: "Cash" }],
  ["Malik ko پانچ ہزار لیٹر HSD", { intent: "supply", client_id: 1, product: "HSD", litres: 5000 }],
  ["گرین فیلڈز والوں سے ڈیڑھ لاکھ ملے", { intent: "payment", client_id: 3, amount: 150000 }],
  ["ملک پٹرولیم کا چیک ملا دو لاکھ حبیب بینک نمبر 445566", { intent: "cheque", client_id: 1, amount: 200000, bank: "Habib Bank (HBL)", cheque_no: "445566" }],
  ["Shah Transport se 2.5 lakh mile", { intent: "payment", client_id: 2, amount: 250000 }],
  ["Malik walay 1 crore ka wada kar rahe hain agle mahine", { intent: "promise", client_id: 1, amount: 10000000 }],
  ["Green Fields ko kal 5000 litre diesel bhejna hai", { intent: "order", client_id: 3, litres: 5000, date: TM }],
  ["Shah ko 7000 L diesel bheja", { intent: "supply", client_id: 2, litres: 7000 }],
  ["Shah ko 7000L diesel bheja", { intent: "supply", client_id: 2, litres: 7000 }],
];
WS.push(
  ["Malik walay 1 crore ka wada kar rahe hain agle mahine", { date: nextMonth }],
  ["Malik ko 5000 litre diesel 32/5 date", { intent: "supply", litres: 5000, date: null }],
  ["Malik 45 tareekh ko 2 lakh dega", { intent: "promise", amount: 200000, date: null, missing_has: "date" }],
  ["Malik ko 5000 litre diesel 1/2 hissa", { intent: "supply", litres: 5000, date: null }],
  ["Malik ko 99999999999999999999 litre diesel", { litres: null, missing_has: "litres" }],
  ["Malik 3 tareekh ko 2 lakh dega", { intent: "promise", amount: 200000 }],
  ["Malik ka order next week 5000 litre diesel", { intent: "order", litres: 5000, date: pk(7) }],
  ["kul supply kitni hui", { intent: "today" }],
  ["Shah ka cheque 2 lakh number 123456 mila", { intent: "cheque", cheque_no: "123456" }],
  ["Shah se 2 lakh mila cheque HBL 4455667", { intent: "cheque", cheque_no: "4455667", bank: "Habib Bank (HBL)" }],
  ["Malik ko 3000 litre diesel bheja driver Zafar", { intent: "supply", client_id: 1, driver_id: 3 }],
  ["Malik ko 3000 litre diesel 5/10 ko bheja", { intent: "supply", litres: 3000 }],
);
for (const [text, exp] of WS) test(`wholesale: ${text}`, () => check(ws(text), exp));

// ---------------- shared number words ----------------
test("quantities: compounds and fractions", () => {
  const v = (s: string) => W.quantities(` ${s} `).map((x) => x.value);
  assert.deepEqual(v("sawa lakh"), [125000]);
  assert.deepEqual(v("do lakh pachas hazar"), [250000]);
  assert.deepEqual(v("3 lakh 20 hazar"), [320000]);
  assert.deepEqual(v("teen hazar paanch sau"), [3500]);
  assert.deepEqual(v("saade teen hazar"), [3500]);
  assert.deepEqual(v("paune do lakh"), [175000]);
  assert.deepEqual(v("5000 aur 3000"), [5000, 3000]);
  assert.deepEqual(v("20 hazar 5 litre"), [20000, 5]);
});
