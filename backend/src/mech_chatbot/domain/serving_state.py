"""Domain-neutral fail-closed serving-state policy."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable


_BAD_EFFECTIVE_STATES = {"expired", "superseded", "draft"}
_HISTORY_LIFECYCLE_STATES = {"published", "archived", "superseded", "retired"}


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


def is_currently_servable(
    metadata: dict[str, Any] | None,
    *,
    today: date | None = None,
    require_current: bool = False,
) -> bool:
    """Validate the serving invariant after retrieval or before byte access."""

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


def is_history_servable(metadata: dict[str, Any] | None, *, today: date | None = None) -> bool:
    """Return whether an old revision is safe to expose to an explicit reader.

    Historical access is limited to source revisions that were actually
    published and approved.  Draft, rejected, revoked, deleting and malformed
    lifecycle rows remain denied even when a caller has the history role.
    """
    meta = metadata or {}
    current_day = today or date.today()
    if _token(meta.get("effective_status")) in {"draft", "revoked"}:
        return False
    if any(
        value not in (None, "") and _date(value) is date.min
        for value in (meta.get("effective_date"), meta.get("expiry_date"))
    ):
        return False
    effective_date = _date(meta.get("effective_date"))
    if effective_date is not None and effective_date > current_day:
        return False
    expired = _token(meta.get("effective_status")) == "expired"
    expiry = _date(meta.get("expiry_date"))
    expired = expired or (expiry is not None and expiry < current_day)
    return (
        _token(meta.get("publication_state")) == "published"
        and _token(meta.get("lifecycle_status")) in _HISTORY_LIFECYCLE_STATES
        and _token(meta.get("review_status")) == "approved"
        and (not _flag(meta.get("is_current")) or expired)
    )


def filter_currently_servable(
    documents: Iterable[Any],
    *,
    today: date | None = None,
    allow_history: bool = False,
) -> list[Any]:
    return [
        document
        for document in (documents or [])
        if is_currently_servable(getattr(document, "metadata", None), today=today)
        or (allow_history and is_history_servable(getattr(document, "metadata", None), today=today))
    ]


__all__ = ["filter_currently_servable", "is_currently_servable", "is_history_servable"]
