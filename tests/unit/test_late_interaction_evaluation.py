from __future__ import annotations

import json

import pytest

from mech_chatbot.evaluation.late_interaction import (
    LateInteractionManifestError,
    build_report,
    evaluate_variant,
    load_manifest,
    preflight_manifest,
)


def _doc(name, *, doc_id, page=1, version="2", score=None):
    metadata = {
        "file_goc": name,
        "doc_id": doc_id,
        "trang_so": page,
        "version_no": version,
        "servable": True,
        "phong_ban_quyen": "Technical",
        "site": "HQ",
        "security_level": "internal",
    }
    if score is not None:
        metadata["retrieval_score"] = score
    return type("Doc", (), {"page_content": name, "metadata": metadata})()


def _case(**overrides):
    case = {
        "case_id": "exact-code",
        "scenario": "exact_code",
        "query": "TK-100-V2",
        "identity": {
            "user_department": "Technical",
            "user_roles": ["viewer"],
            "allowed_departments": ["Technical"],
            "allowed_sites": ["HQ"],
            "max_security_level": "internal",
        },
        "expected_sources": [
            {"document": "technical_effective_core.md", "doc_id": 32, "page": 1, "version": 2, "relevance": 3}
        ],
        "forbidden_sources": [],
    }
    case.update(overrides)
    return case


def test_manifest_rejects_cases_without_explicit_identity(tmp_path):
    path = tmp_path / "manifest.jsonl"
    case = _case()
    case["identity"].pop("allowed_sites")
    path.write_text(json.dumps(case) + "\n", encoding="utf-8")

    with pytest.raises(LateInteractionManifestError, match="allowed_sites"):
        load_manifest(path)


def test_preflight_fails_when_expected_provenance_is_absent():
    result = preflight_manifest(
        [_case()],
        available_sources=[{"document": "other.md", "doc_id": 99, "page": 1, "version": 1}],
        snapshot={"source_collection": "source", "shadow_index_version": "late-v2"},
    )

    assert result["passed"] is False
    assert result["missing_expected_sources"] == ["exact-code:technical_effective_core.md"]


def test_rrf_variant_never_calls_external_reranker():
    docs = [_doc("a.md", doc_id=1), _doc("b.md", doc_id=2)]

    result = evaluate_variant(_case(), docs, variant="rrf")

    assert [d.metadata["doc_id"] for d in result.documents] == [1, 2]
    assert result.fallback_reason is None
    assert result.used_backend == "rrf"


def test_maxsim_partial_coverage_preserves_governed_candidates():
    docs = [_doc("a.md", doc_id=1), _doc("b.md", doc_id=2)]

    def partial_shadow(_docs, _query):
        return {
            "documents": list(reversed(_docs)),
            "used_shadow": False,
            "shadow_hits": 1,
            "coverage": 0.5,
            "fallback_reason": "partial_shadow_coverage",
            "total_latency_ms": 7,
        }

    result = evaluate_variant(_case(), docs, variant="maxsim", shadow_rerank=partial_shadow)

    assert result.documents == docs
    assert result.fallback_reason == "partial_shadow_coverage"
    assert result.coverage == 0.5


def test_reranker_cannot_add_document_outside_governed_candidates():
    docs = [_doc("a.md", doc_id=1)]
    escaped = _doc("restricted.md", doc_id=999)

    result = evaluate_variant(
        _case(),
        docs,
        variant="voyage",
        voyage_rerank=lambda _docs, _query: [escaped, *_docs],
    )

    assert result.documents == docs
    assert result.fallback_reason == "governance_escape"


def test_report_uses_worked_graded_ndcg_and_flags_forbidden_source():
    case = _case(
        expected_sources=[
            {"document": "best.md", "doc_id": 1, "relevance": 3},
            {"document": "related.md", "doc_id": 2, "relevance": 1},
        ],
        forbidden_sources=[{"document": "draft.md", "doc_id": 3}],
    )
    rows = [
        {
            "case": case,
            "ranked_sources": [
                {"document": "related.md", "doc_id": 2},
                {"document": "best.md", "doc_id": 1},
                {"document": "draft.md", "doc_id": 3},
            ],
            "latency_ms": 100,
            "coverage": 1.0,
            "fallback_reason": None,
        }
    ]

    report = build_report(rows, variant="maxsim", run_metadata={"snapshot_fingerprint": "abc"})

    assert report["ranked_retrieval"]["recall_at_5"] == 1.0
    assert report["ranked_retrieval"]["ndcg_at_5"] == pytest.approx(0.7098097414)
    assert report["outcome_confusion"]["wrong_answer"] == 1
    assert report["outcome_confusion"]["leakage"] == 1
    assert report["latency_p95_ms"] == 100
    assert report["fallback_coverage"]["shadow_coverage"] == 1.0
