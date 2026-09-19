"""The matrix boundary rejects incomplete inventory before reading artifacts."""
from pathlib import Path
import json
import hashlib
import subprocess

import pytest

from scripts.integrated_eval.math_query_evidence import load_math_query_evidence
from scripts.integrated_eval.math_query_matrix import ROWS
from scripts.crag_eval.run_rollout import governance_scope_sha256
from scripts.crag_eval.constants import FIXTURE_COLLECTION as query_collection
from scripts.grounded_math_eval.constants import FIXTURE_COLLECTION as math_collection
from mech_chatbot.governance.feature_activation import FEATURE_FLAGS, VERSION_FIELDS
from tests.unit.test_integrated_row_evidence_loader import _row_fixture, _write_json


@pytest.mark.parametrize("rows", [[], [{"id": "math_only"}],
    [{"id": "math_only"}, {"id": "query_only"}, {"id": "query_only"}],
    [{"id": "math_only"}, {"id": "query_only"}, {"id": "math_query"}, {"id": "extra"}]])
def test_matrix_requires_exact_unique_three_row_inventory(tmp_path, rows):
    with pytest.raises(ValueError, match="matrix row inventory"):
        load_math_query_evidence(
            {"schema": "math-query-evidence-v1", "rows": rows},
            expected_conditions={}, versions={},
            source_root=Path(__file__).resolve().parents[2], root=tmp_path)


@pytest.fixture
def matrix_evidence(tmp_path):
    source = Path(__file__).resolve().parents[2]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    versions = {field: "fixture-v1" for field in VERSION_FIELDS}
    conditions, rows = {}, []
    for name, relative, _, digest, enabled in ROWS:
        case_ids = [json.loads(line)["id"] for line in (source / relative).read_bytes().splitlines()
                    if line.strip()]
        conditions[name] = {"git_sha": commit, "manifest_sha256s": [digest],
                            "snapshot_fingerprint": "a" * 64,
                            "provider_configuration_sha256": "b" * 64,
                            "governance_scope_sha256": governance_scope_sha256(source / relative),
                            "benchmark_concurrency": 1,
                            "collection": math_collection if name == "math_only" else query_collection,
                            "execution_context": "evaluation"}
        directory = tmp_path / name
        directory.mkdir()
        rows.append(_row_fixture(
            directory, combination=name, budget_combinations={name: enabled},
            maximum_provider_retries=0, case_ids=case_ids, conditions=conditions[name],
            configuration={"baseline_flags": {flag: False for flag in FEATURE_FLAGS},
                           "flags": {flag: flag in enabled for flag in FEATURE_FLAGS},
                           "versions": versions}))
    return {"schema": "math-query-evidence-v1", "rows": rows}, {
        "expected_conditions": conditions, "versions": versions,
        "source_root": source, "root": tmp_path}


def test_complete_three_row_evidence_passes_without_granting_authority(matrix_evidence):
    manifest, kwargs = matrix_evidence
    result, references = load_math_query_evidence(manifest, **kwargs)
    assert result["passed"] is True, [row["checks"] for row in result["rows"]]
    assert len(references) == 30
    assert result["dispatch_authorized"] is result["default_rollout_authorized"] is False
    assert result["matrix_accepted"] is False
    assert result["quality_acceptance_verified"] is False


def test_matrix_quality_mode_requires_execution_receipts(matrix_evidence):
    manifest, kwargs = matrix_evidence
    with pytest.raises(ValueError, match="matrix_execution_evidence_invalid"):
        load_math_query_evidence(manifest, **kwargs, execution_root=kwargs["root"],
            expected_draft_sha256="c" * 64,
            expected_resolved_cases={name: [] for name, *_ in ROWS})


