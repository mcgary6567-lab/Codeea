"""System administration seed: audit trail, security incidents, API keys, webhooks, backups,
migration jobs, admin settings and a populated notification delivery log.

Runs at the end of the core seed (core data — users, roles, departments, integrations — must exist).
Idempotent: a marker setting guards the whole run.
"""
from __future__ import annotations

import json
import random
import zipfile
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.security import generate_api_key
from app.models.core import (ApiKey, AuditEvent, BackupRecord, Notification, SecurityIncident, Setting, User,
                             Webhook, WebhookDelivery)
from app.models.ops import MigrationJob
from app.services import migration as mig
from app.services import system as sys_svc

MARKER = "seed_system_extra_v1"

# ----------------------------------------------------------------------------- audit trail
# (days_ago, hour, actor_email, action, module, entity_type, entity_id, severity, consequential, description, rationale)
AUDIT_EVENTS = [
    (29, 9, "admin@oqc.local", "login", "security", None, None, "info", False,
     "Signed in from 203.0.113.24 (Chrome on Windows)", None),
    (29, 9, "sysadmin@oqc.local", "update", "settings", "Setting", None, "warning", True,
     "Updated settings: class_grace_minutes, reminder_lead_minutes", "Supervisors asked for a longer grace window before a class is auto-missed"),
    (28, 11, "finance@oqc.local", "approve", "finance", "Invoice", 118, "warning", True,
     "Approved a 15% goodwill discount on invoice INV-2026-00118", "Two classes were missed due to a teacher absence; discount agreed with the parent"),
    (28, 15, "hr@oqc.local", "create", "employees", "Employee", 22, "info", False,
     "Created employee record for a new academic coordinator", None),
    (27, 10, "manager@oqc.local", "role_change", "users", "User", 14, "warning", True,
     "Role changed for coordinator@oqc.local", "Promoted from coordinator to supervisor after the Q3 review"),
    (27, 14, "academics@oqc.local", "update", "curriculum", "CourseDivision", 6, "info", False,
     "Reordered the Nazira 2 lesson sequence", None),
    (26, 8, "sysadmin@oqc.local", "execute", "backups", "BackupRecord", None, "warning", True,
     "Created backup oqc-nightly.zip (4820 KB)", "Nightly scheduled backup"),
    (26, 17, "qa@oqc.local", "create", "qa", "QAReview", 41, "info", False,
     "Completed a QA review for class session 2214 (score 87)", None),
    (25, 12, "billing@oqc.local", "create", "billing", "Invoice", 131, "info", False,
     "Issued invoice INV-2026-00131 to C-00019", None),
    (25, 13, "closer@oqc.local", "update", "crm", "Lead", 88, "info", False,
     "Lead moved from trial_done to negotiation", None),
    (24, 9, "admin@oqc.local", "permission_change", "roles", "Role", 9, "critical", True,
     "Updated permission matrix for role Billing Representative (34 permissions)", "Billing reps need refund visibility but not refund approval"),
    (24, 16, "supervisor@oqc.local", "update", "classes", "ClassSession", 2301, "info", False,
     "Class session marked done with 42 minutes of attendance", None),
    (23, 10, "accountant@oqc.local", "export", "finance", "Payment", None, "warning", True,
     "Exported 214 payment records to CSV", "Month-end reconciliation with the bank statement"),
    (23, 18, "teacher1@oqc.local", "login", "security", None, None, "info", False,
     "Signed in from 39.57.12.90 (Android)", None),
    (22, 11, "hr@oqc.local", "approve", "leaves", "LeaveRequest", 17, "warning", True,
     "Approved 3 days of annual leave for a teacher", "Cover arranged with the substitute pool before approval"),
    (21, 9, "sysadmin@oqc.local", "execute", "integrations", "Integration", None, "info", False,
     "Ran a health check across all providers", None),
    (21, 14, "marketing@oqc.local", "create", "campaigns", "Campaign", 7, "info", False,
     "Launched the September Hifz intake campaign", None),
    (20, 10, "finance@oqc.local", "approve", "payroll", "PayrollRun", 3, "critical", True,
     "Approved the August payroll run (18 employees, PKR 2,140,000)", "Attendance and per-class counts reconciled with the academics report"),
    (20, 15, "admin@oqc.local", "override", "security", "User", 31, "critical", True,
     "Two-factor reset for qaofficer@oqc.local", "Device lost; identity verified over a video call with the HOD"),
    (19, 12, "coordinator@oqc.local", "update", "students", "Student", 44, "info", False,
     "Changed the assigned teacher for S-00044", None),
    (18, 9, "sysadmin@oqc.local", "create", "api_keys", "ApiKey", None, "warning", True,
     "Issued API key 'n8n automation' (prefix oqc_live_9f, 4 scopes)", "Automating lead intake from the website forms"),
    (18, 16, "qaofficer@oqc.local", "update", "qa", "CorrectiveAction", 12, "info", False,
     "Corrective action closed after a follow-up observation", None),
    (17, 11, "billing@oqc.local", "update", "billing", "Invoice", 127, "warning", True,
     "Voided invoice INV-2026-00127", "Duplicate of INV-2026-00126 raised by the billing run"),
    (16, 13, "manager@oqc.local", "create", "tasks", "Task", 96, "info", False,
     "Created a task: prepare the October capacity plan", None),
    (15, 10, "admin@oqc.local", "update", "settings", "Setting", None, "warning", True,
     "Updated settings: referral_credit_amount, referral_nps_gate", "Ambassador programme relaunch approved in the September board meeting"),
    (15, 17, "supervisor@oqc.local", "update", "classes", "ClassSession", 2388, "warning", True,
     "Rescheduled 4 class sessions for a teacher absence", "Teacher reported illness two hours before the shift"),
    (14, 8, "sysadmin@oqc.local", "execute", "backups", "BackupRecord", None, "info", False,
     "Restore test for oqc-nightly.zip: passed (46 tables, 18,204 rows)", None),
    (13, 12, "academics@oqc.local", "approve", "academics", "MonthlyTest", 58, "warning", True,
     "Approved 36 monthly result cards for release", "All scores moderated and cross-checked against class attendance"),
    (12, 14, "hrofficer@oqc.local", "update", "employees", "Employee", 9, "warning", True,
     "Salary revised for an employee", "Annual increment approved by the HOD People and the CEO"),
    (11, 9, "closer@oqc.local", "update", "crm", "Lead", 104, "info", False,
     "Lead won and converted to client C-00041", None),
    (10, 11, "sysadmin@oqc.local", "update", "security", "Setting", None, "critical", True,
     "Force 2FA for privileged roles set to True; 6 user(s) notified", "Security review action item after the September penetration test"),
    (9, 15, "auditor@oqc.local", "export", "audit", "AuditEvent", None, "warning", True,
     "Exported 1,842 audit event(s) to CSV", "Quarterly external audit evidence pack"),
    (8, 10, "finance@oqc.local", "approve", "finance", "Refund", 5, "critical", True,
     "Approved a full refund of GBP 90.00 on C-00013", "Family relocated mid-month; refund policy applied at the CEO's discretion"),
    (7, 13, "manager@oqc.local", "update", "retention", "Student", 27, "info", False,
     "Win-back sequence started for a high-risk student", None),
    (6, 9, "sysadmin@oqc.local", "create", "webhooks", "Webhook", None, "warning", True,
     "Created webhook n8n - operations bus -> https://n8n.oqc.internal/webhook/oqc", "Routing platform events into the operations automation bus"),
    (5, 16, "qa@oqc.local", "update", "safeguarding", "SafeguardingCase", 3, "critical", True,
     "Safeguarding case notes accessed and updated", "Reviewing the case before the weekly safeguarding meeting"),
    (4, 11, "admin@oqc.local", "create", "migration", "MigrationJob", None, "warning", True,
     "Imported 21 clients record(s); 3 skipped, 3 duplicate(s)", "Legacy ERP cutover batch 1, signed off by the CEO"),
    (3, 10, "sysadmin@oqc.local", "revoke", "security", "UserSession", None, "critical", True,
     "Revoked 12 session(s) (all active sessions)", "Rotating credentials after a shared password was found in a chat export"),
    (2, 14, "billing@oqc.local", "execute", "notifications", "Broadcast", None, "warning", True,
     "Broadcast 'Fee schedule update for October' sent to 41 user(s) on in_app, whatsapp", "Annual fee revision announced to all parents"),
    (1, 9, "admin@oqc.local", "login", "security", None, None, "info", False,
     "Signed in from 203.0.113.24 (Chrome on Windows)", None),
    (1, 10, "sysadmin@oqc.local", "delete", "api_keys", "ApiKey", None, "critical", True,
     "Revoked API key 'legacy reporting bridge' (oqc_live_3c)", "Reporting bridge decommissioned after the migration"),
]

