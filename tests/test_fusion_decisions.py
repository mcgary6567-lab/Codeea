"""Fusion gap report, step 4 (21 Oct 2026): the owner's decisions as Branch Properties. Both default to today's
behaviour: exports for every member of staff, and revenue booked when money arrives.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models.core import Setting
from app.models.finance import Invoice, JournalEntry
from app.models.people import Client
from app.services import billing

TAG = "fdc" + uuid.uuid4().hex[:6]


def _login(email: str, password: str) -> TestClient:
    c = TestClient(app)
    assert c.post("/login", data={"username": email, "password": password}, follow_redirects=False).status_code == 303
    return c


def _set(key: str, value) -> object:
    s = SessionLocal()
    try:
        row = s.query(Setting).filter(Setting.key == key).first()
        before = row.value if row else None
        if row is None:
            row = Setting(key=key, group="general", label=key, value_type="boolean", is_editable=True)
            s.add(row)
        row.value = {"value": value}
        s.commit()
        return before
    finally:
        s.close()


def _restore(key: str, before) -> None:
    s = SessionLocal()
    try:
        row = s.query(Setting).filter(Setting.key == key).first()
        if before is None:
            s.delete(row)
        else:
            row.value = before
        s.commit()
    finally:
        s.close()


def test_both_decisions_are_branch_properties_that_default_to_no():
    s = SessionLocal()
    try:
        for key in ("exports_admin_only", "post_invoices_on_issue"):
            row = s.query(Setting).filter(Setting.key == key).first()
            assert row is not None and row.is_editable and row.value_type == "boolean", key
    finally:
        s.close()


def test_exports_can_be_limited_to_admins():
    hr = _login("hr@oqc.local", "People@123")
    admin = _login("admin@oqc.local", "Admin@12345")
    payload = {"name": TAG, "headers": ["A"], "rows": [["1"]], "format": "csv"}
    assert hr.post("/ir/export", json=payload).status_code == 200, "everyone on staff by default"
    before = _set("exports_admin_only", True)
    try:
        assert hr.post("/ir/export", json=payload).status_code == 403
        assert "data-ir-no-export" in hr.get("/hr/employees").text
        assert admin.post("/ir/export", json=payload).status_code == 200
        assert "data-ir-no-export" not in admin.get("/hr/employees").text
    finally:
        _restore("exports_admin_only", before)
    assert hr.post("/ir/export", json=payload).status_code == 200


def _invoice(s, client: Client, amount: float) -> Invoice:
    inv = Invoice(invoice_number=f"{TAG}-{amount:g}"[:30], client_id=client.id, issue_date=date.today(),
                  due_date=date.today() + timedelta(days=7), currency=client.currency or "GBP", subtotal=amount,
                  total=amount, status="draft")
    s.add(inv)
    s.flush()
    return inv


def test_invoices_post_to_the_ledger_on_issue_when_chosen():
    s = SessionLocal()
    try:
        client = s.query(Client).filter(Client.status.in_(("active", "regular"))).first()
        # default: confirming an invoice books nothing in the general ledger
        quiet = _invoice(s, client, 40)
        billing.confirm_invoice(s, quiet, None)
        assert billing.invoice_accrual(s, quiet.id) is None

        row = s.query(Setting).filter(Setting.key == "post_invoices_on_issue").first()
        row.value = {"value": True}
        s.flush()
        inv = _invoice(s, client, 50)
        billing.confirm_invoice(s, inv, None)
        accrual = billing.invoice_accrual(s, inv.id)
        assert accrual is not None
        lines = {(l.account.code, float(l.debit), float(l.credit)) for l in accrual.lines}
        base = billing.convert_to_base(s, 50, inv.currency)
        assert lines == {("1100", base, 0.0), ("4000", 0.0, base)}

        pay = billing.record_payment(s, client, 50, inv.currency, "bank_transfer", f"{TAG}-ref", invoice=inv)
        je = s.query(JournalEntry).filter(JournalEntry.reference_type == "payment", JournalEntry.reference_id == pay.id).one()
        codes = {l.account.code: (float(l.debit), float(l.credit)) for l in je.lines}
        assert codes.get("1100") == (0.0, float(pay.amount_in_base)) and "4000" not in codes, \
            "the receipt clears the receivable instead of booking revenue twice"

        other = _invoice(s, client, 30)
        billing.confirm_invoice(s, other, None)
        billing.cancel_invoice(s, other, None, "raised by mistake")
        assert billing.invoice_accrual(s, other.id).status == "reversed"
    finally:
        s.rollback()
        s.close()
