"""Run a non-formal interleaved CRAG and Claim Repair diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from scripts.crag_eval import diagnostic_aggregation as aggregation_module
from scripts.crag_eval import run_rollout as rollout_module
from scripts.crag_eval.constants import FIXTURE_COLLECTION
from scripts.crag_eval.diagnostic_aggregation import (
    MAX_PAIR_COST_RATIO_SPREAD,
    MAX_PAIR_LATENCY_RATIO_SPREAD,
    build_diagnostic_outcome,
    build_series_summary,
    provider_failures as _provider_failures,
    provider_retries as _provider_retries,
    validate_singleton_arm,
    validate_singleton_evaluation,
)
from scripts.crag_eval.run_rollout import (
    _run,
    governance_scope_sha256,
    require_clean_worktree,
    require_source_commit,
)
from scripts.eval.crag_latency_breakdown import build_latency_breakdown
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
SERIES_ORDER = (
    ("series-01", "candidate-first"),
    ("series-02", "baseline-first"),
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
    runner: Mapping[str, Any]
    case_ids: tuple[str, ...]
    provider_configuration_sha256: str
    provider_environment: Mapping[str, str]
    governance_scope_sha256: str


@dataclass(frozen=True)
class DiagnosticExecution:
    series: tuple[dict[str, Any], ...] = ()
    arm_starts: tuple[str, ...] = ()
    completed_case_pairs: int = 0
    arm_run_count: int = 0
    execution_failure: Mapping[str, str] | None = None
    stopped_case: Mapping[str, Any] | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def _runner_provenance() -> dict[str, Mapping[str, str]]:
    return {
        "diagnostic": _artifact(Path(__file__).resolve()),
        "rollout": _artifact(Path(rollout_module.__file__).resolve()),
        "aggregation": _artifact(Path(aggregation_module.__file__).resolve()),
    }


def _series_plan(case_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        {
            "id": series_id,
            "arm_order": arm_order,
            "case_ids": list(case_ids),
        }
        for series_id, arm_order in SERIES_ORDER
    ]


def build_diagnostic_declaration(
    *,
    source_commit: str,
    manifest: Mapping[str, Any],
    preflight: Mapping[str, Any],
    provider_configuration_sha256: str,
    provider_smoke: Mapping[str, Any],
    governance_scope_sha256: str,
    runner: Mapping[str, Any],
    case_ids: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "schema": "crag-stage-latency-diagnostic-declaration-v3",
        "scope": "supporting_diagnostic_only",
        "measurement_design": "mirrored_case_paired_interleaved",
        "trace_strategy": "one_private_log_per_series_case_arm",
        "case_count": len(case_ids),
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
        "series_plan": _series_plan(case_ids),
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
            "stop and mark inconclusive on any provider retry",
            "do not refresh provider smoke inside the declared window",
            "do not rerun, resume, carry forward, or overwrite any case or series",
        ],
        "formal_window_authorized": False,
        "controlled_demo_pilot_authorized": False,
        "default_rollout_authorized": False,
        "feature_enablement_authorized": False,
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
    if any(
        _sha256(Path(reference["path"])) != reference["sha256"]
        for reference in context.runner.values()
    ):
        raise RuntimeError("diagnostic runner changed during diagnostic")


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


def _manifest_case_ids(path: Path) -> tuple[str, ...]:
    rows = [
        json.loads(raw)
        for raw in path.read_text(encoding="utf-8").splitlines()
        if raw.strip()
    ]
    case_ids = tuple(str(row.get("id") or "").strip() for row in rows)
    if not case_ids or any(not case_id for case_id in case_ids):
        raise ValueError("canonical CRAG manifest contains an empty case ID")
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("canonical CRAG manifest contains duplicate case IDs")
    return case_ids


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
    case_ids = _manifest_case_ids(manifest)
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
        case_ids=case_ids,
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
        case_ids=context.case_ids,
    )
    declaration_path = context.output / "declaration.json"
    _write_json(declaration_path, declaration)
    return _sha256(declaration_path)


def _run_arm(
    context: DiagnosticContext,
    case_dir: Path,
    *,
    label: str,
    enabled: bool,
    arm_starts: tuple[str, ...],
    case_id: str,
) -> dict[str, Any]:
    started_at = arm_starts[-1]
    _require_inputs_unchanged(context)
    validate_provider_smoke_for_arms(
        context.provider_smoke,
        expected_provider_sha256=context.provider_configuration_sha256,
        arm_started_at=arm_starts,
    )
    trace_log = _prepare_arm_trace(case_dir, label)
    timing = _run(
        label,
        context.manifest,
        case_dir,
        trace_log,
        enabled=enabled,
        router_mode=context.router_mode,
        provider_configuration_sha256=context.provider_configuration_sha256,
        governance_scope_sha256_value=context.governance_scope_sha256,
        provider_environment=dict(context.provider_environment),
        started_at=started_at,
        case_id=case_id,
    )
    arm = _load_arm_artifacts(case_dir / label, trace_log, timing)
    _verify_arm_preflight(context, case_dir / label / "preflight.json")
    validate_singleton_evaluation(case_id, arm)
    if not _provider_failures(arm) and not _provider_retries(arm):
        validate_singleton_arm(case_id, arm)
    _require_inputs_unchanged(context)
    return arm


def _prepare_arm_trace(case_dir: Path, label: str) -> Path:
    trace_log = case_dir / label / "rag_trace.jsonl"
    trace_log.parent.mkdir(parents=True, exist_ok=True)
    if trace_log.exists() and trace_log.stat().st_size:
        raise ValueError("case arm trace must be new or empty")
    trace_log.touch(exist_ok=True)
    return trace_log


def _load_arm_artifacts(
    arm_dir: Path,
    trace_log: Path,
    timing: Mapping[str, str],
) -> dict[str, Any]:
    paths = {
        "eval": arm_dir / "eval.json",
        "trace": arm_dir / "trace.json",
        "latency": arm_dir / "latency-breakdown.json",
    }
    reports = {
        name: json.loads(path.read_text(encoding="utf-8"))
        for name, path in paths.items()
        if name != "latency"
    }
    latency = build_latency_breakdown(
        trace_log,
        start=timing["started_at"],
        end=timing["completed_at"],
        execution_contexts={"evaluation"},
    )
    _write_json(paths["latency"], latency)
    return {
        **reports,
        "latency": latency,
        "artifacts": {name: _artifact(path) for name, path in paths.items()},
    }


def _verify_arm_preflight(context: DiagnosticContext, path: Path) -> None:
    report = json.loads(path.read_text(encoding="utf-8"))
    if (
        report.get("fixture_fingerprint")
        != context.preflight_report.get("fixture_fingerprint")
        or report.get("checked_cases") != 1
    ):
        raise RuntimeError("fixture snapshot changed during diagnostic")


def _public_case_arm(arm: Mapping[str, Any]) -> dict[str, Any]:
    evaluation = arm.get("eval") or {}
    latency = arm.get("latency") or {}
    return {
        "eval": {
            "provider_failure_count": _provider_failures({"eval": evaluation}),
            "provider_retries": int(evaluation.get("provider_retries") or 0),
            "total_cases": int(evaluation.get("total_cases") or 0),
            "passed_cases": int(evaluation.get("passed_cases") or 0),
        },
        "latency": {
            "estimated_cost": float(latency.get("estimated_cost") or 0.0),
            "stage_summary": dict(latency.get("stage_summary") or {}),
        },
        "artifacts": dict(arm.get("artifacts") or {}),
    }


def _run_case_pair(
    context: DiagnosticContext,
    *,
    series_id: str,
    ordinal: int,
    case_id: str,
    arm_order: str,
    arm_starts: tuple[str, ...],
) -> tuple[dict[str, Any], tuple[str, ...], bool]:
    case_dir = context.output / series_id / f"case-{ordinal:03d}"
    arms: dict[str, dict[str, Any]] = {}
    for label, enabled in _arm_specs(arm_order):
        arm_starts = (*arm_starts, _utc_now())
        arm = _run_arm(
            context,
            case_dir,
            label=label,
            enabled=enabled,
            arm_starts=arm_starts,
            case_id=case_id,
        )
        arms = {**arms, label: arm}
        if _provider_failures(arm) or _provider_retries(arm):
            pair = _case_pair(case_id, arm_order, arms)
            _write_case_summary(case_dir, pair, stopped=True)
            return pair, arm_starts, True

    pair = _case_pair(case_id, arm_order, arms)
    _write_case_summary(case_dir, pair, stopped=False)
    return pair, arm_starts, False


def _arm_specs(arm_order: str) -> tuple[tuple[str, bool], ...]:
    if arm_order == "candidate-first":
        return (("candidate", True), ("baseline", False))
    return (("baseline", False), ("candidate", True))


def _case_pair(
    case_id: str,
    arm_order: str,
    arms: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    return {"case_id": case_id, "arm_order": arm_order, **arms}


def _write_case_summary(
    case_dir: Path,
    pair: Mapping[str, Any],
    *,
    stopped: bool,
) -> None:
    arms = {
        label: _public_case_arm(pair[label])
        for label in ("baseline", "candidate")
        if label in pair
    }
    report = {
        "case_id": pair["case_id"],
        "arm_order": pair["arm_order"],
        **arms,
    }
    if stopped:
        report = {**report, "stopped": "provider_variance"}
    _write_json(case_dir / "summary.json", report)


def _stopped_case(
    series_id: str,
    case_id: str,
    pair: Mapping[str, Any],
) -> dict[str, Any]:
    labels = ("baseline", "candidate")
    return {
        "series_id": series_id,
        "case_id": case_id,
        "provider_failure_count": sum(
            _provider_failures(pair.get(label)) for label in labels
        ),
        "provider_retry_count": sum(
            _provider_retries(pair.get(label)) for label in labels
        ),
    }


def _run_series(
    context: DiagnosticContext,
    execution: DiagnosticExecution,
    *,
    series_id: str,
    arm_order: str,
) -> DiagnosticExecution:
    case_pairs: tuple[dict[str, Any], ...] = ()
    state = execution
    for ordinal, case_id in enumerate(context.case_ids, start=1):
        pair, starts, stopped = _run_case_pair(
            context,
            series_id=series_id,
            ordinal=ordinal,
            case_id=case_id,
            arm_order=arm_order,
            arm_starts=state.arm_starts,
        )
        state = replace(
            state,
            arm_starts=starts,
            arm_run_count=state.arm_run_count
            + sum(label in pair for label in ("baseline", "candidate")),
        )
        if stopped:
            return replace(state, stopped_case=_stopped_case(series_id, case_id, pair))
        case_pairs = (*case_pairs, pair)
        state = replace(
            state,
            completed_case_pairs=state.completed_case_pairs + 1,
        )
    summary = build_series_summary(
        series_id=series_id,
        arm_order=arm_order,
        case_pairs=case_pairs,
    )
    _write_json(context.output / series_id / "summary.json", summary)
    return replace(state, series=(*state.series, summary))


def _execute_series_plan(context: DiagnosticContext) -> DiagnosticExecution:
    execution = DiagnosticExecution()
    try:
        for series_id, arm_order in SERIES_ORDER:
            execution = _run_series(
                context,
                execution,
                series_id=series_id,
                arm_order=arm_order,
            )
            if execution.stopped_case:
                break
    except (RuntimeError, ValueError) as exc:
        execution = replace(
            execution,
            execution_failure={"error_type": type(exc).__name__},
        )
    return execution


def _finalize_diagnostic(
    context: DiagnosticContext,
    declaration_sha: str,
    execution: DiagnosticExecution,
) -> dict[str, Any]:
    series = list(execution.series)
    try:
        base = build_diagnostic_outcome(
            series,
            source_commit=context.source_commit,
            declaration_sha256=declaration_sha,
        )
        return aggregation_module.finalize_case_paired_outcome(
            base,
            series,
            expected_case_ids=context.case_ids,
            completed_case_pairs=execution.completed_case_pairs,
            arm_run_count=execution.arm_run_count,
            execution_failure=execution.execution_failure,
            stopped_case=execution.stopped_case,
        )
    except (RuntimeError, ValueError) as exc:
        empty = build_diagnostic_outcome(
            [],
            source_commit=context.source_commit,
            declaration_sha256=declaration_sha,
        )
        return aggregation_module.finalize_case_paired_outcome(
            empty,
            [],
            expected_case_ids=context.case_ids,
            completed_case_pairs=execution.completed_case_pairs,
            arm_run_count=execution.arm_run_count,
            execution_failure={"error_type": type(exc).__name__},
            stopped_case=execution.stopped_case,
        )


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
    outcome = _finalize_diagnostic(
        context,
        declaration_sha,
        _execute_series_plan(context),
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
