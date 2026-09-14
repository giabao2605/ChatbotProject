"""Exercise the evaluator and real reports with only external adapters replaced."""

import json
import io
from contextlib import contextmanager
from types import SimpleNamespace
import sys

import pytest

from scripts.eval import run_late_interaction_eval as evaluation

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("response,error", [
    ('noise\nLATE_RESULT {"id":1,"vectors":[[1.0]]}\n', None),
    ('', 'encoder worker exited'),
    ('LATE_RESULT {"id":1,"error":"encode failed"}\n', 'encode failed'),
    ('LATE_RESULT {"id":2,"vectors":[]}\n', 'response id mismatch'),
])
def test_isolated_encoder_protocol(monkeypatch, response, error):
    process = SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO(response),
                              poll=lambda: 1)

    def launch(command, **kwargs):
        assert kwargs['env']['HF_HUB_OFFLINE'] == '1'
        return process

    monkeypatch.setattr(evaluation.subprocess, 'Popen', launch)
    encoder = evaluation.IsolatedEncoder(sys.executable)
    if error:
        with pytest.raises(RuntimeError, match=error):
            encoder.encode('mô-men')
    else:
        assert encoder.encode('mô-men') == [[1.0]]
    assert json.loads(process.stdin.getvalue()) == {'id': 1, 'query': 'mô-men'}
    encoder.close()


@pytest.mark.parametrize('times_out', [False, True])
def test_isolated_encoder_closes_running_worker(monkeypatch, times_out):
    terminated = []

    def wait(*, timeout):
        if times_out:
            raise evaluation.subprocess.TimeoutExpired('encoder', timeout)

    process = SimpleNamespace(stdin=io.StringIO(), poll=lambda: None, wait=wait,
                              terminate=lambda: terminated.append(True))
    monkeypatch.setattr(evaluation.subprocess, 'Popen', lambda *a, **kw: process)
    evaluation.IsolatedEncoder(sys.executable).close()
    assert process.stdin.closed
    assert bool(terminated) is times_out


@pytest.mark.parametrize("outcome", ["complete", "preflight", "provider", "retrieval"])
def test_evaluator_lifecycle_and_reports(monkeypatch, tmp_path, outcome):
    from mech_chatbot.composition import maintenance_runtime, rag_runtime
    from mech_chatbot.rag import rerank

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RAG_LATE_INDEX_VERSION", "original")
    events = []
    metadata = {"file_goc": "source.md", "doc_id": 1, "trang_so": 1,
                "version_no": "1", "noi_dung_goc": "source text"}
    document = SimpleNamespace(page_content="source text", metadata=metadata)
    case = {
        "case_id": "exact", "scenario": "exact_code", "query": "code",
        "identity": {"user_department": "Technical", "user_roles": ["viewer"],
                     "allowed_departments": ["Technical"], "allowed_sites": ["HQ"],
                     "max_security_level": "internal"},
        "expected_sources": [{"document": "source.md", "doc_id": 1, "relevance": 3}],
        "forbidden_sources": [],
    }
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps(case) + "\n", encoding="utf-8")
    readiness = tmp_path / "readiness.json"
    readiness.write_text(json.dumps({"ready_for_serving": outcome != "preflight", "configuration": {
        "source_collection": "declared-source", "shadow_collection": "shadow",
        "index_version": "index", "document_pooling": "none",
        "document_max_length": 48, "query_max_length": 64,
    }}), encoding="utf-8")

    @contextmanager
    def repository(*args, **kwargs):
        events.append("repository-enter")
        try:
            yield
        finally:
            events.append("repository-close")

    class Client:
        def __init__(self, **kwargs):
            events.append("client-open")

        def scroll(self, *, collection_name, **kwargs):
            assert collection_name == "declared-source"
            return [SimpleNamespace(id="point", payload={"metadata": metadata})], None

        def close(self):
            events.append("client-close")

    class Encoder:
        def __init__(self, executable):
            events.append("encoder-open")

        def encode(self, query):
            return [[1.0]]

        def close(self):
            events.append("encoder-close")

    voyage = SimpleNamespace(model="resolved", endpoint="https://provider.test/v1")

    def retrieve(manifest, repetitions, top_k, cache_path, *, source_collection):
        assert source_collection == "declared-source"
        if outcome == "retrieval":
            raise RuntimeError("retrieval failed")
        return {(i, "exact"): ([document], 10.0, "rrf") for i in range(1, repetitions + 1)}

    def voyage_rerank(docs, query, *, runtime, timeout_seconds, **kwargs):
        assert runtime is voyage
        assert timeout_seconds > 0
        return docs

    def shadow(docs, query, client, *, query_encoder, **kwargs):
        assert query_encoder(query) == [[1.0]]
        return dict(documents=docs, used_shadow=True, shadow_hits=1,
                    coverage=1.0, total_latency_ms=5.0)

    monkeypatch.setattr(maintenance_runtime, "configured_repository_runtime", repository)
    monkeypatch.setattr(evaluation, "QdrantClient", Client)
    monkeypatch.setattr(evaluation, "IsolatedEncoder", Encoder)
    monkeypatch.setattr(evaluation, "_retrieve_in_worker", retrieve)
    monkeypatch.setattr(evaluation, "_commit_sha", lambda: "source-commit")
    monkeypatch.setattr(rag_runtime, "_build_voyage_dependency", lambda *_: None if outcome == "provider" else voyage)
    monkeypatch.setattr(rerank, "voyage_rerank_documents", voyage_rerank)
    monkeypatch.setattr(evaluation, "attempt_shadow_rerank", shadow)
    args = ["--manifest", str(manifest), "--output-dir", str(tmp_path), "--run-id", "run",
            "--readiness", str(readiness), "--source-collection", "declared-source",
            "--shadow-collection", "shadow", "--index-version", "index",
            "--repetitions", "2", "--encoder-python", sys.executable]
    if outcome in {"provider", "retrieval"}:
        with pytest.raises(RuntimeError):
            evaluation.main(args)
    else:
        assert evaluation.main(args) == (2 if outcome == "preflight" else 0)
    assert events[-2:] == ["client-close", "repository-close"]
    if outcome == "complete":
        assert events.count("encoder-open") == events.count("encoder-close") == 1
        for variant in evaluation.VARIANTS:
            report = json.loads((tmp_path / "run" / "aggregate" / variant / "eval.json").read_text())
            assert report["run_metadata"]["source_collection"] == "declared-source"
            assert report["run_metadata"]["provider_configuration"]["voyage_model"] == "resolved"
            assert report["provider_failure_count"] == 0
            assert report["ranked_retrieval"]["ndcg_at_10"] == 1.0
    else:
        assert "encoder-open" not in events
        assert not (tmp_path / "run" / "aggregate").exists()