# (days_ago, hour, type, severity, email_or_None, ip, status, description)
INCIDENTS = [
    (27, 21, "login_failed", "low", "teacher3@oqc.local", "39.57.12.90", "resolved", "3 failed sign-in attempts, then a successful login."),
    (25, 2, "login_failed", "medium", None, "185.220.101.44", "open", "5 failed sign-in attempts for an unknown username from a Tor exit node."),
    (24, 3, "lockout", "high", "parent7@oqc.local", "185.220.101.44", "resolved", "Account locked after repeated failures. Password reset issued after identity checks."),
    (22, 19, "2fa_failed", "medium", "finance@oqc.local", "203.0.113.24", "resolved", "Two invalid TOTP codes; the authenticator app clock had drifted."),
    (20, 11, "permission_denied", "low", "teacher8@oqc.local", "110.36.220.7", "resolved", "Attempted to open /finance/payroll without payroll.view."),
    (18, 23, "login_failed", "low", "billing@oqc.local", "203.0.113.99", "resolved", "Two failed attempts followed by a successful sign-in from the same address."),
    (16, 4, "login_failed", "high", None, "45.155.205.233", "open", "Credential stuffing pattern: 14 usernames tried in 90 seconds."),
    (15, 4, "lockout", "critical", "admin@oqc.local", "45.155.205.233", "resolved", "Super admin account locked by the brute-force guard. IP allowlist applied afterwards."),
    (13, 10, "permission_denied", "medium", "coordinator@oqc.local", "110.36.220.7", "resolved", "Attempted to export the employee register without employees.export."),
    (11, 20, "2fa_failed", "low", "qaofficer@oqc.local", "39.57.12.90", "resolved", "Expired TOTP code entered twice; retried successfully."),
    (9, 1, "login_failed", "medium", None, "91.219.236.18", "open", "6 failed attempts against three parent accounts."),
    (7, 12, "permission_denied", "high", "marketing@oqc.local", "203.0.113.24", "open", "Attempted to open a safeguarding case without safeguarding.view."),
    (5, 22, "login_failed", "low", "student4@oqc.local", "119.155.14.6", "resolved", "Student portal password mistyped twice."),
    (3, 6, "2fa_failed", "medium", "sysadmin@oqc.local", "203.0.113.24", "resolved", "New device enrolment attempt with a stale secret; 2FA re-issued."),
    (1, 2, "login_failed", "high", None, "45.155.205.233", "open", "Repeat offender IP: 9 failed attempts overnight. Consider a firewall block."),
]

