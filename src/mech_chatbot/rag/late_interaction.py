"""Late-interaction reranking over an isolated Qdrant shadow collection."""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from functools import lru_cache

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("ONEDNN_MAX_CPU_ISA", "AVX2")

from qdrant_client import models


DEFAULT_COLLECTION = "MechChatbot_LateInteraction_v1"


@dataclass(frozen=True)
class LateInteractionResult:
    documents: tuple
    candidate_count: int
    shadow_hits: int
    coverage: float
    used_shadow: bool
    fallback_reason: str | None
    encode_latency_ms: float
    query_latency_ms: float
    total_latency_ms: float


def enabled() -> bool:
    truthy = {"1", "true", "yes", "y", "on"}
    return os.getenv("RAG_LATE_INTERACTION_ENABLED", "false").strip().lower() in truthy and os.getenv(
        "RAG_LATE_ENCODER_READY", "false"
    ).strip().lower() in truthy


def candidate_key(document) -> str:
    metadata = getattr(document, "metadata", {}) or {}
    content = str(metadata.get("noi_dung_goc") or getattr(document, "page_content", "") or "")
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    identity = "|".join(
        str(value if value is not None else "")
        for value in (
            metadata.get("doc_id"),
            metadata.get("trang_so"),
            metadata.get("chunk_index"),
            content_hash,
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def _encoder():
    try:
        from FlagEmbedding import BGEM3FlagModel
    except ImportError as exc:
        raise RuntimeError("FlagEmbedding is required for BGE-M3 ColBERT vectors") from exc
    return BGEM3FlagModel(
        os.getenv("RAG_LATE_MODEL", "BAAI/bge-m3"),
        use_fp16=os.getenv("EMBEDDING_DEVICE", "cpu").lower().startswith("cuda"),
    )


def encode_query(text: str):
    encoded = _encoder().encode(
        [str(text or "")],
        max_length=int(os.getenv("RAG_LATE_QUERY_MAX_LENGTH", "64")),
        return_dense=False,
        return_sparse=False,
        return_colbert_vecs=True,
    )
    vectors = encoded["colbert_vecs"][0]
    return vectors.tolist() if hasattr(vectors, "tolist") else vectors


def encode_documents(texts):
    encoded = _encoder().encode(
        [str(text or "") for text in texts],
        max_length=int(os.getenv("RAG_LATE_DOCUMENT_MAX_LENGTH", "48")),
        return_dense=False,
        return_sparse=False,
        return_colbert_vecs=True,
    )
    return [item.tolist() if hasattr(item, "tolist") else item for item in encoded["colbert_vecs"]]


def attempt_shadow_rerank(
    candidates,
    query: str,
    client,
    *,
    top_n: int | None = None,
    collection_name: str | None = None,
    query_encoder=None,
) -> LateInteractionResult:
    """Use MaxSim only when every authorized input candidate has a shadow point."""
    started = time.perf_counter()
    docs = list(candidates or ())
    candidate_count = len(docs)
    if not docs:
        return LateInteractionResult((), 0, 0, 1.0, False, "empty_candidates", 0.0, 0.0, 0.0)
    by_key = {candidate_key(document): document for document in docs}
    if len(by_key) != candidate_count:
        total_ms = (time.perf_counter() - started) * 1000
        return LateInteractionResult(
            tuple(docs), candidate_count, 0, 0.0, False, "duplicate_candidate_key", 0.0, 0.0, total_ms,
        )
    index_version = os.getenv("RAG_LATE_INDEX_VERSION", "late-v2")
    try:
        encode_started = time.perf_counter()
        query_vectors = (query_encoder or encode_query)(query)
        encode_ms = (time.perf_counter() - encode_started) * 1000
    except Exception:
        encode_ms = (time.perf_counter() - encode_started) * 1000
        total_ms = (time.perf_counter() - started) * 1000
        return LateInteractionResult(
            tuple(docs), candidate_count, 0, 0.0, False, "encoder_error", encode_ms, 0.0, total_ms,
        )
    try:
        query_started = time.perf_counter()
        response = client.query_points(
            collection_name=collection_name or os.getenv("RAG_LATE_COLLECTION", DEFAULT_COLLECTION),
            query=query_vectors,
            using="late",
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="candidate_key",
                        match=models.MatchAny(any=list(by_key)),
                    ),
                    models.FieldCondition(
                        key="index_version",
                        match=models.MatchValue(value=index_version),
                    ),
                ]
            ),
            with_payload=["candidate_key", "index_version"],
            limit=candidate_count,
        )
        query_ms = (time.perf_counter() - query_started) * 1000
    except Exception:
        query_ms = (time.perf_counter() - query_started) * 1000
        total_ms = (time.perf_counter() - started) * 1000
        return LateInteractionResult(
            tuple(docs), candidate_count, 0, 0.0, False, "shadow_query_error",
            encode_ms, query_ms, total_ms,
        )

    ranked = []
    seen = set()
    for point in getattr(response, "points", ()):
        payload = getattr(point, "payload", {}) or {}
        key = str(payload.get("candidate_key") or "")
        if payload.get("index_version") != index_version or key not in by_key or key in seen:
            continue
        seen.add(key)
        ranked.append((by_key[key], float(getattr(point, "score", 0.0))))
    shadow_hits = len(seen)
    coverage = shadow_hits / candidate_count
    total_ms = (time.perf_counter() - started) * 1000
    if shadow_hits != candidate_count:
        return LateInteractionResult(
            tuple(docs), candidate_count, shadow_hits, coverage, False, "partial_coverage",
            encode_ms, query_ms, total_ms,
        )
    limit = max(1, min(int(top_n or candidate_count), candidate_count))
    selected = []
    for document, score in ranked[:limit]:
        document.metadata["relevance_score"] = score
        document.metadata["rerank_backend"] = "late_interaction"
        selected.append(document)
    return LateInteractionResult(
        tuple(selected), candidate_count, shadow_hits, 1.0, True, None,
        encode_ms, query_ms, total_ms,
    )


