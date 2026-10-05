/**
 * Read numbers from photos with Claude: dispenser meter (totalizer), tanker invoice, expense receipt,
 * and turn a spoken sentence into a POS sale. Each call forces a single "record" tool so the answer
 * comes back as structured data. Callers fall back to typing (or the rule parser) when AI is off.
 */
import Anthropic from "@anthropic-ai/sdk";
import { config, aiEnabled, PRODUCTS } from "../config.js";
import { parseSaleText, type ParsedSale } from "./parseSale.js";

let client: Anthropic | null = null;
const claude = () => (client ??= new Anthropic({ apiKey: config.anthropicKey, maxRetries: 2, timeout: 60_000 }));

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
