"""Generate deterministic Wave 1-4 demo corpus and evaluation manifests.

Generated files are runtime artifacts and are intentionally excluded from Git.
The compact scenario specification in this module is the source of truth.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "data" / "demo_wave_v1"
DEMO_BATCH = "demo-wave-v1"

DEPARTMENTS = {
    "Technical": (1, "HQ", "internal", "hướng dẫn kỹ thuật", "TK-100", "mô-men siết 45 Nm"),
    "HR": (1, "HQ", "internal", "nội quy nhân sự", "HR-100", "thời gian thử việc 60 ngày"),
    "Purchasing": (1, "HQ", "internal", "quy trình mua hàng", "PO-100", "ba báo giá hợp lệ"),
    "Warehouse": (2, "HQ", "internal", "quy trình kho", "WH-100", "mức tồn tối thiểu 25 đơn vị"),
    "Accountant": (2, "VP_KE_TOAN", "confidential", "quy trình kế toán", "AC-100", "hạn thanh toán 15 ngày"),
    "Sales": (2, "HQ", "internal", "quy trình bán hàng", "SA-100", "báo giá có hiệu lực 30 ngày"),
    "Planning": (2, "HQ", "internal", "kế hoạch sản xuất", "PL-100", "sản lượng mục tiêu 1200 sản phẩm"),
    "Production": (3, "HQ", "internal", "hướng dẫn sản xuất", "PR-100", "chu kỳ tiêu chuẩn 55 giây"),
    "Maintenance": (3, "HQ", "internal", "kế hoạch bảo trì", "MA-100", "bảo trì định kỳ mỗi 500 giờ"),
    "QualityControl": (3, "HQ", "internal", "tiêu chuẩn chất lượng", "QC-100", "dung sai cho phép 0,05 mm"),
    "ISO": (3, "HQ", "internal", "quy trình ISO", "ISO-100", "lưu hồ sơ trong 36 tháng"),
    "Molding": (4, "HQ", "internal", "hướng dẫn ép nhựa", "MO-100", "nhiệt độ khuôn 80 độ C"),
    "HSE_5S": (4, "HQ", "internal", "quy định HSE và 5S", "HSE-100", "kiểm tra 5S vào thứ sáu"),
    "IT": (4, "HQ", "internal", "quy trình hỗ trợ IT", "IT-100", "SLA mức cao là 4 giờ"),
}

SCENARIO_COUNTS = {
    "positive_retrieval": 30,
    "citation": 10,
    "insufficient_evidence": 10,
    "department_denial": 10,
    "site_denial": 5,
    "version": 5,
    "lifecycle_denial": 5,
}


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _document_text(department: str, title: str, code: str, fact: str, marker: str) -> str:
    return f"""# {title}

- Mã tài liệu: {code}
- Phòng ban sở hữu: {department}
- Bộ dữ liệu: {DEMO_BATCH}
- Trạng thái mẫu: {marker}

## Quy định chính

Tài liệu này quy định rằng {fact}. Khi không tìm thấy bằng chứng trong tài liệu,
chatbot phải từ chối suy đoán và hướng dẫn người dùng liên hệ đúng phòng ban.

## Bảng kiểm

