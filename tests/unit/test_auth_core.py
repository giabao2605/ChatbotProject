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
    def __init__(self, one=None, many=None):
        self._one = one
        self._many = many or []

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
    ]
    monkeypatch.setattr(core, "engine", _FakeEngine(_FakeConn(script)))
    # tranh goi DB that trong nhanh loc phong ban da archive
    import mech_chatbot.db.repository as repo
    monkeypatch.setattr(repo, "list_known_departments",
                        lambda active_only=True: [{"code": "Technical"}], raising=False)

    out = core.authenticate_user("bob", "s3cret")
    assert out is not None
    assert out["user_id"] == 7
    assert out["username"] == "bob"
    assert set(out["roles"]) == {"viewer", "uploader"}
    assert "Technical" in out["allowed_departments"]
    assert out["max_security_level"] == "internal"
    assert out["allowed_sites"] == ["HN"]
