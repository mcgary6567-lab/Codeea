"""Counters for the Academic Home launchpad (mirrors the ERP: pending client requests + today's class status)."""
from __future__ import annotations

from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.crm import Case
from app.models.erp import TimeChangeRequest, TeacherChangeRequest, ReferredContact
from app.models.people import Leave, Student
from app.models.scheduling import ClassSession


def pending_requests(db: Session) -> list[dict]:
    """Client's Pending Requests row. Each entry links to the request list filtered on Pending."""
    def n(q):
        return q.count()
    return [
        {"label": "Client Complaints", "value": n(db.query(Case).filter(Case.case_type == "complaint", Case.approval_status == "pending")),
         "href": "/requests/complaints?status=pending"},
        {"label": "Leave Applications", "value": n(db.query(Leave).filter(Leave.person_type == "student", Leave.status == "pending")),
         "href": "/requests/leaves?status=pending"},
        {"label": "New References", "value": n(db.query(ReferredContact).filter(ReferredContact.status == "pending")),
         "href": "/requests/references?status=pending"},
        {"label": "Teacher Change Request", "value": n(db.query(TeacherChangeRequest).filter(TeacherChangeRequest.status == "pending")),
         "href": "/requests/teacher-change?status=pending"},
        {"label": "Time Change Request", "value": n(db.query(TimeChangeRequest).filter(TimeChangeRequest.status == "pending")),
         "href": "/requests/time-change?status=pending"},
    ]


def todays_class_status(db: Session, day: date | None = None) -> list[dict]:
    """Today's Class Status Summary row. Each entry links to the class schedule filtered on that status."""
    day = day or date.today()
    counts = {s: c for s, c in db.query(ClassSession.status, func.count(ClassSession.id))
              .filter(ClassSession.date == day).group_by(ClassSession.status)}
    free = db.query(Student).filter(Student.status == "free").count()
    rows = [("Free Students", free, "/students?status=free")]
    for label, key in [("Pending Classes", "pending"), ("Done Classes", "done"), ("Missed Classes", "missed"),
                       ("Student Absent", "absent"), ("Student On-leave", "leave"), ("Cancelled Classes", "cancelled"),
                       ("Rescheduled Classes", "rescheduled")]:
        rows.append((label, counts.get(key, 0), f"/classes?date={day.isoformat()}&status={key}"))
    return [{"label": l, "value": v, "href": h} for l, v, h in rows]
