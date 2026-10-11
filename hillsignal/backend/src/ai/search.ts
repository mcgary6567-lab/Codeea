import { z } from "zod";
import { betaZodOutputFormat } from "@anthropic-ai/sdk/helpers/beta/zod";
import { config } from "../config.js";
import { KNOWN_SECTORS } from "../ingest/sectors.js";
import type { TradeFilters } from "../data/trades.js";
import { ai, assertNotRefused, FALLBACK } from "./client.js";

// Every field is required-but-nullable so the schema is valid for structured outputs.
const Parsed = z.object({
  politicianName: z.string().nullable().describe("Full or last name of a member of Congress"),
  chamber: z.enum(["house", "senate"]).nullable(),
  party: z.enum(["D", "R", "I"]).nullable(),
  state: z.string().nullable().describe("Two-letter US state code"),
  ticker: z.string().nullable().describe("Stock ticker, uppercase"),
  sector: z.enum(KNOWN_SECTORS as [string, ...string[]]).nullable(),
  txType: z.enum(["buy", "sell", "exchange"]).nullable(),
  from: z.string().nullable().describe("Earliest transaction date, YYYY-MM-DD"),
  to: z.string().nullable().describe("Latest transaction date, YYYY-MM-DD"),
  minAmount: z.number().nullable().describe("Minimum trade value in USD"),
  sort: z.enum(["-pubDate", "-txDate", "-amount"]),
  explanation: z.string().describe("One short sentence describing what is being searched for"),
});

const SYSTEM = `You turn questions about U.S. congressional stock trades into search filters for a database of
public financial disclosures (House and Senate Periodic Transaction Reports).

Rules:
- Only set a filter when the question clearly implies it; otherwise null.
- Resolve relative dates ("last month", "this year", "since March") against the date given in the request.
- Map company names to their primary U.S. ticker when you are confident (e.g. "Nvidia" -> NVDA). If unsure, leave ticker null.
- "Senators" -> chamber senate; "representatives"/"House members" -> chamber house. "Democrats" -> D, "Republicans" -> R.
- "Big"/"large" trades -> minAmount 100000 unless a figure is given. "Biggest" -> sort -amount. "Recent"/default -> sort -pubDate.
- sector must be one of the listed values or null.
- If the question is not about congressional trades, return all filters null and say so in explanation.`;

export interface SearchPlan {
  filters: TradeFilters;
  sort: "-pubDate" | "-txDate" | "-amount";
  explanation: string;
}

export async function planSearch(question: string, today = new Date()): Promise<SearchPlan> {
  const msg = await ai().beta.messages.parse({
    model: config.AI_MODEL,
    max_tokens: 4000,
    ...FALLBACK,
    output_config: { effort: "low", format: betaZodOutputFormat(Parsed) },
    system: SYSTEM,
    messages: [{ role: "user", content: `Today is ${today.toISOString().slice(0, 10)}.\n\nQuestion: ${question}` }],
  });
  assertNotRefused(msg);
  const p = msg.parsed_output;
  if (!p) throw new Error("AI search returned no parseable output");
  return {
    filters: cleanFilters(p),
    sort: p.sort,
    explanation: p.explanation,
  };
}

/** Drop nulls and anything malformed so bad model output can't break the SQL layer. */
export function cleanFilters(p: Omit<z.infer<typeof Parsed>, "sort" | "explanation">): TradeFilters {
  const f: TradeFilters = {};
  const isoDate = /^\d{4}-\d{2}-\d{2}$/;
  if (p.politicianName?.trim()) f.politicianName = p.politicianName.trim();
  if (p.chamber) f.chamber = p.chamber;
  if (p.party) f.party = p.party;
  if (p.state && /^[A-Za-z]{2}$/.test(p.state)) f.state = p.state.toUpperCase();
  if (p.ticker && /^[A-Za-z][A-Za-z0-9.\-]{0,9}$/.test(p.ticker)) f.ticker = p.ticker.toUpperCase();
  if (p.sector) f.sector = p.sector;
  if (p.txType) f.txType = p.txType;
  if (p.from && isoDate.test(p.from)) f.from = p.from;
  if (p.to && isoDate.test(p.to)) f.to = p.to;
  if (typeof p.minAmount === "number" && p.minAmount > 0) f.minAmount = p.minAmount;
  return f;
}