NOTIFICATION_MIX = [
    ("class_reminder_student", "Class starts in 30 minutes", "whatsapp", "delivered"),
    ("class_reminder_teacher", "Your class starts in 15 minutes", "in_app", "read"),
    ("invoice_issued", "Your invoice for September is ready", "email", "sent"),
    ("payment_received", "Payment received - thank you", "whatsapp", "delivered"),
    ("payment_failed", "We could not take your payment", "whatsapp", "failed"),
    ("result_card", "Monthly result card is ready", "whatsapp", "delivered"),
    ("class_missed", "A class was marked missed", "in_app", "read"),
    ("leave_approved", "Your leave request was approved", "in_app", "read"),
    ("task_overdue", "A task assigned to you is overdue", "in_app", "queued"),
    ("complaint_sla", "Case SLA at risk", "in_app", "read"),
    ("feedback_survey", "How are we doing?", "whatsapp", "sent"),
    ("win_back", "We miss you - let us restart the classes", "whatsapp", "failed"),
    ("daily_report_due", "Your structured report is due in one hour", "in_app", "read"),
    ("security_2fa_compliance", "Two-factor authentication is now required", "email", "sent"),
    ("integration_down", "WhatsApp integration reported degraded health", "in_app", "read"),
    ("backup_failed", "Nightly backup did not complete", "in_app", "read"),
    ("broadcast", "Fee schedule update for October", "in_app", "read"),
    ("broadcast", "Fee schedule update for October", "whatsapp", "delivered"),
    ("migration_completed", "Migration completed", "in_app", "read"),
    ("account_created", "Welcome to Online Quran College OS", "in_app", "read"),
]

FAILURE_REASONS = ["Recipient number is not on WhatsApp (error 131026)",
                   "SMTP 550: mailbox unavailable",
                   "Provider timeout after 30s",
                   "Template not approved for this language"]

