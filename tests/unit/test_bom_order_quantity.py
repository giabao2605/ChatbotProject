"""A production-order quantity is reviewed evidence, never a copied literal."""
from dataclasses import replace
from decimal import Decimal
import json

import pytest
from sqlalchemy import text

from mech_chatbot.api.evidence_contracts import BomRollupRequest
from mech_chatbot.domain.graph_query import BomRollupQuery, bom_rollup_context
from mech_chatbot.domain.structured_evidence import NumericFact, SourceLocator, ScopeManifest, RoundingRule, scope_payload
from mech_chatbot.db.repositories.structured_evidence import save_extracted_scope, review_numeric_fact, review_scope_completeness, load_approved_scope
from .test_rag_bom_chat import bom_store, store, connection, request, run, PROFILE


@pytest.fixture
def order_store(bom_store):
    store, engine = bom_store
    with store.begin() as conn:
        conn.execute(text("INSERT INTO TaiLieu(DocID,VersionNo,TenFile,ThuMuc,Site,SecurityLevel,LifecycleStatus,ReviewStatus,PublicationState,Servable,IsCurrent,EffectiveStatus,KnowledgeApproverUserID) VALUES(98,4,'order.csv','ENG','factory','internal','published','approved','published',1,1,'effective',7)"))
        conn.execute(text("INSERT INTO PhongBanChiaSe VALUES(98,'ENG')"))
    fact = NumericFact('order-quantity', 'Order count', '7', Decimal('7'), 'piece',
        SourceLocator('98', '4', sheet='Orders', cell='C2', row_key='order-7', table_index=1),
        source_hash='d'*64, extraction_method='fixture')
    save_extracted_scope(ScopeManifest('order', 'order-snapshot', (fact,), source_revisions=(('98','4'),), unit='piece'), db_engine=store)
    review_numeric_fact(fact.fact_id, PROFILE, action='approve', note='Synthetic order source review', db_engine=store)
    review_scope_completeness('order', PROFILE, expected_row_ids=('order-7',), rounding_rule=RoundingRule(0),
        definition='Synthetic production order count', note='Synthetic reviewed quantity target',
        quantity_target={'root_doc_id':1, 'product_scope':'product', 'site':'factory'}, db_engine=store)
    return store, engine


def source_request():
    old = request()
    return replace(old, bom_rollup=BomRollupQuery(old.bom_rollup.graph, None, 'policy', quantity_scope='order'))


def test_source_quantity_is_loaded_bound_and_cited_through_the_public_executor(order_store):
    store, engine = order_store
    result = run(engine, source_request())
    assert result.outcome == 'answered'
    math = result.diagnostics['math_v2']
    assert math['status'] == 'complete' and math['order_quantity'] == '7'
    assert math['steps'][0]['display_value'] == '14.00'
    assert 'order' in math['steps'][0]['source_ids']
    assert math['quantity_scope']['scope_id'] == 'order'
    assert math['quantity_scope']['quantity_binding']['root_doc_id'] == 1
    quantity = next(c for c in result.citations if c['doc_id'] == 98)
    assert quantity['version_no'] == 4 and quantity['locator']['cell'] == 'C2'
    assert '[SRC:'+quantity['source_id']+']' in result.answer
    assert 'người hỏi cung cấp' not in result.answer
    assert result.diagnostics.budget.calculations == 1
    assert result.diagnostics.budget.final_generations == 0


