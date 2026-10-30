# Kết quả dọn toàn repo ngày 29/10/2026

Repo đích: `C:\Users\bao.nguyen\Documents\ChatBotProject`.
Checkout công cụ/báo cáo: `C:\Users\bao.nguyen\.codex\worktrees\repo-safe-cleanup\ChatBotProject`.
Receipt chi tiết nằm tại `.local/runs/repo-cleanup-20261029` trong checkout công cụ.

## Đã thực hiện

Quét toàn repo gồm file ẩn và ignored, không đi theo junction/reparse point.
Lập danh sách phân loại từng thư mục cấp một, các nhóm `.local` và từng candidate
được dọn trong `classification-final.json`; inventory đầy đủ ở `before.json`.

Đã dọn **13.514 file, tổng kích thước 218.359.663 byte (208,24 MiB)**:

| Nhóm | File đã bỏ | Byte logic đã bỏ |
|---|---:|---:|
| Cache pytest, năm output coverage trong tmp, thư mục Playwright rỗng | 10 | 19.835.177 |
| Lượt đầu môi trường dependency-env, dừng khi gặp file bị giữ | 1 | 169.472 |
| Phần file riêng của dependency-env | 3.951 | 40.815.614 |
| Phần file riêng của dependency-env2 | 9.552 | 157.539.400 |

Trước khi dọn đã lưu meta/totals coverage, cấu hình Python và danh sách distribution
để dựng lại môi trường thử. Script tạo môi trường, backup, wheel, manifest cài đặt,
báo cáo kiểm thử và hồ sơ quay lui đều giữ nguyên. Không có thao tác xóa worktree,
bản vẽ, tài liệu nghiệp vụ, code, test, migration hoặc báo cáo chốt.

| Số đo toàn repo | Trước | Sau |
|---|---:|---:|
| Tổng kích thước file | 13.937.321.454 byte (12,980 GiB) | 13.719.114.513 byte (12,777 GiB) |
| Tổng số file | 470.159 | 456.654 |
| Riêng `.local` | 10.958.008.664 byte | 10.759.484.178 byte |

Dung lượng trống ổ đĩa tăng quan sát được **207,17 MiB**, không gán toàn bộ biến
động ổ đĩa cho cleanup. Chênh lệch ròng repo khác tổng byte đã xóa vì log vận hành
tiếp tục ghi và Codex tạo object/turn-diff Git. Không có lỗi đọc trong hai inventory.

## Vì sao không xóa cả hai môi trường

Hai môi trường này vốn dùng hardlink tới nhiều package của `chat_env`. Lượt đầu
dừng đúng khi Windows trả Access denied ở file mypy; kiểm tra Restart Manager
cho thấy file đang được ba process con của App/RAG/Worker giữ. Không đổi ACL,
chmod, ghi nội dung, ép xóa hoặc dừng service.

Sau đó công cụ được siết thêm: bảo vệ toàn bộ hardlink, kể cả link mới xuất hiện
sau khi lập snapshot. Hai manifest xóa cả môi trường ban đầu đã bị thay thế;
chỉ các subtree/file có một link được kiểm kê lại và thực thi. Phần còn lại giữ
hơn 53.000 đường dẫn dùng chung trong mỗi môi trường. Hai thư mục cũ **không còn
là venv thử nghiệm hoàn chỉnh**; cần dựng lại từ thông tin lưu nếu dùng lại.

Không báo đã thu hồi 3,7 GiB: đó chủ yếu là kích thước đường dẫn hardlink trùng
nội dung với runtime. Số byte logic đã bỏ không đồng nghĩa chính xác số byte
trống tăng trên ổ đĩa, vốn còn phụ thuộc overhead và tác vụ khác.

## Những phần được giữ

- Toàn bộ nguồn, cấu hình, `.git`, `chat_env`, `web-ui/dist` và dependency frontend.
- `data`, `src/data`, sáu thư mục bản vẽ; ảnh đối chiếu trong `tmp/pdfs`.
- Bộ service/tunnel; 11 tác vụ Query Ready và một tác vụ Math Disabled.
- Tất cả worktree, `.local/venvs`, `late-interaction-env` còn tham chiếu hoặc chưa rõ.
- Mọi reports, log vận hành, bản quay lui, wheel, file credential và mục lịch sử chưa đủ bằng chứng nghỉ hẳn.
- Bản tổng kết frontend coverage duy nhất, khoảng 12 KiB.

Các đường dẫn chưa rõ được giữ theo yêu cầu, không coi ngày cũ hay Git sạch là
bằng chứng hết dùng. Không di chuyển bộ service hoặc hồ sơ ràng buộc đường dẫn.

## Kiểm chứng

- Công cụ: **47 test đạt; coverage dòng 84,76%**. Có ca Windows khóa file thật,
  snapshot thay đổi, reparse point, alias Windows 8.3, tên Git khác hoa/thường,
  nested Git, vùng bảo vệ và hardlink xuất hiện sau review. Review độc lập đã
  đóng các finding đường dẫn và kiểm tra lại chính sách hardlink.
- Health local/public và RAG có xác thực đạt trước/sau từng nhóm. Runtime vẫn
  commit `d80a1bc2f6beec25f9b28b9c56be7ba8b3cc50cc`, cùng activation bundle hash.
- Bốn service giữ nguyên PID, cấu hình và trạng thái Running; 12 tác vụ giữ nguyên
  action và trạng thái. Checkout phục vụ giữ Git sạch.
- Smoke public HTTP/SSE đạt: login 200; thinking/delta/citation/done; nguồn 200;
  history 200 có câu hỏi; logout 200; gọi me sau logout trả 401.
- Smoke dùng tài khoản thử hiện có, để lại hội thoại
  `fb78a648-446d-437a-b6c0-435614eb50b3`. Không tạo/reset tài khoản hay xóa hội thoại.
- Đối chiếu cuối **16 nhóm file ổn định được bảo vệ không có thay đổi hash**,
  gồm `chat_env` (56.455 file không tính bytecode), nguồn, dữ liệu, giao diện,
  cấu hình, bộ service, reports, database và sáu thư mục bản vẽ. Log/bytecode/PID
  đang biến động không được dùng làm baseline hash. Receipt: `protected-after.json`
  so với `before.json`; inventory sau dọn: `after-inventory.json`.

Các file công cụ/test/docs chưa commit hoặc push. Không triển khai, restart,
build đè frontend, nâng dependency hay chạy migration/cleanup SQL/Qdrant.
Smoke là kiểm tra giao thức web, chưa kiểm tra trực quan trình duyệt hoặc reboot.

Quy trình dùng lại và nơi lưu output mới: [repository-cleanup.md](repository-cleanup.md).