LEGACY_CLIENTS_CSV = """client_code,full_name,email,phone,whatsapp,country,city,timezone,currency,address,status,joined_at,notes
LEG-1001,Ahmed Khan,ahmed.khan@legacy-example.com,+447700900101,+447700900101,United Kingdom,Birmingham,Europe/London,GBP,12 Rose Lane,active,2023-03-14,Migrated from legacy ERP
LEG-1002,Fatima Siddiqui,fatima.s@legacy-example.com,+447700900102,+447700900102,United Kingdom,Manchester,Europe/London,GBP,4 Elm Street,active,2023-04-02,Two children enrolled
LEG-1003,Yusuf Rahman,yusuf.r@legacy-example.com,+12025550103,+12025550103,United States,Houston,America/Chicago,USD,881 Maple Drive,active,2023-05-21,Weekend slots only
LEG-1004,Aisha Malik,aisha.malik@legacy-example.com,+447700900104,+447700900104,United Kingdom,Leeds,Europe/London,GBP,17 Oak Rise,active,2023-06-08,Requests female teachers
LEG-1005,Omar Farooq,omar.farooq@legacy-example.com,+61255550105,+61255550105,Australia,Sydney,Australia/Sydney,AUD,3 Harbour View,active,2023-07-19,Early morning slots
LEG-1006,Khadija Noor,khadija.noor@legacy-example.com,+447700900106,+447700900106,United Kingdom,London,Europe/London,GBP,55 Kings Road,active,2023-08-30,Referred by LEG-1002
LEG-1007,Ibrahim Sheikh,ibrahim.sheikh@legacy-example.com,+12025550107,+12025550107,United States,Chicago,America/Chicago,USD,220 Lake Street,inactive,2023-09-11,Paused after Ramadan
LEG-1008,Maryam Javed,maryam.javed@legacy-example.com,+447700900108,+447700900108,United Kingdom,Bradford,Europe/London,GBP,9 Willow Court,active,2023-10-05,Three siblings
LEG-1009,Bilal Hussain,bilal.hussain@legacy-example.com,+923001230109,+923001230109,Pakistan,Lahore,Asia/Karachi,PKR,House 22 Block C,active,2023-11-17,Local family rate
LEG-1010,Zainab Qureshi,zainab.q@legacy-example.com,+14165550110,+14165550110,Canada,Toronto,America/Toronto,CAD,140 Bay Street,active,2023-12-01,Prefers evening classes
LEG-1011,Hamza Iqbal,hamza.iqbal@legacy-example.com,+447700900111,+447700900111,United Kingdom,Glasgow,Europe/London,GBP,7 Clyde Walk,churned,2024-01-14,Left for a competitor
LEG-1012,Sumaya Ali,sumaya.ali@legacy-example.com,+447700900112,+447700900112,United Kingdom,Cardiff,Europe/London,GBP,31 Bute Terrace,active,2024-02-09,Tajweed focus
LEG-1013,Abdullah Mir,abdullah.mir@legacy-example.com,+12025550113,+12025550113,United States,Dallas,America/Chicago,USD,66 Pine Avenue,active,2024-02-27,Hifz track
LEG-1014,Hafsa Rehman,hafsa.rehman@legacy-example.com,+447700900114,+447700900114,United Kingdom,Luton,Europe/London,GBP,2 Chapel Close,active,2024-03-15,Split payments
LEG-1015,Talha Aziz,talha.aziz@legacy-example.com,+61255550115,+61255550115,Australia,Melbourne,Australia/Melbourne,AUD,18 Collins Way,active,2024-04-04,Weekend intensive
LEG-1016,Ruqayya Baig,ruqayya.baig@legacy-example.com,+447700900116,+447700900116,United Kingdom,Sheffield,Europe/London,GBP,44 Moor Lane,active,2024-04-22,Nazira 1
LEG-1017,Saad Anwar,saad.anwar@legacy-example.com,+14165550117,+14165550117,Canada,Calgary,America/Edmonton,CAD,73 Bow Street,inactive,2024-05-09,Payment on hold
LEG-1018,Amina Tariq,amina.tariq@legacy-example.com,+447700900118,+447700900118,United Kingdom,Leicester,Europe/London,GBP,12 Granby Place,active,2024-06-01,Two students
LEG-1019,Usman Chaudhry,usman.c@legacy-example.com,+923001230119,+923001230119,Pakistan,Karachi,Asia/Karachi,PKR,Flat 4 Sea Breeze,active,2024-06-19,Local family rate
LEG-1020,Safia Nadeem,safia.nadeem@legacy-example.com,+447700900120,+447700900120,United Kingdom,Newcastle,Europe/London,GBP,88 Quay Road,active,2024-07-08,Requests recordings
LEG-1021,Junaid Bashir,junaid.bashir@legacy-example.com,+12025550121,+12025550121,United States,Atlanta,America/New_York,USD,301 Peach Street,active,2024-07-25,Trial converted
LEG-1002,Fatima Siddiqui,fatima.s@legacy-example.com,+447700900102,+447700900102,United Kingdom,Manchester,Europe/London,GBP,4 Elm Street,active,2023-04-02,Duplicate row in the legacy export
LEG-1022,,noname@legacy-example.com,+447700900122,,United Kingdom,Bristol,Europe/London,GBP,,active,2024-08-11,Missing full name in the source system
LEG-1023,Rabia Sultan,rabia.sultan@legacy-example.com,+447700900123,+447700900123,United Kingdom,Nottingham,Europe/London,GBP,5 Trent Road,active,not-a-date,Unparseable joined date
"""

