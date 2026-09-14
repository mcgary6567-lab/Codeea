"""The single way the rest of the platform reads a configurable value list.

Every list the college configures on Configuration -> Lookups (designations, leaving reasons, allowance
types, payment modes, ...) is stored as a :class:`~app.models.config_erp.Lookup` with a stable ``code`` and a
set of :class:`~app.models.config_erp.LookupValue` rows. Callers never query those tables directly; they ask
for a code and get back options:

    from app.services import lookups
    for opt in lookups.values(db, "hr_designation"):
        ...
    ui.select("designation", "Designation", lookups.labels(db, "hr_designation", fallback=["Teacher Remote"]))
    fine = lookups.amount_for(db, "hr_allowance_type", "DA", default=0)

Two properties matter for a configuration screen that real staff edit:

* **A missing lookup never breaks a page.** Every reader may pass ``fallback=`` — a list of plain strings or
  ``(value, label)`` pairs — used when the lookup does not exist, is inactive, or has no active values.
* **Reads are cached** (the designation list is read on every employee form) and the cache is dropped by
  :func:`invalidate` whenever a lookup or one of its values is saved. The configuration router calls it on
  every mutation.

Options are returned as :class:`Option` value objects rather than ORM rows, so a cached list is safe to hand
to a different request/session. The attribute names match ``LookupValue`` (``value``, ``label``,
``label_urdu``, ``amount``, ``sort_no``, ``extra``), so templates read either the same way.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence, Union

from sqlalchemy.orm import Session

from app.models.config_erp import Lookup, LookupValue

FallbackItem = Union[str, tuple, list]


@dataclass(frozen=True)
class Option:
    """One active value of a lookup, detached from the database session."""
    value: str
    label: str
    label_urdu: Optional[str] = None
    amount: Optional[float] = None
    sort_no: int = 0
    extra: dict = field(default_factory=dict)

    def __str__(self) -> str:  # so `{{ opt }}` prints something sensible
        return self.label

    def as_tuple(self) -> tuple[str, str]:
        return self.value, self.label


# --------------------------------------------------------------------------- cache
_CACHE: dict[str, list[Option]] = {}


def invalidate(code: Optional[str] = None) -> None:
    """Drop the cached values for one lookup, or for all of them when ``code`` is None."""
    if code is None:
        _CACHE.clear()
    else:
        _CACHE.pop(code, None)


def cache_size() -> int:
    """Number of lookups currently cached (used by the configuration screen and the tests)."""
    return len(_CACHE)


# --------------------------------------------------------------------------- helpers
def _from_fallback(items: Optional[Sequence[FallbackItem]]) -> list[Option]:
    out: list[Option] = []
    for i, item in enumerate(items or []):
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            out.append(Option(value=str(item[0]), label=str(item[1]), sort_no=i))
        else:
            out.append(Option(value=str(item), label=str(item), sort_no=i))
    return out


def _load(db: Session, code: str) -> list[Option]:
    lookup = db.query(Lookup).filter(Lookup.code == code, Lookup.status == "active").first()
    if not lookup:
        return []
    rows = (db.query(LookupValue)
            .filter(LookupValue.lookup_id == lookup.id, LookupValue.status == "active")
            .order_by(LookupValue.sort_no, LookupValue.id).all())
    return [Option(value=r.value, label=r.label or r.value, label_urdu=r.label_urdu,
                   amount=float(r.amount) if r.amount is not None else None,
                   sort_no=r.sort_no or 0, extra=dict(r.extra or {})) for r in rows]


# --------------------------------------------------------------------------- public API
def values(db: Session, code: str, fallback: Optional[Sequence[FallbackItem]] = None,
           use_cache: bool = True) -> list[Option]:
    """Active options of the lookup ``code``, in sort order.

    Returns ``fallback`` (as options) when the lookup is missing, inactive or empty, so a caller can keep
    working before the lookup has been configured.
    """
    if use_cache and code in _CACHE:
        cached = _CACHE[code]
        return list(cached) if cached else _from_fallback(fallback)
    try:
        loaded = _load(db, code)
    except Exception:  # a half-migrated database must not take a page down
        return _from_fallback(fallback)
    if use_cache:
        _CACHE[code] = loaded
    return list(loaded) if loaded else _from_fallback(fallback)


def labels(db: Session, code: str, fallback: Optional[Sequence[FallbackItem]] = None) -> list[tuple[str, str]]:
    """``[(value, label), ...]`` ready to hand straight to ``ui.select``."""
    return [o.as_tuple() for o in values(db, code, fallback)]


def label_for(db: Session, code: str, value: str, default: Optional[str] = None) -> str:
    """The display label of one value (falls back to the value itself)."""
    for o in values(db, code):
        if o.value == value:
            return o.label
    return default if default is not None else str(value or "")


def amount_for(db: Session, code: str, value: str, default: float = 0.0) -> float:
    """The amount configured against one value (a fine, a bonus, an allowance), or ``default``."""
    for o in values(db, code):
        if o.value == value and o.amount is not None:
            return float(o.amount)
    return float(default)


def option_values(db: Session, code: str, fallback: Optional[Sequence[FallbackItem]] = None) -> list[str]:
    """Just the stored values, for validating a submitted form field."""
    return [o.value for o in values(db, code, fallback)]


def is_valid(db: Session, code: str, value: str, fallback: Optional[Sequence[FallbackItem]] = None) -> bool:
    return value in option_values(db, code, fallback)


def codes(db: Session) -> list[str]:
    """Every active lookup code, for a configuration screen or a health check."""
    return [c for (c,) in db.query(Lookup.code).filter(Lookup.status == "active").order_by(Lookup.code).all()]


def bulk(db: Session, wanted: Iterable[str]) -> dict[str, list[Option]]:
    """Load several lookups at once (one dict per code) — handy for a form with many selects."""
    return {code: values(db, code) for code in wanted}
