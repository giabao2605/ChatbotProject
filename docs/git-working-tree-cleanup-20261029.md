# Phân loại và lưu working tree ngày 29/10/2026

Người dùng yêu cầu kiểm kê 141 thay đổi, làm sạch working tree và push GitHub. Sau khi biết lịch sử phân kỳ, người dùng chọn push các nhánh riêng. Không hợp nhất main và không triển khai thêm lên web.

## Phân loại ban đầu

| Worktree | Số file | Xử lý |
|---|---:|---|
| website-qa-fixes | 80 | Giữ toàn bộ 25 backend, 22 test backend, 21 frontend, 12 test frontend trên nhánh QA. Có sửa chưa nằm trong bản triển khai hiện tại; không copy đè F01–F06. |
| Checkout phục vụ web | 48 | 43 file skill và skills-lock đã xóa, README mô tả chuyển sang skill cá nhân; 3 tài liệu RAG chứa checkpoint/bàn giao. Lưu trên nhánh bàn giao rồi trả checkout phục vụ về đúng commit 27800dc. |
| rag-13-departments | 11 | Lưu .gitignore và 2 tài liệu; 8 SQLite coverage sinh tự động giữ local và ignore. |
| rag-five-fixes | 2 | Nội dung hai test giống HEAD và bản triển khai; làm mới checkout đúng hai file, không tạo commit rỗng. |

## Các nhánh được công bố

- `codex/f01-f06-release-20261029`: chứa commit triển khai `27800dc`, tách commit chuyển skill cá nhân và commit tài liệu bàn giao. Plan hợp nhất giữ cả checkpoint riêng của rag-13; có chú thích lịch sử để không nhận nhầm commit cũ là trạng thái hiện hành.
- `codex/website-qa-fixes`: lưu mã QA cũ và sáu test mới. Đây là nhánh công việc riêng, chưa xác nhận tích hợp với bản đang chạy.
- `codex/rag-13-checkpoint-20261029`: lưu checkpoint RAG ở đúng nền cũ, cùng quy tắc ignore coverage và ngoại lệ component.

## Kiểm tra và giới hạn

Đã so sánh diff/nội dung giữa các worktree, kiểm tra cú pháp 47 file Python thay đổi trong QA, kiểm tra whitespace và rà bí mật phần thay đổi. Giữ hook quét bí mật khi commit. Các số test trong hồ sơ cũ là bằng chứng lịch sử, không phải suite vừa chạy lại.

Audit dependency không sửa thư viện: lock QA có 7 cảnh báo (4 high, 3 moderate); lock bản F01–F06 có 4 (1 high, 3 moderate), đều không có critical theo npm audit tại thời điểm này. Python chat_env có 4 advisory trong langgraph-sdk và tornado. Đây là giới hạn còn mở, không coi việc push nhánh là đã khắc phục dependency hoặc nghiệm thu triển khai mới.

Không push .env, file coverage, dữ liệu runtime, báo cáo audit thô hoặc credential. Không force-push và không xóa nhánh/worktree. Checkout phục vụ giữ commit và cấu hình phát hành hiện có.
