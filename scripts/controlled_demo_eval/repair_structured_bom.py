"""Repair missing Markdown BOM rows for one controlled-demo document.

The command is preview-only by default. ``--execute`` requires the explicit
controlled-demo opt-in and only inserts rows when the target document has no
structured BOM rows yet. Existing differing rows are never overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from scripts.crag_eval.constants import ROOT

SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _structured_row(raw_row: dict, *, payload_key: str) -> dict:
    payload = raw_row.get(payload_key)
    if not payload:
        return {}
    try:
        value = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def build_repair_plan(records, existing_rows) -> dict:
    source_row_ids = [str(record.get("source_row_id") or "").strip() for record in records]
    if not source_row_ids or any(not value for value in source_row_ids):
        raise ValueError("every extracted BOM row must have a source_row_id")
    if len(set(source_row_ids)) != len(source_row_ids):
        raise ValueError("extracted BOM source_row_id values must be unique")
    extracted_by_id = {}
    for source_row_id, record in zip(source_row_ids, records):
        structured = _structured_row(record, payload_key="raw_row_json")
        if str(structured.get("source_row_id") or "").strip() != source_row_id:
            raise ValueError("extracted BOM row lacks matching raw provenance")
        extracted_by_id[source_row_id] = structured
    existing_by_id = {}
    for row in existing_rows:
        structured = _structured_row(row, payload_key="RawRowJson")
        source_row_id = str(structured.get("source_row_id") or "").strip()
        if source_row_id in existing_by_id:
            raise ValueError("existing BOM source_row_id values must be unique")
        existing_by_id[source_row_id] = structured
    existing_source_row_ids = list(existing_by_id)
    if any(not value for value in existing_source_row_ids):
        raise ValueError("existing BOM rows lack source row provenance")
    if not existing_source_row_ids:
        action = "insert"
    elif set(existing_source_row_ids) != set(source_row_ids):
        raise ValueError("existing BOM rows differ from extracted source rows")
    elif existing_by_id != extracted_by_id:
        raise ValueError("existing BOM row content differs from extracted source rows")
    else:
        action = "already_present"
    return {
        "action": action,
        "source_row_ids": source_row_ids,
        "existing_source_row_ids": existing_source_row_ids,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repair(document_path: Path, *, execute: bool) -> dict:
    if execute and os.getenv("CONTROLLED_DEMO_LIVE_OPT_IN") != "1":
        raise RuntimeError("set CONTROLLED_DEMO_LIVE_OPT_IN=1 before --execute")
    if document_path.suffix.casefold() not in {".md", ".markdown"}:
        raise ValueError("repair accepts only Markdown documents")
    from mech_chatbot.ingestion.pdf.bom import extract_bom_records_from_markdown

    records = extract_bom_records_from_markdown(document_path.read_text(encoding="utf-8"))
    if not records:
        raise ValueError("document contains no structured Markdown BOM")

    from sqlalchemy import text
    import mech_chatbot.db.engine as db_engine

    db_engine._ensure_engine()
    filename = document_path.name
    with db_engine.engine.connect() as connection:
        documents = connection.execute(text("""
            SELECT DocID, TenFile, VersionNo, LifecycleStatus, ReviewStatus,
                   PublicationState, IsCurrent, Servable
            FROM dbo.TaiLieu
            WHERE TenFile=:filename AND LifecycleStatus <> 'deleting'
        """), {"filename": filename}).mappings().all()
        if len(documents) != 1:
            raise ValueError(f"expected exactly one live document named {filename}")
        document = dict(documents[0])
        required = (
            str(document.get("LifecycleStatus") or "").casefold() == "published"
            and str(document.get("ReviewStatus") or "").casefold() == "approved"
            and str(document.get("PublicationState") or "").casefold() == "published"
            and bool(document.get("IsCurrent"))
            and bool(document.get("Servable"))
        )
        if not required:
            raise ValueError("target document is not current published approved servable")
        existing = connection.execute(text("""
            SELECT RawRowJson
            FROM dbo.BangKeVatTu
            WHERE DocID=:doc_id AND TrangSo=1
        """), {"doc_id": int(document["DocID"])}).mappings().all()
    plan = build_repair_plan(records, existing)
    if execute and plan["action"] == "insert":
        from mech_chatbot.db.repositories.bom import save_bom_records

        inserted = save_bom_records(int(document["DocID"]), 1, records)
        if inserted != len(records):
            raise RuntimeError(f"expected {len(records)} BOM rows, inserted {inserted}")
        with db_engine.engine.connect() as connection:
            existing = connection.execute(text("""
                SELECT RawRowJson FROM dbo.BangKeVatTu
                WHERE DocID=:doc_id AND TrangSo=1
            """), {"doc_id": int(document["DocID"])}).mappings().all()
        plan = build_repair_plan(records, existing)
        if plan["action"] != "already_present":
            raise RuntimeError("post-insert BOM verification did not become idempotent")
    return {
        "schema": "controlled-demo-structured-bom-repair-v1",
        "passed": True,
        "execute": execute,
        "document": document,
        "document_path": str(document_path.resolve()),
        "document_sha256": _sha256(document_path),
        "record_count": len(records),
        "plan": plan,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    try:
        report = repair(args.document, execute=args.execute)
    except (OSError, UnicodeDecodeError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
