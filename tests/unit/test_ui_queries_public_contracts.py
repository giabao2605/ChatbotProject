from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.unit

ui_queries = pytest.importorskip("mech_chatbot.db.repositories.ui_queries")


class FakeResult:
    def __init__(self, *, rows=(), one=None, scalar_value=None, rowcount=0, mapping=None):
        self._rows = list(rows)
        self._one = one
        self._scalar_value = scalar_value
        self.rowcount = rowcount
        self._mapping = mapping

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._one

    def scalar(self):
        return self._scalar_value

    def mappings(self):
        return self

    def one(self):
        return self._mapping


class FakeConnection:
    def __init__(self, resolver=None):
        self.resolver = resolver or (lambda _sql, _params, _index: FakeResult())
        self.calls = []

    def execute(self, statement, params=None):
        sql = str(statement)
        normalized_params = dict(params or {}) if not isinstance(params, list) else list(params)
        self.calls.append((sql, normalized_params))
        return self.resolver(sql, normalized_params, len(self.calls) - 1)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeEngine:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return self.connection

    def begin(self):
        return self.connection


def install_engine(monkeypatch, resolver=None):
    connection = FakeConnection(resolver)
    monkeypatch.setattr(ui_queries, "engine", FakeEngine(connection))
    return connection


def test_health_and_audit_filters_preserve_public_contract(monkeypatch):
    expected = [(11, "alice", "read_confidential")]

    def resolver(sql, params, _index):
        if "SELECT TOP" in sql:
            return FakeResult(rows=expected)
        return FakeResult()

    connection = install_engine(monkeypatch, resolver)

    assert ui_queries.is_engine_ready() is True
    ui_queries.ping_database()
    rows = ui_queries.list_audit_logs(
        25,
        only_confidential=True,
        username_filter="ali",
        start_dt="2026-01-01",
        end_dt="2026-01-31",
    )

    assert rows == expected
    audit_sql, audit_params = connection.calls[-1]
    assert "TOP 25" in audit_sql
    assert audit_params == {
        "action": "read_confidential",
        "username": "%ali%",
        "start_dt": "2026-01-01",
        "end_dt": "2026-01-31",
    }


def test_audit_action_filter_is_ignored_when_confidential_filter_wins(monkeypatch):
    connection = install_engine(monkeypatch, lambda *_args: FakeResult(rows=[]))

    ui_queries.list_audit_logs(5, only_confidential=True, action_filter="delete")

    _sql, params = connection.calls[-1]
    assert params == {"action": "read_confidential"}


def test_audit_action_filter_uses_partial_match(monkeypatch):
    connection = install_engine(monkeypatch, lambda *_args: FakeResult(rows=[]))

    ui_queries.list_audit_logs(5, action_filter="delete")

    assert connection.calls[-1][1] == {"action": "%delete%"}


def test_audit_filters_are_optional(monkeypatch):
    connection = install_engine(monkeypatch, lambda *_args: FakeResult(rows=[]))

    assert ui_queries.list_audit_logs(1) == []
    assert connection.calls[-1][1] == {}


def test_dashboard_stats_returns_zero_for_missing_or_failed_scalar(monkeypatch):
    values = iter([7, None, RuntimeError("missing table"), 3, 2, 4, 5])

    def resolver(_sql, _params, _index):
        value = next(values)
        if isinstance(value, Exception):
            raise value
        return FakeResult(scalar_value=value)

    install_engine(monkeypatch, resolver)

    assert ui_queries.get_dashboard_stats() == {
        "total_docs": 7,
        "pending_review": 0,
        "published_docs": 0,
        "running_jobs": 3,
        "failed_jobs": 2,
        "today_chats": 4,
        "pending_feedback": 5,
    }


