"""Explicit composition boundary shared by operational CLI scripts."""

from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Iterator, TypeVar

from mech_chatbot.adapters.qdrant_runtime import build_qdrant_admin_runtime
from mech_chatbot.config.repository_runtime import (
    bind_repository_runtime,
    current_qdrant_runtime,
    current_repository_engine,
)
from mech_chatbot.config.settings import (
    QdrantSettings,
    SqlSettings,
    load_settings,
)
from mech_chatbot.db.engine import build_database_runtime


Result = TypeVar("Result")


def _repository_runtime_is_bound(*, include_qdrant: bool) -> bool:
    bound_engine = current_repository_engine()
    bound_qdrant, _bound_collection = current_qdrant_runtime()
    return bound_engine is not None and (
        not include_qdrant or bound_qdrant is not None
    )


@contextmanager
def configured_repository_runtime(
    settings: Any,
    *,
    include_qdrant: bool,
) -> Iterator[None]:
    """Bind CLI-owned dependencies from one explicit settings snapshot."""

    if _repository_runtime_is_bound(include_qdrant=include_qdrant):
        yield
        return

    database_runtime = build_database_runtime(
        SqlSettings.from_settings(settings)
    )
    qdrant_runtime = None
    try:
        if include_qdrant:
            qdrant_runtime = build_qdrant_admin_runtime(
                QdrantSettings.from_settings(settings)
            )
        with bind_repository_runtime(
            db_engine=database_runtime.engine,
            qdrant_client=getattr(qdrant_runtime, "client", None),
            qdrant_collection=getattr(
                qdrant_runtime,
                "collection_name",
                None,
            ),
        ):
            yield
    finally:
        if qdrant_runtime is not None:
            qdrant_runtime.close()
        database_runtime.close()


def with_configured_repository_runtime(
    *,
    include_qdrant: bool,
) -> Callable[[Callable[..., Result]], Callable[..., Result]]:
    """Build, bind and close CLI-owned SQL/Qdrant dependencies."""

    def decorate(function: Callable[..., Result]) -> Callable[..., Result]:
        @wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> Result:
            if _repository_runtime_is_bound(include_qdrant=include_qdrant):
                return function(*args, **kwargs)
            settings = load_settings()
            with configured_repository_runtime(
                settings,
                include_qdrant=include_qdrant,
            ):
                return function(*args, **kwargs)

        return wrapped

    return decorate


__all__ = [
    "configured_repository_runtime",
    "with_configured_repository_runtime",
]
