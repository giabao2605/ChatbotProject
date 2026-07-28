"""Generate department-wide Query Decomposition fixtures and labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.demo_wave.generate_demo_assets import DEPARTMENTS, slug


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "data" / "department_decomposition_eval_v1"
BATCH_ID = "department-decomposition-eval-v1"

ADDITIONAL_FACTS = {
    "Technical": (
        "chu kỳ hiệu chuẩn là 90 ngày",
        "quy trình lắp tiêu chuẩn gồm 3 bước",
    ),
    "HR": (
        "hồ sơ onboarding hoàn tất trong 5 ngày",
        "đánh giá thử việc thực hiện trước ngày thứ 55",
    ),
    "Purchasing": (
        "đơn mua trên 50 triệu đồng cần hai cấp duyệt",
        "nhà cung cấp được đánh giá lại mỗi 12 tháng",
    ),
    "Warehouse": (
        "kiểm kê chu kỳ thực hiện vào thứ sáu",
        "hàng chờ kiểm tra được giữ tối đa 24 giờ",
    ),
    "Accountant": (
        "hóa đơn phải đối soát trong 3 ngày",
        "chứng từ kế toán được lưu trong 10 năm",
    ),
    "Sales": (
        "chiết khấu trên 10 phần trăm cần trưởng phòng duyệt",
        "yêu cầu khách hàng được phản hồi trong 2 giờ",
    ),
    "Planning": (
        "kế hoạch tuần được khóa lúc 16 giờ thứ năm",
        "sai lệch trên 5 phần trăm phải lập lại kế hoạch",
    ),
    "Production": (
        "kiểm tra đầu ca hoàn tất trong 10 phút",
        "dừng chuyền khi lỗi liên tiếp 3 sản phẩm",
    ),
    "Maintenance": (
        "phiếu bảo trì khẩn được phản hồi trong 15 phút",
        "phụ tùng mức A được kiểm kê mỗi tháng",
    ),
    "QualityControl": (
        "lô đầu tiên được kiểm tra 100 phần trăm",
        "báo cáo NCR được phát hành trong 24 giờ",
    ),
    "ISO": (
        "hành động CAPA được đóng trong 30 ngày",
        "đánh giá nội bộ được thực hiện mỗi 6 tháng",
    ),
    "Molding": (
        "áp suất giữ tiêu chuẩn là 60 bar",
        "mẫu đầu phải được duyệt trước khi chạy hàng loạt",
    ),
    "HSE_5S": (
        "sự cố phải được báo trong 15 phút",
        "bình chữa cháy được kiểm tra mỗi 6 tháng",
    ),
    "IT": (
        "ticket P1 được phản hồi trong 15 phút",
        "khôi phục bản sao lưu được kiểm tra mỗi quý",
    ),
}


def _document_text(
    department: str,
    title: str,
    doc_number: str,
    fact: str,
) -> str:
    return f"""# {title}

- Mã tài liệu: {doc_number}
- Phòng ban sở hữu: {department}
- Bộ dữ liệu: {BATCH_ID}

## Quy định kiểm thử

{fact}.

## Phạm vi

