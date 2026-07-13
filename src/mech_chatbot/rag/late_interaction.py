"""Late-interaction reranking over an isolated Qdrant shadow collection."""

from __future__ import annotations

import hashlib
import os
import re
from functools import lru_cache

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("ONEDNN_MAX_CPU_ISA", "AVX2")

from qdrant_client import models


DEFAULT_COLLECTION = "MechChatbot_LateInteraction_v1"


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
        return_dense=False,
        return_sparse=False,
        return_colbert_vecs=True,
    )
    vectors = encoded["colbert_vecs"][0]
    return vectors.tolist() if hasattr(vectors, "tolist") else vectors


def encode_documents(texts):
    encoded = _encoder().encode(
        [str(text or "") for text in texts],
        return_dense=False,
        return_sparse=False,
        return_colbert_vecs=True,
    )
    return [item.tolist() if hasattr(item, "tolist") else item for item in encoded["colbert_vecs"]]


def rerank_with_shadow(
    candidates,
    query_vectors,
    client,
    *,
    collection_name: str | None = None,
    top_n: int | None = None,
    fallback=None,
):
    """Rerank only the already-authorized candidate set using MaxSim scores."""
    docs = list(candidates or [])
    if not docs:
        return []
    top_n = max(1, min(int(top_n or len(docs)), len(docs)))
    by_key = {candidate_key(document): document for document in docs}
    try:
        response = client.query_points(
            collection_name=collection_name or os.getenv("RAG_LATE_COLLECTION", DEFAULT_COLLECTION),
            query=query_vectors,
            using="late",
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="candidate_key",
                        match=models.MatchAny(any=list(by_key)),
                    )
                ]
            ),
            with_payload=True,
            limit=top_n,
        )
        ranked = []
        seen = set()
        for point in getattr(response, "points", ()):
            key = str((getattr(point, "payload", {}) or {}).get("candidate_key") or "")
            if key not in by_key or key in seen:
                continue
            seen.add(key)
            document = by_key[key]
            document.metadata["relevance_score"] = float(getattr(point, "score", 0.0))
            document.metadata["rerank_backend"] = "late_interaction"
            ranked.append(document)
        missing = [document for key, document in by_key.items() if key not in seen]
        if missing and fallback is not None:
            missing = list(fallback(missing))
        ranked.extend(missing)
        return ranked[:top_n]
    except Exception:
        if fallback is not None:
            return list(fallback(docs))[:top_n]
        return docs[:top_n]


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
    candidate_type = str(
        getattr(candidate_schema, "data_type", None)
        or getattr(candidate_schema, "type", None)
        or candidate_schema
        or ""
    ).lower()
    checks = {
        "named_vector_late": late is not None,
        "vector_size_1024": int(getattr(late, "size", 0) or 0) == 1024,
        "float16": "float16" in datatype,
        "hnsw_disabled": int(getattr(hnsw, "m", -1) if hnsw is not None else -1) == 0,
        "max_sim": "max_sim" in comparator or "maxsim" in comparator,
        "candidate_key_index": "keyword" in candidate_type,
    }
    return {"passed": all(checks.values()), "checks": checks}


def preflight(client, source_collection: str, shadow_collection: str = DEFAULT_COLLECTION) -> dict:
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
        if client.collection_exists(shadow_collection):
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
        report["passed"] = bool(
            server_supported
            and report["multivector_client_supported"]
            and report["shadow_schema"]["passed"]
        )
    except Exception as exc:
        report["error"] = type(exc).__name__
    return report
