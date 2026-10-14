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

## WhatsApp payment screenshots → payment inbox (khata credit on cashier confirm)

Inbound WhatsApp images are now handled. `whatsapp/cloud.ts`: `InboundMessage.image` + `parseWebhook` picks up `type==='image'` (media id + caption); `fetchMedia(id)` downloads media→base64 (live only). `ai/agent.ts handleInbound` (image branch, before the normal reply): downloads/`readPhoto("payment")` (amount/method/reference/confidence — null without an AI key), saves the image as a `photos` row, and calls `recordPaymentScreenshot` (`routes/paymentInbox.ts`). That matches the sender's phone to a REAL khata customer (credit_limit>0 OR balance<>0 OR has khata_ledger — a random new number is NOT queued), inserts a PENDING `payment_inbox` row, and notifies cashiers; the customer gets an ack. Nothing is auto-credited — a screenshot is a claim.
`routes/paymentInbox.ts` (perm `cash.receive`): `GET /payment-inbox?status=`, `POST /payment-inbox/:id/confirm` {amount,method,account_id?} → `khataEntry` credit + moves the photo onto the khata entry + marks confirmed (tally-safe), `POST /payment-inbox/:id/reject` {reason}. Web: a "Payment inbox" card on the Cashier desk (`Cashier.tsx`), thumbnail + one-tap Confirm/Reject. `/whatsapp/simulate` takes an optional `image` (base64/data-URL) to test the flow. `test/payment_inbox.test.ts` (queue→confirm→khata credit+tally, reject, unknown-number-not-queued). `table payment_inbox` in db.ts. Table 516 tests green.

## Conventions

- Reply to the user in Roman Urdu/Hindi.
- Never put a model identifier in commits, PR text, code, or any pushed artifact — chat only.

## Late offline sales + zero-sale shift close (never regress)

- **Late offline sale on a closed shift** (`rebookLateSale` in `routes/operations.ts`, `POST /sales` with `offline_at` from a salesman): at close the meters booked every un-entered litre as CASH (`source='meter'`). A late **cash** sale returns `{covered_by_meter:true}` (nothing to add). A late **khata / card / online** sale is moved OUT of that meter cash row (litres & amount reduced, row deleted if emptied), recorded with `at_close=1` at the rate of `offline_at`, tank given back first so `recordSale` nets to zero, and `shifts.cash_expected/variance` reduced by the freed rupees; audit `late_sale_rebooked` + alert type `late_sale`. A duplicate of an at-close slip (same shift/customer/product/litres) returns `duplicate`. Refused (409) only when the meter cash has fewer litres left. Never throw the old "shift already closed" for a salesman's offline sale — that lost the khata entry and the shift report line.
- Web: `useOfflineQueue()` also runs in `Layout.tsx`, so queued tablet sales upload from any page; the shift-close modal (`Shifts.tsx` `CloseShift`) blocks "Close shift" while sales are still queued on the tablet.
- **Zero-sale shift** (strike / pump closed): the close button is enabled with Rs 0 counted when `preview.cash_expected <= 0`; the cashier's handover `pending` list skips shifts with cash_actual=0 AND cash_expected=0. `test/late_sale_zero_shift.test.ts` guards all of it (522 tests).

## Opening balances (go-live on an already-running pump) — CEO-only (never regress)

`routes/opening.ts` + perm `opening.manage` (ADMIN) + web `OpeningBalances.tsx` (`/opening-balances`, System group). Brings the old register forward WITHOUT touching P&L:
- **Khata**: `POST /opening/khata {as_of?, rows:[{customer_id, amount}]}` writes ONE khata_ledger row per customer with `ref='open:'` (debit = owes us, credit = we owe them; 0 clears), replacing any earlier opening row and moving `customers.balance` by the difference; stamped at `pkStart(opening_date setting)`. `POST /opening/khata/import {text}` pastes "name, phone, amount[, limit]" lines (header row skipped): match by phone → exact name → create a khata customer (credit_limit = max(limit, amount)); re-import replaces, never doubles.
- **Parties**: `POST /opening/party {kind: supplier|wholesale|carriage, id, amount}` sets the row's `opening_balance` (their due functions already start from it; tally subtracts it). **Bank**: `POST /opening/bank {id, amount, date?}` sets `opening_balance`/`opening_date`. **Staff**: `POST /opening/staff {user_id, amount}` = `staff_ledger` 'advance' with `ref='open:'`, method NULL — excluded from the cash book (`cashFlows.staff_advances`), bank moves and the cashier day book, so no cash moves. **Stock**: `POST /opening/stock {tank_id, litres}` = a dip row + `tanks.current_l`. **Cash**: the first cash count on the Cash page is the opening (ledger → owner's capital, already so).
- Ledger (`ledger.ts`): khata rows `ref='open:'` → Dr/Cr "Khata receivable" against **"Opening balances (go-live)"** (type "Opening"); staff `open:` advances → Dr "Staff advances" / Cr the same equity account. Nothing reaches "Other income". Corrections' khata void refuses `open:` rows (owner `opening`). `test/opening.test.ts` (527 tests, tally + books/check green).

