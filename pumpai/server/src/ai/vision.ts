/**
 * Read numbers from photos with Claude: dispenser meter (totalizer), tanker invoice, expense receipt,
 * and turn a spoken sentence into a POS sale. Each call forces a single "record" tool so the answer
 * comes back as structured data. Callers fall back to typing (or the rule parser) when AI is off.
 */
import Anthropic from "@anthropic-ai/sdk";
import { config, aiEnabled, PRODUCTS } from "../config.js";
import { parseSaleText, type ParsedSale } from "./parseSale.js";
import { parseWholesaleText, type ParsedWholesale, type Named } from "./parseWholesale.js";
import { pkDate } from "../db.js";
export type WholesaleCtx = { clients: Named[]; tankers: { id: number; number: string }[]; drivers: Named[]; clientId?: number | null };

let client: Anthropic | null = null;
let clientKey = "";
// rebuilt when the key is changed from Settings → Integrations
const claude = () => { if (!client || clientKey !== config.anthropicKey) { clientKey = config.anthropicKey; client = new Anthropic({ apiKey: clientKey, maxRetries: 2, timeout: 60_000 }); } return client; };

async function record<T>(content: Anthropic.Beta.BetaContentBlockParam[], description: string, schema: Record<string, unknown>): Promise<T | null> {
  const res = await claude().beta.messages.create({
    model: config.aiModel,
    max_tokens: 2048,
    betas: ["server-side-fallback-2026-07-01"],
    fallbacks: "default",
    output_config: { effort: config.aiEffort },
    tools: [{ name: "record", description, input_schema: { type: "object", ...schema } as Anthropic.Beta.BetaTool.InputSchema }],
    tool_choice: { type: "tool", name: "record" },
    messages: [{ role: "user", content }],
  });
  const use = res.content.find((b): b is Anthropic.Beta.BetaToolUseBlock => b.type === "tool_use");
  return (use?.input as T) ?? null;
}

export type PhotoKind = "meter" | "invoice" | "receipt" | "bill";
const PROMPTS: Record<PhotoKind, { text: string; description: string; schema: Record<string, unknown> }> = {
  meter: {
    text: "This is a photo of a fuel dispenser at a Pakistani petrol pump. Read the TOTALIZER (the cumulative litres counter, often labelled 'Total' or 'Totalizer', usually the longest number), not the sale amount or the price per litre. If more than one totalizer is visible (one per nozzle), return each one with its side or label. Copy every digit exactly, including decimals. If a digit is unclear, say so in the note and lower the confidence.",
    description: "Record the totalizer reading(s) read from the dispenser photo.",
    schema: {
      properties: {
        readings: { type: "array", items: { type: "object", properties: { label: { type: "string" }, value: { type: "number" } }, required: ["value"] } },
        confidence: { type: "string", enum: ["high", "medium", "low"] },
        note: { type: "string" },
      },
      required: ["readings", "confidence"],
    },
  },
  invoice: {
    text: `This is a fuel tanker delivery invoice / challan from an oil company in Pakistan. Extract the product (${Object.entries(PRODUCTS).map(([k, v]) => `${k} = ${v}`).join(", ")}), invoiced litres, rate per litre, total amount, tanker/vehicle number, supplier (oil company or depot) name, invoice number and date. Leave a field out if it is not on the invoice.`,
    description: "Record the tanker invoice details.",
    schema: {
      properties: {
        product: { type: "string", enum: Object.keys(PRODUCTS) }, invoice_litres: { type: "number" }, rate_per_litre: { type: "number" },
        total_amount: { type: "number" }, tanker_no: { type: "string" }, supplier_name: { type: "string" }, invoice_no: { type: "string" }, date: { type: "string" },
        confidence: { type: "string", enum: ["high", "medium", "low"] },
      },
      required: ["confidence"],
    },
  },
  bill: {
    text: "This is a utility bill in Pakistan (electricity from LESCO, IESCO, MEPCO, GEPCO, FESCO, PESCO, HESCO, QESCO, K-Electric etc., or a gas bill from SNGPL / SSGC). Read the units consumed this month (for electricity: total units / KWH, add peak and off-peak if shown separately), the amount payable within the due date, the due date, the billing month as YYYY-MM, and the reference / consumer number.",
    description: "Record the utility bill details.",
    schema: {
      properties: {
        units: { type: "number" }, amount: { type: "number" }, due_date: { type: "string" }, month: { type: "string", description: "YYYY-MM" },
        reference: { type: "string" }, confidence: { type: "string", enum: ["high", "medium", "low"] },
      },
      required: ["confidence"],
    },
  },
  receipt: {
    text: "This is a receipt or bill for an expense at a petrol pump in Pakistan (it may be handwritten, in Urdu or English). Extract the total amount paid in rupees, who was paid, the date and a short description. Choose the closest category from the list given.",
    description: "Record the expense receipt details.",
    schema: {
      properties: {
        amount: { type: "number" }, paid_to: { type: "string" }, date: { type: "string" }, description: { type: "string" },
        category: { type: "string" }, confidence: { type: "string", enum: ["high", "medium", "low"] },
      },
      required: ["confidence"],
    },
  },
};