def test_combined_execution_and_trace_evidence_binds_same_report_paths(matrix_evidence):
    # Synthetic stored metadata exercises integrity wiring, not authentic observations.
    manifest, kwargs = matrix_evidence
    root = kwargs["root"]
    commit = kwargs["expected_conditions"]["math_only"]["git_sha"]
    digest = "c" * 64
    receipts = root / "arm-receipts"
    receipts.mkdir()
    frozen = {}
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    for index, (row, arm) in enumerate((row, arm) for row in manifest["rows"] for arm in ("baseline", "candidate")):
        name = row["id"]
        evaluation = json.loads(Path(row[arm + "_eval"]["path"]).read_bytes())
        target = root / name / arm / "eval.json"
        target.parent.mkdir(parents=True)
        row[arm + "_eval"] = _write_json(target, evaluation)
        cases = [{"id": case["id"]} for case in evaluation["cases"]]
        frozen[name] = cases
        quality = {"schema": "math-query-arm-quality-observations-v1", "label": arm,
            "expected_case_count": len(cases), "observed_case_count": len(cases),
            "reported_cases_bound": True, "observation_coverage_complete": True,
            "all_applicable_quality_passed": True,
            "cases": [{"case_id": case["id"], "recomputed_matches": True,
                "applicable_quality_passed": True,
                "resolved_case_sha256": hashlib.sha256(canonical(case)).hexdigest(),
                "reported_case_sha256": hashlib.sha256(canonical(reported)).hexdigest()}
                for case, reported in zip(cases, evaluation["cases"])]}
        stem = f"{index:02d}-{name}-{arm}"
        result = {"schema": "math-query-quality-worker-result-v1", "exit_code": 0,
                  "quality": quality, "eval_sha256": row[arm + "_eval"]["sha256"]}
        result_ref = _write_json(receipts / (stem + ".result.json"), result)
        _write_json(receipts / (stem + ".receipt.json"), {
            "schema": "math-query-arm-receipt-v1", "row": name, "arm": arm,
            "source_commit": commit, "draft_sha256": digest,
            "worker_result_sha256": result_ref["sha256"], "worker_result_file": stem + ".result.json",
            "reported_cases_bound": True, "observation_coverage_complete": True, "matrix_accepted": False})
    _write_json(root / "terminal.json", {"schema": "math-query-terminal-v1", "status": "completed",
        "completed_arm_count": 6, "source_commit": commit, "draft_sha256": digest, "matrix_accepted": False})
    options = {**kwargs, "execution_root": root, "expected_draft_sha256": digest,
               "expected_resolved_cases": frozen}
    report, _ = load_math_query_evidence(manifest, **options)
    assert report["passed"] is True
    assert report["quality_binding_verified"] is True
    assert report["observed_candidate_quality_passed"] is True
    assert report["quality_acceptance_verified"] is report["matrix_accepted"] is False
    original = Path(manifest["rows"][0]["baseline_eval"]["path"])
    duplicate = root / "duplicate.json"
    duplicate.write_bytes(original.read_bytes())
    manifest["rows"][0]["baseline_eval"] = {**manifest["rows"][0]["baseline_eval"], "path": str(duplicate)}
    with pytest.raises(ValueError, match="matrix_execution_report_mismatch"):
        load_math_query_evidence(manifest, **options)


@pytest.mark.parametrize("field,value", [("snapshot_fingerprint", "d" * 64),
    ("git_sha", "d" * 40)])
def test_matching_arms_must_still_match_independent_frozen_identity(matrix_evidence, field, value):
    manifest, kwargs = matrix_evidence
    conditions = {name: {**row, field: value} for name, row in kwargs["expected_conditions"].items()}
    result, _ = load_math_query_evidence(manifest, **{**kwargs, "expected_conditions": conditions})
    assert result["passed"] is False
    assert all(not row["checks"]["candidate_conditions_frozen"] for row in result["rows"])


@pytest.mark.parametrize("change", ["duplicate_case", "wrong_case", "count_boolean", "flag_integer"])
def test_rehashed_eval_must_preserve_case_inventory_and_exact_types(matrix_evidence, change):
    manifest, kwargs = matrix_evidence
    row = manifest["rows"][0]
    path = Path(row["candidate_eval"]["path"])
    evaluation = json.loads(path.read_bytes())
    if change in ("duplicate_case", "wrong_case"):
        evaluation["cases"][0]["id"] = (evaluation["cases"][1]["id"]
                                            if change == "duplicate_case" else "unexpected")
    elif change == "count_boolean":
        evaluation["total_cases"] = True
    else:
        evaluation["pipeline_configuration"]["flags"]["RAG_CRAG_ENABLED"] = 0
    changed_row = {**row, "candidate_eval": _write_json(path, evaluation)}
    changed_manifest = {**manifest, "rows": [changed_row, *manifest["rows"][1:]]}
    result, _ = load_math_query_evidence(changed_manifest, **kwargs)
    assert result["passed"] is False
    key = "candidate_pipeline_types_exact" if change == "flag_integer" else "candidate_cases_exact"
    assert result["rows"][0]["checks"][key] is False


@pytest.mark.parametrize("change", ["missing_versions", "bad_hash", "concurrency_boolean",
                                    "different_provider", "missing_row", "wrong_manifest",
                                    "wrong_scope", "wrong_collection"])
