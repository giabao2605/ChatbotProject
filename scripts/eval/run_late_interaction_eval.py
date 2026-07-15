"""Run the milestone 2.5 three-way retrieval benchmark.

Run this script from the isolated Late Interaction environment so BGE-M3
ColBERT encoding remains outside ``chat_env``. No answer-generation LLM is
called; only the configured Voyage rerank variant uses an external provider.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

from qdrant_client import QdrantClient

from mech_chatbot.config.settings import settings
from mech_chatbot.evaluation.late_interaction import (
    build_report,
    evaluate_variant,
    load_manifest,
    preflight_manifest,
    snapshot_fingerprint,
)
from mech_chatbot.rag.late_interaction import attempt_shadow_rerank


VARIANTS = ("rrf", "voyage", "maxsim")


class IsolatedEncoder:
    def __init__(self, python_executable: Path):
        env = dict(os.environ)
        env["HF_HUB_OFFLINE"] = "1"
        env["PYTHONPATH"] = "src"
        env["PYTHONIOENCODING"] = "utf-8"
        self.process = subprocess.Popen(
            [str(python_executable), "scripts/late_interaction/encoder_worker.py"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            encoding="utf-8",
            env=env,
        )
        self.request_id = 0

    def encode(self, query):
        self.request_id += 1
        request = {"id": self.request_id, "query": str(query or "")}
        self.process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError(f"encoder worker exited with {self.process.poll()}")
            if not line.startswith("LATE_RESULT "):
                continue
            response = json.loads(line[len("LATE_RESULT "):])
            if response.get("error"):
                raise RuntimeError(response["error"])
            if response.get("id") != self.request_id:
                raise RuntimeError("encoder worker response id mismatch")
            return response["vectors"]

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.terminate()



def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _commit_sha():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _provider_configuration(top_k):
    from mech_chatbot.rag.rerank import _voyage_runtime

    runtime = _voyage_runtime()
    endpoint = urlsplit(runtime.endpoint)
    configuration = {
        "dense_model": os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"),
        "sparse_model": "Qdrant/bm25",
        "voyage_model": runtime.model,
        "voyage_endpoint": f"{endpoint.scheme}://{endpoint.netloc}{endpoint.path}",
        "voyage_timeout_seconds": float(os.getenv("VOYAGE_RERANK_TIMEOUT_SECONDS", "15")),
        "candidate_top_k": int(top_k),
        "provider_retry_policy": "none",
        "fallback_policy": "preserve_rrf_closed_set",
    }
    serialized = json.dumps(configuration, sort_keys=True, separators=(",", ":"))
    return configuration, hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _source_from_metadata(metadata, point_id=None):
    return {
        "document": metadata.get("file_goc") or "",
        "doc_id": metadata.get("doc_id"),
        "page": metadata.get("trang_so"),
        "version": metadata.get("version_no"),
        "source_id": str(point_id or ""),
    }


def _ranked_sources(documents):
    return [_source_from_metadata(getattr(doc, "metadata", {}) or {}) for doc in documents]


def _scroll_all(client, collection):
    points, offset = [], None
    while True:
        batch, offset = client.scroll(
            collection_name=collection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points.extend(batch)
        if offset is None:
            return points


def _snapshot_rows(points):
    rows = []
    for point in points:
        metadata = (getattr(point, "payload", {}) or {}).get("metadata") or {}
        content = str(metadata.get("noi_dung_goc") or "")
        rows.append({
            "source_point_id": str(point.id),
            "doc_id": metadata.get("doc_id"),
            "page": metadata.get("trang_so"),
            "chunk_index": metadata.get("chunk_index"),
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "department": metadata.get("phong_ban_quyen"),
            "site": metadata.get("site"),
            "security": metadata.get("security_level"),
            "publication": metadata.get("publication_state"),
            "lifecycle": metadata.get("lifecycle_status"),
            "review": metadata.get("review_status"),
            "current": metadata.get("is_current"),
            "servable": metadata.get("servable"),
        })
    return rows


def _retrieve_in_worker(manifest, repetitions, top_k, cache_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.run(
        [
            sys.executable,
            "scripts/late_interaction/retrieval_worker.py",
            "--manifest", str(manifest),
            "--output", str(cache_path),
            "--repetitions", str(repetitions),
            "--top-k", str(top_k),
        ],
        check=True,
        env=env,
    )
    raw = json.loads(cache_path.read_text(encoding="utf-8"))
    return {
        (int(row["repetition"]), row["case_id"]): (
            [SimpleNamespace(**doc) for doc in row["documents"]],
            float(row["latency_ms"]),
            row["retrieval_mode"],
        )
        for row in raw
    }


def _markdown(report):
    ranked = report["ranked_retrieval"]
    outcomes = report["outcome_confusion"]
    fallback = report["fallback_coverage"]
    return "\n".join([
        f"# Late Interaction evaluation: {report['variant']}",
        "",
        f"- Snapshot: `{report['run_metadata']['snapshot_fingerprint']}`",
        f"- Recall@5 / Recall@10: `{ranked['recall_at_5']:.4f}` / `{ranked['recall_at_10']:.4f}`",
        f"- nDCG@5 / nDCG@10: `{ranked['ndcg_at_5']:.4f}` / `{ranked['ndcg_at_10']:.4f}`",
        f"- Wrong answer / leakage: `{outcomes['wrong_answer']}` / `{outcomes['leakage']}`",
        f"- P50 / P95 ms: `{report['latency_p50_ms']:.2f}` / `{report['latency_p95_ms']:.2f}`",
        f"- Fallback rate: `{fallback['fallback_rate']:.4f}`",
        f"- Shadow coverage: `{fallback['shadow_coverage']:.4f}`",
        "",
    ])


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("data/late_interaction_eval_v1/manifest.jsonl"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--source-collection", default=settings.QDRANT_COLLECTION)
    parser.add_argument("--shadow-collection", default="MechChatbot_LateInteraction_v1")
    parser.add_argument("--index-version", default="late-v2")
    parser.add_argument("--readiness", type=Path, required=True)
    parser.add_argument(
        "--encoder-python",
        type=Path,
        default=Path(".local/late-interaction-env/Scripts/python.exe"),
    )
    args = parser.parse_args(argv)
    if args.repetitions < 2:
        parser.error("--repetitions must be at least 2 to expose provider variance")
    if not args.encoder_python.exists():
        parser.error(f"isolated encoder Python does not exist: {args.encoder_python}")
    run_root = args.output_dir / args.run_id
    if run_root.exists():
        parser.error(f"output already exists: {run_root}")

    started = _utc_now()
    cases = load_manifest(args.manifest)
    manifest_sha256 = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    client = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)
    points = _scroll_all(client, args.source_collection)
    sources = [
        _source_from_metadata((point.payload or {}).get("metadata") or {}, point.id)
        for point in points
    ]
    frozen_rows = _snapshot_rows(points)
    fingerprint = snapshot_fingerprint(frozen_rows, index_version=args.index_version)
    snapshot = {
        "source_collection": args.source_collection,
        "source_point_count": len(points),
        "shadow_collection": args.shadow_collection,
        "shadow_index_version": args.index_version,
        "snapshot_fingerprint": fingerprint,
    }
    preflight = preflight_manifest(cases, available_sources=sources, snapshot=snapshot)
    readiness = json.loads(args.readiness.read_text(encoding="utf-8"))
    readiness_configuration = readiness.get("configuration") or {}
    readiness_matches = (
        readiness.get("ready_for_serving") is True
        and readiness_configuration.get("source_collection") == args.source_collection
        and readiness_configuration.get("shadow_collection") == args.shadow_collection
        and readiness_configuration.get("index_version") == args.index_version
    )
    preflight["readiness_matches"] = readiness_matches
    preflight["passed"] = preflight["passed"] and readiness_matches
    _write_json(run_root / "preflight.json", preflight)
    _write_json(run_root / "source-snapshot.json", {**snapshot, "points": frozen_rows})
    if not preflight["passed"]:
        return 2

    os.environ["RAG_LATE_INDEX_VERSION"] = args.index_version
    provider_configuration, provider_configuration_sha256 = _provider_configuration(args.top_k)
    rows_by_variant = {variant: [] for variant in VARIANTS}
    cache_path = Path(".local/late-interaction-eval") / f"{args.run_id}-candidates.json"
    retrieved = _retrieve_in_worker(args.manifest, args.repetitions, args.top_k, cache_path)
    encoder = IsolatedEncoder(args.encoder_python)
    try:
        for repetition in range(1, args.repetitions + 1):
            pair_rows = {variant: [] for variant in VARIANTS}
            for case in cases:
                candidates, retrieval_ms, retrieval_mode = retrieved[(repetition, case["case_id"])]
                for variant in VARIANTS:
                    result = evaluate_variant(
                        case,
                        candidates,
                        variant=variant,
                        voyage_rerank=(
                            (lambda docs, query: __import__(
                                "mech_chatbot.rag.rerank", fromlist=["voyage_rerank_documents"]
                            ).voyage_rerank_documents(docs, query, top_n=len(docs)))
                            if variant == "voyage" else None
                        ),
                        shadow_rerank=(
                            (lambda docs, query: attempt_shadow_rerank(
                                docs,
                                query,
                                client,
                                top_n=len(docs),
                                collection_name=args.shadow_collection,
                                query_encoder=encoder.encode,
                            )) if variant == "maxsim" else None
                        ),
                    )
                    row = {
                        "repetition": repetition,
                        "case": case,
                        "ranked_sources": _ranked_sources(result.documents),
                        "latency_ms": retrieval_ms + result.latency_ms,
                        "retrieval_mode": retrieval_mode,
                        "used_backend": result.used_backend,
                        "candidate_count": result.candidate_count,
                        "shadow_hits": result.shadow_hits,
                        "coverage": result.coverage if variant == "maxsim" else 1.0,
                        "fallback_reason": result.fallback_reason,
                    }
                    pair_rows[variant].append(row)
                    rows_by_variant[variant].append(row)
    finally:
        encoder.close()
        cache_path.unlink(missing_ok=True)

    for repetition in range(1, args.repetitions + 1):
        pair_metadata = {
            **snapshot,
            "commit_sha": _commit_sha(),
            "manifest_sha256": manifest_sha256,
            "repetition": repetition,
            "provider_configuration": provider_configuration,
            "provider_configuration_sha256": provider_configuration_sha256,
        }
        for variant in VARIANTS:
            pair_rows = [
                row for row in rows_by_variant[variant]
                if row["repetition"] == repetition
            ]
            report = build_report(pair_rows, variant=variant, run_metadata=pair_metadata)
            target = run_root / f"pair-{repetition}" / variant
            _write_json(target / "eval.json", report)
            _write_json(target / "trace.json", {
                "schema": "late-interaction-trace-v1",
                "variant": variant,
                "snapshot_fingerprint": fingerprint,
                "cases": [{key: row[key] for key in (
                    "retrieval_mode", "used_backend", "candidate_count", "shadow_hits", "coverage", "fallback_reason"
                )} | {"case_id": row["case"]["case_id"]} for row in pair_rows],
            })

    completed = _utc_now()
    aggregate_metadata = {
        **snapshot,
        "commit_sha": _commit_sha(),
        "utc_start": started,
        "utc_end": completed,
        "manifest": str(args.manifest),
        "manifest_sha256": manifest_sha256,
        "repetitions": args.repetitions,
        "top_k": args.top_k,
        "provider_configuration": provider_configuration,
        "provider_configuration_sha256": provider_configuration_sha256,
    }
    for variant in VARIANTS:
        report = build_report(rows_by_variant[variant], variant=variant, run_metadata=aggregate_metadata)
        target = run_root / "aggregate" / variant
        _write_json(target / "eval.json", report)
        (target / "eval.md").write_text(_markdown(report), encoding="utf-8")
        _write_json(target / "trace.json", {
            "schema": "late-interaction-trace-v1",
            "variant": variant,
            "snapshot_fingerprint": fingerprint,
            "case_count": len(rows_by_variant[variant]),
        })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
