"""One-off: rbac modules/permissions, status labels, section template for grouped sections."""
import io


def sub(path, old, new):
    s = io.open(path, encoding="utf-8").read()
    if new in s:
        print(f"  already applied: {path}")
        return
    assert old in s, (path, old[:70])
    s = s.replace(old, new, 1)
    io.open(path, "w", encoding="utf-8", newline="\n").write(s)
    print(f"  patched {path}")


# ---- rbac: new modules
sub("app/core/rbac.py",
    '''    "registration": "Online Registration",
''',
    '''    "registration": "Online Registration",
    "requests": "Client Requests (leave, time/teacher change, references, complaints)",
    "dashboards": "Academic & Billing Dashboards",
    "academic_config": "Academic Configuration",
''')
sub("app/core/rbac.py",
    '''_FINANCE = ["subscriptions.*", "discounts.*", "scholarships.*", "billing.*", "payments.*", "ledger.*", "accounts.*",
            "expenses.*", "currencies.*", "payroll.*", "clients.view", "students.view", "packages.*", "kpis.*",
            "reports.*", "tasks.*", "decisions.*", "daily_reports.*", "dashboard.view", "notifications.*", "cases.view"]''',
    '''_FINANCE = ["subscriptions.*", "discounts.*", "scholarships.*", "billing.*", "payments.*", "ledger.*", "accounts.*",
            "expenses.*", "currencies.*", "payroll.*", "clients.view", "students.view", "packages.*", "kpis.*",
            "reports.*", "tasks.*", "decisions.*", "daily_reports.*", "dashboard.view", "notifications.*", "cases.view",
            "dashboards.*", "academic_config.view", "academic_config.configure"]''')
sub("app/core/rbac.py",
    '''             "classes.*", "attendance.*", "leaves.*", "qa.view", "ai_monitoring.view", "kpis.*", "tasks.*",
             "decisions.*", "daily_reports.*", "dashboard.*", "reports.*", "notifications.*", "cases.*", "supervisor.*"]''',
    '''             "classes.*", "attendance.*", "leaves.*", "qa.view", "ai_monitoring.view", "kpis.*", "tasks.*",
             "decisions.*", "daily_reports.*", "dashboard.*", "reports.*", "notifications.*", "cases.*", "supervisor.*",
             "requests.*", "dashboards.*", "academic_config.*", "subscriptions.*", "trials.*", "feedback.view"]''')
sub("app/core/rbac.py",
    '''_QA = ["qa.*", "ai_monitoring.*", "recordings.*", "classes.view", "teachers.view", "students.view", "teacher_dev.*",
       "cases.*", "feedback.view", "kpis.*", "tasks.*", "decisions.*", "daily_reports.*", "dashboard.view",
       "reports.*", "notifications.*", "safeguarding.view", "monthly_tests.view", "evaluations.view"]''',
    '''_QA = ["qa.*", "ai_monitoring.*", "recordings.*", "classes.view", "teachers.view", "students.view", "teacher_dev.*",
       "cases.*", "feedback.*", "kpis.*", "tasks.*", "decisions.*", "daily_reports.*", "dashboard.view",
       "reports.*", "notifications.*", "safeguarding.view", "monthly_tests.view", "evaluations.view", "dashboards.view",
       "requests.view"]''')
sub("app/core/rbac.py",
    '''        "ai_monitoring.view", "lesson_plans.view", "evaluations.view", "monthly_tests.view", "calling.*", "leads.view"]},''',
    '''        "ai_monitoring.view", "lesson_plans.view", "evaluations.view", "monthly_tests.view", "calling.*", "leads.view",
        "requests.*", "dashboards.view", "academic_config.view", "payments.view", "billing.add", "billing.update",
        "ledger.add", "registration.*"]},''')
sub("app/core/rbac.py",
    '''        "cases.update", "referrals.*", "tasks.*", "daily_reports.*", "notifications.*", "recordings.view",
        "lesson_plans.view", "calling.*", "kpis.view", "retention.view", "trials.view", "trials.update"]},''',
    '''        "cases.update", "referrals.*", "tasks.*", "daily_reports.*", "notifications.*", "recordings.view",
        "lesson_plans.view", "calling.*", "kpis.view", "retention.view", "trials.view", "trials.update",
        "requests.view", "requests.update", "requests.approve", "dashboards.view", "subscriptions.view", "qa.view"]},''')