LEGACY_INVOICES_CSV = """invoice_number,client_code,client_email,issue_date,due_date,currency,subtotal,discount,total,paid_amount,status,remarks
LEG-INV-8801,LEG-1001,ahmed.khan@legacy-example.com,2024-07-01,2024-07-08,GBP,90.00,0.00,90.00,90.00,paid,legacy import
LEG-INV-8802,LEG-1002,fatima.s@legacy-example.com,2024-07-01,2024-07-08,GBP,160.00,10.00,150.00,150.00,paid,legacy import
LEG-INV-8803,LEG-1003,yusuf.r@legacy-example.com,2024-07-01,2024-07-08,USD,120.00,0.00,120.00,60.00,partial,legacy import
LEG-INV-8804,LEG-1004,aisha.malik@legacy-example.com,,2024-07-08,GBP,90.00,0.00,90.00,0.00,sent,missing issue date
LEG-INV-8805,LEG-1005,omar.farooq@legacy-example.com,2024-07-01,2024-07-08,AUD,140.00,0.00,,0.00,sent,missing total
LEG-INV-8806,LEG-1006,khadija.noor@legacy-example.com,2024-07-01,2024-07-08,GBP,90.00,0.00,90.00,90.00,paid,legacy import
LEG-INV-8807,LEG-1008,maryam.javed@legacy-example.com,2024-07-01,2024-07-08,GBP,240.00,20.00,220.00,220.00,paid,legacy import
LEG-INV-8808,LEG-1009,bilal.hussain@legacy-example.com,2024-07-01,2024-07-08,PKR,18000.00,0.00,18000.00,18000.00,paid,legacy import
LEG-INV-8809,LEG-1010,zainab.q@legacy-example.com,2024-07-01,2024-07-08,CAD,130.00,0.00,130.00,0.00,overdue,legacy import
LEG-INV-8810,LEG-1012,sumaya.ali@legacy-example.com,2024-07-01,2024-07-08,GBP,90.00,0.00,ninety,0.00,sent,total is not a number
LEG-INV-8811,LEG-1013,abdullah.mir@legacy-example.com,2024-07-01,2024-07-08,USD,150.00,0.00,150.00,150.00,paid,legacy import
LEG-INV-8812,LEG-1014,hafsa.rehman@legacy-example.com,2024-07-01,2024-07-08,GBP,90.00,0.00,90.00,45.00,partial,legacy import
"""


def _user_map(db: Session) -> dict[str, User]:
    return {u.email: u for u in db.query(User).all()}


def _seed_audit(db: Session, users: dict[str, User]) -> int:
    now = datetime.utcnow()
    n = 0
    for days, hour, email, action, module, etype, eid, severity, conseq, description, rationale in AUDIT_EVENTS:
        actor = users.get(email)
        ts = (now - timedelta(days=days)).replace(hour=hour, minute=random.randint(0, 59), second=random.randint(0, 59),
                                                 microsecond=0)
        db.add(AuditEvent(actor_id=actor.id if actor else None, actor_name=actor.full_name if actor else "system",
                          action=action, module=module, entity_type=etype, entity_id=eid,
                          description=description, rationale=rationale, severity=severity,
                          is_consequential=conseq, ip="203.0.113.24" if actor else None, created_at=ts))
        n += 1
    return n


