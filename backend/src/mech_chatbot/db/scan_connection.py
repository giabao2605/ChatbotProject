"""GD6 SQL transactions share the existing request deadline and cancellation."""
from contextlib import contextmanager
from dataclasses import dataclass
from time import monotonic

from sqlalchemy import text


def checkpoint():
    from mech_chatbot.rag.execution import current_request_checkpoint
    check = current_request_checkpoint()
    if check is not None:
        check('comprehensive_sql')


@dataclass(frozen=True)
class _ScanConnection:
    connection: object
    driver: object
    previous_timeout: int | None
    previous_lock: int | None

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, *args, **kwargs):
        from mech_chatbot.rag.execution import current_request_budget, RequestDeadlineExceeded
        checkpoint()
        budget = current_request_budget()
        if budget is not None and self.driver is not None:
            remaining = budget.deadline_monotonic - monotonic()
            if remaining < 1:
                raise RequestDeadlineExceeded('Insufficient SQL time within request deadline')
            self.driver.timeout = min(self.previous_timeout or int(remaining), int(remaining))
            lock = int(remaining * 1000)
            if self.previous_lock is not None and self.previous_lock >= 0:
                lock = min(lock, self.previous_lock)
            self.connection.execute(text(f'SET LOCK_TIMEOUT {lock}'))
        return self.connection.execute(*args, **kwargs)


@contextmanager
def scan_connection(engine, *, serializable=False):
    checkpoint()
    with engine.connect() as connection:
        if serializable:
            connection.execution_options(isolation_level='SERIALIZABLE')
        driver, previous_timeout, previous_lock = None, None, None
        try:
            with connection.begin():
                from mech_chatbot.rag.execution import current_request_budget
                if current_request_budget() is not None and connection.dialect.name == 'mssql':
                    driver = connection.connection.driver_connection
                    previous_timeout = driver.timeout
                    previous_lock = int(connection.execute(text('SELECT @@LOCK_TIMEOUT')).scalar_one())
                yield _ScanConnection(connection, driver, previous_timeout, previous_lock)
                checkpoint()
        finally:
            if driver is not None and not connection.invalidated:
                driver.timeout = previous_timeout
            if previous_lock is not None and not connection.invalidated:
                try:
                    connection.execute(text(f'SET LOCK_TIMEOUT {previous_lock}'))
                except BaseException:
                    connection.invalidate()
                    raise
