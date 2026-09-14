"""WP-1 Academic Configuration: every page renders (200), every create / edit / toggle redirects (303) and persists.

Self-contained: defaults DATABASE_URL to the private database data/oqc_wp1.db (seed it first with
    $env:DATABASE_URL='sqlite:///./data/oqc_wp1.db'; .venv/Scripts/python.exe seed.py --reset
) so the shared development database is never locked. Because tests/conftest.py imports the app before this module,
set the variable in the shell when running under pytest. ASCII output only (Windows console is cp1252).

Run:  $env:DATABASE_URL='sqlite:///./data/oqc_wp1.db'; .venv/Scripts/python.exe -m pytest tests/test_erp_config.py -q
"""
from __future__ import annotations

import io
import os
import sys
from datetime import date
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/oqc_wp1.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.academic import Book, Course, Package  # noqa: E402
from app.models.erp import (AssessmentDefinition, BeneficiaryAccount, ClientAcademicGroup, InvoiceAdditionRule,  # noqa: E402
                            InvoiceAdditionType, QAIssueType, QAReviewParameter, QuestionBankItem, SessionSlot, TeamsUser)
from app.models.people import Client, Employee  # noqa: E402

BASE = "/academics/config"
PAGES = ["", "/sessions", "/sessions?category=45+Minutes&status=active", "/courses", "/courses?course_type=Academics+Tutoring",
         "/packages", "/books", "/books?tab=public", "/staff-sorting", "/staff-sorting?shift=morning", "/invoice-additions",
         "/invoice-addition-rules", "/invoice-addition-rules?level=global", "/beneficiary-accounts",
         "/beneficiary-accounts?payment_mode=Bank", "/client-groups", "/teams-users", "/teams-users?tab=client",
         "/question-bank", "/question-bank?question_type=mcq", "/assessments"]


def _purge_test_artifacts(db) -> None:
    """Delete everything this module creates, so it can be re-run against the same database.

    The assertions below are absolute ("there are exactly 32 forty-five minute sessions", "exactly one
    course with this code"), which is what makes them worth having - but it also means a leftover row
    from a previous run would fail them. Rather than weaken the assertions, start from a clean slate.
    """
    rules = (db.query(InvoiceAdditionRule)
             .join(InvoiceAdditionType, InvoiceAdditionRule.addition_type_id == InvoiceAdditionType.id)
             .filter(InvoiceAdditionType.description == "Rescheduling Fee").all())
    for r in rules:
        db.delete(r)
    db.flush()
    for model, criterion in [
        (QuestionBankItem, QuestionBankItem.question.in_(["Read Iqra part 2 page 5.", "Read Iqra part 2 page 6."])),
        (AssessmentDefinition, AssessmentDefinition.title == "Iqra Book Assessment no 2"),
        (SessionSlot, (SessionSlot.category == "45 Minutes") & (SessionSlot.sort_no == 99)),
        (Course, Course.code == "TEST_SCI"),
        (Package, Package.name == "4 Days Package"),
        (Book, Book.title == "Iqra Workbook"),
        (InvoiceAdditionType, InvoiceAdditionType.description.in_(["Rescheduling Fee", "Should fail"])),
        (BeneficiaryAccount, BeneficiaryAccount.account_name == "HBL Main"),
        (ClientAcademicGroup, ClientAcademicGroup.name == "Weekend Group"),
        (TeamsUser, TeamsUser.teams_email.in_(["wp1.staff@quran-college.org", "wp1.client@outlook.com"])),
    ]:
        for row in db.query(model).filter(criterion).all():
            db.delete(row)
    db.commit()


@pytest.fixture(scope="module", autouse=True)
def _schema():
    init_db()
    db = SessionLocal()
    try:
        if not db.query(SessionSlot.id).first():
            from app.seed import erp_config
            erp_config.run(db)
            db.commit()
        _purge_test_artifacts(db)
    finally:
        db.close()


@pytest.fixture(scope="module")
def admin() -> TestClient:
    c = TestClient(app, follow_redirects=False)
    r = c.post("/login", data={"username": "admin@oqc.local", "password": "Admin@12345"})
    assert r.status_code == 303, f"login failed: {r.status_code}"
    return c


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def post(client: TestClient, url: str, data: dict) -> None:
    r = client.post(url, data=data)
    assert r.status_code == 303, f"POST {url} -> {r.status_code}: {r.text[:300]}"


