"""Verify fixture provenance in SQL and Qdrant before any live evaluation call."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from scripts.crag_eval.constants import FIXTURE_BATCH, FIXTURE_COLLECTION, LIVE_OPT_IN

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def check_fixture_cases(cases, sql_documents, qdrant_points, *, collection: str) -> dict:
    if collection != FIXTURE_COLLECTION:
        raise ValueError(f"collection must equal {FIXTURE_COLLECTION}")
    docs = {str(row["TenFile"]).lower(): row for row in sql_documents}
    failures = []
    for case in cases:
        expected_name = str(case.get("expected_document") or "").lower()
        if not expected_name:
            continue
        doc = docs.get(expected_name)
        if not doc:
            failures.append({"case_id": case["id"], "reason": "sql_document_missing"})
            continue
        valid_sql = (
            doc.get("SourceSystem") == FIXTURE_BATCH
            and str(doc.get("LifecycleStatus") or "").lower() == "published"
            and str(doc.get("ReviewStatus") or "").lower() == "approved"
            and str(doc.get("PublicationState") or "").lower() == "published"
            and bool(doc.get("IsCurrent"))
            and int(doc.get("VersionNo") or 0) == int(case.get("expected_version") or 0)
            and bool(doc.get("Servable"))
            and doc.get("OwnerDepartment") == case.get("expected_department")
            and doc.get("Site") == case.get("expected_site")
            and doc.get("SecurityLevel") == case.get("expected_security_level")
        )
        if not valid_sql:
            failures.append({"case_id": case["id"], "reason": "sql_provenance_invalid"})
            continue
        expected_page = int(case.get("expected_page") or 1)
        exists = any(
            int(point.get("doc_id") or 0) == int(doc["DocID"])
            and int(point.get("page") or point.get("page_number") or point.get("trang_so") or 0) == expected_page
            and point.get("source_system") == FIXTURE_BATCH
            and int(point.get("version_no") or 0) == int(case.get("expected_version") or 0)
            and str(point.get("lifecycle_status") or "").lower() == "published"
            and str(point.get("review_status") or "").lower() == "approved"
            and str(point.get("publication_state") or "").lower() == "published"
            and bool(point.get("servable"))
            and bool(point.get("is_current"))
            and point.get("owner_department") == case.get("expected_department")
            and point.get("site") == case.get("expected_site")
            and point.get("security_level") == case.get("expected_security_level")
            and case.get("expected_department") in (point.get("phong_ban_quyen") or [])
            and str(point.get("base_code") or "").lower() == str(doc.get("BaseCode") or "").lower()
            for point in qdrant_points
        )
        if not exists:
            failures.append({"case_id": case["id"], "reason": "qdrant_page_missing"})
    fingerprint_payload = {
        "documents": sorted(
            ({key: row.get(key) for key in (
                "DocID", "TenFile", "VersionNo", "LifecycleStatus", "ReviewStatus",
                "PublicationState", "IsCurrent", "Servable", "SourceSystem",
                "OwnerDepartment", "Site", "SecurityLevel", "BaseCode",
            )}
             for row in sql_documents),
            key=lambda row: (str(row.get("TenFile")), int(row.get("DocID") or 0)),
        ),
        "points": sorted(
            ({key: point.get(key) for key in (
                "doc_id", "page", "page_number", "trang_so", "source_system", "version_no",
                "lifecycle_status", "review_status", "publication_state", "servable", "is_current",
                "owner_department", "site", "security_level", "phong_ban_quyen",
                "base_code", "ma_chinh", "ma_doi_tuong",
                "_point_id", "_content_sha256",
            )}
             for point in qdrant_points),
            key=lambda point: (int(point.get("doc_id") or 0), int(point.get("page") or point.get("page_number") or point.get("trang_so") or 0)),
        ),
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return {
        "schema": "crag-fixture-preflight-v1", "passed": not failures,
        "batch": FIXTURE_BATCH, "collection": collection,
        "checked_cases": len(cases), "failures": failures,
        "fixture_fingerprint": fingerprint,
    }


def run_live_preflight(cases: list[dict]) -> dict:
    if os.getenv(LIVE_OPT_IN) != "1":
        raise RuntimeError(f"set {LIVE_OPT_IN}=1 to access the CRAG staging fixture")
    from sqlalchemy import text
    from qdrant_client import models
    from mech_chatbot.config.settings import QDRANT_COLLECTION
    from mech_chatbot.db.engine import _ensure_engine, engine
    from mech_chatbot.db.repositories.qdrant import _get_qdrant_client

    if QDRANT_COLLECTION != FIXTURE_COLLECTION:
        raise RuntimeError(f"QDRANT_COLLECTION must equal {FIXTURE_COLLECTION}")
    _ensure_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DocID, TenFile, VersionNo, LifecycleStatus, ReviewStatus,
                   PublicationState, IsCurrent, Servable, SourceSystem,
                   OwnerDepartment, Site, SecurityLevel, BaseCode
            FROM dbo.TaiLieu WHERE SourceSystem=:batch
        """), {"batch": FIXTURE_BATCH}).mappings().all()
    client = _get_qdrant_client()
    points = []
    for row in rows:
        found, _ = client.scroll(
            collection_name=FIXTURE_COLLECTION,
            scroll_filter=models.Filter(must=[models.FieldCondition(
                key="metadata.doc_id", match=models.MatchValue(value=int(row["DocID"]))
            )]), limit=100, with_payload=True, with_vectors=False,
        )
        for point in found:
            raw_payload = dict(point.payload or {})
            metadata = dict(raw_payload.get("metadata") or {})
            content = raw_payload.get("page_content") or raw_payload.get("text") or raw_payload.get("content") or ""
            metadata["_point_id"] = str(point.id)
            metadata["_content_sha256"] = hashlib.sha256(str(content).encode("utf-8")).hexdigest()
            points.append(metadata)
    return check_fixture_cases(cases, [dict(row) for row in rows], points, collection=QDRANT_COLLECTION)


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
