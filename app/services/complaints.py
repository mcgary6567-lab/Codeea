"""Complaint lifecycle: who may see a complaint, how it climbs the escalation ladder, and how it closes (docs/COMPLAINTS.md).

A complaint is a Case with ``case_type == "complaint"``. It runs

    Received -> Investigating -> Findings recorded -> (Escalated) -> Resolved, parent confirmation pending
             -> Closed, confirmed by parent            (the family says it is resolved)
             -> Reopened, and escalated                (the family says it is not)

The family's own words stay in ``Case.description`` and are never edited; what the investigation verified goes into
``investigation_finding`` with an outcome and a verified severity. A complaint never closes on the team's word alone:
resolving it opens a confirmation follow-up, and only the family's answer (on a call or in the portal) closes it.
A manager may close it without confirmation only after the family could not be reached the configured number of times.

Complaints that name a member of staff are confidential. The person complained about never sees the complaint, and
apart from the people handling it only the escalation ladder roles and case managers (``cases.assign``) do.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.core import rbac
from app.core.audit import log_action
from app.core.notify import notify
from app.models.core import Role, Setting, User
from app.models.crm import Case, CaseComment, ParentContact, PARENT_CONTACT_CHANNELS, PARENT_SATISFACTION
from app.models.hr_erp import Attachment
from app.models.ops import Task
from app.models.people import Employee, Teacher

# ----------------------------------------------------------------------------- vocabulary
STATUS_LABELS = {
    "open": "Received", "investigating": "Under investigation", "findings_recorded": "Findings recorded",
    "in_progress": "In progress", "waiting": "Waiting", "escalated": "Escalated",
    "pending_confirmation": "Resolved, parent confirmation pending", "reopened": "Reopened",
    "resolved": "Resolved", "closed": "Closed",
}
CLOSURE_LABELS = {"confirmed_by_parent": "Closed, confirmed by parent", "parent_unreachable": "Closed, parent unreachable",
                  "without_confirmation": "Closed without parent confirmation", "internal": "Closed"}
FINDING_OUTCOMES = [("substantiated", "Confirmed"), ("partly_substantiated", "Partly confirmed"),
                    ("not_substantiated", "Not confirmed"), ("inconclusive", "Inconclusive")]
SEVERITIES = [("minor", "Minor"), ("moderate", "Moderate"), ("serious", "Serious"), ("critical", "Critical")]
SATISFACTION_LABELS = {"satisfied": "Satisfied, issue resolved", "partly_satisfied": "Partly satisfied",
                       "not_satisfied": "Not satisfied", "unreachable": "Could not reach the family"}
CHANNEL_LABELS = {"phone": "Phone call", "whatsapp": "WhatsApp", "video": "Video call", "meeting": "Meeting",
                  "email": "Email", "portal": "Parent portal"}
ROOT_CAUSE_LOOKUP = "complaint_root_cause"
ROOT_CAUSE_FALLBACK = ["Teacher performance", "Teacher punctuality", "Scheduling", "Process gap", "Communication gap",
                       "Training need", "System or technical", "Policy", "Parent expectation", "Other"]

# ----------------------------------------------------------------------------- settings
LADDER_KEY = "case_escalation_ladder"
DEFAULT_LADDER = ["academy_manager", "head_of_admissions", "hod_people", "super_admin"]
CONFIRM_DAYS_KEY = "case_confirmation_days"
UNREACHABLE_KEY = "case_unreachable_attempts"
REPEAT_WINDOW_DAYS = 90
EVIDENCE_DIR = BASE_DIR / "storage" / "case_evidence"
EVIDENCE_MAX_BYTES = 25 * 1024 * 1024
EVIDENCE_TYPES = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp3", ".m4a", ".wav", ".ogg", ".oga", ".webm",
                  ".mp4", ".txt", ".doc", ".docx")


def _setting_value(db: Session, key: str, default):
    row = db.query(Setting).filter(Setting.key == key).first()
    value = row.value if row else None
    if isinstance(value, dict) and "value" in value:
        value = value["value"]
    return default if value in (None, "", []) else value


def _store_setting(db: Session, key: str, value, description: str) -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    if row is None:
        row = Setting(key=key, group="cases", is_editable=False, description=description)
        db.add(row)
    row.value = {"value": value}
    db.flush()


def ladder(db: Session) -> list[str]:
    """Role slugs a complaint climbs, lowest first. Each step is the next person a complaint is escalated to."""
    value = _setting_value(db, LADDER_KEY, DEFAULT_LADDER)
    return [s for s in value if isinstance(s, str) and s] if isinstance(value, list) else list(DEFAULT_LADDER)


def confirmation_days(db: Session) -> int:
    try:
        return max(1, int(_setting_value(db, CONFIRM_DAYS_KEY, 2)))
    except (TypeError, ValueError):
        return 2


def unreachable_attempts(db: Session) -> int:
    try:
        return max(1, int(_setting_value(db, UNREACHABLE_KEY, 3)))
    except (TypeError, ValueError):
        return 3


def save_settings(db: Session, ladder_slugs: list[str], days: int, attempts: int) -> None:
    known = {r.slug for r in db.query(Role).filter(Role.portal == "admin")}
    clean: list[str] = []
    for slug in ladder_slugs:
        if slug in known and slug not in clean:
            clean.append(slug)
    if not clean:
        raise ValueError("The escalation ladder needs at least one role.")
    _store_setting(db, LADDER_KEY, clean, "Roles a complaint is escalated through, lowest first.")
    _store_setting(db, CONFIRM_DAYS_KEY, max(1, int(days)), "Days allowed to confirm a resolved complaint with the family.")
    _store_setting(db, UNREACHABLE_KEY, max(1, int(attempts)),
                   "Unanswered confirmation attempts after which a manager may close a complaint without confirmation.")


def is_complaint(case: Case) -> bool:
    return case.case_type == "complaint"


def has_family(case: Case) -> bool:
    return bool(case.client_id or case.student_id)


def status_label(case: Case) -> str:
    if case.status == "closed" and case.closure_type in CLOSURE_LABELS:
        return CLOSURE_LABELS[case.closure_type]
    return STATUS_LABELS.get(case.status, (case.status or "").replace("_", " ").title())


# ----------------------------------------------------------------------------- visibility
def _my_employee_ids(db: Session, user: User) -> list[int]:
    return [i for (i,) in db.query(Employee.id).filter(Employee.user_id == user.id)]


def _my_teacher_ids(db: Session, user: User) -> list[int]:
    return [i for (i,) in db.query(Teacher.id).filter(Teacher.user_id == user.id)]


def subject_user_ids(db: Session, case: Case) -> set[int]:
    """Sign-ins of the people a complaint is about: they never see it, handle it or receive its escalation."""
    ids: set[int] = set()
    if case.against_employee_id:
        emp = db.get(Employee, case.against_employee_id)
        if emp and emp.user_id:
            ids.add(emp.user_id)
    if is_complaint(case) and case.teacher_id:
        t = db.get(Teacher, case.teacher_id)
        if t and t.user_id:
            ids.add(t.user_id)
    return ids


def is_case_manager(db: Session, user: User) -> bool:
    """Case managers see every complaint they are not the subject of: ladder roles and holders of cases.assign."""
    if user is None:
        return False
    if user.is_superuser or rbac.has_permission(user, "cases.assign"):
        return True
    return bool(user.role_slug and user.role_slug in ladder(db))


def visibility_clause(db: Session, user: User):
    """SQL condition limiting Case rows to those ``user`` may see, or None for no limit (superusers)."""
    if user.is_superuser:
        return None
    emp_ids = _my_employee_ids(db, user) or [-1]
    teacher_ids = _my_teacher_ids(db, user) or [-1]
    not_subject = and_(or_(Case.against_employee_id.is_(None), Case.against_employee_id.notin_(emp_ids)),
                       or_(Case.case_type != "complaint", Case.teacher_id.is_(None), Case.teacher_id.notin_(teacher_ids)))
    if is_case_manager(db, user):
        return not_subject
    scope = [Case.assigned_to_id == user.id, Case.escalated_to_id == user.id, Case.raised_by_user_id == user.id,
             Case.findings_by_id == user.id]
    open_to_team = Case.against_employee_id.is_(None)
    if user.department_id:
        scope.append(and_(open_to_team, Case.department_id == user.department_id))
    if user.role_slug == "supervisor":
        supervised = [i for (i,) in db.query(Teacher.id).filter(Teacher.supervisor_id == user.id)]
        if supervised:
            scope.append(and_(open_to_team, Case.teacher_id.in_(supervised)))
    return and_(not_subject, or_(*scope))


def visible(db: Session, user: User, query):
    clause = visibility_clause(db, user)
    return query if clause is None else query.filter(clause)


def can_view(db: Session, user: User, case: Case) -> bool:
    clause = visibility_clause(db, user)
    if clause is None:
        return True
    return db.query(Case.id).filter(Case.id == case.id, clause).first() is not None


def can_read_history(user: User) -> bool:
    """Complaint history on a staff profile is granted by name; reading everything (``*.view``) does not include it."""
    return rbac.has_explicit_permission(user, "complaint_history.view")


# ----------------------------------------------------------------------------- comments and tasks
def add_entry(db: Session, case: Case, user: Optional[User], text: str, kind: str = "comment", internal: bool = True) -> CaseComment:
    entry = CaseComment(case_id=case.id, user_id=user.id if user else None, text=text, is_internal=internal, kind=kind)
    db.add(entry)
    return entry


def _task(db: Session, case: Case, title: str, assignee_id: Optional[int], creator: Optional[User], due: date,
          description: Optional[str] = None) -> Task:
    t = Task(title=title[:200], description=description or case.title, assignee_id=assignee_id, creator_id=creator.id if creator else None,
             department_id=case.department_id, priority=case.priority if case.priority in ("low", "medium", "high", "urgent") else "medium",
             status="todo", due_date=due, entity_type="Case", entity_id=case.id)
    db.add(t)
    db.flush()
    return t


def _close_open_confirmation_tasks(db: Session, case: Case) -> None:
    for t in db.query(Task).filter(Task.entity_type == "Case", Task.entity_id == case.id, Task.status.in_(["todo", "in_progress", "review"]),
                                   Task.title.like("Confirm with the family%")):
        t.status, t.completed_at = "done", datetime.utcnow()


def _family_user(case: Case) -> Optional[User]:
    if case.client and case.client.user:
        return case.client.user
    if case.student and case.student.client and case.student.client.user:
        return case.student.client.user
    return None


def _family_whatsapp(case: Case) -> Optional[str]:
    contact = case.client or (case.student.client if case.student else None)
    return contact.whatsapp if contact else None


# ----------------------------------------------------------------------------- intake helpers (used by crm.open_case)
def employee_for_teacher(db: Session, teacher_id: Optional[int]) -> Optional[int]:
    if not teacher_id:
        return None
    t = db.get(Teacher, teacher_id)
    return t.employee_id if t else None


def find_repeat(db: Session, case: Case) -> Optional[Case]:
    """An earlier complaint from the same family about the same person, or in the same category, within 90 days."""
    if not is_complaint(case) or not case.client_id:
        return None
    since = (case.created_at or datetime.utcnow()) - timedelta(days=REPEAT_WINDOW_DAYS)
    same_subject = []
    if case.against_employee_id:
        same_subject.append(Case.against_employee_id == case.against_employee_id)
    if case.teacher_id:
        same_subject.append(Case.teacher_id == case.teacher_id)
    if case.complaint_type:
        same_subject.append(Case.complaint_type == case.complaint_type)
    if case.category and case.category != "general":
        same_subject.append(Case.category == case.category)
    if not same_subject:
        return None
    return (db.query(Case).filter(Case.id != case.id, Case.case_type == "complaint", Case.client_id == case.client_id,
                                  Case.created_at >= since, or_(*same_subject))
            .order_by(Case.created_at.desc()).first())


def collaborators(db: Session, case: Case) -> list[User]:
    """The first two ladder roles (Academy Manager and Head of Admissions by default) work every complaint together."""
    subjects = subject_user_ids(db, case)
    out: list[User] = []
    for slug in ladder(db)[:2]:
        for u in _users_with_role(db, slug):
            if u.id not in subjects and u not in out:
                out.append(u)
    return out


# ----------------------------------------------------------------------------- escalation ladder
def _users_with_role(db: Session, slug: str) -> list[User]:
    q = db.query(User).join(Role, Role.id == User.role_id).filter(User.is_active.is_(True), Role.slug == slug)
    users = q.order_by(User.id).all()
    if slug == "super_admin" and not users:
        users = db.query(User).filter(User.is_active.is_(True), User.is_superuser.is_(True)).order_by(User.id).all()
    return users


def ladder_step_name(db: Session, level: int) -> str:
    steps = ladder(db)
    if level <= 0 or level > len(steps):
        return "Handling team"
    role = db.query(Role).filter(Role.slug == steps[level - 1]).first()
    return role.name if role else steps[level - 1].replace("_", " ").title()


def next_on_ladder(db: Session, case: Case) -> tuple[int, Optional[User]]:
    """The next ladder step above the one the complaint has reached, skipping steps nobody (other than the subject)
    holds. Returns (level, user); the user is None once the top of the ladder has been reached."""
    subjects = subject_user_ids(db, case)
    steps = ladder(db)
    for idx in range(max(0, case.escalation_level or 0), len(steps)):
        people = [u for u in _users_with_role(db, steps[idx]) if u.id not in subjects]
        if people:
            load = dict(db.query(Case.escalated_to_id, func.count(Case.id))
                        .filter(Case.escalated_to_id.in_([u.id for u in people]), Case.status.notin_(["closed", "resolved"]))
                        .group_by(Case.escalated_to_id).all())
            return idx + 1, min(people, key=lambda u: (load.get(u.id, 0), u.id))
    return case.escalation_level or 0, None


def level_of(db: Session, user: Optional[User]) -> int:
    if user is None:
        return 0
    steps = ladder(db)
    slug = "super_admin" if user.is_superuser and "super_admin" in steps else user.role_slug
    return steps.index(slug) + 1 if slug in steps else 0


# ----------------------------------------------------------------------------- findings
def record_findings(db: Session, case: Case, user: User, finding: str, outcome: str, severity: str,
                    against_employee_id: Optional[int] = None, request=None) -> None:
    finding = (finding or "").strip()
    if not finding:
        raise ValueError("Write down what the investigation found.")
    if outcome not in dict(FINDING_OUTCOMES):
        raise ValueError("Choose whether the investigation confirmed the complaint.")
    if severity not in dict(SEVERITIES):
        raise ValueError("Choose the verified severity.")
    if case.status in ("closed", "pending_confirmation"):
        raise ValueError("This complaint is already resolved; reopen it to change the findings.")
    if against_employee_id:
        emp = db.get(Employee, against_employee_id)
        if emp is not None and emp.user_id == user.id:
            raise ValueError("You cannot record findings on a complaint about yourself.")
    before = {"finding": case.investigation_finding, "outcome": case.finding_outcome, "severity": case.severity}
    if against_employee_id:
        case.against_employee_id = against_employee_id
    case.investigation_finding, case.finding_outcome, case.severity = finding, outcome, severity
    case.findings_by_id, case.findings_at = user.id, datetime.utcnow()
    previous = case.status
    case.status = "findings_recorded"
    add_entry(db, case, user, f"Investigation finding ({dict(FINDING_OUTCOMES)[outcome]}, {dict(SEVERITIES)[severity]}): {finding}", "finding")
    log_action(db, user, "update", "cases", entity=case, description=f"{case.case_number}: investigation finding recorded",
               before=before, after={"finding": finding, "outcome": outcome, "severity": severity, "status_from": previous},
               request=request, consequential=True)


# ----------------------------------------------------------------------------- resolution and confirmation
def resolve(db: Session, case: Case, user: User, resolution: str, root_cause_category: str, root_cause: Optional[str] = None,
            corrective_action: Optional[str] = None, preventive_action: Optional[str] = None, request=None) -> str:
    """Record the resolution. A complaint with a family moves to "parent confirmation pending" and a confirmation
    follow-up is created for the person responsible; anything else is simply resolved. Returns the new status."""
    resolution = (resolution or "").strip()
    root_cause_category = (root_cause_category or "").strip()
    if not resolution or not root_cause_category:
        raise ValueError("The resolution and the root cause are required.")
    if is_complaint(case) and not case.investigation_finding:
        raise ValueError("Record the investigation finding before resolving the complaint.")
    if case.status in ("closed", "pending_confirmation"):
        raise ValueError("This case is already resolved.")
    before = case.status
    case.resolution = resolution
    case.root_cause_category = root_cause_category[:60]
    case.root_cause = (root_cause or "").strip()[:200] or root_cause_category[:200]
    case.corrective_action = (corrective_action or "").strip() or case.corrective_action
    case.preventive_action = (preventive_action or "").strip() or case.preventive_action
    case.resolved_at = datetime.utcnow()
    for fb in case_feedback(db, case):
        fb.status = "resolved"
    if is_complaint(case) and has_family(case):
        case.status = "pending_confirmation"
        case.confirmation_due_at = datetime.utcnow() + timedelta(days=confirmation_days(db))
        responsible = case.assigned_to_id or user.id
        _task(db, case, f"Confirm with the family that {case.case_number} is resolved", responsible, user,
              case.confirmation_due_at.date(), description=f"Call the family, record their answer on the case. Resolution: {resolution}")
        add_entry(db, case, user, f"Resolved, waiting for the family to confirm. Resolution: {resolution}", "status")
        cu = _family_user(case)
        if cu:
            notify(db, cu, f"Is your complaint {case.case_number} resolved?",
                   f"{resolution[:220]} We will call you to confirm, or you can confirm in the portal.",
                   event_type="case_status", link=f"/portal/cases/{case.id}", channels=("in_app", "whatsapp"),
                   recipient_address=_family_whatsapp(case))
    else:
        case.status = "resolved"
        add_entry(db, case, user, f"Resolved: {resolution}", "status")
    log_action(db, user, "status_change", "cases", entity=case, description=f"{case.case_number} {before} -> {case.status}",
               rationale=case.root_cause, before={"status": before}, after={"status": case.status, "root_cause_category": case.root_cause_category},
               request=request, consequential=True)
    _emit(db, case, "case.status_changed")
    return case.status


def case_feedback(db: Session, case: Case):
    from app.models.crm import Feedback
    return db.query(Feedback).filter(Feedback.case_id == case.id).all()


def record_contact(db: Session, case: Case, user: Optional[User], data: dict, request=None) -> ParentContact:
    """Record a conversation with the family about a resolved complaint and act on their answer.

    satisfied        -> closed, confirmed by parent
    partly_satisfied -> reopened for the handling team
    not_satisfied    -> reopened and escalated one step up the ladder
    unreachable      -> stays pending; another confirmation attempt is scheduled for tomorrow
    """
    satisfaction = data.get("satisfaction") or ""
    channel = data.get("channel") or ("portal" if user is None else "phone")
    response = (data.get("parent_response") or "").strip()
    if satisfaction not in PARENT_SATISFACTION:
        raise ValueError("Choose how the family answered.")
    if channel not in PARENT_CONTACT_CHANNELS:
        raise ValueError("Choose how the family was contacted.")
    if satisfaction != "unreachable" and not response:
        raise ValueError("Write down what the family said.")
    if case.status != "pending_confirmation":
        raise ValueError("This complaint is not waiting for the family's confirmation.")
    when = data.get("contacted_at") or datetime.utcnow()
    pc = ParentContact(case_id=case.id, client_id=case.client_id or (case.student.client_id if case.student else None),
                       student_id=case.student_id, purpose="complaint_confirmation", channel=channel, contacted_at=when,
                       contacted_by_id=user.id if user else None, parent_response=response or None, satisfaction=satisfaction,
                       feedback=(data.get("feedback") or "").strip() or None, agreed_action=(data.get("agreed_action") or "").strip() or None,
                       follow_up_date=data.get("follow_up_date"), responsible_id=data.get("responsible_id") or case.assigned_to_id,
                       recording_url=(data.get("recording_url") or "").strip()[:500] or None,
                       recording_attachment_id=data.get("recording_attachment_id"),
                       transcript=(data.get("transcript") or "").strip() or None,
                       referral_mentioned=bool(data.get("referral_mentioned")))
    db.add(pc)
    db.flush()
    said = f' Family said: "{response}"' if response else ""
    add_entry(db, case, user, f"{CHANNEL_LABELS.get(channel, channel)} with the family: {SATISFACTION_LABELS[satisfaction]}.{said}", "contact")
    now = datetime.utcnow()
    if satisfaction == "satisfied":
        case.status, case.closed_at, case.closure_type, case.confirmed_by_parent = "closed", now, "confirmed_by_parent", True
        pc.outcome = "closed"
        _close_open_confirmation_tasks(db, case)
        add_entry(db, case, user, "Closed, confirmed by the family.", "status")
    elif satisfaction == "unreachable":
        case.confirmation_attempts = (case.confirmation_attempts or 0) + 1
        pc.outcome = "awaiting"
        _close_open_confirmation_tasks(db, case)
        _task(db, case, f"Confirm with the family that {case.case_number} is resolved (attempt {case.confirmation_attempts + 1})",
              case.assigned_to_id or (user.id if user else None), user, (now + timedelta(days=1)).date())
    else:
        _reopen(db, case, user, f"Family {SATISFACTION_LABELS[satisfaction].lower()}: {response}")
        pc.outcome = "reopened"
        if satisfaction == "not_satisfied":
            if escalate(db, case, user, "The family is not satisfied with the resolution.", request=request):
                pc.outcome = "escalated"
    if pc.agreed_action and pc.follow_up_date:
        task = _task(db, case, f"Agreed with the family: {pc.agreed_action[:150]}", pc.responsible_id or case.assigned_to_id,
                     user, pc.follow_up_date)
        pc.follow_up_task_id = task.id
    log_action(db, user, "update", "cases", entity=case, request=request, consequential=True,
               description=f"{case.case_number}: family contacted ({channel}), {satisfaction}; case now {case.status}",
               after={"satisfaction": satisfaction, "status": case.status, "contact_id": pc.id})
    _emit(db, case, "case.status_changed")
    return pc


def _reopen(db: Session, case: Case, user: Optional[User], reason: str) -> None:
    case.status = "reopened"
    case.reopen_count = (case.reopen_count or 0) + 1
    case.reopened_at = datetime.utcnow()
    case.closed_at, case.closure_type, case.confirmed_by_parent = None, None, False
    case.confirmation_due_at = None
    case.sla_due_at = datetime.utcnow() + timedelta(hours=case.sla_hours or 48)
    case.sla_breached = False
    case.escalated = False  # the ladder level is kept, so a missed deadline now climbs one step further
    _close_open_confirmation_tasks(db, case)
    add_entry(db, case, user, f"Reopened: {reason}", "status")
    if case.assigned_to_id:
        notify(db, case.assigned_to_id, f"Complaint reopened: {case.case_number}", reason[:300], event_type="case_reopened",
               link=f"/cases/{case.id}")


def reopen(db: Session, case: Case, user: User, reason: str, request=None) -> None:
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Give the reason for reopening.")
    if case.status not in ("pending_confirmation", "resolved", "closed"):
        raise ValueError("Only a resolved or closed case can be reopened.")
    before = case.status
    _reopen(db, case, user, reason)
    log_action(db, user, "status_change", "cases", entity=case, description=f"{case.case_number} reopened", rationale=reason,
               before={"status": before}, after={"status": "reopened"}, request=request, consequential=True)
    _emit(db, case, "case.status_changed")


def close_without_confirmation(db: Session, case: Case, user: User, reason: str, request=None) -> None:
    """A manager closes a complaint the family never answered about, once enough attempts have been made."""
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Give the reason for closing without the family's confirmation.")
    if case.status != "pending_confirmation":
        raise ValueError("Only a complaint waiting for confirmation can be closed this way.")
    if not (user.is_superuser or rbac.is_management(user) or is_case_manager(db, user)):
        raise ValueError("Only a manager can close a complaint without the family's confirmation.")
    needed = unreachable_attempts(db)
    if (case.confirmation_attempts or 0) < needed:
        raise ValueError(f"Try to reach the family {needed} time(s) first; {case.confirmation_attempts or 0} recorded so far.")
    case.status, case.closed_at, case.closure_type = "closed", datetime.utcnow(), "parent_unreachable"
    _close_open_confirmation_tasks(db, case)
    add_entry(db, case, user, f"Closed without the family's confirmation after {case.confirmation_attempts} attempt(s): {reason}", "status")
    log_action(db, user, "status_change", "cases", entity=case, description=f"{case.case_number} closed, family unreachable",
               rationale=reason, before={"status": "pending_confirmation"}, after={"status": "closed"}, request=request,
               consequential=True, severity="warning")
    _emit(db, case, "case.status_changed")


# ----------------------------------------------------------------------------- escalation
def escalate(db: Session, case: Case, user: Optional[User], reason: str, target: Optional[User] = None, request=None) -> Optional[User]:
    """Escalate along the ladder (or to a named person who is not the subject). Returns who it went to."""
    from app.services import crm as crm_svc
    subjects = subject_user_ids(db, case)
    if target is not None and target.id in subjects:
        raise ValueError("A complaint cannot be escalated to the person it is about.")
    if target is None and is_complaint(case):
        level, target = next_on_ladder(db, case)
        if target is not None:
            case.escalation_level = level
    elif target is not None:
        case.escalation_level = max(case.escalation_level or 0, level_of(db, target))
    crm_svc.escalate_case(db, case, user, reason, target, request=request)
    return target


# ----------------------------------------------------------------------------- evidence
async def save_evidence(db: Session, case: Case, user: User, upload, title: str) -> Attachment:
    """Store an uploaded file against the case. Evidence lives outside the shared upload folders and is only
    served by the case's own download route, which checks that the reader may see the case."""
    name = getattr(upload, "filename", "") or ""
    suffix = Path(name).suffix.lower()
    if suffix not in EVIDENCE_TYPES:
        raise ValueError(f"That file type is not accepted. Use one of: {', '.join(t.lstrip('.') for t in EVIDENCE_TYPES)}.")
    data = await upload.read()
    if not data:
        raise ValueError("The file is empty.")
    if len(data) > EVIDENCE_MAX_BYTES:
        raise ValueError("Files must be 25 MB or smaller.")
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch for ch in Path(name).stem if ch.isalnum() or ch in "-_")[:60] or "evidence"
    stored = f"{case.id}-{datetime.utcnow():%Y%m%d%H%M%S%f}-{safe}{suffix}"
    (EVIDENCE_DIR / stored).write_bytes(data)
    att = Attachment(entity_type="case", entity_id=case.id, title=(title or name)[:200], file_name=name[:200],
                     file_path=f"case_evidence/{stored}", content_type=(getattr(upload, "content_type", None) or "application/octet-stream")[:80],
                     size_bytes=len(data), uploaded_by_id=user.id, status="active")
    db.add(att)
    db.flush()
    add_entry(db, case, user, f"Evidence attached: {att.title}", "system")
    log_action(db, user, "create", "cases", entity=case, description=f"{case.case_number}: evidence '{att.title}' attached")
    return att


