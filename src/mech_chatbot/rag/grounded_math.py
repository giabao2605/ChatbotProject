"""Deterministic arithmetic over facts that retain document provenance."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext


@dataclass(frozen=True)
class GroundedFact:
    value: Decimal
    unit: str
    doc_id: int
    page: int
    version: int
    source_id: str


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
    return DerivedClaim(status=status, source_ids=tuple(f.source_id for f in facts))


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

    units = {str(fact.unit or "").strip() for fact in facts}
    values = [fact.value for fact in facts]
    try:
        with localcontext() as context:
            context.prec = 28
            if operation in {"sum", "add"}:
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
    suffix = f" {unit}" if unit else ""
    return DerivedClaim(
        status="valid",
        value=value,
        unit=unit,
        formula=f"{input_values} = {display}{suffix}",
        source_ids=tuple(fact.source_id for fact in facts),
        display_value=display,
        approximate=approximate,
    )
