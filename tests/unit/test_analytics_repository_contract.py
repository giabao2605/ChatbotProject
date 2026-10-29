from __future__ import annotations

from datetime import date
import re
import sqlite3

import pytest

from mech_chatbot.db.repositories import analytics

from ._small_repository_fakes import Engine, Result


pytestmark = pytest.mark.unit


def _install(monkeypatch, outcomes=(), **engine_kwargs):
    fake = Engine(outcomes, **engine_kwargs)
    monkeypatch.setattr(analytics, "_ensure_engine", lambda: None)
    monkeypatch.setattr(analytics, "engine", fake)
    return fake


def test_department_dashboard_combines_owned_shared_and_job_counts(monkeypatch):
    fake = _install(
        monkeypatch,
        (
            Result(rows=(("Technical", 3, 1, 2, 1), ("QA", 1, 0, 1, 0))),
            Result(rows=((" QA ", 4), ("Technical", 2))),
            Result(rows=(("Technical", 1, 2),)),
        ),
    )

    result = analytics.dashboard_by_department()

    assert result == [
        {
            "department": "Technical",
            "owned_total": 3,
            "shared_access": 2,
            "total": 5,
            "pending_review": 1,
            "published": 2,
            "confidential": 1,
            "failed_jobs": 1,
            "running_jobs": 2,
        },
        {
            "department": "QA",
            "owned_total": 1,
            "shared_access": 4,
            "total": 5,
            "pending_review": 0,
            "published": 1,
            "confidential": 0,
            "failed_jobs": 0,
            "running_jobs": 0,
        },
    ]
    assert len(fake.connection.calls) == 3


def test_department_dashboard_keeps_owned_counts_when_shared_query_is_unsupported(monkeypatch):
    _install(
        monkeypatch,
        (
            Result(rows=(("Technical", 2, None, None, None),)),
            RuntimeError("shared table unavailable"),
            Result(rows=()),
        ),
    )

    assert analytics.dashboard_by_department()[0]["total"] == 2


def test_department_dashboard_and_count_fail_closed_on_database_error(monkeypatch):
    _install(monkeypatch, connect_error=RuntimeError("offline"))

    assert analytics.dashboard_by_department() == []
    assert analytics.count_docs_by_department() == {}


def test_count_docs_by_department_normalizes_null_counts(monkeypatch):
    _install(monkeypatch, (Result(rows=(("Technical", 4), ("Unknown", None))),))

    assert analytics.count_docs_by_department() == {"Technical": 4, "Unknown": 0}


def test_usage_analytics_returns_normalized_questions_documents_and_no_answer_rate(monkeypatch):
    _install(
        monkeypatch,
        (
            Result(row=(4, 2, 3, 2, 1)),
            Result(rows=((date(2026, 7, 19), 1), (date(2026, 7, 20), 3))),
            Result(
                rows=(
                    ("Ap suat toi da?", "Khong co thong tin."),
                    ("Áp suất tối đa?", "12 bar"),
                    ("  ", None),
                    (None, "Ngoài phạm vi"),
                )
            ),
            Result(rows=(("manual.pdf", 2),)),
        ),
    )

    result = analytics.get_usage_analytics(days=7, top_n=1)

    assert result == {
        "days": 7,
        "total_questions": 4,
        "total_sessions": 2,
        "total_users": 3,
        "no_answer_count": 2,
        "no_answer_rate": 50.0,
        "likes": 2,
        "dislikes": 1,
        "top_questions": [{"question": "ap suat toi da?", "count": 2}],
        "daily": [
            {"date": "2026-07-19", "count": 1},
            {"date": "2026-07-20", "count": 3},
        ],
        "top_documents": [{"document": "manual.pdf", "count": 2}],
    }


@pytest.mark.parametrize("top_n, expected_count", [(3, 3), (2, 2), (0, 0), ("2; DROP TABLE TaiLieu", 2)])
def test_usage_top_documents_counts_final_citations_once_per_chat_not_images(monkeypatch, top_n, expected_count):
    database = sqlite3.connect(":memory:")
    database.executescript("""
        CREATE TABLE LichSuChat (ChatID INTEGER, SessionID TEXT, Username TEXT,
            DanhGia INTEGER, ThoiGian TEXT, CauHoi_User TEXT, TraLoi_Bot TEXT, RefImages TEXT);
        CREATE TABLE TaiLieu (DocID INTEGER, TenFile TEXT);
        CREATE TABLE AnswerSource (ChatID INTEGER, DocID INTEGER, ChunkRef TEXT);
        CREATE TABLE AnswerEvidence (ChatID INTEGER, DocID INTEGER);
        INSERT INTO TaiLieu VALUES (1,'manual.pdf'),(2,'manual.pdf'),(3,'drawing.pdf'),(4,'uncited.pdf');
        INSERT INTO LichSuChat VALUES
            (1,'session1','QA',0,datetime('now'),'q1','answer','[]'),
            (2,'session2','QA',0,datetime('now'),'q2','answer','[]'),
            (3,'session3','QA',0,datetime('now'),'q3','answer','["phantom.png"]'),
            (4,'old','QA',0,datetime('now','-42 days'),'old','answer','[]');
        INSERT INTO AnswerSource VALUES
            (1,1,'page1'),(1,1,'page2'),(1,1,'page2'),(2,1,'page3'),
            (2,2,'page1'),(3,3,'page1'),(4,3,'old'),(1,NULL,'null'),(1,999,'deleted');
        INSERT INTO AnswerEvidence VALUES (1,4);
    """)
    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, params=None):
            original = str(statement)
            calls.append((original, params))
            query = original.replace("dbo.", "")
            query = re.sub(r"DATEADD\(day,\s*-:d,\s*GETDATE\(\)\)", "datetime('now', '-' || :d || ' days')", query)
            query = query.replace("CAST(ThoiGian AS DATE)", "date(ThoiGian)")
            if "TOP (:limit)" in query:
                query = query.replace("TOP (:limit)", "") + " LIMIT :limit"
            return database.execute(query, params or {})

    class SqliteEngine:
        def connect(self):
            return Connection()

    monkeypatch.setattr(analytics, "_ensure_engine", lambda: None)
    monkeypatch.setattr(analytics, "engine", SqliteEngine())
    try:
        result = analytics.get_usage_analytics(days=7, top_n=top_n)
        expected = [
            {"document": "manual.pdf", "count": 2},
            {"document": "manual.pdf", "count": 1},
            {"document": "drawing.pdf", "count": 1},
        ][:expected_count]
        assert result["top_documents"] == expected
        citation_sql, params = next(call for call in calls if "AnswerSource" in call[0])
        assert "AnswerEvidence" not in citation_sql
        assert "RefImages" not in citation_sql
        assert params == {"d": 7, "limit": expected_count}
        assert database.execute("SELECT COUNT(*) FROM TaiLieu").fetchone()[0] == 4
    finally:
        database.close()


