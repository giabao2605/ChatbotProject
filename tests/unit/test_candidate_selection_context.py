"""Candidate descriptors are alternatives, never an implicit task list."""
import pytest


@pytest.mark.parametrize('value', [True, False, 'true', 1])
def test_text_candidate_codec_is_strict_and_exclusive(value):
    from mech_chatbot.api.evidence_contracts import CandidateSelectionContextRequest
    body = {'candidates':[{'selection_id':'text', 'text_query':value}]}
    if value is True:
        candidate = CandidateSelectionContextRequest.model_validate(body).to_context().candidates[0]
        assert candidate.text_query is True and candidate.question is None
        with pytest.raises(ValueError):
            CandidateSelectionContextRequest.model_validate({'candidates':[
                {**body['candidates'][0], 'scope_comparison':{'scope_a':'a','scope_b':'b'}}]})
    else:
        with pytest.raises(ValueError):
            CandidateSelectionContextRequest.model_validate(body)


def test_codec_preserves_four_alternatives_without_creating_a_query_plan():
    from mech_chatbot.api.evidence_contracts import CandidateSelectionContextRequest
    value = CandidateSelectionContextRequest.model_validate({'candidates': [
        {'selection_id': key, 'scope_comparison': {'scope_a': 'a', 'scope_b': 'b'}}
        for key in ('first', 'second', 'third', 'fourth')]})
    context = value.to_context()
    assert tuple(candidate.selection_id for candidate in context.candidates) == ('first', 'second', 'third', 'fourth')
    assert all(candidate.question is None and candidate.depends_on == () for candidate in context.candidates)


def test_dependencies_follow_task_order_not_alternative_order():
    from mech_chatbot.domain.task_routing import CandidateSelection, CandidateSelectionContext
    context = CandidateSelectionContext((
        CandidateSelection('dependent', scope_comparison=('a', 'b'), depends_on=('source',)),
        CandidateSelection('source', scope_comparison=('c', 'd'))))
    assert context.candidates[0].depends_on == ('source',)


@pytest.mark.parametrize('value', [
    {'candidates': []},
    {'candidates': [{'selection_id': str(index), 'scope_comparison': {'scope_a': 'a', 'scope_b': 'b'}} for index in range(9)]},
    {'candidates': [{'selection_id': True, 'scope_comparison': {'scope_a': 'a', 'scope_b': 'b'}}]},
    {'candidates': [{'selection_id': 'one', 'scope_comparison': {'scope_a': 1, 'scope_b': 'b'}}]},
    {'candidates': [{'selection_id': 'one'}]},
    {'candidates': [{'selection_id': 'one', 'question': ' ', 'scope_comparison': {'scope_a': 'a', 'scope_b': 'b'}}]},
    {'candidates': [{'selection_id': 'one', 'scope_comparison': {'scope_a': 'a', 'scope_b': 'b'}, 'expected': 'ready'}]},
    {'candidates': [{'selection_id': 'one', 'scope_comparison': {'scope_a': 'a', 'scope_b': 'b'}, 'depends_on': ['missing']}]},
    {'candidates': [{'selection_id': 'one', 'scope_comparison': {'scope_a': 'a', 'scope_b': 'b'}, 'depends_on': ['one']}]},
    {'candidates': [{'selection_id': key, 'scope_comparison': {'scope_a': 'a', 'scope_b': 'b'}} for key in ('one', 'one')]},
    {'candidates': [{'selection_id': key, 'scope_comparison': {'scope_a': 'a', 'scope_b': 'b'}, 'depends_on': [other]}
                    for key, other in (('one', 'two'), ('two', 'one'))]},
    {'candidates': [], 'expected_disposition': 'overflow'},
])
def test_codec_rejects_coercion_extra_fields_invalid_actions_and_dependencies(value):
    from mech_chatbot.api.evidence_contracts import CandidateSelectionContextRequest
    with pytest.raises(ValueError):
        CandidateSelectionContextRequest.model_validate(value)


def test_domain_context_is_immutable_and_rejects_non_candidates():
    from dataclasses import FrozenInstanceError
    from mech_chatbot.domain.task_routing import CandidateSelection, CandidateSelectionContext
    candidate = CandidateSelection('one', scope_comparison=('a', 'b'))
    context = CandidateSelectionContext((candidate,))
    with pytest.raises(FrozenInstanceError):
        context.candidates = ()
    with pytest.raises(FrozenInstanceError):
        candidate.selection_id = 'other'
    with pytest.raises(ValueError):
        CandidateSelectionContext(({},))
    with pytest.raises(ValueError):
        CandidateSelectionContext([candidate])


def test_request_requires_candidate_context_exclusive_of_other_actions():
    from dataclasses import replace
    from mech_chatbot.domain.task_routing import CandidateSelection, CandidateSelectionContext
    from mech_chatbot.rag.execution import RagRequest, AccessScope
    context = CandidateSelectionContext((CandidateSelection('one', scope_comparison=('a', 'b')),))
    selected = RagRequest('Compare.', AccessScope(), candidate_selection_context=context)
    for extra in ({'scope_comparison': ('a', 'b')}, {'text_query': True}, {'candidate_selection_context': {}}):
        with pytest.raises(ValueError):
            replace(selected, **extra)


@pytest.mark.parametrize('kind', ['text-with-template', 'plan-with-template', 'invalid-comparison'])
def test_legacy_single_action_guards_survive_candidate_extension(kind):
    from mech_chatbot.domain.graph_query import GraphQuery
    from mech_chatbot.domain.query_plan import QueryBranch, QueryPlan
    from mech_chatbot.rag.execution import RagRequest, AccessScope

    graph = GraphQuery('document:1', 'product', 'factory')
    choices = {
        'text-with-template': {'text_query': True, 'scope_comparison': ('a', 'b')},
        'plan-with-template': {'query_plan': QueryPlan((QueryBranch('graph', 'Show parts.', graph_query=graph),)),
                              'graph_query': graph},
        'invalid-comparison': {'scope_comparison': ('a',)},
    }
    with pytest.raises(ValueError):
        RagRequest('Request.', AccessScope(), **choices[kind])