def evidence(db: Session, case: Case) -> list[Attachment]:
    return (db.query(Attachment).filter(Attachment.entity_type == "case", Attachment.entity_id == case.id, Attachment.status == "active")
            .order_by(Attachment.created_at).all())


def evidence_path(att: Attachment) -> Optional[Path]:
    if not att.file_path or not att.file_path.startswith("case_evidence/"):
        return None
    path = (EVIDENCE_DIR / att.file_path.split("/", 1)[1]).resolve()
    return path if path.parent == EVIDENCE_DIR.resolve() and path.exists() else None


# ----------------------------------------------------------------------------- history and intelligence
from app.services.crm import OPEN_CASE_STATUSES as OPEN_STATES  # noqa: E402  (crm imports this module lazily)


def _complaints(db: Session, viewer: User):
    return visible(db, viewer, db.query(Case).filter(Case.case_type == "complaint"))


def _summarise(rows: list[Case]) -> dict:
    hours = [(c.resolved_at - c.created_at).total_seconds() / 3600 for c in rows if c.resolved_at and c.created_at]
    by_cat: dict[str, int] = {}
    by_sev: dict[str, int] = {}
    for c in rows:
        key = c.complaint_type or (c.category or "general").replace("_", " ").title()
        by_cat[key] = by_cat.get(key, 0) + 1
        if c.severity:
            by_sev[c.severity] = by_sev.get(c.severity, 0) + 1
    return {
        "total": len(rows),
        "open": sum(1 for c in rows if c.status in OPEN_STATES),
        "pending_confirmation": sum(1 for c in rows if c.status == "pending_confirmation"),
        "resolved": sum(1 for c in rows if c.status in ("resolved", "pending_confirmation")),
        "closed": sum(1 for c in rows if c.status == "closed"),
        "confirmed": sum(1 for c in rows if c.closure_type == "confirmed_by_parent"),
        "reopened": sum(1 for c in rows if (c.reopen_count or 0) > 0),
        "repeat": sum(1 for c in rows if c.repeat_of_id),
        "substantiated": sum(1 for c in rows if c.finding_outcome in ("substantiated", "partly_substantiated")),
        "avg_resolution_hours": round(sum(hours) / len(hours), 1) if hours else None,
        "by_category": sorted(by_cat.items(), key=lambda x: -x[1]),
        "by_severity": [(k, by_sev.get(k, 0)) for k, _ in SEVERITIES if by_sev.get(k)],
    }


