"""Central fail-closed serving-state checks shared by all read surfaces."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable


_BAD_EFFECTIVE_STATES = {"expired", "superseded", "draft"}


def _token(value: Any) -> str:
    return str(value or "").strip().lower()


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _token(value) in {"1", "true", "yes", "on"}


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).strip()[:10]).date()
    except (TypeError, ValueError):
        # Invalid lifecycle metadata must never become serving content.
        return date.min


def is_currently_servable(metadata: dict[str, Any] | None, *, today: date | None = None,
                          require_current: bool = False) -> bool:
    """Validate the invariant again after retrieval or before byte access."""
    meta = metadata or {}
    current_day = today or date.today()
    if not _flag(meta.get("servable")):
        return False
    if _token(meta.get("publication_state")) != "published":
        return False
    if _token(meta.get("lifecycle_status")) != "published":
        return False
    if _token(meta.get("review_status")) != "approved":
        return False
    if require_current and not _flag(meta.get("is_current")):
        return False
    if _token(meta.get("effective_status")) in _BAD_EFFECTIVE_STATES:
        return False
    effective = _date(meta.get("effective_date"))
    expiry = _date(meta.get("expiry_date"))
    if effective is not None and effective > current_day:
        return False
    if expiry is not None and expiry < current_day:
        return False
    return True


def filter_currently_servable(documents: Iterable[Any], *, today: date | None = None) -> list[Any]:
    return [
        doc for doc in (documents or [])
        if is_currently_servable(getattr(doc, "metadata", None), today=today)
    ]

