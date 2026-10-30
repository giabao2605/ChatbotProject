"""Characterization tests cho auth core sau P0 refactor.

Khoa lai hanh vi cua `authenticate_user` (da tach ra khoi Streamlit):
- Rate-limited  -> None
- engine None   -> None
- User khong ton tai -> None + ghi nhan that bai
- Sai mat khau       -> None + ghi nhan that bai
- Dang nhap dung     -> tra ve dict day du field

Dung fake engine/connection + monkeypatch, KHONG can SQL Server that.
Can cai: sqlalchemy, bcrypt, python-dotenv (requirements.txt). Marker: unit.
"""
import pytest

pytestmark = pytest.mark.unit

core = pytest.importorskip("mech_chatbot.auth.core")
import bcrypt  # noqa: E402


class _FakeResult:
    def __init__(self, one=None, many=None, rowcount=1):
        self._one = one
        self._many = many or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._many


class _FakeConn:
    """Tra ket qua theo thu tu cac cau execute ma authenticate_user goi."""
    def __init__(self, script):
        self._script = list(script)
        self._i = 0

    def execute(self, *_a, **_k):
        res = self._script[self._i] if self._i < len(self._script) else _FakeResult()
        self._i += 1
        if isinstance(res, Exception):
            raise res
        return res

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeEngine:
    def __init__(self, conn):
        self._conn = conn

    def connect(self):
        return self._conn

    def begin(self):
        return self._conn


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    monkeypatch.setattr(core.rate_limit, "is_rate_limited", lambda *a, **k: False)
    monkeypatch.setattr(core.rate_limit, "record_failure", lambda *a, **k: None)
    monkeypatch.setattr(core.rate_limit, "clear_failures", lambda *a, **k: None)


def test_rate_limited_returns_none(monkeypatch):
    monkeypatch.setattr(core.rate_limit, "is_rate_limited", lambda *a, **k: True)
    assert core.authenticate_user("someone", "pw") is None


def test_engine_none_returns_none(monkeypatch):
    monkeypatch.setattr(core, "engine", None)
    assert core.authenticate_user("someone", "pw") is None


def test_unknown_user_returns_none(monkeypatch):
    called = {"fail": 0}
    monkeypatch.setattr(core.rate_limit, "record_failure",
                        lambda *a, **k: called.__setitem__("fail", called["fail"] + 1))
    conn = _FakeConn([_FakeResult(one=None)])  # user query -> khong tim thay
    monkeypatch.setattr(core, "engine", _FakeEngine(conn))
    assert core.authenticate_user("ghost", "pw") is None
    assert called["fail"] == 1


def test_wrong_password_returns_none(monkeypatch):
    hashed = bcrypt.hashpw(b"correct", bcrypt.gensalt()).decode()
    user_row = (1, "alice", "Alice", "Technical", True, hashed)
    conn = _FakeConn([_FakeResult(one=user_row)])
    monkeypatch.setattr(core, "engine", _FakeEngine(conn))
    assert core.authenticate_user("alice", "wrong") is None


def test_successful_login_returns_profile(monkeypatch):
    hashed = bcrypt.hashpw(b"s3cret", bcrypt.gensalt()).decode()
    user_row = (7, "bob", "Bob", "Technical", True, hashed)
    script = [
        _FakeResult(one=user_row),                 # SELECT user
        _FakeResult(many=[("viewer",), ("uploader",)]),  # roles
        _FakeResult(many=[("Technical",)]),         # UserDepartments
        _FakeResult(one=("internal",)),            # UserSecurityClearance
        _FakeResult(many=[("HN",)]),               # UserSites
        _FakeResult(one=("en",)),                  # PreferredLanguage
    ]
    monkeypatch.setattr(core, "engine", _FakeEngine(_FakeConn(script)))
    # tranh goi DB that trong nhanh loc phong ban da archive
    from mech_chatbot.db.repositories import catalog
    monkeypatch.setattr(catalog, "list_known_departments",
                        lambda active_only=True: [{"code": "Technical"}], raising=False)

    out = core.authenticate_user("bob", "s3cret")
    assert out is not None
    assert out["user_id"] == 7
    assert out["username"] == "bob"
    assert set(out["roles"]) == {"viewer", "uploader"}
    assert "Technical" in out["allowed_departments"]
    assert out["max_security_level"] == "internal"
    assert out["allowed_sites"] == ["HN"]
    assert out["preferred_language"] == "en"


