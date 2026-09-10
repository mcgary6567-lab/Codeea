"""System administration services (Modules 22/23/36/37/39/48).

Users & temp passwords, permission matrix helpers, typed settings, backups & restore tests (SQLite backup API /
PostgreSQL JSON fallback), GDPR-style export & anonymisation, security dashboards, integration testing, broadcasts,
audit diffing. Everything here is DB-engine agnostic (ORM / func only) except the SQLite backup path.
"""
from __future__ import annotations

import io
import json
import os
import re
import secrets
import sqlite3
import string
import tempfile
import zipfile
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings, BASE_DIR
from app.core import rbac
from app.core.audit import log_action, snapshot
from app.core.security import hash_password
from app.database import Base
from app.models.core import (User, Role, Department, Branch, Organization, UserSession, AuditEvent, Notification,
                             NotificationTemplate, CommunicationPreference, Setting, ApiKey, Integration, Webhook,
                             WebhookDelivery, FileAsset, SecurityIncident, BackupRecord)
from app.models.people import Client, Student, Teacher, Employee

PRIVILEGED_ROLES = {"super_admin", "system_admin", "hod_people", "hod_finance", "hod_academics", "hod_qa",
                    "hod_technology", "hod_marketing", "manager", "accountant"}

BACKUP_DIR = settings.storage_dir / "backups"
EXPORT_DIR = settings.storage_dir / "exports"
UPLOAD_DIR = settings.storage_dir / "uploads"
LOGO_DIR = settings.storage_dir / "uploads" / "branding"

SETTING_GROUPS = ["general", "governance", "pricing", "referrals", "safeguarding", "cases", "reminders", "classes",
                  "retention", "teacher_dev", "payroll", "academic", "ai", "security", "backups", "migration"]

TIMEZONES = ["Asia/Karachi", "Europe/London", "America/New_York", "America/Chicago", "America/Los_Angeles",
             "America/Toronto", "Australia/Sydney", "Australia/Melbourne", "Europe/Paris", "Europe/Berlin",
             "Asia/Dubai", "Asia/Riyadh", "Asia/Kolkata", "Asia/Singapore", "UTC"]


def ensure_dirs() -> None:
    for d in (BACKUP_DIR, EXPORT_DIR, UPLOAD_DIR / "migration", LOGO_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ============================================================================= settings
def get_setting(db: Session, key: str, default: Any = None) -> Any:
    s = db.query(Setting).filter(Setting.key == key).first()
    return s.value if s and s.value is not None else default


def get_setting_value(db: Session, key: str, default: Any = None) -> Any:
    """Return the scalar for single-value settings ({"value": X}) or the whole dict otherwise."""
    v = get_setting(db, key)
    if v is None:
        return default
    if isinstance(v, dict) and set(v.keys()) == {"value"}:
        return v["value"]
    return v


def set_setting(db: Session, key: str, value: Any, group: str = "general", description: Optional[str] = None) -> Setting:
    s = db.query(Setting).filter(Setting.key == key).first()
    if not s:
        s = Setting(key=key, group=group, description=description)
        db.add(s)
    if not isinstance(value, dict):
        value = {"value": value}
    s.value = value
    if description:
        s.description = description
    if group and s.group != group and not s.id:
        s.group = group
    db.flush()
    return s


def coerce_like(old: Any, raw: Any) -> Any:
    """Coerce a submitted form string into the type of the existing JSON value."""
    if isinstance(old, bool):
        return str(raw).lower() in ("1", "true", "on", "yes")
    if isinstance(old, int) and not isinstance(old, bool):
        try:
            return int(raw)
        except (TypeError, ValueError):
            try:
                return int(float(raw))
            except (TypeError, ValueError):
                return old
    if isinstance(old, float):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return old
    if isinstance(old, (dict, list)):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return old
    if raw is None:
        return old
    return str(raw)


# ============================================================================= users
def generate_temp_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length - 2)) + secrets.choice(string.digits) + secrets.choice("!@#$%")
        if any(c.isdigit() for c in pwd) and any(c.isalpha() for c in pwd):
            return pwd


def username_from_email(db: Session, email: str, exclude_id: Optional[int] = None) -> str:
    base = re.sub(r"[^a-z0-9._-]", "", (email or "").split("@")[0].lower()) or "user"
    candidate, n = base, 1
    while True:
        q = db.query(User).filter(User.username == candidate)
        if exclude_id:
            q = q.filter(User.id != exclude_id)
        if not q.first():
            return candidate
        n += 1
        candidate = f"{base}{n}"


def user_stats(db: Session) -> dict:
    total = db.query(func.count(User.id)).scalar() or 0
    active = db.query(func.count(User.id)).filter(User.is_active.is_(True)).scalar() or 0
    twofa = db.query(func.count(User.id)).filter(User.two_factor_enabled.is_(True)).scalar() or 0
    locked = db.query(func.count(User.id)).filter(User.locked_until.isnot(None), User.locked_until > datetime.utcnow()).scalar() or 0
    by_portal = dict(db.query(Role.portal, func.count(User.id)).join(User, User.role_id == Role.id).group_by(Role.portal).all())
    must_change = db.query(func.count(User.id)).filter(User.must_change_password.is_(True)).scalar() or 0
    return {"total": total, "active": active, "inactive": total - active, "twofa": twofa,
            "twofa_pct": round(100 * twofa / total, 1) if total else 0, "locked": locked, "by_portal": by_portal,
            "must_change": must_change}


