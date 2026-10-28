"""Online Academics drawn as the old ERP draws it: its card colours, boxed tile panels and Search Options bar (28 Oct 2026)."""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.core.nav import PALETTE
from app.main import app

ERP_COLOURS = ["#309fdb", "#13b6cf", "#2ebfbc", "#3caf85", "#81bb5f", "#dede53", "#fbce4a",
               "#ed813e", "#e95b54", "#e85d88", "#ca589d", "#854e9b", "#5a68ad"]


@pytest.fixture(scope="module")
def admin():
    c = TestClient(app)
    assert c.post("/login", data={"username": "admin@oqc.local", "password": "Admin@12345"}, follow_redirects=False).status_code == 303
    return c


def _cards(html: str) -> list[tuple[str, str]]:
    """(href, band colour) of each module card on the page."""
    return re.findall(r'<a href="([^"]+)" class="oqc-mcard .*?style="background: (#[0-9a-f]{6})"', html, re.S)


def test_palette_is_the_erp_sequence():
    assert PALETTE == ERP_COLOURS


def test_academic_home_has_the_two_boxed_panels_and_thirteen_cards_in_erp_colours(admin):
    html = admin.get("/home/academics").text
    for title in ("Client's Pending Requests", "Today's Class Status Summary"):
        assert f'aria-label="{title}"' in html.replace("&#39;", "'")
    # both tile rows start on the ERP's first colour, as the ERP draws them
    panels = re.findall(r"<section .*?</section>", html, re.S)
    assert len(panels) == 2 and all("background: #309fdb" in p for p in panels)
    cards = _cards(html)
    assert len(cards) == 13
    assert [c for _, c in cards] == ERP_COLOURS
    assert cards[6][0].endswith("/billing")  # Billing Management lands on amber in both systems


def test_sub_module_cards_cycle_the_same_colours(admin):
    cards = _cards(admin.get("/home/academics/classes").text)
    assert [c for _, c in cards][:8] == ERP_COLOURS[:8]


def test_list_pages_carry_the_search_options_bar(admin):
    html = admin.get("/classes").text
    assert "oqc-filters" in html and "Search Options" in html
    form = re.search(r'<form method="get" action="/classes".*?</form>', html, re.S).group(0)
    assert 'name="date_from"' in form and ">Search <" in form
