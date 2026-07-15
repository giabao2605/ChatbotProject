"""Prepare only the additive SQL BOM rows needed by decomposition-eval-v1."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import text

from scripts.crag_eval.constants import FIXTURE_BATCH, FIXTURE_COLLECTION
from scripts.crag_eval.ingest_fixture import ingest_fixture
from scripts.decomposition_eval.constants import BOM_DOCUMENT, BOM_ROWS, DEFAULT_OUTPUT, LIVE_OPT_IN
from scripts.decomposition_eval.generate_manifest import generate_manifest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def prepare_fixture(output: Path = DEFAULT_OUTPUT) -> dict:
    if os.getenv(LIVE_OPT_IN) != "1":
        raise RuntimeError(f"set {LIVE_OPT_IN}=1 before writing the decomposition fixture")
    if os.getenv("RUN_CRAG_EVAL_FIXTURE") != "1":
        raise RuntimeError("set RUN_CRAG_EVAL_FIXTURE=1 to prepare the shared CRAG fixture")
    from mech_chatbot.config.settings import QDRANT_COLLECTION
    if QDRANT_COLLECTION != FIXTURE_COLLECTION:
        raise RuntimeError(f"QDRANT_COLLECTION must equal {FIXTURE_COLLECTION}")
    generate_manifest(output)
    ingest_report = ingest_fixture()
    from mech_chatbot.db.engine import _ensure_engine, engine
    _ensure_engine()
    with engine.begin() as connection:
        doc_id = connection.execute(text("""
            SELECT TOP 1 DocID FROM dbo.TaiLieu
            WHERE SourceSystem=:source AND TenFile=:filename
            ORDER BY DocID DESC
        """), {"source": FIXTURE_BATCH, "filename": BOM_DOCUMENT}).scalar_one()
        connection.execute(text("DELETE FROM dbo.BangKeVatTu WHERE DocID=:doc_id"), {"doc_id": doc_id})
        connection.execute(text("""
            INSERT INTO dbo.BangKeVatTu
                (DocID, TrangSo, MaHang, TenVatTu, SoLuong, Unit, Confidence,
                 RawRowJson, SourceTableIndex)
            VALUES (:doc_id, 1, :part, :part, :value, :unit, 1.0, :raw, :row_index)
        """), [{
            "doc_id": int(doc_id), "part": row["part"], "value": row["value"],
            "unit": row["unit"], "row_index": row["source_table_index"],
            "raw": json.dumps(row, ensure_ascii=False, sort_keys=True),
        } for row in BOM_ROWS])
    return {
        "schema": "decomposition-fixture-prepare-v1", "batch": FIXTURE_BATCH,
        "collection": FIXTURE_COLLECTION, "bom_doc_id": int(doc_id),
        "bom_rows": len(BOM_ROWS), "shared_fixture": ingest_report,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(prepare_fixture(args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
