"""Build and verify the technical authorization that precedes a CRAG pilot."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mech_chatbot.governance.artifact_references import (
    build_json_reference,
    load_json_reference,
)
from mech_chatbot.governance.provider_smoke import (
    provider_smoke_artifact_valid,
    provider_smoke_fresh_for_baseline,
)
from mech_chatbot.governance.rollout_guardrails import evaluate_rollout_series


SCHEMA = "crag-controlled-demo-authorization-v1"
SERIES_SCHEMA = "rollout-guardrail-series-v1"
SMOKE_SCHEMA = "provider-smoke-v1"
REVIEW_MODES = {"multi_reviewer", "single_owner"}


def _reference_identity(reference: object) -> tuple[str, str]:
    if not isinstance(reference, dict):
        return ("", "")
    return (
        str(reference.get("sha256") or reference.get("artifact_sha256") or ""),
        str(reference.get("schema") or reference.get("artifact_schema") or ""),
    )


def _utc(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def _checks(artifact: dict, *, root: Path) -> dict[str, bool]:
    series = load_json_reference(artifact.get("series"), root=root)
    pair_references = (series or {}).get("source_artifacts")
    pair_references_valid = (
        isinstance(pair_references, list)
        and len(pair_references) == 3
        and all(
            isinstance(item, dict)
            and item.get("schema") == "rollout-evidence-pair-v1"
            for item in pair_references
        )
        and len({str(item.get("sha256") or "") for item in pair_references}) == 3
    )
    pairs = (
        [load_json_reference(item, root=root) for item in pair_references]
        if pair_references_valid else []
    )
    recomputed_series = (
        evaluate_rollout_series(
            "crag",
            pairs,
            prior_decisions=(series or {}).get("prior_decisions"),
            minimum_pairs=3,
            pair_references=pair_references,
            root=root,
        )
        if len(pairs) == 3 and all(pairs) else {}
    )
    pair_reference = (
        pairs[0].get("baseline") or {}
        if pairs and pairs[0] is not None else {}
    )
    series_contract_fields = (
        "stage",
        "source_commit",
        "provider_configuration_sha256",
        "pair_count",
        "run_ids",
        "pair_windows",
        "source_artifacts",
        "prior_decisions",
        "passed",
        "production_eligible",
        "checks",
        "minimum_pairs",
        "dependencies",
        "pair_reports",
    )
    series_result_bound = bool(recomputed_series) and all(
        (series or {}).get(field) == recomputed_series.get(field)
        for field in series_contract_fields
    )
    smoke_references = artifact.get("provider_smokes")
    smoke_references_valid = (
        isinstance(smoke_references, list)
        and len(smoke_references) == 3
        and all(
            isinstance(item, dict) and item.get("schema") == SMOKE_SCHEMA
            for item in smoke_references
        )
        and len({str(item.get("sha256") or "") for item in smoke_references}) == 3
    )
    smokes = (
        [load_json_reference(item, root=root) for item in smoke_references]
        if smoke_references_valid else []
    )
    smoke_references_bound_to_pairs = (
        len(pairs) == 3
        and isinstance(smoke_references, list)
        and [
            _reference_identity(item)
            for item in smoke_references
        ] == [
            _reference_identity(pair.get("provider_smoke"))
            for pair in pairs
            if isinstance(pair, dict)
        ]
    )
    source_commit = str(artifact.get("source_commit") or "")
    series_windows = (series or {}).get("pair_windows")
    timing_valid = (
        len(smokes) == 3
        and all(smokes)
        and isinstance(series_windows, list)
        and len(series_windows) == 3
    )
    if timing_valid:
        smoke_times = [_utc(smoke.get("completed_at")) for smoke in smokes]
        pair_times = [
            _utc(window.get("baseline_started_at"))
            if isinstance(window, dict) else None
            for window in series_windows
        ]
        timing_valid = all(
            smoke_time is not None
            and pair_time is not None
            and smoke_time <= pair_time
            for smoke_time, pair_time in zip(smoke_times, pair_times, strict=True)
        )
    freshness_valid = (
        len(smokes) == 3
        and all(smokes)
        and isinstance(series_windows, list)
        and len(series_windows) == 3
        and all(
            provider_smoke_fresh_for_baseline(
                smoke,
                baseline_started_at=window.get("baseline_started_at"),
            )
            for smoke, window in zip(smokes, series_windows, strict=True)
            if isinstance(window, dict)
        )
        and all(isinstance(window, dict) for window in series_windows)
    )
    provider_hash = str((series or {}).get("provider_configuration_sha256") or "")
    run_ids = (series or {}).get("run_ids")
    run_ids_valid = (
        isinstance(run_ids, list)
        and len(run_ids) == 3
        and all(isinstance(run_id, str) and run_id.strip() for run_id in run_ids)
        and len(set(run_ids)) == 3
    )
    source_artifacts = artifact.get("source_artifacts")
    expected_source_artifacts = [
        artifact.get("series"), *(smoke_references or []),
    ]
    return {
        "schema_valid": artifact.get("schema") == SCHEMA,
        "source_commit_present": bool(source_commit),
        "review_mode_valid": (
            (artifact.get("review_governance") or {}).get("mode") in REVIEW_MODES
        ),
        "series_reference_valid": series is not None,
        "series_pair_references_valid": pair_references_valid and all(pairs),
        "complete_series_contract_revalidated": (
            recomputed_series.get("passed") is True
            and recomputed_series.get("production_eligible") is True
        ),
        "series_result_bound": series_result_bound,
        "series_stage_valid": (series or {}).get("stage") == "crag",
        "series_commit_matches": (
            bool(source_commit)
            and (series or {}).get("source_commit") == source_commit
            and pair_reference.get("git_sha") == source_commit
        ),
        "series_passed": (
            (series or {}).get("passed") is True
            and (series or {}).get("production_eligible") is True
        ),
        "exactly_three_independent_pairs": (
            series_result_bound
            and (series or {}).get("pair_count") == 3
            and run_ids_valid
            and (recomputed_series.get("checks") or {}).get(
                "all_pairs_match_stage"
            ) is True
            and (recomputed_series.get("checks") or {}).get(
                "minimum_independent_pairs"
            ) is True
            and (recomputed_series.get("checks") or {}).get(
                "independent_pair_evidence"
            ) is True
            and (recomputed_series.get("checks") or {}).get(
                "prior_milestones_completed"
            ) is True
        ),
        "provider_smoke_references_valid": smoke_references_valid and all(smokes),
        "provider_smokes_bound_to_pairs": smoke_references_bound_to_pairs,
        "provider_smokes_passed": (
            len(smokes) == 3
            and all(
                provider_smoke_artifact_valid(
                    smoke,
                    expected_provider_sha256=provider_hash,
                )
                for smoke in smokes
            )
        ),
        "provider_configuration_matches": (
            bool(provider_hash)
            and pair_reference.get("provider_configuration_sha256") == provider_hash
            and len(smokes) == 3
            and all(
                smoke is not None
                and smoke.get("provider_configuration_sha256") == provider_hash
                for smoke in smokes
            )
        ),
        "provider_smokes_precede_pairs": timing_valid,
        "provider_smokes_fresh_for_pairs": freshness_valid,
        "source_artifacts_bound": source_artifacts == expected_source_artifacts,
    }


def _provider_blocked(artifact: dict, *, root: Path) -> bool:
    references = artifact.get("provider_smokes")
    if not isinstance(references, list):
        return False
    smokes = [load_json_reference(reference, root=root) for reference in references]
    return bool(smokes) and all(smoke is not None for smoke in smokes) and any(
        isinstance(smoke.get("provider_outcome"), dict)
        and smoke["provider_outcome"].get("provider_blocked") is True
        for smoke in smokes
    )


def validate_crag_demo_authorization(
    artifact: object, *, root: str | Path,
) -> dict:
    if not isinstance(artifact, dict):
        return {"schema": "crag-controlled-demo-authorization-validation-v1",
                "passed": False, "checks": {"artifact_object": False}}
    checks = _checks(artifact, root=Path(root))
    derived_passed = all(checks.values())
    expected_decision = (
        "accepted" if derived_passed
        else "inconclusive" if _provider_blocked(artifact, root=Path(root))
        else "rejected"
    )
    checks["result_consistent"] = (
        artifact.get("checks") == checks
        and artifact.get("passed") is derived_passed
        and artifact.get("controlled_demo_eligible") is derived_passed
        and artifact.get("decision") == expected_decision
    )
    return {
        "schema": "crag-controlled-demo-authorization-validation-v1",
        "passed": all(checks.values()),
        "checks": checks,
    }


def build_crag_demo_authorization(
    *, series_path: str | Path, provider_smoke_paths: list[str | Path],
    root: str | Path = ".", review_mode: str = "multi_reviewer",
) -> dict:
    project_root = Path(root)
    if review_mode not in REVIEW_MODES:
        raise ValueError("review mode must be multi_reviewer or single_owner")
    if len(provider_smoke_paths) != 3:
        raise ValueError("CRAG controlled demo requires exactly three provider smokes")
    series_reference = build_json_reference(
        series_path, root=project_root, expected_schema=SERIES_SCHEMA,
    )
    smoke_references = [
        build_json_reference(
            path, root=project_root, expected_schema=SMOKE_SCHEMA,
        )
        for path in provider_smoke_paths
    ]
    series = load_json_reference(series_reference, root=project_root) or {}
    artifact = {
        "schema": SCHEMA,
        "source_commit": str(series.get("source_commit") or ""),
        "review_governance": {"mode": review_mode},
        "series": series_reference,
        "provider_smokes": smoke_references,
        "source_artifacts": [series_reference, *smoke_references],
    }
    checks = _checks(artifact, root=project_root)
    passed = all(checks.values())
    decision = (
        "accepted" if passed
        else "inconclusive" if _provider_blocked(artifact, root=project_root)
        else "rejected"
    )
    artifact.update({
        "checks": checks,
        "passed": passed,
        "controlled_demo_eligible": passed,
        "decision": decision,
    })
    return artifact
