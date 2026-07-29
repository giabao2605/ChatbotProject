"""Characterization tests for the catalog repository public boundary.

The SQL engine, Qdrant metadata writer, semantic cache and audit logger are
system boundaries.  These tests keep those boundaries fake and assert the
repository's returned contracts and externally visible collaborator calls.
"""

from __future__ import annotations

import time

import pytest

from mech_chatbot.db.repositories import catalog


pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, *, one=None, rows=None):
        self._one = one
        self._rows = [] if rows is None else rows

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._rows)


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
def _isolated_catalog(monkeypatch):
    monkeypatch.setenv("CATALOG_CACHE_TTL", "60")
    monkeypatch.setattr(catalog, "_CATALOG_CACHE_TTL", 60.0)
    monkeypatch.setattr(catalog, "_ensure_engine", lambda: None)
    catalog._catalog_cache.clear()
    yield
    catalog._catalog_cache.clear()


def test_resolve_site_uses_registry_mapping_with_database_site(monkeypatch):
    engine = _Engine([_Result(one=("HCM",))])
    seen = []
    monkeypatch.setattr(catalog, "engine", engine)
    monkeypatch.setattr(
        "mech_chatbot.db.registry_ports.resolve_site_by_department",
        lambda department, db_site=None: seen.append((department, db_site)) or db_site,
    )

    assert catalog._resolve_site("Engineering") == "HCM"
    assert seen == [("Engineering", "HCM")]


def test_resolve_site_allows_registry_fallback_when_database_is_unavailable(monkeypatch):
    monkeypatch.setattr(catalog, "engine", _Engine([RuntimeError("offline")]))
    monkeypatch.setattr(
        "mech_chatbot.db.registry_ports.resolve_site_by_department",
        lambda department, db_site=None: "HQ" if db_site is None else db_site,
    )

    assert catalog._resolve_site("Engineering") == "HQ"


def test_resolve_site_fails_closed_when_registry_mapping_fails(monkeypatch):
    monkeypatch.setattr(catalog, "engine", _Engine([_Result(one=None)]))

    def _fail_mapping(*_args, **_kwargs):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(
        "mech_chatbot.db.registry_ports.resolve_site_by_department",
        _fail_mapping,
    )

    assert catalog._resolve_site("Engineering") == "HQ"


@pytest.mark.parametrize(
    ("status", "is_active", "expected"),
    [
        (" ACTIVE ", False, "active"),
        ("disabled", True, "disabled"),
        ("archived", True, "archived"),
        ("unknown", True, "active"),
        (None, False, "disabled"),
    ],
)
def test_department_status_normalization(status, is_active, expected):
    assert catalog._normalize_department_status(status, is_active) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, []),
        ("A, B,A", ["A", "B"]),
        (("A", "B,C", ""), ["A", "B", "C"]),
    ],
)
def test_split_csv_tokens_is_ordered_and_deduplicated(value, expected):
    assert catalog._split_csv_tokens(value) == expected


@pytest.mark.parametrize(
    ("value", "old_code", "new_code", "expected"),
    [
        ("A,B,A", "A", "C", "C,B"),
        ("A,B", "A", None, "B"),
        ("A", "A", "", None),
        ("A,B", "X", "C", "A,B"),
    ],
)
def test_replace_department_token_list_preserves_order_without_duplicates(
    value, old_code, new_code, expected
):
    assert catalog._replace_department_token_list(value, old_code, new_code) == expected


def test_catalog_cache_returns_only_fresh_values_and_supports_scoped_invalidation(monkeypatch):
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    catalog._catalog_cache_put("depts:True", ("Engineering",))
    catalog._catalog_cache_put("sites:True", ("HQ",))

    assert catalog._catalog_cache_get("depts:True") == ("Engineering",)

    monkeypatch.setattr(time, "time", lambda: now + 61)
    assert catalog._catalog_cache_get("depts:True") is None

    catalog._catalog_cache_invalidate("depts:")
    assert "depts:True" not in catalog._catalog_cache
    assert "sites:True" in catalog._catalog_cache
    catalog._catalog_cache_invalidate()
    assert catalog._catalog_cache == {}


@pytest.mark.parametrize(("row", "expected"), [((1,), True), (None, False)])
def test_department_schema_capability_is_reported(monkeypatch, row, expected):
    monkeypatch.setattr(catalog, "engine", _Engine([_Result(one=row)]))
    assert catalog._departments_support_status() is expected