def test_viewer_dashboard_only_exposes_effective_documents_and_own_usage(monkeypatch):
    scalars = iter([6, 9])
    install_engine(monkeypatch, lambda *_args: FakeResult(scalar_value=next(scalars)))
    monkeypatch.setattr(
        ui_queries,
        "get_document_lifecycle_counts",
        lambda **_kwargs: {"effective": 12, "expired": 2, "needs_review": 1},
    )

    result = ui_queries.get_role_dashboard({
        "roles": ["viewer"],
        "username": "alice",
        "allowed_departments": ["MFG"],
        "allowed_sites": ["HCM"],
        "max_security_level": "internal",
    })

    assert result == {
        "document_lifecycle": {"effective": 12},
        "usage": {"today_questions": 6, "recent_questions": 9},
    }


def test_reviewer_uploader_dashboard_applies_department_and_site_scope(monkeypatch):
    scalars = iter([2, 1, 8, 3])
    connection = install_engine(
        monkeypatch,
        lambda *_args: FakeResult(scalar_value=next(scalars)),
    )
    monkeypatch.setattr(
        ui_queries,
        "get_document_lifecycle_counts",
        lambda **_kwargs: {"effective": 4, "expired": 1},
    )

    result = ui_queries.get_role_dashboard({
        "roles": ["uploader", "reviewer"],
        "username": "reviewer-1",
        "allowed_departments": ["B", "A", "A", ""],
        "allowed_sites": ["HCM"],
        "max_security_level": "confidential",
    })

    assert result["ingestion"] == {"running": 2, "failed": 1}
    assert result["review"] == {"pending": 8, "publish_blocked": 3}
    review_params = connection.calls[-1][1]
    assert review_params["dashboard_dept_0"] == "A"
    assert review_params["dashboard_dept_1"] == "B"
    assert review_params["dashboard_site_0"] == "HCM"
    assert set(review_params.values()) >= {"public", "internal", "confidential"}


def test_platform_admin_dashboard_exposes_rollout_counts(monkeypatch):
    def resolver(sql, _params, _index):
        if "DepartmentRolloutPlan" in sql:
            return FakeResult(mapping={
                "PlannedCount": 1,
                "PilotCount": 2,
                "DarkLaunchCount": None,
                "ActiveCount": 4,
                "BlockedCount": 5,
            })
        return FakeResult(scalar_value=9)

    install_engine(monkeypatch, resolver)
    monkeypatch.setattr(
        ui_queries,
        "get_document_lifecycle_counts",
        lambda **_kwargs: {"effective": 3},
    )

    result = ui_queries.get_role_dashboard({
        "roles": ["platform_admin"],
        "username": "ops",
    })

    assert result["ingestion"] == {"running": 9, "failed": 9}
    assert result["rollout"] == {
        "departments_total": 9,
        "departments_planned": 1,
        "departments_pilot": 2,
        "departments_dark_launch": 0,
        "departments_active": 4,
        "departments_blocked": 5,
    }


@pytest.mark.parametrize(
    ("roles", "allowed_sites", "expected_sql_fragment"),
    [(["admin"], ["HCM"], "1 = 1"), (["reviewer"], [], "1 = 0")],
)
def test_reviewer_dashboard_handles_global_admin_and_missing_site_scope(
    monkeypatch, roles, allowed_sites, expected_sql_fragment
):
    connection = install_engine(monkeypatch, lambda *_args: FakeResult(scalar_value=1))
    monkeypatch.setattr(
        ui_queries,
        "get_document_lifecycle_counts",
        lambda **_kwargs: {"effective": 1},
    )

    result = ui_queries.get_role_dashboard({
        "roles": roles,
        "username": "reviewer",
        "allowed_departments": [],
        "allowed_sites": allowed_sites,
    })

    assert result["review"] == {"pending": 1, "publish_blocked": 1}
    review_sql = next(sql for sql, _params in connection.calls if "PublicationState" in sql)
    assert expected_sql_fragment in review_sql


@pytest.mark.parametrize(
    ("function_name", "expected"),
    [
        ("list_recent_documents", [(1, "manual.pdf")]),
        ("list_recent_failed_jobs", [(2, "job.pdf")]),
        ("list_feedbacks", [(3, "question")]),
        ("list_expiring_documents", [(4, "expiring.pdf")]),
        ("list_pending_review_docs", [(5, "review.pdf")]),
        ("list_bulk_action_jobs", [(6, "bulk.pdf")]),
        ("list_users_basic", [(7, "alice")]),
    ],
)
def test_read_lists_return_database_rows(monkeypatch, function_name, expected):
    install_engine(monkeypatch, lambda *_args: FakeResult(rows=expected))
    function = getattr(ui_queries, function_name)

    result = function(True) if function_name == "list_feedbacks" else function()

    assert result == expected