export async function readPhoto(kind: PhotoKind, base64: string, mediaType: string, extra = ""): Promise<Record<string, unknown> | null> {
  if (!aiEnabled()) return null;
  const p = PROMPTS[kind];
  try {
    return await record<Record<string, unknown>>([
      { type: "image", source: { type: "base64", media_type: mediaType as "image/jpeg", data: base64 } },
      { type: "text", text: p.text + (extra ? `\n${extra}` : "") },
    ], p.description, p.schema);
  } catch (e) {
    console.error("[vision]", (e as Error).message);
    return null;
  }
}

/** Spoken sentence → sale. Rules first (instant, offline-safe); Claude refines it when available. */
export async function parseSale(text: string, accounts: { id: number; name: string }[]): Promise<ParsedSale> {
  const rules = parseSaleText(text, accounts);
  if (!aiEnabled()) return rules;
  try {
    const r = await record<Partial<ParsedSale>>([{ type: "text", text:
      `A petrol pump salesman in Pakistan said this (it may be Urdu, Roman Urdu, English or mixed): "${text}".\n` +
      `Fill in the sale. Products: ${Object.entries(PRODUCTS).map(([k, v]) => `${k} = ${v}`).join(", ")}. ` +
      "Give litres if a quantity in litres was said, otherwise amount in rupees (hazar = thousand). Payment: cash, easypaisa, jazzcash, card, raast or khata (credit/udhaar, or when an account name is said). " +
      `Khata accounts (id: name): ${accounts.map((a) => `${a.id}: ${a.name}`).join("; ") || "none"}. Only use a customer_id from this list. Leave out anything that was not said.` }],
    "Record the sale that was said.", {
      properties: {
        product: { type: "string", enum: Object.keys(PRODUCTS) }, litres: { type: "number" }, amount: { type: "number" },
        payment_method: { type: "string", enum: ["cash", "easypaisa", "jazzcash", "card", "raast", "khata"] },
        customer_id: { type: "integer" }, vehicle_no: { type: "string" }, slip_no: { type: "string" },
      },
    });
    if (!r) return rules;
    const acct = accounts.find((a) => a.id === r.customer_id);
    return {
      ...rules, engine: "claude",
      product: r.product ?? rules.product, litres: r.litres ?? (r.amount ? null : rules.litres), amount: r.amount ?? (r.litres ? null : rules.amount),
      payment_method: r.payment_method ?? rules.payment_method, customer_id: acct?.id ?? rules.customer_id, customer_name: acct?.name ?? rules.customer_name,
      vehicle_no: r.vehicle_no?.toUpperCase() ?? rules.vehicle_no, slip_no: r.slip_no ?? rules.slip_no,
    };
  } catch (e) {
    console.error("[voice]", (e as Error).message);
    return rules;
  }
}

