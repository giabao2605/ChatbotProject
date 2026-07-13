# Wave 2 evaluation manifests

Thư mục này chứa khung manifest rỗng cho bốn phòng ban Wave 2:

- `warehouse.jsonl`
- `accountant.jsonl`
- `sales.jsonl`
- `planning.jsonl`

Các file chỉ có comment hướng dẫn. Chúng không phải corpus đánh giá và không
được tính là đã đáp ứng evaluation gate. Owner/Approver của từng phòng phải
xác nhận câu hỏi thật trước khi thêm từng JSON object trên một dòng.

## Schema bắt buộc

Mỗi dòng dữ liệu phải là một JSON object theo schema `pilot-eval-v4`:

```json
{
  "department": "<department code>",
  "question": "<câu hỏi thật đã được xác nhận>",
  "scenario": "<scenario id>",
  "expected_doc_id": "<DocID thật hoặc null cho refusal>",
  "expected_page": "<trang/section thật hoặc null cho refusal>",
  "expected_version_policy": "current",
  "expected_keywords": ["<từ khóa đã xác nhận>"],
  "security_expectation": "<public|internal|confidential và phạm vi truy cập>",
  "refusal_expectation": false
}
```

Không điền placeholder như một record thật. Với refusal case, vẫn phải khai
báo trường document/page nhưng được phép đặt `null`, đồng thời
`refusal_expectation` phải là `true`.

## Scenario tối thiểu cần chuẩn bị

Tất cả phòng ban phải có:

- grounded lookup và câu hỏi nhiều bước;
- đúng version/current document;
- citation đúng tài liệu và trang/section;
- câu hỏi không đủ evidence phải refusal;
- denied access theo department, site và security clearance;
- tài liệu hết hạn, pending hoặc superseded không được dùng;
- cách diễn đạt tiếng Việt có dấu, không dấu và từ viết tắt thực tế.

Nhóm riêng theo phòng:

- Warehouse: tồn kho, nhập/xuất/chuyển kho, stock card và mã vật tư.
- Accountant: hóa đơn, thanh toán, công nợ, sổ cái, lương và thuế; bắt buộc
  có confidential/cross-department denial.
- Sales: báo giá, sales order, hợp đồng, doanh thu và khách hàng.
- Planning: kế hoạch sản xuất, nhu cầu, tiến độ và kế hoạch nguyên vật liệu.

## Validate sau khi có dữ liệu thật

Gộp bốn manifest đã được duyệt thành một file tạm rồi chạy:

```powershell
New-Item -ItemType Directory -Force reports | Out-Null
Get-Content scripts/eval/templates/wave2/*.jsonl |
  Set-Content -Encoding utf8 reports/wave2-evaluation.jsonl

$env:PYTHONPATH="src"
python scripts/eval/pilot_rollout_gate.py reports/wave2-evaluation.jsonl `
  --expected-departments Warehouse,Accountant,Sales,Planning `
  --expected-department-count 4 `
  --minimum 75
```

Validator chỉ kiểm tra manifest và thống kê số lượng; nó không tạo câu hỏi,
không ghi evaluation gate và không kích hoạt rollout.
