"""Metadata-only owner-review artifacts for the Query pilot."""

from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
from typing import Mapping

from scripts.ops.query_decomposition_pilot import pilot_evidence_valid
from scripts.ops.query_pilot_capture_lifecycle import JOURNAL_SCHEMA, delete_capture_set
from scripts.ops.query_pilot_review_capture import (
    CAPTURE_SCHEMA,
    MAX_ANSWER_BYTES,
    MAX_CAPTURE_COUNT,
    REVIEW_CAPTURE_DESIGN_SHA256,
    _METADATA_FIELDS,
    _canonical,
    _digest,
    _exclusive_atomic_bytes, _read_capture_bytes,
    _sha256,
    _strict_object,
    review_capture_cards,
)
from scripts.ops.query_pilot_review_content import (
    load_manifest_questions,
    load_review_item_content,
    load_review_item_content_with_citations,
)


REVIEW_PACK_SCHEMA = "query-decomposition-pilot-review-pack-v3"
REVIEW_RESULT_SCHEMA = "query-decomposition-pilot-review-result-v3"
DELETION_RECEIPT_SCHEMA = "query-decomposition-pilot-capture-deletion-v2"
REJECTION_REASON_CODES = frozenset({
    "answer_incorrect",
    "citation_incorrect",
    "multiple_failures",
    "safety_incorrect",
})
_PACK_FIELDS = frozenset({
    "schema", "source_commit", "review_capture_design_sha256",
    "pilot_draft_sha256", "consolidated_launch_draft_sha256",
    "pilot_authorization_sha256", "schedule_sha256", "pilot_run_root",
    "wal_sha256", "trace_artifact_sha256", "review_tool_sha256",
    "manifest_sha256", "reviewer", "capture_count", "review_item_count",
    "items", "raw_content_persisted",
})
_CAPTURE_ITEM_FIELDS = frozenset({
    "card_id", "case_id", "request_sha256", "trace_id_sha256",
    "review_mode", "capture_file", "capture_artifact_sha256",
    "ciphertext_sha256", "answer_sha256", "answer_utf8_bytes",
    "citation_count", "citations_sha256", "citations_utf8_bytes",
    "evidence",
})
_REFUSAL_ITEM_FIELDS = frozenset({
    "card_id", "case_id", "request_sha256", "trace_id_sha256",
    "review_mode", "evidence",
})
_REVIEW_EVIDENCE_FIELDS = (
    "query_result_status",
    "completion_outcome",
    "refusal_reason_code",
    "refusal_template_passed",
    "security_passed",
    "citation_structure_passed",
    "provenance_passed",
    "leakage_detected",
)
_RESULT_FIELDS = frozenset({
    "schema", "source_commit", "review_capture_design_sha256",
    "consolidated_launch_draft_sha256", "pilot_authorization_sha256",
    "schedule_sha256", "review_pack_sha256", "pilot_run_root",
    "wal_sha256", "trace_artifact_sha256", "review_tool_sha256",
    "reviewer", "evaluated_at", "reviewed_count", "labels", "all_accepted",
    "raw_content_persisted", "default_rollout_authorized",
})
_RECEIPT_FIELDS = frozenset({
    "schema", "source_commit", "review_capture_design_sha256",
    "consolidated_launch_draft_sha256", "pilot_authorization_sha256",
    "schedule_sha256", "review_pack_sha256", "pilot_run_root",
    "wal_sha256", "trace_artifact_sha256", "review_tool_sha256",
    "review_result_sha256", "deleted_at", "deleted_capture_count",
    "capture_artifact_sha256", "ciphertext_sha256",
    "remaining_capture_count", "encrypted_capture_deleted",
    "deletion_journal_sha256",
})
_LABEL_FIELDS = frozenset({
    "card_id", "trace_id_sha256", "answer_correct", "citation_correct",
    "safety_correct", "decision", "reason_code",
})
_WAL_FIELDS = frozenset({
    "schema", "authorization_sha256", "schedule_sha256", "card_id",
    "case_id", "attempt_number", "attempted_at", "completed_at",
    "trace_id_sha256", "runtime_identity_sha256", "evidence",
})


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _capture_path_inside(path: Path, root: Path) -> bool:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    return (
        path.name.endswith(".capture.json")
        and not path.is_symlink()
        and resolved_path.parent == resolved_root
        and resolved_path.is_file()
    )


