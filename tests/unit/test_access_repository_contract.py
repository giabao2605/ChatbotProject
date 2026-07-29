from __future__ import annotations

from contextlib import contextmanager

import pytest

from mech_chatbot.db.repositories import access


pytestmark = [pytest.mark.unit, pytest.mark.security]


class _Result:
    def __init__(self, *, row=None, rows=(), scalar_value=None, rowcount=0):
        self._row = row
        self._rows = list(rows)
        self._scalar_value = scalar_value
        self.rowcount = rowcount

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar_value


class _Engine:
    def __init__(self, results=()):
        self.results = list(results)
        self.calls = []

    @contextmanager
    def connect(self):
        yield self

    @contextmanager
    def begin(self):
        yield self

    def execute(self, statement, params=None):
        query = str(statement)
        bound = dict(params or {})
        self.calls.append((query, bound))
        result = self.results.pop(0) if self.results else _Result()
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.fixture(autouse=True)
def _fixed_repository_boundaries(monkeypatch):
    monkeypatch.setattr(access, "_ensure_engine", lambda: None)


def _install(monkeypatch, *results):
    engine = _Engine(results)
    monkeypatch.setattr(access, "engine", engine)
    return engine


def test_create_access_request_deduplicates_pending_request(monkeypatch):
    engine = _install(monkeypatch, _Result(row=(41,)))

    result = access.create_access_request(
        7,
        "alice",
        "security",
        requested_level="confidential",
        question_text="why",
    )

    assert result == {"request_id": 41, "created": False}
    assert len(engine.calls) == 1
    assert engine.calls[0][1] == {
        "uid": 7,
        "rt": "security",
        "lvl": "confidential",
        "dept": None,
    }


def test_create_access_request_persists_capped_payload_and_audits(monkeypatch):
    engine = _install(monkeypatch, _Result(), _Result(row=(42,)))
    audits = []
    monkeypatch.setattr(access._r_audit, "write_audit_log", lambda **kwargs: audits.append(kwargs))

    result = access.create_access_request(
        7,
        "alice",
        "department",
        requested_dept="Technical",
        question_text="q" * 5000,
        reason="r" * 3000,
    )

    assert result == {"request_id": 42, "created": True}
    insert_query, params = engine.calls[1]
    assert "INSERT INTO dbo.AccessRequests" in insert_query
    assert len(params["q"]) == 4000
    assert len(params["reason"]) == 2000
    assert audits[0]["entity_id"] == 42
    assert audits[0]["details"] == {
        "request_type": "department",
        "level": None,
        "dept": "Technical",
    }


def test_create_access_request_fails_closed_for_database_error(monkeypatch):
    _install(monkeypatch, RuntimeError("database unavailable"))

    assert access.create_access_request(7, "alice", "security") is None


def test_create_access_request_remains_successful_when_audit_is_unavailable(monkeypatch):
    _install(monkeypatch, _Result(), _Result(row=(42,)))
    monkeypatch.setattr(
        access._r_audit,
        "write_audit_log",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
    )

    assert access.create_access_request(7, "alice", "security") == {
        "request_id": 42,
        "created": True,
    }


def test_access_request_reads_map_rows_and_apply_filters(monkeypatch):
    request_row = (1, 7, "alice", "security", "internal", None, "q", "r", "pending", None, None, None, "now")
    user_row = (1, "security", "internal", None, "q", "approved", "reviewer", "ok", "then", "now")
    engine = _install(
        monkeypatch,
        _Result(rows=[request_row]),
        _Result(rows=[request_row]),
        _Result(rows=[user_row]),
        _Result(scalar_value=3),
    )

    assert access.list_access_requests()[0]["request_id"] == 1
    assert engine.calls[0][1] == {"st": "pending"}
    assert access.list_access_requests(status="all", limit=10)[0]["status"] == "pending"
    assert "WHERE Status = :st" not in engine.calls[1][0]
    assert engine.calls[1][1] == {}
    assert access.get_user_access_requests(7)[0]["reviewer_username"] == "reviewer"
    assert engine.calls[2][1] == {"uid": 7}
    assert access.count_pending_access_requests() == 3