def test_ingestion_queue_filters_and_rbac_params(monkeypatch):
    expected = [(1, "manual.pdf", "A")]
    connection = install_engine(monkeypatch, lambda *_args: FakeResult(rows=expected))

    rows = ui_queries.list_ingestion_jobs(
        status="failed",
        dept="A",
        search="manual",
        is_admin=True,
        username="alice",
        allowed_departments=["B", "A", "A"],
    )

    assert rows == expected
    sql, params = connection.calls[-1]
    assert "UploadedBy = :uname" in sql
    assert params == {
        "status": "failed",
        "dept_pick": "A",
        "search": "%manual%",
        "uname": "alice",
        "allowed_dept_0": "A",
        "allowed_dept_1": "B",
    }


def test_ingestion_queue_without_departments_is_owner_only(monkeypatch):
    connection = install_engine(monkeypatch, lambda *_args: FakeResult(rows=[]))

    ui_queries.list_ingestion_jobs(username="bob", allowed_departments=[])

    sql, params = connection.calls[-1]
    assert "AND UploadedBy = :uname" in sql
    assert params == {"uname": "bob"}


def test_bulk_delete_counts_each_success_and_failure(monkeypatch):
    def resolver(_sql, params, _index):
        if params["jid"] == 2:
            raise RuntimeError("locked")
        return FakeResult(rowcount=1)

    connection = install_engine(monkeypatch, resolver)

    assert ui_queries.bulk_delete_ingestion_jobs([1, 2, 3]) == (2, 1)
    assert [params["jid"] for _, params in connection.calls] == [1, 2, 3]


def test_feedback_mutations_return_source_and_use_expected_params(monkeypatch):
    source = (42, "MFG", "HCM")

    def resolver(sql, _params, _index):
        return FakeResult(one=source if "SELECT SourceDocID" in sql else None)

    connection = install_engine(monkeypatch, resolver)

    result = ui_queries.classify_feedback_and_get_source(
        7, "retrieval", "correct", "reviewed"
    )
    ui_queries.delete_feedback(7)

    assert result == source
    assert connection.calls[0][1] == {
        "ft": "retrieval",
        "ca": "correct",
        "note": "reviewed",
        "fid": 7,
    }
    assert connection.calls[-1][1] == {"fid": 7}


def test_feedback_list_can_include_already_reviewed_rows(monkeypatch):
    connection = install_engine(monkeypatch, lambda *_args: FakeResult(rows=[]))

    ui_queries.list_feedbacks(False)

    sql, _params = connection.calls[-1]
    assert "AddedToGoldenSet" not in sql.split("WHERE 1 = 1", 1)[1]


def test_list_documents_applies_normalized_rbac_and_filters(monkeypatch):
    expected = [(8, "public.pdf")]
    connection = install_engine(monkeypatch, lambda *_args: FakeResult(rows=expected))
    monkeypatch.setenv("RBAC_STRICT_SITE_FILTER", "true")

    rows = ui_queries.list_documents(
        allowed_departments=["B", "A", "A", ""],
        max_security_level="internal",
        allowed_sites=["HCM", ""],
        dept="A",
        domain="mechanical",
        sec="internal",
        eff_mode="sap",
        search_kw="pump",
        soon_days=999,
    )

    assert rows == expected
    sql, params = connection.calls[-1]
    assert "d.Site IN (:allowed_site_0)" in sql
    assert "d.Site IS NULL" not in sql
    assert params == {
        "allowed_dept_0": "A",
        "allowed_dept_1": "B",
        "allowed_level_0": "public",
        "allowed_level_1": "internal",
        "allowed_site_0": "HCM",
        "dept": "A",
        "domain": "mechanical",
        "sec": "internal",
        "soon_days": 365,
        "bucket": "expiring_soon",
        "kw": "%pump%",
    }


