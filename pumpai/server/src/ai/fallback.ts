/**
 * Offline rule-based engine for Roman Urdu / Urdu / English WhatsApp messages.
 * Used when no Claude API key is configured, or if the API call fails, so the bot never goes silent.
 */
import { customerTools, runTool, type ToolCtx } from "./tools.js";
import { PRODUCTS } from "../config.js";
import { pkr, rateFmt } from "../services.js";

const has = (t: string, words: string[]) => words.some((w) => t.includes(w));

export async function fallbackReply(ctx: ToolCtx, text: string): Promise<string> {
  const t = text.toLowerCase();
  const call = async (name: string, input: unknown = {}) => JSON.parse((await runTool(customerTools, name, ctx, input)).content);
  const first = String(ctx.customer.name).split(" ")[0];

  if (has(t, ["stop", "unsubscribe", "band karo", "band kar do", "mat bhejo"])) {
    await call("set_marketing_preference", { opt_in: false });
    return "Aap ko offers ki list se hata diya gaya hai. Dobara shuru karne ke liye START likhein.";
  }
  if (t.trim() === "start") {
    await call("set_marketing_preference", { opt_in: true });
    return "Shukriya! Aap ko dobara offers aur price updates milte rahenge. ✅";
  }
  if (has(t, ["manager", "insaan", "human", "agent", "call me", "call karo", "baat karni"])) {
    await call("handoff_to_human", { reason: "Customer asked for a human" });
    return "Ji, main aap ko hamare manager se mila raha hoon. Woh thori der mein yahin reply karenge. 🙏";
  }
  if (has(t, ["complain", "shikayat", "shikait", "kam diya", "kam daala", "kam dala", "cheat", "dhoka", "fraud", "badtameez", "rude", "kharab", "mila nahi"])) {
    const category = has(t, ["kam", "cheat", "dhoka", "short"]) ? "short_measure" : has(t, ["rude", "badtameez"]) ? "staff_behaviour" : has(t, ["card", "payment", "paisay"]) ? "payment" : "other";
    const r = await call("register_complaint", { category, summary: text.slice(0, 200), sentiment: "negative" });
    return `Hamein bohat afsos hai ${first} sahab. Aap ki shikayat darj kar li gayi hai — Ticket ${r.ticket}. Manager 2 ghante ke andar aap se rabta karenge.`;
  }
  const litres = t.match(/(\d[\d,]*)\s*(l|ltr|litre|liter|litres|liters|لیٹر)\b/);
  if (litres && has(t, ["chahiye", "chahye", "order", "bhej", "deliver", "book", "need", "mangwana"])) {
    const qty = Number(litres[1].replace(/,/g, ""));
    const product = has(t, ["petrol", "super"]) ? "PMG" : has(t, ["octane", "hobc"]) ? "HOBC" : "HSD";
    const prices = await call("get_fuel_prices");
    const p = prices.find((x: any) => x.product === product);
    const acct = await call("get_my_account");
    if (has(t, ["confirm", "haan", "han ji", "ok book", "theek hai"])) {
      const r = await call("book_fuel_order", { product, litres: qty, address: "As per customer (confirm on call)", deliver_at: "As requested", payment: acct.available_credit_pkr > 0 ? "khata" : "cash" });
      if (r.error) return `Maazrat, order book nahi ho saka: ${r.error}`;
      return `✅ Order #${r.order_id} book ho gaya: ${qty}L ${PRODUCTS[product]} = ${pkr(r.amount_pkr)}. Delivery se pehle driver ka number bhej diya jayega.`;
    }
    return `${qty}L ${PRODUCTS[product]} @ ${rateFmt(p.price_pkr)}/L = ${pkr(qty * p.price_pkr)}.\n` +
      (acct.credit_limit_pkr > 0 ? `Khata available credit: ${pkr(acct.available_credit_pkr)}.\n` : "") +
      `Delivery address aur time bata dein, phir "confirm ${qty} litre" likh kar order pakka karein.`;
  }
  if (has(t, ["balance", "khata", "udhaar", "udhar", "baqaya", "bakaya", "hisab", "hisaab", "statement", "bill"])) {
    const a = await call("get_my_account");
    if (!a.credit_limit_pkr && !a.khata_balance_pkr) return `${first} sahab, aap ka koi khata account nahi hai. Khata kholne ke liye manager se baat karein — "manager" likhein.`;
    const link = a.khata_balance_pkr > 0 ? (await call("create_payment_link", {})).link : null;
    return `📒 ${a.name} — Khata\nBalance: ${pkr(a.khata_balance_pkr)}\nLimit: ${pkr(a.credit_limit_pkr)} (available ${pkr(a.available_credit_pkr)})` +
      (link ? `\n\nJazzCash / Easypaisa / Raast se pay karein:\n${link}` : "");
  }
  if (has(t, ["pay", "payment", "jazzcash", "easypaisa", "raast", "link"])) {
    const r = await call("create_payment_link", {});
    if (r.error) return "Aap ka koi baqaya nahi hai. Shukriya! 🙏";
    return `Payment link (${pkr(r.amount_pkr)}):\n${r.link}\nJazzCash, Easypaisa, Raast ya card se pay kar sakte hain.`;
  }
  if (has(t, ["points", "point", "loyalty", "reward", "inaam"])) {
    const a = await call("get_my_account");
    return `🎁 Aap ke ${a.loyalty_points} loyalty points hain (= ${pkr(a.points_value_pkr)}). Har Rs 100 par 1 point milta hai. Agli fueling par cashier ko batayein.`;
  }
  if (has(t, ["order status", "mera order", "order kahan", "delivery kab"])) {
    const o = await call("get_my_orders");
    if (!o.length) return "Aap ka koi order nahi mila.";
    return "Aap ke orders:\n" + o.map((x: any) => `#${x.id} ${x.litres}L ${x.product} — ${x.status}`).join("\n");
  }
  if (has(t, ["rate", "price", "qeemat", "qimat", "keemat", "kimat", "قیمت", "kitne ka", "kya rate", "aaj ka"])) {
    const p = await call("get_fuel_prices");
    return "⛽ Aaj ke rates:\n" + p.map((x: any) => `• ${x.name}: ${rateFmt(x.price_pkr)}/L`).join("\n") + `\n(Since ${p[0]?.since ?? "-"})`;
  }
  if (has(t, ["kahan", "location", "address", "map", "timing", "kab khulta", "open"])) {
    const s = await call("find_stations", {});
    return s.map((x: any) => `📍 ${x.name}, ${x.address}\n🕒 ${x.timings}${x.map ? `\n${x.map}` : ""}`).join("\n\n");
  }
  if (has(t, ["mera naam", "my name is", "naam hai"])) {
    const m = text.match(/(?:mera naam|my name is)\s+([a-z\s]+?)(?:\s+hai)?$/i);
    if (m) {
      await call("update_my_name", { name: m[1].trim().replace(/\b\w/g, (c) => c.toUpperCase()) });
      return `Shukriya ${m[1].trim()}! Aap ka naam save ho gaya.`;
    }
  }
  if (has(t, ["shukriya", "thanks", "thank you", "jazakallah", "jazak"])) return "Aap ka bhi shukriya! Safar mubarak 🚗⛽";
  return `Assalam-o-Alaikum ${first}! Main aap ka digital assistant hoon. Aap mujh se pooch sakte hain:\n` +
    "1️⃣ Aaj ka rate\n2️⃣ Khata balance / payment link\n3️⃣ Diesel/petrol order (e.g. \"500 litre diesel chahiye\")\n4️⃣ Loyalty points\n5️⃣ Shikayat\n6️⃣ Manager se baat";
}
