# Wave 1 evaluation manifests

Thư mục này chứa khung manifest rỗng cho `Technical`, `HR` và `Purchasing`.
Các file JSONL chỉ có comment hướng dẫn, không phải evaluation corpus và không
được tính là đã đạt gate.

Mỗi record thật phải theo schema `pilot-eval-v4`, được phòng ban xác nhận và có
department, question, scenario, expected document/page, version policy,
keywords, security expectation và refusal expectation.

Nhóm scenario tối thiểu:

- Technical: drawing, BOM, specification, version, Vision/OCR, citation/refusal.
- HR: policy, procedure, form, effective version, confidential denial.
- Purchasing: quotation, purchase order, supplier, material code, tabular data.

Sau khi có dữ liệu thật, gộp manifest và chạy:

```powershell
New-Item -ItemType Directory -Force reports | Out-Null
Get-Content scripts/eval/templates/wave1/*.jsonl |
  Set-Content -Encoding utf8 reports/wave1-evaluation.jsonl

$env:PYTHONPATH="src"
python scripts/eval/pilot_rollout_gate.py reports/wave1-evaluation.jsonl `
  --expected-departments Technical,HR,Purchasing `
  --expected-department-count 3 `
  --minimum 75
```

Validator không tạo câu hỏi, không ghi evaluation gate và không kích hoạt rollout.
