from dataclasses import replace
from copy import deepcopy
from decimal import Decimal

import pytest

from mech_chatbot.domain.structured_evidence import NumericFact, RoundingRule, ScopeManifest, SourceLocator
from mech_chatbot.rag.grounded_math_v2 import bom_rollup, compare_two_scopes, serialize_result
from mech_chatbot.evaluation.grounded_math_v2 import evaluate_math_v2, summarize_math_v2_evaluations


def scope(name, values, *, doc=1, version=1, unit="money", currency="VND"):
    facts = tuple(NumericFact(
        fact_id=f"{name}-{index}", label="cost", raw_value=value, value=Decimal(value),
        unit=unit, currency=currency, locator=SourceLocator(doc, version, row_key=f"r{index}"),
        review_status="approved", source_hash=f"hash-{doc}-{version}", extraction_method="csv",
    ) for index, value in enumerate(values))
    return ScopeManifest(
        scope_id=name, snapshot_id=f"snapshot-{name}", facts=facts,
        expected_row_ids=tuple(f.locator.row_key for f in facts), expected_row_count=len(facts),
        completeness_reviewed=True, source_revisions=((doc, version),),
        rounding_rule=RoundingRule(2, reviewed=True), definition="cost", period=name,
        unit=unit, currency=currency, authorized=True,
    )


def manifest():
    source = {"doc_id": 2, "version": 3, "page": 1, "source_hash": "h", "snapshot_id": "snap"}
    return {
        "contract_version": "graph-v2", "status": "complete", "snapshot_id": "snap",
        "product_scope": "p", "completeness_reviewed": True, "authorized": True,
        "current": True, "source_revisions": ((2, 3),), "expected_occurrence_ids": ("a/x", "b/x"),
        "occurrences": tuple({
            "occurrence_id": path, "part_key": "x", "unit": "pcs", "currency": "",
            "quantity_path": quantities, "quantity_basis": "per_parent", "sources": (source,),
            "approved": True, "current": True, "authorized": True,
        } for path, quantities in (("a/x", ("2", "3")), ("b/x", ("4", "5")))),
    }


def test_comparison_retains_four_steps_independent_revisions_and_exact_decimal():
    a, b = scope("A", ["0.1", "0.2"]), scope("B", ["0.6"], doc=2, version=7)
    result = compare_two_scopes(a, b)
    assert result.status == "complete"
    assert tuple(s.value for s in result.steps) == (Decimal("0.3"), Decimal("0.6"), Decimal("0.3"), Decimal("100"))
    record = serialize_result(result)
    assert record["domain_step_count"] == 4
    assert record["sources"][1]["source_revisions"] == [[2, 7]]
    assert a.facts[0].value == Decimal("0.1")


def test_zero_denominator_preserves_totals_and_delta():
    result = compare_two_scopes(scope("A", ["0"]), scope("B", ["5"]))
    assert result.status == "partial"
    assert result.steps[-1].status == "undefined"
    assert result.steps[-1].value is None
    assert result.steps[2].value == 5


@pytest.mark.parametrize("change", [
    {"authorized": False}, {"completeness_reviewed": False}, {"snapshot_id": ""},
    {"expected_row_ids": ("missing",)}, {"expected_row_count": 2},
    {"source_revisions": ((1, 99),)}, {"unit": "kg"}, {"currency": "USD"},
    {"definition": "revenue"}, {"rounding_rule": RoundingRule(2, reviewed=False)},
])
def test_scope_refusals_have_no_provenance(change):
    result = compare_two_scopes(scope("A", ["1"]), replace(scope("B", ["2"]), **change))
    assert result.status == "blocked"
    assert serialize_result(result)["sources"] == []
    assert result.steps == ()


@pytest.mark.parametrize("change", [
    {"review_status": "pending"}, {"conflicts": ("material mismatch",)},
    {"source_hash": ""},
    {"value": None}, {"unit": "kg"}, {"currency": "USD"},
])
def test_fact_fail_closed(change):
    b = scope("B", ["2"])
    b = replace(b, facts=(replace(b.facts[0], **change),))
    assert compare_two_scopes(scope("A", ["1"]), b).status == "blocked"


def test_bom_preserves_occurrence_paths_and_multiplies_production_quantity():
    data = manifest()
    result = bom_rollup(data, Decimal("10"), RoundingRule(0, reviewed=True))
    assert result.status == "complete"
    assert result.steps[0].value == Decimal("260")
    record = serialize_result(result)
    assert record["sources"][0]["expected_occurrence_ids"] == ["a/x", "b/x"]
    assert data["occurrences"][0]["quantity_path"] == ("2", "3")


