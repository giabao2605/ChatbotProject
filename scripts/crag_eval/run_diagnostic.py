"""Run a non-formal interleaved CRAG and Claim Repair diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from scripts.crag_eval.constants import FIXTURE_COLLECTION
from scripts.crag_eval.run_rollout import (
    _run,
    governance_scope_sha256,
    require_clean_worktree,
    require_source_commit,
)
from scripts.eval.crag_latency_breakdown import build_latency_breakdown
from scripts.eval.crag_rollout_gate import compare_reports
from scripts.eval.provider_smoke import (
    provider_configuration_sha256_for_settings,
    provider_environment_for_settings,
    validate_provider_smoke_for_arms,
)

ROOT = Path(__file__).resolve().parents[2]
DIAGNOSTIC_OPT_IN = "RAG_CRAG_DIAGNOSTIC_OPT_IN"
CANONICAL_MANIFEST = ROOT / "data" / "crag_eval_v1" / "eval_manifest.jsonl"
CANONICAL_MANIFEST_SHA256 = (
    "beac3aac28b59ac57930b2c7099997efa7bdfda2a76bf65e3f1620d4b0fb897b"
)
MAX_PAIR_LATENCY_RATIO_SPREAD = 0.10
MAX_PAIR_COST_RATIO_SPREAD = 0.10
PAIR_ORDER = (
    ("pair-01", "candidate-first"),
    ("pair-02", "baseline-first"),
)
STAGES = (
    "retrieval",
    "parent_context",
    "rerank",
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
    router_mode: str
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
        "schema": "crag-stage-latency-diagnostic-declaration-v2",
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
            "max_latency_ratio": 1.25,
            "max_cost_ratio": 1.5,
            "max_pair_latency_ratio_spread": MAX_PAIR_LATENCY_RATIO_SPREAD,
            "max_pair_cost_ratio_spread": MAX_PAIR_COST_RATIO_SPREAD,
            "provider_errors": 0,
            "provider_retries": 0,
        },
        "stop_rules": [
            "stop on source, fixture, manifest, provider, governance, or runner drift",
            "stop and mark inconclusive on any provider failure",
            "do not rerun or overwrite any pair",
        ],
        "formal_window_authorized": False,
        "feature_enablement_authorized": False,
    }


def _provider_failures(arm: Mapping[str, Any] | None) -> int:
    if not arm:
        return 0
    report = arm.get("eval") or {}
    direct = int(report.get("provider_failure_count") or 0)
    cases = sum(bool(row.get("provider_failure")) for row in report.get("cases") or [])
    return max(direct, cases)


def _provider_retries(arm: Mapping[str, Any] | None) -> int:
    if not arm:
        return 0
    report = arm.get("eval") or {}
    return int(report.get("provider_retries") or 0)


def _arm_metric(
    pairs: list[Mapping[str, Any]], arm: str, metric: str
) -> float | None:
    values = []
    for pair in pairs:
        payload = pair.get(arm)
        if not isinstance(payload, Mapping):
            continue
        latency = payload.get("latency") or {}
        if metric == "estimated_cost":
            value = latency.get(metric)
        else:
            value = (latency.get("stage_summary") or {}).get(metric, {}).get(
                "latency_p50_ms"
            )
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    return statistics.median(values) if values else None


def _pair_ratios(
    pairs: list[Mapping[str, Any]], metric: str
) -> list[float]:
    ratios = []
    for pair in pairs:
        baseline = _arm_metric([pair], "baseline", metric)
        candidate = _arm_metric([pair], "candidate", metric)
        if baseline and candidate is not None:
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
        baseline = _arm_metric([pair], "baseline", stage)
        candidate = _arm_metric([pair], "candidate", stage)
        if candidate is not None and candidate - (baseline or 0.0) > 0:
            deltas[stage] = candidate - (baseline or 0.0)
    return max(deltas, key=deltas.get) if deltas else "none"


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
    complete = len(pairs) == len(PAIR_ORDER) and all(
        isinstance(pair.get("baseline"), Mapping)
        and isinstance(pair.get("candidate"), Mapping)
        for pair in pairs
    )
    baseline_total = _arm_metric(pairs, "baseline", "total")
    candidate_total = _arm_metric(pairs, "candidate", "total")
    baseline_cost = _arm_metric(pairs, "baseline", "estimated_cost")
    candidate_cost = _arm_metric(pairs, "candidate", "estimated_cost")
    pair_latency_ratios = _pair_ratios(pairs, "total")
    pair_cost_ratios = _pair_ratios(pairs, "estimated_cost")
    latency_ratio_spread = _ratio_spread(pair_latency_ratios)
    cost_ratio_spread = _ratio_spread(pair_cost_ratios)
    latency_ratio = (
        candidate_total / baseline_total
        if baseline_total and candidate_total is not None
        else None
    )
    cost_ratio = (
        candidate_cost / baseline_cost
        if baseline_cost and candidate_cost is not None
        else None
    )
    stage_deltas = {}
    for stage in STAGES:
        baseline = _arm_metric(pairs, "baseline", stage)
        candidate = _arm_metric(pairs, "candidate", stage)
        if candidate is not None:
            stage_deltas[stage] = candidate - (baseline or 0.0)
    positive = {stage: value for stage, value in stage_deltas.items() if value > 0}
    gate_states = [bool((pair.get("gate") or {}).get("passed")) for pair in pairs]
    failed_check_sets = [_failed_checks(pair) for pair in pairs]
    dominant_pair_stages = [_dominant_pair_stage(pair) for pair in pairs]
    arm_order_consistent = bool(
        complete
        and len(set(gate_states)) == 1
        and len(set(failed_check_sets)) == 1
        and len(set(dominant_pair_stages)) == 1
        and latency_ratio_spread is not None
        and latency_ratio_spread <= MAX_PAIR_LATENCY_RATIO_SPREAD
        and cost_ratio_spread is not None
        and cost_ratio_spread <= MAX_PAIR_COST_RATIO_SPREAD
    )
    gates_passed = complete and all(gate_states)
    target_met = bool(
        not provider_failures
        and not provider_retries
        and arm_order_consistent
        and gates_passed
        and latency_ratio is not None
        and latency_ratio <= 1.25
        and cost_ratio is not None
        and cost_ratio <= 1.5
    )
    inconclusive = bool(
        provider_failures
        or provider_retries
        or not complete
        or not arm_order_consistent
    )
    return {
        "schema": "crag-stage-latency-diagnostic-outcome-v2",
        "formal_evidence": False,
        "status": "inconclusive" if inconclusive else "passed" if target_met else "failed",
        "source_commit": source_commit,
        "declaration_sha256": declaration_sha256,
        "pair_count": len(pairs),
        "provider_failure_count": provider_failures,
        "provider_retry_count": provider_retries,
        "arm_order_consistent": arm_order_consistent,
        "pair_latency_ratios": pair_latency_ratios,
        "pair_cost_ratios": pair_cost_ratios,
        "latency_ratio_spread": latency_ratio_spread,
        "cost_ratio_spread": cost_ratio_spread,
        "dominant_pair_stages": dominant_pair_stages,
        "latency_ratio": latency_ratio if complete and not provider_failures else None,
        "cost_ratio": cost_ratio if complete and not provider_failures else None,
        "diagnostic_target_met": target_met,
        "stage_deltas_ms": stage_deltas if complete and not provider_failures else {},
        "dominant_overhead_stage": (
            max(positive, key=positive.get)
            if complete and not provider_failures and positive
            else "none" if complete and not provider_failures else "unavailable"
        ),
        "pairs": [
            {"id": pair.get("id"), "gate_passed": bool((pair.get("gate") or {}).get("passed"))}
            for pair in pairs
        ],
        "formal_window_authorized": False,
        "feature_enablement_authorized": False,
        "next_action": (
            "Wait for externally confirmed provider recovery before a new declaration."
            if provider_failures or provider_retries
            else "Treat the delta as order-sensitive provider variance and use a new declaration."
            if complete and not arm_order_consistent
            else "Complete the predeclared diagnostic before choosing a code fix."
            if not complete
            else "Use the dominant stage to choose one root fix; keep both flags off."
        ),
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _require_inputs_unchanged(context: DiagnosticContext) -> None:
    require_clean_worktree()
    require_source_commit(context.source_commit)
    if _sha256(context.manifest) != context.manifest_sha256:
        raise RuntimeError("manifest changed during diagnostic")
    if _sha256(context.preflight) != context.preflight_sha256:
        raise RuntimeError("preflight changed during diagnostic")
    if _sha256(context.provider_smoke) != context.provider_smoke_sha256:
        raise RuntimeError("provider smoke changed during diagnostic")
    if _sha256(Path(__file__).resolve()) != context.runner["sha256"]:
        raise RuntimeError("diagnostic runner changed during diagnostic")


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
        },
        "latency": {
            "estimated_cost": float(latency_report.get("estimated_cost") or 0.0),
            "stage_summary": dict(latency_report.get("stage_summary") or {}),
        },
        "artifacts": {
            "eval": _artifact(eval_path),
            "trace": _artifact(trace_path),
            "latency": _artifact(latency_path),
        },
    }


def validate_preflight_report(
    report: Mapping[str, Any], *, expected_case_count: int
) -> None:
    if report.get("schema") != "crag-fixture-preflight-v1":
        raise ValueError("CRAG fixture preflight schema is invalid")
    if report.get("passed") is not True:
        raise ValueError("CRAG fixture preflight must pass")
    if report.get("collection") != FIXTURE_COLLECTION:
        raise ValueError(f"preflight collection must equal {FIXTURE_COLLECTION}")
    if int(report.get("checked_cases") or 0) != expected_case_count:
        raise ValueError("preflight case count must match the manifest")
    fingerprint = str(report.get("fixture_fingerprint") or "")
    if len(fingerprint) != 64:
        raise ValueError("preflight fixture fingerprint is invalid")


def validate_canonical_manifest(path: Path) -> None:
    if path.resolve() != CANONICAL_MANIFEST.resolve():
        raise ValueError("diagnostic requires the canonical CRAG manifest path")
    if _sha256(path) != CANONICAL_MANIFEST_SHA256:
        raise ValueError("canonical CRAG manifest hash is not approved")


def _validate_run_inputs(
    manifest: Path,
    preflight: Path,
    provider_smoke: Path,
    output: Path,
    trace: Path,
) -> Mapping[str, Any]:
    if os.getenv(DIAGNOSTIC_OPT_IN) != "1":
        raise RuntimeError(f"set {DIAGNOSTIC_OPT_IN}=1 before running diagnostic")
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
    router_mode: str,
) -> DiagnosticContext:
    preflight_report = _validate_run_inputs(
        manifest,
        preflight,
        provider_smoke,
        output,
        trace,
    )
    require_clean_worktree()
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
        router_mode=router_mode,
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
        router_mode=context.router_mode,
        provider_configuration_sha256=context.provider_configuration_sha256,
        governance_scope_sha256_value=context.governance_scope_sha256,
        provider_environment=dict(context.provider_environment),
        started_at=started_at,
    )
    eval_path = pair_dir / label / "eval.json"
    trace_path = pair_dir / label / "trace.json"
    latency_path = pair_dir / label / "latency-breakdown.json"
    eval_report = json.loads(eval_path.read_text(encoding="utf-8"))
    trace_report = json.loads(trace_path.read_text(encoding="utf-8"))
    latency_report = build_latency_breakdown(
        context.trace,
        start=timing["started_at"],
        end=timing["completed_at"],
        execution_contexts={"evaluation"},
    )
    _write_json(latency_path, latency_report)
    arm = _sanitized_arm(
        eval_report=eval_report,
        latency_report=latency_report,
        eval_path=eval_path,
        trace_path=trace_path,
        latency_path=latency_path,
    )
    _verify_arm_preflight(context, pair_dir / label / "preflight.json")
    _require_inputs_unchanged(context)
    return {**arm, "_gate_inputs": {"eval": eval_report, "trace": trace_report}}


def _verify_arm_preflight(context: DiagnosticContext, path: Path) -> None:
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("fixture_fingerprint") != context.preflight_report.get(
        "fixture_fingerprint"
    ):
        raise RuntimeError("fixture snapshot changed during diagnostic")


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
            return pair, arm_starts, True

    baseline, baseline_inputs = _without_gate_inputs(arms["baseline"])
    candidate, candidate_inputs = _without_gate_inputs(arms["candidate"])
    gate = compare_reports(
        baseline_inputs["eval"],
        candidate_inputs["eval"],
        baseline_inputs["trace"],
        candidate_inputs["trace"],
    )
    pair = {
        "id": pair_id,
        "arm_order": arm_order,
        "baseline": baseline,
        "candidate": candidate,
        "gate": gate,
    }
    _write_json(pair_dir / "gate.json", gate)
    _write_json(pair_dir / "summary.json", pair)
    return pair, arm_starts, False


def _without_gate_inputs(
    arm: Mapping[str, Any],
) -> tuple[dict[str, Any], Mapping[str, Any]]:
    inputs = arm.get("_gate_inputs") or {}
    sanitized = {key: value for key, value in arm.items() if key != "_gate_inputs"}
    return sanitized, inputs


def run_diagnostic(
    *,
    manifest: Path,
    preflight: Path,
    provider_smoke: Path,
    output: Path,
    trace: Path,
    router_mode: str = "offline",
) -> dict[str, Any]:
    context = _prepare_context(
        manifest,
        preflight,
        provider_smoke,
        output,
        trace,
        router_mode,
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
    parser.add_argument(
        "--router-mode", choices=("offline", "provider"), default="offline"
    )
    args = parser.parse_args(argv)
    report = run_diagnostic(
        manifest=args.manifest,
        preflight=args.preflight,
        provider_smoke=args.provider_smoke_artifact,
        output=args.output_dir,
        trace=args.trace,
        router_mode=args.router_mode,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
