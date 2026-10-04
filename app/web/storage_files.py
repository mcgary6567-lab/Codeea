"""Guarded delivery of everything under ``<repo>/storage``.

The storage folder holds invoices, receipts, payslips, result cards, uploaded attachments and backups: personal and
financial documents. It used to be a public StaticFiles mount, so anyone who knew (or guessed) a URL could read a
family's invoice or a colleague's payslip. This router replaces the mount and keeps the URL shape
(``/storage/<folder>/<file>``) so every link already stored in the database and rendered by the templates keeps
working - but each top-level folder now has an access rule, applied before the file is read.

Stored paths are not uniform (``/storage/invoices/X.pdf``, ``storage/certificates/X.pdf``, ``payslips/X.pdf``) and
templates prefix them with ``/storage/`` again, so the requested path is normalised first: empty segments and any
leading ``storage`` segments are dropped, and ``..`` anywhere is a 404 before the filesystem is touched.

Denied attempts (anonymous or signed in) are written to the audit log as ``storage / denied``; successful downloads
are not audited, exactly as the old mount never did.
"""
from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.core import rbac
from app.core.audit import log_action
from app.core.deps import NotAuthenticated, PermissionDenied, get_optional_user
from app.database import get_db
from app.models.academic import MonthlyTest
from app.models.core import User
from app.models.finance import Invoice, Payment, Receipt
from app.models.hr_erp import Attachment
from app.models.people import Client, Employee, Payslip, Student

router = APIRouter()

STORAGE_ROOT = (BASE_DIR / "storage").resolve()
MODULE = "storage"

# Content types the browser should open in place rather than download.
INLINE_TYPES = ("application/pdf", "image/")


@dataclass(frozen=True)
class Rule:
    """One top-level folder's access rule.

    ``public``       no login needed at all (the /verify page links certificates for anyone to check).
    ``perms``        any one of these permissions lets a signed-in user fetch anything in the folder.
    ``staff``        any signed-in user whose role portal is "admin" may fetch anything in the folder.
    ``owner``        a callback that answers whether this signed-in user owns the requested file
                     (families, students and employees reach their own documents this way).
    """
    public: bool = False
    perms: tuple[str, ...] = ()
    staff: bool = False
    owner: Optional[Callable[[Session, User, str], bool]] = None


# ---------------------------------------------------------------------------- ownership checks
# Each receives the normalised relative path ("invoices/INV-2026-00001.pdf") and matches it against the path
# column the generating service stored, whichever spelling that service used.

def _variants(rel: str) -> list[str]:
    return [rel, f"storage/{rel}", f"/storage/{rel}"]


def _my_client(db: Session, user: User) -> Optional[Client]:
    return db.query(Client).filter(Client.user_id == user.id).first()


def _my_student(db: Session, user: User) -> Optional[Student]:
    return db.query(Student).filter(Student.user_id == user.id).first()


def _my_employee(db: Session, user: User) -> Optional[Employee]:
    return db.query(Employee).filter(Employee.user_id == user.id).first()


def _owns_invoice(db: Session, user: User, rel: str) -> bool:
    client = _my_client(db, user)
    if client is None:
        return False
    return db.query(Invoice.id).filter(Invoice.pdf_path.in_(_variants(rel)),
                                       Invoice.client_id == client.id).first() is not None


def _owns_receipt(db: Session, user: User, rel: str) -> bool:
    client = _my_client(db, user)
    if client is None:
        return False
    return (db.query(Receipt.id).join(Payment, Payment.id == Receipt.payment_id)
            .filter(Receipt.pdf_path.in_(_variants(rel)), Payment.client_id == client.id).first()) is not None


def _owns_payslip(db: Session, user: User, rel: str) -> bool:
    emp = _my_employee(db, user)
    if emp is None:
        return False
    return db.query(Payslip.id).filter(Payslip.pdf_path.in_(_variants(rel)),
                                       Payslip.employee_id == emp.id).first() is not None


def _owns_result_card(db: Session, user: User, rel: str) -> bool:
    test = db.query(MonthlyTest).filter(MonthlyTest.result_card_path.in_(_variants(rel))).first()
    if test is None:
        return False
    student = _my_student(db, user)
    if student is not None and test.student_id == student.id:
        return True
    client = _my_client(db, user)
    return client is not None and db.query(Student.id).filter(Student.id == test.student_id,
                                                              Student.client_id == client.id).first() is not None


def _owns_attachment(db: Session, user: User, rel: str) -> bool:
    """A non-staff user may fetch an upload only when an Attachment row says it belongs to them."""
    att = db.query(Attachment).filter(Attachment.file_path.in_(_variants(rel)), Attachment.status == "active").first()
    if att is None or att.entity_id is None:
        return False
    kind = (att.entity_type or "").lower()
    if kind == "client":
        client = _my_client(db, user)
        return client is not None and att.entity_id == client.id
    if kind == "student":
        student = _my_student(db, user)
        if student is not None and att.entity_id == student.id:
            return True
        client = _my_client(db, user)
        return client is not None and db.query(Student.id).filter(Student.id == att.entity_id,
                                                                  Student.client_id == client.id).first() is not None
    if kind == "employee":
        emp = _my_employee(db, user)
        return emp is not None and att.entity_id == emp.id
    return False


