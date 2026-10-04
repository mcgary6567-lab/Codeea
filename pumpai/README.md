# ⛽ PumpAI — AI WhatsApp CRM & forecourt management for Pakistani petrol pumps

A full-stack web app for petrol-pump owners in Pakistan. It covers:

- **WhatsApp CRM:** an AI agent answers customers 24/7 in Roman Urdu, Urdu or English.
- **Forecourt operations:** POS, shifts, tanks, dips, tanker deliveries and prices.
- **Khata (credit) management.**
- **Background automations:** loss/fraud detection, stock-out prediction, payment reminders, win-back campaigns and the owner's daily WhatsApp brief.

It runs fully offline out of the box, using demo data, a built-in rule-based Roman Urdu bot and simulated WhatsApp. Add a Claude API key and WhatsApp Cloud API credentials to switch to the AI agent and live messaging.

## Quick start

```bash
cd pumpai
npm install
npm run dev          # API on :4000, dashboard on http://localhost:5173
```

- **Sign in** (password `demo1234` for all four):

  | Login | Role | Access |
  |---|---|---|
  | `admin@pumpai.pk` | Admin (CEO) | Everything, including Users & Roles, Settings and khata credit limits |
  | `manager@pumpai.pk` | Manager | Dashboard, WhatsApp inbox, customers & khata, orders, complaints, campaigns, stock, prices, alerts, automations |
  | `salesman@pumpai.pk` | Salesman | POS, their own shift, customer lookup/add and prices, at their assigned station only |
  | `wholesale@pumpai.pk` | Wholesale Officer | Only the wholesale module: clients, supplies, returns, payments, dues and statements |
- **First run:** a demo business is created automatically: 2 stations, 5 tanks, about 8 weeks of sales, 32 customers, khata and WhatsApp chats.
- **Reset the demo data:** `npm run seed`.
- **Production:** `npm run build && npm start`. The API serves the built dashboard on :4000. A `Dockerfile` is included.
- **Tests:** `npm test` runs 45 end-to-end API tests covering the WhatsApp agent, shifts, khata, stock, prices, orders, campaigns, automations, the Meta webhook, role-based access, wholesale supply, expenses, suppliers, reports (including exact stock reconciliation across periods), and mid-shift price changes with meter settlement.

Requires Node.js 22+ (uses the built-in `node:sqlite`, so there is no database server to install).

## Features

