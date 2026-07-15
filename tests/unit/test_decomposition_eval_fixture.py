from decimal import Decimal

import pytest

from scripts.decomposition_eval.constants import BOM_ROWS, FIXTURE_COLLECTION
from scripts.decomposition_eval.generate_manifest import cases
from scripts.decomposition_eval.preflight import check_fixture_cases
from scripts.decomposition_eval.run_rollout import build_evaluation_environment


pytestmark = pytest.mark.unit


def _document(doc_id, filename, version=1, *, site="CRAG-EVAL-HQ", security="internal"):
    return {
        "DocID": doc_id, "TenFile": filename, "VersionNo": version,
        "LifecycleStatus": "published", "ReviewStatus": "approved",
        "PublicationState": "published", "IsCurrent": True, "Servable": True,
        "SourceSystem": "crag-eval-v1", "OwnerDepartment": "Technical",
        "Site": site, "SecurityLevel": security,
    }


def _point(document):
    return {
        "doc_id": document["DocID"], "page": 1, "version_no": document["VersionNo"],
        "source_system": "crag-eval-v1", "servable": True, "is_current": True,
        "site": document["Site"], "security_level": document["SecurityLevel"],
    }


def _fixture():
    documents = [
        _document(10, "crag_eval_numbers_v12.md", 12),
        _document(11, "crag_eval_alias_v1.md"),
        _document(12, "crag_eval_bom_v1.md"),
        _document(13, "crag_eval_no_cost_v1.md"),
        _document(14, "crag_eval_restricted_v1.md", site="CRAG-EVAL-REMOTE", security="confidential"),
    ]
    rows = [{
        "DocID": 12, "SoLuong": Decimal(row["value"]), "Unit": row["unit"],
        "RawRowJson": __import__("json").dumps(row, ensure_ascii=False),
    } for row in BOM_ROWS]
    return documents, rows, [_point(document) for document in documents]


def test_manifest_covers_every_roadmap_scenario_and_simple_has_no_branches():
    values = cases()
    ids = {case["id"] for case in values}

    assert len(values) == 8
    assert {"decomp-simple-factual", "decomp-two-intents", "decomp-three-intents", "decomp-sql-bom-doc", "decomp-version-candidate", "decomp-sufficient-missing", "decomp-access-denied", "decomp-code-boundary"} == ids
    assert next(case for case in values if case["evaluation_group"] == "simple")["expected_branches"] == []
    assert max(len(case["expected_branches"]) for case in values) == 3


def test_preflight_resolves_dynamic_source_identity_and_checks_restricted_source():
    documents, rows, points = _fixture()
    report = check_fixture_cases(cases(), documents, rows, points, collection=FIXTURE_COLLECTION)

    assert report["passed"] is True
    resolved = report["case_resolutions"]["decomp-two-intents"]
    assert resolved["expected_citations"][0]["doc_id"] == 10
    assert resolved["expected_citations"][0]["source_id"] == "D10P1"
    assert resolved["expected_claims"][0]["allowed_source_ids"] == ["D10P1"]


def test_preflight_fails_closed_when_bom_provenance_is_missing():
    documents, rows, points = _fixture()
    report = check_fixture_cases(cases(), documents, rows[:-1], points, collection=FIXTURE_COLLECTION)

    assert report["passed"] is False
    assert any(item["reason"] == "bom_source_row_missing" for item in report["failures"])


def test_rollout_toggles_only_decomposition_between_arms(monkeypatch):
    monkeypatch.setenv("RAG_QUERY_DECOMPOSITION_ENABLED", "stale")
    baseline = build_evaluation_environment(enabled=False)
    candidate = build_evaluation_environment(enabled=True)

    assert baseline["RAG_QUERY_DECOMPOSITION_ENABLED"] == "false"
    assert candidate["RAG_QUERY_DECOMPOSITION_ENABLED"] == "true"
    for flag in ("RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED", "RAG_GROUNDED_MATH_ENABLED"):
        assert baseline[flag] == candidate[flag] == "true"
    assert baseline["QDRANT_COLLECTION"] == candidate["QDRANT_COLLECTION"] == FIXTURE_COLLECTION
