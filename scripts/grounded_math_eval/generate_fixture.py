"""Generate deterministic documents, BOM rows and labels for grounded math."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.grounded_math_eval.constants import (
    DEFAULT_OUTPUT, FIXTURE_BATCH, FIXTURE_CODE_PREFIX, FIXTURE_DEPARTMENT,
    FIXTURE_SITE,
)


DOCUMENTS = (
    {
        "key": "bom_v12", "filename": "grounded_math_bom_v12.md",
        "doc_number": "GROUND-MATH-EVAL-BOM-001", "title": "BOM Grounded Math",
        "version": 12,
        "rows": (
            {"row_key": "row-a", "source_table_index": 1, "part": "GROUND-MATH-EVAL-PART-A-100", "value": "2", "unit": "kg"},
            {"row_key": "row-b", "source_table_index": 2, "part": "GROUND-MATH-EVAL-PART-B-200", "value": "4", "unit": "kg"},
            {"row_key": "row-factor", "source_table_index": 3, "part": "GROUND-MATH-EVAL-FACTOR-X-300", "value": "2", "unit": ""},
            {"row_key": "row-zero", "source_table_index": 4, "part": "GROUND-MATH-EVAL-ZERO-X-400", "value": "0", "unit": ""},
            {"row_key": "row-metre", "source_table_index": 5, "part": "GROUND-MATH-EVAL-PART-M-500", "value": "3", "unit": "m"},
        ),
    },
    {
        "key": "bom_v11", "filename": "grounded_math_bom_v11.md",
        "doc_number": "GROUND-MATH-EVAL-BOM-001-V11", "title": "BOM Grounded Math cũ",
        "version": 11,
        "rows": (
            {"row_key": "row-old-a", "source_table_index": 1, "part": "GROUND-MATH-EVAL-OLD-A-600", "value": "1", "unit": "kg"},
        ),
    },
    {
        "key": "bom_other_v12", "filename": "grounded_math_other_v12.md",
        "doc_number": "GROUND-MATH-EVAL-BOM-002", "title": "BOM provenance khác",
        "version": 12,
        "rows": (
            {"row_key": "row-other", "source_table_index": 1, "part": "GROUND-MATH-EVAL-OTHER-A-700", "value": "1", "unit": "kg"},
        ),
    },
)


def _identity() -> dict:
    return {
        "user_department": FIXTURE_DEPARTMENT, "user_roles": ["viewer"],
        "allowed_departments": [FIXTURE_DEPARTMENT], "allowed_sites": [FIXTURE_SITE],
        "max_security_level": "internal",
    }


def _source(document_key: str, row_key: str, *, value: str, unit: str) -> dict:
    document = next(item for item in DOCUMENTS if item["key"] == document_key)
    return {
        "document": document["filename"], "doc_id": f"$DOC:{document_key}",
        "page": 1, "version": document["version"], "source_id": f"$ROW:{row_key}",
        "source_row_key": row_key, "value": value, "unit": unit,
    }


def _calculation(operation, status, formula, unit, sources, **values):
    return {
        "operation": operation, "status": status, "formula": formula, "unit": unit,
        "sources": sources, **values,
    }


def _case(case_id, question, expected, *, outcome="full_answer"):
    primary = DOCUMENTS[0]
    citations = []
    seen_documents = set()
    for source in expected["sources"]:
        document = source["document"]
        if document in seen_documents:
            continue
        seen_documents.add(document)
        key = next(item["key"] for item in DOCUMENTS if item["filename"] == document)
        citations.append({
            "document": document, "doc_id": f"$DOC:{key}", "page": source["page"],
            "version": source["version"], "source_id": f"$PAGE:{key}",
        })
    return {
        "manifest_schema": "rag-eval-manifest-v2", "id": case_id,
        "question": question, "expected_outcome": outcome,
        "evaluation_group": "grounded_math", "expected_claims": [],
        "expected_citations": citations,
        "expected_calculation": expected,
        "expected_document": primary["filename"], "expected_page": 1,
        "expected_version": primary["version"], "expected_sources": [primary["filename"]],
        "expected_department": FIXTURE_DEPARTMENT, "expected_site": FIXTURE_SITE,
        "expected_security_level": "internal", **_identity(),
    }


def _cases() -> list[dict]:
    a = _source("bom_v12", "row-a", value="2", unit="kg")
    b = _source("bom_v12", "row-b", value="4", unit="kg")
    factor = _source("bom_v12", "row-factor", value="2", unit="")
    zero = _source("bom_v12", "row-zero", value="0", unit="")
    metre = _source("bom_v12", "row-metre", value="3", unit="m")
    old = _source("bom_v11", "row-old-a", value="1", unit="kg")
    other = _source("bom_other_v12", "row-other", value="1", unit="kg")
    return [
        _case("math-bom-total", "Tổng BOM của GROUND-MATH-EVAL-PART-A-100 và GROUND-MATH-EVAL-PART-B-200?", _calculation("sum", "valid", "2 + 4 = 6 kg", "kg", [a, b], exact_value="6", display_value="6", allowed_numbers=["2", "4"])),
        _case("math-dedupe-source", "Tổng GROUND-MATH-EVAL-PART-A-100, GROUND-MATH-EVAL-PART-A-100 và GROUND-MATH-EVAL-PART-B-200", _calculation("sum", "valid", "2 + 4 = 6 kg", "kg", [a, b], exact_value="6", display_value="6", allowed_numbers=["2", "4"])),
        _case("math-add", "Cộng GROUND-MATH-EVAL-PART-A-100 và GROUND-MATH-EVAL-PART-B-200", _calculation("add", "valid", "2 + 4 = 6 kg", "kg", [a, b], exact_value="6", display_value="6", allowed_numbers=["2", "4"])),
        _case("math-subtract", "Lấy GROUND-MATH-EVAL-PART-B-200 trừ GROUND-MATH-EVAL-PART-A-100", _calculation("subtract", "valid", "4 - 2 = 2 kg", "kg", [b, a], exact_value="2", display_value="2", allowed_numbers=["4", "2"])),
        _case("math-ratio", "Tỷ lệ GROUND-MATH-EVAL-PART-A-100 so với GROUND-MATH-EVAL-PART-B-200", _calculation("ratio", "valid", "2 / 4 = 0.5", "", [a, b], exact_value="0.5", display_value="0.5", allowed_numbers=["2", "4"])),
        _case("math-percent", "GROUND-MATH-EVAL-PART-A-100 chiếm bao nhiêu phần trăm GROUND-MATH-EVAL-PART-B-200?", _calculation("percent", "valid", "2 / 4 * 100 = 50 %", "%", [a, b], exact_value="50", display_value="50", allowed_numbers=["2", "4", "100"])),
        _case("math-multiply", "Lấy GROUND-MATH-EVAL-PART-A-100 nhân GROUND-MATH-EVAL-FACTOR-X-300", _calculation("multiply", "valid", "2 * 2 = 4 kg", "kg", [a, factor], exact_value="4", display_value="4", allowed_numbers=["2"])),
        _case("math-divide", "Lấy GROUND-MATH-EVAL-PART-B-200 chia GROUND-MATH-EVAL-FACTOR-X-300", _calculation("divide", "valid", "4 / 2 = 2 kg", "kg", [b, factor], exact_value="2", display_value="2", allowed_numbers=["4", "2"])),
        _case("math-missing", "Lấy GROUND-MATH-EVAL-PART-A-100 chia GROUND-MATH-EVAL-MISSING-999", _calculation("divide", "missing_operand", "", "", [a]), outcome="partial_answer"),
        _case("math-unsupported-number", "Cộng GROUND-MATH-EVAL-PART-A-100 và GROUND-MATH-EVAL-MISSING-999", _calculation("add", "missing_operand", "", "", [a]), outcome="partial_answer"),
        _case("math-zero", "Lấy GROUND-MATH-EVAL-PART-A-100 chia GROUND-MATH-EVAL-ZERO-X-400", _calculation("divide", "division_by_zero", "", "", [a, zero]), outcome="partial_answer"),
        _case("math-unit-mismatch", "Cộng GROUND-MATH-EVAL-PART-A-100 và GROUND-MATH-EVAL-PART-M-500", _calculation("add", "ambiguous_unit", "", "", [a, metre]), outcome="partial_answer"),
        _case("math-mixed-version", "Cộng GROUND-MATH-EVAL-PART-A-100 và GROUND-MATH-EVAL-OLD-A-600", _calculation("add", "mixed_version", "", "", [a, old]), outcome="partial_answer"),
        _case("math-ambiguous-provenance", "Cộng GROUND-MATH-EVAL-PART-A-100 và GROUND-MATH-EVAL-OTHER-A-700", _calculation("add", "ambiguous_provenance", "", "", [a, other]), outcome="partial_answer"),
        _case("math-unsupported-conversion", "Quy đổi GROUND-MATH-EVAL-PART-A-100 sang mét", _calculation("unsupported_operation", "unsupported_operation", "", "", [a]), outcome="partial_answer"),
    ]


def generate_fixture(output: Path = DEFAULT_OUTPUT) -> dict:
    output = Path(output)
    corpus = output / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    records = []
    for document in DOCUMENTS:
        rows = "\n".join(
            f"| {row['part']} | {row['value']} | {row['unit'] or 'dimensionless'} | {row['row_key']} |"
            for row in document["rows"]
        )
        path = corpus / document["filename"]
        path.write_text(
            f"# {document['title']}\n\nMã tài liệu: {document['doc_number']}\n\n"
            "| Mã hàng | Giá trị | Đơn vị | Source row |\n|---|---:|---|---|\n" + rows + "\n",
            encoding="utf-8",
        )
        records.append({
            **document, "rows": list(document["rows"]), "batch_id": FIXTURE_BATCH,
            "department": FIXTURE_DEPARTMENT, "site": FIXTURE_SITE,
            "security_level": "internal", "path": str(path.relative_to(output)).replace("\\", "/"),
            "effective_date": "2026-01-01", "expiry_date": "2030-01-01",
        })
    (output / "corpus_manifest.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8"
    )
    cases = _cases()
    (output / "eval_manifest.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in cases), encoding="utf-8"
    )
    summary = {
        "batch_id": FIXTURE_BATCH, "part_code_prefix": FIXTURE_CODE_PREFIX,
        "documents": len(records), "bom_rows": sum(len(row["rows"]) for row in records),
        "cases": len(cases),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(generate_fixture(args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