def test_access_request_reads_fail_closed_for_database_errors(monkeypatch):
    engine = _install(
        monkeypatch,
        RuntimeError("list"),
        RuntimeError("user"),
        RuntimeError("count"),
        RuntimeError("history"),
    )

    assert access.list_access_requests() == []
    assert access.get_user_access_requests(7) == []
    assert access.count_pending_access_requests() == 0
    assert access.get_grant_history() == []
    assert len(engine.calls) == 4


@pytest.mark.parametrize("decision", ["approve", "", None])
def test_resolve_access_request_rejects_invalid_decision_without_db(monkeypatch, decision):
    engine = _install(monkeypatch)

    assert access.resolve_access_request(1, decision, "reviewer") == {
        "ok": False,
        "message": "decision khong hop le",
    }
    assert engine.calls == []


def test_resolve_access_request_rejects_missing_or_completed_request(monkeypatch):
    engine = _install(
        monkeypatch,
        _Result(row=None),
        _Result(row=(7, "alice", "security", "internal", None, "approved")),
    )

    assert access.resolve_access_request(1, "approved", "reviewer")["message"] == "khong tim thay yeu cau"
    assert access.resolve_access_request(2, "rejected", "reviewer")["message"] == "yeu cau da duoc xu ly"
    assert len(engine.calls) == 2


def test_resolve_security_request_applies_clearance_updates_request_and_invalidates_cache(monkeypatch):
    engine = _install(
        monkeypatch,
        _Result(row=(7, "alice", "security", "confidential", None, "pending")),
        _Result(),
        _Result(),
    )
    audits = []
    invalidations = []
    monkeypatch.setattr(access._r_audit, "write_audit_log", lambda **kwargs: audits.append(kwargs))
    monkeypatch.setattr(access._r_semantic_cache, "_invalidate_semantic_cache", invalidations.append)

    result = access.resolve_access_request(5, "approved", "reviewer", reviewer_id=9, review_note="accepted")

    assert result == {"ok": True, "applied": "clearance=confidential", "message": "da xu ly"}
    assert "MERGE dbo.UserSecurityClearance" in engine.calls[1][0]
    assert engine.calls[1][1] == {"uid": 7, "lvl": "confidential"}
    assert engine.calls[2][1] == {
        "st": "approved",
        "rvid": 9,
        "rvuname": "reviewer",
        "note": "accepted",
        "rid": 5,
    }
    assert audits[0]["details"] == {"target_user": "alice", "applied": "clearance=confidential"}
    assert invalidations == ["access_request.resolve"]


@pytest.mark.parametrize("existing", [None, (1,)])
def test_resolve_department_request_adds_only_missing_membership(monkeypatch, existing):
    results = [
        _Result(row=(7, "alice", "department", None, "Technical", "pending")),
        _Result(row=existing),
    ]
    if existing is None:
        results.append(_Result())
    results.append(_Result())
    engine = _install(monkeypatch, *results)
    monkeypatch.setattr(access._r_audit, "write_audit_log", lambda **_kwargs: None)
    monkeypatch.setattr(access._r_semantic_cache, "_invalidate_semantic_cache", lambda _reason: None)

    result = access.resolve_access_request(5, "approved", "reviewer")

    assert result["applied"] == "department+Technical"
    insert_calls = [call for call in engine.calls if "INSERT INTO dbo.UserDepartments" in call[0]]
    assert len(insert_calls) == (1 if existing is None else 0)


def test_resolve_rejected_request_only_updates_status_and_database_errors_return_failure(monkeypatch):
    engine = _install(
        monkeypatch,
        _Result(row=(7, "alice", "security", "internal", None, "pending")),
        _Result(),
        RuntimeError("database unavailable"),
    )
    monkeypatch.setattr(access._r_audit, "write_audit_log", lambda **_kwargs: None)
    monkeypatch.setattr(access._r_semantic_cache, "_invalidate_semantic_cache", lambda _reason: None)

    assert access.resolve_access_request(5, "rejected", "reviewer")["ok"] is True
    assert len(engine.calls) == 2
    failed = access.resolve_access_request(6, "approved", "reviewer")
    assert failed["ok"] is False