def test_department_schema_capability_fails_closed(monkeypatch):
    monkeypatch.setattr(catalog, "engine", _Engine([RuntimeError("offline")]))
    assert catalog._departments_support_status() is False


def test_list_known_departments_maps_status_rows_and_returns_a_copy(monkeypatch):
    rows = [
        ("ENG", "Engineering", "mechanical", "HCM", 1, "ACTIVE", None, None),
        ("OLD", "Legacy", None, "HQ", 1, "archived", None, "2026-01-01"),
        ("OPS", "Operations", None, "HQ", 0, None, "2026-01-02", None),
    ]
    monkeypatch.setattr(catalog, "_departments_support_status", lambda: True)
    monkeypatch.setattr(catalog, "engine", _Engine([_Result(rows=rows)]))

    result = catalog.list_known_departments(active_only=False)

    assert result == [
        {
            "code": "ENG",
            "name": "Engineering",
            "domain": "mechanical",
            "site": "HCM",
            "is_active": True,
            "status": "active",
            "disabled_at": None,
            "archived_at": None,
        },
        {
            "code": "OLD",
            "name": "Legacy",
            "domain": None,
            "site": "HQ",
            "is_active": False,
            "status": "archived",
            "disabled_at": None,
            "archived_at": "2026-01-01",
        },
        {
            "code": "OPS",
            "name": "Operations",
            "domain": None,
            "site": "HQ",
            "is_active": False,
            "status": "disabled",
            "disabled_at": "2026-01-02",
            "archived_at": None,
        },
    ]
    result.append({"code": "LOCAL"})
    assert [item["code"] for item in catalog.list_known_departments(False)] == ["ENG", "OLD", "OPS"]


def test_list_known_departments_supports_legacy_schema_and_database_failure(monkeypatch):
    monkeypatch.setattr(catalog, "_departments_support_status", lambda: False)
    monkeypatch.setattr(
        catalog,
        "engine",
        _Engine([_Result(rows=[("OPS", "Operations", None, "HQ", 1, "active", None, None)])]),
    )
    assert catalog.list_known_departments()[-1]["status"] == "active"

    catalog._catalog_cache.clear()
    monkeypatch.setattr(catalog, "engine", _Engine([RuntimeError("offline")]))
    assert catalog.list_known_departments() == []


def test_upsert_department_validates_code_and_persists_normalized_status(monkeypatch):
    assert catalog.upsert_department("") is False

    monkeypatch.setattr(catalog, "_departments_support_status", lambda: True)
    engine = _Engine([_Result()])
    monkeypatch.setattr(catalog, "engine", engine)
    catalog._catalog_cache_put("depts:True", ["stale"])

    assert catalog.upsert_department("ENG", "Engineering", status="disabled") is True
    assert engine.connection.calls[-1][1] == {
        "c": "ENG",
        "n": "Engineering",
        "d": None,
        "site": None,
        "a": 0,
        "st": "disabled",
    }
    assert catalog._catalog_cache == {}


def test_upsert_department_supports_legacy_schema_and_fails_closed(monkeypatch):
    monkeypatch.setattr(catalog, "_departments_support_status", lambda: False)
    engine = _Engine([_Result()])
    monkeypatch.setattr(catalog, "engine", engine)
    assert catalog.upsert_department("ENG", is_active=True) is True
    assert engine.connection.calls[-1][1]["a"] == 1

    monkeypatch.setattr(catalog, "engine", _Engine([RuntimeError("offline")]))
    assert catalog.upsert_department("ENG") is False


def test_get_department_summary_maps_counts_and_status(monkeypatch):
    script = [
        _Result(one=("ENG", "Engineering", "mechanical", "HCM", 1, "active", None, None)),
        _Result(one=(2,)),
        _Result(one=(3,)),
        _Result(one=(4,)),
        _Result(one=(5,)),
    ]
    monkeypatch.setattr(catalog, "_departments_support_status", lambda: True)
    monkeypatch.setattr(catalog, "engine", _Engine(script))

    assert catalog.get_department_summary("ENG") == {
        "code": "ENG",
        "name": "Engineering",
        "domain": "mechanical",
        "site": "HCM",
        "is_active": True,
        "status": "active",
        "disabled_at": None,
        "archived_at": None,
        "users": 2,
        "pending_jobs": 3,
        "docs": 4,
        "shared_docs": 5,
    }


