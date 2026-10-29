"""Layout, phone width and the WCAG 2.1 AA basics a page scan can check (names, labels, alt text, language, keyboard)."""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.pages import LoginPage, StudentsPage

STAFF_PAGES = ["/home", "/home/self", "/students", "/students/new", "/classes", "/finance/accounts/vouchers", "/home/system"]

# What a screen reader would have nothing to announce for: unlabelled fields, nameless buttons and links, images
# without alt, plus duplicate ids (which break label-for and aria references). A heuristic of the accessible-name rules.
SCAN = """() => {
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const named = el => (el.getAttribute('aria-label') || '').trim() || el.getAttribute('aria-labelledby')
      || (el.getAttribute('title') || '').trim();
  const describe = el => el.outerHTML.slice(0, 140);
  const out = {fields: [], buttons: [], images: [], ids: []};
  for (const el of document.querySelectorAll('input, select, textarea')) {
    if (['hidden', 'submit', 'button', 'reset'].includes(el.type) || !visible(el)) continue;
    const label = (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)) || el.closest('label');
    if (!label && !named(el)) out.fields.push(describe(el));
  }
  for (const el of document.querySelectorAll('button, a[href], [role=button]')) {
    if (!visible(el)) continue;
    if (!el.innerText.trim() && !named(el) && !el.querySelector('img[alt]:not([alt=""])')) out.buttons.push(describe(el));
  }
  for (const el of document.querySelectorAll('img')) if (!el.hasAttribute('alt')) out.images.push(describe(el));
  const seen = {};
  for (const el of document.querySelectorAll('[id]')) { seen[el.id] = (seen[el.id] || 0) + 1; }
  out.ids = Object.keys(seen).filter(k => seen[k] > 1);
  return out;
}"""


def _scan(page):
    return page.evaluate(SCAN)


def test_login_page_is_accessible(page):
    page.goto("/login")
    assert page.locator("html").get_attribute("lang") == "en"
    found = _scan(page)
    assert found == {"fields": [], "buttons": [], "images": [], "ids": []}, found
    expect(page.get_by_label("Email or username")).to_be_visible()  # the label is tied to its field
    expect(page.get_by_label("Password")).to_be_visible()


def test_login_works_from_the_keyboard_alone(page):
    from e2e.conftest import account
    page.goto("/login")
    user, password = account("admin")
    page.keyboard.type(user)  # the username field has autofocus
    page.keyboard.press("Tab")
    page.keyboard.type(password)
    page.keyboard.press("Enter")
    LoginPage(page).expect_signed_in()


def test_first_tab_offers_a_skip_link(admin):
    admin.goto("/students")
    admin.keyboard.press("Tab")
    focused = admin.evaluate("() => [document.activeElement.textContent.trim(), document.activeElement.getAttribute('href')]")
    assert focused[1] and focused[1].startswith("#"), f"first Tab stop is {focused}, not a skip-to-content link"


@pytest.mark.parametrize("path", STAFF_PAGES)
def test_staff_pages_name_every_control(admin, path):
    resp = admin.goto(path)
    assert resp.status == 200, (path, resp.status)
    admin.wait_for_load_state("networkidle")
    found = _scan(admin)
    assert not any(found.values()), found
    assert not admin.errors, admin.errors


@pytest.mark.parametrize("path", ["/login", "/home", "/students", "/finance/accounts/vouchers", "/home/self"])
def test_no_sideways_scroll_on_a_phone(as_role, page, path):
    p = page if path == "/login" else as_role("admin", viewport={"width": 375, "height": 812})
    if path == "/login":
        p.set_viewport_size({"width": 375, "height": 812})
    p.goto(path)
    p.wait_for_load_state("networkidle")
    over = p.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert over <= 1, f"{path} is {over}px wider than a 375px phone"


def test_student_search_finds_and_filters(admin):
    students = StudentsPage(admin).open()
    expect(students.rows.first).to_be_visible()
    first_name = students.rows.first.locator("td").nth(1).inner_text().split("\n")[0].strip()
    students.search_for(first_name)
    assert "q=" in admin.url
    expect(students.rows.first).to_contain_text(first_name.split()[0])
    students.search_for("zz-no-such-student-e2e")
    assert students.rows.count() <= 1  # an empty table, or its single "no rows" row
    assert not admin.errors
