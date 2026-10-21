"""Legacy data migration (Module 35 / SRS Section 20).

CSV + XLSX parsing, field mapping with name-similarity suggestions, validation (required / types / duplicates),
transactional per-row import with skip-on-error, and a reconciliation report.
Engine agnostic: pure ORM, no raw SQL.
"""
from __future__ import annotations

import csv
import difflib
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.core.utils import next_code
from app.models.core import User
from app.models.crm import Lead
from app.models.finance import Invoice, LedgerEntry, Payment
from app.models.ops import MigrationJob
from app.models.people import Client, Student, Teacher

MIGRATION_DIR = settings.storage_dir / "uploads" / "migration"

# ----------------------------------------------------------------------------- entity catalogue
# (target column, label, type, required)
ENTITIES: dict[str, dict] = {
    "clients": {
        "label": "Clients / Parents",
        "icon": "users",
        "match_on": "email / phone / client_code",
        "columns": [
            ("client_code", "Legacy client code", "str", False),
            ("full_name", "Full name", "str", True),
            ("email", "Email", "email", False),
            ("phone", "Phone", "phone", False),
            ("whatsapp", "WhatsApp", "phone", False),
            ("country", "Country", "str", False),
            ("city", "City", "str", False),
            ("timezone", "Timezone", "str", False),
            ("currency", "Currency", "str", False),
            ("address", "Address", "str", False),
            ("status", "Status (active/inactive/churned)", "str", False),
            ("joined_at", "Joined date", "date", False),
            ("notes", "Notes", "str", False),
        ],
    },
    "students": {
        "label": "Students",
        "icon": "graduation-cap",
        "match_on": "student_code / client_code + name",
        "columns": [
            ("student_code", "Legacy student code", "str", False),
            ("full_name", "Full name", "str", True),
            ("client_code", "Parent client code", "str", False),
            ("client_email", "Parent email", "email", False),
            ("gender", "Gender", "str", False),
            ("date_of_birth", "Date of birth", "date", False),
            ("level", "Level", "str", False),
            ("status", "Status (active/trial/frozen/cancelled)", "str", False),
            ("join_date", "Join date", "date", False),
            ("timezone", "Timezone", "str", False),
            ("notes", "Notes", "str", False),
        ],
    },
    "leads": {
        "label": "Leads",
        "icon": "filter",
        "match_on": "email / phone",
        "columns": [
            ("full_name", "Full name", "str", True),
            ("email", "Email", "email", False),
            ("phone", "Phone", "phone", False),
            ("whatsapp", "WhatsApp", "phone", False),
            ("country", "Country", "str", False),
            ("student_name", "Student name", "str", False),
            ("student_age", "Student age", "int", False),
            ("stage", "Stage", "str", False),
            ("notes", "Notes", "str", False),
        ],
    },
    "teachers": {
        "label": "Teachers",
        "icon": "user-check",
        "match_on": "email / teacher_code",
        "columns": [
            ("teacher_code", "Legacy teacher code", "str", False),
            ("full_name", "Full name", "str", True),
            ("email", "Email", "email", False),
            ("phone", "Phone", "phone", False),
            ("gender", "Gender", "str", False),
            ("qualifications", "Qualifications", "str", False),
            ("shift", "Shift", "str", False),
            ("timezone", "Timezone", "str", False),
            ("per_class_rate", "Per class rate", "decimal", False),
            ("status", "Status", "str", False),
            ("courses", "Courses taught (codes, e.g. NAZRA, HIFZ)", "str", False),
            ("verified", "Background check verified (yes/no)", "str", False),
        ],
    },
    "invoices": {
        "label": "Invoices",
        "icon": "receipt",
        "match_on": "invoice_number",
        "financial_field": "total",
        "columns": [
            ("invoice_number", "Invoice number", "str", False),
            ("client_code", "Client code", "str", False),
            ("client_email", "Client email", "email", False),
            ("client_name", "Client name (when there is no code or e-mail)", "str", False),
            ("issue_date", "Issue date", "date", True),
            ("due_date", "Due date", "date", False),
            ("currency", "Currency", "str", False),
            ("subtotal", "Subtotal", "decimal", False),
            ("discount", "Discount", "decimal", False),
            ("total", "Total", "decimal", True),
            ("paid_amount", "Paid amount", "decimal", False),
            ("status", "Status (draft/sent/paid/overdue)", "str", False),
            ("remarks", "Remarks", "str", False),
        ],
    },
    "payments": {
        "label": "Payments",
        "icon": "credit-card",
        "match_on": "payment_number / reference",
        "financial_field": "amount",
        "columns": [
            ("payment_number", "Payment number", "str", False),
            ("client_code", "Client code", "str", False),
            ("client_email", "Client email", "email", False),
            ("client_name", "Client name (when there is no code or e-mail)", "str", False),
            ("invoice_number", "Invoice number", "str", False),
            ("amount", "Amount", "decimal", True),
            ("currency", "Currency", "str", False),
            ("method", "Method (bank/card/cash/wise)", "str", False),
            ("reference", "Reference", "str", False),
            ("received_at", "Received at", "date", True),
            ("notes", "Notes", "str", False),
        ],
    },
    "employees": {
        "label": "Employees (staff)",
        "icon": "id-card",
        "match_on": "employee_code / email",
        "columns": [
            ("employee_code", "Legacy employee code", "str", False),
            ("full_name", "Full name", "str", True),
            ("designation", "Designation", "str", True),
            ("department", "Department (name or code)", "str", False),
            ("email", "Email", "email", False),
            ("phone", "Phone", "phone", False),
            ("gender", "Gender", "str", False),
            ("father_name", "Father name", "str", False),
            ("cnic", "CNIC / national ID", "str", False),
            ("address", "Address", "str", False),
            ("join_date", "Join date", "date", False),
            ("employment_type", "Employment type (full_time/part_time/contract)", "str", False),
            ("shift", "Shift (morning/evening/night)", "str", False),
            ("base_salary", "Basic salary", "decimal", False),
            ("currency", "Salary currency", "str", False),
            ("bank_name", "Bank name", "str", False),
            ("bank_account_no", "Bank account number", "str", False),
            ("status", "Status (active/probation/resigned)", "str", False),
        ],
    },
    "subscriptions": {
        "label": "Subscriptions",
        "icon": "repeat",
        "match_on": "subscription_code",
        "financial_field": "price",
        "columns": [
            ("subscription_code", "Legacy subscription code", "str", False),
            ("student_code", "Student code", "str", True),
            ("client_code", "Client code (when it differs from the student's)", "str", False),
            ("package", "Package name", "str", False),
            ("course", "Course (code or name)", "str", False),
            ("teacher_code", "Teacher code", "str", False),
            ("sessions_per_week", "Classes per week", "int", False),
            ("session_minutes", "Minutes per class", "int", False),
            ("days", "Days (Mon,Wed,Fri or 0,2,4)", "str", False),
            ("price", "Monthly fee", "decimal", True),
            ("currency", "Currency", "str", False),
            ("start_date", "Start date", "date", False),
            ("next_billing_date", "Next billing date", "date", False),
            ("status", "Status (active/frozen/cancelled/expired)", "str", False),
            ("notes", "Notes", "str", False),
        ],
    },
    "courses": {
        "label": "Courses",
        "icon": "book-open",
        "match_on": "code",
        "columns": [
            ("code", "Course code", "str", True),
            ("name", "Course name", "str", True),
            ("course_type", "Type (Islamic Courses / Academics Tutoring)", "str", False),
            ("default_session_minutes", "Default minutes per class", "int", False),
            ("fee", "Default monthly fee", "decimal", False),
            ("completion_target_months", "Target months to complete", "int", False),
            ("description", "Description", "str", False),
            ("is_active", "Active (yes/no)", "str", False),
        ],
    },
    "packages": {
        "label": "Packages",
        "icon": "package",
        "match_on": "name + currency + country",
        "columns": [
            ("name", "Package name", "str", True),
            ("course", "Course (code or name)", "str", False),
            ("sessions_per_week", "Classes per week", "int", False),
            ("session_minutes", "Minutes per class", "int", False),
            ("price", "Price", "decimal", True),
            ("currency", "Currency", "str", False),
            ("billing_cycle", "Billing cycle (monthly/quarterly)", "str", False),
            ("country", "Country (country-specific price)", "str", False),
            ("is_trial", "Trial package (yes/no)", "str", False),
            ("description", "Description", "str", False),
        ],
    },
    "questions": {
        "label": "Question Bank",
        "icon": "help-circle",
        "match_on": "question + assessment",
        "columns": [
            ("question", "Question", "str", True),
            ("answer", "Answer / marking guide", "str", False),
            ("question_type", "Type (oral/written/recitation/mcq)", "str", False),
            ("marks", "Marks", "decimal", False),
            ("assessment", "Assessment (title)", "str", False),
            ("book", "Book (title)", "str", False),
            ("status", "Status (active/inactive)", "str", False),
        ],
    },
}

