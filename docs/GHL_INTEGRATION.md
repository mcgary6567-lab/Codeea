# GoHighLevel ↔ ERP integration

Built 9 October 2026 (item 14 of the integrated ERP brief). GHL and the ERP are one journey, not two systems:

```
GHL lead → GHL automation → qualification → counselling → trial → admission → ERP family and student
→ academics, attendance, assessment, parent follow-up → referral → GHL follow-up
```

| GHL owns | The ERP owns |
|---|---|
| Lead generation, marketing, the sales pipeline, nurturing, its own automations and messaging | Families and students (the master records), teachers, classes, attendance, assessments, QA, complaints, follow-ups, history |

Where: Configuration › **GoHighLevel Sync** (`/admin/integrations/ghl/sync`) for the log and conflicts;
Configuration › Integration Hub › GoHighLevel for the settings. Holders of `integrations.view` see it;
`integrations.configure` can turn sync on or off, retry and resolve conflicts.

## Turning it on

Sync is **off until an administrator turns it on** (a rationale is recorded). This is deliberate: the demo data seeded
on every deploy must never reach a real GHL account, and sending families to GHL is a decision, not a default.

1. Set `GHL_API_KEY` (a GHL private integration token for the location) in the server environment, and
   `GHL_WEBHOOK_SECRET` (or the webhook secret on the integration page). Without the key every call is
   **simulated**: the queue, the log and the ids behave the same and nothing leaves the server.
2. On the GoHighLevel integration page enter the **Location ID**, the **Pipeline ID**, the **Won / Lost stage
   IDs**, and optionally a **stage map** (JSON, ERP stage → GHL stage id), for example
   `{"trial_scheduled": "<id>", "negotiation": "<id>", "payment_pending": "<id>"}`.
3. In GHL create the contact custom fields `erp_lead_code`, `erp_stage`, `erp_client_code`, `erp_status`,
   `erp_students`, `erp_courses`, `erp_teachers` (or set "Send ERP codes as custom fields" to no).
4. In GHL workflows add two webhook actions, signed with the webhook secret (`X-Webhook-Token`):
   contact created / updated → `https://<site>/api/v1/webhooks/ghl`, opportunity stage changed →
   `https://<site>/api/v1/webhooks/ghl/opportunity` (with `contact_id` and the stage id or name).
5. Turn sync on in the console.

## From the ERP to GHL

Every lifecycle event the ERP already raises queues the matching GHL changes. Nothing is typed twice.

| ERP event | GHL change |
|---|---|
| New lead (website, registration, WhatsApp, manual, referral) | Contact upserted with the ERP lead code and stage |
| Lead moves stage | Opportunity moved to the mapped stage (skipped and logged when no stage is mapped) |
| Lead lost | Opportunity lost, tag `erp:lost` |
| Lead enrolled as a family | Contact upserted with the family code, students, courses and teachers; tag `erp:enrolled`; opportunity won |
| Trial booked / attended / no-show | Tags `trial:scheduled` / `trial:attended` / `trial:no-show` |
| First payment | Tag `erp:paying` |
| Subscription frozen / resumed / cancelled | Tag `erp:frozen` added or removed, `erp:cancelled` |
| Course completed | Tag `erp:course-completed` |
| Student at risk | Tag `erp:at-risk` on the family |
| Feedback | Tag `erp:promoter` (score 9–10) or `erp:detractor` |
| Any ERP tag added or removed (CRM › Tags, workflows) | The same tag added or removed in GHL |
| Referral mentioned | Family tagged `erp:ambassador`; the referred lead tagged `referral:referred` with a note |
| Referral credit applied | Tag `referral:credited` |
| Complaint opened / closed | Tag `erp:complaint-open`, then `erp:complaint-resolved` (the complaint's content is never sent) |

## The queue, retries and the log

Every change is a row in the sync log with its status:

| Status | Meaning |
|---|---|
| Pending | Queued; the worker (`ghl_sync_worker`, every two minutes, up to 40 per run within GHL's rate limit) sends it |
| Sent | Accepted by GHL (or simulated) |
| Retrying | Failed; tried again after 2, 5, 15, 60, 240 and 720 minutes |
| Failed for good | Failed six times: a high-priority integration alert is raised and the system administrators are told. *Retry* (one, or everything that failed) puts it back in the queue |
| Skipped | Nothing to do (for example a stage with no GHL stage mapped) |
| Received | A change that came from GHL |
| Conflict / resolved | See below |

An identical change already waiting is never queued twice. A sync problem never blocks the business action that
caused it: the family still enrols, and the change waits in the queue.

## Identifiers and duplicates

- The GHL contact id is stored on the lead and carried to the family when the lead enrols. Every later change uses
  it, so a person is one contact in GHL.
- GHL receives the ERP codes (lead, family, students) as custom fields, so GHL users can always find the ERP record.
- GHL's upsert matches by email and phone; inbound contacts are matched by GHL id, then phone or email, before a
  new lead is created.
- *Sync to GHL now* on a lead or a family pushes it immediately and records the result in the log.

## From GHL to the ERP, and conflicts

| GHL sends | The ERP does |
|---|---|
| A contact for a **lead** | Updates the lead's name, email and phone: GHL owns a lead's contact details |
| A contact for a lead that has **enrolled** | Changes nothing. If GHL's name, email or phone differ from the family record, a **conflict** is logged with both values |
| An opportunity stage change | Moves the lead to the matching ERP stage (by stage id, or by the stage names on the console). Not echoed back to GHL. Ignored for an enrolled lead, an unknown contact or an unmapped stage, with the reason logged |

A conflict is resolved on the console: *Keep the ERP values* (GHL is updated from the family record) or *Take the
GHL values* (the family record is updated). Either way it is written to the audit log.

## Data and code

Migration `741334a89d1d`: `ghl_sync_jobs` (the queue, retry state and log).

Code: `app/services/ghl_sync.py` (event map, queue, worker, API client, inbound handling, conflicts),
`app/services/jobs_ghl.py`, `app/web/ghl_sync.py`, `app/templates/admin/ghl_sync.html`; hooks in
`app/services/automation.py` (`emit`, `remove_tag`) and `app/services/integrations.py` (`emit_event`); webhooks in
`app/api/webhooks.py`. Tests: `tests/test_ghl_sync.py`.

## Needed from the college

GHL API key (private integration token), Location ID, Pipeline ID and stage IDs, the custom fields above, and the two
webhook actions in GHL workflows. Until then everything runs in simulation.
