/**
 * Claude-powered agents:
 *  - WhatsApp customer agent (Roman Urdu / Urdu / English) with tools scoped to the customer
 *  - Owner business assistant ("Ask AI") with analytics tools
 *  - Campaign copywriter
 * Every entry point degrades to the rule-based engine when no API key is configured or the call fails.
 */
import Anthropic from "@anthropic-ai/sdk";
import { config, aiEnabled, aiProvider, PRODUCTS } from "../config.js";
import { geminiAgentLoop, geminiText, toGContents } from "./gemini.js";
import { all, get, run, getSetting, now, pkDate, type Row } from "../db.js";
import { readPhoto } from "./vision.js";
import { recordPaymentScreenshot } from "../routes/paymentInbox.js";
import { customerTools, runTool, toolSchemas, type ToolCtx } from "./tools.js";
import { lookups, lookupKeys } from "../routes/lookups.js";
import { fallbackReply } from "./fallback.js";
import { businessTools } from "./businessTools.js";
import { ensureConversation, storeMessage, sendWhatsApp, sendDirect, fetchMedia, bus } from "../whatsapp/cloud.js";
import { quickAnswer, summaryAnswer } from "./ownerAnswers.js";
import { upsertCustomerByPhone, currentPrices, pkr, normalizePhone } from "../services.js";
import { insights, kpis } from "./analytics.js";
import { handleApprovalReply } from "../routes/approvals.js";
import { handleRatingReply } from "../routes/feedback.js";

let client: Anthropic | null = null;
let clientKey = "";
// rebuilt when the key is changed from Settings → Integrations
const claude = () => { if (!client || clientKey !== config.anthropicKey) { clientKey = config.anthropicKey; client = new Anthropic({ apiKey: clientKey, maxRetries: 2, timeout: 60_000 }); } return client; };

type Tools = Parameters<typeof runTool>[0];

/** Manual tool loop on the beta Messages API with server-side refusal fallbacks enabled. */
async function runAgentLoop(system: string, tools: Tools, messages: Anthropic.Beta.BetaMessageParam[], ctx: ToolCtx, maxSteps = 6) {
  for (let step = 0; step < maxSteps; step++) {
    const response = await claude().beta.messages.create({
      model: config.aiModel,
      max_tokens: 16000,
      betas: ["server-side-fallback-2026-07-01"],
      fallbacks: "default",
      output_config: { effort: config.aiEffort },
      system: [{ type: "text", text: system, cache_control: { type: "ephemeral" } }],
      tools: toolSchemas(tools) as Anthropic.Beta.BetaTool[],
      messages,
    });
    if (response.stop_reason === "refusal") return null;
    messages.push({ role: "assistant", content: response.content as Anthropic.Beta.BetaContentBlockParam[] });
    if (response.stop_reason === "pause_turn") continue;
    const uses = response.content.filter((b): b is Anthropic.Beta.BetaToolUseBlock => b.type === "tool_use");
    if (response.stop_reason !== "tool_use" || uses.length === 0) {
      return response.content.filter((b): b is Anthropic.Beta.BetaTextBlock => b.type === "text").map((b) => b.text).join("\n").trim();
    }
    const results: Anthropic.Beta.BetaToolResultBlockParam[] = [];
    for (const u of uses) {
      const r = await runTool(tools, u.name, ctx, u.input);
      results.push({ type: "tool_result", tool_use_id: u.id, content: r.content, is_error: r.is_error });
    }
    messages.push({ role: "user", content: results });
  }
  return null;
}

