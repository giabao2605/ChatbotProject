import json
from types import SimpleNamespace

import pytest

from scripts.late_interaction import retrieval_worker

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("fails", [False, True, "write"])
def test_worker_passes_explicit_retrieval_dependencies(monkeypatch, tmp_path, fails):
    from mech_chatbot.adapters import qdrant_runtime
    from mech_chatbot.rag import pipeline_steps

    closed = []
    runtime = SimpleNamespace(vector_store=object(), qdrant_client=SimpleNamespace(close=lambda: closed.append(True)), collection_name="source")
    def build(settings, **kwargs):
        assert kwargs.get("create_if_missing") is False
        return runtime
    monkeypatch.setattr(qdrant_runtime, "build_qdrant_runtime", build)
    monkeypatch.setattr(retrieval_worker, "load_manifest", lambda path: [{
        "case_id": "case", "query": "query", "identity": {
            "user_department": "Technical", "user_roles": ["viewer"],
            "allowed_departments": ["Technical"], "max_security_level": "internal",
            "allowed_sites": ["HQ"],
        },
    }])
    def retrieve(query, payload_filter, *, vectorstore, client, collection_name, **kwargs):
        assert vectorstore is runtime.vector_store
        assert client is runtime.qdrant_client
        assert collection_name == "source"
        if fails is True:
            raise RuntimeError("retrieval failed")
        return [], "explicit_rrf"
    monkeypatch.setattr(pipeline_steps, "_explicit_hybrid_rrf", retrieve)
    output = tmp_path / "results.json"
    if fails == "write":
        output.mkdir()
    args = ["--manifest", "unused", "--output", str(output), "--repetitions", "1", "--top-k", "20"]
    if fails == "write":
        with pytest.raises(OSError):
            retrieval_worker.main(args)
    elif fails:
        with pytest.raises(RuntimeError, match="retrieval failed"):
            retrieval_worker.main(args)
        assert not output.exists()
    else:
        assert retrieval_worker.main(args) == 0
        assert json.loads(output.read_text())[0]["retrieval_mode"] == "explicit_rrf"
    assert closed == [True]
