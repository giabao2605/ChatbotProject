"""Characterization tests for the ingestion-jobs repository boundary.

The SQL engine and audit writer are system boundaries.  These tests keep those
boundaries fake and assert returned contracts plus externally visible calls.
"""

from __future__ import annotations

import json

import pytest

from mech_chatbot.db.repositories import jobs


pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, *, one=None, scalar=None, rowcount=0):
        self._one = one
        self._scalar = scalar
        self.rowcount = rowcount

    def fetchone(self):
        return self._one

    def scalar(self):
        return self._scalar


class _Connection:
    def __init__(self, script=()):
        self._script = list(script)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        if not self._script:
            return _Result()
        result = self._script.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class _Engine:
    def __init__(self, script=()):
        self.connection = _Connection(script)

    def connect(self):
        return self.connection

    def begin(self):
        return self.connection


@pytest.fixture(autouse=True)
def _isolated_jobs(monkeypatch):
    monkeypatch.setattr(jobs, "_ensure_engine", lambda: None)


@pytest.mark.parametrize(
    "department_row",
    [
        (1, "disabled"),
        (1, "ARCHIVED"),
        (0, "legacy"),
    ],
)
def test_create_ingestion_job_blocks_unavailable_department(monkeypatch, department_row):
    engine = _Engine([_Result(one=department_row)])
    monkeypatch.setattr(jobs, "engine", engine)
    audits = []
    monkeypatch.setattr(jobs._r_audit, "write_audit_log", lambda *args: audits.append(args))

    assert jobs.create_ingestion_job("manual.pdf", "/in/manual.pdf", "ENG") is None
    assert len(engine.connection.calls) == 1
    assert audits == []


def test_create_ingestion_job_persists_normalized_metadata_and_audits(monkeypatch):
    engine = _Engine([_Result(one=(1, "active")), _Result(one=(73,))])
    monkeypatch.setattr(jobs, "engine", engine)
    audits = []
    monkeypatch.setattr(jobs._r_audit, "write_audit_log", lambda *args: audits.append(args))

    job_id = jobs.create_ingestion_job(
        "manual.pdf",
        "/in/manual.pdf",
        "ENG",
        uploaded_by="bao",
        domain="mechanical",
        security_level="internal",
        cong_doan="assembly",
        site="HCM",
        phong_ban=["ENG", " ", "OPS"],
        upload_meta={"title": "Huong dan"},
    )

    assert job_id == 73
    insert_params = engine.connection.calls[1][1]
    assert insert_params == {
        "f": "manual.pdf",
        "p": "/in/manual.pdf",
        "t": "ENG",
        "u": "bao",
        "dom": "mechanical",
        "sec": "internal",
        "pb": "ENG,OPS",
        "cd": "assembly",
        "site": "HCM",
        "upload_meta_json": json.dumps({"title": "Huong dan"}, ensure_ascii=False),
    }
    assert audits == [
        (
            "bao",
            "upload",
            "IngestionJobs",
            73,
            {
                "file_name": "manual.pdf",
                "thu_muc": "ENG",
                "domain": "mechanical",
                "security_level": "internal",
                "cong_doan": "assembly",
                "site": "HCM",
                "phong_ban": ["ENG", " ", "OPS"],
            },
        )
    ]


def test_create_ingestion_job_defaults_department_and_skips_audit_without_id(monkeypatch):
    engine = _Engine([_Result(one=(0, None)), _Result(one=None)])
    monkeypatch.setattr(jobs, "engine", engine)
    audits = []
    monkeypatch.setattr(jobs._r_audit, "write_audit_log", lambda *args: audits.append(args))

    assert jobs.create_ingestion_job("a.pdf", "/a.pdf", "ENG") is None
    assert engine.connection.calls[1][1]["pb"] == "ENG"
    assert engine.connection.calls[1][1]["upload_meta_json"] is None
    assert audits == []


@pytest.mark.parametrize(
    "script",
    [
        [RuntimeError("guard unavailable")],
        [_Result(one=None), RuntimeError("insert failed")],
    ],
)
def test_create_ingestion_job_fails_closed_for_database_errors(monkeypatch, script):
    monkeypatch.setattr(jobs, "engine", _Engine(script))
    monkeypatch.setattr(jobs._r_audit, "write_audit_log", lambda *_args: None)

    assert jobs.create_ingestion_job("a.pdf", "/a.pdf", "ENG") is None


def test_update_ingestion_job_sends_status_and_error(monkeypatch):
    engine = _Engine([_Result()])
    monkeypatch.setattr(jobs, "engine", engine)

    assert jobs.update_ingestion_job(7, "failed", "invalid") is None
    assert engine.connection.calls[0][1] == {"s": "failed", "e": "invalid", "id": 7}


def test_update_ingestion_job_swallows_database_failure(monkeypatch):
    monkeypatch.setattr(jobs, "engine", _Engine([RuntimeError("offline")]))
    assert jobs.update_ingestion_job(7, "failed") is None


def test_update_ingestion_report_serializes_quality_contract(monkeypatch):
    engine = _Engine([_Result()])
    monkeypatch.setattr(jobs, "engine", engine)
    report = {"quality_score": 0.92, "quality_status": "passed", "note": "đạt"}

    assert jobs.update_ingestion_report(11, report) is True
    assert engine.connection.calls[0][1] == {
        "id": 11,
        "report": json.dumps(report, ensure_ascii=False),
        "score": 0.92,
        "status": "passed",
    }


def test_update_ingestion_report_fails_closed(monkeypatch):
    monkeypatch.setattr(jobs, "engine", _Engine([RuntimeError("offline")]))
    assert jobs.update_ingestion_report(11, {}) is False


