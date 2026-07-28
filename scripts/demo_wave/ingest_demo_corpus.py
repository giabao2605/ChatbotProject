"""Ingest and publish tagged deterministic demo fixtures through the real pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from scripts.demo_wave.generate_demo_assets import (
    DEFAULT_OUTPUT,
    DEMO_BATCH,
    DEPARTMENTS,
    generate_corpus,
)
from mech_chatbot.composition.maintenance_runtime import with_configured_repository_runtime
from mech_chatbot.db.engine import _ensure_engine, engine
from mech_chatbot.db.repositories.jobs import create_ingestion_job, update_ingestion_job
from mech_chatbot.db.repositories.publication import publish_document
from mech_chatbot.db.repositories.qdrant import update_qdrant_metadata
from mech_chatbot.db.repositories.document import delete_document_completely
from mech_chatbot.ingestion.file_ingestor import learn_new_file


def _fixture_qdrant_metadata(record: dict, batch: str, lifecycle: str) -> dict:
    base_code = str(record["doc_number"]).strip().lower()
    return {
        "source_system": batch,
        "doc_number": record["doc_number"],
        "base_code": base_code,
        "ma_chinh": base_code,
        "ma_doi_tuong": [base_code],
        "owner_department": record["department"],
        "phong_ban_quyen": [record["department"]],
        "lifecycle_status": lifecycle,
        "review_status": "approved",
        "publication_state": "published",
        "servable": bool(record["should_serve"]),
        "is_current": bool(record["is_current"]),
        "effective_status": record["effective_status"],
        "effective_date": record["effective_date"],
        "expiry_date": record["expiry_date"],
        "site": record["site"],
        "security_level": record["security_level"],
    }


def _validate_manifest_records(
    records: list[dict],
    output: Path,
    batch: str,
) -> list[dict]:
    corpus_root = (Path(output) / "corpus").resolve()
    required = {
        "batch_id",
        "department",
        "site",
        "security_level",
        "path",
        "title",
        "doc_number",
        "document_type",
        "effective_status",
        "effective_date",
        "expiry_date",
        "is_current",
        "should_serve",
    }
    validated = []
    for index, record in enumerate(records, 1):
        if record.get("batch_id") != batch:
            raise ValueError(
                f"corpus manifest batch_id must equal {batch}; "
                f"found {record.get('batch_id')!r}"
            )
        missing = sorted(required - set(record))
        if missing:
            raise ValueError(
                f"corpus manifest row {index} missing {', '.join(missing)}"
            )
        department = str(record["department"])
        if department not in DEPARTMENTS:
            raise ValueError(
                f"corpus manifest row {index} has unapproved department"
            )
        _, expected_site, expected_security, _, code, _ = DEPARTMENTS[
            department
        ]
        if (
            record["site"] != expected_site
            or record["security_level"] != expected_security
            or record["document_type"] != "generic"
            or not str(record["doc_number"]).startswith(f"{code}-")
        ):
            raise ValueError(
                f"corpus manifest row {index} has invalid governance metadata"
            )
        if record["effective_status"] not in {
            "effective",
            "expired",
            "superseded",
        }:
            raise ValueError(
                f"corpus manifest row {index} has invalid effective_status"
            )
        if not all(
            isinstance(record[field], bool)
            for field in ("is_current", "should_serve")
        ):
            raise ValueError(
                f"corpus manifest row {index} has invalid boolean metadata"
            )
        try:
            effective = date.fromisoformat(str(record["effective_date"]))
            expiry = date.fromisoformat(str(record["expiry_date"]))
        except ValueError as error:
            raise ValueError(
                f"corpus manifest row {index} has invalid dates"
            ) from error
        if expiry <= effective:
            raise ValueError(
                f"corpus manifest row {index} has invalid date range"
            )
        source = (Path(output) / str(record["path"])).resolve()
        try:
            source.relative_to(corpus_root)
        except ValueError as error:
            raise ValueError(
                f"corpus manifest row {index} path escapes fixture corpus"
            ) from error
        if source.suffix.lower() != ".md" or not source.is_file():
            raise ValueError(
                f"corpus manifest row {index} source must be an existing .md"
            )
        validated.append(dict(record))
    return validated


class _FixtureDocumentError(RuntimeError):
    def __init__(self, doc_id: int | None, message: str):
        super().__init__(message)
        self.doc_id = doc_id


def _finalize_ingested_document(
    record: dict,
    batch: str,
    governance,
    file_name: str,
) -> int:
    doc_id = None
    try:
        with engine.begin() as conn:
            doc_id = int(conn.execute(text("""
                SELECT TOP 1 DocID FROM dbo.TaiLieu
                WHERE TenFile=:file_name AND OwnerDepartment=:department
                ORDER BY DocID DESC
            """), {
                "file_name": file_name,
                "department": record["department"],
            }).scalar_one())
            allowed_types = json.loads(
                governance["DocumentTypesJson"] or "[]"
            )
            document_type = (
                "generic" if "generic" in allowed_types else allowed_types[0]
            )
            classification = json.dumps({
                "document_type": document_type,
                "demo_batch_id": batch,
            }, ensure_ascii=False)
            doc_number = record["doc_number"]
            base_code = (
                doc_number[:-3]
                if doc_number.endswith(("-V1", "-V2"))
                else doc_number
            )
            conn.execute(text("""
                UPDATE dbo.TaiLieu SET SourceSystem=:source,
                    ExternalProcessingPolicy='all_external',
                    ClassificationRationale=:rationale,
                    ClassificationModel='demo-deterministic-v1',
                    ClassificationJson=:classification, VersionNo=1,
                    VersionLabel='v1', BaseCode=:base_code, IsCurrent=1,
                    EffectiveStatus='effective', EffectiveDate=:effective_date,
                    ExpiryDate=:expiry_date, KnowledgeOwnerUserID=:owner,
                    KnowledgeApproverUserID=:approver,
                    TaxonomyVersion=:taxonomy
                WHERE DocID=:doc_id
            """), {
                "source": batch,
                "rationale": f"Deterministic fixture {batch}",
                "classification": classification,
                "base_code": base_code,
                "effective_date": record["effective_date"],
                "expiry_date": record["expiry_date"],
                "owner": int(governance["KnowledgeOwnerUserID"]),
                "approver": int(governance["KnowledgeApproverUserID"]),
                "taxonomy": governance["TaxonomyVersion"] or "v1",
                "doc_id": doc_id,
            })
            conn.execute(text("""
                IF NOT EXISTS (
                    SELECT 1 FROM dbo.PhongBanChiaSe
                    WHERE DocID=:doc_id AND DeptCode=:department
                )
                    INSERT INTO dbo.PhongBanChiaSe (DocID, DeptCode)
                    VALUES (:doc_id, :department)
            """), {
                "doc_id": doc_id,
                "department": record["department"],
            })
        if record["effective_status"] != "superseded":
            result = publish_document(
                doc_id,
                reviewer=f"demo_approver_{record['department'].lower()}",
                reviewer_id=int(governance["KnowledgeApproverUserID"]),
                reviewer_roles=["knowledge_approver", "reviewer"],
            )
            if not result:
                raise RuntimeError(result.error)
        lifecycle = (
            "superseded"
            if record["effective_status"] == "superseded"
            else "published"
        )
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE dbo.TaiLieu SET LifecycleStatus=:lifecycle,
                    ReviewStatus='approved', PublicationState='published',
                    Servable=:servable, IsCurrent=:is_current,
                    EffectiveStatus=:effective_status WHERE DocID=:doc_id
            """), {
                "lifecycle": lifecycle,
                "servable": 1 if record["should_serve"] else 0,
                "is_current": 1 if record["is_current"] else 0,
                "effective_status": record["effective_status"],
                "doc_id": doc_id,
            })
        if not update_qdrant_metadata(
            doc_id,
            _fixture_qdrant_metadata(record, batch, lifecycle),
            require_points=True,
        ):
            raise RuntimeError(
                "Cannot bind required Qdrant fixture provenance"
            )
        return doc_id
    except Exception as error:
        raise _FixtureDocumentError(doc_id, str(error)) from error


