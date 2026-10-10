# PumpAI — project memory & standing rules

PumpAI is a petrol-pump CRM: Express + TypeScript + `node:sqlite` (`DatabaseSync`) server, React 18 + Vite + Tailwind mobile-first PWA web, with a double-entry ledger that must tally to the rupee.

## ⚠️ DEPLOYS ARE ADDITIVE — NEVER LOSE OR REGRESS PRIOR WORK

Every deploy ships the **complete current codebase**, not just the latest change. Deploying must never remove, overwrite, or regress a feature built earlier. Concretely:

- The deploy uses `git subtree split --prefix=pumpai` and pushes the whole `pumpai/` tree to the live repo's `main`. `git` is additive — history accumulates; nothing from before is dropped unless code is actually deleted. So **do not delete or simplify-away existing features** when adding new ones.
- Before deploying, make sure the change is purely additive/surgical. Never replace a working file wholesale in a way that drops behaviour.
- After any change, keep the whole-system checks green (see below) so a regression can't slip out.
- The dev branch is `ccr-bd7a93fb-tpb3gq`. Develop and push there only.

## Deploy command (run from `/home/user/Codeea`)

```
git add -A pumpai && git commit -qm "<msg>

<attribution lines>" && git push -q -u origin ccr-bd7a93fb-tpb3gq \
 && git branch -f pumpai-export $(git subtree split --prefix=pumpai) >/dev/null 2>&1 \
 && git push -q pumpai pumpai-export:main && echo "DEPLOYED OK"
```

Deploy after every change. The live Vercel site is network-blocked from the sandbox, so **verify on a local production build** instead: run the demo server with `NODE_ENV=development DEMO_DATA=1 WEB_DIST=.../web/dist` and drive it with Playwright (`/opt/node-tools/node_modules/playwright`). Login: `admin@pumpai.pk` / `demo1234` (email form → "Sign in with email instead"; the "Sign in" button has no `type="submit"`, target it by text).

## STANDING REQUIREMENT — bank-statement letterhead on every document

Every voucher, invoice, bill and statement — customer, khata, wholesale client, carriage/thekedar, supplier, cashier, owner report — must print on the pump's letterhead (logo, business name, phone, address, NTN/STRN, footer with socials), like a bank statement. This is provided **globally** and must not be removed:

- `components/Layout.tsx` renders `<PrintHeader/> <Outlet/> <PrintFooter/>` around every full-page route (so every page print gets the letterhead).
- `components/ui.tsx` `Modal` renders `<PrintHeader/>` … `<PrintFooter/>` (so every modal document — khata statement, vouchers — gets it too).
- `components/Letterhead.tsx` holds `PrintHeader`/`PrintFooter`; they read `/business`.

When adding a new printable document, render it inside a route (Layout covers it) or a `Modal`, and never strip these components.

## Keep the books tallied — run before every deploy

```
cd pumpai/server && NODE_ENV=test npx tsx --test test/*.test.ts     # all must pass
```

`test/system_audit.test.ts` + `test/helpers/tally.ts` assert the whole system ties to the rupee: journal debits=credits, nothing in Suspense, and every party/stock/cash/bank/coupon/wallet/staff/P&L/balance-sheet reconciles to the ledger — including the owner's levers (khata discount, bank card commission/MDR, shop/unit rent). Do not deploy with a red test.

## Roles (web nav + API perms, `server/src/auth.ts`)

