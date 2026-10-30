# Tiến độ thống nhất runtime — 30/10/2026

Trạng thái: đang chuẩn bị, **chưa chuyển production và chưa hoàn thành goal**.
Đọc cùng [hướng dẫn vận hành](windows-service-operations.md) và
[thiết kế đã thống nhất](runtime-consolidation-proposal.md).

## Đã thực hiện trong worktree

- Chuẩn bị ba module launcher tại `scripts/windows_service`: host, runtime,
  configure. Giữ nguyên vòng đời process, DLL, identity và cấu hình hiện hành.
- Thay literal commit không thể tự ghim chính nó trong Git bằng ràng buộc request
  có ACL/thời hạn/nonce, HEAD thực tế, Git sạch, bundle có chữ ký, deployment SHA,
  hash và activation hợp lệ. Không sửa logic backend/frontend.
- Chuẩn bị chuyển command service theo cặp đường dẫn cố định; preview không đổi
  hệ thống, apply chỉ khi STOPPED và cấu hình chưa lệch; có chiều quay lui.
- Chuẩn bị công cụ Cloudflared riêng: giữ token path, so hash binary, kiểm ACL,
  reparse/hardlink, không tự copy/chmod/start/stop. Review sửa trường hợp owner
  TrustedInstaller hợp lệ trên thư mục tổ tiên; không nới quyền ghi binary.
- README trỏ tới một hướng dẫn production; demo có migration được phân biệt rõ.
- Đối chiếu release đang chạy: chữ ký và permanent capability hợp lệ, giữ nguyên
  feature grants/versions. Hồ sơ chuẩn bị không tái dùng approval cũ cho commit mới.
- Kiểm kê lại 12 scheduled task. Mười một Query có packet/authorization hết hạn,
  consumed và disposition kết thúc; đủ bằng chứng xét disable sau kiểm tra lần cuối.
  Task Math giữ Disabled vì script readiness còn kiểm tra sự tồn tại của nó.
- Bộ test launcher/Cloudflared sau sửa review: 41 test đạt. Preview quyền thường
  chặn đúng vì thiếu quyền đọc process. Preview elevated sau UAC đạt đủ 11 task;
  lượt apply `task-retirement-34eb7dfd76524ef7a2035f959b23171c` đã disable đúng 11
  task Query. Cả 12 task hiện Disabled (Math vốn Disabled); so sánh XML chỉ có
  Enabled thay đổi, các thuộc tính khác giữ nguyên. Không xóa task/hồ sơ/worktree.
- Dựng offline đủ 278 phiên bản từ lock có hash; pip check đạt; 56.666 file không
  hardlink/reparse; 11 import native/composition đạt. Runtime đang phục vụ không đổi.
- Baseline hoàn tất: 16 nhóm nội dung bảo vệ, local/public health đạt, RAG đúng
  commit `d80a1bc` và live authorization. Bốn service giữ nguyên PID sau nghỉ task.
- Audit dependency có advisory tồn tại từ trước (Python: 4 advisory/2 package;
  npm: 4 package bị ảnh hưởng). Security review chưa tìm thấy đường khai thác được
  đưa thêm bởi launcher. Không báo audit sạch hoặc tự nâng gói; giữ xử lý riêng.

## Còn phải hoàn tất

1. Giữ wheelhouse đã kiểm chứng cùng lock và baseline; dùng chúng đối chiếu sau
   chuyển, không coi import smoke là kiểm chứng runtime dưới service identity.
2. Chốt review và commit candidate; tích hợp vào main sau khi xác minh worktree
   giữ main không có hoạt động khác. Không push/merge từ xa.
3. Cấp release mới cho đúng commit, chuẩn bị snapshot cấu hình được bảo vệ và
   phương án quay lui đầy đủ. Không chuyển bằng script deploy cũ thiếu rollback.
4. Xác minh bằng chứng drain trước khi dừng bất kỳ service nào. Phiên Codex thường
   không elevated; SCM change/start/stop trả WinError 5. Sau khi người dùng yêu cầu
   mở UAC, probe riêng đã chạy elevated và mở được handle quản lý cả bốn service
   (`admin-probe-result.json`). Probe không đổi service; quyền chỉ thuộc tiến trình
   đó. Bộ chuyển thực tế vẫn cần chạy qua UAC với đầy đủ điều kiện an toàn.
   Probe hiện thấy RAG rảnh 6/6 slot nhưng SQL có hai job classifying không lock;
   chưa chứng minh worker/reconciliation rảnh. Không có admission/drain riêng đã
   xác minh. Request được cấu hình tới 240–300 giây, dài hơn giới hạn stop 90/110
   giây. Không dùng snapshot rảnh hoặc tăng timeout làm bằng chứng không ngắt việc.
5. Giải quyết quyền trên đường dẫn Cloudflared cũ: `.local`/repo có trustee được
   ghi ngoài danh sách tin cậy của công cụ. Không tự sửa ACL hoặc bỏ kiểm tra.
6. Chuyển có kiểm soát, kiểm tra identity qua Service Manager và các luồng web
   xác thực; đối chiếu vùng bảo vệ, kiểm chứng quay lui. Chưa reboot trong phạm vi.
7. Giữ XML/hồ sơ task đã nghỉ. Rà riêng worktree và ignored/untracked/commit riêng
   trước khi xét gỡ. Chưa xóa thêm worktree/file trong đợt này.

Receipt nằm trong `.local/runs/runtime-consolidation-20261030` tại worktree:
`service-tests.xml`, `task-retirement-evidence.json`, `release-preparation/`,
`dependencies/`. Chỉ receipt hoàn tất thành công mới là bằng chứng nghiệm thu.
Các kết quả cleanup 29/10 là lượt riêng, không cộng vào dung lượng của đợt này.
