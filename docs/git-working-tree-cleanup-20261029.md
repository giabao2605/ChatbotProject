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

## Yêu cầu tiếp theo: hợp nhất main

Sau khi các nhánh riêng đã được lưu, người dùng yêu cầu hợp nhất và đồng bộ main local với GitHub. Phần phân loại bên trên là checkpoint trước quyết định này.

- Hợp nhất main cũ `4e8b52f` với bản phát hành/bàn giao, sau đó QA và checkpoint RAG; giữ lịch sử của cả ba nhánh, không force-push.
- Giữ hợp đồng chung của main cùng F01–F06: nguồn theo phiên bản/hash, phân quyền, lọc demo, khôi phục hội thoại và hỏi rõ ngữ cảnh. Giữ các sửa QA về job/source identity, metadata, preview và đăng xuất nhiều tab.
- Review phát hiện và sửa hai điểm giao auth: generation phải gắn với lúc xác thực mật khẩu; đổi mật khẩu và thu hồi phiên phải nằm trong cùng transaction SQL. Tận dụng bảng hiện có, không thêm schema. Có hồi quy cho reset xen giữa login và rollback khi thu hồi lỗi.
- Sửa F04 để câu lịch sử còn thiếu đối tượng không được dùng làm chủ đề đã rõ; hai ca RED và bảy ca liên quan đạt sau sửa.
- Giữ nguồn/checkpoint lịch sử đầy đủ. Quy tắc ignore thư mục components được giới hạn ở thư mục gốc để không che component frontend mới; coverage sinh tự động vẫn chỉ nằm local.

Đã chạy các nhóm kiểm tra bị ảnh hưởng về file/ingestion, quyền và phiên bản nguồn, RAG/provider/routing, ops, job/SQL, API/auth và giao diện. Các fixture cũ được cập nhật đúng hợp đồng kết hợp rồi kiểm tra lại ca lỗi; không hạ kiểm quyền hoặc bỏ pin để làm test đạt. Frontend typecheck/build đạt; 326 file Python thay đổi so với main cũ qua kiểm tra cú pháp. Review độc lập đã đóng các finding nêu trên.

Không chạy lại toàn bộ suite, kiểm tải hoặc GUI trên bản hợp nhất. Không cập nhật dependency ngoài việc giữ các phiên bản từ hai nhánh đã rà; các advisory đã ghi ở trên vẫn là giới hạn còn mở. Không chạy migration hoặc triển khai: web tiếp tục dùng bản `27800dc`; phát hành commit main mới cần luồng triển khai riêng.

## Chuẩn bị CI trước triển khai main

CI sạch phát hiện các dependency đọc tài liệu chưa được khai báo, glob Vitest bị Bash mở rộng và fixture cũ chưa theo hợp đồng nguồn/timeout/OCR hiện hành. Đã khai báo các reader đang dùng, sửa lệnh Vitest và cập nhật fixture; không bỏ kiểm tra quyền, nguồn hoặc deadline. Giới hạn pytest dưới 9.2 sau khi tái hiện lỗi khôi phục thuộc tính Starlette State gây lỗi dây chuyền; 237 ca API/auth đạt với dependency giống CI và pytest 9.1.1. Pandas được giữ ở major 2 phù hợp môi trường service 2.3.3; file requirements.lock.txt vẫn là snapshot không canonical.

Kiểm tra kiến trúc ghi nhận chính xác 15 cạnh phụ thuộc / 31 lần xuất hiện đã có trong commit triển khai `27800dc`, kèm trách nhiệm xử lý trong allowlist. Đây là nợ kiến trúc được ghi nhận, không phải đã refactor xong; scanner và giới hạn số lần xuất hiện vẫn giữ nguyên. Một import facade được đổi sang module cụ thể, không đổi hành vi.

Các kiểm tra cục bộ chỉ xác nhận nhóm bị ảnh hưởng. Trạng thái CI cuối cùng và bản thực tế được triển khai phải đối chiếu theo đúng commit; phần này không tuyên bố CI toàn repo đã đạt hoặc web đã được nâng cấp.