def test_load_user_profile_returns_current_db_permissions(monkeypatch):
    user_row = (7, "bob", "Bob", "Technical", True, "unused")
    script = [
        _FakeResult(one=user_row),                 # SELECT user
        _FakeResult(many=[("viewer",)]),           # roles
        _FakeResult(many=[("Technical",)]),        # UserDepartments
        _FakeResult(one=("public",)),              # UserSecurityClearance
        _FakeResult(many=[("HN",)]),               # UserSites
        _FakeResult(one=("en",)),                  # PreferredLanguage
    ]
    monkeypatch.setattr(core, "engine", _FakeEngine(_FakeConn(script)))
    from mech_chatbot.db.repositories import catalog
    monkeypatch.setattr(catalog, "list_known_departments",
                        lambda active_only=True: [{"code": "Technical"}], raising=False)

    out = core.load_user_profile(user_id=7, username="bob")

    assert out is not None
    assert out["user_id"] == 7
    assert out["roles"] == ["viewer"]
    assert out["allowed_departments"] == ["Technical"]
    assert out["max_security_level"] == "public"
    assert out["preferred_language"] == "en"


def test_load_user_profile_rejects_mismatched_username(monkeypatch):
    user_row = (7, "bob", "Bob", "Technical", True, "unused")
    conn = _FakeConn([_FakeResult(one=user_row)])
    monkeypatch.setattr(core, "engine", _FakeEngine(conn))

    assert core.load_user_profile(user_id=7, username="alice") is None


def test_update_user_preferred_language_persists_valid_lang(monkeypatch):
    conn = _FakeConn([_FakeResult(rowcount=1)])
    monkeypatch.setattr(core, "engine", _FakeEngine(conn))

    assert core.update_user_preferred_language(7, "en") is True


def test_update_user_preferred_language_rejects_invalid_lang(monkeypatch):
    conn = _FakeConn([_FakeResult(rowcount=1)])
    monkeypatch.setattr(core, "engine", _FakeEngine(conn))

    assert core.update_user_preferred_language(7, "fr") is False
    assert conn._i == 0


@pytest.fixture
def strict_store(monkeypatch):
    from sqlalchemy import create_engine, text
    store = create_engine('sqlite://')
    tables = {
        'Users': 'UserID INTEGER, Username TEXT, DisplayName TEXT, Department TEXT, IsActive INTEGER, PreferredLanguage TEXT',
        'Roles': 'RoleID INTEGER, RoleName TEXT', 'UserRoles': 'UserID INTEGER, RoleID INTEGER',
        'UserDepartments': 'UserID INTEGER, Department TEXT',
        'Departments': 'DeptCode TEXT, Status TEXT, IsActive INTEGER',
        'UserSecurityClearance': 'UserID INTEGER, MaxLevel TEXT', 'UserSites': 'UserID INTEGER, Site TEXT',
    }
    with store.begin() as conn:
        for table, columns in tables.items():
            conn.execute(text(f'CREATE TABLE {table} ({columns})'))
        for statement in (
            "INSERT INTO Users VALUES (7,'bob','Bob','Technical',1,'en')",
            "INSERT INTO Roles VALUES (1,'reviewer')", 'INSERT INTO UserRoles VALUES (7,1)',
            "INSERT INTO UserDepartments VALUES (7,'Technical')",
            "INSERT INTO Departments VALUES ('Technical','active',1),('CHUNG','active',1)",
            "INSERT INTO UserSecurityClearance VALUES (7,'internal')", "INSERT INTO UserSites VALUES (7,'HN')",
        ):
            conn.execute(text(statement))
    monkeypatch.setattr(core, 'engine', store)
    yield store
    store.dispose()