def employee_history(db: Session, viewer: User, employee: Optional[Employee] = None, teacher: Optional[Teacher] = None,
                     request=None) -> dict:
    """Complaints naming this employee (or made about them as a teacher), as far as ``viewer`` may see them.
    Every read is written to the audit log, because the list is confidential."""
    teacher_ids = [teacher.id] if teacher is not None else []
    if employee is not None:
        teacher_ids += [i for (i,) in db.query(Teacher.id).filter(Teacher.employee_id == employee.id)]
        if teacher is None and teacher_ids:
            teacher = db.get(Teacher, teacher_ids[0])
    elif teacher is not None and teacher.employee_id:
        employee = db.get(Employee, teacher.employee_id)
    conds = [Case.teacher_id.in_(teacher_ids or [-1])]
    if employee is not None:
        conds.append(Case.against_employee_id == employee.id)
    rows = _complaints(db, viewer).filter(or_(*conds)).order_by(Case.created_at.desc()).all()
    out = _summarise(rows)
    labels = _month_labels(6)
    per_month = {k: 0 for k in labels}
    for c in rows:
        k = c.created_at.strftime("%Y-%m") if c.created_at else None
        if k in per_month:
            per_month[k] += 1
    subject = employee or teacher
    is_self = bool(subject is not None and getattr(subject, "user_id", None) == viewer.id)
    out.update({"rows": rows, "labels": labels, "per_month": [per_month[k] for k in labels], "is_self": is_self,
                "employee_id": employee.id if employee else None})
    if subject is not None:
        log_action(db, viewer, "view", "complaint_history", entity=subject, request=request,
                   description=f"Viewed complaint history of {subject.full_name} ({len(rows)} complaint(s))")
    return out


