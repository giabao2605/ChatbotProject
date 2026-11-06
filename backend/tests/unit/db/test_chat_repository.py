import pytest

pytestmark = pytest.mark.unit

chat_repo = pytest.importorskip("mech_chatbot.db.repositories.chat")


class _Result:
    def __init__(self, rowcount=0):
        self.rowcount = rowcount


class _Conn:
    def __init__(self):
        self.calls = []

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), dict(params or {})))
        return _Result(rowcount=3)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Engine:
    def __init__(self, conn):
        self.conn = conn

    def begin(self):
        return self.conn


def test_clear_chat_history_deletes_children_before_session(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(chat_repo, "engine", _Engine(conn))
    monkeypatch.setattr(chat_repo, "_ensure_engine", lambda: None)

    deleted = chat_repo.clear_chat_history("sid-1", username="alice", is_admin=False)

    assert deleted == 3
    assert "DELETE FROM AnswerSource" in conn.calls[0][0]
    assert "DELETE FROM FeedbackReview" in conn.calls[1][0]
    assert "DELETE FROM LichSuChat" in conn.calls[2][0]
    assert "Username = :username" in conn.calls[0][0]
    assert conn.calls[0][1] == {"session_id": "sid-1", "username": "alice"}