function customerSystemPrompt(tenantId: number, customer: Row) {
  const tenant = get("SELECT * FROM tenants WHERE id=?", tenantId)!;
  const stations = all("SELECT name, city, address, timings, services FROM stations WHERE tenant_id=?", tenantId);
  return `You are the WhatsApp assistant of ${tenant.name}, a petrol pump business in Pakistan.
Stations: ${stations.map((s) => `${s.name} (${s.address}; ${s.timings}; services: ${s.services})`).join(" | ")}.
Products: ${Object.entries(PRODUCTS).map(([k, v]) => `${k} = ${v}`).join(", ")}.
Booking services (key = name): ${lookups(tenantId, "booking_service").map((s) => `${s.key} = ${s.label}`).join(", ")}.
Complaint categories: ${lookupKeys(tenantId, "complaint_category").join(", ")}.

How to reply:
- Mirror the customer's language: Roman Urdu if they write Roman Urdu, Urdu script for Urdu, English for English. Default to Roman Urdu.
- WhatsApp style: short, warm and respectful ("ji", "sahab"/"sahiba" only if obvious), a few emojis at most, no markdown headings or tables. Use *bold* sparingly.
- Never state a price, balance, points or order status from memory: always call the matching tool first. Amounts are in PKR, write as "Rs 1,23,456" style.
- For bulk/delivery orders: collect product, litres, address, time and payment (khata/cash/digital), repeat the total back and only call book_fuel_order after the customer clearly confirms.
- Complaints: apologise, log with register_complaint, give the ticket number. Short-measure complaints are serious.
- Hand off to a human (handoff_to_human) for anger, credit-limit increases, price negotiation, legal/safety issues, or anything you cannot do.
- If they say STOP / "band karo" call set_marketing_preference(false).
- You cannot change prices, grant discounts or waive dues. Do not invent policies.
Customer context: name "${customer.name}", type ${customer.type}, city ${customer.city ?? "unknown"}.`;
}

/** Build Claude message history from stored WhatsApp messages (text only; consecutive same-role merged). */
function history(convId: number, limit = 16): Anthropic.Beta.BetaMessageParam[] {
  const rows = all("SELECT direction, body FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?", convId, limit).reverse();
  const msgs: Anthropic.Beta.BetaMessageParam[] = [];
  for (const r of rows) {
    const role = r.direction === "in" ? "user" : "assistant";
    const last = msgs[msgs.length - 1];
    if (last && last.role === role) last.content = `${last.content}\n${r.body}`;
    else msgs.push({ role, content: r.body });
  }
  while (msgs.length && msgs[0].role !== "user") msgs.shift();
  return msgs;
}

export async function generateCustomerReply(tenantId: number, customer: Row, conversation: Row, text: string) {
  const ctx: ToolCtx = { tenantId, customer, conversation, actions: [] };
  let reply: string | null = null;
  let engine = "rules";
  const provider = aiProvider();
  if (provider !== "none") {
    try {
      const sys = customerSystemPrompt(tenantId, customer);
      const hist = history(conversation.id);
      if (provider === "gemini") {
        reply = await geminiAgentLoop(sys, toolSchemas(customerTools), toGContents(hist as any), ctx, runTool);
        engine = "gemini";
      } else {
        reply = await runAgentLoop(sys, customerTools, hist, ctx);
        engine = "claude";
      }
    } catch (e: any) {
      console.error("[ai] customer agent failed, using rules:", e?.message);
    }
  }
  if (!reply) {
    reply = await fallbackReply(ctx, text);
    engine = "rules";
  }
  return { reply, engine, actions: ctx.actions };
}

