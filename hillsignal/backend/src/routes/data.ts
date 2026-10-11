import { Router } from "express";
import { z } from "zod";
import { requirePlan } from "../auth.js";
import { getIssuer, getPolitician, listIssuers, listPoliticians, overview } from "../data/entities.js";
import { getTrade, listTrades, type Trade } from "../data/trades.js";
import { handler, notFound, pageQuery, parse } from "../http.js";
import { PLAN_CATALOG, GOOGLE_PRODUCTS, entitlementsFor } from "../plans.js";

export const dataRouter = Router();

const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);

export const TradeQuery = z.object({
  ...pageQuery,
  politicianId: z.string().optional(),
  issuerId: z.string().regex(/^\d+$/).optional(),
  chamber: z.enum(["house", "senate"]).optional(),
  party: z.enum(["D", "R", "I"]).optional(),
  state: z.string().length(2).optional(),
  ticker: z.string().max(10).optional(),
  txType: z.enum(["buy", "sell", "exchange"]).optional(),
  from: isoDate.optional(),
  to: isoDate.optional(),
  minAmount: z.coerce.number().nonnegative().optional(),
  sort: z.enum(["-pubDate", "-txDate", "-amount"]).default("-pubDate"),
});

dataRouter.get(
  "/trades",
  handler(async (req, res) => {
    const { page, pageSize, sort, ...filters } = parse(TradeQuery, req.query);
    res.json(await listTrades(filters, req.auth.entitlements, { page, pageSize, sort }));
  }),
);

const csvCell = (v: unknown) => {
  const s = v === null || v === undefined ? "" : String(v);
  // Quote, and neutralize spreadsheet formula injection.
  const safe = /^[=+\-@\t\r]/.test(s) ? `'${s}` : s;
  return `"${safe.replace(/"/g, '""')}"`;
};

dataRouter.get(
  "/trades/export.csv",
  handler(async (req, res) => {
    requirePlan(req, "premium", "CSV export");
    const { sort, page: _p, pageSize: _s, ...filters } = parse(TradeQuery, req.query);
    const header = ["tx_date", "disclosed", "politician", "party", "chamber", "state", "type", "ticker", "asset", "owner", "amount_min", "amount_max", "amount", "source_url"];
    const lines = [header.join(",")];
    for (let page = 1; page <= 50; page++) {
      const { data } = await listTrades(filters, req.auth.entitlements, { page, pageSize: 100, sort });
      for (const t of data as Trade[]) {
        lines.push(
          [t.txDate, t.pubDate.slice(0, 10), t.politician.name, t.politician.party, t.politician.chamber, t.politician.state,
           t.txType, t.issuer?.ticker, t.assetName, t.owner, t.amountMin, t.amountMax, t.amountLabel, t.sourceUrl]
            .map(csvCell)
            .join(","),
        );
      }
      if (data.length < 100) break;
    }
    res.setHeader("Content-Type", "text/csv; charset=utf-8");
    res.setHeader("Content-Disposition", 'attachment; filename="hillsignal-trades.csv"');
    res.send(lines.join("\n"));
  }),
);

dataRouter.get(
  "/trades/:id",
  handler(async (req, res) => {
    const trade = await getTrade(String(req.params.id), req.auth.entitlements);
    if (!trade) throw notFound("Trade");
    res.json(trade);
  }),
);

dataRouter.get(
  "/politicians",
  handler(async (req, res) => {
    const q = parse(
      z.object({
        ...pageQuery,
        search: z.string().max(80).optional(),
        chamber: z.enum(["house", "senate"]).optional(),
        party: z.enum(["D", "R", "I"]).optional(),
        state: z.string().length(2).optional(),
        sort: z.enum(["-lastTrade", "-tradeCount", "name"]).default("-lastTrade"),
      }),
      req.query,
    );
    const { page, pageSize, sort, ...filters } = q;
    res.json(await listPoliticians(filters, req.auth.entitlements, { page, pageSize, sort }));
  }),
);

dataRouter.get(
  "/politicians/:id",
  handler(async (req, res) => {
    const p = await getPolitician(String(req.params.id), req.auth.entitlements);
    if (!p) throw notFound("Politician");
    res.json(p);
  }),
);

dataRouter.get(
  "/issuers",
  handler(async (req, res) => {
    const { page, pageSize, search } = parse(z.object({ ...pageQuery, search: z.string().max(80).optional() }), req.query);
    res.json(await listIssuers({ search }, req.auth.entitlements, { page, pageSize }));
  }),
);

dataRouter.get(
  "/issuers/:id",
  handler(async (req, res) => {
    const i = await getIssuer(String(req.params.id), req.auth.entitlements);
    if (!i) throw notFound("Issuer");
    res.json(i);
  }),
);

dataRouter.get(
  "/stats/overview",
  handler(async (req, res) => {
    res.json(await overview(req.auth.entitlements));
  }),
);

dataRouter.get("/plans", (_req, res) => {
  res.json({
    plans: PLAN_CATALOG.map((p) => ({
      ...p,
      entitlements: entitlementsFor(p.id),
      googleProductIds: Object.entries(GOOGLE_PRODUCTS)
        .filter(([, plan]) => plan === p.id)
        .map(([id]) => id),
    })),
  });
});
