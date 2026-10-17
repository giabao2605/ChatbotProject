"""Offline evidence validation for the actual fourteen-department catalog.

Input assertions of correctness are ignored. Identity, frozen questions,
adjudications and run gates must resolve to hash-pinned external artifacts.
Even complete offline evidence never creates human acceptance or activation.
"""

import math
from pathlib import Path
import re

from mech_chatbot.evaluation.metrics import nearest_rank
from mech_chatbot.evaluation.outcomes import VALID_OUTCOMES
from mech_chatbot.governance.artifact_references import load_json_reference

CANONICAL_DEPARTMENTS = (
    "Accountant", "HR", "HSE_5S", "ISO", "IT", "Maintenance", "Molding",
    "Planning", "Production", "Purchasing", "QualityControl", "Sales", "Technical", "Warehouse",
)
REQUIRED_ARMS = ("math", "query", "graph", "math+query", "math+graph", "query+graph", "math+query+graph")
_BINDING_FIELDS = ("source_commit", "snapshot_fingerprint", "config_hash", "gate_hash", "eval_hash")
_ACTOR_FIELDS = ("user_department", "user_roles", "allowed_departments", "allowed_sites", "max_security_level")


def required_arm_limits(arm, *, variant='candidate'):
    """Read the primary gate's limits, altering only the planned v2 budgets."""
    from scripts.eval.retrieval_intelligence_gate import compare

    stages = {"math": "grounded_math", "query": "query_decomposition", "graph": "graph_retrieval"}
    if arm not in REQUIRED_ARMS:
        raise ValueError("unknown capability arm")
    if variant not in {'baseline','candidate'}:
        raise ValueError('unknown evaluation variant')
    stage = stages.get(arm, "integrated_hardening")
    limits = dict(compare(stage, {}, {})["limits"])
    if variant == 'baseline':
        return limits
    if "math" in arm:
        limits = {**{key: value for key, value in limits.items()
                     if key not in {"max_calculations_per_query", "max_calculations"}},
                  "math_budget_version": "fixed-template-math-budgets-v2",
                  "max_domain_steps_per_template": {"compare_two_scopes": 4, "bom_rollup": 500},
                  "max_recorded_domain_steps_per_request": 508 if "query" in arm else 500}
    if "graph" in arm:
        limits = {**limits, "max_hops": 8, "max_edges": 500, "max_generation_edges": 50}
    return limits


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _normalized(value):
    return " ".join(str(value or "").split()).casefold()


def _binding_matches(record, binding):
    return isinstance(record, dict) and all(record.get(field) == binding.get(field) for field in _BINDING_FIELDS)


def _finite_nonnegative(value):
    return type(value) in (float, int) and math.isfinite(value) and value >= 0


def _verified_identity(identity, department, read):
    if not isinstance(identity, dict):
        return False
    evidence = read(identity.get("verification"))
    return (identity.get("verified") is True and identity.get("synthetic") is False
            and _nonempty(identity.get("identity")) and evidence is not None
            and evidence.get("schema") == "business-identity-v1"
            and evidence.get("identity") == identity["identity"]
            and evidence.get("department") == department and evidence.get("active") is True
            and evidence.get("synthetic") is False)


def _question_valid(question):
    if not isinstance(question, dict):
        return False
    actor = question.get("actor")
    sources = question.get("citations")
    return (all(_nonempty(question.get(field)) for field in ("id", "meaning_key", "question", "ground_truth"))
            and question.get("expected_outcome") in VALID_OUTCOMES
            and type(question.get("critical")) is bool and type(question.get("math")) is bool
            and isinstance(actor, dict) and all(field in actor for field in _ACTOR_FIELDS)
            and isinstance(sources, list)
            and (bool(sources) or question["expected_outcome"] in {"access_denied", "insufficient_evidence"})
            and all(isinstance(s, dict) and all(s.get(f) not in (None, "")
                    for f in ("doc_id", "version", "page", "source_id")) for s in sources))