def test_list_documents_fail_closed_without_scope_and_rejects_unknown_bucket(monkeypatch):
    connection = install_engine(monkeypatch, lambda *_args: FakeResult(rows=[]))
    monkeypatch.setenv("RBAC_STRICT_SITE_FILTER", "false")

    ui_queries.list_documents(
        allowed_departments=[],
        allowed_sites=[],
        bucket="needs-review",
        soon_days=-4,
    )

    sql, params = connection.calls[-1]
    assert sql.count("1 = 0") >= 2
    assert params["soon_days"] == 0
    assert params["bucket"] == "needs_review"
    with pytest.raises(ValueError, match="Unknown document lifecycle bucket"):
        ui_queries.list_documents(global_read_admin=True, bucket="surprise")


def test_global_admin_document_list_skips_department_security_and_site_filters(monkeypatch):
    connection = install_engine(monkeypatch, lambda *_args: FakeResult(rows=[]))

    ui_queries.list_documents(global_read_admin=True, bucket="effective")

    sql, params = connection.calls[-1]
    assert "PhongBanChiaSe" not in sql
    assert "allowed_level" not in params
    assert "allowed_site" not in params


def test_document_lifecycle_counts_fills_missing_buckets_and_casts_values(monkeypatch):
    rows = [("effective", 4), ("expired", None)]
    install_engine(monkeypatch, lambda *_args: FakeResult(rows=rows))

    result = ui_queries.get_document_lifecycle_counts(
        allowed_departments=["A"],
        allowed_sites=["HCM"],
        max_security_level="confidential",
        soon_days=10,
    )

    assert result == {
        "effective": 4,
        "expired": 0,
        "expiring_soon": 0,
        "needs_review": 0,
    }


def test_document_current_and_expired_mutations_preserve_side_effect_contract(monkeypatch):
    connection = install_engine(monkeypatch, lambda *_args: FakeResult(rowcount=1))
    qdrant_calls = []
    invalidations = []
    audit_calls = []
    monkeypatch.setitem(
        sys.modules,
        "mech_chatbot.db.repositories.qdrant",
        SimpleNamespace(update_qdrant_metadata=lambda *args: qdrant_calls.append(args)),
    )
    monkeypatch.setitem(
        sys.modules,
        "mech_chatbot.db.repositories.semantic_cache",
        SimpleNamespace(_invalidate_semantic_cache=lambda value: invalidations.append(value)),
    )
    monkeypatch.setitem(
        sys.modules,
        "mech_chatbot.db.repositories.audit",
        SimpleNamespace(write_audit_log=lambda **kwargs: audit_calls.append(kwargs)),
    )

    ui_queries.set_document_current(12)
    result = ui_queries.mark_document_expired(12, reviewer="reviewer")

    assert result is True
    assert qdrant_calls == [(12, {"effective_status": "expired"})]
    assert invalidations == ["lifecycle.expired"]
    assert audit_calls[0]["username"] == "reviewer"
    assert [params["id"] for _, params in connection.calls] == [12, 12, 12]


def test_mark_document_expired_stops_when_document_does_not_exist(monkeypatch):
    install_engine(monkeypatch, lambda *_args: FakeResult(rowcount=0))

    assert ui_queries.mark_document_expired(404) is False


@pytest.mark.parametrize(
    ("function_name", "args", "rowcount", "expected"),
    [
        ("reject_ingestion_job", (1, "bad metadata"), 1, True),
        ("mark_job_pending_review", (2,), 0, False),
        ("mark_job_published", (3,), 2, True),
        ("delete_ingestion_job", (4,), 0, False),
        ("mark_job_rejected", (5,), 1, True),
        ("update_user_password", (6, "hash"), 1, True),
    ],
)
def test_rowcount_mutations_return_boolean(
    monkeypatch, function_name, args, rowcount, expected
):
    install_engine(monkeypatch, lambda *_args: FakeResult(rowcount=rowcount))

    assert getattr(ui_queries, function_name)(*args) is expected


