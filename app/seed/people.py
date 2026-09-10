"""People seed: teachers (with users + employees), staff employees, clients (parents) with portal users, students."""
from __future__ import annotations

import random
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.academic import Course, Division
from app.models.core import User, Role, Department, Branch
from app.models.people import Teacher, Employee, Client, Student, Household, SalaryStructure

rnd = random.Random(42)

TEACHERS = [  # name, gender, courses, shift, grade
    ("Qari Muhammad Saleem", "male", ["QAIDA", "NAZRA", "TAJWEED"], "evening", "A"),
    ("Hafiz Abdullah Khan", "male", ["HIFZ", "NAZRA"], "night", "A"),
    ("Ustadha Amina Farooq", "female", ["QAIDA", "NAZRA", "ISLAMIC"], "evening", "A"),
    ("Qari Shahid Mehmood", "male", ["TAJWEED", "NAZRA", "TARJUMA"], "morning", "B"),
    ("Ustadha Khadija Bibi", "female", ["QAIDA", "NAZRA"], "night", "B"),
    ("Hafiz Ibrahim Yusuf", "male", ["HIFZ", "TAJWEED"], "evening", "B"),
    ("Ustadha Zainab Ali", "female", ["QAIDA", "ISLAMIC", "TARJUMA"], "morning", "B"),
    ("Qari Adnan Rasheed", "male", ["NAZRA", "TAJWEED"], "night", "C"),
    ("Ustadha Maryam Siddique", "female", ["QAIDA", "NAZRA", "HIFZ"], "evening", "A"),
    ("Hafiz Bilal Nasir", "male", ["HIFZ", "NAZRA"], "night", "B"),
    ("Ustadha Hafsa Rehman", "female", ["QAIDA", "NAZRA", "TAJWEED"], "evening", "B"),
    ("Qari Junaid Akram", "male", ["NAZRA", "TARJUMA", "ISLAMIC"], "morning", "C"),
]

STAFF = [  # email (existing user), designation, dept
    ("hr@oqc.local", "Head of People & Culture", "people"), ("finance@oqc.local", "Head of Finance", "finance"),
    ("academics@oqc.local", "Head of Academics", "academics"), ("qa@oqc.local", "Head of QA", "qa"),
    ("tech@oqc.local", "Head of Technology", "technology"), ("marketing@oqc.local", "Head of Marketing", "marketing"),
    ("manager@oqc.local", "Operations Manager (Morning Group)", "operations"), ("manager2@oqc.local", "Operations Manager (Night Group)", "operations"),
    ("supervisor@oqc.local", "Supervisor — Evening Shift", "operations"), ("supervisor2@oqc.local", "Supervisor — Night Shift", "operations"),
    ("billing@oqc.local", "Billing Representative", "finance"), ("leadgen@oqc.local", "Lead Generator", "marketing"),
    ("closer@oqc.local", "Lead Closer", "marketing"), ("accountant@oqc.local", "Accountant", "finance"),
    ("qaofficer@oqc.local", "QA Officer", "qa"), ("hrofficer@oqc.local", "HR Officer", "people"),
    ("coordinator@oqc.local", "Academic Coordinator", "academics"), ("sysadmin@oqc.local", "System Administrator", "technology"),
]

COUNTRIES = [("United Kingdom", "Europe/London", "GBP", "+44 7"), ("United States", "America/New_York", "USD", "+1 2"),
             ("Canada", "America/Toronto", "CAD", "+1 4"), ("Australia", "Australia/Sydney", "AUD", "+61 4"),
             ("United Kingdom", "Europe/London", "GBP", "+44 7"), ("United Kingdom", "Europe/London", "GBP", "+44 7"),
             ("Pakistan", "Asia/Karachi", "PKR", "+92 3")]