def _contains_raw_content_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            key in {"question", "answer", "content"}
            or _contains_raw_content_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_raw_content_key(item) for item in value)
    return False


def _tool_hashes_valid(value: object) -> bool:
    return bool(
        isinstance(value, dict)
        and value
        and all(
            isinstance(path, str)
            and Path(path).as_posix() == path
            and _digest(digest)
            for path, digest in value.items()
        )
    )


def load_metadata_artifact(path: str | Path, *, schema: str) -> dict:
    """Load one strict metadata-only JSON artifact with no duplicate fields."""
    raw = Path(path).read_bytes()
    value = _strict_object(raw)
    if value.get("schema") != schema:
        raise ValueError("review_artifact_schema_invalid")
    if _contains_raw_content_key(value):
        raise ValueError("review_artifact_contains_raw_content")
    if raw != _canonical(value) + b"\n":
        raise ValueError("review_artifact_not_canonical")
    return value


def load_metadata_rows(path: str | Path) -> list[dict]:
    """Load strict JSONL metadata rows without accepting duplicate fields."""
    rows = []
    for raw in Path(path).read_bytes().splitlines():
        if raw.strip():
            row = _strict_object(raw)
            if _contains_raw_content_key(row):
                raise ValueError("review_artifact_contains_raw_content")
            rows.append(row)
    return rows


def write_metadata_artifact(path: str | Path, value: Mapping[str, object]) -> str:
    """Write one exclusive metadata-only artifact and return its SHA-256."""
    if _contains_raw_content_key(value):
        raise ValueError("review_artifact_contains_raw_content")
    raw = _canonical(dict(value)) + b"\n"
    _exclusive_atomic_bytes(Path(path).resolve(), raw)
    return _sha256(raw)


