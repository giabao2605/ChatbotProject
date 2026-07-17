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

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args, **_kwargs):
        self.calls += 1
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
