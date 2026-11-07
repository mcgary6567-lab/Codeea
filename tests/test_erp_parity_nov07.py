"""Old ERP parity, 7 Nov 2026: the customer's side-by-side screenshots and a crawl of the old ERP's pages (labels only).

Update Subscription as the ERP's dialog, All Subscriptions in the ERP's column order, the Billing Management
Dashboard's regions, and the list pages whose columns differed (Leads, Online Registrations, Evaluations, Student
Leaves, Clients Users, Complaints, Client List, Student Attendance Monitoring).
"""
from __future__ import annotations

import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app

TAG = f"par07-{uuid4().hex[:6]}"


@pytest.fixture(scope="module")
def admin():
    c = TestClient(app)
    assert c.post("/login", data={"username": "admin@oqc.local", "password": "Admin@12345"}, follow_redirects=False).status_code == 303
    return c


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _headers(html: str) -> list[str]:
    head = re.search(r"<thead.*?</thead>", html, re.S)
    return [re.sub(r"<[^>]+>|\s+", " ", h).strip() for h in re.findall(r"<th[^>]*>(.*?)</th>", head.group(0) if head else "", re.S)]


def _sub(db):
    from app.models.finance import Subscription
    from app.services import scheduling as sched
    return db.query(Subscription).filter(Subscription.status.in_(sched.RUNNING_SUBSCRIPTION_STATUSES)).order_by(Subscription.id).first()


# --------------------------------------------------------------------------- Update Subscription
def test_update_subscription_has_the_erp_tabs_fields_and_buttons(admin, db):
    s = _sub(db)
    html = admin.get(f"/subscriptions/{s.id}/edit").text
    for tab in ("Show All", "Update Subscription", "Books", "Subscription status log"):
        assert tab in html, tab
    for label in ("Language", "Days", "Course Method", "Gender", "Session", "Session Time", "Course", "Grade", "Status",
                  "Teacher", "Teacher Share", "Referred By", "Package", "Next Due Date", "Follow Up Date", "Remarks", "Status Remarks"):
        assert f">{label}<" in html or f">{label} <" in html, label
    for b in ("Cancel", "Change Log", "Apply Changes", "Starting Point", "Open Sequence", "History"):
        assert b in html, b
    assert ">Sun<" in html.replace("</label>", "<") or "Sun" in html


def test_apply_changes_saves_the_plain_fields_and_keeps_the_schedule(admin, db):
    from app.models.finance import Subscription
    s = _sub(db)
    before = (s.remarks, s.follow_up_date, s.status_remarks, s.teacher_id, s.slot_id, list(s.days_of_week or []))
    form = {"tab": "update", "language": s.language, "course_method": s.course_method, "course_id": s.course_id or "",
            "package_id": s.package_id or "", "next_billing_date": str(s.next_billing_date or ""), "follow_up_date": "2026-12-01",
            "remarks": f"{TAG} remark", "status_remarks": "", "teacher_id": s.teacher_id or "", "slot_id": s.slot_id or "",
            "status": "regular" if s.status in ("active", "regular") else s.status,
            **{f"day_{d}": "1" for d in (s.days_of_week or [])}}
    try:
        r = admin.post(f"/subscriptions/{s.id}/edit", data=form, follow_redirects=False)
        assert r.status_code == 303
        db.expire_all()
        s = db.get(Subscription, s.id)
        assert s.remarks == f"{TAG} remark" and str(s.follow_up_date) == "2026-12-01"
        assert (s.teacher_id, s.slot_id, list(s.days_of_week or [])) == before[3:], "unchanged teacher / time is not re-run"
    finally:
        s.remarks, s.follow_up_date, s.status_remarks = before[0], before[1], before[2]
        db.commit()


def test_freezing_from_the_form_needs_status_remarks(admin, db):
    s = _sub(db)
    r = admin.post(f"/subscriptions/{s.id}/edit", data={"status": "freeze", "status_remarks": "", "teacher_id": s.teacher_id or "",
                                                       "slot_id": s.slot_id or "", **{f"day_{d}": "1" for d in (s.days_of_week or [])}},
                   follow_redirects=True)
    assert "Status Remarks" in r.text and "reason is required" in r.text
    db.expire_all()
    from app.models.finance import Subscription
    assert db.get(Subscription, s.id).status not in ("freeze", "frozen")


