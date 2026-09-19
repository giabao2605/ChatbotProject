"""Fail-closed reconciliation for the Query Decomposition pilot WAL."""

from __future__ import annotations

from pathlib import Path

from scripts.ops.query_decomposition_pilot import (
    PILOT_CONTRACT_VERSION,
    SEQUENTIAL_PILOT_CONTRACT_VERSION,
    PILOT_DURATION,
    PILOT_REQUEST_COUNT,
    _authorization_and_schedule,
    _sha256_digest,
    _timestamp,
    _wal_rows,
    pilot_evidence_valid,
)
from scripts.ops.query_pilot_review_artifacts import (
    DELETION_RECEIPT_SCHEMA,
    REVIEW_PACK_SCHEMA,
    REVIEW_RESULT_SCHEMA,
    load_metadata_artifact,
    review_artifacts_valid,
)
from scripts.ops.query_pilot_capture_lifecycle import JOURNAL_SCHEMA
from scripts.ops.query_pilot_review_capture import _sha256
from scripts.ops.query_pilot_review_integrity import review_tool_hashes


def _review_valid(
    *, pack_path: str | Path | None, result_path: str | Path | None,
    receipt_path: str | Path | None, capture_dir: str | Path | None,
    trace_path: str | Path | None, journal_path: str | Path | None,
    source_root: str | Path | None, wal_path: Path,
    authorization: dict, authorization_sha: str,
    schedule: dict, schedule_sha: str, rows: list[dict], run_root: Path,
) -> bool:
    if any(
        value is None
        for value in (
            pack_path, result_path, receipt_path, capture_dir, trace_path,
            journal_path, source_root,
        )
    ):
        return False
    try:
        pack_file = Path(pack_path).resolve()
        result_file = Path(result_path).resolve()
        receipt_file = Path(receipt_path).resolve()
        captures = Path(capture_dir).resolve()
        trace_file = Path(trace_path).resolve()
        journal_file = Path(journal_path).resolve()
        source = Path(source_root).resolve()
        bound_run_root = (
            source / str(authorization.get("pilot_run_root") or "")
        ).resolve()
        if not all((
            run_root == bound_run_root,
            pack_file.parent == run_root,
            result_file.parent == run_root,
            receipt_file.parent == run_root,
            captures == run_root / "review-captures",
            trace_file == run_root / "trace.jsonl",
            wal_path == run_root / "pilot.wal.jsonl",
            journal_file == run_root / "capture-deletion.journal.json",
            captures.is_dir(),
        )):
            return False
        pack = load_metadata_artifact(pack_file, schema=REVIEW_PACK_SCHEMA)
        result = load_metadata_artifact(result_file, schema=REVIEW_RESULT_SCHEMA)
        receipt = load_metadata_artifact(
            receipt_file, schema=DELETION_RECEIPT_SCHEMA,
        )
        journal = load_metadata_artifact(journal_file, schema=JOURNAL_SCHEMA)
        wal_sha256 = _sha256(wal_path.read_bytes())
        trace_sha256 = _sha256(trace_file.read_bytes())
        tools = review_tool_hashes(source)
        journal_sha256 = _sha256(journal_file.read_bytes())
        captures_empty = not any(captures.iterdir())
    except (OSError, ValueError):
        return False
    return captures_empty and review_artifacts_valid(
        pack=pack,
        review_result=result,
        deletion_receipt=receipt,
        authorization=authorization,
        authorization_sha256=authorization_sha,
        schedule=schedule,
        schedule_sha256=schedule_sha,
        rows=rows,
        wal_sha256=wal_sha256,
        trace_artifact_sha256=trace_sha256,
        review_tool_sha256=tools,
        deletion_journal=journal,
        deletion_journal_sha256=journal_sha256,
    )


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
    contract_version: str = PILOT_CONTRACT_VERSION,
) -> tuple[bool, bool]:
    sequential = contract_version == SEQUENTIAL_PILOT_CONTRACT_VERSION
    card_by_id = {card.get("card_id"): card for card in cards}
    try:
        per_request = all(
            row.get("schema") == "query-decomposition-pilot-wal-v1"
            and row.get("pilot_contract_version", contract_version) == contract_version
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
            and pilot_evidence_valid(row.get("evidence"))
            for row in rows
        )
        cadence = len(rows) == len(cards) and all(
            row.get("card_id") == card.get("card_id")
            and (
                sequential or index == len(cards) - 1
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
    review_pack_path: str | Path | None = None,
    deletion_receipt_path: str | Path | None = None,
    capture_dir: str | Path | None = None,
    trace_path: str | Path | None = None,
    deletion_journal_path: str | Path | None = None,
    source_root: str | Path | None = None,
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
        wal_file = Path(wal_path).resolve()
        rows = _wal_rows(wal_file)
        parse_valid = True
    except (OSError, ValueError):
        rows, parse_valid = [], False
    cards = schedule.get("cards")
    cards = cards if isinstance(cards, list) else []
    contract_version = schedule.get("pilot_contract_version")
    sequential = contract_version == SEQUENTIAL_PILOT_CONTRACT_VERSION
    row_cards = [row.get("card_id") for row in rows]
    per_request, cadence = _row_contract_valid(
        rows, cards, auth_sha=auth_sha, schedule_sha=schedule_sha,
        runtime_identity_sha256=runtime_identity_sha256,
        expires_at=authorization.get("expires_at"),
        contract_version=contract_version,
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
        **({"sequential_order_and_nonoverlap": cadence} if sequential else {"minimum_24_hours": duration_valid}),
        "no_retry_replacement_or_catch_up": all(
            schedule.get(name) == "none"
            for name in ("retry_policy", "replacement_policy", "catch_up_policy")
        ),
        "concurrency_one": schedule.get("max_concurrency") == 1,
    }
    automated = all(checks.values())
    human_review = automated and _review_valid(
        pack_path=review_pack_path,
        result_path=review_result_path,
        receipt_path=deletion_receipt_path,
        capture_dir=capture_dir,
        trace_path=trace_path,
        journal_path=deletion_journal_path,
        source_root=source_root,
        wal_path=wal_file,
        authorization=authorization,
        authorization_sha=auth_sha,
        schedule=schedule,
        schedule_sha=schedule_sha,
        rows=rows,
        run_root=wal_file.parent,
    )
    return {
        "schema": "query-decomposition-production-pilot-gate-v1",
        "source_commit": authorization.get("source_commit"),
        "activation_bundle_sha256": authorization.get(
            "activation_bundle_sha256"
        ),
        "pilot_contract_version": contract_version,
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
