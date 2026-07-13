# Wave 4 evaluation manifests

Thư mục này chứa khung manifest rỗng cho `Molding`, `HSE_5S` và `IT`. Slot
thứ tư được để trống cho phòng ban thật sau này. Các file JSONL chỉ có comment
hướng dẫn, không phải evaluation corpus và không được tính là đã đạt gate:

- `molding.jsonl`
- `hse_5s.jsonl`
- `it.jsonl`

Mỗi record thật phải theo schema `pilot-eval-v4`, được phòng ban xác nhận và
có department, question, scenario, expected document/page, version policy,
keywords, security expectation và refusal expectation.

Nhóm scenario tối thiểu:

- Molding: drawing, BOM, mold/material specification, Vision/OCR và version.
- HSE_5S: safety, risk, work permit, incident, emergency, 5S và hiệu lực.
- IT: system/network/access/change/backup/security, credential và denial.

Sau khi có dữ liệu thật, gộp manifest và chạy:

```powershell
New-Item -ItemType Directory -Force reports | Out-Null
Get-Content scripts/eval/templates/wave4/*.jsonl |
  Set-Content -Encoding utf8 reports/wave4-evaluation.jsonl

$env:PYTHONPATH="src"
python scripts/eval/pilot_rollout_gate.py reports/wave4-evaluation.jsonl `
  --expected-departments Molding,HSE_5S,IT `
  --expected-department-count 3 `
  --minimum 75
```

Validator không tạo câu hỏi, không ghi evaluation gate và không kích hoạt rollout.
