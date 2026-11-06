import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from mech_chatbot.application.protected_files import DocumentAccessRecord
from mech_chatbot.db.repositories import semantic_cache as repository
from mech_chatbot.rag import semantic_cache
from mech_chatbot.rag.execution import AccessScope
from sqlalchemy import text
from mech_chatbot.config.repository_runtime import bind_repository_runtime
from tests.unit.rag.test_feedback_scoped_flow import scoped_db


pytestmark = pytest.mark.unit


def test_cache_key_distinguishes_language_model_and_department_applicability():
    args = ("Technical", ["CHUNG"], "internal", ["HQ"], ["viewer"])
    baseline = semantic_cache.scope_signature(*args, response_language="vi",
        pipeline_environment={"GPT_MODEL_NAME": "model-a"})
    assert baseline != semantic_cache.scope_signature(*args, response_language="en",
        pipeline_environment={"GPT_MODEL_NAME": "model-a"})
    assert baseline != semantic_cache.scope_signature(*args, response_language="vi",
        pipeline_environment={"GPT_MODEL_NAME": "model-b"})
    assert baseline != semantic_cache.scope_signature("Other display", *args[1:],
        response_language="vi", pipeline_environment={"GPT_MODEL_NAME": "model-a"})


@pytest.mark.parametrize("semantic", [False, True])
def test_changed_verifier_contract_cannot_reuse_previous_cached_answer(monkeypatch, semantic):
    args = ("Technical", ["CHUNG"], "internal", ["HQ"], ["viewer"])
    current = semantic_cache.scope_signature(*args)
    with monkeypatch.context() as previous:
        previous.setattr(semantic_cache, "_VERIFIER_SHA256", "previous-verifier-renderer", raising=False)
        old = semantic_cache.scope_signature(*args)
    assert current != old
    row = {"answer": "Previous incomplete answer", "embedding": "[1.0]"}
    monkeypatch.setattr(repository, "sc_get_exact", lambda scope, *_: row if scope == old else None)
    monkeypatch.setattr(repository, "sc_get_candidates", lambda scope, *_: [row] if scope == old else [])
    monkeypatch.setattr(repository, "sc_record_lookup", Mock())
    validated = Mock(side_effect=AssertionError("Old namespace must not reach authorization validation"))
    monkeypatch.setattr(semantic_cache, "_validated_cache_payload", validated)
    hit = (semantic_cache.lookup("Procedure?", [1.0], current) if semantic
           else semantic_cache.lookup_exact("Procedure?", current))
    assert hit is None
    validated.assert_not_called()


def test_math_scope_contract_does_not_reuse_previous_answer_namespace(monkeypatch):
    args = ("Technical", ["CHUNG"], "internal", ["HQ"], ["viewer"])
    current = semantic_cache.scope_signature(*args)
    monkeypatch.setattr(semantic_cache, "_ANSWER_CONTRACT", "grounded-answer-v10-applicability")
    assert current != semantic_cache.scope_signature(*args)


def test_single_intent_contract_does_not_reuse_previous_math_scope_namespace(monkeypatch):
    args = ("Technical", ["CHUNG"], "internal", ["HQ"], ["viewer"])
    current = semantic_cache.scope_signature(*args)
    monkeypatch.setattr(semantic_cache, "_ANSWER_CONTRACT", "grounded-answer-v11-bom-scope")
    assert current != semantic_cache.scope_signature(*args)


@pytest.mark.parametrize("semantic", [False, True])
@pytest.mark.parametrize("failure", [None, "missing_actor", "revoked", "version", "unavailable", "demo", "scope_unavailable"])
def test_cache_rechecks_current_permissions_and_version(monkeypatch, semantic, failure,scoped_db):
    from mech_chatbot.adapters.protected_files import SqlProtectedFileStore
    from mech_chatbot.db.repositories import doc_metadata
    from mech_chatbot.db.repositories.feedback_access import pin_generation_sources
    engine,profile = scoped_db
    with engine.begin() as conn:
        conn.execute(text('UPDATE TaiLieu SET DocID=41 WHERE DocID=1'))
        conn.execute(text('UPDATE PhongBanChiaSe SET DocID=41 WHERE DocID=1'))
        conn.execute(text('UPDATE DocumentPages SET DocID=41,PageNo=4 WHERE DocID=1'))
        evidence = pin_generation_sources(conn,[{'doc_id':41,'version_no':2,'trang':4,'text':'approved content'}],profile)
    current_metadata = {41: {'tags': None, 'title': 'Manual', 'doc_number': 'MANUAL-001'}}
    if failure == 'demo':
        current_metadata = {41: {'tags': None, 'title': 'Quy trình cấp quyền hệ thống giả lập', 'doc_number': 'DEMO-IT-001'}}
    if failure == 'scope_unavailable':
        current_metadata = {}
    monkeypatch.setattr(doc_metadata, 'get_common_metadata_for_rag', lambda _: current_metadata)

    row = {"cache_id": 1, "question": "pressure", "embedding": "[1.0]",
        "answer": "12 bar [Source: D41P4]", "source_doc_ids": "[41]",
        "citation_snapshot": json.dumps([{"doc_id": 41, "page_no": 4}]),
        "evidence_snapshot": json.dumps(evidence)}
    for name in ("sc_delete", "sc_record_hit", "sc_record_lookup"):
        monkeypatch.setattr(repository, name, Mock())
    monkeypatch.setattr(repository, "sc_docs_all_current", lambda _: True)
    monkeypatch.setattr(repository, "sc_get_exact", lambda *_: row)
    monkeypatch.setattr(repository, "sc_get_candidates", lambda *_: [row])
    record = DocumentAccessRecord(doc_id=41, ten_file="manual.pdf", file_path=None,
        thu_muc=None, security_level="internal", site="HN", lifecycle_status="published",
        review_status="approved", departments=("ENG",), version_no=2)
    if failure == "revoked":
        record = replace(record, departments=("HR",))
    if failure == "version":
        record = replace(record, version_no=3)
    if failure == "unavailable":
        record = None
    monkeypatch.setattr(SqlProtectedFileStore, "load_document", lambda *_: record)
    actor = AccessScope(user_id=9,username='reviewer',department="ENG", roles=frozenset({"viewer"}),
        allowed_departments=frozenset({"ENG"}), allowed_sites=frozenset({"HN"}),
        max_security_level="internal")
    if failure == "missing_actor":
        actor = None
    with bind_repository_runtime(db_engine=engine):
        hit = (semantic_cache.lookup("pressure", [1.0], "scope", actor=actor) if semantic
            else semantic_cache.lookup_exact("pressure", "scope", actor=actor))
    assert bool(hit) is (failure is None)


@pytest.mark.parametrize("answer", [
    "Mình chưa tìm thấy đủ bằng chứng trong tài liệu được phép xem để trả lời phần này.",
    "I could not find enough evidence in the documents you can access to answer this part.",
])
def test_current_refusals_are_not_cacheable(answer):
    from mech_chatbot.rag.semantic_cache import _looks_like_refusal
    assert _looks_like_refusal(answer)


@pytest.mark.parametrize("question", [
    "Cho xem lịch sử phiên bản cũ",
    "Liệt kê đầy đủ tất cả quy định liên quan",
])
def test_scope_sensitive_questions_bypass_semantic_replay(question):
    assert semantic_cache._replay_is_scope_sensitive(question)
    assert semantic_cache.lookup_exact(question, "scope") is None
