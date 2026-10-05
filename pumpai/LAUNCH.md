# PumpAI — go-live checklist (selling to pumps)

PumpAI is installed **once per pump business, on its own server** (see `INSTALL.md`). The Vercel site is a
**showroom demo only** — its data resets; never put a real pump on it.

## 1. Before the first paying customer (one time, your company)

| # | Item | Why / how |
|---|---|---|
| 1 | **Server plan** | One small VPS per pump: 2 vCPU, 2–4 GB RAM, 40 GB SSD, Ubuntu 22.04/24.04 (DigitalOcean, Hetzner, Vultr or a Pakistani host). Price it into the monthly fee. |
| 2 | **Domain** | Buy one brand domain and give each pump a sub-domain, e.g. `almadina.yourbrand.pk` → that pump's server (DNS A record). HTTPS is automatic. |
| 3 | **Off-site backups** | Make one Google Drive / S3 / Backblaze account for backups. On each server set `BACKUP_COPY_CMD` (e.g. `rclone copy "$BACKUP_FILE" gdrive:pumpai/<pump>`). Check "Settings → Backups" shows the off-site copy OK. A backup only on the same server is lost with the server. |
| 4 | **Uptime watch** | Add each pump's `https://<pump>/api/health` to a free monitor (UptimeRobot / Better Stack) that WhatsApps / emails you if it goes down. It also reports if the database is not reachable. |
| 5 | **WhatsApp Business Platform** | Meta Business verification, a phone number per pump (or per your company), approved message templates (bills, khata reminders, payment receipts), the **App Secret** and webhook (`https://<pump>/webhooks/whatsapp`). Meta charges per conversation — include it or pass it on. Without WhatsApp the app still works; messages are just not sent. |
| 6 | **Claude AI key (optional)** | For reading photos (meters, invoices, cheques) and Urdu/English chat. Create a key with a monthly spend limit. Without it the built-in Urdu/English rule engine handles voice commands and questions. |
| 7 | **Legal** | Have a lawyer read `/privacy` and `/terms` (generated with each pump's name) and your customer contract: who owns the data (the pump), support hours, what happens on cancellation (you hand over the backup). Check whether the pump must also report sales to FBR's POS system — PumpAI's receipts and tax report help, but FBR integration is not built in. |
| 8 | **Support details** | Set `VENDOR_NAME`, `VENDOR_PHONE`, `VENDOR_EMAIL` on every install — the owner sees them in Settings → About. |
| 9 | **Update routine** | Test each new version on the demo first, then run `update.sh` on each pump at night. It backs up first and rolls back by itself if the new version does not start. |

## 2. Installing a pump (about 30 minutes)

1. Point the pump's sub-domain to its new server.
2. `sudo bash install.sh --domain <pump.domain> --email <your@mail>` — note the **setup code** it prints.
3. Set `BACKUP_COPY_CMD` and `VENDOR_*` in `/etc/pumpai.env`, `sudo systemctl restart pumpai`.
4. With the owner: open the address, run the setup wizard (business, logo, owner password, stations & tanks, prices).
5. Add the pump to your uptime monitor.

## 3. Day one with the owner (opening balances — do not skip)

The books are only as right as the opening figures. Enter, in this order:

1. **Bank accounts** (Cash & bank → Add bank account) with the balance on the statement today.
2. **Count the office cash** (Cashier → Count) — the cash book starts from this count.
3. **Khata customers** with their current balance and credit limit.
4. **Wholesale clients** with rates and current due; **suppliers** with what the pump owes them.
5. **Staff** with roles, salaries, PINs and any advance already given.
6. **Cheques in hand / issued** into the cheque register.
7. Today's **dip** for every tank.
8. Turn on the customer automations the owner wants (khata reminders, monthly bills…) after showing the message text.

## 4. Pilot

Run the first pump for **2–4 weeks next to its old registers**. Every evening compare: the day book's closing cash
with the counted cash, bank balances with the bank app, khata totals with the register. Fix habits (handover at the
cash counter, cheques into the register) before the old registers are dropped.

## 5. What is in the box (for your sales sheet)

- Sales / POS with offline queue, shifts with meter readings, cash handover to the cashier, shortages on the salesman's account.
- Khata with limits, holds, bills and a private PIN-protected customer page; wholesale supply, tanker trips, order book, recovery, cheques.
- Cashier desk: receipts and payment vouchers, cheque register (received & issued, post-dated), salesmen's cash, day book, banks.
- Bank accounts with Pakistani bank list, balances, statements, transfers, POS digital money into the right account.
- Stock, deliveries, shortage claims, dip charts, stock register; expenses with approvals; suppliers; staff salary, loans, slips, attendance with selfie.
- Owner dashboard: where the money is, what is owed both ways, today across every module, this month's profit; reports, P&L, balance sheet, ledger export (Excel / Tally).
- Voice and typed commands in Urdu, Roman Urdu and English (sale, expense, khata, wholesale), WhatsApp CRM, AI insights.
- Roles: owner, manager, salesman, wholesale officer, cashier — every right can be ticked on / off by the owner.
- Security: per-install secrets, setup code, password and PIN lockouts, sessions that end on password change, signed WhatsApp webhook, audit log of every change, nightly backups with off-site copy.

## Known limits (be honest with customers)

- One pump business per install (several stations of one owner are fine).
- Two accountant tables (Reports, Accounts) scroll sideways on a phone.
- WhatsApp and AI need the pump's own Meta / Anthropic accounts and have running costs.
