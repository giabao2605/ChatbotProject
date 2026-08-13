from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mech_chatbot.rag.entity_resolver import extract_explicit_codes
from mech_chatbot.rag.grounded_math import detect_calculation_operation
from scripts.ops import grounded_math_operator_campaign as campaign


def _inventory(document_count: int = 12) -> list[dict]:
    return [
        {
            "doc_id": index,
            "file_name": f"drawing-{index}.pdf",
            "version": 1,
            "department": "Technical",
            "site": "PHONG_KY_THUAT",
            "operand_labels": [
                f"PART-{index}-A",
                f"PART-{index}-B",
                f"PART-{index}-C",
                f"Description {index} A",
                f"Description {index} B",
            ],
            "operand_styles": {
                f"PART-{index}-A": "part_code",
                f"PART-{index}-B": "part_code",
                f"PART-{index}-C": "part_code",
                f"Description {index} A": "description",
                f"Description {index} B": "description",
            },
        }
        for index in range(1, document_count + 1)
    ]


def test_build_campaign_freezes_100_private_prompts_and_hash_only_public_cards():
    started = datetime(2026, 8, 12, tzinfo=timezone.utc)

    public, private = campaign.build_campaign_cards(_inventory(), started)

    assert public["schema"] == "grounded-math-operator-campaign-v1"
    assert public["traffic_class"] == "owner_authorized_operator_generated"
    assert public["transport"] == "internal_rag_sse"
    assert len(public["cards"]) == len(private["cards"]) == 100
    assert public["campaign_id"] == private["campaign_id"]
    assert public["minimum_runtime_until"] == "2026-08-19T00:00:00Z"
    assert public["cards"][0]["operation"] == "add"
    assert public["cards"][0]["operand_count"] == 2

    card_ids = [card["card_id"] for card in public["cards"]]
    assert len(card_ids) == len(set(card_ids)) == 100
    schedules = [campaign.parse_timestamp(card["scheduled_at"]) for card in public["cards"]]
    assert schedules[0] == started
    assert schedules[-1] - schedules[0] == timedelta(days=7)

    per_document = Counter(card["document_identity_sha256"] for card in public["cards"])
    per_document_operation = Counter(
        (card["document_identity_sha256"], card["operation"])
        for card in public["cards"]
    )
    assert max(per_document.values()) <= 15
    assert max(per_document_operation.values()) <= 3
    assert all(
        count == 1
        for (document_hash, operation), count in per_document_operation.items()
        if operation == "sum"
    )
    assert {card["operation"] for card in public["cards"]} == {
        "sum",
        "add",
        "subtract",
        "ratio",
        "percent",
        "multiply",
    }
    assert {card["operand_style"] for card in public["cards"]} == {
        "document_aggregate",
        "part_code",
        "description",
    }
    preflight = public["preflight"]
    assert preflight["schema"] == "grounded-math-operator-preflight-v1"
    assert preflight["generated"] == preflight["accepted"]
    assert preflight["rejected_by_reason"] == {}
    assert {row["operation"] for row in preflight["by_operation"]} == set(
        campaign.OPERATIONS
    )
    assert {row["operand_style"] for row in preflight["by_operand_style"]} == {
        "document_aggregate",
        "part_code",
        "description",
    }
    assert all(row["accepted"] > 0 for row in preflight["by_operation"])
    assert all(row["accepted"] > 0 for row in preflight["by_operand_style"])

    public_text = json.dumps(public, ensure_ascii=False)
    assert "drawing-" not in public_text
    assert "PART-" not in public_text
    assert "question" not in public_text
    assert "quantity" not in public_text
    assert "answer" not in public_text

    private_by_id = {card["card_id"]: card for card in private["cards"]}
    for card in public["cards"]:
        assert card["operand_count"] in {0, 1, 2}
        private_card = private_by_id[card["card_id"]]
        assert private_card["prompt_sha256"] == card["prompt_sha256"]
        assert hashlib.sha256(private_card["question"].encode()).hexdigest() == card[
            "prompt_sha256"
        ]
        assert detect_calculation_operation(private_card["question"]) == card["operation"]
        assert extract_explicit_codes(private_card["question"])


def test_build_campaign_rejects_cards_that_production_intent_cannot_anchor():
    inventory = _inventory()
    for document in inventory:
        document["file_name"] = "narrative.pdf"
        document["operand_labels"] = ["Chi tiet A", "Chi tiet B", "Chi tiet C"]
        document["operand_styles"] = {
            "Chi tiet A": "description",
            "Chi tiet B": "description",
            "Chi tiet C": "description",
        }

    with pytest.raises(ValueError, match="insufficient_unique_cards"):
        campaign.build_campaign_cards(
            inventory,
            datetime(2026, 8, 12, tzinfo=timezone.utc),
        )


def test_build_campaign_rejects_inventory_that_cannot_supply_100_unique_cards():
    with pytest.raises(ValueError, match="insufficient_unique_cards"):
        campaign.build_campaign_cards(
            _inventory(document_count=1),
            datetime(2026, 8, 12, tzinfo=timezone.utc),
        )


