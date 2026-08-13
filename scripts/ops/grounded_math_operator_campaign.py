"""Plan and dispatch owner-authorized Grounded Math operator traffic."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from itertools import combinations
from pathlib import Path
from urllib.parse import urlparse

from mech_chatbot.rag.entity_resolver import extract_explicit_codes
from mech_chatbot.rag.grounded_math import (
    GroundedFact,
    detect_calculation_operation,
    solve_grounded_calculation,
)
from mech_chatbot.rag.intent import is_bom_lookup


SCHEMA = "grounded-math-operator-campaign-v1"
WAL_SCHEMA = "grounded-math-operator-wal-v1"
TRAFFIC_CLASS = "owner_authorized_operator_generated"
TRANSPORT = "internal_rag_sse"
OPERATIONS = ("add", "subtract", "ratio", "percent", "multiply")
UNAVAILABLE_OPERATIONS = {
    "divide": "corpus_missing_dimensionless_divisor",
    "sum": "transport_has_no_explicit_document_scope",
}
CAMPAIGN_CARD_COUNT = 100
CAMPAIGN_DURATION = timedelta(days=7)
CAMPAIGN_CADENCE = CAMPAIGN_DURATION / (CAMPAIGN_CARD_COUNT - 1)
MAX_CARDS_PER_DOCUMENT = 26
MAX_CARDS_PER_DOCUMENT_OPERATION = 7
OPERATOR_USER_ID = 81
OPERATOR_USERNAME = "admin_bao"
EXPECTED_SERVING_COMMIT = "7b9d57562a669984b843d48d6d7ddf09048c472d"
PART_CODE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{1,63}")
INVENTORY_SQL = """
SELECT
    t.DocID,
    t.TenFile,
    t.VersionNo,
    t.OwnerDepartment,
    t.Site,
    b.ID AS SourceRowID,
    b.TrangSo,
    b.MaHang,
    b.SoLuong,
    b.Unit
FROM dbo.TaiLieu AS t
JOIN dbo.BangKeVatTu AS b ON b.DocID = t.DocID
WHERE t.LifecycleStatus = 'published'
  AND t.ReviewStatus = 'approved'
  AND t.IsCurrent = 1
  AND t.Servable = 1
  AND t.TenFile LIKE '%.pdf'
