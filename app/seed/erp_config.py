"""ERP-parity academic configuration seed (WP-1, audit section 3.13).

Session slots, the ERP's 16 courses, day-packages, public books, invoice additions and rules, beneficiary accounts,
client academic groups, MS Teams users, staff sorting, assessments + question bank, and the QA review parameters /
issue types that WP-5 depends on. Idempotent: every insert checks for an existing row first.
"""
from __future__ import annotations

from datetime import date, time, timedelta

from sqlalchemy.orm import Session

from app.models.academic import Book, Course, Division, Package
from app.models.core import User
from app.models.erp import (AssessmentDefinition, BeneficiaryAccount, ClientAcademicGroup, InvoiceAdditionRule,
                            InvoiceAdditionType, QAIssueType, QAReviewParameter, QuestionBankItem, SessionSlot, TeamsUser)
from app.models.people import Client, Employee

ISLAMIC, TUTORING = "Islamic Courses", "Academics Tutoring"

# code, name, type, fee (PKR / month), arabic, urdu, months, divisions (only used when the course is created here)
ERP_COURSES = [
    ("QAIDA", "Noorani Qaida", ISLAMIC, 4000, "القاعدة النورانية", "نورانی قاعدہ", 6, []),
    ("QAIDA_JTQ", "Qaida JTQ (Tajweedi Qaida)", ISLAMIC, 4500, "القاعدة التجويدية", "تجویدی قاعدہ", 6,
     ["Letters with Tajweed", "Harakat & Madd", "Rules of Noon & Meem", "Practice Recitation"]),
    ("IQRA", "Iqra Book", ISLAMIC, 4000, "كتاب اقرأ", "اقرأ", 5, ["Iqra 1", "Iqra 2", "Iqra 3", "Iqra 4", "Iqra 5", "Iqra 6"]),
    ("NAZRA", "Quran Recitation", ISLAMIC, 5000, "ناظرة", "ناظرہ قرآن", 18, []),
    ("NAZRA_TAJ", "Quran Recitation with Tajweed", ISLAMIC, 6000, "تلاوة مع التجويد", "تجوید کے ساتھ ناظرہ", 24,
     ["Juz 1-5 with Tajweed", "Juz 6-15 with Tajweed", "Juz 16-30 with Tajweed"]),
    ("HIFZ", "Quran Memorization (Hifz)", ISLAMIC, 8000, "حفظ", "حفظِ قرآن", 36, []),
    ("TAJWEED", "Tajweed Course - Basic Level", ISLAMIC, 5500, "تجويد", "تجوید", 9, []),
    ("TAJWEED_ADV", "Tajweed Course - Advanced Level", ISLAMIC, 7000, "التجويد المتقدم", "اعلیٰ تجوید", 12,
     ["Sifaat in depth", "Ahkam of Raa & Laam", "Waqf, Ibtida & Saktah", "Qira'at introduction"]),
    ("TAFSEER", "Tafseer-ul-Quran", ISLAMIC, 7500, "تفسير القرآن", "تفسیر القرآن", 24,
     ["Usool-ut-Tafseer", "Juz Amma Tafseer", "Selected Surahs", "Thematic Tafseer"]),
    ("TARJUMA", "Quran Translation", ISLAMIC, 6500, "ترجمة", "ترجمۂ قرآن", 24, []),
    ("ISLAMIC", "Islamic Studies - Basic Level", ISLAMIC, 4500, "الدراسات الإسلامية", "اسلامیات", 12, []),
    ("ISLAMIC_ADV", "Islamic Studies - Advanced Level", ISLAMIC, 6000, "الدراسات الإسلامية المتقدمة", "اعلیٰ اسلامیات", 18,
     ["Fiqh of Worship", "Hadith Studies", "Seerah in depth", "Islamic History"]),
    ("ARABIC", "Arabic Language", TUTORING, 7000, "اللغة العربية", "عربی زبان", 18,
     ["Alphabet & Vocabulary", "Grammar I (Nahw)", "Grammar II (Sarf)", "Conversation"]),
    ("URDU", "Urdu Language", TUTORING, 5000, "اللغة الأردية", "اردو زبان", 12,
     ["Reading & Writing", "Grammar", "Comprehension", "Conversation"]),
    ("ENGLISH", "English Tuition", TUTORING, 6000, "اللغة الإنجليزية", "انگریزی", 12,
     ["Phonics & Reading", "Grammar", "Writing", "Speaking"]),
    ("MATH", "Math Tuition", TUTORING, 6000, "الرياضيات", "ریاضی", 12,
     ["Numbers & Operations", "Fractions & Decimals", "Algebra Basics", "Geometry"]),
]

