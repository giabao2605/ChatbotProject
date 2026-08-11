"""Run a non-formal, reversed-arm Graph retrieval latency diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
from scripts.graph_eval.diagnostic_metrics import (
    MAX_ARM_ORDER_LATENCY_RATIO_SPREAD,
    MAX_COST_RATIO,
    MAX_LATENCY_P95_RATIO,
    _provider_failures,
    _provider_retries,
    build_diagnostic_outcome,
)
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
METRICS_MODULE = ROOT / "scripts" / "graph_eval" / "diagnostic_metrics.py"


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
    runner: Mapping[str, Any]
    provider_configuration_sha256: str
    provider_environment: Mapping[str, str]
    governance_scope_sha256: str
    cases: tuple[Mapping[str, Any], ...]
    settings: Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def build_case_plan(cases: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    case_ids = [str(case.get("id") or "").strip() for case in cases]
    if not case_ids or any(not case_id for case_id in case_ids):
        raise ValueError("diagnostic cases require non-empty ids")
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("diagnostic case ids must be unique")
    return [
        {
            "ordinal": ordinal,
            "case_id": case_id,
            "arm_order": (
                "candidate-first" if ordinal % 2 else "baseline-first"
            ),
        }
        for ordinal, case_id in enumerate(case_ids, start=1)
    ]


def probe_qdrant_health(
    settings: Any,
    *,
    runtime_builder=None,
) -> dict[str, Any]:
    from mech_chatbot.config.settings import QdrantSettings

    if runtime_builder is None:
        from mech_chatbot.adapters.qdrant_runtime import (
            build_qdrant_admin_runtime,
        )

        runtime_builder = build_qdrant_admin_runtime
    try:
        qdrant_settings = QdrantSettings.from_settings(settings)
        qdrant_settings = QdrantSettings(
            url=qdrant_settings.url,
            api_key=qdrant_settings.api_key,
            collection=FIXTURE_COLLECTION,
            embedding_model=qdrant_settings.embedding_model,
            embedding_device=qdrant_settings.embedding_device,
            embedding_dimension=qdrant_settings.embedding_dimension,
        )
        runtime = runtime_builder(qdrant_settings, timeout_seconds=10)
        try:
            points, _ = runtime.client.scroll(
                collection_name=FIXTURE_COLLECTION,
                limit=1,
                with_payload=False,
                with_vectors=False,
            )
        finally:
            runtime.close()
        point_observed = bool(points)
        return {
            "schema": "graph-qdrant-health-v1",
            "checked_at": _utc_now(),
            "collection": FIXTURE_COLLECTION,
            "passed": point_observed,
            "point_observed": point_observed,
            **({} if point_observed else {"reason": "empty_collection"}),
        }
    except Exception as exc:
        return {
            "schema": "graph-qdrant-health-v1",
            "checked_at": _utc_now(),
            "collection": FIXTURE_COLLECTION,
            "passed": False,
            "point_observed": False,
            "error_type": type(exc).__name__,
        }


def _runner_provenance() -> dict[str, Any]:
    return {
        **_artifact(Path(__file__).resolve()),
        "dependencies": {
            "diagnostic_metrics": _artifact(METRICS_MODULE),
        },
    }


def _require_runner_in_source_commit() -> None:
    for runner in (Path(__file__).resolve(), METRICS_MODULE):
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
    case_plan: list[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": "graph-latency-diagnostic-declaration-v2",
        "scope": "supporting_diagnostic_only",
        "measurement_design": "case_paired_interleaved",
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
        "case_plan": [dict(item) for item in case_plan],
        "health_gate": {
            "before_each_case_pair": True,
            "collection": FIXTURE_COLLECTION,
            "required_status": "passed",
        },
        "limits": {
            "max_latency_p95_ratio": MAX_LATENCY_P95_RATIO,
            "max_cost_ratio": MAX_COST_RATIO,
            "max_arm_order_latency_ratio_spread": (
                MAX_ARM_ORDER_LATENCY_RATIO_SPREAD
            ),
            "provider_errors": 0,
            "provider_retries": 0,
        },
        "review_requirement": {
            "mode": "multi_reviewer",
            "source": "independent",
        },
        "stage_breakdown": ["retrieval", "generation"],
        "stage_evidence_requirement": {
            "retrieval": "every_case_both_arms",
            "generation": (
                "same_case_presence_both_arms_and_at_least_one_pair"
            ),
        },
        "stop_rules": [
            "stop on source, fixture, manifest, provider, governance, or runner drift",
            "stop before a case pair when the Qdrant health preflight fails",
            "stop and mark inconclusive on any fallback, provider failure, or retry",
            "do not rerun, overwrite, or carry forward any case pair",
        ],
        "formal_window_authorized": False,
        "feature_enablement_authorized": False,
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


def _validate_latency_report(
    report: Mapping[str, Any],
    *,
    expected_case_count: int,
) -> None:
    if int(report.get("query_count") or 0) != expected_case_count:
        raise RuntimeError("diagnostic trace query count must match the eval cases")
    if int(report.get("parse_errors") or 0):
        raise RuntimeError("diagnostic trace must have zero parse errors")
    stage_summary = report.get("stage_summary") or {}
    retrieval = stage_summary.get("retrieval") or {}
    if int(retrieval.get("sample_count") or 0) != expected_case_count:
        raise RuntimeError(
            "diagnostic retrieval stage must cover every eval case"
        )
    generation = stage_summary.get("generation") or {}
    generation_count = int(generation.get("sample_count") or 0)
    if generation_count < 0 or generation_count > expected_case_count:
        raise RuntimeError("diagnostic generation stage sample count is invalid")


def _validate_run_inputs(
    manifest: Path,
    preflight: Path,
    provider_smoke: Path,
    output: Path,
    trace: Path,
) -> tuple[Mapping[str, Any], tuple[Mapping[str, Any], ...]]:
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
    cases = tuple(
        json.loads(raw)
        for raw in manifest.read_text(encoding="utf-8").splitlines()
        if raw.strip()
    )
    if not cases or any(not isinstance(case, Mapping) for case in cases):
        raise ValueError("canonical Graph manifest must contain object cases")
    build_case_plan(list(cases))
    manifest_case_count = len(cases)
    validate_preflight_report(
        preflight_report,
        expected_case_count=manifest_case_count,
    )
    return preflight_report, cases


def _prepare_context(
    manifest: Path,
    preflight: Path,
    provider_smoke: Path,
    output: Path,
    trace: Path,
) -> DiagnosticContext:
    preflight_report, cases = _validate_run_inputs(
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
        cases=cases,
        settings=settings,
    )


def _require_inputs_unchanged(context: DiagnosticContext) -> None:
    require_clean_worktree()
    require_source_commit(context.source_commit)
    checks = (
        (context.manifest, context.manifest_sha256, "manifest"),
        (context.preflight, context.preflight_sha256, "preflight"),
        (context.provider_smoke, context.provider_smoke_sha256, "provider smoke"),
        (Path(__file__).resolve(), context.runner["sha256"], "diagnostic runner"),
        (
            METRICS_MODULE,
            context.runner["dependencies"]["diagnostic_metrics"]["sha256"],
            "diagnostic metrics",
        ),
    )
    for path, expected_sha, label in checks:
        if _sha256(path) != expected_sha:
            raise RuntimeError(f"{label} changed during diagnostic")


def _write_declaration(
    context: DiagnosticContext,
    case_plan: list[Mapping[str, Any]],
) -> str:
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
        case_plan=case_plan,
    )
    declaration_path = context.output / "declaration.json"
    _write_json(declaration_path, declaration)
    return _sha256(declaration_path)


def _verify_arm_preflight(
    context: DiagnosticContext,
    path: Path,
    *,
    case_id: str,
) -> None:
    report = json.loads(path.read_text(encoding="utf-8"))
    validate_preflight_report(report, expected_case_count=1)
    if report.get("fixture_fingerprint") != context.preflight_report.get(
        "fixture_fingerprint"
    ):
        raise RuntimeError("fixture snapshot changed during diagnostic")
    if set((report.get("case_resolutions") or {}).keys()) != {case_id}:
        raise RuntimeError("case-scoped preflight resolved an unexpected case")
    case_scoped_fields = {
        "coverage_numerator",
        "coverage_denominator",
        "structured_coverage",
        "domain_coverage",
    }
    expected_graph = {
        key: value
        for key, value in context.preflight_report["graph_report"].items()
        if key not in case_scoped_fields
    }
    actual_graph = {
        key: value
        for key, value in report["graph_report"].items()
        if key not in case_scoped_fields
    }
    if actual_graph != expected_graph:
        raise RuntimeError("graph governance or data-plane evidence changed")


def _run_case_health(
    context: DiagnosticContext,
    case_dir: Path,
) -> dict[str, Any]:
    _require_inputs_unchanged(context)
    health_path = case_dir / "qdrant-health.json"
    report = probe_qdrant_health(context.settings)
    _write_json(health_path, report)
    _require_inputs_unchanged(context)
    return {
        "passed": report.get("passed") is True,
        **({"error_type": report["error_type"]} if report.get("error_type") else {}),
        **({"reason": report["reason"]} if report.get("reason") else {}),
        "artifact": _artifact(health_path),
    }


def _sanitized_arm(
    *,
    eval_report: Mapping[str, Any],
    trace_report: Mapping[str, Any],
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
        "trace": {
            "error_event_count": int(
                trace_report.get("error_event_count") or 0
            ),
            "error_events": dict(trace_report.get("error_events") or {}),
            "fallback_event_count": int(
                trace_report.get("fallback_event_count") or 0
            ),
            "fallback_events": dict(trace_report.get("fallback_events") or {}),
            "retry_event_count": int(
                trace_report.get("retry_event_count") or 0
            ),
            "retry_events": dict(trace_report.get("retry_events") or {}),
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
    case_id: str | None = None,
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
        provider_environment={
            **context.provider_environment,
            "RAG_TRACE_LOG_FILE": str(context.trace.resolve()),
        },
        started_at=started_at,
        case_id=case_id,
    )
    eval_path = pair_dir / label / "eval.json"
    trace_path = pair_dir / label / "trace.json"
    latency_path = pair_dir / label / "latency-breakdown.json"
    eval_report = json.loads(eval_path.read_text(encoding="utf-8"))
    trace_report = json.loads(trace_path.read_text(encoding="utf-8"))
    latency_report = {
        **build_latency_breakdown(
            context.trace,
            start=timing["started_at"],
            end=timing["completed_at"],
            execution_contexts={"evaluation"},
        ),
        "schema": "graph-latency-breakdown-v1",
    }
    _validate_latency_report(
        latency_report,
        expected_case_count=int(eval_report.get("total_cases") or 0),
    )
    _write_json(latency_path, latency_report)
    if not case_id:
        raise RuntimeError("case-scoped diagnostic arm requires a case id")
    _verify_arm_preflight(
        context,
        pair_dir / label / "preflight.json",
        case_id=case_id,
    )
    arm = _sanitized_arm(
        eval_report=eval_report,
        trace_report=trace_report,
        latency_report=latency_report,
        eval_path=eval_path,
        trace_path=trace_path,
        latency_path=latency_path,
    )
    _require_inputs_unchanged(context)
    return arm


def _run_case_pair(
    context: DiagnosticContext,
    plan: Mapping[str, Any],
    arm_starts: tuple[str, ...],
) -> tuple[dict[str, Any], tuple[str, ...], bool]:
    arm_order = str(plan["arm_order"])
    arm_specs = (
        (("candidate", True), ("baseline", False))
        if arm_order == "candidate-first"
        else (("baseline", False), ("candidate", True))
    )
    pair_dir = context.output / f"case-{int(plan['ordinal']):03d}"
    pair_dir.mkdir(parents=True, exist_ok=False)
    health = _run_case_health(context, pair_dir)
    pair_identity = {
        "id": str(plan["case_id"]),
        "ordinal": int(plan["ordinal"]),
        "arm_order": arm_order,
        "health": health,
    }
    if health.get("passed") is not True:
        pair = dict(pair_identity)
        _write_json(pair_dir / "summary.json", pair)
        return pair, arm_starts, True
    arms: dict[str, dict[str, Any]] = {}
    for label, enabled in arm_specs:
        arm_starts = (*arm_starts, _utc_now())
        try:
            arm = _run_arm(
                context,
                pair_dir,
                label=label,
                enabled=enabled,
                arm_starts=arm_starts,
                case_id=str(plan["case_id"]),
            )
        except Exception as exc:
            pair = {
                **pair_identity,
                **arms,
                "execution_failure": {
                    "phase": "arm",
                    "arm": label,
                    "error_type": type(exc).__name__,
                },
            }
            _write_json(pair_dir / "summary.json", pair)
            return pair, arm_starts, True
        arms = {**arms, label: arm}
        if _provider_failures(arm) or _provider_retries(arm):
            pair = {
                **pair_identity,
                **arms,
            }
            _write_json(pair_dir / "summary.json", pair)
            return pair, arm_starts, True

    pair = {
        **pair_identity,
        "baseline": arms["baseline"],
        "candidate": arms["candidate"],
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
    case_plan = build_case_plan(list(context.cases))
    declaration_sha = _write_declaration(context, case_plan)
    arm_starts: tuple[str, ...] = ()
    pairs: tuple[dict[str, Any], ...] = ()
    for plan in case_plan:
        pair, arm_starts, stopped = _run_case_pair(
            context,
            plan,
            arm_starts,
        )
        pairs = (*pairs, pair)
        if stopped:
            break
    outcome = build_diagnostic_outcome(
        list(pairs),
        case_plan=case_plan,
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
