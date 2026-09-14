"""Configuration seed (docs/AUDIT_ACCOUNTS_CONFIG.md — the Configuration area).

The value lists the college configures (lookups), the branch properties their Setup screen keeps, the payment
gateway catalogue, the WhatsApp senders with their throttles, the one-time password configuration and a
realistic backlog of support tickets.

Idempotent: every insert checks for an existing row first, so `seed.py` can be re-run over a live database.
Lookup codes are stable — `app.services.lookups` reads them by code, so renaming one breaks callers; that is
why each lookup below is marked `is_system`.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.utils import next_code
from app.models.config_erp import Lookup, LookupValue, OtpConfiguration, PaymentGateway, SupportTicket, WhatsAppSender
from app.models.core import Department, Setting, User
from app.models.erp import BeneficiaryAccount

HR, ACC, ACAD, CFG, PORTAL, BILL = ("Human Resource", "Accounts", "Online Academics", "Configuration",
                                    "Client Portal", "Billing Management")

# code, app, description, sort_no, [(value, label, label_urdu, amount)]
LOOKUPS: list[tuple[str, str, str, int, list[tuple]]] = [
    ("hr_designation", HR, "HR Designation List", 10, [
        (d, d, None, None) for d in [
            "Academic Excellence", "Academic Manager", "Accountant", "Admission Incharge", "Ayyah", "CEO", "COO",
            "Director", "Finance Head", "IT Head", "Manager", "Observer", "P&C Head", "QA Observer", "Supervisor",
            "Supporting Staff", "Teacher On-Site", "Teacher Remote", "Trainer"]]),
    ("hr_leaving_reason", HR, "HR Employees Leaving Reasons", 20, [
        ("Terminated", "Terminated", "برطرف", None),
        ("Resigned", "Resigned", "مستعفی", None),
        ("Contract completed", "Contract completed", "معاہدہ مکمل", None),
        ("Marriage", "Marriage", "شادی", None)]),
    ("emp_document_type", HR, "EMP Document Type", 30, [
        ("CNIC", "CNIC / National ID", None, None),
        ("Passport", "Passport", None, None),
        ("Degree", "Degree / Certificate", None, None),
        ("Experience Letter", "Experience Letter", None, None),
        ("Contract", "Employment Contract", None, None),
        ("Photo", "Passport Size Photo", None, None),
        ("Bank Letter", "Bank Account Letter", None, None),
        ("Police Verification", "Police Verification", None, None)]),
    ("hr_allowance_type", HR, "HR Allowance Types", 40, [
        ("DA", "DA (Daily Allowance)", "یومیہ الاؤنس", 1500),
        ("Home Allowance", "Home Allowance", "رہائشی الاؤنس", 8000),
        ("Internet Allowance", "Internet Allowance", None, 3000),
        ("Fuel Allowance", "Fuel Allowance", None, 5000)]),
    ("hr_complaint_type", HR, "HR Complaints Types", 50, [
        ("Admin", "Admin", None, None), ("Staff", "Staff", None, None), ("HR", "HR", None, None)]),
    ("hr_employee_request_type", HR, "HR Employee Requests", 60, [
        ("Shift Change", "Shift Change", None, None),
        ("Advance Salary", "Advance Salary", None, None),
        ("Equipment", "Equipment / IT", None, None),
        ("Experience Letter", "Experience Letter", None, None),
        ("Salary Certificate", "Salary Certificate", None, None),
        ("Leave Encashment", "Leave Encashment", None, None),
        ("Transfer", "Department Transfer", None, None),
        ("Other", "Other", None, None)]),
    ("hr_how_came_to_us", HR, "HR How Came To Us", 70, [
        ("Social Media", "Social Media", None, None),
        ("Sales Representative", "Sales Representative", None, None),
        ("Google", "Google", None, None),
        ("Others", "Others", None, None)]),
    ("hr_bpo_sales_team", HR, "HR BPO Sales Team List", 80, [
        ("Team A", "Team A - UK Shift", None, None),
        ("Team B", "Team B - US Shift", None, None),
        ("Team C", "Team C - Gulf Shift", None, None)]),
    ("acc_financial_year", ACC, "Acc Financial Year", 10, [
        ("2024-2025", "FY 2024-2025", None, None),
        ("2025-2026", "FY 2025-2026", None, None),
        ("2026-2027", "FY 2026-2027", None, None)]),
    ("payment_mode", ACC, "Payment Modes", 20, [
        ("Online Payment Gateway", "Online Payment Gateway", None, None),
        ("Bank", "Bank Transfer", None, None),
        ("Cash", "Cash", None, None),
        ("Cheque", "Cheque", None, None),
        ("Adjustment", "Adjustment / Credit Note", None, None)]),
    ("class_query_type", ACAD, "Class Query Types", 10, [
        ("Teacher Absent", "Teacher Absent", None, None),
        ("Student Absent", "Student Absent", None, None),
        ("Audio Problem", "Audio Problem", None, None),
        ("Video Problem", "Video Problem", None, None),
        ("Timing Clash", "Timing Clash", None, None),
        ("Book Not Covered", "Book Not Covered", None, None),
        ("Other", "Other", None, None)]),
    ("reference_type", ACAD, "Reference Types", 20, [
        ("Family", "Family Member", None, None),
        ("Friend", "Friend", None, None),
        ("Colleague", "Colleague", None, None),
        ("Neighbour", "Neighbour", None, None),
        ("Community", "Community / Masjid", None, None)]),
    ("family_complaint_type", PORTAL, "Complaint Types", 10, [
        ("Teaching Quality", "Teaching Quality", "معیارِ تدریس", None),
        ("Teacher Punctuality", "Teacher Punctuality", "وقت کی پابندی", None),
        ("Billing", "Billing", "بلنگ", None),
        ("Class Timing", "Class Timing", "کلاس کا وقت", None),
        ("Technical", "Technical / Connection", None, None),
        ("Behaviour", "Behaviour", None, None),
        ("Other", "Other", None, None)]),
    ("language", CFG, "Languages", 10, [
        ("en", "English", None, None),
        ("ur", "Urdu", "اردو", None),
        ("ar", "Arabic", "عربی", None),
        ("pa", "Punjabi", None, None),
        ("ps", "Pashto", None, None)]),
]

# key, group, label, description, value, value_type, unit, is_secret, is_editable, sort_no
BRANCH_PROPERTIES: list[tuple] = [
    ("branch_display_name", "general", "Branch Display Name",
     "The name printed on documents and shown in the top bar.", "Online Quran College", "text", None, False, True, 10),
    ("secret_pin_code", "general", "Secret Pin Code",
     "Pin required to release a payroll run or void a receipt. Masked on screen; revealing it is audited.",
     "483920", "text", None, True, True, 20),
    ("erp_report_header_image", "general", "ERP HTML Reports Headers Picture",
     "Image placed at the top of every printed HTML report.", "/static/img/report-header.png", "image", None, False, True, 30),
    ("erp_schema_version", "general", "ERP Schema Version",
     "Set by the release process. Shown here so support can confirm the build; not editable by staff.",
     "2026.09", "text", None, False, False, 40),
    ("attendance_login_time_relaxation", "hr", "Attendance Login Time Relaxation",
     "Grace period after the shift start before a late arrival attracts a fine.", 10, "number", "minutes", False, True, 10),
    ("attendance_logout_time_relaxation", "hr", "Attendance Logout Time Relaxation",
     "Grace period before the shift end before an early leave is recorded as a shortage.", 10, "number", "minutes", False, True, 20),
    ("default_ctc_value", "hr", "Employee Basic Detail - Default CTC Value",
     "Default cost to company used when estimating the expense of a new employee.", 45000, "number", "PKR", False, True, 30),
    ("advance_invoice_generation_days", "academics", "Advance Invoice Generation Days",
     "How many days ahead of the period start invoices are raised.", 7, "number", "days", False, True, 10),
    ("online_registration_form_instructions", "academics", "Online Registration - Form Instructions",
     "Shown at the top of the public registration form.",
     "<p>Please complete every field in English. A coordinator will contact you on WhatsApp within one working "
     "day to arrange a free trial class.</p>", "html", None, False, True, 20),
    ("online_registration_form_terms", "academics", "Online Registration - Form Terms and Conditions",
     "Accepted by the family before the registration is submitted.",
     "<ol><li>Fees are billed monthly in advance and are due within seven days of the invoice date.</li>"
     "<li>Classes missed without notice are not refunded; twenty-four hours' notice allows a reschedule.</li>"
     "<li>Recordings are kept for safeguarding and are deleted after the retention period.</li></ol>",
     "html", None, False, True, 30),
    ("accounts_financial_year_start_month", "accounts", "Financial Year Start Month",
     "Month number the accounting year opens on (7 = July).", 7, "number", "month", False, True, 10),
    ("accounts_posting_lock_days", "accounts", "Posting Lock After Close",
     "Days after a period is closed during which a correcting voucher may still be posted.", 5, "number", "days", False, True, 20),
    ("billing_invoice_due_days", "billing", "Invoice Due Days",
     "Days from the invoice date until payment is overdue.", 7, "number", "days", False, True, 10),
    ("billing_late_fee_pct", "billing", "Late Fee Percentage",
     "Charge added to an invoice left unpaid beyond the due date.", 2.5, "number", "percent", False, True, 20),
    ("billing_send_reminders", "billing", "Send Payment Reminders",
     "Whether the billing reminder job messages families about unpaid invoices.", True, "boolean", None, False, True, 30),
]

# name, company, fee %, fixed fee, currency, beneficiary account name, live mode, notes
GATEWAYS = [
    ("Stripe UK", "Stripe", 2.99, 0.30, "GBP", "Quran College Stripe-UK Auto", True,
     "Card payments from UK and EU families. Receipts arrive automatically against the Stripe beneficiary account."),
    ("PayPal International", "PayPal", 3.49, 0.49, "USD", "PayPal Manual", False,
     "Used by families outside card coverage. Receipts are matched by hand."),
]

# description, number, purpose, interval seconds, messages per cycle, api status, live mode
SENDERS = [
    ("General Sender", "+92 300 8811001", "general", 10, 1, "connected", True),
    ("Academics Sender", "+92 300 8811002", "academics", 30, 3, "disconnected", True),
]

# subject, type, module, priority, status, message, developer remarks
TICKETS = [
    ("Result card PDF drops the Tajweed column", "Bug", "Online Academics", "urgent", "in_progress",
     "The monthly result card prints Recitation and Memorization but leaves the Tajweed column blank for Hifz "
     "students. It shows correctly on screen.", "Reproduced on the Hifz template; fix scheduled for this week."),
    ("Add bulk WhatsApp reminder for unpaid invoices", "New Feature", "Billing Management", "normal", "pending",
     "We chase unpaid invoices one family at a time. A bulk reminder from the overdue list would save the billing "
     "team an hour a day.", None),
    ("Attendance shortage counted twice on split shifts", "Bug", "Human Resource", "very_urgent", "resolved",
     "Staff on a split shift show double the shortage minutes, which pushes a fine onto their payslip.",
     "Shortage is now calculated per shift segment rather than per day. Released and verified with HR."),
    ("Trial class slot shows in the wrong timezone for UK families", "Bug", "Online Academics", "urgent", "resolved",
     "A family in Manchester books 7pm and the teacher sees 7pm Karachi time.",
     "Slots are stored in Asia/Karachi and rendered in the family's timezone. Fixed."),
    ("Ledger report should export to Excel", "Change Request", "Accounts", "normal", "pending",
     "The ledger exports to CSV only. Finance wants an Excel file with the opening and closing balance formatted.", None),
    ("Cannot upload an employee contract larger than 2 MB", "Bug", "Human Resource", "normal", "in_progress",
     "Scanned contracts are usually 3-4 MB and the upload fails without an error message.",
     "Raising the limit to 10 MB and adding a clear message when it is exceeded."),
    ("Add Very Urgent to the complaint priority list", "Change Request", "Client Portal", "low", "closed",
     "Family complaints only go up to Urgent; safeguarding matters need one level above.",
     "Priority list now matches the ticket priorities. Closed."),
    ("Teacher portal logs out after ten minutes", "Bug", "Online Academics", "very_urgent", "in_progress",
     "Teachers are signed out mid class and have to log in again while the student waits.",
     "Session lifetime raised and a keep-alive added while a class is live. Testing with two teachers."),
    ("How do I reverse a receipt posted to the wrong family?", "Question", "Accounts", "normal", "resolved",
     "A receipt was posted against the wrong client code and the ledger is now out.",
     "Use Receipt Voucher, reverse the entry with a rationale, then re-post. Steps sent to the accountant."),
    ("Duplicate client records created by the registration form", "Data Fix", "Billing Management", "urgent", "pending",
     "Families who submit the form twice end up with two client codes and two invoices.", None),
    ("Add an Urdu label to the course list", "New Feature", "Configuration", "low", "pending",
     "Coordinators speaking to families in Urdu would like the Urdu course name alongside the English one.", None),
    ("Payroll run rejected without saying why", "Bug", "Human Resource", "urgent", "rejected",
     "The payroll run for last month was rejected and nobody can see the reason.",
     "Not a defect: the run was rejected by the CEO with a rationale recorded in the audit log. Shown on the run "
     "page now, so this ticket is closed as rejected."),
]


# --------------------------------------------------------------------------- lookups
def _seed_lookups(db: Session) -> tuple[int, int]:
    lookups = values = 0
    for code, app, description, sort_no, rows in LOOKUPS:
        lookup = db.query(Lookup).filter(Lookup.code == code).first()
        if not lookup:
            lookup = Lookup(code=code, app=app, description=description, sort_no=sort_no, is_system=True,
                            status="active", notes=f"Read by the application as lookups.values(db, '{code}')")
            db.add(lookup)
            db.flush()
            lookups += 1
        for i, (value, label, urdu, amount) in enumerate(rows, start=1):
            if db.query(LookupValue).filter(LookupValue.lookup_id == lookup.id, LookupValue.value == value).first():
                continue
            db.add(LookupValue(lookup_id=lookup.id, value=value, label=label, label_urdu=urdu, amount=amount,
                               sort_no=i, status="active"))
            values += 1
    db.flush()
    return lookups, values


# --------------------------------------------------------------------------- branch properties
def _seed_branch_properties(db: Session) -> int:
    created = 0
    for key, group, label, description, value, value_type, unit, is_secret, is_editable, sort_no in BRANCH_PROPERTIES:
        s = db.query(Setting).filter(Setting.key == key).first()
        if s:
            # An existing row keeps its value; only fill in the Branch Properties metadata it may be missing.
            if not s.label:
                s.label, s.value_type, s.unit = label, value_type, unit
                s.is_secret, s.is_editable, s.sort_no = is_secret, is_editable, sort_no
                s.description = s.description or description
                s.group = group
            continue
        db.add(Setting(key=key, value={"value": value}, group=group, description=description, label=label,
                       value_type=value_type, unit=unit, is_secret=is_secret, is_editable=is_editable, sort_no=sort_no))
        created += 1
    db.flush()
    return created


# --------------------------------------------------------------------------- payment gateways
def _seed_gateways(db: Session) -> int:
    created = 0
    for name, company, pct, fixed, currency, account_name, live, notes in GATEWAYS:
        if db.query(PaymentGateway).filter(PaymentGateway.name == name).first():
            continue
        account = db.query(BeneficiaryAccount).filter(BeneficiaryAccount.account_name == account_name).first()
        db.add(PaymentGateway(name=name, gateway_company=company, default_transaction_fee_pct=pct, fixed_fee=fixed,
                              currency=currency, beneficiary_account_id=account.id if account else None,
                              live_mode=live, status="active", notes=notes))
        created += 1
    db.flush()
    return created


# --------------------------------------------------------------------------- WhatsApp senders
def _seed_senders(db: Session) -> int:
    created = 0
    now = datetime.utcnow()
    for description, number, purpose, interval, per_cycle, api_status, live in SENDERS:
        if db.query(WhatsAppSender).filter(WhatsAppSender.number == number).first():
            continue
        db.add(WhatsAppSender(description=description, number=number, purpose=purpose, interval_seconds=interval,
                              messages_per_cycle=per_cycle, api_status=api_status, live_mode=live, status="active",
                              last_message_sent_at=now - timedelta(minutes=5) if api_status == "connected" else None,
                              qr_token="seeded-qr-token-general" if api_status == "connected" else None,
                              qr_refreshed_at=now - timedelta(days=2) if api_status == "connected" else None))
        created += 1
    db.flush()
    return created


# --------------------------------------------------------------------------- OTP
def _seed_otp(db: Session) -> int:
    if db.query(OtpConfiguration).first():
        return 0
    db.add(OtpConfiguration(is_enabled=False, channel="email", code_length=6, validity_minutes=10, max_attempts=5,
                            resend_after_seconds=60, required_for_roles=[]))
    db.flush()
    return 1


# --------------------------------------------------------------------------- support tickets
def _seed_tickets(db: Session) -> int:
    created = 0
    now = datetime.utcnow()
    raisers = db.query(User).filter(User.email.in_([
        "admin@oqc.local", "manager@oqc.local", "billing@oqc.local", "accountant@oqc.local",
        "hrofficer@oqc.local", "academics@oqc.local", "coordinator@oqc.local"])).all()
    departments = db.query(Department).order_by(Department.id).all()
    if not raisers:
        return 0
    for i, (subject, ttype, module, priority, status, message, remarks) in enumerate(TICKETS):
        if db.query(SupportTicket).filter(SupportTicket.subject == subject).first():
            continue
        raiser = raisers[i % len(raisers)]
        ticket = SupportTicket(
            ticket_number=next_code(db, SupportTicket, "ticket_number", "TKT-"),
            subject=subject, message=message, ticket_type=ttype, module=module, priority=priority, status=status,
            developer_remarks=remarks, raised_by_id=raiser.id,
            whatsapp_no=raiser.phone or "+92 300 0000000",
            department_id=(departments[i % len(departments)].id if departments else None),
            resolved_at=now - timedelta(days=i) if status in ("resolved", "closed") else None)
        ticket.created_at = now - timedelta(days=len(TICKETS) - i, hours=i)
        db.add(ticket)
        db.flush()
        created += 1
    return created


def run(db: Session) -> None:
    lookups, values = _seed_lookups(db)
    properties = _seed_branch_properties(db)
    gateways = _seed_gateways(db)
    senders = _seed_senders(db)
    otp = _seed_otp(db)
    tickets = _seed_tickets(db)
    db.commit()
    print(f"    config: {lookups} lookups / {values} values, {properties} branch properties, {gateways} gateways, "
          f"{senders} WhatsApp senders, {otp} OTP config, {tickets} support tickets")