def _month_labels(months: int) -> list[str]:
    today = date.today().replace(day=1)
    labels = []
    for i in range(months - 1, -1, -1):
        y, m = today.year, today.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        labels.append(f"{y:04d}-{m:02d}")
    return labels


def escalation_risk(db: Session, case: Case, now: Optional[datetime] = None) -> tuple[int, list[str]]:
    """A 0-100 score and the reasons, for open complaints most likely to need management attention. Rule based,
    so every point can be explained; nothing is decided on it automatically."""
    now = now or datetime.utcnow()
    score, why = 0, []
    if case.priority in ("high", "urgent"):
        score += 20 if case.priority == "high" else 30
        why.append(f"{case.priority} priority")
    if case.severity in ("serious", "critical"):
        score += 20
        why.append(f"verified {case.severity}")
    if case.reopen_count:
        score += 15 * min(2, case.reopen_count)
        why.append(f"reopened {case.reopen_count}x")
    if case.repeat_of_id:
        score += 15
        why.append("repeat complaint from this family")
    if case.sla_breached:
        score += 20
        why.append("response deadline missed")
    elif case.sla_due_at and case.sla_due_at - now <= timedelta(hours=6):
        score += 10
        why.append("deadline within 6 hours")
    if case.against_employee_id:
        prior = db.query(func.count(Case.id)).filter(Case.case_type == "complaint", Case.against_employee_id == case.against_employee_id,
                                                     Case.id != case.id, Case.created_at >= now - timedelta(days=180)).scalar() or 0
        if prior >= 2:
            score += 15
            why.append(f"{prior} other complaints about the same person in 6 months")
    return min(100, score), why


