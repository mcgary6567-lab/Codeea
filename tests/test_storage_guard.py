"""The /storage guard (app/web/storage_files.py): files under storage/ are served per folder rule, never openly.

Re-runnable against the same database: any PDF or archive this module has to create is removed again, and a
payslip whose pdf_path it fills in is reset to what it was. Only audit events (storage / denied) are left behind.
"""
from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import BASE_DIR
from app.database import SessionLocal
from app.main import app
from app.models.academic import Certificate
from app.models.core import AuditEvent, User
from app.models.finance import Invoice
from app.models.people import Client, Employee, Payslip
from app.services import billing, payroll
from app.web.storage_files import FOLDER_RULES, normalise

STORAGE = BASE_DIR / "storage"


def _login(username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code == 303, f"login failed for {username}: {r.status_code}"
    return c


def _rel(stored: str) -> str:
    """The clean 'folder/file' form of whatever spelling a service stored."""
    return "/".join(normalise(stored))


def _storage_url(stored: str) -> str:
    return f"/storage/{_rel(stored)}"


@pytest.fixture(scope="module")
def created_files():
    paths: list[Path] = []
    yield paths
    for p in paths:
        try:
            p.unlink()
        except FileNotFoundError:
            pass


@pytest.fixture(scope="module")
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture(scope="module")
def anon() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def admin() -> TestClient:
    return _login("admin@oqc.local", "Admin@12345")


@pytest.fixture(scope="module")
def billing_rep() -> TestClient:
    return _login("billing@oqc.local", "Billing@123")


@pytest.fixture(scope="module")
def parent() -> TestClient:
    return _login("parent1@oqc.local", "Parent@123")


@pytest.fixture(scope="module")
def teacher() -> TestClient:
    return _login("teacher1@oqc.local", "Teacher@123")


def _ensure_invoice_pdf(db, invoice: Invoice, created_files: list[Path]) -> str:
    """Return the invoice's stored pdf_path, rendering the PDF (and scheduling its removal) when it is not on disk."""
    before = invoice.pdf_path
    if not invoice.pdf_path or not (STORAGE / _rel(invoice.pdf_path)).is_file():
        billing.generate_invoice_pdf(db, invoice)
        path = STORAGE / _rel(invoice.pdf_path)
        created_files.append(path)
        if before is None:
            # keep the database as it was: the file goes at teardown and the row never pointed at one
            db.rollback()
            invoice.pdf_path = before
            return str(path.relative_to(BASE_DIR)).replace("\\", "/")
        db.commit()
    return invoice.pdf_path


@pytest.fixture(scope="module")
def own_invoice(db, created_files) -> tuple[Invoice, str]:
    parent_user = db.query(User).filter(User.email == "parent1@oqc.local").one()
    client = db.query(Client).filter(Client.user_id == parent_user.id).one()
    inv = db.query(Invoice).filter(Invoice.client_id == client.id).order_by(Invoice.id).first()
    assert inv is not None, "seed should give parent1's family at least one invoice"
    return inv, _ensure_invoice_pdf(db, inv, created_files)


@pytest.fixture(scope="module")
def other_invoice(db, created_files, own_invoice) -> tuple[Invoice, str]:
    inv = db.query(Invoice).filter(Invoice.client_id != own_invoice[0].client_id).order_by(Invoice.id).first()
    assert inv is not None
    return inv, _ensure_invoice_pdf(db, inv, created_files)


@pytest.fixture(scope="module")
def own_payslip(db, created_files) -> tuple[Payslip, str]:
    """A payslip of teacher1's employee record with its PDF on disk; pdf_path is restored afterwards."""
    teacher_user = db.query(User).filter(User.email == "teacher1@oqc.local").one()
    emp = db.query(Employee).filter(Employee.user_id == teacher_user.id).one()
    ps = (db.query(Payslip).filter(Payslip.employee_id == emp.id, Payslip.pdf_path.isnot(None))
          .order_by(Payslip.id.desc()).first())
    if ps is not None and (STORAGE / _rel(ps.pdf_path)).is_file():
        yield ps, ps.pdf_path
        return
    ps = ps or db.query(Payslip).filter(Payslip.employee_id == emp.id).order_by(Payslip.id.desc()).first()
    assert ps is not None, "seed should give teacher1 a payslip (ESS ledger)"
    before = ps.pdf_path
    rel = payroll.payslip_pdf(db, ps)
    db.commit()
    created_files.append(BASE_DIR / rel)
    yield ps, rel
    ps.pdf_path = before
    db.commit()


@pytest.fixture(scope="module")
def other_payslip(db, own_payslip) -> tuple[Payslip, str]:
    """Someone else's payslip whose PDF exists on disk."""
    for ps in (db.query(Payslip).filter(Payslip.employee_id != own_payslip[0].employee_id,
                                        Payslip.pdf_path.isnot(None)).order_by(Payslip.id)):
        if (STORAGE / _rel(ps.pdf_path)).is_file():
            return ps, ps.pdf_path
    pytest.skip("no other payslip PDF on disk")


@pytest.fixture(scope="module")
def backup_file(created_files) -> Path:
    folder = STORAGE / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"pytest-guard-{uuid4().hex[:8]}.zip"
    p.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    created_files.append(p)
    return p


# ------------------------------------------------------------------------------------------- policy table
def test_policy_table_covers_every_seeded_folder():
    on_disk = {p.name for p in STORAGE.iterdir() if p.is_dir()}
    listed = set(FOLDER_RULES)
    # Folders the services write into must each have a line in the table (recordings fall to the default rule).
    for folder in ("invoices", "receipts", "payslips", "result_cards", "certificates", "attachments", "uploads",
                   "backups", "agent_screenshots"):
        assert folder in listed, folder
    assert FOLDER_RULES["agent_screenshots"] is None
    assert FOLDER_RULES["certificates"].public is True
    assert on_disk - listed <= {"recordings"}, on_disk - listed


def test_normalise_strips_storage_prefixes_and_refuses_traversal():
    assert normalise("/storage/invoices/X.pdf") == ["invoices", "X.pdf"]
    assert normalise("storage/certificates/X.pdf") == ["certificates", "X.pdf"]
    assert normalise("invoices//X.pdf") == ["invoices", "X.pdf"]
    assert normalise("../app/config.py") is None
    assert normalise("invoices/../../app/config.py") is None
    assert normalise("invoices\\..\\..\\app\\config.py") is None
    assert normalise("C:/Windows/win.ini") is None


# ------------------------------------------------------------------------------------------- invoices
def test_anonymous_cannot_fetch_an_invoice(anon, own_invoice, db):
    inv, stored = own_invoice
    r = anon.get(_storage_url(stored), follow_redirects=False)
    assert r.status_code in (302, 303, 401), r.status_code
    assert r.status_code != 200
    # a browser is sent to login; a plain client gets 401
    r2 = anon.get(_storage_url(stored), headers={"Accept": "text/html"}, follow_redirects=False)
    assert r2.status_code == 303 and r2.headers["location"].startswith("/login")
    r3 = anon.get(_storage_url(stored), headers={"Accept": "application/json"}, follow_redirects=False)
    assert r3.status_code == 401
    db.expire_all()
    assert db.query(AuditEvent).filter(AuditEvent.module == "storage", AuditEvent.action == "denied").count() >= 1


def test_parent_fetches_own_invoice_but_not_another_familys(parent, own_invoice, other_invoice):
    inv, stored = own_invoice
    r = parent.get(_storage_url(stored))
    assert r.status_code == 200, r.status_code
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.headers["content-disposition"].startswith("inline")
    assert r.content[:4] == b"%PDF"
    # the exact string the template builds: '/storage/' + the stored '/storage/invoices/..' path
    assert parent.get(f"/storage/{stored}").status_code == 200

    other, other_stored = other_invoice
    assert other.client_id != inv.client_id
    r = parent.get(_storage_url(other_stored))
    assert r.status_code == 403, r.status_code


def test_billing_user_fetches_any_invoice(billing_rep, own_invoice, other_invoice):
    for _, stored in (own_invoice, other_invoice):
        r = billing_rep.get(_storage_url(stored))
        assert r.status_code == 200, (stored, r.status_code)
        assert r.headers["content-type"].startswith("application/pdf")


def test_missing_invoice_is_404_for_billing_and_403_for_a_family(billing_rep, parent):
    assert billing_rep.get("/storage/invoices/INV-0000-99999.pdf").status_code == 404
    assert parent.get("/storage/invoices/INV-0000-99999.pdf").status_code == 403


# ------------------------------------------------------------------------------------------- payslips
def test_employee_fetches_own_payslip_only(teacher, own_payslip, other_payslip):
    ps, stored = own_payslip
    r = teacher.get(_storage_url(stored))
    assert r.status_code == 200, r.status_code
    assert r.headers["content-type"].startswith("application/pdf")

    other, other_stored = other_payslip
    r = teacher.get(_storage_url(other_stored))
    assert r.status_code == 403, r.status_code


def test_payroll_viewer_fetches_any_payslip(admin, other_payslip):
    _, stored = other_payslip
    assert admin.get(_storage_url(stored)).status_code == 200


def test_family_cannot_fetch_payslips(parent, own_payslip):
    _, stored = own_payslip
    assert parent.get(_storage_url(stored)).status_code == 403


# ------------------------------------------------------------------------------------------- certificates
def test_certificates_are_public(anon, db):
    cert = db.query(Certificate).filter(Certificate.pdf_path.isnot(None)).order_by(Certificate.id).first()
    assert cert is not None, "seed should give at least one certificate"
    path = STORAGE / _rel(cert.pdf_path)
    if not path.is_file():
        pytest.skip("seeded certificate PDF not on disk")
    r = anon.get(_storage_url(cert.pdf_path))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    # the portal template's spelling: '/storage/' ~ 'storage/certificates/..'
    assert anon.get(f"/storage/{cert.pdf_path}").status_code == 200


# ------------------------------------------------------------------------------------------- traversal / hidden
@pytest.mark.parametrize("url", [
    "/storage/../app/config.py",
    "/storage/..%2Fapp%2Fconfig.py",
    "/storage/invoices/..%2F..%2Fapp%2Fconfig.py",
    "/storage/invoices/..%5C..%5Capp%5Cconfig.py",
    "/storage/%2E%2E/app/config.py",
    "/storage/C:/Windows/win.ini",
    "/storage/storage/../app/config.py",
])
def test_traversal_is_404(admin, url):
    r = admin.get(url)
    assert r.status_code == 404, (url, r.status_code)
    assert b"SECRET_KEY" not in r.content


def test_bare_folder_and_root_are_404(admin, anon):
    assert admin.get("/storage/invoices").status_code == 404
    assert admin.get("/storage/invoices/").status_code == 404
    assert anon.get("/storage/").status_code == 404


def test_agent_screenshots_never_served_here(admin, anon):
    for c in (admin, anon):
        assert c.get("/storage/agent_screenshots/anything.png").status_code == 404
        assert c.get("/storage/storage/agent_screenshots/anything.png").status_code == 404
        assert c.get("/storage//agent_screenshots/anything.png").status_code == 404


def test_unlisted_folder_is_404_without_settings_view(teacher, parent, anon, created_files):
    folder = STORAGE / "recordings"
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"pytest-guard-{uuid4().hex[:8]}.txt"
    p.write_text("x")
    created_files.append(p)
    url = f"/storage/recordings/{p.name}"
    assert teacher.get(url).status_code == 404
    assert parent.get(url).status_code == 404
    assert anon.get(url, follow_redirects=False).status_code == 404


# ------------------------------------------------------------------------------------------- backups
def test_backups_only_for_backups_view(admin, teacher, parent, anon, backup_file):
    url = f"/storage/backups/{backup_file.name}"
    assert anon.get(url, follow_redirects=False).status_code in (303, 401)
    assert teacher.get(url).status_code == 403
    assert parent.get(url).status_code == 403
    r = admin.get(url)
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment")


# ------------------------------------------------------------------------------------------- uploads
def test_uploads_are_staff_only(admin, teacher, parent, created_files):
    folder = STORAGE / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"pytest-guard-{uuid4().hex[:8]}.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    created_files.append(p)
    url = f"/storage/uploads/{p.name}"
    r = admin.get(url)
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/png")
    assert r.headers["content-disposition"].startswith("inline")
    assert parent.get(url).status_code == 403          # no Attachment row says it is theirs
    assert teacher.get(url).status_code == 403         # teachers are not portal "admin"


def test_denied_attempts_are_audited(db, teacher, backup_file):
    db.expire_all()
    before = db.query(AuditEvent).filter(AuditEvent.module == "storage", AuditEvent.action == "denied").count()
    teacher.get(f"/storage/backups/{backup_file.name}")
    db.expire_all()
    after = db.query(AuditEvent).filter(AuditEvent.module == "storage", AuditEvent.action == "denied").count()
    assert after == before + 1
    ev = (db.query(AuditEvent).filter(AuditEvent.module == "storage", AuditEvent.action == "denied")
          .order_by(AuditEvent.id.desc()).first())
    assert "teacher1@oqc.local" in (ev.description or "") and backup_file.name in (ev.description or "")


def test_no_temporary_files_leak_between_runs(created_files):
    # Everything created above is removed by the module fixture at teardown. Anything with our prefix that is not
    # on this run's list leaked from an interrupted earlier run: sweep it so the storage tree stays clean.
    stale = [p for p in STORAGE.rglob("pytest-guard-*") if p.is_file() and p not in created_files]
    for p in stale:
        os.remove(p)
    assert not [p for p in STORAGE.rglob("pytest-guard-*") if p.is_file() and p not in created_files]
