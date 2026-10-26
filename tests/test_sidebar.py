"""The area sidebar (26 Oct 2026): search, favourites and recent on top, groups as an accordion, icon rail."""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.core.nav import sidebar_active_url
from app.database import SessionLocal
from app.main import app
from app.models.ops import FavouritePage


def _login(email: str, password: str) -> TestClient:
    c = TestClient(app)
    assert c.post("/login", data={"username": email, "password": password}, follow_redirects=False).status_code == 303
    return c


@pytest.fixture(scope="module")
def admin():
    return _login("admin@oqc.local", "Admin@12345")


def _sidebar(html: str) -> str:
    start = html.find('id="area-sidebar"')
    return html[start:html.index("</aside>", start)] if start >= 0 else ""


def test_only_the_open_pages_group_starts_open(admin):
    sb = _sidebar(admin.get("/classes").text)
    assert 'id="sb-q"' in sb and "data-sb-rail" in sb and "Favourites" in sb
    open_groups = re.findall(r'class="sb-grp is-open[^"]*" data-sb-grp="([^"]+)"', sb)
    assert open_groups == ["Class Management"]
    assert re.search(r'class="sb-item is-active"[^>]*data-sb-label="Class Schedules"', sb)
    assert sb.count("data-sb-star") >= 81, "every page can be starred"


def test_a_detail_page_marks_the_list_it_belongs_to():
    section = {"items": [{"url": "/classes"}, {"url": "/classes/rescheduled"}]}
    assert sidebar_active_url(section, "/classes/123") == "/classes"
    assert sidebar_active_url(section, "/classes/rescheduled") == "/classes/rescheduled"
    assert sidebar_active_url(section, "/finance") == ""


def test_favourites_show_at_the_top_of_every_area(admin):
    admin.post("/favourites/toggle", data={"path": "/finance/invoices", "label": "Invoice List sbtest", "next": "/classes"})
    try:
        hr = _sidebar(admin.get("/hr/me").text)
        assert "Invoice List sbtest" in hr.split("All pages")[0]
    finally:
        s = SessionLocal()
        s.query(FavouritePage).filter(FavouritePage.label == "Invoice List sbtest").delete()
        s.commit()
        s.close()


def test_the_sidebar_script_is_served(admin):
    js = admin.get("/static/js/sidebar.js")
    assert js.status_code == 200 and "oqc-sb-rail" in js.text
    assert "oqc-sb-rail" in admin.get("/classes").text, "the rail choice is applied before the page paints"