def _case_correct(case, question, code, reviewer, dataset_hash, binding):
    if not isinstance(case, dict):
        return False
    outcome = question.get("expected_outcome")
    return (_binding_matches(case, binding) and case.get("case_id") == question.get("id")
            and case.get("department") == code and case.get("dataset_sha256") == dataset_hash
            and case.get("reviewer_id") == reviewer
            and outcome in VALID_OUTCOMES and case.get("expected_outcome") == outcome
            and case.get("actual_outcome") == outcome and case.get("answer_correct") is True
            and case.get("citation_correct") is True and case.get("leaked") is False
            and case.get("wrong_version") is False and _nonempty(case.get("answer"))
            and isinstance(case.get("citations"), list)
            and (bool(case["citations"]) or outcome in {"access_denied", "insufficient_evidence"}))


def _verify_math_reference(actual, reference):
    from decimal import Decimal, DecimalException
    import json
    from mech_chatbot.db.repositories.structured_evidence import _fact, _manifest
    from mech_chatbot.domain.structured_evidence import RoundingRule
    from mech_chatbot.domain.graph_query import GraphQuery
    from mech_chatbot.evaluation.grounded_math_v2 import evaluate_math_v2

    def scope(value):
        if (not isinstance(value['unit'], str) or not isinstance(value.get('currency', ''), str)
                or any(f.get('value') is not None and not isinstance(f['value'], str) for f in value['facts'])):
            raise ValueError('Typed Decimal evidence required')
        return _manifest(json.dumps(value), tuple(_fact(f) for f in value['facts']))
    try:
        template = reference['template']
        if template == 'compare_two_scopes':
            arguments = {'scopes': tuple(scope(s) for s in reference['scopes'])}
        elif template == 'bom_rollup':
            if not isinstance(reference['order_quantity'], str):
                return False
            arguments = {'manifest': reference['manifest'], 'order_quantity': Decimal(reference['order_quantity']),
                         'rounding_rule': RoundingRule(**reference['rounding_rule']),
                         'rounding_scope': scope(reference['rounding_scope']) if reference.get('rounding_scope') else None,
                         **({'order_scope': scope(reference['order_scope']),
                             'quantity_target': GraphQuery(**reference['quantity_target'])} if reference.get('order_scope') else {})}
        else:
            return False
        return evaluate_math_v2(template, actual, **arguments)['passed']
    except (KeyError, ValueError, TypeError, AttributeError, DecimalException):
        return False


def _other_math_branches_valid(case, reference, results, arm):
    branches = reference.get('other_branches', [])
    if not isinstance(branches, list) or branches != case.get('other_branches', []):
        return False
    if branches and ('query' not in arm or len(branches) + len(results) > 3):
        return False
    citations = case.get('citations')
    if not isinstance(citations, list):
        return False
    allowed = {c.get('source_id') for c in citations if isinstance(c, dict) and _nonempty(c.get('source_id'))}
    ids = []
    for branch in branches:
        if (not isinstance(branch, dict) or set(branch) != {'branch_id','kind','status','source_ids'}
                or not _nonempty(branch['branch_id']) or branch['kind'] not in ('graph','text')
                or branch['status'] not in ('complete','partial','unknown','blocked')):
            return False
        sources = branch['source_ids']
        if (not isinstance(sources, list) or not all(_nonempty(s) for s in sources)
                or not set(sources) <= allowed or (bool(sources) != (branch['status'] in ('complete','partial')))):
            return False
        ids = [*ids, branch['branch_id']]
    return len(ids) == len(set(ids))


def _math_outcome_matches(outcome, results, other):
    statuses = [r['status'] for r in (*results, *other)]
    usable = any(s in {'complete','partial'} for s in statuses)
    if outcome == 'full_answer':
        return bool(statuses) and all(s == 'complete' for s in statuses)
    if outcome == 'partial_answer':
        return usable and any(s != 'complete' for s in statuses)
    if outcome in {'insufficient_evidence','access_denied'}:
        return bool(statuses) and not usable
    return outcome == 'clarification_required' and not statuses


