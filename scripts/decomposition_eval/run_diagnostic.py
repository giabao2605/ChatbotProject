"""Run a non-formal query-only cost diagnostic and compare stage usage."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from scripts.crag_eval.run_rollout import governance_scope_sha256
from scripts.decomposition_eval.constants import FIXTURE_BATCH, FIXTURE_COLLECTION
from scripts.decomposition_eval.run_rollout import _run, build_evaluation_environment
from scripts.eval.provider_smoke import (
    provider_configuration_sha256_for_settings,
    provider_environment_for_settings,
)

ROOT = Path(__file__).resolve().parents[2]
DIAGNOSTIC_OPT_IN = "RAG_DECOMPOSITION_DIAGNOSTIC_OPT_IN"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_runner_provenance() -> dict[str, str]:
    runner = Path(__file__).resolve()
    return {"path": str(runner), "sha256": _sha256(runner)}


def build_worktree_provenance() -> dict[str, str | None]:
    def diff_sha(*pathspecs: str) -> str | None:
        command = ["git", "diff", "--binary", "HEAD", "--", *pathspecs]
        diff = subprocess.check_output(command, cwd=ROOT)
        return hashlib.sha256(diff).hexdigest() if diff else None

    return {
        "tracked_diff_sha256": diff_sha("."),
        "python_diff_sha256": diff_sha(":(glob)**/*.py"),
    }


def require_unchanged_diagnostic_inputs(
    *,
    source_commit: str,
    manifest: Path,
    manifest_sha256: str,
    worktree: Mapping[str, str | None],
    runner: Mapping[str, str],
) -> None:
    current_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
    ).strip()
    if current_commit != source_commit:
        raise RuntimeError("source commit changed during diagnostic")
    if _sha256(manifest) != manifest_sha256:
        raise RuntimeError("manifest changed during diagnostic")
    if build_worktree_provenance() != dict(worktree):
        raise RuntimeError("tracked worktree changed during diagnostic")
    if build_runner_provenance() != dict(runner):
        raise RuntimeError("diagnostic runner changed during diagnostic")


def _summary(report: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    summary = report.get("decomposition_usage")
    if not isinstance(summary, Mapping):
        raise ValueError(f"{label} report is missing decomposition usage")
    if summary.get("schema") != "rag-decomposition-usage-summary-v1":
        raise ValueError(f"{label} decomposition usage schema is invalid")
    if summary.get("cost_reconciled") is not True:
        raise ValueError(f"{label} decomposition cost does not reconcile")
    return summary


def _quality_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    decomposition = report.get("decomposition_evaluation") or {}
    if not isinstance(decomposition, Mapping):
        raise ValueError("decomposition evaluation summary is invalid")
    return {
        "total_cases": int(report.get("total_cases") or 0),
        "passed_cases": int(report.get("passed_cases") or 0),
        "provider_failure_count": int(report.get("provider_failure_count") or 0),
        "decomposition": {
            "applicable_cases": int(decomposition.get("applicable_cases") or 0),
            "passed_cases": int(decomposition.get("passed_cases") or 0),
            "branch_accuracy": float(decomposition.get("branch_accuracy") or 0.0),
            "citation_accuracy": float(
                decomposition.get("citation_accuracy") or 0.0
            ),
            "budget_violations": int(decomposition.get("budget_violations") or 0),
            "simple_planner_calls": int(
                decomposition.get("simple_planner_calls") or 0
            ),
        },
    }


def _stage_cost(summary: Mapping[str, Any], stage: str) -> float:
    bucket = summary.get(stage) or {}
    if not isinstance(bucket, Mapping):
        raise ValueError(f"{stage} bucket is invalid")
    return float(bucket.get("estimated_cost") or 0.0)


def build_baseline_outage_diagnostic(
    baseline_path: str | Path,
    *,
    source_commit: str,
    tracked_diff_sha256: str,
) -> dict[str, Any] | None:
    baseline_path = Path(baseline_path)
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    quality = _quality_summary(baseline)
    if quality["provider_failure_count"] == 0:
        return None
    usage = _summary(baseline, "baseline")
    return {
        "schema": "query-decomposition-cost-diagnostic-v1",
        "formal_evidence": False,
        "status": "inconclusive",
        "source_commit": source_commit,
        "tracked_worktree_dirty": bool(tracked_diff_sha256),
        "tracked_diff_sha256": tracked_diff_sha256 or None,
        "baseline": {
            "path": str(baseline_path.resolve()),
            "sha256": _sha256(baseline_path),
            "total_estimated_cost": float(
                baseline.get("total_estimated_cost") or 0.0
            ),
            "quality": quality,
            "usage": usage,
        },
        "candidate": None,
        "cost_ratio": None,
        "diagnostic_target": 1.35,
        "diagnostic_target_met": False,
        "stage_deltas": {},
        "dominant_overhead_stage": "unavailable",
        "next_action": (
            "Preserve this provider-outage tombstone and wait for externally "
            "confirmed recovery before starting one new diagnostic."
        ),
    }


def build_cost_diagnostic(
    baseline_path: str | Path,
    candidate_path: str | Path,
    *,
    source_commit: str,
    tracked_diff_sha256: str,
) -> dict[str, Any]:
    baseline_path = Path(baseline_path)
    candidate_path = Path(candidate_path)
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    baseline_usage = _summary(baseline, "baseline")
    candidate_usage = _summary(candidate, "candidate")
    baseline_total = float(baseline.get("total_estimated_cost") or 0.0)
    candidate_total = float(candidate.get("total_estimated_cost") or 0.0)
    cost_ratio = candidate_total / baseline_total if baseline_total > 0 else None
    baseline_quality = _quality_summary(baseline)
    candidate_quality = _quality_summary(candidate)
    provider_failed = bool(
        baseline_quality["provider_failure_count"]
        or candidate_quality["provider_failure_count"]
    )
    inconclusive = provider_failed or cost_ratio is None
    stages = ("planner", "branch_correction", "final_generation")
    stage_deltas = {
        stage: _stage_cost(candidate_usage, stage)
        - _stage_cost(baseline_usage, stage)
        for stage in stages
    }
    positive = {stage: value for stage, value in stage_deltas.items() if value > 0}
    dominant = (
        "unavailable"
        if inconclusive
        else max(positive, key=positive.get) if positive else "none"
    )
    target_met = not inconclusive and cost_ratio <= 1.35
    return {
        "schema": "query-decomposition-cost-diagnostic-v1",
        "formal_evidence": False,
        "status": (
            "inconclusive" if inconclusive else "passed" if target_met else "failed"
        ),
        "source_commit": source_commit,
        "tracked_worktree_dirty": bool(tracked_diff_sha256),
        "tracked_diff_sha256": tracked_diff_sha256 or None,
        "baseline": {
            "path": str(baseline_path.resolve()),
            "sha256": _sha256(baseline_path),
            "total_estimated_cost": baseline_total,
            "quality": baseline_quality,
            "usage": baseline_usage,
        },
        "candidate": {
            "path": str(candidate_path.resolve()),
            "sha256": _sha256(candidate_path),
            "total_estimated_cost": candidate_total,
            "quality": candidate_quality,
            "usage": candidate_usage,
        },
        "cost_ratio": cost_ratio,
        "diagnostic_target": 1.35,
        "diagnostic_target_met": target_met,
        "stage_deltas": stage_deltas,
        "dominant_overhead_stage": dominant,
        "next_action": (
            "Preserve this provider-outage tombstone and wait for externally "
            "confirmed recovery before starting one new diagnostic."
            if provider_failed
            else "Use the dominant stage to choose a root fix; do not use this "
            "diagnostic as formal rollout evidence."
        ),
    }


def run_diagnostic(
    manifest: str | Path,
    output: str | Path,
    trace: str | Path,
    *,
    collection: str = FIXTURE_COLLECTION,
    fixture_batch: str = FIXTURE_BATCH,
) -> dict[str, Any]:
    if os.getenv(DIAGNOSTIC_OPT_IN) != "1":
        raise RuntimeError(f"set {DIAGNOSTIC_OPT_IN}=1 before running diagnostic")
    manifest = Path(manifest)
    output = Path(output)
    trace = Path(trace)
    if not manifest.is_file() or not trace.is_file():
        raise ValueError("manifest and trace files must exist")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output: {output}")
    output.mkdir(parents=True, exist_ok=True)

    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    manifest_sha256 = _sha256(manifest)
    worktree = build_worktree_provenance()
    runner = build_runner_provenance()
    tracked_diff_sha256 = str(worktree["tracked_diff_sha256"] or "")
    from mech_chatbot.config.settings import load_settings

    settings = load_settings()
    provider_sha = provider_configuration_sha256_for_settings(settings)
    provider_environment = provider_environment_for_settings(settings)
    governance_sha = governance_scope_sha256(manifest)
    baseline_flags = {
        name: value
        for name, value in build_evaluation_environment(
            enabled=False,
            collection=collection,
            fixture_batch=fixture_batch,
        ).items()
        if name.startswith("RAG_") and name.endswith("_ENABLED")
    }
    candidate_flags = {
        name: value
        for name, value in build_evaluation_environment(
            enabled=True,
            collection=collection,
            fixture_batch=fixture_batch,
        ).items()
        if name.startswith("RAG_") and name.endswith("_ENABLED")
    }
    declaration = {
        "schema": "query-decomposition-cost-diagnostic-declaration-v1",
        "declared_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "formal_evidence": False,
        "source_commit": source_commit,
        "tracked_worktree_dirty": bool(tracked_diff_sha256),
        "tracked_diff_sha256": tracked_diff_sha256 or None,
        "manifest": str(manifest.resolve()),
        "manifest_sha256": manifest_sha256,
        "provider_configuration_sha256": provider_sha,
        "runner": runner,
        "worktree": worktree,
        "baseline_flags": baseline_flags,
        "candidate_flags": candidate_flags,
        "diagnostic_target": 1.35,
    }
    (output / "declaration.json").write_text(
        json.dumps(declaration, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _run(
        "baseline",
        manifest,
        output,
        trace,
        enabled=False,
        provider_sha=provider_sha,
        governance_sha=governance_sha,
        collection=collection,
        fixture_batch=fixture_batch,
        provider_environment=provider_environment,
    )
    require_unchanged_diagnostic_inputs(
        source_commit=source_commit,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        worktree=worktree,
        runner=runner,
    )
    baseline_outage = build_baseline_outage_diagnostic(
        output / "baseline" / "eval.json",
        source_commit=source_commit,
        tracked_diff_sha256=tracked_diff_sha256,
    )
    if baseline_outage is not None:
        baseline_outage["declaration_sha256"] = _sha256(
            output / "declaration.json"
        )
        (output / "diagnostic.json").write_text(
            json.dumps(baseline_outage, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return baseline_outage
    _run(
        "candidate",
        manifest,
        output,
        trace,
        enabled=True,
        provider_sha=provider_sha,
        governance_sha=governance_sha,
        collection=collection,
        fixture_batch=fixture_batch,
        provider_environment=provider_environment,
    )
    require_unchanged_diagnostic_inputs(
        source_commit=source_commit,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        worktree=worktree,
        runner=runner,
    )
    baseline_preflight = json.loads(
        (output / "baseline" / "preflight.json").read_text(encoding="utf-8")
    )
    candidate_preflight = json.loads(
        (output / "candidate" / "preflight.json").read_text(encoding="utf-8")
    )
    if baseline_preflight.get("fixture_fingerprint") != candidate_preflight.get(
        "fixture_fingerprint"
    ):
        raise RuntimeError("fixture snapshot changed between diagnostic arms")
    diagnostic = build_cost_diagnostic(
        output / "baseline" / "eval.json",
        output / "candidate" / "eval.json",
        source_commit=source_commit,
        tracked_diff_sha256=tracked_diff_sha256,
    )
    diagnostic["declaration_sha256"] = _sha256(output / "declaration.json")
    (output / "diagnostic.json").write_text(
        json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return diagnostic


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--trace",
        type=Path,
        default=ROOT / "logs" / "rag_trace.jsonl",
    )
    parser.add_argument("--collection", default=FIXTURE_COLLECTION)
    parser.add_argument("--fixture-batch", default=FIXTURE_BATCH)
    args = parser.parse_args(argv)
    report = run_diagnostic(
        args.manifest,
        args.output_dir,
        args.trace,
        collection=args.collection,
        fixture_batch=args.fixture_batch,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