def linked_records(db: Session, user: User) -> dict:
    return {
        "client": db.query(Client).filter(Client.user_id == user.id).first(),
        "student": db.query(Student).filter(Student.user_id == user.id).first(),
        "teacher": db.query(Teacher).filter(Teacher.user_id == user.id).first(),
        "employee": db.query(Employee).filter(Employee.user_id == user.id).first(),
    }


def privileged_users_without_2fa(db: Session) -> list[User]:
    return (db.query(User).join(Role, User.role_id == Role.id)
            .filter(Role.slug.in_(PRIVILEGED_ROLES), User.is_active.is_(True), User.two_factor_enabled.is_(False))
            .order_by(Role.slug, User.full_name).all())


# ============================================================================= permission matrix
def all_permission_strings() -> list[str]:
    return [f"{m}.{a}" for m in rbac.MODULES for a in rbac.ACTIONS]


def expand_permissions(patterns: Iterable[str]) -> set[str]:
    pats = list(patterns or [])
    out: set[str] = set()
    for perm in all_permission_strings():
        if any(rbac._matches(p, perm) for p in pats):
            out.add(perm)
    return out


def compress_permissions(checked: Iterable[str]) -> list[str]:
    """Turn an explicit set of module.action strings back into a compact wildcard list."""
    checked = set(checked)
    everything = set(all_permission_strings())
    if checked >= everything:
        return ["*"]
    out: list[str] = []
    all_view = all(f"{m}.view" in checked for m in rbac.MODULES)
    if all_view:
        out.append("*.view")
    for m in rbac.MODULES:
        row = {a for a in rbac.ACTIONS if f"{m}.{a}" in checked}
        if row == set(rbac.ACTIONS):
            out.append(f"{m}.*")
        else:
            for a in rbac.ACTIONS:
                if a in row and not (all_view and a == "view"):
                    out.append(f"{m}.{a}")
    return out


def matrix_from_form(form, prefix: str = "p") -> set[str]:
    """Read checkbox names like p__students__view from a submitted form."""
    out = set()
    for key in form.keys():
        if key.startswith(prefix + "__"):
            _, module, action = key.split("__", 2)
            if module in rbac.MODULES and action in rbac.ACTIONS:
                out.add(f"{module}.{action}")
    return out


def explain_permission(user: User, perm: str) -> dict:
    """Return has_permission() plus the reason chain (for the permission test tool)."""
    steps: list[str] = []
    if user is None:
        return {"allowed": False, "steps": ["No user selected."]}
    if not user.is_active:
        steps.append("User account is inactive - every permission check fails.")
        return {"allowed": False, "steps": steps}
    if user.is_superuser:
        steps.append("User has the is_superuser flag - all permissions granted unconditionally.")
        return {"allowed": True, "steps": steps}
    denied = [p for p in (user.denied_permissions or []) if rbac._matches(p, perm)]
    if denied:
        steps.append(f"Denied by user-level denied_permissions pattern: {', '.join(denied)} (deny always wins).")
        return {"allowed": False, "steps": steps}
    steps.append("No denied_permissions pattern matches.")
    role_hits = [p for p in (user.role.permissions if user.role else []) if rbac._matches(p, perm)]
    extra_hits = [p for p in (user.extra_permissions or []) if rbac._matches(p, perm)]
    if role_hits:
        steps.append(f"Granted by role '{user.role.name}' pattern: {', '.join(role_hits)}.")
    else:
        steps.append(f"Role '{user.role.name if user.role else 'none'}' has no matching pattern ({len(user.role.permissions) if user.role else 0} patterns checked).")
    if extra_hits:
        steps.append(f"Granted by user-level extra_permissions pattern: {', '.join(extra_hits)}.")
    elif user.extra_permissions:
        steps.append("No extra_permissions pattern matches.")
    allowed = bool(role_hits or extra_hits)
    steps.append("Result: ALLOWED" if allowed else "Result: DENIED - no grant matched.")
    return {"allowed": allowed, "steps": steps, "role_hits": role_hits, "extra_hits": extra_hits}


def role_user_counts(db: Session) -> dict[int, int]:
    return dict(db.query(User.role_id, func.count(User.id)).group_by(User.role_id).all())


# ============================================================================= organization / logo
def save_logo(db: Session, org: Organization, filename: str, content: bytes, actor: Optional[User]) -> FileAsset:
    ensure_dirs()
    ext = Path(filename).suffix.lower() or ".png"
    safe = f"logo-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}{ext}"
    path = LOGO_DIR / safe
    path.write_bytes(content)
    asset = FileAsset(filename=filename, path=str(path.relative_to(BASE_DIR)).replace("\\", "/"), mime_type=f"image/{ext.lstrip('.')}",
                      size_bytes=len(content), owner_id=actor.id if actor else None, entity_type="Organization", entity_id=org.id)
    db.add(asset)
    org.logo_url = "/storage/uploads/branding/" + safe
    db.flush()
    return asset


# ============================================================================= environment
def mask_db_url(url: str) -> str:
    return re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:****@", url or "")


