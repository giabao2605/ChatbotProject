"""Run a non-formal, reversed-arm Graph retrieval latency diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from scripts.crag_eval.run_rollout import (
    governance_scope_sha256,
    require_source_commit,
)
from scripts.eval.crag_latency_breakdown import build_latency_breakdown
from scripts.eval.provider_smoke import (
    provider_configuration_sha256_for_settings,
    provider_environment_for_settings,
    validate_provider_smoke_for_arms,
)
from scripts.graph_eval.constants import (
    FIXTURE_BATCH,
    FIXTURE_COLLECTION,
    LIVE_OPT_IN,
    ROOT,
)
from scripts.graph_eval.run_rollout import _run, require_clean_worktree


DIAGNOSTIC_OPT_IN = "RAG_GRAPH_DIAGNOSTIC_OPT_IN"
CANONICAL_MANIFEST = ROOT / "data" / "graph_eval_v1" / "eval_manifest.jsonl"
CANONICAL_MANIFEST_SHA256 = (
    "def156e8d30a9184fd7105ca5e799ddef311c98a5c88c7bc59001ad42ee8a136"
)
PAIR_ORDER = (
    ("pair-01", "candidate-first"),
    ("pair-02", "baseline-first"),
)
MAX_LATENCY_P95_RATIO = 1.25
MAX_COST_RATIO = 1.5
MAX_PAIR_LATENCY_RATIO_SPREAD = 0.10
MAX_PAIR_COST_RATIO_SPREAD = 0.10
STAGES = (
    "retrieval",
    "parent_context",
    "rerank",
    "graph_retrieval",
    "generation",
    "correction",
    "claim_repair",
)


@dataclass(frozen=True)
class DiagnosticContext:
    manifest: Path
    preflight: Path
    provider_smoke: Path
    output: Path
    trace: Path
    preflight_report: Mapping[str, Any]
    source_commit: str
    manifest_sha256: str
    preflight_sha256: str
    provider_smoke_sha256: str
    runner: Mapping[str, str]
    provider_configuration_sha256: str
    provider_environment: Mapping[str, str]
    governance_scope_sha256: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def _runner_provenance() -> dict[str, str]:
    return _artifact(Path(__file__).resolve())


def _require_runner_in_source_commit() -> None:
    runner = Path(__file__).resolve()
    relative = runner.relative_to(ROOT).as_posix()
    result = subprocess.run(
        ["git", "cat-file", "-e", f"HEAD:{relative}"],
        cwd=ROOT,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "diagnostic runner must be committed before live diagnostic"
        )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_diagnostic_declaration(
    *,
    source_commit: str,
    manifest: Mapping[str, Any],
    preflight: Mapping[str, Any],
    provider_configuration_sha256: str,
    provider_smoke: Mapping[str, Any],
    governance_scope_sha256: str,
    runner: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "graph-latency-diagnostic-declaration-v1",
        "scope": "supporting_diagnostic_only",
        "declared_at": _utc_now(),
        "formal_evidence": False,
        "source_commit": source_commit,
        "manifest": dict(manifest),
        "fixture": dict(preflight),
        "provider_configuration_sha256": provider_configuration_sha256,
        "provider_smoke": dict(provider_smoke),
        "governance_scope_sha256": governance_scope_sha256,
        "runner": dict(runner),
        "concurrency": 1,
        "pair_order": [
            {"id": pair_id, "arm_order": arm_order}
            for pair_id, arm_order in PAIR_ORDER
        ],
        "limits": {
            "max_latency_p95_ratio": MAX_LATENCY_P95_RATIO,
            "max_cost_ratio": MAX_COST_RATIO,
            "max_pair_latency_ratio_spread": (
                MAX_PAIR_LATENCY_RATIO_SPREAD
            ),
            "max_pair_cost_ratio_spread": MAX_PAIR_COST_RATIO_SPREAD,
            "provider_errors": 0,
            "provider_retries": 0,
        },
        "review_requirement": {
            "mode": "multi_reviewer",
            "source": "independent",
        },
        "stop_rules": [
            "stop on source, fixture, manifest, provider, governance, or runner drift",
            "stop and mark inconclusive on any provider failure or retry",
            "do not rerun, overwrite, or carry forward any pair",
        ],
        "formal_window_authorized": False,
        "feature_enablement_authorized": False,
    }


def _provider_failures(arm: Mapping[str, Any] | None) -> int:
    if not arm:
        return 0
    report = arm.get("eval") or {}
    direct = int(report.get("provider_failure_count") or 0)
    cases = sum(
        bool(row.get("provider_failure"))
        for row in report.get("cases") or []
        if isinstance(row, Mapping)
    )
    return max(direct, cases)


def _provider_retries(arm: Mapping[str, Any] | None) -> int:
    if not arm:
        return 0
    return int((arm.get("eval") or {}).get("provider_retries") or 0)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) and result >= 0 else None


def _eval_metric(arm: Mapping[str, Any] | None, metric: str) -> float | None:
    if not isinstance(arm, Mapping):
        return None
    return _finite_number((arm.get("eval") or {}).get(metric))


def _stage_metric(
    arm: Mapping[str, Any] | None,
    stage: str,
) -> float | None:
    if not isinstance(arm, Mapping):
        return None
    summary = (arm.get("latency") or {}).get("stage_summary") or {}
    return _finite_number((summary.get(stage) or {}).get("latency_p95_ms"))


def _pair_ratios(
    pairs: list[Mapping[str, Any]],
    metric: str,
) -> list[float]:
    ratios = []
    for pair in pairs:
        baseline = _eval_metric(pair.get("baseline"), metric)
        candidate = _eval_metric(pair.get("candidate"), metric)
        if baseline is not None and baseline > 0 and candidate is not None:
            ratios.append(candidate / baseline)
    return ratios


def _ratio_spread(values: list[float]) -> float | None:
    return max(values) - min(values) if len(values) == len(PAIR_ORDER) else None


def _failed_checks(pair: Mapping[str, Any]) -> frozenset[str]:
    checks = (pair.get("gate") or {}).get("checks") or {}
    return frozenset(name for name, passed in checks.items() if not passed)


def _dominant_pair_stage(pair: Mapping[str, Any]) -> str:
    deltas = {}
    for stage in STAGES:
        baseline = _stage_metric(pair.get("baseline"), stage)
        candidate = _stage_metric(pair.get("candidate"), stage)
        if baseline is not None and candidate is not None and candidate > baseline:
            deltas[stage] = candidate - baseline
    return max(deltas, key=deltas.get) if deltas else "none"


def _pairs_match_declaration(pairs: list[Mapping[str, Any]]) -> bool:
    observed = [
        (str(pair.get("id") or ""), str(pair.get("arm_order") or ""))
        for pair in pairs
    ]
    return observed == list(PAIR_ORDER)


def build_diagnostic_outcome(
    pairs: list[Mapping[str, Any]],
    *,
    source_commit: str,
    declaration_sha256: str,
) -> dict[str, Any]:
    provider_failures = sum(
        _provider_failures(pair.get(arm))
        for pair in pairs
        for arm in ("baseline", "candidate")
    )
    provider_retries = sum(
        _provider_retries(pair.get(arm))
        for pair in pairs
        for arm in ("baseline", "candidate")
    )
    complete = _pairs_match_declaration(pairs) and all(
        isinstance(pair.get("baseline"), Mapping)
        and isinstance(pair.get("candidate"), Mapping)
        for pair in pairs
    )
    latency_ratios = _pair_ratios(pairs, "latency_p95_ms")
    cost_ratios = _pair_ratios(pairs, "total_estimated_cost")
    latency_spread = _ratio_spread(latency_ratios)
    cost_spread = _ratio_spread(cost_ratios)
    latency_ratio = statistics.median(latency_ratios) if complete and len(
        latency_ratios
    ) == len(PAIR_ORDER) else None
    cost_ratio = statistics.median(cost_ratios) if complete and len(
        cost_ratios
    ) == len(PAIR_ORDER) else None
    gate_states = [bool((pair.get("gate") or {}).get("passed")) for pair in pairs]
    failed_check_sets = [_failed_checks(pair) for pair in pairs]
    dominant_pair_stages = [_dominant_pair_stage(pair) for pair in pairs]
    arm_order_consistent = bool(
        complete
        and len(latency_ratios) == len(PAIR_ORDER)
        and len(cost_ratios) == len(PAIR_ORDER)
        and len(set(gate_states)) == 1
        and len(set(failed_check_sets)) == 1
        and len(set(dominant_pair_stages)) == 1
        and latency_spread is not None
        and latency_spread <= MAX_PAIR_LATENCY_RATIO_SPREAD
        and cost_spread is not None
        and cost_spread <= MAX_PAIR_COST_RATIO_SPREAD
    )
    stage_deltas = {}
    if complete:
        for stage in STAGES:
            pair_deltas = []
            for pair in pairs:
                baseline = _stage_metric(pair.get("baseline"), stage)
                candidate = _stage_metric(pair.get("candidate"), stage)
                if baseline is not None and candidate is not None:
                    pair_deltas.append(candidate - baseline)
            if len(pair_deltas) == len(PAIR_ORDER):
                stage_deltas[stage] = statistics.median(pair_deltas)
    positive_deltas = {
        stage: value for stage, value in stage_deltas.items() if value > 0
    }
    target_met = bool(
        not provider_failures
        and not provider_retries
        and arm_order_consistent
        and all(gate_states)
        and max(latency_ratios, default=math.inf) <= MAX_LATENCY_P95_RATIO
        and max(cost_ratios, default=math.inf) <= MAX_COST_RATIO
    )
    inconclusive = bool(
        provider_failures
        or provider_retries
        or not complete
        or not arm_order_consistent
    )
    return {
        "schema": "graph-latency-diagnostic-outcome-v1",
        "scope": "supporting_diagnostic_only",
        "formal_evidence": False,
        "status": (
            "inconclusive"
            if inconclusive
            else "passed" if target_met else "failed"
        ),
        "source_commit": source_commit,
        "declaration_sha256": declaration_sha256,
        "pair_count": len(pairs),
        "provider_failure_count": provider_failures,
        "provider_retry_count": provider_retries,
        "arm_order_consistent": arm_order_consistent,
        "pair_latency_p95_ratios": latency_ratios,
        "pair_cost_ratios": cost_ratios,
        "latency_p95_ratio_spread": latency_spread,
        "cost_ratio_spread": cost_spread,
        "dominant_pair_stages": dominant_pair_stages,
        "latency_p95_ratio": (
            latency_ratio if complete and not provider_failures else None
        ),
        "cost_ratio": cost_ratio if complete and not provider_failures else None,
        "diagnostic_target_met": target_met,
        "stage_p95_deltas_ms": (
            stage_deltas if complete and not provider_failures else {}
        ),
        "dominant_overhead_stage": (
            max(positive_deltas, key=positive_deltas.get)
            if complete and not provider_failures and positive_deltas
            else "none" if complete and not provider_failures else "unavailable"
        ),
        "pairs": [
            {
                "id": pair.get("id"),
                "arm_order": pair.get("arm_order"),
                "gate_passed": bool((pair.get("gate") or {}).get("passed")),
            }
            for pair in pairs
        ],
        "formal_window_authorized": False,
        "feature_enablement_authorized": False,
        "next_action": (
            "Wait for externally confirmed provider recovery before a new declaration."
            if provider_failures or provider_retries
            else "Treat the result as arm-order-sensitive variance and use a new declaration."
            if complete and not arm_order_consistent
            else "Complete the predeclared diagnostic before choosing a code fix."
            if not complete
            else "Owner may adjudicate a new formal declaration; this artifact grants no authorization."
            if target_met
            else "Investigate the dominant stage; keep Graph and formal rollout off."
        ),
    }


def validate_preflight_report(
    report: Mapping[str, Any],
    *,
    expected_case_count: int,
) -> None:
    if report.get("schema") != "graph-fixture-preflight-v1":
        raise ValueError("Graph fixture preflight schema is invalid")
    if report.get("passed") is not True:
        raise ValueError("Graph fixture preflight must pass")
    if report.get("batch") != FIXTURE_BATCH:
        raise ValueError(f"preflight batch must equal {FIXTURE_BATCH}")
    if report.get("collection") != FIXTURE_COLLECTION:
        raise ValueError(f"preflight collection must equal {FIXTURE_COLLECTION}")
    if int(report.get("checked_cases") or 0) != expected_case_count:
        raise ValueError("preflight case count must match the manifest")
    if len(str(report.get("fixture_fingerprint") or "")) != 64:
        raise ValueError("preflight fixture fingerprint is invalid")
    graph_report = report.get("graph_report")
    if not isinstance(graph_report, Mapping) or graph_report.get(
        "schema"
    ) != "graph-readiness-v1":
        raise ValueError("preflight graph readiness report is invalid")
    if (
        graph_report.get("review_mode") != "multi_reviewer"
        or graph_report.get("review_sample_source") != "independent"
    ):
        raise ValueError(
            "Graph diagnostic requires independent multi-reviewer evidence"
        )


def validate_canonical_manifest(path: Path) -> None:
    if path.resolve() != CANONICAL_MANIFEST.resolve():
        raise ValueError("diagnostic requires the canonical Graph manifest path")
    if _sha256(path) != CANONICAL_MANIFEST_SHA256:
        raise ValueError("canonical Graph manifest hash is not approved")


def _validate_run_inputs(
    manifest: Path,
    preflight: Path,
    provider_smoke: Path,
    output: Path,
    trace: Path,
) -> Mapping[str, Any]:
    if os.getenv(DIAGNOSTIC_OPT_IN) != "1":
        raise RuntimeError(f"set {DIAGNOSTIC_OPT_IN}=1 before running diagnostic")
    if os.getenv(LIVE_OPT_IN) != "1":
        raise RuntimeError(f"set {LIVE_OPT_IN}=1 before accessing graph fixture")
    for path in (manifest, preflight, provider_smoke):
        if not path.is_file():
            raise ValueError(f"required artifact does not exist: {path}")
    validate_canonical_manifest(manifest)
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output: {output}")
    if trace.exists() and trace.stat().st_size:
        raise ValueError("diagnostic trace must be new or empty")
    preflight_report = json.loads(preflight.read_text(encoding="utf-8"))
    manifest_case_count = sum(
        bool(raw.strip()) for raw in manifest.read_text(encoding="utf-8").splitlines()
    )
    validate_preflight_report(
        preflight_report,
        expected_case_count=manifest_case_count,
    )
    return preflight_report


def _prepare_context(
    manifest: Path,
    preflight: Path,
    provider_smoke: Path,
    output: Path,
    trace: Path,
) -> DiagnosticContext:
    preflight_report = _validate_run_inputs(
        manifest,
        preflight,
        provider_smoke,
        output,
        trace,
    )
    require_clean_worktree()
    _require_runner_in_source_commit()
    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    from mech_chatbot.config.settings import load_settings

    settings = load_settings()
    return DiagnosticContext(
        manifest=manifest,
        preflight=preflight,
        provider_smoke=provider_smoke,
        output=output,
        trace=trace,
        preflight_report=preflight_report,
        source_commit=source_commit,
        manifest_sha256=_sha256(manifest),
        preflight_sha256=_sha256(preflight),
        provider_smoke_sha256=_sha256(provider_smoke),
        runner=_runner_provenance(),
        provider_configuration_sha256=(
            provider_configuration_sha256_for_settings(settings)
        ),
        provider_environment=provider_environment_for_settings(settings),
        governance_scope_sha256=governance_scope_sha256(manifest),
    )


def _require_inputs_unchanged(context: DiagnosticContext) -> None:
    require_clean_worktree()
    require_source_commit(context.source_commit)
    checks = (
        (context.manifest, context.manifest_sha256, "manifest"),
        (context.preflight, context.preflight_sha256, "preflight"),
        (context.provider_smoke, context.provider_smoke_sha256, "provider smoke"),
        (Path(__file__).resolve(), context.runner["sha256"], "diagnostic runner"),
    )
    for path, expected_sha, label in checks:
        if _sha256(path) != expected_sha:
            raise RuntimeError(f"{label} changed during diagnostic")


def _write_declaration(context: DiagnosticContext) -> str:
    context.output.mkdir(parents=True, exist_ok=True)
    context.trace.parent.mkdir(parents=True, exist_ok=True)
    context.trace.touch(exist_ok=True)
    declaration = build_diagnostic_declaration(
        source_commit=context.source_commit,
        manifest=_artifact(context.manifest),
        preflight={
            **_artifact(context.preflight),
            "fixture_fingerprint": context.preflight_report[
                "fixture_fingerprint"
            ],
            "collection": context.preflight_report["collection"],
        },
        provider_configuration_sha256=context.provider_configuration_sha256,
        provider_smoke=_artifact(context.provider_smoke),
        governance_scope_sha256=context.governance_scope_sha256,
        runner=context.runner,
    )
    declaration_path = context.output / "declaration.json"
    _write_json(declaration_path, declaration)
    return _sha256(declaration_path)


def _verify_arm_preflight(context: DiagnosticContext, path: Path) -> None:
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("fixture_fingerprint") != context.preflight_report.get(
        "fixture_fingerprint"
    ):
        raise RuntimeError("fixture snapshot changed during diagnostic")
    if report.get("graph_report") != context.preflight_report.get("graph_report"):
        raise RuntimeError("graph review or readiness evidence changed during diagnostic")


def _sanitized_arm(
    *,
    eval_report: Mapping[str, Any],
    latency_report: Mapping[str, Any],
    eval_path: Path,
    trace_path: Path,
    latency_path: Path,
) -> dict[str, Any]:
    return {
        "eval": {
            "provider_failure_count": _provider_failures({"eval": eval_report}),
            "provider_retries": int(eval_report.get("provider_retries") or 0),
            "total_cases": int(eval_report.get("total_cases") or 0),
            "passed_cases": int(eval_report.get("passed_cases") or 0),
            "latency_p95_ms": eval_report.get("latency_p95_ms"),
            "total_estimated_cost": eval_report.get("total_estimated_cost"),
        },
        "latency": {
            "stage_summary": dict(latency_report.get("stage_summary") or {}),
        },
        "artifacts": {
            "eval": _artifact(eval_path),
            "trace": _artifact(trace_path),
            "latency": _artifact(latency_path),
        },
    }


def _run_arm(
    context: DiagnosticContext,
    pair_dir: Path,
    *,
    label: str,
    enabled: bool,
    arm_starts: tuple[str, ...],
) -> dict[str, Any]:
    started_at = arm_starts[-1]
    _require_inputs_unchanged(context)
    validate_provider_smoke_for_arms(
        context.provider_smoke,
        expected_provider_sha256=context.provider_configuration_sha256,
        arm_started_at=arm_starts,
    )
    timing = _run(
        label,
        context.manifest,
        pair_dir,
        context.trace,
        enabled=enabled,
        provider_sha=context.provider_configuration_sha256,
        governance_sha=context.governance_scope_sha256,
        provider_environment=dict(context.provider_environment),
        started_at=started_at,
    )
    eval_path = pair_dir / label / "eval.json"
    trace_path = pair_dir / label / "trace.json"
    latency_path = pair_dir / label / "latency-breakdown.json"
    eval_report = json.loads(eval_path.read_text(encoding="utf-8"))
    latency_report = {
        **build_latency_breakdown(
            context.trace,
            start=timing["started_at"],
            end=timing["completed_at"],
            execution_contexts={"evaluation"},
        ),
        "schema": "graph-latency-breakdown-v1",
    }
    _write_json(latency_path, latency_report)
    _verify_arm_preflight(context, pair_dir / label / "preflight.json")
    arm = _sanitized_arm(
        eval_report=eval_report,
        latency_report=latency_report,
        eval_path=eval_path,
        trace_path=trace_path,
        latency_path=latency_path,
    )
    _require_inputs_unchanged(context)
    return arm


def _run_gate(context: DiagnosticContext, pair_dir: Path) -> dict[str, Any]:
    readiness_path = pair_dir / "graph_readiness.json"
    _write_json(
        readiness_path,
        context.preflight_report["graph_report"],
    )
    gate_path = pair_dir / "gate.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.eval.retrieval_intelligence_gate",
            "graph_retrieval",
            os.fspath(pair_dir / "baseline" / "eval.json"),
            os.fspath(pair_dir / "candidate" / "eval.json"),
            "--baseline-trace",
            os.fspath(pair_dir / "baseline" / "trace.json"),
            "--candidate-trace",
            os.fspath(pair_dir / "candidate" / "trace.json"),
            "--metadata",
            os.fspath(readiness_path),
            "--output",
            os.fspath(gate_path),
        ],
        cwd=ROOT,
        check=False,
    )
    if not gate_path.is_file():
        raise RuntimeError(f"graph gate failed before writing artifacts (exit {result.returncode})")
    return {
        **json.loads(gate_path.read_text(encoding="utf-8")),
        "runner_exit": result.returncode,
    }


def _run_pair(
    context: DiagnosticContext,
    pair_id: str,
    arm_order: str,
    arm_starts: tuple[str, ...],
) -> tuple[dict[str, Any], tuple[str, ...], bool]:
    arm_specs = (
        (("candidate", True), ("baseline", False))
        if arm_order == "candidate-first"
        else (("baseline", False), ("candidate", True))
    )
    pair_dir = context.output / pair_id
    arms: dict[str, dict[str, Any]] = {}
    for label, enabled in arm_specs:
        arm_starts = (*arm_starts, _utc_now())
        arm = _run_arm(
            context,
            pair_dir,
            label=label,
            enabled=enabled,
            arm_starts=arm_starts,
        )
        arms = {**arms, label: arm}
        if _provider_failures(arm) or _provider_retries(arm):
            pair = {
                "id": pair_id,
                "arm_order": arm_order,
                **arms,
                "gate": {"passed": False, "reason": "provider_variance"},
            }
            _write_json(pair_dir / "summary.json", pair)
            return pair, arm_starts, True

    pair = {
        "id": pair_id,
        "arm_order": arm_order,
        "baseline": arms["baseline"],
        "candidate": arms["candidate"],
        "gate": _run_gate(context, pair_dir),
    }
    _write_json(pair_dir / "summary.json", pair)
    return pair, arm_starts, False


def run_diagnostic(
    *,
    manifest: Path,
    preflight: Path,
    provider_smoke: Path,
    output: Path,
    trace: Path,
) -> dict[str, Any]:
    context = _prepare_context(
        Path(manifest),
        Path(preflight),
        Path(provider_smoke),
        Path(output),
        Path(trace),
    )
    declaration_sha = _write_declaration(context)
    arm_starts: tuple[str, ...] = ()
    pairs: tuple[dict[str, Any], ...] = ()
    for pair_id, arm_order in PAIR_ORDER:
        pair, arm_starts, stopped = _run_pair(
            context,
            pair_id,
            arm_order,
            arm_starts,
        )
        pairs = (*pairs, pair)
        if stopped:
            break
    outcome = build_diagnostic_outcome(
        list(pairs),
        source_commit=context.source_commit,
        declaration_sha256=declaration_sha,
    )
    _write_json(context.output / "outcome.json", outcome)
    return outcome


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--provider-smoke-artifact", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run_diagnostic(
        manifest=args.manifest,
        preflight=args.preflight,
        provider_smoke=args.provider_smoke_artifact,
        output=args.output_dir,
        trace=args.trace,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
