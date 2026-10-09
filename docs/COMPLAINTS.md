# Complaints: ticket, investigation, escalation, parent confirmation

Decided with the college on 9 October 2026. A family's complaint is never handled only on WhatsApp or by phone: it
becomes a ticket (a Case of type *complaint*) with a full history, an investigation record, an escalation path, a
resolution, the family's confirmation and an audit trail.

## The flow

```
Received → Under investigation → Findings recorded → (Escalated) → Resolved, parent confirmation pending
        → Closed, confirmed by parent      when the family says it is resolved
        → Reopened (and escalated)         when the family says it is not
```

| Step | Who | Where | What is recorded |
|---|---|---|---|
| Ticket created | Head of Admissions (any holder of `cases.add`) | CRM & Growth › Cases & Complaints › New case | Family, student, category, **what the family reported** (kept as received, never edited), when it happened, how it was received, priority, complaint against (employee, department or process), handling department, assignee |
| Investigation | Assignee, Academy Manager, Head of Admissions | Case › *1. Investigation finding* | **What the investigation verified**, separately from the complaint: confirmed / partly confirmed / not confirmed / inconclusive, and the verified severity (minor, moderate, serious, critical) |
| Collaboration | Anyone handling it | Case › *Comment or decision*, *Assign an action*, *Evidence* | Internal comments, **decisions** (highlighted in the history), actions as tasks with due dates and reminders until done, evidence files |
| Resolution | Assignee | Case › *2. Resolve* | What was done, root cause (from the *Complaint Root Causes* lookup), corrective and preventive action. A complaint cannot be resolved before a finding is recorded |
| Parent confirmation | Responsible person, or the family themselves in the portal | Case › *Parent confirmation*; Parent portal › Requests & Complaints | Date and time, who called, channel, the family's response in their words, satisfaction, feedback, agreed action, follow-up date, responsible person, recording (link or file) and transcript where permitted |

Resolving a complaint with a family moves it to **Resolved, parent confirmation pending** and creates a follow-up
task *"Confirm with the family that CS-… is resolved"* for the responsible person, due after the configured number
of days. The family is told and can answer in the portal. The answer decides what happens:

| The family | Result |
|---|---|
| Satisfied | **Closed, confirmed by parent**; the follow-up is completed |
| Partly satisfied | **Reopened** for the handling team |
| Not satisfied | **Reopened and escalated** one step up the ladder |
| Could not be reached | Stays pending; the attempt is counted and another follow-up is scheduled for tomorrow. After the configured number of attempts a manager may close it as **Closed, parent unreachable**, with a reason |

An agreed action with a follow-up date becomes a task. Overdue tasks are reminded and escalated by the existing
task job (`jobs_ops.escalate_overdue_tasks`), so the responsible person is chased until it is done.

The plain status form cannot close a complaint behind the family's back, and approving a complaint on the
Client Requests › Complaints list starts the same confirmation loop.

## Escalation ladder

Shown on CRM & Growth › Complaint Settings (`cases.configure`) and changed only by a superuser, because the ladder decides
who sees every complaint and its own members hold `cases.configure`. Default:

1. Academy Manager (PDM)
2. Head of Admissions
3. HOD People & Culture (HR)
4. Super Admin / CEO

A complaint climbs one step at a time when someone escalates it, when its response deadline passes
(`crm.case_sla_monitor`, automatically), or when the family is not satisfied. A step nobody holds is skipped; when
several people hold a step the one with the fewest open escalations receives it. Every escalation notifies the
person, raises a management risk alert and is written to the audit log.

## Who can see a complaint

| Person | Sees |
|---|---|
| The person a complaint is about (named employee, or the teacher of a complaint) | **Never**, whatever their role. They also cannot be assigned it, escalated it or given actions on it |
| Ladder roles and holders of `cases.assign` (Academy Manager, Head of Admissions, HR head, academic and QA heads, managers, the CEO) | Every complaint not about themselves |
| Anyone else with `cases.view` | Cases they raised, are assigned, were escalated or recorded the findings; cases of their own department and (supervisors) of the teachers they supervise, **unless the complaint names a member of staff** |
| Families | Their own cases in the parent portal, without internal comments or the investigation finding |

