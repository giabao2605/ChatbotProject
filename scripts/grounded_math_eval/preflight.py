"""Resolve and validate grounded-math SQL/Qdrant provenance before evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from scripts.grounded_math_eval.constants import (
    FIXTURE_BATCH, FIXTURE_COLLECTION, LIVE_OPT_IN,
)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _source_row_key(row):
    try:
        payload = json.loads(row.get("RawRowJson") or "{}")
    except (TypeError, json.JSONDecodeError):
        return ""
    return str(payload.get("row_key") or "").strip()


def check_fixture_cases(cases, sql_documents, bom_rows, qdrant_points, *, collection):
    if collection != FIXTURE_COLLECTION:
        raise ValueError(f"collection must equal {FIXTURE_COLLECTION}")
    documents = {str(row.get("TenFile") or "").casefold(): row for row in sql_documents}
    rows = {_source_row_key(row): row for row in bom_rows if _source_row_key(row)}
    failures = []
    resolutions = {}
    for case in cases:
        calculation = case.get("expected_calculation") or {}
        resolved_sources = []
        for source in calculation.get("sources") or []:
            document = documents.get(str(source.get("document") or "").casefold())
            row = rows.get(str(source.get("source_row_key") or "").strip())
            reason = None
            if not document:
                reason = "sql_document_missing"
            elif document.get("SourceSystem") != FIXTURE_BATCH or not all((
                str(document.get("LifecycleStatus") or "").casefold() == "published",
                str(document.get("ReviewStatus") or "").casefold() == "approved",
                str(document.get("PublicationState") or "").casefold() == "published",
                bool(document.get("IsCurrent")), bool(document.get("Servable")),
                int(document.get("VersionNo") or 0) == int(source.get("version") or 0),
                document.get("OwnerDepartment") == case.get("expected_department"),
                document.get("Site") == case.get("expected_site"),
                document.get("SecurityLevel") == case.get("expected_security_level"),
            )):
                reason = "sql_document_provenance_invalid"
            elif not row or int(row.get("DocID") or 0) != int(document.get("DocID") or 0):
                reason = "bom_source_row_missing"
            elif (
                int(row.get("TrangSo") or 0) != int(source.get("page") or 0)
                or _decimal(row.get("SoLuong")) != _decimal(source.get("value"))
                or str(row.get("Unit") or "").strip().casefold()
                != str(source.get("unit") or "").strip().casefold()
            ):
                reason = "bom_source_row_drift"
            if reason:
                failures.append({"case_id": case["id"], "reason": reason, "source": source.get("source_row_key")})
                continue
            resolved_sources.append({
                **source, "doc_id": int(document["DocID"]),
                "source_id": f"BOM-{int(row['ID'])}",
            })
        expected_documents = {source.get("document") for source in calculation.get("sources") or []}
        for name in expected_documents:
            document = documents.get(str(name or "").casefold())
            if document and not any(
                int(point.get("doc_id") or 0) == int(document["DocID"])
                and int(point.get("page") or point.get("page_number") or point.get("trang_so") or 0) == 1
                and point.get("source_system") == FIXTURE_BATCH
                and bool(point.get("servable")) and bool(point.get("is_current"))
                and int(point.get("version_no") or 0) == int(document.get("VersionNo") or 0)
                and point.get("owner_department") == case.get("expected_department")
                and point.get("site") == case.get("expected_site")
                and point.get("security_level") == case.get("expected_security_level")
                and str(point.get("lifecycle_status") or "").casefold() == "published"
                and str(point.get("review_status") or "").casefold() == "approved"
                and str(point.get("publication_state") or "").casefold() == "published"
                for point in qdrant_points
            ):
                failures.append({"case_id": case["id"], "reason": "qdrant_page_missing", "document": name})
        if len(resolved_sources) == len(calculation.get("sources") or []):
            resolved_citations = []
            for citation in case.get("expected_citations") or []:
                document = documents.get(str(citation.get("document") or "").casefold())
                if document:
                    resolved_citations.append({
                        **citation, "doc_id": int(document["DocID"]),
                        "source_id": f"D{int(document['DocID'])}P{int(citation.get('page') or 1)}",
                    })
            resolutions[case["id"]] = {
                "expected_calculation": {**calculation, "sources": resolved_sources},
                "expected_citations": resolved_citations,
            }
    fingerprint_data = {"documents": sql_documents, "bom_rows": bom_rows, "points": qdrant_points}
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_data, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return {
        "schema": "grounded-math-fixture-preflight-v1", "passed": not failures,
        "batch": FIXTURE_BATCH, "collection": collection, "checked_cases": len(cases),
        "failures": failures, "case_resolutions": resolutions,
        "fixture_fingerprint": fingerprint,
    }


def run_live_preflight(cases: list[dict]) -> dict:
    if os.getenv(LIVE_OPT_IN) != "1":
        raise RuntimeError(f"set {LIVE_OPT_IN}=1 to access the grounded-math staging fixture")
    from sqlalchemy import text
    from qdrant_client import models
    from mech_chatbot.config.settings import QDRANT_COLLECTION
    from mech_chatbot.db.engine import _ensure_engine, engine
    from mech_chatbot.db.repositories.qdrant import _get_qdrant_client

    if QDRANT_COLLECTION != FIXTURE_COLLECTION:
        raise RuntimeError(f"QDRANT_COLLECTION must equal {FIXTURE_COLLECTION}")
    _ensure_engine()
    with engine.connect() as connection:
        documents = [dict(row) for row in connection.execute(text("""
            SELECT DocID, TenFile, VersionNo, LifecycleStatus, ReviewStatus,
                   PublicationState, IsCurrent, Servable, SourceSystem,
                   OwnerDepartment, Site, SecurityLevel
            FROM dbo.TaiLieu WHERE SourceSystem=:batch
        """), {"batch": FIXTURE_BATCH}).mappings().all()]
        bom_rows = [dict(row) for row in connection.execute(text("""
            SELECT b.ID, b.DocID, b.TrangSo, b.SoLuong, b.Unit,
                   b.SourceTableIndex, b.RawRowJson
            FROM dbo.BangKeVatTu b JOIN dbo.TaiLieu t ON t.DocID=b.DocID
            WHERE t.SourceSystem=:batch
        """), {"batch": FIXTURE_BATCH}).mappings().all()]
    client = _get_qdrant_client()
    points = []
    for document in documents:
        found, _ = client.scroll(
            collection_name=FIXTURE_COLLECTION,
            scroll_filter=models.Filter(must=[models.FieldCondition(
                key="metadata.doc_id", match=models.MatchValue(value=int(document["DocID"]))
            )]), limit=100, with_payload=True, with_vectors=False,
        )
        for point in found:
            points.append(dict((point.payload or {}).get("metadata") or {}))
    return check_fixture_cases(
        cases, documents, bom_rows, points, collection=QDRANT_COLLECTION
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    from scripts.eval.run_eval import load_manifest_files
    report = run_live_preflight(load_manifest_files(args.manifest))
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
