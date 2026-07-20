from __future__ import annotations

import json
from datetime import datetime

import pytest

from mech_chatbot.db.repositories import knowledge_governance as governance


pytestmark = [pytest.mark.unit, pytest.mark.security]


class _Result:
    def __init__(self, *, rows=(), row=None, rowcount=0):
        self._rows = list(rows)
        self._row = row
        self.rowcount = rowcount

    def mappings(self):
        return self

    def first(self):
        return self._row

    def all(self):
        return list(self._rows)


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


@pytest.fixture(autouse=True)
def fixed_environment(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_ALLOW_ADMIN_METADATA_OVERRIDE", raising=False)


@pytest.fixture
def fake_db(monkeypatch):
    def install(*results):
        connection = _Connection(results)
        monkeypatch.setattr(governance, "engine", _Engine(connection))
        monkeypatch.setattr(governance, "_ensure_engine", lambda: None)
        return connection

    return install


def _governance_row(**overrides):
    row = {
        "DeptCode": "Technical",
        "KnowledgeOwnerUserID": 11,
        "KnowledgeApproverUserID": 12,
        "TaxonomyVersion": "v2",
        "ExternalProcessingPolicy": "internal_only",
        "IsActive": 1,
        "UpdatedAt": datetime(2026, 1, 2, 3, 4, 5),
        "UpdatedBy": "alice",
    }
    return {**row, **overrides}


def _profile_row(**overrides):
    row = {
        "DeptCode": "Technical",
        "DocumentTypesJson": '["manual", "sop", "manual"]',
        "RequiredMetadataJson": '["site"]',
        "RouterPatternsJson": "not-json",
        "ParentContextEnabled": 1,
        "IsActive": 1,
        "UpdatedAt": None,
        "UpdatedBy": "alice",
    }
    return {**row, **overrides}


def test_get_and_list_governance_map_public_shape_and_empty_input(fake_db):
    assert governance.get_department_knowledge_governance(" ") is None

    fake_db(_Result(row=_governance_row()))
    result = governance.get_department_knowledge_governance(" Technical ")
    assert result["department_code"] == "Technical"
    assert result["taxonomy_owner_user_id"] == 11
    assert result["updated_at"] == "2026-01-02T03:04:05"

    fake_db(_Result(rows=[_governance_row(), _governance_row(DeptCode="Quality", KnowledgeOwnerUserID=None)]))
    listed = governance.list_department_knowledge_governance()
    assert [item["department_code"] for item in listed] == ["Technical", "Quality"]
    assert listed[1]["knowledge_owner_user_id"] is None


def test_get_and_list_domain_profiles_normalize_json_and_defaults(fake_db):
    assert governance.get_department_domain_profile("") is None

    fake_db(_Result(row=_profile_row()))
    result = governance.get_department_domain_profile("Technical")
    assert result["document_types"] == ["manual", "sop"]
    assert result["router_patterns"] == ["not-json"]
    assert result["updated_at"] is None

    fake_db(_Result(rows=[_profile_row(), _profile_row(DeptCode="Quality", DocumentTypesJson=None)]))
    assert governance.list_department_domain_profiles()[1]["document_types"] == []


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"department_code": "", "taxonomy_version": "v1"}, "Department code"),
        ({"department_code": "Tech", "taxonomy_version": ""}, "taxonomy_version"),
        (
            {"department_code": "Tech", "taxonomy_version": "v1", "external_processing_policy": "public"},
            "external_processing_policy",
        ),
        ({"department_code": "Tech", "taxonomy_version": "v1", "knowledge_owner_user_id": 0}, "UserID"),
    ],
)
def test_upsert_governance_rejects_invalid_boundary_input(kwargs, message):
    defaults = {"knowledge_owner_user_id": None, "knowledge_approver_user_id": None}
    with pytest.raises(ValueError, match=message):
        governance.upsert_department_knowledge_governance(**{**defaults, **kwargs})


@pytest.mark.parametrize(
    "principal, message",
    [
        (None, "user dang hoat dong"),
        ({"IsActive": 0, "IsDepartmentMember": 1, "HasApproverRole": 1}, "user dang hoat dong"),
        ({"IsActive": 1, "IsDepartmentMember": 0, "HasApproverRole": 1}, "phai thuoc phong ban"),
        ({"IsActive": 1, "IsDepartmentMember": 1, "HasApproverRole": 0}, "Knowledge Approver"),
    ],
)
def test_upsert_governance_fails_closed_for_invalid_approver(fake_db, principal, message):
    fake_db(_Result(row=principal))
    with pytest.raises(ValueError, match=message):
        governance.upsert_department_knowledge_governance(
            "Technical",
            knowledge_owner_user_id=None,
            knowledge_approver_user_id=12,
            taxonomy_version="v1",
        )