@pytest.mark.parametrize("change", [
    {"status": "partial"}, {"authorized": False}, {"current": False},
    {"completeness_reviewed": False}, {"expected_occurrence_ids": ("a/x",)},
    {"source_revisions": ((2, 8),)}, {"snapshot_id": ""},
])
def test_bom_incomplete_and_stale_fail_closed(change):
    assert bom_rollup({**manifest(), **change}, Decimal("1"), RoundingRule(0, reviewed=True)).status == "blocked"


@pytest.mark.parametrize("change", [
    {"approved": False}, {"authorized": False}, {"current": False},
    {"quantity_path": ("NaN",)}, {"quantity_path": ("-1",)},
    {"quantity_path": ()}, {"quantity_basis": "unknown"}, {"sources": ()},
    {"part_key": ""}, {"occurrence_id": "b/x"},
])
def test_bom_occurrence_validation(change):
    data = manifest()
    data = {**data, "occurrences": ({**data["occurrences"][0], **change}, data["occurrences"][1])}
    assert bom_rollup(data, Decimal("1"), RoundingRule(0, reviewed=True)).status == "blocked"


def test_math_policy_rounds_for_display_only_and_empty_confirmed_scope_is_zero():
    a = replace(scope("A", []), empty_source_locator=SourceLocator(1, 1, sheet='CSV', cell='B1', table_index=1),
                empty_source_hash='a'*64, empty_source_extraction_method='csv')
    b = scope("B", ["1.235"])
    result = compare_two_scopes(a, b)
    assert result.steps[1].value == Decimal("1.235")
    assert result.steps[1].display_value == "1.24"


def test_unpinned_empty_scope_is_blocked_even_when_flags_assert_reviewed_zero():
    assert compare_two_scopes(scope('A',[]),scope('B',['1'])).status == 'blocked'


def test_empty_scope_canonical_contains_header_source_but_legacy_scope_hash_and_payload_stay_stable():
    from dataclasses import asdict
    import hashlib,json
    from mech_chatbot.domain.structured_evidence import reviewed_scope_fingerprint,source_bindings
    a=scope('A',['1'])
    legacy={k:v for k,v in asdict(a).items() if not k.startswith('empty_source_') and k not in {'excluded_row_reasons','quantity_binding'}}
    legacy_hash=hashlib.sha256(json.dumps(legacy,sort_keys=True,ensure_ascii=False,separators=(',',':'),default=str).encode()).hexdigest()
    assert reviewed_scope_fingerprint(a)==legacy_hash
    assert legacy_hash == '498c70e69c1823e8176dc21774d63c876e7d5d1bce339c2253d71ffad1ec33b3'
    assert not any(k.startswith('empty_source_') for k in serialize_result(compare_two_scopes(a,scope('B',['2'])))['sources'][0])
    empty=replace(scope('E',[]),empty_source_locator=SourceLocator(1,1,sheet='CSV',cell='B1',table_index=1),
                  empty_source_hash='a'*64,empty_source_extraction_method='csv')
    result=serialize_result(compare_two_scopes(empty,a))
    assert result['status']=='partial' and result['steps'][0]['value']=='0'
    assert result['sources'][0]['empty_source_locator']['cell']=='B1'
    assert source_bindings(empty)==((empty.empty_source_locator,'a'*64),)


def test_explicit_empty_header_region_requires_a_valid_page_and_finite_box():
    from mech_chatbot.domain.structured_evidence import source_bindings
    empty=replace(scope('E',[]),empty_source_locator=SourceLocator(1,1,page=1,bbox=(0.,0.,10.,10.),table_index=1),
                  empty_source_hash='a'*64,empty_source_extraction_method='checked_header')
    assert source_bindings(empty)==((empty.empty_source_locator,'a'*64),)
    with pytest.raises(ValueError):replace(empty,empty_source_locator=replace(empty.empty_source_locator,bbox=(0.,0.,float('nan'),10.)))
    with pytest.raises(ValueError):replace(empty,empty_source_locator=replace(empty.empty_source_locator,page=True))


def test_large_complete_ledger_does_not_exhaust_precision_by_row_count():
    a, b = scope('A',['123456.78']*2000), scope('B',['123456.79']*2000,doc=2)
    result = compare_two_scopes(a,b)
    assert result.status == 'complete'
    assert result.steps[2].value == Decimal('20.00')


@pytest.mark.parametrize("quantity", [Decimal("NaN"), Decimal("Infinity"), Decimal("-1"), 1.1])
def test_bom_invalid_order_quantity(quantity):
    assert bom_rollup(manifest(), quantity, RoundingRule(0, reviewed=True)).status == "blocked"