Tài liệu synthetic này chỉ dùng để kiểm thử Query Decomposition trong demo.
"""


def _document_specs(department: str) -> list[tuple[str, str, str]]:
    _, _, _, _, code, core_fact = DEPARTMENTS[department]
    process_fact, reference_fact = ADDITIONAL_FACTS[department]
    return [
        ("core", f"{code}-D1", core_fact),
        ("process", f"{code}-D2", process_fact),
        ("reference", f"{code}-D3", reference_fact),
    ]


def generate_corpus(output: Path) -> list[dict]:
    records = []
    for department, (_, site, security, title, _, _) in DEPARTMENTS.items():
        directory = output / "corpus" / department
        directory.mkdir(parents=True, exist_ok=True)
        for marker, doc_number, fact in _document_specs(department):
            path = directory / f"{slug(department)}_decomp_{marker}.md"
            path.write_text(
                _document_text(
                    department,
                    f"{title} - Query Decomposition {marker}",
                    doc_number,
                    fact,
                ),
                encoding="utf-8",
            )
            records.append({
                "batch_id": BATCH_ID,
                "department": department,
                "site": site,
                "security_level": security,
                "path": str(path.relative_to(output)).replace("\\", "/"),
                "title": f"{title} - Query Decomposition {marker}",
                "doc_number": doc_number,
                "document_type": "generic",
                "effective_status": "effective",
                "effective_date": "2026-01-01",
                "expiry_date": "2030-01-01",
                "is_current": True,
                "should_serve": True,
                "expected_fact": fact,
            })
    (output / "corpus_manifest.jsonl").write_text(
        "".join(
            json.dumps(record, ensure_ascii=False) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    return records


def _citation(document: dict) -> dict:
    filename = Path(document["path"]).name
    return {
        "document": filename,
        "doc_id": f"$DOC:{filename}",
        "page": 1,
        "version": 1,
        "source_id": f"$PAGE:{filename}:1",
    }


def _claim(claim_id: str, fact: str, document: dict) -> dict:
    filename = Path(document["path"]).name
    return {
        "id": claim_id,
        "required_terms": [fact],
        "allowed_source_ids": [f"$PAGE:{filename}:1"],
    }


def _branch(position: int, outcome: str, *documents: dict) -> dict:
    return {
        "branch_id": f"branch-{position}",
        "expected_outcome": outcome,
        "expected_citations": [_citation(item) for item in documents],
    }


def _case(
    case_id: str,
    question: str,
    group: str,
    outcome: str,
    department: str,
    documents: list[dict],
    claims: list[dict],
    branches: list[dict],
    **extra,
) -> dict:
    primary = documents[0]
    return {
        "manifest_schema": "rag-eval-manifest-v2",
        "id": case_id,
        "question": question,
        "evaluation_group": group,
        "expected_outcome": outcome,
        "expected_claims": claims,
        "expected_citations": [_citation(item) for item in documents],
        "expected_branches": branches,
        "expected_document": Path(primary["path"]).name,
        "expected_page": 1,
        "expected_version": 1,
        "expected_sources": [Path(item["path"]).name for item in documents],
        "expected_department": department,
        "expected_site": primary["site"],
        "expected_security_level": primary["security_level"],
        "user_department": department,
        "user_roles": ["viewer"],
        "allowed_departments": [department],
        "allowed_sites": [primary["site"]],
        "max_security_level": primary["security_level"],
        **extra,
    }


def generate_eval(output: Path, documents: list[dict]) -> list[dict]:
    by_department = {
        department: [
            item for item in documents if item["department"] == department
        ]
        for department in DEPARTMENTS
    }
    departments = list(DEPARTMENTS)
    cases = []
    for index, department in enumerate(departments):
        own = by_department[department]
        foreign_department = departments[(index + 1) % len(departments)]
        foreign = by_department[foreign_department][0]
        own_claims = [
            _claim(f"{slug(department)}-{position}", item["expected_fact"], item)
            for position, item in enumerate(own, 1)
        ]
        prefix = slug(department)
        cases.extend([
            _case(
                f"{prefix}-decomp-simple",
                f"Theo tài liệu {own[0]['doc_number']}, quy định chính là gì?",
                "simple",
                "full_answer",
                department,
                own[:1],
                own_claims[:1],
                [],
            ),
            _case(
                f"{prefix}-decomp-two-intents",
                (
                    f"Đối chiếu {own[0]['doc_number']} và "
                    f"{own[1]['doc_number']}: nêu hai quy định."
                ),
                "complex",
                "full_answer",
                department,
                own[:2],
                own_claims[:2],
                [
                    _branch(1, "full_answer", own[0]),
                    _branch(2, "full_answer", own[1]),
                ],
            ),
            _case(
                f"{prefix}-decomp-three-intents",
                (
                    f"Tổng hợp {own[0]['doc_number']}, "
                    f"{own[1]['doc_number']} và {own[2]['doc_number']}."
                ),
                "complex",
                "full_answer",
                department,
                own,
                own_claims,
                [
                    _branch(1, "full_answer", own[0]),
                    _branch(2, "full_answer", own[1]),
                    _branch(3, "full_answer", own[2]),
                ],
            ),
            _case(
                f"{prefix}-decomp-access-denied",
                (
                    f"Nêu quy định trong {own[0]['doc_number']} và "
                    f"{foreign['doc_number']}."
                ),
                "complex",
                "partial_answer",
                department,
                own[:1],
                own_claims[:1],
                [
                    _branch(1, "full_answer", own[0]),
                    _branch(2, "access_denied"),
                ],
                forbidden_sources=[Path(foreign["path"]).name],
                preflight_documents=[{
                    "document": Path(foreign["path"]).name,
                    "version": 1,
                    "department": foreign_department,
                    "site": foreign["site"],
                    "security_level": foreign["security_level"],
                }],
            ),
        ])
    (output / "eval_manifest.jsonl").write_text(
        "".join(
            json.dumps(case, ensure_ascii=False) + "\n" for case in cases
        ),
        encoding="utf-8",
    )
    return cases


def generate_fixture(output: Path = DEFAULT_OUTPUT) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    documents = generate_corpus(output)
    cases = generate_eval(output, documents)
    report = {
        "batch_id": BATCH_ID,
        "departments": len(DEPARTMENTS),
        "documents": len(documents),
        "eval_cases": len(cases),
    }
    (output / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(generate_fixture(args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
