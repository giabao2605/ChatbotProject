"""Deterministic arithmetic over facts that retain document provenance."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
import re
import unicodedata


@dataclass(frozen=True)
class GroundedFact:
    value: Decimal
    unit: str
    doc_id: int
    page: int
    version: int
    source_id: str
    label: str = ""


@dataclass(frozen=True)
class CalculationPlan:
    operation: str
    operands: tuple[GroundedFact, ...]


@dataclass(frozen=True)
class DerivedClaim:
    status: str
    value: Decimal | None = None
    unit: str = ""
    formula: str = ""
    source_ids: tuple[str, ...] = ()
    display_value: str = ""
    approximate: bool = False
    provenance: tuple[GroundedFact, ...] = ()


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char)).replace("đ", "d")


def build_calculation_plan(
    question: str,
    facts: tuple[GroundedFact, ...],
) -> CalculationPlan | None:
    """Build a deterministic plan only from BOM operands named in the question."""
    folded = _fold(question)
    has_word = lambda word: bool(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", folded))
    if "phan tram" in folded or "%" in folded:
        operation = "percent"
    elif "ty le" in folded or "ti le" in folded:
        operation = "ratio"
    elif has_word("tru") or "chenh lech" in folded:
        operation = "subtract"
    elif has_word("nhan") or "*" in folded:
        operation = "multiply"
    elif has_word("chia") or "/" in folded:
        operation = "divide"
    elif has_word("tong") or has_word("total"):
        operation = "sum"
    elif has_word("cong"):
        operation = "add"
    else:
        return None

    available = tuple(facts or ())
    if operation == "sum":
        return CalculationPlan(operation, available)

    matched = []
    for fact in available:
        label = _fold(fact.label).strip()
        match = (
            re.search(rf"(?<![a-z0-9]){re.escape(label)}(?![a-z0-9])", folded)
            if label else None
        )
        if match is not None:
            matched.append((match.start(), fact))
    matched.sort(key=lambda item: item[0])
    return CalculationPlan(operation, tuple(fact for _, fact in matched))


def _display(value: Decimal) -> tuple[str, bool]:
    normalized = value.normalize()
    exact = format(normalized, "f")
    if "." in exact:
        exact = exact.rstrip("0").rstrip(".")
    if len(exact.partition(".")[2]) <= 4:
        return exact or "0", False
    rounded = value.quantize(Decimal("0.0001"))
    return format(rounded, "f").rstrip("0").rstrip("."), True


def _invalid(status: str, facts: tuple[GroundedFact, ...]) -> DerivedClaim:
    return DerivedClaim(
        status=status,
        source_ids=tuple(f.source_id for f in facts),
        provenance=facts,
    )


def derive_claim(plan: CalculationPlan) -> DerivedClaim:
    operation = str(plan.operation or "").strip().lower()
    deduped = []
    seen = set()
    for fact in plan.operands or ():
        if fact.source_id in seen:
            continue
        seen.add(fact.source_id)
        deduped.append(fact)
    facts = tuple(deduped)
    if not facts:
        return _invalid("missing_operand", facts)
    if len({(fact.doc_id, fact.version) for fact in facts}) != 1:
        return _invalid("mixed_version", facts)
    labels = [_fold(fact.label).strip() for fact in facts if str(fact.label or "").strip()]
    if operation != "sum" and len(labels) != len(set(labels)):
        return _invalid("ambiguous_provenance", facts)

    units = {str(fact.unit or "").strip() for fact in facts}
    values = [fact.value for fact in facts]
    try:
        with localcontext() as context:
            context.prec = 28
            if operation in {"sum", "add"}:
                if operation == "add" and len(values) < 2:
                    return _invalid("missing_operand", facts)
                if len(units) != 1:
                    return _invalid("ambiguous_unit", facts)
                value = sum(values, Decimal("0"))
                unit = next(iter(units))
                symbol = " + "
            elif operation == "subtract":
                if len(values) != 2:
                    return _invalid("missing_operand", facts)
                if len(units) != 1:
                    return _invalid("ambiguous_unit", facts)
                value = values[0] - values[1]
                unit = next(iter(units))
                symbol = " - "
            elif operation in {"ratio", "percent"}:
                if len(values) != 2:
                    return _invalid("missing_operand", facts)
                if len(units) != 1:
                    return _invalid("ambiguous_unit", facts)
                if values[1] == 0:
                    return _invalid("division_by_zero", facts)
                value = values[0] / values[1]
                unit = ""
                symbol = " / "
                if operation == "percent":
                    value *= Decimal("100")
                    unit = "%"
            elif operation == "multiply":
                if len(values) != 2:
                    return _invalid("missing_operand", facts)
                non_empty_units = [unit for unit in (fact.unit.strip() for fact in facts) if unit]
                if len(non_empty_units) > 1:
                    return _invalid("ambiguous_unit", facts)
                value = values[0] * values[1]
                unit = non_empty_units[0] if non_empty_units else ""
                symbol = " * "
            elif operation == "divide":
                if len(values) != 2:
                    return _invalid("missing_operand", facts)
                if values[1] == 0:
                    return _invalid("division_by_zero", facts)
                left_unit = facts[0].unit.strip()
                right_unit = facts[1].unit.strip()
                if bool(left_unit) == bool(right_unit):
                    return _invalid("ambiguous_unit", facts)
                value = values[0] / values[1]
                unit = left_unit if not right_unit else f"1/{right_unit}"
                symbol = " / "
            else:
                return _invalid("unsupported_operation", facts)
    except (InvalidOperation, ArithmeticError):
        return _invalid("invalid_number", facts)

    display, approximate = _display(value)
    input_values = symbol.join(_display(item)[0] for item in values)
    if operation == "percent":
        input_values += " * 100"
    suffix = f" {unit}" if unit else ""
    return DerivedClaim(
        status="valid",
        value=value,
        unit=unit,
        formula=f"{input_values} = {display}{suffix}",
        source_ids=tuple(fact.source_id for fact in facts),
        display_value=display,
        approximate=approximate,
        provenance=facts,
    )


def make_calculation_provenance(plan: CalculationPlan, claim: DerivedClaim) -> dict:
    """Serialize only deterministic calculation data safe to attach to evidence."""
    return {
        "operation": str(plan.operation or "").strip().lower(),
        "status": claim.status,
        "formula": claim.formula,
        "source_ids": list(claim.source_ids),
        "sources": [
            {
                "doc_id": fact.doc_id,
                "page": fact.page,
                "version": fact.version,
                "source_id": fact.source_id,
                "value": str(fact.value),
                "unit": fact.unit,
                "label": fact.label,
            }
            for fact in claim.provenance
        ],
        "exact_value": str(claim.value) if claim.value is not None else None,
        "display_value": claim.display_value,
        "unit": claim.unit,
        "approximate": claim.approximate,
    }


def _calculation_documents(documents) -> list[tuple[dict, dict]]:
    records = []
    for document in documents or ():
        metadata = getattr(document, "metadata", {}) or {}
        provenance = metadata.get("calculation_provenance")
        if isinstance(provenance, dict):
            records.append((metadata, provenance))
    return records


def _citation(metadata: dict, *, language: str) -> str | None:
    doc_id = metadata.get("doc_id")
    page = metadata.get("trang_so")
    version = metadata.get("version_no")
    file_name = str(metadata.get("file_goc") or "").strip()
    if doc_id is None or page is None or version is None or not file_name:
        return None
    if str(language or "vi").lower().startswith("en"):
        return f"[Source: {file_name}, Page {page}, Version {version}, SourceID D{doc_id}P{page}]"
    return f"[Nguồn: {file_name}, Trang {page}, Version {version}, SourceID D{doc_id}P{page}]"


def _provenance_citations(provenance: dict, documents, *, language: str) -> tuple[str, ...] | None:
    metadata_by_source = {}
    for document in documents or ():
        metadata = getattr(document, "metadata", {}) or {}
        key = (metadata.get("doc_id"), metadata.get("trang_so"), metadata.get("version_no"))
        metadata_by_source[key] = metadata
    refs = provenance.get("sources") or []
    source_keys = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        key = (ref.get("doc_id"), ref.get("page"), ref.get("version"))
        if key not in source_keys:
            source_keys.append(key)
    citations = []
    for key in source_keys:
        metadata = metadata_by_source.get(key)
        citation = _citation(metadata or {}, language=language)
        if citation is None:
            return None
        citations.append(citation)
    return tuple(citations)


def _resolved_citations(metadata: dict, provenance: dict, documents, *, language: str):
    if provenance.get("sources"):
        citations = _provenance_citations(provenance, documents, language=language)
        return citations or None
    fallback = _citation(metadata, language=language)
    return (fallback,) if fallback else None


def _recompute_claim(provenance: dict) -> DerivedClaim | None:
    refs = provenance.get("sources")
    if not isinstance(refs, list):
        return None
    try:
        facts = tuple(
            GroundedFact(
                value=Decimal(str(ref["value"])),
                unit=str(ref["unit"]),
                doc_id=int(ref["doc_id"]),
                page=int(ref["page"]),
                version=int(ref["version"]),
                source_id=str(ref["source_id"]),
                label=str(ref.get("label") or ""),
            )
            for ref in refs
            if isinstance(ref, dict)
        )
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return None
    if len(facts) != len(refs):
        return None
    operation = str(provenance.get("operation") or "").strip().lower()
    claim = derive_claim(CalculationPlan(operation, facts))
    expected = {
        "status": claim.status,
        "formula": claim.formula,
        "source_ids": list(claim.source_ids),
        "exact_value": str(claim.value) if claim.value is not None else None,
        "display_value": claim.display_value,
        "unit": claim.unit,
        "approximate": claim.approximate,
    }
    if any(provenance.get(key) != value for key, value in expected.items()):
        return None
    return claim


def validate_grounded_calculation_answer(answer: str, documents) -> str | None:
    """Validate formula, unit/result and canonical citation before streaming."""
    text = str(answer or "")
    compact = re.sub(r"\s+", "", text).casefold()
    records = _calculation_documents(documents)
    if not records:
        return None
    for metadata, provenance in records:
        recomputed = _recompute_claim(provenance)
        if recomputed is None:
            return "calculation_provenance"
        citations_vi = _resolved_citations(
            metadata, provenance, documents, language="vi",
        )
        citations_en = _resolved_citations(
            metadata, provenance, documents, language="en",
        )
        if not citations_vi or not citations_en:
            return "citation_provenance"
        has_citation = bool(
            citations_vi and all(citation.casefold() in text.casefold() for citation in citations_vi)
        ) or bool(
            citations_en and all(citation.casefold() in text.casefold() for citation in citations_en)
        )
        status = recomputed.status
        if status == "valid":
            formula = re.sub(r"\s+", "", recomputed.formula).casefold()
            shown = " ".join(
                item for item in (recomputed.display_value, recomputed.unit) if item
            )
            qualifier_vi = "xấp xỉ " if recomputed.approximate else ""
            qualifier_en = "approximately " if recomputed.approximate else ""
            result_vi = f"Kết quả tính có kiểm soát: {qualifier_vi}{shown}.".casefold()
            result_en = f"Controlled calculation result: {qualifier_en}{shown}.".casefold()
            if result_vi not in text.casefold() and result_en not in text.casefold():
                return "result_or_unit"
            if not formula or formula not in compact or not has_citation:
                return "formula_or_citation"
        else:
            folded = _fold(text)
            if not has_citation or not any(
                marker in folded
                for marker in ("tra loi duoc mot phan", "khong the tinh", "partial answer", "cannot calculate")
            ):
                return "partial_or_citation"
    return None


def render_grounded_calculation_answer(documents, *, language: str = "vi") -> str | None:
    """Render deterministic calculation claims; no LLM performs arithmetic."""
    lines = []
    is_english = str(language or "vi").lower().startswith("en")
    for metadata, provenance in _calculation_documents(documents):
        if _recompute_claim(provenance) is None:
            return None
        citations = _resolved_citations(
            metadata, provenance, documents, language=language,
        )
        if not citations:
            return None
        citation = " ".join(citations)
        status = str(provenance.get("status") or "missing_operand")
        if status == "valid":
            value = str(provenance.get("display_value") or "").strip()
            unit = str(provenance.get("unit") or "").strip()
            formula = str(provenance.get("formula") or "").strip()
            approximate = bool(provenance.get("approximate"))
            shown = " ".join(item for item in (value, unit) if item)
            if is_english:
                qualifier = "approximately " if approximate else ""
                lines.append(
                    f"Controlled calculation result: {qualifier}{shown}. Formula: {formula}. {citation}"
                )
            else:
                qualifier = "xấp xỉ " if approximate else ""
                lines.append(
                    f"Kết quả tính có kiểm soát: {qualifier}{shown}. Công thức: {formula}. {citation}"
                )
        elif is_english:
            reason = {
                "missing_operand": "an operand is missing",
                "mixed_version": "operands come from different document versions",
                "ambiguous_unit": "the units are incompatible",
                "division_by_zero": "the divisor is zero",
                "invalid_number": "a numeric value is invalid",
                "unsupported_operation": "the operation is not supported",
                "ambiguous_provenance": "the operand provenance is ambiguous",
            }.get(status, "the provenance is ambiguous")
            lines.append(
                "Partial answer: BOM rows were found, but the calculation cannot be completed "
                f"because {reason}. {citation}"
            )
        else:
            reason = {
                "missing_operand": "thiếu toán hạng",
                "mixed_version": "các toán hạng thuộc phiên bản tài liệu khác nhau",
                "ambiguous_unit": "đơn vị không tương thích",
                "division_by_zero": "mẫu số bằng 0",
                "invalid_number": "giá trị số không hợp lệ",
                "unsupported_operation": "phép toán chưa được hỗ trợ",
                "ambiguous_provenance": "provenance của toán hạng không rõ ràng",
            }.get(status, "provenance không rõ ràng")
            lines.append(
                "Tôi trả lời được một phần: đã tìm thấy các dòng BOM, nhưng không thể tính "
                f"vì {reason}. {citation}"
            )
    answer = "\n\n".join(lines) if lines else None
    if answer is not None and validate_grounded_calculation_answer(answer, documents) is not None:
        return None
    return answer
