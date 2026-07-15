"""Generate the human-readable decomposition-eval-v1 labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.decomposition_eval.constants import DEFAULT_OUTPUT


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
    return {
        "branch_id": f"branch-{position}", "expected_outcome": outcome,
        "expected_citations": [_citation(key) for key in keys],
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


def cases():
    number = _claim("number", ["1,500"], "numbers")
    alias = _claim("alias-cycle", ["90 ngày"], "alias")
    version = _claim("version", ["phiên bản", "12"], "numbers")
    bom = _claim("bom-total", ["5", "cái"], "bom")
    return [
        _case("decomp-simple-factual", "Giá trị định mức CRAG-EVAL-NUM-001 là bao nhiêu?", "simple", "full_answer", [number], [_citation("numbers")], []),
        _case("decomp-two-intents", "Giá trị định mức CRAG-EVAL-NUM-001 là bao nhiêu và mắt cú xanh kiểm tra theo chu kỳ nào?", "complex", "full_answer", [number, alias], [_citation("numbers"), _citation("alias")], [_branch(1, "full_answer", "numbers"), _branch(2, "full_answer", "alias")]),
        _case("decomp-three-intents", "Cho biết giá trị CRAG-EVAL-NUM-001, chu kỳ mắt cú xanh và quy trình lắp CRAG-EVAL-PART-C?", "complex", "full_answer", [number, alias, _claim("install", ["quy trình", "lắp", "CRAG-EVAL-PART-C"], "no_cost")], [_citation("numbers"), _citation("alias"), _citation("no_cost")], [_branch(1, "full_answer", "numbers"), _branch(2, "full_answer", "alias"), _branch(3, "full_answer", "no_cost")]),
        _case("decomp-sql-bom-doc", "Tổng BOM CRAG-EVAL-BOM-001 là bao nhiêu và phiên bản hiện hành của CRAG-EVAL-NUM-001 là gì?", "complex", "full_answer", [bom, version], [_citation("bom"), _citation("numbers")], [_branch(1, "full_answer", "bom"), _branch(2, "full_answer", "numbers")], primary="bom", requires_grounded_math=True),
        _case("decomp-version-candidate", "So sánh phiên bản hiện hành của CRAG-EVAL-NUM-001 và CRAG-EVAL-ALIAS-001?", "complex", "full_answer", [version, _claim("alias-version", ["phiên bản", "1"], "alias")], [_citation("numbers"), _citation("alias")], [_branch(1, "full_answer", "numbers"), _branch(2, "full_answer", "alias")]),
        _case("decomp-sufficient-missing", "Giá trị CRAG-EVAL-NUM-001 là bao nhiêu và chi phí CRAG-EVAL-PART-C là bao nhiêu?", "complex", "partial_answer", [number], [_citation("numbers")], [_branch(1, "full_answer", "numbers"), _branch(2, "insufficient_evidence")]),
        _case("decomp-access-denied", "Giá trị CRAG-EVAL-NUM-001 và mã cấu hình CRAG-EVAL-SECRET-001 là gì?", "complex", "partial_answer", [number], [_citation("numbers")], [_branch(1, "full_answer", "numbers"), _branch(2, "access_denied")], forbidden_sources=[DOCS["restricted"][0]], preflight_documents=[{"document": DOCS["restricted"][0], "version": 1, "site": "CRAG-EVAL-REMOTE", "security_level": "confidential"}]),
        _case("decomp-code-boundary", "Đối chiếu CRAG-EVAL-NUM-001 và CRAG-EVAL-ALIAS-001: nêu định mức và chu kỳ kiểm tra.", "complex", "full_answer", [number, alias], [_citation("numbers"), _citation("alias")], [_branch(1, "full_answer", "numbers"), _branch(2, "full_answer", "alias")], allowed_planner_codes=["CRAG-EVAL-NUM-001", "CRAG-EVAL-ALIAS-001"]),
    ]


def generate_manifest(output: Path = DEFAULT_OUTPUT):
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / "eval_manifest.jsonl"
    values = cases()
    manifest.write_text("".join(json.dumps(case, ensure_ascii=False) + "\n" for case in values), encoding="utf-8")
    (output / "README.md").write_text(
        "# decomposition-eval-v1\n\nManifest dùng fixture staging `crag-eval-v1`; DocID và SourceID được preflight giải quyết lúc chạy.\n",
        encoding="utf-8",
    )
    return {"schema": "decomposition-eval-manifest-v1", "cases": len(values), "manifest": str(manifest)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(generate_manifest(args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