def _delete_failed_fixture(doc_id: int) -> None:
    if not delete_document_completely(
        int(doc_id),
        reviewer="demo-fixture-failure-cleanup",
    ):
        raise RuntimeError(
            f"Cannot clean failed fixture document {doc_id}"
        )


def _find_partial_fixture(
    file_name: str,
    file_path: Path,
    department: str,
) -> int | None:
    with engine.connect() as conn:
        doc_id = conn.execute(text("""
            SELECT TOP 1 DocID FROM dbo.TaiLieu
            WHERE TenFile=:file_name AND FilePath=:file_path
              AND OwnerDepartment=:department
            ORDER BY DocID DESC
        """), {
            "file_name": file_name,
            "file_path": str(file_path),
            "department": department,
        }).scalar()
    return int(doc_id) if doc_id is not None else None


def ingest_corpus(
    output: Path,
    limit: int | None = None,
    *,
    batch: str = DEMO_BATCH,
    generator=generate_corpus,
) -> dict:
    manifest = output / "corpus_manifest.jsonl"
    if not manifest.exists():
        generator(output)
    records = [
        json.loads(line)
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    records = _validate_manifest_records(records, output, batch)
    if limit:
        records = records[:limit]
    _ensure_engine()
    completed = skipped = failed = 0
    for record in records:
        with engine.connect() as conn:
            existing = conn.execute(text("""
                SELECT TOP 1 DocID, LifecycleStatus, EffectiveStatus FROM dbo.TaiLieu
                WHERE SourceSystem=:source AND OwnerDepartment=:department AND DocNumber=:doc_number
            """), {"source": batch, "department": record["department"], "doc_number": record["doc_number"]}).fetchone()
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
                repaired = update_qdrant_metadata(
                    int(existing[0]),
                    _fixture_qdrant_metadata(
                        record,
                        batch,
                        expected_lifecycle,
                    ),
                    require_points=True,
                )
                if repaired:
                    skipped += 1
                    continue
            if not delete_document_completely(int(existing[0]), reviewer="demo-wave-repair"):
                raise RuntimeError(f"Cannot repair partial demo document {existing[0]}")
        if not governance or not governance["KnowledgeOwnerUserID"] or not governance["KnowledgeApproverUserID"]:
            raise RuntimeError(f"Missing demo governance principals for {record['department']}")
        file_path = output / record["path"]
        file_name = file_path.name
        upload_meta = {
            "title": record["title"], "summary": f"Fixture {batch}", "tags": f"demo,{record['department']}",
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
            partial_doc_id = _find_partial_fixture(
                file_name,
                file_path,
                record["department"],
            )
            if partial_doc_id is not None:
                _delete_failed_fixture(partial_doc_id)
            update_ingestion_job(job_id, "failed", message)
            failed += 1
            print(f"INGEST FAIL {record['department']}/{file_name}: {message}")
            continue
        try:
            doc_id = _finalize_ingested_document(
                record,
                batch,
                governance,
                file_name,
            )
        except _FixtureDocumentError as error:
            failed_doc_id = error.doc_id or _find_partial_fixture(
                file_name,
                file_path,
                record["department"],
            )
            if failed_doc_id is not None:
                _delete_failed_fixture(failed_doc_id)
            update_ingestion_job(
                job_id,
                "failed",
                str(error),
            )
            failed += 1
            print(f"FIXTURE FAIL {record['department']}/{file_name}: {error}")
            continue
        update_ingestion_job(job_id, "published")
        completed += 1
        print(f"INGEST OK {record['department']}/{file_name} doc={doc_id}")
    return {"requested": len(records), "completed": completed, "skipped": skipped, "failed": failed}


@with_configured_repository_runtime(include_qdrant=True)
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