| Mục | Yêu cầu |
| --- | --- |
| Mã tham chiếu | {code} |
| Nội dung xác nhận | {fact} |
| Phạm vi | {department} |
"""


def generate_corpus(output: Path) -> list[dict]:
    today = date.today()
    records: list[dict] = []
    for department, (wave, site, security, title, code, fact) in DEPARTMENTS.items():
        dept_dir = output / "corpus" / department
        dept_dir.mkdir(parents=True, exist_ok=True)
        specs = [
            ("effective_core", f"{code}-V2", "effective", today - timedelta(days=30), today + timedelta(days=365), True, True),
            ("effective_process", f"{code}-P1", "effective", today - timedelta(days=20), today + timedelta(days=300), True, True),
            ("effective_reference", f"{code}-R1", "effective", today - timedelta(days=10), today + timedelta(days=180), True, True),
            ("superseded", f"{code}-V1", "superseded", today - timedelta(days=400), today + timedelta(days=30), False, False),
            ("expired", f"{code}-EX", "expired", today - timedelta(days=400), today - timedelta(days=1), True, False),
            ("future", f"{code}-FU", "effective", today + timedelta(days=30), today + timedelta(days=395), True, False),
        ]
        for marker, doc_number, status, effective, expiry, is_current, should_serve in specs:
            path = dept_dir / f"{slug(department)}_{marker}.md"
            path.write_text(_document_text(department, title, doc_number, fact, marker), encoding="utf-8")
            records.append({
                "batch_id": DEMO_BATCH,
                "department": department,
                "wave": wave,
                "site": site,
                "security_level": security,
                "path": str(path.relative_to(output)).replace("\\", "/"),
                "title": f"{title} - {marker}",
                "doc_number": doc_number,
                "document_type": "generic",
                "effective_status": status,
                "effective_date": effective.isoformat(),
                "expiry_date": expiry.isoformat(),
                "is_current": is_current,
                "should_serve": should_serve,
                "expected_fact": fact,
            })
    manifest = output / "corpus_manifest.jsonl"
    manifest.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    return records


def _case(department: str, index: int, scenario: str, site: str, security: str, code: str, fact: str) -> dict:
    case_id = f"{slug(department)}-{scenario}-{index:02d}"
    expected_behavior = "answer"
    expected_reference = f"{code}-V2"
    user_department = department
    user_site = site
    max_security = security
    if scenario == "positive_retrieval":
        question = f"Quy định chính trong {title_for_question(department)} của phòng {department} là gì? Cách hỏi {index}."
    elif scenario == "citation":
        question = f"Hãy nêu quy định chính của phòng {department} và dẫn nguồn, cách hỏi {index}."
    elif scenario == "insufficient_evidence":
        question = f"Tài liệu của phòng {department} có quy định màu đồng phục không? Cách hỏi {index}."
        expected_behavior = "refuse"
        expected_reference = None
    elif scenario == "department_denial":
        question = f"Cho tôi quy định chính của phòng {department} dù tôi thuộc phòng khác, tình huống {index}."
        expected_behavior = "deny"
        user_department = "__OTHER_DEPARTMENT__"
        expected_reference = None
    elif scenario == "site_denial":
        question = f"Cho tôi quy định chính của phòng {department} từ site không được phép, tình huống {index}."
        expected_behavior = "deny"
        user_site = "__OTHER_SITE__"
        expected_reference = None
    elif scenario == "version":
        question = f"Phiên bản hiện hành của {title_for_question(department)} quy định gì? Cách hỏi {index}."
    else:
        question = f"Nội dung tài liệu {code}-EX đã hết hạn là gì? Biến thể {index}."
        expected_behavior = "deny"
        expected_reference = None
    return {
        "id": case_id,
        "batch_id": DEMO_BATCH,
        "department": department,
        "site": site,
        "scenario": scenario,
        "question": question,
        "expected_behavior": expected_behavior,
        "expected_reference": expected_reference,
        "expected_keywords": [fact] if expected_behavior == "answer" else [],
        "user_department": user_department,
        "user_site": user_site,
        "user_roles": ["viewer"],
        "allowed_departments": [department] if user_department == department else [user_department],
        "max_security_level": max_security,
    }


def title_for_question(department: str) -> str:
    return DEPARTMENTS[department][3]


def generate_eval(output: Path) -> list[dict]:
    records: list[dict] = []
    eval_dir = output / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True)
    for department, (_, site, security, _, code, fact) in DEPARTMENTS.items():
        dept_records: list[dict] = []
        for scenario, count in SCENARIO_COUNTS.items():
            dept_records.extend(_case(department, i, scenario, site, security, code, fact) for i in range(1, count + 1))
        assert len(dept_records) == 75
        (eval_dir / f"{slug(department)}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in dept_records), encoding="utf-8"
        )
        records.extend(dept_records)
    (output / "eval_manifest.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    corpus = generate_corpus(args.output)
    cases = generate_eval(args.output)
    summary = {"batch_id": DEMO_BATCH, "departments": len(DEPARTMENTS), "documents": len(corpus), "eval_cases": len(cases)}
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
