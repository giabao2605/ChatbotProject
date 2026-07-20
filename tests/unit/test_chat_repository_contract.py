from __future__ import annotations

import json

import pytest


pytestmark = pytest.mark.unit

chat = pytest.importorskip("mech_chatbot.db.repositories.chat")


class _Result:
    def __init__(self, rows=(), *, rowcount=0):
        self.rows = list(rows)
        self.rowcount = rowcount

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _Connection:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        return self.handler(sql, params)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return self.connection

    def begin(self):
        return self.connection


@pytest.fixture
def install_engine(monkeypatch):
    def _install(handler):
        connection = _Connection(handler)
        monkeypatch.setattr(chat, "engine", _Engine(connection))
        monkeypatch.setattr(chat, "_ensure_engine", lambda: None)
        return connection

    return _install


def test_save_chat_history_normalizes_public_payload(install_engine):
    connection = install_engine(lambda _sql, _params: _Result([(73,)]))

    chat_id = chat.save_chat_history(
        "session-1",
        "question",
        "answer",
        image_path="upload.png",
        ref_images=["page-1.png"],
        username="alice",
    )

    assert chat_id == 73
    params = connection.calls[0][1]
    assert params == {
        "session_id": "session-1",
        "user_msg": "question",
        "bot_msg": "answer",
        "image_path": "upload.png",
        "ref_images": '["page-1.png"]',
        "username": "alice",
    }


def test_save_chat_history_returns_none_when_persistence_fails(install_engine):
    def fail(_sql, _params):
        raise RuntimeError("database unavailable")

    install_engine(fail)

    assert chat.save_chat_history("session", "question", "answer") is None


def test_answer_sources_persist_only_mapping_items_with_normalized_values(install_engine):
    connection = install_engine(lambda _sql, _params: _Result())

    chat.save_answer_sources(
        73,
        [
            "not-a-document",
            {
                "doc_id": "12",
                "file_goc": "drawing.pdf",
                "version_no": "3",
                "variant_code": "large",
                "trang": 5,
                "score": "0.875",
                "is_current": True,
            },
            {"doc_id": "invalid", "score": "invalid", "is_current": False},
        ],
    )

    assert len(connection.calls) == 2
    assert connection.calls[0][1] == {
        "cid": 73,
        "doc_id": 12,
        "fn": "drawing.pdf",
        "vn": 3,
        "vc": "large",
        "chunk": "5",
        "score": 0.875,
        "rank_no": 2,
        "is_cur": 1,
    }
    assert connection.calls[1][1]["doc_id"] is None
    assert connection.calls[1][1]["score"] is None
    assert connection.calls[1][1]["is_cur"] == 0


@pytest.mark.parametrize("chat_id, documents", [(None, [{}]), (1, [])])
def test_answer_sources_ignore_empty_inputs(install_engine, chat_id, documents):
    connection = install_engine(lambda _sql, _params: _Result())

    chat.save_answer_sources(chat_id, documents)

    assert connection.calls == []


def test_answer_evidence_deduplicates_documents_and_records_complete_manifest(
    install_engine,
):
    connection = install_engine(lambda _sql, _params: _Result())

    complete = chat.save_answer_evidence(
        73,
        [
            {
                "doc_id": "12",
                "trang": "5",
                "security_level": "confidential",
            },
            {"doc_id": 12, "page_no": 5, "source_id": "duplicate"},
            {"loai_du_lieu": "user_image"},
        ],
    )

    assert complete is True
    assert "DELETE FROM dbo.AnswerEvidence" in connection.calls[0][0]
    inserted = connection.calls[1][1]
    assert inserted == [
        {
            "chat_id": 73,
            "doc_id": 12,
            "page_no": 5,
            "source_ref": "D12P5",
            "security_level": "confidential",
            "rank_no": 1,
        }
    ]
    manifest = connection.calls[-1][1]
    assert manifest == {
        "chat_id": 73,
        "requires_authorization": 1,
        "is_complete": 1,
        "evidence_count": 1,
    }


def test_answer_evidence_marks_manifest_incomplete_for_unverifiable_items(
    install_engine,
):
    connection = install_engine(lambda _sql, _params: _Result())

    complete = chat.save_answer_evidence(
        74,
        [{"doc_id": "invalid", "loai_du_lieu": "retrieved_document"}],
        requires_authorization=True,
    )

    assert complete is False
    assert len(connection.calls) == 2
    assert connection.calls[-1][1] == {
        "chat_id": 74,
        "requires_authorization": 1,
        "is_complete": 0,
        "evidence_count": 0,
    }


def test_answer_evidence_rejects_missing_chat_and_database_failure(install_engine):
    connection = install_engine(lambda _sql, _params: _Result())
    assert chat.save_answer_evidence(None, []) is False
    assert connection.calls == []

    def fail(_sql, _params):
        raise RuntimeError("database unavailable")

    install_engine(fail)
    assert chat.save_answer_evidence(75, [{"doc_id": 1}]) is False


