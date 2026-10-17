from decimal import Decimal

import pytest

from mech_chatbot.rag.graph_retrieval_v2 import traverse_graph_v2


pytestmark = pytest.mark.unit


def source(doc_id):
    return {"doc_id": doc_id, "version": 1, "page": 1,
            "source_quote": "reviewed row", "source_hash": "a" * 64}


def row(doc_id, row_id, child=None, quantity="2", leaf=True):
    return {"occurrence_id": f"{doc_id}:{row_id}:1", "part_key": "part:p",
            "quantity": quantity, "unit": "piece", "quantity_basis": "per_parent",
            "is_leaf": leaf, "child_doc_id": child,
            "sources": (source(doc_id),), "relation_type": "CONTAINS_PART"}


class Reader:
    def __init__(self, batches):
        self.batches = batches
        self.remaining = []

    def assembly(self, doc_id, remaining):
        self.remaining.append(remaining)
        return self.batches[doc_id]


def batch(doc_id, rows=(), **kwargs):
    return {"available": True, "rows_complete": True, "tree_complete": True,
            "snapshot_id": "snapshot-1", "expected_rows": len(rows),
            "source": source(doc_id), "rows": tuple(rows), **kwargs}


def test_shared_child_preserves_occurrence_paths_and_quantity():
    reader = Reader({1: batch(1, [row(1, 1, 2, leaf=False), row(1, 2, 2, "3", False)]),
                     2: batch(2, [row(2, 1, quantity="4")])})
    result = traverse_graph_v2(1, reader, product_scope="product-a")
    assert result["status"] == "complete"
    manifest = result["manifest"]
    assert len(manifest["occurrences"]) == 2
    assert {item["quantity_path"] for item in manifest["occurrences"]} == {("2", "4"), ("3", "4")}
    assert len(set(manifest["expected_occurrence_ids"])) == 2
    assert manifest["source_revisions"] == ((1, 1), (2, 1))


def test_display_edges_preserve_local_quantity_and_evidence_for_the_whole_occurrence_path():
    reader = Reader({1: batch(1, [row(1, 1, 2, leaf=False), row(1, 2, 2, '3', False)]),
                     2: batch(2, [row(2, 1, quantity='4')], snapshot_id='child-snapshot')})
    result = traverse_graph_v2(1, reader, product_scope='product-a')
    leaves = [edge for edge in result['edges'] if edge['source_key'] == 'document:2']
    assert result['status'] == 'complete' and len(leaves) == 2
    assert {edge['quantity_path'] for edge in leaves} == {('2', '4'), ('3', '4')}
    for edge in leaves:
        assert edge['quantity'] == '4' and edge['unit'] == 'piece' and edge['quantity_basis'] == 'per_parent'
        assert {(pin['doc_id'], pin['source_snapshot_id']) for pin in edge['sources']} == {
            (1, 'snapshot-1'), (2, 'child-snapshot')}
        assert all(pin['snapshot_id'] == result['manifest']['snapshot_id'] for pin in edge['sources'])


@pytest.mark.parametrize("change,reason", [
    ({"rows_complete": False}, "coverage_unverified"),
    ({"expected_rows": 2}, "coverage_unverified"),
    ({"available": False}, "coverage_unverified"),
    ({"tree_complete": False}, "coverage_unverified"),
])
def test_no_restricted_details_or_counts_when_coverage_unavailable(change, reason):
    result = traverse_graph_v2(1, Reader({1: batch(1, **change)}), product_scope="a")
    assert result["status"] != "complete"
    assert reason in result["reasons"]
    assert result["manifest"]["completeness_reviewed"] is False
    assert "expected_rows" not in repr(result)


def test_unresolved_child_and_cycles_block_rollup():
    unresolved = traverse_graph_v2(1, Reader({1: batch(1, [row(1, 1, leaf=False)])}), product_scope="a")
    assert unresolved["status"] == "partial"
    assert "binding_unresolved" in unresolved["reasons"]
    cycle = traverse_graph_v2(1, Reader({1: batch(1, [row(1, 1, 1, leaf=False)])}), product_scope="a")
    assert "cycle" in cycle["reasons"]


def test_request_budget_and_generation_limit_are_separate():
    rows = [row(1, index) for index in range(60)]
    result = traverse_graph_v2(1, Reader({1: batch(1, rows)}), product_scope="a")
    assert result["status"] == "complete"
    assert len(result["edges"]) == 50
    assert len(result["manifest"]["occurrences"]) == 60
    limited = traverse_graph_v2(1, Reader({1: batch(1, [row(1, i) for i in range(501)])}), product_scope="a")
    assert limited["status"] == "partial"
    assert "relation_limit" in limited["reasons"]


