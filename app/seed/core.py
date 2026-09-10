"""Core seed: organization, branches, departments, roles, users, currencies, integrations, templates, settings."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.rbac import ROLE_DEFINITIONS
from app.core.security import hash_password
from app.models.core import Organization, Branch, Department, Role, User, Integration, NotificationTemplate, Setting
from app.models.finance import Currency
from app.services.integrations import PROVIDERS

DEPARTMENTS = [
    ("people", "People & Culture", "HR, recruitment, onboarding, attendance, payroll, employee sentiment, development plans, culture KPIs."),
    ("finance", "Finance", "Billing, accounts, payroll, P&L, cash flow, receivables, budget, forecasting."),
    ("academics", "Academics", "Curriculum, lesson planning, student progress, evaluations, academic KPIs."),
    ("qa", "Quality Assurance", "Class sampling, AI/manual review, teacher quality, corrective actions, QA KPIs."),
    ("technology", "Technology", "Infrastructure, integrations, security, incidents, API health, product roadmap."),
    ("marketing", "Marketing", "Leads, campaigns, attribution, GHL, conversion, CAC, CPL, ROI."),
    ("operations", "Operations", "Supervisors, managers, scheduling, class execution, support."),
]

CURRENCIES = [  # code, name, symbol, rate to PKR
    ("PKR", "Pakistani Rupee", "Rs", 1.0, True),
    ("GBP", "British Pound", "£", 355.0, False),
    ("USD", "US Dollar", "$", 278.0, False),
    ("AUD", "Australian Dollar", "A$", 185.0, False),
    ("CAD", "Canadian Dollar", "C$", 205.0, False),
    ("EUR", "Euro", "€", 302.0, False),
]

# (email, password, full name, role slug, department code)
USERS = [
    ("admin@oqc.local", "Admin@12345", "Muhammad Yousuf (CEO)", "super_admin", "technology"),
    ("sysadmin@oqc.local", "Sys@12345", "Bilal Ahmed", "system_admin", "technology"),
    ("hr@oqc.local", "People@123", "Sana Malik", "hod_people", "people"),
    ("finance@oqc.local", "Finance@123", "Imran Qureshi", "hod_finance", "finance"),
    ("academics@oqc.local", "Academ@123", "Qari Abdul Rehman", "hod_academics", "academics"),
    ("qa@oqc.local", "Quality@123", "Hafiz Usman Tariq", "hod_qa", "qa"),
    ("tech@oqc.local", "Tech@123", "Faisal Khan", "hod_technology", "technology"),
    ("marketing@oqc.local", "Market@123", "Ayesha Siddiqui", "hod_marketing", "marketing"),
    ("manager@oqc.local", "Manager@123", "Kamran Ali", "manager", "operations"),
    ("manager2@oqc.local", "Manager@123", "Nadia Hussain", "manager", "operations"),
    ("supervisor@oqc.local", "Super@123", "Zubair Shah", "supervisor", "operations"),
    ("supervisor2@oqc.local", "Super@123", "Rabia Noor", "supervisor", "operations"),
    ("billing@oqc.local", "Billing@123", "Hina Baig", "billing_rep", "finance"),
    ("leadgen@oqc.local", "LeadGen@123", "Saad Farooq", "lead_generator", "marketing"),
    ("closer@oqc.local", "Closer@123", "Omar Javed", "lead_closer", "marketing"),
    ("accountant@oqc.local", "Account@123", "Tariq Mehmood", "accountant", "finance"),
    ("qaofficer@oqc.local", "QAOff@123", "Maryam Iqbal", "qa_officer", "qa"),
    ("hrofficer@oqc.local", "HROff@123", "Adeel Raza", "hr_officer", "people"),
    ("coordinator@oqc.local", "Coord@123", "Ustadha Fatima Zahra", "academic_coordinator", "academics"),
    ("auditor@oqc.local", "Auditor@123", "External Auditor", "auditor", None),
]

TEMPLATES = [
    ("class_reminder_teacher", "in_app", "Class in {{minutes}} minutes", "Your class with {{student}} starts at {{time}}. Room: {{room}}."),
    ("class_reminder_student", "whatsapp", "Class reminder", "Assalamu Alaikum {{name}}, {{student}}'s Quran class with {{teacher}} starts at {{time}}. Join: {{link}}"),
    ("invoice_issued", "whatsapp", "Invoice {{number}}", "Dear {{name}}, invoice {{number}} for {{amount}} is due on {{due}}. Pay: {{link}}"),
    ("payment_received", "whatsapp", "Payment received", "JazakAllah Khair {{name}}. We received {{amount}}. Receipt: {{link}}"),
    ("payment_failed", "in_app", "Payment failed", "Payment for invoice {{number}} failed. Please follow up with {{name}}."),
    ("trial_follow_up", "whatsapp", "Trial follow-up", "Assalamu Alaikum {{name}}, how was {{student}}'s trial class? We'd love to continue the journey. Reply to book."),
    ("lead_follow_up", "whatsapp", "Follow-up", "Assalamu Alaikum {{name}}, this is Online Quran College. Shall we book a free trial for {{student}}?"),
    ("result_card", "whatsapp", "Monthly result card", "Assalamu Alaikum {{name}}, {{student}}'s result card for {{period}} is ready: {{link}}"),
    ("feedback_survey", "whatsapp", "How are we doing?", "Assalamu Alaikum {{name}}, please share 1-minute feedback about {{student}}'s classes: {{link}}"),
    ("ambassador_invite", "whatsapp", "Become an Ambassador", "Assalamu Alaikum {{name}}, families like yours make our college. Share your link {{link}} — both families receive a free week when a new student enrols."),
    ("leave_approved", "in_app", "Leave approved", "Leave from {{start}} to {{end}} has been approved."),
    ("complaint_sla", "in_app", "Case SLA at risk", "Case {{number}} is approaching its SLA deadline."),
    ("task_overdue", "in_app", "Task overdue", "Task “{{title}}” is overdue."),
    ("win_back", "whatsapp", "We miss {{student}}", "Assalamu Alaikum {{name}}, we noticed {{student}} has missed some classes. Can we help with a schedule change or a short refresher course?"),
]

SETTINGS = [
    ("report_deadline_morning", {"time": "10:00"}, "governance", "Morning structured report deadline"),
    ("report_deadline_afternoon", {"time": "17:00"}, "governance", "Afternoon structured report deadline"),
    ("discount_manager_max_pct", {"value": 20}, "pricing", "Manager can approve discounts up to this %"),
    ("discount_ceo_max_pct", {"value": 35}, "pricing", "CEO can approve discounts up to this %; above is prohibited"),
    ("pricing_floor_margin_pct", {"value": 20}, "pricing", "Minimum margin above teacher cost"),
    ("ambassador_tenure_days", {"value": 60}, "referrals", "Tenure milestone for ambassador invite"),
    ("ambassador_min_nps", {"value": 8}, "referrals", "Minimum NPS to gate ambassador invite"),
    ("referral_credit_weeks", {"value": 1}, "referrals", "Free-week equivalent credit for both parties"),
    ("recording_retention_days", {"value": 365}, "safeguarding", "Class recording retention period"),
    ("case_sla_hours", {"complaint": 48, "request": 72, "billing": 24, "technical": 12, "urgent": 4}, "cases", "SLA hours by case type"),
    ("class_reminder_minutes", {"teacher": 15, "student": 30}, "reminders", "Reminder lead time before class"),
    ("missed_class_grace_minutes", {"value": 10}, "classes", "Teacher no-show grace period before auto-missed"),
    ("churn_thresholds", {"medium": 40, "high": 65}, "retention", "Risk score thresholds"),
    ("teacher_grade_rules", {"A": {"qa": 85, "punctuality": 95, "retention": 90}, "B": {"qa": 70, "punctuality": 85, "retention": 75}}, "teacher_dev", "Grade thresholds"),
    ("salary_bands", {"A": 45000, "B": 35000, "C": 27000}, "payroll", "Base salary band by teacher grade (PKR)"),
    ("dor_quota_default", {"value": 8}, "academic", "Default monthly dor (revision) quota items"),
]


def run(db: Session) -> None:
    if not db.query(Organization).first():
        db.add(Organization(name="Online Quran College", legal_name="Online Quran College (Pvt) Ltd", base_currency="PKR",
                            timezone="Asia/Karachi", email="info@onlinequrancollege.local", phone="+92 300 0000000",
                            website="https://onlinequrancollege.local",
                            settings={"report_deadline_morning": "10:00", "report_deadline_afternoon": "17:00"}))
    if not db.query(Branch).first():
        db.add_all([Branch(name="Head Office — Lahore", code="LHR", country="Pakistan", timezone="Asia/Karachi"),
                    Branch(name="Karachi Hub", code="KHI", country="Pakistan", timezone="Asia/Karachi")])
    db.flush()

    dept_by_code = {}
    for code, name, desc in DEPARTMENTS:
        d = db.query(Department).filter(Department.code == code).first()
        if not d:
            d = Department(code=code, name=name, description=desc)
            db.add(d)
            db.flush()
        dept_by_code[code] = d

    role_by_slug = {}
    for slug, spec in ROLE_DEFINITIONS.items():
        r = db.query(Role).filter(Role.slug == slug).first()
        if not r:
            r = Role(slug=slug, name=spec["name"], portal=spec["portal"], permissions=spec["permissions"], is_system=True)
            db.add(r)
        else:
            r.permissions = spec["permissions"]
            r.portal = spec["portal"]
        db.flush()
        role_by_slug[slug] = r

    branch = db.query(Branch).first()
    for email, pwd, name, role_slug, dept in USERS:
        if db.query(User).filter(User.email == email).first():
            continue
        db.add(User(email=email, username=email.split("@")[0], full_name=name, hashed_password=hash_password(pwd),
                    role_id=role_by_slug[role_slug].id, department_id=dept_by_code[dept].id if dept else None,
                    branch_id=branch.id if branch else None, is_superuser=(role_slug == "super_admin"), timezone="Asia/Karachi"))
    db.flush()

    # HODs
    hod_map = {"people": "hr@oqc.local", "finance": "finance@oqc.local", "academics": "academics@oqc.local",
               "qa": "qa@oqc.local", "technology": "tech@oqc.local", "marketing": "marketing@oqc.local", "operations": "manager@oqc.local"}
    for code, email in hod_map.items():
        u = db.query(User).filter(User.email == email).first()
        if u:
            dept_by_code[code].hod_user_id = u.id

    for code, name, symbol, rate, is_base in CURRENCIES:
        if not db.query(Currency).filter(Currency.code == code).first():
            db.add(Currency(code=code, name=name, symbol=symbol, rate_to_base=rate, is_base=is_base))

    for provider, name in PROVIDERS.items():
        if not db.query(Integration).filter(Integration.provider == provider).first():
            db.add(Integration(provider=provider, name=name, status="simulated" if provider in ("whatsapp", "ghl", "smtp", "ai", "n8n", "video") else "not_configured",
                               health="healthy" if provider in ("whatsapp", "ghl", "smtp", "ai", "n8n", "video") else "unknown",
                               last_health_check_at=datetime.utcnow()))

    for event, channel, subject, body in TEMPLATES:
        if not db.query(NotificationTemplate).filter(NotificationTemplate.event_type == event, NotificationTemplate.channel == channel).first():
            db.add(NotificationTemplate(event_type=event, channel=channel, subject=subject, body=body))

    for key, value, group, desc in SETTINGS:
        if not db.query(Setting).filter(Setting.key == key).first():
            db.add(Setting(key=key, value=value, group=group, description=desc))
    db.commit()

    try:
        from app.seed import system_extra
        system_extra.run(db)
    except ImportError:
        pass
