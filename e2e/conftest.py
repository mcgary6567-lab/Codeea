"""Browser tests against a running OQC server (Playwright, sync API, one Chromium per run).

They are kept out of the unit suite (pytest.ini collects tests/ only) because they need a live server:

    .venv/Scripts/python.exe run.py                      # or any deployed test environment
    .venv/Scripts/python.exe -m pytest e2e -q

OQC_E2E_URL points them elsewhere (default http://localhost:8000). The sign-ins default to the seeded demo accounts
(seed.py / DEMO_ACCOUNTS), so they work on a local or demo database only; OQC_E2E_<ROLE>_USER / _PASS override them.
Negative sign-in tests use an account that does not exist, so a run never locks a real one.
"""
from __future__ import annotations

import os

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from e2e.pages import LoginPage

BASE_URL = os.environ.get("OQC_E2E_URL", "http://localhost:8000").rstrip("/")

ACCOUNTS = {
    "admin": ("admin@oqc.local", "Admin@12345"),
    "teacher": ("teacher1@oqc.local", "Teacher@123"),
    "parent": ("parent1@oqc.local", "Parent@123"),
}


def account(role: str) -> tuple[str, str]:
    user, password = ACCOUNTS[role]
    key = f"OQC_E2E_{role.upper()}"
    return os.environ.get(f"{key}_USER", user), os.environ.get(f"{key}_PASS", password)


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def browser() -> Browser:
    headless = os.environ.get("OQC_E2E_HEADED") != "1"
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch(headless=headless)
        except Exception:  # Playwright's own Chromium not downloaded: use the installed Google Chrome instead
            b = pw.chromium.launch(headless=headless, channel="chrome")
        yield b
        b.close()


@pytest.fixture
def context(browser: Browser) -> BrowserContext:
    ctx = browser.new_context(base_url=BASE_URL, viewport={"width": 1440, "height": 900})
    yield ctx
    ctx.close()


@pytest.fixture
def page(context: BrowserContext) -> Page:
    p = context.new_page()
    p.errors = []  # uncaught page errors, checked by the tests that care
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    return p


_states: dict[str, dict] = {}


def _signed_in(browser: Browser, role: str, viewport: dict) -> BrowserContext:
    """A context already signed in as ``role``: one real sign-in per role per run, then its cookies are reused."""
    if role not in _states:
        ctx = browser.new_context(base_url=BASE_URL)
        LoginPage(ctx.new_page()).open().sign_in(*account(role)).expect_signed_in()
        _states[role] = ctx.storage_state()
        ctx.close()
    return browser.new_context(base_url=BASE_URL, viewport=viewport, storage_state=_states[role])


@pytest.fixture
def as_role(browser: Browser):
    opened: list[BrowserContext] = []

    def make(role: str, viewport: dict | None = None) -> Page:
        ctx = _signed_in(browser, role, viewport or {"width": 1440, "height": 900})
        opened.append(ctx)
        p = ctx.new_page()
        p.errors = []
        p.on("pageerror", lambda e: p.errors.append(str(e)))
        return p

    yield make
    for ctx in opened:
        ctx.close()


@pytest.fixture
def admin(as_role) -> Page:
    return as_role("admin")