## Cloud backup copies — Google Drive + Dropbox (never regress)

`server/src/backupCloud.ts` (plain fetch, no SDKs) + routes in `routes/system.ts`; UI `CloudBackups` inside the Settings → Backups card. `makeBackup()` calls `void cloudPushAll(file)` after the local copy (and BACKUP_COPY_CMD still works). Config lives in settings tenant 0: `cloud_gdrive` {client_email, private_key, folder_id}, `cloud_dropbox` {app_key, app_secret, refresh_token, account}, results `cloud_last_<provider>` {at, ok, message, file}.
- **Google Drive**: service-account key JSON + shared folder ID (a pasted folder link is accepted). `PUT /backups/cloud/gdrive` validates the JSON (`parseServiceAccount`), signs an RS256 JWT, fetches a token and checks the folder live before saving. Upload = multipart to `upload/drive/v3/files` with `parents:[folder]`, `supportsAllDrives=true`; keeps the newest 14 `pumpai-*.db` in the folder.
- **Dropbox**: app key/secret + one-time code from `GET /backups/cloud/dropbox/auth-url?app_key=` (offline access) → `POST /backups/cloud/dropbox/connect` swaps it for a refresh token. Upload to `/PumpAI backups/<name>` (overwrite), keeps the newest 14.
- `POST /backups/cloud/:p/push` (send latest now, 502 on failure), `POST /backups/cloud/:p/check`, `DELETE /backups/cloud/:p`. All `settings.manage`. A cloud failure is recorded and shown, never breaks the local backup. `test/backup_cloud.test.ts` mocks both APIs (real JWT verify) — 533 tests.

## Owner / manager menu + role-aware user guide (never regress)

