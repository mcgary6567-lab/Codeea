"""Navigation model.

The UI follows a drill-down launchpad pattern: Home shows one large card per *section*, a section page
shows one card per *item*, and every item is a real page. Breadcrumbs are derived from the same data,
so adding an item here is all that is needed to make it reachable and labelled correctly.
Items are gated by permission (see rbac). Icons are Lucide icon names.
"""
from __future__ import annotations

from app.core import rbac

# Saturated tile palette in the order the launchpad and stat rows cycle through it.
PALETTE = ["#2b7fd3", "#1aa7cf", "#27b7a4", "#33b479", "#7cc24e", "#e4cd39",
           "#ee8a2b", "#e35a4e", "#e95d7c", "#c94ca1", "#8c4ca9", "#5b62b1"]

# Named colours used by templates (stat(color=...)) mapped onto the same palette.
TILE = {
    "sky": "#2b7fd3", "blue": "#2b7fd3", "brand": "#1d6fcf", "cyan": "#1aa7cf", "teal": "#27b7a4",
    "emerald": "#33b479", "green": "#33b479", "lime": "#7cc24e", "amber": "#e4cd39", "yellow": "#e4cd39",
    "orange": "#ee8a2b", "rose": "#e35a4e", "red": "#e35a4e", "pink": "#e95d7c", "fuchsia": "#c94ca1",
    "violet": "#8c4ca9", "purple": "#8c4ca9", "indigo": "#5b62b1", "slate": "#6b7a90", "gray": "#6b7a90",
}


def palette(i: int) -> str:
    return PALETTE[i % len(PALETTE)]


