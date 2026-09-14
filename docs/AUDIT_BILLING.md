# Functional audit — Billing Management area of the existing ERP

Source: the college's live portal (Oracle APEX, confido360 Release 4.07.11), audited 14 September 2026
signed in as the CEO. Only page structure, field labels, filters and actions are recorded. No family, lead or
staff data is copied.

Fourth companion to `AUDIT_ACADEMICS.md`, `AUDIT_HUMAN_RESOURCE.md` and `AUDIT_ACCOUNTS_CONFIG.md`.

> The Academics audit noted Billing Management as "a separate area, not part of this audit". This is that area.
> It is reached from the Home card **Billing Management**, whose address is `online-academic-billing?p275_id=161`.
> The three group pages below carry no menu id of their own.

---

## Shape

Three groups, ten pages.

| Group | Pages (ERP alias) |
|---|---|
| Client Management | Clients List (`client-list1`) · Clients Financial Summary (`clients-financial-summary`) · Leads List (`leads-list`) · Verify Leads (`verify-leads`) |
| Billing | Invoices (`invoice-list`) · Receipt (`receipts1`) · Ledger Additions (`ledger-additions1`) · Client Ledger (`client-ledger1`) |
| Configurations | Billing Groups (`client-groups`) · Lead Closers (`lead-closers`) |

The four pages in the Billing group are the same four already recorded in `AUDIT_ACADEMICS.md` §3.7, reached
here through a second door. The six pages in Client Management and Configurations are new to this audit.

## Clients Financial Summary

The money view of every family, and the page this area exists for.

**Search Options** — Search · From Date · To Date · Shift (Morning · Night) · Currency (30 codes) ·
Status (Black List · Drop Out · On-Leave · Pass Out · Regular · Trial) · Go. Nothing loads until a filter is
applied: the page says "Apply Filters to show data."

**Saved reports** — Primary Report · Receivables · All Dues · a per-representative receivables report. One
saved filter seen: `Exceeded > 0`.

**Action** — **Notify**, which messages the families selected by the row checkboxes.

**Columns** (21, with a select-all checkbox in the first):
ID · Client Name · Status · Reg Date · Shift Name · B.R · Students · Regular Subscriptions · Payment Day ·
Currency · Currency Rate · Balance · Balance Limit · Exceeded · Pending Amount · Received Amount ·
Invoices Total · Billing Remarks · Last Payment · Fee Recurrence.

**What this tells us.** They carry a **balance limit** per family and a derived **Exceeded** figure, and the
page exists to find who is over it and message them. Payment Day and Fee Recurrence sit beside the balance, so
whoever is chasing money can see when the next invoice is due without opening the subscription.

## Leads List

Columns: ID · Guardian Name · Mobile No · Whatsapp No · Shift · Referred By · Country · State · City ·
Street Address · Skype · Email · Zip Code · How Came To Us · Extra Query · Followup Date · Created At · Status.

Referred By is an employee, shown as `code - name`. How Came To Us is a lookup (Social Media, Sales
Representative, Google, Others).

## Verify Leads

The queue between a raw lead and a family. Columns: ID · Guardian Name · Client Code · Converted At ·
Mobile No · Whatsapp No · Shift · Referred By · Country · State · City · Email · How Came To Us · Zip Code ·
Followup Date · Status · Extra Query · Create At.

A converted row carries the **Client Code** it became and the date it converted. Statuses seen include
`Converted` and a numbered `Forward to Verifier` step. Leads arriving from their external marketing tool carry
a source note and that tool's own id in the query field, and have no contact details filled in yet.

**What this tells us.** A lead is verified before it becomes a family, the conversion records which client code
it became, and leads arrive both by hand and from an external system.

## Billing Groups (`client-groups`)

Create. Columns: ID · Group Name · Sudo Name · Representative Name · Status (Active / In-Active).

A billing group is a named book of families with one representative answerable for it. Sudo Name is the short
name shown on reports.

## Lead Closers

Create. Columns: ID · Closer Name · Sudo Name · Representative Name · Status (Active / In-Active).

The same shape as billing groups, for the people who close leads.

## Gap list against the new platform

| Area | Status | What is missing |
|---|---|---|
| Invoices, receipts, ledger additions, client ledger | **Have** | Built in the Academics parity pass; both doors now lead to them |
| Clients Financial Summary | **Missing** | No page puts balance, balance limit, exceeded, pending, received, invoices total, last payment, payment day and fee recurrence on one line per family |
| Balance limit and Exceeded | **Missing** | We hold no credit limit per family and derive no over-limit figure |
| Notify from the summary | **Partial** | We have WhatsApp and email sending and notification templates; no "select families and chase them" action |
| Leads List | **Have** | Ours is richer (stages, scoring, campaigns, sequences, inbox) |
| Verify Leads | **Partial** | We convert a lead to a family, but there is no verification queue and the lead does not record the client code it became |
| Billing Groups | **Partial** | We hold an academic group on the client; no billing group with its own representative and status |
| Lead Closers | **Partial** | We have a Lead Closer role; no catalogue of closers with a short name and status |

## Build order

1. **Billing Groups and Lead Closers**, because the summary and the lead queue both read from them.
2. **Balance limit on the family**, then **Clients Financial Summary** over it.
3. **Notify** from the summary, reusing the existing notification templates and WhatsApp senders.
4. **Verify Leads**, wiring the existing lead conversion so it records the client code and the verifier.
