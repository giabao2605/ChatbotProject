"""Review storage and evaluation-only v2 capability entry points."""
from decimal import Decimal
from functools import partial
from time import monotonic
from mech_chatbot.db.evidence_snapshot import evidence_read_snapshot

from mech_chatbot.domain.structured_evidence import RoundingRule, reviewed_scope_fingerprint
from mech_chatbot.rag.grounded_math_v2 import MathResult, bom_rollup, bom_order_quantity, compare_two_scopes, serialize_result
from mech_chatbot.domain.graph_query import GraphQuery, BomRollupQuery


def list_document_scopes(doc_id, profile):
    from mech_chatbot.db.repositories.structured_evidence import list_document_scopes as load
    return load(doc_id, profile)


def list_scope_facts(scope_id, profile, *, source_storage=None, **pagination):
    from mech_chatbot.db.repositories.structured_evidence import list_scope_facts as load
    page = load(scope_id, profile, **pagination)
    if source_storage is None or not page['items']:
        return page
    from mech_chatbot.db.repositories.structured_evidence import scope_review_sources
    from mech_chatbot.services.evidence_row_context import with_source_rows
    sources = scope_review_sources(scope_id, profile, db_engine=pagination.get('db_engine'))
    return with_source_rows(page, sources, source_storage)


def list_approved_document_scopes(doc_id, profile, *, offset=0, limit=50, deadline_seconds=120):
    from mech_chatbot.db.repositories.structured_evidence import list_approved_document_scopes as load
    deadline = monotonic() + deadline_seconds
    with evidence_read_snapshot(partial(_check_deadline, deadline), deadline):
        return load(doc_id, profile, offset=offset, limit=limit)


def list_approved_graph_scopes(doc_id, profile, *, site, offset=0, limit=50, deadline_seconds=120):
    from mech_chatbot.db.repositories.graph_v2 import list_approved_graph_scopes as load
    deadline = monotonic() + deadline_seconds
    with evidence_read_snapshot(partial(_check_deadline, deadline), deadline):
        return load(doc_id, profile, site=site, offset=offset, limit=limit)


def list_approved_scope_facts(scope_id, profile, *, offset=0, limit=50):
    from mech_chatbot.db.repositories.structured_evidence import load_approved_scope, fact_payload
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Invalid evidence pagination')
    # ponytail: recheck the whole approved scope per page; add SQL paging with a
    # pinned manifest if the existing 100000-row ceiling misses its latency gate.
    scope = load_approved_scope(scope_id, profile)
    if (scope is None or scope.expected_row_count != len(scope.facts)
            or set(scope.expected_row_ids) != {fact.locator.row_key for fact in scope.facts}):
        raise PermissionError('Evidence unavailable')
    return {'items':[fact_payload(fact) for fact in scope.facts[offset:offset+limit]],
            'snapshot_id':scope.snapshot_id, 'scope_hash':reviewed_scope_fingerprint(scope), 'offset':offset, 'limit':limit,
            'has_more':offset+limit < len(scope.facts)}


def list_document_text_facts(doc_id, profile, **pagination):
    from mech_chatbot.db.repositories.text_evidence import list_document_text_facts as load
    return load(doc_id, profile, **pagination)


def list_bom_reference_options(doc_id, profile, **pagination):
    from mech_chatbot.db.repositories.text_evidence import list_bom_reference_options as load
    return load(doc_id, profile, **pagination)


def review_text_fact(fact_id, profile, **values):
    from mech_chatbot.db.repositories.text_evidence import review_text_fact as review
    return review(fact_id, profile, **values)