def test_strict_profile_reads_rights_on_the_existing_evidence_snapshot(strict_store):
    from time import monotonic
    from sqlalchemy import event
    from mech_chatbot.db.evidence_snapshot import evidence_read_snapshot
    connections, statements = [], []
    event.listen(strict_store, 'checkout', lambda *args: connections.append(True))
    event.listen(strict_store, 'before_cursor_execute',
                 lambda conn, cursor, sql, params, context, many: statements.append((id(conn), sql)))
    with evidence_read_snapshot(lambda _: None, monotonic()+30, db_engine=strict_store):
        profile = core.load_user_profile(user_id=7, username='bob', strict=True)
    assert len(connections) == 1 and len({conn for conn, sql in statements}) == 1
    assert all('PasswordHash' not in sql for conn, sql in statements)
    assert profile['roles'] == ['reviewer'] and profile['allowed_departments'] == ['Technical']
    assert profile['allowed_sites'] == ['HN'] and profile['max_security_level'] == 'internal'
    assert profile['preferred_language'] == 'en'


@pytest.mark.parametrize('table', ['Users', 'Roles', 'UserDepartments', 'Departments', 'UserSecurityClearance', 'UserSites'])
def test_strict_profile_never_falls_back_after_an_acl_query_failure(strict_store, table):
    from sqlalchemy import event
    def fail_query(conn, cursor, sql, params, context, many):
        if f'FROM {table} ' in sql:
            raise RuntimeError('private database failure')
    event.listen(strict_store, 'before_cursor_execute', fail_query)
    with pytest.raises(PermissionError, match='Actor profile unavailable') as error:
        core.load_user_profile(user_id=7, username='bob', strict=True)
    assert 'private' not in str(error.value)


@pytest.mark.parametrize('user_id,username', [(None,'bob'), (True,'bob'), (7.0,'bob'), ('7','bob'),
                                            (0,'bob'), (-1,'bob'), (7,None), (7,''), (7,False)])
def test_strict_profile_requires_explicit_typed_actor_identity(strict_store, user_id, username):
    with pytest.raises(ValueError):
        core.load_user_profile(user_id=user_id, username=username, strict=True)


@pytest.mark.parametrize('username,active', [('alice',1), ('bob',0)])
def test_strict_profile_rejects_mismatched_or_inactive_users(strict_store, username, active):
    from sqlalchemy import text
    with strict_store.begin() as conn:
        conn.execute(text('UPDATE Users SET IsActive=:active'), {'active': active})
    with pytest.raises(PermissionError, match='Actor profile unavailable'):
        core.load_user_profile(user_id=7, username=username, strict=True)


@pytest.mark.parametrize('code,status,active', [('Technical','disabled',1), ('Technical','active',0),
                                             ('Legacy','active',1), ('CHUNG','disabled',0)])
def test_strict_department_catalog_never_grants_unknown_or_inactive_departments(strict_store, code, status, active):
    from sqlalchemy import text
    with strict_store.begin() as conn:
        conn.execute(text('UPDATE UserDepartments SET Department=:code'), {'code': code})
        conn.execute(text('UPDATE Departments SET Status=:status,IsActive=:active WHERE DeptCode=:code'),
                     {'code': code, 'status': status, 'active': active})
    with pytest.raises(PermissionError, match='Actor profile unavailable'):
        core.load_user_profile(user_id=7, username='bob', strict=True)


def test_strict_profile_allows_real_empty_sites_and_never_adds_department_rights(strict_store):
    from sqlalchemy import text
    with strict_store.begin() as conn:
        conn.execute(text('DELETE FROM UserSites'))
        conn.execute(text('DELETE FROM UserDepartments'))
    profile = core.load_user_profile(user_id=7, username='bob', strict=True)
    assert profile['allowed_sites'] == [] and profile['allowed_departments'] == []