# --------------------------------------------------------------------------- seed expectations
def test_seed_counts(db):
    assert db.query(SessionSlot).filter(SessionSlot.category == "30 Minutes").count() == 48
    assert db.query(SessionSlot).filter(SessionSlot.category == "45 Minutes").count() == 32
    first = db.query(SessionSlot).filter(SessionSlot.category == "30 Minutes").order_by(SessionSlot.sort_no).first()
    last = db.query(SessionSlot).filter(SessionSlot.category == "30 Minutes").order_by(SessionSlot.sort_no.desc()).first()
    assert first.label == "07:00 AM - 07:30 AM" and first.utc_label == "02:00 AM"
    assert last.label == "06:30 AM - 07:00 AM" and last.sort_no == 48
    assert db.query(Course).count() >= 16
    assert db.query(Course).filter(Course.code == "QAIDA").first().name == "Noorani Qaida"
    assert db.query(Course).filter(Course.course_type == "Academics Tutoring").count() >= 4
    assert db.query(Package).filter(Package.name == "3 Days Package", Package.min_days == 1, Package.max_days == 3).first()
    assert db.query(Book).filter(Book.title == "Iqra Book").first()
    assert db.query(Book).filter(Book.is_public.is_(True)).count() >= 1
    assert db.query(InvoiceAdditionType).count() >= 3
    assert db.query(InvoiceAdditionRule).filter(InvoiceAdditionRule.auto_assigned.is_(True)).count() >= 1
    assert db.query(BeneficiaryAccount).filter(BeneficiaryAccount.is_auto.is_(True)).first().account_name == "Quran College Stripe-UK Auto"
    assert db.query(ClientAcademicGroup).count() >= 2
    assert db.query(AssessmentDefinition).filter(AssessmentDefinition.title == "Iqra Book Assessment no 1").first().passing_marks == 18
    assert db.query(QuestionBankItem).count() >= 12
    assert db.query(QAReviewParameter).count() == 6
    assert db.query(QAIssueType).filter(QAIssueType.severity == "critical").count() == 3


def test_seed_is_idempotent(db):
    from app.seed import erp_config
    before = {m: db.query(m).count() for m in (SessionSlot, Course, Package, Book, InvoiceAdditionType, InvoiceAdditionRule,
                                                BeneficiaryAccount, ClientAcademicGroup, TeamsUser, AssessmentDefinition,
                                                QuestionBankItem, QAReviewParameter, QAIssueType)}
    erp_config.run(db)
    after = {m: db.query(m).count() for m in before}
    assert before == after


def test_people_stage_seeded(db):
    if not db.query(Employee.id).first():
        pytest.skip("people seed not loaded")
    assert db.query(Employee).filter(Employee.sort_no > 0).count() >= 1
    assert db.query(Employee).filter(Employee.father_name.isnot(None)).count() >= 1
    assert db.query(TeamsUser).filter(TeamsUser.person_type == "staff").count() >= 1


# --------------------------------------------------------------------------- pages
@pytest.mark.parametrize("path", PAGES)
def test_pages_render(admin, path):
    r = admin.get(BASE + path)
    assert r.status_code == 200, f"GET {BASE + path} -> {r.status_code}"


def test_assessment_detail_renders(admin, db):
    a = db.query(AssessmentDefinition).order_by(AssessmentDefinition.id).first()
    assert admin.get(f"{BASE}/assessments/{a.id}").status_code == 200
    assert admin.get(f"{BASE}/assessments/999999").status_code == 404


