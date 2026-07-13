"""Ingest and publish tagged deterministic demo fixtures through the real pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from scripts.demo_wave.generate_demo_assets import DEFAULT_OUTPUT, DEMO_BATCH, generate_corpus
from mech_chatbot.db.engine import _ensure_engine, engine
from mech_chatbot.db.repositories.jobs import create_ingestion_job, update_ingestion_job
from mech_chatbot.db.repositories.publication import publish_document
from mech_chatbot.db.repositories.qdrant import update_qdrant_metadata
from mech_chatbot.db.repositories.document import delete_document_completely
from mech_chatbot.ingestion.file_ingestor import learn_new_file


def ingest_corpus(output: Path, limit: int | None = None) -> dict:
    manifest = output / "corpus_manifest.jsonl"
    if not manifest.exists():
        generate_corpus(output)
    records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    if limit:
        records = records[:limit]
    _ensure_engine()
    completed = skipped = failed = 0
    for record in records:
        with engine.connect() as conn:
            existing = conn.execute(text("""
                SELECT TOP 1 DocID, LifecycleStatus, EffectiveStatus FROM dbo.TaiLieu
                WHERE SourceSystem=:source AND OwnerDepartment=:department AND DocNumber=:doc_number
            """), {"source": DEMO_BATCH, "department": record["department"], "doc_number": record["doc_number"]}).fetchone()
            governance = conn.execute(text("""
                SELECT g.KnowledgeOwnerUserID, g.KnowledgeApproverUserID, g.TaxonomyVersion,
                       p.DocumentTypesJson
                FROM dbo.DepartmentKnowledgeGovernance g
                JOIN dbo.DepartmentDomainProfile p ON p.DeptCode=g.DeptCode AND p.IsActive=1
                WHERE g.DeptCode=:department AND g.IsActive=1
            """), {"department": record["department"]}).mappings().first()
        if existing:
            expected_lifecycle = "superseded" if record["effective_status"] == "superseded" else "published"
            if str(existing[1] or "").lower() == expected_lifecycle and str(existing[2] or "").lower() == record["effective_status"]:
                skipped += 1
                continue
            if not delete_document_completely(int(existing[0]), reviewer="demo-wave-repair"):
                raise RuntimeError(f"Cannot repair partial demo document {existing[0]}")
        if not governance or not governance["KnowledgeOwnerUserID"] or not governance["KnowledgeApproverUserID"]:
            raise RuntimeError(f"Missing demo governance principals for {record['department']}")
        file_path = output / record["path"]
        file_name = file_path.name
        upload_meta = {
            "title": record["title"], "summary": f"Fixture {DEMO_BATCH}", "tags": f"demo,{record['department']}",
            "doc_number": record["doc_number"], "effective_date": record["effective_date"], "expiry_date": record["expiry_date"],
            "review_date": record["expiry_date"], "owner_signer": f"Demo Owner {record['department']}",
            "language": "vi", "effective_status": "effective", "site": record["site"],
            "knowledge_owner_user_id": int(governance["KnowledgeOwnerUserID"]),
            "knowledge_approver_user_id": int(governance["KnowledgeApproverUserID"]),
            "taxonomy_version": governance["TaxonomyVersion"] or "v1",
        }
        job_id = create_ingestion_job(
            file_name=file_name, file_path=str(file_path), thu_muc=record["department"],
            uploaded_by=f"demo_owner_{record['department'].lower()}", security_level=record["security_level"],
            site=record["site"], phong_ban=[record["department"]], upload_meta=upload_meta,
        )
        if not job_id:
            failed += 1
            print(f"JOB FAIL {record['department']}/{file_name}")
            continue
        # This command owns synchronous processing. Move the job out of the
        # worker-claimable pending states before loading the ingestion stack.
        update_ingestion_job(job_id, "classifying")
        success, message, _ = learn_new_file(
            str(file_path), file_name, thu_muc=record["department"], security_override=record["security_level"],
            site_override=record["site"], scan_sensitive=True, phong_ban_override=[record["department"]],
        )
        if not success:
            update_ingestion_job(job_id, "failed", message)
            failed += 1
            print(f"INGEST FAIL {record['department']}/{file_name}: {message}")
            continue
        with engine.begin() as conn:
            doc_id = conn.execute(text("""
                SELECT TOP 1 DocID FROM dbo.TaiLieu WHERE TenFile=:file_name AND OwnerDepartment=:department ORDER BY DocID DESC
            """), {"file_name": file_name, "department": record["department"]}).scalar_one()
            allowed_types = json.loads(governance["DocumentTypesJson"] or "[]")
            document_type = "generic" if "generic" in allowed_types else allowed_types[0]
            classification = json.dumps({"document_type": document_type, "demo_batch_id": DEMO_BATCH}, ensure_ascii=False)
            doc_number = record["doc_number"]
            base_code = doc_number[:-3] if doc_number.endswith(("-V1", "-V2")) else doc_number
            conn.execute(text("""
                UPDATE dbo.TaiLieu SET SourceSystem=:source, ExternalProcessingPolicy='all_external',
                    ClassificationRationale=:rationale, ClassificationModel='demo-deterministic-v1',
                    ClassificationJson=:classification, VersionNo=1, VersionLabel='v1', BaseCode=:base_code,
                    IsCurrent=1, EffectiveStatus='effective', EffectiveDate=:effective_date, ExpiryDate=:expiry_date,
                    KnowledgeOwnerUserID=:owner, KnowledgeApproverUserID=:approver, TaxonomyVersion=:taxonomy
                WHERE DocID=:doc_id
            """), {"source": DEMO_BATCH, "rationale": f"Deterministic fixture {DEMO_BATCH}",
                    "classification": classification, "base_code": base_code,
                    "effective_date": record["effective_date"], "expiry_date": record["expiry_date"],
                    "owner": int(governance["KnowledgeOwnerUserID"]), "approver": int(governance["KnowledgeApproverUserID"]),
                    "taxonomy": governance["TaxonomyVersion"] or "v1", "doc_id": doc_id})
            conn.execute(text("""
                IF NOT EXISTS (SELECT 1 FROM dbo.PhongBanChiaSe WHERE DocID=:doc_id AND DeptCode=:department)
                    INSERT INTO dbo.PhongBanChiaSe (DocID, DeptCode) VALUES (:doc_id, :department)
            """), {"doc_id": doc_id, "department": record["department"]})
        if record["effective_status"] != "superseded":
            result = publish_document(doc_id, reviewer=f"demo_approver_{record['department'].lower()}",
                                      reviewer_id=int(governance["KnowledgeApproverUserID"]),
                                      reviewer_roles=["knowledge_approver", "reviewer"])
            if not result:
                update_ingestion_job(job_id, "failed", result.error)
                failed += 1
                print(f"PUBLISH FAIL {record['department']}/{file_name}: {result.error}")
                continue
        lifecycle = "superseded" if record["effective_status"] == "superseded" else "published"
        publication = "published"
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE dbo.TaiLieu SET LifecycleStatus=:lifecycle, ReviewStatus='approved', PublicationState=:publication,
                    Servable=:servable, IsCurrent=:is_current, EffectiveStatus=:effective_status WHERE DocID=:doc_id
            """), {"lifecycle": lifecycle, "publication": publication, "servable": 1 if record["should_serve"] else 0,
                    "is_current": 1 if record["is_current"] else 0, "effective_status": record["effective_status"], "doc_id": doc_id})
        update_qdrant_metadata(doc_id, {"lifecycle_status": lifecycle, "review_status": "approved", "publication_state": publication,
                                         "servable": bool(record["should_serve"]), "is_current": bool(record["is_current"]),
                                         "effective_status": record["effective_status"], "effective_date": record["effective_date"],
                                         "expiry_date": record["expiry_date"]})
        update_ingestion_job(job_id, "published")
        completed += 1
        print(f"INGEST OK {record['department']}/{file_name} doc={doc_id}")
    return {"requested": len(records), "completed": completed, "skipped": skipped, "failed": failed}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    print(json.dumps(ingest_corpus(args.output, args.limit), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