A case someone may not see answers 404, so its existence is not disclosed. Opening a confidential complaint
(one that names a member of staff) is written to the audit log. The same rule applies to the case lists on the
student, family and teacher pages and to the Client Requests › Complaints list.

Evidence files are stored outside the shared upload folders (`storage/case_evidence`, refused by the `/storage`
route) and are served only by `/cases/{id}/evidence/{file}`, which checks the case is visible and records the
download. Accepted: PDF, images, audio, video, Word and text, up to 25 MB.

## Complaint history on staff profiles

HR › Employees › *employee* › **Complaints**, and Online Academics › Teachers › *teacher* › **Complaints**, show
totals, open, awaiting the family, closed (and how many the family confirmed), reopened, repeat, categories, verified
severity, findings confirmed, average resolution time, the last six months and the list of complaints.

It needs the **`complaint_history.view`** permission granted by name. Roles that read everything for oversight
(`*.view`, for example the External Auditor) do not get it, nor does the person themselves. Every read is written
to the audit log (module `complaint_history`). Default holders: Super Admin, HOD People & Culture, HOD Academics,
HOD QA, Manager, Academy Manager, Head of Admissions.

## Complaint intelligence

CRM & Growth › Complaint Intelligence (`/cases/trends`), limited to the complaints the viewer may see:

- a management summary written from the figures (AI gateway module `complaint_summary`; without a live provider a
  deterministic summary is produced from the same numbers, and nothing is acted on automatically)
- received and closed by month, categories rising this month
- **needs management attention**: open complaints scored 0–100 for escalation risk, with the reasons (priority,
  verified severity, reopened, repeat from the same family, deadline missed or close, other complaints about the
  same person in six months)
- people with recurring complaints, by department, root causes, verified severity, overdue confirmation calls

A complaint from a family that complained about the same person, teacher or category in the previous 90 days is
marked as a **repeat** and linked to the earlier one.

## Roles added for this

| Role | Slug | Purpose |
|---|---|---|
| Head of Admissions | `head_of_admissions` | Takes the family's complaint and opens the ticket; admissions, leads, trials, referrals; step 2 of the ladder |
| Academy Manager (PDM) | `academy_manager` | Runs the academic side of a complaint; students, classes, evaluations, retention; step 1 of the ladder |

Both are management roles. They reach production through the role-defaults sync, and are given to real staff on
HR › Users & Access › Users. Local development databases also get `admissions@oqc.local` and `academy@oqc.local`;
deployed instances do not, because demo passwords are rotated only when the deployment credentials change.

## Data

- `cases`: `incident_date`, `against_employee_id`, `against_department_id`, `against_process`,
  `investigation_finding`, `finding_outcome`, `severity`, `findings_by_id`, `findings_at`, `root_cause_category`,
  `corrective_action`, `preventive_action`, `escalation_level`, `confirmation_due_at`, `confirmation_attempts`,
  `confirmed_by_parent`, `closure_type`, `reopen_count`, `reopened_at`, `repeat_of_id`
- `case_comments.kind`: comment, decision, finding, status, contact, system
- `parent_contacts`: one conversation with a family (used for complaint confirmation now; academic, attendance and
  progress calls will use the same record)
- Migration `9cde39c9bd43` also records existing teacher complaints against the teacher's employee record

Code: `app/services/complaints.py` (lifecycle, visibility, ladder, history, intelligence), `app/services/crm.py`
(`open_case`, `change_case_status`, `escalate_case`, `case_sla_monitor`), `app/web/cases.py`,
`app/templates/cases/`, tests in `tests/test_complaints_lifecycle.py`.

## Not included yet (needs an outside service)

- **Recording calls automatically.** Staff attach a recording file or link today. Automatic capture needs the
  calling platform the college uses (GHL's LC Phone, Twilio, Aircall or Zoom Phone) and a consent notice on calls.
- **Transcripts.** Typed or pasted today; automatic transcription needs a speech-to-text provider (Whisper API,
  Deepgram or AssemblyAI).
- **AI-written summaries.** Set `AI_PROVIDER` and `AI_API_KEY`; the deterministic summary is used until then.
