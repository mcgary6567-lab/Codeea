"""Student leaves (Module 12 — student side): request on behalf, approve/reject with session synchronisation
and the post-leave absence retention signal."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import csrf_protect, require
from app.core.notify import notify
from app.core.templating import render
from app.core.utils import paginate, parse_date, parse_int, redirect
from app.database import get_db
from app.models.core import RiskAlert, User
from app.models.people import Leave, Student
from app.models.scheduling import ClassSession
from app.services import scheduling as svc

router = APIRouter(prefix="/leaves/students", dependencies=[Depends(csrf_protect)])

LEAVE_TYPES = ["casual", "sick", "vacation", "emergency", "exam"]
STATUSES = ["pending", "approved", "rejected", "cancelled"]


def _get(db: Session, id: int) -> Leave:
    lv = db.get(Leave, id)
    if not lv or lv.person_type != "student":
        raise HTTPException(404, "Student leave not found")
    return lv


def _notify_parties(db: Session, lv: Leave, title: str, body: str) -> None:
    st = lv.student
    if not st:
        return
    for uid in [st.user_id, st.client.user_id if st.client else None, st.teacher.user_id if st.teacher else None]:
        if uid:
            notify(db, uid, title, body, event_type="leave_status", link="/portal/leaves")
    client = st.client
    if client and client.whatsapp and client.whatsapp_opt_in and client.user_id:
        notify(db, client.user_id, title, body, event_type="leave_status", channels=("whatsapp",), recipient_address=client.whatsapp)


@router.get("", include_in_schema=False)
def list_leaves(request: Request, page: int = 1, status: str = "", leave_type: str = "", q: str = "",
                date_from: str = "", date_to: str = "", db: Session = Depends(get_db),
                user: User = Depends(require("leaves.view"))):
    query = db.query(Leave).filter(Leave.person_type == "student")
    if status:
        query = query.filter(Leave.status == status)
    if leave_type:
        query = query.filter(Leave.leave_type == leave_type)
    df, dt = parse_date(date_from), parse_date(date_to)
    if df:
        query = query.filter(Leave.end_date >= df)
    if dt:
        query = query.filter(Leave.start_date <= dt)
    if q:
        like = f"%{q}%"
        query = query.join(Student, Student.id == Leave.student_id).filter(
            or_(Student.full_name.ilike(like), Student.student_code.ilike(like)))
    pg = paginate(query.order_by(Leave.status == "pending", Leave.start_date.desc(), Leave.id.desc()), page, 30)
    counts = dict(db.query(Leave.status, func.count(Leave.id)).filter(Leave.person_type == "student").group_by(Leave.status).all())
    today = date.today()
    on_leave_now = (db.query(func.count(Leave.id)).filter(Leave.person_type == "student", Leave.status == "approved",
                                                          Leave.start_date <= today, Leave.end_date >= today).scalar() or 0)
    base = f"/leaves/students?status={status}&leave_type={leave_type}&q={q}&date_from={date_from}&date_to={date_to}"
    return render(request, "student_leaves/list.html", {
        "user": user, "page": pg, "status": status, "leave_type": leave_type, "q": q, "date_from": df, "date_to": dt,
        "statuses": STATUSES, "leave_types": LEAVE_TYPES, "base_url": base,
        "stats": {"pending": counts.get("pending", 0), "approved": counts.get("approved", 0),
                  "rejected": counts.get("rejected", 0), "on_leave_now": on_leave_now},
        "student_options": [(s.id, f"{s.student_code} · {s.full_name}") for s in
                            db.query(Student).filter(Student.status.in_(["active", "trial"])).order_by(Student.full_name).limit(500)]})


@router.post("/new", include_in_schema=False)
async def create_leave(request: Request, db: Session = Depends(get_db), user: User = Depends(require("leaves.add"))):
    form = await request.form()
    student_id = parse_int(form.get("student_id"))
    start = parse_date(form.get("start_date"))
    end = parse_date(form.get("end_date"))
    student = db.get(Student, student_id) if student_id else None
    if not student or not start or not end:
        return redirect("/leaves/students", "Select a student and a valid date range.", "error")
    if end < start:
        return redirect("/leaves/students", "The end date must be on or after the start date.", "error")
    lv = Leave(person_type="student", student_id=student.id, leave_type=form.get("leave_type") or "casual",
               start_date=start, end_date=end, reason=form.get("reason") or None, status="pending", requested_by_id=user.id)
    db.add(lv)
    db.flush()
    log_action(db, user, "create", "leaves", entity=lv,
               description=f"Student leave requested on behalf of {student.full_name} ({start} → {end})",
               rationale=lv.reason, request=request)
    if form.get("auto_approve") and _can_approve(user):
        return await _approve(db, user, lv, form.get("note") or "Approved at creation on behalf of the parent.", request)
    db.commit()
    return redirect("/leaves/students?status=pending", f"Leave request created for {student.full_name}.")


def _can_approve(user: User) -> bool:
    from app.core import rbac
    return rbac.has_permission(user, "leaves.approve")


async def _approve(db: Session, user: User, lv: Leave, note: str, request: Request):
    lv.status = "approved"
    lv.approved_by_id = user.id
    lv.approved_at = datetime.utcnow()
    if note:
        lv.reason = (lv.reason or "") + f"\n[Approval] {note}"
    n = svc.apply_student_leave(db, lv, user, request=request)
    log_action(db, user, "approve", "leaves", entity=lv,
               description=f"Student leave approved ({lv.start_date} → {lv.end_date}); {n} class(es) moved to leave",
               rationale=note, request=request, consequential=True)
    _notify_parties(db, lv, "Leave approved",
                    f"Leave from {lv.start_date} to {lv.end_date} is approved. {n} scheduled class(es) were marked as leave.")
    db.commit()
    return redirect("/leaves/students", f"Leave approved; {n} class(es) marked as leave and the parent was notified.")


@router.post("/{id}/approve", include_in_schema=False)
async def approve_leave(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leaves.approve"))):
    lv = _get(db, id)
    if lv.status == "approved":
        return redirect("/leaves/students", "This leave is already approved.", "info")
    form = await request.form()
    return await _approve(db, user, lv, (form.get("note") or form.get("reason") or "").strip(), request)


@router.post("/{id}/reject", include_in_schema=False)
async def reject_leave(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leaves.approve"))):
    lv = _get(db, id)
    form = await request.form()
    note = (form.get("note") or form.get("reason") or "").strip()
    if not note:
        return redirect("/leaves/students", "A note is required when rejecting a leave request.", "error")
    was_approved = lv.status == "approved"
    lv.status = "rejected"
    lv.approved_by_id = user.id
    lv.approved_at = datetime.utcnow()
    lv.reason = (lv.reason or "") + f"\n[Rejected] {note}"
    n = svc.revert_student_leave(db, lv, user, request=request) if was_approved else 0
    log_action(db, user, "reject", "leaves", entity=lv, description=f"Student leave rejected; {n} class(es) restored to pending",
               rationale=note, request=request, consequential=True)
    _notify_parties(db, lv, "Leave request rejected",
                    f"The leave from {lv.start_date} to {lv.end_date} was not approved. {note}")
    db.commit()
    return redirect("/leaves/students", f"Leave rejected; {n} class(es) returned to pending.", "warning")


@router.post("/{id}/cancel", include_in_schema=False)
async def cancel_leave(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("leaves.update", "leaves.approve", any_of=True))):
    lv = _get(db, id)
    form = await request.form()
    note = (form.get("note") or form.get("reason") or "").strip() or "Cancelled by the college."
    lv.status = "cancelled"
    n = svc.revert_student_leave(db, lv, user, request=request)
    log_action(db, user, "cancel", "leaves", entity=lv, description=f"Student leave cancelled; {n} class(es) restored to pending",
               rationale=note, request=request, consequential=True)
    _notify_parties(db, lv, "Leave cancelled", f"The leave from {lv.start_date} to {lv.end_date} was cancelled. {note}")
    db.commit()
    return redirect("/leaves/students", f"Leave cancelled; {n} class(es) returned to pending.", "warning")


@router.get("/post-leave", include_in_schema=False)
def post_leave(request: Request, db: Session = Depends(get_db), user: User = Depends(require("leaves.view"))):
    today = date.today()
    ended = (db.query(Leave).filter(Leave.person_type == "student", Leave.status == "approved", Leave.end_date < today)
             .order_by(Leave.end_date.desc()).limit(60).all())
    rows = []
    for lv in ended:
        s = svc.first_session_after_leave(db, lv)
        rows.append({"leave": lv, "session": s, "absent": bool(s and s.status == "absent")})
    alerts = (db.query(RiskAlert).filter(RiskAlert.alert_type == "post_leave_absence")
              .order_by(RiskAlert.created_at.desc()).limit(20).all())
    return render(request, "student_leaves/post_leave.html", {
        "user": user, "rows": rows, "alerts": alerts,
        "flagged": sum(1 for r in rows if r["absent"])})


@router.post("/post-leave/scan", include_in_schema=False)
def post_leave_scan(request: Request, db: Session = Depends(get_db), user: User = Depends(require("leaves.update", "leaves.approve", any_of=True))):
    n = svc.flag_post_leave_absences(db)
    log_action(db, user, "execute", "leaves", description=f"Post-leave absence scan raised {n} retention alert(s)", request=request)
    db.commit()
    return redirect("/leaves/students/post-leave", f"Scan complete — {n} new post-leave absence alert(s).")
