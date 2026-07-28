import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts.department_decomposition_eval.generate_fixture import (
    BATCH_ID,
    generate_fixture,
)
from scripts.department_decomposition_eval.ingest_fixture import (
    validate_active_departments,
)
from scripts.decomposition_eval.preflight import check_fixture_cases


pytestmark = pytest.mark.unit


def test_department_fixture_covers_every_active_demo_department(tmp_path: Path):
    report = generate_fixture(tmp_path)
    documents = [
        json.loads(line)
        for line in (tmp_path / "corpus_manifest.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    cases = [
        json.loads(line)
        for line in (tmp_path / "eval_manifest.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]

    assert report == {
        "batch_id": BATCH_ID,
        "departments": 14,
        "documents": 42,
        "eval_cases": 56,
    }
    assert len({row["department"] for row in documents}) == 14
    assert all(
        len({row["expected_fact"] for row in documents
             if row["department"] == department}) == 3
        for department in {row["department"] for row in documents}
    )
    assert all(
        len([case for case in cases if case["expected_department"] == department])
        == 4
        for department in {row["department"] for row in documents}
    )


def test_department_fixture_has_simple_full_and_access_denied_cases(
    tmp_path: Path,
):
    generate_fixture(tmp_path)
    cases = [
        json.loads(line)
        for line in (tmp_path / "eval_manifest.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]

    for department in {case["expected_department"] for case in cases}:
        department_cases = [
            case for case in cases
            if case["expected_department"] == department
        ]
        assert [case["evaluation_group"] for case in department_cases].count(
            "simple"
        ) == 1
        assert max(
            len(case["expected_branches"]) for case in department_cases
        ) == 3
        denied = next(
            case for case in department_cases
            if case["expected_outcome"] == "partial_answer"
        )
        assert denied["expected_branches"][1]["expected_outcome"] == (
            "access_denied"
        )
        assert denied["preflight_documents"][0]["department"] != department


def test_preflight_accepts_department_fixture_in_main_collection(tmp_path: Path):
    generate_fixture(tmp_path)
    cases = [
        json.loads(line)
        for line in (tmp_path / "eval_manifest.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    technical_cases = [
        case for case in cases if case["expected_department"] == "Technical"
    ]
    referenced = {
        citation["document"]
        for case in technical_cases
        for citation in case.get("expected_citations") or []
    }
    referenced.update(
        item["document"]
        for case in technical_cases
        for item in case.get("preflight_documents") or []
    )
    manifest_rows = [
        json.loads(line)
        for line in (tmp_path / "corpus_manifest.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    by_name = {
        Path(row["path"]).name: row for row in manifest_rows
        if Path(row["path"]).name in referenced
    }
    documents = []
    points = []
    for doc_id, filename in enumerate(sorted(referenced), 1):
        row = by_name[filename]
        document = {
            "DocID": doc_id,
            "TenFile": filename,
            "VersionNo": 1,
            "LifecycleStatus": "published",
            "ReviewStatus": "approved",
            "PublicationState": "published",
            "IsCurrent": True,
            "Servable": True,
            "SourceSystem": BATCH_ID,
            "OwnerDepartment": row["department"],
            "Site": row["site"],
            "SecurityLevel": row["security_level"],
        }
        documents.append(document)
        points.append({
            "doc_id": doc_id,
            "page": 1,
            "version_no": 1,
            "source_system": BATCH_ID,
            "servable": True,
            "is_current": True,
            "site": row["site"],
            "security_level": row["security_level"],
            "owner_department": row["department"],
            "phong_ban_quyen": [row["department"]],
        })

    report = check_fixture_cases(
        technical_cases,
        documents,
        [],
        points,
        collection="TaiLieuKyThuat_v2",
        expected_collection="TaiLieuKyThuat_v2",
        fixture_batch=BATCH_ID,
    )

    assert report["passed"] is True, report["failures"]
    assert report["batch"] == BATCH_ID

    points[0]["owner_department"] = "WrongDepartment"
    invalid = check_fixture_cases(
        technical_cases,
        documents,
        [],
        points,
        collection="TaiLieuKyThuat_v2",
        expected_collection="TaiLieuKyThuat_v2",
        fixture_batch=BATCH_ID,
    )
    assert invalid["passed"] is False
    assert any(
        failure["reason"] == "qdrant_page_missing"
        for failure in invalid["failures"]
    )


def test_ingest_rejects_manifest_from_another_batch_before_db_access(
    monkeypatch,
    tmp_path: Path,
):
    from scripts.demo_wave import ingest_demo_corpus as ingest

    (tmp_path / "corpus_manifest.jsonl").write_text(
        json.dumps({"batch_id": "wrong-batch"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ingest,
        "_ensure_engine",
        lambda: pytest.fail("database must not be accessed"),
    )

    with pytest.raises(ValueError, match="batch_id"):
        ingest.ingest_corpus(
            tmp_path,
            batch=BATCH_ID,
            generator=lambda _: [],
        )


def test_ingest_rejects_source_path_outside_fixture_corpus_before_db_access(
    monkeypatch,
    tmp_path: Path,
):
    from scripts.demo_wave import ingest_demo_corpus as ingest

    outside = tmp_path.parent / "outside.md"
    outside.write_text("not an approved fixture", encoding="utf-8")
    record = {
        "batch_id": BATCH_ID,
        "department": "Technical",
        "site": "HQ",
        "security_level": "internal",
        "path": "../outside.md",
        "title": "Invalid fixture",
        "doc_number": "TK-100-D1",
        "document_type": "generic",
        "effective_status": "effective",
        "effective_date": "2026-01-01",
        "expiry_date": "2030-01-01",
        "is_current": True,
        "should_serve": True,
    }
    (tmp_path / "corpus_manifest.jsonl").write_text(
        json.dumps(record) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ingest,
        "_ensure_engine",
        lambda: pytest.fail("database must not be accessed"),
    )

    with pytest.raises(ValueError, match="fixture corpus"):
        ingest.ingest_corpus(
            tmp_path,
            batch=BATCH_ID,
            generator=lambda _: [],
        )


def test_ingest_scope_must_match_active_departments():
    expected = {
        "Accountant", "HR", "HSE_5S", "ISO", "IT", "Maintenance",
        "Molding", "Planning", "Production", "Purchasing",
        "QualityControl", "Sales", "Technical", "Warehouse",
    }

    assert validate_active_departments(expected) == sorted(expected)
    with pytest.raises(RuntimeError, match="active departments"):
        validate_active_departments(expected | {"NewDepartment"})


def test_ingest_repairs_existing_qdrant_provenance_before_skip(
    monkeypatch,
    tmp_path: Path,
):
    from scripts.demo_wave import ingest_demo_corpus as ingest

    corpus = tmp_path / "corpus" / "Technical"
    corpus.mkdir(parents=True)
    document_path = corpus / "technical_decomp_core.md"
    document_path.write_text("synthetic fixture", encoding="utf-8")
    record = {
        "batch_id": BATCH_ID,
        "department": "Technical",
        "path": "corpus/Technical/technical_decomp_core.md",
        "title": "Technical fixture",
        "doc_number": "TK-100-D1",
        "document_type": "generic",
        "effective_status": "effective",
        "should_serve": True,
        "is_current": True,
        "effective_date": "2026-01-01",
        "expiry_date": "2030-01-01",
        "site": "HQ",
        "security_level": "internal",
    }
    (tmp_path / "corpus_manifest.jsonl").write_text(
        json.dumps(record) + "\n",
        encoding="utf-8",
    )
    existing = MagicMock()
    existing.fetchone.return_value = (101, "published", "effective")
    governance = MagicMock()
    governance.mappings.return_value.first.return_value = {
        "KnowledgeOwnerUserID": 1,
        "KnowledgeApproverUserID": 2,
        "TaxonomyVersion": "v1",
        "DocumentTypesJson": '["generic"]',
    }
    connection = MagicMock()
    connection.execute.side_effect = [existing, governance]
    context = MagicMock()
    context.__enter__.return_value = connection
    fake_engine = SimpleNamespace(connect=lambda: context)
    metadata_updates = []
    monkeypatch.setattr(ingest, "_ensure_engine", lambda: None)
    monkeypatch.setattr(ingest, "engine", fake_engine)
    monkeypatch.setattr(
        ingest,
        "update_qdrant_metadata",
        lambda doc_id, metadata, require_points=False: (
            metadata_updates.append((doc_id, metadata, require_points)) or True
        ),
    )
    monkeypatch.setattr(
        ingest,
        "delete_document_completely",
        lambda *_args, **_kwargs: pytest.fail("must not delete repaired fixture"),
    )

    report = ingest.ingest_corpus(
        tmp_path,
        batch=BATCH_ID,
        generator=lambda _: [],
    )

    assert report == {
        "requested": 1,
        "completed": 0,
        "skipped": 1,
        "failed": 0,
    }
    assert metadata_updates[0][0] == 101
    assert metadata_updates[0][1]["source_system"] == BATCH_ID
    assert metadata_updates[0][2] is True


@pytest.mark.parametrize("early_failure", [False, True])
def test_ingest_cleans_new_document_when_finalize_fails(
    monkeypatch,
    tmp_path: Path,
    early_failure: bool,
):
    from scripts.demo_wave import ingest_demo_corpus as ingest

    corpus = tmp_path / "corpus" / "Technical"
    corpus.mkdir(parents=True)
    document_path = corpus / "technical_decomp_core.md"
    document_path.write_text("synthetic fixture", encoding="utf-8")
    record = {
        "batch_id": BATCH_ID,
        "department": "Technical",
        "site": "HQ",
        "security_level": "internal",
        "path": "corpus/Technical/technical_decomp_core.md",
        "title": "Technical fixture",
        "doc_number": "TK-100-D1",
        "document_type": "generic",
        "effective_status": "effective",
        "effective_date": "2026-01-01",
        "expiry_date": "2030-01-01",
        "is_current": True,
        "should_serve": True,
    }
    (tmp_path / "corpus_manifest.jsonl").write_text(
        json.dumps(record) + "\n",
        encoding="utf-8",
    )
    existing = MagicMock()
    existing.fetchone.return_value = None
    governance = MagicMock()
    governance.mappings.return_value.first.return_value = {
        "KnowledgeOwnerUserID": 1,
        "KnowledgeApproverUserID": 2,
        "TaxonomyVersion": "v1",
        "DocumentTypesJson": '["generic"]',
    }
    read_connection = MagicMock()
    read_connection.execute.side_effect = [existing, governance]
    read_context = MagicMock()
    read_context.__enter__.return_value = read_connection
    write_connection = MagicMock()
    inserted = MagicMock()
    inserted.scalar_one.return_value = 202
    write_connection.execute.side_effect = [inserted, MagicMock(), MagicMock()]
    write_context = MagicMock()
    write_context.__enter__.return_value = write_connection
    fake_engine = SimpleNamespace(
        connect=lambda: read_context,
        begin=lambda: write_context,
    )
    deleted = []

    class FailedPublication:
        error = "publication failed"

        def __bool__(self):
            return False

    monkeypatch.setattr(ingest, "_ensure_engine", lambda: None)
    monkeypatch.setattr(ingest, "engine", fake_engine)
    monkeypatch.setattr(ingest, "create_ingestion_job", lambda **_kwargs: 12)
    monkeypatch.setattr(ingest, "update_ingestion_job", lambda *_args: True)
    monkeypatch.setattr(
        ingest,
        "learn_new_file",
        lambda *_args, **_kwargs: (True, "ok", {}),
    )
    monkeypatch.setattr(
        ingest,
        "publish_document",
        lambda *_args, **_kwargs: FailedPublication(),
    )
    monkeypatch.setattr(
        ingest,
        "delete_document_completely",
        lambda doc_id, reviewer: deleted.append((doc_id, reviewer)) or True,
    )
    if early_failure:
        def fail_before_doc_lookup(*_args, **_kwargs):
            raise ingest._FixtureDocumentError(None, "lookup failed")

        monkeypatch.setattr(
            ingest,
            "_finalize_ingested_document",
            fail_before_doc_lookup,
        )
        monkeypatch.setattr(
            ingest,
            "_find_partial_fixture",
            lambda *_args: 303,
        )

    report = ingest.ingest_corpus(
        tmp_path,
        batch=BATCH_ID,
        generator=lambda _: [],
    )

    assert report["failed"] == 1
    expected_doc_id = 303 if early_failure else 202
    assert deleted == [
        (expected_doc_id, "demo-fixture-failure-cleanup")
    ]
