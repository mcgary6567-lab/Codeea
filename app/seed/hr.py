"""HR / People & Culture / Payroll seed (Modules 20, 21, 46, 49).

Creates 45 working days of twice-daily staff attendance with corrections, employee leaves, violations,
confidential grievances, salary advances and bonuses, a recruitment pipeline with interviews, onboarding
checklists, provisioning records, teacher training and development plans, then computes teacher grades and
runs payroll for the last three months (two paid, the latest left as a draft).

Idempotent: re-running only tops up what is missing. Deterministic (fixed random seed).
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.core import User, Role, Department, Branch
from app.models.people import (Employee, Teacher, HRAttendance, Leave, Violation, Grievance, SalaryAdvance, Bonus,
                               OnboardingTask, ProvisioningRecord, RecruitmentRequest, Candidate, Interview,
                               TrainingAssignment, DevelopmentPlan, PayrollRun, SalaryStructure)
from app.services import hr as svc
from app.services import payroll as pay

rnd = random.Random(20240921)

ATTENDANCE_DAYS = 45
CORRECTION_REASONS = [
    "Internet outage at home — logged in from the backup device at 09:05 but the app did not record it.",
    "Marked absent although I taught the full evening shift; supervisor can confirm.",
    "Check-out was not recorded because the browser crashed at the end of the shift.",
    "I was on approved half day for a medical appointment.",
]
LEAVE_REASONS = [
    "Family wedding in Multan.", "Fever and doctor-advised rest.", "Annual family visit.",
    "Child's school admission interview.", "Attending a Tajweed certification workshop.",
    "Domestic emergency — water supply failure.", "Eid travel to the village.",
]
VIOLATIONS = [
    ("late", "minor", "Arrived 25 minutes after the shift start without notice.", "Verbal warning", 0),
    ("late", "minor", "Third late arrival this month.", "Written warning", 300),
    ("missed_class", "major", "Two scheduled classes missed without informing the supervisor.", "Written warning + deduction", 1500),
    ("absent", "major", "Absent without leave for a full working day.", "Deduction applied", 1800),
    ("policy", "minor", "Camera off for the whole class contrary to the live-class policy.", "Coaching session", 0),
    ("policy", "minor", "Shared a personal WhatsApp number with a parent.", "Policy refresher assigned", 0),
    ("misconduct", "major", "Raised voice at a student during a recorded session.", "Formal warning + QA re-review", 2000),
    ("late", "minor", "Late to the morning shift after a public holiday.", "Verbal warning", 0),
    ("missed_class", "minor", "One class missed; substitute arranged at short notice.", "Verbal warning", 500),
    ("policy", "major", "Class conducted from an unregistered device.", "Device policy re-signed", 1000),
    ("absent", "minor", "Half day absent without prior approval.", "Deduction applied", 900),
    ("late", "minor", "Late check-in for the PM session twice in one week.", "Verbal warning", 0),
    ("misconduct", "critical", "Discussed another employee's salary in a parent group.", "Final written warning", 3000),
    ("policy", "minor", "Lesson plan not uploaded before class for three consecutive days.", "Coaching session", 0),
    ("missed_class", "major", "Failed to join a trial class; lead was lost.", "Written warning + deduction", 2500),
]
GRIEVANCES = [
    ("workplace", "Night shift rota is being changed at short notice",
     "The night shift rota has been changed three times in the last two weeks with less than 24 hours notice. "
     "This makes it impossible to arrange childcare. I have raised it with my supervisor twice with no change.", False, "investigating"),
    ("management", "Feedback in front of colleagues",
     "During the weekly huddle my class quality was criticised in front of the whole team rather than privately. "
     "I would like coaching conversations to happen one to one.", False, "open"),
    ("pay", "Class pay not matching the classes I delivered",
     "My payslip shows fewer classes than the schedule for last month. Please review the class count and the per-class rate applied.", False, "resolved"),
    ("harassment", "Uncomfortable messages outside working hours",
     "A colleague repeatedly messages me late at night about non-work matters after I asked them to stop. "
     "I do not want my name shared while this is looked at.", True, "investigating"),
]
ADVANCES = [
    (60000, 3, "House rent deposit for a new flat.", "approved"),
    (25000, 2, "Medical treatment for my mother.", "approved"),
    (40000, 4, "Motorbike purchase for the commute.", "pending"),
    (15000, 1, "Eid expenses.", "rejected"),
    (30000, 3, "School fees for two children.", "settled"),
]
BONUSES = [
    ("performance", 12000, "Highest QA average in the quarter."),
    ("performance", 9000, "Zero missed classes for three months."),
    ("retention", 15000, "Recovered five at-risk families."),
    ("referral", 5000, "Referred a Hifz teacher who passed the demo class."),
    ("eid", 10000, "Eid-ul-Fitr bonus."),
    ("eid", 10000, "Eid-ul-Fitr bonus."),
    ("performance", 8000, "Best punctuality across the evening shift."),
    ("other", 6000, "Covered the night shift for two weeks during illness."),
]
REQUESTS = [
    ("Quran Teacher — Evening shift (female)", "academics", 3, date.today() + timedelta(days=30),
     "Waiting list for female students on QAIDA and NAZRA has grown past 20 families."),
    ("Billing Representative", "finance", 1, date.today() + timedelta(days=45),
     "Invoice follow-up backlog and growing dunning workload for UK families."),
    ("QA Officer", "qa", 1, date.today() + timedelta(days=60),
     "QA sampling target of 15% of classes cannot be met with the current team."),
]
CANDIDATES = [
    ("Hafiza Ruqayya Noor", "female", "Quran Teacher", "Referral", "hired", 8.6,
     "Aalima with six years of online teaching; excellent demo class with a 7 year old."),
    ("Ustadha Sumbal Riaz", "female", "Quran Teacher", "Facebook", "offer", 8.1,
     "Strong Tajweed, needs coaching on classroom technology."),
    ("Qari Abdul Wahab", "male", "Quran Teacher", "Madrasa network", "demo", 7.4,
     "Hafiz with ijazah; demo class scheduled with the academics head."),
    ("Ustadha Nimra Aslam", "female", "Quran Teacher", "Website", "interview", 7.0, "Two years' experience teaching Qaida."),
    ("Hafiz Umair Latif", "male", "Quran Teacher", "Referral", "screening", 6.8, "Recent Hifz graduate, no online experience yet."),
    ("Ustadha Ayesha Tariq", "female", "Quran Teacher", "Job board", "applied", None, "CV received; awaiting screening call."),
    ("Ustadha Rabia Kamal", "female", "Quran Teacher", "Facebook", "rejected", 4.2, "Audio quality and Tajweed accuracy below standard in the demo."),
    ("Mubashir Anwar", "male", "Billing Representative", "LinkedIn", "interview", 7.6, "Three years in a UK-facing collections role."),
    ("Sana Yousuf", "female", "Billing Representative", "Website", "screening", 6.5, "Strong written English, no billing background."),
    ("Danish Iqbal", "male", "QA Officer", "LinkedIn", "offer", 8.3, "QA lead at a competing academy; strong rubric experience."),
    ("Hina Zafar", "female", "QA Officer", "Referral", "applied", None, "Internal referral from the academics coordinator."),
    ("Tanveer Ahmed", "male", "QA Officer", "Job board", "rejected", 5.1, "Did not attend the scheduled interview twice."),
]
TRAININGS = [
    ("Tajweed refresher — Makharij & Sifaat", "tajweed", True), ("Advanced Hifz methodology", "methodology", True),
    ("Child engagement for online classes", "engagement", True), ("Classroom technology & platform mastery", "technology", False),
    ("Professional conduct & safeguarding", "conduct", True), ("Parent communication & difficult conversations", "conduct", False),
    ("Tarjuma & Tafseer teaching essentials", "methodology", False), ("AI fluency for teachers", "technology", False),
    ("Qaida foundations for absolute beginners", "tajweed", False), ("Retention: spotting a disengaged student early", "engagement", False),
]
PLAN_GOALS = [
    [{"goal": "Reach a QA average of 85 or above", "month": 2, "status": "in_progress"},
     {"goal": "Zero missed classes for a full quarter", "month": 3, "status": "done"},
     {"goal": "Complete the Tajweed refresher with 80%+", "month": 4, "status": "todo"},
     {"goal": "Mentor one new teacher through onboarding", "month": 6, "status": "todo"}],
    [{"goal": "Cut first-response time on parent cases to under 2 hours", "month": 1, "status": "done"},
     {"goal": "Automate the weekly dunning report", "month": 3, "status": "in_progress"},
     {"goal": "Own the monthly close checklist end to end", "month": 5, "status": "todo"}],
    [{"goal": "Run the QA calibration session each month", "month": 2, "status": "done"},
     {"goal": "Raise sampling coverage to 15% of classes", "month": 4, "status": "in_progress"},
     {"goal": "Publish a rubric refresh with the academics head", "month": 6, "status": "todo"}],
    [{"goal": "Complete AI fluency level 3", "month": 2, "status": "done"},
     {"goal": "Build one automation that saves 2 hours a week", "month": 4, "status": "in_progress"},
     {"goal": "Train the department on prompt discipline", "month": 6, "status": "todo"}],
    [{"goal": "Reduce time-to-hire below 21 days", "month": 3, "status": "in_progress"},
     {"goal": "Interview scorecards used for every candidate", "month": 2, "status": "done"},
     {"goal": "Launch the six-month growth journey for all teachers", "month": 6, "status": "todo"}],
    [{"goal": "Lead the evening shift huddle independently", "month": 2, "status": "done"},
     {"goal": "Keep class completion above 95% for the shift", "month": 4, "status": "in_progress"},
     {"goal": "Coach two grade C teachers up to grade B", "month": 6, "status": "todo"}],
]


def _working_days(n: int) -> list[date]:
    days: list[date] = []
    d = date.today() - timedelta(days=1)
    while len(days) < n:
        if d.weekday() != 6:  # Sunday closed
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)


def _seed_attendance(db: Session, employees: list[Employee]) -> int:
    """Top up twice-daily attendance for every employee; other modules may already have seeded part of it."""
    days = _working_days(ATTENDANCE_DAYS)
    existing = {(e, d, s) for e, d, s in db.query(HRAttendance.employee_id, HRAttendance.date, HRAttendance.session)}
    created = 0
    correction_pool: list[HRAttendance] = []
    for emp in employees:
        if emp.status in ("resigned", "terminated"):
            continue
        for day in days:
            if emp.join_date and emp.join_date > day:
                continue
            for session in ("am", "pm"):
                if (emp.id, day, session) in existing:
                    continue
                roll = rnd.random()
                expected = svc.expected_checkin_minutes(emp, session)
                if roll < 0.92:
                    status, late = "present", 0
                elif roll < 0.965:
                    status, late = "late", rnd.choice([12, 15, 18, 22, 25, 31, 40])
                elif roll < 0.99:
                    status, late = "absent", 0
                else:
                    status, late = "leave", 0
                row = HRAttendance(employee_id=emp.id, date=day, session=session, status=status, late_minutes=late)
                if status in ("present", "late"):
                    minutes = expected + late + (0 if status == "late" else rnd.randint(-6, 8))
                    minutes = max(0, minutes) % (24 * 60)
                    row.check_in = datetime.combine(day, time(minutes // 60, minutes % 60))
                    out = (minutes + 4 * 60 + rnd.randint(-15, 20)) % (24 * 60)
                    row.check_out = datetime.combine(day, time(out // 60, out % 60))
                    row.ip = f"39.57.{rnd.randint(1, 250)}.{rnd.randint(1, 250)}"
                db.add(row)
                created += 1
                if status in ("absent", "late") and len(correction_pool) < 40:
                    correction_pool.append(row)
    db.flush()
    if db.query(HRAttendance).filter(HRAttendance.correction_requested.is_(True)).count() >= 5:
        return created
    rnd.shuffle(correction_pool)
    for i, row in enumerate(correction_pool[:8]):
        row.correction_requested = True
        row.correction_reason = CORRECTION_REASONS[i % len(CORRECTION_REASONS)]
        if i < 4:
            row.correction_status = "pending"
        else:
            row.correction_status = "approved"
            row.status = "present"
            row.late_minutes = 0
    db.flush()
    return created


def _seed_leaves(db: Session, employees: list[Employee]) -> int:
    if db.query(Leave).filter(Leave.person_type == "employee").count() >= 10:
        return 0
    pool = [e for e in employees if e.status not in ("resigned", "terminated")]
    created = 0
    used: set[tuple[int, date]] = set()

    def add(emp: Employee, start: date, days: int, leave_type: str, status: str, reason: str, approver: User | None):
        nonlocal created
        end = start + timedelta(days=days - 1)
        if (emp.id, start) in used:
            return
        used.add((emp.id, start))
        db.add(Leave(person_type="employee", employee_id=emp.id, leave_type=leave_type, start_date=start, end_date=end,
                     reason=reason, status=status, requested_by_id=emp.user_id,
                     approved_by_id=approver.id if (approver and status in ("approved", "rejected")) else None,
                     approved_at=datetime.utcnow() - timedelta(days=2) if status in ("approved", "rejected") else None,
                     reminder_sent_start=status == "approved" and start < date.today(),
                     reminder_sent_end=status == "approved" and end < date.today()))
        created += 1

    approver = db.query(User).filter(User.email == "hr@oqc.local").first()
    for i in range(14):  # past approved leave
        emp = pool[(i * 3) % len(pool)]
        start = date.today() - timedelta(days=rnd.randint(10, 120))
        add(emp, start, rnd.choice([1, 1, 2, 3, 5]), rnd.choice(["casual", "sick", "casual", "annual"]),
            "approved", LEAVE_REASONS[i % len(LEAVE_REASONS)], approver)
    for i in range(3):  # pending
        emp = pool[(i * 7 + 1) % len(pool)]
        start = date.today() + timedelta(days=rnd.randint(3, 21))
        add(emp, start, rnd.choice([1, 2, 4]), rnd.choice(["casual", "annual", "emergency"]),
            "pending", LEAVE_REASONS[(i + 2) % len(LEAVE_REASONS)], None)
    emp = pool[5 % len(pool)]
    add(emp, date.today() + timedelta(days=6), 6, "annual", "rejected",
        "Six days off during the monthly test week.", approver)
    # somebody away right now
    add(pool[2 % len(pool)], date.today() - timedelta(days=1), 3, "sick", "approved",
        "Dengue fever — doctor's note on file.", approver)
    db.flush()
    return created


def _seed_violations(db: Session, employees: list[Employee]) -> int:
    if db.query(Violation).count() >= 10:
        return 0
    pool = [e for e in employees if e.status not in ("resigned", "terminated")]
    reporter = db.query(User).filter(User.email == "hrofficer@oqc.local").first() or db.query(User).filter(User.is_superuser.is_(True)).first()
    created = 0
    for i, (vtype, severity, description, action, deduction) in enumerate(VIOLATIONS):
        emp = pool[(i * 2 + 1) % len(pool)] if i < 12 else pool[3 % len(pool)]  # a couple of repeat offenders
        day = date.today() - timedelta(days=rnd.randint(3, 80))
        db.add(Violation(employee_id=emp.id, violation_type=vtype, severity=severity, description=description,
                         action_taken=action, reported_by_id=reporter.id if reporter else None, date=day,
                         status="closed" if i % 3 == 0 else "open", deduction_amount=deduction))
        created += 1
    db.flush()
    return created


def _seed_grievances(db: Session, employees: list[Employee]) -> int:
    if db.query(Grievance).filter(Grievance.subject == GRIEVANCES[0][1]).first():
        return 0
    handler = db.query(User).filter(User.email == "hr@oqc.local").first()
    pool = [e for e in employees if e.user_id]
    created = 0
    for i, (category, subject, description, anonymous, status) in enumerate(GRIEVANCES):
        emp = pool[(i * 4) % len(pool)] if pool else None
        raised = datetime.utcnow() - timedelta(days=rnd.randint(2, 20))
        g = Grievance(employee_id=None if anonymous else (emp.id if emp else None),
                      submitted_by_id=None if anonymous else (emp.user_id if emp else None),
                      is_anonymous=anonymous, category=category, subject=subject, description=description,
                      status=status, handler_id=handler.id if handler and status != "open" else None,
                      sla_due_at=raised + timedelta(days=svc.GRIEVANCE_SLA_DAYS))
        g.created_at = raised
        if status == "resolved":
            g.resolution = ("Class count re-checked against the schedule: three classes had not been marked done by the teacher. "
                            "The payslip has been adjusted in the next run and the marking process was clarified with the shift supervisor.")
            g.resolved_at = raised + timedelta(days=3)
        db.add(g)
        created += 1
    db.flush()
    return created


def _seed_advances_bonuses(db: Session, employees: list[Employee]) -> tuple[int, int]:
    pool = [e for e in employees if e.status not in ("resigned", "terminated")]
    approver = db.query(User).filter(User.email == "finance@oqc.local").first() or db.query(User).filter(User.is_superuser.is_(True)).first()
    n_adv = n_bonus = 0
    if db.query(SalaryAdvance).count() < 3:
        for i, (amount, installments, reason, status) in enumerate(ADVANCES):
            emp = pool[(i * 5 + 2) % len(pool)]
            remaining = amount if status == "approved" else (0 if status in ("settled", "rejected") else amount)
            db.add(SalaryAdvance(employee_id=emp.id, amount=amount, currency=emp.currency, reason=reason,
                                 installments=installments, remaining=remaining, status=status,
                                 approved_by_id=approver.id if status in ("approved", "settled", "rejected") and approver else None))
            n_adv += 1
    if db.query(Bonus).count() < 5:
        periods = [(date.today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m"),
                   (date.today().replace(day=1) - timedelta(days=32)).strftime("%Y-%m")]
        for i, (btype, amount, reason) in enumerate(BONUSES):
            emp = pool[(i * 3 + 4) % len(pool)]
            status = "approved" if i < 6 else ("pending" if i == 6 else "rejected")
            db.add(Bonus(employee_id=emp.id, amount=amount, currency=emp.currency, bonus_type=btype, reason=reason,
                         period=periods[i % len(periods)], status=status,
                         approved_by_id=approver.id if status != "pending" and approver else None))
            n_bonus += 1
    db.flush()
    return n_adv, n_bonus


def _seed_recruitment(db: Session, depts: dict) -> tuple[int, int, int]:
    if db.query(RecruitmentRequest).count() >= 3:
        return 0, 0, 0
    hr_user = db.query(User).filter(User.email == "hr@oqc.local").first()
    ceo = db.query(User).filter(User.is_superuser.is_(True)).first()
    requests = []
    for title, dept_code, positions, target, description in REQUESTS:
        r = RecruitmentRequest(title=title, department_id=depts[dept_code].id if dept_code in depts else None,
                               requested_by_id=hr_user.id if hr_user else None, positions=positions,
                               description=description, status="approved", approved_by_id=ceo.id if ceo else None,
                               target_date=target)
        r.created_at = datetime.utcnow() - timedelta(days=rnd.randint(25, 70))
        db.add(r)
        requests.append(r)
    db.flush()
    interviewers = db.query(User).filter(User.email.in_(["hr@oqc.local", "academics@oqc.local", "qa@oqc.local", "hrofficer@oqc.local"])).all()
    n_cand = n_int = 0
    for i, (name, gender, applied_for, source, stage, score, notes) in enumerate(CANDIDATES):
        req = requests[0] if "Teacher" in applied_for else (requests[1] if "Billing" in applied_for else requests[2])
        c = Candidate(request_id=req.id, full_name=name, email=f"{name.split()[-1].lower()}{i}@example.com",
                      phone=f"+92 3{rnd.randint(0, 4)}{rnd.randint(1000000, 9999999)}", gender=gender,
                      applied_for=applied_for, source=source, stage=stage, score=score, notes=notes)
        c.created_at = datetime.utcnow() - timedelta(days=rnd.randint(8, 60))
        db.add(c)
        db.flush()
        n_cand += 1
        if stage in ("interview", "demo", "offer", "hired", "rejected"):
            types = ["screening"] + (["demo_class"] if "Teacher" in applied_for else ["technical"])
            if stage in ("offer", "hired"):
                types.append("final")
            for k, itype in enumerate(types):
                at = datetime.utcnow() - timedelta(days=rnd.randint(2, 25), hours=rnd.randint(0, 6))
                done = stage != "interview" or k == 0
                db.add(Interview(candidate_id=c.id, interviewer_id=interviewers[(i + k) % len(interviewers)].id if interviewers else None,
                                 scheduled_at=at, interview_type=itype, status="completed" if done else "scheduled",
                                 score=round((score or 6.5) + rnd.uniform(-0.8, 0.8), 1) if done else None,
                                 feedback=("Clear recitation and a calm manner with the child; follow-up on lesson pacing."
                                           if done and "Teacher" in applied_for else
                                           ("Structured answers, good written English." if done else None))))
                n_int += 1
    db.flush()
    return len(requests), n_cand, n_int


def _seed_onboarding(db: Session, employees: list[Employee], user: User | None) -> int:
    recent = sorted(employees, key=lambda e: (e.join_date or date.min), reverse=True)[:3]
    created = 0
    for e in recent:
        tasks = svc.create_onboarding_tasks(db, e, user)
        for i, t in enumerate(tasks):
            if i < 5:
                t.status = "completed"
                t.completed_at = datetime.utcnow() - timedelta(days=rnd.randint(1, 20))
        created += len(tasks)
    db.flush()
    return created


def _seed_provisioning(db: Session, employees: list[Employee], user: User | None) -> int:
    if db.query(ProvisioningRecord).count() >= 10:
        return 0
    created = 0
    for e in employees:
        if db.query(ProvisioningRecord).filter(ProvisioningRecord.employee_id == e.id,
                                               ProvisioningRecord.action == "onboard").first():
            continue
        rec = svc.provision(db, e, "onboard", user)
        rec.created_at = datetime.combine(e.join_date or date.today(), time(9, 30))
        created += 1
    db.flush()
    return created


def _seed_leaver(db: Session, depts: dict, user: User | None) -> Employee | None:
    existing = db.query(Employee).filter(Employee.full_name == "Faizan Sheikh").first()
    if existing:
        return existing
    data = {"full_name": "Faizan Sheikh", "designation": "Academic Coordinator (Night Group)",
            "department_id": depts["academics"].id if "academics" in depts else None,
            "gender": "male", "email": "faizan.sheikh@oqc.local", "phone": "+92 3331234567",
            "join_date": date.today() - timedelta(days=420), "employment_type": "full_time",
            "shift": "night", "base_salary": 55000, "status": "active"}
    emp, _ = svc.create_employee(db, data, user, create_login=False)
    svc.provision(db, emp, "onboard", user)
    emp.exit_reason = "Resigned to join a family business in Dubai."
    emp.exit_date = date.today() - timedelta(days=12)
    svc.provision(db, emp, "offboard", user)
    db.flush()
    return emp


def _seed_teacher_development(db: Session, teachers: list[Teacher], employees: list[Employee], user: User | None) -> tuple[int, int]:
    n_train = 0
    if db.query(TrainingAssignment).count() < 24:
        for i in range(15):
            t = teachers[i % len(teachers)]
            title, category, gate = TRAININGS[i % len(TRAININGS)]
            if db.query(TrainingAssignment).filter(TrainingAssignment.teacher_id == t.id,
                                                   TrainingAssignment.title == title).first():
                continue
            due = date.today() + timedelta(days=rnd.randint(-40, 45))
            if i % 4 == 0:
                status, score = "completed", rnd.randint(72, 96)
            elif i % 4 == 1:
                status, score = "in_progress", None
            elif i % 4 == 2:
                status, score = "assigned", None
            else:
                status, score = ("failed", rnd.randint(38, 58)) if i % 8 == 3 else ("completed", rnd.randint(70, 90))
            db.add(TrainingAssignment(teacher_id=t.id, title=title, category=category,
                                      description=f"Assigned after the {('QA review' if gate else 'quarterly development check')} for {t.full_name}.",
                                      assigned_by_id=user.id if user else None, due_date=due, status=status, score=score,
                                      is_promotion_gate=gate,
                                      completed_at=datetime.utcnow() - timedelta(days=rnd.randint(1, 30)) if status in ("completed", "failed") else None))
            n_train += 1
    n_plans = 0
    if db.query(DevelopmentPlan).count() < 4:
        pool = [e for e in employees if e.status not in ("resigned", "terminated")]
        for i, goals in enumerate(PLAN_GOALS):
            emp = pool[(i * 5) % len(pool)]
            if db.query(DevelopmentPlan).filter(DevelopmentPlan.employee_id == emp.id).first():
                continue
            start = date.today() - timedelta(days=rnd.randint(30, 120))
            done = sum(1 for g in goals if g["status"] == "done")
            db.add(DevelopmentPlan(employee_id=emp.id, department_id=emp.department_id,
                                   title=f"Six-month growth journey — {emp.full_name}", goals=goals,
                                   ai_fluency_level=rnd.randint(1, 5), start_date=start,
                                   end_date=start + timedelta(days=182),
                                   progress_pct=round(100 * done / len(goals)), status="active",
                                   owner_id=user.id if user else None))
            n_plans += 1
    db.flush()
    return n_train, n_plans


def _seed_payroll(db: Session, user: User | None) -> list[str]:
    periods = []
    first = date.today().replace(day=1)
    for back in range(3, 0, -1):
        d = first
        for _ in range(back):
            d = (d - timedelta(days=1)).replace(day=1)
        periods.append(d.strftime("%Y-%m"))
    done = []
    for i, period in enumerate(periods):
        existing = db.query(PayrollRun).filter(PayrollRun.period == period).first()
        if existing and existing.status in ("approved", "paid"):
            done.append(f"{period}:{existing.status}")
            continue
        run = pay.generate_payroll(db, period, user)
        if i < 2:  # the two older runs are approved and paid
            pay.approve_payroll(db, run, user, rationale="Month-end payroll reviewed against attendance and class counts.")
            pay.mark_paid(db, run, user)
        else:
            pay.generate_run_pdfs(db, run)
        done.append(f"{period}:{run.status}")
    db.flush()
    return done


def _seed_settings(db: Session) -> None:
    from app.models.core import Setting
    if not db.query(Setting).filter(Setting.key == "leave_allowance").first():
        db.add(Setting(key="leave_allowance", value=dict(svc.DEFAULT_LEAVE_ALLOWANCE), group="hr",
                       description="Annual leave allowance per employee by leave type (days)"))
    db.flush()


def run(db: Session) -> None:
    depts = {d.code: d for d in db.query(Department)}
    admin = db.query(User).filter(User.is_superuser.is_(True)).order_by(User.id).first()
    _seed_settings(db)

    leaver = _seed_leaver(db, depts, admin)
    employees = db.query(Employee).order_by(Employee.id).all()
    teachers = db.query(Teacher).order_by(Teacher.id).all()
    active = [e for e in employees if e.status not in ("resigned", "terminated")]

    n_att = _seed_attendance(db, active)
    n_leave = _seed_leaves(db, active)
    n_viol = _seed_violations(db, active)
    n_griev = _seed_grievances(db, active)
    n_adv, n_bonus = _seed_advances_bonuses(db, active)
    n_req, n_cand, n_int = _seed_recruitment(db, depts)
    n_tasks = _seed_onboarding(db, active, admin)
    n_prov = _seed_provisioning(db, employees, admin)
    n_train, n_plans = _seed_teacher_development(db, teachers, active, admin)
    db.commit()

    for t in teachers:
        pay.compute_teacher_grade(db, t, admin)
    db.commit()

    runs = _seed_payroll(db, admin)
    db.commit()

    print(f"    hr: {n_att} attendance sessions, {n_leave} leaves, {n_viol} violations, {n_griev} grievances, "
          f"{n_adv} advances, {n_bonus} bonuses, {n_req} requests / {n_cand} candidates / {n_int} interviews, "
          f"{n_tasks} onboarding tasks, {n_prov} provisioning records, {n_train} trainings, {n_plans} plans, "
          f"payroll {', '.join(runs)}" + (f", leaver {leaver.employee_code}" if leaver else ""))
