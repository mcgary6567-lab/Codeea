"""Human Resource parity seed: contract end dates and staff notices (docs/AUDIT_HUMAN_RESOURCE.md).

Two panels of the HR Home only come alive once the data behind them has shape:

* **Contract Ends (Within 2 months)** reads Employee.contract_end_date. About a third of the live staff get
  one here, and four or five of those end inside the next sixty days so the table is never empty on a
  fresh database.
* **Notifications** (their Employment Management page, and the self portal's Notifications card) reads
  StaffNotice. Ten notices are created across live, scheduled, expired and inactive, spread over the four
  audiences, a few of them in Urdu as theirs are.

Idempotent and deterministic: a contract end is only written where none is set, and a notice is matched
by title before it is created. Running it twice changes nothing the second time.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.core import User
from app.models.hr_erp import StaffNotice
from app.models.people import Employee

LIVE_STATUSES = ["active", "probation", "on_leave"]
WINDOW_DAYS = 60
SOON_ENDS = [12, 21, 33, 45, 58]        # days from today: the ends that land inside the two-month window
LATER_ENDS = [95, 130, 170, 220, 280, 340, 400]  # the rest of the contracted staff, well beyond it

# (title, description, link, audience, start offset, end offset or None, status). Offsets are days from today.
NOTICES = [
    ("Eid ul Adha holidays and class rescheduling",
     "The college is closed for three days over Eid. Classes on those days are moved forward a week; please "
     "confirm the new timings with every parent on WhatsApp before the break.",
     "https://drive.google.com/oqc/hr/eid-schedule.pdf", "all", -5, 25, "active"),
    ("عید الاضحیٰ کی تعطیلات اور کلاسوں کی ترتیب",
     "عید کے موقع پر ادارہ تین دن بند رہے گا۔ ان دنوں کی کلاسیں ایک ہفتہ آگے کر دی گئی ہیں، براہ کرم ہر والدین سے "
     "واٹس ایپ پر نئے اوقات کی تصدیق کر لیں۔",
     None, "all", -3, 30, "active"),
    ("Monthly attendance closes on the 26th",
     "Attendance change requests for this month must be in by the 26th; anything after that is carried to "
     "next month's payroll.",
     None, "Academics", -1, 14, "active"),
    ("New complaint escalation path for admin staff",
     "Complaints are read by People and Culture first and escalated to the Head of Operations only when a "
     "response is not given within three working days.",
     "https://drive.google.com/oqc/hr/complaint-escalation.pdf", "Admin", -2, None, "active"),
    ("Lead follow-up SLA is now 24 hours",
     "Every new lead must have a first contact logged within 24 hours. The lead generator dashboard shows "
     "overdue leads in red from Monday.",
     None, "Marketing", -7, 21, "active"),
    ("Annual Tajweed refresher: registration opens",
     "The two-day refresher runs at the head office next month. Register through the Ustaadh Lab page; "
     "attendance counts towards the annual development plan.",
     "https://drive.google.com/oqc/hr/tajweed-refresher.pdf", "Academics", 7, 37, "active"),
    ("تنخواہ کی ادائیگی کی نئی تاریخ",
     "اگلے ماہ سے تنخواہ ہر مہینے کی پانچ تاریخ کو ادا کی جائے گی۔ بینک کی تفصیلات درست کرانے کے لیے پیپل اینڈ کلچر "
     "سے رابطہ کریں۔",
     None, "all", 14, 44, "active"),
    ("Ramadan timings for the night shift",
     "During Ramadan the night shift starts one hour later and the second session is shortened by thirty "
     "minutes. Duty hours are adjusted in the attendance grid automatically.",
     None, "all", -120, -90, "active"),
    ("Independence Day dress code",
     "Green and white on the 14th, and the head office assembly at 9 am. Remote staff join on the usual "
     "team call.",
     None, "all", -40, -34, "active"),
    ("Draft: revised leave policy (not yet approved)",
     "The revised policy raises casual leave to fourteen days and adds two paternity days. It is with the "
     "management committee and is not in force until this notice is activated.",
     "https://drive.google.com/oqc/hr/leave-policy-draft.pdf", "all", -1, 60, "inactive"),
]


def _contract_ends(db: Session, today: date) -> tuple[int, int, int]:
    """Give about a third of the live staff a contract end, the first few inside the two-month window."""
    staff = db.query(Employee).filter(Employee.status.in_(LIVE_STATUSES)).order_by(Employee.id).all()
    written = 0
    # Top up the window first: on a fresh database this is what fills the HR Home table, and on a database
    # that has aged past its original ends it keeps the panel from going quiet.
    soon = [e for e in staff if e.contract_end_date and today <= e.contract_end_date <= today + timedelta(days=WINDOW_DAYS)]
    need = len(SOON_ENDS) - len(soon)
    # Every third employee by id, never by position in a list that shrinks as dates are written: the same
    # people are chosen on every run, and once they carry a date they are left alone.
    picks = [e for e in staff if e.contract_end_date is None and e.id % 3 == 0]
    for e in picks:
        if need <= 0:
            break
        e.contract_end_date = today + timedelta(days=SOON_ENDS[len(SOON_ENDS) - need])
        need -= 1
        written += 1
    later = [e for e in picks if e.contract_end_date is None]
    for i, e in enumerate(later):
        e.contract_end_date = today + timedelta(days=LATER_ENDS[i % len(LATER_ENDS)] + 7 * (i // len(LATER_ENDS)))
        written += 1
    db.flush()
    with_end = db.query(Employee).filter(Employee.status.in_(LIVE_STATUSES), Employee.contract_end_date.isnot(None)).count()
    in_window = db.query(Employee).filter(Employee.status.in_(LIVE_STATUSES), Employee.contract_end_date >= today,
                                          Employee.contract_end_date <= today + timedelta(days=WINDOW_DAYS)).count()
    return written, with_end, in_window


def _notices(db: Session, today: date) -> int:
    author = db.query(User).filter(User.email == "hr@oqc.local").first()
    made = 0
    for title, description, link, audience, start_off, end_off, status in NOTICES:
        if db.query(StaffNotice).filter(StaffNotice.title == title).first():
            continue
        db.add(StaffNotice(title=title, description=description, link=link, audience=audience, status=status,
                           start_date=today + timedelta(days=start_off),
                           end_date=(today + timedelta(days=end_off)) if end_off is not None else None,
                           created_by_id=author.id if author else None))
        made += 1
    db.flush()
    return made


def _notice_counts(db: Session, today: date) -> dict:
    counts = {"live": 0, "scheduled": 0, "expired": 0, "inactive": 0}
    for n in db.query(StaffNotice):
        if n.status != "active":
            counts["inactive"] += 1
        elif n.start_date > today:
            counts["scheduled"] += 1
        elif n.end_date and n.end_date < today:
            counts["expired"] += 1
        else:
            counts["live"] += 1
    return counts


def run(db: Session) -> None:
    today = date.today()
    written, with_end, in_window = _contract_ends(db, today)
    made = _notices(db, today)
    c = _notice_counts(db, today)
    print(f"    parity_hr: contract ends +{written} ({with_end} live employees carry one, {in_window} end within "
          f"{WINDOW_DAYS} days), staff notices +{made} (live {c['live']} / scheduled {c['scheduled']} / "
          f"expired {c['expired']} / inactive {c['inactive']})")