def test_books_template_csv(admin):
    r = admin.get(f"{BASE}/books/template.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.text.splitlines()[0] == "course_code,title,arabic_title,sort_no,is_public"


def test_launchpad_group_lists_config(admin):
    r = admin.get("/home/academics/config")
    assert r.status_code == 200
    assert "/academics/config/sessions" in r.text


# --------------------------------------------------------------------------- sessions
def test_session_create_edit_toggle(admin, db):
    post(admin, f"{BASE}/sessions/new", {"category": "45 Minutes", "start_time": "07:20", "duration_minutes": "45", "status": "active", "sort_no": "99"})
    s = db.query(SessionSlot).filter(SessionSlot.category == "45 Minutes", SessionSlot.sort_no == 99).first()
    assert s and s.label == "07:20 AM - 08:05 AM"
    post(admin, f"{BASE}/sessions/{s.id}/edit", {"category": "45 Minutes", "start_time": "07:25", "duration_minutes": "45", "status": "active", "sort_no": "99", "label": ""})
    db.expire_all()
    assert db.get(SessionSlot, s.id).label == "07:25 AM - 08:10 AM"
    post(admin, f"{BASE}/sessions/{s.id}/toggle", {})
    db.expire_all()
    assert db.get(SessionSlot, s.id).status == "inactive"
    # duplicate start time in the same category is refused with a flash (still a redirect)
    post(admin, f"{BASE}/sessions/new", {"category": "45 Minutes", "start_time": "07:25", "duration_minutes": "45"})
    assert db.query(SessionSlot).filter(SessionSlot.category == "45 Minutes", SessionSlot.start_time == s.start_time).count() == 1


# --------------------------------------------------------------------------- courses
def test_course_create_edit_toggle(admin, db):
    post(admin, f"{BASE}/courses/new", {"code": "TEST_SCI", "name": "Science Tuition", "course_type": "Academics Tutoring", "fee": "6500",
                                        "attendance_required": "1", "curriculum_link": "https://example.org/science", "status": "active"})
    c = db.query(Course).filter(Course.code == "TEST_SCI").first()
    assert c and c.course_type == "Academics Tutoring" and float(c.fee) == 6500 and c.attendance_required
    post(admin, f"{BASE}/courses/{c.id}/edit", {"name": "Science Tuition (KS3)", "course_type": "Academics Tutoring", "fee": "7000", "status": "active"})
    db.expire_all()
    c = db.get(Course, c.id)
    assert c.name == "Science Tuition (KS3)" and float(c.fee) == 7000 and c.attendance_required is False
    post(admin, f"{BASE}/courses/{c.id}/toggle", {})
    db.expire_all()
    assert db.get(Course, c.id).is_active is False
    # duplicate code refused
    post(admin, f"{BASE}/courses/new", {"code": "TEST_SCI", "name": "Dup"})
    assert db.query(Course).filter(Course.code == "TEST_SCI").count() == 1


# --------------------------------------------------------------------------- packages
def test_package_create_edit_toggle(admin, db):
    post(admin, f"{BASE}/packages/new", {"name": "4 Days Package", "min_days": "1", "max_days": "4", "session_minutes": "30", "price": "48",
                                         "currency": "GBP", "status": "active"})
    p = db.query(Package).filter(Package.name == "4 Days Package").first()
    assert p and p.min_days == 1 and p.max_days == 4 and p.sessions_per_week == 4
    post(admin, f"{BASE}/packages/{p.id}/edit", {"name": "4 Days Package", "min_days": "2", "max_days": "4", "price": "50", "currency": "GBP",
                                                  "status": "active", "reason": "price review"})
    db.expire_all()
    assert db.get(Package, p.id).min_days == 2 and float(db.get(Package, p.id).price) == 50
    post(admin, f"{BASE}/packages/{p.id}/toggle", {})
    db.expire_all()
    assert db.get(Package, p.id).is_active is False


# --------------------------------------------------------------------------- books
def test_book_create_edit_toggle_upload(admin, db):
    course = db.query(Course).filter(Course.code == "IQRA").first()
    post(admin, f"{BASE}/books/new", {"course_id": str(course.id), "title": "Iqra Workbook", "arabic_title": "", "sort_no": "7", "is_public": "1", "status": "active"})
    b = db.query(Book).filter(Book.title == "Iqra Workbook").first()
    assert b and b.is_public and b.order == 7
    post(admin, f"{BASE}/books/{b.id}/edit", {"course_id": str(course.id), "title": "Iqra Workbook 2", "sort_no": "8", "status": "active"})
    db.expire_all()
    b = db.get(Book, b.id)
    assert b.title == "Iqra Workbook 2" and b.is_public is False and b.order == 8
    post(admin, f"{BASE}/books/{b.id}/toggle", {})
    db.expire_all()
    assert db.get(Book, b.id).status == "inactive"
    csv_bytes = ("course_code,title,arabic_title,sort_no,is_public\n"
                 "IQRA,Iqra Reader Part 1,,11,1\n"
                 "QAIDA,Qaida Practice Sheets,,12,0\n"
                 "NOPE,Unknown course,,1,0\n").encode()
    r = admin.post(f"{BASE}/books/upload", files={"file": ("books.csv", io.BytesIO(csv_bytes), "text/csv")})
    assert r.status_code == 303
    assert db.query(Book).filter(Book.title == "Iqra Reader Part 1", Book.is_public.is_(True)).first()
    assert db.query(Book).filter(Book.title == "Qaida Practice Sheets").first()
    # re-upload skips existing rows
    admin.post(f"{BASE}/books/upload", files={"file": ("books.csv", io.BytesIO(csv_bytes), "text/csv")})
    assert db.query(Book).filter(Book.title == "Iqra Reader Part 1").count() == 1


# --------------------------------------------------------------------------- staff sorting
def test_staff_sorting_save_and_move(admin, db):
    staff = db.query(Employee).filter(Employee.status == "active").order_by(Employee.sort_no, Employee.full_name).all()
    if len(staff) < 2:
        pytest.skip("people seed not loaded")
    first, second = staff[0], staff[1]
    post(admin, f"{BASE}/staff-sorting/save", {f"sort_{first.id}": "500"})
    db.expire_all()
    assert db.get(Employee, first.id).sort_no == 500
    post(admin, f"{BASE}/staff-sorting/save", {f"sort_{first.id}": str(first.sort_no or 1)})
    db.expire_all()
    order_before = [e.id for e in db.query(Employee).filter(Employee.status == "active").order_by(Employee.sort_no, Employee.full_name)]
    post(admin, f"{BASE}/staff-sorting/{order_before[1]}/move", {"direction": "up"})
    db.expire_all()
    order_after = [e.id for e in db.query(Employee).filter(Employee.status == "active").order_by(Employee.sort_no, Employee.full_name)]
    assert order_after[0] == order_before[1] and order_after[1] == order_before[0]
    post(admin, f"{BASE}/staff-sorting/{order_before[1]}/move", {"direction": "down"})
    db.expire_all()
    assert [e.id for e in db.query(Employee).filter(Employee.status == "active").order_by(Employee.sort_no, Employee.full_name)] == order_before


# --------------------------------------------------------------------------- invoice additions + rules
def test_invoice_addition_and_rule(admin, db):
    post(admin, f"{BASE}/invoice-additions/new", {"addition_type": "charge", "description": "Rescheduling Fee", "status": "active"})
    t = db.query(InvoiceAdditionType).filter(InvoiceAdditionType.description == "Rescheduling Fee").first()
    assert t and t.addition_type == "charge"
    post(admin, f"{BASE}/invoice-additions/{t.id}/edit", {"addition_type": "charge", "description": "Reschedule Fee", "status": "active"})
    db.expire_all()
    assert db.get(InvoiceAdditionType, t.id).description == "Reschedule Fee"
    post(admin, f"{BASE}/invoice-additions/{t.id}/toggle", {})
    db.expire_all()
    assert db.get(InvoiceAdditionType, t.id).status == "inactive"
    post(admin, f"{BASE}/invoice-additions/{t.id}/toggle", {})

    client = db.query(Client).order_by(Client.id).first()
    level = "client" if client else "global"
    data = {"level": level, "addition_type_id": str(t.id), "from_date": date.today().isoformat(), "to_date": "", "implementation_type": "fixed",
            "amount": "5", "status": "active", "reason": "test"}
    if client:
        data["client_id"] = str(client.id)
    before = db.query(InvoiceAdditionRule).count()
    post(admin, f"{BASE}/invoice-addition-rules/new", data)
    assert db.query(InvoiceAdditionRule).count() == before + 1
    r = db.query(InvoiceAdditionRule).order_by(InvoiceAdditionRule.id.desc()).first()
    assert r.level == level and float(r.amount) == 5 and (r.client_id == client.id if client else r.client_id is None)
    post(admin, f"{BASE}/invoice-addition-rules/{r.id}/edit", {**data, "implementation_type": "percent", "amount": "12.5", "auto_assigned": "1"})
    db.expire_all()
    r = db.get(InvoiceAdditionRule, r.id)
    assert r.implementation_type == "percent" and float(r.amount) == 12.5 and r.auto_assigned
    post(admin, f"{BASE}/invoice-addition-rules/{r.id}/toggle", {})
    db.expire_all()
    assert db.get(InvoiceAdditionRule, r.id).status == "inactive"
    # validation: percent > 100 is refused (redirect with error flash, no change)
    post(admin, f"{BASE}/invoice-addition-rules/{r.id}/edit", {**data, "implementation_type": "percent", "amount": "150"})
    db.expire_all()
    assert float(db.get(InvoiceAdditionRule, r.id).amount) == 12.5


# --------------------------------------------------------------------------- beneficiary accounts
def test_beneficiary_account_crud(admin, db):
    post(admin, f"{BASE}/beneficiary-accounts/new", {"payment_mode": "Bank", "category": "HBL", "account_name": "HBL Main", "account_details": "PK00HABB000", "status": "active"})
    a = db.query(BeneficiaryAccount).filter(BeneficiaryAccount.account_name == "HBL Main").first()
    assert a and a.payment_mode == "Bank" and a.is_auto is False
    post(admin, f"{BASE}/beneficiary-accounts/{a.id}/edit", {"payment_mode": "Online Payment Gateway", "category": "Stripe", "account_name": "HBL Main", "is_auto": "1", "status": "active"})
    db.expire_all()
    a = db.get(BeneficiaryAccount, a.id)
    assert a.payment_mode == "Online Payment Gateway" and a.is_auto
    post(admin, f"{BASE}/beneficiary-accounts/{a.id}/toggle", {})
    db.expire_all()
    assert db.get(BeneficiaryAccount, a.id).status == "inactive"


# --------------------------------------------------------------------------- client academic groups
def test_client_group_crud(admin, db):
    from app.models.core import User
    rep = db.query(User).filter(User.email == "manager@oqc.local").first()
    post(admin, f"{BASE}/client-groups/new", {"name": "Weekend Group", "pseudo_name": "Weekend Manager", "shift_group": "night",
                                              "representative_id": str(rep.id) if rep else "", "status": "active"})
    g = db.query(ClientAcademicGroup).filter(ClientAcademicGroup.name == "Weekend Group").first()
    assert g and g.pseudo_name == "Weekend Manager" and g.shift_group == "night"
    post(admin, f"{BASE}/client-groups/{g.id}/edit", {"name": "Weekend Group", "pseudo_name": "Weekend Lead", "shift_group": "morning", "status": "active"})
    db.expire_all()
    g = db.get(ClientAcademicGroup, g.id)
    assert g.pseudo_name == "Weekend Lead" and g.shift_group == "morning"
    post(admin, f"{BASE}/client-groups/{g.id}/toggle", {})
    db.expire_all()
    assert db.get(ClientAcademicGroup, g.id).status == "inactive"


# --------------------------------------------------------------------------- MS Teams users
def test_teams_user_crud(admin, db):
    emp = db.query(Employee).filter(Employee.status == "active").order_by(Employee.id.desc()).first()
    client = db.query(Client).order_by(Client.id.desc()).first()
    if not emp or not client:
        pytest.skip("people seed not loaded")
    post(admin, f"{BASE}/teams-users/new", {"person_type": "staff", "employee_id": str(emp.id), "teams_email": "wp1.staff@quran-college.org", "status": "active"})
    t = db.query(TeamsUser).filter(TeamsUser.teams_email == "wp1.staff@quran-college.org").first()
    assert t and t.employee_id == emp.id and t.display_name == emp.full_name
    post(admin, f"{BASE}/teams-users/new", {"person_type": "client", "client_id": str(client.id), "teams_email": "wp1.client@outlook.com", "display_name": "Family Account"})
    tc = db.query(TeamsUser).filter(TeamsUser.teams_email == "wp1.client@outlook.com").first()
    assert tc and tc.client_id == client.id and tc.person_type == "client"
    post(admin, f"{BASE}/teams-users/{t.id}/edit", {"person_type": "staff", "employee_id": str(emp.id), "teams_email": "wp1.staff2@quran-college.org", "display_name": "Ustadh", "status": "active"})
    db.expire_all()
    assert db.get(TeamsUser, t.id).teams_email == "wp1.staff2@quran-college.org"
    post(admin, f"{BASE}/teams-users/{t.id}/toggle", {})
    db.expire_all()
    assert db.get(TeamsUser, t.id).status == "inactive"
    # duplicate email refused
    post(admin, f"{BASE}/teams-users/new", {"person_type": "client", "client_id": str(client.id), "teams_email": "wp1.client@outlook.com"})
    assert db.query(TeamsUser).filter(TeamsUser.teams_email == "wp1.client@outlook.com").count() == 1


# --------------------------------------------------------------------------- assessments + question bank
def test_assessment_and_questions(admin, db):
    book = db.query(Book).filter(Book.title == "Iqra Book").first()
    post(admin, f"{BASE}/assessments/new", {"book_id": str(book.id), "title": "Iqra Book Assessment no 2", "passing_marks": "20", "total_marks": "40", "status": "active"})
    a = db.query(AssessmentDefinition).filter(AssessmentDefinition.title == "Iqra Book Assessment no 2").first()
    assert a and a.passing_marks == 20 and a.total_marks == 40
    assert admin.get(f"{BASE}/assessments/{a.id}").status_code == 200
    post(admin, f"{BASE}/question-bank/new", {"assessment_id": str(a.id), "question": "Read Iqra part 2 page 5.", "answer": "Fluent.", "question_type": "recitation", "marks": "10",
                                              "next": f"{BASE}/assessments/{a.id}"})
    q = db.query(QuestionBankItem).filter(QuestionBankItem.question == "Read Iqra part 2 page 5.").first()
    assert q and q.assessment_id == a.id and q.book_id == book.id and q.marks == 10
    post(admin, f"{BASE}/question-bank/{q.id}/edit", {"assessment_id": str(a.id), "book_id": str(book.id), "question": "Read Iqra part 2 page 6.", "question_type": "oral", "marks": "8", "status": "active"})
    db.expire_all()
    q = db.get(QuestionBankItem, q.id)
    assert q.question == "Read Iqra part 2 page 6." and q.question_type == "oral" and q.marks == 8
    post(admin, f"{BASE}/question-bank/{q.id}/toggle", {"next": f"{BASE}/assessments/{a.id}"})
    db.expire_all()
    assert db.get(QuestionBankItem, q.id).status == "inactive"
    post(admin, f"{BASE}/assessments/{a.id}/edit", {"book_id": str(book.id), "title": "Iqra Book Assessment no 2", "passing_marks": "22", "total_marks": "40", "status": "active"})
    db.expire_all()
    assert db.get(AssessmentDefinition, a.id).passing_marks == 22
    # passing > total refused
    post(admin, f"{BASE}/assessments/{a.id}/edit", {"title": "Iqra Book Assessment no 2", "passing_marks": "50", "total_marks": "40"})
    db.expire_all()
    assert db.get(AssessmentDefinition, a.id).passing_marks == 22
    post(admin, f"{BASE}/assessments/{a.id}/toggle", {})
    db.expire_all()
    assert db.get(AssessmentDefinition, a.id).status == "inactive"
    assert admin.get(f"{BASE}/assessments?status=inactive").status_code == 200
    assert admin.get(f"{BASE}/question-bank?assessment_id={a.id}").status_code == 200


# --------------------------------------------------------------------------- permissions and audit
def test_audit_events_written(db):
    from app.models.core import AuditEvent
    assert db.query(AuditEvent).filter(AuditEvent.module == "academic_config").count() >= 20


def test_viewer_cannot_mutate():
    c = TestClient(app, follow_redirects=False)
    r = c.post("/login", data={"username": "auditor@oqc.local", "password": "Auditor@123"})
    assert r.status_code == 303
    r = c.post(f"{BASE}/invoice-additions/new", data={"addition_type": "tax", "description": "Should fail"})
    assert r.status_code in (302, 303, 403)
    if r.status_code in (302, 303):
        assert BASE not in (r.headers.get("location") or "") or "login" in (r.headers.get("location") or "")
    db = SessionLocal()
    try:
        assert not db.query(InvoiceAdditionType).filter(InvoiceAdditionType.description == "Should fail").first()
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