def test_the_books_grid_adds_a_row_and_keeps_the_chosen_books_in_step(admin, db):
    from app.models.academic import Book
    from app.models.finance import Subscription, SubscriptionBook
    s = db.query(Subscription).filter(Subscription.course_id.isnot(None)).order_by(Subscription.id).first()
    book = db.query(Book).filter(Book.course_id == s.course_id).first()
    if not book:
        pytest.skip("no book on this course")
    saved = list(s.books or [])
    try:
        admin.post(f"/subscriptions/{s.id}/books", data={"new_book_id": book.id, "new_starting_point": f"{TAG} p.1"}, follow_redirects=False)
        db.expire_all()
        row = db.query(SubscriptionBook).filter_by(subscription_id=s.id, book_id=book.id).order_by(SubscriptionBook.id.desc()).first()
        assert row and row.starting_point == f"{TAG} p.1" and row.status == "active"
        assert book.id in db.get(Subscription, s.id).books
    finally:
        db.query(SubscriptionBook).filter(SubscriptionBook.subscription_id == s.id, SubscriptionBook.starting_point == f"{TAG} p.1").delete()
        db.get(Subscription, s.id).books = saved
        db.commit()


# --------------------------------------------------------------------------- All Subscriptions, Billing dashboard
def test_all_subscriptions_columns_in_the_erp_order_and_id_opens_update(admin, db):
    html = admin.get("/subscriptions").text
    h = _headers(html)
    h = [x for x in h if x]
    assert h[:11] == ["ID", "Teacher", "Client", "Student", "Reg. Date", "Method", "Course", "Grade", "Language", "Session", "Days"], h[:12]
    s = _sub(db)
    assert f'href="/subscriptions/{s.id}/edit"' in admin.get(f"/subscriptions?sub_id={s.subscription_code}").text


def test_billing_dashboard_has_the_erp_regions(admin):
    html = admin.get("/dashboards/billing").text
    for region in ("Over All Business Volume", "Total Regular Subscriptions", "Clients Receivables", "Opening", "Closing", "Difference",
                   "Invoices - Confirmed", "Payroll Amount", "Regular Subscriptions Flow Chart", "Cancelled Subscriptions Flow Chart",
                   "bdRegBars", "bdCanBars"):
        assert region in html, region


# --------------------------------------------------------------------------- list pages
@pytest.mark.parametrize("url,cols", [
    ("/crm/leads", ["ID", "Guardian Name", "Mobile No", "Whatsapp No", "Shift", "Referred By", "Country", "State", "City", "Street Address",
                    "Skype", "Email", "Zip Code", "How Came To Us", "Extra Query", "Followup Date", "Created At", "Status"]),
    ("/registrations", ["Reg ID", "Guardian Name", "Mobile No", "Whatsapp No", "Country", "State", "City", "Street Address", "Skype",
                        "Email", "Zip Code", "Referred By", "How Came To Us", "Extra Query", "Status"]),
    ("/academics/evaluations", ["ID", "Student", "Teacher", "Assesment", "Start Time", "End Time", "Passing Marks", "Total Marks",
                                "Obtained Marks", "Internal Remarks", "Public Remarks", "Status"]),
    ("/leaves/students", ["ID", "Client", "Student", "Shift", "Leave Apply Date", "Leave Start Date", "Leave End Date", "Leave Reason",
                          "Leave Detail", "Status"]),
    ("/clients/users", ["User Name", "First Name", "Status", "Shift", "Locked", "Phone", "Online Payment Link", "Meeting Link", "Feedback Link"]),
    ("/requests/complaints", ["ID", "Client", "Shift", "Complaint Type", "Title", "Description", "Company Response", "Status", "Created At",
                              "Overdue Actions", "Total Actions", "Take Action"]),
    ("/clients", ["ID", "Client Name", "Status", "Registration Date", "Students", "Currency", "Balance", "Fee Recurrence", "Email", "Cell",
                  "WhatsApp", "State", "Shift Name", "Referred By", "Status Remarks", "Billing Representative", "Academic Manager"]),
])
def test_list_pages_carry_the_erp_columns_in_order(admin, url, cols):
    h = [x for x in _headers(admin.get(url).text) if x]
    assert h[:len(cols)] == cols, (url, h[:len(cols) + 2])


def test_attendance_monitoring_has_alert_date(admin):
    assert "Alert Date" in _headers(admin.get("/classes/attendance-monitoring").text)


# --------------------------------------------------------------------------- Daily Performance Dashboard
def test_daily_performance_has_the_erp_panels_in_order(admin):
    from app.services.dashboards import DAILY_PANELS
    html = admin.get("/dashboards/daily-performance?start=2026-10-01&end=2026-11-07").text
    labels = [label for _, label, _, _ in DAILY_PANELS]
    assert len(labels) == 35 and labels[:5] == ["Dropped Subscriptions", "Freeze Subscriptions", "Completed Subscriptions", "New Trials",
                                                 "Regular Subscriptions"]
    positions = [html.find(label.replace(">", "&gt;")) for label in labels]
    assert all(p > 0 for p in positions), [l for l, p in zip(labels, positions) if p < 0]
    assert "Daily Performance Dashboard" in html and 'name="start"' in html and 'name="end"' in html
