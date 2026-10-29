"""Fusion gap report, step 2 and the open checks (21 Oct 2026): the three QA configuration pages, QA action types
on corrective actions, the "Generate classes" button, the Secret Pin Code kept hashed (FL-D-17), the change-password
rules and Cancel (FL-D-18), approver fields a requester cannot set (FL-D-15) and menu cards drawn once (FL-D-20).
"""
from __future__ import annotations

import collections
import re
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models.core import Setting
from app.models.erp import QAActionType
from app.models.scheduling import CorrectiveAction, QAReview

TAG = "fp2" + uuid.uuid4().hex[:6]


def _login(email: str, password: str) -> TestClient:
    c = TestClient(app)
    assert c.post("/login", data={"username": email, "password": password}, follow_redirects=False).status_code == 303
    return c


@pytest.fixture(scope="module")
def admin():
    return _login("admin@oqc.local", "Admin@12345")


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    s = SessionLocal()
    try:
        ids = [a.id for a in s.query(QAActionType).filter(QAActionType.name.like(f"{TAG}%"))]
        s.query(CorrectiveAction).filter(CorrectiveAction.description.like(f"{TAG}%")).delete(synchronize_session=False)
        s.query(QAActionType).filter(QAActionType.id.in_(ids or [-1])).delete(synchronize_session=False)
        s.commit()
    finally:
        s.close()


# ------------------------------------------------------------------------------------------------ QA configuration
def test_qa_configuration_has_the_three_erp_pages(admin):
    home = admin.get("/qa/config").text
    for key in ("review-parameters", "issue-types", "action-types"):
        assert f"/qa/config/{key}" in home
        assert admin.get(f"/qa/config/{key}").status_code == 200
    assert admin.get("/qa/config/nothing").status_code == 404
    params = admin.get("/qa/config/review-parameters").text
    assert "newParam" in params and "newIssue" not in params, "each page creates only its own records"


def test_an_action_type_sets_the_due_date_of_a_corrective_action(admin):
    r = admin.post("/qa/config/action-types/new", data={"name": f"{TAG} Coaching", "due_days": "5", "status": "active"})
    assert f"Action type &#39;{TAG} Coaching&#39; added" in r.text or f"Action type '{TAG} Coaching' added" in r.text
    dup = admin.post("/qa/config/action-types/new", data={"name": f"{TAG} coaching", "due_days": "5"})
    assert "already exists" in dup.text
    s = SessionLocal()
    try:
        at = s.query(QAActionType).filter(QAActionType.name == f"{TAG} Coaching").one()
        review = s.query(QAReview).first()
        assert review is not None, "the seed has QA reviews"
        rid, atid = review.id, at.id
    finally:
        s.close()
    admin.post(f"/qa/{rid}/corrective-action", data={"description": f"{TAG} pacing", "action_type_id": str(atid)})
    s = SessionLocal()
    try:
        ca = s.query(CorrectiveAction).filter(CorrectiveAction.description == f"{TAG} pacing").one()
        assert ca.action_type_id == atid
        assert ca.due_date is not None and (ca.due_date - date.today()).days == 5
    finally:
        s.close()
    assert f"{TAG} Coaching" in admin.get("/qa/corrective-actions").text
    admin.post(f"/qa/config/action-types/{atid}/toggle")
    s = SessionLocal()
    try:
        assert s.get(QAActionType, atid).status == "inactive"
    finally:
        s.close()


def test_class_schedules_offer_generate_classes(admin):
    html = admin.get("/classes").text
    assert "Generate classes" in html and "Generate sessions" not in html


# ------------------------------------------------------------------------------------------------ FL-D-17 secret pin
def test_the_secret_pin_is_entered_masked_and_kept_as_a_hash(admin):
    from app.web.company_config import is_hashed, setting_value, verify_secret
    s = SessionLocal()
    try:
        row = s.query(Setting).filter(Setting.key == "secret_pin_code").first()
        if row is None:
            pytest.skip("no secret pin in this database")
        sid, before = row.id, row.value
    finally:
        s.close()
    page = admin.get("/config/branch-properties?tab=general").text
    assert 'name="value" type="password"' in page or "type=\"password\"" in page
    mismatch = admin.post(f"/config/branch-properties/{sid}/save", data={"value": "2468", "confirm": "1357", "tab": "general"})
    assert "do not match" in mismatch.text
    letters = admin.post(f"/config/branch-properties/{sid}/save", data={"value": "abcd", "confirm": "abcd", "tab": "general"})
    assert "4 to 12 digits" in letters.text
    try:
        ok = admin.post(f"/config/branch-properties/{sid}/save", data={"value": "246810", "confirm": "246810", "tab": "general"})
        assert "saved" in ok.text
        s = SessionLocal()
        try:
            value = setting_value(s.get(Setting, sid))
            assert is_hashed(value) and "246810" not in str(value)
            assert verify_secret(s, "secret_pin_code", "246810")
            assert not verify_secret(s, "secret_pin_code", "000000")
        finally:
            s.close()
        page = admin.get("/config/branch-properties?tab=general").text
        assert "stored hashed" in page and "246810" not in page
        reveal = admin.post(f"/config/branch-properties/{sid}/reveal", data={"tab": "general"})
        assert "cannot be shown" in reveal.text
    finally:
        s = SessionLocal()
        try:
            s.get(Setting, sid).value = before
            s.commit()
        finally:
            s.close()


# ------------------------------------------------------------------------------------------------ FL-D-18 password form
def test_change_password_shows_its_rules_and_a_cancel(admin):
    html = admin.get("/profile?tab=password").text
    for rule in ("At least 8 characters", "At least one digit", "At least one letter"):
        assert rule in html
    assert ">Cancel</a>" in html


# ------------------------------------------------------------------------------------------------ FL-D-15 approver fields
def test_a_requester_cannot_set_the_status_or_hr_remarks_of_their_own_request():
    from app.models.hr_erp import StaffComplaint
    teacher = _login("teacher1@oqc.local", "Teacher@123")
    title = f"{TAG} complaint"
    teacher.post("/hr/complaints/new", data={"title": title, "description": "x", "complaint_type": "HR",
                                             "status": "approved", "admin_response": "self-approved",
                                             "next": "/hr/me?tab=complaints"})
    s = SessionLocal()
    try:
        rows = s.query(StaffComplaint).filter(StaffComplaint.title == title).all()
        for c in rows:
            assert c.status == "pending" and not c.admin_response
            s.delete(c)
        s.commit()
    finally:
        s.close()
    page = teacher.get("/hr/me?tab=advances").text
    if "Hr Remarks" in page:
        assert re.search(r'<input [^>]*type="text" value="" disabled[^>]*Filled in by People', page)


# ------------------------------------------------------------------------------------------------ FL-D-20 menu cards
def test_area_home_cards_are_drawn_once(admin):
    for url in ("/home", "/home/academics", "/home/hr", "/home/system"):
        body = re.sub(r"<aside.*?</aside>", "", admin.get(url).text, flags=re.S)
        hrefs = re.findall(r'<a href="([^"#?]+)" class="oqc-mcard ', body)
        assert hrefs, url
        assert not [h for h, n in collections.Counter(hrefs).items() if n > 1], url
