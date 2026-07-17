from __future__ import annotations

import json
import hashlib

import pytest

from scripts.controlled_demo_eval.repair_structured_bom import (
    build_repair_plan,
    validate_database_identity,
    validate_document_identity,
)


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
        "ma_hang": source_row_id,
        "ten_vat_tu": f"Part {source_row_id}",
        "vat_lieu": "steel",
        "so_luong": int(quantity),
        "ghi_chu": "demo",
        "don_vi": unit,
        "source_table_index": 1,
        "raw_row_json": json.dumps(raw_row),
    }


def _existing(
    source_row_id: str,
    *,
    quantity: str = "5",
    unit: str = "piece",
    sql_quantity: int | None = None,
    sql_unit: str | None = None,
    sql_part_code: str | None = None,
):
    return {
        "MaHang": sql_part_code or source_row_id,
        "TenVatTu": f"Part {source_row_id}",
        "VatLieu": "steel",
        "SoLuong": int(quantity) if sql_quantity is None else sql_quantity,
        "GhiChu": "demo",
        "Unit": unit if sql_unit is None else sql_unit,
        "SourceTableIndex": 1,
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


@pytest.mark.parametrize(
    "existing",
    [
        _existing("row-a", sql_quantity=999),
        _existing("row-a", sql_unit="kg"),
        _existing("row-a", sql_part_code="WRONG"),
    ],
)
def test_repair_plan_rejects_mismatched_runtime_database_columns(existing):
    with pytest.raises(ValueError, match="database columns differ"):
        build_repair_plan([_record("row-a")], [existing])


def test_document_identity_requires_declared_path_and_content_hash(tmp_path):
    document = tmp_path / "Technical" / "technical_demo_process_v2.md"
    document.parent.mkdir()
    document.write_text("approved BOM", encoding="utf-8")
    content_sha256 = hashlib.sha256(document.read_bytes()).hexdigest()
    aliases = tmp_path / "fixture_aliases.json"
    aliases.write_text(json.dumps({
        "technical-process-v2": {
            "document": document.name,
            "path": "Technical/technical_demo_process_v2.md",
            "content_sha256": content_sha256,
            "version": 2,
            "lifecycle_status": "published",
            "review_status": "approved",
            "publication_state": "published",
            "is_current": True,
            "servable": True,
        }
    }), encoding="utf-8")

    identity = validate_document_identity(
        document, aliases, actual_sha256=content_sha256,
    )

    assert identity["alias"] == "technical-process-v2"
    assert identity["content_sha256"] == content_sha256

    same_name_elsewhere = tmp_path / "Other" / document.name
    same_name_elsewhere.parent.mkdir()
    same_name_elsewhere.write_text("approved BOM", encoding="utf-8")
    with pytest.raises(ValueError, match="path does not match"):
        validate_document_identity(
            same_name_elsewhere, aliases, actual_sha256=content_sha256,
        )

    with pytest.raises(ValueError, match="content SHA-256 does not match"):
        validate_document_identity(
            document, aliases, actual_sha256="0" * 64,
        )

    database_document = {
        "TenFile": document.name,
        "VersionNo": 2,
        "LifecycleStatus": "published",
        "ReviewStatus": "approved",
        "PublicationState": "published",
        "IsCurrent": True,
        "Servable": True,
    }
    validate_database_identity(database_document, identity)

    database_document["ReviewStatus"] = "pending"
    with pytest.raises(ValueError, match="metadata does not match"):
        validate_database_identity(database_document, identity)
