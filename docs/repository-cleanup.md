# Dọn file phát sinh mà không ảnh hưởng web

Không xóa, di chuyển, đổi tên hoặc ghi đè thành phần web dùng khi chạy,
khởi động lại, chạy tác vụ hẹn giờ hoặc quay lui. Chưa xác minh được thì giữ.
Tuổi file, tên cache, Git sạch hoặc không có process nhìn thấy không chứng minh
một file đã hết dùng.

## Nơi làm việc và lưu output

- Công cụ, test và tài liệu được chuẩn bị trong checkout riêng. Checkout phục vụ
  phải giữ nguyên commit và trạng thái Git sạch vì có kiểm tra quản trị phụ thuộc Git.
- Output mới: `.local/runs/<tên-lượt-chạy>`; file tạm: `.local/tmp`;
  môi trường thử nghiệm mới: `.local/venvs`. Không áp dụng đổi vị trí hồi tố.
- Giữ `.local/worktrees`: có tác vụ hẹn giờ và hợp đồng bằng chứng phụ thuộc vị trí.
- Không tự chuyển bộ service, môi trường đang dùng hoặc hồ sơ có chữ ký.
- Không có lịch tự động xóa. Chốt kết quả mỗi lượt, ghi phần còn cần giữ rồi mới
  đưa output trung gian vào danh sách dọn của một lượt kiểm tra riêng.

## Phân loại toàn repo

| Nhóm | Mặc định | Căn cứ |
|---|---|---|
| `.git`, mã nguồn, script, migration, test, fixture, cấu hình, credential | Bảo vệ | Runtime, build, phát triển, kiểm chứng và lịch sử |
| `chat_env`, `web-ui/dist`, dependency frontend, bộ service/tunnel | Bảo vệ | Phục vụ và khôi phục web |
| `data`, `src/data`, sáu thư mục bản vẽ gốc | Bảo vệ | Nguồn, ảnh trang, cache nghiệp vụ và corpus đánh giá |
| `reports`, log vận hành, bản sao và wheel quay lui | Bảo vệ | Hồ sơ chốt, phát hành, điều tra và khôi phục |
| Worktree, venv có scheduled task hoặc tham chiếu chưa kết thúc | Bảo vệ | Bao gồm tác vụ Disabled chưa được cho nghỉ hẳn |
| Output coverage, pytest, Playwright và venv thử nghiệm đã kết thúc | Xét từng mục | Cần chứng cứ nguồn tạo, nhiệm vụ hoàn tất, không còn người dùng |
| Mục còn lại, gồm archive và công cụ cũ | Chưa rõ, giữ | Không suy ra vô dụng từ tên hoặc ngày sửa |

Giữ hồ sơ chốt cùng mọi đầu vào cần giải thích/tái kiểm chứng kết quả; giữ hồ sơ
bản đang chạy và ít nhất một bộ quay lui đầy đủ. Công cụ hiện cố ý chặn toàn bộ
reports/logs/worktrees/venvs mặc định; nếu muốn nghỉ các tài nguyên này phải có
quyết định riêng và cập nhật kiểm thử trước, không bỏ chốt để dọn nhanh.

## Quy trình

1. Quét dung lượng toàn repo, gồm file ẩn và ignored; không đi theo reparse point.
   Lập bảng bảo vệ/đủ điều kiện/chưa rõ, ghi đường dẫn, dung lượng, lý do và nguồn bằng chứng.
2. Đối chiếu service, cây process, scheduled task (cả Disabled), cấu hình,
   release bundle và tham chiếu bằng chứng. Nếu process bị hạn chế quyền quan sát,
   chỉ dùng cấu hình + cây cha đã xác minh; mục không giải thích được phải giữ.
3. Ghi baseline commit/Git status, hash file ổn định được bảo vệ, health local/public
   và RAG có xác thực. Không chạy script preflight cũ có thao tác start/stop service.
4. Lưu thông tin dựng lại môi trường, meta/totals của coverage trước khi dọn.
   Worktree chỉ gỡ khi đã kiểm tra cả ignored/untracked và bảo toàn commit riêng;
   không dùng công cụ xóa file này để gỡ worktree.
5. Tạo manifest chính xác, xem trước, rà kết quả; kiểm tra host lại ngay trước từng nhóm.
   Không chạy đồng thời writer/build/deploy/test tác động vào nhóm đó. Nếu không thể
   xác minh điều này thì không apply. Kiểm tra filesystem theo đường dẫn không phải
   khóa chống một writer cố ý đổi đường dẫn đồng thời.
6. Dọn nhóm nhỏ; file khóa, thay đổi hoặc không đủ quyền thì dừng nhóm, không ép xóa.
   Kiểm tra health/service sau mỗi nhóm. Giữ receipt dù đã dọn một phần.
7. Đối chiếu hash/commit/Git status/service/task trước-sau; kiểm tra đăng nhập,
   chat SSE, citation, history, logout bằng tài khoản hiện có. Không tạo/reset tài khoản,
   không tự xóa hội thoại kiểm thử. Nếu thiếu quyền, ghi đúng phần chưa kiểm chứng.

## Công cụ và manifest

`scripts/ops/safe_repo_cleanup.py` dùng thư viện chuẩn Python 3.11+, nhận repo đích
tường minh và không tự quyết định file đã hết dùng. `inspect_candidate(root, path,
protected_paths)` chỉ đọc và trả snapshot `{digest, bytes, files, directories}`.

Manifest JSON có `version: 1`, `root` tuyệt đối, `protected_paths` bổ sung và `entries`.
Mỗi entry chứa `path` tương đối dùng dấu `/`, `classification: "disposable"`, `reason`,
danh sách `evidence` và `snapshot` nguyên vẹn. Không wildcard, đường dẫn cha,
alternate data stream, tên Windows rút gọn có `~`, reparse point hoặc file tracked.
Vùng bảo vệ tích hợp không thể bị manifest ghi đè; cả cha và con đều bị chặn.

Chạy từ checkout công cụ, truyền manifest đã kiểm kê:

```powershell
python scripts/ops/safe_repo_cleanup.py --root 'C:\Users\bao.nguyen\Documents\ChatBotProject' --manifest '.local/runs/<run>/manifest.json'
# Chỉ thêm --apply sau khi xem trước và kiểm tra host của nhóm vừa đạt.
```

CLI trả JSON, exit code 0 cho preview/removed/absent, 1 khi blocked/partial.
Receipt ghi số file/byte logic đã bỏ. Khi dọn một phần, không tự tạo lại snapshot
để ép chạy tiếp; kiểm tra nguyên nhân và lập lượt mới nếu đủ điều kiện.

Hardlink được bảo vệ hoàn toàn: thực tế Windows có thể giữ file dùng chung bởi
service ngay cả khi đường dẫn được chọn thuộc môi trường thử nghiệm. Công cụ từ
chối file có hơn một link, kiểm tra lại trước unlink, không chmod, sửa ACL, truncate
hay sửa nội dung. Chỉ xét file có một link; môi trường cũ có thể còn lại các file
dùng chung và không còn là môi trường chạy hoàn chỉnh. Giữ thông tin dựng lại.
Báo riêng tổng byte đường dẫn đã bỏ và biến động dung lượng trống vì còn overhead
filesystem, hardlink và tác vụ khác; không coi tổng kích thước thư mục là số thu hồi.

Không commit/push/deploy, restart service, sửa dữ liệu SQL/Qdrant hoặc nâng dependency
như một phần của việc dọn. Bài test công cụ dùng repo tạm, không chạy apply vào web.