def env_summary() -> list[tuple[str, str]]:
    return [
        ("APP_ENV", settings.APP_ENV),
        ("APP_NAME", settings.APP_NAME),
        ("BASE_URL", settings.BASE_URL),
        ("DATABASE_URL", mask_db_url(settings.DATABASE_URL)),
        ("Database engine", "SQLite (local file)" if settings.is_sqlite else "PostgreSQL"),
        ("VIDEO_PROVIDER", settings.VIDEO_PROVIDER + (f" ({settings.JITSI_DOMAIN})" if settings.VIDEO_PROVIDER == "jitsi" else "")),
        ("AI_PROVIDER", settings.AI_PROVIDER),
        ("WhatsApp Cloud API", "configured" if settings.WHATSAPP_TOKEN else "simulated (WHATSAPP_TOKEN empty)"),
        ("GoHighLevel", "configured" if settings.GHL_API_KEY else "simulated (GHL_API_KEY empty)"),
        ("SMTP", f"configured ({settings.SMTP_HOST})" if settings.SMTP_HOST else "simulated (SMTP_HOST empty)"),
        ("AI API key", "set" if settings.AI_API_KEY else "not set"),
        ("SECRET_KEY", "custom" if settings.SECRET_KEY != "dev-secret-change-me" else "DEFAULT - change before production!"),
        ("Session hours", str(settings.SESSION_HOURS)),
        ("Access token minutes", str(settings.ACCESS_TOKEN_MINUTES)),
        ("Login lockout", f"{settings.MAX_LOGIN_ATTEMPTS} attempts / {settings.LOCKOUT_MINUTES} min"),
        ("Storage dir", str(settings.storage_dir)),
        ("Base currency", settings.BASE_CURRENCY),
        ("Default timezone", settings.DEFAULT_TIMEZONE),
    ]


# ============================================================================= backups & DR
def db_file_path() -> Path:
    raw = settings.DATABASE_URL.replace("sqlite:///", "")
    p = Path(raw)
    return p if p.is_absolute() else (BASE_DIR / raw.lstrip("./")).resolve()


CORE_EXPORT_TABLES = ["organizations", "branches", "departments", "roles", "users", "settings", "integrations", "webhooks",
                      "api_keys", "notification_templates", "currencies", "clients", "students", "teachers", "employees"]


def _json_default(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v)


def create_backup(db: Session, actor: Optional[User], backup_type: str = "manual") -> BackupRecord:
    ensure_dirs()
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    zip_name = f"oqc-{stamp}.zip"
    zip_path = BACKUP_DIR / zip_name
    if settings.is_sqlite:
        src_path = db_file_path()
        tmp_db = BACKUP_DIR / f"oqc-{stamp}.db"
        src = sqlite3.connect(str(src_path))
        dst = sqlite3.connect(str(tmp_db))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(tmp_db, arcname=tmp_db.name)
            zf.writestr("MANIFEST.json", json.dumps({"engine": "sqlite", "created_at": datetime.utcnow().isoformat(),
                                                     "source": str(src_path), "type": backup_type}, indent=2))
        tmp_db.unlink(missing_ok=True)
    else:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            counts = {}
            for name in CORE_EXPORT_TABLES:
                table = Base.metadata.tables.get(name)
                if table is None:
                    continue
                rows = [dict(r._mapping) for r in db.execute(select(table)).all()]
                counts[name] = len(rows)
                zf.writestr(f"{name}.json", json.dumps(rows, default=_json_default))
            zf.writestr("MANIFEST.json", json.dumps({"engine": "postgresql", "created_at": datetime.utcnow().isoformat(),
                                                     "type": backup_type, "tables": counts,
                                                     "note": "JSON fallback export. Use pg_dump for a full restore-able backup (see DR runbook)."}, indent=2))
    rec = BackupRecord(filename=zip_name, path=str(zip_path), size_bytes=zip_path.stat().st_size, backup_type=backup_type,
                       status="completed", created_by_id=actor.id if actor else None)
    db.add(rec)
    db.flush()
    return rec


def restore_test(db: Session, rec: BackupRecord) -> dict:
    """Open the backup read-only, count tables and rows; never touches the live database."""
    path = Path(rec.path)
    if not path.exists():
        rec.status = "missing"
        return {"ok": False, "error": "Backup file is missing on disk."}
    result: dict[str, Any] = {"ok": True, "tables": 0, "rows": 0, "detail": []}
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        db_members = [n for n in names if n.endswith(".db")]
        if db_members:
            with tempfile.TemporaryDirectory() as tmp:
                extracted = Path(zf.extract(db_members[0], tmp))
                conn = sqlite3.connect(f"file:{extracted.as_posix()}?mode=ro", uri=True)
                try:
                    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                    for t in tables:
                        n = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                        result["rows"] += n
                        result["detail"].append((t, n))
                    result["tables"] = len(tables)
                    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                    result["integrity"] = integrity
                    result["ok"] = integrity == "ok"
                finally:
                    conn.close()
        else:
            for n in names:
                if n.endswith(".json") and n != "MANIFEST.json":
                    rows = json.loads(zf.read(n))
                    result["tables"] += 1
                    result["rows"] += len(rows)
                    result["detail"].append((n[:-5], len(rows)))
    rec.restore_tested = bool(result["ok"])
    rec.restore_tested_at = datetime.utcnow()
    rec.status = "completed" if result["ok"] else "corrupt"
    result["detail"] = sorted(result["detail"], key=lambda x: -x[1])[:40]
    return result


def cleanup_backups(db: Session, keep: Optional[int] = None) -> int:
    keep = keep if keep is not None else int(get_setting_value(db, "backup_retention_count", 14) or 14)
    recs = db.query(BackupRecord).order_by(BackupRecord.created_at.desc()).all()
    removed = 0
    for rec in recs[keep:]:
        try:
            Path(rec.path).unlink(missing_ok=True)
        except OSError:
            pass
        db.delete(rec)
        removed += 1
    return removed