def test_dispatch_due_is_at_most_once_and_wal_contains_no_prompt_or_raw_trace(tmp_path):
    started = datetime(2026, 8, 12, tzinfo=timezone.utc)
    public, private = campaign.build_campaign_cards(_inventory(), started)
    wal_path = tmp_path / "campaign.wal.jsonl"
    sent: list[str] = []

    def send(question: str, card_id: str) -> str:
        sent.append(question)
        return f"raw-sensitive-trace-{card_id}"

    result = campaign.dispatch_due(public, private, wal_path, started, send)

    assert result == {"card_id": "card-001", "status": "completed"}
    assert len(sent) == 1
    assert campaign.dispatch_due(public, private, wal_path, started, send) is None
    assert len(sent) == 1

    rows = [json.loads(line) for line in wal_path.read_text().splitlines()]
    assert [row["event"] for row in rows] == ["attempt_started", "attempt_completed"]
    rendered = json.dumps(rows, ensure_ascii=False)
    assert sent[0] not in rendered
    assert "raw-sensitive-trace" not in rendered
    assert rows[-1]["trace_id_sha256"] == hashlib.sha256(
        "raw-sensitive-trace-card-001".encode()
    ).hexdigest()


def test_dispatch_due_records_the_actual_completion_time(tmp_path):
    started = datetime(2026, 8, 12, tzinfo=timezone.utc)
    completed = started + timedelta(seconds=3)
    public, private = campaign.build_campaign_cards(_inventory(), started)
    wal_path = tmp_path / "campaign.wal.jsonl"

    campaign.dispatch_due(
        public,
        private,
        wal_path,
        started,
        lambda *_: "trace",
        clock=lambda: completed,
    )

    rows = [json.loads(line) for line in wal_path.read_text().splitlines()]
    assert rows[0]["ts"] == "2026-08-12T00:00:00Z"
    assert rows[1]["ts"] == "2026-08-12T00:00:03Z"


