"""Exact evaluator for deterministic calculation results and provenance."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from mech_chatbot.domain.number_normalization import normalized_number_values


def _decimal(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _sources(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    sources = []
    for item in value:
        if not isinstance(item, dict):
            continue
        number = _decimal(item.get("value"))
        sources.append({
            "doc_id": item.get("doc_id"),
            "page": item.get("page"),
            "version": item.get("version"),
            "source_id": str(item.get("source_id") or "").strip(),
            "value": str(number) if number is not None else None,
            "unit": str(item.get("unit") or "").strip().casefold(),
        })
    return sources


def _allowed_numbers(expected: dict[str, Any]) -> set[str]:
    values: list[Any] = [
        expected.get("exact_value"),
        expected.get("display_value"),
        expected.get("formula"),
        *(expected.get("allowed_numbers") or []),
    ]
    for source in _sources(expected.get("sources")):
        values.extend((
            source["doc_id"], source["page"], source["version"], source["source_id"],
            source["value"],
        ))
    return normalized_number_values(" ".join(str(value or "") for value in values))


def _normalize_expected_contract(expected: dict[str, Any]) -> dict[str, Any]:
    """Map the pre-v2 controlled-demo BOM shape to the canonical contract.

    New manifests use ``exact_value``/``display_value`` and BOM row source IDs.
    Older controlled-demo manifests used ``value``/``display`` and document
    page IDs in calculation sources; keep those artifacts evaluable without
    weakening the canonical contract for new manifests.
    """
    normalized = dict(expected)
    legacy = "value" in normalized or "display" in normalized
    if not legacy:
        return normalized
    normalized.setdefault("exact_value", normalized.get("value"))
    legacy_display = str(normalized.get("display") or "").strip()
    if legacy_display:
        display_value = legacy_display
        unit_for_display = str(normalized.get("unit") or "").strip()
        if unit_for_display and display_value.casefold().endswith(
            f" {unit_for_display.casefold()}"
        ):
            display_value = display_value[: -(len(unit_for_display) + 1)].rstrip()
        normalized.setdefault("display_value", display_value)
    if normalized.get("status") == "derived":
        normalized["status"] = "valid"
    if normalized.get("operation") == "percentage":
        normalized["operation"] = "percent"
    unit = str(normalized.get("unit") or "").strip()
    if unit == "percent":
        normalized["unit"] = "%"
        unit = "%"
    formula = str(normalized.get("formula") or "").strip()
    if unit and formula and not formula.casefold().endswith(unit.casefold()):
        normalized["formula"] = f"{formula} {unit}"
    sources = []
    for source in normalized.get("sources") or []:
        source_copy = dict(source)
        row_id = str(source_copy.get("row_id") or "").strip()
        if row_id:
            source_copy["source_id"] = row_id
        sources.append(source_copy)
    normalized["sources"] = sources
    return normalized


def evaluate_grounded_calculation(
    expected: dict[str, Any] | None,
    actual_records: list[dict[str, Any]] | None,
    *,
    answer: str,
) -> dict[str, Any]:
    """Compare one observed plan with an exact labeled calculation contract."""
    if not expected:
        return {
            "schema": "grounded-calculation-evaluation-v1",
            "applicable": False,
            "passed": not actual_records,
            "calculation_count": len(actual_records or []),
            "checks": {},
            "unsupported_numbers": [],
        }

    expected = _normalize_expected_contract(expected)
    records = [record for record in (actual_records or []) if isinstance(record, dict)]
    actual = records[0] if len(records) == 1 else {}
    expected_decimal = _decimal(expected.get("exact_value"))
    actual_decimal = _decimal(actual.get("exact_value"))
    exact_decimal = (
        expected_decimal is None and actual.get("exact_value") is None
    ) or (
        expected_decimal is not None
        and actual_decimal is not None
        and expected_decimal == actual_decimal
    )
    unsupported = sorted(
        normalized_number_values(answer) - _allowed_numbers(expected),
        key=lambda value: (len(value), value),
    )
    checks = {
        "single_plan": len(records) == 1,
        "status": actual.get("status") == expected.get("status"),
        "operation": actual.get("operation") == expected.get("operation"),
        "exact_decimal": exact_decimal,
        "display_value": str(actual.get("display_value") or "")
        == str(expected.get("display_value") or ""),
        "formula": str(actual.get("formula") or "")
        == str(expected.get("formula") or ""),
        "unit": str(actual.get("unit") or "") == str(expected.get("unit") or ""),
        "provenance": _sources(actual.get("sources"))
        == _sources(expected.get("sources")),
        "unsupported_numbers_zero": not unsupported,
    }
    return {
        "schema": "grounded-calculation-evaluation-v1",
        "applicable": True,
        "passed": all(checks.values()),
        "calculation_count": len(records),
        "checks": checks,
        "unsupported_numbers": unsupported,
    }
