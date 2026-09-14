"""Navigation model.

The UI follows the drill-down launchpad pattern of the college's existing ERP:

    Home  →  section (one card per section)  →  group (one card per group, e.g. "Class Management")  →  page

A section may list pages directly (``items``) or be organised into ``groups``; every leaf item is a real page.
Breadcrumbs are derived from the same data, so adding an item here is all that is needed to make it reachable
and labelled correctly. Items are gated by permission (see rbac). Icons are Lucide icon names.
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


def _i(label: str, url: str, icon: str, perm: str) -> dict:
    return {"label": label, "url": url, "icon": icon, "perm": perm}


# --------------------------------------------------------------------------- Online Academics (mirrors the ERP)
ACADEMIC_GROUPS = [
    {"slug": "dashboards", "label": "Dashboards", "icon": "gauge", "blurb": "Clients, subscriptions, billing, performance", "items": [
        _i("Client Management", "/dashboards/clients", "users", "dashboards.view"),
        _i("Subscriptions (Count)", "/dashboards/subscriptions", "repeat", "dashboards.view"),
        _i("Subscriptions (Amount)", "/dashboards/subscriptions/amount", "coins", "dashboards.view"),
        _i("Billing Management", "/dashboards/billing", "landmark", "dashboards.view"),
        _i("Monthly Performance Dashboard", "/dashboards/monthly-performance", "bar-chart-3", "dashboards.view"),
        _i("Financial Summary", "/dashboards/financial-summary", "pie-chart", "dashboards.view"),
        _i("Monthly Performance Insights", "/dashboards/monthly-insights", "trending-up", "dashboards.view"),
        _i("CEO Command Center", "/command-center", "crown", "command_center.view"),
    ]},
    {"slug": "clients", "label": "Client Management", "icon": "users", "blurb": "Families, trial clients, online registrations", "items": [
        _i("Client List", "/clients", "users", "clients.view"),
        _i("Trial Client List", "/clients/trial", "flask-conical", "clients.view"),
        _i("Online Registrations", "/registrations", "clipboard-list", "registration.view"),
        _i("Clients Users List", "/clients/users", "key-round", "clients.view"),
    ]},
    {"slug": "requests", "label": "Client Requests", "icon": "inbox", "blurb": "Leave, time and teacher changes, references, complaints", "items": [
        _i("Leave Applications", "/requests/leaves", "calendar-off", "requests.view"),
        _i("Time Change Requests", "/requests/time-change", "clock", "requests.view"),
        _i("Teacher Change Requests", "/requests/teacher-change", "user-cog", "requests.view"),
        _i("Refer New Contacts", "/requests/references", "gift", "requests.view"),
        _i("Complaints", "/requests/complaints", "life-buoy", "requests.view"),
    ]},
    {"slug": "students", "label": "Student Management", "icon": "graduation-cap", "blurb": "Students, referrals, leaves", "items": [
        _i("Student List", "/students", "graduation-cap", "students.view"),
        _i("Student Referred List", "/students/referred", "share-2", "students.view"),
        _i("Student Leaves", "/leaves/students", "calendar-off", "leaves.view"),
        _i("On Leave Students", "/students/on-leave", "plane", "students.view"),
        _i("Retention & Churn", "/retention", "heart-pulse", "retention.view"),
    ]},
    {"slug": "subscriptions", "label": "Subscription Management", "icon": "repeat", "blurb": "Create, allocate and report on subscriptions", "items": [
        _i("Create Subscription", "/subscriptions/new", "plus-square", "subscriptions.add"),
        _i("Faculty Allocation", "/subscriptions/allocation", "arrow-left-right", "subscriptions.view"),
        _i("All Subscriptions", "/subscriptions", "file-text", "subscriptions.view"),
        _i("All Subscriptions Value Report", "/subscriptions/value-report", "file-spreadsheet", "subscriptions.view"),
        _i("Subscription Detail Report", "/subscriptions/detail-report", "line-chart", "subscriptions.view"),
        _i("Cancelled Subscriptions", "/subscriptions/cancelled", "x-square", "subscriptions.view"),
    ]},
    {"slug": "classes", "label": "Class Management", "icon": "video", "blurb": "Trials, schedules, arrangements, queries", "items": [
        _i("Running Trials", "/trials/running", "flask-conical", "trials.view"),
        _i("Class Schedules", "/classes", "calendar-days", "classes.view"),
        _i("Class Arrangements", "/classes/arrangements", "arrow-left-right", "classes.update"),
        _i("Rescheduled Classes", "/classes/rescheduled", "calendar-clock", "classes.view"),
        _i("Class Status Summary", "/classes/status-summary", "layout-grid", "classes.view"),
        _i("Class Queries", "/classes/queries", "message-square-warning", "classes.view"),
        _i("Schedule Summary Report", "/classes/schedule-summary", "table", "classes.view"),
        _i("Recurring Schedules", "/schedules", "calendar-range", "schedules.view"),
    ]},
    {"slug": "billing", "label": "Billing Management", "icon": "receipt", "blurb": "Invoices, receipts, ledger", "items": [
        _i("Invoice List", "/finance/invoices", "receipt", "billing.view"),
        _i("Receipts", "/finance/receipts", "badge-check", "payments.view"),
        _i("Ledger Additions", "/finance/ledger-additions", "plus-minus", "ledger.view"),
        _i("Client Ledger Report", "/finance/ledger", "book-open-text", "ledger.view"),
    ]},
    {"slug": "evaluation", "label": "Evaluation", "icon": "clipboard-check", "blurb": "Evaluations, tests, lesson plans, certificates", "items": [
        _i("Evaluations", "/academics/evaluations", "clipboard-check", "evaluations.view"),
        _i("Pending Evaluations", "/academics/evaluations/pending", "clipboard-list", "evaluations.view"),
        _i("Lesson Plans", "/academics/lesson-plans", "notebook-pen", "lesson_plans.view"),
        _i("Monthly Tests", "/academics/monthly-tests", "file-badge", "monthly_tests.view"),
        _i("Certificates", "/academics/certificates", "award", "certificates.view"),
        _i("Curriculum", "/academics/curriculum", "library", "curriculum.view"),
    ]},
    {"slug": "quality", "label": "Quality Management", "icon": "shield-check", "blurb": "Call reviews, feedback, teacher QA", "items": [
        _i("QA Dashboard", "/qa/dashboard", "gauge", "qa.view"),
        _i("Client Feedbacks", "/qa/feedbacks", "message-square-heart", "feedback.view"),
        _i("Call Recordings", "/qa/calls", "phone-call", "qa.view"),
        _i("Agent Un-Matched Calls", "/qa/calls/unmatched", "phone-missed", "qa.view"),
        _i("QA Review Queue", "/qa/queue", "list-checks", "qa.view"),
        _i("Reviewed Calls", "/qa/reviewed", "check-check", "qa.view"),
        _i("Teacher QA Performance", "/qa/teacher-performance", "trending-up", "qa.view"),
        _i("Configurations", "/qa/config", "settings-2", "qa.configure"),
        _i("AI Class Monitoring", "/ai-monitoring", "brain-circuit", "ai_monitoring.view"),
        _i("Class Recordings", "/recordings", "film", "recordings.view"),
    ]},
    {"slug": "teacher-portal", "label": "Teacher Portal", "icon": "user-check", "blurb": "Today's classes and activity", "items": [
        _i("Class Schedule", "/teacher/online-class", "video", "classes.view"),
    ]},
    {"slug": "supervisor-portal", "label": "Supervisor Portal", "icon": "radio", "blurb": "Live monitoring", "items": [
        _i("Monitoring Dashboard", "/supervisor", "radio", "supervisor.view"),
    ]},
    {"slug": "hod-portal", "label": "HOD Portal", "icon": "briefcase", "blurb": "Academic manager view", "items": [
        _i("Monitoring Dashboard", "/hod", "briefcase", "supervisor.view"),
    ]},
    {"slug": "config", "label": "Academic Configuration", "icon": "settings", "blurb": "Sessions, courses, packages, books, accounts", "items": [
        _i("Sessions", "/academics/config/sessions", "clock", "academic_config.view"),
        _i("Courses", "/academics/config/courses", "book-open", "academic_config.view"),
        _i("Packages", "/academics/config/packages", "package", "academic_config.view"),
        _i("Define Books", "/academics/config/books", "book", "academic_config.view"),
        _i("Change Staff Sorting", "/academics/config/staff-sorting", "arrow-up-down", "academic_config.view"),
        _i("Invoice Addition List", "/academics/config/invoice-additions", "list-plus", "academic_config.view"),
        _i("Invoice Additions Master", "/academics/config/invoice-addition-rules", "sliders-horizontal", "academic_config.view"),
        _i("Receipt Beneficiary Accounts", "/academics/config/beneficiary-accounts", "landmark", "academic_config.view"),
        _i("Client Academic Groups", "/academics/config/client-groups", "users-round", "academic_config.view"),
        _i("MS Team Users", "/academics/config/teams-users", "monitor", "academic_config.view"),
        _i("Question Bank", "/academics/config/question-bank", "help-circle", "academic_config.view"),
        _i("Define Assessment", "/academics/config/assessments", "file-check", "academic_config.view"),
        _i("Course Divisions", "/academics/courses", "layers", "courses.view"),
    ]},
]

# --------------------------------------------------------------------------- Human Resource (mirrors the ERP)
HR_GROUPS = [
    {"slug": "dashboards", "label": "Dashboards", "icon": "gauge", "blurb": "Employees, attendance, cost", "items": [
        _i("Employee Management", "/hr/dashboards/employees", "users", "employees.view"),
        _i("Attendance Management", "/hr/dashboards/attendance", "clock", "hr_attendance.view"),
        _i("Financial Management", "/hr/dashboards/financial", "banknote", "payroll.view"),
    ]},
    {"slug": "employment", "label": "Employment Management", "icon": "id-card", "blurb": "Records, requests, violations, bonuses", "items": [
        _i("Employee Record", "/hr/employees", "id-card", "employees.view"),
        _i("Teachers", "/teachers", "user-check", "teachers.view"),
        _i("Employee Requests", "/hr/requests", "inbox", "employees.view"),
        _i("Staff Violations", "/hr/violations", "triangle-alert", "violations.view"),
        _i("Staff Bonuses", "/hr/bonuses", "gift", "payroll.view"),
        _i("Advance Requests", "/hr/advances", "hand-coins", "payroll.view"),
        _i("Complaints", "/hr/complaints", "life-buoy", "employees.view"),
        _i("Grievances (confidential)", "/hr/grievances", "lock", "grievances.view"),
        _i("Downloads", "/hr/downloads", "download", "portal_self.view"),
        _i("Provisioning", "/hr/provisioning", "key-round", "provisioning.view"),
    ]},
    {"slug": "attendance", "label": "Time and Attendance", "icon": "clock", "blurb": "Attendance, leaves, entitlements, progress", "items": [
        _i("Daily Attendance", "/hr/attendance", "clock", "hr_attendance.view"),
        _i("Attendance Change Requests", "/hr/attendance/change-requests", "file-clock", "hr_attendance.view"),
        _i("Employees Progress Sheet", "/hr/progress-sheet", "notebook-pen", "employees.view"),
        _i("Leave Assignment", "/hr/leave-entitlements", "calendar-check", "employees.view"),
        _i("Leave Management", "/hr/leaves", "plane", "leaves.view"),
        _i("Attendance Report", "/hr/attendance/report", "table", "employees.view"),
    ]},
    {"slug": "recruitment", "label": "Recruitment and Hiring", "icon": "briefcase", "blurb": "Vacancies, applications, interviews", "items": [
        _i("Job Requisitions", "/hr/recruitment", "briefcase", "recruitment.view"),
        _i("Job Applications", "/hr/applications", "file-text", "recruitment.view"),
        _i("Interview Panels", "/hr/interview-panels", "users-round", "recruitment.view"),
        _i("Schedule Interviews", "/hr/interviews", "calendar-days", "recruitment.view"),
        _i("Candidate Database", "/hr/candidates", "contact", "recruitment.view"),
        _i("Onboarding", "/hr/onboarding", "clipboard-check", "provisioning.view"),
        _i("Summary", "/hr/recruitment/summary", "bar-chart-3", "recruitment.view"),
    ]},
    {"slug": "benefits", "label": "Benefits Management", "icon": "heart-handshake", "blurb": "Grades, allowances, development", "items": [
        _i("Grades & Allowances", "/hr/config/grades", "layers", "employees.update"),
        _i("Ustaadh Lab", "/hr/teacher-development", "sparkles", "teacher_dev.view"),
    ]},
    {"slug": "financial", "label": "Financial Management", "icon": "banknote", "blurb": "Payroll", "items": [
        _i("Payroll", "/hr/payroll", "banknote", "payroll.view"),
        _i("Teacher Payroll", "/hr/payroll/teachers", "user-check", "payroll.view"),
        _i("Payroll Advances", "/hr/payroll/advances", "hand-coins", "payroll.view"),
        _i("Payroll Bonuses", "/hr/payroll/bonuses", "gift", "payroll.view"),
        _i("Salary Bands", "/hr/payroll/bands", "layers", "payroll.view"),
    ]},
    {"slug": "attachments", "label": "Attachments", "icon": "paperclip", "blurb": "Documents held against any record", "items": [
        _i("Attachments", "/hr/attachments", "paperclip", "employees.view"),
    ]},
    {"slug": "config", "label": "HR Configurations", "icon": "settings", "blurb": "Departments, shifts, holidays, types", "items": [
        _i("HR Configuration", "/hr/config", "settings", "employees.update"),
        _i("Departments", "/hr/config/departments", "building-2", "employees.update"),
        _i("Shifts", "/hr/config/shifts", "sun-moon", "employees.update"),
        _i("Holidays", "/hr/config/holidays", "calendar-x", "employees.update"),
        _i("Violation Types", "/hr/config/violation-types", "triangle-alert", "employees.update"),
        _i("Staff Bonus Types", "/hr/config/bonus-types", "gift", "employees.update"),
        _i("Users", "/hr/config/users", "user-cog", "users.view"),
        _i("Change Staff Sorting", "/academics/config/staff-sorting", "arrow-up-down", "academic_config.view"),
    ]},
]

ACCOUNT_GROUPS = [
    {"slug": "setup", "label": "Setup", "icon": "layers", "blurb": "Heads, chart of accounts, tree", "items": [
        _i("Accounts Heads", "/finance/accounts/heads", "layers", "accounts.view"),
        _i("Chart of Accounts", "/finance/accounts", "landmark", "accounts.view"),
        _i("Accounts Tree View", "/finance/accounts/tree", "git-branch", "accounts.view"),
    ]},
    {"slug": "transactions", "label": "Transactions", "icon": "arrow-left-right", "blurb": "Journal, payment and receipt vouchers", "items": [
        _i("All Vouchers", "/finance/accounts/vouchers", "receipt-text", "accounts.view"),
        _i("Journal Voucher", "/finance/accounts/vouchers/new?type=journal", "book", "accounts.add"),
        _i("Payment Voucher", "/finance/accounts/vouchers/new?type=payment", "arrow-up-right", "accounts.add"),
        _i("Receipt Voucher", "/finance/accounts/vouchers/new?type=receipt", "arrow-down-left", "accounts.add"),
        _i("Journal", "/finance/accounts/journal", "book-open-text", "accounts.view"),
        _i("Expenses", "/finance/expenses", "wallet", "expenses.view"),
    ]},
    {"slug": "reports", "label": "Reports", "icon": "bar-chart-3", "blurb": "Ledger, trial balance, statements", "items": [
        _i("Ledger Report", "/finance/accounts/reports/ledger", "book-open-text", "accounts.view"),
        _i("Trial Balance Report", "/finance/accounts/reports/trial-balance", "scale", "accounts.view"),
        _i("Income Statement", "/finance/accounts/reports/income-statement", "trending-up", "accounts.view"),
        _i("Balance Sheet", "/finance/accounts/reports/balance-sheet", "scale-3d", "accounts.view"),
        _i("Payables Summary", "/finance/accounts/reports/payables", "hourglass", "accounts.view"),
        _i("Account Wise Summary", "/finance/accounts/reports/account-wise", "table", "accounts.view"),
        _i("Approved Advances", "/finance/accounts/reports/approved-advances", "hand-coins", "accounts.view"),
        _i("Profit & Loss", "/finance/accounts/pnl", "pie-chart", "accounts.view"),
        _i("Cash Flow", "/finance/accounts/cash-flow", "waves", "accounts.view"),
        _i("Receivables Aging", "/finance/accounts/aging", "clock-alert", "accounts.view"),
        _i("Budgets", "/finance/accounts/budget", "calculator", "accounts.view"),
        _i("Period Close", "/finance/accounts/close", "lock", "accounts.view"),
        _i("Payables Detail", "/finance/accounts/payables", "hourglass", "accounts.view"),
        _i("Consolidated Statement", "/finance/accounts/consolidated", "layers", "accounts.view"),
        _i("Forecast", "/finance/accounts/forecast", "line-chart", "accounts.view"),
    ]},
]

# Each section: {"slug", "label", "icon", "blurb", "items": [...]} or {"slug", ..., "groups": [...]}
ADMIN_NAV = [
    {"slug": "academics", "label": "Online Academics", "icon": "book-open", "blurb": "Clients, students, subscriptions, classes, quality",
     "home": "/dashboard", "groups": ACADEMIC_GROUPS},
    {"slug": "finance", "label": "Billing Management", "icon": "receipt", "blurb": "Subscriptions, invoices, receipts, discounts", "items": [
        _i("Invoice List", "/finance/invoices", "receipt", "billing.view"),
        _i("Receipts", "/finance/receipts", "badge-check", "payments.view"),
        _i("Payments & Reconciliation", "/finance/payments", "credit-card", "payments.view"),
        _i("Ledger Additions", "/finance/ledger-additions", "plus-minus", "ledger.view"),
        _i("Client Ledger Report", "/finance/ledger", "book-open-text", "ledger.view"),
        _i("All Subscriptions", "/subscriptions", "repeat", "subscriptions.view"),
        _i("Bulk Invoice Generation", "/finance/invoices/bulk", "layers", "billing.add"),
        _i("Credit Notes", "/finance/ledger/credits", "file-minus", "ledger.view"),
        _i("Payment Reconciliation", "/finance/payments/reconciliation", "scale", "payments.view"),
        _i("Failed Payments", "/finance/payments/failed", "circle-x", "payments.view"),
        _i("Discount Register", "/finance/discounts/register", "list-checks", "discounts.view"),
        _i("Scholarships", "/finance/discounts/scholarships", "graduation-cap", "discounts.view"),
        _i("Subscriptions Report", "/finance/subscriptions/report", "file-spreadsheet", "subscriptions.view"),
        _i("Discounts & Scholarships", "/finance/discounts", "percent", "discounts.view"),
        _i("Currencies", "/finance/currencies", "coins", "currencies.view"),
        _i("Billing Dashboard", "/dashboards/billing", "gauge", "dashboards.view"),
        _i("Financial Summary", "/dashboards/financial-summary", "pie-chart", "dashboards.view"),
    ]},
    {"slug": "hr", "label": "Human Resource", "icon": "id-card",
     "blurb": "Employees, attendance, recruitment, payroll", "groups": HR_GROUPS},
    {"slug": "self", "label": "Employee Self Portal", "icon": "user", "blurb": "Your attendance, leaves, payslips and record", "items": [
        _i("My Overview", "/hr/me", "layout-dashboard", "portal_self.view"),
        _i("My Attendance", "/hr/me?tab=attendance", "clock", "portal_self.view"),
        _i("My Leaves", "/hr/me?tab=leaves", "plane", "portal_self.view"),
        _i("My Payslips", "/hr/me?tab=payslips", "banknote", "portal_self.view"),
        _i("My Record", "/hr/me?tab=violations", "shield-alert", "portal_self.view"),
        _i("My Development", "/hr/me?tab=development", "sparkles", "portal_self.view"),
        _i("Raise a Grievance", "/hr/me?tab=grievance", "lock", "portal_self.view"),
        _i("Daily Report", "/daily-reports", "file-clock", "daily_reports.view"),
        _i("My Tasks", "/tasks", "list-checks", "tasks.view"),
        _i("My Profile", "/profile", "user-cog", "portal_self.view"),
    ]},
    {"slug": "accounts", "label": "Accounts", "icon": "landmark",
     "blurb": "Chart of accounts, vouchers, statements", "groups": ACCOUNT_GROUPS},
    {"slug": "crm", "label": "CRM & Growth", "icon": "megaphone", "blurb": "Leads, WhatsApp, trials, campaigns", "items": [
        _i("Leads & Pipeline", "/crm/leads", "funnel", "leads.view"),
        _i("WhatsApp Inbox", "/crm/inbox", "message-circle", "inbox.view"),
        _i("Trials", "/trials", "flask-conical", "trials.view"),
        _i("Online Registrations", "/registrations", "clipboard-list", "registration.view"),
        _i("Campaigns", "/crm/campaigns", "megaphone", "campaigns.view"),
        _i("Marketing Analytics", "/crm/marketing", "trending-up", "marketing.view"),
        _i("Sequences", "/crm/sequences", "workflow", "sequences.view"),
        _i("Ambassadors", "/crm/referrals", "gift", "referrals.view"),
        _i("Cases & Complaints", "/cases", "life-buoy", "cases.view"),
        _i("Feedback & VoC", "/feedback", "message-square-heart", "feedback.view"),
    ]},
    {"slug": "operations", "label": "Operations", "icon": "list-checks", "blurb": "Tasks, KPIs, governance, reports", "items": [
        _i("Supervisor Live", "/supervisor", "radio", "supervisor.view"),
        _i("Alerts", "/alerts", "bell-ring", "dashboard.view"),
        _i("Tasks & Projects", "/tasks", "list-checks", "tasks.view"),
        _i("KPIs", "/kpis", "target", "kpis.view"),
        _i("KPI Scorecards", "/kpis/scorecards", "clipboard-list", "kpis.view"),
        _i("KPIs by Role", "/kpis/roles", "users-round", "kpis.view"),
        _i("Transformation OS", "/transformation", "rocket", "transformation.view"),
        _i("Decision Register", "/decisions", "gavel", "decisions.view"),
        _i("Daily Reports", "/daily-reports", "file-clock", "daily_reports.view"),
        _i("Reports & Exports", "/reports", "bar-chart-3", "reports.view"),
        _i("Safeguarding", "/safeguarding", "shield-alert", "safeguarding.view"),
        _i("AI Governance", "/ai-governance", "scale", "ai_governance.view"),
    ]},
    {"slug": "system", "label": "Configuration", "icon": "settings", "blurb": "Users, roles, integrations, security", "items": [
        _i("Branch Properties", "/config/branch-properties", "sliders-horizontal", "settings.view"),
        _i("Lookups", "/config/lookups", "list", "settings.view"),
        _i("Currency Rates", "/config/currency-rates", "coins", "currencies.view"),
        _i("Payment Gateways", "/config/payment-gateways", "credit-card", "settings.view"),
        _i("WhatsApp Numbers", "/config/whatsapp-senders", "message-circle", "settings.view"),
        _i("OTP Configuration", "/config/otp", "shield-check", "security.view"),
        _i("Support Ticket", "/config/support-tickets", "life-buoy", "settings.view"),
        _i("Users", "/admin/users", "user-cog", "users.view"),
        _i("Roles & Permissions", "/admin/roles", "shield", "roles.view"),
        _i("Roles by Application", "/config/roles", "shield-check", "roles.view"),
        _i("Settings", "/admin/settings", "settings", "settings.view"),
        _i("Notifications", "/admin/notifications", "send", "notifications.configure"),
        _i("Integration Hub", "/admin/integrations", "plug", "integrations.view"),
        _i("API & Webhooks", "/admin/api", "code-2", "api_keys.view"),
        _i("Security Center", "/admin/security", "lock-keyhole", "security.view"),
        _i("Backups & DR", "/admin/backups", "database-backup", "backups.view"),
        _i("Data Migration", "/admin/migration", "database-zap", "migration.view"),
        _i("Audit Log", "/admin/audit", "scroll-text", "audit.view"),
    ]},
]

TEACHER_NAV = [
    {"slug": "teaching", "label": "Teaching", "icon": "book-open", "blurb": "Your classes and students", "items": [
        _i("Online Class", "/teacher/online-class", "video", "portal_teacher.view"),
        _i("My Dashboard", "/teacher", "layout-dashboard", "portal_teacher.view"),
        _i("My Schedule", "/teacher/schedule", "calendar-days", "portal_teacher.view"),
        _i("My Classes", "/teacher/classes", "video", "portal_teacher.view"),
        _i("My Students", "/teacher/students", "graduation-cap", "portal_teacher.view"),
        _i("Lesson Plans", "/teacher/lesson-plans", "notebook-pen", "portal_teacher.view"),
        _i("Evaluations", "/teacher/evaluations", "clipboard-check", "portal_teacher.view"),
        _i("Monthly Tests", "/teacher/monthly-tests", "file-badge", "portal_teacher.view"),
        _i("Trials", "/teacher/trials", "flask-conical", "portal_teacher.view"),
    ]},
    {"slug": "me", "label": "Employee Self Portal", "icon": "user", "blurb": "Performance, attendance, payslips", "items": [
        _i("Performance", "/teacher/performance", "trending-up", "portal_teacher.view"),
        _i("QA Feedback", "/teacher/qa", "shield-check", "portal_teacher.view"),
        _i("Ustaadh Lab", "/teacher/training", "sparkles", "portal_teacher.view"),
        _i("Attendance & Leaves", "/teacher/hr", "clock", "portal_teacher.view"),
        _i("Income & Payslips", "/teacher/income", "banknote", "portal_teacher.view"),
        _i("Daily Report", "/daily-reports", "file-clock", "daily_reports.view"),
        _i("Tasks", "/tasks", "list-checks", "tasks.view"),
    ]},
]

CLIENT_NAV = [
    {"slug": "family", "label": "My Family", "icon": "users", "blurb": "Classes, progress and results", "items": [
        _i("Home", "/portal", "home", "portal_client.view"),
        _i("Schedule & Classes", "/portal/schedule", "calendar-days", "portal_client.view"),
        _i("Attendance", "/portal/attendance", "clipboard-check", "portal_client.view"),
        _i("Progress", "/portal/progress", "trending-up", "portal_client.view"),
        _i("Result Cards", "/portal/result-cards", "file-badge", "portal_client.view"),
        _i("Certificates", "/portal/certificates", "award", "portal_client.view"),
        _i("Leave Requests", "/portal/leaves", "calendar-off", "portal_client.view"),
    ]},
    {"slug": "account", "label": "My Account", "icon": "receipt", "blurb": "Billing, requests, referrals", "items": [
        _i("Invoices & Payments", "/portal/billing", "receipt", "portal_client.view"),
        _i("Change Requests", "/portal/requests", "clock", "portal_client.view"),
        _i("Requests & Complaints", "/portal/cases", "life-buoy", "portal_client.view"),
        _i("Feedback", "/portal/feedback", "message-square-heart", "portal_client.view"),
        _i("Refer a Family", "/portal/referrals", "gift", "portal_client.view"),
        _i("Profile & Consent", "/portal/profile", "user", "portal_client.view"),
    ]},
]

STUDENT_NAV = [
    {"slug": "learning", "label": "My Learning", "icon": "book-open", "blurb": "Classes, lessons and results", "items": [
        _i("Home", "/student", "home", "portal_student.view"),
        _i("My Classes", "/student/classes", "video", "portal_student.view"),
        _i("My Progress", "/student/progress", "trending-up", "portal_student.view"),
        _i("Lesson View", "/student/lesson", "book-open", "portal_student.view"),
        _i("Results", "/student/results", "file-badge", "portal_student.view"),
        _i("Certificates", "/student/certificates", "award", "portal_student.view"),
    ]},
]

# Every portal lands on the launchpad, matching the drill-down style of the existing college ERP.
PORTAL_HOME = {"admin": "/home", "teacher": "/home", "client": "/home", "student": "/home", "auditor": "/home"}


def _source(user) -> list[dict]:
    portal = user.portal if user else "admin"
    return {"teacher": TEACHER_NAV, "client": CLIENT_NAV, "student": STUDENT_NAV}.get(portal, ADMIN_NAV)


def _allowed(user, items: list[dict]) -> list[dict]:
    return [dict(item, color=palette(j)) for j, item in enumerate(items) if rbac.has_permission(user, item["perm"])]


def nav_for(user) -> list[dict]:
    """Sections (with groups/items) the user may see, with a palette colour attached to every card.

    Every section carries a flat ``items`` list (all leaf pages) so breadcrumbs and search work uniformly;
    sections organised in groups also carry ``groups`` with their own filtered items.
    """
    out = []
    for i, section in enumerate(_source(user)):
        if section.get("groups"):
            groups = []
            for j, g in enumerate(section["groups"]):
                items = _allowed(user, g["items"])
                if items:
                    groups.append(dict(g, items=items, color=palette(j), url=f"/home/{section['slug']}/{g['slug']}"))
            if not groups:
                continue
            flat = [it for g in groups for it in g["items"]]
            out.append(dict(section, groups=groups, items=flat, color=palette(i)))
        else:
            items = _allowed(user, section["items"])
            if items:
                out.append(dict(section, items=items, color=palette(i)))
    return out


def section_for(user, slug: str) -> dict | None:
    for section in nav_for(user):
        if section["slug"] == slug:
            return section
    return None


def group_for(user, slug: str, group_slug: str) -> tuple[dict | None, dict | None]:
    section = section_for(user, slug)
    if not section:
        return None, None
    for g in section.get("groups", []):
        if g["slug"] == group_slug:
            return section, g
    return section, None


def breadcrumbs_for(user, path: str) -> list[dict]:
    """Home › Section › Group › Item for the current path, using the longest matching item URL."""
    crumbs = [{"label": "Home", "url": "/home"}]
    if path in ("/home", "/"):
        return crumbs
    best, best_section, best_group, best_len = None, None, None, -1
    for section in nav_for(user):
        if path == f"/home/{section['slug']}":
            return crumbs + [{"label": section["label"], "url": path}]
        for g in section.get("groups", []):
            if path == g["url"]:
                return crumbs + [{"label": section["label"], "url": f"/home/{section['slug']}"}, {"label": g["label"], "url": path}]
            for item in g["items"]:
                u = item["url"]
                if (path == u or path.startswith(u.rstrip("/") + "/")) and len(u) > best_len:
                    best, best_section, best_group, best_len = item, section, g, len(u)
        for item in section["items"] if not section.get("groups") else []:
            u = item["url"]
            if (path == u or path.startswith(u.rstrip("/") + "/")) and len(u) > best_len:
                best, best_section, best_group, best_len = item, section, None, len(u)
    if best_section:
        crumbs.append({"label": best_section["label"], "url": f"/home/{best_section['slug']}"})
    if best_group:
        crumbs.append({"label": best_group["label"], "url": best_group["url"]})
    if best:
        crumbs.append({"label": best["label"], "url": best["url"]})
    return crumbs


def home_for(user) -> str:
    return PORTAL_HOME.get(user.portal if user else "admin", "/home")