def list_assembly_sequence(doc_id, profile):
    from mech_chatbot.db.repositories.text_evidence import load_assembly_sequence
    value = load_assembly_sequence(doc_id, profile, review=True)
    manifest = value['manifest'] or {}
    return {'status': value['status'], 'snapshot_id': manifest.get('snapshot_id'),
            'expected_step_numbers': manifest.get('expected_step_numbers', ()) if value['status'] == 'complete' else (),
            'definition': manifest.get('definition', ''),
            'step_options': [{'fact_id': step['fact_id'], 'sequence_number': step['sequence_number'],
                              'label': step['value'] or step['raw_value'], 'review_status': step['review_status']}
                             for step in manifest.get('steps', ())]}


def review_assembly_sequence(doc_id, profile, **values):
    from mech_chatbot.db.repositories.text_evidence import review_assembly_sequence as review
    return review(doc_id, profile, **values)


def assembly_sequence(doc_id, profile, *, deadline_seconds=120):
    from mech_chatbot.db.repositories.text_evidence import load_assembly_sequence
    from mech_chatbot.rag.graph_retrieval_v2 import assembly_sequence_result
    deadline = monotonic() + deadline_seconds
    try:
        sequence = load_assembly_sequence(doc_id, profile)
    except PermissionError:
        sequence = {'status': 'unknown', 'manifest': None}
    return assembly_sequence_result(sequence, deadline=deadline)


def review_fact(fact_id, profile, **values):
    from mech_chatbot.db.repositories.structured_evidence import review_numeric_fact
    return review_numeric_fact(fact_id, profile, **values)


def review_scope(scope_id, profile, **values):
    from mech_chatbot.db.repositories.structured_evidence import review_scope_completeness
    rule = RoundingRule(values.pop('rounding_scale'), values.pop('rounding_mode'), reviewed=True)
    return review_scope_completeness(scope_id, profile, rounding_rule=rule, **values)


def compare_scopes(scope_a, scope_b, profile):
    deadline=monotonic()+120
    with evidence_read_snapshot(partial(_check_deadline,deadline),deadline):
        return _compare_scopes(scope_a,scope_b,profile)


def _compare_scopes(scope_a, scope_b, profile):
    from mech_chatbot.db.repositories.structured_evidence import load_approved_scope
    scopes = tuple(load_approved_scope(scope, profile) for scope in (scope_a, scope_b))
    if any(scope is None for scope in scopes) or not all(scope_is_current(scope,profile) for scope in scopes):
        return serialize_result(MathResult('compare_two_scopes', 'blocked', reason='scope_unavailable'))
    return serialize_result(compare_two_scopes(*scopes))


def scope_is_current(scope,profile):
    """Close a cross-source read without silently replacing its pinned policy."""
    from mech_chatbot.db.repositories.structured_evidence import load_approved_scope
    current=load_approved_scope(scope.scope_id,profile)
    return bool(current and reviewed_scope_fingerprint(current)==reviewed_scope_fingerprint(scope))


