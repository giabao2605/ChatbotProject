from __future__ import annotations

import json

import pytest

from scripts.controlled_demo_eval.repair_structured_bom import build_repair_plan


def _record(source_row_id: str):
    return {
        "source_row_id": source_row_id,
        "raw_row_json": json.dumps({"source_row_id": source_row_id}),
    }


def test_repair_plan_inserts_only_when_target_has_no_rows():
    plan = build_repair_plan([_record("row-a"), _record("row-b")], [])

    assert plan == {
        "action": "insert",
        "source_row_ids": ["row-a", "row-b"],
        "existing_source_row_ids": [],
    }


def test_repair_plan_is_idempotent_for_same_source_rows():
    existing = [
        {"RawRowJson": json.dumps({"source_row_id": "row-b"})},
        {"RawRowJson": json.dumps({"source_row_id": "row-a"})},
    ]

    plan = build_repair_plan([_record("row-a"), _record("row-b")], existing)

    assert plan["action"] == "already_present"


def test_repair_plan_rejects_partial_or_different_existing_rows():
    existing = [{"RawRowJson": json.dumps({"source_row_id": "row-a"})}]

    with pytest.raises(ValueError, match="existing BOM rows differ"):
        build_repair_plan([_record("row-a"), _record("row-b")], existing)
