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

- **Sign in** (password `demo1234` for all four). After one email sign-in on a tablet, staff can sign in by tapping their name and a 4-digit PIN (demo PINs: admin 1111, manager 2222, salesman 3333, wholesale 4444):

  | Login | Role | Access |
  |---|---|---|
  | `admin@pumpai.pk` | Admin (CEO) | Everything, including Users & Roles, Settings and khata credit limits |
  | `manager@pumpai.pk` | Manager | Dashboard, WhatsApp inbox, customers & khata, orders, complaints, campaigns, stock, prices, alerts, automations |
  | `salesman@pumpai.pk` | Salesman | POS, their own shift, customer lookup/add and prices, at their assigned station only |
  | `wholesale@pumpai.pk` | Wholesale Officer | Only the wholesale module: clients, supplies, returns, payments, dues and statements |
- **First run:** a demo business is created automatically: 2 stations, 5 tanks, about 8 weeks of sales, 32 customers, khata and WhatsApp chats.
- **Reset the demo data:** `npm run seed`.
- **Production:** `npm run build && npm start`. The API serves the built dashboard on :4000. A `Dockerfile` is included.
- **Tests:** `npm test` runs 112 end-to-end API tests covering the WhatsApp agent, shifts, khata, stock, prices, orders, campaigns, automations, the Meta webhook, role-based access, wholesale supply (including per-client margins that follow pump price changes), expenses, suppliers, reports (including exact stock reconciliation across periods), mid-shift price changes with meter settlement, shift handover (readings, gaps, cash expenses, shift report), and a full-circle test that follows one day from the salesman's sales, expenses and meter close through a tanker delivery and a wholesale supply to the manager's notification and the admin's today book, checking every figure against the tanks and the Reports page. Further suites cover PIN sign-in and lockout, sale undo, offline sales synced once at the old price, meter/invoice/bill photos, voice-to-sale parsing (English, Roman Urdu, Urdu), khata receipts, monthly bill links, wholesale messages, QR cards, staff accounts, the cash book, tanker orders, dip charts, the midnight day close, owner questions on WhatsApp, the shop POS, vehicle limits, loyalty payments, licences, checklists, attendance and payroll cuts, owner insights (health score, staff risk, P&L, statement reconciliation, price planner, tank gain/loss), customer portal, khata hold and late charge, government bills, bookings, backups and restore, two-person price approval and push subscriptions, the stock register, coupons, wallets, ratings, the TV rate board, WhatsApp approvals, commission, monthly fixed costs and utility bills, tanker shortage claims, depot comparison, sales tax and withholding, bank statement reconciliation and the general ledger export.

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
| **Shift handover & settlement** | Each shift starts from a handover sheet that shows, for every nozzle, the last closing reading and who handed it over. The salesman confirms or corrects the opening reading; a higher reading (fuel pumped between shifts) is booked as a handover gap: tank stock is reduced and admins/managers get a critical alert. A nozzle can be on only one open shift at a time and a reading below the totalizer is refused. During the shift the salesman can record cash expenses (tea, generator diesel, repairs…); above the approval limit they wait for a manager. At closing the system works out per salesman: litres sold per nozzle and product, litres × rate (split by rate if the price changed), cash, Easypaisa/JazzCash/card, khata per customer with slip numbers, expenses, and **cash to hand over = cash sales − expenses**, then compares it with the cash counted (short/over). The full shift report is available to the salesman and managers from the Shifts page. |
| **Price-change notifications** | When an admin or manager enters new prices, every salesman is notified at once (in the app 🔔 and on WhatsApp if their number is saved) with the change per litre, e.g. "Petrol +Rs 2.50". Their POS is paused until they confirm the dispenser was updated. A salesman on shift also enters the current meter readings, so litres pumped before the change are billed at the old rate and after it at the new rate. The Prices page shows who has confirmed and when. |
| **Wet stock** | Dip vs book variance, tanker short-delivery detection, tank levels with reorder lines. |
| **Prices** | Price updates (1st/16th of the month), stock revaluation gain/loss, one-click WhatsApp price broadcast. |
| **Wholesale supply** | Bulk fuel to dealers, fleets and farms. Each client has their own rate for each product, set by the admin either as **pump price − Rs X** (e.g. one dealer pump − Rs 2, another pump − Rs 4; the rate follows every pump price change automatically) or as a **fixed rate**. The rate card shows the pump price and our margin per litre over the last purchase rate. On a pump price change the wholesale officer and admin are notified of every client's new rate and of fixed-rate clients to review; past supplies keep the rate they were billed at; full rate history. Each client also has a credit limit and an opening balance. Every movement is a ledger entry: supply (fuel out, stock down, due up), return (fuel back into the tank, due down), payment, and admin adjustments. Shows litres out / in per product, billed, received and current due; a statement with running balance and date filter; CSV/Excel export and print. Wrong entries are voided (stock reversed, kept in the ledger as VOID). Credit-limit check on every supply, with an alert at 90%. Separate Wholesale Officer role. |
| **Expenses** | Expense categories with optional monthly budgets, entries by station and payment method, and an approval flow: manager entries above the approval limit (default Rs 10,000) wait for admin approval. Monthly summary by category vs 3-month average and budget, revenue (retail + wholesale) minus expenses, CSV export. Over-budget and unusual-spike alerts appear on the dashboard and in Ask AI. |
| **Easy for every staff member** | **PIN sign-in:** on the pump tablet staff tap their name and enter a 4-digit PIN (locks for 10 minutes after 5 wrong tries); the admin sets PINs on the Users page, and a forgotten PIN is fixed with **Reset PIN** (type a new one or let the app make one), which also removes the lock. **Undo:** a salesman can undo their own sale for 2 minutes after saving, a manager any time while the shift is open; stock, meter, khata and loyalty are reversed and the undo is logged. **Works without internet:** sales made offline are kept on the tablet, upload by themselves when the connection is back, are saved once and billed at the price in force when they were made; the app opens offline. |
| **Photo and voice entry** | **Camera buttons** read the dispenser totalizer at shift start/end, the tanker invoice (litres, rate, tanker no., supplier) and expense or bank-deposit slips. With an AI key Claude fills the numbers; the photo is always kept as proof on the shift report, delivery and expense. **Speak the sale:** the salesman says "police station kahna 20 litre diesel slip 7781" or "do hazar petrol easypaisa" (Urdu or English speech, or typed); fuel, amount, payment, khata account, vehicle and slip are filled in, then Save. Works without an AI key (built-in parser for English, Roman Urdu, Urdu and Urdu digits). |
| **No paper khata** | **WhatsApp receipt for every khata fill** (litres, rate, slip, new balance). **Monthly bills** for khata accounts and **statements** for wholesale clients go out on the 1st as a private, printable link with every fill, slip and payment (also sendable any time). **QR cards:** print an account card and a sticker for each vehicle; at the POS "Scan khata card" (camera or code) fills the account and vehicle. **Wholesale clients** are messaged for every supply, payment and return and when their rate changes. Each of these can be switched off in Settings. |
| **Staff accounts** | Advances, repayments and bonuses; a shift that closes short (Rs 100 or more) is added to the salesman's account automatically. "Pay salary" takes the advance/shortage off the salary, records the salary expense and tells the staff member. Every staff member sees their own account under "My account". |
| **Cash & bank** | Office cash worked out without a register: last cash count + cash handed over from shifts + khata and wholesale cash − bank deposits − cash expenses, supplier payments and advances. Record bank deposits (with slip photo) and cash counts; a count that differs by Rs 500 or more alerts the admin. |
| **Tanker orders & dip charts** | "Order tanker" on each tank suggests the last supplier and the litres that fit; the order goes to the supplier on WhatsApp and closes itself when that tanker is received. Each tank has a dip chart (paste the oil company's chart or make one from the tank diameter), so dips are entered in cm and turned into litres. |
| **Day close & owner on WhatsApp** | Just after midnight the day is closed: the owner gets the day's sales, expenses, supply, stock, office cash and shortages with a printable report link, and the closed day can only be changed by the admin. The owner (and admins/managers) can WhatsApp questions such as "aaj ki sale?", "kal kitna kharcha hua", "stock kitna hai", "cash kitna hai" or "kis ne paise dene hain" and get the numbers back (Claude answers anything else when configured). |
| **Shop & lubricants** | Engine oil, filters, coolant, tyres and tuck-shop items per station with barcode, cost, price and stock. The POS has a **Shop** tab (tap items or scan the barcode); shop cash goes into the shift's cash bag. Stock-in keeps an average cost, counts log the difference, low stock alerts the manager, and shop sales and profit are in every report. |
| **Vehicles, loyalty and receipts** | A khata vehicle can have a fuel type and a **daily litre limit**, checked at the POS (a manager can allow more). Customers can **pay with loyalty points** (1 point = Rs 1). Every sale shows a **QR digital receipt** with an optional Google review link. |
| **Licences, checklist, attendance** | Licences and certificates (explosives, OGRA, fire NOC, nap-tol calibration…) with reminders 30/7/1/0 days before expiry and weekly after, to managers and the owner. A daily/weekly **checklist** with big OK/Problem buttons, readings with allowed ranges (water in tank, density, 5-litre measure) and photos; failures and missed checks alert the managers. **Attendance**: selfie + location check-in (opening a shift checks the salesman in), lateness against duty time, weekly off, leave requests and approvals; unpaid absences are cut at salary/30 per day when paying salary. |
| **Owner insights** | Pump **health score** on the dashboard (stock, cash, khata, licences & checks, staff, sales trend); station comparison; **salesman risk score** (short shifts, litres not entered on the POS, undone sales, late days, failed checks) sent to the owner every Monday; monthly **profit & loss and balance sheet** with Excel export; upload an Easypaisa / JazzCash / card / Raast **statement** to find payments with no sale and sales with no payment; **price-change planner** for the 1st/16th revisions; **tank gain/loss** with leak suspicion. |
| **Customer care** | A private **customer page** link for each khata account (balance, every fill and slip, monthly bills, pay now). **Overdue khata goes on hold** at the POS after N days without payment (institutions optional) and opens on payment; optional monthly late charge (never for institutions). **Government bills** per month with PO number, submission date and cheque payments; printable **payment notice** in English and Urdu. **Bookings** for car wash, oil change, tyre and service — by staff or by the customer on WhatsApp — with reminders and oil-change-due follow-ups. |
| **Safety & help** | **Nightly backups** (last 14 kept) with download and a safe restore on the next start; **two-person approval** for price changes (optional); **phone alerts** (web push) for staff; **Training mode** on the POS for new salesmen (nothing saved); a **Help · مدد** button on every page with Urdu and English steps that can be read aloud. |
| **Stock register** | The **daily stock register** and **monthly return** in the Explosives / OGRA format, made by itself from sales, tanker receipts (tanker and invoice), wholesale supplies and dips: opening, received, sold, book closing, dip closing, gain/loss and closing carried forward per product, with the explosives / OGRA licence numbers in the heading and signature lines. Print it for inspection. |
| **Coupons & wallets** | **Fuel coupons**: sell a book of Rs 500 / 1,000 / 5,000 coupons (any fuel or one fuel, optional expiry), print them with QR codes; at the POS **Coupon** scans one and it can be used only once (undo gives it back; unused coupons can be cancelled). **Company wallet**: a fleet or company deposits first (Raast / bank / cash) and gets a WhatsApp with the balance; at the POS **Wallet** takes each fill from it, and the company is told on WhatsApp when it drops below its alert level. Cash from coupons and wallet deposits goes into the cash book. |
| **Ratings, leaderboard & commission** | After a fill on a customer's account the customer gets a WhatsApp asking for **1–5**; a 1 or 2 asks what went wrong, opens a complaint and alerts the manager, 4–5 get the Google review link. Ratings per salesman and station. **Commission** on lubricants / filters / other shop items (percent per category) and optionally per litre is added to the salary by itself; a **weekly leaderboard** (litres, shop sales, rating, shortages) goes to every salesman on Monday. |
| **TV rate board** | A link for a TV / LED screen at the pump: today's prices in big English and Urdu, with offers; it changes by itself within 20 seconds of a price change. |
| **Approvals on WhatsApp** | A manager's price change (when the two-person rule is on) or an expense above the limit is sent to the owner on WhatsApp — reply **1** to approve or **2** to reject ("1 12" picks one when several wait). Only the owner's number or an admin can approve. |
| **Monthly costs & bills** | Rent, guard, internet and other fixed costs are booked by themselves on their day each month. Take a photo of the **bijli / gas bill**: AI reads units, amount, month and reference; the bill goes into expenses and the owner is told if it is more than 15% above last month. |
| **Accounts & tax** | **Tanker shortage claims:** every tanker short by more than the allowed transit loss (default 0.2%) becomes a claim of the extra litres × purchase rate; send it to the depot on WhatsApp, then record the credit note (it lowers what we owe) or write it off. **Depot comparison:** rate, freight (entered on the delivery) and shortage per litre give the landed cost per litre for each depot and fuel, with the cheapest marked. **Tax:** GST on shop / lubricant sales (inclusive or on top, exempt categories) shown on every shop receipt with the NTN / STRN; income tax withheld when paying a supplier (or rent / services) with the CPR number when deposited; monthly tax report with Excel export. **Bank reconciliation:** upload the bank statement CSV; deposits, cheques and payments are matched with the books, and the rest is listed as "in the bank but not in the books" (charges — add as expense in one tap, unknown receipts) and "in the books but not in the bank yet" (uncleared cheques, deposits in transit). **Ledger export:** the whole business as a double-entry journal with a trial balance, downloadable for Excel / QuickBooks (CSV) or Tally (XML). |
| **Hardware (pending)** | Automatic tank gauges, dispenser/forecourt controller, CCTV with number-plate reading, tanker GPS and generator/electricity meters are listed in Settings as pending and will be connected when the equipment is installed. |
| **Today's book (admin dashboard)** | One panel, from 12:00 am Pakistan time to now: sales (pump + wholesale; cash, digital, khata), expenses (and what is waiting for approval), supply received (tankers, litres, purchase cost), stock left per fuel (opening + received − sold = left now, matching the tanks), current stock value at purchase cost and at today's selling price, estimated profit, how much people owe us and how much we owe, and cash short/over from closed shifts. It uses the same calculation as Reports, so both always agree. |
| **Reports** | For Admin and Manager, any time: presets for 24 hours, 7 days, 1 month, 6 months and 1 year, or a custom date/time range (Pakistan time). Tabs: **Overview** (revenue retail + wholesale, expenses, estimated gross and net profit, money in / money out, chart by hour/day/month), **Sales** (by product, station, payment method, top customers), **Stock** (opening + received + returns − retail − wholesale ± dip adjustment = closing per product, rebuilt from the movement ledger so any past period is exact; tank levels, deliveries, dips, purchases, stock value at cost), **Expenses**, **Receivables** (who owes us: khata customers and wholesale clients, with aging), **Payables** (whom we owe: fuel suppliers, customer advances, expenses awaiting approval), **Khata**, **Wholesale**, **Shifts & cash** (expected vs counted per attendant). Every tab exports to Excel/CSV and prints to PDF. "Ask AI" can answer report questions for any period. |
| **Suppliers** | Fuel supplier (depot) accounts. Choosing a supplier and purchase rate on a tanker delivery records what we owe; payments to the depot reduce it. Purchase rates give the fuel cost used for profit estimates. |
| **Campaigns** | AI-written Roman Urdu broadcasts to segments, with `{name}`, `{balance}` and `{points}` placeholders. Opt-in only. |
| **AI analytics** | Holt-Winters demand forecast with weekly seasonality, days-to-empty per tank with a suggested order, anomaly detection, insight cards, and "Ask your business anything" (Claude with read-only analytics tools). |
| **Automations** | Twenty scheduled jobs on Asia/Karachi time, each of which can be toggled or run on demand (see below). |
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
| Day close | daily 00:05 | Locks the previous day and sends the owner its report and link |
| Monthly bills | 1st of the month 09:00 | Khata bills and wholesale statements with a printable link |
| Licence & certificate expiry | daily 09:00 | Reminders 30/7/1/0 days before and weekly after expiry |
| Daily checklist follow-up | 12:00 and 20:00 | Managers told which daily checks are not done |
| Attendance follow-up | hourly | Staff not checked in an hour after duty time |
| Weekly staff risk | Mondays 10:00 | Risk score per salesman to the owner |
| Overdue khata hold | daily 08:00 | Accounts with no payment for N days put on hold |
| Late-payment charge | 1st of the month 08:00 | Only when a percent is set; never institutions |
| Booking reminders | every 15 min | WhatsApp an hour before each booking |
| Oil change due | daily 10:30 | Reminder 3 months after the last oil change |
| Nightly backup | daily 02:30 | Full database copy, last 14 kept |
| Monthly fixed expenses | daily 07:00 | Rent, guard, internet… booked on their day of the month |
| Weekly salesman leaderboard | Mondays 09:00 | Top 3 and each salesman's own place and commission |

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
4. **Owner number.** In Settings, enter the owner's WhatsApp number for alerts, the daily brief and the day-close report. Messages from this number (and from admins/managers whose number is saved) are answered by the business assistant instead of the customer bot.
5. **Public URL.** Set `PUBLIC_URL` to your domain so bill and day-report links in WhatsApp messages open correctly.
6. **Prices.** The seeded prices are demo values. Enter the current government-notified prices on the Prices page.

7. **Backups.** Backups go to `data/backups` (or `BACKUP_DIR`). Download one regularly and keep it off the server.
8. **Phone alerts.** Optional: set `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY`; otherwise keys are created once and stored in the database. Push needs HTTPS.

See `.env.example` for every option.

## Roadmap ideas

- WhatsApp voice notes from customers (Urdu speech to text)
- Hardware (pending until the equipment arrives): tank gauge probes, forecourt controller, CCTV number-plate reading, tanker GPS, generator/electricity meters
- FBR invoice integration
- PostgreSQL and multi-tenant SaaS billing
