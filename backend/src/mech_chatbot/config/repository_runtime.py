"""Request-local repository dependencies bound by process composition roots.

The compatibility repositories still contain direct ``engine.connect()`` call
sites that are removed in Phase 6.  Phase 5 binds their concrete SQL and
Qdrant dependencies to the current request instead of restoring a mutable
process singleton.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator

from mech_chatbot.config.settings import RepositoryPolicySettings


_DEFAULT_POLICY = RepositoryPolicySettings(
    strict_site_filter=True,
    allow_admin_metadata_override=False,
    allow_admin_approval_override=False,
    publication_max_attempts=5,
)
@dataclass(frozen=True, slots=True)
class RepositoryRuntime:
    policy: RepositoryPolicySettings = _DEFAULT_POLICY
    db_engine: Any = None
    qdrant_client: Any = None
    qdrant_collection: str | None = None


_DEFAULT_RUNTIME = RepositoryRuntime()
_CURRENT_RUNTIME: ContextVar[RepositoryRuntime] = ContextVar(
    "mech_chatbot_repository_runtime",
    default=_DEFAULT_RUNTIME,
)


def current_repository_policy() -> RepositoryPolicySettings:
    return _CURRENT_RUNTIME.get().policy


def current_repository_engine() -> Any:
    return _CURRENT_RUNTIME.get().db_engine


def current_qdrant_runtime() -> tuple[Any, str | None]:
    runtime = _CURRENT_RUNTIME.get()
    return runtime.qdrant_client, runtime.qdrant_collection


@contextmanager
def bind_repository_runtime(
    *,
    policy: RepositoryPolicySettings | None = None,
    db_engine: Any = None,
    qdrant_client: Any = None,
    qdrant_collection: str | None = None,
) -> Iterator[RepositoryRuntime]:
    current = _CURRENT_RUNTIME.get()
    runtime = RepositoryRuntime(
        policy=policy or current.policy,
        db_engine=db_engine if db_engine is not None else current.db_engine,
        qdrant_client=(
            qdrant_client
            if qdrant_client is not None
            else current.qdrant_client
        ),
        qdrant_collection=(
            qdrant_collection
            if qdrant_collection is not None
            else current.qdrant_collection
        ),
    )
    token = _CURRENT_RUNTIME.set(runtime)
    try:
        yield runtime
    finally:
        _CURRENT_RUNTIME.reset(token)


@contextmanager
def bind_repository_policy(
    policy: RepositoryPolicySettings,
) -> Iterator[RepositoryPolicySettings]:
    with bind_repository_runtime(policy=policy):
        yield policy


__all__ = [
    "RepositoryRuntime",
    "bind_repository_policy",
    "bind_repository_runtime",
    "current_qdrant_runtime",
    "current_repository_engine",
    "current_repository_policy",
]
