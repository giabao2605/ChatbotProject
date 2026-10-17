"""Immutable evidence exchanged by ingestion, review and deterministic math."""

from dataclasses import asdict, dataclass
from decimal import Decimal
import hashlib
import json
import math
import re


@dataclass(frozen=True)
class SourceLocator:
    doc_id: str
    version: str
    page: int | None = None
    row_key: str = ""
    table_index: int | None = None
    sheet: str | None = None
    cell: str | None = None
    bbox: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class NumericFact:
    fact_id: str
    label: str
    raw_value: str
    value: Decimal | None
    unit: str
    locator: SourceLocator
    review_status: str = "pending"
    conflicts: tuple[str, ...] = ()
    source_hash: str = ""
    extraction_method: str = ""
    currency: str = ""

    def __post_init__(self):
        if self.value is not None and (not isinstance(self.value, Decimal) or not self.value.is_finite()):
            raise ValueError("Numeric evidence requires finite Decimal values")
        if self.review_status not in {"pending", "approved", "rejected"} or not isinstance(self.conflicts, tuple):
            raise ValueError("Invalid evidence review state")


@dataclass(frozen=True)
class RoundingRule:
    scale: int
    mode: str = "ROUND_HALF_UP"
    reviewed: bool = False


@dataclass(frozen=True)
class BomQuantityBinding:
    root_doc_id: int
    root_version: int
    root_source_hash: str
    product_scope: str
    site: str

    def __post_init__(self):
        if (any(type(v) is not int or not 0 < v <= 2147483647 for v in (self.root_doc_id, self.root_version))
                or not isinstance(self.root_source_hash, str) or not re.fullmatch('[a-f0-9]{64}', self.root_source_hash)
                or any(not isinstance(v, str) or not v.strip() or len(v) > 255 for v in (self.product_scope, self.site))):
            raise ValueError('A pinned BOM quantity target is required')


@dataclass(frozen=True)
class ScopeManifest:
    scope_id: str
    snapshot_id: str
    facts: tuple[NumericFact, ...]
    expected_row_ids: tuple[str, ...] = ()
    expected_row_count: int | None = None
    completeness_reviewed: bool = False
    source_revisions: tuple[tuple[str, str], ...] = ()
    rounding_rule: RoundingRule | None = None
    definition: str = ""
    period: str = ""
    unit: str = ""
    currency: str = ""
    authorized: bool = False
    empty_source_locator: SourceLocator | None = None
    empty_source_hash: str = ""
    empty_source_extraction_method: str = ""
    excluded_row_reasons: tuple[tuple[str, str], ...] = ()
    quantity_binding: BomQuantityBinding | None = None

    def __post_init__(self):
        if self.quantity_binding is not None and not isinstance(self.quantity_binding, BomQuantityBinding):
            raise ValueError('Typed BOM quantity binding required')
        if not all(isinstance(value, tuple) for value in (self.facts, self.expected_row_ids, self.source_revisions)):
            raise ValueError("Scope collections must be immutable tuples")
        if (not isinstance(self.excluded_row_reasons, tuple)
                or any(not isinstance(pair, tuple) or len(pair) != 2
                       or any(not isinstance(v, str) or not v.strip() for v in pair)
                       or len(pair[0]) > 255 or len(pair[1]) > 2000 for pair in self.excluded_row_reasons)
                or len(dict(self.excluded_row_reasons)) != len(self.excluded_row_reasons)):
            raise ValueError('Unique excluded rows with bounded review reasons required')
        if not isinstance(self.empty_source_hash, str) or not isinstance(self.empty_source_extraction_method, str):
            raise ValueError('Typed empty source declaration required')
        if self.empty_source_locator is not None or self.empty_source_hash or self.empty_source_extraction_method:
            _validate_empty_source(self)


DataScope = ScopeManifest


def _validate_empty_source(scope):
    loc = scope.empty_source_locator
    if (scope.facts or not isinstance(loc, SourceLocator)
            or not isinstance(scope.empty_source_hash, str) or not re.fullmatch(r'[a-fA-F0-9]{64}', scope.empty_source_hash)
            or not isinstance(scope.empty_source_extraction_method, str) or not scope.empty_source_extraction_method.strip()
            or len(scope.empty_source_extraction_method) > 128
            or any(not ((type(v) is int and v > 0) or (isinstance(v, str) and v.strip())) for v in (loc.doc_id, loc.version))
            or scope.source_revisions != ((loc.doc_id, loc.version),)
            or type(loc.table_index) is not int or loc.table_index <= 0
            or (loc.page is not None and (type(loc.page) is not int or loc.page <= 0))):
        raise ValueError('A pinned empty table header is required')
    cell = (isinstance(loc.sheet, str) and bool(loc.sheet.strip()) and isinstance(loc.cell, str)
            and re.fullmatch(r'[A-Z]+[1-9]\d*', loc.cell) is not None)
    region = (loc.page is not None and isinstance(loc.bbox, tuple) and len(loc.bbox) == 4
              and all(type(v) in (int, float) and math.isfinite(v) for v in loc.bbox)
              and loc.bbox[0] < loc.bbox[2] and loc.bbox[1] < loc.bbox[3])
    if not cell and not region:
        raise ValueError('An explicit empty table header location is required')