# The list page each sheet belongs to: that page shows an "Import CSV" button opening the import for its sheet.
PAGE_ENTITIES: dict[str, str] = {
    "/clients": "clients", "/students": "students", "/crm/leads": "leads", "/teachers": "teachers",
    "/finance/invoices": "invoices", "/finance/receipts": "payments", "/finance/payments": "payments",
    "/hr/employees": "employees", "/finance/subscriptions": "subscriptions", "/subscriptions": "subscriptions",
    "/academics/courses": "courses", "/academics/config/courses": "courses",
    "/academics/packages": "packages", "/academics/config/packages": "packages",
    "/academics/config/question-bank": "questions",
}


def entity_for_path(path: str) -> str | None:
    return PAGE_ENTITIES.get((path or "").rstrip("/") or "/")


_DAY_NAMES = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _days(raw) -> list[int]:
    out = []
    for part in re.split(r"[,;/\s]+", str(raw or "")):
        p = part.strip().lower()
        if not p:
            continue
        if p.isdigit() and 0 <= int(p) <= 6:
            out.append(int(p))
        elif p[:3] in _DAY_NAMES:
            out.append(_DAY_NAMES[p[:3]])
    return sorted(set(out))


def _yes(raw, default: bool) -> bool:
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in ("yes", "y", "true", "1", "active")


def _course(db: Session, raw):
    from app.models.academic import Course
    if not raw:
        return None
    s = str(raw).strip()
    return (db.query(Course).filter(func.upper(Course.code) == s.upper()).first()
            or db.query(Course).filter(func.lower(Course.name) == s.lower()).first())

DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"]


def entity_columns(entity: str) -> list[tuple]:
    return ENTITIES.get(entity, {}).get("columns", [])


def required_columns(entity: str) -> list[str]:
    return [c[0] for c in entity_columns(entity) if c[3]]


def ensure_dir() -> Path:
    MIGRATION_DIR.mkdir(parents=True, exist_ok=True)
    return MIGRATION_DIR


# ----------------------------------------------------------------------------- parsing
def save_upload(filename: str, content: bytes) -> Path:
    ensure_dir()
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name) or "upload.csv"
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    path = MIGRATION_DIR / f"{stamp}-{safe}"
    path.write_bytes(content)
    return path


def parse_file(path: str | Path, limit: Optional[int] = None) -> tuple[list[str], list[dict]]:
    """Return (headers, rows-as-dicts). Supports .csv/.txt and .xlsx/.xlsm."""
    p = Path(path)
    if not p.exists():
        return [], []
    suffix = p.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        return _parse_xlsx(p, limit)
    return _parse_csv(p, limit)


