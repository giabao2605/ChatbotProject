from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from mech_chatbot.rag import execution


def test_expired_scan_transaction_rolls_back_progress(monkeypatch):
    from mech_chatbot.db.scan_connection import scan_connection
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE progress (value INTEGER)'))
    expired = False
    def checkpoint(stage):
        if expired:
            raise execution.RequestDeadlineExceeded(stage)
    monkeypatch.setattr(execution, 'current_request_checkpoint', lambda:checkpoint)
    with pytest.raises(execution.RequestDeadlineExceeded):
        with scan_connection(engine) as conn:
            conn.execute(text('INSERT INTO progress VALUES (1)'))
            expired = True
    with engine.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM progress')).scalar() == 0


def test_sql_timeout_never_increases_existing_limit_and_is_restored(monkeypatch):
    from mech_chatbot.db.scan_connection import scan_connection
    monkeypatch.setattr(execution, 'current_request_checkpoint', lambda:None)
    monkeypatch.setattr(execution, 'current_request_budget', lambda:SimpleNamespace(deadline_monotonic=105.0))
    monkeypatch.setattr('mech_chatbot.db.scan_connection.monotonic', lambda:100.0)
    driver = SimpleNamespace(timeout=2)
    statements = []
    class Connection:
        dialect = SimpleNamespace(name='mssql')
        connection = SimpleNamespace(driver_connection=driver)
        invalidated = False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def begin(self): return self
        def execute(self, sql, *args, **kwargs):
            statements.append(str(sql))
            assert driver.timeout <= 2
            return SimpleNamespace(scalar_one=lambda:1200)
        def invalidate(self): self.invalidated = True
    with scan_connection(SimpleNamespace(connect=lambda:Connection())) as conn:
        conn.execute(text('SELECT 42'))
    assert driver.timeout == 2
    assert 'SET LOCK_TIMEOUT 1200' in statements
