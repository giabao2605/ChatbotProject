"""One-shot production RRF retrieval worker used by the quality benchmark."""

from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import time

from mech_chatbot.evaluation.late_interaction import load_manifest
from mech_chatbot.rag.rbac import create_rbac_filter
from mech_chatbot.rag.retrieval import current_published_filter


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, required=True)
    parser.add_argument("--top-k", type=int, required=True)
    args = parser.parse_args(argv)
    from mech_chatbot.rag.pipeline_steps import _explicit_hybrid_rrf
    from mech_chatbot.adapters.qdrant_runtime import build_qdrant_runtime
    from mech_chatbot.config.settings import QdrantSettings, Settings

    cases = load_manifest(args.manifest)
    settings = Settings.from_env()
    runtime = build_qdrant_runtime(
        QdrantSettings.from_settings(settings), create_if_missing=False,
    )
    with closing(runtime.qdrant_client):
        return _retrieve_cases(args, cases, settings, runtime, _explicit_hybrid_rrf)


def _retrieve_cases(args, cases, settings, runtime, retrieve):
    runs = []
    for repetition in range(1, args.repetitions + 1):
        for case in cases:
            identity = case["identity"]
            rbac = create_rbac_filter(
                identity["user_department"],
                identity["user_roles"],
                identity["allowed_departments"],
                max_security_level=identity["max_security_level"],
                allowed_sites=identity["allowed_sites"],
            )
            started = time.perf_counter()
            docs, mode = retrieve(
                case["query"],
                current_published_filter(rbac),
                dense_top_k=args.top_k,
                sparse_top_k=args.top_k,
                result_cap=args.top_k,
                phase="late_interaction_eval",
                vectorstore=runtime.vector_store,
                client=runtime.qdrant_client,
                collection_name=runtime.collection_name,
                qdrant_timeout_seconds=settings.QDRANT_SEARCH_TIMEOUT_SECONDS,
            )
            runs.append({
                "repetition": repetition,
                "case_id": case["case_id"],
                "latency_ms": (time.perf_counter() - started) * 1000,
                "retrieval_mode": mode,
                "documents": [
                    {
                        "page_content": str(getattr(doc, "page_content", "") or ""),
                        "metadata": dict(getattr(doc, "metadata", {}) or {}),
                    }
                    for doc in docs
                ],
            })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(runs, ensure_ascii=False, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
