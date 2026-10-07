"""Every feature lives in the business module that owns it (docs/MODULE_STRUCTURE.md).

Currency Rates and Payment Gateways moved from Configuration to Accounts › Setup, Confido Agents to
HR › HR Configurations, the read-only Configuration › Roles list merged into /admin/roles, the Billing currency
list (/finance/currencies) merged into Currency Rates, and the HR branch user list (/hr/config/users) into
/hr/users. This module checks that:

* every old address still answers: a GET with 301 and a POST with 307 (method and body kept), sub-path and
  query string carried over;
* the new pages render for an administrator inside the right area sidebar;
* the people who should open them can (the HR head, an accountant) and a teacher cannot;
* Configuration's home and its sidebar no longer offer the moved screens.

Read-only against the shared development database: no redirect is followed into a handler that changes
anything, so the module can be run any number of times. /hr/users and /hr/permissions belong to another
change and are only checked as redirect targets here.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _login(username: str, password: str) -> TestClient:
    c = TestClient(app, follow_redirects=False)
    c.__enter__()
    r = c.post("/login", data={"username": username, "password": password})
    assert r.status_code == 303, f"login failed for {username}: {r.status_code}"
    return c


@pytest.fixture(scope="module")
def admin():
    c = _login("admin@oqc.local", "Admin@12345")
    yield c
    c.__exit__(None, None, None)


@pytest.fixture(scope="module")
def hr_head():
    c = _login("hr@oqc.local", "People@123")
    yield c
    c.__exit__(None, None, None)


@pytest.fixture(scope="module")
def accountant():
    c = _login("accountant@oqc.local", "Account@123")
    yield c
    c.__exit__(None, None, None)


@pytest.fixture(scope="module")
def teacher():
    c = _login("teacher1@oqc.local", "Teacher@123")
    yield c
    c.__exit__(None, None, None)


def _sidebar(html: str) -> str:
    start = html.find('id="area-sidebar"')
    return html[start:html.index("</aside>", start)] if start >= 0 else ""


def _main(html: str) -> str:
    return html[html.index("<main"):html.index("</main>")]


# =========================================================================== old addresses
GET_REDIRECTS = [
    ("/config/currency-rates", "/finance/currency-rates"),
    ("/config/currency-rates?x=1&y=two", "/finance/currency-rates?x=1&y=two"),
    ("/config/currency-rates/GBP", "/finance/currency-rates/GBP"),
    ("/config/payment-gateways", "/finance/payment-gateways"),
    ("/config/payment-gateways?status=active&sample=250", "/finance/payment-gateways?status=active&sample=250"),
    ("/config/agents", "/hr/confido-agents"),
    ("/config/agents?tab=devices&status=online", "/hr/confido-agents?tab=devices&status=online"),
    ("/config/agents/licenses/7/download", "/hr/confido-agents/licenses/7/download"),
    ("/config/agents/screenshots/3/image", "/hr/confido-agents/screenshots/3/image"),
    ("/config/roles", "/admin/roles"),
    ("/config/roles?app=Accounts", "/admin/roles?app=Accounts"),
    ("/hr/config/users", "/hr/users"),
    ("/hr/config/users?active=yes&q=ali", "/hr/users?active=yes&q=ali"),
    ("/finance/currencies", "/finance/currency-rates"),
    ("/finance/currencies?ref=old", "/finance/currency-rates?ref=old"),
    ("/finance/currencies/gbp", "/finance/currency-rates/GBP"),
]

POST_REDIRECTS = [
    ("/config/currency-rates/manual", "/finance/currency-rates/manual"),
    ("/config/currency-rates/refresh", "/finance/currency-rates/refresh"),
    ("/config/payment-gateways/new", "/finance/payment-gateways/new"),
    ("/config/payment-gateways/5/edit?keep=1", "/finance/payment-gateways/5/edit?keep=1"),
    ("/config/payment-gateways/5/toggle", "/finance/payment-gateways/5/toggle"),
    ("/config/agents/licenses/generate", "/hr/confido-agents/licenses/generate"),
    ("/config/agents/licenses/7/reveal", "/hr/confido-agents/licenses/7/reveal"),
    ("/config/agents/licenses/7/revoke", "/hr/confido-agents/licenses/7/revoke"),
    ("/config/agents/devices/3/block", "/hr/confido-agents/devices/3/block"),
    ("/config/agents/devices/3/unblock", "/hr/confido-agents/devices/3/unblock"),
]


@pytest.mark.parametrize("old,new", GET_REDIRECTS)
def test_old_get_address_is_moved_permanently(admin, old, new):
    r = admin.get(old)
    assert r.status_code == 301, f"GET {old} -> {r.status_code}"
    assert r.headers["location"] == new


@pytest.mark.parametrize("old,new", POST_REDIRECTS)
def test_old_post_address_keeps_method_and_body(admin, old, new):
    r = admin.post(old, data={"rationale": "never followed"})
    assert r.status_code == 307, f"POST {old} -> {r.status_code}"
    assert r.headers["location"] == new


def test_a_followed_post_reaches_the_new_handler():
    """A form still pointing at the old address works end to end (refused here on purpose: unknown currency)."""
    c = _login("admin@oqc.local", "Admin@12345")
    try:
        r = c.post("/config/currency-rates/manual", data={"code": "ZZZ", "rate_to_base": "1.5"}, follow_redirects=True)
        assert r.status_code == 200
        assert str(r.url).endswith("/finance/currency-rates")
        assert "Unknown currency" in r.text
    finally:
        c.__exit__(None, None, None)


def test_old_set_rate_post_still_works_and_lands_on_currency_rates(admin):
    # a zero rate is refused by the shared service, so nothing changes and the run is repeatable
    r = admin.post("/finance/currencies/GBP/rate", data={"rate": "0"})
    assert r.status_code == 303
    assert r.headers["location"] == "/finance/currency-rates"


# =========================================================================== the new pages
NEW_PAGES = [
    ("/finance/currency-rates", "Accounts", "/home/accounts"),
    ("/finance/currency-rates/GBP", "Accounts", "/home/accounts"),
    ("/finance/payment-gateways", "Accounts", "/home/accounts"),
    ("/hr/confido-agents", "Human Resource", "/home/hr"),
    ("/hr/confido-agents?tab=devices", "Human Resource", "/home/hr"),
    ("/hr/confido-agents?tab=screenshots", "Human Resource", "/home/hr"),
]


@pytest.mark.parametrize("url,area,home", NEW_PAGES)
def test_new_page_renders_in_its_business_area(admin, url, area, home):
    r = admin.get(url)
    assert r.status_code == 200, f"GET {url} -> {r.status_code}"
    side = _sidebar(r.text)
    assert f'href="{home}"' in side and f">{area}</span>" in side, f"{url} is not in the {area} sidebar"


def test_accounts_setup_and_hr_configurations_list_the_moved_pages(admin):
    setup = _sidebar(admin.get("/finance/currency-rates").text)
    assert 'href="/finance/currency-rates"' in setup and 'href="/finance/payment-gateways"' in setup
    hr_side = _sidebar(admin.get("/hr/confido-agents").text)
    assert 'href="/hr/confido-agents"' in hr_side
    hr_home = _main(admin.get("/hr/config").text)
    assert 'href="/hr/confido-agents"' in hr_home, "HR Configurations home has a Confido Agents card"
    assert "/hr/config/users" not in hr_home


def test_currency_rates_carries_what_the_billing_list_had(admin):
    body = _main(admin.get("/finance/currency-rates").text)
    assert "Active Subs" in body                                   # active subscriptions per currency
    assert 'action="/finance/currency-rates/GBP/rate"' in body     # quick per-currency Set Rate
    assert 'name="source"' in body                                 # ...recorded as manual or feed
    assert 'action="/finance/currency-rates/GBP/toggle"' in body   # activate / deactivate
    assert "Country" in body and "default currency" in body        # the country -> currency table
    detail = _main(admin.get("/finance/currency-rates/GBP").text)
    assert "Active Subscriptions" in detail and "Invoiced (all time)" in detail


def test_hr_head_opens_confido_agents(hr_head):
    assert hr_head.get("/hr/confido-agents").status_code == 200
    assert hr_head.get("/hr/confido-agents?tab=devices").status_code == 200


def test_accountant_opens_payment_gateways_and_currency_rates(accountant):
    assert accountant.get("/finance/payment-gateways").status_code == 200
    assert accountant.get("/finance/currency-rates").status_code == 200


def test_teacher_is_refused_both(teacher):
    assert teacher.get("/hr/confido-agents").status_code == 403
    assert teacher.get("/finance/payment-gateways").status_code == 403
    assert teacher.post("/finance/payment-gateways/new", data={"name": "x"}).status_code == 403
    assert teacher.post("/hr/confido-agents/licenses/generate", data={}).status_code == 403
    assert teacher.get("/hr/confido-agents/screenshots/1/image").status_code == 403


# =========================================================================== Configuration is system-wide only
MOVED_LABELS = ["Currency Rates", "Payment Gateways", "Confido Agents", "Roles &amp; Permissions", ">Roles<", ">Users<"]
MOVED_URLS = ["/config/currency-rates", "/config/payment-gateways", "/config/agents", "/config/roles",
              "/finance/currency-rates", "/finance/payment-gateways", "/hr/confido-agents", "/admin/roles",
              "/admin/users", "/hr/users"]


def test_configuration_home_no_longer_offers_the_moved_screens(admin):
    main = _main(admin.get("/config").text)
    for needle in MOVED_LABELS + MOVED_URLS + ["Currenc", "currenc", "Gateway", "gateway", "Confido", "Roles"]:
        assert needle not in main, f"the Configuration home still mentions {needle!r}"
    for kept in ["/config/lookups", "/config/branch-properties", "/admin/notifications", "/config/support-tickets",
                 "/config/whatsapp-senders", "/config/otp"]:
        assert kept in main


def test_configuration_sidebar_no_longer_lists_the_moved_screens(admin):
    side = _sidebar(admin.get("/config/lookups").text)
    assert 'href="/home/system"' in side and ">Configuration</span>" in side
    for needle in MOVED_LABELS + MOVED_URLS:
        assert needle not in side, f"the Configuration sidebar still lists {needle!r}"
