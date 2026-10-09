# Student academic journey

Built 9 October 2026 (items 6–11 of the integrated ERP brief). For any student and any period the ERP answers "what
happened with this student, and how did they get here": teacher and class-time history, what was taught and
completed, attendance, absences, holidays and late arrivals, assessments with the evidence behind them, teacher
recommendations and what they set in motion, parent contacts and complaints.

Where: Online Academics › Student List › *student* › **Academic Journey** (`/students/{id}/journey`), and
Online Academics › **Monthly Student Summary** (`/academics/student-summary`). Teachers reach their own students'
journeys from Teacher Portal › My Students.

## Period selector

Current month · Previous month · Specific month · Date range · Entire history (from enrolment, or the first class if
earlier). Every view (timeline, syllabus, assessments) and the figures strip use the chosen window.

Figures for the window: classes scheduled, attended, absent, late arrivals, on leave, holidays, missed by the
teacher, attendance %. A **late arrival** is an attendance mark of *late*, or the student joining more than five
minutes after the start. Holidays are the college's *all shifts* holidays from HR › HR Configurations › Holidays.

## Views

| View | Shows |
|---|---|
| **Timeline** | Newest first, filterable by kind: enrolment; teacher and class-time changes with the reason; every class (attended, absent, late, leave, missed, cancelled) with what was taught; holidays; leave; status changes; lessons completed; assessments and monthly tests; teacher recommendations; complaints, parent contacts and parent feedback (complaints only as far as the viewer may see them) |
| **Monthly summary** | One page per student per month for the Academy / PDM Manager: classes, absences, holidays, late arrivals, syllabus completed, current stage, assessment results, teacher observations, recommendations, parent concerns, outstanding actions, and what needs attention. Printable |
| **Teachers & class times** | Every teacher and class-time period: from, to, teacher (and previous teacher), class time and days (and previous time), change type, reason category and reason, who made the change |
| **Syllabus & progress** | Overall progress, completed in the period, remaining, expected by now (from the course's completion target) and whether the student is behind pace, current academic stage, lessons completed (date, teacher, score), what was taught class by class, lessons completed per month, completions by teacher |
| **Assessments** | Each assessment with its questions, the student's answers and the result of each, strengths, weaknesses, observations, recommendations and follow-up |

"What was taught" for a class combines the class activity (book and page), the delivered lesson plan (sabaq, sabqi,
dor, delivered content), the lesson linked to the class, and the teacher's class notes.

## Teacher and class-time history

`teacher_assignments` holds one row per period during which a student had one teacher at one class time. A change of
teacher or time closes the open row (end date) and opens a new one carrying the previous teacher, previous time and
days, a reason category and the reason, and who made it. Nothing is overwritten.

Reason categories: timing conflict, timing / schedule change, teacher availability, parent request, student request,
academic requirement, teacher performance, capacity / workload, other approved reason.

Recorded automatically by every path that changes a student's teacher or class time:

| Path | Recorded as |
|---|---|
| Student › Change teacher (reason category chosen on the form) | teacher change |
| Subscription › Change teacher / Change session time (reason category on the form) | teacher change / time change |
| New subscription | enrolment or teacher change |
| Client Requests › Teacher Change approved | teacher change, parent request (this used to overwrite the teacher with no history) |
| Client Requests › Time Change approved | time change, parent request |
| Schedules › bulk teacher change, permanent | teacher change, teacher availability; the student's teacher now follows the permanent reassignment |

Temporary cover (class arrangements, dated substitutions) changes individual classes only, which the timeline shows
class by class. A student without history rows is reconstructed once from the teacher-match decisions recorded at
assignment time.

## Evidence-based assessment

The evaluation forms (Online Academics › Evaluations, and Teacher Portal › Evaluations) record, per question: the
question (typed, or picked from the question bank of the chosen assessment), the expected answer, the student's
answer, the result (correct, partly correct, incorrect, not attempted) and marks. With marks entered the score is
worked out from them. Also recorded: current stage, strengths, weaknesses, teacher observations, teacher
recommendations, parent-related observations and required follow-up. The evaluation page shows
*Question → Student answer → Result* and can correct the evidence later (audited).

## Teacher recommendations and automatic actions

Recorded from the journey page or ticked on an assessment. Each creates its follow-up:

| Recommendation | Follow-up |
|---|---|
| Student frequently arrives late | Task for the Academy Manager, due in 3 days |
| Attendance requires improvement | Task for the Academy Manager, due in 2 days, high priority, alert |
| Student needs more parental support | Task for the Academy Manager, due in 3 days |
| Parent needs to be contacted | Task for the Academy Manager, due in 1 day, high priority, alert |
| Student needs additional revision | Task for the student's teacher, due in 7 days |
| Student requires additional practice | Task for the student's teacher, due in 7 days |
| Student is progressing well / Other | Recorded on the timeline only |

The Academy Manager is the holder of that role with the fewest open tasks; without one, the teacher's supervisor,
then the head of academics. Tasks are reminded and escalated by the existing overdue-task job until done. The same
concern raised again within 30 days while the earlier one is still open is **escalated**: a management alert and a
notice to the head of academics. Every recommendation emits the automation event `student.recommendation`, so
workflows (CRM › Automations) can add messages or tags.

## Monthly summary board and digest

`/academics/student-summary` lists every active, trial and frozen student for a month (filter by teacher, name, and
"needing attention only"), with classes held, attended, absent, late, leave, attendance %, lessons completed,
assessments, open follow-ups and flags, students needing attention first. Flags: attendance below 75%, three or more
absences, three or more late arrivals, no progress recorded (no lesson completed and no class content recorded despite four or more classes), behind expected pace,
assessment below the pass mark, open teacher recommendation.

Early each month the job `academic_monthly_student_summary` tells the Academy Managers (or the head of academics)
that last month's summary is ready and how many students need attention, once per month.

## Who sees what

Management roles see every student. A teacher sees their own students; a supervisor the students of the teachers
they supervise; families and students are not given these pages. Complaints in the timeline follow the complaint
visibility rules (docs/COMPLAINTS.md).

## Data and code

Migration `070b2e62e1cc`: `teacher_assignments`, `assessment_answers`, `student_recommendations`, and on
`evaluations` the columns `stage`, `strengths`, `weaknesses`, `recommendations`, `parent_observations`, `follow_up`.

Code: `app/services/journey.py`, `app/web/journey.py`, `app/templates/students/journey.html`,
`app/templates/academics/student_summary.html`, `app/templates/academics/_evidence_fields.html`. Tests:
`tests/test_student_journey.py`.
