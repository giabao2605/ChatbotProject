#!/usr/bin/env python
"""Run three fail-closed full-RAG Voyage/Jina evaluation pairs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mech_chatbot.composition.maintenance_runtime import (
    configured_repository_runtime,
)
from mech_chatbot.config.settings import load_settings
from mech_chatbot.evaluation.full_rag_provider import (
    ARM_ORDERS,
    build_provider_technical_authorization,
    compare_full_rag_provider_reports,
)
from mech_chatbot.evaluation.rerank_provider import canonical_sha256
from scripts.crag_eval.constants import FIXTURE_COLLECTION
from scripts.crag_eval.run_rollout import (
    _artifact_reference,
    _run,
    _sha,
    _utc_now,
    governance_scope_sha256,
    require_clean_worktree,
    require_source_commit,
)
from scripts.eval.provider_smoke import (
    provider_configuration_sha256_for_settings,
    provider_environment_for_settings,
    validate_provider_smoke_for_arms,
)
from scripts.eval.run_rerank_provider_ab import _real_provider_calls


LIVE_OPT_IN = "RUN_RERANK_PROVIDER_FULL_RAG_EVAL"
APPROVED_MANIFEST = (
    ROOT / "data" / "rerank_provider_full_rag_v1" / "eval_manifest.jsonl"
)
APPROVED_MANIFEST_SHA256 = (
    "5eb5f6228a656a0eb766c9aa5718c788c3b9dc79c1b4373494afa12b4e2d6aba"
)
APPROVAL_REF = (
    "codex-thread:019fab5f-2aa9-71d2-9bc1-0ecaf3b6d931"
)
WINDOW_ID = "full-rag-provider-019fab5f-v2"
DECLARATION_PATH = (
    ROOT
    / "reports"
    / "rerank-provider-full-rag"
    / f"{WINDOW_ID}-declaration.json"
)
PROVIDERS = {"baseline": "voyage", "candidate": "jina"}
MIN_VOYAGE_ARM_INTERVAL_SECONDS = 61.0
_SHA256 = re.compile(r"[0-9a-f]{64}")
_FIXED_ENVIRONMENT = {
    "RAG_EXECUTION_CONTEXT": "evaluation",
    "RAG_CRAG_ENABLED": "false",
    "RAG_CLAIM_REPAIR_ENABLED": "false",
    "RAG_GROUNDED_MATH_ENABLED": "false",
    "RAG_LATE_INTERACTION_ENABLED": "false",
    "RAG_QUERY_DECOMPOSITION_ENABLED": "false",
    "RAG_GRAPH_RETRIEVAL_ENABLED": "false",
    "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED": "false",
    "SEMANTIC_CACHE_ENABLED": "false",
    "STRICT_REALTIME_STREAMING": "false",
    "RAG_EVAL_ROUTER_MODE": "offline",
    "LLM_ROUTER_ENABLED": "false",
    "SEMANTIC_ROUTER_ENABLED": "false",
    "QDRANT_COLLECTION": FIXTURE_COLLECTION,
}


def _git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
    ).strip()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"artifact must be an object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _declare_window(value: Mapping[str, Any]) -> None:
    DECLARATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    try:
        with DECLARATION_PATH.open("x", encoding="utf-8") as stream:
            stream.write(payload)
    except FileExistsError as exc:
        raise RuntimeError(
            f"window {WINDOW_ID} is already declared; rerun is forbidden"
        ) from exc


def require_approved_manifest(path: Path) -> None:
    if not path.is_file():
        raise ValueError("full-RAG provider window requires the approved manifest")
    with path.open("r", encoding="utf-8", newline=None) as stream:
        canonical_sha256 = hashlib.sha256(
            stream.read().encode("utf-8")
        ).hexdigest()
    if canonical_sha256 != APPROVED_MANIFEST_SHA256:
        raise ValueError("full-RAG provider window requires the approved manifest")


def _validate_profiles(
    configurations: Mapping[str, Mapping[str, Any]],
) -> None:
    for provider in ("voyage", "jina"):
        profile = configurations.get(provider)
        if not isinstance(profile, Mapping):
            raise ValueError(f"missing {provider} profile configuration")
        if profile.get("provider") != provider:
            raise ValueError(f"{provider} profile identity mismatch")
        if not _SHA256.fullmatch(str(profile.get("profile_sha256") or "")):
            raise ValueError(f"{provider} profile hash is invalid")
        if not _SHA256.fullmatch(
            str(profile.get("generation_provider_sha256") or "")
        ):
            raise ValueError("generation provider hash is invalid")
        if not isinstance(profile.get("environment"), Mapping):
            raise ValueError(f"{provider} profile environment is missing")


def load_profile_configurations() -> dict[str, dict[str, Any]]:
    """Resolve managed profiles without calling either rerank API."""
    settings = load_settings(ROOT / ".env").model_copy(
        update={"RAG_EXECUTION_CONTEXT": "evaluation"},
    )
    with configured_repository_runtime(settings, include_qdrant=False):
        _, metadata = _real_provider_calls(settings)
    common = provider_environment_for_settings(settings)
    generation_sha256 = provider_configuration_sha256_for_settings(settings)
    return {
        "voyage": {
            **metadata["voyage"],
            "generation_provider_sha256": generation_sha256,
            "environment": {
                **common,
                "VOYAGE_API_KEY": str(settings.VOYAGE_API_KEY or ""),
                "VOYAGE_RERANK_MODEL": settings.VOYAGE_RERANK_MODEL,
                "VOYAGE_RERANK_TIMEOUT_SECONDS": str(
                    settings.VOYAGE_RERANK_TIMEOUT_SECONDS
                ),
                "USE_VOYAGE_RERANK": "true",
            },
        },
        "jina": {
            **metadata["jina"],
            "generation_provider_sha256": generation_sha256,
            "environment": {
                **common,
                "JINA_API_KEY": str(settings.JINA_API_KEY or ""),
                "JINA_RERANK_MODEL": settings.JINA_RERANK_MODEL,
                "JINA_RERANK_TIMEOUT_SECONDS": str(
                    settings.JINA_RERANK_TIMEOUT_SECONDS
                ),
            },
        },
    }


def _configuration_sha256(
    provider: str,
    profile: Mapping[str, Any],
) -> str:
    return canonical_sha256(
        {
            "generation": profile["generation_provider_sha256"],
            "rerank_profile": profile["profile_sha256"],
            "rerank_provider": provider,
            "rerank_model": profile["model"],
        }
    )


def _arm_environment(
    provider: str,
    profile: Mapping[str, Any],
) -> dict[str, str]:
    environment = {
        str(key): str(value)
        for key, value in dict(profile["environment"]).items()
    }
    environment.update(_FIXED_ENVIRONMENT)
    environment["RERANK_PROVIDER"] = provider
    return environment


def _arm_reference(
    pair_dir: Path,
    label: str,
    provider: str,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "provider": provider,
        **_artifact_reference(pair_dir / label / "eval.json"),
        **_artifact_reference(
            pair_dir / label / "trace.json",
            prefix="trace",
        ),
        "started_at": result.get("started_at"),
        "completed_at": result.get("completed_at"),
        "runner_exit": result.get("runner_exit"),
    }


def run_window(
    manifest: Path,
    output: Path,
    *,
    approval_ref: str,
    provider_smoke_artifact: Path,
    profile_configurations: Mapping[str, Mapping[str, Any]] | None = None,
    arm_runner: Callable[..., Mapping[str, Any]] = _run,
    smoke_validator: Callable[..., Mapping[str, Any]] = (
        validate_provider_smoke_for_arms
    ),
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    utc_now: Callable[[], str] = _utc_now,
) -> dict[str, Any]:
    if os.getenv(LIVE_OPT_IN) != "1":
        raise RuntimeError(f"set {LIVE_OPT_IN}=1 before running live evaluation")
    if approval_ref != APPROVAL_REF:
        raise ValueError(f"--approval-ref must be {APPROVAL_REF}")
    require_approved_manifest(manifest)
    if not provider_smoke_artifact.is_file():
        raise ValueError("provider smoke artifact must exist")
    if output.exists() and (
        not output.is_dir() or any(output.iterdir())
    ):
        raise ValueError(f"refusing to overwrite non-empty output: {output}")
    require_clean_worktree()
    if DECLARATION_PATH.exists():
        raise RuntimeError(
            f"window {WINDOW_ID} is already declared; rerun is forbidden"
        )
    configurations = (
        dict(profile_configurations)
        if profile_configurations is not None
        else load_profile_configurations()
    )
    _validate_profiles(configurations)
    output.mkdir(parents=True, exist_ok=True)
    source_commit = _git_sha()
    manifest_sha256 = _sha(manifest)
    smoke_sha256 = _sha(provider_smoke_artifact)
    governance_sha256 = governance_scope_sha256(manifest)
    pairs: list[dict[str, Any]] = []
    snapshot_fingerprint: str | None = None
    last_voyage_completed: float | None = None
    arm_starts: list[str] = []
    generation_sha256 = str(
        configurations["voyage"]["generation_provider_sha256"]
    )
    declaration = {
        "schema": "rerank-provider-full-rag-window-declaration-v1",
        "window_id": WINDOW_ID,
        "source_commit": source_commit,
        "manifest_sha256": manifest_sha256,
        "provider_smoke_sha256": smoke_sha256,
        "approval_ref": approval_ref,
        "output_path": str(output.resolve()),
        "status": "declared",
        "production_authorized": False,
        "release_authorized": False,
    }
    _declare_window(declaration)

    for pair_index, arm_order in enumerate(ARM_ORDERS, 1):
        pair_dir = output / f"pair-{pair_index:02d}"
        pair_dir.mkdir()
        ordered_labels = (
            ("baseline", "candidate")
            if arm_order == "baseline-first"
            else ("candidate", "baseline")
        )
        results: dict[str, Mapping[str, Any]] = {}
        for label in ordered_labels:
            provider = PROVIDERS[label]
            if provider == "voyage" and last_voyage_completed is not None:
                elapsed = monotonic() - last_voyage_completed
                remaining = MIN_VOYAGE_ARM_INTERVAL_SECONDS - elapsed
                if remaining > 0:
                    sleeper(remaining)
            trace_path = pair_dir / f"{label}-trace.jsonl"
            trace_path.touch()
            started_at = utc_now()
            arm_starts.append(started_at)
            smoke_validator(
                provider_smoke_artifact,
                expected_provider_sha256=generation_sha256,
                arm_started_at=tuple(arm_starts),
            )
            profile = configurations[provider]
            results[label] = arm_runner(
                label,
                manifest,
                pair_dir,
                trace_path,
                enabled=False,
                router_mode="offline",
                provider_configuration_sha256=str(
                    _configuration_sha256(provider, profile)
                ),
                governance_scope_sha256_value=governance_sha256,
                provider_environment=_arm_environment(provider, profile),
                started_at=started_at,
            )
            if int(results[label].get("runner_exit") or 0) != 0:
                raise RuntimeError(f"{label} arm returned non-zero exit")
            if provider == "voyage":
                last_voyage_completed = monotonic()
            require_clean_worktree()
            require_source_commit(source_commit)
            if _sha(manifest) != manifest_sha256:
                raise RuntimeError(f"manifest changed after pair {pair_index} {label}")
            if _sha(provider_smoke_artifact) != smoke_sha256:
                raise RuntimeError("provider smoke artifact changed during window")

        baseline_eval = _read_json(pair_dir / "baseline" / "eval.json")
        candidate_eval = _read_json(pair_dir / "candidate" / "eval.json")
        baseline_trace = _read_json(pair_dir / "baseline" / "trace.json")
        candidate_trace = _read_json(pair_dir / "candidate" / "trace.json")
        arm_fingerprints = {
            str(
                _read_json(pair_dir / label / "preflight.json").get(
                    "fixture_fingerprint"
                )
                or ""
            )
            for label in ("baseline", "candidate")
        }
        if len(arm_fingerprints) != 1 or not next(iter(arm_fingerprints)):
            raise RuntimeError(f"fixture snapshot changed in pair {pair_index}")
        pair_fingerprint = next(iter(arm_fingerprints))
        if snapshot_fingerprint not in (None, pair_fingerprint):
            raise RuntimeError("fixture snapshot changed across pairs")
        snapshot_fingerprint = pair_fingerprint

        gate = compare_full_rag_provider_reports(
            baseline_eval,
            candidate_eval,
            baseline_trace,
            candidate_trace,
        )
        gate_path = pair_dir / "gate.json"
        _write_json(gate_path, gate)
        pair = {
            "schema": "rerank-provider-full-rag-pair-v1",
            "pair_index": pair_index,
            "arm_order": arm_order,
            "source_commit": source_commit,
            "manifest_sha256": manifest_sha256,
            "snapshot_fingerprint": pair_fingerprint,
            "profile_sha256s": {
                "baseline": configurations["voyage"]["profile_sha256"],
                "candidate": configurations["jina"]["profile_sha256"],
            },
            "baseline": _arm_reference(
                pair_dir,
                "baseline",
                "voyage",
                results["baseline"],
            ),
            "candidate": _arm_reference(
                pair_dir,
                "candidate",
                "jina",
                results["candidate"],
            ),
            "gate_artifact": _artifact_reference(gate_path),
            "gate": gate,
            "release_authorized": False,
        }
        _write_json(pair_dir / "pair.json", pair)
        pairs.append(pair)
        if not gate.get("passed"):
            break

    passed = len(pairs) == 3 and all(
        pair["gate"].get("passed") is True for pair in pairs
    )
    authorization_reference: dict[str, Any] | None = None
    if passed:
        authorization = build_provider_technical_authorization(
            pairs,
            approval_ref,
        )
        _write_json(output / "authorization.json", authorization)
        authorization_reference = _artifact_reference(
            output / "authorization.json"
        )
        passed = authorization.get("technical_authorized") is True
    report = {
        "schema": "rerank-provider-full-rag-window-v1",
        "source_commit": source_commit,
        "manifest_sha256": manifest_sha256,
        "snapshot_fingerprint": snapshot_fingerprint,
        "collection": FIXTURE_COLLECTION,
        "concurrency": 1,
        "arm_orders": list(ARM_ORDERS),
        "profile_sha256s": {
            provider: configurations[provider]["profile_sha256"]
            for provider in ("voyage", "jina")
        },
        "pair_count": len(pairs),
        "pairs": pairs,
        "passed": passed,
        "technical_authorized": passed,
        "release_authorized": False,
        "authorization_artifact": authorization_reference,
        "provider_smoke": _artifact_reference(provider_smoke_artifact),
    }
    declaration = {
        **declaration,
        "status": "accepted" if passed else "rejected",
    }
    _write_json(DECLARATION_PATH, declaration)
    report["window_declaration"] = _artifact_reference(DECLARATION_PATH)
    _write_json(output / "run.json", report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run three full-RAG Voyage/Jina pairs. "
            "Passing creates technical evidence only."
        )
    )
    parser.add_argument("--manifest", type=Path, default=APPROVED_MANIFEST)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--approval-ref", required=True)
    parser.add_argument("--provider-smoke-artifact", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_window(
        args.manifest,
        args.output_dir,
        approval_ref=args.approval_ref,
        provider_smoke_artifact=args.provider_smoke_artifact,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
