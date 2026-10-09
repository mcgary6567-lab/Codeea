# Needs Attention: the attention layer

Built 9 October 2026 (item 15 of the integrated ERP brief). AI is not a separate chatbot: a daily check reads across
students, teachers, academic progress, attendance, QA, complaints, parent communication, follow-ups and referrals,
and brings what needs a person to one place, following the principle

```
Detect → Analyse → Alert → Assign → (Investigate → Act → Follow up → Escalate → Resolve → Record → Learn → Improve)
```

Where: Operations › **Needs Attention** (`/attention`), and a banner on the student's Academic Journey.

## How it works

1. **Detect.** The job `attention_daily_check` runs once a day (and *Check now* on the page) over every active,
   trial and frozen student, every active family and every active teacher.
2. **Analyse.** Each finding is a rule with its evidence, so staff can always see why an item exists (below). A
   student, family or teacher with enough findings gets **one** item, scored and graded high (≥ 60), medium (≥ 30)
   or low.
3. **Alert.** The item carries a one-sentence summary. With an AI provider configured (`AI_PROVIDER`,
   `AI_API_KEY`) the AI gateway writes it from the findings; without one it is assembled from the same findings.
   The page shows which. For example: *"Attendance has declined for two consecutive months (100% → 80% → 50%),
   frequent absences (5 absences in the last 30 days), and follow-ups are overdue (1 overdue follow-up for this
   student or family)."*
4. **Assign.** Recommended actions come with an owner, a due date and a priority. **Nothing is created until a
   person decides:**
   - *Approve and assign*: the chosen actions become tasks for their owners (reminded daily and escalated when
     overdue, as every follow-up is);
   - *Snooze* for 3, 7 or 14 days;
   - *Dismiss* with a reason. The same findings do not come back for 30 days; only something new re-raises it.

Items refresh on every check. An approved item reopens if a new finding appears, and an item resolves itself
when its findings clear.

## What is detected

**Students**

| Finding | Rule | Suggested action (owner) |
|---|---|---|
| Attendance has declined for two consecutive months | Attendance fell across three rolling 30-day windows and is below 85% (or fell 15+ points to below 80%) | Call the family about attendance (Academy Manager, 2 days) |
| Frequent absences | 3+ absences in 30 days | as above |
| Late arrivals are increasing | 3+ late arrivals in 30 days, more than the 30 days before | Talk to the family about arriving on time (Academy Manager) |
| Progress is slowing down | Lessons completed plus classes with recorded content halved against the previous 30 days | Ask the teacher for a catch-up plan (teacher) |
| Syllabus is behind the expected pace | Completion more than 15 points under the course's target pace | as above |
| Frequent teacher changes | 2+ teacher changes in 90 days | Review whether the current teacher is the right fit (Academy Manager) |
| Repeated or reopened complaints | 2+ complaints in 90 days, or a reopened complaint still open | Manager call with the family (Academy Manager, 1 day) |
| Assessment weaknesses | Last assessment failed, 40%+ of its questions wrong, or the monthly test fell 10+ points | Re-assess the weak areas (teacher) |
| Follow-ups are overdue | Any overdue follow-up for the student or family | Chase the overdue follow-ups (Academy Manager) |
| The family was upset / concerned on the last call | Within 30 days, follow-up not done | Manager call with the family |
| High retention risk | The retention model's high risk | Retention call with the family |
| Open teacher recommendation | Older than a week | Act on the teacher's recommendation |

**Families: referral opportunities.** With the college 60+ days, a child active, no open complaint, not yet an
ambassador, and at least one of: scored us 9–10, a teacher reported "progressing well", positive on a recent call.
Suggested: invite to the ambassador programme (Head of Admissions); credit-only rewards as before.

**Teachers: recurring operational problems.** 3+ missed classes in 30 days; 5+ late starts; 2+ complaints in 90
days; QA average below 3/5; three or more of their students needing attention. Suggested: supervisor review, QA review.

## Who sees what

`attention.view` / `attention.update`: Academy Manager, the academic head and Manager in full; Head of Admissions
and supervisors view and decide; the QA head views. Management sees every item; a supervisor sees the students
and teachers they supervise; teacher items (performance and complaints) are for management only. Every decision
is in the audit log.

## Data and code

Migration `2f3ce7987915`: `attention_items`. Code: `app/services/attention.py` (rules, items, decisions),
`app/services/jobs_attention.py`, `app/web/attention.py`, `app/templates/attention/board.html`; summary writer
`attention_summary` in `app/services/ai_gateway.py`. Tests: `tests/test_attention.py`.

## With an AI provider

The rules decide *what* is flagged; the AI only writes the wording, and every AI run is logged under AI Governance
with its model and confidence. The gateway currently speaks to an OpenAI-compatible endpoint; an Anthropic Claude
provider can be added in the same place.