def _seed_incidents(db: Session, users: dict[str, User]) -> int:
    now = datetime.utcnow()
    admin = users.get("sysadmin@oqc.local")
    n = 0
    for days, hour, itype, severity, email, ip, status, description in INCIDENTS:
        target = users.get(email) if email else None
        ts = (now - timedelta(days=days)).replace(hour=hour, minute=random.randint(0, 59), second=0, microsecond=0)
        inc = SecurityIncident(incident_type=itype, severity=severity, description=description, ip=ip,
                               user_id=target.id if target else None, status=status, created_at=ts)
        if status != "open" and admin:
            inc.resolved_by_id = admin.id
            inc.resolved_at = ts + timedelta(hours=random.randint(1, 20))
        db.add(inc)
        n += 1
    return n


def _seed_api_keys(db: Session, users: dict[str, User]) -> int:
    now = datetime.utcnow()
    admin = users.get("sysadmin@oqc.local") or users.get("admin@oqc.local")
    finance = users.get("finance@oqc.local") or admin
    specs = [
        ("n8n automation", admin, ["leads.add", "leads.view", "students.view", "classes.view"], 240,
         now + timedelta(days=180), True, now - timedelta(hours=3)),
        ("Website lead capture", admin, ["leads.add"], 60, now + timedelta(days=365), True, now - timedelta(days=1)),
        ("Legacy reporting bridge", finance, ["invoices.view", "payments.view", "reports.export"], 120,
         now - timedelta(days=2), False, now - timedelta(days=6)),
    ]
    n = 0
    for name, owner, scopes, limit, expires, active, last_used in specs:
        raw, prefix, key_hash = generate_api_key()
        # The raw key is intentionally discarded: only the SHA-256 hash and the prefix are ever stored,
        # so a seeded key can be listed and revoked in the console but never used to authenticate.
        del raw
        db.add(ApiKey(name=name, prefix=prefix, key_hash=key_hash, owner_id=owner.id if owner else None,
                      scopes=scopes, rate_limit_per_minute=limit, expires_at=expires, is_active=active,
                      last_used_at=last_used, created_at=now - timedelta(days=45)))
        n += 1
    return n


def _seed_webhooks(db: Session) -> tuple[int, int]:
    now = datetime.utcnow()
    hooks = [
        Webhook(name="n8n - operations bus", url="https://n8n.oqc.internal/webhook/oqc-events",
                events=["lead.created", "lead.converted", "class.status_changed", "payment.received", "case.opened"],
                secret="whsec_ops_bus_2f7c", is_active=True, last_status="success",
                last_triggered_at=now - timedelta(hours=2)),
        Webhook(name="Finance data warehouse", url="https://warehouse.oqc.internal/hooks/finance",
                events=["invoice.issued", "payment.received", "student.enrolled", "student.cancelled"],
                secret="whsec_warehouse_a91b", is_active=True, last_status="failed",
                last_triggered_at=now - timedelta(hours=9)),
    ]
    for h in hooks:
        db.add(h)
    db.flush()

    plan = [
        (hooks[0], "lead.created", "success", 1, 200, '{"ok":true}', 26),
        (hooks[0], "lead.converted", "success", 1, 200, '{"ok":true}', 22),
        (hooks[0], "class.status_changed", "success", 1, 200, '{"ok":true}', 19),
        (hooks[0], "payment.received", "success", 1, 200, '{"ok":true}', 14),
        (hooks[0], "case.opened", "failed", 3, 502, "Bad Gateway", 9),
        (hooks[0], "class.status_changed", "success", 1, 200, '{"ok":true}', 4),
        (hooks[0], "system.test", "success", 1, 200, '{"ok":true}', 2),
        (hooks[1], "invoice.issued", "success", 1, 201, '{"accepted":true}', 21),
        (hooks[1], "payment.received", "success", 2, 201, '{"accepted":true}', 16),
        (hooks[1], "student.enrolled", "dead", 6, 500, "Internal Server Error", 11),
        (hooks[1], "invoice.issued", "failed", 4, 504, "Gateway Timeout", 6),
        (hooks[1], "student.cancelled", "dead", 6, None, "Connection refused", 3),
    ]
    n = 0
    for hook, event, status, attempts, code, body, days in plan:
        created = now - timedelta(days=days, hours=random.randint(0, 12))
        db.add(WebhookDelivery(webhook_id=hook.id, direction="out", event=event,
                               payload={"event": event, "sent_at": created.isoformat(), "data": {"id": 1000 + n}},
                               status=status, attempts=attempts, response_code=code, response_body=body,
                               next_retry_at=(created + timedelta(minutes=64)) if status == "failed" else None,
                               created_at=created))
        n += 1

    # a little inbound traffic so the integration hub's inbound panel is not empty
    for i, (event, days) in enumerate([("whatsapp.message", 5), ("ghl.contact.updated", 3), ("payment.webhook", 1)]):
        db.add(WebhookDelivery(webhook_id=None, direction="in", event=event,
                               payload={"event": event, "received": True, "ref": f"IN-{i + 1}"},
                               status="success", attempts=1, response_code=200, response_body="processed",
                               created_at=now - timedelta(days=days, hours=2)))
        n += 1
    return len(hooks), n


