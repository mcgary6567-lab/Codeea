# HillSignal backend

Node.js 22 + TypeScript + Express + PostgreSQL. It serves the mobile app's API
(see [`../docs/API.md`](../docs/API.md)), ingests congressional trade disclosures,
runs the Claude-powered AI features, and verifies Google Play subscriptions.

## Run locally

```bash
# Postgres 14+ (or: docker run -e POSTGRES_PASSWORD=hillsignal -e POSTGRES_USER=hillsignal -p 5432:5432 postgres:16)
createdb hillsignal
cp .env.example .env          # set JWT_SECRET; add ANTHROPIC_API_KEY to turn AI on
npm install
npm run seed                  # optional: fictional sample members + trades for UI work
npm run dev                   # http://localhost:4000/health
```

Migrations run automatically on start (`npm run migrate` runs them by hand).

## Load real data

All of this comes from official public sources:

| Command | Source |
|---|---|
| `npm run ingest -- legislators` | Current members of Congress (party, state, photo) from the public-domain [@unitedstates](https://github.com/unitedstates/congress-legislators) dataset. **Run this first.** |
| `npm run ingest -- house --year 2025` | House Clerk yearly index + each Periodic Transaction Report PDF |
| `npm run ingest -- senate --days 30` | Senate eFD Periodic Transaction Reports |
| `npm run ingest -- all` | All of the above for the current year, then push alerts |
| `npm run ingest -- import trades.json` | Trades from a licensed data provider (shape: `ImportRow` in `src/ingest/cli.ts`) |

Each filing is processed once, and its trade IDs are deterministic, so re-runs
are safe. In production, run `npm run ingest -- all` every hour from a scheduler
(Render cron job, Cloud Scheduler, GitHub Actions…), or set `INGEST_INTERVAL_MINUTES=60`
to run it inside the API process.

> **Before launch, check the parsers against live filings.** The House PDF and
> Senate HTML parsers are written for the current formats and tested against
> fixtures in `test/fixtures/`, but the dev sandbox couldn't reach the government
> sites. Run `npm run ingest -- house --limit 20`, compare a few trades with the
> source PDFs, and adjust `src/ingest/house.ts` if needed. Scanned paper filings
> have no text; they are recorded with status `no_text` in the `filings` table
> for manual review.

## AI features (Claude)

| Endpoint | What it does | Plan |
|---|---|---|
| `POST /ai/search` | Plain English → structured filters (structured outputs) → database query | free 3/day, pro 50/day, premium 500/day |
| `GET /ai/trades/:id/insight`, `GET /ai/politicians/:id/insight` | Grounded summary of a trade or member's activity, cached 7 days | pro+ |
| `POST /ai/chat` | Research assistant with tools (`search_trades`, `find_politician`, `market_overview`) over your own database | premium |

- Code: `src/ai/`. Model: `AI_MODEL` (default `claude-opus-5-5`).
- Refusal fallback is on (`fallbacks: "default"`), so a declined request is retried server-side on Anthropic's recommended model. A request that is still declined returns 422 `ai_declined`.
- Prompts tell the model to stick to the disclosed data, never accuse anyone of wrongdoing, and never give buy/sell advice. Every response carries a disclaimer.
- Quotas are per user per UTC day (`ai_usage` table). A failed AI call doesn't use up quota.

## Google Play subscriptions

1. Play Console → Monetize → Subscriptions: create `hillsignal_pro` and `hillsignal_premium`, each with base plans `monthly` and `yearly`.
2. Google Cloud: create a service account, then invite it in Play Console → Users and permissions with *View financial data* and *Manage orders and subscriptions*. Put the JSON key contents in `GOOGLE_SERVICE_ACCOUNT_JSON`.
3. Real-time Developer Notifications: create a Pub/Sub topic, set it in Play Console → Monetization setup, and add a **push** subscription to
   `https://YOUR_API/api/v1/billing/google/rtdn`. Turn on authentication with a service account and set `RTDN_PUSH_AUDIENCE` to the audience you configured. Alternatively, append `?secret=...` and set `RTDN_SHARED_SECRET`.
4. The app sends `obfuscatedAccountIdAndroid = user.id` with each purchase, so a purchase token can't be claimed by a different account.

A user's plan is computed on every request: the best of any live subscription
(active, grace period, or canceled but not yet expired) and an optional manual
grant (`users.granted_plan` / `granted_until`, useful for promo codes and testers).

## Test

```bash
createdb hillsignal_test     # once
npm test                     # 28 tests: parsers, API, billing, plan gating, AI loop against a fake Claude server
npm run typecheck
```

## Deploy

`Dockerfile` builds a small production image. Any host with Node 22 and Postgres
works: Render, Railway, Fly.io, Google Cloud Run + Cloud SQL, AWS. Required in
production: `NODE_ENV=production`, `DATABASE_URL`, `JWT_SECRET` (32+ random chars).
Put it behind HTTPS; the app only talks to HTTPS in release builds.
