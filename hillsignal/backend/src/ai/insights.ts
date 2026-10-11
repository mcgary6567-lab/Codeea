import { z } from "zod";
import { betaZodOutputFormat } from "@anthropic-ai/sdk/helpers/beta/zod";
import { config } from "../config.js";
import { getPolitician } from "../data/entities.js";
import { listTrades, type Trade } from "../data/trades.js";
import { entitlementsFor } from "../plans.js";
import { ai, assertNotRefused, cached, DISCLAIMER, FALLBACK } from "./client.js";

const InsightSchema = z.object({
  headline: z.string().describe("Under 90 characters"),
  summary: z.string().describe("2-4 sentences"),
  keyPoints: z.array(z.string()).describe("3-5 short bullet points grounded in the data"),
  caveats: z.array(z.string()).describe("1-3 limitations of what the data can show"),
});

export type Insight = z.infer<typeof InsightSchema> & { disclaimer: string; generatedAt: string };

const SYSTEM = `You are a careful financial-disclosure analyst for a consumer app about U.S. congressional stock trades.
You explain trades and trading patterns in plain English for retail readers.

Ground every statement in the JSON data provided. Do not invent prices, returns, committee seats, news events or
motives that are not in the data. You may note factual patterns (timing, concentration, reporting delay, size
range, buy/sell balance, spouse vs. self ownership). Never state or imply that a trade was illegal, unethical, or
based on non-public information; describe what was disclosed and let readers judge. Never tell the reader to buy
or sell anything. Amounts are disclosure ranges, not exact values. Reporting gaps over 45 days are late under the
STOCK Act; say so factually when relevant.`;

// The full-history view is fine for analysis; visibility is enforced by the route.
const FULL = entitlementsFor("premium");

const brief = (t: Trade) => ({
  txDate: t.txDate,
  disclosed: t.pubDate.slice(0, 10),
  reportingGapDays: t.reportingGapDays,
  type: t.txType,
  asset: t.issuer?.ticker ?? t.assetName,
  assetType: t.assetType,
  owner: t.owner,
  amount: t.amountLabel,
});

async function generate(context: unknown, instruction: string): Promise<Insight> {
  const msg = await ai().beta.messages.parse({
    model: config.AI_MODEL,
    max_tokens: 16000,
    ...FALLBACK,
    output_config: { effort: "medium", format: betaZodOutputFormat(InsightSchema) },
    system: SYSTEM,
    messages: [{ role: "user", content: `${instruction}\n\n<data>\n${JSON.stringify(context)}\n</data>` }],
  });
  assertNotRefused(msg);
  if (!msg.parsed_output) throw new Error("AI insight returned no parseable output");
  return { ...msg.parsed_output, disclaimer: DISCLAIMER, generatedAt: new Date().toISOString() };
}

export async function tradeInsight(trade: Trade): Promise<Insight> {
  return cached(`trade:${trade.id}:v1`, 24 * 7, async () => {
    const [byPolitician, byIssuer] = await Promise.all([
      listTrades({ politicianId: trade.politician.id }, FULL, { page: 1, pageSize: 30, sort: "-txDate" }),
      trade.issuer
        ? listTrades({ issuerId: trade.issuer.id }, FULL, { page: 1, pageSize: 30, sort: "-txDate" })
        : Promise.resolve(null),
    ]);
    const context = {
      trade: { ...brief(trade), assetName: trade.assetName, sector: trade.issuer?.sector ?? null },
      politician: trade.politician,
      politicianRecentTrades: byPolitician.data.filter((t) => t.id !== trade.id).map(brief),
      sameAssetByOthers: (byIssuer?.data ?? [])
        .filter((t) => t.politician.id !== trade.politician.id)
        .map((t) => ({ politician: t.politician.name, party: t.politician.party, ...brief(t) })),
    };
    return generate(context, "Explain this disclosed trade and how it fits the member's recent activity and other members' trades in the same asset.");
  });
}

export async function politicianInsight(politicianId: string): Promise<Insight | null> {
  const profile = await getPolitician(politicianId, FULL);
  if (!profile) return null;
  const trades = await listTrades({ politicianId }, FULL, { page: 1, pageSize: 100, sort: "-txDate" });
  const newest = trades.data[0]?.id ?? "none";
  return cached(`politician:${politicianId}:${newest}:v1`, 24 * 7, () =>
    generate(
      {
        politician: {
          name: profile.name,
          party: profile.party,
          chamber: profile.chamber,
          state: profile.state,
          tradeCount: profile.tradeCount,
          buyCount: profile.buyCount,
          sellCount: profile.sellCount,
          topIssuers: profile.topIssuers,
        },
        recentTrades: trades.data.map(brief),
      },
      "Summarize this member's disclosed trading activity: what they trade most, buy/sell balance, timing and reporting patterns.",
    ),
  );
}