# Each section: {"slug", "label", "icon", "blurb", "items": [{"label", "url", "icon", "perm"}]}
ADMIN_NAV = [
    {"slug": "overview", "label": "Overview", "icon": "layout-dashboard", "blurb": "Live operations and executive view", "items": [
        {"label": "Academic Home", "url": "/dashboard", "icon": "layout-dashboard", "perm": "dashboard.view"},
        {"label": "CEO Command Center", "url": "/command-center", "icon": "gauge", "perm": "command_center.view"},
        {"label": "Supervisor Live", "url": "/supervisor", "icon": "radio", "perm": "supervisor.view"},
        {"label": "Alerts", "url": "/alerts", "icon": "bell-ring", "perm": "dashboard.view"},
    ]},
    {"slug": "crm", "label": "CRM & Growth", "icon": "megaphone", "blurb": "Leads, WhatsApp, trials, campaigns", "items": [
        {"label": "Leads & Pipeline", "url": "/crm/leads", "icon": "funnel", "perm": "leads.view"},
        {"label": "WhatsApp Inbox", "url": "/crm/inbox", "icon": "message-circle", "perm": "inbox.view"},
        {"label": "Trials", "url": "/trials", "icon": "flask-conical", "perm": "trials.view"},
        {"label": "Campaigns", "url": "/crm/campaigns", "icon": "megaphone", "perm": "campaigns.view"},
        {"label": "Marketing Analytics", "url": "/crm/marketing", "icon": "trending-up", "perm": "marketing.view"},
        {"label": "Sequences", "url": "/crm/sequences", "icon": "workflow", "perm": "sequences.view"},
        {"label": "Ambassadors", "url": "/crm/referrals", "icon": "gift", "perm": "referrals.view"},
    ]},
    {"slug": "people", "label": "Clients & Students", "icon": "users", "blurb": "Families, students, requests, retention", "items": [
        {"label": "Clients / Parents", "url": "/clients", "icon": "users", "perm": "clients.view"},
        {"label": "Students", "url": "/students", "icon": "graduation-cap", "perm": "students.view"},
        {"label": "Registrations", "url": "/registrations", "icon": "clipboard-list", "perm": "registration.view"},
        {"label": "Retention & Churn", "url": "/retention", "icon": "heart-pulse", "perm": "retention.view"},
        {"label": "Cases & Complaints", "url": "/cases", "icon": "life-buoy", "perm": "cases.view"},
        {"label": "Feedback & VoC", "url": "/feedback", "icon": "message-square-heart", "perm": "feedback.view"},
    ]},
    {"slug": "academics", "label": "Academics", "icon": "book-open", "blurb": "Schedules, classes, curriculum, tests", "items": [
        {"label": "Schedules", "url": "/schedules", "icon": "calendar-days", "perm": "schedules.view"},
        {"label": "Class Sessions", "url": "/classes", "icon": "video", "perm": "classes.view"},
        {"label": "Student Leaves", "url": "/leaves/students", "icon": "calendar-off", "perm": "leaves.view"},
        {"label": "Courses & Packages", "url": "/academics/courses", "icon": "book-open", "perm": "courses.view"},
        {"label": "Curriculum", "url": "/academics/curriculum", "icon": "library", "perm": "curriculum.view"},
        {"label": "Lesson Plans", "url": "/academics/lesson-plans", "icon": "notebook-pen", "perm": "lesson_plans.view"},
        {"label": "Evaluations", "url": "/academics/evaluations", "icon": "clipboard-check", "perm": "evaluations.view"},
        {"label": "Monthly Tests", "url": "/academics/monthly-tests", "icon": "file-badge", "perm": "monthly_tests.view"},
        {"label": "Certificates", "url": "/academics/certificates", "icon": "award", "perm": "certificates.view"},
    ]},
    {"slug": "quality", "label": "Quality & AI", "icon": "shield-check", "blurb": "QA reviews, AI monitoring, safeguarding", "items": [
        {"label": "QA Queue", "url": "/qa", "icon": "shield-check", "perm": "qa.view"},
        {"label": "AI Class Monitoring", "url": "/ai-monitoring", "icon": "brain-circuit", "perm": "ai_monitoring.view"},
        {"label": "Recordings", "url": "/recordings", "icon": "film", "perm": "recordings.view"},
        {"label": "Safeguarding", "url": "/safeguarding", "icon": "shield-alert", "perm": "safeguarding.view"},
        {"label": "AI Governance", "url": "/ai-governance", "icon": "scale", "perm": "ai_governance.view"},
    ]},
    {"slug": "finance", "label": "Billing & Finance", "icon": "landmark", "blurb": "Subscriptions, invoices, accounts", "items": [
        {"label": "Subscriptions", "url": "/finance/subscriptions", "icon": "repeat", "perm": "subscriptions.view"},
        {"label": "Invoices", "url": "/finance/invoices", "icon": "receipt", "perm": "billing.view"},
        {"label": "Payments", "url": "/finance/payments", "icon": "credit-card", "perm": "payments.view"},
        {"label": "Discounts & Scholarships", "url": "/finance/discounts", "icon": "percent", "perm": "discounts.view"},
        {"label": "Accounts & P&L", "url": "/finance/accounts", "icon": "landmark", "perm": "accounts.view"},
        {"label": "Expenses", "url": "/finance/expenses", "icon": "wallet", "perm": "expenses.view"},
        {"label": "Currencies", "url": "/finance/currencies", "icon": "coins", "perm": "currencies.view"},
    ]},
    {"slug": "hr", "label": "Human Resource", "icon": "id-card", "blurb": "Teachers, employees, payroll", "items": [
        {"label": "Teachers", "url": "/teachers", "icon": "user-check", "perm": "teachers.view"},
        {"label": "Employees", "url": "/hr/employees", "icon": "id-card", "perm": "employees.view"},
        {"label": "HR Attendance", "url": "/hr/attendance", "icon": "clock", "perm": "hr_attendance.view"},
        {"label": "Staff Leaves", "url": "/hr/leaves", "icon": "plane", "perm": "leaves.view"},
        {"label": "Recruitment", "url": "/hr/recruitment", "icon": "briefcase", "perm": "recruitment.view"},
        {"label": "Payroll", "url": "/hr/payroll", "icon": "banknote", "perm": "payroll.view"},
        {"label": "Violations", "url": "/hr/violations", "icon": "triangle-alert", "perm": "violations.view"},
        {"label": "Grievances", "url": "/hr/grievances", "icon": "lock", "perm": "grievances.view"},
        {"label": "Ustaadh Lab", "url": "/hr/teacher-development", "icon": "sparkles", "perm": "teacher_dev.view"},
        {"label": "Provisioning", "url": "/hr/provisioning", "icon": "key-round", "perm": "provisioning.view"},
    ]},
    {"slug": "operations", "label": "Operations", "icon": "list-checks", "blurb": "Tasks, KPIs, governance, reports", "items": [
        {"label": "Tasks & Projects", "url": "/tasks", "icon": "list-checks", "perm": "tasks.view"},
        {"label": "KPIs", "url": "/kpis", "icon": "target", "perm": "kpis.view"},
        {"label": "Transformation OS", "url": "/transformation", "icon": "rocket", "perm": "transformation.view"},
        {"label": "Decision Register", "url": "/decisions", "icon": "gavel", "perm": "decisions.view"},
        {"label": "Daily Reports", "url": "/daily-reports", "icon": "file-clock", "perm": "daily_reports.view"},
        {"label": "Reports & Exports", "url": "/reports", "icon": "bar-chart-3", "perm": "reports.view"},
    ]},
    {"slug": "system", "label": "Configuration", "icon": "settings", "blurb": "Users, roles, integrations, security", "items": [
        {"label": "Users", "url": "/admin/users", "icon": "user-cog", "perm": "users.view"},
        {"label": "Roles & Permissions", "url": "/admin/roles", "icon": "shield", "perm": "roles.view"},
        {"label": "Settings", "url": "/admin/settings", "icon": "settings", "perm": "settings.view"},
        {"label": "Notifications", "url": "/admin/notifications", "icon": "send", "perm": "notifications.configure"},
        {"label": "Integration Hub", "url": "/admin/integrations", "icon": "plug", "perm": "integrations.view"},
        {"label": "API & Webhooks", "url": "/admin/api", "icon": "code-2", "perm": "api_keys.view"},
        {"label": "Security Center", "url": "/admin/security", "icon": "lock-keyhole", "perm": "security.view"},
        {"label": "Backups & DR", "url": "/admin/backups", "icon": "database-backup", "perm": "backups.view"},
        {"label": "Data Migration", "url": "/admin/migration", "icon": "database-zap", "perm": "migration.view"},
        {"label": "Audit Log", "url": "/admin/audit", "icon": "scroll-text", "perm": "audit.view"},
    ]},
]

