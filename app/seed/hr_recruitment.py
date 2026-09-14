"""Recruitment and Hiring seed (audit docs/AUDIT_HUMAN_RESOURCE.md, "Recruitment and Hiring" and "Payroll").

Six job requisitions for the roles the college actually hires, three interview panels drawn from the existing
staff users, about sixty job applications spread across the whole ten-step pipeline with realistic candidate
details, interviews booked against the panels for everybody sitting at an interview step, and payroll runs for
the last six months in the ERP's own words (Pending / Generated / Posted) with a description each.

Idempotent: every block checks what is already there before inserting. Deterministic (fixed random seed).
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.core import Department, User
from app.models.hr_erp import APPLICATION_STATUSES, InterviewPanel, JobApplication
from app.models.people import Candidate, Employee, Interview, PayrollRun, RecruitmentRequest
from app.services import payroll as pay

rnd = random.Random(20260914)

# title, department code, positions, job type, categories, skills, location, days ago opened, run days
REQUISITIONS = [
    ("Female Quran Teacher", "academics", 6, "full_time", ["Quran", "Tajweed", "Hifz"],
     ["Tajweed", "Hifz", "Child engagement", "Spoken English"], "Remote", 95, 120,
     "Female teachers for the morning and night shifts, teaching Qaida, Nazra and Hifz to children in the UK and US."),
    ("Male Quran Teacher", "academics", 4, "full_time", ["Quran", "Tajweed", "Islamic Studies"],
     ["Tajweed", "Qiraat", "Classroom management"], "Head Office - Lahore", 80, 120,
     "Male teachers for the night shift, mainly Nazra with Tajweed and Hifz revision."),
    ("Teacher Trainer", "academics", 2, "full_time", ["Training", "Tajweed", "Academics"],
     ["Tajweed certification", "Lesson planning", "Coaching", "Assessment"], "Head Office - Lahore", 60, 90,
     "Runs the Ustaadh Lab: onboarding training, monthly refreshers and demo-class coaching."),
    ("Sales Executive", "marketing", 3, "full_time", ["Sales"],
     ["Outbound calling", "CRM", "Spoken English", "Closing"], "Head Office - Lahore", 45, 75,
     "Handles trial bookings and conversion for the UK and US markets on the evening shift."),
    ("HR", "people", 1, "full_time", ["Human Resource", "Administration"],
     ["Recruitment", "Attendance", "Payroll", "Employee relations"], "Head Office - Lahore", 35, 60,
     "People and Culture officer covering attendance, leave, payroll input and staff records."),
    ("Teacher Assessment Officer", "qa", 2, "part_time", ["Quality Assurance", "Academics"],
     ["Recitation assessment", "Report writing", "Tajweed"], "Remote", 25, 60,
     "Assesses recorded classes and monthly tests, and writes the teacher assessment reports."),
]

PANELS = [
    ("Academics Panel", "Interviews teaching and training roles: recitation, Tajweed and demo class.",
     ["academics@oqc.local", "qa@oqc.local", "hr@oqc.local"]),
    ("Management Panel", "Final interviews for every role, chaired by People & Culture.",
     ["hr@oqc.local", "manager@oqc.local", "admin@oqc.local"]),
    ("Sales & Support Panel", "Interviews sales, admin and support roles.",
     ["marketing@oqc.local", "closer@oqc.local", "hrofficer@oqc.local"]),
]

FIRST_M = ["Muhammad", "Ahmed", "Bilal", "Usman", "Hamza", "Abdullah", "Talha", "Zeeshan", "Owais", "Saad",
           "Imran", "Faisal", "Noman", "Kashif", "Rizwan", "Adnan"]
FIRST_F = ["Ayesha", "Fatima", "Maryam", "Hafsa", "Zainab", "Amna", "Sana", "Iqra", "Rabia", "Sidra",
           "Nimra", "Sumaira", "Khadija", "Bushra", "Anum", "Mahnoor"]
LAST = ["Khan", "Ahmed", "Iqbal", "Raza", "Hussain", "Malik", "Butt", "Chaudhry", "Siddiqui", "Farooq",
        "Nawaz", "Shah", "Qureshi", "Javed", "Aslam", "Rashid"]
FATHER = ["Abdul Rehman", "Muhammad Aslam", "Ghulam Nabi", "Muhammad Yousaf", "Abdul Sattar", "Khalid Mehmood",
          "Muhammad Anwar", "Shabbir Ahmed", "Tariq Mahmood", "Nasir Mehmood"]
CITIES = ["Lahore", "Karachi", "Islamabad", "Rawalpindi", "Faisalabad", "Multan", "Gujranwala", "Peshawar",
          "Sialkot", "Bahawalpur"]
QUALIFICATIONS = ["Hafiz-e-Quran, Shahadat-ul-Alamia", "MA Islamic Studies", "BS Computer Science",
                  "Hafiz-e-Quran, BA", "Qari Sanad with Tajweed", "MBA Marketing", "BBA Human Resource",
                  "M.Ed", "BS Psychology", "Shahadat-ul-Alamia (Wifaq-ul-Madaris)", "Intermediate + Qaida Sanad"]
SOURCES = ["Website", "Referral", "Facebook", "LinkedIn", "Walk-in", "Madrasa network", "Job board"]
# how many applications sit at each step of the pipeline (60 in total)
PIPELINE_PLAN = {
    "applied": 14, "on_hold": 5, "initial_selected": 8, "pre_selected": 6, "marked_1st_interview": 5,
    "marked_2nd_interview": 4, "marked_final_interview": 3, "selected": 4, "rejected": 8, "hired": 3,
}
INTERVIEW_STEPS = {"marked_1st_interview": ["screening"], "marked_2nd_interview": ["screening", "technical"],
                   "marked_final_interview": ["screening", "technical", "final"],
                   "selected": ["screening", "technical", "final"], "hired": ["screening", "demo_class", "final"]}
LOCATIONS = ["Head Office - Lahore", "Online - Google Meet", "Head Office - Lahore (Room 2)", "Campus - Rawalpindi"]
FEEDBACK = [
    "Clear recitation with correct Makharij; calm and patient with children.",
    "Strong Tajweed but needs work on lesson pacing for beginners.",
    "Confident spoken English and good rapport on the call.",
    "Sound knowledge, limited experience with online teaching tools.",
    "Well prepared demo class; used the whiteboard well.",
    "Answers were structured and honest; good cultural fit for the night shift.",
]


def _users_by_email(db: Session) -> dict[str, User]:
    return {u.email: u for u in db.query(User).filter(User.email.isnot(None))}


def _requisitions(db: Session, depts: dict[str, Department]) -> list[RecruitmentRequest]:
    hr_user = db.query(User).filter(User.email == "hr@oqc.local").first()
    ceo = db.query(User).filter(User.is_superuser.is_(True)).order_by(User.id).first()
    out: list[RecruitmentRequest] = []
    for title, dept, positions, job_type, categories, skills, location, ago, run_days, description in REQUISITIONS:
        r = db.query(RecruitmentRequest).filter(RecruitmentRequest.title == title).first()
        created = False
        if r is None:
            r = RecruitmentRequest(title=title, requested_by_id=hr_user.id if hr_user else None,
                                   approved_by_id=ceo.id if ceo else None, status="approved")
            r.created_at = datetime.utcnow() - timedelta(days=ago)
            db.add(r)
            created = True
        if created or not r.job_categories:
            start = date.today() - timedelta(days=ago)
            r.department_id = depts[dept].id if dept in depts else r.department_id
            r.positions = positions
            r.description = r.description or description
            r.job_type = job_type
            r.job_categories = list(categories)
            r.skills = list(skills)
            r.job_location = location
            r.start_date = start
            r.end_date = start + timedelta(days=run_days)
            r.target_date = r.target_date or (start + timedelta(days=run_days))
        out.append(r)
    db.flush()
    return out


def _panels(db: Session) -> list[InterviewPanel]:
    users = _users_by_email(db)
    out: list[InterviewPanel] = []
    for name, description, emails in PANELS:
        p = db.query(InterviewPanel).filter(InterviewPanel.name == name).first()
        member_ids = [users[e].id for e in emails if e in users]
        if p is None:
            p = InterviewPanel(name=name, description=description, member_ids=member_ids, status="active")
            db.add(p)
        elif not p.member_ids:
            p.member_ids = member_ids
        out.append(p)
    db.flush()
    return out


def _panel_for(req: RecruitmentRequest, panels: list[InterviewPanel]) -> InterviewPanel:
    title = (req.title or "").lower()
    if "sales" in title or "hr" in title.split():
        return panels[2] if len(panels) > 2 else panels[0]
    if "trainer" in title or "assessment" in title:
        return panels[1] if len(panels) > 1 else panels[0]
    return panels[0]


def _applications(db: Session, reqs: list[RecruitmentRequest], panels: list[InterviewPanel]) -> tuple[int, int, int]:
    if db.query(JobApplication).count() >= 50:
        return 0, 0, 0
    hire_pool = db.query(Employee).filter(Employee.status.in_(["active", "probation"]))\
        .order_by(Employee.join_date.desc()).limit(12).all()
    plan: list[str] = []
    for status, n in PIPELINE_PLAN.items():
        plan.extend([status] * n)
    n_app = n_cand = n_iv = 0
    hires_made = 0
    for i, status in enumerate(plan):
        req = reqs[i % len(reqs)]
        female = "female" in (req.title or "").lower() or rnd.random() < 0.45
        gender = "female" if female else "male"
        first = rnd.choice(FIRST_F if female else FIRST_M)
        name = f"{first} {rnd.choice(LAST)}"
        applied_on = date.today() - timedelta(days=rnd.randint(3, 85))
        panel = _panel_for(req, panels)
        needs_panel = status in INTERVIEW_STEPS
        a = JobApplication(
            request_id=req.id, application_type="profile" if i % 5 else "non_profile", full_name=name,
            father_name=rnd.choice(FATHER), gender=gender, department_id=req.department_id,
            application_date=applied_on, nic_number=f"3520{rnd.randint(1000000, 9999999)}{rnd.randint(0, 9)}",
            cell_no=f"+92 3{rnd.randint(0, 4)}{rnd.randint(1000000, 9999999)}",
            email=f"{first.lower()}.{i}@example.com", qualification=rnd.choice(QUALIFICATIONS),
            experience_years=round(rnd.choice([0, 0.5, 1, 1.5, 2, 3, 4, 5, 7]) + rnd.choice([0, 0.5]), 1),
            expected_salary=rnd.choice([25000, 30000, 35000, 40000, 45000, 55000, 65000]),
            city=rnd.choice(CITIES), source=rnd.choice(SOURCES), status=status,
            panel_id=panel.id if needs_panel else None,
            remarks=f"[{applied_on}] Applied through {'the website form' if i % 2 else 'a staff referral'}.")
        db.add(a)
        db.flush()
        n_app += 1

        if status in ("selected", "hired", "rejected") or needs_panel:
            c = Candidate(request_id=req.id, full_name=name, email=a.email, phone=a.cell_no, gender=gender,
                          applied_for=req.title, source=a.source,
                          stage={"hired": "hired", "rejected": "rejected", "selected": "offer"}.get(status, "interview"),
                          notes=f"{a.qualification} · {a.city} · {a.experience_years:g} year(s) experience")
            c.created_at = datetime.combine(applied_on, datetime.min.time())
            db.add(c)
            db.flush()
            a.candidate_id = c.id
            n_cand += 1

            if status == "hired" and hires_made < len(hire_pool):
                emp = hire_pool[hires_made]
                a.hired_employee_id = emp.id
                c.hired_employee_id = emp.id
                hires_made += 1

            for step, itype in enumerate(INTERVIEW_STEPS.get(status, [])):
                at = datetime.combine(applied_on + timedelta(days=7 + step * 6),
                                      datetime.min.time().replace(hour=rnd.choice([10, 11, 14, 16])))
                if at > datetime.utcnow() + timedelta(days=10):
                    at = datetime.utcnow() + timedelta(days=rnd.randint(1, 6))
                done = at < datetime.utcnow()
                location = rnd.choice(LOCATIONS)
                for uid in (panel.member_ids or []):
                    notes = rnd.choice(FEEDBACK) if done else ""
                    iv = Interview(candidate_id=c.id, interviewer_id=uid, scheduled_at=at, interview_type=itype,
                                   status="completed" if done else "scheduled",
                                   score=round(rnd.uniform(5.5, 9.5), 1) if done else None,
                                   feedback=(f"LOC: {location}\n{notes}".strip() if location else notes) or None)
                    db.add(iv)
                    n_iv += 1
    db.flush()
    return n_app, n_cand, n_iv


def _payroll(db: Session, user: User | None) -> list[str]:
    """Six months of payroll in the ERP's words, each with a description."""
    first = date.today().replace(day=1)
    periods = []
    d = first
    for _ in range(6):
        d = (d - timedelta(days=1)).replace(day=1)
        periods.append(d.strftime("%Y-%m"))
    periods.reverse()
    out = []
    for period in periods:
        run = db.query(PayrollRun).filter(PayrollRun.period == period).order_by(PayrollRun.id.desc()).first()
        month_name = datetime.strptime(period, "%Y-%m").strftime("%B %Y")
        if run is None:
            run = pay.erp_create_run(db, period, f"Staff and teacher salaries - {month_name}", user)
            run = pay.erp_generate_run(db, run, user)
            if user is not None and pay.can_approve_payroll(user):
                pay.erp_post_run(db, run, user, rationale="Historic month reconciled and posted.")
        else:
            if not run.description:
                run.description = f"Staff and teacher salaries - {month_name}"
            if run.status in ("approved", "paid"):
                run.status = "posted"
            elif run.status == "pending_approval":
                run.status = "generated"
            elif run.status == "draft":
                run.status = "generated" if run.payslips else "pending"
        out.append(f"{period}:{run.status}")
    db.flush()
    return out


def run(db: Session) -> None:
    depts = {d.code: d for d in db.query(Department)}
    admin = db.query(User).filter(User.is_superuser.is_(True)).order_by(User.id).first()

    reqs = _requisitions(db, depts)
    panels = _panels(db)
    db.commit()

    n_app, n_cand, n_iv = _applications(db, reqs, panels)
    db.commit()

    runs = _payroll(db, admin)
    db.commit()

    print(f"    hr_recruitment: {len(reqs)} job requisitions, {len(panels)} interview panels, {n_app} applications, "
          f"{n_cand} candidates, {n_iv} interview seats, payroll {', '.join(runs)}")
