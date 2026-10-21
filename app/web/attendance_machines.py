"""Attendance Machine List (HR › Time and Attendance) and the machines' push endpoint.

Staff pages under ``/hr/attendance-machines``; the machine endpoint is ``/iclock/...`` (ZKTeco ADMS "push" mode:
set the machine's server address to this site and its port to 443/80). Logic lives in
``app/services/attendance_machines.py``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import log_action
from app.core.deps import csrf_protect, require
from app.core.templating import render
from app.core.utils import parse_int, redirect
from app.database import get_db
from app.models.core import User
from app.models.hr_erp import AttendanceMachine, AttendancePunch
from app.models.people import Employee
from app.services import attendance_machines as svc

router = APIRouter(dependencies=[Depends(csrf_protect)])
BASE = "/hr/attendance-machines"


def _machine(db: Session, mid: int) -> AttendanceMachine:
    m = db.get(AttendanceMachine, mid)
    if not m:
        raise HTTPException(404, "Machine not found")
    return m


@router.get(BASE, include_in_schema=False)
def machines_page(request: Request, unmatched: str = "", db: Session = Depends(get_db),
                  user: User = Depends(require("hr_attendance.view"))):
    machines = db.query(AttendanceMachine).order_by(AttendanceMachine.name).all()
    q = db.query(AttendancePunch)
    if unmatched == "1":
        q = q.filter(AttendancePunch.employee_id.is_(None))
    punches = q.order_by(AttendancePunch.punched_at.desc(), AttendancePunch.id.desc()).limit(200).all()
    stats = {"machines": len(machines), "active": sum(1 for m in machines if m.status == "active"),
             "punches": db.query(func.count(AttendancePunch.id)).scalar() or 0,
             "unmatched": db.query(func.count(AttendancePunch.id)).filter(AttendancePunch.employee_id.is_(None)).scalar() or 0,
             "linked": db.query(func.count(Employee.id)).filter(Employee.attendance_machine_id.isnot(None)).scalar() or 0}
    return render(request, "hr/attendance_machines.html", {
        "user": user, "machines": machines, "punches": punches, "stats": stats, "unmatched": unmatched,
        "server_host": request.url.hostname, "machine_options": [(m.id, m.name) for m in machines],
        "employee_options": [(e.id, f"{e.employee_code} - {e.full_name}" + (f" (machine {e.attendance_machine_id})" if e.attendance_machine_id else ""))
                             for e in db.query(Employee).filter(Employee.status.in_(("active", "probation"))).order_by(Employee.employee_code)]})


def _apply(m: AttendanceMachine, form) -> str | None:
    name, serial = (form.get("name") or "").strip(), (form.get("serial_no") or "").strip()
    if not name or not serial:
        return "A machine needs a name and its serial number."
    m.name, m.serial_no = name[:120], serial[:60]
    m.model = (form.get("model") or "").strip()[:80] or None
    m.location = (form.get("location") or "").strip()[:120] or None
    m.ip_address = (form.get("ip_address") or "").strip()[:64] or None
    m.notes = (form.get("notes") or "").strip() or None
    return None


@router.post(f"{BASE}/new", include_in_schema=False)
async def machine_new(request: Request, db: Session = Depends(get_db), user: User = Depends(require("hr_attendance.update"))):
    form = await request.form()
    m = AttendanceMachine(name="", serial_no="", status="active")
    err = _apply(m, form)
    if err:
        return redirect(BASE, err, "error")
    if svc.machine_by_serial(db, m.serial_no):
        return redirect(BASE, f"A machine with serial number {m.serial_no} is already registered.", "error")
    db.add(m)
    db.flush()
    log_action(db, user, "create", "hr_attendance", entity=m, description=f"Attendance machine {m.name} ({m.serial_no}) registered", request=request)
    db.commit()
    return redirect(BASE, f"Machine '{m.name}' registered. Point it at this server to start receiving punches.")


@router.post(BASE + "/{mid}/edit", include_in_schema=False)
async def machine_edit(mid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("hr_attendance.update"))):
    m = _machine(db, mid)
    form = await request.form()
    old_serial = m.serial_no
    err = _apply(m, form)
    if err:
        return redirect(BASE, err, "error")
    other = svc.machine_by_serial(db, m.serial_no)
    if m.serial_no != old_serial and other and other.id != m.id:
        db.rollback()
        return redirect(BASE, f"Serial number {m.serial_no} belongs to another machine.", "error")
    log_action(db, user, "update", "hr_attendance", entity=m, description=f"Attendance machine {m.name} updated", request=request)
    db.commit()
    return redirect(BASE, f"Machine '{m.name}' saved.")


@router.post(BASE + "/{mid}/toggle", include_in_schema=False)
async def machine_toggle(mid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("hr_attendance.update"))):
    m = _machine(db, mid)
    m.status = "inactive" if m.status == "active" else "active"
    log_action(db, user, "update", "hr_attendance", entity=m, description=f"Attendance machine {m.name} set {m.status}", request=request)
    db.commit()
    return redirect(BASE, f"Machine '{m.name}' is now {m.status}.")


@router.post(f"{BASE}/upload", include_in_schema=False)
async def punches_upload(request: Request, db: Session = Depends(get_db), user: User = Depends(require("hr_attendance.update"))):
    """A log exported from the machine's own software (CSV, or the tab-separated attendance log)."""
    form = await request.form()
    upload = form.get("file")
    if upload is None or not getattr(upload, "filename", ""):
        return redirect(BASE, "Attach the attendance log file.", "error")
    content = await upload.read()
    machine = db.get(AttendanceMachine, parse_int(form.get("machine_id"))) if parse_int(form.get("machine_id")) else None
    result = svc.import_file(db, machine, upload.filename, content, user)
    log_action(db, user, "create", "hr_attendance", entity_type="AttendancePunch",
               description=f"Attendance log {upload.filename}: {result['added']} punches added, {result['duplicates']} already had, "
                           f"{result['unmatched']} without an employee, {result['days']} attendance days updated", request=request)
    db.commit()
    msg = (f"{result['read']} punches read: {result['added']} new, {result['duplicates']} already recorded, "
           f"{result['unmatched']} with no employee linked; {result['days']} attendance day(s) updated.")
    if result["problems"]:
        msg += " " + "; ".join(result["problems"][:3])
    return redirect(BASE, msg, "success" if result["read"] and not result["problems"] else "warning")


