"""Operations seed: the KPI catalogue and six months of values, projects/sprints/milestones/tasks,
the transformation OS tracker, transitions, development plans, trajectory meetings, decisions,
department scorecards, structured daily reports, report runs and one executive AI insight run.

Idempotent, fixed random seed, ASCII-only output.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.core import AIModelRun, Department, Setting, User
from app.models.ops import (KPI, DailyReport, Decision, DepartmentScorecard, KPIValue, Milestone, Project,
                            ReportRun, Sprint, Task, TaskComment, TrajectoryMeeting, TransformationItem,
                            TransitionRecord)
from app.models.people import DevelopmentPlan, Employee
from app.services import kpi as kpi_svc

# ------------------------------------------------------------------------------------------------ KPI catalogue
# (code, name, role, dept_code, unit, target, direction, frequency, formula_key, description)
KPIS = [
    # ---- teacher
    ("teacher_classes_completed", "Classes completed", "teacher", "academics", "count", 80, "higher", "monthly",
     "classes_completed", "Sessions marked done in the period."),
    ("teacher_class_completion", "Class completion rate", "teacher", "academics", "%", 95, "higher", "monthly",
     "class_completion", "Done sessions as a share of every session that reached a terminal state."),
    ("teacher_missed_rate", "Missed class rate", "teacher", "academics", "%", 3, "lower", "monthly",
     "missed_rate", "Teacher no-shows as a share of terminal sessions."),
    ("teacher_punctuality", "Punctuality", "teacher", "academics", "%", 95, "higher", "monthly",
     "punctuality", "Sessions joined within the grace period."),
    ("teacher_qa_score", "QA review score", "teacher", "academics", "score", 85, "higher", "monthly",
     "qa_score", "Average score of manual QA reviews."),
    ("teacher_ai_score", "AI class monitoring score", "teacher", "academics", "score", 80, "higher", "monthly",
     "ai_score", "Average AI class-analysis score (advisory only)."),
    ("teacher_student_retention", "Student retention", "teacher", "academics", "%", 90, "higher", "monthly",
     "student_retention", "Students retained against the roster at the start of the period."),
    ("teacher_complaints", "Complaints received", "teacher", "academics", "count", 1, "lower", "monthly",
     "complaints", "Complaint cases attributed to the teacher."),
    ("teacher_feedback_rating", "Parent feedback rating", "teacher", "academics", "score", 4.5, "higher", "monthly",
     "feedback_rating", "Average parent survey rating out of 5."),
    ("teacher_utilization", "Teaching utilisation", "teacher", "academics", "%", 75, "higher", "monthly",
     "teacher_utilization", "Taught minutes against contracted capacity."),
    ("teacher_lesson_plan_compliance", "Lesson plan compliance", "teacher", "academics", "%", 90, "higher", "monthly",
     "lesson_plan_compliance", "Sessions delivered with an approved lesson plan."),
    ("teacher_test_improvement", "Monthly test improvement", "teacher", "academics", "%", 5, "higher", "monthly",
     "test_improvement", "Improvement in monthly test scores against the previous month."),
    # ---- supervisor
    ("supervisor_class_coverage", "Class coverage", "supervisor", "operations", "%", 98, "higher", "monthly",
     "class_coverage", "Scheduled classes actually covered by a teacher."),
    ("supervisor_missed_response", "Missed-class response time", "supervisor", "operations", "minutes", 15, "lower",
     "monthly", "missed_response_minutes", "Minutes from a missed class to a supervisor action."),
    ("supervisor_team_qa", "Team QA average", "supervisor", "operations", "score", 85, "higher", "monthly",
     "team_qa", "QA average across the supervised teachers."),
    ("supervisor_retention_rate", "Team retention rate", "supervisor", "operations", "%", 92, "higher", "monthly",
     "retention_rate", "Student retention across the supervised roster."),
    ("supervisor_ops_attendance", "Team attendance", "supervisor", "operations", "%", 96, "higher", "monthly",
     "ops_attendance", "Operations staff attendance."),
    ("supervisor_missed_classes", "Missed classes handled", "supervisor", "operations", "count", 10, "lower",
     "monthly", "classes_missed", "Missed classes in the supervised area."),
    # ---- manager
    ("manager_sla_compliance", "Case SLA compliance", "manager", "operations", "%", 95, "higher", "monthly",
     "sla_compliance", "Cases resolved within their SLA."),
    ("manager_churn_rate", "Student churn", "manager", "operations", "%", 4, "lower", "monthly",
     "churn_rate", "Cancellations against the active roster."),
    ("manager_tasks_done", "Tasks completed on time", "manager", "operations", "%", 90, "higher", "monthly",
     "tasks_done", "Tasks closed on or before their due date."),
    ("manager_active_students", "Active students", "manager", "operations", "count", 120, "higher", "monthly",
     "active_students", "Students with an active enrolment."),
    ("manager_class_completion", "Institutional class completion", "manager", "operations", "%", 95, "higher",
     "monthly", "class_completion", "Completion across every teacher."),
    ("manager_decision_sla", "Decisions closed with an outcome", "manager", "operations", "%", 90, "higher",
     "monthly", None, "Share of due decisions that carry a written outcome (recorded manually)."),
    # ---- HR / people
    ("hr_attendance", "Staff attendance", "hr", "people", "%", 96, "higher", "monthly",
     "hr_attendance", "Present marks against expected staff attendance."),
    ("hr_hiring_time", "Time to hire", "hr", "people", "days", 21, "lower", "monthly",
     "hiring_time_days", "Days from requisition to signed offer."),
    ("hr_onboarding_completion", "Onboarding completion", "hr", "people", "%", 95, "higher", "monthly",
     "onboarding_completion", "Onboarding checklists completed on time."),
    ("hr_turnover", "Staff turnover", "hr", "people", "%", 3, "lower", "monthly",
     "turnover", "Leavers against average headcount."),
    ("hr_enps", "Employee NPS (eNPS)", "hr", "people", "score", 30, "higher", "monthly",
     "enps", "Staff net promoter score. CEO-sensitive - never shared per person."),
    ("hr_development_plans", "Active development plans", "hr", "people", "count", 12, "higher", "monthly",
     None, "Six-month growth journeys currently running (recorded manually)."),
    ("hr_ai_fluency", "Average AI fluency level", "hr", "people", "score", 3, "higher", "monthly",
     None, "Average AI fluency (1-5) across staff development plans."),
    # ---- academics
    ("academic_curriculum_completion", "Curriculum completion", "academic", "academics", "%", 90, "higher", "monthly",
     "curriculum_completion", "Curriculum items completed against plan."),
    ("academic_student_progress", "Student progress", "academic", "academics", "%", 85, "higher", "monthly",
     "student_progress", "Average progress across active students."),
    ("academic_evaluation_pass", "Evaluation pass rate", "academic", "academics", "%", 90, "higher", "monthly",
     "evaluation_pass_rate", "Students passing their scheduled evaluation."),
    ("academic_lesson_plans", "Lesson plans approved", "academic", "academics", "%", 95, "higher", "monthly",
     "lesson_plan_compliance", "Lesson plans approved before delivery."),
    ("academic_test_improvement", "Monthly test improvement", "academic", "academics", "%", 5, "higher", "monthly",
     "test_improvement", "Institution-wide improvement in monthly test scores."),
    # ---- QA
    ("qa_coverage", "QA sampling coverage", "qa", "qa", "%", 10, "higher", "monthly",
     "qa_coverage", "Share of delivered classes reviewed."),
    ("qa_average_score", "QA average score", "qa", "qa", "score", 85, "higher", "monthly",
     "qa_score", "Average QA review score."),
    ("qa_corrective_closure", "Corrective action closure", "qa", "qa", "%", 90, "higher", "monthly",
     "corrective_closure", "Corrective actions closed within the period."),
    ("qa_repeat_issues", "Repeat issues", "qa", "qa", "%", 10, "lower", "monthly",
     "repeat_issues", "Teachers with the same issue raised twice."),
    ("qa_ai_average", "AI monitoring average", "qa", "qa", "score", 80, "higher", "monthly",
     "ai_score", "Average AI class-analysis score across sampled classes."),
    # ---- finance
    ("finance_revenue", "Revenue collected", "finance", "finance", "currency", 3000000, "higher", "monthly",
     "revenue", "Payments received, converted to base currency."),
    ("finance_collection_rate", "Collection rate", "finance", "finance", "%", 90, "higher", "monthly",
     "collection_rate", "Collected against invoiced in the period."),
    ("finance_receivables", "Outstanding receivables", "finance", "finance", "currency", 500000, "lower", "monthly",
     "receivables", "Unpaid invoice balances in base currency."),
    ("finance_payroll_ratio", "Payroll to revenue ratio", "finance", "finance", "%", 45, "lower", "monthly",
     "payroll_ratio", "Payroll cost as a share of collected revenue."),
    ("finance_mrr", "Monthly recurring revenue", "finance", "finance", "currency", 2500000, "higher", "monthly",
     "mrr", "Active subscription value per month."),
    ("finance_cash_flow", "Net cash flow", "finance", "finance", "currency", 400000, "higher", "monthly",
     "cash_flow", "Collections less expenses and payroll."),
    ("finance_pnl", "Net profit", "finance", "finance", "currency", 600000, "higher", "monthly",
     "pnl", "Revenue less expenses and payroll."),
    # ---- marketing
    ("marketing_leads", "New leads", "marketing", "marketing", "count", 120, "higher", "monthly",
     "leads_count", "Leads created in the period."),
    ("marketing_lead_conversion", "Lead conversion", "marketing", "marketing", "%", 25, "higher", "monthly",
     "lead_conversion", "Leads that reached won."),
    ("marketing_trial_conversion", "Trial conversion", "marketing", "marketing", "%", 60, "higher", "monthly",
     "trial_conversion", "Trials that converted to an enrolment."),
    ("marketing_cpl", "Cost per lead", "marketing", "marketing", "currency", 1500, "lower", "monthly",
     "cpl", "Campaign spend divided by leads."),
    ("marketing_cac", "Customer acquisition cost", "marketing", "marketing", "currency", 6000, "lower", "monthly",
     "cac", "Campaign spend divided by new students."),
    ("marketing_roi", "Marketing ROI", "marketing", "marketing", "%", 250, "higher", "monthly",
     "roi", "Attributed revenue against campaign spend."),
    ("marketing_referral_pct", "Referral share of gross adds", "marketing", "marketing", "%", 25, "higher", "monthly",
     "referral_pct", "Referred enrolments as a share of all new students."),
    # ---- technology
    ("tech_integration_health", "Integration health", "technology", "technology", "%", 95, "higher", "monthly",
     "integration_health", "Integrations reporting healthy."),
    ("tech_open_incidents", "Open security incidents", "technology", "technology", "count", 0, "lower", "monthly",
     "open_incidents", "Security incidents still open."),
    ("tech_webhook_success", "Webhook delivery success", "technology", "technology", "%", 98, "higher", "monthly",
     "webhook_success", "Outbound webhook deliveries that succeeded."),
    # ---- CEO / institutional
    ("ceo_health_score", "Institutional health score", "ceo", None, "score", 80, "higher", "monthly",
     "health_score", "Weighted composite of academic, finance, people, quality and growth."),
    ("ceo_nps", "Student & parent NPS", "ceo", None, "score", 50, "higher", "monthly",
     "nps", "Net promoter score across families."),
    ("ceo_active_students", "Active students", "ceo", None, "count", 130, "higher", "monthly",
     "active_students", "Institution-wide active roster."),
    ("ceo_churn", "Churn rate", "ceo", None, "%", 4, "lower", "monthly",
     "churn_rate", "Institution-wide monthly churn."),
    ("ceo_revenue_attribution", "Revenue attributed to campaigns", "ceo", None, "currency", 800000, "higher",
     "monthly", "revenue_attribution", "Revenue traceable to a marketing campaign."),
    ("ceo_referral_pct", "Referral share of growth", "ceo", None, "%", 25, "higher", "monthly",
     "referral_pct", "Referrals as a share of gross adds - the trust metric."),
]

PROJECTS = [
    ("Iqra Tech 2.0 Platform Build", "PRJ-OS", "technology",
     "Replace the legacy spreadsheets and WhatsApp threads with the Digital Operating System across every department.",
     "active", "urgent", -120, 90, [
         ("Sprint 12 - Command Center", "Executive health score, insights and anomaly scan live for the CEO", -28, -15, "completed"),
         ("Sprint 13 - Operations", "Tasks, KPIs, decisions and daily reports in production", -14, -1, "completed"),
         ("Sprint 14 - Governance", "AI governance, reports and transformation tracker", 0, 13, "active"),
     ], [("Command Center signed off", -20, "achieved"), ("Operations modules live", -2, "achieved"),
         ("Governance modules live", 14, "pending"), ("Legacy spreadsheets retired", 60, "pending")]),
    ("WhatsApp Cloud API Migration", "PRJ-WA", "technology",
     "Move every parent conversation from personal numbers to the official Cloud API with templates and audit.",
     "active", "high", -60, 45, [
         ("Migration wave 1", "Billing and support inboxes", -21, -8, "completed"),
         ("Migration wave 2", "Sales and teacher notifications", -7, 7, "active"),
     ], [("Business verification approved", -30, "achieved"), ("All templates approved", 10, "pending"),
         ("Personal numbers decommissioned", 40, "pending")]),
    ("Hifz Curriculum v2", "PRJ-HIFZ", "academics",
     "Rebuild the Hifz track with sabaq / sabqi / manzil quotas, revision cycles and monthly test calibration.",
     "active", "high", -75, 60, [
         ("Curriculum drafting", "Juz 1-10 rebuilt with dor quotas", -30, -16, "completed"),
         ("Teacher enablement", "Train every Hifz teacher on the new cycle", -15, 5, "active"),
     ], [("Juz 1-10 approved", -18, "achieved"), ("Teacher training complete", 6, "pending"),
         ("First calibrated test cycle", 35, "pending")]),
    ("Q4 Enrolment Campaign", "PRJ-Q4", "marketing",
     "Ramadan-lead-up campaign across Meta and Google with referral push and a trial-conversion sprint.",
     "planning", "medium", -20, 75, [
         ("Creative production", "Landing pages, ad sets and WhatsApp sequences", -10, 10, "active"),
     ], [("Creative approved", 8, "pending"), ("Campaign live", 20, "pending"),
         ("300 qualified leads", 70, "pending")]),
]

TASK_BANK = [
    # (project index or None, title, priority, estimate, days_from_today_due, status)
    (0, "Ship the KPI snapshot job to production", "high", 6, -9, "done"),
    (0, "Wire executive insights into the command center", "urgent", 8, -6, "done"),
    (0, "Build the decision register accountability view", "high", 10, 2, "in_progress"),
    (0, "Write the AI governance policy panel", "medium", 5, 4, "todo"),
    (0, "Migrate report exports to the shared service", "medium", 4, -3, "review"),
    (0, "Add daily report reminders 30 minutes before deadline", "high", 3, -12, "done"),
    (0, "Load-test the class session generator", "medium", 6, 9, "todo"),
    (0, "Document the RBAC permission matrix", "low", 3, 12, "todo"),
    (0, "Retire the legacy attendance spreadsheet", "urgent", 12, -2, "in_progress"),
    (0, "Set up nightly database backups with restore test", "urgent", 4, -15, "done"),
    (0, "Add teacher portal offline fallback", "low", 5, 20, "todo"),
    (0, "Review audit log retention policy", "medium", 2, 6, "todo"),
    (1, "Complete Meta business verification", "urgent", 3, -25, "done"),
    (1, "Submit billing template pack for approval", "high", 2, -8, "done"),
    (1, "Migrate the support inbox to the Cloud API", "high", 6, -4, "review"),
    (1, "Train billing reps on the new inbox", "medium", 3, 3, "in_progress"),
    (1, "Move teacher class reminders to templates", "high", 5, 6, "todo"),
    (1, "Decommission the sales personal number", "medium", 2, 30, "todo"),
    (1, "Add opt-out handling to every sequence", "high", 4, 1, "todo"),
    (1, "Reconcile message costs against the finance ledger", "low", 3, 15, "todo"),
    (2, "Rebuild Juz 1-10 sabaq quotas", "high", 20, -20, "done"),
    (2, "Define manzil revision cycle rules", "high", 8, -10, "done"),
    (2, "Calibrate the monthly test rubric", "urgent", 6, -1, "in_progress"),
    (2, "Run the teacher enablement workshop", "high", 5, 4, "todo"),
    (2, "Publish the parent-facing progress guide", "medium", 4, 11, "todo"),
    (2, "Update evaluation templates for the new cycle", "medium", 5, 7, "todo"),
    (2, "Pilot the new cycle with two teachers", "high", 6, -5, "review"),
    (2, "Collect teacher feedback on the dor quota", "low", 2, 18, "todo"),
    (3, "Draft the Ramadan campaign brief", "high", 4, -6, "done"),
    (3, "Produce three ad creatives for Meta", "medium", 8, 5, "in_progress"),
    (3, "Build the referral landing page", "medium", 6, 9, "todo"),
    (3, "Set the campaign budget and CPL target", "high", 2, 2, "todo"),
    (3, "Brief the closers on the new offer", "medium", 3, 12, "todo"),
    (3, "Prepare the WhatsApp nurture sequence", "high", 5, 8, "todo"),
    (None, "Follow up the three highest churn-risk families", "urgent", 3, -1, "in_progress"),
    (None, "Review last month's missed classes with supervisors", "high", 2, -4, "done"),
    (None, "Close the outstanding corrective actions", "high", 4, 3, "todo"),
    (None, "Chase invoices over 30 days", "urgent", 3, -2, "in_progress"),
    (None, "Prepare the payroll run for approval", "high", 5, 5, "todo"),
    (None, "Interview shortlist for the evening supervisor role", "medium", 6, 7, "todo"),
    (None, "Publish the monthly result cards", "high", 4, 1, "todo"),
    (None, "Audit teacher lesson plan compliance", "medium", 3, 10, "todo"),
    (None, "Reconcile the WhatsApp integration failures", "medium", 2, -7, "done"),
    (None, "Update the safeguarding acknowledgement register", "high", 3, 14, "todo"),
    (None, "Refresh the trial-to-enrolment script", "low", 2, 16, "todo"),
    (None, "Verify the new teachers' background checks", "urgent", 3, -3, "review"),
    (None, "Prepare the department scorecard pack", "high", 3, 2, "todo"),
    (None, "Call the five families on the retention watchlist", "urgent", 4, 0, "in_progress"),
    (None, "Fix the duplicate client records from migration", "medium", 5, 8, "todo"),
    (None, "Draft the quarterly board summary", "high", 6, 11, "todo"),
]

RECURRING = [
    ("Daily class status review", "daily", "high", "operations"),
    ("Weekly trajectory meeting preparation", "weekly", "high", "operations"),
    ("Weekly QA sampling plan", "weekly", "medium", "qa"),
    ("Monthly payroll reconciliation", "monthly", "high", "finance"),
    ("Weekly lead pipeline review", "weekly", "medium", "marketing"),
    ("Monthly teacher grade review", "monthly", "medium", "people"),
]

TRANSFORMATION = [
    ("academics", "Curriculum & lesson plan system", "full_launch", 100, -30,
     "Lesson plans, curriculum tree and evaluations fully in the OS."),
    ("academics", "Monthly test calibration engine", "beta", 65, 25,
     "Automated result cards with improvement tracking; calibration rubric under review."),
    ("academics", "Arabic reader with Tajweed marking", "alpha", 30, 70,
     "Verse-level reader with Tajweed classes; needs teacher review tooling."),
    ("operations", "Class execution & attendance", "full_launch", 100, -45,
     "Session generation, join tracking, missed-class escalation."),
    ("operations", "Supervisor coverage board", "beta", 70, 20,
     "Live coverage gaps and reassignment workflow."),
    ("operations", "Auto-reschedule from leave requests", "planned", 5, 90,
     "Student and teacher leave feeding straight into the schedule."),
    ("finance", "Billing, invoices & payment gateway", "full_launch", 100, -60,
     "Invoicing, receipts, aging and gateway reconciliation."),
    ("finance", "Payroll with teacher cost model", "beta", 75, 18,
     "Payslips, advances and bonuses; teacher-cost margin view pending."),
    ("finance", "Forecasting & budget variance", "planned", 10, 100,
     "Rolling 12-month forecast against departmental budgets."),
    ("people", "HR attendance & shift compliance", "full_launch", 100, -40,
     "Check-in, late minutes, corrections and approvals."),
    ("people", "Recruitment pipeline", "beta", 60, 30,
     "Requisitions, candidates and interview scoring."),
    ("people", "Growth journeys & AI fluency", "alpha", 35, 60,
     "Six-month development plans with AI fluency levels per employee."),
    ("qa", "AI class monitoring", "beta", 80, 12,
     "Automated class analysis with a human review queue on every flag."),
    ("qa", "Corrective action workflow", "full_launch", 100, -25,
     "Findings, owners, due dates and closure verification."),
    ("marketing", "Lead capture & GHL sync", "full_launch", 100, -50,
     "Website, WhatsApp and ad leads landing in one pipeline."),
    ("marketing", "Attribution & campaign ROI", "beta", 55, 35,
     "Spend, CPL, CAC and revenue attribution per campaign."),
    ("marketing", "Referral & ambassador programme", "alpha", 40, 55,
     "Ambassador invites, referral links and credit issuing."),
    ("technology", "Integration health monitoring", "beta", 70, 15,
     "Health checks, failure counters and webhook retry visibility."),
    ("technology", "Legacy data migration", "alpha", 45, 40,
     "Clients, students, invoices and payments from the legacy ERP."),
    ("technology", "Single sign-on & MFA rollout", "planned", 0, 120,
     "MFA enforced for every admin-portal role."),
]

TRANSITIONS = [
    ("Supervisor - Evening shift", "in_progress", -12, [
        ("Hand over the evening coverage board", True),
        ("Introduce to all evening teachers", True),
        ("Transfer the escalation WhatsApp group", True),
        ("Shadow two evening shifts", False),
        ("Reassign open cases", False),
    ], "Evening shift has the highest missed-class rate; the incoming supervisor must own the escalation "
       "flow from day one. Outgoing supervisor moves to the academics coordination role."),
    ("Billing Representative", "planned", 5, [
        ("Hand over the billing inbox", False),
        ("Walk through the aging report", False),
        ("Transfer gateway reconciliation access", False),
        ("Introduce to the top 20 accounts", False),
    ], "Planned rotation. Handover starts the week the replacement clears background checks."),
    ("QA Officer", "completed", -60, [
        ("Transfer the sampling calendar", True),
        ("Hand over open corrective actions", True),
        ("Review the scoring rubric together", True),
        ("Joint calibration on ten recordings", True),
    ], "Completed rotation. Calibration scores stayed within two points of the previous officer."),
]

DEV_PLANS = [
    ("academics", "Academics - six-month capability journey", 4, [
        "Every teacher files lesson plans in the OS, no exceptions",
        "Curriculum tree completed for Nazra and Hifz tracks",
        "Monthly test rubric calibrated across all teachers",
        "Evaluation pass rate above 90% for two consecutive months",
        "Teachers use AI lesson recommendations in weekly planning",
        "Academic coordinators run the whole cycle without escalation",
    ]),
    ("operations", "Operations - six-month capability journey", 3, [
        "Missed-class response under 15 minutes across both shifts",
        "Coverage board reviewed every morning before the first class",
        "Every escalation raised as a task with an owner",
        "Supervisor scorecards reviewed weekly with each supervisor",
        "Class completion above 95% for two consecutive months",
        "Shift handover fully documented, no verbal handovers",
    ]),
    ("finance", "Finance - six-month capability journey", 3, [
        "All invoices raised from the OS, no manual receipts",
        "Aging report reviewed weekly with the collections owner",
        "Collection rate above 90%",
        "Payroll run approved from the OS with an audit trail",
        "Monthly P&L closed within five working days",
        "Rolling forecast presented at the trajectory meeting",
    ]),
    ("people", "People & Culture - six-month capability journey", 4, [
        "Attendance corrections handled in the OS within 24 hours",
        "Every new hire onboarded through the checklist",
        "Development plan for every employee, reviewed monthly",
        "eNPS survey run and results discussed openly",
        "Time to hire under 21 days",
        "AI fluency level 3 or above for every department lead",
    ]),
    ("qa", "Quality Assurance - six-month capability journey", 4, [
        "10% of delivered classes sampled every month",
        "AI monitoring flags reviewed within 48 hours",
        "Corrective actions closed within their due date",
        "Repeat issues below 10%",
        "Calibration session run monthly with the academics HOD",
        "QA findings feeding teacher development plans automatically",
    ]),
    ("marketing", "Marketing - six-month capability journey", 4, [
        "Every lead source tracked with UTM and campaign attribution",
        "CPL and CAC reported weekly, not monthly",
        "Trial conversion above 60%",
        "Referral share of gross adds above 25%",
        "Nurture sequences running for every stalled stage",
        "Campaign ROI presented with revenue attribution each month",
    ]),
    ("technology", "Technology - six-month capability journey", 5, [
        "Every integration health-checked with alerting",
        "Legacy migration reconciled and signed off",
        "MFA enforced for all admin-portal roles",
        "Backups tested with a real restore every month",
        "Zero open critical security incidents",
        "Each department can build its own reports without engineering",
    ]),
]

DECISIONS = [
    ("Move all parent messaging to the WhatsApp Cloud API", "strategic", "technology",
     "Personal numbers leave no audit trail, cannot be handed over when staff leave, and put family data outside our "
     "control. The Cloud API gives templates, delivery receipts and a record we can review.", "implemented", -70),
    ("Cap manager-approved discounts at 20 percent", "financial", "finance",
     "Discounts above 20 percent were eroding the margin floor and were being granted inconsistently. The CEO band "
     "stays available for exceptional cases, with a written rationale each time.", "implemented", -60),
    ("Adopt a 10 percent monthly QA sampling target", "operational", "qa",
     "Sampling below 10 percent gave us no confidence in the teacher grade. Ten percent is affordable with the current "
     "QA headcount and covers every teacher at least once a quarter.", "implemented", -55),
    ("Retire the attendance spreadsheet on 31 March", "operational", "operations",
     "Running two systems produced two versions of the truth and hours of reconciliation. A hard cut-off date forces "
     "the migration rather than letting it drift.", "decided", -12),
    ("Hire a second evening supervisor", "hr", "people",
     "Evening shift carries 60 percent of classes with one supervisor; missed-class response time is three times the "
     "morning shift. The cost is recovered by retaining four students.", "decided", 14),
    ("Do not act on AI class scores without a human review", "strategic", "qa",
     "An AI score is evidence, not a verdict. Acting on it directly would be unfair to teachers and would destroy "
     "trust in the monitoring system itself.", "implemented", -45),
    ("Set the referral credit at one free week for both families", "financial", "marketing",
     "A symmetrical reward is easy to explain and cheaper than paid acquisition at the current CAC. Reviewed once "
     "referral share passes 30 percent of gross adds.", "implemented", -40),
    ("Freeze new course launches until Hifz v2 ships", "academic", "academics",
     "Splitting academic attention across new courses while the core Hifz track is being rebuilt would deliver both "
     "badly. Revisit after the first calibrated test cycle.", "decided", 30),
    ("Escalate any invoice over 45 days to the CEO", "financial", "finance",
     "Receivables above 45 days historically convert to write-offs. CEO visibility at 45 days has recovered most of "
     "them in the past two quarters.", "implemented", -35),
    ("Standardise the trajectory meeting on Monday mornings", "operational", "operations",
     "A moving meeting slot meant department heads prepared inconsistently. A fixed slot before the week starts makes "
     "the scorecard deadline meaningful.", "implemented", -50),
    ("Require a decision record for every consequential action", "strategic", None,
     "Approvals, refunds, overrides and reversals were being agreed in chat and forgotten. If it changes money, "
     "grades or someone's employment, it gets an owner and a written rationale.", "implemented", -30),
    ("Pause Google Ads until CPL falls below 1,500", "financial", "marketing",
     "Google CPL is running at double the Meta equivalent with worse trial conversion. Spend moves to Meta and "
     "referrals until the landing page test is complete.", "decided", 7),
    ("Introduce teacher grades A/B/C with salary bands", "hr", "people",
     "Pay was disconnected from quality and retention. Grades tie salary to QA score, punctuality and retention, all "
     "of which are measured monthly and visible to the teacher.", "implemented", -65),
    ("Give every department a monthly scorecard deadline", "operational", None,
     "Verbal updates in the trajectory meeting were unfalsifiable. A written scorecard with highlights and risks "
     "submitted beforehand changes the meeting from reporting to deciding.", "implemented", -48),
    ("Record every class for safeguarding and QA", "strategic", "qa",
     "Recordings protect students and teachers alike and make QA sampling possible. Retention is capped at 365 days "
     "and access is logged.", "implemented", -80),
    ("Reverse the switch to fortnightly payroll", "financial", "finance",
     "Fortnightly payroll doubled the reconciliation work for no staff benefit; teachers preferred the monthly cycle "
     "aligned with their invoices.", "reversed", -25),
    ("Offer a free week instead of a discount to save an account", "financial", "finance",
     "A free week preserves the headline price and the margin floor, while a discount permanently resets the "
     "subscription value.", "decided", 5),
    ("Set the missed-class grace period at 10 minutes", "operational", "operations",
     "Below ten minutes we were flagging connection problems as no-shows; above it, families were left waiting. Ten "
     "minutes matches the observed join-time distribution.", "implemented", -42),
    ("Run the eNPS survey quarterly, results shared openly", "hr", "people",
     "An annual survey is too slow to act on and hiding the result destroys the point. Quarterly, published, with "
     "one committed action per cycle.", "decided", 21),
    ("Move trial classes to the top-graded teachers only", "academic", "academics",
     "Trial conversion correlates strongly with teacher grade. The short-term scheduling cost is smaller than the "
     "conversion gain.", "implemented", -20),
    ("Require background checks before any student contact", "strategic", "people",
     "Non-negotiable safeguarding baseline. No teacher takes a class, including a trial, before the check clears.",
     "implemented", -90),
    ("Buy rather than build the payment gateway integration", "strategic", "technology",
     "Building card handling ourselves would pull PCI scope in-house for no differentiation. The gateway fee is "
     "cheaper than the compliance burden.", "implemented", -75),
    ("Delay the mobile app until the OS modules are stable", "strategic", "technology",
     "A second client on an unstable API would double the bug surface. The web portals are already mobile-responsive.",
     "proposed", 45),
    ("Cap teacher load at 30 classes per week", "hr", "people",
     "Above 30 classes, QA scores and punctuality both decline measurably, and burnout drives resignations that cost "
     "far more than the extra classes earn.", "decided", 10),
    ("Publish monthly result cards to parents automatically", "academic", "academics",
     "Manual result cards were sent late or not at all. Automatic publication makes progress visible and reduces the "
     "'what is my child actually learning' complaints.", "implemented", -15),
]

MEETING_NOTES = [
    "Class completion recovered after the evening coverage fix. Finance flagged receivables drifting past 30 days; "
    "collections owner to report weekly. Marketing CPL still above target on Google.",
    "Hifz v2 pilot went well with two teachers. QA raised repeat issues on tone flags for one teacher - development "
    "plan agreed rather than a warning, in line with the AI policy. Payroll ratio within band.",
    "Missed-class rate spiked mid-month; root cause was a schedule clash after the timezone change. Auto-reschedule "
    "moved up the transformation tracker. Referral share above target for the first time.",
    "Reviewed the department scorecards. People and Finance submitted on time; Marketing late again - deadline "
    "reaffirmed. Decision taken to pause Google Ads pending the landing page test.",
    "Quarter close review. Revenue ahead of plan, churn slightly above target driven by two teacher departures. "
    "Transition records opened for both roles before the handover, not after.",
]

REPORT_RUNS = [
    ("teacher_performance", "Teacher performance", "xlsx", 12),
    ("aging", "Receivables aging", "xlsx", 34),
    ("leads_conversion", "Leads & conversion", "csv", 18),
    ("kpi_snapshot", "KPI snapshot", "xlsx", 48),
    ("hr_attendance", "HR attendance", "csv", 120),
]

DAILY_FIELDS = {
    "academics": (["priorities", "capacity", "blockers", "classes_planned", "lesson_plans"],
                  ["completed", "missed", "tomorrow", "classes_delivered", "students_at_risk"]),
    "operations": (["priorities", "capacity", "blockers", "sessions_scheduled", "coverage_gaps"],
                   ["completed", "missed", "tomorrow", "missed_handled", "parent_calls"]),
    "finance": (["priorities", "capacity", "blockers", "invoices_due"],
                ["completed", "missed", "tomorrow", "overdue_followups"]),
    "marketing": (["priorities", "capacity", "blockers", "leads_to_contact"],
                  ["completed", "missed", "tomorrow", "leads_contacted", "trials_booked"]),
    "people": (["priorities", "capacity", "blockers", "interviews"],
               ["completed", "missed", "tomorrow", "onboarding"]),
    "qa": (["priorities", "capacity", "blockers", "reviews_planned"],
           ["completed", "missed", "tomorrow", "reviews_done", "flags_raised"]),
    "technology": (["priorities", "capacity", "blockers", "open_incidents"],
                   ["completed", "missed", "tomorrow", "incidents_closed"]),
}

MORNING_TEXT = {
    "priorities": ["1. Clear the escalation queue\n2. Review yesterday's misses\n3. Prepare the scorecard",
                   "1. Follow up overdue accounts\n2. Coverage board\n3. One coaching conversation",
                   "1. Close open tasks\n2. Weekly report pack\n3. Team check-in",
                   "1. Trial follow-ups\n2. Pipeline review\n3. Campaign creative sign-off"],
    "capacity": ["Full day", "Full day, one meeting at 14:00", "Half day - clinic appointment after 15:00",
                 "Full day, covering the evening shift too"],
    "blockers": ["None", "Waiting on finance approval for the refund", "Need the updated teacher roster",
                 "Blocked on the gateway credentials", "None - all clear"],
}
AFTERNOON_TEXT = {
    "completed": ["Escalation queue cleared, three families called back, scorecard drafted.",
                  "Two coaching conversations done, coverage board updated, one reassignment made.",
                  "Chased eleven overdue invoices, four promised payment this week.",
                  "Reviewed six classes, raised two corrective actions, closed one from last week."],
    "missed": ["Nothing slipped today.", "Scorecard not finished - finance figures arrived late.",
               "One coaching conversation moved to tomorrow, teacher was in class.",
               "Campaign creative review slipped; designer is a day behind."],
    "tomorrow": ["Finish the scorecard before the meeting", "Call the retention watchlist",
                 "Close the remaining corrective actions", "Review the payroll draft"],
}


def _setting(db: Session, key: str, value, group: str, desc: str) -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    if not row:
        db.add(Setting(key=key, value=value, group=group, description=desc))


def seed_kpis(db: Session) -> dict:
    depts = {d.code: d for d in db.query(Department)}
    owners = {u.department_id: u for u in db.query(User).filter(User.is_active.is_(True))
              if (u.role_slug or "").startswith("hod_")}
    ceo = db.query(User).filter(User.is_superuser.is_(True)).order_by(User.id).first()
    created = 0
    for code, name, role, dept_code, unit, target, direction, freq, formula, desc in KPIS:
        k = db.query(KPI).filter(KPI.code == code).first()
        if k:
            continue
        dept = depts.get(dept_code) if dept_code else None
        owner = owners.get(dept.id) if dept else ceo
        db.add(KPI(code=code, name=name, role_slug=role, department_id=dept.id if dept else None, unit=unit,
                   target=float(target), direction=direction, frequency=freq, formula_key=formula,
                   description=desc, is_custom=False, is_active=True,
                   owner_id=(owner or ceo).id if (owner or ceo) else None))
        created += 1
    db.flush()
    return {"created": created, "total": db.query(KPI).count()}


def _plausible(kpi: KPI, idx: int, n: int, rnd: random.Random) -> float:
    """A believable value for a period with no underlying data: closer to target in recent months."""
    target = kpi.target if kpi.target is not None else 50.0
    ramp = 0.82 + 0.18 * (idx / max(1, n - 1))          # older months further from target
    jitter = rnd.uniform(0.93, 1.07)
    if kpi.direction == "lower":
        value = target * (2.0 - ramp) * jitter if target else rnd.uniform(0, 3)
    else:
        value = target * ramp * jitter
    if kpi.unit == "%":
        value = max(0.0, min(100.0, value))
    if kpi.unit == "count":
        value = max(0.0, round(value))
    return round(float(value), 2)


def seed_kpi_values(db: Session, rnd: random.Random) -> dict:
    periods = kpi_svc.last_n_periods(6)
    kpis = db.query(KPI).filter(KPI.is_active.is_(True)).all()
    from app.models.people import Teacher
    teachers = db.query(Teacher).filter(Teacher.status == "active").limit(12).all()
    existing = {(v.kpi_id, v.period) for v in
                db.query(KPIValue.kpi_id, KPIValue.period).filter(KPIValue.entity_type.is_(None))}
    system, seeded, teacher_rows = 0, 0, 0
    for idx, period in enumerate(periods):
        for k in kpis:
            if (k.id, period) in existing:
                continue
            value = kpi_svc.compute_kpi(db, k, period) if k.formula_key else None
            source = "system"
            if value is None:
                value = _plausible(k, idx, len(periods), rnd)
                source = "seed"
                seeded += 1
            else:
                system += 1
            kpi_svc.upsert_value(db, k, period, value, source=source)
    # per-teacher rows for the two most recent periods so role scorecards have history
    t_kpis = [k for k in kpis if k.role_slug == "teacher"]
    have = {(v.kpi_id, v.period, v.entity_id) for v in
            db.query(KPIValue.kpi_id, KPIValue.period, KPIValue.entity_id)
            .filter(KPIValue.entity_type == "teacher")}
    for period in periods[-2:]:
        for k in t_kpis:
            for t in teachers:
                if (k.id, period, t.id) in have:
                    continue
                v = kpi_svc.compute_kpi(db, k, period, t)
                if v is None:
                    continue
                kpi_svc.upsert_value(db, k, period, v, source="system", entity_type="teacher", entity_id=t.id)
                teacher_rows += 1
    db.flush()
    return {"system": system, "seeded": seeded, "teacher_rows": teacher_rows, "periods": len(periods)}


def seed_projects(db: Session, rnd: random.Random) -> list[Project]:
    depts = {d.code: d for d in db.query(Department)}
    users = {u.role_slug: u for u in db.query(User).filter(User.is_active.is_(True))}
    today = date.today()
    out = []
    owner_by_dept = {"technology": "hod_technology", "academics": "hod_academics", "marketing": "hod_marketing"}
    for name, code, dept_code, desc, status, priority, start_off, end_off, sprints, milestones in PROJECTS:
        p = db.query(Project).filter(Project.name == name).first()
        if not p:
            owner = users.get(owner_by_dept.get(dept_code, "manager")) or users.get("super_admin")
            p = Project(name=name, code=code, description=desc,
                        department_id=depts[dept_code].id if dept_code in depts else None,
                        owner_id=owner.id if owner else None, status=status, priority=priority,
                        start_date=today + timedelta(days=start_off), end_date=today + timedelta(days=end_off),
                        progress_pct=0)
            db.add(p)
            db.flush()
        for s_name, goal, s_start, s_end, s_status in sprints:
            if db.query(Sprint).filter(Sprint.project_id == p.id, Sprint.name == s_name).first():
                continue
            db.add(Sprint(project_id=p.id, name=s_name, goal=goal, start_date=today + timedelta(days=s_start),
                          end_date=today + timedelta(days=s_end), status=s_status))
        for m_title, m_off, m_status in milestones:
            if db.query(Milestone).filter(Milestone.project_id == p.id, Milestone.title == m_title).first():
                continue
            db.add(Milestone(project_id=p.id, title=m_title, due_date=today + timedelta(days=m_off),
                             status=m_status,
                             achieved_at=today + timedelta(days=m_off) if m_status == "achieved" else None))
        out.append(p)
    db.flush()
    return out


def seed_tasks(db: Session, rnd: random.Random, projects: list[Project]) -> dict:
    today = date.today()
    staff = [u for u in db.query(User).filter(User.is_active.is_(True)).order_by(User.id)
             if u.portal == "admin"]
    if not staff:
        return {"created": 0}
    ceo = next((u for u in staff if u.is_superuser), staff[0])
    depts = {d.code: d for d in db.query(Department)}
    created, comments, escalated = 0, 0, 0
    made: list[Task] = []
    for i, (proj_idx, title, priority, estimate, due_off, status) in enumerate(TASK_BANK):
        existing = db.query(Task).filter(Task.title == title).first()
        if existing:
            made.append(existing)
            continue
        assignee = staff[(i * 3 + 1) % len(staff)]
        project = projects[proj_idx] if proj_idx is not None and proj_idx < len(projects) else None
        sprint = None
        if project:
            sprint = (db.query(Sprint).filter(Sprint.project_id == project.id, Sprint.status == "active").first()
                      or db.query(Sprint).filter(Sprint.project_id == project.id).first())
        due = today + timedelta(days=due_off)
        overdue = due < today and status in ("todo", "in_progress", "review")
        t = Task(title=title, description=f"{title}. Raised from the weekly trajectory meeting review.",
                 project_id=project.id if project else None,
                 sprint_id=sprint.id if sprint and status != "done" else None,
                 assignee_id=assignee.id, creator_id=ceo.id,
                 department_id=project.department_id if project else assignee.department_id,
                 priority=priority, status=status, due_date=due,
                 completed_at=datetime.combine(due, datetime.min.time()) + timedelta(hours=17)
                 if status == "done" else None,
                 estimate_hours=float(estimate),
                 escalated=bool(overdue and priority in ("high", "urgent")),
                 assessment_score=rnd.choice([3, 4, 4, 5]) if status == "done" else None)
        if t.escalated:
            t.escalated_at = datetime.utcnow() - timedelta(days=1)
            escalated += 1
        db.add(t)
        db.flush()
        made.append(t)
        created += 1
        if i % 4 == 0:
            db.add(TaskComment(task_id=t.id, user_id=assignee.id,
                               text=rnd.choice([
                                   "Started on this - will need the finance figures before I can close it.",
                                   "Blocked until the integration credentials arrive; chased again today.",
                                   "Half done. The remaining work is the documentation, not the build.",
                                   "Done and verified with the supervisor. Closing after the review.",
                                   "Pushing this to next sprint, the dependency is not ready."])))
            db.add(TaskComment(task_id=t.id, user_id=ceo.id,
                               text=rnd.choice([
                                   "Noted. Keep the due date, tell me on Monday if it slips.",
                                   "Agreed. Record the decision in the register once it is settled.",
                                   "Good. Make sure the change is written up before it ships."])))
            comments += 2
    # dependencies: a few tasks blocked by an earlier one in the same project
    for a, b in ((2, 1), (4, 3), (16, 14), (23, 22), (30, 28)):
        if a < len(made) and b < len(made) and made[a].id != made[b].id and made[a].depends_on_id is None:
            if made[a].project_id and made[a].project_id == made[b].project_id:
                made[a].depends_on_id = made[b].id
    # recurring operational tasks
    for j, (title, recurrence, priority, dept_code) in enumerate(RECURRING):
        if db.query(Task).filter(Task.title == title).first():
            continue
        dept = depts.get(dept_code)
        owner = next((u for u in staff if u.department_id == (dept.id if dept else None)), staff[j % len(staff)])
        db.add(Task(title=title, description="Recurring operational commitment generated by the OS.",
                    assignee_id=owner.id, creator_id=ceo.id, department_id=dept.id if dept else None,
                    priority=priority, status="todo",
                    due_date=today + timedelta(days=1 if recurrence == "daily" else 3),
                    estimate_hours=1.5, recurrence=recurrence))
        created += 1
    db.flush()
    for p in projects:
        total = db.query(Task).filter(Task.project_id == p.id, Task.status != "cancelled").count()
        done = db.query(Task).filter(Task.project_id == p.id, Task.status == "done").count()
        p.progress_pct = int(round(100 * done / total)) if total else 0
    db.flush()
    return {"created": created, "comments": comments, "escalated": escalated}


def seed_transformation(db: Session, rnd: random.Random) -> int:
    depts = {d.code: d for d in db.query(Department)}
    hods = {u.department_id: u for u in db.query(User) if (u.role_slug or "").startswith("hod_")}
    ceo = db.query(User).filter(User.is_superuser.is_(True)).order_by(User.id).first()
    today = date.today()
    created = 0
    for dept_code, name, state, progress, target_off, notes in TRANSFORMATION:
        if db.query(TransformationItem).filter(TransformationItem.system_name == name).first():
            continue
        dept = depts.get(dept_code)
        owner = hods.get(dept.id) if dept else ceo
        db.add(TransformationItem(department_id=dept.id if dept else None, system_name=name, description=notes,
                                  state=state, owner_id=(owner or ceo).id if (owner or ceo) else None,
                                  progress_pct=progress, target_date=today + timedelta(days=target_off),
                                  notes=notes))
        created += 1
    db.flush()
    return created


def seed_transitions(db: Session) -> int:
    employees = db.query(Employee).order_by(Employee.id).all()
    if len(employees) < 6:
        return 0
    today = date.today()
    created = 0
    for i, (role_title, status, start_off, checklist, notes) in enumerate(TRANSITIONS):
        if db.query(TransitionRecord).filter(TransitionRecord.role_title == role_title).first():
            continue
        out_emp = employees[(i * 2) % len(employees)]
        in_emp = employees[(i * 2 + 1) % len(employees)]
        start = today + timedelta(days=start_off)
        db.add(TransitionRecord(role_title=role_title, from_employee_id=out_emp.id, to_employee_id=in_emp.id,
                                handover_notes=notes,
                                checklist=[{"item": item, "done": done} for item, done in checklist],
                                status=status, start_date=start,
                                completed_at=start + timedelta(days=21) if status == "completed" else None))
        created += 1
    db.flush()
    return created


def seed_development(db: Session, rnd: random.Random) -> dict:
    depts = {d.code: d for d in db.query(Department)}
    hods = {u.department_id: u for u in db.query(User) if (u.role_slug or "").startswith("hod_")}
    today = date.today()
    start = today.replace(day=1) - timedelta(days=60)
    start = start.replace(day=1)
    dept_plans, individual = 0, 0
    for dept_code, title, fluency, goals in DEV_PLANS:
        if db.query(DevelopmentPlan).filter(DevelopmentPlan.title == title).first():
            continue
        dept = depts.get(dept_code)
        owner = hods.get(dept.id) if dept else None
        statuses = ["done", "done", "in_progress", "planned", "planned", "planned"]
        db.add(DevelopmentPlan(
            employee_id=None, department_id=dept.id if dept else None, title=title,
            goals=[{"month": i + 1, "goal": g, "status": statuses[i]} for i, g in enumerate(goals)],
            ai_fluency_level=fluency, start_date=start, end_date=start + timedelta(days=182),
            progress_pct=rnd.choice([25, 35, 40, 50, 60]), status="active",
            owner_id=owner.id if owner else None))
        dept_plans += 1
    db.flush()
    # individual AI-fluency journeys so the fluency chart has a population
    have = {p.employee_id for p in db.query(DevelopmentPlan).filter(DevelopmentPlan.employee_id.isnot(None))}
    target = 20 - len(have)
    pool = [e for e in db.query(Employee).filter(Employee.status.in_(["active", "probation"]))
            .order_by(Employee.id).limit(40) if e.id not in have]
    for e in pool[:max(0, target)]:
        level = rnd.choice([1, 2, 2, 3, 3, 3, 4, 4, 5])
        db.add(DevelopmentPlan(
            employee_id=e.id, department_id=e.department_id,
            title=f"Growth journey - {e.full_name}",
            goals=[{"month": i + 1, "goal": g, "status": "done" if i < 2 else "planned"} for i, g in enumerate([
                "Use the OS for every routine task, no side spreadsheets",
                "Own one KPI end to end and explain its movement",
                "Draft one AI-assisted improvement to a daily workflow",
                "Train a colleague on the workflow you rebuilt",
                "Take a documented decision with a written rationale",
                "Run your part of the trajectory meeting unaided"])],
            ai_fluency_level=level, start_date=start, end_date=start + timedelta(days=182),
            progress_pct=rnd.choice([10, 20, 30, 45, 55, 70]), status="active", owner_id=None))
        individual += 1
    db.flush()
    return {"department": dept_plans, "individual": individual}


def seed_meetings_and_decisions(db: Session, rnd: random.Random) -> dict:
    users = [u for u in db.query(User).filter(User.is_active.is_(True)).order_by(User.id) if u.portal == "admin"]
    if not users:
        return {"meetings": 0, "decisions": 0}
    ceo = next((u for u in users if u.is_superuser), users[0])
    hods = [u for u in users if (u.role_slug or "").startswith("hod_")] or users[:5]
    depts = {d.code: d for d in db.query(Department)}
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    from app.services import jobs_ops  # noqa: F401  (keeps the agenda language aligned with the module)
    agenda = "\n".join([
        "Review of last week's decisions and actions (owner by owner)",
        "Department scorecards: score, highlights, risks",
        "Institutional health score and red KPIs",
        "Student growth, retention and churn trajectory",
        "Finance: collections, receivables, payroll ratio",
        "Quality: QA sampling, AI monitoring flags, corrective actions",
        "Transformation OS: systems moving alpha -> beta -> full launch",
        "People: transitions, development plans, AI fluency",
        "New decisions (owner + rationale recorded before the meeting ends)",
    ])
    meetings = []
    for i in range(6):
        meeting_date = monday - timedelta(days=7 * (5 - i)) if i < 5 else monday + timedelta(days=7)
        existing = db.query(TrajectoryMeeting).filter(TrajectoryMeeting.meeting_date == meeting_date).first()
        if existing:
            meetings.append(existing)
            continue
        past = meeting_date < today
        m = TrajectoryMeeting(
            meeting_date=meeting_date, title="Weekly Trajectory Meeting", agenda=agenda,
            attendees=[ceo.full_name] + [u.full_name for u in hods[:5]],
            notes=MEETING_NOTES[i % len(MEETING_NOTES)] if past else None,
            chaired_by_id=ceo.id, status="held" if past else "scheduled")
        db.add(m)
        db.flush()
        meetings.append(m)
    db.flush()

    created = 0
    past_meetings = [m for m in meetings if m.meeting_date < today]
    for i, (title, category, dept_code, rationale, status, due_off) in enumerate(DECISIONS):
        if db.query(Decision).filter(Decision.title == title).first():
            continue
        owner = users[(i * 2 + 1) % len(users)]
        meeting = past_meetings[i % len(past_meetings)] if past_meetings else None
        dept = depts.get(dept_code) if dept_code else None
        outcome = None
        if status == "implemented":
            outcome = rnd.choice([
                "Implemented and holding. Reviewed at the following trajectory meeting with no further action.",
                "Implemented. The metric moved in the expected direction within two months.",
                "Implemented with one adjustment after feedback from the department affected.",
            ])
        elif status == "reversed":
            outcome = ("Reversed after two cycles: the reconciliation cost outweighed the benefit and staff "
                       "preferred the original cadence.")
        d = Decision(title=title, category=category, rationale=rationale, owner_id=owner.id,
                     decided_by_id=ceo.id if status != "proposed" else None,
                     meeting_id=meeting.id if meeting else None,
                     department_id=dept.id if dept else None, status=status,
                     due_date=today + timedelta(days=due_off), outcome=outcome)
        db.add(d)
        created += 1
    db.flush()

    # meeting actions become tasks
    actions = 0
    action_titles = [
        ("Publish the coverage fix note to all supervisors", 3),
        ("Send the weekly collections report to the CEO", 2),
        ("Book the Hifz teacher enablement workshop", 5),
        ("Re-run the landing page test with the new creative", 7),
        ("Open transition records for both departing teachers", 4),
    ]
    for i, m in enumerate(past_meetings[:5]):
        title, offset = action_titles[i % len(action_titles)]
        if db.query(Task).filter(Task.title == title).first():
            continue
        assignee = users[(i * 3) % len(users)]
        db.add(Task(title=title, description=f"Action from the trajectory meeting of {m.meeting_date}.",
                    assignee_id=assignee.id, creator_id=ceo.id, department_id=assignee.department_id,
                    priority="high", status="done" if i < 3 else "in_progress",
                    due_date=m.meeting_date + timedelta(days=offset),
                    completed_at=datetime.combine(m.meeting_date + timedelta(days=offset - 1),
                                                  datetime.min.time()) if i < 3 else None,
                    entity_type="TrajectoryMeeting", entity_id=m.id, estimate_hours=2.0))
        actions += 1
    db.flush()
    return {"meetings": len(meetings), "decisions": created, "actions": actions}


def seed_scorecards(db: Session, rnd: random.Random) -> int:
    periods = kpi_svc.last_n_periods(3)
    depts = db.query(Department).filter(Department.is_active.is_(True)).order_by(Department.name).all()
    highlights = [
        "Class completion held above target for the whole month; two teachers moved from grade B to A.",
        "Collections improved after the weekly aging review; no invoice passed 45 days.",
        "Every new hire completed onboarding inside the checklist window.",
        "QA sampling hit 11 percent, the first month above the 10 percent target.",
        "Referral share reached a quarter of gross adds without any extra spend.",
        "All integrations healthy for 29 of 30 days; the one failure auto-recovered.",
        "Missed-class response time halved after the escalation rule went live.",
    ]
    risks = [
        "Evening shift is still single-covered; one absence puts six classes at risk.",
        "Two large accounts are drifting past 30 days and drive most of the receivable.",
        "Recruitment pipeline is thin for the supervisor role - three weeks to first interview.",
        "Repeat QA issues concentrated in two teachers; development plans opened rather than warnings.",
        "Google CPL remains double the Meta equivalent; spend paused pending the landing page test.",
        "Legacy migration has 40 duplicate client records still unresolved.",
        "Curriculum rebuild depends on two people; no bus factor cover yet.",
    ]
    created = 0
    for pi, period in enumerate(periods):
        for di, dept in enumerate(depts):
            if db.query(DepartmentScorecard).filter(DepartmentScorecard.department_id == dept.id,
                                                    DepartmentScorecard.period == period).first():
                continue
            m = kpi_svc.department_metrics(db, dept, period, live=False)
            score = m["score"] if m["score"] is not None else round(rnd.uniform(58, 88), 1)
            submitted = not (pi == len(periods) - 1 and di % 4 == 3)   # current month: a couple still outstanding
            db.add(DepartmentScorecard(
                department_id=dept.id, period=period, score=score,
                metrics={r["kpi"].code: r["value"] for r in m["rows"] if r["value"] is not None},
                highlights=highlights[(di + pi) % len(highlights)],
                risks=risks[(di + pi + 2) % len(risks)],
                submitted_by_id=dept.hod_user_id,
                submitted_at=datetime.utcnow() - timedelta(days=30 * (len(periods) - pi)) if submitted else None,
                status="submitted" if submitted else "draft"))
            created += 1
    db.flush()
    return created


def seed_daily_reports(db: Session, rnd: random.Random) -> dict:
    dept_code = {d.id: d.code for d in db.query(Department)}
    staff = [u for u in db.query(User).filter(User.is_active.is_(True)).order_by(User.id)
             if u.portal in ("admin", "teacher")][:15]
    if not staff:
        return {"created": 0, "late": 0}
    today = date.today()
    created, late_count = 0, 0
    existing = {(r.user_id, r.report_date, r.slot) for r in
                db.query(DailyReport.user_id, DailyReport.report_date, DailyReport.slot)
                .filter(DailyReport.report_date >= today - timedelta(days=15))}
    numeric_defaults = {
        "classes_planned": (4, 9), "lesson_plans": (2, 6), "classes_delivered": (3, 9),
        "sessions_scheduled": (18, 46), "missed_handled": (0, 4), "parent_calls": (2, 9),
        "invoices_due": (4, 18), "overdue_followups": (2, 11), "leads_to_contact": (8, 25),
        "leads_contacted": (6, 22), "trials_booked": (0, 5), "interviews": (0, 3), "onboarding": (0, 4),
        "reviews_planned": (3, 9), "reviews_done": (2, 8), "flags_raised": (0, 3),
        "open_incidents": (0, 2), "incidents_closed": (0, 2),
    }
    text_defaults = {
        "coverage_gaps": ["None known", "Evening 18:00 slot uncovered", "Two teachers on leave Thursday"],
        "collection_target": ["PKR 180,000", "PKR 240,000", "PKR 95,000"],
        "payments_collected": ["PKR 164,000 across 9 payments", "PKR 212,500 across 12 payments"],
        "escalations": ["One missed class escalated to the HOD", "None", "Two reassignments made"],
        "students_at_risk": ["Two students on the watchlist", "None today", "One family requested a teacher change"],
        "campaigns_live": ["Meta retargeting, Google search", "Meta prospecting only"],
        "attendance_exceptions": ["Three late marks to verify", "None", "One correction request pending"],
        "deployments": ["KPI snapshot job", "None planned", "Reports module hotfix"],
        "curriculum_notes": ["Sabaq pace on track for the Hifz cohort.",
                             "Two students moved to a slower revision cycle."],
        "finance_risks": ["Two accounts drifting past 30 days.", "None material this week."],
        "people_risks": ["Supervisor vacancy still open.", "None."],
        "conversion_notes": ["Price objection twice; timing objection three times.",
                             "Parents asking for female teachers in the evening slot."],
        "corrective_actions": ["Two opened, one closed.", "None outstanding."],
        "integration_health": ["All green.", "WhatsApp webhook retried twice, recovered."],
    }
    for day_offset in range(14, 0, -1):
        d = today - timedelta(days=day_offset)
        if d.weekday() == 6:      # Sunday off
            continue
        for i, u in enumerate(staff):
            code = dept_code.get(u.department_id, "operations")
            if code not in DAILY_FIELDS:
                code = "operations"
            for slot in ("morning", "afternoon"):
                if (u.id, d, slot) in existing:
                    continue
                # deterministic gaps so re-seeding does not fill them in
                if (u.id * 7 + day_offset * 3 + (0 if slot == "morning" else 1)) % 9 == 0:
                    continue
                fields = DAILY_FIELDS[code][0 if slot == "morning" else 1]
                content = {}
                for f in fields:
                    if f in MORNING_TEXT:
                        content[f] = rnd.choice(MORNING_TEXT[f])
                    elif f in AFTERNOON_TEXT:
                        content[f] = rnd.choice(AFTERNOON_TEXT[f])
                    elif f in numeric_defaults:
                        lo, hi = numeric_defaults[f]
                        content[f] = str(rnd.randint(lo, hi))
                    elif f in text_defaults:
                        content[f] = rnd.choice(text_defaults[f])
                is_late = rnd.random() < 0.18
                base_hour = 10 if slot == "morning" else 17
                submitted = datetime.combine(d, datetime.min.time()) + timedelta(
                    hours=base_hour, minutes=rnd.randint(5, 90) if is_late else -rnd.randint(15, 120))
                db.add(DailyReport(user_id=u.id, department_id=u.department_id, report_date=d, slot=slot,
                                   content=content,
                                   summary=(content.get("priorities") or content.get("completed") or "")[:200] or None,
                                   submitted_at=submitted, is_late=is_late))
                created += 1
                late_count += 1 if is_late else 0
        if created and created % 200 == 0:
            db.flush()
    db.flush()
    return {"created": created, "late": late_count}


def seed_report_runs(db: Session, rnd: random.Random) -> int:
    user = db.query(User).filter(User.is_superuser.is_(True)).order_by(User.id).first()
    created = 0
    for i, (key, name, fmt, rows) in enumerate(REPORT_RUNS):
        if db.query(ReportRun).filter(ReportRun.report_type == key).first():
            continue
        end = date.today() - timedelta(days=i * 3)
        db.add(ReportRun(name=name, report_type=key,
                         params={"start": (end - timedelta(days=29)).isoformat(), "end": end.isoformat()},
                         format=fmt, file_path=None, generated_by_id=user.id if user else None,
                         generated_at=datetime.utcnow() - timedelta(days=i * 3, hours=2), row_count=rows))
        created += 1
    db.flush()
    return created


def seed_insights(db: Session) -> bool:
    period = kpi_svc.resolve_period("this_month")
    if db.query(AIModelRun).filter(AIModelRun.module == "insights").first():
        return False
    try:
        from app.services import insights as insight_svc
        insight_svc.executive_insights(db, period, force=True)
        db.flush()
        return True
    except Exception:      # an insight run must never break the seed
        return False


def run(db: Session) -> None:
    rnd = random.Random(20260909)
    _setting(db, "ai_thresholds",
             {"class_monitoring": 0.8, "lead_scoring": 0.7, "churn": 0.75, "complaint_classification": 0.8,
              "lesson_recommendation": 0.65, "insights": 0.7, "sentiment": 0.7, "qa_recommendation": 0.8,
              "transcription": 0.6, "anomaly": 0.7},
             "ai", "Minimum AI confidence per module before an output may be acted on without review")
    _setting(db, "trajectory_agenda",
             ["Review of last week's decisions and actions (owner by owner)",
              "Department scorecards: score, highlights, risks",
              "Institutional health score and red KPIs",
              "Student growth, retention and churn trajectory",
              "Finance: collections, receivables, payroll ratio",
              "Quality: QA sampling, AI monitoring flags, corrective actions",
              "Transformation OS: systems moving alpha -> beta -> full launch",
              "People: transitions, development plans, AI fluency",
              "New decisions (owner + rationale recorded before the meeting ends)"],
             "governance", "Weekly trajectory meeting agenda template")
    db.flush()

    kpis = seed_kpis(db)
    values = seed_kpi_values(db, rnd)
    projects = seed_projects(db, rnd)
    tasks = seed_tasks(db, rnd, projects)
    items = seed_transformation(db, rnd)
    transitions = seed_transitions(db)
    plans = seed_development(db, rnd)
    gov = seed_meetings_and_decisions(db, rnd)
    scorecards = seed_scorecards(db, rnd)
    reports = seed_daily_reports(db, rnd)
    runs = seed_report_runs(db, rnd)
    insight = seed_insights(db)
    db.flush()

    print(f"    ops: {kpis['total']} KPIs ({kpis['created']} new), "
          f"{values['system']} computed + {values['seeded']} seeded KPI values over {values['periods']} months "
          f"({values['teacher_rows']} per-teacher), {len(projects)} projects, {tasks['created']} tasks "
          f"({tasks['escalated']} escalated, {tasks['comments']} comments), {items} transformation items, "
          f"{transitions} transitions, {plans['department']} department + {plans['individual']} individual "
          f"development plans, {gov['meetings']} trajectory meetings, {gov['decisions']} decisions, "
          f"{gov['actions']} meeting actions, {scorecards} scorecards, {reports['created']} daily reports "
          f"({reports['late']} late), {runs} report runs, insights={'yes' if insight else 'existing'}")