def delete_backup(db: Session, rec: BackupRecord) -> None:
    try:
        Path(rec.path).unlink(missing_ok=True)
    except OSError:
        pass
    db.delete(rec)


# ============================================================================= GDPR: export & anonymise
def export_client_data(db: Session, client: Client, actor: Optional[User]) -> Path:
    from app.models.finance import Invoice, Payment, LedgerEntry, Subscription
    from app.models.crm import Case, Feedback
    ensure_dirs()
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    path = EXPORT_DIR / f"client-{client.client_code}-{stamp}.zip"
    students = db.query(Student).filter(Student.client_id == client.id).all()
    bundle = {
        "client.json": snapshot(client),
        "students.json": [snapshot(s) for s in students],
        "subscriptions.json": [snapshot(s) for s in db.query(Subscription).filter(Subscription.client_id == client.id)],
        "invoices.json": [snapshot(i) for i in db.query(Invoice).filter(Invoice.client_id == client.id)],
        "payments.json": [snapshot(p) for p in db.query(Payment).filter(Payment.client_id == client.id)],
        "ledger.json": [snapshot(l) for l in db.query(LedgerEntry).filter(LedgerEntry.client_id == client.id)],
        "cases.json": [snapshot(c) for c in db.query(Case).filter(Case.client_id == client.id)],
        "feedback.json": [snapshot(f) for f in db.query(Feedback).filter(Feedback.client_id == client.id)],
        "communication_preferences.json": [snapshot(p) for p in db.query(CommunicationPreference).filter(CommunicationPreference.client_id == client.id)],
    }
    if client.user_id:
        u = db.get(User, client.user_id)
        if u:
            bundle["user.json"] = snapshot(u)
            bundle["notifications.json"] = [snapshot(n) for n in db.query(Notification).filter(Notification.user_id == u.id).limit(2000)]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in bundle.items():
            zf.writestr(name, json.dumps(data, indent=2, default=_json_default))
        zf.writestr("README.txt", f"Subject access export for {client.client_code} generated {datetime.utcnow().isoformat()}Z by "
                                  f"{actor.full_name if actor else 'system'}. Contains personal data - handle per retention policy.")
    return path


def anonymise_client(db: Session, client: Client, actor: User, rationale: str, request=None) -> dict:
    before = snapshot(client, ["full_name", "email", "phone", "whatsapp", "address", "notes", "status"])
    code = client.client_code
    client.full_name = f"Anonymised {code}"
    client.email = None
    client.phone = None
    client.whatsapp = None
    client.address = None
    client.notes = None
    client.city = None
    client.ghl_contact_id = None
    client.whatsapp_opt_in = False
    client.preferences = {}
    n_students = 0
    for s in db.query(Student).filter(Student.client_id == client.id):
        s.full_name = f"Anonymised {s.student_code}"
        s.date_of_birth = None
        s.notes = None
        n_students += 1
        if s.user_id:
            su = db.get(User, s.user_id)
            if su:
                _anonymise_user(su, s.student_code)
    if client.user_id:
        u = db.get(User, client.user_id)
        if u:
            _anonymise_user(u, code)
    for pref in db.query(CommunicationPreference).filter(CommunicationPreference.client_id == client.id):
        pref.opted_in = False
    log_action(db, actor, "anonymise", "security", entity=client, severity="critical", consequential=True, rationale=rationale,
               description=f"Anonymised churned client {code} and {n_students} student record(s)", before=before,
               after=snapshot(client, ["full_name", "email", "phone", "whatsapp", "address", "notes", "status"]), request=request)
    return {"students": n_students}


def _anonymise_user(u: User, code: str) -> None:
    u.full_name = f"Anonymised {code}"
    u.email = f"anon-{code.lower()}@anonymised.invalid"
    u.username = f"anon-{code.lower()}"
    u.phone = None
    u.avatar_url = None
    u.is_active = False
    u.two_factor_enabled = False
    u.two_factor_secret = None


# ============================================================================= security
def security_dashboard(db: Session) -> dict:
    now = datetime.utcnow()
    day_ago = now - timedelta(hours=24)
    open_q = db.query(SecurityIncident).filter(SecurityIncident.status == "open")
    by_type = dict(db.query(SecurityIncident.incident_type, func.count(SecurityIncident.id)).filter(SecurityIncident.status == "open").group_by(SecurityIncident.incident_type).all())
    by_sev = dict(db.query(SecurityIncident.severity, func.count(SecurityIncident.id)).filter(SecurityIncident.status == "open").group_by(SecurityIncident.severity).all())
    failed_24h = db.query(func.count(SecurityIncident.id)).filter(SecurityIncident.incident_type == "login_failed", SecurityIncident.created_at >= day_ago).scalar() or 0
    locked = db.query(User).filter(User.locked_until.isnot(None), User.locked_until > now).all()
    active_sessions = db.query(func.count(UserSession.id)).filter(UserSession.revoked.is_(False), UserSession.expires_at > now).scalar() or 0
    priv_no_2fa = privileged_users_without_2fa(db)
    with_ips = db.query(User).filter(User.allowed_ips.isnot(None), User.allowed_ips != "").all()
    # 14-day trend of failed logins
    labels, series = [], []
    for i in range(13, -1, -1):
        d = (now - timedelta(days=i)).date()
        labels.append(d.strftime("%d %b"))
        series.append(db.query(func.count(SecurityIncident.id)).filter(SecurityIncident.incident_type.in_(["login_failed", "lockout"]),
                                                                       func.date(SecurityIncident.created_at) == d.isoformat()).scalar() or 0)
    return {"open_total": open_q.count(), "by_type": by_type, "by_severity": by_sev, "failed_24h": failed_24h,
            "locked": locked, "active_sessions": active_sessions, "priv_no_2fa": priv_no_2fa, "with_ips": with_ips,
            "trend_labels": labels, "trend_series": series, "force_2fa": bool(get_setting_value(db, "security_force_2fa_privileged", False))}


