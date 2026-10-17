"""Two deterministic templates over reviewed, complete, pinned evidence.

Callers must establish authorization and currentness from the repository;
these objects are never an approval mechanism for request-supplied evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal, DecimalException, InvalidOperation, localcontext
import json
from math import prod

from mech_chatbot.domain.structured_evidence import RoundingRule, ScopeManifest, scope_payload, source_bindings
from mech_chatbot.domain.graph_query import GraphQuery

_ROUNDING_MODES = {
    "ROUND_HALF_UP", "ROUND_HALF_EVEN", "ROUND_HALF_DOWN", "ROUND_UP",
    "ROUND_DOWN", "ROUND_CEILING", "ROUND_FLOOR", "ROUND_05UP",
}


@dataclass(frozen=True)
class MathStep:
    step_id: str
    value: Decimal | None
    unit: str
    currency: str
    formula: str
    source_ids: tuple[str, ...]
    display_value: str = ""
    status: str = "valid"


@dataclass(frozen=True)
class MathResult:
    template: str
    status: str
    steps: tuple[MathStep, ...] = ()
    reason: str = ""
    evidence_json: str = "[]"
    rounding_rule: RoundingRule | None = None


def _finite(value) -> bool:
    return (isinstance(value, Decimal) and value.is_finite()
            and len(value.as_tuple().digits) <= 4096 and abs(value.as_tuple().exponent) <= 4096)


def _policy_valid(policy) -> bool:
    return (isinstance(policy, RoundingRule) and policy.reviewed is True
            and type(policy.scale) is int and -12 <= policy.scale <= 12
            and policy.mode in _ROUNDING_MODES)


def _precision(values, *, product_factors=1) -> int:
    # Sums need carry digits for row count; products need spans for path depth.
    integer_span = max((max(1, v.adjusted() + 1) for v in values), default=1)
    fraction_span = max((max(0, -v.as_tuple().exponent) for v in values), default=0)
    precision = max(64, (integer_span + fraction_span) * product_factors
                    + len(str(len(values))) + 32)
    if precision > 8192:
        raise InvalidOperation("numeric range exceeds deterministic contract")
    return precision


def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value):
        return _json_value(scope_payload(value) if isinstance(value, ScopeManifest) else asdict(value))
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _step(step_id, value, unit, currency, formula, source_ids, policy):
    display = "" if value is None else format(
        value.quantize(Decimal(1).scaleb(-policy.scale), rounding=policy.mode), "f",
    )
    return MathStep(step_id, value, unit, currency, formula, tuple(source_ids),
                    display, "undefined" if value is None else "valid")


def _source_identity_valid(doc_id, version, page=None):
    return (all((type(value) is int and value > 0)
                or (isinstance(value, str) and bool(value.strip())) for value in (doc_id, version))
            and (page is None or (type(page) is int and page > 0)))


def _scope_valid(scope) -> bool:
    if not isinstance(scope, ScopeManifest):
        return False
    ids = tuple(f.fact_id for f in scope.facts)
    row_ids = tuple(f.locator.row_key for f in scope.facts)
    if not (scope.authorized is True and scope.completeness_reviewed is True
            and scope.scope_id and scope.snapshot_id and scope.definition and scope.unit
            and isinstance(scope.unit, str) and isinstance(scope.currency, str)
            and _policy_valid(scope.rounding_rule) and len(set(ids)) == len(ids)
            and len(set(scope.expected_row_ids)) == len(scope.expected_row_ids)
            and all(row_ids) and len(set(row_ids)) == len(row_ids)
            and set(row_ids) == set(scope.expected_row_ids)
            and type(scope.expected_row_count) is int and scope.expected_row_count == len(ids)):
        return False
    revisions = tuple(scope.source_revisions)
    if (any(not _source_identity_valid(doc, version) for doc, version in revisions)
            or len({doc for doc, _ in revisions}) != len(revisions)):
        return False
    if not scope.facts:
        return bool(source_bindings(scope))
    hashes = {}
    for fact in scope.facts:
        locator = fact.locator
        if not (fact.fact_id and fact.review_status == "approved" and not fact.conflicts
                and fact.source_hash and fact.extraction_method and _finite(fact.value)
                and fact.unit == scope.unit and fact.currency == scope.currency
                and _source_identity_valid(locator.doc_id, locator.version, locator.page)
                and (locator.page is not None or locator.row_key or locator.cell)):
            return False
        doc_key = (locator.doc_id, locator.version)
        if doc_key in hashes and hashes[doc_key] != fact.source_hash:
            return False
        hashes[doc_key] = fact.source_hash
    return bool(revisions) and set(hashes) <= set(revisions)


def compare_two_scopes(scope_a: ScopeManifest, scope_b: ScopeManifest) -> MathResult:
    """Track sum A, sum B, B-A and (B-A)/A*100 as four distinct steps."""
    template = "compare_two_scopes"
    try:
        valid = _scope_valid(scope_a) and _scope_valid(scope_b)
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        return MathResult(template, "blocked", reason="invalid_scope_evidence")
    if (scope_a.scope_id == scope_b.scope_id or scope_a.definition != scope_b.definition
            or scope_a.unit != scope_b.unit or scope_a.currency != scope_b.currency
            or scope_a.rounding_rule != scope_b.rounding_rule):
        return MathResult(template, "blocked", reason="incompatible_scopes")
    policy = scope_a.rounding_rule
    values = tuple(f.value for s in (scope_a, scope_b) for f in s.facts)
    try:
        with localcontext() as context:
            context.prec = _precision(values)
            a = sum((f.value for f in scope_a.facts), Decimal(0))
            b = sum((f.value for f in scope_b.facts), Decimal(0))
            delta = b - a
            percent = None if a == 0 else delta / a * 100
            steps = (
                _step("sum_a", a, scope_a.unit, scope_a.currency, f"sum({scope_a.scope_id})", (scope_a.scope_id,), policy),
                _step("sum_b", b, scope_b.unit, scope_b.currency, f"sum({scope_b.scope_id})", (scope_b.scope_id,), policy),
                _step("delta", delta, scope_a.unit, scope_a.currency, "sum_b - sum_a", (scope_a.scope_id, scope_b.scope_id), policy),
                _step("percent_change", percent, "%", "", "delta / sum_a * 100", (scope_a.scope_id, scope_b.scope_id), policy),
            )
    except DecimalException:
        return MathResult(template, "blocked", reason="numeric_range")
    return MathResult(template, "partial" if a == 0 else "complete", steps,
                      "zero_denominator" if a == 0 else "",
                      json.dumps(_json_value((scope_a, scope_b)), ensure_ascii=False), policy)


def _manifest_values(manifest):
    if not isinstance(manifest, Mapping):
        return None
    if not (manifest.get("contract_version") == "graph-v2"
            and manifest.get("status") == "complete" and manifest.get("snapshot_id")
            and manifest.get("product_scope") and manifest.get("completeness_reviewed") is True
            and manifest.get("authorized") is True and manifest.get("current") is True):
        return None
    occurrences = manifest.get("occurrences", ())
    expected = tuple(manifest.get("expected_occurrence_ids", ()))
    ids = tuple(o.get("occurrence_id") for o in occurrences)
    revisions = tuple(tuple(rev) for rev in manifest.get("source_revisions", ()))
    if (any(not _source_identity_valid(doc, version) for doc, version in revisions)
            or len(set(ids)) != len(ids) or len(set(expected)) != len(expected)
            or set(ids) != set(expected) or len({doc for doc, _ in revisions}) != len(revisions)):
        return None
    parsed, hashes = [], {}
    for occurrence in occurrences:
        if not (occurrence.get("occurrence_id") and occurrence.get("part_key")
                and occurrence.get("unit") and isinstance(occurrence["unit"], str)
                and isinstance(occurrence.get("currency", ""), str) and occurrence.get("approved") is True
                and occurrence.get("authorized") is True and occurrence.get("current") is True
                and occurrence.get("quantity_basis") == "per_parent"):
            return None
        path = tuple(occurrence.get("quantity_path", ()))
        if not path or len(path) > 8 or any(not isinstance(q, (str, Decimal)) for q in path):
            return None
        quantities = tuple(Decimal(q) for q in path)
        if any(not _finite(q) or q < 0 for q in quantities):
            return None
        sources = occurrence.get("sources", ())
        if not sources:
            return None
        for source in sources:
            key = (source.get("doc_id"), source.get("version"))
            if not (_source_identity_valid(*key, source.get("page")) and source.get("source_hash")
                    and source.get("snapshot_id") == manifest["snapshot_id"]
                    and (source.get("page") is not None or source.get("row_key") or source.get("cell"))):
                return None
            if key in hashes and hashes[key] != source["source_hash"]:
                return None
            hashes[key] = source["source_hash"]
        parsed.append((occurrence, quantities))
    if not revisions or not set(hashes) <= set(revisions):
        return None
    return parsed


def bom_order_quantity(scope, target, manifest):
    """Accept one reviewed piece count bound to this exact assembly source."""
    if (not isinstance(target, GraphQuery) or not isinstance(manifest, Mapping)
            or not _scope_valid(scope) or len(scope.facts) != 1 or scope.quantity_binding is None):
        return None
    binding, fact = scope.quantity_binding, scope.facts[0]
    if (scope.unit != 'piece' or scope.currency or fact.value < 0 or fact.value.adjusted() > 127
            or fact.value != fact.value.to_integral_value()
            or target.root_key != 'document:'+str(binding.root_doc_id)
            or (target.product_scope, target.site) != (binding.product_scope, binding.site)
            or manifest.get('product_scope') != binding.product_scope):
        return None
    if not any(isinstance(source, Mapping) and str(source.get('doc_id')) == str(binding.root_doc_id)
               and str(source.get('version')) == str(binding.root_version)
               and source.get('source_hash') == binding.root_source_hash
               for source in manifest.get('assembly_sources', ()) or ()):
        return None
    return fact.value


def bom_rollup(manifest: Mapping, order_quantity: Decimal, rounding_rule: RoundingRule,
               *, rounding_scope: ScopeManifest | None = None, order_scope=None, quantity_target=None) -> MathResult:
    """Multiply each physical occurrence path, then sum by part/unit/currency."""
    template = "bom_rollup"
    if not (_finite(order_quantity) and order_quantity >= 0 and _policy_valid(rounding_rule)):
        return MathResult(template, "blocked", reason="invalid_quantity_or_rounding")
    if order_scope is not None and (quantity_target is None or bom_order_quantity(order_scope, quantity_target, manifest) != order_quantity):
        return MathResult(template, 'blocked', reason='invalid_order_quantity_evidence')
    try:
        parsed = _manifest_values(manifest)
        if parsed is None:
            return MathResult(template, "blocked", reason="invalid_manifest_evidence")
        if rounding_scope is not None and not (
            _scope_valid(rounding_scope) and rounding_scope.rounding_rule == rounding_rule
            and all((o['unit'], o.get('currency', '')) ==
                    (rounding_scope.unit, rounding_scope.currency) for o, _ in parsed)
        ):
            return MathResult(template, "blocked", reason="incompatible_rounding_scope")
        quantities = (order_quantity, *(q for _, path in parsed for q in path))
        with localcontext() as context:
            context.prec = _precision(quantities, product_factors=max(
                (len(path) + 1 for _, path in parsed), default=1,
            ))
            totals, sources = {}, {}
            for occurrence, path in parsed:
                key = (occurrence["part_key"], occurrence["unit"], occurrence.get("currency", ""))
                totals[key] = totals.get(key, Decimal(0)) + order_quantity * prod(path)
                sources.setdefault(key, []).append(occurrence["occurrence_id"])
            steps = tuple(_step(
                part, value, unit, currency, "order_quantity * sum(product(quantity_path))",
                (*sources[(part, unit, currency)], *((order_scope.scope_id,) if order_scope is not None else ())), rounding_rule,
            ) for (part, unit, currency), value in sorted(totals.items()))
    except (DecimalException, ValueError, TypeError, AttributeError, KeyError):
        return MathResult(template, "blocked", reason="invalid_manifest_evidence")
    evidence = {**manifest, "order_quantity": order_quantity}
    sources = (evidence,) if rounding_scope is None else (evidence, rounding_scope)
    if order_scope is not None:
        sources = (*sources, order_scope)
    return MathResult(template, "complete", steps, evidence_json=json.dumps(
        _json_value(sources), ensure_ascii=False), rounding_rule=rounding_rule)


def serialize_result(result: MathResult) -> dict:
    """Return recorded operands and real source locators; invent no citations."""
    return {
        "schema": "grounded-calculation-v2", "template": result.template,
        "status": result.status, "reason": result.reason,
        "domain_step_count": len(result.steps), "steps": _json_value(result.steps),
        "sources": json.loads(result.evidence_json),
        "rounding_rule": _json_value(result.rounding_rule),
    }