sub("app/core/rbac.py",
    '''        "hr_attendance.add", "hr_attendance.view", "notifications.*", "tasks.view", "tasks.update", "curriculum.view",
        "daily_reports.add", "daily_reports.view", "grievances.add", "recordings.view", "trials.view", "trials.update"]},''',
    '''        "hr_attendance.add", "hr_attendance.view", "notifications.*", "tasks.view", "tasks.update", "curriculum.view",
        "daily_reports.add", "daily_reports.view", "grievances.add", "recordings.view", "trials.view", "trials.update",
        "requests.add", "requests.view"]},''')
sub("app/core/rbac.py",
    '''        "subscriptions.view", "subscriptions.update", "inbox.*", "cases.add", "cases.view", "tasks.*",
        "daily_reports.*", "notifications.*", "calling.*", "currencies.view", "reports.view", "reports.export"]},''',
    '''        "subscriptions.view", "subscriptions.update", "inbox.*", "cases.add", "cases.view", "tasks.*",
        "daily_reports.*", "notifications.*", "calling.*", "currencies.view", "reports.view", "reports.export",
        "dashboards.view", "requests.view", "academic_config.view"]},''')
sub("app/core/rbac.py",
    '''        "discounts.view", "packages.view", "teachers.view", "schedules.add", "schedules.view", "tasks.*",
        "daily_reports.*", "notifications.*", "calling.*", "marketing.view", "referrals.view"]},''',
    '''        "discounts.view", "packages.view", "teachers.view", "schedules.add", "schedules.view", "tasks.*",
        "daily_reports.*", "notifications.*", "calling.*", "marketing.view", "referrals.view", "dashboards.view",
        "requests.view", "requests.add"]},''')
sub("app/core/rbac.py",
    '''        "payroll.view", "currencies.*", "subscriptions.view", "clients.view", "reports.*", "tasks.*",
        "daily_reports.*", "notifications.*", "scholarships.view", "discounts.view"]},''',
    '''        "payroll.view", "currencies.*", "subscriptions.view", "clients.view", "reports.*", "tasks.*",
        "daily_reports.*", "notifications.*", "scholarships.view", "discounts.view", "dashboards.view",
        "academic_config.view"]},''')
sub("app/core/rbac.py",
    '''        "teachers.view", "students.view", "teacher_dev.view", "teacher_dev.add", "cases.view", "tasks.*",
        "daily_reports.*", "notifications.*", "monthly_tests.view", "evaluations.view"]},''',
    '''        "teachers.view", "students.view", "teacher_dev.view", "teacher_dev.add", "cases.view", "tasks.*",
        "daily_reports.*", "notifications.*", "monthly_tests.view", "evaluations.view", "feedback.*", "dashboards.view"]},''')
sub("app/core/rbac.py",
    '''        "teachers.view", "schedules.*", "classes.view", "classes.update", "attendance.*", "leaves.view",
        "tasks.*", "daily_reports.*", "notifications.*", "trials.view", "trials.update"]},''',
    '''        "teachers.view", "schedules.*", "classes.view", "classes.update", "attendance.*", "leaves.view",
        "tasks.*", "daily_reports.*", "notifications.*", "trials.view", "trials.update", "requests.*",
        "academic_config.*", "dashboards.view", "subscriptions.view", "subscriptions.add"]},''')

