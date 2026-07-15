"""Run a frozen regular-retrieval versus governed-graph evaluation pair."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.crag_eval.run_rollout import _artifact_reference, _sha, _utc_now, governance_scope_sha256
from scripts.graph_eval.constants import FIXTURE_COLLECTION, LIVE_OPT_IN, ROOT


def require_clean_worktree():
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True
    ).strip()
    if status:
        raise RuntimeError("graph rollout requires a clean tracked worktree")


def build_evaluation_environment(*, enabled):
    environment = os.environ.copy()
    environment.update({
        "RAG_EXECUTION_CONTEXT": "evaluation", "RAG_CRAG_ENABLED": "true",
        "RAG_CLAIM_REPAIR_ENABLED": "true", "RAG_GROUNDED_MATH_ENABLED": "false",
        "RAG_QUERY_DECOMPOSITION_ENABLED": "false", "RAG_LATE_INTERACTION_ENABLED": "false",
        "RAG_GRAPH_RETRIEVAL_ENABLED": str(enabled).lower(),
        "SEMANTIC_CACHE_ENABLED": "false", "STRICT_REALTIME_STREAMING": "false",
        "QDRANT_COLLECTION": FIXTURE_COLLECTION, "RAG_EVAL_PREFLIGHT_KIND": "graph",
        "RAG_EVAL_ROUTER_MODE": "offline", "LLM_ROUTER_ENABLED": "false",
        "SEMANTIC_ROUTER_ENABLED": "false",
    })
    return environment


def _run(label, manifest, output, trace, *, enabled, provider_sha, governance_sha):
    environment = build_evaluation_environment(enabled=enabled)
    environment.update({
        "RAG_EVAL_PROVIDER_CONFIGURATION_SHA256": provider_sha,
        "RAG_EVAL_GOVERNANCE_SCOPE_SHA256": governance_sha,
        "RAG_EVAL_CONCURRENCY": "1",
    })
    started_at = _utc_now()
    result = subprocess.run([
        sys.executable, "-m", "scripts.eval.run_eval", "--manifest", str(manifest),
        "--output-dir", str(output), "--run-label", label,
    ], cwd=ROOT, env=environment, check=False)
    completed_at = _utc_now()
    run_dir = output / label
    if not (run_dir / "eval.json").exists():
        raise RuntimeError(f"{label} failed before writing artifacts (exit {result.returncode})")
    snapshot = subprocess.run([
        sys.executable, "-m", "scripts.eval.rag_trace_snapshot", str(trace),
        "--start", started_at, "--end", completed_at, "--context", "evaluation",
        "--json-output", str(run_dir / "trace.json"),
        "--markdown-output", str(run_dir / "trace.md"),
    ], cwd=ROOT, env=environment, check=False)
    if snapshot.returncode:
        raise RuntimeError(f"trace snapshot failed for {label}")
    return {"started_at": started_at, "completed_at": completed_at, "runner_exit": result.returncode}


def run_rollout(manifest, output, trace, *, rollback_test_artifact=None):
    if os.getenv(LIVE_OPT_IN) != "1":
        raise RuntimeError(f"set {LIVE_OPT_IN}=1 before live graph evaluation")
    manifest, output, trace = Path(manifest), Path(output), Path(trace)
    if not manifest.is_file() or not trace.is_file():
        raise ValueError("manifest and trace files must exist")
    require_clean_worktree()
    for label in ("baseline", "candidate"):
        directory = output / label
        if directory.exists() and any(directory.iterdir()):
            raise ValueError(f"refusing to overwrite {directory}")
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    manifest_sha = _sha(manifest)
    provider = {key: os.getenv(key) for key in ("GPT_MODEL_NAME", "OPENAI_BASE_URL", "MAX_CONCURRENT_RAG") if os.getenv(key)}
    provider_sha = hashlib.sha256(json.dumps(provider, sort_keys=True).encode()).hexdigest()
    governance_sha = governance_scope_sha256(manifest)
    baseline = _run("baseline", manifest, output, trace, enabled=False, provider_sha=provider_sha, governance_sha=governance_sha)
    require_clean_worktree()
    candidate = _run("candidate", manifest, output, trace, enabled=True, provider_sha=provider_sha, governance_sha=governance_sha)
    require_clean_worktree()
    if _sha(manifest) != manifest_sha:
        raise RuntimeError("manifest changed during graph rollout")
    baseline_preflight = json.loads((output / "baseline" / "preflight.json").read_text(encoding="utf-8"))
    candidate_preflight = json.loads((output / "candidate" / "preflight.json").read_text(encoding="utf-8"))
    fingerprint = baseline_preflight["fixture_fingerprint"]
    if fingerprint != candidate_preflight["fixture_fingerprint"]:
        raise RuntimeError("graph fixture changed between variants")
    readiness = output / "graph_readiness.json"
    readiness.write_text(json.dumps(candidate_preflight["graph_report"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    gate_path = output / "gate.json"
    gate_result = subprocess.run([
        sys.executable, "-m", "scripts.eval.retrieval_intelligence_gate", "graph_retrieval",
        str(output / "baseline" / "eval.json"), str(output / "candidate" / "eval.json"),
        "--baseline-trace", str(output / "baseline" / "trace.json"),
        "--candidate-trace", str(output / "candidate" / "trace.json"),
        "--metadata", str(readiness), "--output", str(gate_path),
    ], cwd=ROOT, check=False)
    rollback = {}
    if rollback_test_artifact:
        evidence = json.loads(Path(rollback_test_artifact).read_text(encoding="utf-8"))
        if evidence.get("passed") is not True or evidence.get("git_sha") != git_sha or set(evidence.get("flags") or []) != {"RAG_GRAPH_RETRIEVAL_ENABLED"}:
            raise ValueError("rollback evidence must pass for this commit and graph flag")
        rollback = _artifact_reference(Path(rollback_test_artifact))
    context = {
        "git_sha": git_sha, "manifest_sha256": manifest_sha,
        "snapshot_fingerprint": fingerprint, "provider_configuration_sha256": provider_sha,
        "governance_scope_sha256": governance_sha, "concurrency": 1,
        "collection": FIXTURE_COLLECTION,
    }
    pair = {
        "schema": "rollout-evidence-pair-v1", "run_id": output.name,
        "stage": "graph_retrieval", "evidence_type": "staging_evaluation",
        "baseline": {**context, **_artifact_reference(output / "baseline" / "eval.json"), **baseline},
        "candidate": {**context, **_artifact_reference(output / "candidate" / "eval.json"), **candidate},
        "data_plane": {"production_collection": os.getenv("RAG_PRODUCTION_QDRANT_COLLECTION", "TaiLieuKyThuat_v2"), "mutation_mode": "staging"},
        "gate": _artifact_reference(gate_path),
        "rollback": {"flags": ["RAG_GRAPH_RETRIEVAL_ENABLED"], "defaults_disabled": True, **rollback},
    }
    pair_path = output / "rollout_pair.json"
    pair_path.write_text(json.dumps(pair, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    report = {
        "schema": "graph-rollout-run-v1", "git_sha": git_sha,
        "manifest_sha256": manifest_sha, "fixture_fingerprint": fingerprint,
        "baseline": baseline, "candidate": candidate, "gate_exit": gate_result.returncode,
        "passed": bool(gate["passed"]), "rollout_pair_sha256": _sha(pair_path),
    }
    (output / "run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--trace", type=Path, default=ROOT / "logs" / "rag_trace.jsonl")
    parser.add_argument("--rollback-test-artifact", type=Path)
    args = parser.parse_args()
    report = run_rollout(args.manifest, args.output_dir, args.trace, rollback_test_artifact=args.rollback_test_artifact)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
