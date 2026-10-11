import type Anthropic from "@anthropic-ai/sdk";
import { z } from "zod";
import { config } from "../config.js";
import { getPolitician, listPoliticians, overview } from "../data/entities.js";
import { listTrades, type Trade } from "../data/trades.js";
import type { Entitlements } from "../plans.js";
import { ai, assertNotRefused, DISCLAIMER, FALLBACK } from "./client.js";

type Msg = Anthropic.Beta.BetaMessageParam;
type Tool = Anthropic.Beta.BetaTool;

const SYSTEM = `You are HillSignal's research assistant. You answer questions about U.S. members of Congress and
their publicly disclosed stock trades (STOCK Act Periodic Transaction Reports), using the tools to look up data.

- Always look data up with the tools before answering factual questions; never guess trades or numbers.
- Cite specifics from tool results (names, dates, tickers, amount ranges). Amounts are disclosure ranges.
- If the tools return nothing relevant, say you couldn't find it in the disclosures.
- Do not accuse anyone of illegal or unethical conduct or speculate about non-public information.
- Do not give personalized investment advice or tell the user to buy or sell. You can explain what was disclosed.
- Be concise: short paragraphs or bullets, suitable for a phone screen.`;

const nullableStr = { type: ["string", "null"] } as const;

const TOOLS: Tool[] = [
  {
    name: "search_trades",
    description:
      "Search disclosed trades. Every argument is optional (pass null to skip). Returns up to `limit` trades, newest first by default.",
    strict: true,
    input_schema: {
      type: "object",
      properties: {
        politicianName: { ...nullableStr, description: "Name or last name of the member" },
        ticker: { ...nullableStr, description: "Uppercase ticker, e.g. NVDA" },
        chamber: { type: ["string", "null"], enum: ["house", "senate", null] },
        party: { type: ["string", "null"], enum: ["D", "R", "I", null] },
        state: { ...nullableStr, description: "Two-letter state code" },
        txType: { type: ["string", "null"], enum: ["buy", "sell", "exchange", null] },
        from: { ...nullableStr, description: "Earliest transaction date YYYY-MM-DD" },
        to: { ...nullableStr, description: "Latest transaction date YYYY-MM-DD" },
        minAmount: { type: ["number", "null"] },
        sort: { type: "string", enum: ["-pubDate", "-txDate", "-amount"] },
        limit: { type: "integer", enum: [5, 10, 25, 50] },
      },
      required: ["politicianName", "ticker", "chamber", "party", "state", "txType", "from", "to", "minAmount", "sort", "limit"],
      additionalProperties: false,
    },
  },
  {
    name: "find_politician",
    description: "Look up members of Congress by name. Returns profile and trading summary for the best matches.",
    strict: true,
    input_schema: {
      type: "object",
      properties: { name: { type: "string" } },
      required: ["name"],
      additionalProperties: false,
    },
  },
  {
    name: "market_overview",
    description: "Totals for the last 30 days plus the most-traded tickers and most active members over 90 days.",
    strict: true,
    input_schema: { type: "object", properties: {}, required: [], additionalProperties: false },
  },
];

const SearchInput = z.object({
  politicianName: z.string().nullable(),
  ticker: z.string().nullable(),
  chamber: z.enum(["house", "senate"]).nullable(),
  party: z.enum(["D", "R", "I"]).nullable(),
  state: z.string().nullable(),
  txType: z.enum(["buy", "sell", "exchange"]).nullable(),
  from: z.string().nullable(),
  to: z.string().nullable(),
  minAmount: z.number().nullable(),
  sort: z.enum(["-pubDate", "-txDate", "-amount"]),
  limit: z.number().int().min(1).max(50),
});

const compact = (t: Trade) => ({
  id: t.id,
  politician: `${t.politician.name} (${t.politician.party ?? "?"}-${t.politician.state}, ${t.politician.chamber})`,
  type: t.txType,
  asset: t.issuer?.ticker ? `${t.issuer.ticker} ${t.issuer.name}` : t.assetName,
  txDate: t.txDate,
  disclosed: t.pubDate.slice(0, 10),
  amount: t.amountLabel,
  owner: t.owner,
});

const opt = <T>(v: T | null) => v ?? undefined;

async function runTool(name: string, input: unknown, ent: Entitlements): Promise<unknown> {
  switch (name) {
    case "search_trades": {
      const a = SearchInput.parse(input);
      const res = await listTrades(
        {
          politicianName: opt(a.politicianName),
          ticker: opt(a.ticker),
          chamber: opt(a.chamber),
          party: opt(a.party),
          state: opt(a.state),
          txType: opt(a.txType),
          from: opt(a.from),
          to: opt(a.to),
          minAmount: opt(a.minAmount),
        },
        ent,
        { page: 1, pageSize: a.limit, sort: a.sort },
      );
      return { totalMatching: res.meta.total, trades: res.data.map(compact) };
    }
    case "find_politician": {
      const { name: q } = z.object({ name: z.string() }).parse(input);
      const matches = await listPoliticians({ search: q }, ent, { page: 1, pageSize: 3, sort: "-tradeCount" });
      return Promise.all(matches.data.map((p) => getPolitician(p.id, ent)));
    }
    case "market_overview":
      return overview(ent);
    default:
      throw new Error(`unknown tool ${name}`);
  }
}

const MAX_TOOL_ROUNDS = 6;

/** Agentic loop over our own data tools. Returns the final assistant text. */
export async function chat(history: { role: "user" | "assistant"; content: string }[], ent: Entitlements) {
  const messages: Msg[] = history.map((m) => ({ role: m.role, content: m.content }));
  messages.push({ role: "system", content: `Today is ${new Date().toISOString().slice(0, 10)}.` });

  for (let round = 0; round <= MAX_TOOL_ROUNDS; round++) {
    const msg = await ai().beta.messages.create({
      model: config.AI_MODEL,
      max_tokens: 16000,
      ...FALLBACK,
      output_config: { effort: "medium" },
      cache_control: { type: "ephemeral" },
      system: SYSTEM,
      tools: TOOLS,
      // On the last round, take tools away so the model must answer.
      tool_choice: round === MAX_TOOL_ROUNDS ? { type: "none" } : { type: "auto" },
      messages,
    });
    assertNotRefused(msg);
    if (msg.stop_reason !== "tool_use") {
      const text = msg.content
        .filter((b): b is Anthropic.Beta.BetaTextBlock => b.type === "text")
        .map((b) => b.text)
        .join("\n")
        .trim();
      return { reply: text || "I couldn't find an answer in the disclosures.", disclaimer: DISCLAIMER };
    }
    messages.push({ role: "assistant", content: msg.content });
    const results: Anthropic.Beta.BetaToolResultBlockParam[] = [];
    for (const block of msg.content) {
      if (block.type !== "tool_use") continue;
      try {
        const out = await runTool(block.name, block.input, ent);
        results.push({ type: "tool_result", tool_use_id: block.id, content: JSON.stringify(out) });
      } catch (err) {
        results.push({ type: "tool_result", tool_use_id: block.id, content: `Error: ${String(err)}`, is_error: true });
      }
    }
    messages.push({ role: "user", content: results });
  }
  return { reply: "That question needed too many lookups. Try asking something more specific.", disclaimer: DISCLAIMER };
}
