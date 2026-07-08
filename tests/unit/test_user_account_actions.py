import pytest

pytestmark = pytest.mark.unit

user_repo = pytest.importorskip("mech_chatbot.db.repositories.ui_queries")


class _Result:
    def __init__(self, one=None, rowcount=0):
        self._one = one
        self.rowcount = rowcount

    def fetchone(self):
        return self._one


class _Conn:
    def __init__(self):
        self.calls = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        params = dict(params or {})
        self.calls.append((sql, params))
        if "SELECT UserID, Username, DisplayName, IsActive" in sql:
            return _Result(one=(7, "alice", "Alice", True))
        if "RoleName = 'admin'" in sql:
            return _Result(one=None)
        if "DELETE FROM Users" in sql:
            return _Result(rowcount=1)
        return _Result(rowcount=1)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Engine:
    def __init__(self, conn):
        self.conn = conn

    def begin(self):
        return self.conn


def test_set_user_active_status_blocks_self_disable(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(user_repo, "engine", _Engine(conn))

    res = user_repo.set_user_active_status(7, False, actor_username="alice", actor_id=7)

    assert res["ok"] is False
    assert "đang đăng nhập" in res["message"]
    assert not any("UPDATE Users SET IsActive" in sql for sql, _ in conn.calls)


def test_delete_user_account_cleans_permission_rows(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(user_repo, "engine", _Engine(conn))

    res = user_repo.delete_user_account(7, actor_username="admin", actor_id=1)

    assert res["ok"] is True
    joined_sql = "\n".join(sql for sql, _ in conn.calls)
    assert "DELETE FROM UserRoles" in joined_sql
    assert "DELETE FROM UserDepartments" in joined_sql
    assert "DELETE FROM UserSites" in joined_sql
    assert "DELETE FROM UserSecurityClearance" in joined_sql
    assert "DELETE FROM Users" in joined_sql
    assert "UPDATE dbo.AccessRequests" in joined_sql
