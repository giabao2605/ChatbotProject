"""Fail-closed control-plane contracts for the CRAG production pilot."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from threading import Lock
from typing import Any

from mech_chatbot.evaluation.metrics import nearest_rank
from mech_chatbot.evaluation.outcomes import VALID_OUTCOMES


ASSIGNMENT_VERSION = "hmac-sha256-v1"
SAMPLING_VERSION = "sha256-25pct-v1"
_REPLAY_NONCES: dict[str, int] = {}
_REPLAY_NONCE_LOCK = Lock()
FORBIDDEN_RAW_FIELDS = {
    "api_key",
    "authorization",
    "credential",
    "password",
    "prompt",
    "question",
    "raw_prompt",
    "secret",
    "token",
    "user_id",
    "username",
}
PILOT_ENV_FIELDS = {
    "experiment_id": "CRAG_PILOT_EXPERIMENT_ID",
    "assignment_salt": "CRAG_PILOT_ASSIGNMENT_SALT",
    "eligible_department": "CRAG_PILOT_DEPARTMENT",
    "cohort_sha256": "CRAG_PILOT_COHORT_SHA256",
    "control_url": "CRAG_PILOT_CONTROL_URL",
    "candidate_url": "CRAG_PILOT_CANDIDATE_URL",
    "control_deployment_id": "CRAG_PILOT_CONTROL_DEPLOYMENT_ID",
    "candidate_deployment_id": "CRAG_PILOT_CANDIDATE_DEPLOYMENT_ID",
    "snapshot_fingerprint": "CRAG_PILOT_SNAPSHOT_FINGERPRINT",
}


def _digest(salt: str, value: str) -> str:
    return hmac.new(
        salt.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()


@dataclass(frozen=True)
class PilotConfig:
    experiment_id: str
    assignment_salt: str
    eligible_department: str
    cohort_sha256: str
    control_url: str
    candidate_url: str
    control_deployment_id: str
    candidate_deployment_id: str
    snapshot_fingerprint: str

    def __post_init__(self):
        for field, value in self.__dict__.items():
            if not str(value or "").strip():
                raise ValueError(f"pilot config requires {field}")
        if self.control_url.rstrip("/") == self.candidate_url.rstrip("/"):
            raise ValueError("control and candidate deployments must be isolated")


@dataclass(frozen=True)
class PilotRoute:
    experiment_id: str
    assignment_version: str
    arm: str
    eligible: bool
    deployment_url: str
    deployment_id: str
    opposite_url: str
    opposite_deployment_id: str
    actor_hash: str
    matched_pair_id: str
    snapshot_fingerprint: str
    cohort_sha256: str
    replay_signing_key: str = field(repr=False)


@dataclass(frozen=True)
class ReplayRequest:
    target_arm: str
    target_url: str
    target_deployment_id: str
    payload: dict[str, Any]
    headers: dict[str, str]
    metadata: dict[str, Any]
    signing_key: str = field(repr=False)


def load_pilot_config(environ: dict[str, str] | None = None) -> PilotConfig | None:
    """Load the optional pilot contract; enabled but incomplete fails closed."""
    env = os.environ if environ is None else environ
    enabled = str(env.get("CRAG_PILOT_ENABLED", "false")).strip().casefold() in {
        "1", "true", "yes", "on"
    }
    if not enabled:
        return None
    values = {}
    for field, name in PILOT_ENV_FIELDS.items():
        value = str(env.get(name) or "").strip()
        if not value:
            raise ValueError(f"{name} is required when CRAG_PILOT_ENABLED=true")
        values[field] = value
    return PilotConfig(**values)


def validate_deployment_contract(
    config: dict[str, Any],
    control_health: dict[str, Any],
    candidate_health: dict[str, Any],
) -> dict:
    """Verify process isolation and pinned serving state before pilot traffic."""
    deployments = config.get("deployments") or {}
    expected_git = config.get("git_sha")
    expected_snapshot = config.get("snapshot_fingerprint")
    control_flags = control_health.get("feature_flags") or {}
    candidate_flags = candidate_health.get("feature_flags") or {}
    checks = {
        "both_healthy": control_health.get("status") == "ok"
        and candidate_health.get("status") == "ok",
        "deployments_isolated": bool(control_health.get("deployment_id"))
        and bool(candidate_health.get("deployment_id"))
        and control_health.get("deployment_id") != candidate_health.get("deployment_id"),
        "deployment_ids_pinned": control_health.get("deployment_id")
        == (deployments.get("control") or {}).get("id")
        and candidate_health.get("deployment_id")
        == (deployments.get("candidate") or {}).get("id"),
        "commit_pinned": bool(expected_git)
        and control_health.get("git_sha") == expected_git
        and candidate_health.get("git_sha") == expected_git,
        "snapshot_pinned": bool(expected_snapshot)
        and control_health.get("snapshot_fingerprint") == expected_snapshot
        and candidate_health.get("snapshot_fingerprint") == expected_snapshot,
        "control_flags_disabled": control_flags.get("RAG_CRAG_ENABLED") is False
        and control_flags.get("RAG_CLAIM_REPAIR_ENABLED") is False,
        "candidate_flags_enabled": candidate_flags.get("RAG_CRAG_ENABLED") is True
        and candidate_flags.get("RAG_CLAIM_REPAIR_ENABLED") is True,
    }
    return {
        "schema": "crag-pilot-deployment-preflight-v1",
        "passed": all(checks.values()),
        "checks": checks,
        "git_sha": expected_git,
        "snapshot_fingerprint": expected_snapshot,
        "deployments": {
            "control": {
                "id": control_health.get("deployment_id"),
                "feature_flags": control_flags,
            },
            "candidate": {
                "id": candidate_health.get("deployment_id"),
                "feature_flags": candidate_flags,
            },
        },
    }


def assign_pilot_route(
    config: PilotConfig,
    *,
    user_id: str,
    department: str,
    request_id: str,
) -> PilotRoute:
    """Assign one identity to a stable arm without inspecting query content."""
    actor = str(user_id or "").strip()
    request = str(request_id or "").strip()
    if not actor or not request:
        raise ValueError("user_id and request_id are required for pilot assignment")
    actor_hash = _digest(
        config.assignment_salt, f"{config.experiment_id}|actor|{actor}"
    )
    eligible = str(department or "").strip().casefold() == str(
        config.eligible_department
    ).strip().casefold()
    bucket = int(
        _digest(
            config.assignment_salt,
            f"{config.experiment_id}|assignment|{actor}",
        )[:16],
        16,
    )
    arm = "candidate" if eligible and bucket % 2 else "control"
    if arm == "candidate":
        deployment_url = config.candidate_url
        deployment_id = config.candidate_deployment_id
        opposite_url = config.control_url
        opposite_id = config.control_deployment_id
    else:
        deployment_url = config.control_url
        deployment_id = config.control_deployment_id
        opposite_url = config.candidate_url
        opposite_id = config.candidate_deployment_id
    matched_pair_id = _digest(
        config.assignment_salt,
        f"{config.experiment_id}|pair|{actor}|{request}",
    )[:32]
    replay_signing_key = _digest(
        config.assignment_salt,
        f"{config.experiment_id}|replay-key|{matched_pair_id}",
    )
    return PilotRoute(
        experiment_id=config.experiment_id,
        assignment_version=ASSIGNMENT_VERSION,
        arm=arm,
        eligible=eligible,
        deployment_url=deployment_url.rstrip("/"),
        deployment_id=deployment_id,
        opposite_url=opposite_url.rstrip("/"),
        opposite_deployment_id=opposite_id,
        actor_hash=actor_hash,
        matched_pair_id=matched_pair_id,
        snapshot_fingerprint=config.snapshot_fingerprint,
        cohort_sha256=config.cohort_sha256,
        replay_signing_key=replay_signing_key,
    )


def build_replay_request(
    route: PilotRoute,
    payload: dict[str, Any],
    *,
    original_trace_id: str,
) -> ReplayRequest:
    """Build a metadata-marked replay for the other isolated deployment."""
    if not route.eligible:
        raise ValueError("ineligible requests must not be replayed")
    trace_id = str(original_trace_id or "").strip()
    if not trace_id:
        raise ValueError("original_trace_id is required")
    target_arm = "control" if route.arm == "candidate" else "candidate"
    payload_sha256 = canonical_payload_sha256(payload)
    nonce = secrets.token_hex(16)
    expires_at = int(time.time()) + 300
    signature = _digest(
        route.replay_signing_key,
        _replay_signature_message(
            route.experiment_id,
            route.matched_pair_id,
            route.arm,
            route.opposite_deployment_id,
            trace_id,
            payload_sha256,
            nonce,
            expires_at,
        ),
    )
    headers = {
        "X-RAG-Pilot-Replay": "true",
        "X-RAG-Pilot-Experiment-ID": route.experiment_id,
        "X-RAG-Matched-Pair-ID": route.matched_pair_id,
        "X-RAG-Original-Trace-ID": trace_id,
        "X-RAG-Assigned-Arm": route.arm,
        "X-RAG-Pilot-Payload-SHA256": payload_sha256,
        "X-RAG-Pilot-Replay-Nonce": nonce,
        "X-RAG-Pilot-Replay-Expires": str(expires_at),
        "X-RAG-Pilot-Replay-Signature": signature,
    }
    return ReplayRequest(
        target_arm=target_arm,
        target_url=route.opposite_url,
        target_deployment_id=route.opposite_deployment_id,
        payload=dict(payload),
        headers=headers,
        metadata={
            "experiment_id": route.experiment_id,
            "matched_pair_id": route.matched_pair_id,
            "assigned_arm": route.arm,
            "target_arm": target_arm,
            "target_deployment_id": route.opposite_deployment_id,
            "snapshot_fingerprint": route.snapshot_fingerprint,
        },
        signing_key=route.replay_signing_key,
    )


def refresh_replay_auth(replay: ReplayRequest) -> ReplayRequest:
    """Issue a fresh short-lived signature after a replay leaves the queue."""
    headers = dict(replay.headers)
    payload_sha256 = canonical_payload_sha256(replay.payload)
    nonce = secrets.token_hex(16)
    expires_at = int(time.time()) + 300
    headers.update({
        "X-RAG-Pilot-Payload-SHA256": payload_sha256,
        "X-RAG-Pilot-Replay-Nonce": nonce,
        "X-RAG-Pilot-Replay-Expires": str(expires_at),
        "X-RAG-Pilot-Replay-Signature": _digest(
            replay.signing_key,
            _replay_signature_message(
                replay.metadata["experiment_id"],
                replay.metadata["matched_pair_id"],
                replay.metadata["assigned_arm"],
                replay.target_deployment_id,
                headers["X-RAG-Original-Trace-ID"],
                payload_sha256,
                nonce,
                expires_at,
            ),
        ),
    })
    return replace(replay, headers=headers)


def canonical_payload_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _replay_signature_message(
    experiment_id, matched_pair_id, assigned_arm, target_deployment_id,
    original_trace_id, payload_sha256, nonce, expires_at,
) -> str:
    return "|".join(str(value) for value in (
        experiment_id, matched_pair_id, assigned_arm, target_deployment_id,
        original_trace_id, payload_sha256, nonce, expires_at,
    ))


def verify_replay_signature(
    *,
    assignment_salt: str,
    experiment_id: str,
    matched_pair_id: str,
    assigned_arm: str,
    target_deployment_id: str,
    original_trace_id: str,
    payload_sha256: str,
    nonce: str,
    expires_at: int,
    signature: str,
    now: int | None = None,
) -> bool:
    current = int(time.time()) if now is None else int(now)
    if not (
        len(str(payload_sha256)) == 64
        and len(str(nonce)) == 32
        and current <= int(expires_at) <= current + 600
    ):
        return False
    replay_key = _digest(
        assignment_salt, f"{experiment_id}|replay-key|{matched_pair_id}"
    )
    expected = _digest(replay_key, _replay_signature_message(
        experiment_id, matched_pair_id, assigned_arm, target_deployment_id,
        original_trace_id, payload_sha256, nonce, expires_at,
    ))
    if not signature or not hmac.compare_digest(expected, signature):
        return False
    with _REPLAY_NONCE_LOCK:
        for key, expiry in list(_REPLAY_NONCES.items()):
            if expiry < current:
                _REPLAY_NONCES.pop(key, None)
        if nonce in _REPLAY_NONCES:
            return False
        _REPLAY_NONCES[nonce] = int(expires_at)
    return True


def should_sample_for_adjudication(
    experiment_id: str,
    matched_pair_id: str,
    outcome: dict[str, Any],
) -> bool:
    """Select all risk cases and a stable 25 percent target for normal answers."""
    risk_case = (
        bool(outcome.get("refusal"))
        or int(outcome.get("correction_count") or 0) > 0
        or int(outcome.get("repair_count") or 0) > 0
        or bool(outcome.get("access_denied"))
        or bool(outcome.get("provider_error"))
    )
    if risk_case:
        return True
    digest = hashlib.sha256(
        f"{experiment_id}|sample|{matched_pair_id}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < 0.25


def _parse_time(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _assert_no_raw_fields(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).strip().casefold() in FORBIDDEN_RAW_FIELDS:
                raise ValueError(f"forbidden raw field: {path}.{key}")
            _assert_no_raw_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_raw_fields(child, f"{path}[{index}]")


_CONFUSION_KEYS = (
    "correct_answer",
    "correct_refusal",
    "wrong_refusal",
    "wrong_refusal_type",
    "wrong_answer",
    "leakage",
    "admin_exception",
)


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a non-negative integer")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        raise ValueError(f"{name} must be a non-negative integer")
    return int(number)


def _nonnegative_float(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite and non-negative") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return number


def _normalized_confusion(row: dict[str, Any]) -> dict[str, int]:
    supplied = row.get("outcome_confusion")
    if supplied is not None and not isinstance(supplied, dict):
        raise ValueError("outcome_confusion must be an object")
    if isinstance(supplied, dict):
        confusion = {
            key: _nonnegative_int(supplied.get(key) or 0, key)
            for key in _CONFUSION_KEYS
        }
        scalar_fields = {
            "wrong_refusal": int(row.get("wrong_refusal") or 0),
            "wrong_refusal_type": int(row.get("wrong_refusal_type") or 0),
            "wrong_answer": int(row.get("wrong_answer") or 0),
            "leakage": int(row.get("leakage") or 0),
            "admin_exception": int(bool(row.get("admin_exception"))),
            "correct_refusal": int(row.get("correct_refusal") or 0),
        }
        for key, value in scalar_fields.items():
            if key in row and value != confusion[key]:
                raise ValueError(f"conflicting outcome confusion for {key}")
        return confusion

    required = {"wrong_refusal", "wrong_answer", "leakage"}
    missing = sorted(required - set(row))
    if missing:
        raise ValueError(f"missing outcome metric: {', '.join(missing)}")
    confusion = {
        "correct_answer": 0,
        "correct_refusal": _nonnegative_int(row.get("correct_refusal") or 0, "correct_refusal"),
        "wrong_refusal": _nonnegative_int(row.get("wrong_refusal") or 0, "wrong_refusal"),
        "wrong_refusal_type": _nonnegative_int(row.get("wrong_refusal_type") or 0, "wrong_refusal_type"),
        "wrong_answer": _nonnegative_int(row.get("wrong_answer") or 0, "wrong_answer"),
        "leakage": _nonnegative_int(row.get("leakage") or 0, "leakage"),
        "admin_exception": int(bool(row.get("admin_exception"))),
    }
    if not any(confusion.values()):
        confusion["correct_answer"] = 1
    return confusion


def _arm_metrics(pairs: list[dict], arm: str) -> dict:
    rows = [pair.get(arm) or {} for pair in pairs]
    required_metrics = {
        "latency_ms",
        "estimated_cost",
        "provider_retries",
        "fallback_count",
        "correction_count",
        "repair_count",
        "claim_valid",
        "claim_total",
        "citation_valid",
        "citation_total",
    }
    for index, row in enumerate(rows):
        missing = sorted(required_metrics - set(row))
        if missing:
            raise ValueError(
                f"{arm} row {index} missing metrics: {', '.join(missing)}"
            )
        _nonnegative_float(row["latency_ms"], "latency_ms")
        _nonnegative_float(row["estimated_cost"], "estimated_cost")
        for name in required_metrics - {"latency_ms", "estimated_cost"}:
            _nonnegative_int(row[name], name)
        if _nonnegative_int(row["claim_valid"], "claim_valid") > _nonnegative_int(
            row["claim_total"], "claim_total"
        ):
            raise ValueError("claim_valid cannot exceed claim_total")
        if _nonnegative_int(
            row["citation_valid"], "citation_valid"
        ) > _nonnegative_int(row["citation_total"], "citation_total"):
            raise ValueError("citation_valid cannot exceed citation_total")
    normalized = [_normalized_confusion(row) for row in rows]
    latencies = [float(row.get("latency_ms") or 0.0) for row in rows]
    claim_valid = sum(int(row.get("claim_valid") or 0) for row in rows)
    claim_total = sum(int(row.get("claim_total") or 0) for row in rows)
    citation_valid = sum(int(row.get("citation_valid") or 0) for row in rows)
    citation_total = sum(int(row.get("citation_total") or 0) for row in rows)
    outcome_confusion = {
        key: sum(item[key] for item in normalized) for key in _CONFUSION_KEYS
    }
    leakage = sum(
        item["leakage"] for item in normalized if not item["admin_exception"]
    )
    provider_retries = sum(int(row.get("provider_retries") or 0) for row in rows)
    return {
        "queries": len(rows),
        "wrong_refusal": outcome_confusion["wrong_refusal"],
        "wrong_answer": outcome_confusion["wrong_answer"],
        "leakage": leakage,
        "admin_exception_count": sum(
            item["admin_exception"] for item in normalized
        ),
        "latency_p50_ms": nearest_rank(latencies, 0.50),
        "latency_p95_ms": nearest_rank(latencies, 0.95),
        "estimated_cost": sum(float(row.get("estimated_cost") or 0.0) for row in rows),
        "provider_retries": provider_retries,
        "provider_retry_rate": provider_retries / len(rows) if rows else 0.0,
        "fallback_count": sum(int(row.get("fallback_count") or 0) for row in rows),
        "fallback_rate": (
            sum(int(row.get("fallback_count") or 0) for row in rows) / len(rows)
            if rows
            else 0.0
        ),
        "max_corrections_per_query": max(
            (int(row.get("correction_count") or 0) for row in rows), default=0
        ),
        "max_repairs_per_query": max(
            (int(row.get("repair_count") or 0) for row in rows), default=0
        ),
        "claim_precision": claim_valid / claim_total if claim_total else None,
        "claim_numerator": claim_valid,
        "claim_denominator": claim_total,
        "citation_accuracy": (
            citation_valid / citation_total if citation_total else None
        ),
        "citation_numerator": citation_valid,
        "citation_denominator": citation_total,
        "outcome_confusion": outcome_confusion,
    }


def _abort_report(pairs: list[dict], windows: list[dict]) -> dict:
    reasons: list[str] = []
    if any(
        _normalized_confusion(pair.get("candidate") or {})["leakage"] > 0
        and not _normalized_confusion(pair.get("candidate") or {})[
            "admin_exception"
        ]
        for pair in pairs
    ):
        reasons.append("leakage")
    if any(
        bool((pair.get("candidate") or {}).get("severe_wrong_answer"))
        for pair in pairs
    ):
        reasons.append("severe_wrong_answer")
    if any(
        int((pair.get("candidate") or {}).get("correction_count") or 0) > 1
        or int((pair.get("candidate") or {}).get("repair_count") or 0) > 1
        for pair in pairs
    ):
        reasons.append("correction_or_repair_budget")

    valid_windows = []
    voyage_breach = False
    for window in sorted(windows, key=lambda item: _parse_time(item["start_at"])):
        start = _parse_time(window["start_at"])
        end = _parse_time(window["end_at"])
        kind = str(window.get("kind") or "performance")
        valid = kind == "performance" and int(
            window.get("eligible_queries") or 0
        ) == 50 and (
            end - start
        ).total_seconds() >= 1800
        latency_breach = float(window.get("candidate_p95_ms") or 0) > float(
            window.get("control_p95_ms") or 0
        ) * 1.25
        cost_breach = float(window.get("candidate_cost") or 0) > float(
            window.get("control_cost") or 0
        ) * 1.5
        calls = int(window.get("voyage_calls") or 0)
        errors = int(window.get("voyage_errors") or 0)
        if kind == "voyage" and calls == 50 and errors / calls > 0.05:
            voyage_breach = True
        if kind == "performance":
            valid_windows.append({
                "start": start,
                "end": end,
                "valid": valid,
                "performance_breach": latency_breach or cost_breach,
            })
    for first, second in zip(valid_windows, valid_windows[1:]):
        if (
            first["valid"]
            and second["valid"]
            and first["end"] <= second["start"]
            and first["performance_breach"]
            and second["performance_breach"]
        ):
            reasons.append("two_consecutive_latency_or_cost_windows")
            break
    if voyage_breach:
        reasons.append("voyage_rerank_error_rate")
    return {"triggered": bool(reasons), "reasons": reasons}


def _balance_report(assignments: list[dict]) -> dict:
    report = {"roles": {}, "sites": {}, "query_type": {}}
    for assignment in assignments:
        arm = assignment.get("assigned_arm")
        if arm not in {"control", "candidate"}:
            continue
        dimensions = {
            "roles": assignment.get("roles") or ["unknown"],
            "sites": assignment.get("sites") or ["unknown"],
            "query_type": [assignment.get("query_type") or "unknown"],
        }
        for dimension, values in dimensions.items():
            for raw in values:
                value = str(raw or "unknown")[:100]
                counts = report[dimension].setdefault(
                    value, {"control": 0, "candidate": 0}
                )
                counts[arm] += 1
    return report


def _valid_adjudication(pair: dict[str, Any]) -> bool:
    record = pair.get("adjudication") or {}
    reviewer_ids = [str(value).strip() for value in (record.get("reviewer_ids") or [])]
    reasons = [str(value).strip() for value in (record.get("reason_codes") or [])]
    disagreement = record.get("disagreement")
    if (
        record.get("schema") != "evaluation-adjudication-v1"
        or record.get("case_id") != pair.get("matched_pair_id")
        or len(reviewer_ids) != len(set(reviewer_ids))
        or not all(reviewer_ids)
        or len(reasons) != len(reviewer_ids)
        or not all(value.strip() for value in reasons)
        or record.get("outcome_label") not in VALID_OUTCOMES
        or not isinstance(record.get("answer_correct"), bool)
        or not isinstance(record.get("citation_correct"), bool)
        or not isinstance(disagreement, bool)
    ):
        return False
    if disagreement:
        return len(reviewer_ids) == 3 and record.get("resolved_by") == reviewer_ids[2]
    return len(reviewer_ids) == 2 and record.get("resolved_by") == "consensus"


def _sampling_report(assignments: list[dict[str, Any]]) -> dict[str, Any]:
    daily: dict[str, dict[str, int]] = {}
    for assignment in assignments:
        timestamp = _parse_time(assignment.get("timestamp"))
        key = timestamp.date().isoformat()
        row = daily.setdefault(
            key,
            {
                "risk_eligible": 0,
                "risk_sampled": 0,
                "normal_eligible": 0,
                "normal_sampled": 0,
            },
        )
        risk = (
            bool(assignment.get("refusal"))
            or bool(assignment.get("access_denied"))
            or bool(assignment.get("provider_error"))
            or int(assignment.get("correction_count") or 0) > 0
            or int(assignment.get("repair_count") or 0) > 0
        )
        sampled = assignment.get("sampled_for_adjudication") is True
        prefix = "risk" if risk else "normal"
        row[f"{prefix}_eligible"] += 1
        if sampled:
            row[f"{prefix}_sampled"] += 1
    complete = bool(daily) and all(
        row["risk_sampled"] == row["risk_eligible"]
        and (
            row["normal_eligible"] == 0
            or row["normal_sampled"] / row["normal_eligible"] >= 0.20
        )
        for row in daily.values()
    )
    return {"daily": daily, "complete": complete}


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text.lower())


def build_pilot_artifact(
    config: dict[str, Any],
    matched_pairs: list[dict[str, Any]],
    *,
    assignment_events: list[dict[str, Any]],
    monitoring_windows: list[dict[str, Any]],
) -> dict:
    """Build the only artifact allowed to decide the CRAG production pilot."""
    _assert_no_raw_fields(config)
    _assert_no_raw_fields(matched_pairs)
    _assert_no_raw_fields(assignment_events)
    _assert_no_raw_fields(monitoring_windows)
    pairs = list(matched_pairs)
    assignments = list(assignment_events)
    pair_ids = [str(pair.get("matched_pair_id") or "") for pair in pairs]
    start = _parse_time(config["start_at"])
    end = _parse_time(config["end_at"])
    duration_days = (end - start).total_seconds() / 86400
    expected_cohort = str((config.get("eligible_cohort") or {}).get("sha256") or "")
    control = _arm_metrics(pairs, "control")
    candidate = _arm_metrics(pairs, "candidate")
    abort = _abort_report(pairs, monitoring_windows)
    owners = config.get("owners") or {}
    signoffs = config.get("reviewer_signoff") or {}
    deployments = config.get("deployments") or {}
    control_flags = (deployments.get("control") or {}).get("flags") or {}
    candidate_flags = (deployments.get("candidate") or {}).get("flags") or {}
    deployment_preflight = config.get("deployment_preflight") or {}
    preflight_deployments = deployment_preflight.get("deployments") or {}
    assigned_counts = {
        arm: sum(event.get("assigned_arm") == arm for event in assignments)
        for arm in ("control", "candidate")
    }
    sampling = _sampling_report(assignments)
    sampled_assignment_ids = {
        str(event.get("matched_pair_id") or "")
        for event in assignments
        if event.get("sampled_for_adjudication") is True
    }
    pair_times = [
        _parse_time(pair.get("timestamp"))
        for pair in pairs
        if pair.get("timestamp")
    ]
    assignment_times = [
        _parse_time(event.get("timestamp"))
        for event in assignments
        if event.get("timestamp")
    ]
    trace_snapshot = config.get("trace_snapshot") or {}
    trace_filters = trace_snapshot.get("filters") or {}
    trace_source = trace_snapshot.get("source") or {}
    try:
        coverage_start = _parse_time(trace_filters.get("start"))
        coverage_end = _parse_time(trace_filters.get("end"))
        coverage_valid = coverage_start == start and coverage_end == end
    except (TypeError, ValueError):
        coverage_valid = False
    performance_windows = [
        window for window in monitoring_windows if window.get("kind") == "performance"
    ]
    voyage_windows = [
        window for window in monitoring_windows if window.get("kind") == "voyage"
    ]
    for window in performance_windows:
        _nonnegative_int(window.get("eligible_queries"), "eligible_queries")
        for name in (
            "control_p95_ms",
            "candidate_p95_ms",
            "control_cost",
            "candidate_cost",
        ):
            _nonnegative_float(window.get(name), name)
    for window in voyage_windows:
        calls = _nonnegative_int(window.get("voyage_calls"), "voyage_calls")
        errors = _nonnegative_int(window.get("voyage_errors"), "voyage_errors")
        if errors > calls:
            raise ValueError("voyage_errors cannot exceed voyage_calls")
    windows_trace_bound = bool(monitoring_windows) and all(
        _is_sha256(window.get("trace_sha256"))
        and window.get("trace_sha256")
        == (config.get("source_artifacts") or {}).get("trace_sha256")
        for window in monitoring_windows
    )
    performance_windows_valid = bool(performance_windows) and all(
        int(window.get("eligible_queries") or 0) == 50
        and (
            _parse_time(window.get("end_at")) - _parse_time(window.get("start_at"))
        ).total_seconds()
        >= 1800
        for window in performance_windows
    )
    voyage_windows_valid = bool(voyage_windows) and all(
        int(window.get("voyage_calls") or 0) == 50 for window in voyage_windows
    )
    ordered_performance_windows = sorted(
        performance_windows, key=lambda window: _parse_time(window.get("start_at"))
    )
    performance_windows_non_overlapping = all(
        _parse_time(first.get("end_at")) <= _parse_time(second.get("start_at"))
        for first, second in zip(
            ordered_performance_windows, ordered_performance_windows[1:]
        )
    )
    performance_windows_cover_range = bool(ordered_performance_windows) and (
        _parse_time(ordered_performance_windows[0].get("start_at")) == start
        and _parse_time(ordered_performance_windows[-1].get("end_at")) == end
        and all(
            _parse_time(first.get("end_at"))
            == _parse_time(second.get("start_at"))
            for first, second in zip(
                ordered_performance_windows, ordered_performance_windows[1:]
            )
        )
    )
    monitoring_windows_in_range = bool(monitoring_windows) and all(
        start <= _parse_time(window.get("start_at"))
        <= _parse_time(window.get("end_at")) <= end
        for window in monitoring_windows
    )
    source_artifacts = config.get("source_artifacts") or {}
    sources_bound = all(
        _is_sha256(source_artifacts.get(key))
        for key in (
            "assignments_sha256",
            "pairs_sha256",
            "windows_sha256",
            "preflight_sha256",
            "trace_snapshot_sha256",
            "trace_sha256",
        )
    )
    trace_snapshot_valid = (
        trace_snapshot.get("schema") == "rag-refusal-snapshot-v1"
        and trace_source.get("git_sha") == config.get("git_sha")
        and trace_source.get("sha256") == source_artifacts.get("trace_sha256")
        and int(trace_snapshot.get("parse_errors") or 0) == 0
        and set(trace_filters.get("execution_contexts") or []) == {"production"}
        and int((trace_snapshot.get("system_metrics") or {}).get("query_count") or 0)
        == len(assignments)
        and sum(
            int(window.get("eligible_queries") or 0)
            for window in performance_windows
        ) == len(assignments)
        and int((trace_snapshot.get("voyage_rerank") or {}).get("call_count") or 0)
        == sum(int(window.get("voyage_calls") or 0) for window in voyage_windows)
    )
    assignment_by_id = {
        str(event.get("matched_pair_id") or ""): event for event in assignments
    }
    assignment_ids = [str(event.get("matched_pair_id") or "") for event in assignments]
    expected_department = str(
        (config.get("eligible_cohort") or {}).get("department") or ""
    ).strip().casefold()
    checks = {
        "time_range_valid": 0 <= duration_days <= 14,
        "minimum_seven_days": duration_days >= 7,
        "maximum_fourteen_days": duration_days <= 14,
        "minimum_matched_pairs": len(pairs) >= 100,
        "matched_pairs_unique": bool(pairs) and len(set(pair_ids)) == len(pair_ids)
        and all(pair_ids),
        "matched_pair_payload_complete": bool(pairs)
        and all(
            pair.get("assigned_arm") in {"control", "candidate"}
            and isinstance(pair.get("control"), dict)
            and isinstance(pair.get("candidate"), dict)
            for pair in pairs
        ),
        "pair_timestamps_in_window": len(pair_times) == len(pairs)
        and all(start <= timestamp <= end for timestamp in pair_times),
        "both_arms_observed": assigned_counts["control"] > 0
        and assigned_counts["candidate"] > 0,
        "all_pairs_adjudicated": bool(pairs)
        and all(_valid_adjudication(pair) for pair in pairs),
        "cohort_immutable": bool(expected_cohort)
        and bool(expected_department)
        and all(pair.get("cohort_sha256") == expected_cohort for pair in pairs)
        and all(
            event.get("cohort_sha256") == expected_cohort
            and str(event.get("department") or "").strip().casefold()
            == expected_department
            for event in assignments
        ),
        "assignment_events_unique": bool(assignments)
        and all(assignment_ids)
        and len(set(assignment_ids)) == len(assignment_ids),
        "pair_assignment_consistent": all(
            assignment_by_id.get(pair_id, {}).get("assigned_arm")
            == pair.get("assigned_arm")
            for pair_id, pair in zip(pair_ids, pairs)
        ),
        "owners_assigned": all(
            bool(owners.get(role)) for role in ("rag", "security_qa", "operations")
        ),
        "reviewer_signoff_complete": all(
            signoffs.get(role) is True
            for role in ("rag", "security_qa", "operations")
        ),
        "daily_sampling_complete": sampling["complete"],
        "sampled_pairs_complete": bool(sampled_assignment_ids)
        and sampled_assignment_ids == set(pair_ids),
        "assignment_timestamps_in_window": len(assignment_times) == len(assignments)
        and all(start <= timestamp <= end for timestamp in assignment_times),
        "monitoring_evidence_complete": coverage_valid
        and trace_snapshot_valid
        and performance_windows_valid
        and performance_windows_non_overlapping
        and performance_windows_cover_range
        and voyage_windows_valid
        and monitoring_windows_in_range
        and windows_trace_bound
        and sources_bound,
        "isolated_deployments": bool((deployments.get("control") or {}).get("id"))
        and bool((deployments.get("candidate") or {}).get("id"))
        and (deployments.get("control") or {}).get("id")
        != (deployments.get("candidate") or {}).get("id"),
        "feature_flags_correct": (
            control_flags.get("RAG_CRAG_ENABLED") is False
            and control_flags.get("RAG_CLAIM_REPAIR_ENABLED") is False
            and candidate_flags.get("RAG_CRAG_ENABLED") is True
            and candidate_flags.get("RAG_CLAIM_REPAIR_ENABLED") is True
        ),
        "deployment_preflight_passed": (
            deployment_preflight.get("schema")
            == "crag-pilot-deployment-preflight-v1"
            and deployment_preflight.get("passed") is True
            and deployment_preflight.get("git_sha") == config.get("git_sha")
            and deployment_preflight.get("snapshot_fingerprint")
            == config.get("snapshot_fingerprint")
            and (preflight_deployments.get("control") or {}).get("id")
            == (deployments.get("control") or {}).get("id")
            and (preflight_deployments.get("candidate") or {}).get("id")
            == (deployments.get("candidate") or {}).get("id")
            and (preflight_deployments.get("control") or {}).get("feature_flags")
            == control_flags
            and (preflight_deployments.get("candidate") or {}).get("feature_flags")
            == candidate_flags
        ),
        "assignment_version_pinned": config.get("assignment_version")
        == ASSIGNMENT_VERSION,
        "sampling_version_pinned": config.get("sampling_version")
        == SAMPLING_VERSION,
        "snapshot_pinned": bool(config.get("snapshot_fingerprint")),
        "voyage_policy_pinned": config.get("voyage_policy")
        == "immediate_local_fallback_no_retry",
        "wrong_refusal_reduced": (
            candidate["wrong_refusal"] < control["wrong_refusal"]
            if control["wrong_refusal"] > 0
            else candidate["wrong_refusal"] == 0
        ),
        "wrong_answer_not_increased": candidate["wrong_answer"]
        <= control["wrong_answer"],
        "leakage_zero": candidate["leakage"] == 0,
        "latency_within_budget": float(candidate["latency_p95_ms"] or 0)
        <= float(control["latency_p95_ms"] or 0) * 1.25,
        "cost_within_budget": candidate["estimated_cost"]
        <= control["estimated_cost"] * 1.5,
        "correction_budget": candidate["max_corrections_per_query"] <= 1,
        "repair_budget": candidate["max_repairs_per_query"] <= 1,
        "claim_precision_at_least_99": candidate["claim_precision"] is not None
        and candidate["claim_precision"] >= 0.99,
        "citation_accuracy_at_least_99": candidate["citation_accuracy"] is not None
        and candidate["citation_accuracy"] >= 0.99,
        "no_abort_condition": not abort["triggered"],
    }
    prerequisites = (
        checks["minimum_seven_days"]
        and checks["minimum_matched_pairs"]
        and checks["maximum_fourteen_days"]
    )
    live_evidence_complete = all(
        checks[name]
        for name in (
            "all_pairs_adjudicated",
            "daily_sampling_complete",
            "sampled_pairs_complete",
            "assignment_timestamps_in_window",
            "monitoring_evidence_complete",
        )
    )
    if abort["triggered"]:
        decision = "aborted"
    elif duration_days >= 14 and (
        not checks["minimum_matched_pairs"] or not live_evidence_complete
    ):
        decision = "inconclusive"
    elif not prerequisites or not live_evidence_complete:
        decision = "running"
    elif all(checks.values()):
        decision = "accepted"
    else:
        decision = "rejected"
    return {
        "schema": "crag-production-pilot-v1",
        "run_id": config.get("run_id"),
        "experiment_id": config.get("experiment_id"),
        "git_sha": config.get("git_sha"),
        "start_at": config.get("start_at"),
        "end_at": config.get("end_at"),
        "duration_days": duration_days,
        "assignment_version": config.get("assignment_version"),
        "sampling_version": config.get("sampling_version"),
        "snapshot_fingerprint": config.get("snapshot_fingerprint"),
        "eligible_cohort": config.get("eligible_cohort"),
        "deployments": deployments,
        "deployment_preflight": deployment_preflight,
        "owners": owners,
        "reviewer_signoff": signoffs,
        "voyage_policy": config.get("voyage_policy"),
        "matched_pair_count": len(pairs),
        "matched_pair_ids": pair_ids,
        "assigned_arm_counts": assigned_counts,
        "sampling_summary": sampling,
        "balance": _balance_report(assignments),
        "assignment_event_count": len(assignments),
        "source_artifacts": source_artifacts,
        "metrics": {"control": control, "candidate": candidate},
        "monitoring_window_count": len(monitoring_windows),
        "abort": abort,
        "checks": checks,
        "decision": decision,
        "passed": decision == "accepted",
    }
