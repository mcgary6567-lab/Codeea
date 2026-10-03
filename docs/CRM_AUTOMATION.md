# CRM automation — workflows, tags and the pipeline plan

Source: a customer's GoHighLevel rollout tracker (`GHL-rollout-tracker-v1.0.xlsx`, October 2026): seven sprints,
twenty numbered automations (AUTO-001 … AUTO-020), three pipelines spelled out stage by stage, a tag taxonomy,
and KPI targets. This document records what of it the platform does, how, and what still needs an outside
account.

## What was already there

Leads with a seven-stage pipeline, lead sources and activities, Verify Leads, lead closers; a sequence engine
delivering timed WhatsApp follow-ups every five minutes; WhatsApp Cloud API and SMTP adapters with message
templates; a shared inbox; online registration, trial booking and conversion; public survey links with NPS
that opens a case on a low score; ambassador invites; weekly retention risk scoring; case SLA monitoring;
billing jobs for invoicing, overdue reminders, failed payments, auto-renew and auto-unfreeze; an integration
hub with slots for Meta Ads, Google Ads, GHL, n8n and a payment gateway.

## What the automation engine adds

`app/models/automation.py`, `app/services/automation.py`, pages under `/crm/automations` and `/crm/tags`.

- **Tags** on leads, families and students, in eight categories (source, market, status, course, temperature,
  consent, pipeline, other). Adding a tag is itself an event a workflow can start from.
- **Workflows**: a trigger, an optional filter, and an ordered list of steps. Step kinds: wait, wait until a
  date on the record (a booked demo, a freeze end), send WhatsApp, send email, send SMS, notify staff, add or
  remove a tag, move a lead's stage, create a task, enrol in a sequence, if/else (continue, stop or jump),
  outbound webhook, stop.
- **Runs**: one contact going through one workflow, with a log line per step and a reason whenever a step was
  skipped or the run stopped. Runs park at waits; the scheduler advances them every five minutes. A workflow
  marked run-once never starts twice for the same contact. Exit rules stop a run when the lead is won or lost,
  the family replies, or a status changes.
- **Events** come from the code that does the work, so nothing depends on polling: lead created, stage changed,
  lost, converted; trial booked, attended, no-show; payment received (and the first payment); subscription
  frozen, resumed, cancelled; course completed; survey answered; student at risk; student absent; tag added.
  Two daily detectors add lead.stale (no activity for 7 days) and student.inactive (no attended class for 14).
- **Consent**: WhatsApp needs the opt-in, email needs an address and no `unsubscribed` tag, SMS needs the
  `consent:sms` tag and a provider. Every skipped send says why in the run log.

## The catalogue (seeded, editable on the Automations page)

| Code | Workflow | Starts when | Plan item |
|---|---|---|---|
| AUTO-003 | New Lead Notification | lead created | S1-16 |
| AUTO-002 | Lead Magnet Delivery | tag src:website added | S3-01 |
| AUTO-005 | Demo Confirmation | trial booked | S3-03 |
| AUTO-006 | Pre-Demo Reminders (24h, 1h) | trial booked | S3-04 |
| AUTO-007 | Post-Demo Nurture, 5 touches | trial attended | S3-05 |
| AUTO-008 | Price Objection Handler | tag objection:price added | S3-06 |
| AUTO-009 | Scholarship Inquiry Handler | tag scholarship added | S3-07 |
| AUTO-010 | Lost Lead Reactivation (30 days) | lead lost | S3-08 |
| AUTO-011 | Enrollment Welcome, 7 messages | lead converted | S3-09 |
| AUTO-012 | Portal Credentials on First Payment | first payment | S3-10 |
| AUTO-013 | First Class Reminder | lead converted | S3-11 |
| AUTO-014 | No-Show Recovery | student absent | S3-12 |
| AUTO-015 | At-Risk Alert (14-day inactivity) | student inactive | S3-13 |
| AUTO-016 | Monthly Progress Check-In | lead converted | S3-14 |
| AUTO-017 | Completion Celebration + Next Course | course completed | S3-15 |
| AUTO-018 | Review Request (7 days) | course completed | S3-16 |
| AUTO-019 | Referral Trigger (NPS 9-10) | survey answered | S3-17 |
| AUTO-020 | Database Reactivation | lead stale | S3-18 |
| P1-S2 / S5 / S6 / S8 | Pipeline 1 stage workflows | lead moved to the stage | WorkFlows sheet |
| P2-FREEZE / P2-RESUME | Pipeline 2 freeze and resume | subscription frozen / resumed | WorkFlows sheet |
| P3-CANCEL / P3-ALUMNI | Pipeline 3 cancelled and alumni | subscription cancelled / course completed | WorkFlows sheet |
| COMP-CONSENT | Consent tags on enrolment | lead converted | S3-20 |

Pipeline 1 maps onto the lead stages: Form Submitted = new, Demo Booking Pending = contacted, Demo Scheduled =
trial scheduled, Demo Completed = trial done, Admission Review = negotiation, **Payment Pending = payment_pending
(new stage)**, Student Enrolled = won, Stale / Lost = lost.

## Still needs an outside account

| Plan item | What is missing | What the platform does meanwhile |
|---|---|---|
| AUTO-001 missed-call text-back | A telephony provider | Nothing; no call events exist |
| AUTO-004 UTM capture | Nothing: leads already carry source and campaign | Done |
| SMS steps, 10DLC | An SMS provider and US registration | SMS steps are logged as skipped |
| Meta lead forms and Conversion API | Meta app credentials | The Meta Ads integration slot is ready; leads can arrive by webhook |
| Stripe multi-currency checkout | Stripe keys | The payment gateway runs in simulation |
| Google review link | A Google Business profile | AUTO-018 sends the link set in Setup |

## Deliberate differences from the GoHighLevel plan

| Theirs | Ours | Why |
|---|---|---|
| Three separate pipelines with their own opportunities | One lead record moving through stages, plus the family and student records | A family is one record here; the stages are the pipeline |
| Fourteen smart lists | Tag filters on the lead, client and student lists | Same lists without a second place to maintain them |
| An AI employee answering enquiries | Not built | Out of scope for this pass |