MASKING_POLICY = [
    ("Client / Parent", "phone, whatsapp, email, address", "clients.update or management role", "Masked with *** (last 3 digits) in lists and portals; full value on the client detail for authorised staff."),
    ("Student", "date_of_birth, notes, safeguarding flags", "students.update / safeguarding.view", "Minors are identified by student code in QA, AI monitoring and recordings; names only for assigned teacher and academics."),
    ("Employee", "CNIC / national ID, address, bank details", "employees.update", "CNIC always masked in UI except HR officers editing the record; never exported to CSV without employees.export."),
    ("Payroll", "salary, bonus, advances, payslips", "payroll.view", "Visible to HOD People, HOD Finance, accountant and the employee themselves."),
    ("Grievances", "submitter identity, description", "grievances.view", "Confidential channel; anonymous grievances hide the submitter even from HODs."),
    ("Recordings", "class video / audio", "recordings.view + audit 'recording_access'", "Every playback is audit-logged as a consequential access."),
    ("Safeguarding", "case notes, reporter", "safeguarding.view", "Access logged as 'safeguarding_access' (consequential, warning severity)."),
    ("Users", "hashed_password, two_factor_secret", "never displayed", "Excluded from audit snapshots and API responses."),
    ("API keys", "raw key / key_hash", "never displayed after creation", "Only the 12-char prefix is stored in clear; hash is SHA-256."),
    ("Staff eNPS / anti-poaching", "feedback marked confidential", "CEO only (is_ceo)", "Hidden from every other role including HODs."),
]

RETENTION_KEYS = [
    ("recording_retention_days", "Class recordings", "safeguarding", 365, "Recordings older than this are purged by the recordings module."),
    ("retention_leads_days", "Lost leads (personal data)", "retention", 730, "Lost leads older than this are anonymised."),
    ("retention_notifications_days", "Read notifications", "retention", 180, "Read notifications older than this are deleted nightly."),
    ("retention_sessions_days", "Expired login sessions", "retention", 30, "Expired / revoked sessions older than this are deleted nightly."),
    ("retention_audit_days", "Audit events", "retention", 0, "0 = never deleted. The audit trail is immutable and retained indefinitely."),
]


# ============================================================================= integrations
INBOUND_WEBHOOKS = {
    "whatsapp": "/api/v1/webhooks/whatsapp",
    "ghl": "/api/v1/webhooks/ghl",
    "n8n": "/api/v1/system/webhooks/inbound/n8n",
    "payment": "/api/v1/webhooks/payment",
    "meta_ads": "/api/v1/webhooks/meta-leads",
    "zoom": "/api/v1/webhooks/zoom",
}

