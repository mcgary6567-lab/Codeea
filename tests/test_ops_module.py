"""Operations module tests: KPI role/department scorecards, transformation OS, decision register,
structured daily reports, the report catalogue, AI governance, and the /api/v1/ops REST surface.

Runs against the seeded development database (ASCII output only - the Windows console is cp1252).
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.core import AIModelRun, Department, RiskAlert, Setting, User
from app.models.ops import (KPI, DailyReport, Decision, DepartmentScorecard, Project, ReportRun, Task,
                            TrajectoryMeeting, TransformationItem, TransitionRecord)
from app.models.people import DevelopmentPlan
from app.services import kpi as kpi_svc
from app.services import reports as report_svc


def _client(username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code == 303, f"login failed for {username}"
    return c


@pytest.fixture()
def manager():
    return _client("manager@oqc.local", "Manager@123")


PERIODS = kpi_svc.last_n_periods(6)


# --------------------------------------------------------------------------- pages
@pytest.mark.parametrize("url", [
    "/command-center", "/command-center?period=last_month", "/command-center?period=quarter",
    "/command-center?period=ytd",
    "/kpis", "/kpis?role=teacher", "/kpis?frequency=monthly", "/kpis/scorecards", "/kpis/roles",
    "/kpis/roles?role=supervisor",
    "/tasks", "/tasks?view=all&layout=list", "/tasks/projects",
    "/transformation", "/transformation/roles", "/transformation/transitions", "/transformation/development",
    "/transformation/governance", "/transformation/meetings",
    "/decisions", "/decisions?status=implemented", "/decisions?category=financial", "/decisions/gaps",
    "/decisions/gaps?days=90",
    "/daily-reports", "/daily-reports/history", "/daily-reports/team",
    "/reports", "/reports/runs", "/reports?group=Finance",
    "/ai-governance", "/ai-governance/queue", "/ai-governance/dashboard", "/ai-governance/policy",
    "/ai-governance/registry",
])
def test_admin_pages(admin, url):
    assert admin.get(url).status_code == 200, url


@pytest.mark.parametrize("period", PERIODS)
def test_period_selectors(admin, period):
    for url in ("/kpis?period=", "/kpis/scorecards?period=", "/kpis/roles?period=",
                "/kpis/roles?role=supervisor&period="):
        assert admin.get(url + period).status_code == 200, url + period


@pytest.mark.parametrize("months", [3, 6, 12])
def test_ai_dashboard_windows(admin, months):
    assert admin.get(f"/ai-governance/dashboard?months={months}").status_code == 200


def test_every_report_renders_and_paginates(admin):
    end = date.today()
    start = end - timedelta(days=60)
    for meta in report_svc.REPORTS:
        r = admin.get(f"/reports/{meta['key']}?start={start}&end={end}")
        assert r.status_code == 200, meta["key"]
    assert admin.get("/reports/does_not_exist", follow_redirects=False).status_code == 303


def test_detail_pages(admin, db):
    kpi = db.query(KPI).order_by(KPI.id).first()
    assert admin.get(f"/kpis/{kpi.id}").status_code == 200
    task = db.query(Task).order_by(Task.id).first()
    assert admin.get(f"/tasks/{task.id}").status_code == 200
    project = db.query(Project).order_by(Project.id).first()
    assert admin.get(f"/tasks/projects/{project.id}").status_code == 200
    decision = db.query(Decision).order_by(Decision.id).first()
    assert admin.get(f"/decisions/{decision.id}").status_code == 200
    meeting = db.query(TrajectoryMeeting).order_by(TrajectoryMeeting.id).first()
    assert admin.get(f"/transformation/meetings/{meeting.id}").status_code == 200
    run = db.query(AIModelRun).order_by(AIModelRun.id).first()
    assert admin.get(f"/ai-governance/{run.id}").status_code == 200


def test_missing_records_redirect_not_crash(admin):
    for url in ("/kpis/999999", "/tasks/999999", "/decisions/999999", "/ai-governance/999999",
                "/transformation/meetings/999999"):
        assert admin.get(url, follow_redirects=False).status_code == 303, url


# --------------------------------------------------------------------------- seed expectations
def test_seed_shape(db):
    assert db.query(KPI).count() >= 45
    assert db.query(KPI).filter(KPI.role_slug == "teacher").count() >= 8
    for role in ("teacher", "supervisor", "manager", "hr", "academic", "qa", "finance", "marketing", "ceo"):
        assert db.query(KPI).filter(KPI.role_slug == role).count() > 0, role
    for code in ("teacher_test_improvement", "marketing_referral_pct", "ceo_nps", "hr_enps"):
        assert db.query(KPI).filter(KPI.code == code).first() is not None, code
    assert db.query(Project).count() >= 4
    assert db.query(Task).count() >= 50
    assert db.query(Task).filter(Task.escalated.is_(True)).count() > 0
    assert db.query(Task).filter(Task.recurrence.isnot(None)).count() > 0
    assert db.query(Task).filter(Task.depends_on_id.isnot(None)).count() > 0
    assert db.query(TransformationItem).count() >= 14
    assert {i.state for i in db.query(TransformationItem)} >= {"planned", "alpha", "beta", "full_launch"}
    assert db.query(TransitionRecord).count() >= 3
    assert db.query(DevelopmentPlan).filter(DevelopmentPlan.employee_id.is_(None)).count() >= 7
    assert db.query(TrajectoryMeeting).count() >= 6
    assert db.query(Decision).count() >= 25
    assert db.query(DepartmentScorecard).count() >= 14
    assert db.query(DailyReport).count() >= 200
    assert db.query(DailyReport).filter(DailyReport.is_late.is_(True)).count() > 0
    assert db.query(ReportRun).count() >= 4
    assert db.query(AIModelRun).filter(AIModelRun.module == "insights").count() >= 1
    assert db.query(Setting).filter(Setting.key == "ai_thresholds").first() is not None


def test_kpi_history_for_charts(db):
    from app.models.ops import KPIValue
    for period in PERIODS:
        assert db.query(KPIValue).filter(KPIValue.period == period,
                                         KPIValue.entity_type.is_(None)).count() > 10, period


# --------------------------------------------------------------------------- forms
def test_task_lifecycle(admin, db):
    project = db.query(Project).order_by(Project.id).first()
    r = admin.post("/tasks/create", data={"title": "Ops test task", "priority": "high",
                                          "project_id": str(project.id),
                                          "due_date": date.today().isoformat()}, follow_redirects=False)
    assert r.status_code == 303
    task_id = int(r.headers["location"].rsplit("/", 1)[1])
    assert admin.post(f"/tasks/{task_id}/move", data={"status": "in_progress"},
                      follow_redirects=False).status_code == 303
    assert admin.post(f"/tasks/{task_id}/comment", data={"text": "Working on it."},
                      follow_redirects=False).status_code == 303
    assert admin.post(f"/tasks/{task_id}/complete", data={"assessment_score": "4"},
                      follow_redirects=False).status_code == 303
    db.expire_all()
    t = db.query(Task).filter(Task.id == task_id).first()
    assert t.status == "done" and t.completed_at is not None
    assert admin.post(f"/tasks/{task_id}/delete", data={"rationale": "test cleanup"},
                      follow_redirects=False).status_code == 303


def test_kpi_forms(admin, db):
    kpi = db.query(KPI).filter(KPI.formula_key.is_(None)).first() or db.query(KPI).first()
    period = PERIODS[-1]
    r = admin.post(f"/kpis/{kpi.id}/value", data={"period": period, "value": "42",
                                                  "rationale": "Manual entry from the monthly pack"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert admin.post(f"/kpis/{kpi.id}/remind", data={"period": period},
                      follow_redirects=False).status_code == 303
    assert admin.post("/kpis/snapshot", data={"period": period}, follow_redirects=False).status_code == 303


def test_scorecard_submission(admin, db):
    dept = db.query(Department).filter(Department.is_active.is_(True)).order_by(Department.name).first()
    period = PERIODS[-1]
    r = admin.post("/kpis/scorecards/submit",
                   data={"period": period, "department_id": str(dept.id),
                         "highlights": "Automated test highlight.", "risks": "Automated test risk."},
                   follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    row = (db.query(DepartmentScorecard)
           .filter(DepartmentScorecard.department_id == dept.id, DepartmentScorecard.period == period).first())
    assert row is not None and row.status == "submitted"


def test_transformation_forms(admin, db):
    dept = db.query(Department).order_by(Department.id).first()
    r = admin.post("/transformation/items/create",
                   data={"system_name": "Ops test system", "description": "created by the test suite",
                         "department_id": str(dept.id), "state": "planned", "progress_pct": "10",
                         "target_date": (date.today() + timedelta(days=30)).isoformat()},
                   follow_redirects=False)
    assert r.status_code == 303
    item = (db.query(TransformationItem).filter(TransformationItem.system_name == "Ops test system")
            .order_by(TransformationItem.id.desc()).first())
    assert item is not None
    assert admin.post(f"/transformation/items/{item.id}/state", data={"state": "beta"},
                      follow_redirects=False).status_code == 303
    assert admin.post(f"/transformation/items/{item.id}/edit",
                      data={"system_name": "Ops test system", "state": "beta", "progress_pct": "55"},
                      follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.query(TransformationItem).filter(TransformationItem.id == item.id).first().state == "beta"
    assert admin.post(f"/transformation/items/{item.id}/delete", data={"rationale": "test cleanup"},
                      follow_redirects=False).status_code == 303

    r = admin.post("/transformation/transitions/create",
                   data={"role_title": "Ops test role", "status": "planned",
                         "checklist": "Hand over the inbox\nIntroduce the team",
                         "handover_notes": "Created by the test suite."}, follow_redirects=False)
    assert r.status_code == 303
    tr = (db.query(TransitionRecord).filter(TransitionRecord.role_title == "Ops test role")
          .order_by(TransitionRecord.id.desc()).first())
    assert tr is not None and len(tr.checklist) == 2
    assert admin.post(f"/transformation/transitions/{tr.id}/check", data={"index": "0"},
                      follow_redirects=False).status_code == 303
    assert admin.post(f"/transformation/transitions/{tr.id}/edit",
                      data={"role_title": "Ops test role", "status": "in_progress",
                            "checklist": '[{"item": "Hand over the inbox", "done": true}]'},
                      follow_redirects=False).status_code == 303

    r = admin.post("/transformation/development/create",
                   data={"title": "Ops test plan", "department_id": str(dept.id), "ai_fluency_level": "3",
                         "goal_1": "First month goal", "goal_2": "Second month goal"}, follow_redirects=False)
    assert r.status_code == 303
    plan = (db.query(DevelopmentPlan).filter(DevelopmentPlan.title == "Ops test plan")
            .order_by(DevelopmentPlan.id.desc()).first())
    assert plan is not None and len(plan.goals) == 2
    assert admin.post(f"/transformation/development/{plan.id}/edit",
                      data={"title": "Ops test plan", "ai_fluency_level": "4", "progress_pct": "50",
                            "status": "active", "goal_1": "First month goal", "goal_status_1": "done"},
                      follow_redirects=False).status_code == 303

    assert admin.post("/transformation/governance/rhythm",
                      data={"morning": "10:00", "afternoon": "17:00",
                            "agenda": "Review decisions\nScorecards\nDecisions"},
                      follow_redirects=False).status_code == 303


def test_meeting_workflow(admin, db):
    r = admin.post("/transformation/meetings/create",
                   data={"title": "Ops test meeting", "meeting_date": date.today().isoformat(),
                         "attendees": "CEO, HOD Finance", "agenda": "Item one\nItem two",
                         "status": "scheduled"}, follow_redirects=False)
    assert r.status_code == 303
    meeting_id = int(r.headers["location"].rsplit("/", 1)[1])
    assert admin.get(f"/transformation/meetings/{meeting_id}").status_code == 200
    assert admin.post(f"/transformation/meetings/{meeting_id}/edit",
                      data={"title": "Ops test meeting", "meeting_date": date.today().isoformat(),
                            "status": "held", "notes": "Minutes recorded by the test suite.",
                            "agenda": "Item one", "attendees": "CEO"},
                      follow_redirects=False).status_code == 303
    owner = db.query(User).filter(User.is_superuser.is_(True)).first()
    assert admin.post(f"/transformation/meetings/{meeting_id}/decision",
                      data={"title": "Ops test meeting decision", "owner_id": str(owner.id),
                            "category": "operational",
                            "rationale": "Recorded by the automated test to prove the workflow."},
                      follow_redirects=False).status_code == 303
    assert admin.post(f"/transformation/meetings/{meeting_id}/action",
                      data={"title": "Ops test meeting action", "assignee_id": str(owner.id),
                            "priority": "high"}, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.query(Decision).filter(Decision.meeting_id == meeting_id).count() >= 1
    assert db.query(Task).filter(Task.entity_type == "TrajectoryMeeting",
                                 Task.entity_id == meeting_id).count() >= 1


def test_decision_workflow(admin, db):
    owner = db.query(User).filter(User.is_superuser.is_(True)).first()
    r = admin.post("/decisions/create",
                   data={"title": "Ops test decision", "owner_id": str(owner.id), "category": "operational",
                         "status": "decided", "due_date": date.today().isoformat(),
                         "rationale": "A written rationale long enough to satisfy the rule."},
                   follow_redirects=False)
    assert r.status_code == 303
    decision_id = int(r.headers["location"].rsplit("/", 1)[1])
    assert admin.post(f"/decisions/{decision_id}/outcome",
                      data={"outcome": "Outcome recorded by the test."}, follow_redirects=False).status_code == 303
    assert admin.post(f"/decisions/{decision_id}/status",
                      data={"status": "implemented"}, follow_redirects=False).status_code == 303
    assert admin.post(f"/decisions/{decision_id}/reverse",
                      data={"rationale": "Reversed by the automated test with a full reason."},
                      follow_redirects=False).status_code == 303
    db.expire_all()
    d = db.query(Decision).filter(Decision.id == decision_id).first()
    assert d.status == "reversed" and "Reversed on" in (d.outcome or "")


def test_decision_requires_owner_and_rationale(admin, db):
    before = db.query(Decision).count()
    admin.post("/decisions/create", data={"title": "No owner", "rationale": "long enough rationale here"},
               follow_redirects=False)
    admin.post("/decisions/create", data={"title": "No rationale", "owner_id": "1", "rationale": "short"},
               follow_redirects=False)
    db.expire_all()
    assert db.query(Decision).count() == before


def test_reversal_requires_rationale(admin, db):
    d = db.query(Decision).filter(Decision.status == "decided").order_by(Decision.id).first()
    r = admin.post(f"/decisions/{d.id}/reverse", data={"rationale": "no"}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.query(Decision).filter(Decision.id == d.id).first().status == "decided"


def test_daily_report_submit_and_lateness(admin, db):
    from app.services import jobs_ops
    today = jobs_ops.org_now().date()
    r = admin.post("/daily-reports",
                   data={}, follow_redirects=False)  # GET-only endpoint must not accept a bare POST
    assert r.status_code in (405, 303)
    r = admin.post("/daily-reports/submit",
                   data={"slot": "morning", "report_date": today.isoformat(),
                         "priorities": "1. Run the ops test suite", "capacity": "Full day",
                         "blockers": "None"}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    admin_user = db.query(User).filter(User.email == "admin@oqc.local").first()
    rec = (db.query(DailyReport).filter(DailyReport.user_id == admin_user.id,
                                        DailyReport.report_date == today,
                                        DailyReport.slot == "morning").first())
    assert rec is not None and rec.content.get("priorities")
    deadline = jobs_ops.deadline_time(db, "morning")
    assert rec.is_late == (jobs_ops.org_now().time() > deadline)
    empty = admin.post("/daily-reports/submit", data={"slot": "afternoon"}, follow_redirects=False)
    assert empty.status_code == 303   # rejected with a flash, not a crash


def test_daily_report_csv_export(admin):
    r = admin.get("/daily-reports/export?scope=team", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")


def test_report_xlsx_export_writes_a_file(admin, db):
    end = date.today()
    start = end - timedelta(days=30)
    r = admin.get(f"/reports/teacher_performance/export?format=xlsx&start={start}&end={end}",
                  follow_redirects=False)
    assert r.status_code == 200
    assert len(r.content) > 1000
    db.expire_all()
    run = (db.query(ReportRun).filter(ReportRun.report_type == "teacher_performance", ReportRun.format == "xlsx")
           .order_by(ReportRun.id.desc()).first())
    assert run is not None and run.file_path
    assert os.path.exists(run.file_path), run.file_path
    csv_run = admin.get(f"/reports/invoices/export?format=csv&start={start}&end={end}", follow_redirects=False)
    assert csv_run.status_code == 200


def test_export_is_audit_logged(admin, db):
    from app.models.core import AuditEvent
    before = db.query(AuditEvent).filter(AuditEvent.module == "reports", AuditEvent.action == "export").count()
    admin.get("/reports/aging/export?format=csv", follow_redirects=False)
    db.expire_all()
    after = db.query(AuditEvent).filter(AuditEvent.module == "reports", AuditEvent.action == "export").count()
    assert after == before + 1


def test_ai_review_and_policy(admin, db):
    run = db.query(AIModelRun).filter(AIModelRun.review_status == "pending").order_by(AIModelRun.id).first()
    if run is None:
        run = db.query(AIModelRun).order_by(AIModelRun.id).first()
    assert run is not None
    r = admin.post(f"/ai-governance/{run.id}/review",
                   data={"status": "approved", "note": "Reviewed by the automated test."},
                   follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    refreshed = db.query(AIModelRun).filter(AIModelRun.id == run.id).first()
    assert refreshed.review_status == "approved" and refreshed.reviewed_by_id is not None
    bad = admin.post(f"/ai-governance/{run.id}/review", data={"status": "rejected"}, follow_redirects=False)
    assert bad.status_code == 303          # rejection without a note is refused
    db.expire_all()
    assert db.query(AIModelRun).filter(AIModelRun.id == run.id).first().review_status == "approved"
    assert admin.post("/ai-governance/policy",
                      data={"threshold_class_monitoring": "0.85", "rationale": "tightened after review"},
                      follow_redirects=False).status_code == 303
    db.expire_all()
    setting = db.query(Setting).filter(Setting.key == "ai_thresholds").first()
    assert float(setting.value["class_monitoring"]) == 0.85


# --------------------------------------------------------------------------- permissions / scoping
def test_manager_pages(manager, db):
    for url in ("/tasks", "/kpis", "/kpis/scorecards", "/kpis/roles", "/decisions", "/decisions/gaps",
                "/daily-reports", "/daily-reports/team", "/reports", "/reports/runs"):
        assert manager.get(url).status_code == 200, url
    for url in ("/transformation", "/ai-governance"):
        assert manager.get(url).status_code == 403, url


def test_manager_sees_no_ceo_only_alerts(manager, db):
    secrets = [a.title for a in db.query(RiskAlert).filter(RiskAlert.visibility == "ceo_only")]
    if not secrets:
        pytest.skip("no ceo_only alerts in the seeded data")
    for url in ("/tasks", "/kpis", "/decisions", "/daily-reports", "/reports"):
        body = manager.get(url).text
        for title in secrets:
            assert title not in body, f"{title} leaked on {url}"


def test_teacher_scope(db):
    c = _client("teacher1@oqc.local", "Teacher@123")
    r = c.get("/tasks")
    assert r.status_code == 200
    teacher_user = db.query(User).filter(User.email == "teacher1@oqc.local").first()
    other = (db.query(Task).filter(Task.assignee_id != teacher_user.id, Task.creator_id != teacher_user.id)
             .order_by(Task.id).first())
    if other is not None:
        assert other.title not in r.text
        assert c.get(f"/tasks/{other.id}").status_code == 403
    assert c.get("/daily-reports").status_code == 200
    assert c.get("/daily-reports/history").status_code == 200
    assert c.get("/daily-reports/team").status_code == 403
    assert c.get("/reports").status_code == 403
    assert c.get("/decisions").status_code == 403
    today = date.today()
    assert c.post("/daily-reports/submit",
                  data={"slot": "afternoon", "report_date": today.isoformat(),
                        "completed": "Delivered six classes.", "missed": "Nothing.",
                        "tomorrow": "Prepare the monthly test."}, follow_redirects=False).status_code == 303


# --------------------------------------------------------------------------- API
def test_api_surface(admin, db):
    assert admin.get("/api/v1/ops/tasks?limit=5").status_code == 200
    assert admin.get("/api/v1/ops/projects").status_code == 200
    assert admin.get("/api/v1/ops/kpis").status_code == 200
    assert admin.get("/api/v1/ops/decisions?limit=5").status_code == 200
    assert admin.get("/api/v1/ops/daily-reports").status_code == 200
    kpi = db.query(KPI).order_by(KPI.id).first()
    assert admin.get(f"/api/v1/ops/kpis/{kpi.code}").status_code == 200
    assert admin.get(f"/api/v1/ops/kpis/{kpi.code}/history").status_code == 200
    assert admin.get("/api/v1/ops/kpis/not_a_kpi").status_code == 404


def test_api_task_crud(admin):
    r = admin.post("/api/v1/ops/tasks", json={"title": "API ops test task", "priority": "medium"})
    assert r.status_code == 201
    task_id = r.json()["id"]
    assert admin.get(f"/api/v1/ops/tasks/{task_id}").status_code == 200
    assert admin.patch(f"/api/v1/ops/tasks/{task_id}", json={"status": "in_progress"}).json()["status"] == "in_progress"
    assert admin.post(f"/api/v1/ops/tasks/{task_id}/complete?assessment_score=5").json()["status"] == "done"
    assert admin.get("/api/v1/ops/tasks/999999").status_code == 404


def test_api_decision_rules(admin, db):
    owner = db.query(User).filter(User.is_superuser.is_(True)).first()
    bad = admin.post("/api/v1/ops/decisions", json={"title": "x", "rationale": "short", "owner_id": owner.id})
    assert bad.status_code == 422
    r = admin.post("/api/v1/ops/decisions",
                   json={"title": "API ops decision", "rationale": "A rationale long enough to be accepted.",
                         "owner_id": owner.id})
    assert r.status_code == 201
    decision_id = r.json()["id"]
    no_reason = admin.post(f"/api/v1/ops/decisions/{decision_id}/status", json={"status": "reversed"})
    assert no_reason.status_code == 422
    ok = admin.post(f"/api/v1/ops/decisions/{decision_id}/status",
                    json={"status": "reversed", "rationale": "Reversed with a written reason."})
    assert ok.status_code == 200 and ok.json()["status"] == "reversed"


def test_api_daily_report(admin):
    r = admin.post("/api/v1/ops/daily-reports",
                   json={"slot": "morning", "content": {"priorities": "API submitted report"}})
    assert r.status_code == 201
    assert r.json()["content"]["priorities"] == "API submitted report"
    assert admin.post("/api/v1/ops/daily-reports", json={"slot": "morning", "content": {}}).status_code == 422


def test_api_command_center(admin):
    r = admin.get("/api/v1/ops/command-center")
    assert r.status_code == 200
    data = r.json()
    for key in ("period", "health", "metrics", "scorecards", "tasks", "decisions", "transformation",
                "daily_reports", "series", "alerts", "anomalies"):
        assert key in data, key
    assert data["health"]["score"] is not None
    assert isinstance(data["scorecards"], list) and data["scorecards"]
    assert data["tasks"]["open"] >= 0
