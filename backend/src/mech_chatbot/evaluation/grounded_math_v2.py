"""Offline v2 gate: independently recompute both fixed arithmetic templates.

A passing offline record is not a pilot decision or release authorization.
The inputs here are the evaluator's pinned reference evidence, not copies
obtained from the observed answer.
"""

from decimal import Decimal, DecimalException, localcontext
import json
import re

from mech_chatbot.domain.task_routing import CANDIDATE_TASK_ROUTING, MIXED_CANDIDATE_TASK_ROUTING
from mech_chatbot.rag.grounded_math_v2 import (
    _finite, _json_value, _manifest_values, _policy_valid, _precision, _scope_valid, bom_order_quantity,
)


def _reference_step(name, value, unit, currency, formula, source_ids, policy):
    return {
        "step_id": name, "value": None if value is None else str(value),
        "unit": unit, "currency": currency, "formula": formula,
        "source_ids": list(source_ids),
        "display_value": "" if value is None else format(
            value.quantize(Decimal(10) ** -policy.scale, rounding=policy.mode), "f",
        ),
        "status": "undefined" if value is None else "valid",
    }


def _record(template, *, steps=(), sources=(), policy=None, status="complete", reason=""):
    return {
        "schema": "grounded-calculation-v2", "template": template,
        "status": status, "reason": reason, "domain_step_count": len(steps),
        "steps": list(steps), "sources": _json_value(sources),
        "rounding_rule": _json_value(policy),
    }


def _comparison_reference(scopes):
    if len(scopes) != 2:
        return None
    a, b = scopes
    try:
        valid = _scope_valid(a) and _scope_valid(b)
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        return _record("compare_two_scopes", status="blocked", reason="invalid_scope_evidence")
    if (a.scope_id == b.scope_id or a.definition != b.definition
            or a.unit != b.unit or a.currency != b.currency or a.rounding_rule != b.rounding_rule):
        return _record("compare_two_scopes", status="blocked", reason="incompatible_scopes")
    policy = a.rounding_rule
    try:
        with localcontext() as context:
            context.prec = _precision(tuple(f.value for s in scopes for f in s.facts))
            totals = []
            for scope in scopes:
                total = Decimal(0)
                for fact in scope.facts:
                    total += fact.value
                totals.append(total)
            delta = totals[1] - totals[0]
            percent = (delta / totals[0]) * Decimal(100) if totals[0] else None
            steps = (
                _reference_step("sum_a", totals[0], a.unit, a.currency, f"sum({a.scope_id})", (a.scope_id,), policy),
                _reference_step("sum_b", totals[1], b.unit, b.currency, f"sum({b.scope_id})", (b.scope_id,), policy),
                _reference_step("delta", delta, a.unit, a.currency, "sum_b - sum_a", (a.scope_id, b.scope_id), policy),
                _reference_step("percent_change", percent, "%", "", "delta / sum_a * 100", (a.scope_id, b.scope_id), policy),
            )
    except DecimalException:
        return _record("compare_two_scopes", status="blocked", reason="numeric_range")
    return _record("compare_two_scopes", steps=steps, sources=scopes, policy=policy,
                   status="partial" if percent is None else "complete",
                   reason="zero_denominator" if percent is None else "")


def _bom_reference(manifest, order_quantity, rounding_rule, rounding_scope, order_scope=None, quantity_target=None):
    if manifest is None:
        return None
    if not (_finite(order_quantity) and order_quantity >= 0 and _policy_valid(rounding_rule)):
        return _record("bom_rollup", status="blocked", reason="invalid_quantity_or_rounding")
    if order_scope is not None and (quantity_target is None or bom_order_quantity(order_scope, quantity_target, manifest) != order_quantity):
        return _record('bom_rollup', status='blocked', reason='invalid_order_quantity_evidence')
    try:
        parsed = _manifest_values(manifest)
        if parsed is None:
            return _record("bom_rollup", status="blocked", reason="invalid_manifest_evidence")
        if rounding_scope is not None and not (
            _scope_valid(rounding_scope) and rounding_scope.rounding_rule == rounding_rule
            and all((o['unit'], o.get('currency', '')) ==
                    (rounding_scope.unit, rounding_scope.currency) for o, _ in parsed)
        ):
            return _record("bom_rollup", status="blocked", reason="incompatible_rounding_scope")
        with localcontext() as context:
            context.prec = _precision(
                (order_quantity, *(q for _, path in parsed for q in path)),
                product_factors=max((len(path) + 1 for _, path in parsed), default=1),
            )
            reference, occurrence_ids = {}, {}
            for occurrence, path in parsed:
                amount = order_quantity
                for factor in path:
                    amount *= factor
                key = (occurrence["part_key"], occurrence["unit"], occurrence.get("currency", ""))
                reference[key] = reference.get(key, Decimal(0)) + amount
                occurrence_ids.setdefault(key, []).append(occurrence["occurrence_id"])
            steps = tuple(_reference_step(
                part, amount, unit, currency, "order_quantity * sum(product(quantity_path))",
                (*occurrence_ids[(part, unit, currency)], *((order_scope.scope_id,) if order_scope is not None else ())), rounding_rule,
            ) for (part, unit, currency), amount in sorted(reference.items()))
    except (DecimalException, ValueError, TypeError, AttributeError, KeyError):
        return _record("bom_rollup", status="blocked", reason="invalid_manifest_evidence")
    evidence = {**manifest, "order_quantity": order_quantity}
    sources = (evidence,) if rounding_scope is None else (evidence, rounding_scope)
    if order_scope is not None:
        sources = (*sources, order_scope)
    return _record("bom_rollup", steps=steps, sources=sources, policy=rounding_rule)


