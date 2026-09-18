"""Parity seed for the two Configuration gaps closed on the 17 September walk (docs/AUDIT_PARITY_WALK_2026-09-17.md).

    QA Feedback Questions   eight catalogue questions across the client, student and staff audiences
    Confido Agents          the Agent Licenses Allowed branch property, three licences (one revoked), five devices
                            across the online / offline / blocked states on real employees, and about thirty
                            screenshots over the last week pointing at one generated placeholder image

Idempotent: questions are matched on (audience, text), licences on their key, devices on their machine id, the
screenshots by their seed note, so `seed.py` can be re-run over a live database. Runs after `config_erp`
(the Setup screen) and `hr` (employees). ASCII output only.
"""
from __future__ import annotations

import struct
import zlib
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models.config_erp import AgentDevice, AgentLicense, AgentScreenshot
from app.models.core import Setting, User
from app.models.erp import FeedbackQuestion
from app.models.people import Employee

# applies_to, question, question_urdu, answer_type, is_required
QUESTIONS: list[tuple[str, str, str | None, str, bool]] = [
    ("client", "Was the teacher punctual for the classes this month?",
     "کیا استاد اس ماہ کلاسوں کے لیے وقت کے پابند تھے؟", "yes_no", True),
    ("client", "How would you rate the teacher's explanation and patience?",
     "استاد کی وضاحت اور صبر کو آپ کتنے نمبر دیں گے؟", "rating", True),
    ("client", "How satisfied are you with the class timing and reminders?", None, "rating", True),
    ("client", "Is there anything the academic team should change?",
     "کیا اکیڈمک ٹیم کو کچھ بدلنا چاہیے؟", "text", False),
    ("student", "Did you understand today's lesson?", "کیا آپ کو آج کا سبق سمجھ آیا؟", "yes_no", True),
    ("student", "How much did you enjoy the class?", None, "rating", True),
    ("staff", "How supported do you feel by your supervisor?", None, "rating", True),
    ("staff", "What would make your shift easier?", "آپ کی شفٹ کو آسان بنانے کے لیے کیا کیا جا سکتا ہے؟", "text", False),
]

AGENT_SETTING = ("agent_licenses_allowed", "hr", "Agent Licenses Allowed",
                 "How many Confido recording-agent licences the branch may have active at once (Configuration > Confido Agents).",
                 5, "number", "licences", 40)

# key, issued-to user email, status, days ago issued, notes
LICENSES = [
    ("OQC-AGENT-SEED-0001-m9Kx7tQ2Lp4Vb8Rn", "manager@oqc.local", "active", 40, "Operations manager - office desktop"),
    ("OQC-AGENT-SEED-0002-Wq3Zr8Hd5Ns1Ct6Y", "supervisor@oqc.local", "active", 21, "Supervisor - desktop and night laptop"),
    ("OQC-AGENT-SEED-0003-Fp2Jm6Vx9Kb4Ld7T", "qaofficer@oqc.local", "revoked", 60, "Revoked when the QA laptop was replaced"),
]

# machine id, machine name, licence index, employee email, os, version, ip, status, minutes since last seen
DEVICES = [
    ("SEED-PC-MGR-01", "OPS-MANAGER-PC", 0, "manager@oqc.local", "Windows 11 Pro", "4.7.11", "192.168.10.21", "online", 2),
    ("SEED-PC-SUP-01", "SUPERVISOR-PC", 1, "supervisor@oqc.local", "Windows 10 Pro", "4.7.11", "192.168.10.34", "online", 4),
    ("SEED-LT-SUP-02", "SUPERVISOR-LAPTOP", 1, "supervisor@oqc.local", "Windows 11 Home", "4.7.9", "10.8.0.12", "offline", 9 * 60),
    ("SEED-PC-QA-01", "QA-OBSERVER-PC", 2, "qaofficer@oqc.local", "Windows 10 Pro", "4.6.2", "192.168.10.57", "blocked", 30 * 24 * 60),
    ("SEED-PC-ACC-01", "ACCOUNTS-PC", 0, "accountant@oqc.local", "Windows 11 Pro", "4.7.11", "192.168.10.44", "offline", 26 * 60),
]

SCREENSHOT_COUNT = 30
SEED_NOTE = "seed: parity walk placeholder"
PLACEHOLDER = Path("agent_screenshots") / "seed" / "placeholder.png"


