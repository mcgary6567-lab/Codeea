"""Online registration (Module 6): public /register form -> Lead + Trial, and the admin /registrations conversion desk."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.audit import log_action
from app.core.deps import require, csrf_protect, get_optional_user
from app.core.notify import notify
from app.core.templating import render
from app.core.utils import redirect, paginate, next_code, parse_int, parse_bool
from app.database import get_db
from app.models.academic import Course
from app.models.core import User, Role
from app.models.crm import Lead, LeadSource, Referral
from app.models.people import Client, Student
from app.models.scheduling import Trial
from app.services import people as svc

router = APIRouter(dependencies=[Depends(csrf_protect)])

HOW_HEARD = ["Google search", "Facebook / Instagram", "YouTube", "TikTok", "A friend or family member",
             "My local masjid", "WhatsApp", "Other"]
PREFERRED_TIMES = ["Weekday mornings", "Weekday afternoons", "Weekday evenings", "Late evenings", "Weekends", "Flexible"]


# --------------------------------------------------------------------------- helpers
def _website_source(db: Session) -> LeadSource:
    src = db.query(LeadSource).filter(func.lower(LeadSource.name) == "website").first()
    if not src:
        src = LeadSource(name="Website", source_type="inbound", is_active=True)
        db.add(src)
        db.flush()
    return src


def _duplicate_check(db: Session, phone: str | None, email: str | None) -> tuple[list[Lead], list[Client]]:
    """Duplicate detection across Leads and Clients on phone tail / email."""
    lead_dups: list[Lead] = []
    try:
        from app.services.crm import find_duplicates
        lead_dups = find_duplicates(db, phone=phone, email=email, whatsapp=phone)
    except Exception:
        conds = []
        if email:
            conds.append(func.lower(Lead.email) == email.lower())
        if phone and len(phone) >= 7:
            conds.append(Lead.phone.like(f"%{phone[-8:]}"))
        if conds:
            lead_dups = db.query(Lead).filter(or_(*conds)).limit(5).all()
    cconds = []
    if email:
        cconds.append(func.lower(Client.email) == email.lower())
    if phone and len(phone) >= 7:
        cconds.append(Client.phone.like(f"%{phone[-8:]}"))
        cconds.append(Client.whatsapp.like(f"%{phone[-8:]}"))
    client_dups = db.query(Client).filter(or_(*cconds)).limit(5).all() if cconds else []
    return lead_dups, client_dups


def _students_from_lead(lead: Lead) -> list[dict]:
    """Recover the registered children from the structured line stored in Lead.notes."""
    import re
    out: list[dict] = []
    for line in (lead.notes or "").splitlines():
        if not line.lower().startswith("students:"):
            continue
        for chunk in line.split(":", 1)[1].split(";"):
            m = re.match(r"\s*(.+?)\s*\((male|female)\s*,\s*(\d+|\?)y\)\s*$", chunk)
            if m:
                out.append({"name": m.group(1), "gender": m.group(2),
                            "age": int(m.group(3)) if m.group(3).isdigit() else None})
            elif chunk.strip():
                out.append({"name": chunk.strip(), "gender": "male", "age": None})
    if not out and lead.student_name:
        out.append({"name": lead.student_name, "gender": "male", "age": lead.student_age})
    while len(out) < (lead.students_count or 1):
        out.append({"name": "", "gender": "male", "age": None})
    return out


def _score(db: Session, lead: Lead) -> None:
    try:
        from app.services.crm import score_lead
        score_lead(db, lead)
        return
    except Exception:
        pass
    try:
        from app.services.ai_gateway import ai
        result, run = ai(db, "lead_scoring", "score_lead", {
            "country": lead.country, "source": "Website", "has_whatsapp": bool(lead.whatsapp),
            "students_count": lead.students_count, "preferred_time": lead.preferred_time, "lead_id": lead.id}, entity=lead)
        lead.score = int(result.get("score", 0))
        lead.score_factors = dict(result.get("factors") or {})
    except Exception:
        lead.score = 50


def _notify_owners(db: Session, lead: Lead, students: list[dict]) -> None:
    targets: dict[int, User] = {}
    for slug in ("lead_closer", "hod_marketing"):
        for u in db.query(User).join(Role, User.role_id == Role.id).filter(Role.slug == slug, User.is_active.is_(True)):
            targets[u.id] = u
    body = (f"{lead.full_name} ({lead.country or 'unknown country'}) registered on the website for "
            f"{len(students)} student(s): " + ", ".join(f"{s['name']} ({s['age'] or '?'})" for s in students) +
            f". Preferred time: {lead.preferred_time or 'not stated'}. Score {lead.score}.")
    for u in targets.values():
        notify(db, u, f"New website registration — {lead.lead_code}", body, event_type="lead_created", link="/registrations")


# --------------------------------------------------------------------------- public form
@router.get("/register", include_in_schema=False)
def register_form(request: Request, ref: str = "", db: Session = Depends(get_db), user=Depends(get_optional_user)):
    courses = db.query(Course).filter(Course.is_active.is_(True)).order_by(Course.order).all()
    ambassador = db.query(Client).filter(func.upper(Client.referral_code) == ref.strip().upper()).first() if ref.strip() else None
    return render(request, "registration/register.html", {
        "user": None, "course_options": [(c.id, c.name) for c in courses], "countries": svc.COUNTRY_NAMES, "timezones": svc.TIMEZONES,
        "country_map": {k: {"timezone": v[1], "currency": v[2], "dial": v[3]} for k, v in svc.COUNTRY_MAP.items()},
        "how_heard": HOW_HEARD, "preferred_times": PREFERRED_TIMES, "ref": ref.strip().upper(),
        "ambassador": ambassador, "values": {}, "error": None, "signed_in": user is not None})


@router.post("/register", include_in_schema=False)
async def register_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    if (form.get("website") or "").strip():          # honeypot
        return render(request, "registration/thanks.html", {"user": None, "lead": None, "students": []})

    def rerender(msg: str):
        courses = db.query(Course).filter(Course.is_active.is_(True)).order_by(Course.order).all()
        return render(request, "registration/register.html", {
            "user": None, "course_options": [(c.id, c.name) for c in courses], "countries": svc.COUNTRY_NAMES, "timezones": svc.TIMEZONES,
            "country_map": {k: {"timezone": v[1], "currency": v[2], "dial": v[3]} for k, v in svc.COUNTRY_MAP.items()},
            "how_heard": HOW_HEARD, "preferred_times": PREFERRED_TIMES, "ref": (form.get("ref") or "").upper(),
            "ambassador": None, "values": dict(form), "error": msg, "signed_in": False}, status_code=200)

    full_name = (form.get("full_name") or "").strip()
    phone = (form.get("phone") or "").strip()
    email = (form.get("email") or "").strip().lower() or None
    if not full_name:
        return rerender("Please tell us the parent or guardian's full name.")
    if not phone and not email:
        return rerender("Please give us either a phone number or an email address so we can reach you.")
    if not parse_bool(form.get("consent")):
        return rerender("We need your consent to the safeguarding, recording and data-protection policy before we can register a child.")

    students = []
    for i in (1, 2, 3):
        name = (form.get(f"s{i}_name") or "").strip()
        if not name:
            continue
        students.append({"name": name, "age": parse_int(form.get(f"s{i}_age")), "gender": form.get(f"s{i}_gender") or "male",
                         "course_id": parse_int(form.get(f"s{i}_course_id"))})
    if not students:
        return rerender("Please add at least one student with their name and age.")

    country = form.get("country") or "United Kingdom"
    info = svc.COUNTRY_MAP.get(country, svc.COUNTRY_MAP["Other"])
    timezone = form.get("timezone") or info[1]
    ref_code = (form.get("ref") or "").strip().upper() or None
    lead_dups, client_dups = _duplicate_check(db, phone, email)

    source = _website_source(db)
    first = students[0]
    lead = Lead(lead_code=next_code(db, Lead, "lead_code", "L-"), full_name=full_name, email=email, phone=phone or None,
                whatsapp=(form.get("whatsapp") or phone or None), country=country, timezone=timezone,
                student_name=first["name"], student_age=first["age"], students_count=len(students),
                course_interest_id=first["course_id"], preferred_time=form.get("preferred_time") or None,
                source_id=source.id, referral_code=ref_code, stage="new",
                whatsapp_opt_in=parse_bool(form.get("whatsapp_opt_in")),
                is_duplicate_of_id=lead_dups[0].id if lead_dups else None,
                notes="\n".join([
                    f"How did you hear about us: {form.get('how_heard') or 'not stated'}",
                    f"Students: " + "; ".join(f"{s['name']} ({s['gender']}, {s['age'] or '?'}y)" for s in students),
                    f"Message: {(form.get('message') or '').strip()}" if (form.get("message") or "").strip() else "",
                    f"Existing client match: {', '.join(c.client_code for c in client_dups)}" if client_dups else "",
                ]).strip())
    db.add(lead)
    db.flush()
    _score(db, lead)

    # referral: link the ambassador's Referral ledger row
    if ref_code:
        try:
            from app.services.crm import link_referral
            link_referral(db, lead)
        except Exception:
            amb = db.query(Client).filter(func.upper(Client.referral_code) == ref_code).first()
            if amb:
                db.add(Referral(ambassador_client_id=amb.id, referral_code=ref_code, referred_name=full_name,
                                referred_phone=phone or None, referred_lead_id=lead.id, status="lead",
                                invited_at=datetime.utcnow(), credit_currency=amb.currency))
                db.flush()

    trial = Trial(lead_id=lead.id, student_name=first["name"], course_id=first["course_id"], status="requested",
                  notes=f"Requested from the website registration form. Preferred time: {lead.preferred_time or 'flexible'}.",
                  follow_up_date=(datetime.utcnow() + timedelta(days=1)).date())
    db.add(trial)
    db.flush()

    log_action(db, None, "create", "registration", entity=lead, request=request,
               description=f"Website registration {lead.lead_code}: {full_name} — {len(students)} student(s)"
                           + (" (possible duplicate)" if (lead_dups or client_dups) else ""),
               after={"students": len(students), "score": lead.score, "referral": ref_code,
                      "duplicate_of_lead": lead.is_duplicate_of_id,
                      "duplicate_clients": [c.client_code for c in client_dups]})
    _notify_owners(db, lead, students)
    db.commit()
    return render(request, "registration/thanks.html", {"user": None, "lead": lead, "students": students})


# --------------------------------------------------------------------------- admin desk
@router.get("/registrations", include_in_schema=False)
def registrations(request: Request, page: int = 1, q: str = "", stage: str = "", dup: str = "",
                  db: Session = Depends(get_db), user: User = Depends(require("registration.view"))):
    src = db.query(LeadSource).filter(func.lower(LeadSource.name) == "website").first()
    query = db.query(Lead).filter(Lead.source_id == (src.id if src else -1))
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Lead.full_name.ilike(like), Lead.lead_code.ilike(like), Lead.email.ilike(like),
                                 Lead.phone.ilike(like), Lead.student_name.ilike(like)))
    if stage:
        query = query.filter(Lead.stage == stage)
    if dup == "yes":
        query = query.filter(Lead.is_duplicate_of_id.isnot(None))
    pg = paginate(query.order_by(Lead.created_at.desc()), page, 25)
    base_q = db.query(Lead).filter(Lead.source_id == (src.id if src else -1))
    all_rows = base_q.all()
    stats = {"total": len(all_rows), "new": sum(1 for l in all_rows if l.stage == "new"),
             "converted": sum(1 for l in all_rows if l.converted_client_id),
             "duplicates": sum(1 for l in all_rows if l.is_duplicate_of_id),
             "students": sum(l.students_count or 1 for l in all_rows),
             "avg_score": round(sum(l.score or 0 for l in all_rows) / len(all_rows), 1) if all_rows else 0}
    return render(request, "registration/list.html", {"user": user, "page": pg, "q": q, "stage": stage, "dup": dup,
                                                      "stats": stats, "base_url": f"/registrations?q={q}&stage={stage}&dup={dup}",
                                                      "stages": ["new", "contacted", "trial_scheduled", "trial_done", "negotiation", "won", "lost"],
                                                      "can_convert": True})


@router.get("/registrations/{id}", include_in_schema=False)
def registration_detail(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("registration.view"))):
    lead = db.query(Lead).get(id)
    if not lead:
        raise HTTPException(404, "Registration not found")
    courses = db.query(Course).filter(Course.is_active.is_(True)).order_by(Course.order).all()
    trials = db.query(Trial).filter(Trial.lead_id == lead.id).order_by(Trial.created_at.desc()).all()
    dup = db.query(Lead).get(lead.is_duplicate_of_id) if lead.is_duplicate_of_id else None
    referral = db.query(Referral).filter(Referral.referred_lead_id == lead.id).first()
    return render(request, "registration/detail.html", {"user": user, "lead": lead, "trials": trials,
                                                        "course_options": [(c.id, c.name) for c in courses],
                                                        "kids": _students_from_lead(lead),
                                                        "relationships": svc.RELATIONSHIPS,
                                                        "can_convert": rbac.has_permission(user, "registration.approve"),
                                                        "dup": dup, "referral": referral,
                                                        "countries": svc.COUNTRY_NAMES, "timezones": svc.TIMEZONES,
                                                        "converted": db.query(Client).get(lead.converted_client_id) if lead.converted_client_id else None})


@router.post("/registrations/{id}/convert", include_in_schema=False)
async def convert(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("registration.approve"))):
    lead = db.query(Lead).get(id)
    if not lead:
        raise HTTPException(404, "Registration not found")
    if lead.converted_client_id:
        return redirect(f"/clients/{lead.converted_client_id}", "This registration was already converted.", "info")
    form = await request.form()
    names = [n.strip() for n in form.getlist("student_name") if (n or "").strip()]
    if not names:
        return redirect(f"/registrations/{lead.id}", "Add at least one student name to convert this registration.", "error")
    country = form.get("country") or lead.country or "United Kingdom"
    info = svc.COUNTRY_MAP.get(country, svc.COUNTRY_MAP["Other"])
    client, pwd = svc.create_client_with_portal(db, {
        "full_name": lead.full_name, "email": lead.email, "phone": lead.phone, "whatsapp": lead.whatsapp,
        "country": country, "timezone": form.get("timezone") or lead.timezone or info[1], "currency": info[2],
        "relationship_to_student": form.get("relationship_to_student") or "father", "status": "trial",
        "source": "Website", "consent_given": True, "whatsapp_opt_in": lead.whatsapp_opt_in,
        "lead_id": lead.id, "notes": lead.notes}, user, request=request, with_portal=True)

    ages = form.getlist("student_age")
    genders = form.getlist("student_gender")
    course_ids = form.getlist("student_course_id")
    created: list[Student] = []
    for i, name in enumerate(names):
        s = svc.create_student(db, client, {
            "full_name": name, "gender": (genders[i] if i < len(genders) else "male") or "male",
            "age": parse_int(ages[i] if i < len(ages) else None),
            "course_id": parse_int(course_ids[i] if i < len(course_ids) else None) or lead.course_interest_id,
            "status": "trial", "timezone": client.timezone, "guardian_consent": True}, user, request=request)
        created.append(s)

    lead.stage = "won"
    lead.converted_client_id = client.id
    lead.converted_at = datetime.utcnow()
    for tr in db.query(Trial).filter(Trial.lead_id == lead.id):
        tr.client_id = client.id
        if created and not tr.student_id:
            tr.student_id = created[0].id
        if tr.status == "requested":
            tr.status = "scheduled" if tr.scheduled_at else "requested"
    for ref in db.query(Referral).filter(Referral.referred_lead_id == lead.id):
        ref.referred_client_id = client.id
        if ref.status in ("invited", "ask", "lead"):
            ref.status = "signed_up"
    log_action(db, user, "approve", "registration", entity=lead, request=request, consequential=True,
               description=f"Registration {lead.lead_code} converted to client {client.client_code} with {len(created)} student(s)",
               after={"client_id": client.id, "students": [s.student_code for s in created]})
    if client.user_id:
        notify(db, client.user_id, "Welcome to Online Quran College",
               f"Your family account {client.client_code} is ready. We will confirm the trial class times shortly.",
               event_type="enrolment", link="/portal")
    db.commit()
    target = f"/students/{created[0].id}" if created else f"/clients/{client.id}"
    msg = f"Converted to client {client.client_code} with {len(created)} student(s)."
    if pwd:
        msg += f" Portal password: {pwd}"
    return redirect(target, msg)


@router.post("/registrations/{id}/dismiss", include_in_schema=False)
async def dismiss(id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("registration.update"))):
    lead = db.query(Lead).get(id)
    if not lead:
        raise HTTPException(404)
    form = await request.form()
    reason = (form.get("reason") or "").strip()
    if not reason:
        return redirect(f"/registrations/{lead.id}", "A reason is required.", "error")
    lead.stage = "lost"
    lead.lost_reason = reason[:200]
    log_action(db, user, "update", "registration", entity=lead, rationale=reason, request=request,
               description=f"Registration {lead.lead_code} dismissed")
    db.commit()
    return redirect("/registrations", "Registration dismissed.", "warning")
