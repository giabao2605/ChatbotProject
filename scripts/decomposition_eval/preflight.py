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

from mech_chatbot.composition.maintenance_runtime import with_configured_repository_runtime


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


def _resolve_calculation(
    calculation,
    resolved_claims,
    resolved_citations,
    actual_rows,
    bom_document,
):
    if not calculation or not bom_document:
        return None
    resolved_sources = []
    for source in calculation.get("sources") or []:
        row = actual_rows.get(str(source.get("source_row_key") or ""))
        if row:
            resolved_sources.append({
                **source,
                "doc_id": int(bom_document["DocID"]),
                "source_id": f"BOM-{int(row['ID'])}",
            })
    if len(resolved_sources) != len(calculation.get("sources") or []):
        return None
    allowed_numbers = list(calculation.get("allowed_numbers") or [])
    allowed_numbers.extend(
        term
        for claim in resolved_claims
        for term in claim.get("required_terms") or []
    )
    allowed_numbers.extend(
        citation.get(field)
        for citation in resolved_citations
        for field in ("doc_id", "page", "version", "source_id")
    )
    return {
        **calculation,
        "allowed_numbers": list(dict.fromkeys(allowed_numbers)),
        "sources": resolved_sources,
    }


def validate_manifest_scope(cases, *, min_complex=10, min_simple=3):
    groups = {"complex": 0, "simple": 0}
    for case in cases:
        group = str(case.get("evaluation_group") or "")
        if group in groups:
            groups[group] += 1
    if groups["complex"] < min_complex:
        raise ValueError(f"manifest requires at least {min_complex} complex cases")
    if groups["simple"] < min_simple:
        raise ValueError(f"manifest requires at least {min_simple} simple negative cases")
    return groups


