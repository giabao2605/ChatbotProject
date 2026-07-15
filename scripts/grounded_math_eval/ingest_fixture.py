"""Ingest, publish and seed deterministic BOM rows in isolated staging."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import text

from scripts.grounded_math_eval.constants import (
    DEFAULT_OUTPUT, FIXTURE_BATCH, FIXTURE_COLLECTION, LIVE_OPT_IN,
)
from scripts.grounded_math_eval.generate_fixture import generate_fixture

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _metadata(record):
    code = str(record["doc_number"]).strip().lower()
    part_codes = [str(row["part"]).strip().lower() for row in record.get("rows") or []]
    return {
        "source_system": FIXTURE_BATCH, "doc_number": record["doc_number"],
        "base_code": code, "ma_chinh": code,
        "ma_doi_tuong": list(dict.fromkeys([code, *part_codes])),
        "ma_vat_tu": part_codes,
        "version_no": record["version"], "version_label": f"v{record['version']}",
        "lifecycle_status": "published", "review_status": "approved",
        "publication_state": "published", "servable": True, "is_current": True,
        "effective_status": "effective",
    }


def _seed_bom_rows(connection, doc_id: int, rows: list[dict]) -> None:
    connection.execute(text("DELETE FROM dbo.BangKeVatTu WHERE DocID=:doc_id"), {"doc_id": doc_id})
    connection.execute(text("""
        INSERT INTO dbo.BangKeVatTu
            (DocID, TrangSo, MaHang, TenVatTu, SoLuong, Unit, Confidence,
             RawRowJson, SourceTableIndex)
        VALUES
            (:doc_id, 1, :part, :part, :value, :unit, 1.0, :raw, :row_index)
    """), [{
        "doc_id": doc_id, "part": row["part"], "value": row["value"],
        "unit": row["unit"], "row_index": int(row["source_table_index"]),
        "raw": json.dumps(row, ensure_ascii=False, sort_keys=True),
    } for row in rows])
    count = connection.execute(text(
        "SELECT COUNT(1) FROM dbo.BangKeVatTu WHERE DocID=:doc_id"
    ), {"doc_id": doc_id}).scalar_one()
    if int(count) != len(rows):
        raise RuntimeError(f"BOM seed count mismatch for DocID {doc_id}: {count}/{len(rows)}")


def ingest_fixture(output: Path = DEFAULT_OUTPUT) -> dict:
    if os.getenv(LIVE_OPT_IN) != "1":
        raise RuntimeError(f"set {LIVE_OPT_IN}=1 before writing the grounded-math fixture")
    from mech_chatbot.config.settings import QDRANT_COLLECTION
    if QDRANT_COLLECTION != FIXTURE_COLLECTION:
        raise RuntimeError(f"QDRANT_COLLECTION must equal {FIXTURE_COLLECTION}")
    from mech_chatbot.db.engine import _ensure_engine, engine
    from mech_chatbot.db.repositories.document import delete_document_completely
    from mech_chatbot.db.repositories.jobs import create_ingestion_job, update_ingestion_job
    from mech_chatbot.db.repositories.publication import publish_document
    from mech_chatbot.db.repositories.qdrant import (
        _get_qdrant_client, update_qdrant_metadata,
    )
    from mech_chatbot.ingestion.file_ingestor import learn_new_file

    client = _get_qdrant_client()
    if not client.collection_exists(FIXTURE_COLLECTION):
        from mech_chatbot.rag.bootstrap import client as _initialized_client  # noqa: F401
    from scripts.create_qdrant_indexes import create_indexes
    if not create_indexes():
        raise RuntimeError("could not create required fixture payload indexes")

    manifest = Path(output) / "corpus_manifest.jsonl"
    if not manifest.exists():
        generate_fixture(output)
    records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    _ensure_engine()
    completed = skipped = 0
    for record in records:
        with engine.connect() as connection:
            existing = connection.execute(text("""
                SELECT TOP 1 DocID, Servable, PublicationState, LifecycleStatus,
                       ReviewStatus, IsCurrent
                FROM dbo.TaiLieu WHERE SourceSystem=:source AND DocNumber=:number
                ORDER BY DocID DESC
            """), {"source": FIXTURE_BATCH, "number": record["doc_number"]}).mappings().first()
            governance = connection.execute(text("""
                SELECT g.KnowledgeOwnerUserID, g.KnowledgeApproverUserID, g.TaxonomyVersion,
                       p.DocumentTypesJson
                FROM dbo.DepartmentKnowledgeGovernance g
                JOIN dbo.DepartmentDomainProfile p ON p.DeptCode=g.DeptCode AND p.IsActive=1
                WHERE g.DeptCode=:department AND g.IsActive=1
            """), {"department": record["department"]}).mappings().first()
        if existing and all((
            bool(existing["Servable"]), bool(existing["IsCurrent"]),
            existing["PublicationState"] == "published",
            existing["LifecycleStatus"] == "published", existing["ReviewStatus"] == "approved",
        )):
            with engine.begin() as connection:
                _seed_bom_rows(connection, int(existing["DocID"]), record["rows"])
            update_qdrant_metadata(int(existing["DocID"]), _metadata(record), require_points=True)
            skipped += 1
            continue
        if existing and not delete_document_completely(int(existing["DocID"]), reviewer="grounded-math-eval-retry"):
            raise RuntimeError(f"could not remove incomplete fixture DocID {existing['DocID']}")
        if not governance or not governance["KnowledgeOwnerUserID"] or not governance["KnowledgeApproverUserID"]:
            raise RuntimeError(f"missing governance principals for {record['department']}")
        file_path = Path(output) / record["path"]
        upload_meta = {
            "title": record["title"], "summary": f"Fixture {FIXTURE_BATCH}",
            "tags": f"grounded-math-eval,{FIXTURE_BATCH}", "doc_number": record["doc_number"],
            "effective_date": record["effective_date"], "expiry_date": record["expiry_date"],
            "review_date": record["expiry_date"], "owner_signer": "Grounded Math Eval Owner",
            "language": "vi", "effective_status": "effective", "site": record["site"],
            "knowledge_owner_user_id": int(governance["KnowledgeOwnerUserID"]),
            "knowledge_approver_user_id": int(governance["KnowledgeApproverUserID"]),
            "taxonomy_version": governance["TaxonomyVersion"] or "v1",
        }
        job_id = create_ingestion_job(
            file_name=file_path.name, file_path=str(file_path), thu_muc=record["department"],
            uploaded_by="grounded_math_eval_owner", security_level=record["security_level"],
            site=record["site"], phong_ban=[record["department"]], upload_meta=upload_meta,
        )
        if not job_id:
            raise RuntimeError(f"could not create ingestion job for {file_path.name}")
        update_ingestion_job(job_id, "classifying")
        success, message, _ = learn_new_file(
            str(file_path), file_path.name, thu_muc=record["department"],
            security_override=record["security_level"], site_override=record["site"],
            scan_sensitive=True, phong_ban_override=[record["department"]],
        )
        if not success:
            update_ingestion_job(job_id, "failed", message)
            raise RuntimeError(f"ingest failed for {file_path.name}: {message}")
        with engine.begin() as connection:
            doc_id = int(connection.execute(text(
                "SELECT TOP 1 DocID FROM dbo.TaiLieu WHERE TenFile=:name ORDER BY DocID DESC"
            ), {"name": file_path.name}).scalar_one())
            allowed_types = json.loads(governance["DocumentTypesJson"] or "[]")
            document_type = "generic" if "generic" in allowed_types else allowed_types[0]
            connection.execute(text("""
                UPDATE dbo.TaiLieu SET SourceSystem=:source, DocNumber=:number, BaseCode=:number,
                    VersionNo=:version, VersionLabel=:label, ClassificationModel='grounded-math-fixture-v1',
                    ClassificationJson=:classification, ClassificationRationale=:rationale,
                    IsCurrent=1, EffectiveStatus='effective', KnowledgeOwnerUserID=:owner,
                    KnowledgeApproverUserID=:approver, TaxonomyVersion=:taxonomy
                WHERE DocID=:doc_id
            """), {
                "source": FIXTURE_BATCH, "number": record["doc_number"], "version": record["version"],
                "label": f"v{record['version']}",
                "classification": json.dumps({"document_type": document_type, "fixture_batch": FIXTURE_BATCH}),
                "rationale": f"Deterministic fixture {FIXTURE_BATCH}",
                "owner": int(governance["KnowledgeOwnerUserID"]),
                "approver": int(governance["KnowledgeApproverUserID"]),
                "taxonomy": governance["TaxonomyVersion"] or "v1", "doc_id": doc_id,
            })
            _seed_bom_rows(connection, doc_id, record["rows"])
        result = publish_document(
            doc_id, reviewer="grounded_math_eval_approver",
            reviewer_id=int(governance["KnowledgeApproverUserID"]),
            reviewer_roles=["knowledge_approver", "reviewer"],
        )
        if not result:
            delete_document_completely(doc_id, reviewer="grounded-math-eval-failed-publish")
            raise RuntimeError(f"publish failed for {file_path.name}: {result.error}")
        update_qdrant_metadata(doc_id, _metadata(record))
        update_ingestion_job(job_id, "published")
        completed += 1
    return {"batch": FIXTURE_BATCH, "requested": len(records), "completed": completed, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(ingest_fixture(args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
