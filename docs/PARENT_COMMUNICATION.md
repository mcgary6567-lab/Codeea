# Parent communication and referral follow-up

Built 9 October 2026 (items 12–13 of the integrated ERP brief). Every conversation with a family is recorded, whatever
it is about, so there is a reliable record of what was discussed, what the family said and what was agreed. What a
conversation identifies is followed up until it is done, and a referral mentioned in passing is never lost.

Where: Online Academics › Client Management › **Parent Contact Log** (`/parent-contacts`); *Record a conversation*
from a family's record (Communication log tab), from a student's Academic Journey, or from the log.

## What is recorded

| Field | Notes |
|---|---|
| Family and student | A conversation can be about one student or the whole family |
| About | Academic progress, attendance, performance, complaint, billing, schedule / timing, referral, general (complaint confirmation calls from docs/COMPLAINTS.md appear here too) |
| How, who called, when, length | Phone, WhatsApp, video, meeting, email; we called them or they called us |
| Summary, what the family said | The family's words kept as said |
| How the family felt | Positive, neutral, concerned, upset |
| Issue identified, category | Academic, attendance, behaviour, teacher, schedule, billing, technical, other |
| Agreed action, follow-up date, responsible person | Defaults: two days, the person recording |
| Recording and transcript | A file (audio, video, PDF or text, up to 25 MB) or a link from the calling system, and the transcript, **only where legally permitted and the family has been told**. Files are kept outside the shared upload folders and opened only through the conversation, which records each opening |
| Referral | The referred person's name, phone or email, relation, note |

Who can use it (`parent_contacts`): Academy Manager, Head of Admissions, Manager and the academic head in full;
supervisors view, record and update; billing representatives, lead closers and academic coordinators view and
record; the QA head views. A conversation about a complaint follows the complaint's confidentiality rules.

## Issue → action → responsible person → due date → reminder → completion

An issue or an agreed action creates a **follow-up task** for the responsible person, due on the follow-up date. A
teacher or behaviour issue, or an upset family, makes it high priority. The job `contacts_follow_up_reminders`
reminds the responsible person **once a day for every follow-up that is due or overdue until it is done**. It covers
follow-ups from conversations, teacher recommendations (docs/STUDENT_JOURNEY.md) and referrals. The existing overdue
job escalates anything more than a day late. *Mark done* asks what was done and keeps it on the task.

**Raise as a complaint** turns the issue into a complaint ticket in one step, with the family's words as the
complaint. The complaint workflow (investigation, escalation ladder, family confirmation) takes over.

## Referrals

Flow: referral mentioned → referral created → referred lead → sign-up → reward eligibility → reward approval →
reward applied.

| The family gave | What happens |
|---|---|
| A name and a phone or email | A lead is created (source: Referral, carrying the family's referral code) and linked on the referral ledger. It is assigned to a closer with a *Call referred family* task due tomorrow. WhatsApp opt-in is off for the referred person, so no automatic message goes out before someone has spoken to them |
| Nothing yet | A referral **ask** is recorded on the ledger with a *Collect referral details* task for the responsible person, due in two days |

Converting the lead into a family marks the referral **signed up** (existing behaviour). The job
`contacts_referral_follow_ups` then:

- marks a signed-up referral **eligible** once the referred family has an active subscription, and creates an
  *Approve referral credit* task for the Head of Admissions;
- reminds the owner once a week about asks whose details were never collected.

**Rewards are account credit only, for both families** (SRS Module 43, confirmed 9 October 2026). The credit is
**never posted automatically**: a person approves it on CRM › Ambassadors (*Qualify*), which posts it to both
ledgers. The referral ledger shows each referral's stage, including *Eligible: credit to approve*.

## Where conversations appear

- The family's record › Communication log: every conversation, newest first.
- The student's Academic Journey: on the timeline (family kind) and in the monthly summary (*Parent
  communication*).
- The automation engine: event `parent.contacted`, so workflows can react (for example, a thank-you message after a
  positive call).

## Not included yet (needs an outside service)

- **Recording calls automatically.** Staff attach a file or a link today. Capturing calls automatically needs the
  calling platform the college uses (GHL's LC Phone, Twilio, Aircall or Zoom Phone) and a consent notice.
- **Transcription.** Typed or pasted today; automatic transcripts need a speech-to-text provider.

## Data and code

Migration `6a50058576b1`: on `parent_contacts` the columns `direction`, `duration_minutes`, `sentiment`, `issue`,
`issue_category`, `referral_id`; on `referrals` the column `eligible_at`.

Code: `app/services/contacts.py`, `app/services/jobs_contacts.py`, `app/web/parent_contacts.py`,
`app/templates/parent_contacts/`. Tests: `tests/test_parent_communication.py`.