@pytest.mark.parametrize("number", ["NaN", "Infinity"])
def test_nonfinite_fact_rejected_at_shared_evidence_boundary(number):
    with pytest.raises(ValueError, match="finite Decimal"):
        replace(scope("A", ["1"]).facts[0], value=Decimal(number))


def test_gate_recomputes_values_and_retains_offline_boundary():
    scopes = (scope("A", ["10", "20"]), scope("B", ["45"], doc=2, version=7))
    result = serialize_result(compare_two_scopes(*scopes))
    evaluation = evaluate_math_v2("compare_two_scopes", result, scopes=scopes)
    assert evaluation["passed"]
    assert not evaluation["accepted"]
    assert evaluation["evidence_kind"] == "offline"


@pytest.mark.parametrize("tamper", [
    lambda r: r["steps"][0].update(value="999"),
    lambda r: r["steps"][3].update(value="10"),
    lambda r: r["steps"][0].update(display_value="10.01"),
    lambda r: r["steps"][0].update(formula="invented"),
    lambda r: r["steps"][0].update(source_ids=["other"]),
    lambda r: r["steps"][0].update(currency="USD"),
    lambda r: r["steps"].pop(),
    lambda r: r.update(domain_step_count=1),
    lambda r: r.update(status="blocked"),
    lambda r: r["sources"][0]["facts"][0].update(raw_value="999"),
    lambda r: r["sources"][0].update(snapshot_id="stale"),
    lambda r: r.update(rounding_rule={"scale": 0}),
    lambda r: r["sources"][0]["facts"][0]["locator"].update(version=True),
    lambda r: r["sources"][0].update(expected_row_count=True),
    lambda r: r["rounding_rule"].update(scale=2.0),
    lambda r: r["rounding_rule"].update(reviewed=1),
    lambda r: r.update(domain_step_count=4.0),
    lambda r: r['sources'][0]['facts'][0]['locator'].update(bbox=[float('nan'), 0, 1, 1]),
    lambda r: r['rounding_rule'].update(scale={2}),
])
def test_gate_rejects_tampered_arithmetic_steps_or_provenance(tamper):
    scopes = (scope("A", ["10"]), scope("B", ["15"]))
    actual = deepcopy(serialize_result(compare_two_scopes(*scopes)))
    tamper(actual)
    assert not evaluate_math_v2("compare_two_scopes", actual, scopes=scopes)["passed"]


def test_gate_bom_independently_verifies_occurrence_multiplier():
    data, policy = manifest(), RoundingRule(0, reviewed=True)
    actual = serialize_result(bom_rollup(data, Decimal("10"), policy))
    assert evaluate_math_v2("bom_rollup", actual, manifest=data, order_quantity=Decimal("10"), rounding_rule=policy)["passed"]
    actual["steps"][0]["value"] = "80"
    assert not evaluate_math_v2("bom_rollup", actual, manifest=data, order_quantity=Decimal("10"), rounding_rule=policy)["passed"]


def test_bom_gate_does_not_equate_boolean_and_integer_source_pages():
    data, policy = manifest(), RoundingRule(0, reviewed=True)
    actual = serialize_result(bom_rollup(data, Decimal("10"), policy))
    actual['sources'][0]['occurrences'][0]['sources'][0]['page'] = True
    assert not evaluate_math_v2('bom_rollup', actual, manifest=data,
                              order_quantity=Decimal('10'), rounding_rule=policy)['passed']


@pytest.mark.parametrize('field', ['unit', 'currency'])
def test_bom_rejects_boolean_unit_or_currency(field):
    data = manifest()
    data = {**data, 'occurrences': tuple({**o, field: True} for o in data['occurrences'])}
    assert bom_rollup(data, Decimal('10'), RoundingRule(0, reviewed=True)).status == 'blocked'


@pytest.mark.parametrize('field', ['unit', 'currency'])
def test_comparison_rejects_boolean_unit_or_currency(field):
    scopes = tuple(replace(s, **{field: True}, facts=tuple(
        replace(f, **{field: True}) for f in s.facts)) for s in (scope('A', ['10']), scope('B', ['15'])))
    assert compare_two_scopes(*scopes).status == 'blocked'


