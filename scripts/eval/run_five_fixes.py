"""Paired, bounded evaluation using the existing RAG composition; no chat writes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def put(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


class Budget:
    """Reserve a conservative upper bound before each HTTP call, including retries."""
    def __init__(self, path, arm, limit):
        self.path, self.arm, self.limit = path, arm, limit
        self.data = json.loads(path.read_text()) if path.exists() else {"calls": []}

    def reserve(self, provider, amount, details):
        total = sum(c["reserved_usd"] for c in self.data["calls"])
        arm_total = sum(c["reserved_usd"] for c in self.data["calls"] if c["arm"] == self.arm)
        if total + amount > 10 or arm_total + amount > self.limit:
            raise RuntimeError("Evaluation API budget exhausted before call")
        item = {"arm": self.arm, "provider": provider, "reserved_usd": amount, **details}
        self.data["calls"].append(item)
        put(self.path, self.data)
        return item

    def install(self, model, qdrant_url):
        import httpx
        import requests
        send = httpx.Client.send
        request_send = requests.Session.send
        budget = self
        from urllib.parse import urlsplit
        qdrant_host = urlsplit(qdrant_url).hostname

        def guarded(client, request, **kwargs):
            host = request.url.host
            if host == "openrouter.ai" and request.method == "POST":
                body = json.loads(request.content)
                if body.get("model") != model or not request.url.path.endswith("/chat/completions"):
                    raise RuntimeError("Unexpected paid provider/model")
                maximum = body.get("max_tokens") or body.get("max_completion_tokens")
                if not maximum or body.get("tools"):
                    raise RuntimeError("Unbounded output or paid tools are forbidden")
                # UTF-8 byte count plus framing bounds input tokens; use the higher tariff.
                input_bound = len(request.content) + 2048
                if input_bound > 200000:
                    raise RuntimeError("Evaluation input exceeds frozen budget bound")
                item = budget.reserve("openrouter", input_bound * 0.0000004 + maximum * 0.0000018,
                                      {"input_token_bound": input_bound, "output_token_bound": maximum})
                response = send(client, request, **kwargs)
                raw = response.read().decode("utf-8", errors="replace")
                payloads = [json.loads(line[6:]) for line in raw.splitlines()
                            if line.startswith("data: {")]
                if not payloads:
                    try:
                        payloads = [json.loads(raw)]
                    except ValueError:
                        payloads = []
                item["usage"] = next((p["usage"] for p in reversed(payloads) if p.get("usage")), None)
                item["http_status"] = response.status_code
                put(budget.path, budget.data)
                return response
            if host not in {"localhost", "127.0.0.1", qdrant_host}:
                raise RuntimeError("Unexpected network destination in benchmark")
            if request.method not in {"GET", "HEAD"} and not any(
                    part in request.url.path for part in ("/scroll", "/query", "/search", "/count")):
                raise RuntimeError("Qdrant writes forbidden in benchmark")
            return send(client, request, **kwargs)

        def guarded_requests(session, request, **kwargs):
            from urllib.parse import urlsplit
            if urlsplit(request.url).hostname != "api.jina.ai" or request.method != "POST":
                raise RuntimeError("Unexpected requests destination")
            body = request.body or b""
            data = json.loads(body)
            count_bound = len(str(data.get("query", "")).encode()) * max(1, len(data.get("documents", [])))
            count_bound += sum(len(str(d).encode()) + 128 for d in data.get("documents", []))
            # Highest public package tariff, $10 / 10M tokens, even when trial-funded.
            item = budget.reserve("jina", count_bound * 0.000001, {"input_token_bound": count_bound})
            response = request_send(session, request, **kwargs)
            item["usage"] = response.json().get("usage") if response.ok else None
            item["http_status"] = response.status_code
            put(budget.path, budget.data)
            return response

        httpx.Client.send = guarded
        requests.Session.send = guarded_requests


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--runtime-root", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--arm", required=True)
    ap.add_argument("--limit-usd", type=float, default=4)
    args = ap.parse_args()
    sys.path.insert(0, str(args.source / "src"))
    os.chdir(args.runtime_root)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                      HF_HOME="C:/Users/bao.nguyen/.cache/huggingface",
                      FASTEMBED_CACHE_PATH="C:/Users/bao.nguyen/.codex/cache/fastembed")
    import bcrypt, pyarrow, onnxruntime, numpy  # noqa: F401: established Windows DLL load order
    from sqlalchemy import event
    from mech_chatbot.config.settings import load_settings
    from mech_chatbot.composition.maintenance_runtime import configured_repository_runtime
    from mech_chatbot.composition.rag_runtime import build_rag_runtime
    from mech_chatbot.config.repository_runtime import current_repository_engine
    from mech_chatbot.config.logging import bind_trace_runtime
    from mech_chatbot.llm.external_ai import external_processing_context
    from mech_chatbot.rag.execution import AccessScope, RagInvocation, RagRequest, collect_rag_events
    import mech_chatbot.rag.phases.generation as generation
    from mech_chatbot.rag.context_builders import format_docs
    from langchain_core.documents import Document
    cache = Path(os.environ["HF_HOME"]) / "hub/models--BAAI--bge-m3"
    snapshot = cache / "snapshots" / (cache / "refs/main").read_text().strip()
    settings = load_settings(args.runtime_root / ".env").model_copy(update={
        "RAG_EXECUTION_CONTEXT": "evaluation", "EMBEDDING_MODEL": str(snapshot)})
    args.output.mkdir(parents=True, exist_ok=True)
    budget = Budget(args.output / "budget.json", args.arm, args.limit_usd)
    budget.install(settings.GPT_MODEL_NAME, settings.QDRANT_URL)
    manifest_bytes = args.manifest.read_bytes()
    cases = [json.loads(line) for line in manifest_bytes.decode("utf-8").splitlines() if line.strip()]
    assert len(cases) == 36 and len({c["id"] for c in cases}) == 36
    report = {"arm": args.arm, "source_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=args.source, text=True).strip(),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(), "cases": []}
    target = args.output / (args.arm + ".json")
    if target.exists():
        raise RuntimeError("Do not overwrite or silently repeat a paid arm")
    captured = {}
    original = generation.generate_answer

    def capture(plan, **kwargs):
        captured["context"] = plan.evidence.context_text
        captured["evidence"] = [{"metadata": d.metadata, "text": d.metadata.get("noi_dung_goc") or d.page_content}
                                for d in plan.evidence.retrieved_docs]
        return original(plan, **kwargs)

    generation.generate_answer = capture
    with configured_repository_runtime(settings, include_qdrant=False):
        def protect_sql(conn, cursor, statement, parameters, context, executemany):
            lower = statement.strip().lower()
            if not lower.startswith(("select", "with")) and "externalaicallaudit" not in lower:
                raise RuntimeError("Benchmark forbids application database writes")
        event.listen(current_repository_engine(), "before_cursor_execute", protect_sql)
        runtime = build_rag_runtime(settings, trace_persist=lambda *_: None)
        try:
            for case in cases:
                captured.clear()
                started = time.monotonic()
                item = {"id": case["id"], "group": case["group"], "mode": case.get("mode", "live"),
                        "oracle": case["oracle"]}
                try:
                    access = AccessScope(department="Technical", roles=frozenset({"viewer"}),
                        allowed_departments=frozenset(case.get("departments", ["Technical", "HR", "IT", "Warehouse", "Accountant"])),
                        allowed_sites=frozenset({"HQ", "HN", "PHONG_KY_THUAT"}), max_security_level="internal")
                    trace = "five-fixes-" + args.arm + "-" + case["id"]
                    with bind_trace_runtime(runtime.trace_runtime), external_processing_context("five-fixes-evaluation", False, trace):
                        if item["mode"] == "policy":
                            from mech_chatbot.rag.rbac import document_matches_access_scope
                            allowed = document_matches_access_scope(case["metadata"], user_department=case["display_department"],
                                user_roles=["viewer"], allowed_departments=case["departments"],
                                max_security_level="internal", allowed_sites=["HQ"])
                            item.update(answer="allowed" if allowed else "denied", citations=[], evidence=[])
                        elif item["mode"] == "fixed_context":
                            from mech_chatbot.rag.pipeline_steps import GenerationTurn, GenerationEvidence, GenerationControl, GenerationPlan, GenerationOutcome
                            from types import SimpleNamespace
                            docs = [Document(page_content=d["text"], metadata=d["metadata"]) for d in case["documents"]]
                            context = format_docs(docs)
                            outcome = GenerationOutcome()
                            plan = GenerationPlan(turn=GenerationTurn(user_question=case["question"], effective_question=case["question"],
                                chat_history_str="",new_part_ids=[],response_language="vi",user_department="Technical",user_roles=["viewer"]),
                                evidence=GenerationEvidence(context_text=context,retrieved_docs=docs,intent_data={},base_k=len(docs),retrieval_mode="fixed_context"),
                                control=GenerationControl(trace_id=trace,started_at=time.time(),outcome=outcome),
                                runtime=SimpleNamespace(retrieval_adapter=runtime.retrieval,provider_adapter=runtime.provider))
                            answer = "".join(original(plan))
                            item.update(answer=answer, refusal_reason=outcome.refusal_reason, context=context,
                                        evidence=case["documents"], citations=[])
                        else:
                            result = collect_rag_events(runtime.executor.run(RagRequest(question=case["question"], access=access,
                                history=tuple(case.get("history", [])),current_part_ids=tuple(case.get("current_part_ids", []))),
                                RagInvocation(trace_id=trace,mode="evaluation")))
                            item.update(answer=result.answer,citations=list(result.citations), diagnostics=dict(result.diagnostics), **captured)
                    item["valid"] = bool(item["answer"].strip())
                except Exception as exc:
                    item.update(valid=False,error=type(exc).__name__ + ": " + str(exc)[:250])
                item["latency_seconds"] = round(time.monotonic()-started,3)
                report["cases"].append(item)
                put(target,report)
                print(args.arm,case["id"],item["valid"],item.get("error", ""), flush=True)
        finally:
            runtime.close()
    return 0 if all(c["valid"] for c in report["cases"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