def _query_branch_shape(branch, allowed, earlier):
    fields = {'branch_id','template','depends_on','executed','status','data_complete','reason','source_ids'}
    if (not isinstance(branch, dict) or set(branch) != fields
            or not isinstance(branch['branch_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', branch['branch_id'])
            or branch['branch_id'] in earlier or branch['template'] not in ('graph_query','compare_two_scopes','bom_rollup')
            or type(branch['executed']) is not bool or type(branch['data_complete']) is not bool
            or branch['status'] not in ('complete','partial','unknown','blocked')
            or (branch['reason'] is not None and not _nonempty(branch['reason']))):
        return False
    deps, sources = branch['depends_on'], branch['source_ids']
    if (not isinstance(deps, list) or len(deps) > 2 or not all(_nonempty(d) for d in deps)
            or len(deps) != len(set(deps)) or not set(deps) <= set(earlier)
            or not isinstance(sources, list) or not all(_nonempty(s) for s in sources)
            or len(sources) != len(set(sources)) or not set(sources) <= allowed):
        return False
    status, complete = branch['status'], branch['data_complete']
    return (bool(sources) == (status in ('complete','partial'))
            and (status != 'complete' or complete)
            and (status not in ('unknown','blocked') or not complete)
            and (status != 'partial' or branch['template'] == 'graph_query' or not complete))


def _query_math_branches_valid(case, reference, results, expected, arm):
    from mech_chatbot.evaluation.grounded_math_v2 import _json_matches

    branches = case.get('query_branches')
    if ('query' not in arm or case.get('query_branch_schema') != 'query-branch-execution-v2'
            or reference.get('query_branch_schema') != 'query-branch-execution-v2'
            or not isinstance(branches, list) or not 1 <= len(branches) <= 3
            or case.get('other_branches', []) or reference.get('other_branches', [])
            or not _json_matches(branches, reference.get('query_branches'))):
        return False
    allowed = {c.get('source_id') for c in case['citations'] if isinstance(c, dict) and _nonempty(c.get('source_id'))}
    earlier, calculations = {}, []
    for branch in branches:
        if not _query_branch_shape(branch, allowed, earlier):
            return False
        executable = all(earlier[d]['data_complete'] for d in branch['depends_on'])
        if branch['executed'] != executable:
            return False
        if not executable:
            if branch['status'] != 'blocked' or branch['reason'] != 'dependency_unavailable':
                return False
        elif branch['status'] == 'blocked':
            return False
        elif branch['template'] != 'graph_query':
            if branch['reason'] == 'evidence_unavailable':
                if branch['status'] != 'unknown':
                    return False
            else:
                reason = ('scope_unavailable' if branch['template'] == 'compare_two_scopes' else 'bom_evidence_unavailable')
                if branch['reason'] != (reason if branch['status'] == 'unknown' else None):
                    return False
                calculations.append(branch)
        earlier = {**earlier, branch['branch_id']: branch}
    if len(calculations) != len(results):
        return False
    return all(isinstance(actual, dict) and isinstance(source, dict)
               and actual.get('branch_id') == source.get('branch_id') == branch['branch_id']
               and actual.get('template') == source.get('template') == branch['template']
               and actual.get('status') == ('blocked' if branch['status'] == 'unknown' else branch['status'])
               for branch, actual, source in zip(calculations, results, expected))


def _math_case_passes(case, question, code, reviewer, binding, arm, read):
    count, results = case.get('calculation_count'), case.get('math_results')
    if type(count) is not int or count < 0 or not isinstance(results, list):
        return False
    if not question.get('math'):
        return count == 0 and not results
    reference = read(question.get('math_reference'))
    if not (isinstance(reference, dict) and reference.get('schema') == 'fixed-template-math-reference-v2'
            and _binding_matches(reference, binding) and reference.get('case_id') == question['id']
            and reference.get('department') == code and reference.get('reviewer_id') == reviewer):
        return False
    expected = reference.get('records')
    if not isinstance(expected, list) or len(expected) != len(results) or not 0 <= len(results) <= (3 if 'query' in arm else 1):
        return False
    if not _other_math_branches_valid(case, reference, results, arm):
        return False
    branched = any(record.get('query_branch_schema') is not None or record.get('query_branches', []) != []
                   for record in (case, reference))
    if branched and not _query_math_branches_valid(case, reference, results, expected, arm):
        return False
    limits = required_arm_limits(arm)
    counts = {'compare_two_scopes': 0, 'bom_rollup': 0}
    comparisons = 0
    for actual, source in zip(results, expected):
        if not isinstance(actual, dict) or not isinstance(source, dict):
            return False
        template, steps, declared = actual.get('template'), actual.get('steps'), actual.get('domain_step_count')
        if (not isinstance(template,str) or template not in counts or source.get('template') != template or not isinstance(steps, list)
                or type(declared) is not int or declared != len(steps)
                or declared > limits['max_domain_steps_per_template'][template]
                or not _verify_math_reference(actual, source)):
            return False
        counts = {**counts, template: counts[template] + declared}
        comparisons += template == 'compare_two_scopes'
    request_limit = (500 if any(r['template'] == 'bom_rollup' for r in results) else 0) + 4 * comparisons
    return (count == sum(counts.values()) and counts['bom_rollup'] <= 500
            and count <= min(request_limit, limits['max_recorded_domain_steps_per_request'])
            and _math_outcome_matches(question['expected_outcome'], case['query_branches'] if branched else results,
                                      [] if branched else reference.get('other_branches', [])))


def _department_result(department, binding, read):
    code = department.get("code")
    questions = department.get("questions") or []
    questions = questions if isinstance(questions, list) else []
    owner, approver = department.get("owner"), department.get("approver")
    dataset_ref = department.get("dataset_reference")
    dataset = read(dataset_ref)
    reviewer = approver.get("identity") if isinstance(approver, dict) else None
    question_ids = [q.get("id") for q in questions if isinstance(q, dict)]
    meanings = [_normalized(q.get("meaning_key")) for q in questions if isinstance(q, dict)]
    checks = {
        "owner_verified": _verified_identity(owner, code, read),
        "approver_verified": _verified_identity(approver, code, read),
        "at_least_75_real_questions": len(questions) >= 75,
        "question_contracts": all(_question_valid(q) for q in questions),
        "unique_questions": len(question_ids) == len(set(question_ids)) == len(questions)
        and len(meanings) == len(set(meanings)) and all(meanings),
        "frozen_dataset": dataset is not None and dataset.get("schema") == "department-dataset-v2"
        and dataset.get("frozen") is True and dataset.get("real") is True
        and dataset.get("department") == code and dataset.get("reviewer_id") == reviewer
        and dataset.get("questions") == questions,
    }
    verification = read(department.get("verification"))
    cases = verification.get("cases", []) if verification is not None else []
    cases = cases if isinstance(cases, list) else []
    case_ids = [c.get("case_id") for c in cases if isinstance(c, dict)]
    indexed = {c["case_id"]: c for c in cases if isinstance(c, dict) and c.get("case_id")}
    checks["verification_artifact"] = (verification is not None
        and verification.get("schema") == "department-case-evaluation-v2"
        and len(case_ids) == len(set(case_ids)) == len(questions) and set(case_ids) == set(question_ids))
    checks["no_leaks_or_wrong_versions"] = bool(cases) and all(
        isinstance(case, dict) and case.get("leaked") is False and case.get("wrong_version") is False
        for case in cases)
    dataset_hash = dataset_ref.get("sha256") if isinstance(dataset_ref, dict) else None
    correct = tuple(_case_correct(indexed.get(q.get("id")), q, code, reviewer, dataset_hash, binding)
                    for q in questions if isinstance(q, dict))
    rate = sum(correct) / len(questions) if questions else 0.0
    checks["correctness_at_least_90_percent"] = rate >= 0.9
    checks["math_and_critical_exact"] = bool(questions) and all(
        passed for q, passed in zip(questions, correct) if q.get("math") or q.get("critical"))
    return {"code": code, "question_count": len(questions), "correctness_rate": rate,
            "offline_passed": all(checks.values()), "checks": checks, "accepted": False}


def _matrix_result(payload, binding, contract, read):
    expected = {(a, c, v) for a in REQUIRED_ARMS for c in (1, 5) for v in ("baseline", "candidate")}
    indexed, reports, failures = {}, {}, []
    departments = {d["code"]: d for d in payload.get("departments", []) if isinstance(d, dict) and d.get("code")}
    for run in payload.get("runs", []):
        if not isinstance(run, dict):
            failures.append("invalid_run")
            continue
        key = (run.get("arm"), run.get("concurrency"), run.get("variant"))
        if key in indexed or key not in expected:
            failures.append("duplicate_or_unknown_run")
            continue
        indexed[key] = run
        evaluation, gate = read(run.get("evaluation_reference")), read(run.get("gate_reference"))
        run_binding = {**binding, 'source_commit':payload.get('baseline_source_commit')} if key[2]=='baseline' else binding
        if not (_nonempty(run_binding['source_commit']) and re.fullmatch('[0-9a-f]{40}',run_binding['source_commit'])
                and _binding_matches(run, run_binding) and _binding_matches(evaluation, run_binding)
                and _binding_matches(gate, run_binding) and evaluation is not None and gate is not None):
            failures.append(f"{key}:binding_or_artifact_missing")
            continue
        expected_limits = (contract or {}).get('baseline_limits' if key[2]=='baseline' else 'limits', {}).get(key[0])
        gate_checks = gate.get("checks")
        if not (gate.get("schema") == "department-arm-gate-v2" and gate.get("passed") is True
                and isinstance(gate_checks, dict) and gate_checks and all(v is True for v in gate_checks.values())
                and gate.get("limits") == expected_limits
                and gate.get("evaluation_sha256") == run["evaluation_reference"].get("sha256")
                and all(evaluation.get(f) == run.get(f) == gate.get(f) for f in ("arm", "variant", "concurrency"))):
            failures.append(f"{key}:gate_contract_mismatch")
        cases = evaluation.get("cases") or []
        if evaluation.get("schema") != "department-arm-evaluation-v2" or not isinstance(cases, list):
            failures.append(f"{key}:evaluation_invalid")
            continue
        if key[2] == 'baseline' and 'math' in key[0] and any(
                not isinstance(c,dict) or type(c.get('calculation_count')) is not int
                or not 0 <= c['calculation_count'] <= 1 for c in cases):
            failures.append(f"{key}:legacy_math_budget_failed")
        if any(not isinstance(case, dict) or case.get("leaked") is not False
               or case.get("wrong_version") is not False for case in cases):
            failures.append(f"{key}:leak_or_wrong_version")
        case_ids = [c.get("case_id") for c in cases if isinstance(c, dict)]
        expected_ids = {q["id"] for d in departments.values() for q in d.get("questions", []) if _question_valid(q)}
        if len(case_ids) != len(set(case_ids)) or set(case_ids) != expected_ids:
            failures.append(f"{key}:case_coverage_mismatch")
        observed = {c.get("case_id"): c for c in cases if isinstance(c, dict)}
        positive_templates = set()
        for code, department in departments.items():
            questions = department.get("questions") or []
            if not questions:
                failures.append(f"{key}:{code}:questions_missing")
                continue
            correct = [_case_correct(observed.get(q.get("id")), q, code,
                (department.get("approver") or {}).get("identity"),
                (department.get("dataset_reference") or {}).get("sha256"), run_binding) for q in questions]
            if key[2] == "candidate" and (sum(correct) / len(questions) < 0.9
                    or any(not value for q, value in zip(questions, correct) if q.get("math") or q.get("critical"))):
                failures.append(f"{key}:{code}:quality_failed")
            if key[2] == 'candidate' and 'math' in key[0]:
                math_valid = all(isinstance(observed.get(q.get('id')), dict) and _math_case_passes(
                    observed[q['id']], q, code, (department.get('approver') or {}).get('identity'),
                    binding, key[0], read) for q in questions)
                if not math_valid:
                    failures.append(f"{key}:{code}:math_evidence_or_budget_failed")
                else:
                    positive_templates = positive_templates | {r['template'] for q in questions
                        for r in observed[q['id']]['math_results'] if r['status'] == 'complete'}
        if key[2] == 'candidate' and 'math' in key[0] and positive_templates != {'compare_two_scopes', 'bom_rollup'}:
            failures.append(f"{key}:math_templates_missing")
        if not cases or any(not isinstance(c, dict) or not _finite_nonnegative(c.get("latency_ms"))
                            or not _finite_nonnegative(c.get("estimated_cost")) for c in cases):
            failures.append(f"{key}:telemetry_missing")
            continue
        reports[key] = {"latency": nearest_rank([c["latency_ms"] for c in cases], 0.95),
                        "cost": sum(c["estimated_cost"] for c in cases)}
    if set(indexed) != expected:
        failures.append("matrix_incomplete")
    for arm in REQUIRED_ARMS:
        for concurrency in (1, 5):
            baseline, candidate = reports.get((arm, concurrency, "baseline")), reports.get((arm, concurrency, "candidate"))
            if not baseline or not candidate:
                continue
            limits = (contract or {}).get("limits", {}).get(arm) or {}
            for metric, limit in (("latency", "max_latency_ratio"), ("cost", "max_cost_ratio")):
                ratio = candidate[metric] / baseline[metric] if baseline[metric] else (1 if candidate[metric] == 0 else math.inf)
                if not _finite_nonnegative(limits.get(limit)) or ratio > limits[limit]:
                    failures.append(f"{arm}:{concurrency}:{metric}_budget_failed")
    return {"required_run_count": len(expected), "observed_run_count": len(indexed),
            "offline_passed": not failures, "failures": failures}


def evaluate_department_readiness(payload, *, root: str | Path) -> dict:
    """Report readiness gaps; never mutate identity, approval, flags or runtime."""
    cache = {}

    def read(reference):
        if not isinstance(reference, dict):
            return None
        key = (str(reference.get("path")), str(reference.get("sha256")), str(reference.get("schema")))
        if key not in cache:
            cache[key] = load_json_reference(reference, root=root)
        return cache[key]

    payload = payload if isinstance(payload, dict) else {}
    binding = {field: payload.get(field) for field in _BINDING_FIELDS}
    catalog = payload.get("catalog") or []
    active = [d.get("code") for d in catalog if isinstance(d, dict) and d.get("active") is True]
    departments = payload.get("departments") or []
    departments = [d for d in departments if isinstance(d, dict)]
    codes = [d.get("code") for d in departments]
    results = [_department_result(d, binding, read) for d in departments]
    contract = read(payload.get("contract_reference"))
    verified_reviewers = {d["approver"]["identity"] for d in departments
                         if _verified_identity(d.get("approver"), d.get("code"), read)}
    checks = {
        "schema": payload.get("schema") == "department-readiness-v2",
        "binding_complete": all(isinstance(binding[f], str) and re.fullmatch(
            r"[0-9a-f]{40}" if f == "source_commit" else r"[0-9a-f]{64}", binding[f]) for f in _BINDING_FIELDS),
        "catalog_exact": len(active) == len(set(active)) == 14 and set(active) == set(CANONICAL_DEPARTMENTS),
        "department_coverage": len(codes) == len(set(codes)) == 14 and set(codes) == set(CANONICAL_DEPARTMENTS),
        "contract_frozen_and_reviewed": contract is not None and contract.get("schema") == "rag-integration-contract-v2"
        and contract.get("frozen") is True and contract.get("approved") is True
        and contract.get("reviewer_id") in verified_reviewers and _binding_matches(contract, binding)
        and contract.get("limits") == {arm: required_arm_limits(arm) for arm in REQUIRED_ARMS},
        "baseline_contract_unchanged": contract is not None and _nonempty(payload.get('baseline_source_commit'))
        and re.fullmatch('[0-9a-f]{40}',payload['baseline_source_commit']) is not None
        and contract.get('baseline_source_commit') == payload['baseline_source_commit']
        and contract.get('baseline_limits') == {arm: required_arm_limits(arm,variant='baseline') for arm in REQUIRED_ARMS},
        "all_departments_passed": bool(results) and all(r["offline_passed"] for r in results),
    }
    matrix = _matrix_result(payload, binding, contract, read)
    checks["matrix_passed"] = matrix["offline_passed"]
    passed = all(checks.values())
    return {"schema": "department-readiness-report-v2", "department_count": len(active),
            "offline_passed": passed, "status": "OFFLINE PASS" if passed else "NOT READY",
            "accepted": False, "evidence_kind": "offline", "checks": checks,
            "departments": results, "matrix": matrix}
