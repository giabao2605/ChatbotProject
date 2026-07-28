import json
from decimal import Decimal

import pytest

from scripts.decomposition_eval.constants import BOM_ROWS, FIXTURE_COLLECTION
from scripts.decomposition_eval.generate_manifest import cases
from scripts.decomposition_eval.preflight import check_fixture_cases, validate_manifest_scope
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
        "owner_department": document["OwnerDepartment"],
        "phong_ban_quyen": [document["OwnerDepartment"]],
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
        "ID": 100 + index, "DocID": 12, "TrangSo": 1,
        "SoLuong": Decimal(row["value"]), "Unit": row["unit"],
        "RawRowJson": __import__("json").dumps(row, ensure_ascii=False),
    } for index, row in enumerate(BOM_ROWS, 1)]
    return documents, rows, [_point(document) for document in documents]


def test_manifest_covers_every_roadmap_scenario_and_simple_has_no_branches():
    values = cases()
    ids = {case["id"] for case in values}

    assert len(values) == 13
    assert {
        "decomp-simple-factual", "decomp-simple-alias", "decomp-simple-install",
        "decomp-two-intents", "decomp-three-intents", "decomp-sql-bom-doc",
        "decomp-version-candidate", "decomp-sufficient-missing",
        "decomp-access-denied", "decomp-code-boundary",
        "decomp-bom-alias", "decomp-install-version", "decomp-three-source-compare",
    } == ids
    complex_cases = [case for case in values if case["evaluation_group"] == "complex"]
    simple_cases = [case for case in values if case["evaluation_group"] == "simple"]
    assert len(complex_cases) >= 10
    assert len(simple_cases) >= 3
    assert all(case["expected_branches"] == [] for case in simple_cases)
    assert max(len(case["expected_branches"]) for case in values) == 3
    version_case = next(
        case for case in values if case["id"] == "decomp-version-candidate"
    )
    assert [
        claim["required_terms"] for claim in version_case["expected_claims"]
    ] == [
        ["phiên bản", "CRAG-EVAL-NUM-001", "12"],
        ["phiên bản", "CRAG-EVAL-ALIAS-001", "1"],
    ]


def test_manifest_scope_requires_ten_complex_and_three_simple_negative_cases():
    report = validate_manifest_scope(cases())

    assert report == {"complex": 10, "simple": 3}
    with pytest.raises(ValueError, match="at least 10 complex"):
        validate_manifest_scope(cases()[:-1])
    with pytest.raises(ValueError, match="at least 3 simple"):
        validate_manifest_scope([
            case for case in cases() if case["id"] != "decomp-simple-install"
        ])


def test_manifest_labels_every_case_that_runs_grounded_math():
    math_cases = [case for case in cases() if case.get("requires_grounded_math")]

    assert len(math_cases) == 3
    assert all(case.get("expected_calculation") for case in math_cases)
    assert all(
        [source["source_row_key"] for source in case["expected_calculation"]["sources"]]
        == ["decomp-row-a", "decomp-row-b"]
        for case in math_cases
    )


def test_preflight_resolves_dynamic_source_identity_and_checks_restricted_source():
    documents, rows, points = _fixture()
    report = check_fixture_cases(cases(), documents, rows, points, collection=FIXTURE_COLLECTION)

    assert report["passed"] is True
    resolved = report["case_resolutions"]["decomp-two-intents"]
    assert resolved["expected_citations"][0]["doc_id"] == 10
    assert resolved["expected_citations"][0]["source_id"] == "D10P1"
    assert resolved["expected_claims"][0]["allowed_source_ids"] == ["D10P1"]


def test_preflight_resolves_grounded_math_row_sources():
    documents, rows, points = _fixture()
    report = check_fixture_cases(cases(), documents, rows, points, collection=FIXTURE_COLLECTION)

    resolved = report["case_resolutions"]["decomp-sql-bom-doc"][
        "expected_calculation"
    ]
    assert [source["doc_id"] for source in resolved["sources"]] == [12, 12]
    assert [source["source_id"] for source in resolved["sources"]] == [
        "BOM-101", "BOM-102",
    ]
    alias_math = report["case_resolutions"]["decomp-bom-alias"][
        "expected_calculation"
    ]
    assert "90 ngày" in alias_math["allowed_numbers"]
    mixed_math = report["case_resolutions"]["decomp-three-source-compare"][
        "expected_calculation"
    ]
    assert "1,500" in mixed_math["allowed_numbers"]


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


def test_rollout_can_read_fixture_from_main_collection():
    environment = build_evaluation_environment(
        enabled=True,
        collection="TaiLieuKyThuat_v2",
        fixture_batch="department-decomposition-eval-v1",
    )

    assert environment["QDRANT_COLLECTION"] == "TaiLieuKyThuat_v2"
    assert environment["RAG_EVAL_EXPECTED_COLLECTION"] == "TaiLieuKyThuat_v2"
    assert (
        environment["RAG_EVAL_FIXTURE_BATCH"]
        == "department-decomposition-eval-v1"
    )