# ---------------------------------------------------------------------------- the policy table
# One line per top-level folder under storage/. Anything not listed falls to DEFAULT_RULE.
STAFF_UPLOAD_RULE = Rule(staff=True, owner=_owns_attachment)
FOLDER_RULES: dict[str, Optional[Rule]] = {
    "invoices": Rule(perms=("billing.view", "payments.view", "ledger.view"), owner=_owns_invoice),      # billing desk, or the family billed
    "receipts": Rule(perms=("billing.view", "payments.view", "ledger.view"), owner=_owns_receipt),      # billing desk, or the family that paid
    "payslips": Rule(perms=("payroll.view",), owner=_owns_payslip),                                      # payroll, or the employee paid
    "result_cards": Rule(perms=("monthly_tests.view", "evaluations.view", "academics.view"),
                         owner=_owns_result_card),                                                      # academics staff, the family or the student
    "certificates": Rule(public=True),                                                                  # public: /verify links them for anyone to check
    "attachments": STAFF_UPLOAD_RULE,                                                                   # staff; others only when the Attachment row is theirs
    "uploads": STAFF_UPLOAD_RULE,                                                                       # staff (expense receipts, photos, branding, migration files)
    "downloads": STAFF_UPLOAD_RULE,                                                                     # staff (HR documents published to staff)
    "exports": STAFF_UPLOAD_RULE,                                                                       # staff (subject-access and data exports)
    "reports": STAFF_UPLOAD_RULE,                                                                       # staff (generated report files)
    "migration": STAFF_UPLOAD_RULE,                                                                     # staff (legacy import files)
    "backups": Rule(perms=("backups.view",)),                                                           # database archives: backups.view only
    "agent_screenshots": None,                                                                          # never here: /config/agents/screenshots/{id} is the only route
}
DEFAULT_RULE = Rule(perms=("settings.view",))                                                           # any other folder: staff with settings.view; everyone else 404


# ---------------------------------------------------------------------------- helpers
def normalise(path: str) -> Optional[list[str]]:
    """Split the requested path into clean segments, or None when it must not be served.

    Drops empty and "." segments and any leading "storage" segments (templates prefix stored paths that already
    carry it). Refuses "..", drive letters, backslashes and NUL outright - the resolved-path check in the route is
    the final guard, this is the readable one.
    """
    raw = path.replace("\\", "/")
    if "\x00" in raw or ":" in raw:
        return None
    parts = [p for p in raw.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        return None
    while parts and parts[0] == "storage":
        parts = parts[1:]
    return parts or None


def _is_staff(user: User) -> bool:
    return user.portal == "admin"


def _deny(db: Session, request: Request, user: Optional[User], rel: str, reason: str) -> None:
    log_action(db, user, "denied", MODULE, entity_type="StorageFile",
               description=f"{'anonymous' if user is None else user.email} refused /storage/{rel}: {reason}",
               request=request, severity="warning", consequential=False, commit=True)


def _authorise(db: Session, request: Request, user: Optional[User], top: str, rel: str) -> None:
    """Raise unless ``user`` may read ``rel``. Unknown folders are 404 to anyone without settings.view."""
    if top not in FOLDER_RULES:
        rule = DEFAULT_RULE
        if user is None or not rbac.has_any(user, rule.perms):
            if user is not None:
                _deny(db, request, user, rel, "unlisted folder")
            raise HTTPException(status_code=404, detail="Not found")
        return
    rule = FOLDER_RULES[top]
    if rule is None:
        raise HTTPException(status_code=404, detail="Not found")
    if rule.public:
        return
    if user is None:
        _deny(db, request, None, rel, "not signed in")
        if "text/html" in request.headers.get("accept", ""):
            raise NotAuthenticated()
        raise HTTPException(status_code=401, detail="Not authenticated")
    if rule.perms and rbac.has_any(user, rule.perms):
        return
    if rule.staff and _is_staff(user):
        return
    if rule.owner is not None and rule.owner(db, user, rel):
        return
    _deny(db, request, user, rel, "no permission and not the owner")
    raise PermissionDenied(rule.perms[0] if rule.perms else f"storage.{top}")


@router.get("/storage/{path:path}", include_in_schema=False)
def storage_file(path: str, request: Request, db: Session = Depends(get_db),
                 user: Optional[User] = Depends(get_optional_user)):
    parts = normalise(path)
    if not parts or len(parts) < 2:
        raise HTTPException(status_code=404, detail="Not found")
    rel = "/".join(parts)
    target = (STORAGE_ROOT / Path(*parts)).resolve()
    if STORAGE_ROOT not in target.parents:
        raise HTTPException(status_code=404, detail="Not found")
    _authorise(db, request, user, parts[0], rel)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    disposition = "inline" if media_type.startswith(INLINE_TYPES) else "attachment"
    return FileResponse(str(target), media_type=media_type, filename=target.name, content_disposition_type=disposition)


__all__ = ["router", "FOLDER_RULES", "DEFAULT_RULE", "normalise"]