# --------------------------------------------------------------------------- placeholder image
def _png_bytes(width: int = 320, height: int = 180) -> bytes:
    """A small valid PNG (soft blue gradient with a darker band, like a desktop with a taskbar), no Pillow needed."""
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter: none
        band = y > height - 24
        for x in range(width):
            if band:
                rows += bytes((30, 41, 59))
            else:
                rows += bytes((200 + (x * 40) // width, 215 + (y * 30) // height, 240))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
            + chunk(b"IEND", b""))


def _ensure_placeholder() -> Path:
    root = settings.storage_dir / "agent_screenshots"
    root.mkdir(parents=True, exist_ok=True)
    keep = root / ".gitkeep"
    if not keep.exists():
        keep.touch()
    target = settings.storage_dir / PLACEHOLDER
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(_png_bytes())
    return target


# --------------------------------------------------------------------------- feedback questions
def _seed_questions(db: Session) -> int:
    created = 0
    next_sort: dict[str, int] = {}
    for applies_to, question, urdu, answer_type, required in QUESTIONS:
        if applies_to not in next_sort:
            from sqlalchemy import func
            next_sort[applies_to] = (db.query(func.max(FeedbackQuestion.sort_no))
                                     .filter(FeedbackQuestion.applies_to == applies_to).scalar() or 0)
        if db.query(FeedbackQuestion).filter(FeedbackQuestion.applies_to == applies_to,
                                             FeedbackQuestion.question == question).first():
            continue
        next_sort[applies_to] += 1
        db.add(FeedbackQuestion(question=question, question_urdu=urdu, answer_type=answer_type, applies_to=applies_to,
                                is_required=required, sort_no=next_sort[applies_to], status="active"))
        created += 1
    db.flush()
    return created


# --------------------------------------------------------------------------- the setting
def _seed_setting(db: Session) -> int:
    key, group, label, description, value, value_type, unit, sort_no = AGENT_SETTING
    if db.query(Setting).filter(Setting.key == key).first():
        return 0
    db.add(Setting(key=key, value={"value": value}, group=group, description=description, label=label,
                   value_type=value_type, unit=unit, is_secret=False, is_editable=True, sort_no=sort_no))
    db.flush()
    return 1


# --------------------------------------------------------------------------- licences, devices, screenshots
def _user(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email).first()


def _employee(db: Session, email: str, fallback: list[Employee]) -> Employee | None:
    user = _user(db, email)
    emp = db.query(Employee).filter(Employee.user_id == user.id).first() if user else None
    if emp:
        return emp
    return fallback.pop(0) if fallback else None


def _seed_licenses(db: Session) -> tuple[list[AgentLicense], int]:
    admin = _user(db, "admin@oqc.local")
    now = datetime.utcnow()
    rows, created = [], 0
    for key, email, status, days_ago, notes in LICENSES:
        lic = db.query(AgentLicense).filter(AgentLicense.license_key == key).first()
        if not lic:
            owner = _user(db, email)
            lic = AgentLicense(license_key=key, issued_to_user_id=owner.id if owner else None,
                               issued_by_id=admin.id if admin else None, issued_at=now - timedelta(days=days_ago),
                               expires_at=None, status=status, notes=notes)
            db.add(lic)
            db.flush()
            created += 1
        rows.append(lic)
    return rows, created


def _seed_devices(db: Session, licences: list[AgentLicense]) -> tuple[list[AgentDevice], int]:
    now = datetime.utcnow()
    spare = (db.query(Employee).filter(Employee.status == "active", Employee.is_teacher.is_(False))
             .order_by(Employee.id).limit(10).all())
    rows, created = [], 0
    for machine_id, name, lic_idx, email, os_name, version, ip, status, minutes in DEVICES:
        device = db.query(AgentDevice).filter(AgentDevice.machine_id == machine_id).first()
        if not device:
            emp = _employee(db, email, spare)
            device = AgentDevice(license_id=licences[lic_idx].id, employee_id=emp.id if emp else None,
                                 machine_name=name, machine_id=machine_id, os_name=os_name, agent_version=version,
                                 ip_address=ip, last_seen_at=now - timedelta(minutes=minutes), status=status)
            db.add(device)
            db.flush()
            created += 1
        rows.append(device)
    return rows, created


def _seed_screenshots(db: Session, devices: list[AgentDevice]) -> int:
    existing = db.query(AgentScreenshot).filter(AgentScreenshot.note == SEED_NOTE).count()
    if existing >= SCREENSHOT_COUNT:
        return 0
    _ensure_placeholder()
    capturing = [d for d in devices if d.status != "blocked"] or devices
    anchor = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    created = 0
    for i in range(existing, SCREENSHOT_COUNT):
        device = capturing[i % len(capturing)]
        captured_at = anchor - timedelta(hours=i * 5 + (i % 3), minutes=(i * 7) % 60)  # spread over ~6 days
        db.add(AgentScreenshot(device_id=device.id, employee_id=device.employee_id, captured_at=captured_at,
                               image_path=PLACEHOLDER.as_posix(), note=SEED_NOTE))
        created += 1
    db.flush()
    return created


def run(db: Session) -> None:
    questions = _seed_questions(db)
    setting = _seed_setting(db)
    licences, new_licences = _seed_licenses(db)
    devices, new_devices = _seed_devices(db, licences)
    shots = _seed_screenshots(db, devices)
    db.commit()
    print(f"    parity_config: {questions} feedback questions, {setting} setting, {new_licences} licences, "
          f"{new_devices} devices, {shots} screenshots (+ placeholder at storage/{PLACEHOLDER.as_posix()})")
