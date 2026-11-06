"""P0 #5 — Test logic rate-limit + fallback in-process (engine=None).

Phan SQL backend can DB that -> nam o tests/integration/test_rate_limit_db.py
(chi chay khi RUN_DB_TESTS=1). O day chi test:
- ham THUAN: is_over_threshold, count_within_window
- duong fallback in-process qua API cong khai voi engine=None
"""
import pytest

pytestmark = pytest.mark.security

from mech_chatbot.auth import rate_limit as rl  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_mem():
    rl._reset_mem()
    yield
    rl._reset_mem()


class TestPureHelpers:
    def test_is_over_threshold(self):
        assert rl.is_over_threshold(5, max_failures=5) is True
        assert rl.is_over_threshold(6, max_failures=5) is True
        assert rl.is_over_threshold(4, max_failures=5) is False

    def test_count_within_window(self):
        now = 1000.0
        # 3 moc trong cua so 600s, 1 moc qua cu (cach 700s)
        ts = [now - 10, now - 100, now - 500, now - 700]
        assert rl.count_within_window(ts, now, window_seconds=600) == 3

    def test_count_empty(self):
        assert rl.count_within_window([], 1000.0, 600) == 0


class TestInProcessFallback:
    def test_not_limited_initially(self):
        assert rl.is_rate_limited(None, "alice") is False

    def test_locks_after_max_failures(self):
        for _ in range(rl.MAX_FAILURES):
            rl.record_failure(None, "bob")
        assert rl.is_rate_limited(None, "bob") is True

    def test_below_threshold_not_locked(self):
        for _ in range(rl.MAX_FAILURES - 1):
            rl.record_failure(None, "carol")
        assert rl.is_rate_limited(None, "carol") is False

    def test_clear_resets(self):
        for _ in range(rl.MAX_FAILURES):
            rl.record_failure(None, "dave")
        assert rl.is_rate_limited(None, "dave") is True
        rl.clear_failures(None, "dave")
        assert rl.is_rate_limited(None, "dave") is False

    def test_users_are_independent(self):
        for _ in range(rl.MAX_FAILURES):
            rl.record_failure(None, "eve")
        assert rl.is_rate_limited(None, "eve") is True
        # User khac khong bi anh huong
        assert rl.is_rate_limited(None, "frank") is False
