# Wave 3 rollout checklist

Wave 3 gồm `Production`, `Maintenance`, `QualityControl` và `ISO`. Code và
cấu hình được chuẩn bị trước, nhưng cả bốn phòng phải giữ trạng thái `planned`
cho tới khi dữ liệu thật đáp ứng đầy đủ readiness gate.

Không đánh dấu hoàn tất bằng dữ liệu mẫu. Không tạo Owner/Approver, corpus,
câu hỏi hoặc evaluation gate giả.

## Checklist dùng cho từng phòng

- [ ] Có Knowledge Owner và Knowledge Approver thật, còn active.
- [ ] Taxonomy, governance policy và domain profile đã được duyệt.
- [ ] Site, security level và owner/shared departments của corpus đã backfill.
- [ ] Có corpus current, approved, published, servable và còn hiệu lực.
- [ ] Có ít nhất 75 câu hỏi thật đã được phòng ban xác nhận.
- [ ] Có test citation, refusal, version, lifecycle và RBAC denial.
- [ ] Evaluation gate thật đã pass.
- [ ] Toàn bộ Wave 1 và Wave 2 đã `active`.
- [ ] Dark launch 3-5 ngày không có leak hoặc regression blocker.

## Trọng tâm theo phòng

- Production: drawing, BOM, work instruction, production order, version và
  truy vấn giao thoa với Technical/Planning.
- Maintenance: thiết bị, manual, lịch bảo trì, phụ tùng và tài liệu hết hạn.
- QualityControl: tiêu chuẩn, kết quả đo, NCR, CAPA, citation và refusal.
- ISO: controlled document, phiên bản hiệu lực, audit, nonconformity và
  không sử dụng bản superseded.

Backend là nguồn quyết định readiness. UI không được tự tính hoặc bỏ qua
`missing_prerequisites` do API trả về.