def _clean(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return v.isoformat()[:10]
    return str(v).strip()


def _parse_csv(p: Path, limit: Optional[int]) -> tuple[list[str], list[dict]]:
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover
        text = raw.decode("utf-8", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows: list[dict] = []
    headers: list[str] = []
    for i, row in enumerate(reader):
        if i == 0:
            headers = [_clean(h) or f"column_{n + 1}" for n, h in enumerate(row)]
            continue
        if not any(_clean(c) for c in row):
            continue
        rows.append({headers[n]: _clean(c) for n, c in enumerate(row) if n < len(headers)})
        if limit and len(rows) >= limit:
            break
    return headers, rows


def _parse_xlsx(p: Path, limit: Optional[int]) -> tuple[list[str], list[dict]]:
    from openpyxl import load_workbook
    wb = load_workbook(filename=str(p), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    headers: list[str] = []
    rows: list[dict] = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            headers = [_clean(h) or f"column_{n + 1}" for n, h in enumerate(row)]
            continue
        if not any(_clean(c) for c in row):
            continue
        rows.append({headers[n]: _clean(c) for n, c in enumerate(row) if n < len(headers)})
        if limit and len(rows) >= limit:
            break
    wb.close()
    return headers, rows


def row_count(path: str | Path) -> int:
    _, rows = parse_file(path)
    return len(rows)


# ----------------------------------------------------------------------------- mapping
# Other names a file may use for a field: the old ERP's list headings (our list pages reproduce them, so a file
# from Export CSV maps too) and common spellings. Tried in order, after the field's own name and label.
SYNONYMS: dict[str, dict[str, list[str]]] = {
    "clients": {
        "client_code": ["client code", "client id", "id", "code", "legacy code", "family code"],
        "full_name": ["client name", "name", "parent name", "guardian name", "family name", "client"],
        "phone": ["cell", "cell no", "mobile", "mobile no", "phone no", "contact", "contact no"],
        "whatsapp": ["whatsapp no", "whatsapp number"],
        "joined_at": ["registration date", "reg date", "joining date", "join date", "created"],
        "country": ["country name"], "city": ["city name"], "status": ["client status", "family status"],
        "notes": ["remarks", "status remarks", "comments"],
    },
    "students": {
        "student_code": ["student code", "student id", "id", "code", "legacy code"],
        "full_name": ["student name", "name", "student"],
        "client_code": ["client code", "parent code", "family code", "client id"],
        "client_email": ["parent email", "client email", "guardian email"],
        "date_of_birth": ["dob", "d o b", "birth date", "date of birth"],
        "join_date": ["registration date", "reg date", "joining date", "admission date"],
        "status": ["student status"], "notes": ["remarks", "comments"],
    },
    "leads": {
        "full_name": ["lead", "lead name", "name", "parent name", "client name"],
        "phone": ["cell", "mobile", "contact", "contact no", "phone no"],
        "student_age": ["age"], "stage": ["status", "lead status"], "notes": ["remarks", "comments"],
    },
    "teachers": {
        "teacher_code": ["code", "id", "teacher id", "teacher code"],
        "courses": ["courses", "course", "subjects"], "verified": ["verified", "background check", "is verified"],
        "full_name": ["teacher", "teacher name", "name"],
        "phone": ["cell", "mobile", "contact no"], "per_class_rate": ["rate", "class rate", "per class"],
        "shift": ["shift name"],
    },
    "invoices": {
        "invoice_number": ["id", "invoice no", "invoice id", "invoice", "bill no"],
        "client_name": ["client", "client name", "family", "parent name"],
        "issue_date": ["invoice date", "date", "bill date"],
        "subtotal": ["subs total", "sub total", "gross amount"],
        "discount": ["discount amount", "subs discount"],
        "total": ["net amount", "grand total", "total amount", "amount", "net"],
        "paid_amount": ["paid", "received", "received amount"], "remarks": ["description", "notes"],
    },
    "payments": {
        "payment_number": ["id", "receipt no", "receipt number", "receipt id", "payment no", "voucher no"],
        "client_name": ["client", "client name", "family", "parent name"],
        "amount": ["total amount", "received amount", "net amount", "amount received", "paid amount"],
        "method": ["payment mode", "mode", "payment method", "receiving destination"],
        "reference": ["ref no", "reference no", "transaction id", "txn id"],
        "received_at": ["receipt date", "received date", "payment date", "date"],
        "invoice_number": ["invoice no", "invoice id"], "notes": ["description", "remarks"],
    },
    "employees": {
        "employee_code": ["id", "employee id", "emp code", "emp id", "code"],
        "full_name": ["name", "employee name", "staff name"],
        "phone": ["cell", "mobile", "contact no"], "cnic": ["nic", "national id", "cnic no"],
        "join_date": ["joining date", "date of joining", "doj", "hire date"],
        "shift": ["shift name"], "base_salary": ["basic salary", "salary", "basic pay"],
        "bank_account_no": ["account no", "bank account", "iban"],
    },
    "subscriptions": {
        "subscription_code": ["code", "id", "subscription id", "subscription no"],
        "student_code": ["student id", "student"], "teacher_code": ["teacher id"],
        "package": ["package name"], "course": ["course name"],
        "sessions_per_week": ["classes per week", "no of classes", "sessions"],
        "session_minutes": ["minutes", "class duration", "duration", "session"],
        "days": ["class days", "week days"], "price": ["fee", "monthly fee", "amount"],
        "start_date": ["subscription date", "date"], "next_billing_date": ["next bill", "next billing"],
        "notes": ["remarks"],
    },
    "courses": {
        "code": ["course code", "short name"], "name": ["course", "course name"],
        "course_type": ["type"], "default_session_minutes": ["minutes", "duration"], "is_active": ["status", "active"],
    },
    "questions": {
        "question": ["question text", "questions", "question title"], "answer": ["answers", "correct answer", "marking guide"],
        "question_type": ["type", "question type"], "marks": ["mark", "max marks", "score", "points"],
        "assessment": ["assessment title", "assessment name", "test", "exam"], "book": ["book title", "book name"],
    },
    "packages": {
        "name": ["description", "package", "package name"], "sessions_per_week": ["maximum days", "days", "classes per week"],
        "session_minutes": ["session", "minutes", "duration"], "is_trial": ["trial"],
    },
}


def suggest_mapping(entity: str, headers: list[str]) -> dict[str, str]:
    """Suggest target column -> file header: the field's own name or label first, then the other names a file
    may use (SYNONYMS), then a close spelling. Each heading is used once."""
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", (s or "").lower())

    norm_headers = {norm(h): h for h in headers}
    mapping: dict[str, str] = {}
    used: set[str] = set()
    cols = entity_columns(entity)

    def take(target: str, key: str) -> bool:
        h = norm_headers.get(key)
        if h and h not in used and target not in mapping:
            mapping[target] = h
            used.add(h)
            return True
        return False

    for target, label, _t, _req in cols:
        for key in (norm(target), norm(label.split("(")[0])):
            if take(target, key):
                break
    for target, _label, _t, _req in cols:
        for alt in SYNONYMS.get(entity, {}).get(target, []):
            if take(target, norm(alt)):
                break
    for target, label, _t, _req in cols:
        if target in mapping:
            continue
        free = [k for k in norm_headers if norm_headers[k] not in used]
        close = difflib.get_close_matches(norm(target), free, n=1, cutoff=0.8) or \
            difflib.get_close_matches(norm(label.split("(")[0]), free, n=1, cutoff=0.8)
        if close:
            take(target, close[0])
    # a required field outranks an optional one for a heading both could use (packages list: "Description" is the
    # package's name, not its description)
    for target, _label, _t, required in cols:
        if not required or target in mapping:
            continue
        for alt in SYNONYMS.get(entity, {}).get(target, []):
            h = norm_headers.get(norm(alt))
            holder = next((t for t, v in mapping.items() if v == h), None)
            if h and holder and not next(c for c in cols if c[0] == holder)[3]:
                del mapping[holder]
                mapping[target] = h
                break
    return mapping


def map_row(row: dict, mapping: dict) -> dict:
    return {target: (row.get(header) or "").strip() if isinstance(row.get(header), str) else row.get(header)
            for target, header in (mapping or {}).items() if header}


# ----------------------------------------------------------------------------- coercion
def parse_any_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s[:len(fmt) + 6], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


def coerce(value: Any, kind: str) -> tuple[Any, Optional[str]]:
    """Return (converted_value, error_message)."""
    s = "" if value is None else str(value).strip()
    if s == "":
        return None, None
    if kind == "date":
        d = parse_any_date(s)
        return (d, None) if d else (None, f"'{s[:40]}' is not a recognised date")
    if kind == "int":
        m = re.search(r"-?\d+(\.\d+)?", s.replace(",", ""))  # "30 Minutes" -> 30
        if m:
            return int(float(m.group(0))), None
        return None, f"'{s[:40]}' is not a whole number"
    if kind == "decimal":
        cleaned = re.sub(r"[^0-9.\-]", "", s.replace(",", ""))
        try:
            return float(Decimal(cleaned)), None
        except (InvalidOperation, ValueError, TypeError):
            return None, f"'{s[:40]}' is not a number"
    if kind == "email":
        if "@" not in s or "." not in s.split("@")[-1]:
            return s, f"'{s[:40]}' is not a valid email address"
        return s.lower(), None
    if kind == "phone":
        digits = re.sub(r"[^0-9+]", "", s)
        if len(re.sub(r"\D", "", digits)) < 7:
            return s, f"'{s[:40]}' is not a valid phone number"
        return digits, None
    return s, None


def _dup_key(entity: str, data: dict) -> Optional[str]:
    if entity == "clients":
        return (data.get("email") or data.get("phone") or data.get("client_code") or "").lower() or None
    if entity == "students":
        return (data.get("student_code") or f"{data.get('client_code', '')}|{data.get('full_name', '')}").lower() or None
    if entity == "leads":
        return (data.get("email") or data.get("phone") or "").lower() or None
    if entity == "teachers":
        return (data.get("email") or data.get("teacher_code") or "").lower() or None
    if entity == "invoices":
        return (data.get("invoice_number") or "").lower() or None
    if entity == "payments":
        return (data.get("payment_number") or data.get("reference") or "").lower() or None
    if entity == "employees":
        return (data.get("employee_code") or data.get("email") or "").lower() or None
    if entity == "subscriptions":
        return (data.get("subscription_code") or "").lower() or None
    if entity == "courses":
        return (data.get("code") or "").lower() or None
    if entity == "packages":
        return f"{data.get('name', '')}|{data.get('currency', '')}|{data.get('country', '')}".lower() if data.get("name") else None
    if entity == "questions":
        return f"{data.get('question', '')}|{data.get('assessment', '')}".strip().lower() if data.get("question") else None
    return None


def _exists_in_db(db: Session, entity: str, data: dict) -> bool:
    if entity == "clients":
        email, phone = data.get("email"), data.get("phone")
        q = db.query(Client.id)
        if email:
            if q.filter(func.lower(Client.email) == str(email).lower()).first():
                return True
        if phone and db.query(Client.id).filter(Client.phone == phone).first():
            return True
        return False
    if entity == "students":
        code = data.get("student_code")
        if code and db.query(Student.id).filter(Student.student_code == code).first():
            return True
        name, ccode = data.get("full_name"), data.get("client_code")
        if name and ccode:
            client = db.query(Client).filter(Client.client_code == ccode).first()
            if client and db.query(Student.id).filter(Student.client_id == client.id, Student.full_name == name).first():
                return True
        return False
    if entity == "leads":
        email, phone = data.get("email"), data.get("phone")
        if email and db.query(Lead.id).filter(func.lower(Lead.email) == str(email).lower()).first():
            return True
        if phone and db.query(Lead.id).filter(Lead.phone == phone).first():
            return True
        return False
    if entity == "teachers":
        email = data.get("email")
        if email and db.query(Teacher).join(User, Teacher.user_id == User.id).filter(func.lower(User.email) == str(email).lower()).first():
            return True
        return False
    if entity == "invoices":
        num = data.get("invoice_number")
        return bool(num and db.query(Invoice.id).filter(Invoice.invoice_number == num).first())
    if entity == "payments":
        num = data.get("payment_number")
        if num and db.query(Payment.id).filter(Payment.payment_number == num).first():
            return True
        ref = data.get("reference")
        return bool(ref and db.query(Payment.id).filter(Payment.reference == ref).first())
    if entity == "employees":
        from app.models.people import Employee
        code, email = data.get("employee_code"), data.get("email")
        if code and db.query(Employee.id).filter(Employee.employee_code == code).first():
            return True
        return bool(email and db.query(Employee.id).filter(func.lower(Employee.email) == str(email).lower()).first())
    if entity == "subscriptions":
        from app.models.finance import Subscription
        code = data.get("subscription_code")
        return bool(code and db.query(Subscription.id).filter(Subscription.subscription_code == code).first())
    if entity == "courses":
        from app.models.academic import Course
        code = data.get("code")
        return bool(code and db.query(Course.id).filter(func.upper(Course.code) == str(code).upper()).first())
    if entity == "packages":
        from app.models.academic import Package
        name = data.get("name")
        if not name:
            return False
        q = db.query(Package.id).filter(func.lower(Package.name) == str(name).lower())
        if data.get("currency"):
            q = q.filter(Package.currency == str(data["currency"]).upper())
        q = q.filter(Package.country == data["country"]) if data.get("country") else q.filter(Package.country.is_(None))
        return bool(q.first())
    if entity == "questions":
        from app.models.erp import QuestionBankItem
        text = data.get("question")
        if not text:
            return False
        assessment = _assessment(db, data.get("assessment"))
        q = db.query(QuestionBankItem.id).filter(func.lower(QuestionBankItem.question) == str(text).strip().lower())
        q = q.filter(QuestionBankItem.assessment_id == assessment.id) if assessment else q
        return bool(q.first())
    return False


def _assessment(db: Session, raw):
    from app.models.erp import AssessmentDefinition
    title = str(raw or "").strip()
    if not title:
        return None
    return db.query(AssessmentDefinition).filter(func.lower(AssessmentDefinition.title) == title.lower()).first()


def _book(db: Session, raw):
    from app.models.academic import Book
    title = str(raw or "").strip()
    if not title:
        return None
    return db.query(Book).filter(func.lower(Book.title) == title.lower()).first()


QUESTION_TYPES = ("oral", "written", "recitation", "mcq")


# ----------------------------------------------------------------------------- validation
def validate_job(db: Session, job: MigrationJob) -> dict:
    """Check required fields, types, in-file duplicates and database duplicates. Stores errors on the job."""
    headers, rows = parse_file(job.file_path or "")
    mapping = job.field_mapping or {}
    cols = {c[0]: c for c in entity_columns(job.entity)}
    errors: list[dict] = []
    seen: dict[str, int] = {}
    duplicates = 0
    for idx, row in enumerate(rows, start=2):  # row 1 is the header
        data = map_row(row, mapping)
        clean: dict[str, Any] = {}
        for target, raw in data.items():
            spec = cols.get(target)
            if not spec:
                continue
            value, err = coerce(raw, spec[2])
            clean[target] = value
            if err:
                errors.append({"row": idx, "field": target, "message": err, "severity": "error"})
        for req in required_columns(job.entity):
            if clean.get(req) in (None, ""):
                errors.append({"row": idx, "field": req, "message": f"Required field '{req}' is empty or unmapped", "severity": "error"})
        key = _dup_key(job.entity, {k: v for k, v in clean.items() if v is not None})
        if key:
            if key in seen:
                duplicates += 1
                errors.append({"row": idx, "field": "duplicate", "message": f"Duplicate of row {seen[key]} in this file (key {key[:60]})", "severity": "warning"})
            else:
                seen[key] = idx
        if _exists_in_db(db, job.entity, {k: v for k, v in clean.items() if v is not None}):
            duplicates += 1
            errors.append({"row": idx, "field": "duplicate", "message": "A matching record already exists in the database - row will be skipped", "severity": "warning"})
    job.records_total = len(rows)
    job.duplicates_found = duplicates
    job.errors = errors[:500]
    job.status = "validated"
    hard = sum(1 for e in errors if e["severity"] == "error")
    return {"rows": len(rows), "errors": hard, "warnings": len(errors) - hard, "duplicates": duplicates,
            "unmapped_required": [c for c in required_columns(job.entity) if c not in mapping]}


# ----------------------------------------------------------------------------- import
def _client_for(db: Session, data: dict) -> Optional[Client]:
    code = data.get("client_code")
    if code:
        c = db.query(Client).filter(Client.client_code == code).first()
        if c:
            return c
    email = data.get("client_email") or data.get("email")
    if email:
        c = db.query(Client).filter(func.lower(Client.email) == str(email).lower()).first()
        if c:
            return c
    name = (data.get("client_name") or "").strip()
    if name:
        found = db.query(Client).filter(func.lower(Client.full_name) == name.lower()).limit(2).all()
        if len(found) > 1:
            raise ValueError(f"more than one family is called '{name}': give the client code")
        if found:
            return found[0]
    return None


def _ledger(db: Session, client_id: int, entry_date: date, entry_type: str, description: str,
            debit: float = 0.0, credit: float = 0.0, currency: str = "PKR", ref_type: str = "", ref_id: int = 0) -> LedgerEntry:
    last = (db.query(LedgerEntry).filter(LedgerEntry.client_id == client_id)
            .order_by(LedgerEntry.entry_date.desc(), LedgerEntry.id.desc()).first())
    balance = float(last.balance_after or 0) if last else 0.0
    balance = balance + float(debit or 0) - float(credit or 0)
    e = LedgerEntry(client_id=client_id, entry_date=entry_date, entry_type=entry_type, description=description,
                    debit=debit, credit=credit, currency=currency, balance_after=balance,
                    reference_type=ref_type or None, reference_id=ref_id or None)
    db.add(e)
    return e


def _import_row(db: Session, entity: str, clean: dict, actor: Optional[User]) -> str:
    """Insert one record. Returns a short label. Raises ValueError to skip the row."""
    if entity == "clients":
        obj = Client(
            client_code=clean.get("client_code") or next_code(db, Client, "client_code", "C-"),
            full_name=clean["full_name"], email=clean.get("email"), phone=clean.get("phone"),
            whatsapp=clean.get("whatsapp") or clean.get("phone"), country=clean.get("country") or "Pakistan",
            city=clean.get("city"), timezone=clean.get("timezone") or settings.DEFAULT_TIMEZONE,
            currency=clean.get("currency") or settings.BASE_CURRENCY, address=clean.get("address"),
            status=clean.get("status") or "active", notes=clean.get("notes"), source="migration",
            joined_at=clean.get("joined_at") or date.today())
        if db.query(Client).filter(Client.client_code == obj.client_code).first():
            raise ValueError(f"client_code {obj.client_code} already exists")
        db.add(obj)
        db.flush()
        return obj.client_code
    if entity == "students":
        client = _client_for(db, clean)
        if not client:
            raise ValueError("no matching client (client_code / client_email)")
        obj = Student(
            student_code=clean.get("student_code") or next_code(db, Student, "student_code", "S-"),
            client_id=client.id, full_name=clean["full_name"], gender=clean.get("gender") or "male",
            date_of_birth=clean.get("date_of_birth"), level=clean.get("level"),
            status=clean.get("status") or "active", timezone=clean.get("timezone") or client.timezone,
            join_date=clean.get("join_date") or date.today(), notes=clean.get("notes"))
        if db.query(Student).filter(Student.student_code == obj.student_code).first():
            raise ValueError(f"student_code {obj.student_code} already exists")
        db.add(obj)
        db.flush()
        return obj.student_code
    if entity == "leads":
        obj = Lead(lead_code=next_code(db, Lead, "lead_code", "L-"), full_name=clean["full_name"],
                   email=clean.get("email"), phone=clean.get("phone"), whatsapp=clean.get("whatsapp"),
                   country=clean.get("country"), student_name=clean.get("student_name"),
                   student_age=clean.get("student_age"), stage=clean.get("stage") or "new", notes=clean.get("notes"))
        db.add(obj)
        db.flush()
        return obj.lead_code
    if entity == "teachers":
        obj = Teacher(teacher_code=clean.get("teacher_code") or next_code(db, Teacher, "teacher_code", "T-"),
                      full_name=clean["full_name"], gender=clean.get("gender") or "male",
                      qualifications=clean.get("qualifications"), shift=clean.get("shift") or "morning",
                      timezone=clean.get("timezone") or settings.DEFAULT_TIMEZONE,
                      per_class_rate=clean.get("per_class_rate") or 0, status=clean.get("status") or "active")
        if db.query(Teacher).filter(Teacher.teacher_code == obj.teacher_code).first():
            raise ValueError(f"teacher_code {obj.teacher_code} already exists")
        if clean.get("courses"):
            obj.courses = [c.strip().upper() for c in re.split(r"[,;/]+", str(clean["courses"])) if c.strip()]
        db.add(obj)
        db.flush()
        if _yes(clean.get("verified"), False):
            from app.core import rbac
            if actor is None or not rbac.has_permission(actor, "teachers.approve"):
                raise ValueError("verified=yes needs the teachers.approve permission (or leave the column out and verify later)")
            from app.services.people import set_teacher_verification
            set_teacher_verification(db, obj, True, actor, "Verified in the legacy ERP; imported as verified")
        return obj.teacher_code
    if entity == "invoices":
        client = _client_for(db, clean)
        if not client:
            raise ValueError("no matching client (client_code / client_email / client_name)")
        total = float(clean.get("total") or 0)
        issue = clean.get("issue_date") or date.today()
        obj = Invoice(invoice_number=clean.get("invoice_number") or next_code(db, Invoice, "invoice_number", "INV-", width=6),
                      client_id=client.id, issue_date=issue, due_date=clean.get("due_date") or issue,
                      currency=clean.get("currency") or client.currency,
                      subtotal=clean.get("subtotal") if clean.get("subtotal") is not None else total,
                      discount=clean.get("discount") or 0, total=total,
                      paid_amount=clean.get("paid_amount") or 0, total_in_base=total,
                      status=clean.get("status") or ("paid" if float(clean.get("paid_amount") or 0) >= total > 0 else "sent"),
                      remarks=clean.get("remarks") or "Imported from legacy system")
        if db.query(Invoice).filter(Invoice.invoice_number == obj.invoice_number).first():
            raise ValueError(f"invoice_number {obj.invoice_number} already exists")
        db.add(obj)
        db.flush()
        _ledger(db, client.id, issue, "invoice", f"Invoice {obj.invoice_number} (migrated)", debit=total,
                currency=obj.currency, ref_type="Invoice", ref_id=obj.id)
        return obj.invoice_number
    if entity == "payments":
        client = _client_for(db, clean)
        if not client:
            raise ValueError("no matching client (client_code / client_email / client_name)")
        amount = float(clean.get("amount") or 0)
        received = clean.get("received_at") or date.today()
        invoice = None
        if clean.get("invoice_number"):
            invoice = db.query(Invoice).filter(Invoice.invoice_number == clean["invoice_number"]).first()
        obj = Payment(payment_number=clean.get("payment_number") or next_code(db, Payment, "payment_number", "PAY-", width=6),
                      client_id=client.id, invoice_id=invoice.id if invoice else None, amount=amount,
                      currency=clean.get("currency") or client.currency, amount_in_base=amount,
                      method=clean.get("method") or "bank", reference=clean.get("reference"),
                      status="succeeded", received_at=datetime.combine(received, datetime.min.time()),
                      notes=clean.get("notes") or "Imported from legacy system")
        if db.query(Payment).filter(Payment.payment_number == obj.payment_number).first():
            raise ValueError(f"payment_number {obj.payment_number} already exists")
        db.add(obj)
        db.flush()
        if invoice:
            invoice.paid_amount = float(invoice.paid_amount or 0) + amount
            if float(invoice.paid_amount) >= float(invoice.total or 0):
                invoice.status = "paid"
        _ledger(db, client.id, received, "payment", f"Payment {obj.payment_number} (migrated)", credit=amount,
                currency=obj.currency, ref_type="Payment", ref_id=obj.id)
        return obj.payment_number
    if entity == "employees":
        from app.models.core import Department
        from app.models.people import Employee
        dept = None
        if clean.get("department"):
            d = str(clean["department"]).strip()
            dept = (db.query(Department).filter(func.lower(Department.code) == d.lower()).first()
                    or db.query(Department).filter(func.lower(Department.name) == d.lower()).first())
            if dept is None:
                raise ValueError(f"no department '{d}' (add it under Human Resource > Departments first)")
        obj = Employee(employee_code=clean.get("employee_code") or next_code(db, Employee, "employee_code", "E-"),
                       full_name=clean["full_name"], designation=clean["designation"], department_id=dept.id if dept else None,
                       email=clean.get("email"), phone=clean.get("phone"), gender=(clean.get("gender") or "male").lower(),
                       father_name=clean.get("father_name"), cnic=clean.get("cnic"), address=clean.get("address"),
                       join_date=clean.get("join_date") or date.today(),
                       employment_type=(clean.get("employment_type") or "full_time").lower(),
                       shift=(clean.get("shift") or "morning").lower(), base_salary=clean.get("base_salary") or 0,
                       currency=(clean.get("currency") or "PKR").upper()[:3], bank_name=clean.get("bank_name"),
                       bank_account_no=clean.get("bank_account_no"), status=(clean.get("status") or "active").lower())
        if db.query(Employee).filter(Employee.employee_code == obj.employee_code).first():
            raise ValueError(f"employee_code {obj.employee_code} already exists")
        db.add(obj)
        db.flush()
        return obj.employee_code
    if entity == "subscriptions":
        from app.models.academic import Package
        from app.models.finance import Subscription
        from app.services.billing import convert_to_base
        student = db.query(Student).filter(Student.student_code == clean["student_code"]).first()
        if student is None:
            raise ValueError(f"no student {clean['student_code']} (import the students first)")
        client = db.query(Client).filter(Client.client_code == clean["client_code"]).first() if clean.get("client_code") else student.client
        if client is None:
            raise ValueError("the student has no family and no client_code was given")
        package = db.query(Package).filter(func.lower(Package.name) == str(clean["package"]).lower()).first() if clean.get("package") else None
        if clean.get("package") and package is None:
            raise ValueError(f"no package '{clean['package']}'")
        course = _course(db, clean.get("course")) or (package.course if package else None)
        if clean.get("course") and course is None:
            raise ValueError(f"no course '{clean['course']}'")
        teacher = db.query(Teacher).filter(Teacher.teacher_code == clean["teacher_code"]).first() if clean.get("teacher_code") else None
        if clean.get("teacher_code") and teacher is None:
            raise ValueError(f"no teacher {clean['teacher_code']}")
        currency = (clean.get("currency") or (package.currency if package else None) or client.currency or settings.BASE_CURRENCY).upper()[:3]
        price = float(clean["price"])
        start = clean.get("start_date") or date.today()
        status = (clean.get("status") or "active").lower()
        obj = Subscription(subscription_code=clean.get("subscription_code") or next_code(db, Subscription, "subscription_code", "SUB-"),
                           client_id=client.id, student_id=student.id, package_id=package.id if package else None,
                           course_id=course.id if course else None, teacher_id=teacher.id if teacher else None,
                           sessions_per_week=clean.get("sessions_per_week") or (package.sessions_per_week if package else 5),
                           session_minutes=clean.get("session_minutes") or (package.session_minutes if package else 30),
                           list_price=price, price=price, currency=currency, price_in_base=convert_to_base(db, price, currency),
                           start_date=start, next_billing_date=clean.get("next_billing_date"), status=status,
                           days_of_week=_days(clean.get("days")), notes=clean.get("notes") or "Imported from legacy system",
                           cancelled_at=date.today() if status == "cancelled" else None)
        if db.query(Subscription).filter(Subscription.subscription_code == obj.subscription_code).first():
            raise ValueError(f"subscription_code {obj.subscription_code} already exists")
        db.add(obj)
        db.flush()
        if teacher and not student.teacher_id and status == "active":
            student.teacher_id = teacher.id
        return obj.subscription_code
    if entity == "courses":
        from app.models.academic import Course
        obj = Course(code=str(clean["code"]).strip().upper()[:20], name=clean["name"],
                     course_type=clean.get("course_type") or "Islamic Courses",
                     default_session_minutes=clean.get("default_session_minutes") or 30, fee=clean.get("fee") or 0,
                     completion_target_months=clean.get("completion_target_months"), description=clean.get("description"),
                     is_active=_yes(clean.get("is_active"), True),
                     order=(db.query(func.max(Course.order)).scalar() or 0) + 1)
        db.add(obj)
        db.flush()
        return obj.code
    if entity == "packages":
        from app.models.academic import Package
        course = _course(db, clean.get("course"))
        if clean.get("course") and course is None:
            raise ValueError(f"no course '{clean['course']}'")
        per_week = clean.get("sessions_per_week") or 5
        obj = Package(name=clean["name"], course_id=course.id if course else None, sessions_per_week=per_week,
                      session_minutes=clean.get("session_minutes") or 30, price=clean["price"],
                      currency=(clean.get("currency") or settings.BASE_CURRENCY).upper()[:3],
                      billing_cycle=(clean.get("billing_cycle") or "monthly").lower(), country=clean.get("country"),
                      is_trial=_yes(clean.get("is_trial"), False), description=clean.get("description"),
                      min_days=min(per_week, 7), max_days=min(per_week, 7))
        db.add(obj)
        db.flush()
        return obj.name
    if entity == "questions":
        from app.models.erp import QuestionBankItem
        assessment, book = _assessment(db, clean.get("assessment")), _book(db, clean.get("book"))
        if clean.get("assessment") and assessment is None:
            raise ValueError(f"no assessment '{clean['assessment']}'")
        if clean.get("book") and book is None:
            raise ValueError(f"no book '{clean['book']}'")
        qtype = str(clean.get("question_type") or "oral").strip().lower()
        if qtype == "multiple choice":
            qtype = "mcq"
        if qtype not in QUESTION_TYPES:
            raise ValueError(f"type must be one of {', '.join(QUESTION_TYPES)}, not '{qtype}'")
        status = str(clean.get("status") or "active").strip().lower()
        obj = QuestionBankItem(question=str(clean["question"]).strip(), answer=clean.get("answer"), question_type=qtype,
                               marks=float(clean.get("marks") or 1), assessment_id=assessment.id if assessment else None,
                               book_id=book.id if book else None,
                               status="inactive" if status in ("inactive", "in-active", "no", "0") else "active")
        db.add(obj)
        db.flush()
        return f"Q{obj.id}"
    raise ValueError(f"unsupported entity {entity}")


def import_job(db: Session, job: MigrationJob, actor: Optional[User]) -> dict:
    """Per-row transactional import with skip-on-error. Returns a summary dict."""
    headers, rows = parse_file(job.file_path or "")
    mapping = job.field_mapping or {}
    cols = {c[0]: c for c in entity_columns(job.entity)}
    imported, skipped, duplicates = 0, 0, 0
    errors: list[dict] = list(job.errors or [])[:0]
    seen: set[str] = set()
    financial_total = 0.0
    fin_field = ENTITIES.get(job.entity, {}).get("financial_field")
    for idx, row in enumerate(rows, start=2):
        data = map_row(row, mapping)
        clean: dict[str, Any] = {}
        row_error: Optional[str] = None
        dropped: list[tuple[str, str]] = []
        for target, raw in data.items():
            spec = cols.get(target)
            if not spec:
                continue
            value, err = coerce(raw, spec[2])
            if err:
                if spec[3]:
                    row_error = err
                else:  # a bad optional value is left out, never stored as it was ("bad-email" in the e-mail column)
                    dropped.append((target, err))
                continue
            if value not in (None, ""):
                clean[target] = value
        for req in required_columns(job.entity):
            if clean.get(req) in (None, ""):
                row_error = f"Required field '{req}' is empty"
        key = _dup_key(job.entity, clean)
        if not row_error and key and key in seen:
            row_error = "Duplicate row within the file"
            duplicates += 1
        if not row_error and _exists_in_db(db, job.entity, clean):
            row_error = "Record already exists in the database"
            duplicates += 1
        if row_error:
            skipped += 1
            errors.append({"row": idx, "field": "-", "message": row_error, "severity": "warning"})
            continue
        savepoint = db.begin_nested()
        try:
            _import_row(db, job.entity, clean, actor)
            savepoint.commit()
            imported += 1
            for target, err in dropped:
                errors.append({"row": idx, "field": target, "message": f"Imported without {target}: {err}", "severity": "warning"})
            if key:
                seen.add(key)
            if fin_field and clean.get(fin_field) is not None:
                financial_total += float(clean[fin_field])
        except Exception as exc:
            savepoint.rollback()
            skipped += 1
            errors.append({"row": idx, "field": "-", "message": f"{type(exc).__name__}: {str(exc)[:180]}", "severity": "error"})
    job.records_total = len(rows)
    job.records_imported = imported
    job.records_skipped = skipped
    job.duplicates_found = duplicates
    job.errors = errors[:500]
    job.status = "imported" if imported else "failed"
    job.completed_at = datetime.utcnow()
    return {"imported": imported, "skipped": skipped, "duplicates": duplicates, "rows": len(rows),
            "financial_total": round(financial_total, 2)}


# ----------------------------------------------------------------------------- reconciliation
def reconciliation(db: Session, job: MigrationJob) -> dict:
    headers, rows = parse_file(job.file_path or "")
    mapping = job.field_mapping or {}
    fin_field = ENTITIES.get(job.entity, {}).get("financial_field")
    file_total = 0.0
    if fin_field and mapping.get(fin_field):
        for row in rows:
            value, err = coerce(row.get(mapping[fin_field]), "decimal")
            if value:
                file_total += float(value)
    imported_total = 0.0
    if job.entity == "invoices":
        imported_total = float(db.query(func.coalesce(func.sum(Invoice.total), 0))
                               .filter(Invoice.remarks.like("%legacy%")).scalar() or 0)
    elif job.entity == "payments":
        imported_total = float(db.query(func.coalesce(func.sum(Payment.amount), 0))
                               .filter(Payment.notes.like("%legacy%")).scalar() or 0)
    hard = [e for e in (job.errors or []) if e.get("severity") == "error"]
    warn = [e for e in (job.errors or []) if e.get("severity") != "error"]
    return {
        "file_rows": len(rows), "headers": headers, "mapped_fields": len([v for v in mapping.values() if v]),
        "records_total": job.records_total, "records_imported": job.records_imported,
        "records_skipped": job.records_skipped, "duplicates": job.duplicates_found,
        "financial_field": fin_field, "file_total": round(file_total, 2), "imported_total": round(imported_total, 2),
        "variance": round(imported_total - file_total, 2) if fin_field else 0.0,
        "hard_errors": len(hard), "warnings": len(warn),
        "success_pct": round(100.0 * job.records_imported / job.records_total, 1) if job.records_total else 0.0,
    }


# ----------------------------------------------------------------------------- CSV helpers
def template_csv(entity: str) -> str:
    cols = entity_columns(entity)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c[0] for c in cols])
    sample = {
        "clients": ["LEG-1001", "Ahmed Khan", "ahmed.khan@example.com", "+447700900123", "+447700900123", "United Kingdom",
                    "Birmingham", "Europe/London", "GBP", "12 Rose Lane", "active", "2024-03-14", "Migrated from legacy ERP"],
        "students": ["LEG-S-2001", "Zainab Ahmed", "LEG-1001", "ahmed.khan@example.com", "female", "2014-06-02",
                     "Nazira 2", "active", "2024-03-20", "Europe/London", "Reads fluently"],
        "leads": ["Bilal Yusuf", "bilal@example.com", "+923001234567", "+923001234567", "Pakistan", "Hamza", "9", "new", "Facebook enquiry"],
        "teachers": ["LEG-T-11", "Qari Abdul Rehman", "abdul.rehman@example.com", "+923011234567", "male",
                     "Hafiz-e-Quran, Tajweed Ijazah", "morning", "Asia/Karachi", "350", "active"],
        "invoices": ["LEG-INV-5001", "LEG-1001", "ahmed.khan@example.com", "2025-08-01", "2025-08-07", "GBP",
                     "45.00", "0.00", "45.00", "45.00", "paid", "August tuition"],
        "payments": ["LEG-PAY-9001", "LEG-1001", "ahmed.khan@example.com", "LEG-INV-5001", "45.00", "GBP",
                     "bank", "TRX-88213", "2025-08-03", "Bank transfer"],
        "questions": ["Recite Surah Al-Fatiha with tajweed", "Correct makharij, no lahn jali", "recitation", "5",
                      "", "", "active"],
    }.get(entity)
    if sample:
        w.writerow(sample[:len(cols)])
    return buf.getvalue()


def errors_csv(job: MigrationJob) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["row", "field", "severity", "message"])
    for e in (job.errors or []):
        w.writerow([e.get("row"), e.get("field"), e.get("severity"), e.get("message")])
    return buf.getvalue()


