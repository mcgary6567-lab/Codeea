"""KPI engine (Module 26 / Section 17 of the SRS).

* Period helpers (``resolve_period``, ``period_bounds``, ``last_n_periods``)
* ``executive_metrics(db, start, end)`` – every institutional number the CEO Command Center needs, computed
  straight from the models (all tables may be empty – every value degrades to 0 / None gracefully).
* ``health_score(metrics)`` – 0-100 composite of academic / finance / people / quality / growth sub-scores.
* Formula registry: ``compute_kpi(db, kpi, period, entity=None)`` implements every ``formula_key`` in the
  seeded KPI catalogue; ``snapshot_kpis(db, period)`` stores ``KPIValue`` rows (system source).
* Department scorecard metrics + RAG helpers.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.core import Department, Integration, SecurityIncident, User, WebhookDelivery
from app.models.people import (Student, Teacher, Employee, HRAttendance, Candidate, OnboardingTask, Payslip, PayrollRun,
                               Client)
from app.models.academic import StudentProgress, Evaluation, LessonPlan, MonthlyTest
from app.models.scheduling import ClassSession, QAReview, AIClassAnalysis, CorrectiveAction, Trial
from app.models.crm import Lead, Campaign, CampaignMetric, Case, Feedback, Referral
from app.models.finance import Payment, Invoice, Subscription, Expense, Currency
from app.models.ops import KPI, KPIValue, Task, DepartmentScorecard

PERIOD_CHOICES = [("this_month", "This month"), ("last_month", "Last month"), ("quarter", "This quarter"),
                  ("ytd", "Year to date"), ("custom", "Custom range")]

# --------------------------------------------------------------------------------------------------- periods


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def add_months(d: date, n: int) -> date:
    y, m = d.year, d.month + n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def period_bounds(period: str) -> tuple[date, date]:
    """``YYYY-MM`` -> (first day, last day). Also accepts ``YYYY-MM-DD`` (single day)."""
    parts = period.split("-")
    if len(parts) >= 3:
        d = date(int(parts[0]), int(parts[1]), int(parts[2]))
        return d, d
    y, m = int(parts[0]), int(parts[1])
    s = date(y, m, 1)
    return s, month_end(s)


def period_key(d: Optional[date] = None) -> str:
    return (d or date.today()).strftime("%Y-%m")


def last_n_periods(n: int, end: Optional[date] = None) -> list[str]:
    end = month_start(end or date.today())
    return [period_key(add_months(end, -i)) for i in range(n - 1, -1, -1)]


def period_label(p: str) -> str:
    try:
        s, _ = period_bounds(p)
        return s.strftime("%b %Y")
    except Exception:
        return p


@dataclass
class Period:
    key: str
    start: date
    end: date
    label: str

    @property
    def month(self) -> str:
        return period_key(self.end)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> "Period":
        if self.key in ("this_month", "last_month") or (self.start.day == 1 and self.end == month_end(self.start)):
            ps = add_months(self.start, -1)
            return Period("custom", ps, month_end(ps), ps.strftime("%b %Y"))
        n = self.days
        pe = self.start - timedelta(days=1)
        ps = pe - timedelta(days=n - 1)
        return Period("custom", ps, pe, f"{ps:%d %b} - {pe:%d %b %Y}")

    @property
    def qs(self) -> str:
        if self.key == "custom":
            return f"period=custom&start={self.start.isoformat()}&end={self.end.isoformat()}"
        return f"period={self.key}"


def resolve_period(name: str = "this_month", start: Optional[date] = None, end: Optional[date] = None,
                   today: Optional[date] = None) -> Period:
    today = today or date.today()
    name = name or "this_month"
    if name == "last_month":
        s = add_months(month_start(today), -1)
        return Period(name, s, month_end(s), s.strftime("%B %Y"))
    if name == "quarter":
        qm = (today.month - 1) // 3 * 3 + 1
        s = date(today.year, qm, 1)
        e = month_end(add_months(s, 2))
        return Period(name, s, e, f"Q{(today.month - 1) // 3 + 1} {today.year}")
    if name == "ytd":
        s = date(today.year, 1, 1)
        return Period(name, s, today, f"YTD {today.year}")
    if name == "custom" and start and end:
        if end < start:
            start, end = end, start
        return Period(name, start, end, f"{start:%d %b %Y} - {end:%d %b %Y}")
    if len(name) == 7 and name[4] == "-":
        try:
            s, e = period_bounds(name)
            return Period(name, s, e, s.strftime("%B %Y"))
        except ValueError:
            pass
    s = month_start(today)
    return Period("this_month", s, month_end(s), s.strftime("%B %Y"))


def dt_range(s: date, e: date) -> tuple[datetime, datetime]:
    return datetime.combine(s, time.min), datetime.combine(e, time.max)


def working_days(s: date, e: date) -> int:
    n, d = 0, s
    while d <= e:
        if d.weekday() < 6:
            n += 1
        d += timedelta(days=1)
    return max(1, n)


def pct(part: float, whole: float, digits: int = 1) -> float:
    return round(100.0 * part / whole, digits) if whole else 0.0


def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _empty(db: Session, model) -> bool:
    return db.query(model.id).first() is None


def rates_map(db: Session) -> dict[str, float]:
    return {c.code: _f(c.rate_to_base) or 1.0 for c in db.query(Currency)}


def to_base(amount, currency: str, rates: dict[str, float]) -> float:
    return _f(amount) * rates.get(currency or "", 1.0)


# --------------------------------------------------------------------------------------------------- building blocks

def revenue(db: Session, s: date, e: date) -> float:
    a, b = dt_range(s, e)
    return _f(db.query(func.sum(Payment.amount_in_base)).filter(Payment.status == "completed", Payment.received_at >= a, Payment.received_at <= b).scalar())


def expenses(db: Session, s: date, e: date) -> float:
    return _f(db.query(func.sum(Expense.amount_in_base)).filter(Expense.status.in_(["approved", "paid"]), Expense.expense_date >= s, Expense.expense_date <= e).scalar())


def payroll(db: Session, s: date, e: date) -> float:
    months = set()
    d = month_start(s)
    while d <= e:
        months.add(period_key(d))
        d = add_months(d, 1)
    return _f(db.query(func.sum(Payslip.net)).join(PayrollRun, PayrollRun.id == Payslip.payroll_run_id).filter(PayrollRun.period.in_(list(months))).scalar())


def mrr(db: Session) -> float:
    return _f(db.query(func.sum(Subscription.price_in_base)).filter(Subscription.status == "active").scalar())


def active_students(db: Session, at: Optional[date] = None) -> int:
    if at is None or at >= date.today():
        return db.query(Student).filter(Student.status == "active").count()
    return db.query(Student).filter(Student.status.in_(["active", "frozen", "cancelled", "graduated"]), Student.join_date <= at,
                                    or_(Student.cancelled_at.is_(None), Student.cancelled_at > at)).count()


def churn(db: Session, s: date, e: date) -> dict:
    cancelled = db.query(Student).filter(Student.cancelled_at >= s, Student.cancelled_at <= e).count()
    base = db.query(Student).filter(Student.status.in_(["active", "frozen", "cancelled", "graduated"]), Student.join_date < s,
                                    or_(Student.cancelled_at.is_(None), Student.cancelled_at >= s)).count()
    return {"cancelled": cancelled, "base": base, "rate": pct(cancelled, base)}


def session_counts(db: Session, s: date, e: date, teacher_id: Optional[int] = None) -> dict:
    q = db.query(ClassSession.status, func.count(ClassSession.id)).filter(ClassSession.date >= s, ClassSession.date <= e)
    if teacher_id:
        q = q.filter(ClassSession.teacher_id == teacher_id)
    counts = {st: n for st, n in q.group_by(ClassSession.status)}
    terminal = sum(counts.get(k, 0) for k in ("done", "missed", "absent", "leave", "cancelled", "rescheduled"))
    done = counts.get("done", 0)
    missed = counts.get("missed", 0)
    lateq = db.query(func.count(ClassSession.id)).filter(ClassSession.date >= s, ClassSession.date <= e, ClassSession.status == "done", ClassSession.teacher_late_minutes > 5)
    if teacher_id:
        lateq = lateq.filter(ClassSession.teacher_id == teacher_id)
    late = lateq.scalar() or 0
    return {"counts": counts, "total": sum(counts.values()), "terminal": terminal, "done": done, "missed": missed, "late": late,
            "completion": pct(done, terminal), "missed_rate": pct(missed, terminal), "punctuality": round(100 * (done - late) / done, 1) if done else None}


def teacher_utilization(db: Session, s: date, e: date, teacher_id: Optional[int] = None) -> Optional[float]:
    q = db.query(Teacher).filter(Teacher.status == "active")
    if teacher_id:
        q = q.filter(Teacher.id == teacher_id)
    teachers = q.all()
    if not teachers:
        return None
    capacity = sum(t.max_classes_per_day or 12 for t in teachers) * working_days(s, e)
    done = session_counts(db, s, e, teacher_id)["done"]
    return round(100 * done / capacity, 1) if capacity else None


def qa_avg(db: Session, s: date, e: date, teacher_id: Optional[int] = None) -> Optional[float]:
    a, b = dt_range(s, e)
    q = db.query(func.avg(QAReview.overall_score)).filter(QAReview.overall_score.isnot(None), QAReview.created_at >= a, QAReview.created_at <= b)
    if teacher_id:
        q = q.filter(QAReview.teacher_id == teacher_id)
    v = q.scalar()
    return round(float(v), 1) if v is not None else None


def ai_avg(db: Session, s: date, e: date, teacher_id: Optional[int] = None) -> Optional[float]:
    a, b = dt_range(s, e)
    q = db.query(func.avg(AIClassAnalysis.overall_score)).filter(AIClassAnalysis.created_at >= a, AIClassAnalysis.created_at <= b)
    if teacher_id:
        q = q.filter(AIClassAnalysis.teacher_id == teacher_id)
    v = q.scalar()
    return round(float(v), 1) if v is not None else None


def nps(db: Session, s: date, e: date, respondent_type: Optional[str] = None, teacher_id: Optional[int] = None) -> dict:
    a, b = dt_range(s, e)
    q = db.query(Feedback.nps, Feedback.rating).filter(Feedback.created_at >= a, Feedback.created_at <= b)
    if respondent_type == "staff":
        q = q.filter(Feedback.respondent_type == "staff")
    elif respondent_type == "customer":
        q = q.filter(Feedback.respondent_type.in_(["client", "student"]))
    if teacher_id:
        q = q.filter(Feedback.teacher_id == teacher_id)
    rows = q.all()
    scores = [n for n, _ in rows if n is not None]
    ratings = [r for _, r in rows if r is not None]
    if not scores:
        return {"nps": None, "responses": len(rows), "rating": round(sum(ratings) / len(ratings), 2) if ratings else None}
    prom = sum(1 for n in scores if n >= 9)
    det = sum(1 for n in scores if n <= 6)
    return {"nps": round(100 * (prom - det) / len(scores), 1), "responses": len(scores), "promoters": prom, "detractors": det,
            "rating": round(sum(ratings) / len(ratings), 2) if ratings else None}


def lead_metrics(db: Session, s: date, e: date) -> dict:
    a, b = dt_range(s, e)
    new = db.query(Lead).filter(Lead.created_at >= a, Lead.created_at <= b).count()
    won = db.query(Lead).filter(Lead.converted_at >= a, Lead.converted_at <= b).count()
    if not won:
        won = db.query(Lead).filter(Lead.stage == "won", Lead.updated_at >= a, Lead.updated_at <= b).count()
    contacted = db.query(Lead).filter(Lead.created_at >= a, Lead.created_at <= b, Lead.stage.in_(["contacted", "trial_scheduled", "trial_done", "negotiation", "won"])).count()
    trial_stage = db.query(Lead).filter(Lead.created_at >= a, Lead.created_at <= b, Lead.stage.in_(["trial_scheduled", "trial_done", "negotiation", "won"])).count()
    trial_done_stage = db.query(Lead).filter(Lead.created_at >= a, Lead.created_at <= b, Lead.stage.in_(["trial_done", "negotiation", "won"])).count()
    won_cohort = db.query(Lead).filter(Lead.created_at >= a, Lead.created_at <= b, Lead.stage == "won").count()
    trials_q = db.query(Trial).filter(Trial.scheduled_at >= a, Trial.scheduled_at <= b)
    trials = trials_q.count()
    attended = trials_q.filter(Trial.status.in_(["attended", "converted"])).count()
    converted = db.query(Trial).filter(Trial.scheduled_at >= a, Trial.scheduled_at <= b, Trial.status == "converted").count()
    decided = db.query(Trial).filter(Trial.scheduled_at >= a, Trial.scheduled_at <= b, Trial.status.in_(["attended", "converted", "no_show", "lost"])).count()
    return {"new": new, "won": won, "contacted": contacted, "trial_stage": trial_stage, "trial_done_stage": trial_done_stage, "won_cohort": won_cohort,
            "conversion": pct(won, new) if new else 0.0, "trials": trials, "trials_attended": attended, "trials_converted": converted,
            "trial_conversion": pct(converted, decided) if decided else 0.0, "trial_attendance": pct(attended, trials) if trials else 0.0,
            "has_data": not _empty(db, Lead)}


def marketing_metrics(db: Session, s: date, e: date) -> dict:
    rates = rates_map(db)
    a, b = dt_range(s, e)
    spend = 0.0
    rows = db.query(CampaignMetric, Campaign).join(Campaign, Campaign.id == CampaignMetric.campaign_id).filter(CampaignMetric.date >= s, CampaignMetric.date <= e).all()
    attributed = 0.0
    if rows:
        for m, c in rows:
            spend += to_base(m.spend, c.currency, rates)
            attributed += to_base(m.revenue, c.currency, rates)
    else:
        for c in db.query(Campaign).filter(or_(Campaign.start_date.is_(None), Campaign.start_date <= e), or_(Campaign.end_date.is_(None), Campaign.end_date >= s)):
            # prorate the campaign spend over the overlap with the period
            cs, ce = c.start_date or s, c.end_date or e
            total_days = max(1, (ce - cs).days + 1)
            overlap = max(0, (min(ce, e) - max(cs, s)).days + 1)
            spend += to_base(c.spend, c.currency, rates) * overlap / total_days
    leads = lead_metrics(db, s, e)
    if not attributed:
        # revenue from clients acquired in the period
        attributed = _f(db.query(func.sum(Payment.amount_in_base)).join(Client, Client.id == Payment.client_id)
                        .filter(Payment.status == "completed", Client.joined_at >= s, Client.joined_at <= e).scalar())
    return {"spend": round(spend, 2), "attributed_revenue": round(attributed, 2), "cpl": round(spend / leads["new"], 2) if leads["new"] else None,
            "cac": round(spend / leads["won"], 2) if leads["won"] else None,
            "roi": round(100 * (attributed - spend) / spend, 1) if spend else None}


def receivables(db: Session) -> dict:
    rates = rates_map(db)
    total, count, overdue = 0.0, 0, 0
    for inv in db.query(Invoice).filter(Invoice.status.in_(["sent", "partial", "overdue"])):
        bal = _f(inv.total) - _f(inv.paid_amount)
        if bal > 0:
            total += bal * rates.get(inv.currency, 1.0)
            count += 1
            if inv.status == "overdue" or (inv.due_date and inv.due_date < date.today()):
                overdue += 1
    return {"amount": round(total, 2), "count": count, "overdue": overdue}


def collection_rate(db: Session, s: date, e: date) -> Optional[float]:
    rates = rates_map(db)
    billed, collected = 0.0, 0.0
    for inv in db.query(Invoice).filter(Invoice.issue_date >= s, Invoice.issue_date <= e, Invoice.status.notin_(["draft", "void"])):
        r = rates.get(inv.currency, 1.0)
        billed += _f(inv.total) * r
        collected += min(_f(inv.paid_amount), _f(inv.total)) * r
    return round(100 * collected / billed, 1) if billed else None


def case_metrics(db: Session, s: date, e: date, teacher_id: Optional[int] = None, department_id: Optional[int] = None) -> dict:
    a, b = dt_range(s, e)
    base = db.query(Case)
    if teacher_id:
        base = base.filter(Case.teacher_id == teacher_id)
    if department_id:
        base = base.filter(Case.department_id == department_id)
    open_q = base.filter(Case.status.in_(["open", "in_progress", "waiting", "escalated"]))
    opened = base.filter(Case.created_at >= a, Case.created_at <= b)
    complaints = opened.filter(Case.case_type == "complaint").count()
    resolved = base.filter(Case.resolved_at >= a, Case.resolved_at <= b).all()
    within = sum(1 for c in resolved if not c.sla_breached and (c.sla_due_at is None or (c.resolved_at and c.resolved_at <= c.sla_due_at)))
    return {"open": open_q.count(), "opened": opened.count(), "complaints": complaints, "resolved": len(resolved),
            "sla_compliance": round(100 * within / len(resolved), 1) if resolved else None,
            "breached_open": open_q.filter(Case.sla_breached.is_(True)).count(), "has_data": not _empty(db, Case)}


def high_risk_students(db: Session) -> int:
    return db.query(Student).filter(Student.risk_level == "high", Student.status.in_(["active", "trial", "frozen"])).count()


def referral_share(db: Session, s: date, e: date) -> dict:
    adds = db.query(Student).filter(Student.join_date >= s, Student.join_date <= e).all()
    referred_clients = {r[0] for r in db.query(Referral.referred_client_id).filter(Referral.referred_client_id.isnot(None))}
    referred = sum(1 for st in adds if st.client_id in referred_clients or (st.client and (st.client.source or "").lower() == "referral"))
    return {"gross_adds": len(adds), "referred": referred, "pct": pct(referred, len(adds)) if adds else 0.0}


def hr_attendance_pct(db: Session, s: date, e: date, department_id: Optional[int] = None) -> Optional[float]:
    q = db.query(HRAttendance.status, func.count(HRAttendance.id)).filter(HRAttendance.date >= s, HRAttendance.date <= e)
    if department_id:
        q = q.join(Employee, Employee.id == HRAttendance.employee_id).filter(Employee.department_id == department_id)
    counts = dict(q.group_by(HRAttendance.status).all())
    total = sum(v for k, v in counts.items() if k != "holiday")
    if not total:
        return None
    present = counts.get("present", 0) + counts.get("late", 0) + counts.get("half_day", 0) * 0.5
    return round(100 * present / total, 1)


# --------------------------------------------------------------------------------------------------- executive metrics

def executive_metrics(db: Session, start: date, end: date, with_previous: bool = True) -> dict:
    """Every number on the CEO cockpit for one period. Safe on empty tables."""
    rev = revenue(db, start, end)
    exp = expenses(db, start, end)
    pay = payroll(db, start, end)
    sess = session_counts(db, start, end)
    ch = churn(db, start, end)
    leads = lead_metrics(db, start, end)
    mk = marketing_metrics(db, start, end)
    rec = receivables(db)
    cases = case_metrics(db, start, end)
    cust = nps(db, start, end, "customer")
    staff = nps(db, start, end, "staff")
    ref = referral_share(db, start, end)
    m = {
        "start": start, "end": end,
        "revenue": round(rev, 2), "expenses": round(exp, 2), "payroll": round(pay, 2), "net": round(rev - exp - pay, 2),
        "mrr": round(mrr(db), 2), "active_subscriptions": db.query(Subscription).filter(Subscription.status == "active").count(),
        "active_students": active_students(db), "trial_students": db.query(Student).filter(Student.status == "trial").count(),
        "new_students": db.query(Student).filter(Student.join_date >= start, Student.join_date <= end).count(),
        "churn_rate": ch["rate"], "churned": ch["cancelled"], "churn_base": ch["base"],
        "new_leads": leads["new"], "won_leads": leads["won"], "lead_conversion": leads["conversion"],
        "trials": leads["trials"], "trials_attended": leads["trials_attended"], "trials_converted": leads["trials_converted"],
        "trial_conversion": leads["trial_conversion"], "trial_attendance": leads["trial_attendance"],
        "funnel": {"Leads": leads["new"], "Contacted": leads["contacted"], "Trial booked": leads["trial_stage"], "Trial done": leads["trial_done_stage"], "Won": leads["won_cohort"]},
        "sessions_total": sess["terminal"], "sessions_done": sess["done"], "sessions_missed": sess["missed"], "sessions_late": sess["late"],
        "class_completion": sess["completion"], "missed_rate": sess["missed_rate"], "punctuality": sess["punctuality"],
        "session_status_counts": sess["counts"],
        "teacher_utilization": teacher_utilization(db, start, end), "active_teachers": db.query(Teacher).filter(Teacher.status == "active").count(),
        "qa_avg": qa_avg(db, start, end), "qa_reviews": db.query(QAReview).filter(QAReview.created_at >= dt_range(start, end)[0], QAReview.created_at <= dt_range(start, end)[1]).count(),
        "ai_avg": ai_avg(db, start, end),
        "nps": cust["nps"], "nps_responses": cust["responses"], "satisfaction_rating": cust["rating"],
        "enps": staff["nps"], "enps_responses": staff["responses"],
        "payroll_ratio": round(100 * pay / rev, 1) if rev else None,
        "marketing_spend": mk["spend"], "attributed_revenue": mk["attributed_revenue"], "cpl": mk["cpl"], "cac": mk["cac"], "roi": mk["roi"],
        "receivables": rec["amount"], "receivables_count": rec["count"], "overdue_invoices": rec["overdue"],
        "collection_rate": collection_rate(db, start, end),
        "open_cases": cases["open"], "cases_opened": cases["opened"], "complaints": cases["complaints"], "sla_compliance": cases["sla_compliance"],
        "sla_breached_open": cases["breached_open"],
        "high_risk_students": high_risk_students(db),
        "referral_pct": ref["pct"], "gross_adds": ref["gross_adds"], "referred_adds": ref["referred"],
        "hr_attendance": hr_attendance_pct(db, start, end),
        "employees": db.query(Employee).filter(Employee.status.in_(["active", "probation"])).count(),
        "open_tasks": db.query(Task).filter(Task.status.in_(["todo", "in_progress", "review"])).count(),
        "overdue_tasks": db.query(Task).filter(Task.status.in_(["todo", "in_progress", "review"]), Task.due_date < date.today()).count(),
    }
    if with_previous:
        p = Period("custom", start, end, "").previous()
        prev = executive_metrics(db, p.start, p.end, with_previous=False)
        m["previous"] = prev
        m["deltas"] = {k: _delta(m.get(k), prev.get(k)) for k in ("revenue", "expenses", "active_students", "new_leads", "mrr", "sessions_done", "churn_rate", "missed_rate", "complaints")}
    return m


def _delta(cur, prev) -> Optional[float]:
    try:
        if prev in (None, 0) or cur is None:
            return None
        return round(100.0 * (float(cur) - float(prev)) / abs(float(prev)), 1)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------------------------------- health score

HEALTH_WEIGHTS = {"academic": 0.25, "finance": 0.25, "people": 0.15, "quality": 0.20, "growth": 0.15}


def _avg(parts: list[Optional[float]]) -> Optional[float]:
    vals = [p for p in parts if p is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def health_score(m: dict) -> dict:
    """Composite Institutional Health Score (0-100) with sub-scores and a plain-English explanation."""
    subs: dict[str, Optional[float]] = {}
    notes: dict[str, list[str]] = {k: [] for k in HEALTH_WEIGHTS}

    # academic: class completion + missed-rate + utilisation
    if m.get("sessions_total"):
        comp = clamp(m["class_completion"])
        miss = clamp(100 - m["missed_rate"] * 8)
        util = clamp((m.get("teacher_utilization") or 0) / 70 * 100) if m.get("teacher_utilization") is not None else None
        subs["academic"] = _avg([comp, comp, miss, util])
        notes["academic"].append(f"Class completion {m['class_completion']}% (target 95%)")
        notes["academic"].append(f"Missed-class rate {m['missed_rate']}% (target < 3%)")
        if util is not None:
            notes["academic"].append(f"Teacher utilisation {m['teacher_utilization']}% (target 70%)")
    else:
        subs["academic"] = None
        notes["academic"].append("No class sessions in this period")

    # finance: collection rate + payroll ratio + revenue growth
    parts = []
    if m.get("collection_rate") is not None:
        parts.append(clamp(m["collection_rate"]))
        notes["finance"].append(f"Collection rate {m['collection_rate']}% (target 90%)")
    if m.get("payroll_ratio") is not None:
        parts.append(clamp(100 - max(0.0, m["payroll_ratio"] - 40) * 2))
        notes["finance"].append(f"Payroll ratio {m['payroll_ratio']}% of revenue (target <= 40%)")
    d = (m.get("deltas") or {}).get("revenue")
    if d is not None:
        parts.append(clamp(50 + d))
        notes["finance"].append(f"Revenue {'+' if d >= 0 else ''}{d}% vs previous period")
    elif m.get("revenue"):
        parts.append(60.0)
        notes["finance"].append("Revenue recorded; no previous period to compare")
    subs["finance"] = _avg(parts)
    if not parts:
        notes["finance"].append("No invoices, payments or payroll in this period")

    # people: utilisation, HR attendance, eNPS
    parts = []
    if m.get("teacher_utilization") is not None:
        parts.append(clamp(m["teacher_utilization"] / 70 * 100))
    if m.get("hr_attendance") is not None:
        parts.append(clamp(m["hr_attendance"]))
        notes["people"].append(f"Staff attendance {m['hr_attendance']}% (target 95%)")
    if m.get("enps") is not None:
        parts.append(clamp((m["enps"] + 100) / 2))
        notes["people"].append(f"eNPS {m['enps']} from {m.get('enps_responses', 0)} staff responses")
    subs["people"] = _avg(parts)
    if not parts:
        notes["people"].append("No attendance or staff-sentiment data yet")

    # quality: QA, AI, complaints per 100 students
    parts = []
    if m.get("qa_avg") is not None:
        parts.append(clamp(m["qa_avg"]))
        notes["quality"].append(f"QA average {m['qa_avg']} (target 80)")
    if m.get("ai_avg") is not None:
        parts.append(clamp(m["ai_avg"]))
        notes["quality"].append(f"AI class-monitoring average {m['ai_avg']} (target 80)")
    if m.get("active_students"):
        per100 = 100.0 * (m.get("complaints") or 0) / m["active_students"]
        parts.append(clamp(100 - per100 * 10))
        notes["quality"].append(f"{m.get('complaints', 0)} complaints ({per100:.1f} per 100 students)")
    subs["quality"] = _avg(parts)
    if not parts:
        notes["quality"].append("No QA reviews, AI analyses or complaints recorded")

    # growth: lead conversion, trial conversion, churn, NPS
    parts = []
    if m.get("new_leads"):
        parts.append(clamp(m["lead_conversion"] / 25 * 100))
        notes["growth"].append(f"Lead conversion {m['lead_conversion']}% (target 25%)")
    if m.get("trials"):
        parts.append(clamp(m["trial_conversion"] / 40 * 100))
        notes["growth"].append(f"Trial conversion {m['trial_conversion']}% (target 40%)")
    if m.get("churn_base"):
        parts.append(clamp(100 - m["churn_rate"] * 10))
        notes["growth"].append(f"Churn {m['churn_rate']}% (target < 3%)")
    if m.get("nps") is not None:
        parts.append(clamp((m["nps"] + 100) / 2))
        notes["growth"].append(f"NPS {m['nps']} from {m.get('nps_responses', 0)} responses")
    subs["growth"] = _avg(parts)
    if not parts:
        notes["growth"].append("No leads, trials or feedback in this period")

    weight_sum = sum(HEALTH_WEIGHTS[k] for k, v in subs.items() if v is not None)
    score = round(sum(HEALTH_WEIGHTS[k] * v for k, v in subs.items() if v is not None) / weight_sum, 1) if weight_sum else 0.0
    band, color = health_band(score)
    missing = [k for k, v in subs.items() if v is None]
    summary = f"Institutional Health Score {score}/100 - {band}."
    if missing:
        summary += " No data yet for: " + ", ".join(missing) + " (excluded from the weighting)."
    return {"score": score, "band": band, "color": color, "subs": subs, "notes": notes, "weights": HEALTH_WEIGHTS, "summary": summary,
            "sub_bands": {k: (health_band(v) if v is not None else ("No data", "slate")) for k, v in subs.items()}}


def health_band(score: Optional[float]) -> tuple[str, str]:
    if score is None:
        return "No data", "slate"
    if score >= 80:
        return "Healthy", "emerald"
    if score >= 65:
        return "Stable", "sky"
    if score >= 50:
        return "Watch", "amber"
    return "Critical", "rose"


# --------------------------------------------------------------------------------------------------- trend series

def monthly_series(db: Session, n: int = 6) -> dict:
    periods = last_n_periods(n)
    out = {"labels": [period_label(p) for p in periods], "periods": periods, "revenue": [], "expenses": [], "payroll": [], "students": [],
           "new_students": [], "cancelled": [], "leads": [], "sessions_done": [], "missed_rate": []}
    for p in periods:
        s, e = period_bounds(p)
        out["revenue"].append(round(revenue(db, s, e), 2))
        out["expenses"].append(round(expenses(db, s, e), 2))
        out["payroll"].append(round(payroll(db, s, e), 2))
        out["students"].append(active_students(db, e))
        out["new_students"].append(db.query(Student).filter(Student.join_date >= s, Student.join_date <= e).count())
        out["cancelled"].append(db.query(Student).filter(Student.cancelled_at >= s, Student.cancelled_at <= e).count())
        a, b = dt_range(s, e)
        out["leads"].append(db.query(Lead).filter(Lead.created_at >= a, Lead.created_at <= b).count())
        sc = session_counts(db, s, e)
        out["sessions_done"].append(sc["done"])
        out["missed_rate"].append(sc["missed_rate"])
    return out


# --------------------------------------------------------------------------------------------------- RAG / targets

def rag(value: Optional[float], target: Optional[float], direction: str = "higher") -> str:
    if value is None:
        return "grey"
    if target is None:
        return "blue"
    if direction == "lower":
        if value <= target:
            return "green"
        return "amber" if value <= target * 1.15 + 0.5 else "red"
    if value >= target:
        return "green"
    return "amber" if value >= target * 0.85 else "red"


RAG_COLORS = {"green": "emerald", "amber": "amber", "red": "rose", "grey": "slate", "blue": "sky"}
RAG_LABELS = {"green": "On target", "amber": "At risk", "red": "Off target", "grey": "No data", "blue": "Tracking"}


def rag_score(status: str) -> Optional[float]:
    return {"green": 100.0, "amber": 60.0, "red": 20.0}.get(status)


# --------------------------------------------------------------------------------------------------- formula registry

Formula = Callable[[Session, date, date, Optional[str], Optional[int]], Optional[float]]


def _teacher_of(entity_type, entity_id) -> Optional[int]:
    return entity_id if entity_type == "teacher" and entity_id else None


def _dept_of(entity_type, entity_id) -> Optional[int]:
    return entity_id if entity_type == "department" and entity_id else None


def _count_or_none(db, model, q_count: int) -> Optional[float]:
    return None if _empty(db, model) else float(q_count)


def f_classes_completed(db, s, e, et, eid):
    if _empty(db, ClassSession):
        return None
    return float(session_counts(db, s, e, _teacher_of(et, eid))["done"])


def f_classes_missed(db, s, e, et, eid):
    if _empty(db, ClassSession):
        return None
    return float(session_counts(db, s, e, _teacher_of(et, eid))["missed"])


def f_missed_rate(db, s, e, et, eid):
    sc = session_counts(db, s, e, _teacher_of(et, eid))
    return sc["missed_rate"] if sc["terminal"] else None


def f_completion(db, s, e, et, eid):
    sc = session_counts(db, s, e, _teacher_of(et, eid))
    return sc["completion"] if sc["terminal"] else None


def f_punctuality(db, s, e, et, eid):
    return session_counts(db, s, e, _teacher_of(et, eid))["punctuality"]


def f_qa(db, s, e, et, eid):
    return qa_avg(db, s, e, _teacher_of(et, eid))


def f_ai(db, s, e, et, eid):
    return ai_avg(db, s, e, _teacher_of(et, eid))


def f_complaints(db, s, e, et, eid):
    if _empty(db, Case):
        return None
    return float(case_metrics(db, s, e, teacher_id=_teacher_of(et, eid))["complaints"])


def f_feedback_rating(db, s, e, et, eid):
    return nps(db, s, e, "customer", _teacher_of(et, eid))["rating"]


def f_retention(db, s, e, et, eid):
    tid = _teacher_of(et, eid)
    q = db.query(Student).filter(Student.join_date < s, or_(Student.cancelled_at.is_(None), Student.cancelled_at >= s), Student.status.in_(["active", "frozen", "cancelled", "graduated"]))
    if tid:
        q = q.filter(Student.teacher_id == tid)
    base = q.count()
    if not base:
        return None
    lost = q.filter(Student.cancelled_at >= s, Student.cancelled_at <= e).count()
    return round(100 * (base - lost) / base, 1)


def f_utilization(db, s, e, et, eid):
    return teacher_utilization(db, s, e, _teacher_of(et, eid))


def f_coverage(db, s, e, et, eid):
    sc = session_counts(db, s, e)
    return round(100 - sc["missed_rate"], 1) if sc["terminal"] else None


def f_missed_response(db, s, e, et, eid):
    a, b = dt_range(s, e)
    rows = db.query(ClassSession.scheduled_start, ClassSession.status_changed_at).filter(ClassSession.status == "missed", ClassSession.date >= s, ClassSession.date <= e,
                                                                                        ClassSession.status_changed_at.isnot(None)).all()
    vals = [max(0.0, (c - st).total_seconds() / 60 - 5 * 60) for st, c in rows if c and st]  # org time offset tolerant
    vals = [min(v, 240) for v in vals]
    return round(sum(vals) / len(vals), 1) if vals else None


def f_sla(db, s, e, et, eid):
    return case_metrics(db, s, e, department_id=_dept_of(et, eid))["sla_compliance"]


def f_hr_attendance(db, s, e, et, eid):
    return hr_attendance_pct(db, s, e, _dept_of(et, eid))


def f_ops_attendance(db, s, e, et, eid):
    d = db.query(Department).filter(Department.code == "operations").first()
    return hr_attendance_pct(db, s, e, d.id if d else None)


def f_team_qa(db, s, e, et, eid):
    return qa_avg(db, s, e)


def f_retention_rate(db, s, e, et, eid):
    ch = churn(db, s, e)
    return round(100 - ch["rate"], 1) if ch["base"] else None


def f_churn(db, s, e, et, eid):
    ch = churn(db, s, e)
    return ch["rate"] if ch["base"] else None


def f_lead_conversion(db, s, e, et, eid):
    lm = lead_metrics(db, s, e)
    return lm["conversion"] if lm["new"] else None


def f_trial_conversion(db, s, e, et, eid):
    lm = lead_metrics(db, s, e)
    return lm["trial_conversion"] if lm["trials"] else None


def f_leads(db, s, e, et, eid):
    lm = lead_metrics(db, s, e)
    return float(lm["new"]) if lm["has_data"] else None


def f_tasks_done(db, s, e, et, eid):
    a, b = dt_range(s, e)
    q = db.query(Task).filter(Task.status == "done", Task.completed_at >= a, Task.completed_at <= b)
    did = _dept_of(et, eid)
    if did:
        q = q.filter(Task.department_id == did)
    return None if _empty(db, Task) else float(q.count())


def f_hiring_time(db, s, e, et, eid):
    a, b = dt_range(s, e)
    rows = db.query(Candidate, Employee).join(Employee, Employee.id == Candidate.hired_employee_id).filter(Candidate.stage == "hired", Employee.join_date >= s, Employee.join_date <= e).all()
    vals = [(emp.join_date - cand.created_at.date()).days for cand, emp in rows if cand.created_at]
    return round(sum(vals) / len(vals), 1) if vals else None


def f_onboarding(db, s, e, et, eid):
    q = db.query(OnboardingTask).join(Employee, Employee.id == OnboardingTask.employee_id).filter(Employee.join_date >= add_months(s, -2), Employee.join_date <= e)
    total = q.count()
    if not total:
        return None
    done = q.filter(OnboardingTask.status.in_(["done", "completed"])).count()
    return pct(done, total)


def f_turnover(db, s, e, et, eid):
    head = db.query(Employee).filter(Employee.join_date <= e).count()
    if not head:
        return None
    exits = db.query(Employee).filter(Employee.exit_date >= s, Employee.exit_date <= e).count()
    return pct(exits, head)


def f_enps(db, s, e, et, eid):
    return nps(db, s, e, "staff")["nps"]


def f_nps(db, s, e, et, eid):
    return nps(db, s, e, "customer")["nps"]


def f_curriculum_completion(db, s, e, et, eid):
    total = db.query(StudentProgress).join(Student, Student.id == StudentProgress.student_id).filter(Student.status == "active").count()
    if not total:
        return None
    done = db.query(StudentProgress).join(Student, Student.id == StudentProgress.student_id).filter(Student.status == "active", StudentProgress.status == "completed").count()
    return pct(done, total)


def f_student_progress(db, s, e, et, eid):
    a, b = dt_range(s, e)
    done = db.query(StudentProgress).filter(StudentProgress.completed_at >= a, StudentProgress.completed_at <= b).count()
    act = active_students(db)
    if _empty(db, StudentProgress) or not act:
        return None
    return round(done / act, 2)


def f_evaluation_pass(db, s, e, et, eid):
    q = db.query(Evaluation).filter(Evaluation.date >= s, Evaluation.date <= e, Evaluation.result.in_(["pass", "fail"]))
    total = q.count()
    if not total:
        return None
    return pct(q.filter(Evaluation.result == "pass").count(), total)


def f_lesson_plan_compliance(db, s, e, et, eid):
    q = db.query(LessonPlan).filter(LessonPlan.plan_date >= s, LessonPlan.plan_date <= e)
    tid = _teacher_of(et, eid)
    if tid:
        q = q.filter(LessonPlan.teacher_id == tid)
    total = q.count()
    if not total:
        return None
    return pct(q.filter(LessonPlan.status.in_(["delivered", "partially_delivered"])).count(), total)


def f_qa_coverage(db, s, e, et, eid):
    sc = session_counts(db, s, e)
    if not sc["done"]:
        return None
    a, b = dt_range(s, e)
    reviews = db.query(QAReview).filter(QAReview.created_at >= a, QAReview.created_at <= b).count()
    return round(min(100.0, 100 * reviews / sc["done"]), 1)


def f_corrective_closure(db, s, e, et, eid):
    a, b = dt_range(s, e)
    q = db.query(CorrectiveAction).filter(CorrectiveAction.created_at >= add_months(a, -1), CorrectiveAction.created_at <= b)
    total = q.count()
    if not total:
        return None
    return pct(q.filter(CorrectiveAction.status == "closed").count(), total)


def f_repeat_issues(db, s, e, et, eid):
    if _empty(db, QAReview):
        return None
    a, b = dt_range(s, e)
    rows = db.query(QAReview.teacher_id, func.count(QAReview.id)).filter(QAReview.created_at >= a, QAReview.created_at <= b, QAReview.overall_score < 70).group_by(QAReview.teacher_id).all()
    return float(sum(1 for _, n in rows if n >= 2))


def f_revenue(db, s, e, et, eid):
    return None if _empty(db, Payment) else round(revenue(db, s, e), 2)


def f_receivables(db, s, e, et, eid):
    return None if _empty(db, Invoice) else receivables(db)["amount"]


def f_collection(db, s, e, et, eid):
    return collection_rate(db, s, e)


def f_payroll_ratio(db, s, e, et, eid):
    rev = revenue(db, s, e)
    return round(100 * payroll(db, s, e) / rev, 1) if rev else None


def f_pnl(db, s, e, et, eid):
    if _empty(db, Payment) and _empty(db, Expense):
        return None
    return round(revenue(db, s, e) - expenses(db, s, e) - payroll(db, s, e), 2)


def f_cash_flow(db, s, e, et, eid):
    if _empty(db, Payment) and _empty(db, Expense):
        return None
    paid_exp = _f(db.query(func.sum(Expense.amount_in_base)).filter(Expense.status == "paid", Expense.expense_date >= s, Expense.expense_date <= e).scalar())
    return round(revenue(db, s, e) - paid_exp - payroll(db, s, e), 2)


def f_cpl(db, s, e, et, eid):
    return marketing_metrics(db, s, e)["cpl"]


def f_cac(db, s, e, et, eid):
    return marketing_metrics(db, s, e)["cac"]


def f_attribution(db, s, e, et, eid):
    mk = marketing_metrics(db, s, e)
    return mk["attributed_revenue"] if (mk["spend"] or mk["attributed_revenue"]) else None


def f_roi(db, s, e, et, eid):
    return marketing_metrics(db, s, e)["roi"]


def f_health(db, s, e, et, eid):
    return health_score(executive_metrics(db, s, e))["score"]


def f_mrr(db, s, e, et, eid):
    return None if _empty(db, Subscription) else round(mrr(db), 2)


def f_active_students(db, s, e, et, eid):
    return None if _empty(db, Student) else float(active_students(db, e))


def f_test_improvement(db, s, e, et, eid):
    months = {period_key(s)}
    d = s
    while d <= e:
        months.add(period_key(d))
        d = add_months(d, 1)
    q = db.query(func.avg(MonthlyTest.improvement_pct)).filter(MonthlyTest.period.in_(list(months)), MonthlyTest.improvement_pct.isnot(None))
    tid = _teacher_of(et, eid)
    if tid:
        q = q.filter(MonthlyTest.teacher_id == tid)
    v = q.scalar()
    return round(float(v), 1) if v is not None else None


def f_referral_pct(db, s, e, et, eid):
    r = referral_share(db, s, e)
    return r["pct"] if r["gross_adds"] else None


def f_integration_health(db, s, e, et, eid):
    rows = db.query(Integration).filter(Integration.status != "not_configured").all()
    if not rows:
        return None
    return pct(sum(1 for i in rows if i.health == "healthy"), len(rows))


def f_open_incidents(db, s, e, et, eid):
    return float(db.query(SecurityIncident).filter(SecurityIncident.status == "open").count())


def f_webhook_success(db, s, e, et, eid):
    a, b = dt_range(s, e)
    q = db.query(WebhookDelivery).filter(WebhookDelivery.created_at >= a, WebhookDelivery.created_at <= b)
    total = q.count()
    if not total:
        return None
    return pct(q.filter(WebhookDelivery.status == "success").count(), total)


FORMULAS: dict[str, Formula] = {
    "classes_completed": f_classes_completed, "classes_missed": f_classes_missed, "missed_rate": f_missed_rate, "class_completion": f_completion,
    "punctuality": f_punctuality, "qa_score": f_qa, "ai_score": f_ai, "complaints": f_complaints, "feedback_rating": f_feedback_rating,
    "student_retention": f_retention, "teacher_utilization": f_utilization, "class_coverage": f_coverage, "missed_response_minutes": f_missed_response,
    "sla_compliance": f_sla, "hr_attendance": f_hr_attendance, "ops_attendance": f_ops_attendance, "team_qa": f_team_qa,
    "retention_rate": f_retention_rate, "churn_rate": f_churn, "lead_conversion": f_lead_conversion, "trial_conversion": f_trial_conversion,
    "leads_count": f_leads, "tasks_done": f_tasks_done, "hiring_time_days": f_hiring_time, "onboarding_completion": f_onboarding,
    "turnover": f_turnover, "enps": f_enps, "nps": f_nps, "curriculum_completion": f_curriculum_completion, "student_progress": f_student_progress,
    "evaluation_pass_rate": f_evaluation_pass, "lesson_plan_compliance": f_lesson_plan_compliance, "qa_coverage": f_qa_coverage,
    "corrective_closure": f_corrective_closure, "repeat_issues": f_repeat_issues, "revenue": f_revenue, "receivables": f_receivables,
    "collection_rate": f_collection, "payroll_ratio": f_payroll_ratio, "pnl": f_pnl, "cash_flow": f_cash_flow, "cpl": f_cpl, "cac": f_cac,
    "revenue_attribution": f_attribution, "roi": f_roi, "health_score": f_health, "mrr": f_mrr, "active_students": f_active_students,
    "test_improvement": f_test_improvement, "referral_pct": f_referral_pct, "integration_health": f_integration_health,
    "open_incidents": f_open_incidents, "webhook_success": f_webhook_success,
}


def compute_kpi(db: Session, kpi: KPI, period, entity=None) -> Optional[float]:
    """Compute a KPI for ``period`` (``YYYY-MM`` string, a ``Period`` or a (start, end) tuple).

    ``entity`` may be a ``Teacher``/``Department`` instance or a ``("teacher", id)`` tuple. Returns ``None`` when the
    underlying data does not exist (never raises)."""
    fn = FORMULAS.get(kpi.formula_key or "")
    if fn is None:
        return None
    if isinstance(period, Period):
        s, e = period.start, period.end
    elif isinstance(period, (tuple, list)):
        s, e = period
    else:
        s, e = period_bounds(str(period))
    et, eid = None, None
    if isinstance(entity, Teacher):
        et, eid = "teacher", entity.id
    elif isinstance(entity, Department):
        et, eid = "department", entity.id
    elif isinstance(entity, (tuple, list)) and len(entity) == 2:
        et, eid = entity
    try:
        v = fn(db, s, e, et, eid)
    except Exception:
        return None
    if v is None:
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def upsert_value(db: Session, kpi: KPI, period: str, value: float, source: str = "system", entity_type: Optional[str] = None,
                 entity_id: Optional[int] = None, entered_by_id: Optional[int] = None, target: Optional[float] = None) -> KPIValue:
    q = db.query(KPIValue).filter(KPIValue.kpi_id == kpi.id, KPIValue.period == period)
    q = q.filter(KPIValue.entity_type == entity_type) if entity_type else q.filter(KPIValue.entity_type.is_(None))
    q = q.filter(KPIValue.entity_id == entity_id) if entity_id else q.filter(KPIValue.entity_id.is_(None))
    row = q.first()
    if row and row.source == "manual" and source != "manual":
        return row  # never overwrite a human entry with a system snapshot
    if not row:
        row = KPIValue(kpi_id=kpi.id, period=period, entity_type=entity_type, entity_id=entity_id)
        db.add(row)
    row.value = value
    row.target = target if target is not None else kpi.target
    row.source = source
    row.entered_by_id = entered_by_id
    row.created_at = datetime.utcnow()
    return row


def snapshot_kpis(db: Session, period: Optional[str] = None, source: str = "system") -> dict:
    """Store KPIValue rows for every active system KPI for ``period`` (default: current month), including
    per-teacher values for teacher-role KPIs. Returns counts."""
    period = period or period_key()
    kpis = db.query(KPI).filter(KPI.is_active.is_(True), KPI.formula_key.isnot(None)).all()
    teachers = db.query(Teacher).filter(Teacher.status == "active").all()
    stored, skipped, teacher_rows = 0, 0, 0
    for k in kpis:
        v = compute_kpi(db, k, period)
        if v is None:
            skipped += 1
        else:
            upsert_value(db, k, period, v, source=source)
            stored += 1
        if k.role_slug == "teacher":
            for t in teachers:
                tv = compute_kpi(db, k, period, t)
                if tv is not None:
                    upsert_value(db, k, period, tv, source=source, entity_type="teacher", entity_id=t.id)
                    teacher_rows += 1
    db.flush()
    return {"period": period, "stored": stored, "skipped": skipped, "teacher_rows": teacher_rows}


def latest_value(db: Session, kpi: KPI, period: Optional[str] = None, entity_type: Optional[str] = None, entity_id: Optional[int] = None) -> Optional[KPIValue]:
    q = db.query(KPIValue).filter(KPIValue.kpi_id == kpi.id)
    q = q.filter(KPIValue.entity_type == entity_type) if entity_type else q.filter(KPIValue.entity_type.is_(None))
    q = q.filter(KPIValue.entity_id == entity_id) if entity_id else q.filter(KPIValue.entity_id.is_(None))
    if period:
        row = q.filter(KPIValue.period == period).first()
        if row:
            return row
    return q.order_by(KPIValue.period.desc(), KPIValue.created_at.desc()).first()


def kpi_row(db: Session, kpi: KPI, period: str, entity=None, live: bool = True) -> dict:
    """Value + target + RAG for one KPI in one period (live compute with stored fallback)."""
    et, eid = (None, None)
    if isinstance(entity, Teacher):
        et, eid = "teacher", entity.id
    elif isinstance(entity, Department):
        et, eid = "department", entity.id
    value, source = None, "none"
    if live and kpi.formula_key:
        value = compute_kpi(db, kpi, period, entity)
        source = "live" if value is not None else source
    if value is None:
        row = latest_value(db, kpi, period, et, eid)
        if row:
            value, source = row.value, row.source
    status = rag(value, kpi.target, kpi.direction)
    return {"kpi": kpi, "value": value, "target": kpi.target, "unit": kpi.unit, "rag": status, "color": RAG_COLORS[status],
            "label": RAG_LABELS[status], "source": source, "direction": kpi.direction}


DEPT_ROLE_SLUGS = {"people": ["hr"], "finance": ["finance"], "academics": ["academic", "teacher"], "qa": ["qa"], "marketing": ["marketing"],
                   "operations": ["supervisor", "manager"], "technology": ["technology"]}


def department_kpis(db: Session, dept: Department) -> list[KPI]:
    slugs = DEPT_ROLE_SLUGS.get(dept.code, [])
    return db.query(KPI).filter(KPI.is_active.is_(True), or_(KPI.department_id == dept.id, KPI.role_slug.in_(slugs) if slugs else False)).order_by(KPI.role_slug, KPI.name).all()


def department_metrics(db: Session, dept: Department, period: str, live: bool = True) -> dict:
    rows = [kpi_row(db, k, period, dept if k.formula_key in ("sla_compliance", "hr_attendance", "tasks_done") else None, live=live) for k in department_kpis(db, dept)]
    scores = [rag_score(r["rag"]) for r in rows]
    scores = [x for x in scores if x is not None]
    score = round(sum(scores) / len(scores), 1) if scores else None
    counts = {c: sum(1 for r in rows if r["rag"] == c) for c in ("green", "amber", "red", "grey", "blue")}
    return {"department": dept, "rows": rows, "score": score, "counts": counts, "band": health_band(score)}


def format_value(value: Optional[float], unit: str) -> str:
    if value is None:
        return "-"
    if unit == "%":
        return f"{value:.1f}%"
    if unit == "currency":
        return f"{value:,.0f}"
    if unit == "count":
        return f"{int(round(value)):,}" if abs(value - round(value)) < 1e-6 else f"{value:,.1f}"
    if unit == "days":
        return f"{value:.1f} d"
    if unit == "minutes":
        return f"{value:.0f} min"
    return f"{value:,.1f}"