def test_usage_analytics_preserves_empty_shape_on_database_failure(monkeypatch):
    _install(monkeypatch, connect_error=RuntimeError("offline"))

    result = analytics.get_usage_analytics(days=14, top_n=5)

    assert result["days"] == 14
    assert result["total_questions"] == 0
    assert result["no_answer_rate"] == 0.0
    assert result["top_questions"] == []


def test_trace_summary_is_idempotent_when_trace_already_exists(monkeypatch):
    fake = _install(monkeypatch, (Result(row=(1,)),))

    analytics.save_rag_trace_summary("trace-existing", {"question": "ignored"})

    assert len(fake.connection.calls) == 1


def test_trace_summary_caps_identity_and_sanitizes_metrics(monkeypatch):
    fake = _install(monkeypatch, (Result(row=None), Result()))
    trace_id = "t" * 100

    analytics.save_rag_trace_summary(
        trace_id,
        {
            "department": "Technical",
            "roles": "admin",
            "model": "answer-v1",
            "question": "pressure?",
            "tokens_in": "12",
            "tokens_out": "bad",
            "cost": "0.125",
            "final_latency_ms": 321,
            "refusal": True,
            "refusal_reason": "insufficient_evidence",
            "docs_count": "2",
            "retrieval_mode": "hybrid",
        },
    )

    params = fake.connection.calls[1][1]
    assert params["tid"] == "t" * 80
    assert params["tin"] == 12
    assert params["tout"] is None
    assert params["cost"] == pytest.approx(0.125)
    assert params["refusal"] == 1
    assert params["docs"] == 2


def test_trace_summary_ignores_empty_id_and_database_failure(monkeypatch):
    fake = _install(monkeypatch, begin_error=RuntimeError("offline"))

    analytics.save_rag_trace_summary("", {})
    analytics.save_rag_trace_summary("trace", {})

    assert fake.connection.calls == []


def test_observability_shapes_all_dashboard_sections(monkeypatch):
    _install(
        monkeypatch,
        (
            Result(row=(4, 1.23456, 125.9, 1)),
            Result(rows=(("Technical", 3, 100, 25, 1.2, 120.7),)),
            Result(rows=(("2026-07-20", 4, 1.23456),)),
            Result(row=(1, 2, 3, 4, 5, 6, 7, 8)),
            Result(rows=(("insufficient_evidence", 1),)),
            Result(rows=(("pressure?", "Technical", 0.1234567, 10, 5),)),
        ),
    )

    result = analytics.get_observability(days=7)

    assert result["total_requests"] == 4
    assert result["total_cost"] == 1.2346
    assert result["avg_latency_ms"] == 125
    assert result["refusal_rate"] == 25.0
    assert result["by_department"][0]["avg_latency_ms"] == 120
    assert result["daily"] == [{"date": "2026-07-20", "requests": 4, "cost": 1.2346}]
    assert result["step_latency"] == {
        "context": 1,
        "intent": 2,
        "hyde": 3,
        "glossary": 4,
        "retrieval": 5,
        "rerank": 6,
        "gate": 7,
        "llm": 8,
    }
    assert result["refusals"] == [{"reason": "insufficient_evidence", "count": 1}]
    assert result["top_costly"][0]["cost"] == pytest.approx(0.123457)


def test_observability_preserves_safe_shape_on_database_failure(monkeypatch):
    _install(monkeypatch, connect_error=RuntimeError("offline"))

    result = analytics.get_observability(days=30)

    assert result == {
        "total_requests": 0,
        "total_cost": 0.0,
        "avg_latency_ms": 0,
        "refusal_rate": 0.0,
        "by_department": [],
        "daily": [],
        "step_latency": {},
        "refusals": [],
        "top_costly": [],
    }