DAY_PACKAGES = [  # name, min, max, price GBP
    ("2 Days Package", 1, 2, 30), ("3 Days Package", 1, 3, 40), ("5 Days Package", 1, 5, 55),
]

ADDITION_TYPES = [("discount", "Special Discount"), ("charge", "Late Fee"), ("tax", "Tax")]

BENEFICIARY_ACCOUNTS = [  # mode, category, name, details, is_auto
    ("Online Payment Gateway", "Stripe", "Quran College Stripe-UK Auto", "acct_stripe_uk", True),
    ("Online Payment Gateway", "PayPal", "PayPal Manual", "payments@quran-college.org", False),
    ("Bank", "UBL", "Company Account", "PK36UNIL0109000123456789", False),
    ("Bank", "Meezan Bank", "Meezan 5533", "PK24MEZN0001230100005533", False),
    ("Bank", "Wise", "Wise UK", "GB29NWBK60161331926819", False),
    ("Cash", "Cash", "Cash by Admissions", None, False),
]

QA_PARAMETERS = [  # name, description, weight
    ("Engagement", "Student kept attentive and participating throughout the class", 1.0),
    ("Tajweed Accuracy", "Teacher's own recitation and correction of the student's Tajweed", 1.5),
    ("Adab", "Islamic manners, greeting, patience and respectful tone", 1.0),
    ("Lesson Planning", "Sabaq / sabqi / dor covered as planned and recorded", 1.0),
    ("Punctuality", "Joined on time and used the full session", 1.0),
    ("Environment", "Quiet background, camera on, proper dress and lighting", 0.5),
]

QA_ISSUES = [  # name, severity
    ("Engagement", "normal"), ("Tajweed Accuracy", "normal"), ("Adab", "normal"), ("Lesson Planning", "normal"),
    ("Late Join", "critical"), ("Missed Class", "critical"), ("Misconduct", "critical"),
]

DESIGNATION_RANK = [  # substring -> rank (lower sorts first); teachers after managers
    ("Head of", 10), ("Manager", 20), ("Supervisor", 30), ("Coordinator", 40), ("Officer", 50), ("Representative", 55),
    ("Accountant", 55), ("Lead", 60), ("Administrator", 65), ("Teacher", 70),
]

FATHER_NAMES = ["Muhammad Aslam", "Abdul Rasheed", "Ghulam Mustafa", "Muhammad Yousuf", "Hafiz Abdul Qadir",
                "Muhammad Siddique", "Sheikh Abdul Majeed", "Rana Muhammad Sharif", "Malik Nazir Ahmed", "Chaudhry Bashir"]

QUESTIONS = {  # assessment title -> [(question, answer, type, marks)]
    "Iqra Book Assessment no 1": [
        ("Recite the single Arabic letters from Alif to Yaa in order.", "All 29 letters named with correct makhraj.", "recitation", 5),
        ("Identify the three harakat and read: بَ بِ بُ", "Fatha, kasra, damma - ba, bi, bu.", "oral", 3),
        ("Read the joined word: كَتَبَ", "ka-ta-ba, joined smoothly without pausing.", "recitation", 4),
        ("Which letters are pronounced from the throat (huroof-ul-halq)?", "ء ه ع ح غ خ", "written", 4),
        ("Read the words with sukoon: أَبْ  أَتْ  أَثْ", "Clean stop on the sakin letter with no extra vowel.", "recitation", 4),
        ("What is tanween and how is it written?", "Double vowel sign giving an 'n' sound: ً ٍ ٌ", "written", 3),
        ("Read the words with shaddah: رَبَّ  إِنَّ", "Doubled letter held; ghunnah on noon mushaddad.", "recitation", 4),
        ("Write the letters that never join to the letter after them.", "ا د ذ ر ز و", "written", 3),
    ],
    "Monthly Test": [
        ("Recite the current sabaq portion from memory.", "Fluent, no more than two prompts.", "recitation", 30),
        ("Recite this week's sabqi portion.", "Fluent with correct madd lengths.", "recitation", 20),
        ("Recite the dor (revision) portion selected by the examiner.", "Assessed for retention and Tajweed.", "recitation", 30),
        ("Explain the rule of noon sakinah when followed by a throat letter.", "Izhaar - clear pronunciation without ghunnah.", "oral", 10),
        ("Which of these is a madd letter? (a) ب (b) و (c) ت (d) ق", "(b) و - when preceded by damma.", "mcq", 10),
    ],
}


