"""Generate deterministic CRAG evaluation documents and a live manifest."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.crag_eval.constants import (
    DEFAULT_OUTPUT, FIXTURE_BATCH, FIXTURE_CODE_PREFIX, FIXTURE_DEPARTMENT,
    FIXTURE_REMOTE_SITE, FIXTURE_SITE,
)


DOCUMENTS = (
    {
        "key": "numbers", "filename": "crag_eval_numbers_v12.md", "doc_number": "CRAG-EVAL-NUM-001",
        "title": "Thông số chuẩn CRAG", "version": 12, "security_level": "internal", "site": FIXTURE_SITE,
        "body": "Giá trị định mức là 1,500 đơn vị. Khe hở chuẩn là 12,50 mm. Phiên bản hiện hành là 12.",
    },
    {
        "key": "bom", "filename": "crag_eval_bom_v1.md", "doc_number": "CRAG-EVAL-BOM-001",
        "title": "BOM kiểm thử CRAG", "version": 1, "security_level": "internal", "site": FIXTURE_SITE,
        "body": "| Mã | Số lượng |\n|---|---:|\n| CRAG-EVAL-PART-A | 2 |\n| CRAG-EVAL-PART-B | 3 |\n\nKhông có tổng BOM được phê duyệt trong tài liệu này.",
    },
    {
        "key": "no_cost", "filename": "crag_eval_no_cost_v1.md", "doc_number": "CRAG-EVAL-NOCOST-001",
        "title": "Hướng dẫn không có chi phí", "version": 1, "security_level": "internal", "site": FIXTURE_SITE,
        "body": "Tài liệu mô tả quy trình lắp CRAG-EVAL-PART-C. Tài liệu không công bố chi phí hoặc đơn giá.",
    },
    {
        "key": "alias", "filename": "crag_eval_alias_v1.md", "doc_number": "CRAG-EVAL-ALIAS-001",
        "title": "Từ điển biệt danh kỹ thuật", "version": 1, "security_level": "internal", "site": FIXTURE_SITE,
        "body": "Trong xưởng, cụm từ 'mắt cú xanh' là biệt danh của cảm biến quang CRAG-EVAL-SENSOR-77. Chu kỳ kiểm tra là 90 ngày.",
    },
    {
        "key": "restricted", "filename": "crag_eval_restricted_v1.md", "doc_number": "CRAG-EVAL-SECRET-001",
        "title": "Thông số hạn chế CRAG", "version": 1, "security_level": "confidential", "site": FIXTURE_REMOTE_SITE,
        "body": "Mã cấu hình hạn chế là CRAG-EVAL-SECRET-RED. Chỉ người dùng được cấp quyền mới được truy cập.",
    },
)


def _identity(*, roles=None, sites=None, clearance="internal") -> dict:
    return {
        "user_department": FIXTURE_DEPARTMENT,
        "user_roles": roles or ["viewer"],
        "allowed_departments": [FIXTURE_DEPARTMENT],
        "allowed_sites": sites or [FIXTURE_SITE],
        "max_security_level": clearance,
    }


def _source(document: dict) -> dict:
    return {
        "expected_document": document["filename"], "expected_page": 1,
        "expected_version": document["version"], "expected_sources": [document["filename"]],
    }


def _cases() -> list[dict]:
    by_key = {doc["key"]: doc for doc in DOCUMENTS}
    return [
        {"id": "crag-number-thousands", "question": "Giá trị định mức CRAG-EVAL-NUM-001 là bao nhiêu?", "expected_outcome": "full_answer", "expected_keywords": ["1,500"], **_identity(), **_source(by_key["numbers"])},
        {"id": "crag-number-decimal", "question": "Khe hở chuẩn của CRAG-EVAL-NUM-001 là bao nhiêu?", "expected_outcome": "full_answer", "expected_keywords": ["12,50"], **_identity(), **_source(by_key["numbers"])},
        {"id": "crag-version-citation", "question": "Phiên bản hiện hành của CRAG-EVAL-NUM-001 là gì? Hãy dẫn nguồn.", "expected_outcome": "full_answer", "expected_keywords": ["12"], **_identity(), **_source(by_key["numbers"])},
        {"id": "crag-bom-no-derived-total", "question": "Tổng số lượng BOM CRAG-EVAL-BOM-001 là bao nhiêu?", "expected_outcome": "insufficient_evidence", "expected_keywords": [], **_identity(), **_source(by_key["bom"])},
        {"id": "crag-no-cost-refusal", "question": "Chi phí của CRAG-EVAL-PART-C là bao nhiêu?", "expected_outcome": "insufficient_evidence", "expected_keywords": [], **_identity(), **_source(by_key["no_cost"])},
        {"id": "crag-alias-correction", "question": "Cảm biến quang mã 77 có chu kỳ kiểm tra bao lâu?", "expected_outcome": "full_answer", "expected_keywords": ["90 ngày"], "requires_correction": True, **_identity(), **_source(by_key["alias"])},
        {"id": "crag-restricted-denial", "question": "Mã cấu hình của CRAG-EVAL-SECRET-001 là gì?", "expected_outcome": "access_denied", "forbidden_sources": [by_key["restricted"]["filename"]], **_identity(clearance="internal"), **_source(by_key["restricted"])},
        {"id": "crag-admin-exception", "question": "Mã cấu hình của CRAG-EVAL-SECRET-001 là gì?", "expected_outcome": "full_answer", "expected_keywords": ["CRAG-EVAL-SECRET-RED"], "admin_exception": True, **_identity(roles=["admin"], sites=[FIXTURE_REMOTE_SITE], clearance="confidential"), **_source(by_key["restricted"])},
    ]


def generate_fixture(output: Path = DEFAULT_OUTPUT) -> dict:
    output = Path(output)
    corpus = output / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    today = date.today()
    records = []
    for doc in DOCUMENTS:
        path = corpus / doc["filename"]
        path.write_text(
            f"# {doc['title']}\n\n- Mã tài liệu: {doc['doc_number']}\n- Phiên bản: {doc['version']}\n"
            f"- Bộ dữ liệu: {FIXTURE_BATCH}\n\n## Nội dung đã phê duyệt\n\n{doc['body']}\n",
            encoding="utf-8",
        )
        records.append({
            **doc, "batch_id": FIXTURE_BATCH, "department": FIXTURE_DEPARTMENT,
            "path": str(path.relative_to(output)).replace("\\", "/"),
            "effective_date": (today - timedelta(days=1)).isoformat(),
            "expiry_date": (today + timedelta(days=365)).isoformat(),
        })
    (output / "corpus_manifest.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8"
    )
    cases = _cases()
    (output / "eval_manifest.jsonl").write_text(
        "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases), encoding="utf-8"
    )
    summary = {
        "batch_id": FIXTURE_BATCH, "part_code_prefix": FIXTURE_CODE_PREFIX,
        "documents": len(records), "cases": len(cases),
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(generate_fixture(args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