/** Full inbound pipeline used by the Meta webhook and the in-app simulator. */
export async function handleInbound(tenantId: number, msg: { from: string; name?: string; text: string; waId?: string; image?: { id?: string; mime?: string; caption?: string; base64?: string } }) {
  // the owner or a manager asking about the business gets the business assistant, not the customer bot
  const boss = staffByPhone(tenantId, msg.from);
  if (boss) {
    if (msg.waId && get("SELECT id FROM outbox WHERE ref=?", `wa:${msg.waId}`)) return { duplicate: true };
    run("INSERT INTO outbox (tenant_id,to_phone,to_name,kind,ref,text,simulated,created_at) VALUES (?,?,?,?,?,?,?,?)",
      tenantId, boss.phone, boss.name, "owner_question", msg.waId ? `wa:${msg.waId}` : null, msg.text, 1, new Date().toISOString());
    // "1" / "2" answers a waiting approval (price change, big expense)
    const approval = await handleApprovalReply(tenantId, msg.from, msg.text);
    if (approval) { await sendDirect(tenantId, boss, "approval_reply", null, approval); return { handled_by: "approval", reply: approval }; }
    const r = await askBusiness(tenantId, msg.text);
    const answer = r.engine === "rules" && !quickAnswer(tenantId, msg.text) ? summaryAnswer(tenantId) + "\n\n(Poochhein: sale, kharcha, stock, cash, supply, shift, kis ne dene hain — 'kal' likhein to kal ka.)" : r.answer;
    await sendDirect(tenantId, boss, "owner_answer", null, answer);
    return { handled_by: "owner_assistant", engine: r.engine, reply: answer };
  }
  const customer = upsertCustomerByPhone(tenantId, msg.from, msg.name);
  const conv = ensureConversation(tenantId, customer.id);
  if (msg.waId && get("SELECT id FROM messages WHERE wa_id=?", msg.waId)) return { duplicate: true };
  storeMessage(conv, "in", "customer", msg.text, null, msg.waId);
  // "1"-"5" answers the rating question sent after a fill
  const rating = await handleRatingReply(tenantId, customer, msg.text);
  if (rating) {
    const sent = await sendWhatsApp(tenantId, get("SELECT * FROM customers WHERE id=?", customer.id)!, rating, "system", { kind: "rating_reply" });
    return { handled_by: "rating", reply: rating, message: sent.message };
  }
  // a payment screenshot from a khata customer → read it (if a key is set) and queue it for the cashier to confirm
  if (msg.image) {
    const media = msg.image.base64 ? { base64: msg.image.base64, mime: msg.image.mime ?? "image/jpeg" } : msg.image.id ? await fetchMedia(msg.image.id) : null;
    let parsed: Record<string, unknown> | null = null, photoId: number | null = null;
    if (media) {
      parsed = await readPhoto("payment", media.base64, media.mime).catch(() => null);
      try { photoId = run("INSERT INTO photos (tenant_id,kind,ref,mime,data,ai_result,created_at) VALUES (?,?,?,?,?,?,?)",
        tenantId, "payment", null, media.mime, Buffer.from(media.base64, "base64"), parsed ? JSON.stringify(parsed) : null, now()).id; } catch { photoId = null; }
    }
    const row = await recordPaymentScreenshot(tenantId, { phone: msg.from, sender_name: msg.name ?? (parsed?.sender as string) ?? null,
      amount: (parsed?.amount as number) ?? null, method: (parsed?.method as string) ?? null, reference: (parsed?.reference as string) ?? null,
      confidence: (parsed?.confidence as string) ?? null, photo_id: photoId, caption: msg.image.caption ?? null });
    const ack = row
      ? `Shukriya ${customer.name}! 🧾 Payment screenshot mil gaya${row.amount ? ` (Rs ${Math.round(Number(row.amount)).toLocaleString("en-PK")})` : ""}. Humari team check kar ke aap ke khate me jama kar degi. ✅`
      : "Payment screenshot mil gaya. Agar aap khata customer hain to humari team jald jama kar degi; warna pump par rabta karein.";
    const sent = await sendWhatsApp(tenantId, get("SELECT * FROM customers WHERE id=?", customer.id)!, ack, "system", { kind: "payment_ack" });
    return { handled_by: "payment_screenshot", queued: Boolean(row), payment_inbox_id: row?.id ?? null, message: sent.message };
  }
  const fresh = get("SELECT * FROM conversations WHERE id=?", conv.id)!;
  if (fresh.mode === "human") {
    bus.emit("event", { type: "needs_human", tenant_id: tenantId, conversation_id: conv.id });
    return { handled_by: "human_queue" };
  }
  const { reply, engine, actions } = await generateCustomerReply(tenantId, customer, fresh, msg.text);
  const sent = await sendWhatsApp(tenantId, get("SELECT * FROM customers WHERE id=?", customer.id)!, reply, "ai", { engine, actions });
  return { reply, engine, actions, message: sent.message };
}