def intelligence(db: Session, viewer: User, months: int = 6) -> dict:
    """What management needs from complaints: volume and direction, repeats, reopenings, recurring people and
    departments, root causes and the open cases most at risk."""
    rows = _complaints(db, viewer).all()
    out = _summarise(rows)
    labels = _month_labels(months)
    opened = {k: 0 for k in labels}
    closed = {k: 0 for k in labels}
    cat_month: dict[str, dict[str, int]] = {}
    for c in rows:
        k = c.created_at.strftime("%Y-%m") if c.created_at else None
        if k in opened:
            opened[k] += 1
            cat = c.complaint_type or (c.category or "general").replace("_", " ").title()
            cat_month.setdefault(cat, {}).setdefault(k, 0)
            cat_month[cat][k] += 1
        if c.closed_at and c.closed_at.strftime("%Y-%m") in closed:
            closed[c.closed_at.strftime("%Y-%m")] += 1
    rising = []
    if len(labels) >= 2:
        last, prev = labels[-1], labels[-2]
        for cat, counts in cat_month.items():
            a, b = counts.get(prev, 0), counts.get(last, 0)
            if b > a and b >= 2:
                rising.append((cat, a, b))
    rising.sort(key=lambda x: -(x[2] - x[1]))
    by_employee: dict[int, dict] = {}
    for c in rows:
        if c.against_employee_id:
            d = by_employee.setdefault(c.against_employee_id, {"employee": c.against_employee, "total": 0, "open": 0, "substantiated": 0})
            d["total"] += 1
            d["open"] += 1 if c.status in OPEN_STATES else 0
            d["substantiated"] += 1 if c.finding_outcome in ("substantiated", "partly_substantiated") else 0
    recurring_people = sorted((d for d in by_employee.values() if d["total"] >= 2), key=lambda d: -d["total"])
    by_dept: dict[str, int] = {}
    for c in rows:
        name = (c.against_department.name if c.against_department else None) or (c.department.name if c.department else "Unassigned")
        by_dept[name] = by_dept.get(name, 0) + 1
    root: dict[str, int] = {}
    for c in rows:
        if c.root_cause_category:
            root[c.root_cause_category] = root.get(c.root_cause_category, 0) + 1
    now = datetime.utcnow()
    at_risk = []
    for c in rows:
        if c.status in OPEN_STATES:
            score, why = escalation_risk(db, c, now)
            if score >= 30:
                at_risk.append({"case": c, "score": score, "why": why})
    at_risk.sort(key=lambda x: -x["score"])
    overdue_confirmation = [c for c in rows if c.status == "pending_confirmation" and c.confirmation_due_at and c.confirmation_due_at < now]
    out.update({
        "labels": labels, "opened": [opened[k] for k in labels], "closed_by_month": [closed[k] for k in labels],
        "rising": rising[:6], "recurring_people": recurring_people[:10],
        "by_department": sorted(by_dept.items(), key=lambda x: -x[1]), "root_causes": sorted(root.items(), key=lambda x: -x[1]),
        "at_risk": at_risk[:10], "overdue_confirmation": overdue_confirmation,
        "unresolved": [c for c in rows if c.status in OPEN_STATES],
    })
    return out


