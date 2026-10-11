# HillSignal API contract (v1)

Base URL: `{API_URL}/api/v1`. All bodies are JSON. Authenticated routes need
`Authorization: Bearer <token>`. Errors always look like
`{ "error": { "code": "string", "message": "string" } }`.

Error codes the app must handle:

| HTTP | code                | Meaning                                              |
|------|---------------------|------------------------------------------------------|
| 400  | `invalid_request`   | Validation failed                                    |
| 401  | `unauthorized`      | Missing/invalid token                                |
| 402  | `upgrade_required`  | Feature needs a higher plan. Body also has `requiredPlan` |
| 404  | `not_found`         |                                                      |
| 409  | `conflict`          | e.g. email already registered                        |
| 422  | `ai_declined`       | The AI declined the request; ask the user to rephrase |
| 429  | `quota_exceeded`    | Daily AI quota used up                               |
| 429  | `rate_limited`      | Too many requests in a short time                    |
| 503  | `ai_unavailable`    | AI not configured on server, or temporarily failing  |
| 503  | `billing_unavailable` | Google Play verification not configured on server  |

## Types

```ts
type Plan = "free" | "pro" | "premium";
type Chamber = "house" | "senate";
type Party = "D" | "R" | "I";
type TxType = "buy" | "sell" | "exchange";

interface PoliticianRef { id: string; name: string; party: Party | null; // null = not yet matched to a member record
  chamber: Chamber; state: string; photoUrl: string | null }
interface IssuerRef { id: string; name: string; ticker: string | null; sector: string | null }

interface Trade {
  id: string;
  politician: PoliticianRef;
  issuer: IssuerRef | null;
  assetName: string;            // as written in the filing
  assetType: string | null;     // "stock", "option", "etf", "bond", ...
  txType: TxType;
  txDate: string;               // YYYY-MM-DD
  pubDate: string;              // ISO timestamp the disclosure was published
  reportingGapDays: number;     // pubDate - txDate in days
  owner: "self" | "spouse" | "joint" | "dependent" | null;
  amountMin: number | null;     // USD, disclosure range lower bound
  amountMax: number | null;     // USD, upper bound (null = open-ended "over $X")
  amountLabel: string;          // "$1,001 - $15,000"
  sourceUrl: string | null;     // link to the official filing
}

interface Politician extends PoliticianRef {
  district: string | null;
  tradeCount: number;
  lastTradeDate: string | null;
  volumeMin: number;             // sum of amountMin
}

interface Issuer extends IssuerRef { tradeCount: number; politicianCount: number; lastTradeDate: string | null }

interface Paged<T> { data: T[]; meta: { page: number; pageSize: number; total: number; delayDays: number } }

interface Entitlements {
  plan: Plan;
  delayDays: number;            // trades published in the last N days are hidden (free plan)
  historyDays: number | null;   // null = full history
  watchlistLimit: number;
  aiSearchPerDay: number;       // 0 = not available
  aiInsights: boolean;
  aiChat: boolean;
  alerts: boolean;
  csvExport: boolean;
}

interface User { id: string; email: string; name: string | null; plan: Plan; planExpiresAt: string | null; createdAt: string }
```

## Auth

| Method | Path             | Body                                   | Response |
|--------|------------------|----------------------------------------|----------|
| POST   | `/auth/register` | `{ email, password (min 8), name? }`   | `{ token, user, entitlements }` |
| POST   | `/auth/login`    | `{ email, password }`                  | `{ token, user, entitlements }` |
| GET    | `/me`            | (auth)                                 | `{ user, entitlements }` |
| DELETE | `/me`            | (auth) — deletes account (Play Store requirement) | `204` |

Browsing routes (`/trades`, `/politicians`, `/issuers`, `/stats`) work without a token
and use **free** entitlements; with a token they use the user's plan.

## Market data

| Method | Path | Query | Response |
|---|---|---|---|
| GET | `/trades` | `page`, `pageSize` (≤100), `politicianId`, `issuerId`, `chamber`, `party`, `state`, `ticker`, `txType`, `from`, `to` (txDate, YYYY-MM-DD), `minAmount`, `sort` = `-pubDate` (default) \| `-txDate` \| `-amount` | `Paged<Trade>` |
| GET | `/trades/:id` | | `Trade` |
| GET | `/trades/export.csv` | same as `/trades` (premium only, up to 5,000 rows) | `text/csv` |
| GET | `/politicians` | `page`, `pageSize`, `search`, `chamber`, `party`, `state`, `sort` = `-lastTrade` (default) \| `-tradeCount` \| `name` | `Paged<Politician>` |
| GET | `/politicians/:id` | | `Politician & { topIssuers: (IssuerRef & { tradeCount: number })[]; buyCount: number; sellCount: number }` |
| GET | `/issuers` | `page`, `pageSize`, `search` | `Paged<Issuer>` |
| GET | `/issuers/:id` | | `Issuer` |
| GET | `/stats/overview` | | `{ totalTrades, totalPoliticians, totalIssuers, last30Days: { trades, buys, sells, volumeMin }, topTickers: { ticker, name, tradeCount }[], mostActive: (PoliticianRef & { tradeCount })[] }` |

## Watchlist & alerts (auth)

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/watchlist` | | `{ politicians: Politician[], issuers: Issuer[] }` |
| POST | `/watchlist` | `{ kind: "politician" \| "issuer", targetId }` | `201 { ok: true }` (402 when over `watchlistLimit`) |
| DELETE | `/watchlist/:kind/:targetId` | | `204` |
| GET | `/feed` | `page`, `pageSize` | `Paged<Trade>` trades for watched politicians/issuers |
| POST | `/devices` | `{ expoPushToken, platform: "android" \| "ios" }` | `201 { ok: true }` |

Push alerts (pro+) are sent when a new trade from a watched politician/issuer
is ingested. Push data payload: `{ type: "trade", tradeId }`.

## AI (auth)

| Method | Path | Body | Plan | Response |
|---|---|---|---|---|
| POST | `/ai/search` | `{ query: string }` e.g. "tech stocks senators sold last month" | free (3/day), pro, premium | `{ filters: TradeFilters, explanation: string, results: Paged<Trade>, remaining: number, disclaimer: string }` |
| GET | `/ai/trades/:id/insight` | | pro+ | `{ insight: Insight }` |
| GET | `/ai/politicians/:id/insight` | | pro+ | `{ insight: Insight }` |
| POST | `/ai/chat` | `{ messages: { role: "user" \| "assistant", content: string }[] }` (last must be user, ≤ 20) | premium | `{ reply: string, disclaimer: string }` |

```ts
interface TradeFilters { politicianName?: string; chamber?: Chamber; party?: Party; state?: string; ticker?: string; sector?: string; txType?: TxType; from?: string; to?: string; minAmount?: number }
interface Insight { headline: string; summary: string; keyPoints: string[]; caveats: string[]; disclaimer: string; generatedAt: string }
```

## Membership / billing

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/plans` | | `{ plans: { id: Plan; name: string; tagline: string; features: string[]; entitlements: Entitlements; googleProductIds: string[] }[] }` |
| POST | `/billing/google/verify` | (auth) `{ productId, purchaseToken }` | `{ user, entitlements }` |
| POST | `/billing/google/rtdn` | Google Pub/Sub push (server-to-server) | `204` |

The app gets localized prices from Google Play itself (react-native-iap). After a
successful purchase, the app sends the token to `/billing/google/verify`, then
acknowledges/finishes the transaction.