/** Owner (settings phone) or an active admin/manager whose WhatsApp number matches. */
function staffByPhone(tenantId: number, from: string): { phone: string; name: string } | null {
  const p = normalizePhone(from);
  const owner = getSetting(tenantId, "owner_phone") || get("SELECT owner_phone FROM tenants WHERE id=?", tenantId)?.owner_phone;
  if (owner && normalizePhone(owner) === p) return { phone: p, name: get("SELECT owner_name FROM tenants WHERE id=?", tenantId)?.owner_name ?? "Owner" };
  const u = get("SELECT name FROM users WHERE tenant_id=? AND phone=? AND active=1 AND role IN ('admin','manager')", tenantId, p);
  return u ? { phone: p, name: u.name } : null;
}

/** Owner/manager "Ask AI" about the business. */
export async function askBusiness(tenantId: number, question: string) {
  const provider = aiProvider();
  if (provider !== "none") {
    const ctx: ToolCtx = { tenantId, customer: {}, conversation: {}, actions: [] };
    const system = `You are the AI business analyst for a Pakistani petrol pump owner. Answer in the language of the question (English or Roman Urdu).
Use tools to fetch real numbers; never guess. Be concise: lead with the answer, then 2-5 bullet points with figures and one concrete recommendation.
Products: PMG = petrol, HOBC = hi-octane, HSD = diesel. Currency PKR. Today is ${pkDate()} (Pakistan time).`;
    try {
      const answer = provider === "gemini"
        ? await geminiAgentLoop(system, toolSchemas(businessTools), [{ role: "user", parts: [{ text: question }] }], ctx, runTool, 8)
        : await runAgentLoop(system, businessTools, [{ role: "user", content: question }], ctx, 8);
      if (answer) return { answer, engine: provider };
    } catch (e: any) {
      console.error("[ai] business agent failed:", e?.message);
    }
  }
  const quick = quickAnswer(tenantId, question);
  if (quick) return { answer: quick, engine: "rules" };
  const k = kpis(tenantId);
  const cards = insights(tenantId);
  const answer =
    `Today: ${Math.round(k.today.litres).toLocaleString()}L sold, ${pkr(k.today.amount)} revenue across ${k.today.txns} sales. ` +
    `Khata outstanding ${pkr(k.khata.outstanding)} from ${k.khata.debtors} customers. ${k.open_alerts} open alerts.\n\n` +
    cards.map((c) => `• ${c.title} — ${c.body}`).join("\n") +
    `\n\n(Connect a Claude or free Gemini API key for free-form answers to any question.)`;
  return { answer, engine: "rules" };
}

/** Write a WhatsApp campaign message for a segment and goal. */
export async function writeCampaign(tenantId: number, goal: string, segment: string) {
  const tenant = get("SELECT name FROM tenants WHERE id=?", tenantId)!;
  const prices = currentPrices(tenantId);
  const campaignSystem = "You write WhatsApp marketing messages for a Pakistani petrol pump. Roman Urdu, under 450 characters, friendly, 1-3 emojis, a clear call to action, end with 'STOP likh kar unsubscribe karein'. Output only the message text.";
  const campaignUser = `Business: ${tenant.name}. Segment: ${segment}. Goal: ${goal}. Current prices: ${JSON.stringify(prices)}. Use {name} as the customer-name placeholder.`;
  if (aiProvider() === "gemini") {
    try {
      const text = await geminiText(campaignSystem, campaignUser);
      if (text) return { message: text, engine: "gemini" };
    } catch (e: any) {
      console.error("[ai] campaign writer (gemini) failed:", e?.message);
    }
  } else if (aiEnabled()) {
    try {
      const r = await claude().messages.create({
        model: config.aiModel,
        max_tokens: 2000,
        output_config: { effort: "low" },
        system: campaignSystem,
        messages: [{ role: "user", content: campaignUser }],
      });
      const text = r.content.filter((b): b is Anthropic.TextBlock => b.type === "text").map((b) => b.text).join("").trim();
      if (text) return { message: text, engine: "claude" };
    } catch (e: any) {
      console.error("[ai] campaign writer failed:", e?.message);
    }
  }
  return {
    message: `Assalam-o-Alaikum {name}! ⛽ ${tenant.name} ki taraf se khaas offer: ${goal}. Aaj hi tashreef layein ya isi number par reply karein. STOP likh kar unsubscribe karein.`,
    engine: "rules",
  };
}