- **Salesman** menu is intentionally short: Sales/POS, Shifts, Bookings, Daily checks, Machines, My account. Salesman has **no** Prices page, **no** Customers page, **no** Staff-attendance (kiosk). Salesman cannot add/edit customers — only picks existing khata accounts at the POS (`/pos/khata-accounts`, gated by `sales.create`). Self-attendance is in My account.
- The POS shows a prominent read-only "Aaj ke rate" (today's fuel rates) banner; rates are set by admin/manager on the Prices page.

## AI provider — Claude OR free Gemini OR rules (never regress)

Two AI backends, chosen by `aiProvider()` in `server/src/config.ts`: **Claude** (`anthropicKey`, paid) wins if set; else **Gemini** (`geminiKey`, free tier) via `server/src/ai/gemini.ts` (REST, no SDK); else the built-in **rule engine**. Every AI entry point (WhatsApp agent, Ask AI, photo reading, voice parsers, coaching, campaigns) must keep all three paths working. `gemini.ts` takes `runTool` as a parameter (not an import) to avoid a require cycle — keep it that way. Keys are set in Settings → Integrations (`gemini_key`/`gemini_model`) or `.env`. WhatsApp (Meta Cloud API) is free to set up; only proactive templates cost — never add a paid-only dependency.

## CEO-only "Other income / expense / discount" (never regress)

`routes/otherEntries.ts` + `other_entries` table + perm `other_entries.manage` (ADMIN). Web page `OtherEntries.tsx` (`/other-entries`, in the Money group, CEO-only). The ledger is **derived**, so money/balance moves go through the EXISTING source tables (so cash/bank/party reconciliations stay untouched) and the contra is routed in `ledger.ts`:
- income → `otherMoney` (cashier_vouchers/bank_txns), ref `oth-inc:` → `specialSide` → **"Other income (CEO)"**; added to P&L `other_income` via `otherEntriesIncome()`.
- expense → `otherMoney` out, ref `oth-exp:` → **"Expense: Other (CEO)"**; auto-counted in P&L expenses.
- discount → writes the party's OWN ledger (khata_ledger credit / wholesale_txns / carriage_txns payment) with ref `oth-disc:` → `isOtherDisc` routes the contra to **"Other discount (CEO)"**; reduces the party balance AND P&L net via `otherEntriesDiscount()`. A discount needs a real party (no free-text).
Never post these to "Suspense"; keep `test/other_entries.test.ts` + the whole-system tally green.

**CEO-private:** these are the owner's own dealings — a manager must not see them. `journal(t,from,to,showPrivate=true)` and `profitAndLoss(t,month,showPrivate=true)` take a flag; the manager-facing routes (ledger, analysis/pl, targets, owner/overview) pass `req.user!.role==="admin"`. With `showPrivate=false` the three entries are reclassified to **Owner's capital / Owner's drawings** with generic narration and dropped from P&L, so the manager's books still balance but reveal nothing. Internal/test callers default to `true` (full view) — the tally always uses the full journal.

Same `showPrivate` gate hides them from the **cashier** too: `cashierDayBook(t,d,showPrivate)` drops discounts (no money — this also fixes a phantom bank-in) and relabels cash income/expense as "Owner money in/out"; `bankMoves(t,acc,showPrivate)` relabels bank ones in the statement; and `/cashier/desk` genericizes the recent-vouchers list. All pass `req.user!.role==="admin"`. The cash in the drawer still reconciles (amounts unchanged), only the party/reason is hidden.

## Cashier voucher void (CEO-only)

`POST /cashier/vouchers/:id/void` (perm `cashier.void` = ADMIN) reverses a wrong cash receive/pay by its `src`: khata → delete khata_ledger + restore customers.balance; wtx → void wholesale_txns (+ delete the depot supplier leg); stx/staff/bank/expense → delete the source row (expense also closeApproval); cash "other" (src null) → just voided=1 (cashFlows reads voided=0). Always sets the voucher voided=1 + reason + audit (`cashier_voucher_void`). cashPosition reads the source tables (vouchers only for "other" cash), so reversing the src + voiding the voucher keeps cash/bank/party reconciliations exact. Cheque vouchers are refused (cancel in the cheque register). UI: a CEO-only Void button on the Cashier desk's recent-vouchers list (`test/cashier_void.test.ts`). Correction gaps still open (no direct fix yet): closed-shift sales, closed shifts, manual khata payment/charge, stock deliveries.

## CEO Corrections — void a wrong sale / manual khata entry / stock delivery (never regress)

`routes/corrections.ts` + perm `corrections.manage` (ADMIN). Web page `Corrections.tsx` (`/corrections`, System group, CEO-only, 3 tabs). Companion to the cashier voucher void (cash receive/pay) and Other-entries undo. The ledger is DERIVED, so each void undoes the SOURCE row(s) + restores the paired balance column(s) + writes an audit row; after any of them `GET /books/check` stays 0 farq.
- **Sale void** `POST /corrections/sale/:id/void` → `reverseSale(tenantId, sale)` in `services.ts` mirrors `recordSale` leg-for-leg: deletes khata_ledger `SALE-<id>` rows + moves customers.balance back; restores wallet_balance + deletes wallet_ledger (sale_id); reactivates a used coupon; reverses loyalty points + loyalty_redemptions; adds litres back to the product's tank + winds nozzle totalizer back; deletes the sale (cash/card is derived from the row). Works on any sale incl. an at_close / meter close entry. `GET /corrections/sales?q=` lists recent sales.
- **Manual khata void** `POST /corrections/khata/:id/void` → delete khata_ledger row + restore customers.balance. REFUSES rows owned elsewhere: ref `SALE-%` (use sale void), `oth-disc:%` (Other entries), or any row a cashier voucher's `src='khata:<id>'` points at (use cashier void). `GET /corrections/khata?customer_id=` flags each row `voidable`/`owner`.
- **Delivery void** `POST /corrections/delivery/:id/void` → delete the `supplier_txns` purchase row(s) by `delivery_id` (reverses payable + Fuel-purchases expense), `tanks.current_l -= received_l`, delete a still-OPEN `shortage_claims` row (refuse if claim already claimed/recovered/written_off), delete the `deliveries` row. `GET /corrections/deliveries` flags `voidable`.
- **Supplier txn void** `POST /corrections/supplier-txn/:id/void` → delete the supplier_txns row; if it's a payment with a withholding sibling (same supplier + same txn_date + method 'WHT') delete that + its `tax_withholdings` record too (refuse if the WHT CPR is already deposited). REFUSES a delivery-linked purchase (use delivery void) and a cashier-voucher payment (use cashier void). `GET /corrections/supplier-txns` flags `voidable`/`owner` (excludes WHT rows).
- **Wholesale txn void** `POST /corrections/wholesale-txn/:id/void` → set `wholesale_txns.voided=1` + reason; on a supply restore `tanks.current_l += litres`; delete any depot-leg `supplier_txns` ref `wtx:<id>`. REFUSES a bulk trip (`trip_id`, void from wholesale page) and a cashier-voucher payment. `GET /corrections/wholesale-txns`.
- **Rent void** `POST /corrections/rent/:id/void` → delete the office voucher (by `rental_payments.voucher_id`), the matching bank_txn (account+kind+amount+note, if any), and the `rental_payments` row — reverses cash/bank AND the rent income. `GET /corrections/rent`.
- Carriage/thekedar entries already void via `POST /carriage/txns/:id/void` (perm `carriage.void`); the Corrections page points there, no duplicate.
NOT built (deliberate, too risky for zero-rupee/meter integrity once later shifts exist): full "reopen a closed shift". Instead void the specific wrong sale(s) — sale void works on closed-shift sales too. Web `Corrections.tsx` has 6 tabs (Sale/Delivery/Khata/Supplier/Wholesale/Rent). `test/corrections.test.ts` guards every path + CEO-only + reason-required + tally (513 tests total).

## Live "Hisaab check" — whole-system reconciliation on one button (CEO-only)

`routes/books.ts` `reconcileBooks(t)` + `GET /books/check` (perm `audit.view` = ADMIN). It runs the SAME ties `test/helpers/tally.ts` enforces — journal debit=credit, nothing in Suspense, and every cash/bank/khata/wholesale/carriage/supplier/coupon/wallet/staff/bypass-stock balance equals its ledger account — but LIVE and WITHOUT throwing (each check wrapped in `safe()`, TOL=1 rupee), returning `{ok, checked_at, max_diff, checks:[{label,book,ledger,diff,ok,note}], failed}`. Web: `Audit.tsx` `BooksCheck` card — the CEO presses it (especially after a correction like a voucher void) and sees green "Sab tally — 0 rupaye ka farq" or the exact mismatched line(s). Keep it mirroring tally.ts; `test/books_check.test.ts` guards it (all-green on demo, still 0 after a CEO void, manager 403).

## Carriage / thekedar collection (overdue + reminders)

`routes/carriage.ts`: `thekedarAging(t)` (due per thekedar via `thekedarDue`, last-payment days, FIFO age-bucket 0-30/31-60/60+, `overdue` = owes AND no payment in `carriage_overdue_days` setting, default 20) + `GET /carriage/call-list` (owing thekedars worst-first + totals). `carriageReminders(t)` WhatsApps each overdue thekedar his kiraya balance via `sendDirect`, throttled to once per 5 days using the `outbox` ref `carr-reminder:<id>`. Registered as the daily `carriage_reminders` automation job (noon). Web `Carriage.tsx` shows a "Wasooli list" card (aging buckets + per-thekedar days-since-payment). `test/carriage_collection.test.ts`. Mirrors the wholesale call-list so carriage receivable no longer ages invisibly.

## Conventions

- Reply to the user in Roman Urdu/Hindi.
- Never put a model identifier in commits, PR text, code, or any pushed artifact — chat only.