PROVIDER_META: dict[str, dict] = {
    "whatsapp": {"icon": "message-circle", "color": "emerald", "secrets": ["WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID"],
                 "fields": [("phone_number_display", "Business phone (display)"), ("business_account_id", "WABA ID"), ("template_language", "Default template language"), ("daily_limit", "Daily conversation limit")],
                 "test_label": "Send test WhatsApp", "test_placeholder": "+447700900123", "rate_limit": "80 msg/s per phone number; 1,000 business-initiated conversations/day on a new number (tier upgrades automatically)."},
    "ghl": {"icon": "funnel", "color": "indigo", "secrets": ["GHL_API_KEY"],
            "fields": [("location_id", "Location ID"), ("pipeline_id", "Pipeline ID"), ("won_stage_id", "Won stage ID"), ("lost_stage_id", "Lost stage ID"), ("source_tag", "Contact source tag")],
            "test_label": "Upsert test contact", "test_placeholder": "test@example.com", "rate_limit": "100 requests / 10 seconds per location; 200,000 per day."},
    "n8n": {"icon": "workflow", "color": "orange", "secrets": ["(per-webhook secret, see API & Webhooks)"],
            "fields": [("base_url", "n8n base URL"), ("workflow_ids", "Workflow IDs (comma separated)"), ("inbound_secret", "Inbound shared secret (X-OQC-Secret)")],
            "test_label": "Emit test event to n8n", "test_placeholder": "", "rate_limit": "Outbound deliveries retried with exponential backoff (2,4,8,16,32,64 min) then marked dead after 6 attempts."},
    "zoom": {"icon": "video", "color": "sky", "secrets": ["ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET"],
             "fields": [("account_email", "Zoom account email"), ("recordings_folder", "Recordings download folder"), ("auto_record", "Auto record (cloud/local/off)")],
             "test_label": "Ping (simulated)", "test_placeholder": "", "rate_limit": "Zoom API: light 30/s, medium 20/s, heavy 10/s per account."},
    "google": {"icon": "mail", "color": "rose", "secrets": ["GOOGLE_SERVICE_ACCOUNT_JSON"],
               "fields": [("workspace_domain", "Workspace domain"), ("calendar_id", "Shared calendar ID"), ("drive_folder_id", "Drive folder for exports")],
               "test_label": "Ping (simulated)", "test_placeholder": "", "rate_limit": "Calendar 1,000,000 queries/day; Drive 12,000 queries/60s."},
    "meta_ads": {"icon": "megaphone", "color": "sky", "secrets": ["META_ACCESS_TOKEN", "META_APP_SECRET"],
                 "fields": [("ad_account_id", "Ad account ID"), ("pixel_id", "Pixel ID"), ("lead_form_ids", "Lead form IDs"), ("sync_hours", "Metrics sync interval (hours)")],
                 "test_label": "Ping (simulated)", "test_placeholder": "", "rate_limit": "Marketing API: 200 calls/hour per user by default (BUC)."},
    "google_ads": {"icon": "search", "color": "amber", "secrets": ["GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_REFRESH_TOKEN"],
                   "fields": [("customer_id", "Customer ID"), ("conversion_action", "Conversion action name"), ("sync_hours", "Metrics sync interval (hours)")],
                   "test_label": "Ping (simulated)", "test_placeholder": "", "rate_limit": "15,000 operations/day basic access."},
    "payment": {"icon": "credit-card", "color": "emerald", "secrets": ["PAYMENT_SECRET_KEY", "PAYMENT_WEBHOOK_SECRET"],
                "fields": [("gateway", "Gateway (stripe / paypal / wise / bank)"), ("supported_currencies", "Supported currencies"), ("statement_descriptor", "Statement descriptor")],
                "test_label": "Ping (simulated)", "test_placeholder": "", "rate_limit": "Stripe: 100 read + 100 write requests/second live mode."},
    "smtp": {"icon": "send", "color": "indigo", "secrets": ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"],
             "fields": [("from_name", "From name"), ("reply_to", "Reply-to address"), ("daily_limit", "Daily send limit")],
             "test_label": "Send test email", "test_placeholder": "you@example.com", "rate_limit": "Provider dependent (Google Workspace 2,000/day, SES 14 msg/s default)."},
    "slack": {"icon": "hash", "color": "violet", "secrets": ["SLACK_BOT_TOKEN"],
              "fields": [("alerts_channel", "Alerts channel"), ("ops_channel", "Ops channel"), ("ceo_channel", "CEO channel")],
              "test_label": "Ping (simulated)", "test_placeholder": "", "rate_limit": "chat.postMessage ~1 message/second per channel."},
    "openproject": {"icon": "kanban", "color": "slate", "secrets": ["OPENPROJECT_API_KEY"],
                    "fields": [("base_url", "OpenProject URL"), ("project_identifier", "Project identifier"), ("sync_direction", "Sync direction (push / pull / both)")],
                    "test_label": "Ping (simulated)", "test_placeholder": "", "rate_limit": "Self-hosted: no hard limit; keep sync at 5 min intervals."},
    "ai": {"icon": "brain-circuit", "color": "violet", "secrets": ["AI_PROVIDER", "AI_API_KEY"],
           "fields": [("model", "Model name"), ("monthly_budget_usd", "Monthly budget (USD)"), ("max_tokens", "Max tokens per call"), ("human_review_threshold", "Auto-flag confidence threshold")],
           "test_label": "Run simulated inference", "test_placeholder": "", "rate_limit": "Provider dependent; gateway records every run with cost and latency (AI Governance)."},
    "video": {"icon": "radio", "color": "brand", "secrets": ["VIDEO_PROVIDER", "JITSI_DOMAIN"],
              "fields": [("room_prefix", "Room name prefix"), ("recording_enabled", "Recording enabled (true/false)"), ("max_participants", "Max participants")],
              "test_label": "Build test join URL", "test_placeholder": "", "rate_limit": "Public meet.jit.si: fair use; self-host for > 50 concurrent rooms."},
}


def test_integration(db: Session, provider: str, target: str = "") -> dict:
    from app.services import integrations as integ
    target = (target or "").strip()
    if provider == "whatsapp":
        return integ.send_whatsapp(db, target or "+447700900000", "Online Quran College: integration test message. Please ignore.")
    if provider == "smtp":
        return integ.send_email(db, target or "test@example.com", "OQC integration test", "This is a test email from the Integration Hub.")
    if provider == "ghl":
        return integ.ghl_upsert_contact(db, {"email": target or "integration-test@example.com", "firstName": "OQC", "lastName": "Test", "tags": ["oqc-test"]})
    if provider == "n8n":
        integ.emit_event(db, "system.test", {"message": "Integration Hub test event", "at": datetime.utcnow().isoformat()})
        db.flush()
        n = integ.deliver_pending_webhooks(db)
        return {"queued_for_active_webhooks": True, "processed": n}
    if provider == "video":
        url = integ.build_join_url("oqc-test-room", "Integration Test")
        integ._record(db, "video", True, simulated=True)
        return {"join_url": url}
    if provider == "ai":
        from app.services.ai_gateway import ai
        try:
            result, run = ai(db, module="insights", task="summarise", payload={"text": "Integration hub connectivity test."}, entity=None)
            integ._record(db, "ai", True, simulated=(settings.AI_PROVIDER == "simulated"))
            return {"run_id": run.id if run else None, "provider": run.provider if run else settings.AI_PROVIDER, "simulated": settings.AI_PROVIDER == "simulated"}
        except Exception as exc:
            integ._record(db, "ai", True, simulated=True)
            return {"simulated": True, "note": f"Gateway returned {type(exc).__name__}; simulated ping recorded."}
    integ._record(db, provider, True, simulated=True)
    return {"simulated": True, "note": "No live adapter for this provider yet - configuration validated and a simulated ping recorded."}


