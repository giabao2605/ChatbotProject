"""Create or resumably backfill the BGE-M3 MaxSim shadow collection."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
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
    encode_query,
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
        client.create_payload_index(
            collection_name=collection,
            field_name="index_version",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )


def _document(point):
    payload = getattr(point, "payload", {}) or {}
    metadata = payload.get("metadata") or {}
    content = metadata.get("noi_dung_goc") or payload.get("page_content") or payload.get("text") or ""
    metadata = dict(metadata)
    metadata["_source_point_id"] = str(getattr(point, "id", "") or "")
    return Document(page_content=str(content), metadata=metadata)


def _existing_records(client, collection, keys):
    if not keys:
        return set()
    points, _ = client.scroll(
        collection_name=collection,
        scroll_filter=models.Filter(must=[models.FieldCondition(
            key="candidate_key", match=models.MatchAny(any=list(keys))
        )]),
        limit=len(keys),
        with_payload=True,
        with_vectors=["late"],
    )
    return {
        str((getattr(point, "payload", {}) or {}).get("candidate_key") or ""): point
        for point in points
    }


def _governance_payload(metadata):
    return {
        "doc_id": metadata.get("doc_id"),
        "page": metadata.get("trang_so"),
        "version_no": metadata.get("version_no"),
        "department": metadata.get("owner_department") or metadata.get("thu_muc") or metadata.get("phong_ban_quyen"),
        "site": metadata.get("site"),
        "security_level": metadata.get("security_level"),
        "publication_state": metadata.get("publication_state"),
        "lifecycle_status": metadata.get("lifecycle_status"),
        "review_status": metadata.get("review_status"),
        "is_current": metadata.get("is_current"),
        "servable": metadata.get("servable"),
    }


def _canonical_record(document, *, index_version):
    metadata = document.metadata or {}
    content = str(metadata.get("noi_dung_goc") or document.page_content or "")
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    governance = _governance_payload(metadata)
    required = (
        "doc_id", "page", "version_no", "department", "site", "security_level",
        "publication_state", "lifecycle_status", "review_status", "is_current", "servable",
    )
    missing = tuple(field for field in required if governance.get(field) is None)
    canonical_chunk = metadata.get("chunk_index")
    canonical_chunk = "" if canonical_chunk is None else str(canonical_chunk)
    identity = {
        "candidate_key": candidate_key(document),
        "content_hash": content_hash,
        "source_point_id": str(metadata.get("_source_point_id") or ""),
        "source_point_ids": [str(metadata.get("_source_point_id") or "")],
        "doc_id": governance["doc_id"],
        "page": governance["page"],
        "canonical_chunk_index": canonical_chunk,
        "version_no": governance["version_no"],
        "index_version": str(index_version),
    }
    governance_json = json.dumps(governance, sort_keys=True, ensure_ascii=False, default=str)
    payload = {
        **identity,
        **governance,
        "governance_fingerprint": hashlib.sha256(governance_json.encode("utf-8")).hexdigest(),
    }
    return payload, missing


def _core_provenance_matches(existing, desired):
    fields = (
        "candidate_key", "content_hash", "doc_id", "page",
        "canonical_chunk_index", "version_no",
    )
    return all(existing.get(field) == desired.get(field) for field in fields)


def _valid_existing_vector(point, token_vector_count):
    vectors = getattr(point, "vector", None)
    late = vectors.get("late") if isinstance(vectors, dict) else None
    if not isinstance(late, list) or not late:
        return False
    expected_count = int(token_vector_count or 0)
    return (
        expected_count == len(late)
        and all(isinstance(token, list) and len(token) == 1024 for token in late)
    )


def estimate_storage(
    *, source_points, source_dimension, shadow_token_vectors, shadow_dimension=1024,
):
    source_dense_bytes = int(source_points) * int(source_dimension) * 4
    shadow_vector_bytes = int(shadow_token_vectors) * int(shadow_dimension) * 2
    return {
        "source_dense_bytes": source_dense_bytes,
        "shadow_vector_bytes": shadow_vector_bytes,
        "shadow_storage_ratio": (
            shadow_vector_bytes / source_dense_bytes if source_dense_bytes else float("inf")
        ),
    }


def build_readiness_artifact(
    *,
    qdrant_report,
    encoder_report,
    backfill_report,
    storage_report,
    benchmark_report,
    commit_sha,
    started_at,
    ended_at,
    configuration,
):
    safe_configuration = {
        key: configuration.get(key)
        for key in (
            "source_collection", "shadow_collection", "index_version", "batch_size",
            "benchmark_iterations",
            "document_max_length", "query_max_length",
        )
        if configuration.get(key) is not None
    }
    backfill_report = backfill_report or {}
    storage_report = storage_report or {}
    return {
        "schema": "late-interaction-readiness-v1",
        "commit_sha": str(commit_sha),
        "started_at": str(started_at),
        "ended_at": str(ended_at),
        "configuration": safe_configuration,
        "capability_passed": bool((qdrant_report or {}).get("capability_passed")),
        "ready_for_serving": bool((qdrant_report or {}).get("ready_for_serving")),
        "shadow_coverage": float(backfill_report.get("coverage") or 0.0),
        "governance_drift": int(backfill_report.get("governance_rejected") or 0),
        "provenance_drift": int(backfill_report.get("provenance_drift") or 0),
        "vector_schema_rejected": int(
            backfill_report.get("vector_schema_rejected") or 0
        ),
        "orphan_points": int(backfill_report.get("orphan_points") or 0),
        "shadow_storage_ratio": float(storage_report.get("shadow_storage_ratio") or 0.0),
        "qdrant": qdrant_report or {},
        "encoder": encoder_report or {},
        "backfill": backfill_report,
        "storage": storage_report,
        "benchmark": benchmark_report or {},
    }


def write_readiness_artifacts(output_dir, artifact):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "readiness.json"
    markdown_path = output_dir / "readiness.md"
    json_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    checks = [
        ("Capability", artifact.get("capability_passed")),
        ("Serving ready", artifact.get("ready_for_serving")),
        ("Coverage", artifact.get("shadow_coverage")),
        ("Storage ratio", artifact.get("shadow_storage_ratio")),
        ("Governance drift", artifact.get("governance_drift")),
        ("Provenance drift", artifact.get("provenance_drift")),
        ("Vector schema rejected", artifact.get("vector_schema_rejected")),
        ("Orphan points", artifact.get("orphan_points")),
    ]
    markdown = [
        "# Late Interaction Readiness",
        "",
        f"- Commit: `{artifact.get('commit_sha')}`",
        f"- Window: `{artifact.get('started_at')}` to `{artifact.get('ended_at')}`",
        "",
        "| Check | Value |",
        "|---|---:|",
    ]
    markdown.extend(f"| {name} | {value} |" for name, value in checks)
    for title, key in (
        ("Qdrant", "qdrant"),
        ("Encoder", "encoder"),
        ("Backfill", "backfill"),
        ("Storage", "storage"),
        ("Benchmark", "benchmark"),
        ("Configuration", "configuration"),
    ):
        markdown.extend([
            "",
            f"## {title}",
            "",
            "```json",
            json.dumps(artifact.get(key) or {}, ensure_ascii=False, indent=2),
            "```",
        ])
    markdown_path.write_text("\n".join(markdown) + "\n", encoding="utf-8")
    return json_path, markdown_path


def backfill(
    client,
    source_collection,
    shadow_collection=DEFAULT_COLLECTION,
    batch_size=8,
    *,
    index_version="late-v2",
    prune_orphans=False,
    encoder=encode_documents,
):
    ensure_shadow_collection(client, shadow_collection)
    offset = None
    uploaded = 0
    source_total = 0
    eligible = 0
    already_valid = 0
    governance_rejected = 0
    governance_missing_fields = Counter()
    non_servable_sources = 0
    governance_repaired = 0
    provenance_drift = 0
    vector_schema_rejected = 0
    source_ids_repaired = 0
    stale_reindexed = 0
    source_keys = set()
    source_key_counts = Counter()
    covered_keys = set()
    blocked_keys = set()
    source_governance = {}
    while True:
        points, offset = client.scroll(
            collection_name=source_collection,
            offset=offset,
            limit=max(1, min(int(batch_size), 32)),
            with_payload=True,
            with_vectors=False,
        )
        source_total += len(points)
        documents = [_document(point) for point in points]
        if documents:
            grouped_records = {}
            for document in documents:
                payload, missing = _canonical_record(document, index_version=index_version)
                key = payload["candidate_key"]
                if not document.page_content.strip():
                    missing = (*missing, "content")
                if missing:
                    governance_rejected += 1
                    governance_missing_fields.update(missing)
                    continue
                non_servable_sources += int(payload["servable"] is not True)
                eligible += 1
                source_keys.add(key)
                source_key_counts[key] += 1
                previous_governance = source_governance.get(key)
                if (
                    previous_governance is not None
                    and previous_governance != payload["governance_fingerprint"]
                ):
                    provenance_drift += 1
                    blocked_keys.add(key)
                    covered_keys.discard(key)
                    continue
                source_governance[key] = payload["governance_fingerprint"]
                previous = grouped_records.get(key)
                if previous is not None:
                    _, previous_payload = previous
                    if (
                        not _core_provenance_matches(previous_payload, payload)
                        or previous_payload["governance_fingerprint"] != payload["governance_fingerprint"]
                    ):
                        provenance_drift += 1
                        blocked_keys.add(key)
                        covered_keys.discard(key)
                        grouped_records.pop(key, None)
                        continue
                    previous_payload["source_point_ids"] = sorted(set(
                        previous_payload["source_point_ids"] + payload["source_point_ids"]
                    ))
                    previous_payload["source_point_id"] = previous_payload["source_point_ids"][0]
                    continue
                if key not in blocked_keys:
                    grouped_records[key] = (document, payload)
            records = [
                (key, document, payload)
                for key, (document, payload) in grouped_records.items()
            ]
            existing = _existing_records(
                client, shadow_collection, [key for key, _, _ in records],
            )
            pending = []
            for key, document, payload in records:
                existing_point = existing.get(key)
                if existing_point is not None:
                    existing_payload = getattr(existing_point, "payload", {}) or {}
                    if not _core_provenance_matches(existing_payload, payload):
                        provenance_drift += 1
                        blocked_keys.add(key)
                        covered_keys.discard(key)
                        continue
                    existing_ids = existing_payload.get("source_point_ids") or [
                        existing_payload.get("source_point_id")
                    ]
                    merged_ids = sorted(set(
                        str(item) for item in existing_ids + payload["source_point_ids"] if item
                    ))
                    payload["source_point_ids"] = merged_ids
                    payload["source_point_id"] = merged_ids[0] if merged_ids else ""
                    if not _valid_existing_vector(
                        existing_point, existing_payload.get("token_vector_count"),
                    ):
                        vector_schema_rejected += 1
                        blocked_keys.add(key)
                        covered_keys.discard(key)
                        continue
                    if existing_payload.get("index_version") != str(index_version):
                        pending.append((key, document, payload, True))
                        continue
                    governance_changed = (
                        existing_payload.get("governance_fingerprint") != payload["governance_fingerprint"]
                    )
                    source_ids_changed = sorted(existing_ids) != merged_ids
                    if governance_changed or source_ids_changed:
                        client.set_payload(
                            collection_name=shadow_collection,
                            payload=payload,
                            points=[getattr(existing_point, "id")],
                            wait=True,
                        )
                        governance_repaired += int(governance_changed)
                        source_ids_repaired += int(source_ids_changed)
                    else:
                        already_valid += 1
                    covered_keys.add(key)
                    continue
                pending.append((key, document, payload, False))
            vectors = encoder([document.page_content for _, document, _, _ in pending]) if pending else []
            if len(vectors) != len(pending):
                raise RuntimeError("encoder returned an unexpected vector count")
            shadow_points = []
            for (key, _document_item, payload, is_stale), vector in zip(pending, vectors):
                shadow_points.append(models.PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, key)),
                    vector={"late": vector},
                    payload={
                        **payload,
                        "token_vector_count": len(vector),
                    },
                ))
                covered_keys.add(key)
                if is_stale:
                    stale_reindexed += 1
            if shadow_points:
                client.upsert(collection_name=shadow_collection, points=shadow_points, wait=True)
            uploaded += sum(not item[3] for item in pending)
        if offset is None:
            break
    shadow_points = []
    shadow_offset = None
    while True:
        page, shadow_offset = client.scroll(
            collection_name=shadow_collection,
            offset=shadow_offset,
            limit=256,
            with_payload=["candidate_key"],
            with_vectors=False,
        )
        shadow_points.extend(page)
        if shadow_offset is None:
            break
    orphan_ids = [
        getattr(point, "id") for point in shadow_points
        if str((getattr(point, "payload", {}) or {}).get("candidate_key") or "") not in source_keys
    ]
    if prune_orphans and orphan_ids:
        client.delete(
            collection_name=shadow_collection,
            points_selector=models.PointIdsList(points=orphan_ids),
            wait=True,
        )
    covered_sources = sum(
        count for key, count in source_key_counts.items()
        if key in covered_keys and key not in blocked_keys
    )
    return {
        "source_total": source_total,
        "eligible": eligible,
        "non_servable_sources": non_servable_sources,
        "unique_candidates": len(source_keys),
        "duplicate_sources": eligible - len(source_keys),
        "uploaded": uploaded,
        "already_valid": already_valid,
        "governance_repaired": governance_repaired,
        "source_ids_repaired": source_ids_repaired,
        "stale_reindexed": stale_reindexed,
        "governance_rejected": governance_rejected,
        "governance_missing_fields": dict(sorted(governance_missing_fields.items())),
        "provenance_drift": provenance_drift,
        "vector_schema_rejected": vector_schema_rejected,
        "orphan_points": 0 if prune_orphans else len(orphan_ids),
        "pruned_orphans": len(orphan_ids) if prune_orphans else 0,
        "coverage": covered_sources / eligible if eligible else 1.0,
        "collection": shadow_collection,
        "index_version": str(index_version),
    }


def smoke_encoder():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, "-c", (
            "import json; "
            "from mech_chatbot.rag.late_interaction import encode_documents, encode_query; "
            "query=encode_query('BOM smoke test'); "
            "documents=encode_documents(['BOM PART-A quantity 2']); "
            "assert query and query[0] and documents and documents[0] and documents[0][0]; "
            "assert len(query[0]) == 1024 and len(documents[0][0]) == 1024; "
            "print(json.dumps({'query_shape':[len(query),len(query[0])],"
            "'document_shape':[len(documents[0]),len(documents[0][0])] }))"
        )],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    try:
        shape = json.loads(completed.stdout.strip()) if completed.returncode == 0 else None
    except (TypeError, ValueError, json.JSONDecodeError):
        shape = None
    passed = completed.returncode == 0 and isinstance(shape, dict)
    return {
        "passed": passed,
        "return_code": completed.returncode,
        **(shape or {}),
        "offline": True,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        **({"error": "encoder_smoke_failed"} if not passed else {}),
    }


def _source_dimension(collection):
    params = getattr(getattr(collection, "config", None), "params", None)
    vectors = getattr(params, "vectors", None)
    if isinstance(vectors, dict):
        vector = vectors.get("dense") or next(iter(vectors.values()), None)
    else:
        vector = vectors
    return int(getattr(vector, "size", 0) or 0)


def _shadow_token_vectors(client, collection):
    offset = None
    total = 0
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            offset=offset,
            limit=256,
            with_payload=["token_vector_count"],
            with_vectors=False,
        )
        total += sum(
            int((getattr(point, "payload", {}) or {}).get("token_vector_count") or 0)
            for point in points
        )
        if offset is None:
            return total


def _percentile(values, percentile):
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile + 0.999999)))
    return round(ordered[index], 2)


def benchmark(client, collection, *, iterations=20):
    points, _ = client.scroll(
        collection_name=collection,
        limit=20,
        with_payload=["candidate_key"],
        with_vectors=False,
    )
    keys = [
        str((getattr(point, "payload", {}) or {}).get("candidate_key") or "")
        for point in points
    ]
    keys = [key for key in keys if key]
    if not keys:
        return {"passed": False, "reason": "empty_shadow_collection"}
    encode_times = []
    query_times = []
    for attempt in range(max(1, int(iterations)) + 2):
        started = time.perf_counter()
        vectors = encode_query("BOM smoke test")
        encode_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        client.query_points(
            collection_name=collection,
            query=vectors,
            using="late",
            query_filter=models.Filter(must=[models.FieldCondition(
                key="candidate_key", match=models.MatchAny(any=keys),
            )]),
            with_payload=False,
            limit=len(keys),
        )
        query_ms = (time.perf_counter() - started) * 1000
        if attempt >= 2:
            encode_times.append(encode_ms)
            query_times.append(query_ms)
    return {
        "passed": True,
        "iterations": len(encode_times),
        "encode_p50_ms": _percentile(encode_times, 0.50),
        "encode_p95_ms": _percentile(encode_times, 0.95),
        "query_p50_ms": _percentile(query_times, 0.50),
        "query_p95_ms": _percentile(query_times, 0.95),
    }


def _commit_sha():
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-collection", default=os.getenv("QDRANT_COLLECTION", "TaiLieuKyThuat_v2"))
    parser.add_argument("--shadow-collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--smoke-encoder", action="store_true")
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--benchmark-iterations", type=int, default=20)
    parser.add_argument("--index-version", default=os.getenv("RAG_LATE_INDEX_VERSION", "late-v2"))
    parser.add_argument("--prune-orphans", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    started_at = datetime.now(timezone.utc)
    client = _client()
    encoder_report = smoke_encoder() if args.smoke_encoder else {
        "passed": False, "reason": "not_checked", "offline": True,
    }
    qdrant_report = preflight(
        client,
        args.source_collection,
        args.shadow_collection,
        encoder_report=encoder_report,
    )
    qdrant_report["client_version"] = importlib.metadata.version("qdrant-client")
    backfill_report = {}
    storage_report = {}
    benchmark_report = {}
    if not args.preflight_only and qdrant_report["capability_passed"]:
        backfill_report = backfill(
            client,
            args.source_collection,
            args.shadow_collection,
            args.batch_size,
            index_version=args.index_version,
            prune_orphans=args.prune_orphans,
        )
        source = client.get_collection(args.source_collection)
        storage_report = estimate_storage(
            source_points=int(backfill_report.get("eligible") or 0),
            source_dimension=_source_dimension(source),
            shadow_token_vectors=_shadow_token_vectors(client, args.shadow_collection),
        )
        qdrant_report = preflight(
            client,
            args.source_collection,
            args.shadow_collection,
            encoder_report=encoder_report,
            backfill_report=backfill_report,
            storage_report=storage_report,
        )
        qdrant_report["client_version"] = importlib.metadata.version("qdrant-client")
        if args.benchmark and qdrant_report["ready_for_serving"]:
            benchmark_report = benchmark(
                client, args.shadow_collection, iterations=args.benchmark_iterations,
            )
    ended_at = datetime.now(timezone.utc)
    output_dir = args.output_dir or (
        ROOT / "reports" / "late-interaction" / started_at.strftime("%Y%m%dT%H%M%SZ")
    )
    artifact = build_readiness_artifact(
        qdrant_report=qdrant_report,
        encoder_report=encoder_report,
        backfill_report=backfill_report,
        storage_report=storage_report,
        benchmark_report=benchmark_report,
        commit_sha=_commit_sha(),
        started_at=started_at.isoformat(),
        ended_at=ended_at.isoformat(),
        configuration={
            "source_collection": args.source_collection,
            "shadow_collection": args.shadow_collection,
            "index_version": args.index_version,
            "batch_size": args.batch_size,
            "benchmark_iterations": args.benchmark_iterations if args.benchmark else None,
            "document_max_length": int(os.getenv("RAG_LATE_DOCUMENT_MAX_LENGTH", "48")),
            "query_max_length": int(os.getenv("RAG_LATE_QUERY_MAX_LENGTH", "64")),
        },
    )
    json_path, markdown_path = write_readiness_artifacts(output_dir, artifact)
    print(json.dumps({
        "capability_passed": artifact["capability_passed"],
        "ready_for_serving": artifact["ready_for_serving"],
        "json": str(json_path),
        "markdown": str(markdown_path),
    }, ensure_ascii=False))
    if args.preflight_only:
        return 0 if artifact["capability_passed"] else 2
    return 0 if artifact["ready_for_serving"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
