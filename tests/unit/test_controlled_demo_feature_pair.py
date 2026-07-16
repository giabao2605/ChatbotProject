import json

import pytest


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("stage", "baseline_enabled", "candidate_enabled"),
    [
        ("crag", set(), {"RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"}),
        (
            "grounded_math",
            {"RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"},
            {
                "RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED",
                "RAG_GROUNDED_MATH_ENABLED",
            },
        ),
        (
            "query_decomposition",
            {
                "RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED",
                "RAG_GROUNDED_MATH_ENABLED",
            },
            {
                "RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED",
                "RAG_GROUNDED_MATH_ENABLED", "RAG_QUERY_DECOMPOSITION_ENABLED",
            },
        ),
    ],
)
def test_feature_pair_environment_isolates_exact_stage_delta(
    stage, baseline_enabled, candidate_enabled,
):
    from scripts.controlled_demo_eval.run_feature_pair import (
        FEATURE_FLAGS,
        build_feature_environment,
    )

    baseline = build_feature_environment(
        stage, candidate=False, collection="TaiLieuKyThuat_v2",
    )
    candidate = build_feature_environment(
        stage, candidate=True, collection="TaiLieuKyThuat_v2",
    )

    assert baseline["QDRANT_COLLECTION"] == "TaiLieuKyThuat_v2"
    assert candidate["RAG_EVAL_PREFLIGHT_KIND"] == "controlled_demo"
    assert candidate["RAG_EXECUTION_CONTEXT"] == "evaluation"
    assert candidate["SEMANTIC_CACHE_ENABLED"] == "false"
    assert {
        name for name in FEATURE_FLAGS if baseline[name] == "true"
    } == baseline_enabled
    assert {
        name for name in FEATURE_FLAGS if candidate[name] == "true"
    } == candidate_enabled
    assert baseline["RAG_LATE_INTERACTION_ENABLED"] == "false"
    assert candidate["RAG_LATE_INTERACTION_ENABLED"] == "false"


def test_feature_pair_environment_rejects_unsupported_stage():
    from scripts.controlled_demo_eval.run_feature_pair import build_feature_environment

    with pytest.raises(ValueError, match="unsupported controlled-demo stage"):
        build_feature_environment("unknown", candidate=False, collection="main")


def test_feature_pair_rejects_non_main_collection():
    from scripts.controlled_demo_eval.run_feature_pair import validate_collection

    with pytest.raises(ValueError, match="TaiLieuKyThuat_v2"):
        validate_collection("MechChatbot_Controlled_Demo_v2")


def test_readiness_requires_full_preflight_and_clean_provider_smoke(tmp_path):
    from scripts.controlled_demo_eval.run_feature_pair import validate_readiness_artifacts

    preflight = tmp_path / "preflight.json"
    preflight.write_text(json.dumps({
        "schema": "controlled-demo-main-preflight-v1",
        "passed": True,
        "collection": "TaiLieuKyThuat_v2",
        "checked_cases": 44,
        "failures": [],
        "fixture_fingerprint": "snapshot",
    }), encoding="utf-8")
    smoke = tmp_path / "smoke.json"
    smoke.write_text(json.dumps({
        "schema": "provider-smoke-v1",
        "passed": True,
        "request_count": 5,
        "successful_requests": 5,
        "failed_requests": 0,
        "provider_retries": 0,
        "provider_configuration_sha256": "provider-sha",
        "provider_outcome": {"provider_blocked": False},
    }), encoding="utf-8")

    report = validate_readiness_artifacts(
        preflight, smoke, expected_provider_sha256="provider-sha",
    )

    assert report["snapshot_fingerprint"] == "snapshot"
    assert report["preflight"]["artifact_sha256"]
    assert report["provider_smoke"]["artifact_sha256"]

    invalid = json.loads(smoke.read_text(encoding="utf-8"))
    invalid["request_count"] = 4
    smoke.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(ValueError, match="provider smoke"):
        validate_readiness_artifacts(
            preflight, smoke, expected_provider_sha256="provider-sha",
        )


def test_inventory_binds_pair_to_prepared_milestone_manifest(tmp_path):
    from scripts.controlled_demo_eval.run_feature_pair import validate_manifest_inventory

    manifest = tmp_path / "crag.jsonl"
    manifest.write_text("{}\n", encoding="utf-8")
    import hashlib

    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({
        "schema": "controlled-demo-manifest-inventory-v1",
        "source_case_count": 44,
        "source_manifests": [{"path": "source.jsonl", "sha256": "source-sha"}],
        "groups": {
            "factual": {"case_count": 12},
            "insufficient_evidence": {"case_count": 3},
            "access_denied": {"case_count": 3},
            "grounded_math": {"case_count": 10},
            "complex": {"case_count": 9},
            "graphrag": {"case_count": 6},
            "global": {"case_count": 1},
        },
        "milestones": {
            "crag": {
                "path": str(manifest.resolve()),
                "case_count": 18,
                "minimum_cases": 20,
                "sha256": manifest_sha,
            }
        },
    }), encoding="utf-8")

    report = validate_manifest_inventory(inventory, "crag", manifest)

    assert report["manifest_sha256"] == manifest_sha
    assert report["inventory"]["artifact_sha256"]

    manifest.write_text('{"changed":true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="inventory"):
        validate_manifest_inventory(inventory, "crag", manifest)