def scope_payload(scope: ScopeManifest) -> dict:
    """Keep absent empty declarations out of historical canonical payloads."""
    payload = asdict(scope)
    if not scope.excluded_row_reasons:
        payload = {k: v for k, v in payload.items() if k != 'excluded_row_reasons'}
    if scope.quantity_binding is None:
        payload = {k: v for k, v in payload.items() if k != 'quantity_binding'}
    if scope.empty_source_locator is None:
        return {k: v for k, v in payload.items() if k not in {
            'empty_source_locator', 'empty_source_hash', 'empty_source_extraction_method'}}
    return payload


def source_bindings(scope: ScopeManifest) -> tuple[tuple[SourceLocator, str], ...]:
    """Bind actual rows or one validated empty declaration to source bytes."""
    if scope.facts:
        return tuple((fact.locator, fact.source_hash) for fact in scope.facts)
    if scope.empty_source_locator is None:
        return ()
    _validate_empty_source(scope)
    return ((scope.empty_source_locator, scope.empty_source_hash),)


def reviewed_scope_fingerprint(scope: ScopeManifest) -> str:
    """Bind calculations to reviewed values and policy, not just the raw file."""
    payload = {**scope_payload(scope), 'facts': tuple(asdict(fact) for fact in sorted(scope.facts, key=lambda fact:fact.fact_id))}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,
                                    separators=(',',':'),default=str).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class AssemblyStep:
    number: int
    text: str
    locator: SourceLocator
    review_status: str = "pending"
    conflicts: tuple[str, ...] = ()
    raw_text: str = ''


@dataclass(frozen=True)
class TextFact:
    fact_id: str
    kind: str
    label: str
    raw_value: str
    value: str | None
    locator: SourceLocator
    review_status: str = 'pending'
    conflicts: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()
    sequence_number: int | None = None
    source_hash: str = ''
    extraction_method: str = ''
    snapshot_id: str = ''
    bom_position: str = ''
    linked_step_fact_id: str | None = None
    linked_bom_fact_id: str | None = None
    linked_bom_fingerprint: str | None = None

    def __post_init__(self):
        if self.linked_bom_fact_id is not None and (
                self.kind != 'drawing_note' or self.linked_step_fact_id is not None
                or not isinstance(self.linked_bom_fact_id, str)
                or not self.linked_bom_fact_id.strip() or len(self.linked_bom_fact_id) > 450):
            raise ValueError('Only drawing notes may reference one bounded BOM identity')
        if self.linked_bom_fingerprint is not None and (
                not self.linked_bom_fact_id or not isinstance(self.linked_bom_fingerprint, str)
                or len(self.linked_bom_fingerprint) != 64
                or any(c not in '0123456789abcdef' for c in self.linked_bom_fingerprint)):
            raise ValueError('A bound BOM fingerprint is required')
        if self.kind not in {'material', 'symbol', 'assembly_step', 'drawing_note'}:
            raise ValueError('Unsupported technical field kind')
        if self.review_status not in {'pending', 'approved', 'rejected'}:
            raise ValueError('Invalid field review state')
        if not isinstance(self.conflicts, tuple) or not isinstance(self.candidates, tuple):
            raise ValueError('Technical field collections must be immutable tuples')
        if not isinstance(self.raw_value, str) or len(self.raw_value) > 32000:
            raise ValueError('Bounded raw text required')
        if self.value is not None and (not isinstance(self.value, str) or len(self.value) > 16000):
            raise ValueError('Bounded normalized text required')
        if self.sequence_number is not None and type(self.sequence_number) is not int:
            raise ValueError('Integer step number required')
        if self.linked_step_fact_id is not None and (
                self.kind != 'drawing_note' or not isinstance(self.linked_step_fact_id, str)
                or not self.linked_step_fact_id.strip() or len(self.linked_step_fact_id) > 450):
            raise ValueError('Only drawing notes may reference a bounded step identity')
        if self.kind == 'drawing_note':
            loc = self.locator
            if (type(loc.page) is not int or loc.page < 1 or not isinstance(loc.bbox, tuple)
                    or len(loc.bbox) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in loc.bbox)
                    or loc.bbox[0] >= loc.bbox[2] or loc.bbox[1] >= loc.bbox[3]):
                raise ValueError('Drawing notes require a finite source page region')