def _capture_record(path: Path) -> dict:
    raw = _read_capture_bytes(path)
    artifact = _strict_object(raw)
    if set(artifact) != _METADATA_FIELDS | {"ciphertext_base64"}:
        raise ValueError("review_capture_binding_invalid")
    if raw != _canonical(artifact) + b"\n":
        raise ValueError("review_capture_binding_invalid")
    try:
        ciphertext = base64.b64decode(
            artifact["ciphertext_base64"], validate=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("review_capture_binding_invalid") from exc
    metadata = {name: artifact[name] for name in _METADATA_FIELDS}
    if (
        metadata.get("schema") != CAPTURE_SCHEMA
        or _sha256(ciphertext) != metadata.get("ciphertext_sha256")
    ):
        raise ValueError("review_capture_binding_invalid")
    return {
        **metadata,
        "capture_artifact_sha256": _sha256(raw),
        "capture_file": path.name,
    }


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _timestamp_valid(value: object) -> bool:
    return _timestamp(value) is not None


def _pack_sha256(pack: Mapping[str, object]) -> str:
    return _sha256(_canonical(dict(pack)) + b"\n")


def _wal_rows_valid(
    cards: list[dict], rows: list[dict], *,
    authorization_sha256: str, schedule_sha256: str,
) -> bool:
    if not all((
        isinstance(cards, list),
        isinstance(rows, list),
        all(isinstance(card, dict) for card in cards),
        all(isinstance(row, dict) for row in rows),
    )):
        return False
    card_by_id = {card.get("card_id"): card for card in cards}
    row_by_id = {row.get("card_id"): row for row in rows}
    if not all((
        len(card_by_id) == len(cards) == 100,
        len(row_by_id) == len(rows) == 100,
        set(row_by_id) == set(card_by_id),
        len({row.get("trace_id_sha256") for row in rows}) == 100,
    )):
        return False
    return all(
        set(row) == _WAL_FIELDS
        and row.get("schema") == "query-decomposition-pilot-wal-v1"
        and row.get("authorization_sha256") == authorization_sha256
        and row.get("schedule_sha256") == schedule_sha256
        and row.get("case_id") == card_by_id[card_id].get("case_id")
        and row.get("attempt_number") == 1
        and isinstance(row.get("evidence"), dict)
        and pilot_evidence_valid(row.get("evidence"))
        and _digest(row.get("trace_id_sha256"))
        and _digest(row.get("runtime_identity_sha256"))
        for card_id, row in row_by_id.items()
    )


def _review_items_valid(
    items: list[dict], *, cards: list[dict], rows: list[dict],
    selected: frozenset[str],
) -> bool:
    card_by_id = {card["card_id"]: card for card in cards}
    row_by_id = {row["card_id"]: row for row in rows}
    for item in items:
        card_id = item.get("card_id")
        card = card_by_id.get(card_id)
        row = row_by_id.get(card_id)
        if not isinstance(card, dict) or not isinstance(row, dict):
            return False
        if not all((
            item.get("case_id") == card.get("case_id") == row.get("case_id"),
            item.get("request_sha256") == card.get("request_sha256"),
            item.get("trace_id_sha256") == row.get("trace_id_sha256"),
        )):
            return False
        mode = item.get("review_mode")
        evidence = row.get("evidence")
        if not isinstance(evidence, dict) or item.get("evidence") != {
            name: evidence.get(name) for name in _REVIEW_EVIDENCE_FIELDS
        }:
            return False
        if mode == "encrypted_answer" and card_id not in selected:
            return False
        if mode == "deterministic_safe_refusal":
            if not all((
                card_id not in selected,
                evidence.get("owner_review_required") is True,
            )):
                return False
    return True


def build_review_pack(
    *, authorization: Mapping[str, object], authorization_sha256: str,
    schedule: Mapping[str, object], schedule_sha256: str,
    rows: list[dict], capture_dir: str | Path, wal_sha256: str,
    trace_artifact_sha256: str, review_tool_sha256: Mapping[str, str],
) -> dict:
    """Build a metadata-only review pack from completed WAL and captures."""
    selected_ids = review_capture_cards(schedule)
    cards = schedule["cards"]
    row_by_id = {row.get("card_id"): row for row in rows}
    source_commit = authorization.get("source_commit")
    pilot_draft_sha256 = (authorization.get("pilot_draft") or {}).get("sha256")
    consolidated_draft_sha256 = (
        authorization.get("consolidated_launch_draft") or {}
    ).get("sha256")
    manifest_sha256 = (schedule.get("manifest") or {}).get("sha256")
    pilot_run_root = authorization.get("pilot_run_root")
    if not all((
        _wal_rows_valid(
            cards,
            rows,
            authorization_sha256=authorization_sha256,
            schedule_sha256=schedule_sha256,
        ),
        _digest(source_commit, length=40),
        _digest(pilot_draft_sha256),
        _digest(consolidated_draft_sha256),
        _digest(authorization_sha256),
        _digest(schedule_sha256),
        _digest(manifest_sha256),
        isinstance(pilot_run_root, str) and bool(pilot_run_root),
        _digest(wal_sha256),
        _digest(trace_artifact_sha256),
        _tool_hashes_valid(review_tool_sha256),
        isinstance(authorization.get("actor"), str),
    )):
        raise ValueError("review_pack_inputs_invalid")
    capture_root = Path(capture_dir).resolve()
    capture_paths = sorted(capture_root.glob("*.capture.json"))
    expected_capture_files = {
        f"{card_id}.capture.json" for card_id in selected_ids
    }
    try:
        actual_capture_files = {path.name for path in capture_root.iterdir()}
    except OSError as exc:
        raise ValueError("review_capture_count_invalid") from exc
    if (
        len(capture_paths) != MAX_CAPTURE_COUNT
        or actual_capture_files != expected_capture_files
    ):
        raise ValueError("review_capture_count_invalid")
    if not all(_capture_path_inside(path, capture_root) for path in capture_paths):
        raise ValueError("review_capture_binding_invalid")
    captures = [_capture_record(path) for path in capture_paths]
    capture_by_card = {value["card_id"]: value for value in captures}
    if (
        len(capture_by_card) != MAX_CAPTURE_COUNT
        or set(capture_by_card) != selected_ids
    ):
        raise ValueError("review_capture_binding_invalid")
    items = []
    for card in cards:
        card_id = card["card_id"]
        row = row_by_id[card_id]
        if card_id in selected_ids:
            capture = capture_by_card[card_id]
            if not all((
                capture["source_commit"] == source_commit,
                capture["review_capture_design_sha256"]
                == REVIEW_CAPTURE_DESIGN_SHA256,
                capture["pilot_draft_sha256"] == pilot_draft_sha256,
                capture["consolidated_launch_draft_sha256"]
                == consolidated_draft_sha256,
                capture["pilot_authorization_sha256"] == authorization_sha256,
                capture["schedule_sha256"] == schedule_sha256,
                capture["case_id"] == card.get("case_id") == row.get("case_id"),
                capture["request_sha256"] == card.get("request_sha256"),
                capture["trace_id_sha256"] == row.get("trace_id_sha256"),
                capture["attempt_number"] == row.get("attempt_number") == 1,
                _digest(capture["answer_sha256"]),
                _digest(capture["citations_sha256"]),
                type(capture["answer_utf8_bytes"]) is int,
                0 <= capture["answer_utf8_bytes"] <= MAX_ANSWER_BYTES,
            )):
                raise ValueError("review_capture_binding_invalid")
            items.append({
                "card_id": card_id,
                "case_id": card["case_id"],
                "request_sha256": card["request_sha256"],
                "trace_id_sha256": row["trace_id_sha256"],
                "review_mode": "encrypted_answer",
                "capture_file": capture["capture_file"],
                "capture_artifact_sha256": capture["capture_artifact_sha256"],
                "ciphertext_sha256": capture["ciphertext_sha256"],
                "answer_sha256": capture["answer_sha256"],
                "answer_utf8_bytes": capture["answer_utf8_bytes"],
                "citation_count": capture["citation_count"],
                "citations_sha256": capture["citations_sha256"],
                "citations_utf8_bytes": capture["citations_utf8_bytes"],
                "evidence": {
                    name: row["evidence"].get(name)
                    for name in _REVIEW_EVIDENCE_FIELDS
                },
            })
        elif row["evidence"].get("owner_review_required") is True:
            items.append({
                "card_id": card_id,
                "case_id": card["case_id"],
                "request_sha256": card["request_sha256"],
                "trace_id_sha256": row["trace_id_sha256"],
                "review_mode": "deterministic_safe_refusal",
                "evidence": {
                    name: row["evidence"].get(name)
                    for name in _REVIEW_EVIDENCE_FIELDS
                },
            })
    return {
        "schema": REVIEW_PACK_SCHEMA,
        "source_commit": source_commit,
        "review_capture_design_sha256": REVIEW_CAPTURE_DESIGN_SHA256,
        "pilot_draft_sha256": pilot_draft_sha256,
        "consolidated_launch_draft_sha256": consolidated_draft_sha256,
        "pilot_authorization_sha256": authorization_sha256,
        "schedule_sha256": schedule_sha256,
        "pilot_run_root": pilot_run_root,
        "wal_sha256": wal_sha256,
        "trace_artifact_sha256": trace_artifact_sha256,
        "review_tool_sha256": dict(review_tool_sha256),
        "manifest_sha256": manifest_sha256,
        "reviewer": authorization["actor"],
        "capture_count": MAX_CAPTURE_COUNT,
        "review_item_count": len(items),
        "items": items,
        "raw_content_persisted": False,
    }


def review_label_valid(item: Mapping[str, object], label: object) -> bool:
    """Validate one owner label against the exact review item identity."""
    if not isinstance(label, dict) or set(label) != _LABEL_FIELDS:
        return False
    booleans = tuple(
        label.get(name) for name in (
            "answer_correct", "citation_correct", "safety_correct",
        )
    )
    decision = label.get("decision")
    reason = label.get("reason_code")
    identity_valid = all((
        label.get("card_id") == item.get("card_id"),
        label.get("trace_id_sha256") == item.get("trace_id_sha256"),
        _digest(label.get("trace_id_sha256")),
    ))
    failed = [
        name.removesuffix("_correct") + "_incorrect"
        for name, value in zip(
            ("answer_correct", "citation_correct", "safety_correct"),
            booleans,
            strict=True,
        )
        if value is False
    ]
    expected_rejection_reason = (
        failed[0] if len(failed) == 1 else "multiple_failures"
    )
    accepted = decision == "accepted" and all(booleans) and reason == "pass"
    rejected = (
        decision == "rejected"
        and not all(booleans)
        and reason in REJECTION_REASON_CODES
        and reason == expected_rejection_reason
    )
    return (
        identity_valid
        and all(type(value) is bool for value in booleans)
        and (accepted or rejected)
    )


def _labels_valid(pack: Mapping[str, object], labels: object) -> bool:
    items = pack.get("items")
    if not isinstance(items, list) or not isinstance(labels, list):
        return False
    expected = {
        (item.get("card_id"), item.get("trace_id_sha256")) for item in items
    }
    actual = []
    for label in labels:
        item = next((
            value for value in items
            if isinstance(value, dict)
            and isinstance(label, dict)
            and value.get("card_id") == label.get("card_id")
            and value.get("trace_id_sha256") == label.get("trace_id_sha256")
        ), None)
        if item is None or not review_label_valid(item, label):
            return False
        actual.append((label["card_id"], label["trace_id_sha256"]))
    return len(actual) == len(set(actual)) and set(actual) == expected


def finalize_review_result(
    pack: Mapping[str, object], labels: list[dict], *, reviewer: str,
    evaluated_at: str,
) -> dict:
    """Finalize metadata-only owner labels bound to one immutable pack."""
    if not all((
        pack.get("schema") == REVIEW_PACK_SCHEMA,
        reviewer == pack.get("reviewer"),
        _timestamp_valid(evaluated_at),
        _labels_valid(pack, labels),
    )):
        raise ValueError("review_labels_invalid")
    normalized_labels = [dict(label) for label in labels]
    return {
        "schema": REVIEW_RESULT_SCHEMA,
        "source_commit": pack["source_commit"],
        "review_capture_design_sha256": REVIEW_CAPTURE_DESIGN_SHA256,
        "consolidated_launch_draft_sha256": (
            pack["consolidated_launch_draft_sha256"]
        ),
        "pilot_authorization_sha256": pack["pilot_authorization_sha256"],
        "schedule_sha256": pack["schedule_sha256"],
        "pilot_run_root": pack["pilot_run_root"],
        "wal_sha256": pack["wal_sha256"],
        "trace_artifact_sha256": pack["trace_artifact_sha256"],
        "review_tool_sha256": dict(pack["review_tool_sha256"]),
        "review_pack_sha256": _pack_sha256(pack),
        "reviewer": reviewer,
        "evaluated_at": evaluated_at,
        "reviewed_count": len(normalized_labels),
        "labels": normalized_labels,
        "all_accepted": all(
            label["decision"] == "accepted" for label in normalized_labels
        ),
        "raw_content_persisted": False,
        "default_rollout_authorized": False,
    }


def _result_valid(
    pack: Mapping[str, object], result: Mapping[str, object], *,
    require_all_accepted: bool = True,
) -> bool:
    labels = result.get("labels")
    decisions_accepted = (
        isinstance(labels, list)
        and all(
            isinstance(label, dict) and label.get("decision") == "accepted"
            for label in labels
        )
    )
    return all((
        set(result) == _RESULT_FIELDS,
        result.get("schema") == REVIEW_RESULT_SCHEMA,
        result.get("source_commit") == pack.get("source_commit"),
        result.get("review_capture_design_sha256")
        == REVIEW_CAPTURE_DESIGN_SHA256,
        result.get("consolidated_launch_draft_sha256")
        == pack.get("consolidated_launch_draft_sha256"),
        result.get("pilot_authorization_sha256")
        == pack.get("pilot_authorization_sha256"),
        result.get("schedule_sha256") == pack.get("schedule_sha256"),
        result.get("pilot_run_root") == pack.get("pilot_run_root"),
        result.get("wal_sha256") == pack.get("wal_sha256"),
        result.get("trace_artifact_sha256")
        == pack.get("trace_artifact_sha256"),
        result.get("review_tool_sha256") == pack.get("review_tool_sha256"),
        result.get("review_pack_sha256") == _pack_sha256(pack),
        result.get("reviewer") == pack.get("reviewer"),
        _timestamp_valid(result.get("evaluated_at")),
        result.get("reviewed_count") == pack.get("review_item_count"),
        _labels_valid(pack, labels),
        result.get("all_accepted") is decisions_accepted,
        not require_all_accepted or decisions_accepted,
        result.get("raw_content_persisted") is False,
        result.get("default_rollout_authorized") is False,
    ))


def delete_review_captures(
    capture_dir: str | Path, *, pack: Mapping[str, object],
    review_result: Mapping[str, object], deleted_at: str,
    journal_path: str | Path | None = None,
) -> dict:
    """Delete reviewed ciphertext and return a hash-bound metadata receipt."""
    deleted = _timestamp(deleted_at)
    evaluated = _timestamp(review_result.get("evaluated_at"))
    if not _result_valid(
        pack, review_result, require_all_accepted=False,
    ) or deleted is None or evaluated is None or deleted < evaluated:
        raise ValueError("capture_deletion_inputs_invalid")
    capture_items = [
        item for item in pack["items"]
        if item.get("review_mode") == "encrypted_answer"
    ]
    target = Path(capture_dir).resolve()
    expected_files = {item["capture_file"] for item in capture_items}
    item_by_file = {item["capture_file"]: item for item in capture_items}
    result_sha256 = _sha256(_canonical(dict(review_result)) + b"\n")
    journal_file = Path(journal_path).resolve() if journal_path else (
        target.parent / "capture-deletion.journal.json"
    )
    if not journal_file.exists():
        paths = sorted(target.glob("*.capture.json"))
        try:
            actual_files = {path.name for path in target.iterdir()}
        except OSError as exc:
            raise ValueError("capture_deletion_set_invalid") from exc
        if (
            {path.name for path in paths} != expected_files
            or actual_files != expected_files
            or not all(_capture_path_inside(path, target) for path in paths)
        ):
            raise ValueError("capture_deletion_set_invalid")
        if any(
            _sha256(path.read_bytes())
            != item_by_file[path.name]["capture_artifact_sha256"]
            for path in paths
        ):
            raise ValueError("capture_deletion_binding_invalid")
    _, journal_sha256 = delete_capture_set(
        capture_dir=target,
        journal_path=journal_file,
        operation="owner_review",
        binding_sha256=result_sha256,
        expected_files={
            name: item_by_file[name]["capture_artifact_sha256"]
            for name in expected_files
        },
    )
    return {
        "schema": DELETION_RECEIPT_SCHEMA,
        "source_commit": pack["source_commit"],
        "review_capture_design_sha256": REVIEW_CAPTURE_DESIGN_SHA256,
        "consolidated_launch_draft_sha256": (
            pack["consolidated_launch_draft_sha256"]
        ),
        "pilot_authorization_sha256": pack["pilot_authorization_sha256"],
        "schedule_sha256": pack["schedule_sha256"],
        "pilot_run_root": pack["pilot_run_root"],
        "wal_sha256": pack["wal_sha256"],
        "trace_artifact_sha256": pack["trace_artifact_sha256"],
        "review_tool_sha256": dict(pack["review_tool_sha256"]),
        "review_pack_sha256": _pack_sha256(pack),
        "review_result_sha256": result_sha256,
        "deleted_at": deleted_at,
        "deleted_capture_count": len(expected_files),
        "capture_artifact_sha256": sorted(
            item["capture_artifact_sha256"] for item in capture_items
        ),
        "ciphertext_sha256": sorted(
            item["ciphertext_sha256"] for item in capture_items
        ),
        "remaining_capture_count": 0,
        "encrypted_capture_deleted": True,
        "deletion_journal_sha256": journal_sha256,
    }


def review_artifacts_valid(
    *, pack: Mapping[str, object], review_result: Mapping[str, object],
    deletion_receipt: Mapping[str, object],
    authorization: Mapping[str, object], authorization_sha256: str,
    schedule: Mapping[str, object], schedule_sha256: str,
    rows: list[dict], wal_sha256: str, trace_artifact_sha256: str,
    review_tool_sha256: Mapping[str, str],
    deletion_journal: Mapping[str, object], deletion_journal_sha256: str,
) -> bool:
    """Validate the complete metadata-only owner-review trust chain."""
    try:
        selected = review_capture_cards(schedule)
        items = pack.get("items")
        if not isinstance(items, list) or not _wal_rows_valid(
            schedule["cards"],
            rows,
            authorization_sha256=authorization_sha256,
            schedule_sha256=schedule_sha256,
        ):
            return False
        selected_items = [
            item for item in items if item.get("review_mode") == "encrypted_answer"
        ]
        refusal_required = {
            row.get("card_id") for row in rows
            if isinstance(row.get("evidence"), dict)
            and row["evidence"].get("owner_review_required") is True
        }
        reviewed_ids = {item.get("card_id") for item in items}
        item_fields_valid = (
            all(isinstance(item, dict) for item in items)
            and all(
                item.get("review_mode") in {
                    "encrypted_answer", "deterministic_safe_refusal",
                }
                and set(item) == (
                    _CAPTURE_ITEM_FIELDS
                    if item["review_mode"] == "encrypted_answer"
                    else _REFUSAL_ITEM_FIELDS
                )
                for item in items
            )
            and _review_items_valid(
                items,
                cards=schedule["cards"],
                rows=rows,
                selected=selected,
            )
        )
        capture_artifacts = sorted(
            item.get("capture_artifact_sha256") for item in selected_items
        )
        ciphertexts = sorted(
            item.get("ciphertext_sha256") for item in selected_items
        )
        deleted = _timestamp(deletion_receipt.get("deleted_at"))
        evaluated = _timestamp(review_result.get("evaluated_at"))
        pack_valid = all((
            set(pack) == _PACK_FIELDS,
            not _contains_raw_content_key(pack),
            pack.get("schema") == REVIEW_PACK_SCHEMA,
            pack.get("source_commit") == authorization.get("source_commit"),
            pack.get("review_capture_design_sha256")
            == REVIEW_CAPTURE_DESIGN_SHA256,
            pack.get("pilot_draft_sha256")
            == (authorization.get("pilot_draft") or {}).get("sha256"),
            pack.get("consolidated_launch_draft_sha256")
            == (
                authorization.get("consolidated_launch_draft") or {}
            ).get("sha256"),
            pack.get("pilot_authorization_sha256") == authorization_sha256,
            pack.get("schedule_sha256") == schedule_sha256,
            pack.get("pilot_run_root") == authorization.get("pilot_run_root"),
            pack.get("wal_sha256") == wal_sha256,
            pack.get("trace_artifact_sha256") == trace_artifact_sha256,
            pack.get("review_tool_sha256") == dict(review_tool_sha256),
            pack.get("manifest_sha256")
            == (schedule.get("manifest") or {}).get("sha256"),
            pack.get("reviewer") == authorization.get("actor"),
            pack.get("capture_count") == len(selected_items) == MAX_CAPTURE_COUNT,
            pack.get("review_item_count") == len(items),
            pack.get("raw_content_persisted") is False,
            item_fields_valid,
            {item.get("card_id") for item in selected_items} == selected,
            refusal_required <= reviewed_ids,
            len(reviewed_ids) == len(items),
            all(_digest(value) for value in capture_artifacts + ciphertexts),
        ))
        receipt_valid = all((
            set(deletion_receipt) == _RECEIPT_FIELDS,
            not _contains_raw_content_key(review_result),
            not _contains_raw_content_key(deletion_receipt),
            deletion_receipt.get("schema") == DELETION_RECEIPT_SCHEMA,
            deletion_receipt.get("source_commit") == pack.get("source_commit"),
            deletion_receipt.get("review_capture_design_sha256")
            == REVIEW_CAPTURE_DESIGN_SHA256,
            deletion_receipt.get("consolidated_launch_draft_sha256")
            == pack.get("consolidated_launch_draft_sha256"),
            deletion_receipt.get("pilot_authorization_sha256")
            == authorization_sha256,
            deletion_receipt.get("schedule_sha256") == schedule_sha256,
            deletion_receipt.get("pilot_run_root") == pack.get("pilot_run_root"),
            deletion_receipt.get("wal_sha256") == wal_sha256,
            deletion_receipt.get("trace_artifact_sha256")
            == trace_artifact_sha256,
            deletion_receipt.get("review_tool_sha256")
            == dict(review_tool_sha256),
            deletion_receipt.get("review_pack_sha256") == _pack_sha256(pack),
            deletion_receipt.get("review_result_sha256")
            == _sha256(_canonical(dict(review_result)) + b"\n"),
            deleted is not None,
            evaluated is not None,
            deleted is not None and evaluated is not None
            and deleted >= evaluated,
            deletion_receipt.get("deleted_capture_count") == MAX_CAPTURE_COUNT,
            deletion_receipt.get("capture_artifact_sha256") == capture_artifacts,
            deletion_receipt.get("ciphertext_sha256") == ciphertexts,
            deletion_receipt.get("remaining_capture_count") == 0,
            deletion_receipt.get("encrypted_capture_deleted") is True,
            deletion_receipt.get("deletion_journal_sha256")
            == deletion_journal_sha256,
        ))
        journal_valid = all((
            deletion_journal.get("schema") == JOURNAL_SCHEMA,
            deletion_journal.get("operation") == "owner_review",
            deletion_journal.get("status") == "finalized",
            deletion_journal.get("remaining_capture_count") == 0,
            deletion_journal.get("binding_sha256")
            == deletion_receipt.get("review_result_sha256"),
            _digest(deletion_journal_sha256),
        ))
        return (
            pack_valid and _result_valid(pack, review_result)
            and receipt_valid and journal_valid
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


__all__ = [
    "DELETION_RECEIPT_SCHEMA", "REJECTION_REASON_CODES", "REVIEW_PACK_SCHEMA",
    "REVIEW_RESULT_SCHEMA", "build_review_pack", "delete_review_captures",
    "finalize_review_result", "load_manifest_questions",
    "load_metadata_artifact", "load_metadata_rows", "load_review_item_content",
    "load_review_item_content_with_citations",
    "review_artifacts_valid", "review_label_valid", "write_metadata_artifact",
]
