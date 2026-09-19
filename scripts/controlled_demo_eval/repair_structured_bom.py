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


def _text_value(value) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _int_value(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _folded_text(value) -> str | None:
    normalized = _text_value(value)
    return normalized.casefold() if normalized is not None else None


def _runtime_columns(row: dict, *, extracted: bool) -> dict:
    keys = {
        "part_code": "ma_hang" if extracted else "MaHang",
        "description": "ten_vat_tu" if extracted else "TenVatTu",
        "material": "vat_lieu" if extracted else "VatLieu",
        "quantity": "so_luong" if extracted else "SoLuong",
        "note": "ghi_chu" if extracted else "GhiChu",
        "unit": "don_vi" if extracted else "Unit",
        "source_table_index": (
            "source_table_index" if extracted else "SourceTableIndex"
        ),
    }
    return {
        "part_code": _text_value(row.get(keys["part_code"])),
        "description": _text_value(row.get(keys["description"])),
        "material": _text_value(row.get(keys["material"])),
        "quantity": _int_value(row.get(keys["quantity"])),
        "note": _text_value(row.get(keys["note"])),
        "unit": _text_value(row.get(keys["unit"])),
        "source_table_index": _int_value(row.get(keys["source_table_index"])),
    }


def build_repair_plan(records, existing_rows) -> dict:
    source_row_ids = [str(record.get("source_row_id") or "").strip() for record in records]
    if not source_row_ids or any(not value for value in source_row_ids):
        raise ValueError("every extracted BOM row must have a source_row_id")
    if len(set(source_row_ids)) != len(source_row_ids):
        raise ValueError("extracted BOM source_row_id values must be unique")
    extracted_by_id = {}
    extracted_columns_by_id = {}
    for source_row_id, record in zip(source_row_ids, records):
        structured = _structured_row(record, payload_key="raw_row_json")
        if str(structured.get("source_row_id") or "").strip() != source_row_id:
            raise ValueError("extracted BOM row lacks matching raw provenance")
        extracted_by_id[source_row_id] = structured
        extracted_columns_by_id[source_row_id] = _runtime_columns(
            record, extracted=True,
        )
    existing_by_id = {}
    existing_columns_by_id = {}
    for row in existing_rows:
        structured = _structured_row(row, payload_key="RawRowJson")
        source_row_id = str(structured.get("source_row_id") or "").strip()
        if source_row_id in existing_by_id:
            raise ValueError("existing BOM source_row_id values must be unique")
        existing_by_id[source_row_id] = structured
        existing_columns_by_id[source_row_id] = _runtime_columns(
            row, extracted=False,
        )
    existing_source_row_ids = list(existing_by_id)
    if any(not value for value in existing_source_row_ids):
        raise ValueError("existing BOM rows lack source row provenance")
    if not existing_source_row_ids:
        action = "insert"
    elif set(existing_source_row_ids) != set(source_row_ids):
        raise ValueError("existing BOM rows differ from extracted source rows")
    elif existing_by_id != extracted_by_id:
        raise ValueError("existing BOM row content differs from extracted source rows")
    elif existing_columns_by_id != extracted_columns_by_id:
        raise ValueError("existing BOM database columns differ from extracted rows")
    else:
        action = "already_present"
    return {
        "action": action,
        "source_row_ids": source_row_ids,
        "existing_source_row_ids": existing_source_row_ids,
    }


def validate_document_identity(
    document_path: Path,
    fixture_aliases_path: Path,
    *,
    actual_sha256: str,
) -> dict:
    try:
        aliases_bytes = fixture_aliases_path.read_bytes()
        aliases = json.loads(aliases_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("fixture aliases cannot be read") from exc
    if not isinstance(aliases, dict):
        raise ValueError("fixture aliases must be a JSON object")
    matches = [
        (alias, identity)
        for alias, identity in aliases.items()
        if isinstance(identity, dict)
        and str(identity.get("document") or "").strip() == document_path.name
    ]
    if len(matches) != 1:
        raise ValueError("expected exactly one fixture identity for document")
    alias, identity = matches[0]
    declared_relative_path = str(identity.get("path") or "").strip()
    if not declared_relative_path:
        raise ValueError("fixture identity lacks document path")
    declared_path = (fixture_aliases_path.parent / declared_relative_path).resolve()
    if document_path.resolve() != declared_path:
        raise ValueError("document path does not match fixture identity")
    declared_sha256 = str(identity.get("content_sha256") or "").strip().lower()
    if (
        len(declared_sha256) != 64
        or any(character not in "0123456789abcdef" for character in declared_sha256)
    ):
        raise ValueError("fixture identity lacks valid content SHA-256")
    if actual_sha256.lower() != declared_sha256:
        raise ValueError("document content SHA-256 does not match fixture identity")
    return {
        "alias": str(alias),
        **identity,
        "content_sha256": declared_sha256,
        "declared_path": str(declared_path),
        "fixture_aliases_sha256": hashlib.sha256(aliases_bytes).hexdigest(),
    }


def validate_database_identity(document: dict, fixture_identity: dict) -> None:
    actual = {
        "document": _text_value(document.get("TenFile")),
        "version": _int_value(document.get("VersionNo")),
        "lifecycle_status": _folded_text(document.get("LifecycleStatus")),
        "review_status": _folded_text(document.get("ReviewStatus")),
        "publication_state": _folded_text(document.get("PublicationState")),
        "is_current": bool(document.get("IsCurrent")),
        "servable": bool(document.get("Servable")),
    }
    expected = {
        "document": _text_value(fixture_identity.get("document")),
        "version": _int_value(fixture_identity.get("version")),
        "lifecycle_status": _folded_text(fixture_identity.get("lifecycle_status")),
        "review_status": _folded_text(fixture_identity.get("review_status")),
        "publication_state": _folded_text(fixture_identity.get("publication_state")),
        "is_current": fixture_identity.get("is_current") is True,
        "servable": fixture_identity.get("servable") is True,
    }
    if actual != expected:
        raise ValueError("database document metadata does not match fixture identity")
    required_state = {
        "lifecycle_status": "published",
        "review_status": "approved",
        "publication_state": "published",
        "is_current": True,
        "servable": True,
    }
    if any(actual[key] != value for key, value in required_state.items()):
        raise ValueError("target document is not current published approved servable")


def repair(
    document_path: Path,
    fixture_aliases_path: Path,
    *,
    execute: bool,
) -> dict:
    if execute and os.getenv("CONTROLLED_DEMO_LIVE_OPT_IN") != "1":
        raise RuntimeError("set CONTROLLED_DEMO_LIVE_OPT_IN=1 before --execute")
    if document_path.suffix.casefold() not in {".md", ".markdown"}:
        raise ValueError("repair accepts only Markdown documents")
    try:
        document_bytes = document_path.read_bytes()
    except OSError as exc:
        raise ValueError("document cannot be read") from exc
    document_sha256 = hashlib.sha256(document_bytes).hexdigest()
    fixture_identity = validate_document_identity(
        document_path,
        fixture_aliases_path,
        actual_sha256=document_sha256,
    )
    from mech_chatbot.ingestion.pdf.bom import extract_bom_records_from_markdown

    try:
        document_text = document_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("document is not valid UTF-8") from exc
    records = extract_bom_records_from_markdown(document_text)
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
        validate_database_identity(document, fixture_identity)
        existing = connection.execute(text("""
            SELECT MaHang, TenVatTu, VatLieu, SoLuong, GhiChu, Unit,
                   SourceTableIndex, RawRowJson
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
                SELECT MaHang, TenVatTu, VatLieu, SoLuong, GhiChu, Unit,
                       SourceTableIndex, RawRowJson
                FROM dbo.BangKeVatTu
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
        "document_sha256": document_sha256,
        "fixture_aliases_path": str(fixture_aliases_path.resolve()),
        "fixture_aliases_sha256": fixture_identity["fixture_aliases_sha256"],
        "fixture_identity": fixture_identity,
        "record_count": len(records),
        "plan": plan,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document", type=Path, required=True)
    parser.add_argument("--fixture-aliases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    try:
        report = repair(
            args.document,
            args.fixture_aliases,
            execute=args.execute,
        )
    except (OSError, UnicodeDecodeError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