- `Layout.tsx` grouped menu (admin/manager): TOP (Dashboard, WhatsApp, Alerts) → **Pinned** (star on hover, `pumpai.nav.pins`) → **Recent** (last 4 pages, `pumpai.nav.recent`) → GROUPS (one open at a time, `pumpai.nav.openKey`; the active page's group is always open) → BOTTOM (User guide). Every NAV route must be filed in a GROUP (or TOP/BOTTOM); unfiled routes still render under the groups as "strays" so nothing disappears. Group header = English + small Urdu line; items show Urdu in the tooltip (flat menus for salesman/cashier/wholesale keep inline Urdu). Sidebar is w-64.
- `PageGuideButton` (floating "?") opens `/help#<module>` via `ROUTE_GUIDE` (route → guides.json id); add a mapping when adding a page with a guide.
- `Help.tsx` opens on the user's OWN guide: `guides.json` modules carry `roles[]`; admin/manager get role chips + "All modules"; the featured training video is the role's own. Modules with `media:false` (opening, corrections, other-entries, backups) have steps/tips only (no GIF/PDF). Arriving with `#id` switches to "All" and opens that module's steps.

## Direct (walk-in) discount at the POS (never regress)

`sales.discount_kind` ('khata' | 'direct') + `sales.discount_ref` (free text). `recordSale` defaults the kind from the method (khata → 'khata', else 'direct'); a 'khata' kind needs a khata customer, 'direct' is refused on khata/coupon/loyalty; discount < gross. `POST /sales` takes `discount`, `discount_kind`, `discount_ref`; the salesman's cap is `direct_discount_max` (default 500, Settings → limits; `/pos/today.direct_discount_max`), `customers.edit` roles uncapped. `POST /sales/:id/discount-ref {ref}` (own sale of the open shift, or manager any time; edits audited `discount_ref_changed`). `directDiscountWatch` raises alert type `direct_discount` once per shift when the shift's direct discounts reach `direct_discount_alert` (default 2000).
- Money: `sales.amount` is NET, so cash expected / handover / day book are automatically net. Ledger (`ledger.ts` fuel voucher) books revenue at gross with Dr **"Discount given — direct"** (khata ones stay on "Discount given — khata"); P&L `discount_direct`; `shiftSummary.direct_discount/_n`; `/reports/discounts` adds `khata_total`, `direct_total`, `direct_count`, `by_reference`, and `discount_kind/discount_ref/payment_method` per row. Sale void reverses it (row deleted).
- POS (`Pos.tsx`): rose "Direct discount" button in the payment grid (enabled for cash/card/online), `DirectDiscountPicker` (Rs fixed or Rs/litre, shows gross − discount = net, limit), the "Sale saved" screen then demands the reference (OK / backdrop blocked until typed; offline sales get it via `patchQueued`). `test/direct_discount.test.ts`.

## Documents as PDFs + "WhatsApp" next to every Print (never regress)

`server/src/docs.ts` renders vouchers, khata / wholesale / carriage / supplier statements, shift reports and fuel receipts with the multi-page `Pdf` writer (`newPage()`, shared logo image) on the letterhead; salary slips reuse `slipUrl`. `docLink()` = signed public `/doc/<jwt>.pdf` (`doc_link_days` setting, default 365), served by `app.get("/doc/:token")` in index.ts. `routes/docs.ts`: `GET /docs/:kind/:id/pdf`, `GET …/contacts` (party phone, staff with phones incl. tenant owner, recent numbers from `sent_docs`, previous sends, `api` flag), `POST …/send {phones[], note?, from?, to?}`; `allow()` = the page's own perms, salesman only own shift/sales. Sending: with the Cloud API (`config.wa.token`+`phoneNumberId`) `sendDocumentToPhone` sends a `document` message (falls back to text+link) and a khata customer's conversation gets a copy; without it the response carries `wa.me` links the browser opens (channel `phone`). Every send → `sent_docs` + audit `document_sent`. Web `components/WhatsAppDoc.tsx` (`<WhatsAppDoc kind id from? to? />`) is placed beside Print in KhataStatement, Cashier VoucherSlip + today's vouchers, Wholesale/Carriage/Supplier statements, ShiftReport, Pos saved screen (receipt) and SlipsList. `test/docs_whatsapp.test.ts` (543 tests).

## Bypass leftover unloaded into our own tank (never regress)

`POST /bypass/to-tank {product, litres, tank_id, delivery_id?, txn_date?, note?}` (perm wholesale.manage): the litres leave the bypass stock at its average cost as a `bypass_drops` row with `client_id NULL`, `tank_id` set (`to_delivery_id`, `note`), a `deliveries` row is written on that tank at `purchase_rate = unit_cost` with `bypass_drop_id` (supplier "<latest bypass supplier> (bypass leftover)", no new `supplier_txns` — the bypass purchase already covers it), and `tanks.current_l += litres`. Ledger (`ledger.ts` bypass drops loop): `tank_id` rows book **Dr Fuel purchases / Cr Bypass stock** (client drops stay Dr Bypass fuel cost); so `bypassStock()`, the balance sheet, stock register / avg cost / P&L COGS all follow. `GET /bypass/to-tank` lists transfers; `POST /bypass/to-tank/:id/void {reason}` (wholesale.void) marks the drop voided, deletes the delivery row and takes the litres out of the tank (refused if the tank no longer holds them). Corrections' delivery void refuses `bypass_drop_id` rows. Web: `LeftoverToTank` card on the Bypass section (Wholesale → Tanker trips). `test/bypass_to_tank.test.ts` (547 tests, tally green).

## Opening balance as DUE or ADVANCE (never regress)

Party balances are signed everywhere: positive = they owe us, negative = their advance lies with us. `OpeningBalanceField` (web) = amount + "Unhein dena hai (due)" / "Advance diya hua (credit)" toggle, used in the khata `CustomerForm` (create, admin only) and the wholesale `ClientForm`. Server: `POST /customers {opening_balance, opening_kind}` (perm `credit.set_limit`) writes the go-live `open:` khata row via `setKhataOpening` (exported from `routes/opening.ts`); wholesale `opening_kind` signs `opening_balance` (`signedOpening`). An advance is eaten by sales first: `recordSale`'s check `balance + amount > credit_limit` holds naturally, POS lists khata accounts with `credit_limit > 0 OR balance < -0.5` and shows `advance`; the limit error reads "Advance khatam…". Labels: `dueLabel/dueTone` (web format.ts), `dueText` (server services.ts), statements/PDF say "advance with us" when negative. An advance received TODAY also carries `opening_method` (+ `opening_account_id` for non-cash, via `bankAccountFor`): khata → a normal `khataEntry` credit (cash book / bank / ledger Dr Cash-Bank, Cr Khata receivable), wholesale → a `payment` txn via `insertTxn` with `opening_balance` forced to 0; no method = old go-live advance (opening row / signed opening_balance, no money moves). Cheques are refused there (use Receive payment). Web `OpeningBalanceField` shows the method chips + `AccountPicker` only on create. `test/opening_advance.test.ts` (tally green).

## Number entry on phones (never regress)

Every `<input type="number">` carries `inputMode="decimal"` (phone keypad); `main.tsx` also sets `inputmode` on any number input added later (MutationObserver), blurs a focused number input on mouse wheel and swallows ArrowUp/ArrowDown on it, so a typed figure never changes by a stray scroll or key. `index.css`: spinner arrows removed for all browsers, `font-variant-numeric: tabular-nums`, `input/select/textarea { min-width:0; max-width:100% }`, grid children `min-width:0` (no horizontal overflow), and `.input` is 16px under 640px (no iOS focus zoom). The QuickAdd `AccountForm` (mobile "Add customer") has the same `OpeningBalanceField` as `CustomerForm`.