def test_get_department_summary_handles_legacy_missing_and_database_failure(monkeypatch):
    assert catalog.get_department_summary("") is None
    monkeypatch.setattr(catalog, "_departments_support_status", lambda: False)
    monkeypatch.setattr(catalog, "engine", _Engine([_Result(one=None)]))
    assert catalog.get_department_summary("MISSING") is None

    monkeypatch.setattr(catalog, "engine", _Engine([RuntimeError("offline")]))
    assert catalog.get_department_summary("ENG") is None


def _summary(status="active", *, users=0, pending_jobs=0):
    return {
        "code": "ENG",
        "name": "Engineering",
        "domain": "mechanical",
        "site": "HCM",
        "is_active": status == "active",
        "status": status,
        "users": users,
        "pending_jobs": pending_jobs,
    }


def test_set_department_status_rejects_missing_archived_and_in_use_departments(monkeypatch):
    monkeypatch.setattr(catalog, "get_department_summary", lambda _code: None)
    assert catalog.set_department_status("UNKNOWN", "disabled")["ok"] is False

    monkeypatch.setattr(catalog, "get_department_summary", lambda _code: _summary("archived"))
    assert catalog.set_department_status("ENG", "active")["ok"] is False

    monkeypatch.setattr(
        catalog,
        "get_department_summary",
        lambda _code: _summary("active", users=2, pending_jobs=1),
    )
    blocked = catalog.archive_department("ENG")
    assert blocked["ok"] is False
    assert blocked["summary"]["users"] == 2


def test_set_department_status_persists_audits_and_returns_fresh_summary(monkeypatch):
    summaries = iter([_summary("active"), _summary("disabled")])
    monkeypatch.setattr(catalog, "get_department_summary", lambda _code: next(summaries))
    monkeypatch.setattr(catalog, "upsert_department", lambda *_args, **_kwargs: True)
    audits = []
    monkeypatch.setattr(catalog._r_audit, "write_audit_log", lambda *args: audits.append(args))

    result = catalog.set_department_status("ENG", "disabled", actor="admin")

    assert result == {"ok": True, "status": "disabled", "summary": _summary("disabled")}
    assert audits[0][0:3] == ("admin", "department_status", "Departments")
    assert audits[0][-1] == {"code": "ENG", "from": "active", "to": "disabled", "force": False}


def test_set_department_status_reports_persistence_failure(monkeypatch):
    monkeypatch.setattr(catalog, "get_department_summary", lambda _code: _summary())
    monkeypatch.setattr(catalog, "upsert_department", lambda *_args, **_kwargs: False)
    assert catalog.set_department_status("ENG", "disabled")["ok"] is False


