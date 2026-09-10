"""Role-based access control.

Permissions are strings of the form ``module.action``.
Actions: view, add, update, approve, delete, execute, export, assign, configure.
Wildcards: ``*`` (everything), ``module.*`` (all actions on a module), ``*.view`` (view on every module).
"""
from __future__ import annotations

from typing import Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.core import User

ACTIONS = ["view", "add", "update", "approve", "delete", "execute", "export", "assign", "configure"]

# Every module in the platform (used by the roles UI and by nav gating)
MODULES = {
    # platform
    "dashboard": "Academic Home Dashboard",
    "command_center": "CEO Command Center",
    "users": "Users",
    "roles": "Roles & Permissions",
    "settings": "System Settings",
    "audit": "Audit Log",
    "notifications": "Notifications",
    "integrations": "Integration Hub",
    "api_keys": "API Keys",
    "webhooks": "Webhooks",
    "security": "Security Center",
    "backups": "Backups & DR",
    "migration": "Data Migration",
    "ai_governance": "AI Governance",
    "reports": "Reports & Exports",
    # CRM
    "leads": "Leads & Pipeline",
    "campaigns": "Campaigns",
    "marketing": "Marketing Analytics",
    "inbox": "WhatsApp Inbox",
    "sequences": "Automation Sequences",
    "referrals": "Ambassador Program",
    "feedback": "Feedback & VoC",
    "cases": "Complaints & Cases",
    "retention": "Retention & Churn",
    "trials": "Trial Management",
    "registration": "Online Registration",
    # people
    "clients": "Clients / Parents",
    "students": "Students",
    "teachers": "Teachers",
    "employees": "Employees",
    "hr_attendance": "HR Attendance",
    "leaves": "Leaves",
    "recruitment": "Recruitment",
    "violations": "Violations",
    "grievances": "Grievances (Confidential)",
    "provisioning": "Onboarding / Offboarding",
    "payroll": "Payroll",
    "teacher_dev": "Teacher Development / Ustaadh Lab",
    "safeguarding": "Safeguarding",
    # academic
    "courses": "Courses & Divisions",
    "packages": "Packages & Pricing",
    "curriculum": "Curriculum",
    "lesson_plans": "Lesson Plans",
    "evaluations": "Evaluations",
    "monthly_tests": "Monthly Tests & Result Cards",
    "certificates": "Certificates",
    "arabic_view": "Shared Arabic Lesson View",
    # operations
    "schedules": "Schedules",
    "classes": "Class Sessions",
    "attendance": "Class Attendance",
    "supervisor": "Supervisor Live Monitoring",
    "recordings": "Recordings",
    "ai_monitoring": "AI Class Monitoring",
    "qa": "Quality Assurance",
    "calling": "In-Platform Calling",
    "tasks": "Tasks & Projects",
    "kpis": "KPIs",
    "transformation": "Transformation OS",
    "decisions": "Decision Register",
    "daily_reports": "Structured Daily Reports",
    # finance
    "subscriptions": "Subscriptions",
    "discounts": "Discount Ladder",
    "scholarships": "Scholarships",
    "billing": "Billing & Invoices",
    "payments": "Payments & Receipts",
    "ledger": "Client Ledger",
    "accounts": "Accounts & Finance",
    "expenses": "Expenses",
    "currencies": "Currencies",
    # portals
    "portal_teacher": "Teacher Portal",
    "portal_client": "Client / Parent Portal",
    "portal_student": "Student Portal",
}

_VIEW_ALL = ["*.view"]
_TECH = ["users.*", "roles.*", "settings.*", "audit.*", "integrations.*", "api_keys.*", "webhooks.*",
         "security.*", "backups.*", "migration.*", "ai_governance.*", "notifications.*", "reports.*", "dashboard.*"]
_HR = ["employees.*", "hr_attendance.*", "leaves.*", "recruitment.*", "violations.*", "grievances.*",
       "provisioning.*", "payroll.*", "teacher_dev.*", "teachers.*", "feedback.view", "kpis.*", "tasks.*",
       "decisions.*", "daily_reports.*", "transformation.*", "dashboard.view", "reports.*", "notifications.*"]
_FINANCE = ["subscriptions.*", "discounts.*", "scholarships.*", "billing.*", "payments.*", "ledger.*", "accounts.*",
            "expenses.*", "currencies.*", "payroll.*", "clients.view", "students.view", "packages.*", "kpis.*",
            "reports.*", "tasks.*", "decisions.*", "daily_reports.*", "dashboard.view", "notifications.*", "cases.view"]
