"""Read-only Math/Query evidence checks; passing is not execution authorization.

Expected conditions and versions must come from the caller's independently
verified frozen contract, never from the evidence being checked.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re

from mech_chatbot.governance.feature_activation import FEATURE_FLAGS, VERSION_FIELDS
from scripts.integrated_eval.compose_gate_metadata import CONDITION_FIELDS, load_row_evidence
from scripts.integrated_eval.contracts import require_artifact_reference
from scripts.integrated_eval.math_query_matrix import ROWS
from scripts.crag_eval.run_rollout import governance_scope_sha256
from scripts.crag_eval.constants import FIXTURE_COLLECTION as query_collection
from scripts.grounded_math_eval.constants import FIXTURE_COLLECTION as math_collection


def reconcile_matrix_execution(run_root: Path, *, expected_commit: str, expected_draft_sha256: str,
                               expected_resolved_cases: dict | None = None) -> dict:
    """Read stored execution receipts; never infer quality acceptance from coverage."""
    from scripts.integrated_eval.math_query_dispatch import _redirected, _unique_object, _reject_nonfinite
    from scripts.integrated_eval.math_query_quality import validate_stored_quality_binding

    def read(path):
        if any(_redirected(part) for part in (path, *path.parents)):
            raise ValueError("redirected artifact")
        raw = path.read_bytes()
        return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_nonfinite), raw

    try:
        if expected_resolved_cases is not None and (
                not isinstance(expected_resolved_cases, dict)
                or set(expected_resolved_cases) != {row[0] for row in ROWS}):
            raise ValueError("missing frozen cases")
        if not re.fullmatch(r"[0-9a-f]{40}", expected_commit) or not re.fullmatch(r"[0-9a-f]{64}", expected_draft_sha256):
            raise ValueError("invalid expected identity")
        root = Path(run_root)
        terminal, _ = read(root / "terminal.json")
        if (terminal.get("schema") != "math-query-terminal-v1" or terminal.get("status") != "completed"
                or type(terminal.get("completed_arm_count")) is not int or terminal["completed_arm_count"] != 6
                or terminal.get("source_commit") != expected_commit
                or terminal.get("draft_sha256") != expected_draft_sha256
                or terminal.get("matrix_accepted") is not False):
            raise ValueError("incomplete terminal")
        inventory = set()
        quality_outcomes = {}
        for index, (row, arm) in enumerate((row[0], arm) for row in ROWS for arm in ("baseline", "candidate")):
            stem = f"{index:02d}-{row}-{arm}"
            receipt_name, result_name = stem + ".receipt.json", stem + ".result.json"
            inventory.update((receipt_name, result_name))
            receipt, _ = read(root / "arm-receipts" / receipt_name)
            result, raw = read(root / "arm-receipts" / result_name)
            evaluation, eval_bytes = read(root / row / arm / "eval.json")
            if result.get("eval_sha256") != hashlib.sha256(eval_bytes).hexdigest():
                raise ValueError("evaluation report drift")
            expected = {"schema": "math-query-arm-receipt-v1", "row": row, "arm": arm,
                "source_commit": expected_commit, "draft_sha256": expected_draft_sha256,
                "worker_result_file": result_name, "worker_result_sha256": hashlib.sha256(raw).hexdigest(),
                "reported_cases_bound": True, "observation_coverage_complete": True, "matrix_accepted": False}
            if (json.dumps(receipt, sort_keys=True) != json.dumps(expected, sort_keys=True)
                    or any(result.get(flag, False) is not False or result.get("quality", {}).get(flag, False) is not False
                           for flag in ("matrix_accepted", "dispatch_authorized", "default_rollout_authorized"))
                    or type(result.get("exit_code")) is not int or result["exit_code"] not in (0, 2)
                    or result.get("quality", {}).get("reported_cases_bound") is not True
                    or result.get("quality", {}).get("observation_coverage_complete") is not True):
                raise ValueError("arm evidence mismatch")
            if expected_resolved_cases is not None:
                validate_stored_quality_binding(result["quality"], evaluation["cases"],
                                               expected_resolved_cases[row], label=arm)
                quality_outcomes[f"{row}:{arm}"] = result["quality"]["all_applicable_quality_passed"]
        if {path.name for path in (root / "arm-receipts").iterdir()} != inventory:
            raise ValueError("unexpected arm inventory")
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("matrix_execution_evidence_invalid") from None
    return {"execution_complete": True, "completed_arm_count": 6, "matrix_accepted": False,
            "quality_binding_verified": expected_resolved_cases is not None,
            "quality_outcomes": quality_outcomes}


def validate_matrix_conditions(conditions: dict, versions: dict, source_root: Path) -> None:
    if set(versions) != set(VERSION_FIELDS) or any(
        not isinstance(value, str) or not value.strip() for value in versions.values()
    ):
        raise ValueError("matrix versions incomplete")
    if set(conditions) != {row[0] for row in ROWS}:
        raise ValueError("matrix expected conditions incomplete")
    for name, relative, _, digest, _ in ROWS:
        row = conditions[name]
        if not isinstance(row, dict) or set(row) != set(CONDITION_FIELDS):
            raise ValueError("matrix expected conditions incomplete")
        hashes = ("snapshot_fingerprint", "provider_configuration_sha256", "governance_scope_sha256")
        if (not re.fullmatch(r"[0-9a-f]{40}", str(row["git_sha"]))
                or any(not re.fullmatch(r"[0-9a-f]{64}", str(row[key])) for key in hashes)
                or row["manifest_sha256s"] != [digest]
                or type(row["benchmark_concurrency"]) is not int
                or row["benchmark_concurrency"] != 1
                or row["execution_context"] != "evaluation"
                or row["collection"] != (math_collection if name == "math_only" else query_collection)
                or row["governance_scope_sha256"] != governance_scope_sha256(source_root / relative)):
            raise ValueError("matrix expected conditions invalid")
    for field in ("git_sha", "provider_configuration_sha256"):
        if len({row[field] for row in conditions.values()}) != 1:
            raise ValueError("matrix global conditions differ")


def _arm_identity_checks(row, *, expected, configuration, case_ids, root):
    checks = {}
    for label in ("baseline", "candidate"):
        evaluation = require_artifact_reference(row[label + "_eval"], root=root)
        cases = evaluation.get("cases") or []
        ids = [case.get("id") for case in cases]
        checks[label + "_cases_exact"] = (
            len(ids) == len(case_ids) and len(set(ids)) == len(ids)
            and set(ids) == set(case_ids)
            and type(evaluation.get("total_cases")) is int
            and evaluation["total_cases"] == len(case_ids)
            and type(evaluation.get("case_count", len(ids))) is int
            and evaluation.get("case_count", len(ids)) == len(case_ids))
        checks[label + "_conditions_frozen"] = all(
            type(evaluation.get(key)) is type(value) and evaluation.get(key) == value
            for key, value in expected.items())
        pipeline = {"flags": configuration["baseline_flags" if label == "baseline" else "flags"],
                    "versions": configuration["versions"]}
        checks[label + "_pipeline_types_exact"] = (
            json.dumps(evaluation.get("pipeline_configuration"), sort_keys=True)
            == json.dumps(pipeline, sort_keys=True))
        checks[label + "_retry_total_zero"] = (
            type(evaluation.get("provider_retries")) is int
            and evaluation["provider_retries"] == 0)
        checks[label + "_provider_failures_zero"] = (
            type(evaluation.get("provider_failure_count")) is int
            and evaluation["provider_failure_count"] == 0
            and all(case.get("provider_failure") is False for case in cases))
        trace = require_artifact_reference(row[label + "_trace"], root=root)
        expected_ids = [f"eval:{label}:{case_id}" for case_id in case_ids]
        checks[label + "_trace_ids_exact"] = (
            all(case.get("trace_id") == f"eval:{label}:{case.get('id')}" for case in cases)
            and Counter(case.get("trace_id") for case in cases) == Counter(expected_ids)
            and _raw_trace_ids_match(trace, expected_ids))
        checks[label + "_retry_events_zero"] = (
            type(trace.get("retry_event_count")) is int and trace["retry_event_count"] == 0)
        checks[label + "_error_events_zero"] = (
            type(trace.get("error_event_count")) is int and trace["error_event_count"] == 0)
    return checks


def _raw_trace_ids_match(trace, expected_ids):
    source, filters = trace["source"], trace["filters"]
    raw = Path(source["path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != source["sha256"]:
        return False
    start = datetime.fromisoformat(filters["start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(filters["end"].replace("Z", "+00:00"))
    observed = []
    for line in raw.splitlines():
        event = json.loads(line)
        if event.get("event") != "rag_end" or event.get("execution_context") != "evaluation":
            continue
        timestamp = datetime.fromisoformat(str(event.get("ts")).replace("Z", "+00:00"))
        if start <= timestamp <= end:
            observed.append(event.get("trace_id"))
    return Counter(observed) == Counter(expected_ids)


def load_math_query_evidence(
    manifest: dict, *, expected_conditions: dict, versions: dict,
    source_root: Path, root: Path,
    execution_root: Path | None = None, expected_draft_sha256: str | None = None,
    expected_resolved_cases: dict | None = None,
) -> tuple[dict, list]:
    """Check evidence integrity, not independent quality or matrix acceptance."""
    rows = manifest.get("rows")
    if (manifest.get("schema") != "math-query-evidence-v1" or not isinstance(rows, list)
            or len(rows) != 3 or any(not isinstance(row, dict) for row in rows)
            or sorted(str(row.get("id")) for row in rows) != sorted(row[0] for row in ROWS)):
        raise ValueError("matrix row inventory invalid")
    validate_matrix_conditions(expected_conditions, versions, source_root)
    execution = None
    if any(value is not None for value in (execution_root, expected_draft_sha256, expected_resolved_cases)):
        if any(value is None for value in (execution_root, expected_draft_sha256, expected_resolved_cases)):
            raise ValueError("matrix_execution_binding_required")
        execution = reconcile_matrix_execution(execution_root,
            expected_commit=next(iter(expected_conditions.values()))["git_sha"],
            expected_draft_sha256=expected_draft_sha256, expected_resolved_cases=expected_resolved_cases)
        for row in rows:
            for arm in ("baseline", "candidate"):
                reference = row[arm + "_eval"]
                path = Path(reference["path"])
                if not path.is_absolute():
                    path = root / path
                if path.resolve() != (execution_root / row["id"] / arm / "eval.json").resolve():
                    raise ValueError("matrix_execution_report_mismatch")
    by_id = {row["id"]: row for row in rows}
    reports, references = [], []
    for name, relative, count, digest, enabled in ROWS:
        raw = (source_root / relative).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("matrix manifest drift")
        case_ids = [json.loads(line)["id"] for line in raw.splitlines() if line.strip()]
        if len(case_ids) != count or len(set(case_ids)) != count:
            raise ValueError("matrix manifest case inventory invalid")
        configuration = {"baseline_flags": {flag: False for flag in FEATURE_FLAGS},
                         "flags": {flag: flag in enabled for flag in FEATURE_FLAGS},
                         "versions": dict(versions)}
        report, row_references = load_row_evidence(
            by_id[name], expected_configuration=configuration, root=root,
            baseline_combinations={name: frozenset()}, candidate_combinations={name: enabled},
            maximum_provider_retries=0)
        checks = {**report["checks"], **_arm_identity_checks(
            by_id[name], expected=expected_conditions[name], configuration=configuration,
            case_ids=case_ids, root=root)}
        reports.append({**report, "checks": checks, "passed": all(checks.values())})
        references.extend(row_references)
    return {"schema": "math-query-evidence-result-v1",
            "passed": all(report["passed"] for report in reports), "rows": reports,
            "observed_candidate_quality_passed": bool(execution and all(
                execution["quality_outcomes"][name + ":candidate"] for name, *_ in ROWS)),
            "quality_acceptance_verified": False,
            "quality_binding_verified": execution is not None, "matrix_accepted": False,
            "dispatch_authorized": False, "default_rollout_authorized": False}, references