def test_upsert_governance_persists_audits_and_returns_canonical_read(fake_db, monkeypatch):
    conn = fake_db(
        _Result(row={"IsActive": 1, "IsDepartmentMember": 1, "HasApproverRole": 1}),
        _Result(row={"IsActive": 1, "IsDepartmentMember": 1, "HasApproverRole": 1}),
        _Result(rowcount=1),
    )
    audits = []
    monkeypatch.setattr(governance._r_audit, "write_audit_log", lambda *args: audits.append(args))
    monkeypatch.setattr(governance, "get_department_knowledge_governance", lambda code: {"department_code": code})

    result = governance.upsert_department_knowledge_governance(
        " Technical ",
        knowledge_owner_user_id="11",
        knowledge_approver_user_id=12,
        taxonomy_version="v2",
        external_processing_policy="INTERNAL_ONLY",
        is_active=False,
        updated_by=" alice ",
    )

    assert result == {"department_code": "Technical"}
    merge_params = conn.calls[-1][1]
    assert merge_params["owner"] == 11
    assert merge_params["policy"] == "internal_only"
    assert merge_params["active"] == 0
    assert audits[0][0:3] == ("alice", "department_knowledge_governance_upsert", "DepartmentKnowledgeGovernance")


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"department_code": "", "document_types": ["manual"], "required_metadata": ["site"]}, "Department code"),
        ({"department_code": "Tech", "document_types": [], "required_metadata": ["site"]}, "document_types"),
        ({"department_code": "Tech", "document_types": ["manual"], "required_metadata": []}, "required_metadata"),
    ],
)
def test_upsert_domain_profile_rejects_invalid_boundary_input(kwargs, message):
    with pytest.raises(ValueError, match=message):
        governance.upsert_department_domain_profile(router_patterns=[], **kwargs)


def test_upsert_domain_profile_deduplicates_values_and_audits(fake_db, monkeypatch):
    conn = fake_db(_Result(rowcount=1))
    audits = []
    monkeypatch.setattr(governance._r_audit, "write_audit_log", lambda *args: audits.append(args))
    monkeypatch.setattr(governance, "get_department_domain_profile", lambda code: {"department_code": code})

    result = governance.upsert_department_domain_profile(
        "Technical",
        document_types=["manual", " manual ", "sop"],
        required_metadata=["site"],
        router_patterns=["pressure"],
        parent_context_enabled=False,
        updated_by="",
    )

    assert result == {"department_code": "Technical"}
    params = conn.calls[0][1]
    assert json.loads(params["types"]) == ["manual", "sop"]
    assert params["parent_context"] == 0
    assert params["updated_by"] == "System"
    assert audits[0][1] == "department_domain_profile_upsert"


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"doc_id": "bad"}, "DocID"),
        ({"doc_id": 1, "taxonomy_version": " "}, "taxonomy_version"),
        ({"doc_id": 1, "parent_page": "bad"}, "parent_page"),
        ({"doc_id": 1, "parent_page": 0}, "parent_page"),
    ],
)
def test_update_document_metadata_rejects_invalid_input(kwargs, message):
    with pytest.raises(ValueError, match=message):
        governance.update_document_governance_metadata(**kwargs)


def test_update_document_metadata_noop_and_missing_document(fake_db):
    assert governance.update_document_governance_metadata(1) is True

    fake_db(_Result(row=None))
    assert governance.update_document_governance_metadata(1, taxonomy_version="v2") is False


