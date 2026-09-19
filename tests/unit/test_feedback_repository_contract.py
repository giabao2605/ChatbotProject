from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from mech_chatbot.db.repositories import feedback


pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, *, rows=(), row=None, rowcount=0):
        self._rows = list(rows)
        self._row = row
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, results=()):
        self.results = list(results)
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((str(statement), dict(params or {})))
        result = self.results.pop(0) if self.results else _Result()
        if isinstance(result, BaseException):
            raise result
        return result

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
def fake_db(monkeypatch):
    def install(*results):
        connection = _Connection(results)
        monkeypatch.setattr(feedback, "engine", _Engine(connection))
        monkeypatch.setattr(feedback, "_ensure_engine", lambda: None)
        return connection

    return install


def test_question_normalization_is_accent_insensitive_and_stable():
    assert feedback.normalize_question("  Kiểm   tra ÁP suất ") == "kiem tra ap suat"
    assert feedback._question_hash("Kiểm tra áp suất") == feedback._question_hash("kiem TRA ap suat")
    assert feedback._role_weight(["guest", "reviewer", "unknown"]) == 2.0
    assert feedback._role_weight(None) == 1.0


def test_mark_feedback_stale_validates_input_and_fails_closed(fake_db):
    assert feedback.mark_feedback_stale_for_doc(None) == 0

    conn = fake_db(_Result(rowcount=2))
    assert feedback.mark_feedback_stale_for_doc(7, resolved_by_doc_id=8) == 2
    assert conn.calls[0][1] == {"d": 7, "rb": 8}

    fake_db(RuntimeError("database unavailable"))
    assert feedback.mark_feedback_stale_for_doc(7) == 0


def test_recompute_quality_scores_weights_roles_and_ignores_bad_timestamp(fake_db):
    now = datetime.now()
    conn = fake_db(
        _Result(rows=[("alice", "admin"), ("alice", "viewer"), ("bob", "user")]),
        _Result(
            rows=[
                (10, 1, now - timedelta(days=90), "alice"),
                (10, -1, now - timedelta(days=1), "bob"),
                (11, 1, "not-a-date", "nobody"),
                (None, 1, now, "alice"),
            ]
        ),
        _Result(rowcount=2),
        _Result(rowcount=1),
        _Result(rowcount=1),
    )

    assert feedback.recompute_doc_quality_scores() == 2

    inserts = [(sql, params) for sql, params in conn.calls if "INSERT INTO DocQualityScore" in sql]
    assert [params["d"] for _, params in inserts] == [10, 11]
    assert inserts[0][1]["lk"] == 1
    assert inserts[0][1]["dk"] == 1
    assert inserts[1][1]["n"] == 1


def test_recompute_quality_scores_tolerates_role_lookup_failure(fake_db):
    conn = fake_db(
        RuntimeError("roles unavailable"),
        _Result(rows=[]),
        _Result(rowcount=0),
    )

    assert feedback.recompute_doc_quality_scores() == 0
    assert any("DELETE FROM DocQualityScore" in sql for sql, _ in conn.calls)


@pytest.mark.parametrize("worst_first, expected", [(True, "ASC"), (False, "DESC")])
def test_get_doc_quality_ranking_maps_rows_and_reliability(fake_db, worst_first, expected):
    row = (1, "manual.pdf", 2, True, "published", 4, 1, 3.5, 0.4, 0.8, 3.1, 3, datetime(2026, 1, 2))
    conn = fake_db(_Result(rows=[row]))

    result = feedback.get_doc_quality_ranking(limit="5", worst_first=worst_first)

    assert result[0]["doc_id"] == 1
    assert result[0]["reliable"] is True
    assert f"QualityScore {expected}" in conn.calls[0][0]
    assert conn.calls[0][1] == {"lim": 5}


def test_golden_answer_public_contract_handles_validation_success_empty_and_db_failure(fake_db):
    assert feedback.upsert_golden_answer("", "answer") is None
    assert feedback.upsert_golden_answer("question", "") is None

    conn = fake_db(_Result(), _Result())
    digest = feedback.upsert_golden_answer(
        "Question", "Answer", source_doc_id=4, department="Technical", site="HQ", created_by="alice"
    )
    assert digest == feedback._question_hash("Question")
    assert conn.calls[-1][1]["sd"] == 4

    fake_db(RuntimeError("write failed"))
    assert feedback.upsert_golden_answer("Question", "Answer") is None

    fake_db(_Result(row=None))
    assert feedback.find_golden_answer("missing") is None

    fake_db(_Result(row=(9, "Question", "Answer", 4, "Technical", "HQ")))
    assert feedback.find_golden_answer("Question") == {
        "golden_id": 9,
        "question": "Question",
        "answer": "Answer",
        "source_doc_id": 4,
        "department": "Technical",
        "site": "HQ",
    }

    fake_db(RuntimeError("read failed"))
    assert feedback.find_golden_answer("Question") is None


