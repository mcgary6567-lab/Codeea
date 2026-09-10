"""Sidebar navigation per portal. Items are gated by permission (see rbac). Icons are Lucide icon names."""
from __future__ import annotations

from app.core import rbac

# Each section: {"label", "items": [{"label", "url", "icon", "perm"}]}
ADMIN_NAV = [
    {"label": "Overview", "items": [
        {"label": "Academic Home", "url": "/dashboard", "icon": "layout-dashboard", "perm": "dashboard.view"},
        {"label": "CEO Command Center", "url": "/command-center", "icon": "gauge", "perm": "command_center.view"},
        {"label": "Supervisor Live", "url": "/supervisor", "icon": "radio", "perm": "supervisor.view"},
        {"label": "Alerts", "url": "/alerts", "icon": "bell-ring", "perm": "dashboard.view"},
    ]},
    {"label": "CRM & Growth", "items": [
        {"label": "Leads & Pipeline", "url": "/crm/leads", "icon": "funnel", "perm": "leads.view"},
        {"label": "WhatsApp Inbox", "url": "/crm/inbox", "icon": "message-circle", "perm": "inbox.view"},
        {"label": "Trials", "url": "/trials", "icon": "flask-conical", "perm": "trials.view"},
        {"label": "Campaigns", "url": "/crm/campaigns", "icon": "megaphone", "perm": "campaigns.view"},
        {"label": "Marketing Analytics", "url": "/crm/marketing", "icon": "trending-up", "perm": "marketing.view"},
        {"label": "Sequences", "url": "/crm/sequences", "icon": "workflow", "perm": "sequences.view"},
        {"label": "Ambassadors", "url": "/crm/referrals", "icon": "gift", "perm": "referrals.view"},
    ]},
    {"label": "Clients & Students", "items": [
        {"label": "Clients / Parents", "url": "/clients", "icon": "users", "perm": "clients.view"},
        {"label": "Students", "url": "/students", "icon": "graduation-cap", "perm": "students.view"},
        {"label": "Registrations", "url": "/registrations", "icon": "clipboard-list", "perm": "registration.view"},
        {"label": "Retention & Churn", "url": "/retention", "icon": "heart-pulse", "perm": "retention.view"},
        {"label": "Cases & Complaints", "url": "/cases", "icon": "life-buoy", "perm": "cases.view"},
        {"label": "Feedback & VoC", "url": "/feedback", "icon": "message-square-heart", "perm": "feedback.view"},
    ]},
    {"label": "Academics", "items": [
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
    {"label": "Quality & AI", "items": [
        {"label": "QA Queue", "url": "/qa", "icon": "shield-check", "perm": "qa.view"},
        {"label": "AI Class Monitoring", "url": "/ai-monitoring", "icon": "brain-circuit", "perm": "ai_monitoring.view"},
        {"label": "Recordings", "url": "/recordings", "icon": "film", "perm": "recordings.view"},
        {"label": "Safeguarding", "url": "/safeguarding", "icon": "shield-alert", "perm": "safeguarding.view"},
        {"label": "AI Governance", "url": "/ai-governance", "icon": "scale", "perm": "ai_governance.view"},
    ]},
    {"label": "Finance", "items": [
        {"label": "Subscriptions", "url": "/finance/subscriptions", "icon": "repeat", "perm": "subscriptions.view"},
        {"label": "Invoices", "url": "/finance/invoices", "icon": "receipt", "perm": "billing.view"},
        {"label": "Payments", "url": "/finance/payments", "icon": "credit-card", "perm": "payments.view"},
        {"label": "Discounts & Scholarships", "url": "/finance/discounts", "icon": "percent", "perm": "discounts.view"},
        {"label": "Accounts & P&L", "url": "/finance/accounts", "icon": "landmark", "perm": "accounts.view"},
        {"label": "Expenses", "url": "/finance/expenses", "icon": "wallet", "perm": "expenses.view"},
        {"label": "Currencies", "url": "/finance/currencies", "icon": "coins", "perm": "currencies.view"},
    ]},
    {"label": "People & Culture", "items": [
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
    {"label": "Operations", "items": [
        {"label": "Tasks & Projects", "url": "/tasks", "icon": "list-checks", "perm": "tasks.view"},
        {"label": "KPIs", "url": "/kpis", "icon": "target", "perm": "kpis.view"},
        {"label": "Transformation OS", "url": "/transformation", "icon": "rocket", "perm": "transformation.view"},
        {"label": "Decision Register", "url": "/decisions", "icon": "gavel", "perm": "decisions.view"},
        {"label": "Daily Reports", "url": "/daily-reports", "icon": "file-clock", "perm": "daily_reports.view"},
        {"label": "Reports & Exports", "url": "/reports", "icon": "bar-chart-3", "perm": "reports.view"},
    ]},
    {"label": "System", "items": [
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
    {"label": "Teaching", "items": [
        {"label": "My Dashboard", "url": "/teacher", "icon": "layout-dashboard", "perm": "portal_teacher.view"},
        {"label": "My Schedule", "url": "/teacher/schedule", "icon": "calendar-days", "perm": "portal_teacher.view"},
        {"label": "My Classes", "url": "/teacher/classes", "icon": "video", "perm": "portal_teacher.view"},
        {"label": "My Students", "url": "/teacher/students", "icon": "graduation-cap", "perm": "portal_teacher.view"},
        {"label": "Lesson Plans", "url": "/teacher/lesson-plans", "icon": "notebook-pen", "perm": "portal_teacher.view"},
        {"label": "Evaluations", "url": "/teacher/evaluations", "icon": "clipboard-check", "perm": "portal_teacher.view"},
        {"label": "Monthly Tests", "url": "/teacher/monthly-tests", "icon": "file-badge", "perm": "portal_teacher.view"},
        {"label": "Trials", "url": "/teacher/trials", "icon": "flask-conical", "perm": "portal_teacher.view"},
    ]},
    {"label": "Me", "items": [
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
    {"label": "Family", "items": [
        {"label": "Home", "url": "/portal", "icon": "home", "perm": "portal_client.view"},
        {"label": "Schedule & Classes", "url": "/portal/schedule", "icon": "calendar-days", "perm": "portal_client.view"},
        {"label": "Attendance", "url": "/portal/attendance", "icon": "clipboard-check", "perm": "portal_client.view"},
        {"label": "Progress", "url": "/portal/progress", "icon": "trending-up", "perm": "portal_client.view"},
        {"label": "Result Cards", "url": "/portal/result-cards", "icon": "file-badge", "perm": "portal_client.view"},
        {"label": "Certificates", "url": "/portal/certificates", "icon": "award", "perm": "portal_client.view"},
        {"label": "Leave Requests", "url": "/portal/leaves", "icon": "calendar-off", "perm": "portal_client.view"},
    ]},
    {"label": "Account", "items": [
        {"label": "Invoices & Payments", "url": "/portal/billing", "icon": "receipt", "perm": "portal_client.view"},
        {"label": "Requests & Complaints", "url": "/portal/cases", "icon": "life-buoy", "perm": "portal_client.view"},
        {"label": "Feedback", "url": "/portal/feedback", "icon": "message-square-heart", "perm": "portal_client.view"},
        {"label": "Refer a Family", "url": "/portal/referrals", "icon": "gift", "perm": "portal_client.view"},
        {"label": "Profile & Consent", "url": "/portal/profile", "icon": "user", "perm": "portal_client.view"},
    ]},
]

STUDENT_NAV = [
    {"label": "Learning", "items": [
        {"label": "Home", "url": "/student", "icon": "home", "perm": "portal_student.view"},
        {"label": "My Classes", "url": "/student/classes", "icon": "video", "perm": "portal_student.view"},
        {"label": "My Progress", "url": "/student/progress", "icon": "trending-up", "perm": "portal_student.view"},
        {"label": "Lesson View", "url": "/student/lesson", "icon": "book-open", "perm": "portal_student.view"},
        {"label": "Results", "url": "/student/results", "icon": "file-badge", "perm": "portal_student.view"},
        {"label": "Certificates", "url": "/student/certificates", "icon": "award", "perm": "portal_student.view"},
    ]},
]

PORTAL_HOME = {"admin": "/dashboard", "teacher": "/teacher", "client": "/portal", "student": "/student", "auditor": "/dashboard"}


def nav_for(user) -> list[dict]:
    portal = user.portal if user else "admin"
    source = {"teacher": TEACHER_NAV, "client": CLIENT_NAV, "student": STUDENT_NAV}.get(portal, ADMIN_NAV)
    out = []
    for section in source:
        items = [i for i in section["items"] if rbac.has_permission(user, i["perm"])]
        if items:
            out.append({"label": section["label"], "items": items})
    return out


def home_for(user) -> str:
    return PORTAL_HOME.get(user.portal if user else "admin", "/dashboard")
