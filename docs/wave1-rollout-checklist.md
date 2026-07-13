# Wave 1 rollout checklist

Wave 1 gồm `Technical`, `HR` và `Purchasing`. Ba phòng được bootstrap ở trạng
thái `pilot`; trạng thái này không thay thế readiness gate và không tự cấp quyền
phục vụ dữ liệu.

Không đánh dấu hoàn tất bằng dữ liệu mẫu. Không tạo Owner/Approver, corpus,
câu hỏi hoặc evaluation gate giả.

## Checklist dùng cho từng phòng

- [ ] Taxonomy, governance policy và domain profile active đã được duyệt.
- [ ] Site, security level và owner/shared departments đã được backfill.
- [ ] Luồng ingest, publish, lifecycle và cache invalidation hoạt động đúng.
- [ ] RBAC department/site/security chặn đúng truy cập ngoài phạm vi.
- [ ] Có Knowledge Owner và Knowledge Approver thật, còn active.
- [ ] Có corpus current, approved, published, servable và còn hiệu lực.
- [ ] Có ít nhất 75 câu hỏi thật đã được phòng ban xác nhận.
- [ ] Evaluation gate thật đã pass.
- [ ] Dark launch 3–5 ngày không có leak hoặc regression blocker.

## Trọng tâm theo phòng

- Technical: drawing, BOM, specification, version, Vision/OCR và citation.
- HR: policy, procedure, form, confidential denial và đúng ngày hiệu lực.
- Purchasing: quotation, purchase order, supplier, material code và dữ liệu bảng.

Backend là nguồn quyết định readiness. UI không được tự tính hoặc bỏ qua
`missing_prerequisites` do API trả về.
