# Functional audit — Accounts and Configuration areas of the existing ERP

Source: the college's live portal (Oracle APEX, confido360), audited 14 September 2026 signed in as the CEO.
Only page structure, field labels, filters and actions are recorded. No staff or family data is copied.

Third companion to `AUDIT_ACADEMICS.md` and `AUDIT_HUMAN_RESOURCE.md`.

> **Navigating their portal.** Every Home card carries a menu id in the address, for example
> `hr-home?p14_id=63` and `accounts-home?p16_id=62`. The card list on each area page is a report filtered by
> that id, so opening the same page without it renders the page frame with an empty menu. Typing an address
> that does not exist ends the session and returns you to the sign-in page.

> **Walked again on 17 September 2026** — see `AUDIT_PARITY_WALK_2026-09-17.md`. Configuration has a tenth card, Confido Agents (licences, devices, screenshots of the vendor's desktop recording agent). Built as the data and the agent's two API calls; the agent program itself is theirs.

---

## Accounts

Three groups, thirteen pages. Their accounting is a conventional double-entry ledger.

| Group | Pages |
|---|---|
| Setup | Accounts Heads · Chart of Accounts · Accounts Tree View |
| Transactions | Journal Voucher · Payment Voucher · Receipt Voucher |
| Reports | Ledger Report · Trial Balance Report · Income Statement · Balance Sheet · Payables Summary · Account Wise Summary · Approved Advances |

**What this tells us.** They post money through three voucher types rather than the single journal entry we
use, and they expect a trial balance and an account-wise summary alongside the income statement and balance
sheet. Approved Advances appears here as well as in Human Resource, so a staff advance is visible to whoever
is closing the books.

## Configuration

Nine pages, no sub-grouping.

| Page | Contents |
|---|---|
| Currency Rates | **Add Manual Currency Rate**, plus an automatic **ERP Currency Rates** feed |
| Roles | Roles grouped by Application (Accounts, Billing Management, Client Portal, Configuration, Human Resource, Online Academics). Columns: Role · Description · Status · **Assign Users** |
| Lookups | Every configurable value list, grouped by App. Columns: ID · Description · Status · Sort No |
| Setup (Branch Properties) | Settings with tabs Show All · General · HR · Academics · Accounts. Each row is a named setting with a description, a value and its own Save |
| Notification Templates | Create; message templates |
| Support Ticket | Tickets raised to the software vendor |
| WhatsApp Numbers | Connected WhatsApp senders |
| OTP Configuration | Tabs **User Wise** and **Setup**; per-user token status |
| Payment Gateways | ID · Payment Gateway Name · Default Transaction Fee % · Gateway Company · Status |

### Branch Properties — the settings they actually keep

Named settings seen, each with a description and a value:

- Secret Pin Code
- HR → Attendance Login Time Relaxation — grace before a late-arrival fine applies
- HR → Attendance Logout Time Relaxation — the same for leaving early
- HR → Employee Basic Detail → Default CTC Value — default cost to company, for estimating employee expense
- Online Academics → Advance Invoice Generation Days — how many days ahead invoices are raised
- Online Registration → Form Instructions
- Online Registration → Form Terms and Conditions
- ERP HTML Reports Headers Picture

### Lookups — the value lists they configure

Grouped by application. Examples seen: Acc Financial Year · HR BPO Sales Team List · HR How Came To Us
(Social Media, Sales Representative, Google, Others) · EMP Document Type · HR Employees Leaving Reasons
(Terminated, Resigned, Contract completed, Marriage) · HR Complaints Types (Admin, Staff, HR) ·
HR Allowance Types (DA, Home Allowance) · HR Employee Requests · HR Designation List.

### WhatsApp Numbers — columns

API Status · Description · WhatsApp Number · Live Mode · Last Message Sent Time · Interval In Seconds ·
Messages Per Cycle · Status · Qr Code. Two senders are configured, one general and one for academics, each
with its own throttle (an interval in seconds and a number of messages per cycle) and a QR code to reconnect.

### Support Tickets — columns

Subject · Department · Message · WhatsApp No · Ticket Type · Module · Priority · Status · Developer Remarks.
Ticket Type includes New Feature; Module names the area of the system; Priority runs to Very Urgent.

## Gap list against the new platform

| Area | Status | What is missing |
|---|---|---|
| Chart of accounts | **Have** | Ours is richer: accounts, journal, expenses, budgets, aging, cash flow, forecast and period close |
| Voucher entry | **Missing** | They post through Journal, Payment and Receipt Vouchers; we post journal entries only, with no payment or receipt voucher form |
| Accounts Tree View | **Missing** | No hierarchical view of the chart of accounts |
| Trial Balance | **Missing** | Not built; we have the profit and loss and the ledger but no trial balance |
| Balance Sheet | **Partial** | We produce a profit and loss and a cash flow; no balance sheet |
| Payables Summary | **Partial** | We have a payables page; theirs is a summary report |
| Account Wise Summary | **Missing** | No per-account summary report |
| Currency rates | **Partial** | We store rates and history; no automatic feed and no explicit "add manual rate" screen |
| Roles | **Have** | Ours are richer (20 roles, per-action permissions). Theirs groups roles by application and assigns users from the role; ours assigns roles from the user |
| Lookups | **Missing** | No single place to edit every configurable value list. Ours are hard-coded constants or per-module tables |
| Branch Properties | **Partial** | We have a settings page; theirs carries named operational settings we do not have, notably attendance grace periods, default cost to company and how many days ahead invoices are raised |
| Notification templates | **Have** | Ours exist and are seeded |
| Support tickets | **Missing** | No channel for raising a request to whoever maintains the software |
| WhatsApp numbers | **Partial** | We have one WhatsApp integration setting; theirs supports several senders, each with a send throttle, live mode and QR reconnection |
| OTP configuration | **Missing** | No one-time-password setup, per user or global |
| Payment gateways | **Partial** | We record a gateway on a payment and have beneficiary accounts; no gateway catalogue with a default transaction fee |

## Recommended build order

1. **Lookups and Branch Properties.** Everything else reads from them, and the attendance grace periods and
   advance invoice days change behaviour we have already built.
2. **Vouchers and the missing accounting reports** — payment and receipt vouchers, trial balance, balance
   sheet, account-wise summary, tree view.
3. **Payment gateway catalogue and WhatsApp senders**, which make the billing and messaging work configurable
   rather than fixed.
4. **Support tickets and OTP**, which are self-contained and can follow.

---

## Status after the build

Everything in the gap list above is now built, seeded and tested.

**Accounts.** Accounts Heads, Chart of Accounts (type, head, postable and search filters, with head, postable
and opening-balance columns) and Accounts Tree View. Journal, Payment and Receipt Vouchers share one entry:
a voucher is a journal entry with a type and the party and payment details a payment or receipt needs, so the
ledger, trial balance and the statements all read one set of lines. Reports: Ledger, Trial Balance, Income
Statement, Balance Sheet, Payables Summary, Account Wise Summary and Approved Advances, each with a date
range, a print view and CSV. Our own Profit and Loss, Cash Flow, Receivables Aging, Budgets, Period Close,
Payables Detail, Consolidated Statement and Forecast are kept and are now on the launchpad.

Cancelling a posted voucher posts a mirror contra voucher of the same type referencing the original and marks
the original cancelled, so the two net to zero and the ledger stays balanced. Cancelling a draft writes no
reversal. A receipt against a family with an invoice selected goes through the billing service and the journal
it posts is adopted as the receipt voucher, so nothing is posted twice.

On the seeded data the trial balance balances and so does the balance sheet; the seed asserts both and says so.

**Configuration.** Lookups (14 lists, 85 values) with a single service every module reads through, Branch
Properties with the tabs they use and a masked, audited reveal for secrets, Currency Rates with a manual entry
and a rate feed, Payment Gateways, WhatsApp Numbers with a per-sender send throttle, Support Tickets, OTP
Configuration, and a Roles page grouped by application alongside our own permission editor.

### Deliberate differences from the ERP

| Theirs | Ours | Why |
|---|---|---|
| Three separate voucher ledgers | One journal entry carrying a voucher type | A single set of lines means the trial balance and the statements cannot disagree with the vouchers |
| Cancelling edits the original | Cancelling posts a mirror reversal | The original stays readable and the audit trail is complete |
| Secrets shown in the settings list | Masked, with an audited reveal | A settings page is read by more people than the secret is meant for |
| Rate feed live | Rate feed simulated, and says so on screen | We have no contract with a rate provider; the screen does not pretend otherwise |