/** Wholesale command (supply / payment / order / promise / cheque / return / trip / question) from one sentence. */
export async function parseWholesale(text: string, ctx: WholesaleCtx): Promise<ParsedWholesale> {
  const rules = parseWholesaleText(text, ctx);
  if (!aiEnabled()) return rules;
  try {
    const list = (xs: { id: number; name?: string; number?: string }[]) => xs.map((x) => `${x.id}: ${x.name ?? x.number}`).join("; ") || "none";
    const r = await record<Partial<ParsedWholesale> & { drops?: { client_id: number; litres: number }[] }>([{ type: "text", text:
      `The wholesale officer of a petrol pump in Pakistan said this (Urdu, Roman Urdu, English or mixed): "${text}". Today is ${pkDate()}.\n` +
      "Work out the ONE entry they want: supply (fuel sent to a client), return (fuel brought back), payment (money received), order (client booked fuel for a day), " +
      "promise (client will pay on a day), cheque (cheque received, with bank and number), trip (one tanker, several clients each with litres), balance (asking a client's due) or today (asking today's totals).\n" +
      `Products: ${Object.entries(PRODUCTS).map(([k, v]) => `${k} = ${v}`).join(", ")}. Numbers: hazar = 1,000, lakh = 100,000, crore = 10,000,000, dedh = 1.5, dhai = 2.5. ` +
      "Dates as YYYY-MM-DD: kal is tomorrow for orders/promises and yesterday for things already done; parson is two days.\n" +
      `Clients (id: name): ${list(ctx.clients)}. Tankers: ${list(ctx.tankers)}. Drivers: ${list(ctx.drivers)}. Only use ids from these lists. Leave out anything that was not said.` }],
    "Record the wholesale entry that was said.", {
      properties: {
        intent: { type: "string", enum: ["supply", "payment", "order", "promise", "cheque", "return", "trip", "balance", "today", "unknown"] },
        client_id: { type: "integer" }, product: { type: "string", enum: Object.keys(PRODUCTS) }, litres: { type: "number" }, amount: { type: "number" }, rate: { type: "number" },
        method: { type: "string", enum: ["Cash", "Bank transfer", "Cheque", "Raast", "JazzCash", "Easypaisa"] }, date: { type: "string" },
        bank: { type: "string" }, cheque_no: { type: "string" }, tanker_id: { type: "integer" }, driver_id: { type: "integer" }, location: { type: "string" },
        drops: { type: "array", items: { type: "object", properties: { client_id: { type: "integer" }, litres: { type: "number" } }, required: ["client_id", "litres"] } },
      },
      required: ["intent"],
    });
    if (!r || !r.intent) return rules;
    const client = ctx.clients.find((c) => c.id === r.client_id);
    const tanker = ctx.tankers.find((x) => x.id === r.tanker_id);
    const driver = ctx.drivers.find((x) => x.id === r.driver_id);
    const drops = (r.drops ?? []).map((d) => ({ ...d, client_name: ctx.clients.find((c) => c.id === d.client_id)?.name ?? "" })).filter((d) => d.client_name && d.litres > 0);
    const merged: ParsedWholesale = {
      ...rules, engine: "claude", intent: r.intent as ParsedWholesale["intent"],
      client_id: client?.id ?? rules.client_id, client_name: client?.name ?? rules.client_name,
      product: r.product ?? rules.product, litres: r.litres ?? rules.litres, amount: r.amount ?? rules.amount, rate: r.rate ?? rules.rate, method: r.method ?? rules.method,
      date: r.date && /^\d{4}-\d{2}-\d{2}$/.test(r.date) ? r.date : rules.date, bank: r.bank ?? rules.bank, cheque_no: r.cheque_no ?? rules.cheque_no,
      tanker_id: tanker?.id ?? rules.tanker_id, tanker: tanker?.number ?? rules.tanker, driver_id: driver?.id ?? rules.driver_id, driver: driver?.name ?? rules.driver,
      location: r.location ?? rules.location, drops: drops.length >= 2 ? drops : rules.drops,
    };
    return merged;
  } catch (e) {
    console.error("[wholesale voice]", (e as Error).message);
    return rules;
  }
}