FIRST_M = ["Ahmed", "Yusuf", "Ibrahim", "Hamza", "Zayd", "Bilal", "Umar", "Ali", "Musa", "Idris", "Adam", "Ismail", "Talha", "Saad", "Rayyan"]
FIRST_F = ["Maryam", "Aisha", "Fatima", "Zainab", "Hafsa", "Sumayya", "Khadija", "Amina", "Safiya", "Hana", "Zara", "Iman", "Noor", "Layla", "Sara"]
FAMILY = ["Khan", "Ahmed", "Malik", "Hussain", "Siddiqui", "Chaudhry", "Rahman", "Iqbal", "Shaikh", "Qureshi", "Butt", "Mirza", "Farooqi", "Hashmi",
          "Ansari", "Baig", "Raza", "Javed", "Akhtar", "Nawaz", "Abbasi", "Awan", "Bhatti", "Dar", "Gill", "Hamid", "Kazmi", "Lodhi", "Naqvi", "Zaidi"]
PARENT_FIRST = ["Mohammed", "Abdul", "Tariq", "Imran", "Nadeem", "Shahid", "Kashif", "Asif", "Rizwan", "Faisal", "Sajid", "Waqar", "Salman", "Zubair", "Farhan",
                "Samina", "Rukhsana", "Nasreen", "Shabana", "Farzana", "Bushra", "Uzma", "Saima", "Ambreen", "Nazia", "Sadia", "Rabia", "Huma", "Fouzia", "Asma"]


def _teacher_user(db: Session, idx: int, name: str, role: Role, dept: Department, branch: Branch) -> User:
    email = f"teacher{idx}@oqc.local"
    u = db.query(User).filter(User.email == email).first()
    if not u:
        u = User(email=email, username=f"teacher{idx}", full_name=name, hashed_password=hash_password("Teacher@123"),
                 role_id=role.id, department_id=dept.id, branch_id=branch.id, timezone="Asia/Karachi")
        db.add(u)
        db.flush()
    return u


