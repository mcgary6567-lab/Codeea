"""Queries that SQLite accepts but PostgreSQL rejects.

The dev database is SQLite and production is PostgreSQL. `SELECT DISTINCT` over a whole row works on SQLite but
PostgreSQL refuses it when the row has a JSON column ("could not identify an equality operator for type json").
The User table has two, and the subscriptions filter options did exactly that, so every subscriptions page
answered 500 in production while every test passed. This guards every mapped table that carries JSON.
"""
from __future__ import annotations

import pathlib
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON
from sqlalchemy.dialects import postgresql

from app.database import Base, SessionLocal
from app.main import app
from app.models.core import User
from app.models.people import Teacher

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def json_tables() -> set[str]:
    return {t.name for t in Base.metadata.sorted_tables if any(isinstance(c.type, JSON) for c in t.columns)}


def test_user_table_is_one_of_the_json_tables():
    assert "users" in json_tables()


def test_no_whole_row_distinct_on_a_json_table():
    """Static guard: `db.query(Model)...distinct()` where Model maps a table with a JSON column."""
    json_models = {m.class_.__name__ for m in Base.registry.mappers if m.local_table.name in json_tables()}
    offenders = []
    for path in APP.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        if ".distinct(" not in src:
            continue
        src = re.sub(r"\n\s+\.", ".", src)  # fold a method chain continued on the next line onto one line
        for m in re.finditer(r"query\(\s*([A-Z]\w*)\s*\)[^;\n]*?\.distinct\(\)", src):
            if m.group(1) in json_models:
                offenders.append(f"{path.relative_to(APP.parent)}: {m.group(0)[:80]}")
    assert not offenders, "SELECT DISTINCT over a row with JSON columns breaks on PostgreSQL:\n" + "\n".join(offenders)


def test_supervisor_options_compile_without_distinct_for_postgres():
    from app.web.subscriptions import _options

    db = SessionLocal()
    try:
        q = db.query(User).filter(User.id.in_(db.query(Teacher.supervisor_id).filter(Teacher.supervisor_id.isnot(None))))
        sql = str(q.statement.compile(dialect=postgresql.dialect()))
        assert "DISTINCT" not in sql.upper()
        assert isinstance(_options(db)["supervisor_options"], list)
    finally:
        db.close()


@pytest.fixture(scope="module")
def admin() -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"username": "admin@oqc.local", "password": "Admin@12345"}, follow_redirects=False)
    assert r.status_code == 303
    return c


def test_every_subscriptions_page_renders(admin):
    for url in ("/subscriptions", "/subscriptions?report=employee", "/subscriptions?report=followups", "/subscriptions/cancelled",
                "/subscriptions/value-report", "/subscriptions/detail-report", "/subscriptions/allocation", "/subscriptions/new"):
        r = admin.get(url)
        assert r.status_code == 200, url