def test_invalid_frozen_contract_is_rejected_before_artifact_acceptance(matrix_evidence, change):
    manifest, kwargs = matrix_evidence
    conditions = {name: dict(row) for name, row in kwargs["expected_conditions"].items()}
    versions = kwargs["versions"]
    if change == "missing_versions":
        versions = {}
    elif change == "missing_row":
        conditions.pop("math_query")
    else:
        field, value = {"bad_hash": ("snapshot_fingerprint", "bad"),
                        "concurrency_boolean": ("benchmark_concurrency", True),
                        "different_provider": ("provider_configuration_sha256", "d" * 64),
                        "wrong_scope": ("governance_scope_sha256", "c" * 64),
                        "wrong_collection": ("collection", "other"),
                        "wrong_manifest": ("manifest_sha256s", ["d" * 64])}[change]
        conditions["math_query"][field] = value
    with pytest.raises(ValueError, match="matrix"):
        load_math_query_evidence(manifest, **{**kwargs, "expected_conditions": conditions,
                                              "versions": versions})


def test_case_trace_identity_cannot_be_replaced_by_a_different_request(matrix_evidence):
    manifest, kwargs = matrix_evidence
    row = manifest["rows"][0]
    path = Path(row["candidate_eval"]["path"])
    evaluation = json.loads(path.read_bytes())
    evaluation["cases"][0]["trace_id"] = "eval:candidate:unrelated-request"
    changed = {**row, "candidate_eval": _write_json(path, evaluation)}
    report, _ = load_math_query_evidence(
        {**manifest, "rows": [changed, *manifest["rows"][1:]]}, **kwargs)
    assert report["rows"][0]["checks"]["candidate_trace_ids_exact"] is False


def test_duplicate_raw_request_cannot_replace_missing_case_even_with_same_count(matrix_evidence):
    from scripts.eval.rag_trace_snapshot import build_snapshot

    manifest, kwargs = matrix_evidence
    row = manifest["rows"][0]
    snapshot_path = Path(row["candidate_trace"]["path"])
    snapshot = json.loads(snapshot_path.read_bytes())
    raw_path = Path(snapshot["source"]["path"])
    events = [json.loads(line) for line in raw_path.read_bytes().splitlines()]
    ends = [event for event in events if event["event"] == "rag_end"
            and event["trace_id"].startswith("eval:candidate:")]
    replaced_id = ends[1]["trace_id"]
    changed = [{**event, "trace_id": ends[0]["trace_id"]}
               if event["trace_id"] == replaced_id else event for event in events]
    raw_path.write_text("\n".join(json.dumps(event) for event in changed) + "\n", encoding="utf-8")
    row_changes = {}
    for label in ("baseline", "candidate"):
        path = Path(row[label + "_trace"]["path"])
        original = json.loads(path.read_bytes())
        rebuilt = build_snapshot(raw_path, start=original["filters"]["start"],
                                 end=original["filters"]["end"], execution_contexts={"evaluation"})
        row_changes[label + "_trace"] = _write_json(path, rebuilt)
    report, _ = load_math_query_evidence(
        {**manifest, "rows": [{**row, **row_changes}, *manifest["rows"][1:]]}, **kwargs)
    checks = report["rows"][0]["checks"]
    assert checks["candidate_trace_bound"] is True
    assert checks["candidate_trace_ids_exact"] is False
    assert report["passed"] is False


@pytest.mark.parametrize("field,value,check", [
    ("provider_failure_count", 1, "candidate_provider_failures_zero"),
    ("provider_failure_count", None, "candidate_provider_failures_zero"),
    ("case_count", 999, "candidate_cases_exact"),
])
def test_failure_and_cost_denominator_telemetry_cannot_be_ignored(matrix_evidence, field, value, check):
    manifest, kwargs = matrix_evidence
    row = manifest["rows"][0]
    path = Path(row["candidate_eval"]["path"])
    artifact = {**json.loads(path.read_bytes()), field: value}
    changed = {**row, "candidate_eval": _write_json(path, artifact)}
    result, _ = load_math_query_evidence(
        {**manifest, "rows": [changed, *manifest["rows"][1:]]}, **kwargs)
    assert result["rows"][0]["checks"][check] is False


def test_manifest_governance_hashes_are_row_specific_not_global(matrix_evidence):
    manifest, kwargs = matrix_evidence
    assert len({row["governance_scope_sha256"]
                for row in kwargs["expected_conditions"].values()}) == 3
    result, _ = load_math_query_evidence(manifest, **kwargs)
    assert result["passed"] is True