| Area | What it does |
|---|---|
| **WhatsApp AI agent** | Claude with tools: live prices, khata balance and statement, payment links (JazzCash/Easypaisa/Raast), bulk diesel/petrol order booking with a credit check, complaints with tickets, station finder, loyalty points, opt-out (STOP), and hand-off to a human. It mirrors the customer's language. |
| **Inbox** | WhatsApp-style shared inbox with live updates (SSE). Take over or hand back any chat. An AI "draft reply" copilot. A simulator that runs the real webhook pipeline. |
| **CRM** | Customers, vehicles and fleets, AI segments (VIP, Regular, At risk, New, Fleet, Agri), churn probability, credit-risk score, loyalty points (1 per Rs 100). |
| **Khata** | Credit limits enforced at sale time, ledger, payment receipts on WhatsApp, automatic reminders with payment links. |
| **Orders** | Orders booked by the AI over WhatsApp. Confirm, dispatch and deliver steps each notify the customer. Delivery posts the sale and debits khata. |
| **Add anything, from one place** | An **"+ Add new"** button in the sidebar (and tiles on the dashboard) lets the admin add a khata account (police, school, govt office, hospital, fleet, farmer, business) with its vehicles and credit limit, a customer, supplier, wholesale client, station, tank with nozzles, staff user or expense category; managers get the items their role allows. New items appear immediately for everyone who uses them, e.g. a new khata account shows up in the salesman's POS marked **NEW**, and the right people are notified in-app: salesmen for new/closed khata accounts, new vehicles and new tanks at their station; managers for new customers, suppliers and stations; wholesale officers for new wholesale clients. Stations and tanks are also managed under Settings. |
| **Salesman POS** | A big-button, colour-coded, English + Urdu screen built for tablets and phones, usable with little reading: tap the fuel (Petrol / Hi-Octane / Diesel with today's rate), enter rupees or litres on a large keypad or quick buttons, tap the payment (Cash, Easypaisa, JazzCash, Card, Raast or Khata), then Save. A full-screen green tick confirms each sale. The side panel shows cash in hand that should be there, total sales and the last sales. It asks to start the shift first and pauses after a price change until confirmed. |
| **Institutional khata** | Credit accounts for police stations, schools, government offices and hospitals (alongside fleets, farmers and businesses). On the POS the salesman picks the account from big picture cards, taps the vehicle (e.g. police mobile) and types the slip / parchi number. Every entry keeps the litres, **the rate on that day**, amount, vehicle, slip and station, so the monthly **bill/statement** (date range, print, Excel) shows exactly what was given and at what price, with running balance and totals per fuel. Salesmen never see account balances, only "near limit" / "limit full". |
| **POS & shifts** | Sale entry by rupees or litres, all payment methods. 12-hour shifts open with the meter (totalizer) readings automatically. At shift end the salesman enters each nozzle's closing reading and the cash in hand: litres not entered on the POS are booked as cash sales, tank stock is reduced, expected vs counted cash is reconciled, a shift receipt is shown and managers get the shift report (WhatsApp too when cash is short). A reminder goes to the salesman after 12 hours and an overdue alert to managers after 13. |
| **Price-change notifications** | When an admin or manager enters new prices, every salesman is notified at once (in the app 🔔 and on WhatsApp if their number is saved) with the change per litre, e.g. "Petrol +Rs 2.50". Their POS is paused until they confirm the dispenser was updated. A salesman on shift also enters the current meter readings, so litres pumped before the change are billed at the old rate and after it at the new rate. The Prices page shows who has confirmed and when. |
| **Wet stock** | Dip vs book variance, tanker short-delivery detection, tank levels with reorder lines. |
| **Prices** | Price updates (1st/16th of the month), stock revaluation gain/loss, one-click WhatsApp price broadcast. |
| **Wholesale supply** | Bulk fuel to dealers, fleets and farms. Each client has their own per-litre rate for each product (set by the admin, with rate history), a credit limit and an opening balance. Every movement is a ledger entry: supply (fuel out, stock down, due up), return (fuel back into the tank, due down), payment, and admin adjustments. Shows litres out / in per product, billed, received and current due; a statement with running balance and date filter; CSV/Excel export and print. Wrong entries are voided (stock reversed, kept in the ledger as VOID). Credit-limit check on every supply, with an alert at 90%. Separate Wholesale Officer role. |
| **Expenses** | Expense categories with optional monthly budgets, entries by station and payment method, and an approval flow: manager entries above the approval limit (default Rs 10,000) wait for admin approval. Monthly summary by category vs 3-month average and budget, revenue (retail + wholesale) minus expenses, CSV export. Over-budget and unusual-spike alerts appear on the dashboard and in Ask AI. |
| **Reports** | For Admin and Manager, any time: presets for 24 hours, 7 days, 1 month, 6 months and 1 year, or a custom date/time range (Pakistan time). Tabs: **Overview** (revenue retail + wholesale, expenses, estimated gross and net profit, money in / money out, chart by hour/day/month), **Sales** (by product, station, payment method, top customers), **Stock** (opening + received + returns − retail − wholesale ± dip adjustment = closing per product, rebuilt from the movement ledger so any past period is exact; tank levels, deliveries, dips, purchases, stock value at cost), **Expenses**, **Receivables** (who owes us: khata customers and wholesale clients, with aging), **Payables** (whom we owe: fuel suppliers, customer advances, expenses awaiting approval), **Khata**, **Wholesale**, **Shifts & cash** (expected vs counted per attendant). Every tab exports to Excel/CSV and prints to PDF. "Ask AI" can answer report questions for any period. |
| **Suppliers** | Fuel supplier (depot) accounts. Choosing a supplier and purchase rate on a tanker delivery records what we owe; payments to the depot reduce it. Purchase rates give the fuel cost used for profit estimates. |
| **Campaigns** | AI-written Roman Urdu broadcasts to segments, with `{name}`, `{balance}` and `{points}` placeholders. Opt-in only. |
| **AI analytics** | Holt-Winters demand forecast with weekly seasonality, days-to-empty per tank with a suggested order, anomaly detection, insight cards, and "Ask your business anything" (Claude with read-only analytics tools). |
| **Automations** | Six scheduled jobs on Asia/Karachi time, each of which can be toggled or run on demand (see below). |
| **Users & roles** | Four roles: Admin (CEO), Manager, Salesman and Wholesale Officer. Access is enforced on every API route and mirrored in the UI from one permission table (`server/src/auth.ts`). The admin creates users, assigns roles and stations, resets passwords, and can disable or delete accounts; the last active admin is protected. Salesmen are locked to their own station and shift, and must open a shift before selling. Databases with the old owner/accountant/attendant roles are upgraded automatically. |

### Automations

| Job | Schedule | Action |
|---|---|---|
| AI customer scoring | daily 02:00 | Segments, churn score and credit-risk score |
| Loss & fraud detection | every 30 min | Dip variance, short deliveries, cash shortages (z-score per attendant), sales drops. Sends critical alerts to the owner on WhatsApp. |
| Tank stock-out prediction | hourly | Alerts and a suggested tanker order before the reorder level is reached |
| Khata reminders | daily 11:00 | Balance plus payment link, at most once per 3 days per customer |
| AI win-back campaign | Mondays 12:00 | AI-written offer to customers at risk of churning (opted-in only) |
| Owner's daily brief | daily 21:00 | Sales, cash/digital/khata, stock, alerts and forecast, sent on WhatsApp |

## Architecture

```
WhatsApp users ─► Meta Cloud API ─► /webhooks/whatsapp ─┐
Dashboard (React + Vite + Tailwind) ─► /api (Express) ──┼─► services (sales, khata, stock, alerts)
                                                         ├─► AI agent (Claude tool loop | rule-engine fallback)
                                                         ├─► analytics (forecast, anomalies, scoring)
                                                         ├─► automations (node-cron, Asia/Karachi)
                                                         └─► SQLite (node:sqlite, WAL)
```

```
pumpai/
├── server/src
│   ├── index.ts              Express app, auth, static hosting
│   ├── db.ts / seed.ts       schema + demo data
│   ├── services.ts           sales, khata, prices, alerts (business rules)
│   ├── ai/agent.ts           Claude agents: WhatsApp, Ask-AI, campaign writer
│   ├── ai/tools.ts           customer-scoped agent tools
│   ├── ai/businessTools.ts   read-only analytics tools for the owner
│   ├── ai/fallback.ts        offline Roman Urdu intent engine
│   ├── ai/analytics.ts       forecasting, anomaly detection, scoring, KPIs
│   ├── automation/           scheduled jobs
│   ├── whatsapp/cloud.ts     Cloud API send (24h window → template), webhook parse, signature check
│   └── routes/               operations, crm, whatsapp, insights, users, wholesale, expenses, suppliers, reports
└── web/src/pages             Dashboard, Inbox, Customers, Khata, Orders, Complaints, Campaigns,
                              POS, Shifts, Stock, Prices, Wholesale, Expenses, Suppliers, Reports, Alerts, Automations, Users, Settings
```

## Going live

1. **Claude.** Set `ANTHROPIC_API_KEY`. The default model is `claude-opus-5-5` at `AI_EFFORT=low`, which suits chat. Server-side refusal fallbacks are enabled. If the API fails, the bot falls back to the rule engine so it never goes silent.
2. **WhatsApp Cloud API.** In Meta Business Manager, create an app and add WhatsApp. Then:
   - Set `WA_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_APP_SECRET` and `WA_VERIFY_TOKEN`.
   - Point the webhook at `https://<your-domain>/webhooks/whatsapp` and subscribe to `messages`.
   - Get a **utility template** approved, named `general_update`, with a body of `{{1}}`. It is used automatically for messages sent outside the 24-hour customer-service window.
3. **Payments.** Set `PAYMENT_LINK_BASE` to your JazzCash/Easypaisa/Raast payment-link endpoint. Mark payments as received from the customer page; this sends the customer an automatic WhatsApp receipt.
4. **Owner number.** In Settings, enter the owner's WhatsApp number for alerts and the daily brief.
5. **Prices.** The seeded prices are demo values. Enter the current government-notified prices on the Prices page.

See `.env.example` for every option.

## Roadmap ideas

- Voice-note transcription (Urdu ASR) and meter-photo OCR for attendants
- IoT tank probes and forecourt-controller integration for automatic nozzle transactions
- Number-plate recognition for fleet and loyalty
- FBR invoice integration
- PostgreSQL and multi-tenant SaaS billing
