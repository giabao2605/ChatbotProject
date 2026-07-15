"""Resolve and validate decomposition sources before any provider request."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from scripts.crag_eval.constants import FIXTURE_BATCH, FIXTURE_COLLECTION
from scripts.decomposition_eval.constants import BOM_DOCUMENT, BOM_ROWS, LIVE_OPT_IN

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _row_key(row):
    try:
        return str(json.loads(row.get("RawRowJson") or "{}").get("row_key") or "")
    except (TypeError, json.JSONDecodeError):
        return ""


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _resolve_citation(citation, documents):
    value = dict(citation)
    document = documents.get(str(value.get("document") or "").casefold())
    if not document:
        return value
    page = int(value.get("page") or 1)
    value["doc_id"] = int(document["DocID"])
    value["source_id"] = f"D{int(document['DocID'])}P{page}"
    return value


def check_fixture_cases(cases, sql_documents, bom_rows, qdrant_points, *, collection):
    if collection != FIXTURE_COLLECTION:
        raise ValueError(f"collection must equal {FIXTURE_COLLECTION}")
    documents = {str(row.get("TenFile") or "").casefold(): row for row in sql_documents}
    points_by_doc = {}
    for point in qdrant_points:
        points_by_doc.setdefault(int(point.get("doc_id") or 0), []).append(point)
    failures = []
    resolutions = {}
    for case in cases:
        referenced = {
            citation.get("document")
            for citation in case.get("expected_citations") or []
        }
        referenced.update(
            citation.get("document")
            for branch in case.get("expected_branches") or []
            for citation in branch.get("expected_citations") or []
        )
        referenced.update(
            item.get("document") for item in case.get("preflight_documents") or []
        )
        for filename in filter(None, referenced):
            document = documents.get(str(filename).casefold())
            if not document:
                failures.append({"case_id": case["id"], "document": filename, "reason": "sql_document_missing"})
                continue
            expected_extra = next((item for item in case.get("preflight_documents") or [] if item.get("document") == filename), {})
            expected_site = expected_extra.get("site", "CRAG-EVAL-HQ")
            expected_security = expected_extra.get("security_level", "internal")
            expected_version = expected_extra.get("version") or next((citation.get("version") for citation in case.get("expected_citations") or [] if citation.get("document") == filename), None) or next((citation.get("version") for branch in case.get("expected_branches") or [] for citation in branch.get("expected_citations") or [] if citation.get("document") == filename), None)
            sql_valid = all((
                document.get("SourceSystem") == FIXTURE_BATCH,
                str(document.get("LifecycleStatus") or "").casefold() == "published",
                str(document.get("ReviewStatus") or "").casefold() == "approved",
                str(document.get("PublicationState") or "").casefold() == "published",
                bool(document.get("IsCurrent")), bool(document.get("Servable")),
                int(document.get("VersionNo") or 0) == int(expected_version or 0),
                document.get("OwnerDepartment") == "Technical",
                document.get("Site") == expected_site,
                document.get("SecurityLevel") == expected_security,
            ))
            point_valid = any(
                int(point.get("page") or point.get("trang_so") or 0) == 1
                and point.get("source_system") == FIXTURE_BATCH
                and int(point.get("version_no") or 0) == int(expected_version or 0)
                and bool(point.get("servable")) and bool(point.get("is_current"))
                and point.get("site") == expected_site
                and point.get("security_level") == expected_security
                for point in points_by_doc.get(int(document["DocID"]), [])
            )
            if not sql_valid:
                failures.append({"case_id": case["id"], "document": filename, "reason": "sql_provenance_invalid"})
            elif not point_valid:
                failures.append({"case_id": case["id"], "document": filename, "reason": "qdrant_page_missing"})
        resolved_citations = [_resolve_citation(item, documents) for item in case.get("expected_citations") or []]
        resolved_branches = [{
            **branch,
            "expected_citations": [_resolve_citation(item, documents) for item in branch.get("expected_citations") or []],
        } for branch in case.get("expected_branches") or []]
        page_ids = {item["document"]: item["source_id"] for item in resolved_citations}
        for branch in resolved_branches:
            page_ids.update({item["document"]: item["source_id"] for item in branch["expected_citations"]})
        resolved_claims = []
        for claim in case.get("expected_claims") or []:
            allowed = []
            for source_id in claim.get("allowed_source_ids") or []:
                if str(source_id).startswith("$PAGE:"):
                    document = str(source_id)[6:].rsplit(":", 1)[0]
                    allowed.append(page_ids.get(document, source_id))
                else:
                    allowed.append(source_id)
            resolved_claims.append({**claim, "allowed_source_ids": allowed})
        resolutions[case["id"]] = {
            "expected_citations": resolved_citations,
            "expected_branches": resolved_branches,
            "expected_claims": resolved_claims,
        }
    expected_rows = {row["row_key"]: row for row in BOM_ROWS}
    actual_rows = {_row_key(row): row for row in bom_rows if _row_key(row)}
    bom_document = documents.get(BOM_DOCUMENT.casefold())
    for key, expected in expected_rows.items():
        row = actual_rows.get(key)
        if not row or not bom_document or int(row.get("DocID") or 0) != int(bom_document["DocID"]):
            failures.append({"case_id": "decomp-sql-bom-doc", "reason": "bom_source_row_missing", "row_key": key})
        elif _decimal(row.get("SoLuong")) != _decimal(expected["value"]) or str(row.get("Unit")) != expected["unit"]:
            failures.append({"case_id": "decomp-sql-bom-doc", "reason": "bom_source_row_drift", "row_key": key})
    fingerprint = hashlib.sha256(json.dumps({"documents": sql_documents, "bom_rows": bom_rows, "points": qdrant_points}, sort_keys=True, default=str).encode()).hexdigest()
    return {
        "schema": "decomposition-fixture-preflight-v1", "passed": not failures,
        "batch": FIXTURE_BATCH, "collection": collection, "checked_cases": len(cases),
        "failures": failures, "case_resolutions": resolutions,
        "fixture_fingerprint": fingerprint,
    }


def run_live_preflight(cases):
    if os.getenv(LIVE_OPT_IN) != "1":
        raise RuntimeError(f"set {LIVE_OPT_IN}=1 to access the decomposition fixture")
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
            SELECT b.DocID, b.SoLuong, b.Unit, b.RawRowJson
            FROM dbo.BangKeVatTu b JOIN dbo.TaiLieu t ON t.DocID=b.DocID
            WHERE t.SourceSystem=:batch
        """), {"batch": FIXTURE_BATCH}).mappings().all()]
    client = _get_qdrant_client()
    points = []
    for document in documents:
        found, _ = client.scroll(collection_name=FIXTURE_COLLECTION, scroll_filter=models.Filter(must=[models.FieldCondition(key="metadata.doc_id", match=models.MatchValue(value=int(document["DocID"]))) ]), limit=100, with_payload=True, with_vectors=False)
        points.extend(dict((point.payload or {}).get("metadata") or {}) for point in found)
    return check_fixture_cases(cases, documents, bom_rows, points, collection=QDRANT_COLLECTION)


def main():
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