def validate_shadow_schema(collection) -> dict:
    """Validate the immutable serving contract of an existing shadow collection."""
    config = getattr(collection, "config", None)
    params = getattr(config, "params", None)
    vectors = getattr(params, "vectors", {}) or {}
    late = vectors.get("late") if isinstance(vectors, dict) else None
    datatype = str(getattr(late, "datatype", "") or "").lower()
    hnsw = getattr(late, "hnsw_config", None)
    multivector = getattr(late, "multivector_config", None)
    comparator = str(getattr(multivector, "comparator", "") or "").lower()
    payload_schema = getattr(collection, "payload_schema", {}) or {}
    candidate_schema = payload_schema.get("candidate_key") if isinstance(payload_schema, dict) else None
    index_version_schema = payload_schema.get("index_version") if isinstance(payload_schema, dict) else None
    candidate_type = str(
        getattr(candidate_schema, "data_type", None)
        or getattr(candidate_schema, "type", None)
        or candidate_schema
        or ""
    ).lower()
    index_version_type = str(
        getattr(index_version_schema, "data_type", None)
        or getattr(index_version_schema, "type", None)
        or index_version_schema
        or ""
    ).lower()
    checks = {
        "named_vector_late": late is not None,
        "vector_size_1024": int(getattr(late, "size", 0) or 0) == 1024,
        "float16": "float16" in datatype,
        "hnsw_disabled": int(getattr(hnsw, "m", -1) if hnsw is not None else -1) == 0,
        "max_sim": "max_sim" in comparator or "maxsim" in comparator,
        "candidate_key_index": "keyword" in candidate_type,
        "index_version_index": "keyword" in index_version_type,
    }
    return {"passed": all(checks.values()), "checks": checks}


def preflight(
    client,
    source_collection: str,
    shadow_collection: str = DEFAULT_COLLECTION,
    *,
    encoder_report=None,
    backfill_report=None,
    storage_report=None,
) -> dict:
    """Report server and source-collection capability without mutating Qdrant."""
    report = {
        "server_version": None,
        "source_collection": source_collection,
        "points_count": None,
        "vectors_count": None,
        "shadow_points_count": 0,
        "shadow_point_ratio": 0.0,
        "multivector_client_supported": all(
            hasattr(models, name) for name in ("MultiVectorConfig", "MultiVectorComparator")
        ),
        "capability_passed": False,
        "ready_for_serving": False,
        "passed": False,
    }
    try:
        info = client.info()
        collection = client.get_collection(source_collection)
        report["server_version"] = str(getattr(info, "version", "") or "")
        report["points_count"] = getattr(collection, "points_count", None)
        report["vectors_count"] = getattr(collection, "vectors_count", None)
        match = re.match(r"^(\d+)\.(\d+)", report["server_version"])
        server_supported = bool(match and tuple(map(int, match.groups())) >= (1, 10))
        shadow_exists = client.collection_exists(shadow_collection)
        report["shadow_exists"] = shadow_exists
        if shadow_exists:
            shadow = client.get_collection(shadow_collection)
            report["shadow_points_count"] = int(getattr(shadow, "points_count", 0) or 0)
            source_points = int(report["points_count"] or 0)
            report["shadow_point_ratio"] = (
                report["shadow_points_count"] / source_points if source_points else 0.0
            )
            report["shadow_schema"] = validate_shadow_schema(shadow)
        else:
            report["shadow_schema"] = {"passed": True, "checks": {"collection_absent": True}}
        report["server_multivector_supported"] = server_supported
        encoder_passed = bool((encoder_report or {}).get("passed"))
        report["encoder"] = encoder_report or {"passed": False, "reason": "not_checked"}
        report["capability_passed"] = bool(
            server_supported
            and report["multivector_client_supported"]
            and report["shadow_schema"]["passed"]
            and encoder_passed
        )
        coverage = float((backfill_report or {}).get("coverage") or 0.0)
        governance_drift = int((backfill_report or {}).get("governance_rejected") or 0)
        provenance_drift = int((backfill_report or {}).get("provenance_drift") or 0)
        vector_schema_rejected = int(
            (backfill_report or {}).get("vector_schema_rejected") or 0
        )
        orphan_points = int((backfill_report or {}).get("orphan_points") or 0)
        storage_ratio = float((storage_report or {}).get("shadow_storage_ratio") or 0.0)
        report["ready_for_serving"] = bool(
            report["capability_passed"]
            and shadow_exists
            and coverage >= 1.0
            and governance_drift == 0
            and provenance_drift == 0
            and vector_schema_rejected == 0
            and orphan_points == 0
            and storage_report is not None
            and float((storage_report or {}).get("shadow_vector_bytes") or 0) > 0
            and storage_ratio <= 25.0
        )
        report["passed"] = report["ready_for_serving"]
    except Exception as exc:
        report["error"] = type(exc).__name__
    return report
