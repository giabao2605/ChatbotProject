import importlib.util
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _case(**overrides):
    case = {
        "id": "case-1",
        "question": "Gia tri la bao nhieu?",
        "expected_outcome": "full_answer",
        "user_department": "CRAG_EVAL",
        "user_roles": ["viewer"],
        "allowed_departments": ["CRAG_EVAL"],
        "allowed_sites": ["CRAG-EVAL-HQ"],
        "max_security_level": "internal",
        "expected_document": "crag_eval_numbers_v12.md",
        "expected_page": 1,
        "expected_version": 12,
        "expected_department": "CRAG_EVAL",
        "expected_site": "CRAG-EVAL-HQ",
        "expected_security_level": "internal",
    }
    case.update(overrides)
    return case


def test_manifest_validation_requires_complete_live_identity(tmp_path):
    runner = _load("run_eval_identity", "scripts/eval/run_eval.py")
    path = tmp_path / "cases.jsonl"
    invalid = _case()
    invalid.pop("allowed_sites")
    path.write_text(json.dumps(invalid) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="allowed_sites"):
        runner.load_manifest_files([path])


def test_manifest_validation_rejects_invalid_outcome_and_missing_provenance(tmp_path):
    runner = _load("run_eval_contract", "scripts/eval/run_eval.py")
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(_case(expected_outcome="maybe")) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid expected_outcome"):
        runner.load_manifest_files([path])

    path.write_text(json.dumps(_case(expected_document=None)) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected_document"):
        runner.load_manifest_files([path])


def test_manifest_v2_requires_labeled_claim_and_citation_ground_truth(tmp_path):
    runner = _load("run_eval_manifest_v2", "scripts/eval/run_eval.py")
    path = tmp_path / "cases.jsonl"
    path.write_text(
        json.dumps(_case(manifest_schema="rag-eval-manifest-v2")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expected_claims"):
        runner.load_manifest_files([path])

    valid = _case(
        manifest_schema="rag-eval-manifest-v2",
        expected_claims=[
            {
                "id": "nominal-value",
                "required_terms": ["1,500"],
                "allowed_source_ids": ["D41P1"],
            }
        ],
        expected_citations=[
            {
                "document": "crag_eval_numbers_v12.md",
                "doc_id": 41,
                "page": 1,
                "version": 12,
                "source_id": "D41P1",
            }
        ],
    )
    path.write_text(json.dumps(valid) + "\n", encoding="utf-8")

    loaded = runner.load_manifest_files([path])

    assert loaded[0]["manifest_schema"] == "rag-eval-manifest-v2"


def test_legacy_manifest_is_versioned_without_breaking_compatibility(tmp_path):
    runner = _load("run_eval_manifest_legacy", "scripts/eval/run_eval.py")
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(_case()) + "\n", encoding="utf-8")

    loaded = runner.load_manifest_files([path])

    assert loaded[0]["manifest_schema"] == "rag-eval-manifest-v1-legacy"


def test_manifest_v2_allows_clarification_without_grounded_claims(tmp_path):
    runner = _load("run_eval_manifest_clarification", "scripts/eval/run_eval.py")
    path = tmp_path / "cases.jsonl"
    path.write_text(
        json.dumps(
            _case(
                manifest_schema="rag-eval-manifest-v2",
                expected_outcome="clarification_required",
                expected_claims=[],
                expected_citations=[],
            )
        ) + "\n",
        encoding="utf-8",
    )

    assert runner.load_manifest_files([path])[0]["expected_claims"] == []

    path.write_text(json.dumps(_case(expected_document="")) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected_document"):
        runner.load_manifest_files([path])


def test_output_paths_are_isolated_by_run_label(tmp_path):
    runner = _load("run_eval_outputs", "scripts/eval/run_eval.py")

    baseline = runner.resolve_output_paths(tmp_path, "baseline")
    candidate = runner.resolve_output_paths(tmp_path, "candidate")

    assert baseline["json"] == tmp_path / "baseline" / "eval.json"
    assert baseline["markdown"] == tmp_path / "baseline" / "eval.md"
    assert candidate["json"] != baseline["json"]


def test_cli_accepts_multiple_manifests():
    runner = _load("run_eval_cli", "scripts/eval/run_eval.py")
    args = runner.parse_args([
        "--manifest", "one.jsonl", "--manifest", "two.jsonl",
        "--output-dir", "reports/run", "--run-label", "candidate",
    ])
    assert args.manifest == [Path("one.jsonl"), Path("two.jsonl")]


def test_main_evaluator_uses_typed_evaluation_invocation(tmp_path, monkeypatch):
    from mech_chatbot.rag.execution import (
        RagCompleted,
        RagDiagnostics,
        RagPrepared,
        RagToken,
    )

    monkeypatch.setenv("RAG_EXECUTION_CONTEXT", "production")
    runner = _load("run_eval_typed_execution", "scripts/eval/run_eval.py")
    manifest = tmp_path / "cases.jsonl"
    manifest.write_text(
        json.dumps(_case(expected_document="target.md", expected_sources=["target.md"])) + "\n",
        encoding="utf-8",
    )
    diagnostics = RagDiagnostics.from_mapping({
        "retrieved_docs": [{"file_goc": "target.md", "source_id": "D41P1"}],
        "pipeline_namespace": "typed-evaluation",
        "generation_metrics": {},
    })
    observed = []

    class FakeExecutor:
        def run(self, request, invocation, cancellation=None):
            observed.append((request, invocation))
            yield RagPrepared("", (), (), diagnostics)
            yield RagToken("Cau tra loi co can cu")
            yield RagCompleted("answered", invocation.trace_id, diagnostics)

    report, passed = runner.run_evaluation(
        [manifest],
        tmp_path / "output",
        "candidate",
        preflight=False,
        intent_extractor=lambda *args, **kwargs: (
            None, None, None, None, None, {"version_policy": "current_only"}
        ),
        rag_executor=FakeExecutor(),
        number_normalizer=lambda _value: set(),
    )

    assert passed is True
    request, invocation = observed[0]
    assert invocation.mode == "evaluation"
    assert invocation.trace_id == "eval:candidate:case-1"
    assert request.question == "Gia tri la bao nhieu?"
    assert request.access.department == "CRAG_EVAL"
    assert request.access.roles == frozenset({"viewer"})
    assert request.access.allowed_departments == frozenset({"CRAG_EVAL"})
    assert request.access.allowed_sites == frozenset({"CRAG-EVAL-HQ"})
    assert request.access.max_security_level == "internal"
    assert report["execution_context"] == "evaluation"
    assert report["pipeline_variants"]["typed-evaluation"]["cases"] == 1


def test_typed_evaluator_preserves_failure_diagnostics(tmp_path):
    from mech_chatbot.rag.execution import RagDiagnostics, RagFailed, RagPrepared

    runner = _load("run_eval_typed_failure", "scripts/eval/run_eval.py")
    manifest = tmp_path / "cases.jsonl"
    manifest.write_text(json.dumps(_case()) + "\n", encoding="utf-8")
    diagnostics = RagDiagnostics.from_mapping({
        "pipeline_namespace": "typed-evaluation",
        "correction_count": 1,
        "final_generation_count": 1,
        "generation_metrics": {
            "provider_retries": 2,
            "input_tokens": 120,
            "output_tokens": 8,
            "estimated_cost": 0.25,
            "repair_count": 1,
        },
    })

    class FailingExecutor:
        def run(self, request, invocation, cancellation=None):
            yield RagPrepared("", (), (), diagnostics)
            yield RagFailed(
                "RuntimeError",
                "provider unavailable after retries",
                True,
                RuntimeError("provider unavailable after retries"),
                diagnostics=diagnostics,
            )

    report, passed = runner.run_evaluation(
        [manifest],
        tmp_path / "output",
        "candidate",
        preflight=False,
        intent_extractor=lambda *args, **kwargs: (
            None, None, None, None, None, {"version_policy": "current_only"}
        ),
        rag_executor=FailingExecutor(),
        number_normalizer=lambda _value: set(),
    )

    assert passed is False
    assert report["provider_retries"] == 2
    assert report["total_input_tokens"] == 120
    assert report["total_output_tokens"] == 8
    assert report["total_estimated_cost"] == 0.25
    assert report["cases"][0]["correction_count"] == 1
    assert report["cases"][0]["repair_count"] == 1
    assert report["cases"][0]["final_generation_count"] == 1


def test_eval_v4_artifact_contains_shared_foundation_metrics(tmp_path, monkeypatch):
    runner = _load("run_eval_v4_artifact", "scripts/eval/run_eval.py")

    manifest = tmp_path / "cases.jsonl"
    case = _case(
        manifest_schema="rag-eval-manifest-v2",
        expected_document="target.md",
        expected_sources=["target.md"],
        expected_claims=[
            {
                "id": "nominal-value",
                "required_terms": ["1,500"],
                "allowed_source_ids": ["D41P1"],
            }
        ],
        expected_citations=[
            {
                "document": "target.md",
                "doc_id": 41,
                "page": 1,
                "version": 12,
                "source_id": "D41P1",
            }
        ],
    )
    manifest.write_text(json.dumps(case) + "\n", encoding="utf-8")

    intent_extractor = lambda *args, **kwargs: (
        None, None, None, None, None, {"version_policy": "current_only"}
    )
    retrieved = [
        {
            "file_goc": f"other-{index}.md",
            "source_id": f"D{index}P1",
        }
        for index in range(1, 12)
    ] + [
        {
            "file_goc": "target.md",
            "doc_id": 41,
            "source_id": "D41P1",
            "trang": 1,
            "version_no": 12,
        }
    ]
    answer = (
        "Giá trị định mức là 1,500. "
        "[Nguồn: target.md, Trang 1, Version 12, SourceID D41P1]"
    )
    rag_chat = lambda *args, **kwargs: (
            iter([answer]),
            "target.md Trang 1 Version 12 SourceID D41P1",
            [],
            [],
            {
                "retrieved_docs": retrieved,
                "citation_docs": [retrieved[-1]],
                "evidence_state": "SUFFICIENT",
                "pipeline_namespace": "eval-v4",
                "generation_metrics": {},
            },
        )

    report, passed = runner.run_evaluation(
        [manifest],
        tmp_path / "output",
        "candidate",
        preflight=False,
        intent_extractor=intent_extractor,
        rag_chat=rag_chat,
        number_normalizer=lambda _value: set(),
    )

    assert passed is True
    assert report["schema"] == "rag-labeled-eval-v4"
    assert report["evaluator_version"] == "evaluation-foundation-v1"
    assert set(report["pipeline_configuration"]["flags"]) == {
        "RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED",
        "RAG_GROUNDED_MATH_ENABLED", "RAG_LATE_INTERACTION_ENABLED",
        "RAG_QUERY_DECOMPOSITION_ENABLED", "RAG_GRAPH_RETRIEVAL_ENABLED",
        "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",
    }
    assert set(report["pipeline_configuration"]["versions"]) == {
        "RAG_PLANNER_VERSION", "RAG_LATE_INDEX_VERSION",
        "RAG_GRAPH_SERVING_EPOCH", "RAG_COMMUNITY_SERVING_EPOCH",
    }
    assert report["manifest_sha256s"] == [
        hashlib.sha256(manifest.read_bytes()).hexdigest()
    ]
    assert report["ranked_retrieval"]["recall_at_10"] == 0.0
    assert report["ranked_retrieval"]["recall_at_20"] == 1.0
    assert report["ranked_retrieval"]["mrr"] == pytest.approx(1 / 12)
    assert report["claim_evaluation"]["faithfulness"]["value"] == 1.0
    assert report["citation_evaluation"]["citation_accuracy"]["value"] == 1.0
    assert report["risk_coverage"]["selected_threshold"] is None
    assert (tmp_path / "output" / "candidate" / "eval.json").is_file()
    assert (tmp_path / "output" / "candidate" / "eval.md").is_file()


def test_eval_artifact_preserves_provider_retries_when_generation_stream_fails(tmp_path):
    runner = _load("run_eval_failed_stream_metrics", "scripts/eval/run_eval.py")
    manifest = tmp_path / "cases.jsonl"
    manifest.write_text(json.dumps(_case()) + "\n", encoding="utf-8")
    intent_extractor = lambda *args, **kwargs: (
        None, None, None, None, None, {"version_policy": "current_only"}
    )
    generation_metrics = {
        "provider_retries": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost": 0.0,
        "repair_count": 0,
        "calculation_count": 0,
    }

    def failed_stream():
        generation_metrics.update({
            "provider_retries": 2,
            "input_tokens": 120,
            "output_tokens": 8,
            "estimated_cost": 0.25,
            "repair_count": 1,
            "calculation_count": 1,
        })
        raise RuntimeError("provider unavailable after retries")
        yield "unreachable"

    rag_chat = lambda *args, **kwargs: (
        failed_stream(),
        "",
        [],
        [],
        {
            "pipeline_namespace": "eval-v4",
            "generation_metrics": generation_metrics,
            "correction_count": 1,
            "planner_count": 1,
            "graph_traversal_count": 1,
        },
    )

    report, passed = runner.run_evaluation(
        [manifest],
        tmp_path / "output",
        "baseline",
        preflight=False,
        intent_extractor=intent_extractor,
        rag_chat=rag_chat,
        number_normalizer=lambda _value: set(),
    )

    assert passed is False
    assert report["provider_retries"] == 2
    assert report["pipeline_variants"]["eval-v4"]["provider_retries"] == 2
    assert report["cases"][0]["provider_retries"] == 2
    assert report["total_input_tokens"] == 120
    assert report["total_output_tokens"] == 8
    assert report["total_estimated_cost"] == 0.25
    assert report["budget_counts"] == {
        "correction_count": 1,
        "repair_count": 1,
        "calculation_count": 1,
        "planner_count": 1,
        "subquery_count": 0,
        "final_generation_count": 0,
            "graph_traversal_count": 1,
            "graph_edge_count": 0,
        }


def test_eval_artifact_reports_failure_family_seed_and_holdout_coverage(tmp_path):
    runner = _load("run_eval_failure_families", "scripts/eval/run_eval.py")
    manifest = tmp_path / "failure-family.jsonl"
    case_ids = ["evidence-seed", "dev-1", "dev-2", "dev-3", "dev-4", "holdout-1", "holdout-2"]
    cases = []
    for case_id in case_ids:
        cases.append(_case(
            id=case_id,
            failure_family="EVIDENCE_POLICY_ERROR",
            seed_case_id="evidence-seed",
            expected_policy={
                "outcome": "full_answer",
                "evidence_state": "SUFFICIENT",
                "correction_allowed": False,
            },
            invariants=["leakage_zero", "governance_unchanged"],
            mutation_axes=["paraphrase"],
            holdout=case_id.startswith("holdout-"),
        ))
    manifest.write_text(
        "\n".join(json.dumps(case) for case in cases) + "\n",
        encoding="utf-8",
    )
    intent_extractor = lambda *args, **kwargs: (
        None, None, None, None, None, {"version_policy": "current_only"}
    )
    retrieved = {
        "file_goc": "crag_eval_numbers_v12.md",
        "doc_id": 41,
        "trang": 1,
        "version_no": 12,
    }
    rag_chat = lambda *args, **kwargs: (
        iter(["Giá trị được xác nhận từ tài liệu."]),
        "",
        [],
        [],
        {"retrieved_docs": [retrieved], "citation_docs": [], "evidence_state": "SUFFICIENT"},
    )

    report, passed = runner.run_evaluation(
        [manifest], tmp_path / "result", "candidate", preflight=False,
        intent_extractor=intent_extractor, rag_chat=rag_chat,
        number_normalizer=lambda _value: set(),
    )

    assert passed is True
    assert report["failure_family_evaluation"]["decision"] == "accepted"
    assert report["failure_family_evaluation"]["families"]["EVIDENCE_POLICY_ERROR"][
        "holdout_variant_count"
    ] == 2
    assert all(row["seed_case_id"] == "evidence-seed" for row in report["cases"])
    assert "Failure families" in (
        tmp_path / "result" / "candidate" / "eval.md"
    ).read_text(encoding="utf-8")


def test_eval_case_fails_when_runtime_answer_policy_differs_from_family_contract(tmp_path):
    runner = _load("run_eval_policy_contract", "scripts/eval/run_eval.py")
    manifest = tmp_path / "policy-contract.jsonl"
    manifest.write_text(json.dumps(_case(
        id="policy-seed",
        failure_family="EVIDENCE_POLICY_ERROR",
        seed_case_id="policy-seed",
        expected_policy={
            "outcome": "full_answer",
            "evidence_state": "AMBIGUOUS",
            "correction_allowed": True,
        },
        invariants=["leakage_zero"],
        mutation_axes=[],
        holdout=False,
    )) + "\n", encoding="utf-8")
    intent_extractor = lambda *args, **kwargs: (
        None, None, None, None, None, {"version_policy": "current_only"}
    )
    rag_chat = lambda *args, **kwargs: (
        iter(["Giá trị được xác nhận từ tài liệu."]),
        "",
        [],
        [],
        {
            "retrieved_docs": [{
                "file_goc": "crag_eval_numbers_v12.md",
                "doc_id": 41,
                "trang": 1,
                "version_no": 12,
            }],
            "citation_docs": [],
            "answer_outcome": "full_answer",
            "evidence_state": "SUFFICIENT",
            "correction_allowed": False,
        },
    )

    report, passed = runner.run_evaluation(
        [manifest], tmp_path / "result", "candidate", preflight=False,
        intent_extractor=intent_extractor, rag_chat=rag_chat,
        number_normalizer=lambda _value: set(),
    )

    assert passed is False
    assert report["cases"][0]["policy_evaluation"]["passed"] is False
    assert report["cases"][0]["policy_evaluation"]["actual"] == {
        "outcome": "full_answer",
        "evidence_state": "SUFFICIENT",
        "correction_allowed": False,
    }


def test_eval_report_includes_decomposition_branch_and_budget_evidence(tmp_path, monkeypatch):
    runner = _load("run_eval_decomposition", "scripts/eval/run_eval.py")
    manifest = tmp_path / "decomposition.jsonl"
    manifest.write_text(json.dumps(_case(
        evaluation_group="complex",
        expected_branches=[{
            "branch_id": "branch-1",
            "expected_outcome": "full_answer",
            "expected_citations": [{"document": "crag_eval_numbers_v12.md", "doc_id": 41}],
        }],
    )) + "\n", encoding="utf-8")
    monkeypatch.setenv("RAG_QUERY_DECOMPOSITION_ENABLED", "true")
    intent_extractor = lambda *args, **kwargs: (
        None, None, None, None, None, {"version_policy": "current_only"}
    )
    rag_chat = lambda *args, **kwargs: (
        iter(["Giá trị được xác nhận từ tài liệu."]),
        "",
        [],
        [],
        {
            "pipeline_namespace": "decomposition-v1",
            "retrieved_docs": [{"file_goc": "crag_eval_numbers_v12.md", "doc_id": 41}],
            "citation_docs": [],
            "planner_count": 1,
            "subquery_count": 1,
            "correction_count": 0,
            "final_generation_count": 1,
            "deadline_exceeded": False,
            "decomposition_branches": [{
                "branch_id": "branch-1",
                "outcome": "full_answer",
                "citations": [{"document": "crag_eval_numbers_v12.md", "doc_id": 41}],
            }],
        },
    )

    report, passed = runner.run_evaluation(
        [manifest], tmp_path / "output", "candidate", preflight=False,
        intent_extractor=intent_extractor, rag_chat=rag_chat,
        number_normalizer=lambda _value: set(),
    )

    assert passed is True
    assert report["decomposition_evaluation"]["branch_accuracy"] == 1.0
    assert report["decomposition_evaluation"]["citation_accuracy"] == 1.0
    assert report["decomposition_evaluation"]["budget_violations"] == 0


def test_regular_baseline_is_not_forced_to_emit_graph_edge_evidence(tmp_path, monkeypatch):
    runner = _load("run_eval_graph_baseline", "scripts/eval/run_eval.py")
    case = _case(
        evaluation_group="relational",
        expected_relation={
            "source_key": "document:41", "relation_type": "HAS_PAGE",
            "target_key": "page:41:1",
        },
    )
    manifest = tmp_path / "graph.jsonl"
    manifest.write_text(json.dumps(case) + "\n", encoding="utf-8")
    intent_extractor = lambda *args, **kwargs: (
        None, None, None, None, None, {"version_policy": "current_only"}
    )
    rag_chat = lambda *args, **kwargs: (
        iter(["Giá trị được xác nhận từ tài liệu."]), "", [], [], {
            "retrieved_docs": [{
                "file_goc": "crag_eval_numbers_v12.md", "doc_id": 41,
                "trang": 1, "version_no": 12,
            }],
            "citation_docs": [], "evidence_state": "SUFFICIENT",
            "generation_metrics": {},
        },
    )
    monkeypatch.setenv("RAG_GRAPH_RETRIEVAL_ENABLED", "false")
    baseline, baseline_passed = runner.run_evaluation(
        [manifest], tmp_path / "baseline", "baseline", preflight=False,
        intent_extractor=intent_extractor, rag_chat=rag_chat,
        number_normalizer=lambda _value: set(),
    )
    monkeypatch.setenv("RAG_GRAPH_RETRIEVAL_ENABLED", "true")
    candidate, candidate_passed = runner.run_evaluation(
        [manifest], tmp_path / "candidate", "candidate", preflight=False,
        intent_extractor=intent_extractor, rag_chat=rag_chat,
        number_normalizer=lambda _value: set(),
    )

    assert baseline_passed is True
    assert baseline["cases"][0]["graph_evaluation"]["relation_matched"] is False
    assert candidate_passed is False
    assert candidate["cases"][0]["graph_evaluation"]["relation_matched"] is False


def test_eval_runner_requires_exact_grounded_math_and_provenance(tmp_path):
    runner = _load("run_eval_grounded_math", "scripts/eval/run_eval.py")
    source = {
        "document": "bom-v12.pdf", "doc_id": 41, "page": 3,
        "version": 12, "source_id": "BOM-1", "value": "7", "unit": "kg",
    }
    expected_calculation = {
        "operation": "sum", "status": "valid", "exact_value": "7",
        "display_value": "7", "formula": "2 + 5 = 7 kg", "unit": "kg",
        "allowed_numbers": ["2", "5"], "sources": [source],
    }
    manifest = tmp_path / "grounded-math.jsonl"
    manifest.write_text(json.dumps(_case(
        manifest_schema="rag-eval-manifest-v2",
        evaluation_group="grounded_math",
        expected_document="bom-v12.pdf",
        expected_page=3,
        expected_claims=[],
        expected_citations=[source],
        expected_calculation=expected_calculation,
    )) + "\n", encoding="utf-8")
    intent_extractor = lambda *args, **kwargs: (
        None, None, None, None, None, {"version_policy": "current_only"}
    )
    retrieved = {
        "file_goc": "bom-v12.pdf", "doc_id": 41, "trang": 3,
        "version_no": 12, "source_id": "BOM-1",
    }
    provenance = dict(expected_calculation)
    rag_chat = lambda *args, **kwargs: (
        iter(["Kết quả 7 kg. Công thức 2 + 5 = 7 kg. "
              "[Nguồn: bom-v12.pdf, Trang 3, Version 12, SourceID BOM-1]"]),
        "bom-v12.pdf Trang 3 Version 12 SourceID BOM-1",
        [], [], {
            "retrieved_docs": [retrieved], "citation_docs": [retrieved],
            "calculation_provenance": [provenance], "evidence_state": "SUFFICIENT",
            "generation_metrics": {},
        },
    )

    report, passed = runner.run_evaluation(
        [manifest], tmp_path / "result", "candidate", preflight=False,
        intent_extractor=intent_extractor, rag_chat=rag_chat,
    )

    assert passed is True
    assert report["grounded_math_evaluation"]["passed_cases"] == 1
    assert report["grounded_math_evaluation"]["check_totals"]["provenance"] == {
        "passed": 1, "applicable": 1,
    }
    assert report["budget_counts"]["calculation_count"] == 1


def test_rollout_offline_router_mode_disables_provider_router(monkeypatch):
    rollout = _load("crag_rollout_router_mode", "scripts/crag_eval/run_rollout.py")
    monkeypatch.setenv("LLM_ROUTER_ENABLED", "true")

    env = rollout.build_evaluation_environment(enabled=True, router_mode="offline")

    assert env["LLM_ROUTER_ENABLED"] == "false"
    assert env["SEMANTIC_ROUTER_ENABLED"] == "false"
    assert env["RAG_EVAL_ROUTER_MODE"] == "offline"


def test_rollout_provider_router_mode_preserves_explicit_router_configuration(monkeypatch):
    rollout = _load("crag_rollout_provider_mode", "scripts/crag_eval/run_rollout.py")
    monkeypatch.setenv("LLM_ROUTER_ENABLED", "true")
    monkeypatch.setenv("SEMANTIC_ROUTER_ENABLED", "false")

    env = rollout.build_evaluation_environment(enabled=False, router_mode="provider")

    assert env["LLM_ROUTER_ENABLED"] == "true"
    assert env["SEMANTIC_ROUTER_ENABLED"] == "false"
    assert env["RAG_EVAL_ROUTER_MODE"] == "provider"


def test_crag_rollout_arm_binds_trace_log_file(monkeypatch, tmp_path):
    rollout = _load("crag_rollout_trace_env", "scripts/crag_eval/run_rollout.py")
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps(_case()) + "\n", encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    output = tmp_path / "rollout"
    env_values = []

    def fake_run(command, **kwargs):
        env_values.append(kwargs["env"]["RAG_TRACE_LOG_FILE"])
        if "scripts.eval.run_eval" in command:
            run_dir = output / "baseline"
            run_dir.mkdir(parents=True)
            (run_dir / "eval.json").write_text(
                json.dumps({"schema": "rag-labeled-eval-v4"}),
                encoding="utf-8",
            )
        else:
            (output / "baseline" / "trace.json").write_text(
                json.dumps({"schema": "rag-refusal-snapshot-v1"}),
                encoding="utf-8",
            )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(rollout.subprocess, "run", fake_run)

    rollout._run(
        "baseline",
        manifest,
        output,
        trace,
        enabled=False,
        router_mode="offline",
        provider_configuration_sha256="provider",
        governance_scope_sha256_value="scope",
    )

    assert env_values == [str(trace), str(trace)]


def test_crag_rollout_records_runtime_resolved_provider_configuration_hash(
    monkeypatch, tmp_path
):
    from mech_chatbot.config import settings as settings_module
    from mech_chatbot.config.settings import Settings

    rollout = _load("crag_rollout_provider_hash", "scripts/crag_eval/run_rollout.py")
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps(_case()) + "\n", encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    output = tmp_path / "rollout"

    monkeypatch.setenv(rollout.LIVE_OPT_IN, "1")
    monkeypatch.setattr(rollout, "require_clean_worktree", lambda: None)
    snapshot = Settings.from_env(
        {
            "PROXYLLM_API_KEY": "test-provider-key",
            "PROXYLLM_BASE_URL": "https://provider.example/v1",
            "GPT_MODEL_NAME": "snapshot-model",
            "MAX_CONCURRENT_RAG": "7",
        }
    )
    monkeypatch.setattr(settings_module, "load_settings", lambda: snapshot)
    monkeypatch.setattr(
        rollout.subprocess,
        "check_output",
        lambda *args, **kwargs: "abc123\n",
    )

    def fake_arm(label, *args, **kwargs):
        run_dir = output / label
        run_dir.mkdir(parents=True)
        (run_dir / "eval.json").write_text(
            json.dumps({"schema": "rag-labeled-eval-v4", "arm": label}),
            encoding="utf-8",
        )
        (run_dir / "trace.json").write_text(
            json.dumps({"schema": "rag-refusal-snapshot-v1", "arm": label}),
            encoding="utf-8",
        )
        (run_dir / "preflight.json").write_text(
            json.dumps({"fixture_fingerprint": "fixture-sha"}),
            encoding="utf-8",
        )
        return {
            "label": label,
            "started_at": "2026-07-18T00:00:00Z",
            "completed_at": "2026-07-18T00:01:00Z",
            "runner_exit": 0,
        }

    def fake_subprocess_run(command, **kwargs):
        gate_path = Path(command[command.index("--output") + 1])
        gate_path.write_text(
            json.dumps({"schema": "crag-rollout-gate-v1", "passed": True}),
            encoding="utf-8",
        )
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(rollout, "_run", fake_arm)
    monkeypatch.setattr(rollout.subprocess, "run", fake_subprocess_run)
    from mech_chatbot.evaluation import rollout_guardrails

    monkeypatch.setattr(
        rollout_guardrails,
        "evaluate_rollout_pair",
        lambda pair: {"production_eligible": True, "checks": {}},
    )

    report = rollout.run_rollout(manifest, output, trace)

    assert (
        report["provider_configuration_sha256"]
        == "26e3767de31a51ce116fe21158fc060e9348b1a0ab766892467204504f751f2c"
    )


def test_rollout_rejects_dirty_tracked_worktree(monkeypatch):
    rollout = _load("crag_rollout_clean_tree", "scripts/crag_eval/run_rollout.py")
    monkeypatch.setattr(
        rollout.subprocess,
        "check_output",
        lambda *args, **kwargs: " M src/mech_chatbot/rag/pipeline.py\n",
    )

    with pytest.raises(RuntimeError, match="clean tracked worktree"):
        rollout.require_clean_worktree()


def test_crag_rollout_pair_requires_commit_pinned_rollback_evidence(tmp_path):
    rollout = _load("crag_rollout_pair", "scripts/crag_eval/run_rollout.py")
    gate = tmp_path / "gate.json"
    gate.write_text(
        json.dumps({
            "schema": "crag-rollout-gate-v1", "passed": True,
            "checks": {"wrong_answer_not_increased": True, "leakage_zero": True},
        }),
        encoding="utf-8",
    )
    def run_evidence(arm, started_at, completed_at):
        evaluation = tmp_path / f"{arm}-eval.json"
        trace = tmp_path / f"{arm}-trace.json"
        evaluation.write_text(
            json.dumps({"schema": "rag-labeled-eval-v4", "arm": arm}),
            encoding="utf-8",
        )
        trace.write_text(
            json.dumps({"schema": "rag-refusal-snapshot-v1", "arm": arm}),
            encoding="utf-8",
        )
        return {
            **rollout._artifact_reference(evaluation),
            **rollout._artifact_reference(trace, prefix="trace"),
            "started_at": started_at,
            "completed_at": completed_at,
        }
    baseline_evidence = run_evidence(
        "baseline", "2026-07-14T00:00:00Z", "2026-07-14T00:01:00Z"
    )
    candidate_evidence = run_evidence(
        "candidate", "2026-07-14T00:02:00Z", "2026-07-14T00:03:00Z"
    )
    evidence = tmp_path / "rollback.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": "rollback-test-evidence-v1",
                "git_sha": "abc123",
                "passed": True,
                "flags": ["RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"],
            }
        ),
        encoding="utf-8",
    )

    pair = rollout.build_rollout_pair(
        run_id="run-1",
        git_sha="abc123",
        manifest_sha256="manifest-sha",
        snapshot_fingerprint="snapshot-sha",
        provider_configuration_sha256="provider-sha",
        governance_scope_sha256_value="governance-sha",
        baseline_evidence=baseline_evidence,
        candidate_evidence=candidate_evidence,
        gate_artifact=gate,
        rollback_test_artifact=evidence,
    )

    assert pair["rollback"]["artifact_sha256"]
    assert pair["rollback"]["artifact_schema"] == "rollback-test-evidence-v1"
    assert pair["baseline"]["git_sha"] == pair["candidate"]["git_sha"]
    assert pair["baseline"]["artifact_sha256"] != pair["candidate"]["artifact_sha256"]

    evidence.write_text(
        json.dumps(
            {
                "schema": "rollback-test-evidence-v1",
                "git_sha": "different-commit",
                "passed": True,
                "flags": ["RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="rollout commit"):
        rollout.build_rollout_pair(
            run_id="run-1",
            git_sha="abc123",
            manifest_sha256="manifest-sha",
            snapshot_fingerprint="snapshot-sha",
            provider_configuration_sha256="provider-sha",
            governance_scope_sha256_value="governance-sha",
            baseline_evidence=baseline_evidence,
            candidate_evidence=candidate_evidence,
            gate_artifact=gate,
            rollback_test_artifact=evidence,
        )


def test_preflight_checks_sql_and_qdrant_provenance():
    preflight = _load("crag_preflight", "scripts/crag_eval/preflight.py")
    cases = [_case()]

    report = preflight.check_fixture_cases(
        cases,
        sql_documents=[{
            "DocID": 41,
            "TenFile": "crag_eval_numbers_v12.md",
            "VersionNo": 12,
            "LifecycleStatus": "published",
            "ReviewStatus": "approved",
            "PublicationState": "published",
            "IsCurrent": True,
            "SourceSystem": "crag-eval-v1",
            "Servable": True, "OwnerDepartment": "CRAG_EVAL", "Site": "CRAG-EVAL-HQ",
            "SecurityLevel": "internal",
            "BaseCode": "CRAG-EVAL-NUM-001",
        }],
        qdrant_points=[{
            "doc_id": 41, "page": 1, "source_system": "crag-eval-v1", "version_no": 12,
            "lifecycle_status": "published", "review_status": "approved", "publication_state": "published",
            "servable": True, "is_current": True, "owner_department": "CRAG_EVAL",
            "site": "CRAG-EVAL-HQ", "security_level": "internal", "phong_ban_quyen": ["CRAG_EVAL"],
            "base_code": "crag-eval-num-001",
        }],
        collection="MechChatbot_CRAG_Eval_v1",
    )

    assert report["passed"] is True
    assert report["checked_cases"] == 1


def test_preflight_rejects_wrong_collection_and_missing_page():
    preflight = _load("crag_preflight_fail", "scripts/crag_eval/preflight.py")

    with pytest.raises(ValueError, match="collection"):
        preflight.check_fixture_cases([], [], [], collection="TaiLieuKyThuat_v2")

    report = preflight.check_fixture_cases(
        [_case(expected_page=2)],
        sql_documents=[{
            "DocID": 41, "TenFile": "crag_eval_numbers_v12.md", "VersionNo": 12,
            "LifecycleStatus": "published", "ReviewStatus": "approved",
            "PublicationState": "published", "IsCurrent": True,
            "SourceSystem": "crag-eval-v1",
            "Servable": True, "OwnerDepartment": "CRAG_EVAL", "Site": "CRAG-EVAL-HQ",
            "SecurityLevel": "internal",
            "BaseCode": "CRAG-EVAL-NUM-001",
        }],
        qdrant_points=[{
            "doc_id": 41, "page": 1, "source_system": "crag-eval-v1", "version_no": 12,
            "lifecycle_status": "published", "review_status": "approved", "publication_state": "published",
            "servable": True, "is_current": True, "owner_department": "CRAG_EVAL",
            "site": "CRAG-EVAL-HQ", "security_level": "internal", "phong_ban_quyen": ["CRAG_EVAL"],
            "base_code": "crag-eval-num-001",
        }],
        collection="MechChatbot_CRAG_Eval_v1",
    )
    assert report["passed"] is False
    assert report["failures"][0]["reason"] == "qdrant_page_missing"


def test_cleanup_plan_is_strictly_scoped(tmp_path):
    cleanup = _load("crag_cleanup", "scripts/crag_eval/cleanup_fixture.py")
    fixture_root = tmp_path / "data" / "crag_eval_v1"
    fixture_root.mkdir(parents=True)

    plan = cleanup.build_cleanup_plan(fixture_root, tmp_path)

    assert plan["source_system"] == "crag-eval-v1"
    assert plan["collection"] == "MechChatbot_CRAG_Eval_v1"
    assert plan["asset_root"] == str(fixture_root.resolve())
    with pytest.raises(ValueError, match="fixture asset root"):
        cleanup.build_cleanup_plan(tmp_path, tmp_path)


def test_fixture_generation_is_deterministic_and_identity_complete(tmp_path):
    generator = _load("crag_generator", "scripts/crag_eval/generate_fixture.py")

    first = generator.generate_fixture(tmp_path)
    first_manifest = (tmp_path / "eval_manifest.jsonl").read_bytes()
    second = generator.generate_fixture(tmp_path)

    assert first == second
    assert first_manifest == (tmp_path / "eval_manifest.jsonl").read_bytes()
    assert first["documents"] == 5
    cases = [json.loads(line) for line in (tmp_path / "eval_manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(case.get("admin_exception") is True for case in cases)
    assert any(case.get("requires_correction") is True for case in cases)
    assert any(case.get("requires_repair") is True for case in cases)
    assert any(case.get("evaluation_force_ambiguous") is True for case in cases)
    assert any(case.get("evaluation_draft_override") for case in cases)
    for case in cases:
        assert all(case.get(field) for field in (
            "user_department", "user_roles", "allowed_departments", "allowed_sites", "max_security_level"
        ))


def test_eval_main_uses_composed_rag_runtime(monkeypatch, tmp_path):
    runner = _load("run_eval_composition", "scripts/eval/run_eval.py")
    logging_config = importlib.import_module("mech_chatbot.config.logging")
    settings = object()
    log_config = object()
    executor = object()
    events = []
    runtime = SimpleNamespace(
        executor=executor,
        close=lambda: events.append("close"),
    )

    @contextmanager
    def bind_runtime(value, *, include_qdrant):
        assert value is settings
        assert include_qdrant is True
        events.append("bind")
        try:
            yield
        finally:
            events.append("unbind")

    monkeypatch.setattr(runner, "load_settings", lambda: settings)
    monkeypatch.setattr(runner, "load_manifest_files", lambda _paths: [])
    monkeypatch.setattr(
        logging_config.LoggingConfig,
        "from_settings",
        lambda value: log_config if value is settings else None,
    )
    monkeypatch.setattr(
        logging_config,
        "configure_logging",
        lambda value: events.append("logging") if value is log_config else None,
    )
    monkeypatch.setattr(
        runner,
        "_default_preflight_runner",
        lambda: lambda _cases: {"passed": True},
    )
    monkeypatch.setattr(runner, "configured_repository_runtime", bind_runtime)
    monkeypatch.setattr(
        runner,
        "build_rag_runtime",
        lambda value: runtime if value is settings else None,
    )
    monkeypatch.setattr(
        runner,
        "parse_args",
        lambda _argv=None: SimpleNamespace(
            manifest=[tmp_path / "manifest.jsonl"],
            output_dir=tmp_path,
            run_label="baseline",
        ),
    )

    def run_evaluation(*args, **kwargs):
        assert kwargs["rag_executor"] is executor
        events.append("run")
        return {}, True

    monkeypatch.setattr(runner, "run_evaluation", run_evaluation)

    assert runner.main([]) == 0
    assert events == ["bind", "logging", "run", "close", "unbind"]


def test_eval_main_does_not_compose_runtime_before_failed_preflight(monkeypatch, tmp_path):
    runner = _load("run_eval_preflight_order", "scripts/eval/run_eval.py")
    logging_config = importlib.import_module("mech_chatbot.config.logging")
    settings = object()
    preflight_report = {"passed": False}
    events = []

    monkeypatch.setattr(runner, "load_manifest_files", lambda _paths: [])
    monkeypatch.setattr(
        runner,
        "_default_preflight_runner",
        lambda: lambda _cases: preflight_report,
    )
    monkeypatch.setattr(runner, "load_settings", lambda: settings)

    @contextmanager
    def bind_runtime(value, *, include_qdrant):
        assert value is settings
        assert include_qdrant is True
        yield

    monkeypatch.setattr(runner, "configured_repository_runtime", bind_runtime)
    monkeypatch.setattr(
        runner,
        "build_rag_runtime",
        lambda _settings: pytest.fail("runtime composition happened before preflight passed"),
    )
    monkeypatch.setattr(
        logging_config,
        "configure_logging",
        lambda _config: pytest.fail("logging initialized before preflight passed"),
    )
    monkeypatch.setattr(
        runner,
        "parse_args",
        lambda _argv=None: SimpleNamespace(
            manifest=[tmp_path / "manifest.jsonl"],
            output_dir=tmp_path,
            run_label="baseline",
        ),
    )

    def run_evaluation(*args, **kwargs):
        assert kwargs["preflight_runner"]([]) is preflight_report
        events.append("failed-preflight")
        raise RuntimeError("fixture preflight failed; no LLM request was sent")

    monkeypatch.setattr(runner, "run_evaluation", run_evaluation)

    with pytest.raises(RuntimeError, match="fixture preflight failed"):
        runner.main([])
    assert events == ["failed-preflight"]