# ----------------------------------------------------------------------------- Section 20 cutover checklist
DEFAULT_CHECKLIST = [
    {"key": "inventory", "phase": "1. Inventory", "title": "Inventory every legacy system",
     "detail": "List the legacy ERP, spreadsheets, WhatsApp exports, Google Sheets, accounting files and paper records. Record owner, record count, format and refresh frequency.", "done": False},
    {"key": "field_map", "phase": "1. Inventory", "title": "Map legacy fields to OQC entities",
     "detail": "For each source file, map its columns onto clients / students / leads / teachers / invoices / payments using the wizard's mapping step. Note fields that have no home.", "done": False},
    {"key": "cleanse", "phase": "2. Cleansing", "title": "De-duplicate and cleanse at source",
     "detail": "Fix names, normalise phone numbers to E.164, resolve duplicate families, and drop test rows before exporting.", "done": False},
    {"key": "templates", "phase": "2. Cleansing", "title": "Export into the OQC CSV templates",
     "detail": "Download the per-entity template from this page and re-shape the legacy export into it. Keep one file per entity.", "done": False},
    {"key": "dry_run", "phase": "3. Dry run", "title": "Validate on a staging database",
     "detail": "Run upload -> map -> validate against staging. Fix every hard error; review each duplicate warning.", "done": False},
    {"key": "order", "phase": "3. Dry run", "title": "Confirm the import order",
     "detail": "Clients, then students, then teachers, then invoices, then payments, then leads. Children need their parents to exist.", "done": False},
    {"key": "backup", "phase": "4. Cutover", "title": "Take a full backup immediately before cutover",
     "detail": "Create a backup in /admin/backups and run a restore test on it. This is the rollback point.", "done": False},
    {"key": "freeze", "phase": "4. Cutover", "title": "Freeze the legacy system",
     "detail": "Announce a write freeze on the legacy ERP so no new records appear mid-migration.", "done": False},
    {"key": "import", "phase": "4. Cutover", "title": "Run the production import",
     "detail": "Import each entity in order, checking the reconciliation report after every run.", "done": False},
    {"key": "reconcile", "phase": "5. Verify", "title": "Reconcile counts and financial totals",
     "detail": "Row counts, client balances and the imported financial total must match the legacy figures within the agreed tolerance.", "done": False},
    {"key": "spot_check", "phase": "5. Verify", "title": "Spot-check 20 records end to end",
     "detail": "Pick 20 families across countries and check profile, students, subscription, invoices, ledger balance and portal login.", "done": False},
    {"key": "parallel", "phase": "6. Stabilise", "title": "Run parallel for one billing cycle",
     "detail": "Keep the legacy system read-only for one month and compare invoices and payments before decommissioning.", "done": False},
    {"key": "train", "phase": "6. Stabilise", "title": "Train staff and publish the new SOPs",
     "detail": "Every department signs off that their daily workflow works in the OS.", "done": False},
    {"key": "decommission", "phase": "6. Stabilise", "title": "Archive and decommission the legacy system",
     "detail": "Take a final legacy export, store it with the off-site backups, then revoke access.", "done": False},
]


def checklist(db: Session) -> list[dict]:
    from app.services.system import get_setting
    stored = get_setting(db, "migration_checklist")
    state = {}
    if isinstance(stored, dict):
        state = stored.get("items", stored)
    out = []
    for item in DEFAULT_CHECKLIST:
        row = dict(item)
        if isinstance(state, dict):
            row["done"] = bool(state.get(item["key"], item["done"]))
        out.append(row)
    return out
