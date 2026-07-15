import pytest

from scripts.eval.run_eval import load_manifest_files
from scripts.grounded_math_eval.constants import FIXTURE_BATCH, FIXTURE_COLLECTION
from scripts.grounded_math_eval.generate_fixture import generate_fixture
from scripts.grounded_math_eval.preflight import check_fixture_cases
from scripts.grounded_math_eval.cleanup_fixture import build_cleanup_plan
from scripts.grounded_math_eval.run_rollout import build_evaluation_environment


pytestmark = pytest.mark.unit


def test_grounded_math_fixture_is_deterministic_and_manifest_is_valid(tmp_path):
    first = generate_fixture(tmp_path)
    first_manifest = (tmp_path / "eval_manifest.jsonl").read_bytes()
    second = generate_fixture(tmp_path)

    cases = load_manifest_files([tmp_path / "eval_manifest.jsonl"])

    assert first == second == {
        "batch_id": FIXTURE_BATCH, "part_code_prefix": "GROUND-MATH-EVAL-",
        "documents": 3, "bom_rows": 7, "cases": 15,
    }
    assert (tmp_path / "eval_manifest.jsonl").read_bytes() == first_manifest
    assert {case["evaluation_group"] for case in cases} == {"grounded_math"}
    assert {case["expected_calculation"]["status"] for case in cases} >= {
        "valid", "missing_operand", "division_by_zero", "ambiguous_unit", "mixed_version",
        "ambiguous_provenance", "unsupported_operation",
    }


def test_grounded_math_preflight_resolves_real_document_and_bom_row_ids(tmp_path):
    generate_fixture(tmp_path)
    cases = load_manifest_files([tmp_path / "eval_manifest.jsonl"])
    governance = {
        "OwnerDepartment": "Technical", "Site": "GROUND-MATH-EVAL-HQ",
        "SecurityLevel": "internal",
    }
    documents = [
        {
            "DocID": 41, "TenFile": "grounded_math_bom_v12.md", "VersionNo": 12,
            "LifecycleStatus": "published", "ReviewStatus": "approved",
            "PublicationState": "published", "IsCurrent": True, "Servable": True,
            "SourceSystem": FIXTURE_BATCH, **governance,
        },
        {
            "DocID": 42, "TenFile": "grounded_math_bom_v11.md", "VersionNo": 11,
            "LifecycleStatus": "published", "ReviewStatus": "approved",
            "PublicationState": "published", "IsCurrent": True, "Servable": True,
            "SourceSystem": FIXTURE_BATCH, **governance,
        },
        {
            "DocID": 43, "TenFile": "grounded_math_other_v12.md", "VersionNo": 12,
            "LifecycleStatus": "published", "ReviewStatus": "approved",
            "PublicationState": "published", "IsCurrent": True, "Servable": True,
            "SourceSystem": FIXTURE_BATCH, **governance,
        },
    ]
    row_specs = {
        "row-a": (101, 41, "2", "kg"), "row-b": (102, 41, "4", "kg"),
        "row-factor": (103, 41, "2", ""), "row-zero": (104, 41, "0", ""),
        "row-metre": (105, 41, "3", "m"), "row-old-a": (106, 42, "1", "kg"),
        "row-other": (107, 43, "1", "kg"),
    }
    rows = [
        {"ID": row_id, "DocID": doc_id, "TrangSo": 1, "SoLuong": value,
         "Unit": unit, "SourceTableIndex": index,
         "RawRowJson": '{"row_key":"' + key + '"}'}
        for index, (key, (row_id, doc_id, value, unit)) in enumerate(row_specs.items(), 1)
    ]
    points = [
        {"doc_id": doc_id, "page": 1, "source_system": FIXTURE_BATCH,
         "servable": True, "is_current": True, "version_no": version,
         "owner_department": "Technical", "site": "GROUND-MATH-EVAL-HQ",
         "security_level": "internal", "lifecycle_status": "published",
         "review_status": "approved", "publication_state": "published"}
        for doc_id, version in ((41, 12), (42, 11), (43, 12))
    ]

    report = check_fixture_cases(
        cases, documents, rows, points, collection=FIXTURE_COLLECTION
    )

    assert report["passed"] is True
    resolved = report["case_resolutions"]["math-add"]
    assert [source["source_id"] for source in resolved["expected_calculation"]["sources"]] == [
        "BOM-101", "BOM-102",
    ]
    assert resolved["expected_citations"] == [{
        "document": "grounded_math_bom_v12.md", "doc_id": 41, "page": 1,
        "version": 12, "source_id": "D41P1",
    }]


def test_grounded_math_preflight_fails_on_source_row_drift(tmp_path):
    generate_fixture(tmp_path)
    case = load_manifest_files([tmp_path / "eval_manifest.jsonl"])[0]
    document = {
        "DocID": 41, "TenFile": "grounded_math_bom_v12.md",
        "LifecycleStatus": "published", "ReviewStatus": "approved",
        "PublicationState": "published", "IsCurrent": True, "Servable": True,
        "SourceSystem": FIXTURE_BATCH, "VersionNo": 12,
        "OwnerDepartment": "Technical", "Site": "GROUND-MATH-EVAL-HQ",
        "SecurityLevel": "internal",
    }
    rows = [
        {"ID": 101, "DocID": 41, "TrangSo": 1, "SoLuong": "999", "Unit": "kg",
         "SourceTableIndex": index, "RawRowJson": '{"row_key":"' + key + '"}'}
        for index, key in enumerate(("row-a", "row-b"), 1)
    ]
    points = [{
        "doc_id": 41, "page": 1, "source_system": FIXTURE_BATCH,
        "servable": True, "is_current": True, "version_no": 12,
        "owner_department": "Technical", "site": "GROUND-MATH-EVAL-HQ",
        "security_level": "internal", "lifecycle_status": "published",
        "review_status": "approved", "publication_state": "published",
    }]

    report = check_fixture_cases(
        [case], [document], rows, points, collection=FIXTURE_COLLECTION
    )

    assert report["passed"] is False
    assert {failure["reason"] for failure in report["failures"]} == {"bom_source_row_drift"}


def test_grounded_math_cleanup_scope_cannot_target_another_directory(tmp_path):
    workspace = tmp_path / "workspace"
    expected = workspace / "data" / "grounded_math_eval_v1"

    assert build_cleanup_plan(expected, workspace)["source_system"] == FIXTURE_BATCH
    with pytest.raises(ValueError, match="asset root"):
        build_cleanup_plan(workspace / "data" / "production", workspace)


def test_grounded_math_rollout_toggles_only_math_between_arms(monkeypatch):
    monkeypatch.setenv("RAG_CRAG_ENABLED", "false")
    baseline = build_evaluation_environment(enabled=False, router_mode="offline")
    candidate = build_evaluation_environment(enabled=True, router_mode="offline")

    assert baseline["RAG_CRAG_ENABLED"] == candidate["RAG_CRAG_ENABLED"] == "true"
    assert baseline["RAG_CLAIM_REPAIR_ENABLED"] == candidate["RAG_CLAIM_REPAIR_ENABLED"] == "true"
    assert baseline["RAG_GROUNDED_MATH_ENABLED"] == "false"
    assert candidate["RAG_GROUNDED_MATH_ENABLED"] == "true"
    assert candidate["RAG_EVAL_PREFLIGHT_KIND"] == "grounded_math"
    assert candidate["QDRANT_COLLECTION"] == FIXTURE_COLLECTION