def test_strict_profile_language_failure_keeps_verified_rights(strict_store):
    from sqlalchemy import event
    def fail_language(conn, cursor, sql, params, context, many):
        if 'SELECT PreferredLanguage' in sql:
            raise RuntimeError('language unavailable')
    event.listen(strict_store, 'before_cursor_execute', fail_language)
    profile = core.load_user_profile(user_id=7, username='bob', strict=True)
    assert profile['preferred_language'] == 'vi'
    assert profile['allowed_sites'] == ['HN'] and profile['allowed_departments'] == ['Technical']


@pytest.mark.parametrize('user_id', [None,7])
def test_legacy_profile_lookup_preserves_success_by_username_or_id(strict_store,monkeypatch,user_id):
    from sqlalchemy import text
    from mech_chatbot.db.repositories import catalog
    with strict_store.begin() as conn:
        conn.execute(text('ALTER TABLE Users ADD PasswordHash TEXT'))
    monkeypatch.setattr(catalog,'list_known_departments',lambda active_only=True:[{'code':'Technical'}])
    profile=core.load_user_profile(user_id=user_id,username='bob')
    assert profile['user_id']==7 and profile['username']=='bob' and profile['roles']==['reviewer']
    assert profile['allowed_departments']==['Technical'] and profile['allowed_sites']==['HN']
    assert profile['max_security_level']=='internal' and 'PasswordHash' not in profile


@pytest.mark.parametrize('fault', ['engine','identity','missing','inactive','mismatch','database'])
def test_legacy_profile_lookup_keeps_its_none_result_on_unavailable_actor(strict_store,monkeypatch,fault):
    from sqlalchemy import text
    with strict_store.begin() as conn:
        conn.execute(text('ALTER TABLE Users ADD PasswordHash TEXT'))
        if fault=='inactive':conn.execute(text('UPDATE Users SET IsActive=0'))
        if fault=='database':conn.execute(text('DROP TABLE Roles'))
    if fault=='engine':monkeypatch.setattr(core,'engine',None)
    user_id=None if fault in ('identity','missing') else 7
    username=None if fault=='identity' else 'ghost' if fault in ('missing','mismatch') else 'bob'
    assert core.load_user_profile(user_id=user_id,username=username) is None


@pytest.mark.parametrize('statement', ["UPDATE Roles SET RoleName=NULL", "UPDATE UserSites SET Site=NULL",
                                       "UPDATE Departments SET Status=NULL"])
def test_strict_profile_rejects_malformed_acl_rows(strict_store, statement):
    from sqlalchemy import text
    with strict_store.begin() as conn:
        conn.execute(text(statement))
    with pytest.raises(PermissionError, match='Actor profile unavailable'):
        core.load_user_profile(user_id=7, username='bob', strict=True)


@pytest.mark.parametrize('clearance', [None, 'invalid'])
def test_strict_profile_missing_or_invalid_clearance_remains_public(strict_store, clearance):
    from sqlalchemy import text
    with strict_store.begin() as conn:
        conn.execute(text('DELETE FROM UserSecurityClearance'))
        if clearance is not None:
            conn.execute(text('INSERT INTO UserSecurityClearance VALUES (7,:level)'), {'level': clearance})
    profile = core.load_user_profile(user_id=7, username='bob', strict=True)
    assert profile['max_security_level'] == 'public' and profile['allowed_sites'] == ['HN']


def test_default_profile_keeps_existing_acl_fallbacks(monkeypatch):
    failure = RuntimeError('legacy fixture unavailable')
    script = [_FakeResult(one=(7,'bob','Bob','Technical',True,'unused')),
              _FakeResult(many=[('viewer',)]), failure, failure, failure, failure]
    monkeypatch.setattr(core, 'engine', _FakeEngine(_FakeConn(script)))
    profile = core.load_user_profile(user_id=7, username='bob')
    assert profile['allowed_departments'] == [core.SHARE_ALL_DEPARTMENT]
    assert profile['allowed_sites'] == [] and profile['max_security_level'] == 'public'
    assert profile['preferred_language'] == 'vi'
