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


class _LastAdminConn(_Conn):
    def execute(self, stmt, params=None):
        result = super().execute(stmt, params)
        if "SELECT COUNT(*)" in str(stmt):
            return _Result(one=(0,))
        if "RoleName = 'admin'" in str(stmt):
            return _Result(one=(1,))
        return result


@pytest.mark.parametrize('active,add_roles,del_roles', [(False, [], []), (True, [], ['admin']),
    (True, ['admin'], ['admin']), (True, [], ['admin '])])
def test_role_update_cannot_remove_last_active_admin(monkeypatch, active, add_roles, del_roles):
    conn = _LastAdminConn()
    monkeypatch.setattr(user_repo, 'engine', _Engine(conn))

    assert user_repo.update_user_active_and_roles(7, active, add_roles, del_roles) is False
    assert not any('UPDATE Users' in sql or 'DELETE ur' in sql for sql, _ in conn.calls)


@pytest.mark.parametrize('function,args', [
    ('set_user_active_status', (7, False)), ('delete_user_account', (7,)),
    ('update_user_active_and_roles', (7, False, [], [])),
])
def test_admin_mutations_lock_before_reading_account_state(monkeypatch, function, args):
    conn = _LastAdminConn()
    conn.dialect = type('Dialect', (), {'name': 'mssql'})()
    original_execute = conn.execute

    def execute(stmt, params=None):
        if 'sp_getapplock' in str(stmt):
            conn.calls.append((str(stmt), dict(params or {})))
            return _Result(one=(0,))
        return original_execute(stmt, params)

    conn.execute = execute
    monkeypatch.setattr(user_repo, 'engine', _Engine(conn))
    getattr(user_repo, function)(*args)

    assert 'sp_getapplock' in conn.calls[0][0]
    assert "@LockOwner = 'Transaction'" in conn.calls[0][0]


def test_admin_mutation_stops_when_transaction_lock_is_unavailable(monkeypatch):
    conn = _Conn()
    conn.dialect = type('Dialect', (), {'name': 'mssql'})()

    def execute(stmt, params=None):
        conn.calls.append((str(stmt), dict(params or {})))
        return _Result(one=(-1,))

    conn.execute = execute
    monkeypatch.setattr(user_repo, 'engine', _Engine(conn))
    with pytest.raises(RuntimeError, match='Tạm thời chưa thể cập nhật'):
        user_repo.set_user_active_status(7, False)
    assert len(conn.calls) == 1 and 'sp_getapplock' in conn.calls[0][0]


@pytest.mark.parametrize('action,method,args', [
    ('user_active', 'set_user_active_status', (7, {'is_active': False})),
    ('user_delete', 'delete_user_account', (7,)),
])
def test_user_api_preserves_repository_refusal(monkeypatch, action, method, args):
    from mech_chatbot.api.routers import operations

    monkeypatch.setattr(operations.ui_query_service, method, lambda *_a, **_k: {'ok': False, 'message': 'blocked'})
    profile = {'user_id': 1, 'username': 'qa-security-admin', 'roles': ['security_admin']}
    assert getattr(operations, action)(*args, profile=profile)['ok'] is False