def _placeholder_zip(path, tables: dict) -> int:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("MANIFEST.json", json.dumps({"generated_by": "seed", "tables": list(tables)}, indent=2))
        for name, rows in tables.items():
            zf.writestr(f"{name}.json", json.dumps(rows, default=str))
    return path.stat().st_size


def _seed_backups(db: Session, users: dict[str, User]) -> int:
    sys_svc.ensure_dirs()
    now = datetime.utcnow()
    admin = users.get("sysadmin@oqc.local") or users.get("admin@oqc.local")
    n = 0
    # five historical archives written as small but genuinely valid ZIPs so download and restore-test work
    for days, btype, tested in [(21, "scheduled", True), (14, "scheduled", True), (7, "scheduled", False),
                                (3, "manual", True), (1, "scheduled", False)]:
        stamp = (now - timedelta(days=days)).strftime("%Y%m%d-%H%M%S")
        path = sys_svc.BACKUP_DIR / f"oqc-{stamp}.zip"
        tables = {"organizations": [{"id": 1, "name": "Online Quran College"}],
                  "roles": [{"id": i, "slug": f"role_{i}"} for i in range(1, 21)],
                  "users": [{"id": i, "email": f"user{i}@oqc.local"} for i in range(1, 61)]}
        size = _placeholder_zip(path, tables)
        rec = BackupRecord(filename=path.name, path=str(path), size_bytes=size, backup_type=btype,
                           status="completed", restore_tested=tested,
                           restore_tested_at=(now - timedelta(days=days) + timedelta(minutes=20)) if tested else None,
                           created_by_id=admin.id if admin else None, created_at=now - timedelta(days=days))
        db.add(rec)
        n += 1
    # one real archive produced by the backup service itself
    real = sys_svc.create_backup(db, admin, backup_type="manual")
    sys_svc.restore_test(db, real)
    n += 1
    return n


def _seed_migration(db: Session, users: dict[str, User]) -> int:
    mig.ensure_dir()
    now = datetime.utcnow()
    admin = users.get("admin@oqc.local")
    clients_path = mig.MIGRATION_DIR / "20260801-090000-legacy-clients.csv"
    clients_path.write_text(LEGACY_CLIENTS_CSV, encoding="utf-8")
    invoices_path = mig.MIGRATION_DIR / "20260805-141500-legacy-invoices.csv"
    invoices_path.write_text(LEGACY_INVOICES_CSV, encoding="utf-8")

    clients_map = {c[0]: c[0] for c in mig.entity_columns("clients")}
    invoices_map = {c[0]: c[0] for c in mig.entity_columns("invoices")}

    job1 = MigrationJob(
        name="Clients - legacy ERP cutover batch 1", entity="clients", source_system="legacy_erp",
        file_path=str(clients_path), field_mapping=clients_map, status="reconciled",
        records_total=24, records_imported=21, records_skipped=3, duplicates_found=3,
        errors=[{"row": 23, "field": "duplicate", "message": "Duplicate of row 3 in this file (key LEG-1002)", "severity": "warning"},
                {"row": 24, "field": "full_name", "message": "Required field 'full_name' is empty", "severity": "warning"},
                {"row": 25, "field": "joined_at", "message": "Could not parse 'not-a-date' as a date", "severity": "warning"}],
        created_by_id=admin.id if admin else None, created_at=now - timedelta(days=4),
        completed_at=now - timedelta(days=4) + timedelta(minutes=6))
    job2 = MigrationJob(
        name="Invoices - legacy ERP dry run", entity="invoices", source_system="legacy_erp",
        file_path=str(invoices_path), field_mapping=invoices_map, status="validated",
        records_total=12, records_imported=0, records_skipped=0, duplicates_found=0,
        errors=[{"row": 5, "field": "issue_date", "message": "Required field 'issue_date' is empty or unmapped", "severity": "error"},
                {"row": 6, "field": "total", "message": "Required field 'total' is empty or unmapped", "severity": "error"},
                {"row": 11, "field": "total", "message": "'ninety' is not a valid decimal", "severity": "error"},
                {"row": 11, "field": "duplicate", "message": "A matching record already exists in the database - row will be skipped", "severity": "warning"}],
        created_by_id=admin.id if admin else None, created_at=now - timedelta(days=2))
    db.add_all([job1, job2])
    return 2


