"""Hide links the signed-in user cannot open.

Pages are shared between roles, and a link drawn for one role often leads somewhere another role may not go
(the family name on a teacher's student list, "Edit" for a read-only HR officer, a dashboard tile for billing).
Clicking one gave "403 · Access denied". Crawling every role on 13 Oct 2026 found about 65 kinds of them, so
instead of guarding each template by hand, every rendered page is filtered here:

* each ``<a href="/...">`` is matched against the app's GET routes, in the order the app matches them;
* the route's guards are read from its dependencies: ``require(...)`` permissions, ``require_staff``,
  ``require_ceo``, ``require_management`` and the confidential grievance guard;
* a link the user may not open is dropped when it is a button (``ui.button``) and turned into plain text
  otherwise, so names and tiles stay readable.

Checks made inside a route (a teacher limited to their own students, and so on) are not visible here; those
routes stay linked and keep answering 403 for the rows they refuse. Superusers skip the filter.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Callable, Optional

from app.core import rbac

Guard = Callable[[object], bool]

_INDEX: Optional[list[tuple[re.Pattern, tuple[Guard, ...]]]] = None
_A = re.compile(r'<a\b([^>]*?)\bhref="(/[^"]*)"([^>]*)>(.*?)</a>', re.S)
_BUTTON = "inline-flex items-center gap-1.5 rounded"  # the classes every ui.button starts with (macros.html)


def _guards(dependant) -> list[Guard]:
    out: list[Guard] = []
    for d in dependant.dependencies:
        fn = d.call
        name = getattr(fn, "__name__", "")
        if name == "_dep" and fn.__closure__:  # app.core.deps.require(*perms, any_of=...)
            cells = [c.cell_contents for c in fn.__closure__]
            perms = next((v for v in cells if isinstance(v, tuple)), ())
            any_of = next((v for v in cells if isinstance(v, bool)), False)
            if perms:
                if any_of:
                    out.append(lambda u, p=perms: any(rbac.has_permission(u, x) for x in p))
                else:
                    out.append(lambda u, p=perms: all(rbac.has_permission(u, x) for x in p))
        elif name == "require_staff":
            out.append(lambda u: getattr(u, "portal", "admin") not in ("client", "student"))
        elif name == "require_ceo":
            out.append(rbac.is_ceo)
        elif name == "require_management":
            out.append(rbac.is_management)
        elif name == "grievance_guard":
            out.append(rbac.can_read_grievances)
        out += _guards(d)
    return out


def _walk(routes, prefix: str = ""):
    from fastapi.routing import APIRoute
    for r in routes:
        if isinstance(r, APIRoute):
            yield prefix + r.path, r
        elif hasattr(r, "original_router"):  # FastAPI's wrapper for an included router
            ctx = getattr(r, "include_context", None)
            yield from _walk(r.original_router.routes, prefix + (getattr(ctx, "prefix", "") or ""))
        elif hasattr(r, "routes") and hasattr(r, "path"):
            yield from _walk(r.routes, prefix + (r.path or ""))


def _index() -> list[tuple[re.Pattern, tuple[Guard, ...]]]:
    global _INDEX
    if _INDEX is None:
        from starlette.routing import compile_path

        from app.main import app
        built = []
        for path, route in _walk(app.routes):
            if "GET" in (route.methods or ()):
                built.append((compile_path(path)[0], tuple(_guards(route.dependant))))
        _INDEX = built
    return _INDEX


@lru_cache(maxsize=4096)
def _guards_for(path: str) -> Optional[tuple[Guard, ...]]:
    for regex, guards in _index():
        if regex.match(path):
            return guards
    return None  # not an app page (static files, storage): leave it alone


_AREA = re.compile(r"^/home/([^/]+)(?:/([^/]+))?/?$")


def can_open(user, href: str) -> bool:
    path = href.split("?", 1)[0].split("#", 1)[0]
    area = _AREA.match(path)
    if area:  # an area or group home: it exists only when the user's menu has it (app/web/launchpad.py)
        from app.core.nav import group_for, section_for
        if area.group(2):
            return all(group_for(user, area.group(1), area.group(2)))
        return section_for(user, area.group(1)) is not None
    guards = _guards_for(path)
    return guards is None or all(g(user) for g in guards)


def filter_links(html: str, user) -> str:
    if user is None or getattr(user, "is_superuser", False):
        return html

    def sub(m: re.Match) -> str:
        if can_open(user, m.group(2)):
            return m.group(0)
        attrs = m.group(1) + m.group(3)
        if _BUTTON in attrs:
            return ""
        cls = re.search(r'class="([^"]*)"', attrs)
        return f'<span class="{cls.group(1) if cls else ""}">{m.group(4)}</span>'

    return _A.sub(sub, html)
