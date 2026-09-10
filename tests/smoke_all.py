"""Full-platform smoke crawler.

Signs in as every seeded role and issues a GET against every registered web route that takes no
path parameters (parameterised routes are exercised with a real id where one can be resolved).
Any 500, template error or unexpected redirect is reported.

    .venv/Scripts/python.exe tests/smoke_all.py            # crawl every role
    .venv/Scripts/python.exe tests/smoke_all.py admin      # crawl one role

Output is ASCII-only so it is safe on a cp1252 Windows console.
"""
from __future__ import annotations

import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.database import SessionLocal  # noqa: E402

ROLES = {
    "admin": ("admin@oqc.local", "Admin@12345"),
    "sysadmin": ("sysadmin@oqc.local", "Sys@12345"),
    "manager": ("manager@oqc.local", "Manager@123"),
    "supervisor": ("supervisor@oqc.local", "Super@123"),
    "hod_finance": ("finance@oqc.local", "Finance@123"),
    "hod_people": ("hr@oqc.local", "People@123"),
    "hod_academics": ("academics@oqc.local", "Academ@123"),
    "hod_qa": ("qa@oqc.local", "Quality@123"),
    "hod_marketing": ("marketing@oqc.local", "Market@123"),
    "closer": ("closer@oqc.local", "Closer@123"),
    "billing": ("billing@oqc.local", "Billing@123"),
    "accountant": ("accountant@oqc.local", "Account@123"),
    "qa_officer": ("qaofficer@oqc.local", "QAOff@123"),
    "hr_officer": ("hrofficer@oqc.local", "HROff@123"),
    "coordinator": ("coordinator@oqc.local", "Coord@123"),
    "teacher": ("teacher1@oqc.local", "Teacher@123"),
    "parent": ("parent1@oqc.local", "Parent@123"),
    "student": ("student1@oqc.local", "Student@123"),
    "auditor": ("auditor@oqc.local", "Auditor@123"),
}

SKIP = re.compile(r"^/(static|storage|logout|api/(docs|redoc|openapi))")


def collect_get_paths() -> list[str]:
    """Every GET path registered on the app.

    Recent FastAPI versions store ``app.include_router`` results as an ``_IncludedRouter``
    wrapper instead of flattening the child routes into ``app.routes``, so the real routes
    live on ``original_router`` and the mount prefix on ``include_context.prefix``.
    """
    paths: set[str] = set()

    def walk(routes, prefix: str = ""):
        for r in routes:
            original = getattr(r, "original_router", None)
            if original is not None:
                ctx = getattr(r, "include_context", None)
                walk(original.routes, prefix + (getattr(ctx, "prefix", "") or ""))
                continue
            path = getattr(r, "path", None)
            methods = getattr(r, "methods", set()) or set()
            if path and "GET" in methods:
                paths.add(prefix + path)
            sub = getattr(r, "routes", None)
            if sub:
                walk(sub, prefix + (path or ""))

    walk(app.routes)
    return sorted(p for p in paths if not SKIP.match(p))


def resolve_ids() -> dict[str, str]:
    """Real primary keys so parameterised routes can be crawled."""
    from app.models.people import Client, Student, Teacher, Employee
    from app.models.core import User
    ids: dict[str, str] = {}
    db = SessionLocal()
    try:
        def first(model):
            row = db.query(model.id).order_by(model.id).first()
            return str(row[0]) if row else None

        mapping = {
            "student_id": Student, "client_id": Client, "teacher_id": Teacher,
            "employee_id": Employee, "user_id": User,
        }
        for key, model in mapping.items():
            v = first(model)
            if v:
                ids[key] = v
        generic = first(Student) or "1"
        ids.setdefault("id", generic)
    finally:
        db.close()
    return ids


def fill(path: str, ids: dict[str, str]) -> str | None:
    """Substitute path params; return None when a param cannot be resolved."""
    def repl(m):
        name = m.group(1).split(":")[0]
        return ids.get(name, ids.get("id", ""))
    out = re.sub(r"\{([^}]+)\}", repl, path)
    return None if "{" in out or "//" in out else out


def crawl(role: str, creds: tuple[str, str], paths: list[str], ids: dict[str, str]) -> tuple[int, int, list[str]]:
    user, pwd = creds
    problems: list[str] = []
    ok = checked = 0
    with TestClient(app) as c:
        r = c.post("/login", data={"username": user, "password": pwd}, follow_redirects=False)
        if r.status_code != 303:
            return 0, 0, [f"LOGIN FAILED {user} -> {r.status_code}"]
        for p in paths:
            target = fill(p, ids)
            if target is None:
                continue
            checked += 1
            try:
                resp = c.get(target, follow_redirects=False)
            except Exception as exc:  # template crash, unhandled error
                problems.append(f"{resp_code_str(None)} {target} :: {type(exc).__name__}: {str(exc)[:140]}")
                continue
            if resp.status_code >= 500:
                problems.append(f"{resp.status_code} {target}")
            else:
                ok += 1
    return ok, checked, problems


def resp_code_str(code) -> str:
    return "EXC" if code is None else str(code)


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    paths = collect_get_paths()
    ids = resolve_ids()
    print(f"Crawling {len(paths)} GET routes; ids={ids}")
    total_problems = 0
    for role, creds in ROLES.items():
        if only and role != only:
            continue
        ok, checked, problems = crawl(role, creds, paths, ids)
        status = "OK " if not problems else "FAIL"
        print(f"[{status}] {role:<14} {ok}/{checked} pages served")
        for p in problems[:12]:
            print(f"        {p}")
        if len(problems) > 12:
            print(f"        ... and {len(problems) - 12} more")
        total_problems += len(problems)
    print()
    print("RESULT:", "no server errors" if total_problems == 0 else f"{total_problems} problems")
    return 1 if total_problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