def test_update_document_metadata_syncs_qdrant_audit_and_cache(fake_db, monkeypatch):
    previous = {
        "OwnerDepartment": "Technical",
        "KnowledgeOwnerUserID": 10,
        "KnowledgeApproverUserID": 12,
        "TaxonomyVersion": "v1",
        "ParentApplicable": 0,
        "ParentSection": None,
        "ParentPage": None,
    }
    conn = fake_db(
        _Result(row=previous),
        _Result(row={"IsActive": 1, "IsDepartmentMember": 1, "HasApproverRole": 1}),
        _Result(rowcount=1),
    )
    qdrant_calls = []
    audit_calls = []
    cache_calls = []
    monkeypatch.setattr(
        governance._r_qdrant,
        "update_qdrant_metadata",
        lambda *args, **kwargs: qdrant_calls.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(governance._r_audit, "write_audit_log", lambda *args: audit_calls.append(args))
    monkeypatch.setattr(governance._r_semantic_cache, "_invalidate_semantic_cache", cache_calls.append)

    assert governance.update_document_governance_metadata(
        5,
        knowledge_owner_user_id=11,
        taxonomy_version="v2",
        parent_applicable=True,
        parent_section=" Safety ",
        parent_page=3,
        updated_by="alice",
    ) is True

    assert conn.calls[-1][1]["parent_page"] == 3
    assert qdrant_calls[0][0][1]["parent_applicable"] is True
    assert audit_calls[0][0:4] == ("alice", "document_governance_metadata_update", "TaiLieu", 5)
    assert cache_calls == ["document.governance"]


def test_update_document_metadata_rolls_back_sql_when_qdrant_fails(fake_db, monkeypatch):
    previous = {
        "OwnerDepartment": "Technical",
        "KnowledgeOwnerUserID": 10,
        "KnowledgeApproverUserID": 12,
        "TaxonomyVersion": "v1",
        "ParentApplicable": 0,
        "ParentSection": "Old",
        "ParentPage": 1,
    }
    conn = fake_db(_Result(row=previous), _Result(rowcount=1), _Result(rowcount=1))
    calls = []

    def qdrant_update(*args, **kwargs):
        calls.append((args, kwargs))
        return len(calls) > 1

    monkeypatch.setattr(governance._r_qdrant, "update_qdrant_metadata", qdrant_update)

    with pytest.raises(RuntimeError, match="SQL da rollback"):
        governance.update_document_governance_metadata(5, taxonomy_version="v2")

    assert len(conn.calls) == 3
    assert conn.calls[-1][1]["taxonomy"] == "v1"
    assert calls[-1][0][1]["taxonomy_version"] == "v1"


def test_update_document_metadata_fails_when_sql_updates_no_rows(fake_db, monkeypatch):
    previous = {
        "OwnerDepartment": "Technical",
        "KnowledgeOwnerUserID": None,
        "KnowledgeApproverUserID": None,
        "TaxonomyVersion": "v1",
        "ParentApplicable": 0,
        "ParentSection": None,
        "ParentPage": None,
    }
    fake_db(_Result(row=previous), _Result(rowcount=0))
    monkeypatch.setattr(governance._r_qdrant, "update_qdrant_metadata", lambda *_args, **_kwargs: True)
    assert governance.update_document_governance_metadata(5, parent_section="new") is False


def test_validate_metadata_actor_is_fail_closed_and_override_requires_env(fake_db, monkeypatch):
    assert governance.validate_document_metadata_actor(1, None) == (
        False,
        "Metadata update phai co user da xac thuc",
    )

    fake_db(_Result(row=None))
    assert governance.validate_document_metadata_actor(1, 2)[0] is False

    fake_db(_Result(row={"KnowledgeOwnerUserID": 2, "KnowledgeApproverUserID": 3}))
    assert governance.validate_document_metadata_actor(1, 2) == (True, "")

    fake_db(_Result(row={"KnowledgeOwnerUserID": 2, "KnowledgeApproverUserID": 3}))
    assert governance.validate_document_metadata_actor(1, 4, actor_roles=["admin"])[0] is False

    monkeypatch.setenv("KNOWLEDGE_ALLOW_ADMIN_METADATA_OVERRIDE", "true")
    fake_db(_Result(row={"KnowledgeOwnerUserID": 2, "KnowledgeApproverUserID": 3}))
    assert governance.validate_document_metadata_actor(1, 4, actor_roles=[" ADMIN "]) == (True, "")


def test_list_missing_site_documents_clamps_limit_and_maps_rows(fake_db):
    row = {
        "DocID": "7",
        "TenFile": " manual.pdf ",
        "ThuMuc": " Technical ",
        "OwnerDepartment": "Technical",
        "Domain": "Engineering",
        "LifecycleStatus": "active",
        "ReviewStatus": "approved",
        "PublicationState": "published",
        "NgayTaiLen": datetime(2026, 2, 3),
    }
    conn = fake_db(_Result(rows=[row]))

    result = governance.list_missing_site_documents(99999)

    assert result[0]["doc_id"] == 7
    assert result[0]["file_name"] == "manual.pdf"
    assert result[0]["uploaded_at"] == "2026-02-03T00:00:00"
    assert conn.calls[0][1] == {"limit": 5000}
