from __future__ import annotations

from decimal import Decimal
import json

import pytest

from mech_chatbot.db.repositories import bom


pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, *, scalar_value=None, rows=None):
        self._scalar_value = scalar_value
        self._rows = rows or []

    def scalar(self):
        return self._scalar_value

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0
        self.params = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args, **_kwargs):
        self.calls += 1
        self.params.append(_args[1] if len(_args) > 1 else _kwargs.get("params", {}))
        if self.calls == 1:
            return _Result(scalar_value=0)
        return _Result(rows=self.rows)


class _Engine:
    def __init__(self, rows):
        self.connection = _Connection(rows)

    def connect(self):
        return self.connection


def test_bom_search_restores_exact_decimal_and_stable_source_row(monkeypatch):
    raw = json.dumps({
        "quantity_decimal": "12.50",
        "source_row_id": "TECH-BOM-003",
    })
    legacy_raw = json.dumps(["LEGACY-PART", "4"])
    rows = [
        (41, 1, "DEMO-PART-C", "Chi tiết C", "DEMO-MAT-RUBBER", None,
         None, "technical_demo_process_v2.md", 2, "internal", "HQ",
         "all_external", 301, "kg", raw),
        (41, 1, "LEGACY-PART", "Legacy", None, 4, None,
         "technical_demo_process_v2.md", 2, "internal", "HQ",
         "all_external", 302, "piece", legacy_raw),
    ]
    monkeypatch.setattr(bom, "_ensure_engine", lambda: None)
    monkeypatch.setattr(bom, "engine", _Engine(rows))

    results = bom.search_bom_by_code(
        ["DEMO-PART-C"],
        user_roles=["admin"],
        allowed_departments=["Technical"],
        allowed_sites=["HQ"],
        max_security_level="internal",
    )

    assert results[0][5] == Decimal("12.50")
    assert results[0][14] == "TECH-BOM-003"
    assert results[0].quantity == Decimal("12.50")
    assert results[0].source_row_id == "TECH-BOM-003"
    assert results[0].unit == "kg"
    assert results[1][5] == Decimal("4")
    assert results[1][14] == "BOM-302"


def test_bom_fact_search_supports_document_scope_without_part_code(monkeypatch):
    raw = json.dumps({
        "quantity_decimal": "12.50",
        "source_row_id": "TECH-BOM-003",
    })
    rows = [
        (41, 1, "DEMO-PART-C", "Chi tiết C", "DEMO-MAT-RUBBER", None,
         None, "technical_demo_process_v2.md", 2, "internal", "HQ",
         "all_external", 301, "kg", raw),
    ]
    fake_engine = _Engine(rows)
    monkeypatch.setattr(bom, "_ensure_engine", lambda: None)
    monkeypatch.setattr(bom, "engine", fake_engine)

    results = bom.search_bom_facts(
        document_ids=[41],
        user_roles=["admin"],
        allowed_departments=["Technical"],
        allowed_sites=["HQ"],
        max_security_level="internal",
    )

    assert [row.source_row_id for row in results] == ["TECH-BOM-003"]
    assert results[0].quantity == Decimal("12.50")
    assert json.loads(fake_engine.connection.params[-1]["document_ids"]) == [41]


def test_bom_search_removes_only_one_to_one_cross_extractor_shadow(monkeypatch):
    def row(row_id, code, quantity, raw, source_table_index=None):
        if source_table_index is None:
            source_table_index = 1 if raw.get("source_row_id") else 0
        return (
            137, 1, code, None, "Inox 304", quantity, None,
            "assembly.pdf", 1, "internal", "HQ", "internal_only",
            row_id, None, json.dumps(raw, ensure_ascii=False), source_table_index,
        )

    rows = [
        row(193, "PART-A", 2, {
            "stt": "C5", "ma_hang": "PART-A", "vat_tu": "Khung dưới",
            "vat_lieu": "Inox 304", "sl": "2", "ghi_chu": "",
        }),
        row(197, "PART-A", 2, {
            "cells": ["", "C5", "PART-A", "Khung dưới", "Inox 304", "2"],
            "source_row_id": "table-1-row-2",
        }),
        row(201, "PART-B", 1, {
            "cells": ["", "C6", "PART-B", "Chân trái", "Inox 304", "1"],
            "source_row_id": "table-1-row-3",
        }),
        row(202, "PART-B", 1, {
            "cells": ["", "C7", "PART-B", "Chân phải", "Inox 304", "1"],
            "source_row_id": "table-1-row-4",
        }),
        row(203, "PART-C", 1, {
            "stt": "C8", "ma_hang": "PART-C", "vat_tu": "Giằng",
            "vat_lieu": "Inox 304", "sl": "1", "ghi_chu": "",
        }),
        row(204, "PART-C", 2, {
            "cells": ["", "C8", "PART-C", "Giằng", "Inox 304", "2"],
            "source_row_id": "table-1-row-5",
        }),
        row(205, "PART-D", 1, {
            "stt": "C9", "ma_hang": "PART-D", "vat_tu": "Nẹp",
            "vat_lieu": "Inox 304", "sl": "1", "ghi_chu": "",
        }),
        row(206, "PART-D", 1, {
            "stt": "C9", "ma_hang": "PART-D", "vat_tu": "Nẹp",
            "vat_lieu": "Inox 304", "sl": "1", "ghi_chu": "",
        }),
        row(207, "PART-D", 1, {
            "cells": ["", "C9", "PART-D", "Nẹp", "Inox 304", "1"],
            "source_row_id": "table-1-row-6",
        }),
        row(208, "PART-E", 1, {
            "stt": "C10", "ma_hang": "PART-E", "vat_tu": "Chân trái",
            "vat_lieu": "Inox 304", "sl": "1", "ghi_chu": "",
        }),
        row(209, "PART-E", 1, {
            "cells": [
                "", "C10", "PART-E", "Chân trái", "Chân phải", "Inox 304", "1",
            ],
            "source_row_id": "table-1-row-7",
        }),
        row(210, "PART-F", 1, {
            "stt": "C11", "ma_hang": "PART-F", "vat_tu": "Giá đỡ",
            "vat_lieu": "Inox 304", "sl": "1", "ghi_chu": "",
        }),
        row(211, "PART-F", 1, {
            "cells": ["", "C11", "PART-F", "Giá đỡ", "Inox 304", "1"],
            "source_row_id": "FORGED-BY-MODEL",
        }, source_table_index=0),
    ]
    monkeypatch.setattr(bom, "_ensure_engine", lambda: None)
    monkeypatch.setattr(bom, "engine", _Engine(rows))

    results = bom.search_bom_facts(part_codes=["PART"], user_roles=["admin"])

    assert [item.source_row_id for item in results] == [
        "table-1-row-2",
        "table-1-row-3", "table-1-row-4",
        "BOM-203", "table-1-row-5",
        "BOM-205", "BOM-206", "table-1-row-6",
        "BOM-208", "table-1-row-7",
        "BOM-210", "FORGED-BY-MODEL",
    ]