@pytest.mark.parametrize('template', ['compare_two_scopes', 'bom_rollup'])
@pytest.mark.parametrize('field', ['doc_id', 'version', 'page'])
@pytest.mark.parametrize('value', [True, 1.0])
def test_math_rejects_malformed_source_identities_in_reference_inputs(template, field, value):
    if template == 'compare_two_scopes':
        a = scope('A', ['10'])
        fact = replace(a.facts[0], locator=replace(a.facts[0].locator, **{field: value}))
        revisions = ((value if field == 'doc_id' else 1, value if field == 'version' else 1),)
        a = replace(a, facts=(fact,), source_revisions=revisions)
        actual = compare_two_scopes(a, scope('B', ['15']))
    else:
        data = manifest()
        source = {**data['occurrences'][0]['sources'][0], field: value}
        data = {**data, 'source_revisions': ((source['doc_id'], source['version']),),
                'occurrences': tuple({**o, 'sources': (source,)} for o in data['occurrences'])}
        actual = bom_rollup(data, Decimal('10'), RoundingRule(0, reviewed=True))
    assert actual.status == 'blocked'


def test_math_preserves_opaque_string_source_identities():
    scopes = (scope('A', ['10'], doc='drawing-a', version='rev-a'),
              scope('B', ['15'], doc='drawing-b', version='rev-b'))
    actual = serialize_result(compare_two_scopes(*scopes))
    assert actual['status'] == 'complete'
    assert evaluate_math_v2('compare_two_scopes', actual, scopes=scopes)['passed']


def test_bom_gate_verifies_rounding_source_and_rejects_missing_or_changed_proof():
    data = manifest()
    policy_scope = scope('quantity-policy', ['1'], doc=8, version=7, unit='pcs', currency='')
    policy = policy_scope.rounding_rule
    actual = serialize_result(bom_rollup(data, Decimal('10'), policy, rounding_scope=policy_scope))
    reference = dict(manifest=data, order_quantity=Decimal('10'), rounding_rule=policy,
                     rounding_scope=policy_scope)
    assert actual['steps'][0]['value'] == '260'
    assert evaluate_math_v2('bom_rollup', actual, **reference)['passed']
    changed = deepcopy(actual)
    changed['sources'][1]['source_revisions'] = [[8, 99]]
    assert not evaluate_math_v2('bom_rollup', changed, **reference)['passed']
    missing = {**actual, 'sources': actual['sources'][:1]}
    assert not evaluate_math_v2('bom_rollup', missing, **reference)['passed']


@pytest.mark.parametrize('change', [
    {'unit': 'kg'}, {'currency': 'USD'}, {'authorized': False},
    {'completeness_reviewed': False}, {'rounding_rule': RoundingRule(0, reviewed=True)},
])
def test_bom_blocks_incompatible_or_unreviewed_rounding_scope(change):
    data = manifest()
    candidate = scope('quantity-policy', ['1'], unit='pcs', currency='')
    policy = candidate.rounding_rule
    candidate = replace(candidate, **change)
    actual = serialize_result(bom_rollup(data, Decimal('10'), policy, rounding_scope=candidate))
    assert actual['status'] == 'blocked' and actual['sources'] == []
    assert evaluate_math_v2('bom_rollup', actual, manifest=data, order_quantity=Decimal('10'),
                            rounding_rule=policy, rounding_scope=candidate)['passed']


def test_gate_zero_denominator_and_invalid_contract_are_not_silent_passes():
    scopes = (scope("A", ["0"]), scope("B", ["10"]))
    actual = serialize_result(compare_two_scopes(*scopes))
    assert evaluate_math_v2("compare_two_scopes", actual, scopes=scopes)["passed"]
    assert not evaluate_math_v2("unknown", actual)["passed"]
    assert not evaluate_math_v2("compare_two_scopes", actual, scopes=())["passed"]


def test_summary_requires_positive_both_templates_and_every_case_passes():
    scopes = (scope("A", ["10"]), scope("B", ["15"]))
    comparison = evaluate_math_v2("compare_two_scopes", serialize_result(compare_two_scopes(*scopes)), scopes=scopes)
    data, policy = manifest(), RoundingRule(0, reviewed=True)
    bom = evaluate_math_v2("bom_rollup", serialize_result(bom_rollup(data, Decimal("1"), policy)),
                           manifest=data, order_quantity=Decimal("1"), rounding_rule=policy)
    assert not summarize_math_v2_evaluations([])["offline_passed"]
    assert not summarize_math_v2_evaluations([comparison])["offline_passed"]
    summary = summarize_math_v2_evaluations([comparison, bom])
    assert summary["offline_passed"] and summary["exact_correctness_rate"] == 1.0
    assert not summary["accepted"]
    assert not summarize_math_v2_evaluations([comparison, {**bom, "passed": False}])["offline_passed"]