def management_summary(db: Session, viewer: User, data: Optional[dict] = None) -> dict:
    """A short written summary for management. The AI gateway writes it when a provider is configured; without one a
    deterministic summary is produced from the same figures. Staff decide what to do with it."""
    from app.services.ai_gateway import ai
    data = data or intelligence(db, viewer)
    facts = {
        "total": data["total"], "open": data["open"], "pending_confirmation": data["pending_confirmation"],
        "reopened": data["reopened"], "repeat": data["repeat"], "avg_resolution_hours": data["avg_resolution_hours"],
        "rising": [{"category": c, "previous": a, "latest": b} for c, a, b in data["rising"]],
        "recurring_people": [{"name": d["employee"].full_name if d["employee"] else "?", "total": d["total"], "open": d["open"]}
                             for d in data["recurring_people"][:5]],
        "top_root_causes": data["root_causes"][:3], "high_risk_open": len(data["at_risk"]),
        "overdue_confirmation": len(data["overdue_confirmation"]),
    }
    # Reuse today's summary when the figures have not changed, so opening the page does not log a new AI run each time.
    import json
    from app.models.core import AIModelRun
    fingerprint = json.dumps(facts, default=str)[:2000]
    since = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    run = (db.query(AIModelRun).filter(AIModelRun.module == "complaint_summary", AIModelRun.created_at >= since,
                                       AIModelRun.input_summary == fingerprint)
           .order_by(AIModelRun.id.desc()).first())
    if run is not None and isinstance(run.output, dict):
        result = run.output
    else:
        result, run = ai(db, "complaint_summary", "summarise_complaints", facts)
    return {"text": result.get("summary") or "", "points": result.get("points") or [], "run": run}


def _emit(db: Session, case: Case, event: str) -> None:
    from app.services.integrations import emit_event
    emit_event(db, event, {"case_id": case.id, "case_number": case.case_number, "status": case.status})


def open_count_filter():
    return Case.status.in_(list(OPEN_STATES) + ["pending_confirmation"])


def staff_options(db: Session, case: Optional[Case] = None) -> list[tuple[int, str]]:
    """Staff who can be given a case: never the person it is about."""
    excluded = subject_user_ids(db, case) if case is not None else set()
    staff = (db.query(User).join(Role, Role.id == User.role_id)
             .filter(User.is_active.is_(True), Role.portal == "admin").order_by(User.full_name).all())
    return [(u.id, u.full_name) for u in staff if u.id not in excluded]


def users_by_id(db: Session, ids: Iterable[int]) -> dict[int, User]:
    ids = [i for i in ids if i]
    return {u.id: u for u in db.query(User).filter(User.id.in_(ids or [-1]))}
