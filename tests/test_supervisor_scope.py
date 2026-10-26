"""Supervisors see only the teachers they supervise and those teachers' students and families (27 Oct 2026)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models.core import User
from app.models.finance import Subscription
from app.models.people import Client, Student, Teacher
from app.models.scheduling import Schedule


def _login(email: str, password: str) -> TestClient:
    c = TestClient(app)
    assert c.post("/login", data={"username": email, "password": password}, follow_redirects=False).status_code == 303
    return c


@pytest.fixture(scope="module")
def world():
    s = SessionLocal()
    sup = s.query(User).filter(User.email == "supervisor@oqc.local").one()
    mine = {t.id for t in s.query(Teacher).filter(Teacher.supervisor_id == sup.id)}
    assert mine, "the seed gives the supervisor some teachers"

    def pick(model, col):
        own = s.query(model).filter(col.in_(mine)).first()
        other = s.query(model).filter(col.isnot(None), col.notin_(mine)).first()
        return own, other

    st_own, st_other = pick(Student, Student.teacher_id)
    sub_own, sub_other = pick(Subscription, Subscription.teacher_id)
    sch_own, sch_other = pick(Schedule, Schedule.teacher_id)
    other_teacher = s.query(Teacher).filter(Teacher.id.notin_(mine)).first()
    own_client = st_own.client_id if st_own else None
    # a family none of whose students is taught by the supervisor's teachers
    other_client = next((c for c in s.query(Client).all()
                         if c.students and all(x.teacher_id not in mine for x in c.students)), None)
    data = {"student": (st_own.id if st_own else None, st_other.id if st_other else None,
                        st_other.student_code if st_other else None),
            "subscription": (sub_own.id if sub_own else None, sub_other.id if sub_other else None),
            "schedule": (sch_own.id if sch_own else None, sch_other.id if sch_other else None),
            "teacher": (next(iter(mine)), other_teacher.id if other_teacher else None,
                        other_teacher.teacher_code if other_teacher else None),
            "client": (own_client, other_client.id if other_client else None,
                       other_client.client_code if other_client else None)}
    s.close()
    return data


@pytest.fixture(scope="module")
def sup():
    return _login("supervisor@oqc.local", "Super@123")


def _denied(r) -> bool:
    return r.status_code in (302, 303, 403)


@pytest.mark.parametrize("kind,urls", [
    ("student", ["/students/{}", "/students/{}/print", "/academics/progress/{}"]),
    ("subscription", ["/subscriptions/{}", "/subscriptions/{}/day-wise", "/finance/subscriptions/{}"]),
    ("schedule", ["/schedules/{}"]),
    ("teacher", ["/teachers/{}"]),
    ("client", ["/clients/{}"]),
])
def test_own_records_open_and_others_are_refused(sup, world, kind, urls):
    own, other = world[kind][0], world[kind][1]
    for url in urls:
        if own:
            assert sup.get(url.format(own)).status_code == 200, url.format(own)
        if other:
            assert _denied(sup.get(url.format(other), follow_redirects=False)), url.format(other)


def test_lists_hold_only_their_own(sup, world):
    other_student = world["student"][2]   # codes, not names: two families may share a name
    other_teacher = world["teacher"][2]
    other_family = world["client"][2]
    main = lambda url: sup.get(url).text.split("<main", 1)[-1]  # noqa: E731
    if other_student:
        assert other_student not in main("/students?rows=500")
    if other_teacher:
        assert other_teacher not in main("/teachers?rows=500").split("<table", 1)[-1]
    if other_family:
        assert other_family not in main("/clients?rows=500")


def test_management_still_sees_everything(world):
    admin = _login("admin@oqc.local", "Admin@12345")
    for kind, url in (("student", "/students/{}"), ("client", "/clients/{}"), ("teacher", "/teachers/{}")):
        if world[kind][1]:
            assert admin.get(url.format(world[kind][1])).status_code == 200
