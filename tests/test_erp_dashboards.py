"""ERP dashboards (docs/AUDIT_ACADEMICS.md 3.14) and the erp_billing / erp_quality seeds.

Run against a private seeded database, e.g. (PowerShell):

    $env:DATABASE_URL='sqlite:///./data/oqc_wpC.db'
    .venv/Scripts/python.exe seed.py --reset
    .venv/Scripts/python.exe -m pytest tests/test_erp_dashboards.py

The default below is applied before anything from ``app`` is imported so the module also works standalone.
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/oqc_wpC.db")

from datetime import date, timedelta  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.models.crm import Feedback  # noqa: E402
from app.models.erp import CallRecord, LedgerAddition  # noqa: E402
from app.models.finance import Invoice, Payment, Subscription  # noqa: E402
from app.models.people import Client, Employee, Teacher  # noqa: E402
from app.models.scheduling import QAReview  # noqa: E402
from app.services import dashboards as svc  # noqa: E402

URLS = ["/dashboards/clients", "/dashboards/subscriptions", "/dashboards/subscriptions/amount",
        "/dashboards/billing", "/dashboards/monthly-performance", "/dashboards/financial-summary",
        "/dashboards/monthly-insights"]


@pytest.fixture()
def billing_user(client: TestClient):
    r = client.post("/login", data={"username": "billing@oqc.local", "password": "Billing@123"},
                    follow_redirects=False)
    assert r.status_code == 303
    return client


def _series_ok(series: dict) -> None:
    assert set(series) >= {"labels", "data"}
    assert len(series["labels"]) == len(series["data"])
    assert all(isinstance(v, (int, float)) for v in series["data"])


# ============================================================================ pages
@pytest.mark.parametrize("url", URLS)
def test_every_dashboard_renders_without_filters(admin: TestClient, url: str):
    r = admin.get(url)
    assert r.status_code == 200, f"{url} -> {r.status_code}"
    assert "<html" in r.text.lower()


def test_dashboards_index_redirects(admin: TestClient):
    r = admin.get("/dashboards", follow_redirects=False)
    assert r.status_code in (302, 303, 307)


@pytest.mark.parametrize("url", URLS)
def test_every_dashboard_renders_for_the_billing_rep(billing_user: TestClient, url: str):
    assert billing_user.get(url).status_code == 200


def test_dashboards_require_authentication(client: TestClient):
    r = client.get("/dashboards/clients", follow_redirects=False)
    assert r.status_code in (302, 303, 307, 401, 403)


def test_client_dashboard_with_filters(admin: TestClient, db):
    c = db.query(Client).first()
    qs = (f"?date_from={(date.today() - timedelta(days=400)).isoformat()}&date_to={date.today().isoformat()}"
          f"&client_id={c.id}&country={c.country}&shift={c.shift}&status=regular")
    assert admin.get("/dashboards/clients" + qs).status_code == 200
    for status in [key for key, _ in svc.CLIENT_STATUS_OPTIONS]:
        assert admin.get(f"/dashboards/clients?status={status}").status_code == 200


def test_subscription_dashboards_with_filters(admin: TestClient, db):
    t = db.query(Teacher).first()
    for base in ("/dashboards/subscriptions", "/dashboards/subscriptions/amount"):
        qs = (f"?date_from={(date.today() - timedelta(days=400)).isoformat()}&date_to={date.today().isoformat()}"
              f"&teacher_id={t.id}&shift=night&status=regular&country=United Kingdom")
        assert admin.get(base + qs).status_code == 200
        for status in [key for key, _ in svc.SUBSCRIPTION_STATUS_OPTIONS]:
            assert admin.get(f"{base}?status={status}").status_code == 200


def test_billing_dashboard_with_filters(admin: TestClient, db):
    t = db.query(Teacher).first()
    qs = (f"?date_from={(date.today() - timedelta(days=90)).isoformat()}&date_to={date.today().isoformat()}"
          f"&teacher_id={t.id}&shift=morning&country=United Kingdom")
    assert admin.get("/dashboards/billing" + qs).status_code == 200


def test_monthly_performance_with_filters(admin: TestClient, db):
    e = db.query(Employee).first()
    start = (date.today() - timedelta(days=60)).isoformat()
    for dept in [key for key, _ in svc.DEPARTMENT_TABS]:
        qs = f"?start={start}&end={date.today().isoformat()}&department={dept}&employee_ids={e.id}"
        assert admin.get("/dashboards/monthly-performance" + qs).status_code == 200
    assert admin.get("/dashboards/monthly-performance?department=nonsense").status_code == 200


def test_financial_summary_with_filters(admin: TestClient, db):
    c = db.query(Client).first()
    qs = (f"?date_from={(date.today() - timedelta(days=200)).isoformat()}&date_to={date.today().isoformat()}"
          f"&client_id={c.id}&shift={c.shift}&currency={c.currency}&country={c.country}&status=regular")
    assert admin.get("/dashboards/financial-summary" + qs).status_code == 200
    opts = svc.options(db)
    if opts["groups"]:
        assert admin.get(f"/dashboards/financial-summary?group_id={opts['groups'][0][0]}").status_code == 200
    if opts["states"]:
        assert admin.get(f"/dashboards/financial-summary?state={opts['states'][0]}").status_code == 200


def test_monthly_insights_with_month(admin: TestClient):
    for month, _label in svc.recent_months(4):
        assert admin.get(f"/dashboards/monthly-insights?month={month}").status_code == 200
    assert admin.get("/dashboards/monthly-insights?month=not-a-month").status_code == 200


# ============================================================================ services
def test_options_lists_are_populated(db):
    opts = svc.options(db)
    assert opts["clients"] and opts["teachers"] and opts["employees"]
    assert opts["countries"] and opts["currencies"]
    assert opts["base"]


def test_client_dashboard_numbers(db):
    d = svc.client_dashboard(db)
    total_clients = db.query(Client).count()
    assert d["total"] == total_clients > 0
    assert sum(d["tiles"].values()) <= d["total"]
    assert d["tiles"]["regular"] > 0
    for series in d["charts"].values():
        _series_ok(series)
    assert sum(d["charts"]["country"]["data"]) == d["total"]
    assert sum(d["charts"]["currency"]["data"]) == d["total"]
    filtered = svc.client_dashboard(db, status="regular")
    assert filtered["total"] == filtered["tiles"]["regular"] <= d["total"]


def test_subscription_dashboard_count_and_amount(db):
    counts = svc.subscription_dashboard(db)
    assert counts["count"] == db.query(Subscription).count() > 0
    assert counts["total"] == counts["count"]
    assert sum(counts["tiles"].values()) <= counts["count"]
    for series in counts["charts"].values():
        _series_ok(series)

    amounts = svc.subscription_dashboard(db, amount=True)
    assert amounts["count"] == counts["count"]
    assert amounts["total"] > 0
    assert amounts["tiles"]["regular"] > 0
    assert sum(amounts["charts"]["course"]["data"]) == pytest.approx(amounts["total"], rel=0.02)


def test_billing_dashboard_numbers(db):
    d = svc.billing_dashboard(db, date_from=date.today() - timedelta(days=120), date_to=date.today())
    assert d["volume"]["count"] > 0
    assert d["volume"]["monthly_base"] > 0
    assert d["volume"]["annual_base"] == pytest.approx(d["volume"]["monthly_base"] * 12, rel=0.01)
    assert set(d["receivables"]) == {"opening", "closing", "difference"}
    assert d["receivables"]["difference"] == pytest.approx(
        d["receivables"]["closing"] - d["receivables"]["opening"], abs=0.05)
    assert len(d["payroll"]["rows"]) == 3
    assert d["invoices"]["confirmed_base"] >= 0
    assert len(d["flow"]["labels"]) == len(d["flow"]["regular"]["data"]) == len(d["flow"]["cancelled"]["data"])


def test_monthly_performance_panels(db):
    d = svc.monthly_performance(db, start=date.today() - timedelta(days=90), end=date.today())
    assert len(d["panels"]) == len(svc.PERFORMANCE_PANELS)
    keys = {p["key"] for p in d["panels"]}
    assert {"regular", "missed_classes", "invoices_confirmed", "violations"} <= keys
    for p in d["panels"]:
        _series_ok(p["series"])
        assert p["value"] >= 0
        assert p["value"] == pytest.approx(sum(p["series"]["data"]), rel=0.01, abs=0.05)
    assert d["totals"]["missed_classes"] >= 0
    assert d["totals"]["receipts_confirmed"] > 0

    academics = svc.monthly_performance(db, start=date.today() - timedelta(days=90), end=date.today(),
                                        department="academics")
    assert 0 < len(academics["panels"]) < len(d["panels"])
    assert {p["department"] for p in academics["panels"]} == {"academics"}


def test_monthly_performance_employee_filter_narrows(db):
    teacher = db.query(Teacher).filter(Teacher.employee_id.isnot(None)).first()
    start, end = date.today() - timedelta(days=120), date.today()
    everyone = svc.monthly_performance(db, start=start, end=end)
    one = svc.monthly_performance(db, start=start, end=end, employee_ids=[teacher.employee_id])
    assert one["totals"]["missed_classes"] <= everyone["totals"]["missed_classes"]
    assert one["totals"]["new_enrollment"] <= everyone["totals"]["new_enrollment"]


def test_financial_summary_numbers(db):
    d = svc.financial_summary(db)
    t = d["tiles"]
    assert t["total_clients"] == db.query(Client).count() > 0
    assert t["invoices_amount"] > 0
    assert t["received_amount"] > 0
    assert 0 <= t["collection_efficiency"] <= 200
    assert t["avg_revenue_per_client"] == pytest.approx(t["received_amount"] / t["total_clients"], rel=0.01)
    assert t["regular_subscriptions"] > 0
    assert t["active_students"] > 0
    assert t["clients_without_subscription"] <= t["total_clients"]
    assert t["clients_zero_students"] <= t["total_clients"]
    assert t["ledger_addition"] >= 0 and t["ledger_deduction"] >= 0
    for series in d["charts"].values():
        _series_ok(series)
    assert len(d["charts"]["top_outstanding"]["labels"]) <= 10
    assert sum(d["charts"]["currency"]["data"]) == t["total_clients"]


def test_financial_summary_client_filter(db):
    c = db.query(Client).first()
    d = svc.financial_summary(db, client_id=c.id)
    assert d["tiles"]["total_clients"] == 1


def test_monthly_insights_table(db):
    d = svc.monthly_insights(db)
    assert len(d["rows"]) == len(svc.INSIGHT_ROWS)
    assert d["previous_month"] == svc.previous_month(d["month"])
    labels = {r["label"] for r in d["rows"]}
    assert {"Leads", "Trial", "Current Student", "Income", "Fee Recovery %", "Trial Conversion %"} <= labels
    for r in d["rows"]:
        assert r["change"] in ("No Change", "up", "down")
        assert r["direction"] in ("flat", "up", "down")
        assert set(r["current"]) == {"day", "night", "total"}
        # Day + Night never exceeds Total for additive rows. "teacher" counts distinct teachers, and a
        # teacher who serves both shifts is counted in both columns, so it is deliberately not additive.
        if r["format"] in ("int", "money") and r["key"] != "teacher":
            assert r["current"]["day"] + r["current"]["night"] <= r["current"]["total"] + 0.01
    by_key = {r["key"]: r for r in d["rows"]}
    assert by_key["total_session"]["current"]["total"] > 0
    assert by_key["teacher"]["current"]["total"] > 0
    assert 0 <= by_key["fee_recovery"]["current"]["total"] <= 100
    assert by_key["pending_fee"]["current"]["total"] == pytest.approx(
        100 - by_key["fee_recovery"]["current"]["total"], abs=0.2)


def test_monthly_insights_accepts_any_recent_month(db):
    for month, _label in svc.recent_months(6):
        d = svc.monthly_insights(db, month=month)
        assert d["month"] == month
        assert len(d["rows"]) == len(svc.INSIGHT_ROWS)


# ============================================================================ seeds
def test_erp_billing_seed_migrated_invoices(db):
    assert db.query(Invoice).filter(Invoice.status.in_(["sent", "void"])).count() == 0
    assert db.query(Invoice).filter(Invoice.is_bulk.is_(True)).count() > 0
    assert db.query(Invoice).filter(Invoice.subs_total > 0).count() > 0
    for inv in db.query(Invoice).filter(Invoice.status.in_(["confirmed", "paid"])).limit(20).all():
        assert inv.confirmed_at is not None


def test_erp_billing_seed_filled_receipts(db):
    payments = db.query(Payment).all()
    assert payments
    assert all(p.receipt_date for p in payments)
    assert all(p.category for p in payments)
    assert all(p.receiving_destination for p in payments)
    assert all(p.receiver_name for p in payments)
    assert sum(1 for p in payments if p.beneficiary_account_id) > 0
    assert sum(1 for p in payments if p.billing_rep_id) > 0


def test_erp_billing_seed_created_ledger_additions(db):
    rows = db.query(LedgerAddition).all()
    assert len(rows) >= 25
    statuses = {r.status for r in rows}
    assert "confirmed" in statuses
    assert len(statuses) >= 2
    assert {r.effect for r in rows} == {"add", "minus"}
    assert len({r.addition_type for r in rows}) >= 4
    oldest = min(r.addition_date for r in rows)
    assert (date.today() - oldest).days <= 95
    for r in rows:
        if r.status == "confirmed":
            assert r.ledger_entry_id is not None
            assert float(r.lc_amount or 0) > 0


def test_erp_quality_seed_call_records(db):
    calls = db.query(CallRecord).all()
    assert len(calls) >= 120
    assert {c.source for c in calls} == {"AGENT", "TEAMS", "ZOOM"}
    mapped = [c for c in calls if c.session_id]
    assert len(mapped) >= int(0.7 * len(calls))
    assert sum(1 for c in calls if c.review_state == "unmapped") >= 10
    assert {"queued", "in_review", "reviewed"} <= {c.review_state for c in calls}
    assert all(c.recording_url and c.duration_minutes >= 0 for c in calls)


def test_erp_quality_seed_call_reviews(db):
    reviews = db.query(QAReview).filter(QAReview.sample_type == "call").all()
    assert len(reviews) >= 35
    statuses = {r.status for r in reviews}
    assert {"completed", "in_review", "queued", "flagged", "rejected"} <= statuses
    completed = [r for r in reviews if r.status == "completed"]
    assert len(completed) >= len(reviews) / 3
    for r in completed:
        assert r.parameter_scores
        assert 1 <= float(r.overall_rating) <= 5
        assert r.remarks and r.reviewed_at is not None
    assert any(r.issues for r in reviews)
    assert all(r.call_record_id for r in reviews)


def test_erp_quality_seed_feedback_and_evaluations(db):
    from app.models.academic import Evaluation

    feedback = db.query(Feedback).all()
    assert feedback
    assert all(f.feedback_source for f in feedback)
    assert len({f.feedback_source for f in feedback}) >= 3
    seeded = db.query(Feedback).filter(Feedback.trigger == "erp_seed").all()
    assert len(seeded) >= 30
    assert all(1 <= (f.rating or 0) <= 5 and f.session_id for f in seeded)

    evaluations = db.query(Evaluation).all()
    assert evaluations
    assert db.query(Evaluation).filter(Evaluation.is_manual.is_(True)).count() >= len(evaluations) // 5
    assert db.query(Evaluation).filter(Evaluation.assessment_id.isnot(None)).count() > 0
    assert db.query(Evaluation).filter(Evaluation.due_date.isnot(None)).count() > 0
    assert db.query(Evaluation).filter(Evaluation.result == "fail").count() > 0
