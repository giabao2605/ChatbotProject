from __future__ import annotations

import json

import pytest

from scripts.controlled_demo_eval.repair_structured_bom import build_repair_plan


def _record(source_row_id: str, *, quantity: str = "5", unit: str = "piece"):
    raw_row = {
        "source_row_id": source_row_id,
        "quantity_decimal": quantity,
        "cells": [source_row_id, quantity, unit],
        "source_table_index": 1,
        "source_row_index": 1,
    }
    return {
        "source_row_id": source_row_id,
        "raw_row_json": json.dumps(raw_row),
    }


def _existing(source_row_id: str, *, quantity: str = "5", unit: str = "piece"):
    return {
        "RawRowJson": json.dumps({
            "source_row_id": source_row_id,
            "quantity_decimal": quantity,
            "cells": [source_row_id, quantity, unit],
            "source_table_index": 1,
            "source_row_index": 1,
        })
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
        _existing("row-b"),
        _existing("row-a"),
    ]

    plan = build_repair_plan([_record("row-a"), _record("row-b")], existing)

    assert plan["action"] == "already_present"


def test_repair_plan_rejects_partial_or_different_existing_rows():
    existing = [_existing("row-a")]

    with pytest.raises(ValueError, match="existing BOM rows differ"):
        build_repair_plan([_record("row-a"), _record("row-b")], existing)


@pytest.mark.parametrize(
    "existing",
    [
        _existing("row-a", quantity="999"),
        _existing("row-a", unit="kg"),
    ],
)
def test_repair_plan_rejects_same_source_id_with_different_content(existing):
    with pytest.raises(ValueError, match="content differs"):
        build_repair_plan([_record("row-a")], [existing])
