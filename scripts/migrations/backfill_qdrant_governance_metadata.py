"""Copy document-level governance metadata from SQL into existing Qdrant points.

This does not re-embed or publish documents.  It only enriches existing point
payloads so parent context, policy audit, taxonomy and rule-first routing work
for data ingested before V0022.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from sqlalchemy import text  # noqa: E402

from mech_chatbot.db.engine import _ensure_engine, engine  # noqa: E402
from mech_chatbot.db.repositories.qdrant import update_qdrant_metadata  # noqa: E402


def _document_type(value):
    try:
        payload = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    return str(payload.get("document_type") or "").strip() if isinstance(payload, dict) else ""


def main() -> int:
    _ensure_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT t.DocID, t.OwnerDepartment, t.KnowledgeOwnerUserID,
                       t.KnowledgeApproverUserID, t.TaxonomyVersion,
                       t.ParentApplicable, t.ServingEpoch, t.ClassificationJson,
                       t.Title, t.DocNumber, t.ExternalProcessingPolicy,
                       ISNULL(p.ParentContextEnabled, 1) AS ParentContextEnabled
                FROM dbo.TaiLieu t
                LEFT JOIN dbo.DepartmentDomainProfile p
                    ON p.DeptCode = t.OwnerDepartment AND p.IsActive = 1
                WHERE t.TrangThaiVector = 1
                ORDER BY t.DocID
                """
            )
        ).mappings().all()
    updated = 0
    failed = []
    for row in rows:
        doc_type = _document_type(row["ClassificationJson"])
        payload = {
            "owner_department": row["OwnerDepartment"] or "",
            "knowledge_owner_user_id": row["KnowledgeOwnerUserID"],
            "knowledge_approver_user_id": row["KnowledgeApproverUserID"],
            "taxonomy_version": row["TaxonomyVersion"] or "v1",
            "parent_applicable": bool(row["ParentApplicable"]),
            "parent_context_enabled": bool(row["ParentContextEnabled"]),
            "serving_epoch": int(row["ServingEpoch"] or 0),
            "document_type": doc_type,
            "document_type_family": doc_type,
            "title": row["Title"] or "",
            "doc_number": row["DocNumber"] or "",
            "external_processing_policy": row["ExternalProcessingPolicy"] or "all_external",
        }
        if update_qdrant_metadata(int(row["DocID"]), payload, require_points=True):
            updated += 1
        else:
            failed.append(int(row["DocID"]))
    print(json.dumps({"total": len(rows), "updated": updated, "failed_doc_ids": failed}, ensure_ascii=False))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