@pytest.mark.parametrize('change', ['rights', 'revision', 'pending', 'target', 'product', 'site', 'root_hash', 'root_snapshot'])
def test_changed_or_inaccessible_quantity_cannot_emit_totals_or_private_metadata(order_store, change):
    store, engine = order_store
    statements = {
        'rights': "DELETE FROM PhongBanChiaSe WHERE DocID=98",
        'revision': "UPDATE TaiLieu SET VersionNo=5 WHERE DocID=98",
        'pending': "UPDATE StructuredEvidenceScope SET CompletenessReviewed=0 WHERE ScopeID='order'",
        'root_snapshot': "UPDATE StructuredSourceSnapshot SET SourceHash='invalid' WHERE DocID=1",
    }
    with store.begin() as conn:
        if change in statements:
            conn.execute(text(statements[change]))
        else:
            raw = json.loads(conn.execute(text("SELECT ManifestJson FROM StructuredEvidenceScope WHERE ScopeID='order'")).scalar_one())
            key, value = {'target':('root_doc_id',2),'product':('product_scope','other'),
                          'site':('site','other'),'root_hash':('root_source_hash','b'*64)}[change]
            raw['quantity_binding'] = {**raw['quantity_binding'], key:value}
            conn.execute(text("UPDATE StructuredEvidenceScope SET ManifestJson=:payload WHERE ScopeID='order'"), {'payload':json.dumps(raw)})
    result = run(engine, source_request())
    assert result.outcome == 'refused'
    assert result.diagnostics['math_v2']['status'] == 'blocked'
    assert result.diagnostics['math_v2']['quantity_scope'] is None
    assert result.diagnostics['math_v2']['order_quantity'] is None
    assert not result.citations and '14.00' not in result.answer
    assert result.diagnostics.budget.calculations == 0


def test_reviewed_quantity_target_is_pinned_by_server_and_cleared_on_ordinary_review(order_store):
    store, _ = order_store
    scope = load_approved_scope('order', PROFILE, db_engine=store)
    binding = scope_payload(scope)['quantity_binding']
    assert binding == dict(root_doc_id=1, root_version=1, root_source_hash='a'*64, product_scope='product', site='factory')
    review_scope_completeness('order', PROFILE, expected_row_ids=('order-7',), rounding_rule=RoundingRule(0),
        definition='Ordinary reviewed count', note='Remove quantity target', quantity_target=None, db_engine=store)
    assert load_approved_scope('order', PROFILE, db_engine=store).quantity_binding is None


@pytest.mark.parametrize('changes', [{'root_doc_id':True}, {'root_doc_id':0}, {'site':'other'}, {'product_scope':' '}])
def test_invalid_quantity_target_is_not_approved(order_store, changes):
    store, _ = order_store
    with pytest.raises((ValueError, PermissionError)):
        review_scope_completeness('order', PROFILE, expected_row_ids=('order-7',), rounding_rule=RoundingRule(0),
            definition='Synthetic count', note='Invalid target',
            quantity_target={'root_doc_id':1,'product_scope':'product','site':'factory',**changes}, db_engine=store)


def test_bom_contract_and_transport_preserve_exactly_one_quantity_basis():
    body = dict(root_key='document:1', product_scope='product', site='factory', rounding_scope='policy')
    source = BomRollupRequest(**body, quantity_scope='order').to_rollup()
    assert bom_rollup_context(source) == {**body, 'direction':'contains', 'quantity_scope':'order'}
    literal = BomRollupRequest(**body, order_quantity='7').to_rollup()
    assert bom_rollup_context(literal) == {**body, 'direction':'contains', 'order_quantity':'7'}
    for extra in ({}, {'order_quantity':'7','quantity_scope':'order'}, {'quantity_scope':' '}):
        with pytest.raises(ValueError):
            BomRollupRequest(**body, **extra).to_rollup()


def test_legacy_scope_canonical_payload_omits_unset_quantity_binding():
    assert 'quantity_binding' not in scope_payload(ScopeManifest('old','snapshot',()))


@pytest.mark.parametrize('value,unit,currency', [('7.5','piece',''),('-7','piece',''),('7','kg',''),('7','piece','VND')])
def test_quantity_review_rejects_fractional_negative_or_incompatible_units(order_store, value, unit, currency):
    store, _ = order_store
    review_numeric_fact('order-quantity', PROFILE, action='approve', value_text=value, unit=unit, currency=currency,
                        note='Synthetic incompatible quantity', db_engine=store)
    with pytest.raises(ValueError, match='whole piece'):
        review_scope_completeness('order', PROFILE, expected_row_ids=('order-7',), rounding_rule=RoundingRule(0),
            definition='Synthetic count', note='Invalid quantity basis',
            quantity_target={'root_doc_id':1,'product_scope':'product','site':'factory'}, db_engine=store)


