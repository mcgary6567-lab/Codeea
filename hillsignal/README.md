# HillSignal

A mobile app for following U.S. congressional stock trades, with AI analysis and
paid memberships. It is an original product in the same category as
capitoltrades.com, with its own name, design and data pipeline. It doesn't copy
their code, design or data.

```
hillsignal/
├── backend/   Node.js 22 + TypeScript + Express + PostgreSQL + Claude API + Google Play Billing
├── mobile/    React Native (Expo) app for Android (iOS-ready) with Google Play subscriptions
└── docs/API.md   API contract shared by both
```

## How it works

```
 House Clerk PTR PDFs ─┐
 Senate eFD reports ───┼─► ingest (cron) ─► PostgreSQL ─► REST API ─► Mobile app
 @unitedstates members ┘                        │             │
                                                │             ├─► Claude (AI search, insights, chat)
                                                │             └─► Google Play (verify subscriptions)
                                                └─► Expo push alerts for followed members/stocks
```

## Plans (edit in `backend/src/plans.ts`)

| | Free | Pro | Premium |
|---|---|---|---|
| Trade data | delayed 3 days, last 12 months | real-time, full history | real-time, full history |
| Follows | 3 | 50 | 500 |
| AI search | 3/day | 50/day | 500/day |
| AI insights on trades & members | – | ✓ | ✓ |
| Push alerts | – | ✓ | ✓ |
| "Ask HillSignal" AI chat | – | – | ✓ |
| CSV export | – | – | ✓ |

You set prices in Play Console; the app shows Google's localized price. A common
starting point is Pro at $4.99/mo and Premium at $12.99/mo, with a yearly discount.

## Getting started

1. Backend: follow [`backend/README.md`](backend/README.md) (Postgres, `.env`, `npm run dev`).
2. Mobile: follow [`mobile/README.md`](mobile/README.md) (`npx expo start`, dev build for billing).
3. Load data with `npm run ingest -- legislators`, then `npm run ingest -- all`.

## Before you publish — read this

**1. Commercial use of congressional disclosure data.** Under the Ethics in Government
Act (5 U.S.C. § 13107(c)), it is unlawful to obtain or use these financial disclosure
reports "for any commercial purpose, other than by news and communications media for
dissemination to the general public." The Senate eFD site makes you accept this before
searching. Apps in this space generally operate as news/data publishers, but a paid
subscription app is a gray area. **Talk to a lawyer before you charge money.** The
lower-risk path is to license the data from a commercial provider and load it with
`npm run ingest -- import`.

**2. Don't scrape capitoltrades.com.** Their compiled dataset, design and API belong to
2iQ Research. Use the official government sources (already built in) or a licensed feed.

**3. Google Play requirements:**
- Digital subscriptions must use Google Play Billing (implemented).
- Users must be able to delete their account in the app (implemented) *and* from a web link listed in Play Console.
- You need a privacy policy URL and a completed Data safety form (email, purchase history, push token, AI prompts).
- Use the Financial features declaration and keep the "not investment advice" disclaimers (shown on every AI response).
- Content must stay factual: the AI prompts forbid accusations and buy/sell advice. Keep that if you edit them.

**4. Sample data.** `npm run seed` creates *fictional* members, marked "(Sample)", for
development only. Never ship fake trades attached to real people.

## What's verified and what isn't

- ✅ Backend: typecheck clean, 28 automated tests pass. They cover auth, plan gating and delays, watchlist limits, Play purchase verification and RTDN downgrades, account deletion, CSV export, the PTR parsers (fixtures), and the AI tool loop against a fake Claude server.
- ⚠️ Not run against the live services: this was built in a sandbox without internet access to House/Senate sites, Google Play or the Claude API. Run the parsers on real filings and do a Play internal-testing purchase before launch.