def test_decomposition_rollout_records_runtime_provider_hash(monkeypatch, tmp_path):
    from mech_chatbot.config import settings as settings_module
    from mech_chatbot.config.settings import Settings
    from scripts.eval.provider_smoke import provider_configuration_sha256_for_settings
    from scripts.decomposition_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("{}\n", encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    output = tmp_path / "rollout"
    snapshot = Settings.from_env({
        "PROXYLLM_API_KEY": "test-provider-key",
        "PROXYLLM_BASE_URL": "https://provider.example/v1",
        "GPT_MODEL_NAME": "snapshot-model",
        "MAX_CONCURRENT_RAG": "7",
    })
    expected = provider_configuration_sha256_for_settings(snapshot)
    smoke = tmp_path / "provider-smoke.json"
    smoke.write_text(
        json.dumps({
            "schema": "provider-smoke-v1",
            "passed": True,
            "request_count": 5,
            "successful_requests": 5,
            "failed_requests": 0,
            "provider_retries": 0,
            "provider_configuration_sha256": expected,
            "provider_outcome": {"provider_blocked": False},
        }),
        encoding="utf-8",
    )

    monkeypatch.setenv(rollout.LIVE_OPT_IN, "1")
    monkeypatch.setattr(settings_module, "load_settings", lambda: snapshot)
    monkeypatch.setattr(rollout, "require_clean_worktree", lambda: None)
    monkeypatch.setattr(
        rollout.subprocess, "check_output", lambda *args, **kwargs: "abc123\n"
    )

    def fake_arm(label, *args, **kwargs):
        run_dir = output / label
        run_dir.mkdir(parents=True)
        for name, payload in (
            ("eval.json", {"schema": "rag-labeled-eval-v4"}),
            ("trace.json", {"schema": "rag-refusal-snapshot-v1"}),
            ("preflight.json", {"fixture_fingerprint": "fixture-sha"}),
        ):
            (run_dir / name).write_text(json.dumps(payload), encoding="utf-8")
        return {
            "started_at": "2026-07-28T00:00:00Z",
            "completed_at": "2026-07-28T00:01:00Z",
            "runner_exit": 0,
        }

    def fake_subprocess_run(command, **kwargs):
        gate_path = output / "gate.json"
        gate_path.write_text(
            json.dumps({
                "schema": "retrieval-intelligence-gate-v1",
                "passed": True,
            }),
            encoding="utf-8",
        )
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(rollout, "_run", fake_arm)
    monkeypatch.setattr(rollout.subprocess, "run", fake_subprocess_run)
    from mech_chatbot.evaluation import rollout_guardrails
    monkeypatch.setattr(
        rollout_guardrails,
        "evaluate_rollout_pair",
        lambda pair: {
            "production_eligible": False,
            "checks": {"rollback_contract_valid": False},
        },
    )

    report = rollout.run_rollout(
        manifest,
        output,
        trace,
        provider_smoke_artifact=smoke,
        collection="TaiLieuKyThuat_v2",
        fixture_batch="department-decomposition-eval-v1",
    )
    pair = json.loads((output / "rollout_pair.json").read_text(encoding="utf-8"))

    assert pair["baseline"]["provider_configuration_sha256"] == expected
    assert pair["candidate"]["provider_configuration_sha256"] == expected
    assert report["passed"] is False
    assert report["production_eligible"] is False
    assert report["decision_status"] == "pending_human_review"
    assert pair["data_plane"]["mutation_mode"] == "in_place"
    assert report["guardrail_checks"] == {"rollback_contract_valid": False}


def test_decomposition_rollout_rejects_mismatched_provider_smoke(
    monkeypatch, tmp_path
):
    from mech_chatbot.config import settings as settings_module
    from mech_chatbot.config.settings import Settings
    from scripts.decomposition_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("{}\n", encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    smoke = tmp_path / "provider-smoke.json"
    smoke.write_text(
        json.dumps({
            "schema": "provider-smoke-v1",
            "passed": True,
            "request_count": 5,
            "successful_requests": 5,
            "failed_requests": 0,
            "provider_retries": 0,
            "provider_configuration_sha256": "different-provider",
            "provider_outcome": {"provider_blocked": False},
        }),
        encoding="utf-8",
    )

    monkeypatch.setenv(rollout.LIVE_OPT_IN, "1")
    monkeypatch.setattr(
        settings_module,
        "load_settings",
        lambda: Settings.from_env({
            "PROXYLLM_API_KEY": "test-provider-key",
            "GPT_MODEL_NAME": "snapshot-model",
        }),
    )
    monkeypatch.setattr(rollout, "require_clean_worktree", lambda: None)

    with pytest.raises(ValueError, match="provider smoke"):
        rollout.run_rollout(
            manifest,
            tmp_path / "rollout",
            trace,
            provider_smoke_artifact=smoke,
        )