def _json_matches(observed, expected):
    """Compare canonical evidence without Python's bool/int/float coercion."""
    try:
        return json.dumps(observed, sort_keys=True, allow_nan=False) == json.dumps(
            expected, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return False


def _validate_routing_tasks(tasks, disposition, contract=None):
    from mech_chatbot.rag.query_decomposition import GRAPH_TASK_ROUTING, _allowed_outputs

    allowed = _allowed_outputs(contract or GRAPH_TASK_ROUTING)
    if type(tasks) is not list or len(tasks) > 8 or disposition == 'ready' and not 1 <= len(tasks) <= 3:
        raise ValueError('Bounded observed tasks required')
    seen = set()
    for task in tasks:
        if type(task) is not dict or set(task) != {'selection_id', 'template', 'requested_outputs', 'depends_on'}:
            raise ValueError('Invalid observed task shape')
        key, template, outputs, deps = (task[k] for k in ('selection_id', 'template', 'requested_outputs', 'depends_on'))
        if (key is not None and (type(key) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', key))
                or type(template) is not str or template not in allowed
                or type(outputs) is not list or not 1 <= len(outputs) <= len(allowed[template])
                or any(type(v) is not str or v not in allowed[template] for v in outputs)
                or len(set(outputs)) != len(outputs)
                or type(deps) is not list or len(deps) > 2
                or any(type(v) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', v) for v in deps)
                or len(set(deps)) != len(deps) or key is None and deps
                or disposition == 'ready' and (key is None or key in seen or not set(deps) <= seen)):
            raise ValueError('Invalid observed task references or outputs')
        seen.add(key)


def _validate_routing_observation(value, planners, contract=None):
    from mech_chatbot.rag.query_decomposition import GRAPH_TASK_ROUTING

    if (type(value) is not dict or set(value) != {'schema', 'contract', 'origin', 'disposition', 'tasks'}
            or value['schema'] != 'task-routing-observation-v3' or value['contract'] != (contract or GRAPH_TASK_ROUTING)
            or type(value['origin']) is not str or value['origin'] not in ('fast_path', 'planner', 'prevalidation')
            or type(value['disposition']) is not str or value['disposition'] not in ('ready', 'clarify', 'unsupported', 'overflow', 'invalid')
            or type(planners) is not int or planners != (1 if value['origin'] == 'planner' else 0)):
        raise ValueError('Invalid candidate routing observation')
    origin, disposition, tasks = value['origin'], value['disposition'], value['tasks']
    _validate_routing_tasks(tasks, disposition, contract)
    if (origin == 'prevalidation' and (disposition not in ('unsupported', 'invalid') or tasks)
            or origin == 'fast_path' and (disposition not in ('ready', 'overflow')
                or len(tasks) != (1 if disposition == 'ready' else 0))
            or origin == 'planner' and (disposition == 'overflow' and len(tasks) <= 3
                                       or disposition == 'invalid' and tasks)):
        raise ValueError('Inconsistent candidate routing disposition')


def export_task_routing_observation(result):
    """Export bounded candidate decisions, never provider text or acceptance."""
    from mech_chatbot.rag.execution import RagCollectedResult

    if not isinstance(result, RagCollectedResult) or result.observed_task_routing is None:
        raise ValueError('A completed candidate routing observation is required')
    _validate_observed_outcome(result)
    value, budget = result.observed_task_routing, result.diagnostics.budget
    _validate_routing_observation(value, budget.planners)
    if value['disposition'] != 'ready':
        reasons = {'overflow': ('intent_overflow',), 'clarify': ('task_clarification',),
                   'unsupported': ('task_unsupported', 'task_clarification'),
                   'invalid': ('task_invalid_plan', 'task_binding_mismatch')}
        actions = tuple(getattr(budget, field) for field in (
            'subqueries', 'corrections', 'repairs', 'calculations', 'graph_edges',
            'graph_examined_relations', 'provider_retries', 'final_generations'))
        if (result.outcome != 'refused' or result.diagnostics.get('answer_outcome') != 'refused'
                or result.refusal_reason not in reasons[value['disposition']]
                or any(type(v) is not int or v != 0 for v in actions)
                or result.observed_math_results or result.citations or result.diagnostics.retrieved_documents
                or any(key in result.diagnostics for key in ('query_v2', 'math_v2', 'graph_v2'))):
            raise ValueError('Nonready routing requires no evidence or actions')
    return json.loads(json.dumps({**value, 'planner_count': budget.planners,
                                 'accepted': False, 'offline_only': True}, allow_nan=False))


def _validate_candidate_routing_observation(value, planners, contract=CANDIDATE_TASK_ROUTING):
    from mech_chatbot.rag.query_decomposition import GRAPH_TASK_ROUTING

    fields = {'schema', 'contract', 'origin', 'disposition', 'tasks',
              'candidate_context_sha256', 'resolved_request_sha256'}
    if (type(value) is not dict or set(value) != fields
            or value['schema'] != ('mixed-candidate-task-routing-observation-v5-draft'
                if contract == MIXED_CANDIDATE_TASK_ROUTING else 'candidate-task-routing-observation-v4')
            or value['contract'] != contract):
        raise ValueError('Invalid candidate routing observation contract')
    legacy = {key: value[key] for key in ('origin', 'disposition', 'tasks')}
    validation_contract = MIXED_CANDIDATE_TASK_ROUTING if contract == MIXED_CANDIDATE_TASK_ROUTING else GRAPH_TASK_ROUTING
    _validate_routing_observation({**legacy, 'schema': 'task-routing-observation-v3',
                                  'contract': validation_contract}, planners, validation_contract)
    if value['disposition'] != 'ready' and any(
            task['selection_id'] is not None or task['depends_on'] for task in value['tasks']):
        raise ValueError('Nonready candidates require redacted task references')
    context, resolved = value['candidate_context_sha256'], value['resolved_request_sha256']
    context_missing = value['origin'] == 'prevalidation' and value['disposition'] == 'invalid'
    if (not (type(context) is str and re.fullmatch(r'[0-9a-f]{64}', context))
            and not (context is None and context_missing)
            or value['disposition'] == 'ready' and not (
                type(resolved) is str and re.fullmatch(r'[0-9a-f]{64}', resolved))
            or value['disposition'] != 'ready' and resolved is not None):
        raise ValueError('Invalid candidate routing hashes')


def export_candidate_task_routing_observation(result):
    """Export the separate V4 candidate contract without source descriptors."""
    return _export_candidate_task_routing_observation(result, CANDIDATE_TASK_ROUTING)


def export_mixed_candidate_task_routing_observation(result):
    """Export mixed draft receipts without conferring historical gate authority."""
    return _export_candidate_task_routing_observation(result, MIXED_CANDIDATE_TASK_ROUTING)


def _export_candidate_task_routing_observation(result, contract):
    from mech_chatbot.rag.execution import RagCollectedResult

    if not isinstance(result, RagCollectedResult) or result.observed_task_routing is None:
        raise ValueError('A completed V4 candidate routing observation is required')
    _validate_observed_outcome(result)
    value, budget = result.observed_task_routing, result.diagnostics.budget
    _validate_candidate_routing_observation(value, budget.planners, contract)
    if type(budget.provider_retries) is not int or budget.provider_retries != 0:
        raise ValueError('Candidate routing does not allow provider retries')
    if value['disposition'] != 'ready':
        reasons = {'overflow': ('intent_overflow',), 'clarify': ('task_clarification',),
                   'unsupported': ('task_unsupported', 'task_clarification'),
                   'invalid': ('task_invalid_plan', 'task_binding_mismatch')}
        actions = tuple(getattr(budget, field) for field in (
            'subqueries', 'corrections', 'repairs', 'calculations', 'graph_edges',
            'graph_examined_relations', 'provider_retries', 'final_generations'))
        if (result.outcome != 'refused' or result.diagnostics.get('answer_outcome') != 'refused'
                or result.refusal_reason not in reasons[value['disposition']]
                or any(type(v) is not int or v != 0 for v in actions)
                or result.observed_math_results or result.citations or result.diagnostics.retrieved_documents
                or any(key in result.diagnostics for key in ('query_v2', 'math_v2', 'graph_v2'))):
            raise ValueError('Nonready routing requires no evidence or actions')
    return json.loads(json.dumps({**value, 'planner_count': budget.planners,
                                 'accepted': False, 'offline_only': True}, allow_nan=False))


def export_task_routing_diagnostics(result):
    """Separate redacted decoder receipt; legacy observations remain exact."""
    return _routing_diagnostic_receipt(result, export_task_routing_observation(result), 'task-routing-diagnostics-v1')


def export_candidate_task_routing_diagnostics(result):
    """Keep V4 decoder evidence separate from historical V3 receipts."""
    return _routing_diagnostic_receipt(result, export_candidate_task_routing_observation(result),
                                       'candidate-task-routing-diagnostics-v4-draft')


def export_mixed_candidate_task_routing_diagnostics(result):
    return _routing_diagnostic_receipt(result, export_mixed_candidate_task_routing_observation(result),
                                       'mixed-candidate-task-routing-diagnostics-v5-draft')


def _routing_diagnostic_receipt(result, routing, schema):
    from mech_chatbot.rag.query_decomposition import TASK_ROUTING_FAILURE_CODES

    code = result.observed_task_routing_failure_code
    decoder_failed = routing['origin'] == 'planner' and routing['disposition'] == 'invalid'
    if (type(result.trace_id) is not str or not 1 <= len(result.trace_id) <= 100 or not result.trace_id.isprintable()
            or decoder_failed and (type(code) is not str or code not in TASK_ROUTING_FAILURE_CODES)
            or not decoder_failed and code is not None):
        raise ValueError('A bounded failure code must match completed candidate routing')
    return {'schema':schema, 'trace_id':result.trace_id,
            'task_routing_contract':routing['contract'], 'origin':routing['origin'],
            'disposition':routing['disposition'], 'planner_count':routing['planner_count'],
            'failure_code':code, 'accepted':False, 'offline_only':True}


def _validate_observed_outcome(result):
    outcome = result.diagnostics.get('answer_outcome')
    reason = result.refusal_reason
    if (type(outcome) is not str or outcome not in (
            'full_answer', 'partial_answer', 'refused')
            or type(result.outcome) is not str or result.outcome not in ('answered', 'refused')
            or (result.outcome == 'refused') != (outcome == 'refused')
            or (reason is not None and type(reason) is not str)):
        raise ValueError('Invalid observed outcome')
    return outcome, reason


def _observation_outcome(result):
    outcome, reason = _validate_observed_outcome(result)
    routing = None
    if result.observed_task_routing is not None:
        exporter = (export_mixed_candidate_task_routing_observation
                    if type(result.observed_task_routing) is dict
                    and result.observed_task_routing.get('contract') == MIXED_CANDIDATE_TASK_ROUTING
                    else export_candidate_task_routing_observation
                    if type(result.observed_task_routing) is dict
                    and result.observed_task_routing.get('contract') == CANDIDATE_TASK_ROUTING
                    else export_task_routing_observation)
        routing = exporter(result)
    if reason != 'intent_overflow':
        return 'insufficient_evidence' if outcome == 'refused' else outcome
    planners = 0
    if routing is not None:
        if routing['disposition'] != 'overflow':
            raise ValueError('Intent overflow requires an overflow routing decision')
        planners = routing['planner_count']
    actions = tuple(getattr(result.diagnostics.budget, field) for field in (
        'subqueries', 'corrections', 'repairs', 'calculations',
        'graph_edges', 'graph_examined_relations', 'provider_retries', 'final_generations'))
    if (outcome != 'refused' or type(result.diagnostics.budget.planners) is not int
            or result.diagnostics.budget.planners != planners
            or any(type(value) is not int or value != 0 for value in actions)
            or result.observed_math_results or result.citations or result.diagnostics.retrieved_documents
            or any(key in result.diagnostics for key in ('query_v2', 'math_v2', 'graph_v2'))):
        raise ValueError('Intent overflow requires an untouched execution')
    return 'clarification_required'


def export_math_observation(result):
    """Project an opt-in completed execution; never read golden inputs or approve."""
    from mech_chatbot.rag.execution import RagCollectedResult

    if not isinstance(result, RagCollectedResult) or result.observed_math_results is None:
        raise ValueError('A completed opt-in Math observation is required')
    actual_outcome = _observation_outcome(result)
    records = result.observed_math_results
    budget = result.diagnostics.budget
    count = budget.calculations
    if (type(count) is not int or not 0 <= count <= 508 or len(records) > 3
            or any(((record.get('schema') != 'grounded-calculation-v2' or record.get('template') == 'sum_reviewed_scope')
                    and not (record.get('schema') == 'grounded-calculation-scope-total-v1'
                             and record.get('template') == 'sum_reviewed_scope'
                             and record.get('status') in {'complete', 'blocked'}
                             and record.get('domain_step_count') == int(record.get('status') == 'complete')))
                   or type(record.get('domain_step_count')) is not int
                   or not isinstance(record.get('steps'), list)
                   or record['domain_step_count'] != len(record['steps']) for record in records)
            or sum(record['domain_step_count'] for record in records) != count):
        raise ValueError('Incomplete recorded Math steps')
    if any(record.get('schema') == 'grounded-calculation-scope-total-v1' for record in records) and (
            len(records) != 1 or result.diagnostics.get('query_v2')):
        raise ValueError('Scope total observations require one standalone template')
    source_ids = {card['source_id'] for card in result.citations}
    query = result.diagnostics.get('query_v2') or {}
    branches = [{field: branch[field] for field in (
                    'branch_id','template','depends_on','executed','status','data_complete','reason')} | {
                 'source_ids': [card['source_id'] for card in branch.get('sources', ())
                                if card['source_id'] in source_ids]}
                for branch in query.get('branches', ())]
    return json.loads(json.dumps({
        'schema': 'math-observation-v2', 'trace_id': result.trace_id,
        'math_results': records, 'calculation_count': count, 'query_branches': branches,
        'execution_budget': {name: getattr(budget,name) for name in (
            'planners','subqueries','corrections','repairs','graph_edges','graph_examined_relations',
            'provider_retries','final_generations','deadline_exceeded')},
        'query_branch_schema': ('query-branch-mixed-v1-draft'
            if query.get('schema') == 'query-plan-mixed-v1-draft'
            else 'query-branch-execution-v2') if branches else None,
        'answer': result.answer, 'citations': result.citations,
        'answer_outcome': result.diagnostics.get('answer_outcome'),
        'actual_outcome': actual_outcome,
        'accepted': False, 'offline_only': True,
    }, allow_nan=False))


def evaluate_math_v2(template, actual, *, scopes=(), manifest=None,
                     order_quantity=None, rounding_rule=None, rounding_scope=None, order_scope=None, quantity_target=None) -> dict:
    """Verify every recorded step, outcome, policy and source against reference."""
    reference = None
    if template == "compare_two_scopes":
        reference = _comparison_reference(scopes)
    elif template == "bom_rollup":
        reference = _bom_reference(manifest, order_quantity, rounding_rule, rounding_scope, order_scope, quantity_target)
    checks = {
        "reference_available": reference is not None,
        "schema": isinstance(actual, dict) and actual.get("schema") == "grounded-calculation-v2",
    }
    if reference is not None:
        observed = actual if isinstance(actual, dict) else {}
        checks.update({field: _json_matches(observed.get(field), value) for field, value in reference.items()})
    return {
        "schema": "grounded-calculation-evaluation-v2", "template": template,
        "applicable": reference is not None, "passed": all(checks.values()),
        "expected_status": reference["status"] if reference else "unknown",
        "domain_step_count": reference["domain_step_count"] if reference else 0,
        "checks": checks, "evidence_kind": "offline", "accepted": False,
    }


def summarize_math_v2_evaluations(evaluations) -> dict:
    """Require 100% exact checks and a positive case of each fixed template."""
    rows = tuple(evaluations)
    valid = tuple(row for row in rows if isinstance(row, dict))
    passed = sum(row.get("passed") is True for row in valid)
    positive = {row.get("template") for row in valid
                if row.get("passed") is True and row.get("expected_status") == "complete"}
    return {
        "schema": "grounded-calculation-gate-v2", "case_count": len(rows),
        "exact_correctness_rate": passed / len(rows) if rows else 0.0,
        "offline_passed": bool(rows) and passed == len(rows)
        and positive == {"compare_two_scopes", "bom_rollup"},
        "evidence_kind": "offline", "accepted": False,
    }