ORDER BY t.DocID, b.ID
"""


class CampaignStopped(RuntimeError):
    """The campaign cannot continue without violating its declared contract."""


def _sha256(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def canonical_json(value: object) -> bytes:
    """Return the stable byte representation used by campaign bindings."""

    return _canonical_json(value)


def _format_timestamp(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    if normalized.microsecond:
        return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp_timezone_missing")
    return parsed.astimezone(timezone.utc)


def _normalized_inventory(inventory: Iterable[dict]) -> list[dict]:
    normalized: list[dict] = []
    for item in inventory:
        facts = sorted(
            (
                {
                    "label": str(fact["label"]).strip(),
                    "value": str(Decimal(str(fact["value"]))),
                    "unit": str(fact.get("unit") or "").strip(),
                    "page": int(fact["page"]),
                    "source_id": str(fact["source_id"]),
                }
                for fact in item.get("operand_facts") or ()
                if str(fact.get("label") or "").strip()
            ),
            key=lambda fact: (fact["label"].casefold(), fact["source_id"]),
        )
        normalized.append(
            {
                "doc_id": str(item["doc_id"]),
                "file_name": str(item["file_name"]).strip(),
                "version": str(item["version"]),
                "department": str(item["department"]).strip(),
                "site": str(item["site"]).strip(),
                "operand_facts": facts,
            }
        )
    return sorted(normalized, key=lambda item: (item["doc_id"], item["file_name"]))


def inventory_from_rows(rows: Iterable[dict]) -> list[dict]:
    source_rows = [dict(row) for row in rows]
    documents: dict[object, dict] = {}
    facts: dict[object, list[dict]] = defaultdict(list)
    quantity_code_counts = Counter(
        str(row.get("MaHang") or "").strip().casefold()
        for row in source_rows
        if str(row.get("MaHang") or "").strip()
        and row.get("SoLuong") is not None
    )
    for row in source_rows:
        doc_id = row["DocID"]
        documents.setdefault(
            doc_id,
            {
                "doc_id": doc_id,
                "file_name": str(row.get("TenFile") or "").strip(),
                "version": row.get("VersionNo") or 0,
                "department": str(row.get("OwnerDepartment") or "").strip(),
                "site": str(row.get("Site") or "").strip(),
            },
        )
        label = str(row.get("MaHang") or "").strip()
        if (
            not PART_CODE_RE.fullmatch(label)
            or row.get("SoLuong") is None
            or quantity_code_counts[label.casefold()] != 1
        ):
            continue
        try:
            value = str(Decimal(str(row["SoLuong"])))
            page = int(row.get("TrangSo") or 1)
            source_id = str(row.get("SourceRowID") or row.get("ID"))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if not source_id or source_id == "None":
            continue
        facts[doc_id].append(
            {
                "label": label,
                "value": value,
                "unit": str(row.get("Unit") or "").strip(),
                "page": page,
                "source_id": source_id,
            }
        )
    return [
        {
            **documents[doc_id],
            "operand_facts": facts[doc_id],
        }
        for doc_id in sorted(documents, key=str)
    ]


def _pair_prompt_candidates(labels: list[str]) -> list[dict]:
    operation_templates = {
        "add": "Theo BOM, cộng số lượng {left} với {right}.",
        "subtract": "Theo BOM, lấy số lượng {left} trừ số lượng {right}.",
        "ratio": "Tỷ lệ số lượng {left} so với {right} trong BOM là bao nhiêu?",
        "percent": "Số lượng {left} bằng bao nhiêu phần trăm số lượng {right} trong BOM?",
    }
    candidates: list[dict] = []
    for operation, template in operation_templates.items():
        pairs = (
            list(combinations(labels, 2))
            if operation == "add"
            else [(left, right) for left in labels for right in labels if left != right]
        )
        for left, right in pairs[:MAX_CARDS_PER_DOCUMENT_OPERATION]:
            candidates.append(
                {
                    "operation": operation,
                    "template_id": f"{operation}-pair",
                    "operand_labels": [left, right],
                    "operand_style": "part_code",
                    "prompt": template.format(left=left, right=right),
                }
            )
    return candidates


def _multiply_prompt_candidates(labels: list[str]) -> list[dict]:
    multiply_template = "Theo BOM, nhân số lượng {left} với số lượng {right}."
    return [
        {
            "operation": "multiply",
            "template_id": "multiply-pair",
            "operand_labels": [left, right],
            "operand_style": "part_code",
            "prompt": multiply_template.format(left=left, right=right),
        }
        for left, right in list(combinations(labels, 2))[
            :MAX_CARDS_PER_DOCUMENT_OPERATION
        ]
    ]


def _validated_candidates(
    candidates: list[dict], facts: tuple[GroundedFact, ...]
) -> tuple[dict, list[dict]]:
    by_operation: dict[str, list[dict]] = defaultdict(list)
    audit: list[dict] = []
    for candidate in candidates:
        rejection_reason = None
        if detect_calculation_operation(candidate["prompt"]) != candidate["operation"]:
            rejection_reason = "operation_not_detected"
        elif not extract_explicit_codes(candidate["prompt"]):
            rejection_reason = "explicit_code_missing"
        elif not is_bom_lookup(candidate["prompt"]):
            rejection_reason = "bom_intent_not_detected"
        else:
            result = solve_grounded_calculation(candidate["prompt"], facts)
            if result.status != "valid":
                rejection_reason = f"calculation_{result.status}"
        accepted = rejection_reason is None
        audit.append(
            {
                "operation": candidate["operation"],
                "operand_style": candidate["operand_style"],
                "accepted": accepted,
                "rejection_reason": rejection_reason,
            }
        )
        if accepted:
            by_operation[candidate["operation"]].append(candidate)
    return by_operation, audit


def _interleave_candidates(by_operation: dict) -> list[dict]:
    interleaved: list[dict] = []
    for variant_index in range(MAX_CARDS_PER_DOCUMENT_OPERATION):
        for operation in OPERATIONS:
            variants = by_operation[operation]
            if variant_index < len(variants):
                interleaved.append(variants[variant_index])
    return interleaved


def _prompt_candidates(document: dict) -> tuple[list[dict], list[dict]]:
    labels = [fact["label"] for fact in document["operand_facts"]]
    facts = tuple(
        GroundedFact(
            value=Decimal(fact["value"]),
            unit=fact["unit"],
            doc_id=int(document["doc_id"]),
            page=fact["page"],
            version=int(document["version"]),
            source_id=fact["source_id"],
            label=fact["label"],
        )
        for fact in document["operand_facts"]
    )
    candidates = _pair_prompt_candidates(labels)
    candidates.extend(_multiply_prompt_candidates(labels))
    by_operation, audit = _validated_candidates(candidates, facts)
    return _interleave_candidates(by_operation), audit


def _document_identity(document: dict) -> dict:
    return {
        key: document[key]
        for key in ("doc_id", "file_name", "version", "department", "site")
    }


def _preflight_summary(documents: list[dict], audit_by_document: dict) -> dict:
    rows = []
    for document in documents:
        document_hash = _sha256(_canonical_json(_document_identity(document)))
        for item in audit_by_document[document["doc_id"]]:
            rows.append({**item, "document_identity_sha256": document_hash})

    def summarize(field: str) -> list[dict]:
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            groups[str(row[field])].append(row)
        result = []
        for value in sorted(groups):
            group = groups[value]
            rejected = Counter(
                str(item["rejection_reason"])
                for item in group
                if not item["accepted"]
            )
            result.append(
                {
                    field: value,
                    "generated": len(group),
                    "accepted": sum(bool(item["accepted"]) for item in group),
                    "rejected_by_reason": dict(sorted(rejected.items())),
                }
            )
        return result

    rejected = Counter(
        str(row["rejection_reason"]) for row in rows if not row["accepted"]
    )
    return {
        "schema": "grounded-math-operator-preflight-v1",
        "production_checks": [
            "detect_calculation_operation",
            "extract_explicit_codes",
            "is_bom_lookup",
            "solve_grounded_calculation",
        ],
        "generated": len(rows),
        "accepted": sum(bool(row["accepted"]) for row in rows),
        "deterministic_validated": sum(bool(row["accepted"]) for row in rows),
        "rejected_by_reason": dict(sorted(rejected.items())),
        "by_operation": summarize("operation"),
        "by_operand_style": summarize("operand_style"),
        "by_document": summarize("document_identity_sha256"),
    }


def _select_candidates(
    documents: list[dict],
) -> tuple[list[tuple[dict, dict]], dict]:
    generated = {
        document["doc_id"]: _prompt_candidates(document) for document in documents
    }
    queues = {doc_id: value[0] for doc_id, value in generated.items()}
    audit_by_document = {doc_id: value[1] for doc_id, value in generated.items()}
    selected: list[tuple[dict, dict]] = []
    positions = defaultdict(int)
    made_progress = True
    while len(selected) < CAMPAIGN_CARD_COUNT and made_progress:
        made_progress = False
        for document in documents:
            if len(selected) >= CAMPAIGN_CARD_COUNT:
                break
            position = positions[document["doc_id"]]
            queue = queues[document["doc_id"]]
            if position >= min(len(queue), MAX_CARDS_PER_DOCUMENT):
                continue
            selected.append((document, queue[position]))
            positions[document["doc_id"]] += 1
            made_progress = True
    if len(selected) != CAMPAIGN_CARD_COUNT:
        raise ValueError("insufficient_unique_cards")
    if {candidate["operation"] for _, candidate in selected} != set(OPERATIONS):
        raise ValueError("insufficient_operation_coverage")
    if {candidate["operand_style"] for _, candidate in selected} != {"part_code"}:
        raise ValueError("insufficient_operand_style_coverage")
    return selected, _preflight_summary(documents, audit_by_document)


def build_campaign_cards(
    inventory: Iterable[dict], started_at: datetime
) -> tuple[dict, dict]:
    if started_at.tzinfo is None:
        raise ValueError("timestamp_timezone_missing")
    started = started_at.astimezone(timezone.utc)
    documents = _normalized_inventory(inventory)
    selected, preflight = _select_candidates(documents)
    inventory_sha256 = _sha256(_canonical_json(documents))

    raw_cards: list[dict] = []
    for index, (document, candidate) in enumerate(selected):
        document_identity = _document_identity(document)
        operand_identity = {
            "document": document_identity,
            "labels": candidate["operand_labels"],
            "style": candidate["operand_style"],
        }
        scheduled = started + CAMPAIGN_DURATION * index / (CAMPAIGN_CARD_COUNT - 1)
        raw_cards.append(
            {
                "card_id": f"card-{index + 1:03d}",
                "operation": candidate["operation"],
                "template_id": candidate["template_id"],
                "scheduled_at": _format_timestamp(scheduled),
                "document_identity_sha256": _sha256(_canonical_json(document_identity)),
                "operand_identity_sha256": _sha256(_canonical_json(operand_identity)),
                "operand_count": len(candidate["operand_labels"]),
                "operand_style": candidate["operand_style"],
                "prompt_sha256": _sha256(candidate["prompt"]),
                "prompt": candidate["prompt"],
            }
        )

    campaign_seed = {
        "schema": SCHEMA,
        "traffic_class": TRAFFIC_CLASS,
        "transport": TRANSPORT,
        "started_at": _format_timestamp(started),
        "inventory_sha256": inventory_sha256,
        "preflight": preflight,
        "cards": [
            {key: value for key, value in card.items() if key != "prompt"}
            for card in raw_cards
        ],
    }
    campaign_id = _sha256(_canonical_json(campaign_seed))[:24]
    common = {
        "schema": SCHEMA,
        "campaign_id": campaign_id,
        "traffic_class": TRAFFIC_CLASS,
        "transport": TRANSPORT,
        "started_at": _format_timestamp(started),
        "minimum_runtime_until": _format_timestamp(started + CAMPAIGN_DURATION),
        "inventory_sha256": inventory_sha256,
        "preflight": preflight,
    }
    public = {
        **common,
        "cards": [
            {key: value for key, value in card.items() if key != "prompt"}
            for card in raw_cards
        ],
    }
    private = {
        **common,
        "cards": [
            {
                "card_id": card["card_id"],
                "prompt_sha256": card["prompt_sha256"],
                "question": card["prompt"],
            }
            for card in raw_cards
        ],
    }
    return public, private


def build_owner_declaration(
    manifest: dict,
    window: dict,
    state: dict,
    health: dict,
    release_decisions: dict,
    *,
    approved_at: datetime,
    tool_sha256: str,
    declared_at: datetime | None = None,
) -> dict:
    started_at = parse_timestamp(manifest["started_at"])
    approved = approved_at.astimezone(timezone.utc)
    declared = (declared_at or approved_at).astimezone(timezone.utc)
    if approved > started_at or declared > started_at:
        raise ValueError("owner_declaration_must_precede_campaign")
    if len(tool_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in tool_sha256
    ):
        raise ValueError("operator_tool_sha256_invalid")
    runtime_bindings = json.loads(
        json.dumps(window.get("expected_runtime") or {}, ensure_ascii=False)
    )
    return {
        "schema": "grounded-math-operator-owner-declaration-v1",
        "owner": "bao.nguyen",
        "actor": {
            "user_id": OPERATOR_USER_ID,
            "username": OPERATOR_USERNAME,
        },
        "approved_at": _format_timestamp(approved),
        "declared_at": _format_timestamp(declared),
        "approval_source": "codex_task_user_authorization",
        "traffic_class": TRAFFIC_CLASS,
        "transport": TRANSPORT,
        "count_toward_pilot": True,
        "organic_claim_allowed": False,
        "quality_claim_allowed": False,
        "ui_parity_claim_allowed": False,
        "scope": "controlled_demo",
        "default_rollout_authorized": False,
        "selection_bias_disclosed": True,
        "generator_used_structured_values": True,
        "unavailable_operations": dict(UNAVAILABLE_OPERATIONS),
        "review_contract": {
            "primary_reviewers": ["bao.nguyen"],
            "stratified_cases": 20,
            "review_all_failures_and_low_confidence": True,
            "codex_role": "metadata_and_technical_assistance_only",
        },
        "runtime_bindings": runtime_bindings,
        "bindings": {
            "manifest_sha256": _sha256(_canonical_json(manifest)),
            "inventory_sha256": manifest["inventory_sha256"],
            "window_sha256": _sha256(_canonical_json(window)),
            "state_sha256": _sha256(_canonical_json(state)),
            "health_sha256": _sha256(_canonical_json(health)),
            "release_decisions_sha256": _sha256(_canonical_json(release_decisions)),
            "operator_tool_sha256": tool_sha256,
        },
    }


def _read_wal(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if not isinstance(row, dict) or row.get("schema") != WAL_SCHEMA:
            raise CampaignStopped("wal_invalid")
        rows.append(row)
    return rows


def _append_wal(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def single_instance_lock(path: str | Path):
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    stream = lock_path.open("a+b")
    locked = False
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        yield
    except OSError as exc:
        raise CampaignStopped("runner_already_active") from exc
    finally:
        if locked:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def loopback_runtime_url(value: object) -> str:
    raw_url = str(value or "")
    try:
        parsed = urlparse(raw_url)
        valid = all(
            (
                parsed.scheme == "http",
                parsed.hostname in {"127.0.0.1", "localhost"},
                parsed.username is None,
                parsed.password is None,
                parsed.path in {"", "/"},
                not parsed.query,
                not parsed.fragment,
                "?" not in raw_url,
                "#" not in raw_url,
                parsed.port is None or 0 < parsed.port <= 65535,
            )
        )
    except ValueError:
        valid = False
    if not valid:
        raise CampaignStopped("non_loopback_runtime_url")
    return raw_url[:-1] if parsed.path == "/" else raw_url


def send_internal_rag_sse(
    base_url: str,
    service_token: str,
    question: str,
    *,
    post=None,
    timeout_seconds: float = 150.0,
) -> str:
    if not service_token:
        raise CampaignStopped("service_token_missing")
    normalized_url = loopback_runtime_url(base_url)
    part_ids = extract_explicit_codes(question)
    if len(part_ids) != 2 or any(
        not PART_CODE_RE.fullmatch(part_id) for part_id in part_ids
    ):
        raise CampaignStopped("operator_part_codes_invalid")
    if post is None:
        import requests

        post = requests.post
    from mech_chatbot.adapters.pilot_replay import iter_sse_events

    response = None
    try:
        response = post(
            normalized_url + "/chat/stream",
            headers={"X-RAG-Service-Token": service_token},
            json={
                "user_id": OPERATOR_USER_ID,
                "username": OPERATOR_USERNAME,
                "user_question": question,
                "current_part_ids": part_ids,
                "response_language": "vi",
            },
            stream=True,
            timeout=(10, timeout_seconds),
        )
        response.raise_for_status()
        trace_id = ""
        for event, payload in iter_sse_events(response):
            if event == "error":
                raise CampaignStopped("rag_stream_error")
            if event == "done" and payload.get("ok") is True:
                trace_id = str(payload.get("trace_id") or "")
        if not trace_id:
            raise CampaignStopped("rag_trace_missing")
        return trace_id
    finally:
        if response is not None:
            response.close()


def dispatch_due(
    public: dict,
    private: dict,
    wal_path: str | Path,
    now: datetime,
    send: Callable[[str, str], str],
    *,
    clock: Callable[[], datetime] | None = None,
) -> dict | None:
    if public.get("campaign_id") != private.get("campaign_id"):
        raise CampaignStopped("manifest_mismatch")
    path = Path(wal_path)
    rows = _read_wal(path)
    if any(row.get("event") in {"attempt_failed", "attempt_ambiguous"} for row in rows):
        raise CampaignStopped("ambiguous_attempt")
    terminal_ids = {
        row["card_id"]
        for row in rows
        if row.get("event") in {"attempt_completed", "attempt_failed", "attempt_ambiguous"}
    }
    started_ids = {
        row["card_id"] for row in rows if row.get("event") == "attempt_started"
    }
    ambiguous = started_ids - terminal_ids
    if ambiguous:
        raise CampaignStopped("ambiguous_attempt")

    attempt_times = [
        parse_timestamp(row["ts"])
        for row in rows
        if row.get("event") == "attempt_started"
    ]
    current = now.astimezone(timezone.utc)
    if attempt_times and current < max(attempt_times) + CAMPAIGN_CADENCE:
        return None

    private_by_id = {card["card_id"]: card for card in private["cards"]}
    due = next(
        (
            card
            for card in public["cards"]
            if card["card_id"] not in terminal_ids
            and parse_timestamp(card["scheduled_at"]) <= current
        ),
        None,
    )
    if due is None:
        return None
    private_card = private_by_id.get(due["card_id"])
    if not private_card or private_card.get("prompt_sha256") != due.get("prompt_sha256"):
        raise CampaignStopped("private_manifest_mismatch")

    timestamp = _format_timestamp(current)
    _append_wal(
        path,
        {
            "schema": WAL_SCHEMA,
            "event": "attempt_started",
            "card_id": due["card_id"],
            "ts": timestamp,
            "prompt_sha256": due["prompt_sha256"],
        },
    )
    try:
        trace_id = send(private_card["question"], due["card_id"])
    except Exception:
        terminal_time = (clock or (lambda: datetime.now(timezone.utc)))().astimezone(
            timezone.utc
        )
        _append_wal(
            path,
            {
                "schema": WAL_SCHEMA,
                "event": "attempt_ambiguous",
                "card_id": due["card_id"],
                "ts": _format_timestamp(max(current, terminal_time)),
                "prompt_sha256": due["prompt_sha256"],
            },
        )
        raise CampaignStopped("ambiguous_attempt") from None
    terminal_time = (clock or (lambda: datetime.now(timezone.utc)))().astimezone(
        timezone.utc
    )
    _append_wal(
        path,
        {
            "schema": WAL_SCHEMA,
            "event": "attempt_completed",
            "card_id": due["card_id"],
            "ts": _format_timestamp(max(current, terminal_time)),
            "prompt_sha256": due["prompt_sha256"],
            "trace_id_sha256": _sha256(trace_id),
        },
    )
    return {"card_id": due["card_id"], "status": "completed"}
