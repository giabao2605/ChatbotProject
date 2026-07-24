"""SQL engine construction owned by process composition roots.

Importing this module is side-effect free: it does not read environment
variables, load dotenv files, or create a SQLAlchemy engine.  A process parses
``SqlSettings`` once and explicitly passes that projection to
``create_db_engine``.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any
import urllib.parse

from sqlalchemy import create_engine
from mech_chatbot.config.repository_runtime import (
    bind_repository_runtime,
    current_repository_engine,
)

if TYPE_CHECKING:
    from mech_chatbot.config.settings import SqlSettings

logger = logging.getLogger("MechChatbot")


class DatabaseConfigurationError(ValueError):
    """The SQL settings cannot describe a supported authentication mode."""


class DatabaseEngineError(RuntimeError):
    """SQLAlchemy could not construct the configured database engine."""


def build_conn_str(settings: SqlSettings) -> str:
    """Build an ODBC connection string from one immutable settings snapshot."""
    if settings.username and settings.password:
        return (
            f"DRIVER={settings.driver};"
            f"SERVER={settings.server};"
            f"DATABASE={settings.database};"
            f"UID={settings.username};"
            f"PWD={settings.password};"
            "TrustServerCertificate=yes;"
        )
    if not settings.trusted_connection:
        raise DatabaseConfigurationError(
            "SQL authentication requires both username and password when "
            "trusted connection is disabled."
        )
    return (
        f"DRIVER={settings.driver};"
        f"SERVER={settings.server};"
        f"DATABASE={settings.database};"
        "Trusted_Connection=yes;"
    )


def create_db_engine(settings: SqlSettings) -> Any:
    """Construct one SQLAlchemy engine without retaining process-global state."""
    params = urllib.parse.quote_plus(build_conn_str(settings))
    try:
        created_engine = create_engine(
            f"mssql+pyodbc:///?odbc_connect={params}",
            pool_size=20,
            max_overflow=30,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
    except Exception as error:
        logger.error(
            "Khong the khoi tao SQLAlchemy Engine (provider=%s).",
            type(error).__name__,
        )
        raise DatabaseEngineError(
            "Unable to initialize the SQL database engine."
        ) from None
    logger.info("Da khoi tao SQLAlchemy Engine thanh cong.")
    return created_engine


@dataclass(frozen=True, slots=True)
class DatabaseRuntime:
    """Immutable lifecycle owner for one process-specific SQL engine."""

    engine: Any

    def close(self) -> None:
        self.engine.dispose()


def build_database_runtime(settings: SqlSettings) -> DatabaseRuntime:
    """Create the immutable SQL dependency bundle for one process."""
    return DatabaseRuntime(engine=create_db_engine(settings))


class _ContextBoundEngine:
    """Compatibility proxy for legacy direct repository call sites.

    It owns no connection and stores no mutable process state.  Calls are
    delegated to the engine bound by the current app request composition.
    """

    @staticmethod
    def _target() -> Any:
        selected = current_repository_engine()
        if selected is None:
            raise RuntimeError(
                "SQLAlchemy Engine chua san sang. Kiem tra connection string / "
                "ODBC driver / SQL Server."
            )
        return selected

    def connect(self, *args: Any, **kwargs: Any) -> Any:
        return self._target().connect(*args, **kwargs)

    def begin(self, *args: Any, **kwargs: Any) -> Any:
        return self._target().begin(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target(), name)


# Phase 6 removes this proxy as the remaining repository shims gain typed
# constructor dependencies.  The proxy is intentionally stateless and cannot
# create an engine during import.
engine = _ContextBoundEngine()


@contextmanager
def bind_repository_engine(selected_engine: Any):
    """Bind a SQL engine for one request, task or focused compatibility test."""

    with bind_repository_runtime(db_engine=selected_engine):
        yield selected_engine


def _ensure_engine(candidate: Any | None = None) -> None:
    """Keep the legacy failure message while accepting explicit dependencies."""
    selected = current_repository_engine() if candidate is None else candidate
    if isinstance(selected, _ContextBoundEngine):
        selected = current_repository_engine()
    if selected is None:
        raise RuntimeError(
            "SQLAlchemy Engine chua san sang. Kiem tra connection string / "
            "ODBC driver / SQL Server."
        )


def resolve_engine(candidate: Any | None = None) -> Any:
    """Return an explicitly supplied engine, with legacy fallback for shims."""
    selected = current_repository_engine() if candidate is None else candidate
    if isinstance(selected, _ContextBoundEngine):
        selected = current_repository_engine()
    _ensure_engine(selected)
    return selected


# Read-only compatibility defaults for unknown external imports.  They are not
# runtime configuration and are removed with the repository shim in Phase 6.
SQL_SERVER = r"localhost\SQLEXPRESS"
SQL_DATABASE = "Mech_Chatbot_DB"
SQL_DRIVER = "ODBC Driver 17 for SQL Server"
SQL_USERNAME = None
SQL_PASSWORD = None
SQL_TRUSTED_CONNECTION = True


__all__ = [
    "DatabaseConfigurationError",
    "DatabaseEngineError",
    "DatabaseRuntime",
    "SQL_DATABASE",
    "SQL_DRIVER",
    "SQL_PASSWORD",
    "SQL_SERVER",
    "SQL_TRUSTED_CONNECTION",
    "SQL_USERNAME",
    "_ensure_engine",
    "bind_repository_engine",
    "build_database_runtime",
    "build_conn_str",
    "create_db_engine",
    "engine",
    "current_repository_engine",
    "resolve_engine",
]
