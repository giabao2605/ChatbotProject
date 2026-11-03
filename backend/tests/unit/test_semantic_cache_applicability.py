from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mech_chatbot.rag import semantic_cache
from mech_chatbot.rag.execution import AccessScope
from mech_chatbot.rag.phases.preparation import _normalize_request


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("change", [
    {"user_id": 10}, {"username": "other"}, {"user_department": "HR"},
    {"user_roles": ["approver"]}, {"execution_context": "evaluation"},
])
def test_cache_identity_isolates_answer_applicability(change):
    args = dict(user_department="ENG", allowed_departments=["ENG", "HR"],
        max_security_level="internal", allowed_sites=["HN"], user_roles=["viewer"],
        user_id=9, username="reviewer", execution_context="production")
    assert semantic_cache.scope_signature(**args) != semantic_cache.scope_signature(**{**args, **change})


def test_admin_cache_still_isolates_full_roles_and_grants():
    args = dict(user_department="ENG", allowed_departments=["ENG"],
        max_security_level="internal", allowed_sites=["HN"], user_roles=["admin"])
    baseline = semantic_cache.scope_signature(**args)
    for change in ({"user_roles": ["admin", "approver"]}, {"allowed_departments": ["HR"]},
                   {"allowed_sites": ["HCM"]}, {"max_security_level": "secret"}):
        assert baseline != semantic_cache.scope_signature(**{**args, **change})


@pytest.mark.parametrize("field,value", [
    ("history", ({"role": "user", "content": "earlier"},)),
    ("conversation_context", {"query_mode": "comprehensive"}),
    ("scope_comparison", ("A", "B")), ("graph_query", object()),
    ("bom_rollup", object()), ("query_plan", object()), ("text_query", True),
    ("candidate_selection_context", object()), ("scope_total", "assembly"),
])
def test_explicit_context_or_query_action_disables_cache(field, value):
    request = dict(question="Quy định đào tạo thế nào?", image_path=None, history=(),
        current_part_ids=(), conversation_context=None,
        access=AccessScope(user_id=9, roles=frozenset({"viewer"})), response_language="vi")
    context = _normalize_request(SimpleNamespace(request=SimpleNamespace(**{**request, field: value}), trace_id="cache"))
    assert not context.cache_eligible


def test_missing_user_identity_is_not_cacheable():
    request = SimpleNamespace(question="Quy định đào tạo?", image_path=None, history=(),
        current_part_ids=(), conversation_context=None, access=AccessScope(), response_language="vi")
    assert not _normalize_request(SimpleNamespace(request=request, trace_id="cache")).cache_eligible


@pytest.mark.parametrize("question", ["Quy định hiện nay thế nào?", "Quy định hôm nay?",
    "What applies next month?", "Quy định có hiệu lực năm 2025?",
    "Quy định áp dụng cho tôi?", "What applies to me?", "Liệt kê mọi quy định",
    "Show all procedures", "Quy định từng áp dụng?", "Historical procedures"])
def test_unverifiable_time_personal_or_exhaustive_requests_never_read_or_write(monkeypatch, question):
    from mech_chatbot.db.repositories import semantic_cache as repository
    exact, candidates, put = Mock(), Mock(), Mock()
    monkeypatch.setattr(repository, "sc_get_exact", exact)
    monkeypatch.setattr(repository, "sc_get_candidates", candidates)
    monkeypatch.setattr(repository, "sc_put", put)
    assert semantic_cache.lookup_exact(question, "scope") is None
    assert semantic_cache.lookup(question, [1.0], "scope") is None
    semantic_cache.store(question, [1.0], "answer", "", [], [1], "scope", "model", 0,
        citation_snapshot=[{"doc_id": 1}], evidence_snapshot=[{"doc_id": 1}])
    exact.assert_not_called()
    candidates.assert_not_called()
    put.assert_not_called()