_ACADEMIC = ["courses.*", "packages.view", "curriculum.*", "lesson_plans.*", "evaluations.*", "monthly_tests.*",
             "certificates.*", "arabic_view.*", "students.*", "clients.view", "teachers.view", "schedules.*",
             "classes.*", "attendance.*", "leaves.*", "qa.view", "ai_monitoring.view", "kpis.*", "tasks.*",
             "decisions.*", "daily_reports.*", "dashboard.*", "reports.*", "notifications.*", "cases.*", "supervisor.*"]
_QA = ["qa.*", "ai_monitoring.*", "recordings.*", "classes.view", "teachers.view", "students.view", "teacher_dev.*",
       "cases.*", "feedback.view", "kpis.*", "tasks.*", "decisions.*", "daily_reports.*", "dashboard.view",
       "reports.*", "notifications.*", "safeguarding.view", "monthly_tests.view", "evaluations.view"]
_MARKETING = ["leads.*", "campaigns.*", "marketing.*", "inbox.*", "sequences.*", "referrals.*", "trials.*",
              "registration.*", "clients.view", "clients.add", "students.view", "feedback.view", "kpis.*", "tasks.*",
              "decisions.*", "daily_reports.*", "dashboard.view", "reports.*", "notifications.*", "calling.*"]

ROLE_DEFINITIONS: dict[str, dict] = {
    "super_admin": {"name": "Super Admin / CEO", "portal": "admin", "permissions": ["*"]},
    "system_admin": {"name": "System Administrator", "portal": "admin", "permissions": _TECH + _VIEW_ALL},
    "hod_people": {"name": "HOD — People & Culture", "portal": "admin", "permissions": _HR + _VIEW_ALL},
    "hod_finance": {"name": "HOD — Finance", "portal": "admin", "permissions": _FINANCE + _VIEW_ALL},
    "hod_academics": {"name": "HOD — Academics", "portal": "admin", "permissions": _ACADEMIC + _VIEW_ALL},
    "hod_qa": {"name": "HOD — QA", "portal": "admin", "permissions": _QA + _VIEW_ALL},
    "hod_technology": {"name": "HOD — Technology", "portal": "admin", "permissions": _TECH + _VIEW_ALL},
    "hod_marketing": {"name": "HOD — Marketing", "portal": "admin", "permissions": _MARKETING + _VIEW_ALL},
    "manager": {"name": "Manager", "portal": "admin", "permissions": [
        "dashboard.*", "students.*", "clients.*", "teachers.view", "schedules.*", "classes.*", "attendance.*",
        "supervisor.*", "leaves.*", "cases.*", "trials.*", "subscriptions.*", "discounts.view", "discounts.add",
        "discounts.approve", "billing.view", "ledger.view", "kpis.*", "tasks.*", "decisions.*", "daily_reports.*",
        "reports.*", "notifications.*", "referrals.*", "retention.*", "feedback.view", "qa.view",
        "ai_monitoring.view", "lesson_plans.view", "evaluations.view", "monthly_tests.view", "calling.*", "leads.view"]},
    "supervisor": {"name": "Supervisor", "portal": "admin", "permissions": [
        "dashboard.*", "supervisor.*", "classes.*", "attendance.*", "schedules.view", "schedules.update",
        "students.view", "teachers.view", "clients.view", "leaves.view", "leaves.add", "cases.view", "cases.add",
        "cases.update", "referrals.*", "tasks.*", "daily_reports.*", "notifications.*", "recordings.view",
        "lesson_plans.view", "calling.*", "kpis.view", "retention.view", "trials.view", "trials.update"]},
    "teacher": {"name": "Teacher", "portal": "teacher", "permissions": [
        "portal_teacher.*", "classes.view", "classes.execute", "classes.update", "attendance.add", "attendance.view",
        "lesson_plans.*", "evaluations.add", "evaluations.view", "evaluations.update", "monthly_tests.view",
        "monthly_tests.update", "students.view", "leaves.add", "leaves.view", "arabic_view.*", "teacher_dev.view",
        "hr_attendance.add", "hr_attendance.view", "notifications.*", "tasks.view", "tasks.update", "curriculum.view",
        "daily_reports.add", "daily_reports.view", "grievances.add", "recordings.view", "trials.view", "trials.update"]},
    "billing_rep": {"name": "Billing Representative", "portal": "admin", "permissions": [
        "dashboard.view", "billing.*", "payments.*", "ledger.*", "clients.view", "clients.update", "students.view",
        "subscriptions.view", "subscriptions.update", "inbox.*", "cases.add", "cases.view", "tasks.*",
        "daily_reports.*", "notifications.*", "calling.*", "currencies.view", "reports.view", "reports.export"]},
    "lead_generator": {"name": "Lead Generator", "portal": "admin", "permissions": [
        "dashboard.view", "leads.view", "leads.add", "leads.update", "campaigns.view", "inbox.*", "tasks.*",
        "daily_reports.*", "notifications.*", "marketing.view", "calling.*", "trials.view"]},
    "lead_closer": {"name": "Lead Closer", "portal": "admin", "permissions": [
        "dashboard.view", "leads.*", "trials.*", "inbox.*", "sequences.view", "registration.*", "clients.add",
        "clients.view", "students.add", "students.view", "subscriptions.add", "subscriptions.view", "discounts.add",
        "discounts.view", "packages.view", "teachers.view", "schedules.add", "schedules.view", "tasks.*",
        "daily_reports.*", "notifications.*", "calling.*", "marketing.view", "referrals.view"]},
    "accountant": {"name": "Accountant", "portal": "admin", "permissions": [
        "dashboard.view", "accounts.*", "expenses.*", "payments.*", "ledger.*", "billing.view", "billing.export",
        "payroll.view", "currencies.*", "subscriptions.view", "clients.view", "reports.*", "tasks.*",
        "daily_reports.*", "notifications.*", "scholarships.view", "discounts.view"]},
    "qa_officer": {"name": "QA Officer", "portal": "admin", "permissions": [
        "dashboard.view", "qa.*", "ai_monitoring.view", "ai_monitoring.update", "recordings.view", "classes.view",
        "teachers.view", "students.view", "teacher_dev.view", "teacher_dev.add", "cases.view", "tasks.*",
        "daily_reports.*", "notifications.*", "monthly_tests.view", "evaluations.view"]},
    "hr_officer": {"name": "HR Officer", "portal": "admin", "permissions": [
        "dashboard.view", "employees.*", "hr_attendance.*", "leaves.*", "recruitment.*", "violations.*",
        "grievances.*", "provisioning.*", "payroll.view", "payroll.add", "teachers.view", "tasks.*",
        "daily_reports.*", "notifications.*", "teacher_dev.view"]},
    "academic_coordinator": {"name": "Academic Coordinator", "portal": "admin", "permissions": [
        "dashboard.*", "courses.*", "curriculum.*", "packages.view", "lesson_plans.*", "evaluations.*",
        "monthly_tests.*", "certificates.*", "arabic_view.*", "students.view", "students.update", "clients.view",
        "teachers.view", "schedules.*", "classes.view", "classes.update", "attendance.*", "leaves.view",
        "tasks.*", "daily_reports.*", "notifications.*", "trials.view", "trials.update"]},
    "client": {"name": "Client / Parent", "portal": "client", "permissions": ["portal_client.*", "notifications.*"]},
    "student": {"name": "Student", "portal": "student", "permissions": ["portal_student.*", "notifications.*"]},
    "auditor": {"name": "External Auditor (read-only)", "portal": "admin", "permissions": _VIEW_ALL + ["audit.view", "reports.view", "reports.export"]},
}

