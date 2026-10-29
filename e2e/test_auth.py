"""Sign-in: the happy path, the rejections, and where each role lands."""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import account
from e2e.pages import LoginPage, Shell


def test_admin_signs_in_and_out(page):
    login = LoginPage(page).open()
    login.sign_in(*account("admin")).expect_signed_in()
    cookie = next(c for c in page.context.cookies() if c["httpOnly"])
    assert cookie["sameSite"] == "Lax"  # the session cookie is out of reach of page script and cross-site posts
    Shell(page).sign_out()
    page.goto("/home")
    expect(page).to_have_url(re.compile(r"/login"))


def test_unknown_account_gets_the_generic_error(page):
    LoginPage(page).open().sign_in("nobody-e2e@example.invalid", "Wrong#Pass1").expect_rejected()
    expect(page.get_by_text("Invalid credentials.")).to_be_visible()  # never says which half was wrong


def test_empty_form_is_stopped_by_the_browser(page):
    login = LoginPage(page).open()
    login.submit.click()
    expect(page).to_have_url(re.compile(r"/login$"))
    assert login.username.evaluate("el => el.validity.valueMissing")


def test_next_cannot_send_you_to_another_site(page, base_url):
    LoginPage(page).open(next_url="https://evil.example/").sign_in(*account("admin")).expect_signed_in()
    assert page.url.startswith(base_url)


@pytest.mark.parametrize("path", ["/home", "/students", "/finance/accounts/vouchers", "/admin/roles"])
def test_signed_out_visitor_is_sent_to_login(page, path):
    page.goto(path)
    expect(page).to_have_url(re.compile(r"/login"))


@pytest.mark.parametrize("role,path", [("teacher", "/admin/roles"), ("parent", "/students"), ("parent", "/finance/accounts/vouchers")])
def test_a_role_without_the_permission_never_sees_the_page(as_role, base_url, role, path):
    page = as_role(role)
    resp = page.goto(path)
    # staff roles get the 403 page; portal roles are sent to their own home instead
    assert resp.status == 403 or page.url != base_url + path, (resp.status, page.url)
    if resp.status == 403:
        expect(page.get_by_text("Access denied")).to_be_visible()