def test_archive_department_allows_empty_department_without_force(monkeypatch):
    summaries = iter([_summary("active"), _summary("archived")])
    monkeypatch.setattr(catalog, "get_department_summary", lambda _code: next(summaries))
    monkeypatch.setattr(catalog, "upsert_department", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(catalog._r_audit, "write_audit_log", lambda *_args: None)

    assert catalog.archive_department("ENG")["status"] == "archived"


@pytest.mark.parametrize(
    ("source", "target", "message"),
    [
        ("", "ENG", "bat buoc"),
        (" ENG ", "ENG", "cung 1"),
    ],
)
def test_reassign_department_data_validates_codes(source, target, message):
    result = catalog.reassign_department_data(source, target)
    assert result["ok"] is False
    assert message in result["message"]


def test_reassign_department_data_rejects_missing_or_invalid_lifecycle(monkeypatch):
    monkeypatch.setattr(catalog, "get_department_summary", lambda _code: None)
    assert catalog.reassign_department_data("OLD", "ENG")["ok"] is False

    summaries = {"OLD": _summary("active"), "ENG": _summary("disabled")}
    monkeypatch.setattr(catalog, "get_department_summary", summaries.get)
    assert "active" in catalog.reassign_department_data("OLD", "ENG")["message"]

    summaries = {"OLD": _summary("archived"), "ENG": _summary("active")}
    monkeypatch.setattr(catalog, "get_department_summary", summaries.get)
    assert "archived" in catalog.reassign_department_data("OLD", "ENG")["message"]


def test_reassign_department_data_moves_sql_rbac_and_reports_qdrant_failures(monkeypatch):
    summaries = {"OLD": _summary("active"), "ENG": _summary("active")}
    monkeypatch.setattr(catalog, "get_department_summary", summaries.get)
    script = [
        _Result(rows=[(11, "OLD")]),
        _Result(),
        _Result(),
        _Result(),
        _Result(),
        _Result(rows=[("ENG",), ("SHARED",)]),
        _Result(rows=[(21, "OLD", "OLD,SHARED")]),
        _Result(),
        _Result(rows=[(7,), (8,)]),
        _Result(),
        _Result(one=None),
        _Result(),
        _Result(one=(1,)),
        _Result(),
    ]
    engine = _Engine(script)
    monkeypatch.setattr(catalog, "engine", engine)
    qdrant_calls = []
    monkeypatch.setattr(
        catalog._r_qdrant,
        "update_qdrant_metadata",
        lambda doc_id, payload: qdrant_calls.append((doc_id, payload)) or False,
    )
    monkeypatch.setattr(
        catalog,
        "set_department_status",
        lambda *args, **kwargs: {"ok": True, "status": "disabled"},
    )
    audits = []
    cache_invalidations = []
    monkeypatch.setattr(catalog._r_audit, "write_audit_log", lambda *args: audits.append(args))
    monkeypatch.setattr(
        catalog._r_semantic_cache,
        "_invalidate_semantic_cache",
        lambda reason: cache_invalidations.append(reason),
    )

    result = catalog.reassign_department_data(" OLD ", " ENG ", actor="admin")

    assert result == {
        "ok": True,
        "source": "OLD",
        "target": "ENG",
        "moved_docs": 1,
        "moved_users": 2,
        "qdrant_failures": [11],
        "status_result": {"ok": True, "status": "disabled"},
    }
    assert qdrant_calls == [
        (11, {"phong_ban_quyen": ["ENG", "SHARED"], "department": "ENG"})
    ]
    calls = engine.connection.calls
    assert any(
        "UPDATE dbo.TaiLieu SET ThuMuc" in statement
        and params == {"t": "ENG", "id": 11}
        for statement, params in calls
    )
    assert any(
        "UPDATE dbo.IngestionJobs SET ThuMuc" in statement
        and params == {"t": "ENG", "pb": "ENG,SHARED", "id": 21}
        for statement, params in calls
    )
    assert any(
        "DELETE FROM dbo.UserDepartments" in statement
        and params == {"src": "OLD"}
        for statement, params in calls
    )
    assert any(
        "UPDATE dbo.Users SET Department" in statement
        and params == {"dst": "ENG", "src": "OLD"}
        for statement, params in calls
    )
    assert audits[-1][0:3] == ("admin", "department_reassign", "Departments")
    assert cache_invalidations == ["dept.reassign"]


def test_reassign_department_data_supports_no_user_move_and_reports_transaction_failure(monkeypatch):
    summaries = {"OLD": _summary("active"), "ENG": _summary("active")}
    monkeypatch.setattr(catalog, "get_department_summary", summaries.get)
    script = [
        _Result(rows=[(12, "SHARED")]),
        _Result(),
        _Result(),
        _Result(),
        _Result(rows=[]),
        _Result(rows=[(22, "SHARED", None)]),
        _Result(),
    ]
    monkeypatch.setattr(catalog, "engine", _Engine(script))
    qdrant_calls = []
    monkeypatch.setattr(
        catalog._r_qdrant,
        "update_qdrant_metadata",
        lambda doc_id, payload: qdrant_calls.append((doc_id, payload)) or True,
    )
    monkeypatch.setattr(catalog, "set_department_status", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(catalog._r_audit, "write_audit_log", lambda *_args: None)
    monkeypatch.setattr(catalog._r_semantic_cache, "_invalidate_semantic_cache", lambda *_args: None)
    result = catalog.reassign_department_data("OLD", "ENG", move_users=False)
    assert result["ok"] is True
    assert result["moved_users"] == 0
    assert result["qdrant_failures"] == []
    assert qdrant_calls == [(12, {"phong_ban_quyen": [], "department": "ENG"})]

    monkeypatch.setattr(catalog, "engine", _Engine([RuntimeError("transaction failed")]))
    result = catalog.reassign_department_data("OLD", "ENG")
    assert result == {"ok": False, "message": "transaction failed"}


def test_list_known_sites_maps_rows_uses_cache_and_fails_closed(monkeypatch):
    monkeypatch.setattr(
        catalog,
        "engine",
        _Engine([_Result(rows=[("HQ", "Headquarters", 1), ("OLD", "Legacy", 0)])]),
    )
    result = catalog.list_known_sites(active_only=False)
    assert result == [
        {"code": "HQ", "name": "Headquarters", "is_active": True},
        {"code": "OLD", "name": "Legacy", "is_active": False},
    ]
    result.clear()
    assert len(catalog.list_known_sites(False)) == 2

    catalog._catalog_cache.clear()
    monkeypatch.setattr(catalog, "engine", _Engine([RuntimeError("offline")]))
    assert catalog.list_known_sites() == []


def test_upsert_site_validates_mutates_and_invalidates_cache(monkeypatch):
    assert catalog.upsert_site("") is False
    engine = _Engine([_Result()])
    monkeypatch.setattr(catalog, "engine", engine)
    catalog._catalog_cache_put("sites:True", ["stale"])
    assert catalog.upsert_site("HCM", "Ho Chi Minh", is_active=False) is True
    assert engine.connection.calls[-1][1] == {"c": "HCM", "n": "Ho Chi Minh", "a": 0}
    assert catalog._catalog_cache == {}

    monkeypatch.setattr(catalog, "engine", _Engine([RuntimeError("offline")]))
    assert catalog.upsert_site("HCM") is False


def test_get_user_sites_returns_rows_and_fails_closed(monkeypatch):
    monkeypatch.setattr(catalog, "engine", _Engine([_Result(rows=[("HQ",), ("HCM",)])]))
    assert catalog.get_user_sites(7) == ["HQ", "HCM"]
    monkeypatch.setattr(catalog, "engine", _Engine([RuntimeError("offline")]))
    assert catalog.get_user_sites(7) == []


@pytest.mark.parametrize(
    ("method_name", "values", "reason"),
    [
        ("set_user_sites", ["HQ", "", "HCM"], "user.sites"),
        ("set_user_departments", ["ENG", None, "OPS"], "user.departments"),
    ],
)
def test_user_scope_replacement_filters_blanks_and_invalidates_cache(
    monkeypatch, method_name, values, reason
):
    engine = _Engine([_Result(), _Result()])
    monkeypatch.setattr(catalog, "engine", engine)
    invalidations = []
    monkeypatch.setattr(
        catalog._r_semantic_cache,
        "_invalidate_semantic_cache",
        lambda value: invalidations.append(value),
    )

    assert getattr(catalog, method_name)(7, values) is True
    assert len(engine.connection.calls[-1][1]) == 2
    assert invalidations == [reason]


@pytest.mark.parametrize("method_name", ["set_user_sites", "set_user_departments"])
def test_user_scope_replacement_handles_empty_and_database_failure(monkeypatch, method_name):
    monkeypatch.setattr(catalog._r_semantic_cache, "_invalidate_semantic_cache", lambda *_args: None)
    engine = _Engine([_Result()])
    monkeypatch.setattr(catalog, "engine", engine)
    assert getattr(catalog, method_name)(7, []) is True
    assert len(engine.connection.calls) == 1

    monkeypatch.setattr(catalog, "engine", _Engine([RuntimeError("offline")]))
    assert getattr(catalog, method_name)(7, ["ENG"]) is False


def test_set_user_clearance_validates_mutates_and_fails_closed(monkeypatch):
    assert catalog.set_user_clearance(7, "secret") is False

    engine = _Engine([_Result()])
    monkeypatch.setattr(catalog, "engine", engine)
    invalidations = []
    monkeypatch.setattr(
        catalog._r_semantic_cache,
        "_invalidate_semantic_cache",
        lambda reason: invalidations.append(reason),
    )
    assert catalog.set_user_clearance(7, "confidential") is True
    assert engine.connection.calls[-1][1] == {"uid": 7, "lvl": "confidential"}
    assert invalidations == ["user.clearance_set"]

    monkeypatch.setattr(catalog, "engine", _Engine([RuntimeError("offline")]))
    assert catalog.set_user_clearance(7, "public") is False