def _label(start: time, minutes: int) -> str:
    total = start.hour * 60 + start.minute + minutes
    end = time((total // 60) % 24, total % 60)
    return f"{start.strftime('%I:%M %p')} - {end.strftime('%I:%M %p')}"


def _seed_sessions(db: Session) -> int:
    created = 0
    for category, minutes, count in (("30 Minutes", 30, 48), ("45 Minutes", 45, 32)):
        existing = {s.start_time for s in db.query(SessionSlot).filter(SessionSlot.category == category)}
        base = 7 * 60  # 07:00 AM - the ERP's first slot, wrapping through midnight to 06:30 AM
        for i in range(count):
            total = (base + i * minutes) % (24 * 60)
            start = time(total // 60, total % 60)
            if start in existing:
                continue
            db.add(SessionSlot(category=category, label=_label(start, minutes), start_time=start, duration_minutes=minutes,
                               status="active", sort_no=i + 1))
            created += 1
    db.flush()
    return created


def _seed_courses(db: Session) -> tuple[int, int]:
    created = updated = 0
    for order, (code, name, ctype, fee, ar, ur, months, divisions) in enumerate(ERP_COURSES):
        c = db.query(Course).filter(Course.code == code).first()
        if c:
            changed = False
            if c.name != name:
                c.name, changed = name, True
            if c.course_type != ctype:
                c.course_type, changed = ctype, True
            if not c.fee:
                c.fee, changed = fee, True
            if c.attendance_required is None:
                c.attendance_required, changed = True, True
            if c.order != order:
                c.order, changed = order, True
            updated += 1 if changed else 0
            continue
        c = Course(code=code, name=name, arabic_name=ar, urdu_name=ur, course_type=ctype, fee=fee, attendance_required=True,
                   completion_target_months=months, order=order, default_session_minutes=30,
                   description=f"{name} - {'Islamic studies programme' if ctype == ISLAMIC else 'academic tutoring'} delivered one-to-one online.")
        db.add(c)
        db.flush()
        for i, dname in enumerate(divisions):
            db.add(Division(course_id=c.id, name=dname, order=i, expected_weeks=max(4, int(months * 4.3 / max(1, len(divisions))))))
        created += 1
    db.flush()
    return created, updated


def _seed_packages(db: Session) -> int:
    created = 0
    for p in db.query(Package):
        if p.max_days is None or (p.min_days == 1 and p.max_days == 5 and p.sessions_per_week != 5):
            p.min_days = 1
            p.max_days = max(1, min(7, p.sessions_per_week or 5))
    for name, mn, mx, price in DAY_PACKAGES:
        if db.query(Package).filter(Package.name == name).first():
            continue
        db.add(Package(name=name, course_id=None, sessions_per_week=mx, session_minutes=30, price=price, currency="GBP",
                       billing_cycle="monthly", min_days=mn, max_days=mx, is_active=True, is_trial=False,
                       description=f"Between {mn} and {mx} classes per week, 30 minutes each"))
        created += 1
    db.flush()
    return created


def _seed_books(db: Session) -> int:
    created = 0
    iqra = db.query(Course).filter(Course.code == "IQRA").first()
    if iqra and not db.query(Book).filter(Book.course_id == iqra.id, Book.title == "Iqra Book").first():
        db.add(Book(course_id=iqra.id, title="Iqra Book", arabic_title="كتاب اقرأ", order=1, is_public=True, status="active",
                    description="Six-part beginner reading primer (Iqra 1-6)."))
        created += 1
    db.flush()
    if not db.query(Book).filter(Book.is_public.is_(True)).first():
        qaida = db.query(Course).filter(Course.code == "QAIDA").first()
        first = db.query(Book).filter(Book.course_id == qaida.id).order_by(Book.order).first() if qaida else None
        if first:
            first.is_public = True
    return created


def _seed_invoice_additions(db: Session) -> tuple[int, int]:
    created = rules = 0
    types = {}
    for at, desc in ADDITION_TYPES:
        t = db.query(InvoiceAdditionType).filter(InvoiceAdditionType.description == desc).first()
        if not t:
            t = InvoiceAdditionType(addition_type=at, description=desc, status="active")
            db.add(t)
            created += 1
        types[at] = t
    db.flush()
    today = date.today()
    if not db.query(InvoiceAdditionRule).filter(InvoiceAdditionRule.level == "global", InvoiceAdditionRule.addition_type_id == types["tax"].id).first():
        db.add(InvoiceAdditionRule(level="global", from_date=today.replace(day=1), to_date=None, addition_type_id=types["tax"].id,
                                   implementation_type="fixed", amount=0, status="inactive", auto_assigned=False))
        rules += 1
    if not db.query(InvoiceAdditionRule).filter(InvoiceAdditionRule.auto_assigned.is_(True)).first():
        client = db.query(Client).filter(Client.status == "active").order_by(Client.id).first()
        db.add(InvoiceAdditionRule(level="client" if client else "global", client_id=client.id if client else None,
                                   from_date=today.replace(day=1), to_date=today.replace(day=1) + timedelta(days=180),
                                   addition_type_id=types["discount"].id, implementation_type="percent", amount=10,
                                   status="active", auto_assigned=True))
        rules += 1
    db.flush()
    return created, rules


def _seed_beneficiary_accounts(db: Session) -> int:
    created = 0
    for mode, cat, name, details, auto in BENEFICIARY_ACCOUNTS:
        if db.query(BeneficiaryAccount).filter(BeneficiaryAccount.account_name == name).first():
            continue
        db.add(BeneficiaryAccount(payment_mode=mode, category=cat, account_name=name, account_details=details, is_auto=auto, status="active"))
        created += 1
    db.flush()
    return created


def _seed_client_groups(db: Session) -> int:
    created = 0
    morning = db.query(User).filter(User.email == "manager@oqc.local").first()
    night = db.query(User).filter(User.email == "manager2@oqc.local").first() or morning
    for name, pseudo, shift, rep in (("Morning Group", "Morning Manager", "morning", morning), ("Night Group", "Night Manager", "night", night)):
        g = db.query(ClientAcademicGroup).filter(ClientAcademicGroup.name == name).first()
        if not g:
            g = ClientAcademicGroup(name=name, pseudo_name=pseudo, shift_group=shift, representative_id=rep.id if rep else None, status="active")
            db.add(g)
            created += 1
        elif g.representative_id is None and rep:
            g.representative_id = rep.id
    db.flush()
    groups = {g.shift_group: g for g in db.query(ClientAcademicGroup)}
    for c in db.query(Client).filter(Client.academic_group_id.is_(None)):
        g = groups.get(c.shift or "night") or groups.get("night")
        if g:
            c.academic_group_id = g.id
    return created


def _seed_teams_users(db: Session) -> int:
    created = 0
    staff = db.query(Employee).filter(Employee.status == "active").order_by(Employee.is_teacher.desc(), Employee.id).limit(6).all()
    for e in staff:
        email = f"{(e.full_name or 'staff').lower().replace(' ', '.').replace('.', '.')}@quran-college.org"
        email = "".join(ch for ch in email if ch.isalnum() or ch in "@._")
        if db.query(TeamsUser).filter(TeamsUser.teams_email == email).first():
            continue
        db.add(TeamsUser(person_type="staff", employee_id=e.id, teams_email=email, display_name=e.full_name, status="active"))
        created += 1
    clients = db.query(Client).filter(Client.status == "active").order_by(Client.id).limit(4).all()
    for c in clients:
        email = (c.email or f"{c.client_code.lower()}@outlook.com").lower()
        if db.query(TeamsUser).filter(TeamsUser.teams_email == email).first():
            continue
        db.add(TeamsUser(person_type="client", client_id=c.id, teams_email=email, display_name=c.full_name, status="active"))
        created += 1
    db.flush()
    return created


def _rank(designation: str) -> int:
    for needle, rank in DESIGNATION_RANK:
        if needle.lower() in (designation or "").lower():
            return rank
    return 80


def _seed_staff_sorting(db: Session) -> int:
    employees = db.query(Employee).order_by(Employee.id).all()
    if any(e.sort_no for e in employees):
        return 0
    ordered = sorted(employees, key=lambda e: (_rank(e.designation), e.status != "active", e.full_name or ""))
    for i, e in enumerate(ordered, start=1):
        e.sort_no = i
    for e, father in zip(ordered[:len(FATHER_NAMES)], FATHER_NAMES):
        if not e.father_name:
            e.father_name = father
    db.flush()
    return len(ordered)


def _seed_assessments(db: Session) -> tuple[int, int]:
    created = questions = 0
    iqra_course = db.query(Course).filter(Course.code == "IQRA").first()
    iqra_book = db.query(Book).filter(Book.course_id == iqra_course.id).order_by(Book.order).first() if iqra_course else None
    hifz_course = db.query(Course).filter(Course.code == "HIFZ").first()
    hifz_book = db.query(Book).filter(Book.course_id == hifz_course.id).order_by(Book.order).first() if hifz_course else None
    for title, book, passing, total in (("Iqra Book Assessment no 1", iqra_book, 18, 30), ("Monthly Test", hifz_book, 30, 100)):
        a = db.query(AssessmentDefinition).filter(AssessmentDefinition.title == title).first()
        if not a:
            a = AssessmentDefinition(title=title, book_id=book.id if book else None, passing_marks=passing, total_marks=total, status="active")
            db.add(a)
            db.flush()
            created += 1
        for q, ans, qtype, marks in QUESTIONS.get(title, []):
            if db.query(QuestionBankItem).filter(QuestionBankItem.assessment_id == a.id, QuestionBankItem.question == q).first():
                continue
            db.add(QuestionBankItem(assessment_id=a.id, book_id=a.book_id, question=q, answer=ans, question_type=qtype, marks=marks, status="active"))
            questions += 1
    db.flush()
    return created, questions


def _seed_qa_config(db: Session) -> tuple[int, int]:
    params = issues = 0
    for i, (name, desc, weight) in enumerate(QA_PARAMETERS, start=1):
        if db.query(QAReviewParameter).filter(QAReviewParameter.name == name).first():
            continue
        db.add(QAReviewParameter(name=name, description=desc, weight=weight, max_rating=5, sort_no=i, status="active"))
        params += 1
    for name, severity in QA_ISSUES:
        if db.query(QAIssueType).filter(QAIssueType.name == name).first():
            continue
        db.add(QAIssueType(name=name, severity=severity, status="active",
                           description=f"{'Critical' if severity == 'critical' else 'Coaching'} issue: {name.lower()}"))
        issues += 1
    db.flush()
    return params, issues


_DEFERRED_ARMED = False
_PEOPLE_STAGE_DONE = False


def _run_people_stage(db: Session) -> str:
    """Rows that need employees / clients (seeded by app/seed/people.py, which runs after this module)."""
    global _PEOPLE_STAGE_DONE
    groups = _seed_client_groups(db)
    teams = _seed_teams_users(db)
    sorted_staff = _seed_staff_sorting(db)
    db.commit()
    _PEOPLE_STAGE_DONE = True
    return f"groups +{groups}, teams +{teams}, staff sorted {sorted_staff}"


def _arm_deferred() -> None:
    """The seed runner executes core, academic, erp_config, people, ... - employees and clients therefore do not exist
    yet on a fresh reset. Defer the people-dependent rows to the end of the seed process (same pattern as
    academic_curriculum)."""
    global _DEFERRED_ARMED
    if _DEFERRED_ARMED:
        return
    _DEFERRED_ARMED = True
    import atexit

    def _finish() -> None:
        if _PEOPLE_STAGE_DONE:
            return
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            if not db.query(Employee.id).first():
                return
            print("  + seed.erp_config (people stage): " + _run_people_stage(db))
        except Exception as exc:  # pragma: no cover - seeding must never break the run
            db.rollback()
            print(f"  ! seed.erp_config deferred stage failed: {exc}")
        finally:
            db.close()

    atexit.register(_finish)


def run(db: Session) -> None:
    sessions = _seed_sessions(db)
    courses_new, courses_upd = _seed_courses(db)
    packages = _seed_packages(db)
    books = _seed_books(db)
    add_types, add_rules = _seed_invoice_additions(db)
    accounts = _seed_beneficiary_accounts(db)
    assessments, questions = _seed_assessments(db)
    qa_params, qa_issues = _seed_qa_config(db)
    db.commit()
    print(f"    erp_config: sessions +{sessions}, courses +{courses_new}/~{courses_upd}, packages +{packages}, books +{books}, "
          f"additions +{add_types}/{add_rules} rules, accounts +{accounts}, assessments +{assessments}/{questions} questions, "
          f"qa +{qa_params} params/{qa_issues} issues")
    if db.query(Employee.id).first():
        print("    erp_config: " + _run_people_stage(db))
    else:
        _arm_deferred()