def dependency_source_attributes(cards, profile):
    """Recheck current executed evidence and SQL policy before a dependent prompt."""
    from sqlalchemy import text
    from mech_chatbot.application.chat_citations import structured_citation
    from mech_chatbot.db.evidence_snapshot import evidence_read_connection, evidence_snapshot_active
    from mech_chatbot.db.repositories.structured_evidence import approved_scope_in_connection, _source, _table
    from mech_chatbot.rag.scope_comparison import _sources
    if not isinstance(profile, dict):
        profile = {key:getattr(profile,key) for key in ('roles','allowed_departments','allowed_sites','max_security_level')}
    if not cards:
        raise PermissionError('Dependency evidence unavailable')
    attrs, scopes = {}, {}
    with evidence_read_connection() as conn:
        for source in cards:
            card = structured_citation(source, page_has_vision=lambda *_:False)
            if card is None or source.get('source_id') != card['source_id']:
                raise PermissionError('Dependency evidence unavailable')
            if card.get('scope_id'):
                key = card['scope_id']
                if key not in scopes:
                    scope = approved_scope_in_connection(conn,key,profile)
                    scopes[key] = {c['source_id'] for record in _sources((scope,), profile)
                        if (c := structured_citation(record,page_has_vision=lambda *_:False))} if scope else set()
                if card['source_id'] not in scopes[key]:
                    raise PermissionError('Dependency evidence unavailable')
            elif not (card.get('graph_context') and card.get('graph_hash') and evidence_snapshot_active()):
                raise PermissionError('Dependency evidence unavailable')
            doc = _source(conn,card['doc_id'],card['version_no'],profile)
            pin = conn.execute(text(f"SELECT SourceHash FROM {_table(conn,'StructuredSourceSnapshot')} WHERE "
                "DocID=:doc AND VersionNo=:version AND SnapshotID=:snapshot AND CurrentSnapshot=1"),
                {'doc':card['doc_id'],'version':card['version_no'],'snapshot':card['snapshot_id']}).scalar()
            if pin != card['source_hash']:
                raise PermissionError('Dependency evidence unavailable')
            attrs[card['doc_id']] = {'doc_id':card['doc_id'],'security_level':doc['SecurityLevel'],
                'external_processing_policy':doc.get('ExternalProcessingPolicy') or 'internal_only'}
    return tuple(attrs[key] for key in sorted(attrs))


def graph(root_key, profile, *, product_scope, site, direction='contains', deadline_seconds=120):
    from mech_chatbot.db.repositories import graph_v2
    deadline=monotonic()+deadline_seconds
    load=partial(graph_v2.retrieve_graph_v2,root_key,profile,product_scope=product_scope,site=site,
                 direction=direction,deadline=deadline)
    if monotonic()>=deadline:return load()
    with evidence_read_snapshot(partial(_check_deadline,deadline),deadline,db_engine=graph_v2.engine):
        return load()


def _check_deadline(deadline,stage):
    if monotonic()>=deadline:raise TimeoutError('Evidence request deadline exceeded')


def rollup(root_key, profile, *, product_scope, site, order_quantity=None, quantity_scope=None, rounding_scope,
           deadline_seconds=120):
    BomRollupQuery(GraphQuery(root_key, product_scope, site), order_quantity, rounding_scope, quantity_scope)
    deadline=monotonic()+deadline_seconds
    with evidence_read_snapshot(partial(_check_deadline,deadline),deadline):
        return _rollup(root_key,profile,product_scope=product_scope,site=site,order_quantity=order_quantity,
                       rounding_scope=rounding_scope,quantity_scope=quantity_scope,deadline=deadline)


def _rollup(root_key, profile, *, product_scope, site, order_quantity, rounding_scope,deadline,quantity_scope=None):
    from mech_chatbot.db.repositories.structured_evidence import load_approved_scope
    policy_scope = load_approved_scope(rounding_scope, profile)
    if policy_scope is None or policy_scope.rounding_rule is None:
        return serialize_result(MathResult('bom_rollup', 'blocked', reason='rounding_policy_unavailable'))
    tree = graph(root_key, profile, product_scope=product_scope, site=site,
                 deadline_seconds=max(0,deadline-monotonic()))
    if not scope_is_current(policy_scope,profile):
        return serialize_result(MathResult('bom_rollup','blocked',reason='rounding_policy_unavailable'))
    order_scope = load_approved_scope(quantity_scope, profile) if quantity_scope else None
    if order_scope and not scope_is_current(order_scope, profile):
        order_scope = None
    target = GraphQuery(root_key, product_scope, site)
    quantity = bom_order_quantity(order_scope, target, tree.get('manifest') or {}) if quantity_scope else Decimal(order_quantity)
    if quantity is None:
        return serialize_result(MathResult('bom_rollup','blocked',reason='bom_evidence_unavailable'))
    return serialize_result(bom_rollup(tree.get('manifest') or {}, quantity,
                                       policy_scope.rounding_rule, rounding_scope=policy_scope,
                                       order_scope=order_scope, quantity_target=target))
