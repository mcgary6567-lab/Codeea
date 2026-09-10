"""Academic seed: courses, divisions, packages (+ curriculum sample — extended by the academic module)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.academic import Course, Division, Package, CurriculumVersion

COURSES = [
    ("QAIDA", "Norani Qaida", "القاعدة النورانية", "نورانی قاعدہ", "Foundation of Arabic letters, harakat, joining and basic Tajweed for beginners.", 6,
     ["Letters & Sounds", "Harakat & Tanween", "Sukoon & Shaddah", "Madd Letters", "Joining & Practice"]),
    ("NAZRA", "Nazra (Quran Reading)", "ناظرة", "ناظرہ قرآن", "Fluent reading of the Holy Quran with correct pronunciation.", 18,
     ["Juz 1–5", "Juz 6–10", "Juz 11–15", "Juz 16–20", "Juz 21–25", "Juz 26–30"]),
    ("HIFZ", "Hifz (Memorisation)", "حفظ", "حفظِ قرآن", "Memorisation of the Holy Quran with sabaq, sabqi and dor methodology.", 36,
     ["Juz 30 (Amma)", "Juz 29", "Juz 28", "Juz 1–5", "Juz 6–10", "Juz 11–15", "Juz 16–20", "Juz 21–27"]),
    ("TAJWEED", "Tajweed", "تجويد", "تجوید", "Rules of recitation: makhaarij, sifaat, noon/meem rules, madd, waqf.", 9,
     ["Makhaarij & Sifaat", "Noon Sakinah & Tanween", "Meem Sakinah", "Madd Rules", "Waqf & Ibtida", "Practical Recitation"]),
    ("TARJUMA", "Tarjuma (Translation)", "ترجمة", "ترجمۂ قرآن", "Word-by-word translation and understanding of the Quran.", 24,
     ["Juz 30 Translation", "Juz 1–5", "Juz 6–15", "Juz 16–30"]),
    ("ISLAMIC", "Islamic Studies", "الدراسات الإسلامية", "اسلامیات", "Aqeedah, Fiqh essentials, Seerah, duas and adab for children.", 12,
     ["Aqeedah & Pillars", "Salah & Wudu", "Seerah", "Daily Duas & Adab"]),
]

PACKAGES = [  # name, course, sessions/week, minutes, price, currency, country
    ("Trial Class", None, 1, 30, 0, "GBP", None, True),
    ("Starter — 2 days/week", None, 2, 30, 30, "GBP", "United Kingdom", False),
    ("Standard — 3 days/week", None, 3, 30, 40, "GBP", "United Kingdom", False),
    ("Intensive — 5 days/week", None, 5, 30, 55, "GBP", "United Kingdom", False),
    ("Hifz — 5 days/week (45 min)", "HIFZ", 5, 45, 75, "GBP", "United Kingdom", False),
    ("Standard — 3 days/week (USD)", None, 3, 30, 50, "USD", "United States", False),
    ("Intensive — 5 days/week (USD)", None, 5, 30, 70, "USD", "United States", False),
    ("Standard — 3 days/week (AUD)", None, 3, 30, 75, "AUD", "Australia", False),
    ("Intensive — 5 days/week (CAD)", None, 5, 30, 90, "CAD", "Canada", False),
    ("Local — 5 days/week (PKR)", None, 5, 30, 6000, "PKR", "Pakistan", False),
]


def run(db: Session) -> None:
    for code, name, ar, ur, desc, months, divisions in COURSES:
        c = db.query(Course).filter(Course.code == code).first()
        if not c:
            c = Course(code=code, name=name, arabic_name=ar, urdu_name=ur, description=desc, completion_target_months=months,
                       order=COURSES.index((code, name, ar, ur, desc, months, divisions)))
            db.add(c)
            db.flush()
            for i, dname in enumerate(divisions):
                db.add(Division(course_id=c.id, name=dname, order=i, expected_weeks=max(4, int(months * 4.3 / len(divisions)))))
            db.add(CurriculumVersion(course_id=c.id, version="1.0", is_current=True, notes="Initial curriculum baseline"))
    db.flush()
    for name, course_code, spw, mins, price, cur, country, is_trial in PACKAGES:
        if db.query(Package).filter(Package.name == name).first():
            continue
        course = db.query(Course).filter(Course.code == course_code).first() if course_code else None
        db.add(Package(name=name, course_id=course.id if course else None, sessions_per_week=spw, session_minutes=mins, price=price,
                       currency=cur, country=country, is_trial=is_trial,
                       description=f"{spw} × {mins}-minute live one-to-one classes per week" if not is_trial else "Free 30-minute assessment class"))
    db.commit()
    # Curriculum content (books/chapters/lessons) is added by app/seed/academic_curriculum.py if present
    try:
        from app.seed import academic_curriculum
        academic_curriculum.run(db)
    except ImportError:
        pass
