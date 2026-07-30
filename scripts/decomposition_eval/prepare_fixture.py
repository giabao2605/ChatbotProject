"""Prepare only the additive SQL BOM rows needed by decomposition-eval-v1."""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import text

from scripts.crag_eval.constants import FIXTURE_BATCH, FIXTURE_COLLECTION
from scripts.decomposition_eval.constants import BOM_DOCUMENT, BOM_ROWS, DEFAULT_OUTPUT, LIVE_OPT_IN
from scripts.decomposition_eval.generate_manifest import generate_manifest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mech_chatbot.composition.maintenance_runtime import with_configured_repository_runtime


def _missing_bom_rows(existing):
    expected = {row["row_key"]: row for row in BOM_ROWS}
    expected_parts = {row["part"]: row["row_key"] for row in BOM_ROWS}
    actual = {}
    for row in existing:
        try:
            row_key = str(
                json.loads(row.get("RawRowJson") or "{}").get("row_key") or ""
            )
        except (TypeError, json.JSONDecodeError):
            row_key = ""
        if row_key not in expected:
            expected_row_key = expected_parts.get(row.get("MaHang"))
            if expected_row_key:
                raise RuntimeError(
                    f"unkeyed decomposition BOM row: {expected_row_key}"
                )
            continue
        if row_key in actual:
            raise RuntimeError(f"duplicate decomposition BOM row: {row_key}")
        actual[row_key] = row
    missing = []
    for row_key, wanted in expected.items():
        current = actual.get(row_key)
        if current is None:
            missing.append(wanted)
            continue
        try:
            quantity_matches = Decimal(str(current.get("SoLuong"))) == Decimal(
                wanted["value"]
            )
        except (InvalidOperation, TypeError, ValueError):
            quantity_matches = False
        try:
            index_matches = int(current.get("SourceTableIndex") or 0) == wanted[
                "source_table_index"
            ]
        except (TypeError, ValueError):
            index_matches = False
        if not all((
            current.get("MaHang") == wanted["part"],
            quantity_matches,
            current.get("Unit") == wanted["unit"],
            index_matches,
        )):
            raise RuntimeError(f"conflicting decomposition BOM row: {row_key}")
    return missing


@with_configured_repository_runtime(include_qdrant=True)
def prepare_fixture(output: Path = DEFAULT_OUTPUT) -> dict:
    if os.getenv(LIVE_OPT_IN) != "1":
        raise RuntimeError(f"set {LIVE_OPT_IN}=1 before writing the decomposition fixture")
    if os.getenv("RUN_CRAG_EVAL_FIXTURE") != "1":
        raise RuntimeError("set RUN_CRAG_EVAL_FIXTURE=1 to prepare the shared CRAG fixture")
    from mech_chatbot.config.repository_runtime import current_qdrant_runtime
    _client, collection = current_qdrant_runtime()
    if collection != FIXTURE_COLLECTION:
        raise RuntimeError(f"QDRANT_COLLECTION must equal {FIXTURE_COLLECTION}")
    generate_manifest(output)
    from mech_chatbot.db.engine import _ensure_engine, engine
    _ensure_engine()
    with engine.begin() as connection:
        doc_id = connection.execute(text("""
            SELECT TOP 1 DocID FROM dbo.TaiLieu
            WHERE SourceSystem=:source AND TenFile=:filename
            ORDER BY DocID DESC
        """), {"source": FIXTURE_BATCH, "filename": BOM_DOCUMENT}).scalar_one()
        existing = connection.execute(text("""
            SELECT MaHang, SoLuong, Unit, SourceTableIndex, RawRowJson
            FROM dbo.BangKeVatTu
            WHERE DocID=:doc_id
        """), {"doc_id": doc_id}).mappings().all()
        missing = _missing_bom_rows(existing)
        if missing:
            connection.execute(text("""
                INSERT INTO dbo.BangKeVatTu
                    (DocID, TrangSo, MaHang, TenVatTu, SoLuong, Unit, Confidence,
                     RawRowJson, SourceTableIndex)
                VALUES (:doc_id, 1, :part, :part, :value, :unit, 1.0, :raw, :row_index)
            """), [{
            "doc_id": int(doc_id), "part": row["part"], "value": row["value"],
            "unit": row["unit"], "row_index": row["source_table_index"],
            "raw": json.dumps(row, ensure_ascii=False, sort_keys=True),
            } for row in missing])
    return {
        "schema": "decomposition-fixture-prepare-v1", "batch": FIXTURE_BATCH,
        "collection": FIXTURE_COLLECTION, "bom_doc_id": int(doc_id),
        "bom_rows": len(BOM_ROWS), "bom_rows_inserted": len(missing),
        "shared_fixture": {"mode": "preexisting_required"},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(prepare_fixture(args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
