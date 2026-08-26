"""Fail-closed reconciliation for the Query Decomposition pilot WAL."""

from __future__ import annotations

from pathlib import Path

from scripts.ops.query_decomposition_pilot import (
    PILOT_CONTRACT_VERSION,
    PILOT_DURATION,
    PILOT_REQUEST_COUNT,
    _EVIDENCE_FIELDS,
    _authorization_and_schedule,
    _read_json,
    _sha256_digest,
    _timestamp,
    _wal_rows,
)


def _evidence_valid(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != _EVIDENCE_FIELDS:
        return False
    common = all((
        value.get("route") == "query_decomposition",
        value.get("security_passed") is True,
        value.get("leakage_detected") is False,
        value.get("subquery_count") in {2, 3},
        value.get("intent_count") == value.get("subquery_count"),
        value.get("intent_coverage_complete") is True,
        value.get("intent_overflow") is False,
        value.get("planner_calls") in {0, 1},
        value.get("correction_count") in {0, 1},
        value.get("provider_retries") == 0,
        type(value.get("final_latency_ms")) is int,
        0 <= value.get("final_latency_ms") <= value.get("request_deadline_ms"),
    ))
    answered = all((
        value.get("query_result_status") == "valid",
        value.get("completion_outcome") == "answered",
        value.get("refusal_reason_code") is None,
        value.get("refusal_template_passed") is False,
        value.get("owner_review_required") is False,
        value.get("citation_structure_passed") is True,
        value.get("provenance_passed") is True,
        value.get("final_generations") == 1,
    ))
    safe_refusal = all((
        value.get("query_result_status") == "safe_refusal",
        value.get("completion_outcome") == "refused",
        value.get("refusal_reason_code") == "evidence_gate",
        value.get("refusal_template_passed") is True,
        value.get("owner_review_required") is True,
        value.get("final_generations") == 0,
    ))
    return common and (answered or safe_refusal)


def _review_valid(
    path: str | Path | None, *, authorization: dict,
    schedule_sha: str, rows: list[dict],
) -> bool:
    if path is None:
        return False
    try:
        review, _, _ = _read_json(path)
    except (OSError, ValueError):
        return False
    reviewed = review.get("accepted_trace_sha256")
    actual = {row["trace_id_sha256"] for row in rows}
    required = {
        row["trace_id_sha256"] for row in rows
        if row.get("evidence", {}).get("owner_review_required") is True
    }
    return all((
        review.get("schema") == "query-decomposition-pilot-review-result-v1",
        review.get("source_commit") == authorization.get("source_commit"),
        review.get("schedule_sha256") == schedule_sha,
        review.get("reviewer") == authorization.get("actor"),
        isinstance(reviewed, list),
        len(set(reviewed or ())) >= 20,
        set(reviewed or ()) <= actual,
        required <= set(reviewed or ()),
        review.get("all_accepted") is True,
        not any(name in review for name in ("question", "answer", "content")),
    ))


def _failed_gate(reason: str) -> dict:
    checks = {
        "authorization_and_schedule_valid": False,
        "wal_parse_valid": False,
        "exactly_100_rows": False,
        "every_card_exactly_once": False,
        "trace_ids_unique": False,
        "runtime_identity_bound": False,
        "per_request_contract": False,
        "frozen_schedule_cadence": False,
        "minimum_24_hours": False,
        "no_retry_replacement_or_catch_up": False,
        "concurrency_one": False,
    }
    return {
        "schema": "query-decomposition-production-pilot-gate-v1",
        "source_commit": None,
        "activation_bundle_sha256": None,
        "pilot_contract_version": PILOT_CONTRACT_VERSION,
        "eligible_request_count": 0,
        "checks": checks,
        "automated_gate_passed": False,
        "human_review_passed": False,
        "pilot_accepted": False,
        "default_rollout_authorized": False,
        "reason": reason,
    }


def _row_contract_valid(
    rows: list[dict], cards: list[dict], *, auth_sha: str,
    schedule_sha: str, runtime_identity_sha256: str, expires_at: object,
) -> tuple[bool, bool]:
    card_by_id = {card.get("card_id"): card for card in cards}
    try:
        per_request = all(
            row.get("schema") == "query-decomposition-pilot-wal-v1"
            and row.get("authorization_sha256") == auth_sha
            and row.get("schedule_sha256") == schedule_sha
            and row.get("attempt_number") == 1
            and row.get("case_id")
            == card_by_id.get(row.get("card_id"), {}).get("case_id")
            and _timestamp(row.get("attempted_at"))
            >= _timestamp(card_by_id[row.get("card_id")]["scheduled_at"])
            and _timestamp(row.get("completed_at"))
            >= _timestamp(row.get("attempted_at"))
            and _timestamp(row.get("completed_at")) <= _timestamp(expires_at)
            and _sha256_digest(row.get("trace_id_sha256"))
            and row.get("runtime_identity_sha256") == runtime_identity_sha256
            and _evidence_valid(row.get("evidence"))
            for row in rows
        )
        cadence = len(rows) == len(cards) and all(
            row.get("card_id") == card.get("card_id")
            and (
                index == len(cards) - 1
                or _timestamp(row.get("attempted_at"))
                < _timestamp(cards[index + 1].get("scheduled_at"))
            )
            and (
                index == len(rows) - 1
                or _timestamp(row.get("completed_at"))
                <= _timestamp(rows[index + 1].get("attempted_at"))
            )
            for index, (row, card) in enumerate(zip(rows, cards, strict=True))
        )
    except (KeyError, TypeError, ValueError):
        return False, False
    return per_request, cadence


def build_pilot_gate(
    *, schedule_path: str | Path, authorization_path: str | Path,
    wal_path: str | Path, runtime_identity_sha256: str,
    review_result_path: str | Path | None = None,
) -> dict:
    """Reconcile the exact 100-request contract without authorizing rollout."""
    try:
        authorization, auth_sha, schedule, schedule_sha = (
            _authorization_and_schedule(
                Path(authorization_path).resolve(), Path(schedule_path).resolve(),
            )
        )
    except (OSError, ValueError):
        return _failed_gate("authorization_or_schedule_invalid")
    try:
        rows = _wal_rows(Path(wal_path).resolve())
        parse_valid = True
    except (OSError, ValueError):
        rows, parse_valid = [], False
    cards = schedule.get("cards")
    cards = cards if isinstance(cards, list) else []
    row_cards = [row.get("card_id") for row in rows]
    per_request, cadence = _row_contract_valid(
        rows, cards, auth_sha=auth_sha, schedule_sha=schedule_sha,
        runtime_identity_sha256=runtime_identity_sha256,
        expires_at=authorization.get("expires_at"),
    ) if parse_valid else (False, False)
    duration_valid = bool(
        len(rows) == PILOT_REQUEST_COUNT and per_request and cadence
        and _timestamp(rows[-1]["completed_at"])
        - _timestamp(rows[0]["attempted_at"]) >= PILOT_DURATION
    )
    checks = {
        "authorization_and_schedule_valid": True,
        "wal_parse_valid": parse_valid,
        "exactly_100_rows": len(rows) == PILOT_REQUEST_COUNT,
        "every_card_exactly_once": (
            len(row_cards) == len(set(row_cards)) == PILOT_REQUEST_COUNT
            and set(row_cards) == {card.get("card_id") for card in cards}
        ),
        "trace_ids_unique": (
            len({row.get("trace_id_sha256") for row in rows}) == len(rows)
        ),
        "runtime_identity_bound": _sha256_digest(runtime_identity_sha256),
        "per_request_contract": per_request,
        "frozen_schedule_cadence": cadence,
        "minimum_24_hours": duration_valid,
        "no_retry_replacement_or_catch_up": all(
            schedule.get(name) == "none"
            for name in ("retry_policy", "replacement_policy", "catch_up_policy")
        ),
        "concurrency_one": schedule.get("max_concurrency") == 1,
    }
    automated = all(checks.values())
    human_review = automated and _review_valid(
        review_result_path, authorization=authorization,
        schedule_sha=schedule_sha, rows=rows,
    )
    return {
        "schema": "query-decomposition-production-pilot-gate-v1",
        "source_commit": authorization.get("source_commit"),
        "activation_bundle_sha256": authorization.get(
            "activation_bundle_sha256"
        ),
        "pilot_contract_version": PILOT_CONTRACT_VERSION,
        "eligible_request_count": len(rows) if automated else 0,
        "checks": checks,
        "automated_gate_passed": automated,
        "human_review_passed": human_review,
        "pilot_accepted": automated and human_review,
        "default_rollout_authorized": False,
        "reason": (
            "accepted" if automated and human_review
            else "human_review_pending" if automated
            else "automated_gate_failed"
        ),
    }


__all__ = ["build_pilot_gate"]