# ---- templating: labels + colours
sub("app/core/templating.py",
    '''    "analysed": "indigo", "overridden": "amber", "false_positive": "slate",
}
''',
    '''    "analysed": "indigo", "overridden": "amber", "false_positive": "slate",
    # ERP vocabularies
    "regular": "emerald", "freeze": "violet", "on_leave": "violet", "drop_out": "rose", "black_list": "slate",
    "pass_out": "indigo", "available": "sky", "flagged": "orange", "unmapped": "slate", "mapped": "sky",
    "in_review": "indigo", "add": "emerald", "minus": "rose", "forward_to_verifier": "amber",
}

# Display labels that match the college's existing ERP vocabulary. Use ``{{ value|label('student') }}``.
STATUS_LABELS: dict[str, dict[str, str]] = {
    "client": {"trial": "Trial", "active": "Regular", "regular": "Regular", "inactive": "Black List", "black_list": "Black List",
               "churned": "Drop Out", "drop_out": "Drop Out", "on_leave": "On Leave", "frozen": "On Leave", "pass_out": "Pass Out"},
    "student": {"trial": "Trial", "active": "Regular", "regular": "Regular", "frozen": "On Leave", "on_leave": "On Leave",
                "cancelled": "Drop Out", "drop_out": "Drop Out", "graduated": "Pass Out", "pass_out": "Pass Out",
                "black_list": "Black List", "free": "Free"},
    "subscription": {"pending_approval": "Pending Approval", "trial": "Trial", "active": "Regular", "regular": "Regular",
                     "frozen": "Freeze", "freeze": "Freeze", "cancelled": "Cancelled", "expired": "Completed", "completed": "Completed"},
    "invoice": {"draft": "Draft", "sent": "Pending", "pending": "Pending", "confirmed": "Confirmed", "partial": "Partially Paid",
                "paid": "Paid", "overdue": "Overdue", "void": "Cancelled", "cancelled": "Cancelled"},
    "receipt": {"pending": "Pending", "completed": "Confirmed", "confirmed": "Confirmed", "failed": "Failed",
                "refunded": "Refunded", "cancelled": "Cancelled"},
    "class": {"pending": "Pending", "available": "Teacher is Available", "started": "Started", "done": "Done", "missed": "Missed",
              "absent": "Student Absent", "leave": "Student On-Leave", "cancelled": "Cancelled", "rescheduled": "Rescheduled",
              "free": "Free"},
    "qa": {"queued": "Pending", "pending": "Pending", "in_review": "In-Progress", "in_progress": "In-Progress",
           "completed": "Completed", "approved": "Approved", "flagged": "Flagged", "rejected": "Rejected"},
    "request": {"pending": "Pending", "approved": "Approved", "rejected": "Rejected", "cancelled": "Cancelled"},
    "registration": {"new": "Pending", "contacted": "Forward to Verifier", "trial_scheduled": "Trial Scheduled",
                     "trial_done": "Trial Done", "negotiation": "Negotiation", "won": "Converted", "lost": "Lost"},
}


def label(value: Any, kind: str = "") -> str:
    """ERP-style display label for a status value (falls back to Title Case)."""
    if value is None:
        return "-"
    table = STATUS_LABELS.get(kind, {})
    return table.get(str(value), titleize(value))
''')
sub("app/core/templating.py",
    '''    "pct": pct,
})''',
    '''    "pct": pct, "label": label,
})''')
sub("app/core/templating.py",
    '''    "TILE": TILE, "palette": palette,
})''',
    '''    "TILE": TILE, "palette": palette, "STATUS_LABELS": STATUS_LABELS,
})''')
sub("app/core/templating.py",
    "from app.core.nav import nav_for, home_for, breadcrumbs_for, TILE, palette",
    "from app.core.nav import nav_for, home_for, breadcrumbs_for, TILE, palette  # noqa: F401")

# ---- section template: grouped sections render group cards
sub("app/templates/launchpad/section.html",
    '''    {% for item in section["items"] %}
      {{ ui.module_card(item.label, item.url, icon=item.icon, color=item.color) }}
    {% endfor %}''',
    '''    {% if section.groups %}
      {% for g in section.groups %}
        {{ ui.module_card(g.label, g.url, icon=g.icon, color=g.color, subtitle=g.blurb) }}
      {% endfor %}
    {% else %}
      {% for item in section["items"] %}
        {{ ui.module_card(item.label, item.url, icon=item.icon, color=item.color) }}
      {% endfor %}
    {% endif %}''')
print("done")