# roles allowed to see CEO-only material (anti-poaching, eNPS)
CEO_ROLES = {"super_admin"}
MANAGEMENT_ROLES = {"super_admin", "manager", "hod_people", "hod_finance", "hod_academics", "hod_qa", "hod_technology", "hod_marketing", "system_admin"}


def _matches(pattern: str, perm: str) -> bool:
    if pattern == "*" or pattern == perm:
        return True
    module, _, action = perm.partition(".")
    p_module, _, p_action = pattern.partition(".")
    return (p_module in ("*", module)) and (p_action in ("*", action))


def has_permission(user: "User", perm: str) -> bool:
    if user is None or not user.is_active:
        return False
    if user.is_superuser:
        return True
    if any(_matches(p, perm) for p in (user.denied_permissions or [])):
        return False
    grants: Iterable[str] = list(user.role.permissions if user.role else []) + list(user.extra_permissions or [])
    return any(_matches(p, perm) for p in grants)


def has_any(user: "User", perms: Iterable[str]) -> bool:
    return any(has_permission(user, p) for p in perms)


def is_ceo(user: "User") -> bool:
    return bool(user and (user.is_superuser or user.role_slug in CEO_ROLES))


def is_management(user: "User") -> bool:
    return bool(user and (user.is_superuser or user.role_slug in MANAGEMENT_ROLES))