def test_preview_and_independent_math_reference_keep_source_quantity(order_store):
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.services.rag_evidence_service import rollup
    from mech_chatbot.evaluation.grounded_math_v2 import evaluate_math_v2
    from mech_chatbot.db.repositories.graph_v2 import retrieve_graph_v2
    from mech_chatbot.adapters.chat_runtime import _query_plan_payload
    from mech_chatbot.domain.query_plan import QueryBranch, QueryPlan
    from time import monotonic
    store, engine = order_store
    with bind_repository_runtime(db_engine=engine):
        actual = rollup('document:1', PROFILE, product_scope='product', site='factory', quantity_scope='order', rounding_scope='policy')
        tree = retrieve_graph_v2('document:1', PROFILE, product_scope='product', site='factory', deadline=monotonic()+10)
        order = load_approved_scope('order', PROFILE)
        policy = load_approved_scope('policy', PROFILE)
    assert actual['steps'][0]['display_value'] == '14.00'
    arguments = dict(manifest=tree['manifest'], order_quantity=Decimal('7'), rounding_rule=policy.rounding_rule,
                     rounding_scope=policy, order_scope=order, quantity_target=source_request().bom_rollup.graph)
    assert evaluate_math_v2('bom_rollup', actual, **arguments)['passed']
    wrong = {**actual, 'steps':[{**actual['steps'][0], 'value':'15', 'display_value':'15.00'}]}
    assert not evaluate_math_v2('bom_rollup', wrong, **arguments)['passed']
    from mech_chatbot.rag.grounded_math_v2 import bom_rollup
    assert bom_rollup(arguments['manifest'],Decimal('7'),policy.rounding_rule,rounding_scope=policy,
                      order_scope=order,quantity_target={}).status == 'blocked'
    payload = _query_plan_payload(QueryPlan((QueryBranch('materials','BOM từ lệnh sản xuất',bom_rollup=source_request().bom_rollup),)))
    action = payload['branches'][0]['bom_rollup']
    assert action['quantity_scope'] == 'order' and 'order_quantity' not in action


def test_quantity_binding_metadata_requires_target_access_even_for_scope_catalog(order_store):
    from mech_chatbot.db.repositories.structured_evidence import list_document_scopes, list_approved_document_scopes
    store, _ = order_store
    with store.begin() as conn:
        conn.execute(text('DELETE FROM PhongBanChiaSe WHERE DocID=1'))
    assert load_approved_scope('order', PROFILE, db_engine=store) is None
    assert list_approved_document_scopes(98, PROFILE, db_engine=store)['items'] == []
    review_rows = list_document_scopes(98, PROFILE, db_engine=store)
    assert len(review_rows) == 1 and review_rows[0]['quantity_binding'] is None


def test_old_literal_descriptor_hash_and_new_source_selector_stay_distinct():
    from dataclasses import asdict
    from hashlib import sha256
    from mech_chatbot.rag.query_decomposition import _request_key
    from mech_chatbot.rag.candidate_task_routing import _candidate_payload
    from mech_chatbot.domain.task_routing import CandidateSelection
    from mech_chatbot.domain.query_plan import QueryBranch, QueryPlan
    from scripts.eval.semantic_task_routing import _selector
    literal = request()
    old = {'graph':asdict(literal.bom_rollup.graph), 'order_quantity':'3', 'rounding_scope':'policy'}
    serialized = json.dumps([literal.question,literal.response_language,{'bom_rollup':old}],ensure_ascii=False,sort_keys=True,separators=(',',':'))
    assert _request_key(literal) == sha256(serialized.encode()).hexdigest()
    candidate = CandidateSelection('materials', bom_rollup=literal.bom_rollup)
    assert _candidate_payload(candidate)['bom_rollup'] == old
    branch = QueryBranch('materials', literal.question, bom_rollup=literal.bom_rollup)
    planned = replace(literal, bom_rollup=None, query_plan=QueryPlan((branch,)))
    old_branch = {**asdict(branch), 'bom_rollup':old}
    serialized = json.dumps([planned.question,planned.response_language,{'query_plan':{'branches':(old_branch,)}}],ensure_ascii=False,sort_keys=True,separators=(',',':'))
    assert _request_key(planned) == sha256(serialized.encode()).hexdigest()
    nested = {'graph':old['graph'], 'quantity_scope':'order', 'rounding_scope':'policy'}
    selected = _selector({'selection_id':'materials','selection_type':'bom_rollup','bom_rollup':nested})
    assert selected['bom_rollup']['quantity_scope'] == 'order' and 'order_quantity' not in selected['bom_rollup']
