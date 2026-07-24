"""Characterization tests for the public publication validation seams."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from mech_chatbot.db.repositories import publication


_FAIL_CLOSED_CONTRACT_ISSUES = {
    ("file_name", "required"),
    ("owner_department", "required"),
    ("domain", "required"),
    ("site", "required"),
    ("source_system", "required"),
    ("classification_rationale", "required"),
    ("classification_model", "required"),
    ("taxonomy_version", "required"),
    ("knowledge_owner_user_id", "required"),
    ("knowledge_approver_user_id", "required"),
    ("taxonomy_owner_approver", "required"),
    ("shared_departments", "required"),
    ("security_level", "invalid"),
    ("lifecycle_status", "not_publishable"),
    ("document_type", "required"),
    ("version", "required"),
    ("external_processing_policy", "invalid"),
    ("parent_section", "required"),
    ("parent_page", "required"),
    ("source_system", "required_by_profile"),
    ("shared_departments", "required_by_profile"),
    ("unknown", "required_by_profile"),
}


class _MappedResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _Connection:
    def __init__(self, row, calls):
        self._row = row
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement, params):
        self._calls.append((statement, params))
        return _MappedResult(self._row)


class _Engine:
    def __init__(self, row):
        self._row = row
        self.calls = []

    def connect(self):
        return _Connection(self._row, self.calls)


@pytest.fixture
def install_database_row(monkeypatch):
    def install(row):
        fake_engine = _Engine(row)
        monkeypatch.setattr(publication, "engine", fake_engine)
        monkeypatch.setattr(publication, "_ensure_engine", lambda: None)
        return fake_engine

    return install


@pytest.fixture
def valid_contract_row():
    return {
        "DocID": 42,
        "TenFile": "procedure.pdf",
        "OwnerDepartment": "MECH",
        "Domain": "maintenance",
        "SecurityLevel": "internal",
        "Site": "HCM",
        "SourceSystem": "manual-upload",
        "ExternalProcessingPolicy": "internal_only",
        "ClassificationRationale": "approved taxonomy match",
        "ClassificationModel": "rules-v1",
        "ClassificationJson": '{"document_type": "procedure"}',
        "LifecycleStatus": "draft",
        "ReviewStatus": "approved",
        "VersionNo": 1,
        "KnowledgeOwnerUserID": 10,
        "KnowledgeApproverUserID": 11,
        "TaxonomyVersion": "v2",
        "ParentApplicable": False,
        "ParentSection": None,
        "ParentPage": None,
        "DepartmentTaxonomyVersion": "v2",
        "DepartmentKnowledgeOwnerUserID": 10,
        "DepartmentKnowledgeApproverUserID": 11,
        "DepartmentGovernanceActive": True,
        "DocumentTypesJson": '["procedure", "manual"]',
        "RequiredMetadataJson": '["owner_department", "document_type"]',
        "DomainProfileActive": True,
        "DeptCount": 1,
        "TechnicalDocType": None,
    }


def _issue_pairs(result):
    return {(issue.field, issue.code) for issue in result.issues}


def test_publish_contract_rejects_invalid_document_id_without_database(monkeypatch):
    def fail_if_called():
        raise AssertionError("database boundary must not be called")

    monkeypatch.setattr(publication, "_ensure_engine", fail_if_called)

    result = publication.validate_publish_contract("not-an-id")

    assert not result
    assert result.to_dict() == {
        "doc_id": None,
        "valid": False,
        "issues": [
            {"field": "doc_id", "code": "invalid", "message": "DocID khong hop le"}
        ],
    }


def test_publish_contract_fails_closed_when_document_is_missing(install_database_row):
    engine = install_database_row(None)

    result = publication.validate_publish_contract("42")

    assert not result
    assert _issue_pairs(result) == {("doc_id", "not_found")}
    assert engine.calls[0][1] == {"doc_id": 42}


def test_publish_contract_accepts_complete_canonical_metadata(
    install_database_row,
    valid_contract_row,
):
    install_database_row(valid_contract_row)

    result = publication.validate_publish_contract(42)

    assert result
    assert result.doc_id == 42
    assert result.issues == ()


def test_publish_contract_reports_all_fail_closed_metadata_reasons(
    install_database_row,
    valid_contract_row,
):
    row = deepcopy(valid_contract_row)
    row.update(
        {
            "TenFile": " ",
            "OwnerDepartment": None,
            "Domain": "",
            "SecurityLevel": "secret",
            "Site": "",
            "SourceSystem": None,
            "ExternalProcessingPolicy": "sometimes_external",
            "ClassificationRationale": "",
            "ClassificationModel": None,
            "ClassificationJson": "not-json",
            "LifecycleStatus": "retired",
            "VersionNo": "unknown",
            "KnowledgeOwnerUserID": None,
            "KnowledgeApproverUserID": None,
            "TaxonomyVersion": "",
            "ParentApplicable": True,
            "ParentSection": "",
            "ParentPage": None,
            "DepartmentKnowledgeOwnerUserID": None,
            "DeptCount": 0,
            "DocumentTypesJson": "not-json",
            "RequiredMetadataJson": '["source_system", "shared_departments", "unknown"]',
            "TechnicalDocType": None,
        }
    )
    install_database_row(row)

    result = publication.validate_publish_contract(42)

    assert not result
    assert _FAIL_CLOSED_CONTRACT_ISSUES <= _issue_pairs(result)


@pytest.mark.parametrize(
    ("row_updates", "expected_issue"),
    [
        ({"DepartmentGovernanceActive": False}, ("department_governance", "required")),
        ({"DomainProfileActive": False}, ("domain_profile", "required")),
        ({"DepartmentTaxonomyVersion": "v1"}, ("taxonomy_version", "stale")),
        ({"DocumentTypesJson": '["manual"]'}, ("document_type", "not_allowed")),
    ],
)
def test_publish_contract_enforces_active_governance_and_domain_profile(
    install_database_row,
    valid_contract_row,
    row_updates,
    expected_issue,
):
    row = {**valid_contract_row, **row_updates}
    install_database_row(row)

    result = publication.validate_publish_contract(42)

    assert not result
    assert expected_issue in _issue_pairs(result)


def test_publish_actor_rejects_invalid_document_id_without_database(monkeypatch):
    def fail_if_called():
        raise AssertionError("database boundary must not be called")

    monkeypatch.setattr(publication, "_ensure_engine", fail_if_called)

    result = publication.validate_publish_actor(None, reviewer_id=11)

    assert not result
    assert _issue_pairs(result) == {("doc_id", "invalid")}


def test_publish_actor_fails_closed_when_document_is_missing(install_database_row):
    install_database_row(None)

    result = publication.validate_publish_actor(42, reviewer_id=11)

    assert not result
    assert _issue_pairs(result) == {("doc_id", "not_found")}


def test_publish_actor_accepts_active_department_approver_role(
    install_database_row,
):
    engine = install_database_row(
        {
            "KnowledgeApproverUserID": None,
            "DepartmentApproverUserID": 11,
            "ActorActive": 1,
            "ActorInOwnerDepartment": 1,
        }
    )

    result = publication.validate_publish_actor(
        "42",
        reviewer_id="11",
        reviewer_roles=[" Knowledge_Approver ", ""],
    )

    assert result
    assert result.issues == ()
    assert engine.calls[0][1] == {"doc_id": 42, "actor_id": 11}


def test_publish_actor_reports_all_authorization_failures(install_database_row):
    install_database_row(
        {
            "KnowledgeApproverUserID": 11,
            "DepartmentApproverUserID": 12,
            "ActorActive": 0,
            "ActorInOwnerDepartment": 0,
        }
    )

    result = publication.validate_publish_actor(
        42,
        reviewer_id=99,
        reviewer_roles=["viewer"],
    )

    assert not result
    assert _issue_pairs(result) == {
        ("reviewer_id", "inactive"),
        ("reviewer_id", "department_required"),
        ("reviewer_roles", "role_required"),
        ("knowledge_approver_user_id", "forbidden"),
    }


def test_publish_actor_requires_authenticated_actor_and_configured_approver(
    install_database_row,
):
    install_database_row(
        {
            "KnowledgeApproverUserID": None,
            "DepartmentApproverUserID": None,
            "ActorActive": 0,
            "ActorInOwnerDepartment": 0,
        }
    )

    result = publication.validate_publish_actor(42, reviewer_id="invalid")

    assert not result
    assert _issue_pairs(result) == {
        ("reviewer_id", "required"),
        ("knowledge_approver_user_id", "required"),
    }


def test_publish_actor_allows_explicit_admin_emergency_override(
    install_database_row,
):
    install_database_row(
        {
            "KnowledgeApproverUserID": 11,
            "DepartmentApproverUserID": None,
            "ActorActive": 1,
            "ActorInOwnerDepartment": 1,
        }
    )

    result = publication.validate_publish_actor(
        42,
        reviewer_id=99,
        reviewer_roles=["admin"],
        policy=replace(
            publication._default_policy(),
            allow_admin_approval_override=True,
        ),
    )

    assert result


def test_publish_actor_denies_admin_override_unless_deployment_opts_in(
    install_database_row,
):
    install_database_row(
        {
            "KnowledgeApproverUserID": 11,
            "DepartmentApproverUserID": None,
            "ActorActive": 1,
            "ActorInOwnerDepartment": 1,
        }
    )

    result = publication.validate_publish_actor(42, reviewer_id=99, reviewer_roles=["admin"])

    assert not result
    assert _issue_pairs(result) == {("knowledge_approver_user_id", "forbidden")}