@router.post(f"{BASE}/link", include_in_schema=False)
async def link_employee(request: Request, db: Session = Depends(get_db), user: User = Depends(require("hr_attendance.update"))):
    """Link a machine user ID to an employee; their earlier unmatched punches are matched and applied."""
    form = await request.form()
    pin = (form.get("machine_user_id") or "").strip()[:20]
    emp = db.get(Employee, parse_int(form.get("employee_id")) or 0)
    if not pin or not emp:
        return redirect(BASE, "Choose the employee and the machine user ID.", "error")
    taken = db.query(Employee).filter(Employee.attendance_machine_id == pin, Employee.id != emp.id).first()
    if taken:
        return redirect(BASE, f"Machine user {pin} is already linked to {taken.full_name}.", "error")
    emp.attendance_machine_id = pin
    days = set()
    for p in db.query(AttendancePunch).filter(AttendancePunch.machine_user_id == pin, AttendancePunch.employee_id.is_(None)):
        p.employee_id = emp.id
        days.add(p.punched_at.date())
    db.flush()
    rebuilt = sum(1 for d in days if svc.rebuild_day(db, emp.id, d))
    log_action(db, user, "update", "hr_attendance", entity=emp,
               description=f"{emp.employee_code} linked to machine user {pin}; {rebuilt} attendance day(s) rebuilt", request=request)
    db.commit()
    return redirect(BASE, f"{emp.full_name} linked to machine user {pin}; {rebuilt} attendance day(s) updated from earlier punches.")


# ============================================================================ machine push (ZKTeco ADMS / iclock)
machine_router = APIRouter()


def _known(db: Session, sn: str) -> AttendanceMachine:
    m = svc.machine_by_serial(db, sn)
    if not m or m.status != "active":
        raise HTTPException(403, "Unknown or inactive machine")
    return m


@machine_router.get("/iclock/cdata", include_in_schema=False)
def iclock_handshake(request: Request, SN: str = "", db: Session = Depends(get_db)):
    m = _known(db, SN)
    from datetime import datetime
    m.last_seen_at = datetime.utcnow()
    m.ip_address = (request.client.host if request.client else m.ip_address)
    db.commit()
    body = "\n".join([f"GET OPTION FROM: {SN}", "ATTLOGStamp=None", "OPERLOGStamp=9999", "ATTPHOTOStamp=None",
                      "ErrorDelay=60", "Delay=30", "TransTimes=00:00;14:05", "TransInterval=1",
                      "TransFlag=TransData AttLog", "Realtime=1", "Encrypt=0"])
    return PlainTextResponse(body)


@machine_router.post("/iclock/cdata", include_in_schema=False)
async def iclock_data(request: Request, SN: str = "", table: str = "", db: Session = Depends(get_db)):
    m = _known(db, SN)
    text = (await request.body()).decode("utf-8", errors="replace")
    if table.upper() != "ATTLOG":
        from datetime import datetime
        m.last_seen_at = datetime.utcnow()
        db.commit()
        return PlainTextResponse("OK")
    punches = svc.parse_attlog(text)
    result = svc.record(db, m, punches, source="push")
    db.commit()
    return PlainTextResponse(f"OK: {result['added'] + result['duplicates']}")


@machine_router.get("/iclock/getrequest", include_in_schema=False)
def iclock_getrequest(SN: str = "", db: Session = Depends(get_db)):
    m = _known(db, SN)
    from datetime import datetime
    m.last_seen_at = datetime.utcnow()
    db.commit()
    return PlainTextResponse("OK")


router.include_router(machine_router)
