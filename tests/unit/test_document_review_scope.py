"""Regression tests for review mutations outside an actor's assignment."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from mech_chatbot.api.routers import documents


@pytest.mark.parametrize(
    ("route", "mutation"),
    [
        (documents.document_reject, "reject_document"),
        (documents.document_archive, "archive_document"),
    ],
)
def test_document_review_mutation_denies_unassigned_reviewer_before_write(
    monkeypatch, route, mutation
):
    profile = {"user_id": 94, "username": "reviewer_a", "roles": ["reviewer"]}
    monkeypatch.setattr(
        documents,
        "validate_publish_actor",
        lambda doc_id, reviewer_id, reviewer_roles: SimpleNamespace(
            valid=False, issues=(SimpleNamespace(message="Wrong department"),)
        ),
        raising=False,
    )
    monkeypatch.setattr(
        documents,
        mutation,
        lambda *_args, **_kwargs: pytest.fail("denied review must not mutate document"),
    )

    with pytest.raises(HTTPException) as error:
        route(163, profile)

    assert error.value.status_code == 403


def test_document_review_mutation_allows_assigned_reviewer(monkeypatch):
    profile = {"user_id": 100, "username": "reviewer_b", "roles": ["reviewer"]}
    seen = []
    monkeypatch.setattr(
        documents,
        "validate_publish_actor",
        lambda doc_id, reviewer_id, reviewer_roles: seen.append(
            (doc_id, reviewer_id, reviewer_roles)
        ) or SimpleNamespace(valid=True, issues=()),
        raising=False,
    )
    monkeypatch.setattr(
        documents,
        "reject_document",
        lambda doc_id, reviewer: seen.append((doc_id, reviewer)) or True,
    )

    assert documents.document_reject(163, profile) == {"ok": True}
    assert seen == [(163, 100, ["reviewer"]), (163, "reviewer_b")]


@pytest.mark.parametrize("action", ["reject", "delete"])
def test_bulk_review_denies_other_department_before_any_write(monkeypatch, action):
    profile = {"user_id": 94, "username": "reviewer_a", "roles": ["reviewer"]}
    monkeypatch.setattr(
        documents,
        "validate_publish_actor",
        lambda *_args: SimpleNamespace(valid=False, issues=()),
    )

    class Publication:
        def resolve_latest_doc_id(self, job_id):
            assert job_id == 155
            return 163

    class Review:
        def execute(self, *_args):
            pytest.fail("denied batch must not mutate any item")

    runtime = SimpleNamespace(publication_port=Publication(), review_documents=Review())
    with pytest.raises(HTTPException) as error:
        documents._documents_review_bulk_impl(
            {"action": action, "items": [{"job_id": 155, "doc_id": 163}]},
            profile,
            runtime,
        )

    assert error.value.status_code == 403


def test_bulk_review_denies_mismatched_job_and_document(monkeypatch):
    profile = {"user_id": 100, "username": "reviewer_b", "roles": ["reviewer"]}
    monkeypatch.setattr(
        documents,
        "validate_publish_actor",
        lambda *_args: SimpleNamespace(valid=True, issues=()),
    )

    class Publication:
        def resolve_latest_doc_id(self, _job_id):
            return 164

    class Review:
        def execute(self, *_args):
            pytest.fail("mismatched pair must not mutate either record")

    runtime = SimpleNamespace(publication_port=Publication(), review_documents=Review())
    with pytest.raises(HTTPException) as error:
        documents._documents_review_bulk_impl(
            {"action": "reject", "items": [{"job_id": 155, "doc_id": 163}]},
            profile,
            runtime,
        )

    assert error.value.status_code == 403


def test_ingestion_reject_denies_other_department_before_job_write(monkeypatch):
    profile = {"user_id": 94, "username": "reviewer_a", "roles": ["reviewer"]}
    monkeypatch.setattr(
        documents,
        "validate_publish_actor",
        lambda *_args: SimpleNamespace(valid=False, issues=()),
    )
    monkeypatch.setattr(
        documents,
        "reject_ingestion_job",
        lambda *_args: pytest.fail("denied job must not be rejected"),
    )

    class Publication:
        def resolve_latest_doc_id(self, _job_id):
            return 163

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(runtime=SimpleNamespace(publication_port=Publication())))
    )
    with pytest.raises(HTTPException) as error:
        documents.ingestion_reject(155, {"reason": "bad"}, request, profile)

    assert error.value.status_code == 403


def test_pending_review_list_hides_other_department_metadata(monkeypatch):
    profile = {"user_id": 100, "username": "reviewer_b", "roles": ["reviewer"]}
    monkeypatch.setattr(
        documents,
        "list_pending_review_docs",
        lambda: [
            {"DocID": 144, "Title": "QA A private title"},
            {"DocID": 169, "Title": "QA B title"},
            {"DocID": None, "Title": "Unassigned pending job"},
        ],
    )
    monkeypatch.setattr(
        documents,
        "validate_publish_actor",
        lambda doc_id, *_args: SimpleNamespace(valid=doc_id == 169, issues=()),
    )

    result = documents.documents_pending_review(profile)

    assert result == {"documents": [{"DocID": 169, "Title": "QA B title"}]}


def test_bulk_review_queue_hides_other_department_jobs(monkeypatch):
    profile = {
        "user_id": 100, "roles": ["reviewer"], "allowed_departments": ["B"],
    }
    monkeypatch.setattr(documents, "list_bulk_action_jobs", lambda: [
        {"JobID": 1, "DocID": 144, "ThuMuc": "A", "TenFile": "secret.pdf"},
        {"JobID": 2, "DocID": 169, "ThuMuc": "B", "TenFile": "own.pdf"},
        {"JobID": 3, "DocID": None, "ThuMuc": "A", "TenFile": "failed-secret.pdf"},
        {"JobID": 4, "DocID": None, "ThuMuc": "B", "TenFile": "failed-own.pdf"},
    ])
    monkeypatch.setattr(
        documents, "validate_publish_actor",
        lambda doc_id, *_args: SimpleNamespace(valid=doc_id == 169, issues=()),
    )

    result = documents.ingestion_bulk_jobs(profile)

    assert [row["JobID"] for row in result["jobs"]] == [2, 4]


@pytest.mark.parametrize("action", ["reject", "delete"])
def test_bulk_review_allows_job_without_document_in_own_department(monkeypatch, action):
    profile = {
        "user_id": 100, "username": "reviewer_b", "roles": ["reviewer"],
        "allowed_departments": ["B"],
    }
    monkeypatch.setattr(documents, "list_bulk_action_jobs", lambda: [
        {"JobID": 4, "DocID": None, "ThuMuc": "B", "TenFile": "failed-own.pdf"},
    ])

    class Publication:
        def resolve_latest_doc_id(self, _job_id):
            return None

    class Review:
        def execute(self, command, _actor):
            assert command.items[0].job_id == 4
            return SimpleNamespace(ok=True, updated=1, pending=0, failed=0, failures=())

    runtime = SimpleNamespace(publication_port=Publication(), review_documents=Review())
    result = documents._documents_review_bulk_impl(
        {"action": action, "items": [{"job_id": 4, "doc_id": None}]},
        profile, runtime,
    )

    assert result["ok"] is True


def test_publish_contract_denies_other_department_before_read(monkeypatch):
    profile = {"user_id": 100, "username": "reviewer_b", "roles": ["reviewer"]}
    monkeypatch.setattr(
        documents,
        "validate_publish_actor",
        lambda *_args: SimpleNamespace(valid=False, issues=()),
    )
    monkeypatch.setattr(
        documents,
        "validate_publish_contract",
        lambda *_args: pytest.fail("contract must not reveal other department metadata"),
    )

    with pytest.raises(HTTPException) as error:
        documents.document_publish_contract(144, profile)

    assert error.value.status_code == 403


def test_bulk_delete_reports_missing_job_in_response(monkeypatch):
    monkeypatch.setattr(documents, "bulk_delete_ingestion_jobs", lambda _ids: (0, 1))

    result = documents.ingestion_bulk_delete(
        {"ids": [2147483647]}, {"roles": ["admin"]},
    )

    assert result == {"ok": False, "deleted": 0, "failed": 1}
