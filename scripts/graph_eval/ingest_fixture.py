"""Ingest graph-eval-v1 through the real lifecycle, then seed governed graph facts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import text

from scripts.graph_eval.constants import DEFAULT_OUTPUT, FIXTURE_BATCH, FIXTURE_COLLECTION, LIVE_OPT_IN, ROOT
from scripts.graph_eval.generate_fixture import generate_fixture

SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _metadata(record, doc_id):
    state = record["state"]
    published = state not in {"draft", "unpublished"}
    current = state not in {"superseded"}
    effective = "expired" if state == "expired" else ("superseded" if state == "superseded" else ("draft" if state == "draft" else "effective"))
    return {
        "source_system": FIXTURE_BATCH, "doc_id": int(doc_id),
        "doc_number": record["doc_number"], "base_code": record["doc_number"].lower(),
        "version_no": record["version"], "version_label": f"v{record['version']}",
        "owner_department": record["department"], "department": record["department"],
        "site": record["site"], "security_level": record["security_level"],
        "lifecycle_status": "published" if published else "draft",
        "review_status": "approved" if published else "pending_review",
        "publication_state": "published" if published else "draft",
        "servable": published and current, "is_current": current,
        "effective_status": effective,
    }


def _seed_bom(connection, doc_id, rows):
    connection.execute(text("DELETE FROM dbo.BangKeVatTu WHERE DocID=:doc_id"), {"doc_id": doc_id})
    if rows:
        connection.execute(text("""
            INSERT dbo.BangKeVatTu
                (DocID,TrangSo,MaHang,TenVatTu,VatLieu,NormalizedMaterial,SoLuong,Unit,Confidence,RawRowJson,SourceTableIndex)
            VALUES (:doc_id,1,:part,:name,:material,:material,:value,:unit,1,:raw,:row_index)
        """), [{
            "doc_id": doc_id, "part": row["part"], "name": row["name"],
            "material": row["material"], "value": int(row["value"]), "unit": row["unit"],
            "raw": json.dumps(row, ensure_ascii=False, sort_keys=True),
            "row_index": int(row["source_table_index"]),
        } for row in rows])


def _seed_document_page(connection, doc_id, record, output):
    file_path = Path(output) / record["path"]
    connection.execute(text("""
        IF NOT EXISTS (SELECT 1 FROM dbo.DocumentPages WHERE DocID=:doc_id AND PageNo=1)
            INSERT dbo.DocumentPages
                (DocID,FileName,PageNo,TextExtract,ExtractionStatus)
            VALUES (:doc_id,:filename,1,:content,'fixture')
        ELSE
            UPDATE dbo.DocumentPages SET FileName=:filename, TextExtract=:content,
                ExtractionStatus='fixture', UpdatedAt=GETDATE()
            WHERE DocID=:doc_id AND PageNo=1
    """), {
        "doc_id": doc_id, "filename": record["filename"],
        "content": file_path.read_text(encoding="utf-8"),
    })


def _apply_record_state(connection, doc_id, state):
    if state == "superseded":
        connection.execute(text("""
            UPDATE dbo.TaiLieu SET IsCurrent=0, Servable=0,
                LifecycleStatus='superseded', EffectiveStatus='superseded'
            WHERE DocID=:doc_id
        """), {"doc_id": doc_id})
    elif state == "expired":
        connection.execute(text("""
            UPDATE dbo.TaiLieu SET EffectiveStatus='expired', ExpiryDate='2020-01-01', Servable=0
            WHERE DocID=:doc_id
        """), {"doc_id": doc_id})
    elif state == "unpublished":
        connection.execute(text("""
            UPDATE dbo.TaiLieu SET IsCurrent=1, Servable=0,
                LifecycleStatus='draft', ReviewStatus='approved',
                PublicationState='draft', EffectiveStatus='draft'
            WHERE DocID=:doc_id
        """), {"doc_id": doc_id})


def ingest_fixture(output: Path = DEFAULT_OUTPUT):
    if os.getenv(LIVE_OPT_IN) != "1":
        raise RuntimeError(f"set {LIVE_OPT_IN}=1 before writing graph-eval-v1")
    from mech_chatbot.config.settings import QDRANT_COLLECTION
    if QDRANT_COLLECTION != FIXTURE_COLLECTION:
        raise RuntimeError(f"QDRANT_COLLECTION must equal {FIXTURE_COLLECTION}")
    from mech_chatbot.db.engine import _ensure_engine, engine
    from mech_chatbot.db.repositories.document import delete_document_completely
    from mech_chatbot.db.repositories.jobs import create_ingestion_job, update_ingestion_job
    from mech_chatbot.db.repositories.publication import publish_document
    from mech_chatbot.db.repositories.qdrant import _get_qdrant_client, update_qdrant_metadata
    from mech_chatbot.ingestion.file_ingestor import learn_new_file
    from scripts.graph.seed_deterministic import seed

    manifest = Path(output) / "corpus_manifest.jsonl"
    if not manifest.exists():
        generate_fixture(output)
    records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    client = _get_qdrant_client()
    if not client.collection_exists(FIXTURE_COLLECTION):
        from mech_chatbot.rag.bootstrap import client as _bootstrap_client  # noqa: F401
    from scripts.create_qdrant_indexes import create_indexes
    if not create_indexes():
        raise RuntimeError("could not create graph fixture payload indexes")
    _ensure_engine()
    fixture_names = json.dumps([record["filename"] for record in records])
    with engine.begin() as connection:
        orphan_ids = [int(row[0]) for row in connection.execute(text("""
            SELECT DocID FROM dbo.TaiLieu
            WHERE SourceSystem=:batch
              AND TenFile NOT IN (SELECT [value] FROM OPENJSON(:names))
        """), {"batch": FIXTURE_BATCH, "names": fixture_names}).all()]
        if orphan_ids:
            connection.execute(text("""
                DELETE p FROM dbo.GraphExtractionProposal p
                WHERE p.SourceDocID IN (SELECT DocID FROM dbo.TaiLieu WHERE SourceSystem=:batch AND TenFile NOT IN (SELECT [value] FROM OPENJSON(:names)));
                DELETE e FROM dbo.KnowledgeGraphEdge e
                WHERE e.SourceDocID IN (SELECT DocID FROM dbo.TaiLieu WHERE SourceSystem=:batch AND TenFile NOT IN (SELECT [value] FROM OPENJSON(:names)))
                   OR e.SourceNodeID IN (SELECT NodeID FROM dbo.KnowledgeGraphNode WHERE SourceDocID IN (SELECT DocID FROM dbo.TaiLieu WHERE SourceSystem=:batch AND TenFile NOT IN (SELECT [value] FROM OPENJSON(:names))))
                   OR e.TargetNodeID IN (SELECT NodeID FROM dbo.KnowledgeGraphNode WHERE SourceDocID IN (SELECT DocID FROM dbo.TaiLieu WHERE SourceSystem=:batch AND TenFile NOT IN (SELECT [value] FROM OPENJSON(:names))));
                DELETE n FROM dbo.KnowledgeGraphNode n
                WHERE n.SourceDocID IN (SELECT DocID FROM dbo.TaiLieu WHERE SourceSystem=:batch AND TenFile NOT IN (SELECT [value] FROM OPENJSON(:names)));
            """), {"batch": FIXTURE_BATCH, "names": fixture_names})
    for orphan_id in orphan_ids:
        if not delete_document_completely(orphan_id, reviewer="graph-eval-reconcile"):
            raise RuntimeError(f"could not remove obsolete fixture DocID {orphan_id}")
    doc_ids = {}
    for record in records:
        with engine.connect() as connection:
            existing = connection.execute(text("""
                SELECT TOP 1 DocID FROM dbo.TaiLieu
                WHERE SourceSystem=:batch AND TenFile=:filename ORDER BY DocID DESC
            """), {"batch": FIXTURE_BATCH, "filename": record["filename"]}).scalar()
            governance = connection.execute(text("""
                SELECT g.KnowledgeOwnerUserID, g.KnowledgeApproverUserID, g.TaxonomyVersion,
                       p.DocumentTypesJson
                FROM dbo.DepartmentKnowledgeGovernance g
                JOIN dbo.DepartmentDomainProfile p ON p.DeptCode=g.DeptCode AND p.IsActive=1
                WHERE g.DeptCode=:department AND g.IsActive=1
            """), {"department": record["department"]}).mappings().first()
        if existing:
            doc_ids[record["key"]] = int(existing)
            with engine.begin() as connection:
                _seed_bom(connection, int(existing), record.get("rows") or [])
                _apply_record_state(connection, int(existing), record["state"])
            continue
        if not governance or not governance["KnowledgeOwnerUserID"] or not governance["KnowledgeApproverUserID"]:
            raise RuntimeError(f"missing governance principals for {record['department']}")
        file_path = Path(output) / record["path"]
        upload_meta = {
            "title": record["title"], "summary": f"Fixture {FIXTURE_BATCH}",
            "tags": f"graph-eval,{FIXTURE_BATCH}", "doc_number": record["doc_number"],
            "effective_date": record["effective_date"], "expiry_date": record["expiry_date"],
            "review_date": record["expiry_date"], "owner_signer": "Graph Eval Owner",
            "language": "vi", "effective_status": "effective", "site": record["site"],
            "knowledge_owner_user_id": int(governance["KnowledgeOwnerUserID"]),
            "knowledge_approver_user_id": int(governance["KnowledgeApproverUserID"]),
            "taxonomy_version": governance["TaxonomyVersion"] or "v1",
        }
        job_id = create_ingestion_job(
            file_name=file_path.name, file_path=str(file_path), thu_muc=record["department"],
            uploaded_by="graph_eval_owner", security_level=record["security_level"],
            site=record["site"], phong_ban=[record["department"]], upload_meta=upload_meta,
        )
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
                UPDATE dbo.TaiLieu SET SourceSystem=:batch, DocNumber=:number, BaseCode=:number,
                    VersionNo=:version, VersionLabel=:label, OwnerDepartment=:department,
                    ClassificationModel='graph-eval-fixture-v1',
                    ClassificationJson=:classification, ClassificationRationale=:rationale,
                    KnowledgeOwnerUserID=:owner, KnowledgeApproverUserID=:approver,
                    TaxonomyVersion=:taxonomy
                WHERE DocID=:doc_id
            """), {
                "batch": FIXTURE_BATCH, "number": record["doc_number"], "version": record["version"],
                "label": f"v{record['version']}", "department": record["department"],
                "classification": json.dumps({
                    "document_type": document_type,
                    "fixture_key": record["key"], "fixture_batch": FIXTURE_BATCH,
                }),
                "rationale": f"Deterministic fixture {FIXTURE_BATCH}",
                "owner": int(governance["KnowledgeOwnerUserID"]),
                "approver": int(governance["KnowledgeApproverUserID"]),
                "taxonomy": governance["TaxonomyVersion"] or "v1", "doc_id": doc_id,
            })
            _seed_bom(connection, doc_id, record.get("rows") or [])
            _seed_document_page(connection, doc_id, record, output)
        if record["state"] not in {"draft", "unpublished"}:
            result = publish_document(
                doc_id, reviewer="graph_eval_approver",
                reviewer_id=int(governance["KnowledgeApproverUserID"]),
                reviewer_roles=["knowledge_approver", "reviewer"],
            )
            if not result:
                delete_document_completely(doc_id, reviewer="graph-eval-failed-publish")
                raise RuntimeError(f"publish failed for {file_path.name}: {result.error}")
            update_ingestion_job(job_id, "published")
            with engine.begin() as connection:
                _apply_record_state(connection, doc_id, record["state"])
        doc_ids[record["key"]] = doc_id

    with engine.begin() as connection:
        family_id = connection.execute(text("""
            MERGE dbo.DocumentFamily AS target
            USING (SELECT :code BaseCode) AS source ON target.BaseCode=source.BaseCode
            WHEN MATCHED THEN UPDATE SET FamilyName=:name, Department='Technical', UpdatedAt=GETDATE()
            WHEN NOT MATCHED THEN INSERT (BaseCode,FamilyName,Department)
                VALUES (:code,:name,'Technical')
            OUTPUT INSERTED.FamilyID;
        """), {"code": "GRAPH-EVAL-ASM-001", "name": "Graph Eval Assembly"}).scalar_one()
        connection.execute(text("""
            UPDATE dbo.TaiLieu SET FamilyID=:family, IsCurrent=0, Servable=0,
                LifecycleStatus='superseded', EffectiveStatus='superseded'
            WHERE DocID=:old;
            UPDATE dbo.TaiLieu SET FamilyID=:family, SupersedesDocID=:old,
                IsCurrent=1, Servable=1
            WHERE DocID=:current;
        """), {
            "family": family_id, "old": doc_ids["assembly_v1"],
            "current": doc_ids["assembly_v2"],
        })
        for record in records:
            _seed_document_page(connection, doc_ids[record["key"]], record, output)
    for record in records:
        update_qdrant_metadata(doc_ids[record["key"]], _metadata(record, doc_ids[record["key"]]), require_points=True)
    counts = seed(source_system=FIXTURE_BATCH)
    return {"schema": "graph-eval-ingest-v1", "batch": FIXTURE_BATCH, "documents": len(doc_ids), **counts}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(ingest_fixture(args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
