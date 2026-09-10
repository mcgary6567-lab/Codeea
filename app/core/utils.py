"""Shared helpers: code generation, pagination, flash messages, redirects, parsing."""
from __future__ import annotations

import base64
import json
import math
from datetime import date, datetime, timedelta
from typing import Any, Optional, Type
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Query, Session

FLASH_COOKIE = "oqc_flash"


# ----------------------------------------------------------------------------- codes
def next_code(db: Session, model: Type, field: str, prefix: str, width: int = 5) -> str:
    """Generate sequential codes like C-00001, S-00042. Uses max(id)+1 (fine for single-writer local use)."""
    max_id = db.query(func.max(model.id)).scalar() or 0
    n = max_id + 1
    while True:
        code = f"{prefix}{n:0{width}d}"
        if not db.query(model).filter(getattr(model, field) == code).first():
            return code
        n += 1


# ----------------------------------------------------------------------------- pagination
class Page:
    def __init__(self, items: list, total: int, page: int, per_page: int):
        self.items = items
        self.total = total
        self.page = page
        self.per_page = per_page
        self.pages = max(1, math.ceil(total / per_page)) if per_page else 1

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def start(self) -> int:
        return 0 if self.total == 0 else (self.page - 1) * self.per_page + 1

    @property
    def end(self) -> int:
        return min(self.total, self.page * self.per_page)

    def page_numbers(self) -> list[int]:
        lo, hi = max(1, self.page - 2), min(self.pages, self.page + 2)
        return list(range(lo, hi + 1))

    def __iter__(self):
        return iter(self.items)

    def __len__(self):
        return len(self.items)


def paginate(query: Query, page: int = 1, per_page: int = 25) -> Page:
    page = max(1, int(page or 1))
    per_page = max(1, min(200, int(per_page or 25)))
    total = query.order_by(None).count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    return Page(items, total, page, per_page)


# ----------------------------------------------------------------------------- flash + redirects
def _encode_flash(messages: list[dict]) -> str:
    return base64.urlsafe_b64encode(json.dumps(messages).encode()).decode()


def _decode_flash(raw: Optional[str]) -> list[dict]:
    if not raw:
        return []
    try:
        return json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
    except Exception:
        return []


def redirect(url: str, flash: Optional[str] = None, level: str = "success", status_code: int = 303) -> RedirectResponse:
    resp = RedirectResponse(url=url, status_code=status_code)
    if flash:
        resp.set_cookie(FLASH_COOKIE, _encode_flash([{"level": level, "message": flash}]), max_age=30, httponly=True, samesite="lax")
    return resp


def pop_flash(request: Request) -> list[dict]:
    return _decode_flash(request.cookies.get(FLASH_COOKIE))


def back_url(request: Request, default: str = "/") -> str:
    ref = request.headers.get("referer")
    return ref if ref else default


def login_url(request: Request) -> str:
    return "/login?next=" + quote(str(request.url.path))


# ----------------------------------------------------------------------------- parsing
def parse_date(value: Optional[str], default: Optional[date] = None) -> Optional[date]:
    if not value:
        return default
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return default


def parse_datetime(value: Optional[str], default: Optional[datetime] = None) -> Optional[datetime]:
    if not value:
        return default
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return default


def parse_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def parse_bool(value: Any) -> bool:
    return str(value).lower() in ("1", "true", "on", "yes")


def month_key(d: Optional[date] = None) -> str:
    d = d or date.today()
    return d.strftime("%Y-%m")


def month_bounds(period: str) -> tuple[date, date]:
    y, m = [int(x) for x in period.split("-")]
    start = date(y, m, 1)
    end = date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)
    return start, end


def money(value: Any, currency: str = "") -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0.0
    s = f"{v:,.2f}"
    return f"{currency} {s}".strip()


def humanize_delta(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    diff = datetime.utcnow() - dt
    secs = int(diff.total_seconds())
    if secs < 0:
        secs = -secs
        suffix = "from now"
    else:
        suffix = "ago"
    if secs < 60:
        return f"{secs}s {suffix}"
    if secs < 3600:
        return f"{secs // 60}m {suffix}"
    if secs < 86400:
        return f"{secs // 3600}h {suffix}"
    return f"{secs // 86400}d {suffix}"


def pct(part: float, whole: float, digits: int = 1) -> float:
    return round(100.0 * part / whole, digits) if whole else 0.0