def test_bulk_metadata_departments_formats_archived_and_falls_back(monkeypatch):
    primary_rows = [("A", "active"), ("B", "disabled"), (None, "active")]
    primary = install_engine(monkeypatch, lambda *_args: FakeResult(rows=primary_rows))

    assert ui_queries.list_bulk_meta_departments() == ["A", "B (disabled)"]
    assert len(primary.calls) == 1

    def fallback_resolver(sql, _params, _index):
        if "LEFT JOIN" in sql:
            raise RuntimeError("old schema")
        return FakeResult(rows=[("Legacy",), (None,)])

    install_engine(monkeypatch, fallback_resolver)
    assert ui_queries.list_bulk_meta_departments() == ["Legacy"]


def test_bulk_metadata_document_list_preserves_optional_filters(monkeypatch):
    expected = [(19, "manual.pdf", "MFG", "mechanical")]
    connection = install_engine(monkeypatch, lambda *_args: FakeResult(rows=expected))

    rows = ui_queries.list_docs_for_bulk_meta(dept="MFG", domain="mechanical")

    assert rows == expected
    assert connection.calls[-1][1] == {"dept": "MFG", "domain": "mechanical"}


def test_bulk_metadata_document_list_accepts_no_optional_filters(monkeypatch):
    connection = install_engine(monkeypatch, lambda *_args: FakeResult(rows=[]))

    assert ui_queries.list_docs_for_bulk_meta() == []
    assert connection.calls[-1][1] == {}


def test_bulk_metadata_departments_and_department_counts_return_safe_empty_values(monkeypatch):
    def fail(*_args):
        raise RuntimeError("database offline")

    install_engine(monkeypatch, fail)

    assert ui_queries.list_bulk_meta_departments() == []
    assert ui_queries.count_dept_users("MFG") == 0
    assert ui_queries.count_dept_pending_jobs("MFG") == 0


def test_department_counts_and_clearance_return_public_values(monkeypatch):
    answers = iter([(4,), (3,), ("confidential",)])
    install_engine(monkeypatch, lambda *_args: FakeResult(one=next(answers)))

    assert ui_queries.count_dept_users("MFG") == 4
    assert ui_queries.count_dept_pending_jobs("MFG") == 3
    assert ui_queries.get_user_clearance(7) == "confidential"


def test_update_user_roles_writes_status_then_role_deltas(monkeypatch):
    connection = install_engine(monkeypatch, lambda *_args: FakeResult(rowcount=1))

    assert ui_queries.update_user_active_and_roles(
        7, False, ["reviewer"], ["viewer"]
    ) is True

    assert connection.calls[0][1] == {"active": 0, "uid": 7}
    assert connection.calls[1][1] == {"uid": 7, "role": "reviewer"}
    assert connection.calls[2][1] == {"uid": 7, "role": "viewer"}


@pytest.mark.parametrize(
    ("target", "actor_id", "is_admin", "other_admins", "message"),
    [
        (None, 1, False, 1, "Không tìm thấy"),
        ((7, "alice", "Alice", True), 7, False, 1, "đang đăng nhập"),
        ((7, "alice", "Alice", True), 1, True, 0, "admin hoạt động cuối cùng"),
    ],
)
def test_disable_user_rejects_unsafe_transitions(
    monkeypatch, target, actor_id, is_admin, other_admins, message
):
    def resolver(sql, _params, _index):
        if "SELECT UserID, Username" in sql:
            return FakeResult(one=target)
        if "RoleName = 'admin'" in sql and "COUNT(*)" not in sql:
            return FakeResult(one=(1,) if is_admin else None)
        if "COUNT(*)" in sql:
            return FakeResult(one=(other_admins,))
        return FakeResult(rowcount=1)

    install_engine(monkeypatch, resolver)

    result = ui_queries.set_user_active_status(
        7, False, actor_username="operator", actor_id=actor_id
    )

    assert result["ok"] is False
    assert message in result["message"]


def test_set_user_active_status_updates_and_audits(monkeypatch):
    def resolver(sql, _params, _index):
        if "SELECT UserID, Username" in sql:
            return FakeResult(one=(7, "alice", "Alice", False))
        return FakeResult(rowcount=1)

    connection = install_engine(monkeypatch, resolver)

    result = ui_queries.set_user_active_status(
        7, True, actor_username="operator", actor_id=1
    )

    assert result["ok"] is True
    assert connection.calls[-1][1]["action"] == "user_activate"
    assert "from=False; to=True" in connection.calls[-1][1]["details"]


