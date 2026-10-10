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

## Conventions

- Reply to the user in Roman Urdu/Hindi.
- Never put a model identifier in commits, PR text, code, or any pushed artifact — chat only.
