"""One request-local, serializable SQL read for fixed evidence templates."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import monotonic
from typing import Any, Callable
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from .engine import resolve_engine

_CURRENT_READ: ContextVar[Any] = ContextVar('evidence_snapshot_connection', default=None)


class EvidenceSnapshotLost(RuntimeError):
    """A failed SQL transaction cannot supply further evidence for this request."""


def evidence_snapshot_active():
    return _CURRENT_READ.get() is not None


@dataclass(frozen=True, slots=True)
class _ReadConnection:
    connection: Any
    checkpoint: Callable[[str], None]
    deadline: float
    lock_timeout: int | None
    db_engine: Any

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, *args, **kwargs):
        self.checkpoint('evidence_read')
        try:
            if self.lock_timeout is not None:
                remaining = max(0, int((self.deadline - monotonic()) * 1000))
                timeout = min(remaining, self.lock_timeout) if self.lock_timeout >= 0 else remaining
                self.connection.execute(text(f'SET LOCK_TIMEOUT {timeout}'))
            return self.connection.execute(*args, **kwargs)
        except SQLAlchemyError:
            if self.connection.invalidated:
                raise EvidenceSnapshotLost('Evidence snapshot unavailable') from None
            if self.connection.dialect.name == 'mssql':
                try:
                    transaction_state = self.connection.execute(text('SELECT XACT_STATE()')).scalar_one()
                except SQLAlchemyError:
                    self.connection.invalidate()
                    raise EvidenceSnapshotLost('Evidence snapshot unavailable') from None
                if transaction_state != 1:
                    raise EvidenceSnapshotLost('Evidence snapshot unavailable') from None
            raise


@contextmanager
def evidence_read_connection(db_engine=None):
    current = _CURRENT_READ.get()
    if current is not None:
        if db_engine is not None and resolve_engine(db_engine) is not current.db_engine:
            raise EvidenceSnapshotLost('Evidence database changed during request')
        yield current
    else:
        with resolve_engine(db_engine).connect() as connection:
            yield connection


@contextmanager
def evidence_read_snapshot(checkpoint, deadline, *, db_engine=None):
    checkpoint('evidence_snapshot')
    current = _CURRENT_READ.get()
    if current is not None:
        if db_engine is not None and resolve_engine(db_engine) is not current.db_engine:
            raise EvidenceSnapshotLost('Evidence database changed during request')
        yield
        checkpoint('evidence_snapshot')
        return
    selected_engine = resolve_engine(db_engine)
    with selected_engine.connect() as connection:
        if connection.dialect.name not in {'mssql', 'sqlite'}:
            raise ValueError('Unsupported evidence database')
        connection.execution_options(isolation_level='SERIALIZABLE')
        previous_timeout = None
        try:
            with connection.begin():
                if connection.dialect.name == 'sqlite':
                    # SQLite's legacy driver does not begin a transaction for SELECT.
                    connection.execute(text('BEGIN'), {})
                else:
                    previous_timeout = int(connection.execute(text('SELECT @@LOCK_TIMEOUT'), {}).scalar_one())
                token = _CURRENT_READ.set(_ReadConnection(connection, checkpoint, deadline, previous_timeout, selected_engine))
                try:
                    yield
                    checkpoint('evidence_snapshot')
                finally:
                    _CURRENT_READ.reset(token)
        finally:
            if previous_timeout is not None and not connection.invalidated:
                try:
                    connection.execute(text(f'SET LOCK_TIMEOUT {previous_timeout}'))
                except BaseException:
                    connection.invalidate()
                    raise
