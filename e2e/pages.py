"""Page objects: each screen's selectors live here once, so a markup change is fixed in one place."""
from __future__ import annotations

import re

from playwright.sync_api import Page, expect


class LoginPage:
    def __init__(self, page: Page):
        self.page = page
        self.username = page.locator("input[name=username]")
        self.password = page.locator("input[name=password]")
        self.submit = page.get_by_role("button", name="Sign in")
        self.error = page.locator("text=/invalid|incorrect|too many|locked/i").first

    def open(self, next_url: str = "") -> "LoginPage":
        self.page.goto("/login" + (f"?next={next_url}" if next_url else ""))
        return self

    def sign_in(self, username: str, password: str) -> "LoginPage":
        self.username.fill(username)
        self.password.fill(password)
        self.submit.click()
        return self

    def expect_signed_in(self) -> None:
        expect(self.page).not_to_have_url(re.compile(r"/login"))

    def expect_rejected(self) -> None:
        expect(self.page).to_have_url(re.compile(r"/login"))
        expect(self.error).to_be_visible()


class Shell:
    """The top bar and drawer every signed-in page shares."""

    def __init__(self, page: Page):
        self.page = page
        self.menu = page.get_by_role("button", name=re.compile("menu", re.I)).first
        self.drawer_search = page.locator("#drawer_q")

    def sign_out(self) -> None:
        self.page.goto("/logout")


class StudentsPage:
    def __init__(self, page: Page):
        self.page = page
        self.search = page.locator("#f_q")  # the list's own filter, not the top-bar search
        self.rows = page.locator("table tbody tr")

    def open(self, **params: str) -> "StudentsPage":
        query = "&".join(f"{k}={v}" for k, v in params.items())
        self.page.goto("/students" + (f"?{query}" if query else ""))
        return self

    def search_for(self, text: str) -> "StudentsPage":
        self.search.fill(text)
        self.search.press("Enter")
        self.page.wait_for_load_state("networkidle")
        return self