def test_list_users_with_access_joins_clearance_and_departments(monkeypatch):
    engine = _install(
        monkeypatch,
        _Result(rows=[(7, "alice", "Alice", "Engineering", 1), (8, "bob", None, None, 0)]),
        _Result(rows=[(7, "confidential")]),
        _Result(rows=[(7, "Technical"), (7, "Quality")]),
    )

    result = access.list_users_with_access(limit=20)

    assert result == [
        {
            "user_id": 7,
            "username": "alice",
            "display_name": "Alice",
            "department": "Engineering",
            "is_active": True,
            "max_level": "confidential",
            "departments": ["Technical", "Quality"],
        },
        {
            "user_id": 8,
            "username": "bob",
            "display_name": None,
            "department": None,
            "is_active": False,
            "max_level": "public",
            "departments": [],
        },
    ]
    assert "TOP 20" in engine.calls[0][0]


def test_revoke_clearance_validates_level_and_records_previous_value(monkeypatch):
    engine = _install(monkeypatch, _Result(row=("confidential",)), _Result())
    audits = []
    invalidations = []
    monkeypatch.setattr(access._r_audit, "write_audit_log", lambda **kwargs: audits.append(kwargs))
    monkeypatch.setattr(access._r_semantic_cache, "_invalidate_semantic_cache", invalidations.append)

    assert access.revoke_user_clearance(7, "secret", "admin")["ok"] is False
    result = access.revoke_user_clearance(7, "internal", "admin", actor_id=1, reason="policy")

    assert result == {"ok": True, "from": "confidential", "to": "internal", "message": "da cap nhat"}
    assert engine.calls[1][1] == {"uid": 7, "lvl": "internal"}
    assert audits[0]["details"] == {"from": "confidential", "to": "internal", "reason": "policy"}
    assert invalidations == ["user.clearance_revoke"]


def test_revoke_department_validates_input_and_reports_removed_rows(monkeypatch):
    engine = _install(monkeypatch, _Result(rowcount=1))
    audits = []
    invalidations = []
    monkeypatch.setattr(access._r_audit, "write_audit_log", lambda **kwargs: audits.append(kwargs))
    monkeypatch.setattr(access._r_semantic_cache, "_invalidate_semantic_cache", invalidations.append)

    assert access.revoke_user_department(7, "", "admin")["ok"] is False
    result = access.revoke_user_department(7, "Technical", "admin", reason="transfer")

    assert result == {"ok": True, "removed": 1, "message": "da thu hoi"}
    assert engine.calls[0][1] == {"uid": 7, "d": "Technical"}
    assert audits[0]["details"] == {"department": "Technical", "removed": 1, "reason": "transfer"}
    assert invalidations == ["user.dept_revoke"]


@pytest.mark.parametrize(
    ("operation", "args"),
    [
        (access.list_users_with_access, ()),
        (access.revoke_user_clearance, (7, "internal", "admin")),
        (access.revoke_user_department, (7, "Technical", "admin")),
    ],
)
def test_access_management_operations_fail_closed_for_database_errors(monkeypatch, operation, args):
    _install(monkeypatch, RuntimeError("database unavailable"))

    result = operation(*args)

    if operation is access.list_users_with_access:
        assert result == []
    else:
        assert result["ok"] is False


def test_get_grant_history_maps_audit_rows(monkeypatch):
    engine = _install(monkeypatch, _Result(rows=[("now", "admin", "clearance_revoke", "User", 7, "{}")]))

    assert access.get_grant_history(limit=12) == [
        {
            "created_at": "now",
            "username": "admin",
            "action": "clearance_revoke",
            "entity_type": "User",
            "entity_id": 7,
            "details": "{}",
        }
    ]
    assert "TOP 12" in engine.calls[0][0]
    assert "access_request_approved" in engine.calls[0][0]
