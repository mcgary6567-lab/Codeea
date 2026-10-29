"""Security hygiene seen from the browser: headers, CSRF, reflected input, files served from storage."""
from __future__ import annotations

import pytest

PAYLOADS = ['<script>window.__xss=1</script>', '"><img src=x onerror="window.__xss=1">', "' OR 1=1 --", "%' UNION SELECT 1 --"]


def test_pages_carry_the_security_headers(page):
    headers = page.goto("/login").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "SAMEORIGIN"
    csp = headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors" in csp
    assert "geolocation=()" in headers["permissions-policy"]


def test_a_cross_site_form_post_is_blocked(admin, base_url):
    resp = admin.request.post(base_url + "/students/new", headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
    assert resp.status == 403


@pytest.mark.parametrize("payload", PAYLOADS)
def test_search_input_is_escaped_not_run(admin, payload):
    for path in ("/students?q=", "/search?q="):
        admin.goto(path + payload.replace("#", "%23"))
        assert admin.evaluate("window.__xss") is None, f"{path} ran the payload"
        assert admin.locator("body").count() == 1  # the page still rendered (no 500 from a SQL-looking string)
    assert not admin.errors


def test_uploads_are_sandboxed(page):
    resp = page.request.get("/storage/does-not-exist.svg")
    if resp.status == 200:
        assert resp.headers["content-security-policy"].startswith("sandbox")


def test_staff_screen_captures_are_not_public(page):
    assert page.request.get("/storage/agent_screenshots/any.png").status == 404
