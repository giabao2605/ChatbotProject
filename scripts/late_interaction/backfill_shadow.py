"""Create or resumably backfill the BGE-M3 MaxSim shadow collection."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.documents import Document
from qdrant_client import QdrantClient, models


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for item in (ROOT, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from mech_chatbot.rag.late_interaction import (  # noqa: E402
    DEFAULT_COLLECTION,
    candidate_key,
    encode_documents,
    preflight,
    validate_shadow_schema,
)


def _client():
    load_dotenv(ROOT / ".env")
    return QdrantClient(
        url=os.environ["QDRANT_URL"],
        api_key=os.environ.get("QDRANT_API_KEY"),
        timeout=120,
    )


def ensure_shadow_collection(client, collection=DEFAULT_COLLECTION, vector_size=1024):
    if client.collection_exists(collection):
        schema = validate_shadow_schema(client.get_collection(collection))
        if not schema["passed"]:
            raise RuntimeError(f"existing shadow collection has incompatible schema: {schema['checks']}")
    else:
        client.create_collection(
            collection_name=collection,
            vectors_config={
                "late": models.VectorParams(
                    size=int(vector_size),
                    distance=models.Distance.COSINE,
                    datatype=models.Datatype.FLOAT16,
                    hnsw_config=models.HnswConfigDiff(m=0),
                    multivector_config=models.MultiVectorConfig(
                        comparator=models.MultiVectorComparator.MAX_SIM
                    ),
                )
            },
        )
        client.create_payload_index(
            collection_name=collection,
            field_name="candidate_key",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )


def _document(point):
    payload = getattr(point, "payload", {}) or {}
    metadata = payload.get("metadata") or {}
    content = payload.get("page_content") or payload.get("text") or metadata.get("noi_dung_goc") or ""
    return Document(page_content=str(content), metadata=dict(metadata))


def _existing_keys(client, collection, keys):
    if not keys:
        return set()
    points, _ = client.scroll(
        collection_name=collection,
        scroll_filter=models.Filter(must=[models.FieldCondition(
            key="candidate_key", match=models.MatchAny(any=list(keys))
        )]),
        limit=len(keys),
        with_payload=["candidate_key"],
        with_vectors=False,
    )
    return {
        str((getattr(point, "payload", {}) or {}).get("candidate_key") or "")
        for point in points
    }


def _governance_payload(metadata):
    return {
        "doc_id": metadata.get("doc_id"),
        "page": metadata.get("trang_so"),
        "version_no": metadata.get("version_no"),
        "department": metadata.get("owner_department") or metadata.get("thu_muc") or metadata.get("phong_ban_quyen"),
        "site": metadata.get("site"),
        "security_level": metadata.get("security_level") or "confidential",
        "publication_state": metadata.get("publication_state"),
        "lifecycle_status": metadata.get("lifecycle_status"),
        "review_status": metadata.get("review_status"),
        "is_current": metadata.get("is_current"),
        "servable": metadata.get("servable"),
    }


def backfill(client, source_collection, shadow_collection=DEFAULT_COLLECTION, batch_size=8):
    ensure_shadow_collection(client, shadow_collection)
    offset = None
    uploaded = 0
    skipped = 0
    already_indexed = 0
    governance_rejected = 0
    eligible_total = 0
    while True:
        points, offset = client.scroll(
            collection_name=source_collection,
            offset=offset,
            limit=max(1, min(int(batch_size), 32)),
            with_payload=True,
            with_vectors=False,
        )
        documents = [_document(point) for point in points]
        eligible = [document for document in documents if document.page_content.strip() and document.metadata.get("doc_id")]
        skipped += len(documents) - len(eligible)
        eligible_total += len(eligible)
        if eligible:
            keyed = [(candidate_key(document), document) for document in eligible]
            existing = _existing_keys(client, shadow_collection, [key for key, _ in keyed])
            already_indexed += len(existing)
            pending = []
            for key, document in keyed:
                if key in existing:
                    continue
                governance = _governance_payload(document.metadata)
                required = ("doc_id", "page", "version_no", "department", "site", "publication_state",
                            "lifecycle_status", "review_status", "is_current", "servable")
                if any(governance.get(field) is None for field in required):
                    governance_rejected += 1
                    continue
                pending.append((key, document, governance))
            vectors = encode_documents([document.page_content for _, document, _ in pending]) if pending else []
            shadow_points = []
            for (key, document, governance), vector in zip(pending, vectors):
                shadow_points.append(models.PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, key)),
                    vector={"late": vector},
                    payload={
                        "candidate_key": key,
                        **governance,
                    },
                ))
            client.upsert(collection_name=shadow_collection, points=shadow_points, wait=True)
            uploaded += len(shadow_points)
        if offset is None:
            break
    covered = uploaded + already_indexed
    return {
        "uploaded": uploaded,
        "already_indexed": already_indexed,
        "skipped": skipped,
        "governance_rejected": governance_rejected,
        "eligible": eligible_total,
        "coverage": covered / eligible_total if eligible_total else 1.0,
        "collection": shadow_collection,
    }


def smoke_encoder():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, "-c", (
            "from mech_chatbot.rag.late_interaction import encode_query; "
            "vectors=encode_query('BOM smoke test'); "
            "assert vectors and vectors[0]; print(len(vectors), len(vectors[0]))"
        )],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return {
        "passed": completed.returncode == 0,
        "return_code": completed.returncode,
        "shape": completed.stdout.strip() if completed.returncode == 0 else None,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-collection", default=os.getenv("QDRANT_COLLECTION", "TaiLieuKyThuat_v2"))
    parser.add_argument("--shadow-collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--smoke-encoder", action="store_true")
    args = parser.parse_args(argv)
    client = _client()
    report = preflight(client, args.source_collection)
    print(report)
    if args.smoke_encoder:
        encoder_report = smoke_encoder()
        print({"encoder": encoder_report})
        if not encoder_report["passed"]:
            return 2
    if not report["passed"] or args.preflight_only:
        return 0 if report["passed"] else 2
    print(backfill(client, args.source_collection, args.shadow_collection, args.batch_size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
