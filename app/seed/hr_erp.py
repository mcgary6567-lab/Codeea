"""ERP parity seed for the Human Resource area (docs/AUDIT_HUMAN_RESOURCE.md).

Builds the catalogues the Employment Management pages depend on — violation types with their standard
fines, bonus types with their standard amounts, salary grades and the HR downloads shelf — then fills the
staff request lists (employee requests, complaints, bonuses, violations and advances) with 90 days of
history spread across pending / approved / rejected / cancelled, and backfills the ERP columns that were
appended to the employee record.

Idempotent: every block checks before it inserts, so re-running only tops up what is missing.
Deterministic (fixed random seed), so counts do not drift between runs.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.core import User
from app.models.hr_erp import (EMPLOYEE_REQUEST_TYPES, STAFF_COMPLAINT_TYPES, BonusType, EmployeeRequest, Grade,
                               HRDownload, StaffComplaint, ViolationType)
from app.models.people import Bonus, Employee, SalaryAdvance, Violation

rnd = random.Random(20260914)

# description, urdu description, penalty (PKR), severity
VIOLATION_TYPES = [
    ("Starting a class late or ending it early without informing the supervisor",
     "سپروائزر کو اطلاع دیے بغیر کلاس دیر سے شروع کرنا یا وقت سے پہلے ختم کرنا", 200, "minor"),
    ("Not entering the class data in the LMS, or activating the class late",
     "ایل ایم ایس میں کلاس کا ڈیٹا درج نہ کرنا یا کلاس دیر سے ایکٹیویٹ کرنا", 200, "minor"),
    ("Missing a class without arranging a substitute",
     "متبادل کا بندوبست کیے بغیر کلاس مِس کرنا", 200, "major"),
    ("Not using a pen in class while teaching",
     "کلاس میں پڑھاتے ہوئے قلم استعمال نہ کرنا", 200, "minor"),
    ("Using Google or a similar tool during the class instead of prepared material",
     "کلاس کے دوران تیار شدہ مواد کے بجائے گوگل یا اس جیسا ذریعہ استعمال کرنا", 400, "major"),
    ("Monthly class quality below 40%",
     "ماہانہ کلاس کوالٹی چالیس فیصد سے کم رہنا", 300, "major"),
    ("Class quality below 30% for three consecutive months",
     "مسلسل تین ماہ کلاس کوالٹی تیس فیصد سے کم رہنا", 300, "critical"),
]

BONUS_TYPES = [
    ("Full monthly attendance with no leave, late arrival or early departure",
     "پورے مہینے کی حاضری، نہ چھٹی، نہ تاخیر، نہ جلدی روانگی", 500),
    ("Passing a trial class", "ٹرائل کلاس کامیاب کرانا", 300),
    ("Trial passed by the manager on the teacher's behalf", "منیجر کی جانب سے ٹرائل پاس کرانا", 500),
    ("Class quality above 70%", "کلاس کوالٹی ستر فیصد سے زیادہ", 300),
    ("Class quality above 90%", "کلاس کوالٹی نوے فیصد سے زیادہ", 500),
    ("Extra class delivered on request", "درخواست پر اضافی کلاس پڑھانا", 1000),
]

GRADES = [
    ("Grade A", "Senior teaching and management staff.", 40000, 60000, {"internet": 3000, "medical": 2500, "fuel": 2000}),
    ("Grade B", "Established teachers and officers.", 30000, 40000, {"internet": 2500, "medical": 1500}),
    ("Grade C", "New joiners and supporting staff.", 22000, 30000, {"internet": 2000}),
]

DOWNLOADS = [
    ("HR Policy Manual 2026", "https://drive.google.com/oqc/hr/policy-manual-2026.pdf", "policy"),
    ("Leave Application Form", "https://drive.google.com/oqc/hr/leave-application-form.pdf", "form"),
    ("Code of Conduct and Class Etiquette", "https://drive.google.com/oqc/hr/code-of-conduct.pdf", "policy"),
    ("Salary Certificate Request Form", "https://drive.google.com/oqc/hr/salary-certificate-request.pdf", "form"),
]

REQUEST_DESCRIPTIONS = {
    "Salary Certificate": "I need a salary certificate addressed to the bank for a personal account opening.",
    "Experience Letter": "Please issue an experience letter covering my service to date for a visa application.",
    "Equipment": "My headset has stopped working; requesting a replacement headset for live classes.",
    "Shift Change": "Requesting a move from the night shift to the morning shift from the start of next month.",
    "Designation Review": "Requesting a review of my designation after completing two years as Teacher Remote.",
    "Document Correction": "My father's name is spelled incorrectly on my employee record; correction attached.",
    "Resignation": "Submitting my resignation with one month's notice for family reasons.",
    "Other": "Requesting permission to attend an external Tajweed certification on a working Saturday.",
}

HR_REMARKS = {
    "approved": ["Issued and emailed to the employee.", "Approved by People & Culture; effective immediately.",
                 "Approved after confirming with the line manager."],
    "rejected": ["Declined — the same request was issued last month.",
                 "Declined; please re-apply after the probation period.",
                 "Declined — no budget line for this equipment in the current quarter."],
    "cancelled": ["Withdrawn by the employee.", "Cancelled — raised in error."],
    "pending": ["With People & Culture for review.", "Awaiting the line manager's confirmation."],
}

COMPLAINTS = [
    ("Facility", "Power outage during the night shift", False,
     "The generator does not start during the night shift, so the last two classes are taught on mobile data."),
    ("Payroll", "Internet allowance missing from the payslip", False,
     "My internet allowance was not included in last month's payslip although it appears in my salary structure."),
    ("Academics", "Lesson plan template is out of date", False,
     "The lesson plan template on the portal still refers to the old Qaida numbering and confuses new teachers."),
    ("HR", "Attendance device does not read my check-in", False,
     "The attendance page shows me absent on days I taught a full shift; this is the third month running."),
    ("Management", "Rota changed without notice", False,
     "The rota for the coming week was published on Sunday night, leaving no time to arrange childcare."),
    ("Facility", "Classroom microphone is faulty", False,
     "The microphone in room 2 cuts out mid-class and parents have started to comment on it."),
    ("Other", "Parking space is being used by visitors", False,
     "Staff parking is taken by visitors every morning and there is nowhere left for the early shift."),
    ("Payroll", "Advance deduction taken twice", False,
     "My advance instalment was deducted twice in the same month; requesting a correction in the next run."),
    ("Management", "Concern about how a colleague is being spoken to", True,
     "A supervisor repeatedly raises their voice at a colleague in front of the team. I am reporting it in "
     "confidence because the person affected does not want to be named."),
    ("HR", "Confidential concern about overtime pressure", True,
     "Staff on the night shift are being asked to take extra classes without any record of the extra hours. "
     "I would rather this were handled by People & Culture than by my own manager."),
]

ADMIN_RESPONSES = {
    "approved": "Reviewed by People & Culture; the corrective action has been agreed with the department head.",
    "rejected": "Reviewed; no further action is needed because the matter was resolved locally.",
    "cancelled": "Withdrawn by the member of staff before review.",
}

ADVANCE_REASONS = [
    "School fees for two children due at the start of term.",
    "Medical expenses for a parent's operation.",
    "House rent advance requested by the landlord.",
    "Travel costs for a family funeral in Peshawar.",
    "Repair of the motorcycle used for the commute.",
    "Eid expenses; requesting three instalments.",
    "Laptop replacement so that classes are not interrupted.",
    "Deposit for a new tenancy closer to the campus.",
]

ADVANCE_HR_REMARKS = {
    "approved": "Approved; recovered in equal instalments from the next payroll run.",
    "rejected": "Declined — an earlier advance is still being recovered.",
    "cancelled": "Cancelled at the employee's request.",
    "pending": "Awaiting the Finance check on the outstanding balance.",
}

STATUS_SPREAD = ["pending", "approved", "approved", "rejected", "approved", "pending", "cancelled", "approved",
                 "rejected", "pending"]

EMPLOYEE_TYPE_BY_DEPARTMENT = {
    "academics": "Academics", "qa": "Academics", "operations": "Academics",
    "marketing": "Marketing", "people": "Admin", "finance": "Admin", "technology": "Admin",
}

RELIGIONS = ["Islam", "Islam", "Islam", "Islam", "Christianity"]
BLOOD_GROUPS = ["A+", "B+", "O+", "AB+", "A-", "O-", "B-"]
BANKS = ["Meezan Bank", "HBL", "Bank Alfalah", "UBL", "Allied Bank", "Faysal Bank"]
MOTHER_NAMES = ["Fatima Bibi", "Ayesha Begum", "Khadija Bibi", "Zainab Begum", "Maryam Bibi", "Hafsa Begum",
                "Rukhsana Begum", "Naseem Akhtar", "Shahida Parveen", "Razia Sultana"]


# --------------------------------------------------------------------------- catalogues
def _violation_types(db: Session) -> int:
    made = 0
    for i, (desc, urdu, amount, severity) in enumerate(VIOLATION_TYPES):
        if db.query(ViolationType).filter(ViolationType.description == desc).first():
            continue
        db.add(ViolationType(description=desc, description_urdu=urdu, penalty_amount=amount, severity=severity,
                             status="active", sort_no=(i + 1) * 10))
        made += 1
    db.flush()
    return made


def _bonus_types(db: Session) -> int:
    made = 0
    for i, (desc, urdu, amount) in enumerate(BONUS_TYPES):
        if db.query(BonusType).filter(BonusType.description == desc).first():
            continue
        db.add(BonusType(description=desc, description_urdu=urdu, bonus_amount=amount, status="active",
                         sort_no=(i + 1) * 10))
        made += 1
    db.flush()
    return made


def _grades(db: Session) -> int:
    made = 0
    for name, desc, lo, hi, allowances in GRADES:
        if db.query(Grade).filter(Grade.name == name).first():
            continue
        db.add(Grade(name=name, description=desc, basic_min=lo, basic_max=hi, allowances=allowances, status="active"))
        made += 1
    db.flush()
    return made


def _downloads(db: Session) -> int:
    made = 0
    for desc, link, category in DOWNLOADS:
        if db.query(HRDownload).filter(HRDownload.description == desc).first():
            continue
        db.add(HRDownload(description=desc, link=link, category=category, status="active"))
        made += 1
    db.flush()
    return made


# --------------------------------------------------------------------------- employee backfill
def _backfill_employees(db: Session) -> int:
    grades = {g.name: g for g in db.query(Grade).order_by(Grade.id)}
    grade_list = [grades.get("Grade A"), grades.get("Grade B"), grades.get("Grade C")]
    grade_list = [g for g in grade_list if g is not None]
    touched = 0
    for i, e in enumerate(db.query(Employee).order_by(Employee.id).all()):
        changed = False
        dept_code = e.department.code if e.department else ""
        want_type = EMPLOYEE_TYPE_BY_DEPARTMENT.get(dept_code, "Admin")
        if e.employee_type != want_type:
            e.employee_type = want_type
            changed = True
        if not e.shift_code:
            e.shift_code = {"morning": "M", "evening": "E", "night": "N"}.get(e.shift or "morning", "M")
            changed = True
        if e.duty_hours is None:
            e.duty_hours = 6.0 if e.is_teacher else 8.0
            changed = True
        if not e.whatsapp and e.phone:
            e.whatsapp = e.phone
            changed = True
        if grade_list and not e.grade_id:
            basic = float(e.base_salary or 0)
            pick = grade_list[-1]
            for g in grade_list:
                if float(g.basic_min or 0) <= basic <= float(g.basic_max or 0):
                    pick = g
                    break
            else:
                pick = grade_list[0] if basic >= float(grade_list[0].basic_max or 0) else grade_list[-1]
            e.grade_id = pick.id
            changed = True
        # a realistic subset carries the personal and bank details
        if i % 3 != 2:
            if not e.mother_name:
                e.mother_name = MOTHER_NAMES[i % len(MOTHER_NAMES)]
                changed = True
            if not e.religion:
                e.religion = RELIGIONS[i % len(RELIGIONS)]
                changed = True
            if not e.blood_group:
                e.blood_group = BLOOD_GROUPS[i % len(BLOOD_GROUPS)]
                changed = True
            if not e.bank_name:
                e.bank_name = BANKS[i % len(BANKS)]
                changed = True
            if not e.bank_account_no:
                e.bank_account_no = f"PK{36 + (i % 40):02d}MEZN{(10**11) + i * 7919:012d}"
                changed = True
        if changed:
            touched += 1
    db.flush()
    return touched


# --------------------------------------------------------------------------- staff request lists
def _actives(db: Session) -> list[Employee]:
    return [e for e in db.query(Employee).order_by(Employee.id).all() if e.status not in ("resigned", "terminated")]


def _decider(db: Session) -> User | None:
    return db.query(User).filter(User.is_superuser.is_(True)).order_by(User.id).first()


def _employee_requests(db: Session, employees: list[Employee], decider: User | None, target: int = 25) -> int:
    have = db.query(EmployeeRequest).count()
    if have >= target or not employees:
        return 0
    made = 0
    for n in range(have, target):
        e = employees[n % len(employees)]
        rtype = EMPLOYEE_REQUEST_TYPES[n % len(EMPLOYEE_REQUEST_TYPES)]
        status = STATUS_SPREAD[n % len(STATUS_SPREAD)]
        when = date.today() - timedelta(days=rnd.randint(1, 90))
        req = EmployeeRequest(employee_id=e.id, request_date=when, request_type=rtype,
                              description=REQUEST_DESCRIPTIONS.get(rtype, "Staff request."),
                              hr_remarks=rnd.choice(HR_REMARKS[status]), status=status)
        if status != "pending":
            req.decided_by_id = decider.id if decider else None
            req.decided_at = datetime.combine(when + timedelta(days=2), datetime.min.time())
        db.add(req)
        made += 1
    db.flush()
    return made


def _complaints(db: Session, employees: list[Employee], decider: User | None) -> int:
    have = db.query(StaffComplaint).count()
    if have >= len(COMPLAINTS) or not employees:
        return 0
    made = 0
    for n in range(have, len(COMPLAINTS)):
        ctype, title, secret, description = COMPLAINTS[n]
        e = employees[(n * 3) % len(employees)]
        status = STATUS_SPREAD[n % len(STATUS_SPREAD)]
        c = StaffComplaint(employee_id=e.id, complaint_type=ctype if ctype in STAFF_COMPLAINT_TYPES else "Other",
                           title=title, description=description, is_secret=secret, status=status,
                           admin_response=ADMIN_RESPONSES.get(status))
        if status != "pending":
            c.decided_by_id = decider.id if decider else None
            c.decided_at = datetime.utcnow() - timedelta(days=rnd.randint(1, 60))
        c.created_at = datetime.utcnow() - timedelta(days=rnd.randint(2, 90))
        db.add(c)
        made += 1
    db.flush()
    return made


def _bonuses(db: Session, employees: list[Employee], decider: User | None, target: int = 12) -> int:
    types = db.query(BonusType).order_by(BonusType.sort_no).all()
    have = db.query(Bonus).filter(Bonus.bonus_type_id.isnot(None)).count()
    if have >= target or not types or not employees:
        return 0
    made = 0
    for n in range(have, target):
        e = employees[(n * 5) % len(employees)]
        bt = types[n % len(types)]
        status = STATUS_SPREAD[n % len(STATUS_SPREAD)]
        when = date.today() - timedelta(days=rnd.randint(3, 90))
        b = Bonus(employee_id=e.id, amount=float(bt.bonus_amount or 0), currency="PKR", bonus_type="performance",
                  bonus_type_id=bt.id, reason=bt.description, period=f"{when:%Y-%m}", status=status)
        if status == "approved":
            b.acceptance_date = when + timedelta(days=1)
            b.approved_by_id = decider.id if decider else None
        if status != "pending":
            b.decided_at = datetime.combine(when + timedelta(days=1), datetime.min.time())
        db.add(b)
        made += 1
    db.flush()
    return made


def _violations(db: Session, employees: list[Employee], decider: User | None, target: int = 10) -> int:
    types = db.query(ViolationType).order_by(ViolationType.sort_no).all()
    have = db.query(Violation).filter(Violation.violation_type_id.isnot(None)).count()
    if have >= target or not types or not employees:
        return 0
    made = 0
    for n in range(have, target):
        e = employees[(n * 7) % len(employees)]
        vt = types[n % len(types)]
        status = STATUS_SPREAD[n % len(STATUS_SPREAD)]
        when = date.today() - timedelta(days=rnd.randint(3, 90))
        v = Violation(employee_id=e.id, violation_type="policy", severity=vt.severity or "minor",
                      description=vt.description, violation_type_id=vt.id, date=when,
                      status="closed" if status in ("approved", "rejected", "cancelled") else "open",
                      approval_status=status, reported_by_id=decider.id if decider else None,
                      remarks="Reported by the shift supervisor and reviewed with the employee.",
                      deduction_amount=float(vt.penalty_amount or 0) if status == "approved" else 0)
        if status != "pending":
            v.decided_by_id = decider.id if decider else None
            v.decided_at = datetime.combine(when + timedelta(days=1), datetime.min.time())
        db.add(v)
        made += 1
    db.flush()
    return made


def _advances(db: Session, employees: list[Employee], decider: User | None, target: int = 8) -> int:
    have = db.query(SalaryAdvance).filter(SalaryAdvance.hr_remarks.isnot(None)).count()
    if have >= target or not employees:
        return 0
    made = 0
    for n in range(have, target):
        e = employees[(n * 11) % len(employees)]
        status = STATUS_SPREAD[n % len(STATUS_SPREAD)]
        when = date.today() - timedelta(days=rnd.randint(5, 90))
        amount = float(rnd.choice([10000, 15000, 20000, 25000, 30000]))
        a = SalaryAdvance(employee_id=e.id, amount=amount, currency="PKR",
                          reason=ADVANCE_REASONS[n % len(ADVANCE_REASONS)], installments=rnd.choice([1, 2, 3]),
                          remaining=amount if status == "approved" else 0, status=status, request_date=when,
                          hr_remarks=ADVANCE_HR_REMARKS[status])
        if status == "approved":
            a.approved_by_id = decider.id if decider else None
        if status != "pending":
            a.decided_at = datetime.combine(when + timedelta(days=2), datetime.min.time())
        db.add(a)
        made += 1
    db.flush()
    return made


# --------------------------------------------------------------------------- entry point
def run(db: Session) -> None:
    n_vt = _violation_types(db)
    n_bt = _bonus_types(db)
    n_gr = _grades(db)
    n_dl = _downloads(db)
    db.commit()

    employees = _actives(db)
    decider = _decider(db)
    n_emp = _backfill_employees(db)
    n_req = _employee_requests(db, employees, decider)
    n_comp = _complaints(db, employees, decider)
    n_bonus = _bonuses(db, employees, decider)
    n_viol = _violations(db, employees, decider)
    n_adv = _advances(db, employees, decider)
    db.commit()

    print(f"    hr_erp: {n_vt} violation types, {n_bt} bonus types, {n_gr} grades, {n_dl} downloads, "
          f"{n_emp} employees backfilled, {n_req} employee requests, {n_comp} complaints, {n_bonus} bonuses, "
          f"{n_viol} violations, {n_adv} advances")
