# Wave 3 evaluation manifests

Thư mục này chứa khung manifest rỗng cho `Production`, `Maintenance`,
`QualityControl` và `ISO`. Các file JSONL chỉ có comment hướng dẫn, không phải
evaluation corpus và không được tính là đã đạt gate:

- `production.jsonl`
- `maintenance.jsonl`
- `quality_control.jsonl`
- `iso.jsonl`

Mỗi record thật phải theo schema `pilot-eval-v4`, được phòng ban xác nhận và
có department, question, scenario, expected document/page, version policy,
keywords, security expectation và refusal expectation.

Nhóm scenario tối thiểu:

- Production: drawing, BOM, work instruction, production order và version.
- Maintenance: equipment manual, schedule, spare parts, current/expired.
- QualityControl: standards, measurement, NCR, CAPA, citation/refusal.
- ISO: controlled documents, effective version, audit, nonconformity và
  superseded denial.

Sau khi có dữ liệu thật, gộp manifest và chạy:

```powershell
New-Item -ItemType Directory -Force reports | Out-Null
Get-Content scripts/eval/templates/wave3/*.jsonl |
  Set-Content -Encoding utf8 reports/wave3-evaluation.jsonl

$env:PYTHONPATH="src"
python scripts/eval/pilot_rollout_gate.py reports/wave3-evaluation.jsonl `
  --expected-departments Production,Maintenance,QualityControl,ISO `
  --expected-department-count 4 `
  --minimum 75
```

Validator không tạo câu hỏi, không ghi evaluation gate và không kích hoạt rollout.
