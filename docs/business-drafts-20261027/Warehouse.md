# Dự thảo: Nhận hàng, lưu kho và xuất hàng

- Mã dự thảo: DRAFT-WAREHOUSE-001
- Phiên bản: 0.1 — ngày 27/10/2026
- Trạng thái: NỘI DUNG ĐÃ ĐƯỢC BAO.NGUYEN DUYỆT ngày 28/10/2026; chưa phát hành trên chatbot
- Phòng dự kiến sở hữu: Kho
- Người soạn: Codex theo yêu cầu của bao.nguyen
- Người duyệt nội dung: bao.nguyen — ngày 28/10/2026
- Người sở hữu và ngày hiệu lực nghiệp vụ: CHƯA XÁC NHẬN

Nội dung quy trình đã được bao.nguyen đọc và duyệt. Các mục CHƯA XÁC NHẬN vẫn chưa có giá trị cụ thể; không tự điền hoặc dùng thay quy định khác đang có hiệu lực.

## Mục đích và phạm vi
Chuẩn hóa việc ghi nhận, thực hiện và lưu hồ sơ cho hoạt động: nhận hàng, lưu kho và xuất hàng. Các bước dưới đây là đề xuất, cần đối chiếu cách làm thực tế.

## Đầu vào đề xuất
- Đơn mua/lệnh điều chuyển hoặc căn cứ nhận hàng
- Phiếu yêu cầu xuất hoặc lệnh sản xuất hợp lệ

## Các bước đề xuất
1. Đối chiếu hàng nhận với chứng từ về mã, số lượng và tình trạng; ghi nhận chênh lệch.
2. Phân biệt hàng chờ kiểm tra với hàng đã được xác nhận trạng thái; lưu vị trí và lô khi có.
3. Cập nhật nhập kho theo kết quả kiểm tra và căn cứ được phép sử dụng.
4. Kiểm tra yêu cầu xuất, đối chiếu mã/lô/số lượng và ghi người giao nhận.
5. Đối chiếu tồn thực tế với sổ kho; tách chênh lệch để xác minh trước khi điều chỉnh.

## Trách nhiệm cần xác nhận
- Người đề nghị: cung cấp đầu vào và làm rõ thông tin còn thiếu.
- Bộ phận phụ trách: kiểm tra, thực hiện trong phạm vi được giao và lưu kết quả.
- Người có thẩm quyền: quyết định các nội dung thuộc phạm vi được công ty phân công; tên/chức danh cụ thể chưa xác nhận.

## Điểm quyết định chưa có dữ liệu công ty
- Thẩm quyền duyệt xuất/điều chỉnh tồn: **CHƯA XÁC NHẬN**.
- Mức tồn tối thiểu theo mã hàng: **CHƯA XÁC NHẬN**.
- Lịch kiểm kê: **CHƯA XÁC NHẬN**.
- Điều kiện và thời hạn lưu hàng chờ kiểm tra: **CHƯA XÁC NHẬN**.

## Hồ sơ đầu ra đề xuất
- Phiếu nhập/xuất và vị trí lưu
- Biên bản kiểm kê/chênh lệch

## Xử lý khi thiếu căn cứ
Ghi rõ thông tin còn thiếu, chuyển đầu mối xác nhận và giữ việc liên quan ở trạng thái chưa hoàn tất. Không tự điền thời hạn, số tiền, thông số, số lượng mẫu hoặc cấp duyệt.

## Câu hỏi rà soát tài liệu
1. Những bước nào khác với cách phòng đang làm?
2. Ai có thẩm quyền duyệt và những ngưỡng/thời hạn nào đã có căn cứ?
3. Hồ sơ nào chứng minh quy trình này được chấp thuận và có hiệu lực?

## Điều kiện phát hành
Nội dung đã được bao.nguyen duyệt; bằng chứng lưu trong approval.json cùng thư mục. Phát hành qua luồng nạp và duyệt hiện có, với phân quyền và ngày hiệu lực được xác định. Bản này chưa được nạp vào kho nguồn của chatbot.
