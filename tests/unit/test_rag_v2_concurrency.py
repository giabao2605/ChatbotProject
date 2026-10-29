"""Concurrent v2 plans use independent SQLite connections and request ledgers."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from threading import Barrier, Lock, local

import pytest
from sqlalchemy import create_engine, event

from mech_chatbot.config.repository_runtime import bind_repository_runtime, current_repository_engine
from mech_chatbot.domain.query_plan import QueryBranch, QueryPlan
from mech_chatbot.rag.execution import (
    DefaultRagExecutor, RagInvocation, collect_rag_events, current_request_budget,
)
from .test_rag_bom_chat import FixtureEngine, bom_store, connection, store
from .test_rag_query_plan import combined_request


def _triple_request(index, denied):
    query = combined_request()
    graph, bom = query.query_plan.branches
    bom = replace(bom, bom_rollup=replace(bom.bom_rollup, order_quantity=str(index + 3)))
    compare = QueryBranch('compare', 'So sánh hai phạm vi',
                          scope_comparison=('quantity-scope', 'policy'))
    access = replace(query.access, allowed_departments=frozenset({'finance'})) if denied else query.access
    return replace(query, question='Cụm chứa gì; tính BOM; so sánh hai phạm vi',
                   access=access, query_plan=QueryPlan((graph, bom, compare)))


@pytest.fixture
def file_store(bom_store, tmp_path):
    source, _ = bom_store
    engine = create_engine('sqlite:///' + (tmp_path / 'v2.sqlite').as_posix())

    @event.listens_for(engine, 'connect')
    def sqlite_functions(dbapi, _record):
        dbapi.create_function('CHARINDEX', 2, lambda needle, value: value.find(needle) + 1)

        def json_value(value, path):
            result = json.loads(value) if value else {}
            for key in path[2:].split('.'):
                result = result.get(key) if isinstance(result, dict) else None
            return result

        dbapi.create_function('JSON_VALUE', 2, json_value)

    with source.connect() as original, engine.connect() as target:
        original.connection.driver_connection.backup(target.connection.driver_connection)
    try:
        yield engine
    finally:
        engine.dispose()


def _assert_allowed(result, index):
    assert result.outcome == 'answered'
    query = result.diagnostics['query_v2']
    assert query['status'] == 'complete'
    assert [branch['status'] for branch in query['branches']] == ['complete'] * 3
    graph, bom, compare = query['branches']
    assert graph['graph']['manifest']['occurrences'][0]['quantity_path'] == ('2',)
    assert [(step['step_id'], step['display_value']) for step in bom['math']['steps']] == [
        ('part:p', f'{2 * (index + 3)}.00')]
    assert [(step['step_id'], step['display_value']) for step in compare['math']['steps']] == [
        ('sum_a', '2.00'), ('sum_b', '1.00'), ('delta', '-1.00'), ('percent_change', '-50.00')]
    assert {card['doc_id']: card['version_no'] for card in result.citations} == {1: 1, 99: 5}
    assert {card['source_hash'] for card in result.citations} == {'a' * 64, 'b' * 64}
    assert [{card['doc_id'] for card in branch['sources']} for branch in query['branches']] == [
        {1}, {1, 99}, {1, 99}]
    assert all(branch['sources'] for branch in query['branches'])
    assert {card['source_id'] for card in result.citations} == {
        card['source_id'] for branch in query['branches'] for card in branch['sources']}
    assert [record['branch_id'] for record in result.observed_math_results] == ['total', 'compare']
    assert result.observed_math_results[0]['sources'][0]['order_quantity'] == str(index + 3)
    budget = result.diagnostics.budget
    assert (budget.subqueries, budget.calculations, budget.graph_edges,
            budget.graph_examined_relations, budget.planners, budget.final_generations) == (3, 5, 2, 2, 0, 0)


def _assert_denied(result):
    assert result.outcome == 'refused' and result.citations == ()
    assert [record['template'] for record in result.observed_math_results] == ['bom_rollup', 'compare_two_scopes']
    assert all(record['status'] == 'blocked' and not record['steps'] and not record['sources']
               for record in result.observed_math_results)
    query = result.diagnostics['query_v2']
    assert query['status'] == 'unknown'
    assert [branch['status'] for branch in query['branches']] == ['unknown'] * 3
    assert all(branch['sources'] == [] for branch in query['branches'])
    graph = query['branches'][0]['graph']
    assert graph['edges'] == ()
    manifest = graph['manifest']
    assert manifest['status'] == 'unknown' and manifest['authorized'] is False
    assert all(not manifest[key] for key in (
        'occurrences', 'expected_occurrence_ids', 'source_revisions', 'source_snapshots', 'assembly_sources'))
    public = result.answer + json.dumps(dict(result.diagnostics), default=str)
    assert all(secret not in public for secret in (
        'part:p', 'policy-snapshot', '"snapshot"', 'a' * 64, 'b' * 64, 'fixture-bom.pdf', 'policy.csv'))
    budget = result.diagnostics.budget
    assert (budget.subqueries, budget.calculations, budget.graph_edges,
            budget.graph_examined_relations, budget.planners, budget.final_generations) == (3, 0, 0, 0, 0, 0)


@pytest.mark.parametrize('concurrency', [1, 5])
@pytest.mark.parametrize('deny_last', [False, True])
def test_triple_requests_overlap_without_sharing_math_acl_or_ledgers(file_store, record_property, concurrency, deny_last):
    executor = DefaultRagExecutor()
    runtime = FixtureEngine(file_store)
    worker = local()
    barrier, lock = Barrier(concurrency, timeout=10), Lock()
    first_reads, active, maximum = [], 0, 0

    @event.listens_for(file_store, 'before_cursor_execute')
    def overlap(conn, _cursor, statement, _params, _context, _many):
        nonlocal active, maximum
        if getattr(worker, 'read_started', False) or not statement.lstrip().upper().startswith(('SELECT', 'WITH')):
            return
        worker.read_started = True
        with lock:
            first_reads.append((worker.index, id(conn.connection.driver_connection), id(current_request_budget())))
            active += 1
            maximum = max(maximum, active)
        try:
            barrier.wait()
        finally:
            with lock:
                active -= 1

    def execute(index):
        worker.index, worker.read_started = index, False
        original = current_repository_engine()
        assert current_request_budget() is None
        with bind_repository_runtime(db_engine=runtime):
            result = collect_rag_events(executor.run(_triple_request(index, deny_last and index == concurrency - 1),
                RagInvocation(f'concurrent-{index}', mode='evaluation', capture_math_observations=True)))
            assert current_repository_engine() is runtime and current_request_budget() is None
        assert current_repository_engine() is original
        return result

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(execute, range(concurrency)))
    assert maximum == concurrency
    assert len(first_reads) == concurrency
    assert len({connection_id for _, connection_id, _ in first_reads}) == concurrency
    assert len({ledger_id for _, _, ledger_id in first_reads}) == concurrency
    for index, result in enumerate(results):
        if deny_last and index == concurrency - 1:
            _assert_denied(result)
        else:
            _assert_allowed(result, index)
    record_property('concurrency', concurrency)
    record_property('denied_actor_count', int(deny_last))
    record_property('maximum_overlapping_first_reads', maximum)
    record_property('independent_sql_connections', len({value for _, value, _ in first_reads}))
    record_property('independent_request_ledgers', len({value for _, _, value in first_reads}))