TEACHER_NAV = [
    {"slug": "teaching", "label": "Teaching", "icon": "book-open", "blurb": "Your classes and students", "items": [
        {"label": "My Dashboard", "url": "/teacher", "icon": "layout-dashboard", "perm": "portal_teacher.view"},
        {"label": "My Schedule", "url": "/teacher/schedule", "icon": "calendar-days", "perm": "portal_teacher.view"},
        {"label": "My Classes", "url": "/teacher/classes", "icon": "video", "perm": "portal_teacher.view"},
        {"label": "My Students", "url": "/teacher/students", "icon": "graduation-cap", "perm": "portal_teacher.view"},
        {"label": "Lesson Plans", "url": "/teacher/lesson-plans", "icon": "notebook-pen", "perm": "portal_teacher.view"},
        {"label": "Evaluations", "url": "/teacher/evaluations", "icon": "clipboard-check", "perm": "portal_teacher.view"},
        {"label": "Monthly Tests", "url": "/teacher/monthly-tests", "icon": "file-badge", "perm": "portal_teacher.view"},
        {"label": "Trials", "url": "/teacher/trials", "icon": "flask-conical", "perm": "portal_teacher.view"},
    ]},
    {"slug": "me", "label": "Employee Self Portal", "icon": "user", "blurb": "Performance, attendance, payslips", "items": [
        {"label": "Performance", "url": "/teacher/performance", "icon": "trending-up", "perm": "portal_teacher.view"},
        {"label": "QA Feedback", "url": "/teacher/qa", "icon": "shield-check", "perm": "portal_teacher.view"},
        {"label": "Ustaadh Lab", "url": "/teacher/training", "icon": "sparkles", "perm": "portal_teacher.view"},
        {"label": "Attendance & Leaves", "url": "/teacher/hr", "icon": "clock", "perm": "portal_teacher.view"},
        {"label": "Income & Payslips", "url": "/teacher/income", "icon": "banknote", "perm": "portal_teacher.view"},
        {"label": "Daily Report", "url": "/daily-reports", "icon": "file-clock", "perm": "daily_reports.view"},
        {"label": "Tasks", "url": "/tasks", "icon": "list-checks", "perm": "tasks.view"},
    ]},
]

