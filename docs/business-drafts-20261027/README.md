# Bộ tài liệu nghiệp vụ cho 13 phòng — nội dung đã được duyệt

bao.nguyen đã đọc và duyệt toàn bộ 13 bản ngày **28/10/2026**, qua chỉ thị trực tiếp trong chat. Bằng chứng và hash của từng bản được lưu trong [approval.json](approval.json). Không cần duyệt lại phần nội dung đã được chấp thuận.

Các ô **CHƯA XÁC NHẬN** vẫn được giữ nguyên; việc duyệt không tạo ra số tiền, thời hạn, thông số hay cấp duyệt cụ thể cho các ô đó. Đã nạp và phát hành đủ 13 tài liệu qua luồng duyệt hiện có, hiệu lực **28/10/2026**: từng phòng đọc tài liệu của mình, admin đọc tất cả. DocID lần lượt 1173–1185; phiên bản kho nguồn là 1, nội dung nghiệp vụ giữ phiên bản 0.1 đã duyệt.

Đã kiểm tra quyền 182/182 tổ hợp tài liệu/tài khoản hiện có; 13 tài khoản tải đúng hash file gốc và bị chặn tải chéo phòng. Bằng chứng hiện hành: [publication](../../reports/remaining-followup-20261027/business-source-publication.json), [access](../../reports/remaining-followup-20261027/business-source-access.json), [downloads](../../reports/remaining-followup-20261027/business-source-downloads.json). Kiểm tra câu trả lời được ghi riêng trong [review](../../reports/remaining-followup-20261027/business-source-review.json); phát hành tài liệu không đồng nghĩa mọi câu hỏi đều đạt.

| Phòng | Tài liệu |
| --- | --- |
| Nhân sự | [Tiếp nhận nhân sự và theo dõi thử việc](HR.md) |
| Mua hàng | [Đề nghị mua, lựa chọn nhà cung cấp và theo dõi giao hàng](Purchasing.md) |
| Kho | [Nhận hàng, lưu kho và xuất hàng](Warehouse.md) |
| Kế toán | [Tiếp nhận chứng từ và xử lý đề nghị thanh toán](Accountant.md) |
| Kinh doanh | [Tiếp nhận yêu cầu, báo giá và bàn giao đơn hàng](Sales.md) |
| Kế hoạch | [Lập và điều chỉnh kế hoạch sản xuất](Planning.md) |
| Sản xuất | [Chuẩn bị lệnh, thực hiện và bàn giao sản phẩm](Production.md) |
| Bảo trì | [Bảo trì định kỳ và xử lý sự cố thiết bị](Maintenance.md) |
| Chất lượng | [Kiểm tra chất lượng và xử lý không phù hợp](QualityControl.md) |
| ISO | [Kiểm soát tài liệu, đánh giá nội bộ và theo dõi khắc phục](ISO.md) |
| Ép nhựa | [Chuẩn bị khuôn, xác nhận mẫu đầu và theo dõi ép](Molding.md) |
| An toàn và 5S | [Kiểm tra điều kiện an toàn, ghi nhận sự cố và theo dõi 5S](HSE_5S.md) |
| CNTT | [Tiếp nhận yêu cầu hỗ trợ và quản lý thay đổi truy cập](IT.md) |

Các file tại đây và `approval.json` giữ nguyên bản đã duyệt để đối chiếu hash. Bản gốc được bảo vệ dưới `data/raw/Uploads` chỉ cập nhật phần trạng thái, chủ sở hữu quản trị và ngày/phạm vi phát hành; phần quy trình không đổi. Chủ sở hữu/người duyệt tài liệu là tài khoản hiện có `admin_bao` (UserID 81), theo ủy quyền trực tiếp; không đổi người phụ trách mặc định của phòng hoặc quyền tài khoản. Tài liệu Kế toán dùng cơ sở kế toán hiện có, mức confidential; quy trình Nhân sự tổng quát dùng mức internal và chỉ chia sẻ HR, không chứa hồ sơ nhân viên.
