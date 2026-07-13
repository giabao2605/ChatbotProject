# Wave 2 rollout checklist

Wave 2 gồm `Warehouse`, `Accountant`, `Sales` và `Planning`. Code/configuration
được chuẩn bị trước, nhưng cả bốn phòng phải giữ trạng thái `planned` cho tới
khi dữ liệu thật đáp ứng đầy đủ readiness gate.

Không đánh dấu hoàn tất bằng dữ liệu mẫu. Không tạo Owner/Approver, corpus,
câu hỏi hoặc evaluation gate giả.

## Checklist dùng cho từng phòng

Áp dụng độc lập cho cả bốn department code:

- [ ] Có Knowledge Owner thật và còn active.
- [ ] Có Knowledge Approver thật, khác Owner khi policy yêu cầu.
- [ ] Taxonomy, governance policy và domain profile đã được người phụ trách duyệt.
- [ ] Site, security level, owner/shared departments của corpus đã backfill.
- [ ] Có ít nhất một tài liệu current, approved, published, servable và còn hiệu lực.
- [ ] Không còn tài liệu publish thiếu site hoặc metadata bắt buộc.
- [ ] Có ít nhất 75 câu hỏi thật theo manifest `pilot-eval-v4`.
- [ ] Có test citation, refusal, version, lifecycle, department/site/security denial.
- [ ] Evaluation gate được ghi từ kết quả thật và đã pass.
- [ ] Toàn bộ phòng Wave 1 đã ở trạng thái `active`.
- [ ] Dark launch được Owner/Approver xác nhận và theo dõi 3-5 ngày.
- [ ] Chỉ chuyển `active` sau khi dark launch không có leak hoặc regression blocker.

## Yêu cầu riêng

### Warehouse

- Kiểm tra bảng tồn kho, phiếu nhập/xuất/chuyển và stock card.
- Đối chiếu chính xác mã vật tư, đơn vị tính, số lượng và kỳ dữ liệu.

### Accountant

- Mặc định corpus là `confidential` tại site `VP_KE_TOAN`.
- Bắt buộc test user thiếu clearance, sai site và sai phòng ban.
- Không đưa secret, credential hoặc thông tin không được phê duyệt vào corpus.

### Sales

- Phân biệt báo giá, đơn hàng, hợp đồng, hóa đơn và báo cáo doanh thu.
- Test dữ liệu khách hàng theo đúng security/department scope.

### Planning

- Phân biệt kế hoạch sản xuất, nhu cầu, tiến độ và kế hoạch nguyên vật liệu.
- Test mốc thời gian, version và tài liệu kế hoạch đã bị supersede.

## Trạng thái rollout hợp lệ

```text
planned -> dark_launch -> active
              |
              +-> blocked
```

`planned` không có nghĩa là corpus đã sẵn sàng. Backend là nguồn quyết định
readiness; UI chỉ hiển thị `missing_prerequisites` do API trả về.

Platform admin có thể kiểm tra trực tiếp bằng `GET
/api/catalog/rollout/readiness` trong session đã đăng nhập. Không tự sửa số
liệu readiness ở frontend hoặc database để bỏ qua gate.