CLIENT_NAV = [
    {"slug": "family", "label": "My Family", "icon": "users", "blurb": "Classes, progress and results", "items": [
        {"label": "Home", "url": "/portal", "icon": "home", "perm": "portal_client.view"},
        {"label": "Schedule & Classes", "url": "/portal/schedule", "icon": "calendar-days", "perm": "portal_client.view"},
        {"label": "Attendance", "url": "/portal/attendance", "icon": "clipboard-check", "perm": "portal_client.view"},
        {"label": "Progress", "url": "/portal/progress", "icon": "trending-up", "perm": "portal_client.view"},
        {"label": "Result Cards", "url": "/portal/result-cards", "icon": "file-badge", "perm": "portal_client.view"},
        {"label": "Certificates", "url": "/portal/certificates", "icon": "award", "perm": "portal_client.view"},
        {"label": "Leave Requests", "url": "/portal/leaves", "icon": "calendar-off", "perm": "portal_client.view"},
    ]},
    {"slug": "account", "label": "My Account", "icon": "receipt", "blurb": "Billing, requests, referrals", "items": [
        {"label": "Invoices & Payments", "url": "/portal/billing", "icon": "receipt", "perm": "portal_client.view"},
        {"label": "Requests & Complaints", "url": "/portal/cases", "icon": "life-buoy", "perm": "portal_client.view"},
        {"label": "Feedback", "url": "/portal/feedback", "icon": "message-square-heart", "perm": "portal_client.view"},
        {"label": "Refer a Family", "url": "/portal/referrals", "icon": "gift", "perm": "portal_client.view"},
        {"label": "Profile & Consent", "url": "/portal/profile", "icon": "user", "perm": "portal_client.view"},
    ]},
]

STUDENT_NAV = [
    {"slug": "learning", "label": "My Learning", "icon": "book-open", "blurb": "Classes, lessons and results", "items": [
        {"label": "Home", "url": "/student", "icon": "home", "perm": "portal_student.view"},
        {"label": "My Classes", "url": "/student/classes", "icon": "video", "perm": "portal_student.view"},
        {"label": "My Progress", "url": "/student/progress", "icon": "trending-up", "perm": "portal_student.view"},
        {"label": "Lesson View", "url": "/student/lesson", "icon": "book-open", "perm": "portal_student.view"},
        {"label": "Results", "url": "/student/results", "icon": "file-badge", "perm": "portal_student.view"},
        {"label": "Certificates", "url": "/student/certificates", "icon": "award", "perm": "portal_student.view"},
    ]},
]

# Every portal lands on the launchpad, matching the drill-down style of the existing college ERP.
PORTAL_HOME = {"admin": "/home", "teacher": "/home", "client": "/home", "student": "/home", "auditor": "/home"}


def _source(user) -> list[dict]:
    portal = user.portal if user else "admin"
    return {"teacher": TEACHER_NAV, "client": CLIENT_NAV, "student": STUDENT_NAV}.get(portal, ADMIN_NAV)


def nav_for(user) -> list[dict]:
    """Sections and items the user may see, with a palette colour attached to each section and item."""
    out = []
    for i, section in enumerate(_source(user)):
        items = [dict(item, color=palette(j)) for j, item in enumerate(section["items"]) if rbac.has_permission(user, item["perm"])]
        if items:
            out.append(dict(section, items=items, color=palette(i)))
    return out


def section_for(user, slug: str) -> dict | None:
    for section in nav_for(user):
        if section["slug"] == slug:
            return section
    return None


def breadcrumbs_for(user, path: str) -> list[dict]:
    """Home › Section › Item for the current path, using the longest matching item URL."""
    crumbs = [{"label": "Home", "url": "/home"}]
    if path in ("/home", "/"):
        return crumbs
    best, best_section, best_len = None, None, -1
    for section in nav_for(user):
        if path == f"/home/{section['slug']}":
            return crumbs + [{"label": section["label"], "url": path}]
        for item in section["items"]:
            u = item["url"]
            if (path == u or path.startswith(u.rstrip("/") + "/")) and len(u) > best_len:
                best, best_section, best_len = item, section, len(u)
    if best_section:
        crumbs.append({"label": best_section["label"], "url": f"/home/{best_section['slug']}"})
    if best:
        crumbs.append({"label": best["label"], "url": best["url"]})
    return crumbs


def home_for(user) -> str:
    return PORTAL_HOME.get(user.portal if user else "admin", "/home")
