"""Build a commit-pinned, fail-closed integrated hardening readiness artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for value in (ROOT, SRC):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from mech_chatbot.evaluation.integrated_hardening import (
    evaluate_integrated_readiness,
    execute_security_manifest,
    validate_combination_matrix,
    validate_security_manifest,
)
from mech_chatbot.rag.semantic_cache import pipeline_namespace
from scripts.integrated_eval.contracts import assert_clean_worktree


def build_preflight(
    *, matrix, security_cases, prerequisites, offline_evidence, git_sha,
    prerequisite_verification=None,
) -> dict:
    matrix_report = validate_combination_matrix(matrix)
    security_report = validate_security_manifest(security_cases)
    security_execution = execute_security_manifest(security_cases)
    namespaces = [
        pipeline_namespace({**item["flags"], **item["versions"]})
        for item in matrix.get("combinations") or []
        if isinstance(item, dict) and item.get("flags") and item.get("versions")
    ]
    cache_isolation = (
        matrix_report["passed"]
        and len(namespaces) == len(matrix.get("combinations") or [])
        and len(set(namespaces)) == len(namespaces)
    )
    evidence_commit_matches = (
        offline_evidence.get("schema") == "integrated-offline-verification-v1"
        and offline_evidence.get("git_sha") == git_sha
    )
    readiness = evaluate_integrated_readiness(
        matrix_report=matrix_report,
        security_manifest_report={
            **security_report,
            "passed": security_report["passed"] and security_execution["passed"],
        },
        cache_isolation_passed=(
            evidence_commit_matches
            and cache_isolation
            and offline_evidence.get("cache_isolation_passed") is True
        ),
        strict_stream_passed=(
            evidence_commit_matches
            and offline_evidence.get("strict_stream_passed") is True
        ),
        rollback_passed=(
            evidence_commit_matches
            and offline_evidence.get("rollback_passed") is True
        ),
        prerequisites=prerequisites,
    )
    readiness.update({
        "git_sha": git_sha,
        "offline_evidence_commit_matches": evidence_commit_matches,
        "matrix_validation": matrix_report,
        "security_manifest_validation": security_report,
        "security_execution": security_execution,
        "cache_namespaces": namespaces,
        "prerequisite_verification": prerequisite_verification or {},
    })
    return readiness


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_jsonl(path):
    return [
        json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


_PREREQUISITE_SCHEMAS = {
    "crag": "crag-production-pilot-v1",
    "grounded_math": "grounded-math-rollout-run-v1",
    "late_interaction": "retrieval-intelligence-gate-v1",
    "query_decomposition": "decomposition-rollout-run-v1",
    "graph_retrieval": "graph-rollout-run-v1",
}


def _milestone_outcome_valid(name, artifact, decision, git_sha):
    if artifact.get("schema") != _PREREQUISITE_SCHEMAS[name]:
        return False
    artifact_sha = artifact.get("git_sha")
    if git_sha and artifact_sha != git_sha:
        return False
    if name == "late_interaction" and artifact.get("stage") != "late_interaction":
        return False
    if decision == "accepted":
        if name == "crag":
            return artifact.get("decision") == "accepted" and artifact.get("passed") is True
        if name == "grounded_math":
            return artifact.get("passed") is True and artifact.get("production_eligible") is True
        return artifact.get("passed") is True
    if decision == "rejected":
        return (
            artifact.get("decision") == "rejected"
            or artifact.get("passed") is False
            or artifact.get("production_eligible") is False
        )
    return False


def _prerequisites(payload, git_sha=None):
    stages = payload.get("stages") or {}
    required = {
        "crag", "grounded_math", "late_interaction", "query_decomposition",
        "graph_retrieval",
    }
    statuses = {}
    verification = {}
    for name in required:
        value = stages.get(name) or {}
        requested_complete = isinstance(value, dict) and value.get("complete") is True
        artifact_valid = False
        if requested_complete:
            path_value = str(value.get("artifact_path") or "").strip()
            path = Path(path_value)
            if path_value and not path.is_absolute():
                path = ROOT / path
            try:
                artifact_bytes = path.read_bytes()
                artifact = json.loads(artifact_bytes.decode("utf-8"))
                artifact_valid = (
                    hashlib.sha256(artifact_bytes).hexdigest()
                    == str(value.get("artifact_sha256") or "").lower()
                    and value.get("artifact_schema") == _PREREQUISITE_SCHEMAS[name]
                    and _milestone_outcome_valid(
                        name, artifact, value.get("decision"), git_sha
                    )
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                artifact_valid = False
        statuses[name] = requested_complete and artifact_valid
        verification[name] = {
            "requested_complete": requested_complete,
            "artifact_verified": artifact_valid,
            "reason": value.get("reason") if isinstance(value, dict) else None,
        }
    verification["schema_valid"] = (
        payload.get("schema") == "integrated-prerequisites-v1"
        and set(stages) == required
    )
    if not verification["schema_valid"]:
        statuses = {name: False for name in required}
    return statuses, verification


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--security-manifest", type=Path, required=True)
    parser.add_argument("--prerequisites", type=Path, required=True)
    parser.add_argument("--offline-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    assert_clean_worktree(ROOT)
    git_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    prerequisites, prerequisite_verification = _prerequisites(
        _read_json(args.prerequisites), git_sha
    )
    artifact = build_preflight(
        matrix=_read_json(args.matrix),
        security_cases=_read_jsonl(args.security_manifest),
        prerequisites=prerequisites,
        offline_evidence=_read_json(args.offline_evidence),
        git_sha=git_sha,
        prerequisite_verification=prerequisite_verification,
    )
    artifact.update({
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "inputs": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                args.matrix, args.security_manifest, args.prerequisites,
                args.offline_evidence,
            )
        },
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    return 0 if artifact["ready_for_live_matrix"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