def reset_integration_counters(db: Session) -> int:
    n = 0
    for i in db.query(Integration).all():
        i.calls_today = 0
        i.failures_today = 0
        n += 1
    return n


# ============================================================================= platform events (outbound webhooks)
EVENT_CATALOGUE = [
    ("lead.created", "A new lead entered the pipeline"),
    ("lead.converted", "Lead converted to a client"),
    ("class.status_changed", "Class session started / done / missed / rescheduled"),
    ("invoice.issued", "Invoice issued to a client"),
    ("payment.received", "Payment recorded"),
    ("case.opened", "Complaint / request opened"),
    ("case.resolved", "Case resolved"),
    ("student.enrolled", "Student enrolled (subscription active)"),
    ("student.cancelled", "Student cancelled"),
    ("monthly_test.delivered", "Monthly result card delivered"),
    ("referral.qualified", "Referral qualified for credit"),
    ("qa.review_completed", "QA review completed"),
    ("ai.flag_raised", "AI monitoring raised a flag"),
    ("system.test", "Test event from the admin console"),
    ("*", "All events"),
]

# Section 18 notification trigger catalogue: (event_type, description, audience, channels, automated_by)
TRIGGER_CATALOGUE = [
    ("lead_follow_up", "Lead follow-up nudges (day 0/1/3/7)", "Lead", "whatsapp", "Sequences job (crm)"),
    ("trial_follow_up", "Post-trial follow-up", "Client", "whatsapp", "Trials module"),
    ("class_reminder_teacher", "Class starting soon", "Teacher", "in_app, push", "Scheduler (classes) 15 min before"),
    ("class_reminder_student", "Class starting soon", "Client / Student", "whatsapp", "Scheduler (classes) 30 min before"),
    ("class_missed", "Teacher no-show / missed class", "Supervisor, Manager", "in_app", "Auto-missed job (grace period)"),
    ("invoice_issued", "Invoice issued", "Client", "whatsapp, email", "Billing run (subscriptions)"),
    ("payment_received", "Payment received + receipt", "Client", "whatsapp, email", "Payments module"),
    ("payment_failed", "Payment failed / overdue", "Billing rep, Client", "in_app, whatsapp", "Overdue job (billing)"),
    ("result_card", "Monthly result card ready", "Client", "whatsapp", "Monthly tests module"),
    ("feedback_survey", "Feedback survey (tenure 30/90/180, post-PTM)", "Client", "whatsapp", "Feedback job"),
    ("ambassador_invite", "Ambassador programme invite", "Client", "whatsapp", "Referrals job (tenure + NPS gate)"),
    ("leave_approved", "Leave approved / rejected", "Teacher / Client", "in_app", "Leaves module"),
    ("complaint_sla", "Case SLA at risk / breached", "Assignee, HOD", "in_app", "Cases SLA job"),
    ("task_overdue", "Task overdue / escalated", "Assignee, Manager", "in_app", "Tasks job"),
    ("win_back", "Win-back after missed classes / freeze", "Client", "whatsapp", "Retention job (risk score)"),
    ("daily_report_due", "Structured report deadline reminder", "Staff", "in_app", "Daily reports job"),
    ("safeguarding_flag", "Safeguarding concern raised", "HOD QA, CEO", "in_app", "AI monitoring / manual"),
    ("security_lockout", "Account locked after failed logins", "System admin", "in_app", "Login flow"),
    ("security_2fa_compliance", "2FA required for privileged role", "Privileged user", "in_app, email", "Security Center (manual send)"),
    ("integration_down", "Integration health degraded / down", "System admin", "in_app", "Health check job (hourly)"),
    ("backup_failed", "Nightly backup failed", "System admin", "in_app", "Backup job (nightly)"),
    ("migration_completed", "Migration job imported / reconciled", "Actor", "in_app", "Migration wizard"),
    ("broadcast", "Admin broadcast", "Selected audience", "in_app, email, whatsapp", "Manual (Notifications admin)"),
]

SAMPLE_VARS = {"name": "Ahmed Khan", "student": "Zainab", "teacher": "Qari Abdul Rehman", "time": "18:30", "minutes": "15",
               "room": "oqc-s-00012", "link": "https://app.onlinequrancollege.local/x/abc123", "number": "INV-2026-00123",
               "amount": "GBP 45.00", "due": "05 Oct 2026", "period": "Sep 2026", "title": "Call parent", "start": "10 Sep", "end": "12 Sep"}


def render_placeholders(text: str, variables: dict) -> str:
    def repl(m):
        key = m.group(1).strip()
        return str(variables.get(key, "{{" + key + "}}"))
    return re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", repl, text or "")