def test_dispatch_due_never_retries_an_ambiguous_started_attempt(tmp_path):
    started = datetime(2026, 8, 12, tzinfo=timezone.utc)
    public, private = campaign.build_campaign_cards(_inventory(), started)
    wal_path = tmp_path / "campaign.wal.jsonl"
    wal_path.write_text(
        json.dumps(
            {
                "schema": "grounded-math-operator-wal-v1",
                "event": "attempt_started",
                "card_id": "card-001",
                "ts": "2026-08-12T00:00:00Z",
                "prompt_sha256": public["cards"][0]["prompt_sha256"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(campaign.CampaignStopped, match="ambiguous_attempt"):
        campaign.dispatch_due(
            public,
            private,
            wal_path,
            started + timedelta(hours=2),
            lambda _question, _card_id: "must-not-send",
        )


def test_dispatch_due_does_not_catch_up_faster_than_the_frozen_cadence(tmp_path):
    started = datetime(2026, 8, 12, tzinfo=timezone.utc)
    public, private = campaign.build_campaign_cards(_inventory(), started)
    wal_path = tmp_path / "campaign.wal.jsonl"

    assert campaign.dispatch_due(public, private, wal_path, started, lambda *_: "trace-1")
    too_soon = started + campaign.CAMPAIGN_CADENCE - timedelta(seconds=1)
    assert campaign.dispatch_due(public, private, wal_path, too_soon, lambda *_: "trace-2") is None

    result = campaign.dispatch_due(
        public,
        private,
        wal_path,
        started + campaign.CAMPAIGN_CADENCE,
        lambda *_: "trace-2",
    )
    assert result == {"card_id": "card-002", "status": "completed"}


def test_dispatch_exception_is_recorded_as_ambiguous_and_stops_campaign(tmp_path):
    started = datetime(2026, 8, 12, tzinfo=timezone.utc)
    public, private = campaign.build_campaign_cards(_inventory(), started)
    wal_path = tmp_path / "campaign.wal.jsonl"

    with pytest.raises(campaign.CampaignStopped, match="ambiguous_attempt"):
        campaign.dispatch_due(
            public,
            private,
            wal_path,
            started,
            lambda *_: (_ for _ in ()).throw(ConnectionError("sensitive detail")),
        )

    rendered = wal_path.read_text(encoding="utf-8")
    assert "sensitive detail" not in rendered
    assert [json.loads(line)["event"] for line in rendered.splitlines()] == [
        "attempt_started",
        "attempt_ambiguous",
    ]
    with pytest.raises(campaign.CampaignStopped, match="ambiguous_attempt"):
        campaign.dispatch_due(public, private, wal_path, started, lambda *_: "must-not-send")


def test_send_internal_rag_sse_returns_only_done_trace_and_uses_fixed_owner_actor():
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def iter_lines(self, decode_unicode=True):
            assert decode_unicode is True
            yield 'event: token'
            yield 'data: {"text":"sensitive answer"}'
            yield ''
            yield 'event: done'
            yield 'data: {"ok":true,"trace_id":"raw-trace-id"}'
            yield ''

        def close(self):
            captured["closed"] = True

    def post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return Response()

    trace = campaign.send_internal_rag_sse(
        "http://127.0.0.1:8200", "secret-token", "private prompt", post=post
    )

    assert trace == "raw-trace-id"
    assert captured["url"] == "http://127.0.0.1:8200/chat/stream"
    assert captured["headers"] == {"X-RAG-Service-Token": "secret-token"}
    assert captured["json"] == {
        "user_id": 81,
        "username": "admin_bao",
        "user_question": "private prompt",
        "response_language": "vi",
    }
    assert captured["stream"] is True
    assert captured["closed"] is True


def test_send_internal_rag_sse_rejects_non_loopback_before_exposing_token():
    with pytest.raises(campaign.CampaignStopped, match="non_loopback_runtime_url"):
        campaign.send_internal_rag_sse(
            "https://example.com",
            "secret-token",
            "private prompt",
            post=lambda *_args, **_kwargs: pytest.fail("network must not run"),
        )


def test_inventory_query_never_reads_quantities_answers_or_raw_rows():
    lowered = campaign.INVENTORY_SQL.lower()

    assert "select" in lowered
    for forbidden in ("soluong", "unit", "rawrowjson", "answer", "formula"):
        assert forbidden not in lowered
    assert "tenfile like '%.pdf'" in lowered


def test_single_instance_lock_rejects_a_second_runner(tmp_path):
    lock_path = tmp_path / "campaign.lock"

    with campaign.single_instance_lock(lock_path):
        with pytest.raises(campaign.CampaignStopped, match="runner_already_active"):
            with campaign.single_instance_lock(lock_path):
                pass

    assert Path(lock_path).exists()


def test_inventory_rows_are_aggregated_without_copying_source_values():
    rows = [
        {
            "DocID": 7,
            "TenFile": "drawing.pdf",
            "VersionNo": 3,
            "OwnerDepartment": "Technical",
            "Site": "PHONG_KY_THUAT",
            "MaHang": "PART-A",
            "TenVatTu": "Part A",
            "SoLuong": 999,
        },
        {
            "DocID": 7,
            "TenFile": "drawing.pdf",
            "VersionNo": 3,
            "OwnerDepartment": "Technical",
            "Site": "PHONG_KY_THUAT",
            "MaHang": "PART-A",
            "TenVatTu": "Part A",
            "SoLuong": 1000,
        },
        {
            "DocID": 7,
            "TenFile": "drawing.pdf",
            "VersionNo": 3,
            "OwnerDepartment": "Technical",
            "Site": "PHONG_KY_THUAT",
            "MaHang": None,
            "TenVatTu": "Part B",
            "SoLuong": 1001,
        },
    ]

    assert campaign.inventory_from_rows(rows) == [
        {
            "doc_id": 7,
            "file_name": "drawing.pdf",
            "version": 3,
            "department": "Technical",
            "site": "PHONG_KY_THUAT",
            "operand_labels": ["PART-A", "Part A", "Part B"],
            "operand_styles": {
                "PART-A": "part_code",
                "Part A": "description",
                "Part B": "description",
            },
        }
    ]
    assert "999" not in json.dumps(campaign.inventory_from_rows(rows))


def test_owner_declaration_binds_frozen_artifacts_and_disallows_claim_inflation():
    started = datetime(2026, 8, 12, tzinfo=timezone.utc)
    manifest, _private = campaign.build_campaign_cards(_inventory(), started)
    window = {"expected_runtime": {"pilot": {"git_sha": "7" * 40}, "main": {}}}
    state = {"activation_scope": "controlled_demo", "expected_runtime": window["expected_runtime"]}
    health = {"pilot": {"status": "ok"}, "main": {"status": "ok"}}
    decisions = {"status": "incomplete"}

    declaration = campaign.build_owner_declaration(
        manifest,
        window,
        state,
        health,
        decisions,
        approved_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
        tool_sha256="a" * 64,
    )

    assert declaration["owner"] == "bao.nguyen"
    assert declaration["actor"] == {"user_id": 81, "username": "admin_bao"}
    assert declaration["count_toward_pilot"] is True
    assert declaration["organic_claim_allowed"] is False
    assert declaration["quality_claim_allowed"] is False
    assert declaration["ui_parity_claim_allowed"] is False
    assert declaration["default_rollout_authorized"] is False
    assert declaration["bindings"]["manifest_sha256"] == hashlib.sha256(
        campaign.canonical_json(manifest)
    ).hexdigest()
    assert declaration["runtime_bindings"] == window["expected_runtime"]
    assert declaration["unavailable_operations"] == {
        "divide": "corpus_missing_dimensionless_divisor"
    }
    assert "drawing-" not in json.dumps(declaration)
