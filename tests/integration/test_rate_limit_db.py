"""P0 #5 — Integration test rate-limit voi SQL Server THAT.

Chi chay khi RUN_DB_TESTS=1 (conftest tu dong skip neu chua bat).
Can bang LoginAttempts da tao (chay migration V0007 truoc).

Dung username ngau nhien moi lan de khong dung cham du lieu that.
"""
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.security]


@pytest.fixture
def engine():
    from mech_chatbot.db.repository import engine as _engine
    if _engine is None:
        pytest.skip("Khong co engine DB")
    return _engine


@pytest.fixture
def username(engine):
    from mech_chatbot.auth import rate_limit as rl
    name = "pytest_" + uuid.uuid4().hex[:12]
    yield name
    # Don dep sau test
    rl.clear_failures(engine, name)


def test_db_lock_after_max_failures(engine, username):
    from mech_chatbot.auth import rate_limit as rl
    assert rl.is_rate_limited(engine, username) is False
    for _ in range(rl.MAX_FAILURES):
        rl.record_failure(engine, username)
    assert rl.is_rate_limited(engine, username) is True


def test_db_clear_resets(engine, username):
    from mech_chatbot.auth import rate_limit as rl
    for _ in range(rl.MAX_FAILURES):
        rl.record_failure(engine, username)
    assert rl.is_rate_limited(engine, username) is True
    rl.clear_failures(engine, username)
    assert rl.is_rate_limited(engine, username) is False


def test_db_shared_across_connections(engine, username):
    """Ghi qua 1 ket noi, dem qua ket noi khac van thay -> chung that su."""
    from mech_chatbot.auth import rate_limit as rl
    for _ in range(rl.MAX_FAILURES):
        rl.record_failure(engine, username)
    # _sql_count mo ket noi MOI -> neu thay du nguong nghia la store dung chung
    assert rl._sql_count(engine, username, rl.WINDOW_SECONDS) >= rl.MAX_FAILURES