def test_regression_question_and_run_crud_use_public_shapes(fake_db):
    assert feedback.add_regression_question(" ") is None

    conn = fake_db(_Result(row=(14,)))
    assert feedback.add_regression_question(
        "Check pressure", expected_doc_id=3, expected_keywords=[" pressure ", "", "limit"]
    ) == 14
    assert conn.calls[0][1]["kw"] == "pressure, limit"

    conn = fake_db(_Result(rows=[(14, "Q", 3, "pressure", "Tech", "HQ", True, "now")]))
    assert feedback.list_regression_questions()[0]["reg_qid"] == 14
    assert "WHERE IsActive = 1" in conn.calls[0][0]

    conn = fake_db(_Result())
    assert feedback.set_regression_question_active(14, False) is True
    assert conn.calls[0][1] == {"a": 0, "id": 14}

    conn = fake_db(_Result())
    assert feedback.save_regression_run(14, "batch", "answer", [3, None, 7], True, False, True) is True
    assert conn.calls[0][1]["m"] == "3,7"
    assert conn.calls[0][1]["dh"] == 1

    fake_db(_Result(row=None))
    assert feedback.get_latest_regression_batch() is None

    fake_db(_Result(rows=[]))
    assert feedback.get_regression_runs(batch_id="") == []

    fake_db(_Result(rows=[(1, 14, "Q", 1, 1, 1, "3", 3, 12, None, "A", "now")]))
    assert feedback.get_regression_runs("batch")[0]["passed"] == 1


def test_get_regression_runs_resolves_latest_batch(fake_db, monkeypatch):
    monkeypatch.setattr(feedback, "get_latest_regression_batch", lambda: "latest")
    conn = fake_db(_Result(rows=[]))

    assert feedback.get_regression_runs() == []
    assert conn.calls[0][1] == {"b": "latest"}


def test_cleanup_dangling_records_returns_per_operation_counts(fake_db):
    fake_db_conn = fake_db(
        _Result(rowcount=1),
        _Result(rowcount=None),
        _Result(rowcount=2),
        _Result(rowcount=3),
    )

    assert feedback.cleanup_dangling_records() == {
        "golden_source_nulled": 1,
        "feedback_source_nulled": 0,
        "answersource_orphan_deleted": 2,
        "quality_orphan_deleted": 3,
    }
    assert len(fake_db_conn.calls) == 4


def test_update_chat_feedback_enforces_owner_and_idempotency(fake_db):
    fake_db(_Result(row=None))
    assert feedback.update_chat_feedback(1, 1, voter_username="alice") is False

    fake_db(_Result(row=("bob", None)))
    assert feedback.update_chat_feedback(1, 1, voter_username="alice") is False

    conn = fake_db(_Result(row=("alice", 1)))
    assert feedback.update_chat_feedback(1, 1, voter_username="Alice") is True
    assert len(conn.calls) == 1


def test_update_chat_feedback_creates_review_with_source_context(fake_db):
    conn = fake_db(
        _Result(row=("alice", None)),
        _Result(rowcount=1),
        _Result(row=("Question", "Answer")),
        _Result(row=None),
        _Result(row=(7, 3)),
        _Result(row=("Technical", "HQ")),
        _Result(rowcount=1),
    )

    assert feedback.update_chat_feedback(5, -1, voter_username="alice") is True

    insert = next(params for sql, params in conn.calls if "INSERT INTO FeedbackReview" in sql)
    assert insert["sd"] == 7
    assert insert["sv"] == 3
    assert insert["dept"] == "Technical"
    assert insert["ch"] == feedback._question_hash("Question")


def test_update_chat_feedback_fails_closed_on_db_error(fake_db):
    fake_db(RuntimeError("database unavailable"))
    assert feedback.update_chat_feedback(1, -1, voter_username="alice") is False


def test_ensure_regression_question_deduplicates_or_delegates(fake_db, monkeypatch):
    assert feedback.ensure_regression_question(" ") is None

    fake_db(_Result(rows=[(4, "  KIỂM tra áp suất ")]))
    assert feedback.ensure_regression_question("kiem tra ap suat") == 4

    fake_db(RuntimeError("lookup failed"))
    captured = {}

    def add(question, **kwargs):
        captured.update({"question": question, **kwargs})
        return 8

    monkeypatch.setattr(feedback, "add_regression_question", add)
    assert feedback.ensure_regression_question("new", expected_doc_id=2, expected_keywords=["x"]) == 8
    assert captured["expected_doc_id"] == 2