def placeholders_in(text: str) -> list[str]:
    return sorted(set(re.findall(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", text or "")))


# ============================================================================= broadcast
def broadcast_recipients(db: Session, target: str) -> list[User]:
    q = db.query(User).join(Role, User.role_id == Role.id).filter(User.is_active.is_(True))
    if target.startswith("role:"):
        q = q.filter(Role.slug == target.split(":", 1)[1])
    elif target.startswith("department:"):
        q = q.filter(User.department_id == int(target.split(":", 1)[1]))
    elif target == "all_clients":
        q = q.filter(Role.portal == "client")
    elif target == "all_teachers":
        q = q.filter(Role.portal == "teacher")
    elif target == "all_staff":
        q = q.filter(Role.portal == "admin")
    elif target == "all_users":
        pass
    else:
        return []
    return q.order_by(User.full_name).all()


def send_broadcast(db: Session, actor: User, target: str, title: str, body: str, channels: list[str], link: Optional[str] = None) -> int:
    from app.core.notify import notify
    users = broadcast_recipients(db, target)
    for u in users:
        chans = ["in_app"] if "in_app" in channels else []
        if "email" in channels and u.email and "@" in u.email:
            notify(db, u, title, body, event_type="broadcast", link=link, channels=("email",), recipient_address=u.email)
        if "whatsapp" in channels:
            addr = u.phone
            if not addr:
                c = db.query(Client).filter(Client.user_id == u.id).first()
                addr = (c.whatsapp or c.phone) if c else None
            if addr:
                notify(db, u, title, body, event_type="broadcast", link=link, channels=("whatsapp",), recipient_address=addr)
        if chans:
            notify(db, u, title, body, event_type="broadcast", link=link, channels=("in_app",))
    return len(users)


# ============================================================================= audit helpers
def audit_diff(before: Optional[dict], after: Optional[dict]) -> list[dict]:
    before, after = before or {}, after or {}
    keys = sorted(set(before) | set(after))
    rows = []
    for k in keys:
        b, a = before.get(k), after.get(k)
        rows.append({"key": k, "before": b, "after": a, "changed": b != a, "added": k not in before, "removed": k not in after})
    return rows


def audit_stats(db: Session, days: int = 30) -> dict:
    since = datetime.utcnow() - timedelta(days=days)
    by_module = db.query(AuditEvent.module, func.count(AuditEvent.id)).filter(AuditEvent.created_at >= since).group_by(AuditEvent.module).order_by(func.count(AuditEvent.id).desc()).all()
    by_actor = db.query(AuditEvent.actor_name, func.count(AuditEvent.id)).filter(AuditEvent.created_at >= since).group_by(AuditEvent.actor_name).order_by(func.count(AuditEvent.id).desc()).limit(12).all()
    by_action = db.query(AuditEvent.action, func.count(AuditEvent.id)).filter(AuditEvent.created_at >= since).group_by(AuditEvent.action).order_by(func.count(AuditEvent.id).desc()).limit(12).all()
    by_severity = dict(db.query(AuditEvent.severity, func.count(AuditEvent.id)).filter(AuditEvent.created_at >= since).group_by(AuditEvent.severity).all())
    labels, series = [], []
    for i in range(days - 1, -1, -1):
        d = (datetime.utcnow() - timedelta(days=i)).date()
        labels.append(d.strftime("%d %b"))
        series.append(db.query(func.count(AuditEvent.id)).filter(func.date(AuditEvent.created_at) == d.isoformat()).scalar() or 0)
    total = db.query(func.count(AuditEvent.id)).filter(AuditEvent.created_at >= since).scalar() or 0
    consequential = db.query(func.count(AuditEvent.id)).filter(AuditEvent.created_at >= since, AuditEvent.is_consequential.is_(True)).scalar() or 0
    return {"by_module": by_module, "by_actor": by_actor, "by_action": by_action, "by_severity": by_severity,
            "labels": labels, "series": series, "total": total, "consequential": consequential,
            "all_time": db.query(func.count(AuditEvent.id)).scalar() or 0}


def audit_csv(events: Iterable[AuditEvent]) -> str:
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "created_at", "actor", "action", "module", "entity_type", "entity_id", "severity", "consequential", "description", "rationale", "ip"])
    for e in events:
        w.writerow([e.id, e.created_at.isoformat() if e.created_at else "", e.actor_name or "", e.action, e.module, e.entity_type or "",
                    e.entity_id or "", e.severity, "yes" if e.is_consequential else "no", e.description or "", e.rationale or "", e.ip or ""])
    return buf.getvalue()


# ============================================================================= DR runbook
DR_RUNBOOK = [
    ("1. Detect & declare", "Health endpoint /health fails or data corruption suspected. System admin declares an incident in the Security Center and notifies the CEO (Slack #alerts + WhatsApp)."),
    ("2. Freeze writes", "Stop the uvicorn service (systemctl stop oqc) so no further writes hit a corrupted database. Put nginx in maintenance mode (return 503 page)."),
    ("3. Pick the restore point", "Choose the newest backup with 'Restore tested' = yes from /admin/backups (or the off-site copy). Confirm its timestamp is within the configured RPO."),
    ("4. Restore - SQLite", "Unzip oqc-YYYYmmdd-HHMMSS.zip, run `sqlite3 restored.db 'PRAGMA integrity_check'`, then replace data/oqc.db (keep the corrupted file as oqc.db.corrupt for forensics)."),
    ("4. Restore - PostgreSQL", "createdb oqc_restore && pg_restore -d oqc_restore --no-owner backup.dump; validate row counts; then swap DATABASE_URL (or rename databases) and restart."),
    ("5. Restore storage", "rclone sync remote:oqc-backups/storage ./storage - recordings, uploads, certificates and result cards live outside the database."),
    ("6. Verify", "Start the service, log in as system admin, check /admin/settings (jobs tab), Integration Hub health, latest invoices and class sessions. Run a restore test on the backup you just used."),
    ("7. Replay & communicate", "Re-run any webhook deliveries in 'failed' state, re-send notifications queued during the outage, and post an incident summary in the Decision Register."),
    ("8. Post-mortem", "Within 48h: root cause, RPO/RTO actually achieved vs targets, follow-up tasks in Tasks & Projects. Update this runbook if any step was unclear."),
]