def _seed_settings(db: Session) -> int:
    keys = 0
    sys_svc.set_setting(db, "backup_retention_count", 14, group="backups",
                        description="How many backup archives to keep before pruning.")
    sys_svc.set_setting(db, "dr_targets", {"rpo_hours": 24, "rto_hours": 4,
                                           "offsite": "rclone sync to S3 (eu-west-2) nightly at 02:30 UTC"},
                        group="backups", description="Disaster recovery objectives (RPO/RTO) and off-site strategy.")
    sys_svc.set_setting(db, "ai_thresholds", {"human_review_confidence": 0.75, "safeguarding_alert": 0.60,
                                              "auto_flag_qa": 0.80, "monthly_budget_usd": 250},
                        group="governance", description="Confidence thresholds and budget for the AI gateway.")
    sys_svc.set_setting(db, "security_force_2fa_privileged", True, group="security",
                        description="Privileged roles must enrol in two-factor authentication.")
    sys_svc.set_setting(db, "migration_checklist",
                        {"items": {item["key"]: item["key"] in ("inventory", "field_map", "cleanse", "templates",
                                                                "dry_run", "order", "backup")
                                   for item in mig.DEFAULT_CHECKLIST}},
                        group="migration", description="Legacy ERP cutover checklist (SRS Section 20).")
    keys += 5
    for key, label, group, default, _note in sys_svc.RETENTION_KEYS:
        sys_svc.set_setting(db, key, default, group=group, description=label)
        keys += 1
    return keys


def _seed_notifications(db: Session, users: dict[str, User]) -> int:
    now = datetime.utcnow()
    recipients = [u for u in users.values() if u.is_active]
    if not recipients:
        return 0
    rnd = random.Random(20260909)
    n = 0
    for i in range(80):
        event, title, channel, status = NOTIFICATION_MIX[i % len(NOTIFICATION_MIX)]
        target = recipients[i % len(recipients)]
        created = now - timedelta(days=rnd.randint(0, 29), hours=rnd.randint(0, 23), minutes=rnd.randint(0, 59))
        address = None
        if channel == "email":
            address = target.email
        elif channel == "whatsapp":
            address = target.phone or "+447700900" + str(100 + i)[-3:]
        note = Notification(user_id=target.id, channel=channel, event_type=event, title=title,
                            body="Automatically generated by the platform. Open the OS for the full detail.",
                            link="/dashboard", recipient_address=address, status=status,
                            attempts=3 if status == "failed" else 1,
                            error=rnd.choice(FAILURE_REASONS) if status == "failed" else None,
                            is_read=(status == "read"),
                            sent_at=created if status in ("sent", "delivered", "read") else None,
                            created_at=created)
        db.add(note)
        n += 1
    return n


def run(db: Session) -> None:
    if db.query(Setting).filter(Setting.key == MARKER).first():
        return
    random.seed(20260909)
    users = _user_map(db)
    if not users:
        return

    audit_n = _seed_audit(db, users)
    incident_n = _seed_incidents(db, users)
    key_n = _seed_api_keys(db, users)
    hook_n, delivery_n = _seed_webhooks(db)
    backup_n = _seed_backups(db, users)
    job_n = _seed_migration(db, users)
    setting_n = _seed_settings(db)
    note_n = _seed_notifications(db, users)

    db.add(Setting(key=MARKER, value=True, group="general",
                   description="System administration demo data has been seeded."))
    db.commit()
    print(f"    system_extra: {audit_n} audit events, {incident_n} security incidents, {key_n} API keys, "
          f"{hook_n} webhooks / {delivery_n} deliveries, {backup_n} backups, {job_n} migration jobs, "
          f"{setting_n} settings, {note_n} notifications")
