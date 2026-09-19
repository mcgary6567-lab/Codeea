"""The global search finds pages, not only records, and only the pages the searcher may open.

Their portal's search finds pages first. Ours searched eight record tables and nothing else, so typing
"accounts" answered "No matches found" to the chief executive.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _login(username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code == 303, f"login failed for {username}"
    return c


@pytest.fixture(scope="module")
def admin() -> TestClient:
    return _login("admin@oqc.local", "Admin@12345")


@pytest.fixture(scope="module")
def parent() -> TestClient:
    return _login("parent1@oqc.local", "Parent@123")


def _links(html: str) -> list[str]:
    """Links inside the results only. The slide-out menu above the page lists every launchpad address,
    so scraping the whole page would find every page whether or not the search returned it."""
    start = html.find("Results for")
    end = html.find("<footer", start)
    return re.findall(r'href="([^"]+)"', html[start:end if end > 0 else None])


def test_an_area_name_finds_the_area_and_its_pages(admin):
    r = admin.get("/search?q=accounts")
    assert r.status_code == 200
    body = r.text
    assert "No matches found" not in body
    assert "Pages" in body
    links = _links(body)
    assert "/home/accounts" in links, "the area itself is the first thing that word should find"
    assert any(u.startswith("/finance/accounts") for u in links), "and the pages inside it"


def test_a_page_name_wins_over_an_area_match(admin):
    body = admin.get("/search?q=trial+balance").text
    links = _links(body)
    assert "/finance/accounts/reports/trial-balance" in links
    # the page named for the words is listed before pages that only match through their area
    first_page = next(u for u in links if u.startswith(("/finance", "/home/accounts")))
    assert first_page == "/finance/accounts/reports/trial-balance"


def test_every_word_typed_must_match(admin):
    body = admin.get("/search?q=attendance+summary").text
    links = _links(body)
    assert "/hr/attendance/summary" in links
    assert "/finance/accounts/reports/trial-balance" not in links


def test_a_family_cannot_find_staff_pages_by_name(parent):
    r = parent.get("/search?q=employee")
    assert r.status_code == 200
    links = _links(r.text)
    assert not any(u.startswith("/hr/") for u in links), "staff pages must not surface for a family"


def test_records_still_come_back(admin):
    body = admin.get("/search?q=C-00001").text
    assert "Clients" in body and "/clients/" in body
