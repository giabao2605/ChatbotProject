import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from mech_chatbot.application.protected_files import DocumentAccessRecord
from mech_chatbot.db.repositories import semantic_cache as repository
from mech_chatbot.rag import semantic_cache
from mech_chatbot.rag.execution import AccessScope


pytestmark = pytest.mark.unit


def test_cache_key_distinguishes_language_model_and_display_is_not_a_grant():
    args = ("Technical", ["CHUNG"], "internal", ["HQ"], ["viewer"])
    baseline = semantic_cache.scope_signature(*args, response_language="vi",
        pipeline_environment={"GPT_MODEL_NAME": "model-a"})
    assert baseline != semantic_cache.scope_signature(*args, response_language="en",
        pipeline_environment={"GPT_MODEL_NAME": "model-a"})
    assert baseline != semantic_cache.scope_signature(*args, response_language="vi",
        pipeline_environment={"GPT_MODEL_NAME": "model-b"})
    assert baseline == semantic_cache.scope_signature("Other display", *args[1:],
        response_language="vi", pipeline_environment={"GPT_MODEL_NAME": "model-a"})


@pytest.mark.parametrize("semantic", [False, True])
@pytest.mark.parametrize("failure", [None, "missing_actor", "revoked", "version", "unavailable"])
def test_cache_rechecks_current_permissions_and_version(monkeypatch, semantic, failure):
    from mech_chatbot.adapters.protected_files import SqlProtectedFileStore

    row = {"cache_id": 1, "question": "pressure", "embedding": "[1.0]",
        "answer": "12 bar [Source: D41P4]", "source_doc_ids": "[41]",
        "citation_snapshot": json.dumps([{"doc_id": 41, "page_no": 4}]),
        "evidence_snapshot": json.dumps([{"doc_id": 41, "version_no": 2}])}
    for name in ("sc_delete", "sc_record_hit", "sc_record_lookup"):
        monkeypatch.setattr(repository, name, Mock())
    monkeypatch.setattr(repository, "sc_docs_all_current", lambda _: True)
    monkeypatch.setattr(repository, "sc_get_exact", lambda *_: row)
    monkeypatch.setattr(repository, "sc_get_candidates", lambda *_: [row])
    record = DocumentAccessRecord(doc_id=41, ten_file="manual.pdf", file_path=None,
        thu_muc=None, security_level="internal", site="HQ", lifecycle_status="published",
        review_status="approved", departments=("Technical",), version_no=2)
    if failure == "revoked":
        record = replace(record, departments=("HR",))
    if failure == "version":
        record = replace(record, version_no=3)
    if failure == "unavailable":
        record = None
    monkeypatch.setattr(SqlProtectedFileStore, "load_document", lambda *_: record)
    actor = AccessScope(department="Technical", roles=frozenset({"viewer"}),
        allowed_departments=frozenset({"Technical"}), allowed_sites=frozenset({"HQ"}),
        max_security_level="internal")
    if failure == "missing_actor":
        actor = None
    hit = (semantic_cache.lookup("pressure", [1.0], "scope", actor=actor) if semantic
        else semantic_cache.lookup_exact("pressure", "scope", actor=actor))
    assert bool(hit) is (failure is None)
