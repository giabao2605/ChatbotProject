"""Generate the human-readable decomposition-eval-v1 labels."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.decomposition_eval.constants import (
    BOM_DOCUMENT,
    BOM_ROWS,
    DEFAULT_OUTPUT,
    MATH_QUERY_INTERACTION_CASE_IDS,
    QUERY_ONLY_TERMINAL_BRANCH_OUTCOMES,
)


DOCS = {
    "numbers": ("crag_eval_numbers_v12.md", 12),
    "alias": ("crag_eval_alias_v1.md", 1),
    "bom": ("crag_eval_bom_v1.md", 1),
    "no_cost": ("crag_eval_no_cost_v1.md", 1),
    "restricted": ("crag_eval_restricted_v1.md", 1),
}

def _identity():
    return {
        "user_department": "Technical", "user_roles": ["viewer"],
        "allowed_departments": ["Technical"], "allowed_sites": ["CRAG-EVAL-HQ"],
        "max_security_level": "internal",
    }


def _citation(key):
    document, version = DOCS[key]
    return {
        "document": document, "doc_id": f"$DOC:{document}", "page": 1,
        "version": version, "source_id": f"$PAGE:{document}:1",
    }


def _claim(claim_id, terms, key):
    document, _ = DOCS[key]
    return {
        "id": claim_id, "required_terms": terms,
        "allowed_source_ids": [f"$PAGE:{document}:1"],
    }


def _branch(position, outcome, *keys):
    citations = [_citation(key) for key in keys]
    return {
        "branch_id": f"branch-{position}", "expected_outcome": outcome,
        "expected_citations": citations,
        "expected_rendered_citations": [dict(citation) for citation in citations],
    }


def _bom_calculation():
    sources = [{
        "document": BOM_DOCUMENT, "doc_id": f"$DOC:{BOM_DOCUMENT}",
        "page": 1, "version": 1, "source_id": f"$ROW:{row['row_key']}",
        "source_row_key": row["row_key"], "value": row["value"],
        "unit": row["unit"],
    } for row in BOM_ROWS]
    return {
        "operation": "sum", "status": "valid",
        "formula": "2 + 3 = 5", "unit": "",
        "exact_value": "5", "display_value": "5",
        "allowed_numbers": ["2", "3"], "sources": sources,
    }


def _case(case_id, question, group, outcome, claims, citations, branches, *, primary="numbers", **extra):
    document, version = DOCS[primary]
    sources = list(dict.fromkeys(citation["document"] for citation in citations))
    return {
        "manifest_schema": "rag-eval-manifest-v2", "id": case_id,
        "question": question, "evaluation_group": group,
        "expected_outcome": outcome, "expected_claims": claims,
        "expected_citations": citations, "expected_branches": branches,
        "expected_document": document, "expected_page": 1,
        "expected_version": version, "expected_sources": sources or [document],
        "expected_department": "Technical", "expected_site": "CRAG-EVAL-HQ",
        "expected_security_level": "internal", **_identity(), **extra,
    }


def _labeled_cases():
    number = _claim("number", ["1,500"], "numbers")
    alias = _claim("alias-cycle", ["90 ngày"], "alias")
    version = _claim(
        "version", ["phiên bản", "CRAG-EVAL-NUM-001", "12"], "numbers"
    )
    bom = _claim("bom-total", ["5"], "bom")
    install = _claim("install", ["quy trình", "lắp", "CRAG-EVAL-PART-C"], "no_cost")
    alias_version = _claim(
        "alias-version",
        ["phiên bản", "CRAG-EVAL-ALIAS-001", "1"],
        "alias",
    )
    return [
        _case("decomp-simple-factual", "Giá trị định mức CRAG-EVAL-NUM-001 là bao nhiêu?", "simple", "full_answer", [number], [_citation("numbers")], []),
        _case("decomp-simple-alias", "Mắt cú xanh kiểm tra theo chu kỳ nào?", "simple", "full_answer", [alias], [_citation("alias")], [], primary="alias"),
        _case("decomp-simple-install", "Quy trình lắp CRAG-EVAL-PART-C là gì?", "simple", "full_answer", [install], [_citation("no_cost")], [], primary="no_cost"),
        _case("decomp-two-intents", "Giá trị định mức CRAG-EVAL-NUM-001 là bao nhiêu và mắt cú xanh kiểm tra theo chu kỳ nào?", "complex", "full_answer", [number, alias], [_citation("numbers"), _citation("alias")], [_branch(1, "full_answer", "numbers"), _branch(2, "full_answer", "alias")]),
        _case("decomp-three-intents", "Cho biết giá trị CRAG-EVAL-NUM-001, chu kỳ mắt cú xanh và quy trình lắp CRAG-EVAL-PART-C?", "complex", "full_answer", [number, alias, install], [_citation("numbers"), _citation("alias"), _citation("no_cost")], [_branch(1, "full_answer", "numbers"), _branch(2, "full_answer", "alias"), _branch(3, "full_answer", "no_cost")]),
        _case("decomp-sql-bom-doc", "Tổng BOM CRAG-EVAL-BOM-001 là bao nhiêu và phiên bản hiện hành của CRAG-EVAL-NUM-001 là gì?", "complex", "full_answer", [bom, version], [_citation("bom"), _citation("numbers")], [_branch(1, "full_answer", "bom"), _branch(2, "full_answer", "numbers")], primary="bom", requires_grounded_math=True, expected_calculation=_bom_calculation()),
        _case("decomp-version-candidate", "So sánh phiên bản hiện hành của CRAG-EVAL-NUM-001 và CRAG-EVAL-ALIAS-001?", "complex", "full_answer", [version, alias_version], [_citation("numbers"), _citation("alias")], [_branch(1, "full_answer", "numbers"), _branch(2, "full_answer", "alias")]),
        _case("decomp-sufficient-missing", "Giá trị CRAG-EVAL-NUM-001 là bao nhiêu và chi phí CRAG-EVAL-PART-C là bao nhiêu?", "complex", "partial_answer", [number], [_citation("numbers")], [_branch(1, "full_answer", "numbers"), _branch(2, "insufficient_evidence")]),
        _case("decomp-access-denied", "Giá trị CRAG-EVAL-NUM-001 và mã cấu hình CRAG-EVAL-SECRET-001 là gì?", "complex", "partial_answer", [number], [_citation("numbers")], [_branch(1, "full_answer", "numbers"), _branch(2, "access_denied")], forbidden_sources=[DOCS["restricted"][0]], preflight_documents=[{"document": DOCS["restricted"][0], "version": 1, "site": "CRAG-EVAL-REMOTE", "security_level": "confidential"}]),
        _case("decomp-code-boundary", "Đối chiếu CRAG-EVAL-NUM-001 và CRAG-EVAL-ALIAS-001: nêu định mức và chu kỳ kiểm tra.", "complex", "full_answer", [number, alias], [_citation("numbers"), _citation("alias")], [_branch(1, "full_answer", "numbers"), _branch(2, "full_answer", "alias")], allowed_planner_codes=["CRAG-EVAL-NUM-001", "CRAG-EVAL-ALIAS-001"]),
        _case("decomp-bom-alias", "Tổng BOM CRAG-EVAL-BOM-001 là bao nhiêu và mắt cú xanh kiểm tra theo chu kỳ nào?", "complex", "full_answer", [bom, alias], [_citation("bom"), _citation("alias")], [_branch(1, "full_answer", "bom"), _branch(2, "full_answer", "alias")], primary="bom", requires_grounded_math=True, expected_calculation=_bom_calculation()),
        _case("decomp-install-version", "Nêu quy trình lắp CRAG-EVAL-PART-C và phiên bản hiện hành của CRAG-EVAL-ALIAS-001.", "complex", "full_answer", [install, alias_version], [_citation("no_cost"), _citation("alias")], [_branch(1, "full_answer", "no_cost"), _branch(2, "full_answer", "alias")], primary="no_cost"),
        _case("decomp-three-source-compare", "Đối chiếu định mức CRAG-EVAL-NUM-001, tổng BOM CRAG-EVAL-BOM-001 và quy trình lắp CRAG-EVAL-PART-C.", "complex", "full_answer", [number, bom, install], [_citation("numbers"), _citation("bom"), _citation("no_cost")], [_branch(1, "full_answer", "numbers"), _branch(2, "full_answer", "bom"), _branch(3, "full_answer", "no_cost")], requires_grounded_math=True, expected_calculation=_bom_calculation()),
    ]


def _source_preflight_documents(case):
    sources = [
        *(case.get("preflight_documents") or []),
        *(case.get("expected_citations") or []),
        *(
            citation
            for branch in case.get("expected_branches") or []
            for citation in branch.get("expected_citations") or []
        ),
    ]
    by_document = {}
    for source in sources:
        document = source.get("document")
        if document and document not in by_document:
            by_document[document] = {
                key: source[key]
                for key in ("document", "version", "base_code")
                if source.get(key) is not None
            }
    return list(by_document.values())


def _query_only_case(case):
    outcomes = QUERY_ONLY_TERMINAL_BRANCH_OUTCOMES.get(case["id"])
    if outcomes is None:
        return case
    base = {
        key: value
        for key, value in case.items()
        if key not in {"requires_grounded_math", "expected_calculation"}
    }
    return {
        **base,
        "expected_outcome": "insufficient_evidence",
        "expected_claims": [],
        "expected_citations": [],
        "expected_terminal_claim_count": 0,
        "expected_terminal_rendered_source_count": 0,
        "preflight_documents": _source_preflight_documents(case),
        "expected_branches": [
            {
                **branch,
                "expected_outcome": outcome,
                "expected_citations": (
                    branch["expected_citations"]
                    if outcome == "full_answer"
                    else []
                ),
                "expected_rendered_citations": [],
            }
            for branch, outcome in zip(
                case["expected_branches"], outcomes, strict=True
            )
        ],
    }


def cases():
    return [
        {**_query_only_case(case), "evaluation_scope": "query_only"}
        for case in _labeled_cases()
    ]


def interaction_cases():
    approved = set(MATH_QUERY_INTERACTION_CASE_IDS)
    return [
        {**case, "evaluation_scope": "math_query_interaction"}
        for case in _labeled_cases()
        if case["id"] in approved
    ]


def generate_manifest(output: Path = DEFAULT_OUTPUT):
    output.mkdir(parents=True, exist_ok=True)
    query_manifest = output / "eval_manifest.jsonl"
    interaction_manifest = output / "math_query_interaction_manifest.jsonl"
    query_values = cases()
    interaction_values = interaction_cases()
    query_manifest.write_text(
        "".join(
            json.dumps(case, ensure_ascii=False) + "\n"
            for case in query_values
        ),
        encoding="utf-8",
    )
    interaction_manifest.write_text(
        "".join(
            json.dumps(case, ensure_ascii=False) + "\n"
            for case in interaction_values
        ),
        encoding="utf-8",
    )
    query_sha = hashlib.sha256(query_manifest.read_bytes()).hexdigest()
    interaction_sha = hashlib.sha256(
        interaction_manifest.read_bytes()
    ).hexdigest()
    (output / "README.md").write_text(
        "# decomposition-eval-v1\n\n"
        "Manifest dùng fixture staging `crag-eval-v1`; DocID và SourceID "
        "được preflight giải quyết lúc chạy.\n\n"
        "- `eval_manifest.jsonl`: Query-only, 13 case gồm 10 complex và "
        "3 simple; Grounded Math phải OFF. Các case high-risk terminal khóa "
        "claim và citation render bằng `0`.\n"
        "- `math_query_interaction_manifest.jsonl`: 3 case Math+Query giữ "
        "nguyên expectation `full_answer` và phép `sum`; không dùng làm "
        "formal evidence cho Query-only.\n\n"
        "Citation của evidence đủ điều kiện phục vụ được so khớp theo tập "
        "canonical source identity duy nhất: nhiều chunk cùng một nguồn/trang "
        "được gộp, nhưng bất kỳ source identity khác expectation đều làm gate "
        "fail. Số document raw retrieval vẫn được giữ riêng trong telemetry.\n\n"
        f"Query-only SHA-256: `{query_sha}`\n\n"
        f"Math+Query interaction SHA-256: `{interaction_sha}`\n",
        encoding="utf-8",
    )
    return {
        "schema": "decomposition-eval-manifest-v2",
        "query_only": {
            "cases": len(query_values),
            "manifest": str(query_manifest),
            "sha256": query_sha,
        },
        "math_query_interaction": {
            "cases": len(interaction_values),
            "manifest": str(interaction_manifest),
            "sha256": interaction_sha,
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(generate_manifest(args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
