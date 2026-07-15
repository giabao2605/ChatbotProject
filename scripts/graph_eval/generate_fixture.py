"""Generate isolated documents and labels for governed GraphRAG evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.graph_eval.constants import (
    DEFAULT_OUTPUT, FIXTURE_BATCH, FIXTURE_REMOTE_SITE, FIXTURE_SITE,
)


DOCUMENTS = (
    {"key": "assembly_v1", "filename": "graph_eval_assembly_v1.md", "doc_number": "GRAPH-EVAL-ASM-001", "version": 1, "department": "Technical", "site": FIXTURE_SITE, "security_level": "internal", "state": "superseded", "title": "Assembly graph v1", "body": "Phiên bản cũ của cụm GRAPH-EVAL-ASM-001."},
    {"key": "assembly_v2", "filename": "graph_eval_assembly_v2.md", "doc_number": "GRAPH-EVAL-ASM-001", "version": 2, "department": "Technical", "site": FIXTURE_SITE, "security_level": "internal", "state": "current", "title": "Assembly graph v2", "body": "Cụm GRAPH-EVAL-ASM-001 dùng danh mục vật tư có provenance SQL."},
    {"key": "production", "filename": "graph_eval_production_v1.md", "doc_number": "GRAPH-EVAL-PROD-001", "version": 1, "department": "Production", "site": FIXTURE_SITE, "security_level": "internal", "state": "current", "title": "Production graph", "body": "Chu kỳ sản xuất chuẩn là 55 giây."},
    {"key": "maintenance", "filename": "graph_eval_maintenance_v1.md", "doc_number": "GRAPH-EVAL-MAINT-001", "version": 1, "department": "Maintenance", "site": FIXTURE_SITE, "security_level": "internal", "state": "current", "title": "Maintenance graph", "body": "Chu kỳ kiểm tra bảo trì là 500 giờ."},
    {"key": "site_restricted", "filename": "graph_eval_site_restricted_v1.md", "doc_number": "GRAPH-EVAL-SITE-001", "version": 1, "department": "Technical", "site": FIXTURE_REMOTE_SITE, "security_level": "internal", "state": "current", "title": "Site restricted graph", "body": "Mã site hạn chế là GRAPH-EVAL-SITE-RED."},
    {"key": "security_restricted", "filename": "graph_eval_security_restricted_v1.md", "doc_number": "GRAPH-EVAL-SEC-001", "version": 1, "department": "Technical", "site": FIXTURE_SITE, "security_level": "confidential", "state": "current", "title": "Security restricted graph", "body": "Mã confidential là GRAPH-EVAL-SEC-RED."},
    {"key": "department_restricted", "filename": "graph_eval_department_restricted_v1.md", "doc_number": "GRAPH-EVAL-DEPT-001", "version": 1, "department": "HR", "site": FIXTURE_SITE, "security_level": "internal", "state": "current", "title": "Department restricted graph", "body": "Mã phòng ban hạn chế là GRAPH-EVAL-DEPT-RED."},
    {"key": "draft", "filename": "graph_eval_draft_v1.md", "doc_number": "GRAPH-EVAL-DRAFT-001", "version": 1, "department": "Technical", "site": FIXTURE_SITE, "security_level": "internal", "state": "draft", "title": "Draft graph", "body": "Thông tin nháp không được serving."},
    {"key": "unpublished", "filename": "graph_eval_unpublished_v1.md", "doc_number": "GRAPH-EVAL-UNPUBLISHED-001", "version": 1, "department": "Technical", "site": FIXTURE_SITE, "security_level": "internal", "state": "unpublished", "title": "Unpublished graph", "body": "Thông tin chưa publish không được serving."},
    {"key": "expired", "filename": "graph_eval_expired_v1.md", "doc_number": "GRAPH-EVAL-EXPIRED-001", "version": 1, "department": "Technical", "site": FIXTURE_SITE, "security_level": "internal", "state": "expired", "title": "Expired graph", "body": "Thông tin đã hết hiệu lực không được serving."},
)

BOM_ROWS = (
    {"row_key": "graph-row-a", "part": "GRAPH-EVAL-PART-A", "name": "Graph Part A", "value": "2", "unit": "cái", "material": "steel", "source_table_index": 1},
    {"row_key": "graph-row-b", "part": "GRAPH-EVAL-PART-B", "name": "Graph Part B", "value": "3", "unit": "cái", "material": "aluminum", "source_table_index": 2},
    {"row_key": "graph-row-c", "part": "GRAPH-EVAL-PART-C", "name": "Graph Part C", "value": "1", "unit": "cái", "material": "copper", "source_table_index": 3},
    {"row_key": "graph-row-d", "part": "GRAPH-EVAL-PART-D", "name": "Graph Part D", "value": "4", "unit": "cái", "material": "brass", "source_table_index": 4},
    {"row_key": "graph-row-e", "part": "GRAPH-EVAL-PART-E", "name": "Graph Part E", "value": "2", "unit": "cái", "material": "rubber", "source_table_index": 5},
    {"row_key": "graph-row-f", "part": "GRAPH-EVAL-PART-F", "name": "Graph Part F", "value": "6", "unit": "cái", "material": "nylon", "source_table_index": 6},
)


def _identity(*, site=FIXTURE_SITE, clearance="internal"):
    return {
        "user_department": "Technical", "user_roles": ["viewer"],
        "allowed_departments": ["Technical", "Production", "Maintenance"],
        "allowed_sites": [site], "max_security_level": clearance,
    }


def _citation(key):
    document = next(item for item in DOCUMENTS if item["key"] == key)
    return {
        "document": document["filename"], "doc_id": f"$DOC:{key}", "page": 1,
        "version": document["version"], "source_id": f"$PAGE:{key}",
    }


def _claim(claim_id, terms, key):
    return {"id": claim_id, "required_terms": terms, "allowed_source_ids": [f"$PAGE:{key}"]}


def _case(case_id, question, outcome, key, relation, claims, *, scenario="relational", **extra):
    document = next(item for item in DOCUMENTS if item["key"] == key)
    citations = [_citation(key)] if outcome in {"full_answer", "partial_answer"} else []
    return {
        "manifest_schema": "rag-eval-manifest-v2", "id": case_id,
        "question": question, "evaluation_group": scenario,
        "expected_outcome": outcome, "expected_claims": claims,
        "expected_citations": citations, "expected_relation": relation,
        "expected_document": document["filename"], "expected_page": 1,
        "expected_version": document["version"],
        "expected_sources": [document["filename"]],
        "expected_department": document["department"], "expected_site": document["site"],
        "expected_security_level": document["security_level"],
        **_identity(), **extra,
    }


def cases():
    return [
        _case("graph-family-version", "GRAPH-EVAL-ASM-001 có phiên bản hiện hành nào?", "full_answer", "assembly_v2", {"source_key": "$FAMILY:assembly", "relation_type": "HAS_VERSION", "target_key": "$DOC:assembly_v2"}, [_claim("version", ["phiên bản", "2"], "assembly_v2")]),
        _case("graph-supersedes", "GRAPH-EVAL-ASM-001 phiên bản 2 thay thế tài liệu nào?", "full_answer", "assembly_v2", {"source_key": "$DOC:assembly_v2", "relation_type": "SUPERSEDES", "target_key": "$DOC:assembly_v1"}, [_claim("supersedes", ["phiên bản", "1"], "assembly_v2")]),
        _case("graph-contains-part", "GRAPH-EVAL-ASM-001 chứa bộ phận GRAPH-EVAL-PART-A nào?", "full_answer", "assembly_v2", {"source_key": "$DOC:assembly_v2", "relation_type": "CONTAINS_PART", "target_key": "part:graph-eval-part-a"}, [_claim("contains", ["GRAPH-EVAL-PART-A"], "assembly_v2")]),
        _case("graph-uses-material", "GRAPH-EVAL-PART-A sử dụng vật liệu gì?", "full_answer", "assembly_v2", {"source_key": "part:graph-eval-part-a", "relation_type": "USES_MATERIAL", "target_key": "material:steel"}, [_claim("material", ["steel"], "assembly_v2")]),
        _case("graph-production-page", "Tài liệu GRAPH-EVAL-PROD-001 liên kết tới trang nguồn nào và chu kỳ là bao nhiêu?", "full_answer", "production", {"source_key": "$DOC:production", "relation_type": "HAS_PAGE", "target_key": "$PAGEKEY:production"}, [_claim("production", ["55", "giây"], "production")]),
        _case("graph-maintenance-page", "Tài liệu GRAPH-EVAL-MAINT-001 liên kết tới trang nguồn nào và chu kỳ là bao nhiêu?", "full_answer", "maintenance", {"source_key": "$DOC:maintenance", "relation_type": "HAS_PAGE", "target_key": "$PAGEKEY:maintenance"}, [_claim("maintenance", ["500", "giờ"], "maintenance")]),
        _case("graph-site-denied", "Mã GRAPH-EVAL-SITE-001 là gì?", "access_denied", "site_restricted", {}, [], scenario="governance", forbidden_sources=["graph_eval_site_restricted_v1.md"]),
        _case("graph-security-denied", "Mã GRAPH-EVAL-SEC-001 là gì?", "access_denied", "security_restricted", {}, [], scenario="governance", forbidden_sources=["graph_eval_security_restricted_v1.md"]),
        _case("graph-department-denied", "Mã GRAPH-EVAL-DEPT-001 là gì?", "access_denied", "department_restricted", {}, [], scenario="governance", forbidden_sources=["graph_eval_department_restricted_v1.md"]),
        _case("graph-draft-blocked", "Nội dung GRAPH-EVAL-DRAFT-001 là gì?", "insufficient_evidence", "draft", {}, [], scenario="governance", expected_retrieval=False),
        _case("graph-unpublished-blocked", "Nội dung GRAPH-EVAL-UNPUBLISHED-001 là gì?", "insufficient_evidence", "unpublished", {}, [], scenario="governance", expected_retrieval=False),
        _case("graph-superseded-blocked", "Nội dung GRAPH-EVAL-ASM-001 phiên bản 1 là gì?", "insufficient_evidence", "assembly_v1", {}, [], scenario="governance", expected_retrieval=False),
        _case("graph-expired-blocked", "Nội dung GRAPH-EVAL-EXPIRED-001 là gì?", "insufficient_evidence", "expired", {}, [], scenario="governance", expected_retrieval=False),
    ]


def generate_fixture(output: Path = DEFAULT_OUTPUT):
    output = Path(output)
    corpus = output / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    records = []
    for document in DOCUMENTS:
        rows = BOM_ROWS if document["key"] == "assembly_v2" else ()
        table = ""
        if rows:
            table = "\n\n| Mã hàng | Tên | Số lượng | Đơn vị | Vật liệu |\n|---|---|---:|---|---|\n" + "\n".join(
                f"| {row['part']} | {row['name']} | {row['value']} | {row['unit']} | {row['material']} |"
                for row in rows
            )
        path = corpus / document["filename"]
        path.write_text(f"# {document['title']}\n\nMã tài liệu: {document['doc_number']}\nPhiên bản: {document['version']}\n\n{document['body']}{table}\n", encoding="utf-8")
        records.append({
            **document, "rows": list(rows), "batch_id": FIXTURE_BATCH,
            "path": str(path.relative_to(output)).replace("\\", "/"),
            "effective_date": "2026-01-01", "expiry_date": "2030-01-01",
        })
    (output / "corpus_manifest.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
    values = cases()
    (output / "eval_manifest.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in values), encoding="utf-8")
    summary = {
        "schema": "graph-eval-fixture-v1", "documents": len(records),
        "cases": len(values), "bom_rows": len(BOM_ROWS),
        "batch": FIXTURE_BATCH, "collection": "MechChatbot_Graph_Eval_v1",
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(generate_fixture(args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