def test_level_limit_timeout_and_invalid_quantities():
    batches = {i: batch(i, [row(i, 1, i + 1, leaf=False)]) for i in range(1, 10)}
    result = traverse_graph_v2(1, Reader(batches), product_scope="a")
    assert "depth_limit" in result["reasons"]
    timed = traverse_graph_v2(1, Reader({}), product_scope="a", deadline=0)
    assert timed["status"] == "unknown"
    assert "deadline" in timed["reasons"]
    for quantity in ("NaN", "Infinity", "-2", "oops"):
        result = traverse_graph_v2(1, Reader({1: batch(1, [row(1, 1, quantity=quantity)])}), product_scope="a")
        assert "invalid_occurrence" in result["reasons"]


def test_independent_source_snapshots_and_process_rows_not_counted_as_parts():
    process = {**row(1, 2), "relation_type": "PRECEDES_OPERATION"}
    result = traverse_graph_v2(1, Reader({1: batch(1, [process])}), product_scope="a")
    assert result["status"] == "partial"
    assert not result["manifest"]["occurrences"]
    independent = traverse_graph_v2(1, Reader({1: batch(1, [row(1, 1, 2, leaf=False)]),
                       2: batch(2, [row(2, 1)], snapshot_id="different")}), product_scope="a")
    assert independent['status'] == 'complete'
    assert len(independent['manifest']['source_snapshots']) == 2
    from mech_chatbot.domain.structured_evidence import RoundingRule
    from mech_chatbot.rag.grounded_math_v2 import bom_rollup
    assert bom_rollup(independent['manifest'], Decimal('3'), RoundingRule(0, reviewed=True)).steps[0].value == Decimal('12')


def test_conflicting_pin_for_same_source_blocks_snapshot():
    conflicting = {**row(1, 1), 'sources': ({**source(1), 'snapshot_id':'other-pin'},)}
    result = traverse_graph_v2(1, Reader({1:batch(1,[conflicting])}), product_scope='a')
    assert 'snapshot_mismatch' in result['reasons']


def test_where_used_follows_reviewed_parent_documents_with_one_request_budget():
    from mech_chatbot.rag.graph_retrieval_v2 import traverse_where_used_v2

    class Parents:
        def __init__(self):
            self.calls = []

        def parents(self, key, remaining):
            self.calls.append((key, remaining))
            links = {'part:p': ('document:2',), 'document:2': ('document:1',)}
            return tuple({'occurrence_id': f'{parent}/{key}', 'source_key': parent,
                          'target_key': key, 'relation_type': 'CONTAINS_PART',
                          'sources': (source(int(parent.split(':')[1])),)}
                         for parent in links.get(key, ()))

    reader = Parents()
    result = traverse_where_used_v2('part:p', reader)
    assert [edge['source_key'] for edge in result['edges']] == ['document:2', 'document:1']
    assert reader.calls == [('part:p', 500), ('document:2', 499), ('document:1', 498)]
    assert result['status'] == 'partial'
    assert result['manifest'] is None
    assert result['reasons'] == ('coverage_unverified',)


@pytest.mark.parametrize('shape,reason', [('cycle', 'cycle'), ('depth', 'depth_limit'),
                                        ('wide', 'relation_limit')])
def test_where_used_stops_at_cycles_and_whole_request_limits(shape, reason):
    from mech_chatbot.rag.graph_retrieval_v2 import traverse_where_used_v2

    class Parents:
        def parents(self, key, remaining):
            index = 0 if key == 'part:p' else int(key.split(':')[1])
            parents = (range(1, 502) if shape == 'wide' else
                       (1,) if shape == 'cycle' else (index + 1,))
            return tuple({'occurrence_id': str(parent), 'source_key': f'document:{parent}',
                          'target_key': key, 'relation_type': 'CONTAINS_PART',
                          'sources': (source(parent),)} for parent in parents)

    result = traverse_where_used_v2('part:p', Parents())
    assert reason in result['reasons']
    assert result['status'] == 'partial' and result['manifest'] is None
    assert len(result['edges']) <= 50


def test_process_order_respects_shared_deadline_and_relation_limit():
    from mech_chatbot.rag.graph_retrieval_v2 import assembly_sequence_result
    assert assembly_sequence_result({'status':'complete'}, deadline=0)['reasons'] == ('deadline',)
    over_limit = {'status':'complete', 'manifest': {'steps':[{}] * 501}}
    result = assembly_sequence_result(over_limit)
    assert result['status'] == 'partial' and result['reasons'] == ('relation_limit',)
    assert result['manifest'] is None and result['edges'] == ()