def test_sessions_are_owner_scoped_even_when_admin_flag_is_true(install_engine):
    connection = install_engine(
        lambda _sql, _params: _Result(
            [
                ("s1", "2026-07-20", "A question that is definitely longer than thirty chars", "alice"),
                ("s2", "2026-07-19", "", "alice"),
            ]
        )
    )

    sessions = chat.get_all_sessions("alice", is_admin=True)

    assert sessions == [
        {
            "session_id": "s1",
            "thoi_gian": "2026-07-20",
            "cau_hoi": "A question that is definitely ...",
            "owner": "alice",
        },
        {
            "session_id": "s2",
            "thoi_gian": "2026-07-19",
            "cau_hoi": "(Khong co tieu de)",
            "owner": "alice",
        },
    ]
    sql, params = connection.calls[0]
    assert "WHERE Username = :username" in sql
    assert params == {"username": "alice"}


def test_sessions_fail_closed_on_database_error(install_engine):
    def fail(_sql, _params):
        raise RuntimeError("database unavailable")

    install_engine(fail)

    assert chat.get_all_sessions("alice") == []


def test_history_returns_owner_messages_when_complete_evidence_is_readable(
    install_engine, monkeypatch
):
    monkeypatch.setenv("RBAC_STRICT_SITE_FILTER", "true")

    def handle(sql, params):
        if "FROM LichSuChat" in sql:
            assert params == {"session_id": "s1", "username": "alice"}
            return _Result([(73, "question", "answer", None, 1, '["page.png"]')])
        if "FROM dbo.ChatEvidenceManifest" in sql:
            return _Result([(73, 1, 1, 1)])
        if "FROM dbo.AnswerEvidence" in sql:
            return _Result(
                [(73, 900, 12, "internal", "HCM", 1, "published", "published", "approved", 1)]
            )
        return _Result()

    connection = install_engine(handle)

    history = chat.get_chat_history(
        "s1",
        username="alice",
        user_clearance="internal",
        allowed_departments=["QA"],
        allowed_sites=["HCM"],
    )

    assert history == [
        {"role": "user", "content": "question", "image": None},
        {
            "role": "assistant",
            "content": "answer",
            "chat_id": 73,
            "danh_gia": 1,
            "ref_images": ["page.png"],
        },
    ]
    evidence_sql = next(sql for sql, _ in connection.calls if "FROM dbo.AnswerEvidence" in sql)
    assert "pb.DeptCode IN (:dept_0)" in evidence_sql


def test_history_redacts_answer_after_permissions_are_revoked(install_engine, monkeypatch):
    monkeypatch.setenv("RBAC_STRICT_SITE_FILTER", "true")

    def handle(sql, _params):
        if "FROM LichSuChat" in sql:
            return _Result([(73, "question", "secret answer", None, None, '["page.png"]')])
        if "FROM dbo.ChatEvidenceManifest" in sql:
            return _Result([(73, 1, 1, 1)])
        if "FROM dbo.AnswerEvidence" in sql:
            return _Result(
                [(73, 900, 12, "confidential", "HCM", 1, "published", "published", "approved", 1)]
            )
        return _Result()

    install_engine(handle)

    history = chat.get_chat_history(
        "s1",
        username="alice",
        user_clearance="public",
        allowed_departments=["QA"],
        allowed_sites=["HCM"],
    )

    assert "không còn quyền xem" in history[1]["content"]
    assert history[1]["ref_images"] == []


def test_history_authorization_error_fails_closed(install_engine):
    def handle(sql, _params):
        if "FROM LichSuChat" in sql:
            return _Result([(73, "question", "secret answer", None, None, "not-json")])
        raise RuntimeError("authorization lookup failed")

    install_engine(handle)

    history = chat.get_chat_history("s1", username="alice")

    assert "không còn quyền xem" in history[1]["content"]
    assert history[1]["ref_images"] == []


def test_empty_history_and_database_failure_return_no_messages(install_engine):
    install_engine(lambda _sql, _params: _Result())
    assert chat.get_chat_history("missing", username="alice") == []

    def fail(_sql, _params):
        raise RuntimeError("database unavailable")

    install_engine(fail)
    assert chat.get_chat_history("s1", username="alice") == []


def test_clear_history_is_owner_scoped_and_admin_is_not_a_bypass(install_engine):
    connection = install_engine(lambda _sql, _params: _Result(rowcount=2))

    deleted = chat.clear_chat_history("s1", username="alice", is_admin=True)

    assert deleted == 2
    assert len(connection.calls) == 3
    assert all("Username = :username" in sql for sql, _ in connection.calls)
    assert all(
        params == {"session_id": "s1", "username": "alice"}
        for _, params in connection.calls
    )


def test_clear_history_propagates_database_failure(install_engine):
    def fail(_sql, _params):
        raise RuntimeError("database unavailable")

    install_engine(fail)

    with pytest.raises(RuntimeError, match="database unavailable"):
        chat.clear_chat_history("s1", username="alice")