def test_get_pending_job_maps_atomic_pick(monkeypatch):
    row = (12, "a.pdf", "/a.pdf", "ENG", "mechanical", "internal", "ENG", "cut", "HCM")
    engine = _Engine([_Result(one=row)])
    monkeypatch.setattr(jobs, "engine", engine)

    assert jobs.get_pending_job("worker-2") == {
        "job_id": 12,
        "ten_file": "a.pdf",
        "file_path": "/a.pdf",
        "thu_muc": "ENG",
        "domain": "mechanical",
        "security_level": "internal",
        "phong_ban": "ENG",
        "cong_doan": "cut",
        "site": "HCM",
    }
    assert engine.connection.calls[0][1] == {"worker_id": "worker-2"}


@pytest.mark.parametrize("script", [[_Result(one=None)], [RuntimeError("offline")]])
def test_get_pending_job_returns_none_when_empty_or_unavailable(monkeypatch, script):
    monkeypatch.setattr(jobs, "engine", _Engine(script))
    assert jobs.get_pending_job() is None


@pytest.mark.parametrize(
    "message",
    [
        "[QUOTA_EXCEEDED] try later",
        "Quota Exceeded",
        "RESOURCE_EXHAUSTED",
        "free_tier_requests exhausted",
    ],
)
def test_mark_job_failed_routes_quota_failures(monkeypatch, message):
    calls = []
    monkeypatch.setattr(
        jobs,
        "mark_job_waiting_quota",
        lambda job_id, error: calls.append((job_id, error)) or "waiting",
    )

    assert jobs.mark_job_failed(17, message) == "waiting"
    assert calls == [(17, message)]


def test_mark_job_failed_records_retryable_failure(monkeypatch):
    engine = _Engine([_Result()])
    monkeypatch.setattr(jobs, "engine", engine)

    assert jobs.mark_job_failed(17, "extract failed") is None
    assert engine.connection.calls[0][1] == {"id": 17, "e": "extract failed"}


def test_mark_job_failed_swallows_database_failure(monkeypatch):
    monkeypatch.setattr(jobs, "engine", _Engine([RuntimeError("offline")]))
    assert jobs.mark_job_failed(17, "extract failed") is None


def test_mark_job_waiting_quota_persists_retry_window(monkeypatch):
    engine = _Engine([_Result()])
    monkeypatch.setattr(jobs, "engine", engine)

    assert jobs.mark_job_waiting_quota(17, "quota", retry_after_hours=6) is None
    assert engine.connection.calls[0][1] == {"id": 17, "e": "quota", "h": 6}


def test_mark_job_waiting_quota_swallows_database_failure(monkeypatch):
    monkeypatch.setattr(jobs, "engine", _Engine([RuntimeError("offline")]))
    assert jobs.mark_job_waiting_quota(17, "quota") is None


def test_set_job_priority_converts_value_and_fails_closed(monkeypatch):
    engine = _Engine([_Result()])
    monkeypatch.setattr(jobs, "engine", engine)
    assert jobs.set_job_priority(5, "10") is True
    assert engine.connection.calls[0][1] == {"p": 10, "id": 5}

    monkeypatch.setattr(jobs, "engine", _Engine([RuntimeError("offline")]))
    assert jobs.set_job_priority(5, 20) is False
    assert jobs.set_job_priority(5, "urgent") is False


@pytest.mark.parametrize(("rowcount", "expected"), [(1, True), (0, False)])
def test_cancel_job_reports_whether_job_changed(monkeypatch, rowcount, expected):
    engine = _Engine([_Result(rowcount=rowcount)])
    monkeypatch.setattr(jobs, "engine", engine)

    assert jobs.cancel_job(6, "admin") is expected
    assert engine.connection.calls[0][1] == {"by": "admin", "id": 6}


def test_cancel_job_fails_closed(monkeypatch):
    monkeypatch.setattr(jobs, "engine", _Engine([RuntimeError("offline")]))
    assert jobs.cancel_job(6) is False


def test_requeue_job_resets_job_and_fails_closed(monkeypatch):
    engine = _Engine([_Result()])
    monkeypatch.setattr(jobs, "engine", engine)
    assert jobs.requeue_job(8) is True
    assert engine.connection.calls[0][1] == {"id": 8}

    monkeypatch.setattr(jobs, "engine", _Engine([RuntimeError("offline")]))
    assert jobs.requeue_job(8) is False


def test_queue_eta_seconds_uses_recent_average(monkeypatch):
    engine = _Engine([_Result(scalar=3), _Result(scalar=12.75)])
    monkeypatch.setattr(jobs, "engine", engine)

    assert jobs.queue_eta_seconds() == {
        "pending": 3,
        "avg_seconds": 12.8,
        "eta_seconds": 38,
    }


@pytest.mark.parametrize(("pending", "average"), [(None, None), (2, 0)])
def test_queue_eta_seconds_uses_safe_defaults(monkeypatch, pending, average):
    monkeypatch.setattr(
        jobs,
        "engine",
        _Engine([_Result(scalar=pending), _Result(scalar=average)]),
    )
    assert jobs.queue_eta_seconds() == {
        "pending": int(pending or 0),
        "avg_seconds": 90.0,
        "eta_seconds": int((pending or 0) * 90),
    }


def test_queue_eta_seconds_fails_closed(monkeypatch):
    monkeypatch.setattr(jobs, "engine", _Engine([RuntimeError("offline")]))
    assert jobs.queue_eta_seconds() == {"pending": 0, "avg_seconds": 0, "eta_seconds": 0}