def check_fixture_cases(
    cases,
    sql_documents,
    bom_rows,
    qdrant_points,
    *,
    collection,
    expected_collection=FIXTURE_COLLECTION,
    fixture_batch=FIXTURE_BATCH,
):
    if collection != expected_collection:
        raise ValueError(f"collection must equal {expected_collection}")
    documents = {str(row.get("TenFile") or "").casefold(): row for row in sql_documents}
    points_by_doc = {}
    for point in qdrant_points:
        points_by_doc.setdefault(int(point.get("doc_id") or 0), []).append(point)
    failures = []
    resolutions = {}
    actual_rows = {_row_key(row): row for row in bom_rows if _row_key(row)}
    bom_document = documents.get(BOM_DOCUMENT.casefold())
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
            expected_department = expected_extra.get(
                "department", case.get("expected_department", "Technical")
            )
            expected_site = expected_extra.get(
                "site", case.get("expected_site", "CRAG-EVAL-HQ")
            )
            expected_security = expected_extra.get(
                "security_level",
                case.get("expected_security_level", "internal"),
            )
            expected_version = expected_extra.get("version") or next((citation.get("version") for citation in case.get("expected_citations") or [] if citation.get("document") == filename), None) or next((citation.get("version") for branch in case.get("expected_branches") or [] for citation in branch.get("expected_citations") or [] if citation.get("document") == filename), None)
            expected_base_code = (
                expected_extra.get("base_code")
                or next((
                    citation.get("base_code")
                    for citation in case.get("expected_citations") or []
                    if citation.get("document") == filename
                ), None)
                or next((
                    citation.get("base_code")
                    for branch in case.get("expected_branches") or []
                    for citation in branch.get("expected_citations") or []
                    if citation.get("document") == filename
                ), None)
            )
            sql_valid = all((
                document.get("SourceSystem") == fixture_batch,
                str(document.get("LifecycleStatus") or "").casefold() == "published",
                str(document.get("ReviewStatus") or "").casefold() == "approved",
                str(document.get("PublicationState") or "").casefold() == "published",
                bool(document.get("IsCurrent")), bool(document.get("Servable")),
                int(document.get("VersionNo") or 0) == int(expected_version or 0),
                document.get("OwnerDepartment") == expected_department,
                document.get("Site") == expected_site,
                document.get("SecurityLevel") == expected_security,
            ))
            point_valid = any(
                int(point.get("page") or point.get("trang_so") or 0) == 1
                and point.get("source_system") == fixture_batch
                and int(point.get("version_no") or 0) == int(expected_version or 0)
                and (
                    expected_base_code is None
                    or str(point.get("base_code") or "").casefold()
                    == str(expected_base_code).casefold()
                )
                and bool(point.get("servable")) and bool(point.get("is_current"))
                and point.get("owner_department") == expected_department
                and expected_department
                in (point.get("phong_ban_quyen") or [])
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
        resolution = {
            "expected_citations": resolved_citations,
            "expected_branches": resolved_branches,
            "expected_claims": resolved_claims,
        }
        resolved_calculation = _resolve_calculation(
            case.get("expected_calculation"),
            resolved_claims,
            resolved_citations,
            actual_rows,
            bom_document,
        )
        if resolved_calculation:
            resolution["expected_calculation"] = resolved_calculation
        resolutions[case["id"]] = resolution
    if any(case.get("expected_calculation") for case in cases):
        expected_rows = {row["row_key"]: row for row in BOM_ROWS}
        for key, expected in expected_rows.items():
            row = actual_rows.get(key)
            if (
                not row
                or not bom_document
                or int(row.get("DocID") or 0) != int(bom_document["DocID"])
            ):
                failures.append({
                    "case_id": "decomp-sql-bom-doc",
                    "reason": "bom_source_row_missing",
                    "row_key": key,
                })
            elif (
                _decimal(row.get("SoLuong")) != _decimal(expected["value"])
                or str(row.get("Unit")) != expected["unit"]
            ):
                failures.append({
                    "case_id": "decomp-sql-bom-doc",
                    "reason": "bom_source_row_drift",
                    "row_key": key,
                })
    fingerprint = hashlib.sha256(json.dumps({"documents": sql_documents, "bom_rows": bom_rows, "points": qdrant_points}, sort_keys=True, default=str).encode()).hexdigest()
    return {
        "schema": "decomposition-fixture-preflight-v1", "passed": not failures,
        "batch": fixture_batch,
        "collection": collection,
        "checked_cases": len(cases),
        "failures": failures, "case_resolutions": resolutions,
        "fixture_fingerprint": fingerprint,
    }


@with_configured_repository_runtime(include_qdrant=True)
def run_live_preflight(cases):
    validate_manifest_scope(cases)
    if os.getenv(LIVE_OPT_IN) != "1":
        raise RuntimeError(f"set {LIVE_OPT_IN}=1 to access the decomposition fixture")
    from sqlalchemy import text
    from qdrant_client import models
    from mech_chatbot.config.repository_runtime import current_qdrant_runtime
    from mech_chatbot.db.engine import _ensure_engine, engine
    client, collection = current_qdrant_runtime()
    fixture_batch = os.getenv("RAG_EVAL_FIXTURE_BATCH", FIXTURE_BATCH)
    expected_collection = os.getenv(
        "RAG_EVAL_EXPECTED_COLLECTION", FIXTURE_COLLECTION
    )
    if collection != expected_collection:
        raise RuntimeError(
            f"QDRANT_COLLECTION must equal {expected_collection}"
        )
    _ensure_engine()
    with engine.connect() as connection:
        documents = [dict(row) for row in connection.execute(text("""
            SELECT DocID, TenFile, VersionNo, LifecycleStatus, ReviewStatus,
                   PublicationState, IsCurrent, Servable, SourceSystem,
                   OwnerDepartment, Site, SecurityLevel
            FROM dbo.TaiLieu WHERE SourceSystem=:batch
        """), {"batch": fixture_batch}).mappings().all()]
        bom_rows = [dict(row) for row in connection.execute(text("""
            SELECT b.ID, b.DocID, b.TrangSo, b.SoLuong, b.Unit, b.RawRowJson
            FROM dbo.BangKeVatTu b JOIN dbo.TaiLieu t ON t.DocID=b.DocID
            WHERE t.SourceSystem=:batch
        """), {"batch": fixture_batch}).mappings().all()]
    points = []
    for document in documents:
        found, _ = client.scroll(collection_name=collection, scroll_filter=models.Filter(must=[models.FieldCondition(key="metadata.doc_id", match=models.MatchValue(value=int(document["DocID"]))) ]), limit=100, with_payload=True, with_vectors=False)
        points.extend(dict((point.payload or {}).get("metadata") or {}) for point in found)
    return check_fixture_cases(
        cases,
        documents,
        bom_rows,
        points,
        collection=collection,
        expected_collection=expected_collection,
        fixture_batch=fixture_batch,
    )


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
