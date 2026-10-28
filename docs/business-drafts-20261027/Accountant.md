# Dự thảo: Tiếp nhận chứng từ và xử lý đề nghị thanh toán

- Mã dự thảo: DRAFT-ACCOUNTANT-001
- Phiên bản: 0.1 — ngày 27/10/2026
- Trạng thái: NỘI DUNG ĐÃ ĐƯỢC BAO.NGUYEN DUYỆT ngày 28/10/2026; chưa phát hành trên chatbot
- Phòng dự kiến sở hữu: Kế toán
- Người soạn: Codex theo yêu cầu của bao.nguyen
- Người duyệt nội dung: bao.nguyen — ngày 28/10/2026
- Người sở hữu và ngày hiệu lực nghiệp vụ: CHƯA XÁC NHẬN

Nội dung quy trình đã được bao.nguyen đọc và duyệt. Các mục CHƯA XÁC NHẬN vẫn chưa có giá trị cụ thể; không tự điền hoặc dùng thay quy định khác đang có hiệu lực.

## Mục đích và phạm vi
Chuẩn hóa việc ghi nhận, thực hiện và lưu hồ sơ cho hoạt động: tiếp nhận chứng từ và xử lý đề nghị thanh toán. Các bước dưới đây là đề xuất, cần đối chiếu cách làm thực tế.

## Đầu vào đề xuất
- Đề nghị thanh toán có đối tượng và căn cứ
- Hợp đồng/đơn mua, giao nhận và chứng từ liên quan theo loại nghiệp vụ

## Các bước đề xuất
1. Kiểm tra hồ sơ và ghi những chứng từ thiếu hoặc thông tin chưa thống nhất.
2. Đối chiếu đối tượng, nội dung, số tiền và điều kiện thanh toán với căn cứ đã được duyệt.
3. Tách các khoản còn tranh chấp hoặc chưa đủ căn cứ; yêu cầu bổ sung từ người đề nghị.
4. Chuyển bộ hồ sơ hoàn chỉnh đến cấp có thẩm quyền; lưu quyết định trước khi thực hiện thanh toán.
5. Ghi nhận kết quả và lưu hồ sơ theo phân quyền; đối chiếu khoản chưa hoàn tất.

## Trách nhiệm cần xác nhận
- Người đề nghị: cung cấp đầu vào và làm rõ thông tin còn thiếu.
- Bộ phận phụ trách: kiểm tra, thực hiện trong phạm vi được giao và lưu kết quả.
- Người có thẩm quyền: quyết định các nội dung thuộc phạm vi được công ty phân công; tên/chức danh cụ thể chưa xác nhận.

## Điểm quyết định chưa có dữ liệu công ty
- Danh mục chứng từ theo từng nghiệp vụ: **CHƯA XÁC NHẬN**.
- Cấp duyệt và hạn mức: **CHƯA XÁC NHẬN**.
- Thời hạn thanh toán/đối soát: **CHƯA XÁC NHẬN**.
- Thời hạn lưu và quyền xem chứng từ: **CHƯA XÁC NHẬN**.

## Hồ sơ đầu ra đề xuất
- Hồ sơ thanh toán có quyết định
- Kết quả đối chiếu và danh sách còn thiếu

## Xử lý khi thiếu căn cứ
Ghi rõ thông tin còn thiếu, chuyển đầu mối xác nhận và giữ việc liên quan ở trạng thái chưa hoàn tất. Không tự điền thời hạn, số tiền, thông số, số lượng mẫu hoặc cấp duyệt.

## Câu hỏi rà soát tài liệu
1. Những bước nào khác với cách phòng đang làm?
2. Ai có thẩm quyền duyệt và những ngưỡng/thời hạn nào đã có căn cứ?
3. Hồ sơ nào chứng minh quy trình này được chấp thuận và có hiệu lực?

## Điều kiện phát hành
Nội dung đã được bao.nguyen duyệt; bằng chứng lưu trong approval.json cùng thư mục. Phát hành qua luồng nạp và duyệt hiện có, với phân quyền và ngày hiệu lực được xác định. Bản này chưa được nạp vào kho nguồn của chatbot.
