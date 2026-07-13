# Wave 4 rollout checklist

Wave 4 hiện gồm `Molding`, `HSE_5S` và `IT`. Slot thứ tư được để trống cho
phòng ban thật sau này; không tạo placeholder. Cả ba phòng phải giữ trạng thái
`planned` cho tới khi dữ liệu thật đáp ứng đầy đủ readiness gate.

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
- [ ] Toàn bộ Wave 1, Wave 2 và Wave 3 đã `active`.
- [ ] Dark launch 3-5 ngày không có leak hoặc regression blocker.

## Trọng tâm theo phòng

- Molding: drawing, BOM, mold specification, material, version, Vision/OCR và
  truy vấn giao thoa với Technical/Production/Maintenance.
- HSE_5S: safety rule, risk assessment, work permit, incident, emergency, 5S
  và đúng phiên bản còn hiệu lực.
- IT: system guide, network, access, incident, change, backup, security,
  credential detection và confidential denial.

Backend là nguồn quyết định readiness. UI không được tự tính hoặc bỏ qua
`missing_prerequisites` do API trả về.
