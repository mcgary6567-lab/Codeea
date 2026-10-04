"""Capture every launchpad page of the running app for the user guide.

For every unique page URL: one 1440x900 screenshot, and a JSON record of what the page shows
(title, subtitle, stat tiles, table columns, tabs, filter fields, action buttons, empty-state text).
Also renders every Lucide icon the navigation uses to a PNG, for the guide's page headings.

Run with the app listening on http://localhost:8000 (dev seed loaded).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import os

REPO_ROOT = Path(__file__).resolve().parents[2]
# Screenshots, page facts and the finished PDF land here (override with GUIDE_DIR).
GUIDE_DIR = Path(os.environ.get("GUIDE_DIR", REPO_ROOT / "build" / "user_guide" / "out"))
GUIDE_DIR.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(REPO_ROOT))
from app.core import nav  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://localhost:8000"
OUT = GUIDE_DIR
SHOTS = OUT / "shots"
ICONS = OUT / "icons"
SHOTS.mkdir(exist_ok=True)
ICONS.mkdir(exist_ok=True)

ACCOUNTS = {
    "admin": ("admin@oqc.local", "Admin@12345"),
    "teacher": ("teacher1@oqc.local", "Teacher@123"),
    "client": ("parent1@oqc.local", "Parent@123"),
    "student": ("student1@oqc.local", "Student@123"),
}

EXTRACT_JS = r"""
() => {
  const txt = (el) => (el ? (el.innerText ?? el.textContent ?? '').replace(/\s+/g, ' ').trim() : '');
  const main = document.querySelector('main') || document.body;
  const h1 = main.querySelector('h1');
  const sub = h1 && h1.nextElementSibling && h1.nextElementSibling.tagName === 'P' ? txt(h1.nextElementSibling) : '';
  const stats = [];
  main.querySelectorAll('[style*="background"]').forEach(el => {
    const kids = el.children;
    if (kids.length >= 2 && /text-\[30px\]/.test(kids[0].className || '')) {
      stats.push({label: txt(kids[1]), value: txt(kids[0])});
    }
  });
  const tables = [];
  main.querySelectorAll('table').forEach(t => {
    const heads = Array.from(t.querySelectorAll('thead th')).map(txt).filter(Boolean);
    if (heads.length) tables.push({columns: heads, rows: t.querySelectorAll('tbody tr').length});
  });
  const tabs = [];
  main.querySelectorAll('nav a, [role=tablist] a, [role=tablist] button').forEach(a => { const s = txt(a); if (s && s.length < 40) tabs.push(s); });
  const fields = [];
  main.querySelectorAll('form label').forEach(l => { const s = txt(l); if (s && s.length < 60 && !fields.includes(s)) fields.push(s); });
  main.querySelectorAll('form input[placeholder], form select, form textarea[placeholder]').forEach(i => {
    const s = i.getAttribute('placeholder') || (i.getAttribute('name') || '').replace(/_/g, ' ');
    if (s && s.length < 60 && !fields.includes(s) && !/select/i.test(s)) fields.push(s);
  });
  const buttons = [];
  main.querySelectorAll('button, a.inline-flex, a[class*="rounded"][class*="px-"]').forEach(b => {
    const s = txt(b);
    if (!s || s.length > 40 || /^\d+$/.test(s)) return;
    if (b.closest('table') || b.closest('nav')) return;
    if (!buttons.includes(s)) buttons.push(s);
  });
  const rowActions = [];
  main.querySelectorAll('table tbody a, table tbody button').forEach(b => {
    const s = txt(b) || b.getAttribute('title') || '';
    if (s && s.length < 30 && !/^[\d.,\-\s%A-Z]{1,3}$/.test(s) && !rowActions.includes(s)) rowActions.push(s);
  });
  const cards = [];
  main.querySelectorAll('a[href] > div + div').forEach(d => { const s = txt(d); if (s && s.length < 60) cards.push(s); });
  const empty = Array.from(main.querySelectorAll('td[colspan], .text-slate-400')).map(txt).filter(s => s && s.length < 120).slice(0, 3);
  const headings = Array.from(main.querySelectorAll('h2, h3')).map(txt).filter(s => s && s.length < 70);
  const sidebar = Array.from(document.querySelectorAll('#area-sidebar a')).map(txt).filter(Boolean);
  return {title: txt(h1), subtitle: sub, stats, tables, tabs: [...new Set(tabs)], fields, buttons, rowActions: rowActions.slice(0, 25), cards, empty, headings, sidebar};
}
"""


def slug(url: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", url.strip("/")) or "home"
    return s.strip("_")[:90]


def pages_for(portal: str, source: list[dict]) -> list[dict]:
    """Every launchpad page, in guide order: home, then per section its home, groups and items."""
    out: list[dict] = [{"portal": portal, "url": "/home", "kind": "home", "label": "Home"}]
    for sec in source:
        out.append({"portal": portal, "url": f"/home/{sec['slug']}", "kind": "section", "label": sec["label"],
                    "icon": sec["icon"], "section": sec["slug"]})
        if sec.get("groups"):
            for g in sec["groups"]:
                out.append({"portal": portal, "url": f"/home/{sec['slug']}/{g['slug']}", "kind": "group", "label": g["label"],
                            "icon": g["icon"], "section": sec["slug"], "group": g["slug"]})
                for it in g["items"]:
                    out.append({"portal": portal, "url": it["url"], "kind": "page", "label": it["label"], "icon": it["icon"],
                                "section": sec["slug"], "group": g["slug"], "perm": it["perm"]})
        else:
            for it in sec["items"]:
                out.append({"portal": portal, "url": it["url"], "kind": "page", "label": it["label"], "icon": it["icon"],
                            "section": sec["slug"], "perm": it["perm"]})
    return out


def login(page, username: str, password: str) -> None:
    page.goto(f"{BASE}/logout", wait_until="load")
    page.goto(f"{BASE}/login", wait_until="load")
    page.fill("input[name=username]", username)
    page.fill("input[name=password]", password)
    page.click("form button:has-text(\"Sign in\")")
    page.wait_for_load_state("load")
    assert "/login" not in page.url, f"login failed for {username}: {page.url}"


def capture(page, rec: dict, name: str) -> dict:
    page.goto(BASE + rec["url"], wait_until="load")
    try:
        page.wait_for_load_state("networkidle", timeout=4000)
    except Exception:
        pass
    page.wait_for_timeout(250)  # let lucide swap icons and Alpine settle
    out = SHOTS / f"{name}.png"
    page.screenshot(path=str(out), full_page=False)
    facts = page.evaluate(EXTRACT_JS)
    facts["status_url"] = page.url
    facts["shot"] = out.name
    return facts


def main() -> None:
    plan: list[dict] = pages_for("admin", nav.ADMIN_NAV)
    plan += pages_for("teacher", nav.TEACHER_NAV)
    plan += pages_for("client", nav.CLIENT_NAV)
    plan += pages_for("student", nav.STUDENT_NAV)
    extras = [
        {"portal": "admin", "url": "/search?q=trial+balance", "kind": "extra", "label": "Search results"},
        {"portal": "admin", "url": "/notifications", "kind": "extra", "label": "Notifications"},
        {"portal": "admin", "url": "/profile", "kind": "extra", "label": "My Profile"},
    ]
    plan += extras

    results: dict[str, dict] = {}
    with sync_playwright() as p:
        browser = None
        for channel in ("chrome", "msedge"):
            try:
                browser = p.chromium.launch(channel=channel, headless=True)
                break
            except Exception as e:  # noqa: BLE001
                print("launch failed for", channel, e)
        assert browser, "no installed browser could be launched"
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        page = ctx.new_page()
        page.set_default_timeout(20000)  # a page that never finishes loading must not stall the whole run

        # anonymous pages
        page.goto(f"{BASE}/login", wait_until="load")
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "login.png"))
        page.goto(f"{BASE}/register", wait_until="load")
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "register.png"))

        current = None
        for rec in plan:
            key = f"{rec['portal']}:{rec['url']}"
            if key in results:
                continue
            if rec["portal"] != current:
                login(page, *ACCOUNTS[rec["portal"]])
                current = rec["portal"]
                # the menu drawer and the header
                if current == "admin":
                    page.goto(f"{BASE}/home", wait_until="load")
                    page.wait_for_timeout(300)
                    page.click("button[title=Menu]")
                    page.wait_for_timeout(400)
                    page.screenshot(path=str(SHOTS / "menu_drawer.png"))
                    page.keyboard.press("Escape")
            name = f"{rec['portal']}__{slug(rec['url'])}"
            try:
                facts = capture(page, rec, name)
            except Exception as e:  # noqa: BLE001
                facts = {"error": str(e)}
            results[key] = {**rec, **facts}
            print(f"{rec['portal']:8} {rec['url']:55} {facts.get('title', '')[:40]!s:40} {'ERR ' + facts['error'][:60] if 'error' in facts else ''}")

        # a record detail page for the guide's "open a record" section: first client
        login(page, *ACCOUNTS["admin"])
        page.goto(f"{BASE}/clients", wait_until="load")
        href = page.evaluate("() => { const a = document.querySelector('main table tbody a[href^=\"/clients/\"]'); return a ? a.getAttribute('href') : null; }")
        if href:
            page.goto(BASE + href, wait_until="load")
            page.wait_for_timeout(300)
            page.screenshot(path=str(SHOTS / "admin__client_detail.png"))
            results["admin:client_detail"] = {"portal": "admin", "url": href, "kind": "extra", "label": "Client record", **page.evaluate(EXTRACT_JS), "shot": "admin__client_detail.png"}
        page.goto(f"{BASE}/hr/employees", wait_until="load")
        href = page.evaluate("() => { const a = document.querySelector('main table tbody a[href^=\"/hr/employees/\"]'); return a ? a.getAttribute('href') : null; }")
        if href:
            page.goto(BASE + href, wait_until="load")
            page.wait_for_timeout(300)
            page.screenshot(path=str(SHOTS / "admin__employee_detail.png"))
            results["admin:employee_detail"] = {"portal": "admin", "url": href, "kind": "extra", "label": "Employee record", **page.evaluate(EXTRACT_JS), "shot": "admin__employee_detail.png"}

        # icons: every Lucide name used by the navigation, plus the header ones
        names = set()
        for src in (nav.ADMIN_NAV, nav.TEACHER_NAV, nav.CLIENT_NAV, nav.STUDENT_NAV):
            for sec in src:
                names.add(sec["icon"])
                for g in sec.get("groups", []):
                    names.add(g["icon"])
                    for it in g["items"]:
                        names.add(it["icon"])
                for it in sec.get("items", []):
                    names.add(it["icon"])
        names |= {"menu", "bell", "home", "circle-user", "search", "arrow-left", "plus", "pencil", "trash-2", "printer",
                  "download", "filter", "log-out", "check", "x", "eye", "chevron-right", "save", "upload", "refresh-cw",
                  "info", "lightbulb", "alert-triangle", "mouse-pointer-click", "keyboard", "shield", "file-text",
                  "circle-check", "layout-grid", "external-link", "moon", "sun"}
        html = "<html><body style='margin:0;background:white'>" + "".join(
            f"<div id='i-{n}' style='width:64px;height:64px;display:inline-flex;align-items:center;justify-content:center;color:#1d6fcf'>"
            f"<i data-lucide='{n}' style='width:48px;height:48px'></i></div>" for n in sorted(names)
        ) + "<script src='/static/vendor/lucide.min.js'></script><script>lucide.createIcons();</script></body></html>"
        page.goto(f"{BASE}/login", wait_until="load")
        page.set_content(html.replace("/static/", f"{BASE}/static/"), wait_until="load")
        page.wait_for_timeout(500)
        for n in sorted(names):
            el = page.query_selector(f"#i-{n}")
            if el:
                el.screenshot(path=str(ICONS / f"{n}.png"), omit_background=True)
        browser.close()

    (OUT / "pages.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    errs = [k for k, v in results.items() if "error" in v]
    print(f"\n{len(results)} pages captured, {len(errs)} errors, {len(list(ICONS.glob('*.png')))} icons")
    for k in errs:
        print("  ERR", k, results[k]["error"][:100])


if __name__ == "__main__":
    main()