def run(db: Session) -> None:
    roles = {r.slug: r for r in db.query(Role)}
    depts = {d.code: d for d in db.query(Department)}
    branch = db.query(Branch).first()
    courses = {c.code: c for c in db.query(Course)}
    supervisors = [u for u in db.query(User).filter(User.email.in_(["supervisor@oqc.local", "supervisor2@oqc.local"]))]

    # ---- staff employees
    for i, (email, designation, dept) in enumerate(STAFF, start=1):
        u = db.query(User).filter(User.email == email).first()
        if not u or db.query(Employee).filter(Employee.user_id == u.id).first():
            continue
        base = {"people": 120000, "finance": 110000, "academics": 115000, "qa": 105000, "technology": 130000, "marketing": 110000, "operations": 85000}[dept]
        if "Officer" in designation or "Representative" in designation or "Generator" in designation or "Closer" in designation or "Accountant" in designation or "Coordinator" in designation or "Supervisor" in designation:
            base = 55000
        e = Employee(employee_code=f"E-{i:05d}", user_id=u.id, full_name=u.full_name, designation=designation, department_id=depts[dept].id,
                     branch_id=branch.id, gender="female" if any(n in u.full_name for n in ("Sana", "Ayesha", "Nadia", "Rabia", "Hina", "Maryam", "Fatima")) else "male",
                     email=email, phone=f"+92 3{rnd.randint(0, 4)}{rnd.randint(1000000, 9999999)}", join_date=date.today() - timedelta(days=rnd.randint(200, 1500)),
                     employment_type="full_time", shift="night" if "Night" in designation else "morning", shift_start="09:00", shift_end="17:00",
                     base_salary=base, currency="PKR", status="active", background_check_status="verified", background_check_date=date.today() - timedelta(days=300),
                     device_name=f"OQC-{dept.upper()[:4]}-{i:03d}")
        db.add(e)
        db.flush()
        db.add(SalaryStructure(employee_id=e.id, basic=base, allowances={"internet": 3000, "medical": 2500}, deductions={"tax": round(base * 0.02)},
                               absence_deduction_per_day=round(base / 26), late_deduction_per_instance=500, currency="PKR"))
    db.flush()

    # ---- teachers
    teacher_dept = depts["academics"]
    n_staff = db.query(Employee).count()
    for i, (name, gender, tcourses, shift, grade) in enumerate(TEACHERS, start=1):
        if db.query(Teacher).filter(Teacher.teacher_code == f"T-{i:05d}").first():
            continue
        u = _teacher_user(db, i, name, roles["teacher"], teacher_dept, branch)
        emp = db.query(Employee).filter(Employee.user_id == u.id).first()
        if not emp:
            band = {"A": 45000, "B": 35000, "C": 27000}[grade]
            emp = Employee(employee_code=f"E-{n_staff + i:05d}", user_id=u.id, full_name=name, designation="Quran Teacher", department_id=teacher_dept.id,
                           branch_id=branch.id, gender=gender, email=u.email, phone=f"+92 3{rnd.randint(0, 4)}{rnd.randint(1000000, 9999999)}",
                           join_date=date.today() - timedelta(days=rnd.randint(90, 1200)), employment_type="full_time", shift=shift,
                           shift_start={"morning": "06:00", "evening": "14:00", "night": "20:00"}[shift],
                           shift_end={"morning": "14:00", "evening": "22:00", "night": "04:00"}[shift], base_salary=band, currency="PKR",
                           salary_band=grade, status="active", is_teacher=True, background_check_status="verified" if i != 12 else "pending",
                           background_check_date=date.today() - timedelta(days=200) if i != 12 else None, device_name=f"OQC-TCHR-{i:03d}")
            db.add(emp)
            db.flush()
            db.add(SalaryStructure(employee_id=emp.id, basic=band, allowances={"internet": 3000}, deductions={}, per_class_rate=250,
                                   absence_deduction_per_day=round(band / 26), late_deduction_per_instance=300, currency="PKR"))
        avail = {d: [{"morning": "06:00-14:00", "evening": "14:00-22:00", "night": "20:00-04:00"}[shift]] for d in ["mon", "tue", "wed", "thu", "fri", "sat"]}
        t = Teacher(teacher_code=f"T-{i:05d}", user_id=u.id, employee_id=emp.id, full_name=name, gender=gender, courses=tcourses,
                    qualifications="Hafiz-e-Quran, Tajweed certification (Jamia Ashrafia)" if "Hafiz" in name or "Qari" in name else "Aalima, Tajweed diploma",
                    shift=shift, availability=avail, per_class_rate=250, grade=grade,
                    qa_score_avg={"A": 88.0, "B": 78.0, "C": 66.0}[grade] + rnd.uniform(-3, 3), punctuality_score={"A": 97, "B": 90, "C": 80}[grade] + rnd.uniform(-2, 2),
                    retention_rate={"A": 93, "B": 84, "C": 72}[grade] + rnd.uniform(-2, 2), rating=round({"A": 4.8, "B": 4.4, "C": 3.9}[grade] + rnd.uniform(-0.2, 0.1), 1),
                    supervisor_id=(supervisors[i % len(supervisors)].id if supervisors else None), is_verified=(i != 12),
                    bio="Experienced online Quran teacher focused on Tajweed accuracy and gentle pedagogy for children.")
        db.add(t)
    db.flush()
    teachers = db.query(Teacher).order_by(Teacher.id).all()

    # ---- clients & students
    divisions = {c.code: db.query(Division).filter(Division.course_id == c.id).order_by(Division.order).all() for c in courses.values()}
    course_codes = ["QAIDA", "NAZRA", "NAZRA", "HIFZ", "TAJWEED", "QAIDA", "TARJUMA", "ISLAMIC", "NAZRA"]
    student_counter = db.query(Student).count()
    for i in range(1, 41):
        code = f"C-{i:05d}"
        if db.query(Client).filter(Client.client_code == code).first():
            continue
        country, tz, cur, prefix = COUNTRIES[i % len(COUNTRIES)]
        family = FAMILY[i % len(FAMILY)]
        parent = f"{PARENT_FIRST[i % len(PARENT_FIRST)]} {family}"
        email = f"parent{i}@oqc.local"
        u = db.query(User).filter(User.email == email).first()
        if not u:
            u = User(email=email, username=f"parent{i}", full_name=parent, hashed_password=hash_password("Parent@123"), role_id=roles["client"].id, timezone=tz)
            db.add(u)
            db.flush()
        hh = Household(name=f"{family} Family", country=country)
        db.add(hh)
        db.flush()
        joined = date.today() - timedelta(days=rnd.randint(5, 700))
        status = "active" if i <= 32 else ("trial" if i <= 36 else "churned")
        c = Client(client_code=code, user_id=u.id, household_id=hh.id, full_name=parent, email=email, phone=f"{prefix}{rnd.randint(100000000, 999999999)}",
                   whatsapp=f"{prefix}{rnd.randint(100000000, 999999999)}", country=country, city=rnd.choice(["London", "Birmingham", "Manchester", "New York", "Toronto", "Sydney", "Lahore", "Bradford", "Leeds"]),
                   timezone=tz, currency=cur, relationship_to_student=rnd.choice(["father", "mother", "mother", "father", "self"]), status=status,
                   source=rnd.choice(["Meta Ads", "Google Ads", "Referral", "WhatsApp", "Website"]), consent_given=True, consent_at=joined,
                   referral_code=f"REF{family[:3].upper()}{i:03d}", is_ambassador=(i % 9 == 0), joined_at=joined,
                   billing_rep_id=(db.query(User).filter(User.email == "billing@oqc.local").first() or u).id)
        db.add(c)
        db.flush()
        n_students = 2 if i % 4 == 0 else 1
        for k in range(n_students):
            student_counter += 1
            gender = "male" if (i + k) % 2 == 0 else "female"
            first = rnd.choice(FIRST_M if gender == "male" else FIRST_F)
            age = rnd.randint(5, 16) if c.relationship_to_student != "self" else rnd.randint(19, 45)
            ccode = course_codes[(i + k) % len(course_codes)]
            course = courses[ccode]
            # teacher match: same gender preference for females, course fit
            fit = [t for t in teachers if ccode in (t.courses or []) and t.is_verified and (gender == "male" or t.gender == "female" or age < 8)]
            teacher = fit[(i + k) % len(fit)] if fit else teachers[0]
            semail = f"student{student_counter}@oqc.local"
            su = db.query(User).filter(User.email == semail).first()
            if not su:
                su = User(email=semail, username=f"student{student_counter}", full_name=f"{first} {family}", hashed_password=hash_password("Student@123"),
                          role_id=roles["student"].id, timezone=tz)
                db.add(su)
                db.flush()
            divs = divisions.get(ccode) or []
            div = divs[min(len(divs) - 1, rnd.randint(0, 2))] if divs else None
            s_status = "active" if status == "active" else ("trial" if status == "trial" else "cancelled")
            st = Student(student_code=f"S-{student_counter:05d}", user_id=su.id, client_id=c.id, full_name=f"{first} {family}", gender=gender,
                         date_of_birth=date.today() - timedelta(days=age * 365 + rnd.randint(0, 300)), age=age, is_minor=age < 18,
                         course_id=course.id, division_id=div.id if div else None, level=div.name if div else None, teacher_id=teacher.id,
                         status=s_status, timezone=tz, preferred_language=rnd.choice(["English", "Urdu", "English"]), join_date=joined,
                         cancelled_at=(date.today() - timedelta(days=rnd.randint(5, 60))) if s_status == "cancelled" else None,
                         cancel_reason=rnd.choice(["Financial reasons", "Timing clash", "Moved to local madrasa"]) if s_status == "cancelled" else None,
                         sabaq_position=f"{div.name if div else course.name} — lesson {rnd.randint(1, 20)}", guardian_consent=True,
                         risk_score=rnd.choice([8, 12, 18, 25, 33, 45, 58, 72]), )
            st.risk_level = "high" if st.risk_score >= 65 else ("medium" if st.risk_score >= 40 else "low")
            db.add(st)
    db.commit()

    try:
        from app.seed import people_extra
        people_extra.run(db)
    except ImportError:
        pass