@pytest.mark.parametrize(
    ("target", "actor_id", "is_admin", "other_admins", "message"),
    [
        (None, 1, False, 1, "Không tìm thấy"),
        ((7, "alice", "Alice", True), 7, False, 1, "đang đăng nhập"),
        ((7, "alice", "Alice", True), 1, True, 0, "admin hoạt động cuối cùng"),
    ],
)
def test_delete_user_rejects_unsafe_transitions(
    monkeypatch, target, actor_id, is_admin, other_admins, message
):
    def resolver(sql, _params, _index):
        if "SELECT UserID, Username" in sql:
            return FakeResult(one=target)
        if "RoleName = 'admin'" in sql and "COUNT(*)" not in sql:
            return FakeResult(one=(1,) if is_admin else None)
        if "COUNT(*)" in sql:
            return FakeResult(one=(other_admins,))
        return FakeResult(rowcount=1)

    install_engine(monkeypatch, resolver)

    result = ui_queries.delete_user_account(
        7, actor_username="operator", actor_id=actor_id
    )

    assert result["ok"] is False
    assert message in result["message"]


def test_delete_user_reports_failed_delete_after_cleanup(monkeypatch):
    def resolver(sql, _params, _index):
        if "SELECT UserID, Username" in sql:
            return FakeResult(one=(7, "alice", "Alice", True))
        if "RoleName = 'admin'" in sql:
            return FakeResult(one=None)
        return FakeResult(rowcount=0)

    connection = install_engine(monkeypatch, resolver)

    result = ui_queries.delete_user_account(7, actor_username="operator", actor_id=1)

    assert result == {"ok": False, "deleted": 0, "message": "Xóa tài khoản thất bại."}
    assert connection.calls[-1][1]["details"] == "target=alice; deleted=0"


def test_create_user_and_read_identity_assignments(monkeypatch):
    def resolver(sql, _params, _index):
        if "OUTPUT INSERTED.UserID" in sql:
            return FakeResult(one=(17,))
        if "SELECT r.RoleName" in sql:
            return FakeResult(rows=[("reviewer",), ("viewer",)])
        if "SELECT Department" in sql:
            return FakeResult(rows=[("MFG",), ("QA",)])
        return FakeResult(rowcount=1)

    connection = install_engine(monkeypatch, resolver)

    user_id = ui_queries.create_user_with_roles(
        "alice", "hash", "Alice", "MFG", ["reviewer"], ["MFG", "", "QA"]
    )
    roles = ui_queries.get_user_roles(user_id)
    departments = ui_queries.get_user_departments(user_id)

    assert user_id == 17
    assert roles == ["reviewer", "viewer"]
    assert departments == ["MFG", "QA"]
    bulk_insert = next(params for sql, params in connection.calls if "UserDepartments" in sql)
    assert bulk_insert == [{"uid": 17, "dept": "MFG"}, {"uid": 17, "dept": "QA"}]


def test_create_user_skips_empty_role_and_department_assignments(monkeypatch):
    def resolver(sql, _params, _index):
        if "OUTPUT INSERTED.UserID" in sql:
            return FakeResult(one=(18,))
        return FakeResult(rowcount=1)

    connection = install_engine(monkeypatch, resolver)

    user_id = ui_queries.create_user_with_roles(
        "bob", "hash", "Bob", "", [], ["", None]
    )

    assert user_id == 18
    assert len(connection.calls) == 1


def test_fetch_source_metadata_uses_stable_positional_parameter_mapping(monkeypatch):
    expected = [(3, "a.pdf"), (9, "b.pdf")]
    connection = install_engine(monkeypatch, lambda *_args: FakeResult(rows=expected))

    rows = ui_queries.fetch_sources_meta_rows([3, 9])

    assert rows == expected
    assert connection.calls[-1][1] == {"id_0": 3, "id_1": 9}
