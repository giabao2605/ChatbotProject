# Vận hành Windows và chuyển launcher vào Git

Ngày chuẩn bị: 30/10/2026. **Chưa chuyển production.** Đường dẫn mới chỉ là mã
ứng viên trong worktree cho tới khi có receipt chuyển đổi và kiểm tra web đạt.

## Bản chuẩn và nơi làm việc

Checkout phục vụ: `C:\Users\bao.nguyen\Documents\ChatBotProject`.
App 8080, RAG 8100 và Worker cùng dùng `src` và `chat_env` tại đây. Frontend là
`web-ui/dist`; không build lại trong đợt chỉ chuyển launcher. Service host vẫn
dùng Python 3.12 đã cài trong Windows. Giữ thứ tự nạp native DLL và các service
identity hiện hành. Giữ `.env`, overlay, token, cache, dữ liệu và quyền truy cập.

Sửa và kiểm thử trong worktree; tích hợp bản đã kiểm chứng vào `main` trước khi
phát hành. Checkout production ghim commit phát hành, không tự pull/reload.
Các worktree giữ bằng chứng không phải bản production thay thế.

Mã chuẩn: `scripts/windows_service/service_host.py`, `service_runtime.py`,
`configure_service.py`. Bộ `.local/domain-service-20261021` hiện còn dùng và phải
giữ nguyên tới sau chuyển đổi, nghiệm thu và xác nhận đường quay lui.

## Kiểm tra trạng thái, không thay đổi hệ thống

```powershell
$repo = 'C:\Users\bao.nguyen\Documents\ChatBotProject'
git -C $repo rev-parse HEAD
git -C $repo status --porcelain
Get-CimInstance Win32_Service |
  Where-Object Name -in @('MechRAGApp','MechRAGRag','MechRAGWorker','Cloudflared') |
  Select-Object Name, State, ProcessId, PathName, StartName, StartMode
Invoke-RestMethod 'http://127.0.0.1:8080/api/health'
Invoke-RestMethod 'https://mechraglab.click/api/health'
```

Health đạt cần `status=ok`, `db=ok`, `ingestion_worker=ready`. Health không chứng
minh hết request/job. Không in `.env`, token hoặc khóa ký ra terminal/báo cáo.
Đối chiếu commit với bundle được cấu hình, hash bundle và chữ ký; RAG health có
xác thực phải cho đúng commit và `live_authorized=true`.

## Chuẩn bị phát hành

1. Kiểm tra mọi thay đổi nguồn và WIP; ghi commit live, candidate và main. Không
   đổi nhánh ở worktree của người khác khi chưa xác minh ownership/trạng thái.
2. Chạy test launcher và review quyền/rollback. Tạo commit candidate sạch. Mã
   launcher không nhúng SHA của chính commit chứa nó: preflight ràng buộc request
   được ACL bảo vệ với HEAD thực tế, deployment SHA, bundle/hash/chữ ký và hợp
   đồng activation. Không thay các kiểm tra này bằng biến môi trường tin cậy mặc định.
3. Dùng cơ chế phát hành v8 đang có để cấp bundle mới cho đúng candidate, giữ
   nguyên feature grants/versions. Ghi chỉ dẫn cho phép của task hiện tại. Không
   tái dùng approval ngày 29/10 cho commit mới hoặc nói CI xanh khi chưa có kết quả.
4. Lưu cấu hình service đầy đủ, recovery, identity, ACL, hash các launcher cũ,
   frontend, dependency và cấu hình. Bản sao cấu hình chứa secret phải nằm trong
   thư mục chỉ chủ vận hành/SYSTEM/Administrators đọc được, ngoài Git.
5. Chuẩn bị restore commit/config/bundle và command cũ. Giữ binary/launcher cũ.
   Không dùng nguyên script deploy lịch sử: bản 29/10 không có rollback và có
   thao tác chép frontend không thuộc phạm vi chuyển launcher.

## Điều kiện được chuyển

- Có quyền thực tế query/change/start/stop service; quyền ghi repo không thay thế
  quyền quản lý Windows Service. Không đổi ACL để vượt lỗi Access denied.
- Có bằng chứng đã ngừng nhận công việc mới và mọi request/job đang xử lý đã xong.
  Hiện chưa có endpoint drain production được xác minh. Snapshot TCP hoặc heartbeat
  không đủ chứng minh không có việc mới xuất hiện. Thiếu điều kiện này thì dừng
  trước khi stop; không sử dụng timeout cưỡng bức như một cách drain.
- Có release hợp lệ cho commit mới, snapshot quay lui và người vận hành có quyền
  khôi phục. Tránh chạy đồng thời deployment/build/tác vụ dùng tài nguyên mục tiêu.

Host hiện có giới hạn dừng 110 giây, HTTP graceful shutdown 90 giây; job kéo dài
có thể bị ngắt khi hết giới hạn. Vì vậy kiểm tra hết việc là điều kiện bắt buộc.

## Chuyển và kiểm tra

Thực hiện một lượt bảo trì có kiểm soát. Không dùng các lệnh dưới để tự dừng service.
Sau khi các điều kiện trên đạt và service đã dừng sạch, cập nhật checkout production
đến commit đã cấp release, chọn bundle mới và deployment SHA/hash nhất quán.
Không đổi dependency, dữ liệu hoặc build frontend.

Chạy từ **checkout production đã cập nhật**, dùng host Python hiện hành:

```powershell
$hostPython = 'C:\Users\bao.nguyen\AppData\Local\Programs\Python\Python312\python.exe'
$configure = Join-Path $repo 'scripts\windows_service\configure_service.py'
# Mặc định xem trước khi app còn chạy; command phải khớp bộ cũ.
& $hostPython -B $configure app --relocate
# Chỉ sau khi đối chiếu kết quả và đủ điều kiện bảo trì:
& $hostPython -B $configure app --relocate --apply
```

Làm tương tự cho `rag` và `worker`. Công cụ chỉ đổi command giữa hai vị trí biết
trước, từ chối identity/command lệch, không tự start/stop hoặc đổi recovery/startup.
Chế độ thực thi yêu cầu service STOPPED; preview trả trạng thái đang quan sát.
Không chạy từ worktree chuẩn bị vì root của command được suy ra từ vị trí file.

Tạo request identity mới có nonce/thời hạn và ACL đúng như cơ chế hiện hành; chạy
preflight dưới từng service identity. Phải kiểm tra `ok`, nonce, request hash,
source commit, Windows/SQL identity, quyền dữ liệu/model và activation trước khi
khởi động phục vụ. Xóa request preflight của chính lượt chuyển sau khi thu receipt;
request còn tồn tại khiến host chạy chế độ probe thay vì phục vụ.

Chuyển Cloudflared riêng: binary đích đề xuất
`C:\ProgramData\MechRAGServices\bin\cloudflared.exe`, byte/hash phải bằng bản cũ,
quyền ghi chỉ dành người quản trị được tin cậy. Giữ nguyên token-file và tài khoản
service. Không nâng phiên bản, ghi đè binary đang chạy hoặc chuyển token.

`scripts/windows_service/relocate_cloudflared.py` xem trước mặc định; `--apply`
chỉ đổi command khi service đã dừng, cấu hình không lệch và hai binary bằng hash.
`--rollback --apply` đảo về command cũ, cùng các kiểm tra. Cần kiểm cả quyền thay
thế trên thư mục cha của binary cũ và mới; không tự whitelist một identity lạ
hoặc sửa quyền repo đang phục vụ để vượt kiểm tra. Nếu thư mục cũ không đạt,
phương án quay lui cần được giải quyết trước khi chuyển Cloudflared.

Khởi động qua Service Manager rồi kiểm tra local/public health, RAG authorization,
login, SSE có câu trả lời và citation, mở nguồn, history và logout. Dùng tài khoản
thử sẵn có; không tạo/reset tài khoản hoặc xóa hội thoại để làm sạch kết quả.
Đối chiếu nội dung các vùng bảo vệ; ghi rõ thay đổi release được phép.

## Quay lui

Nếu nghiệm thu không đạt, ngừng nhận việc và chờ hết việc trước khi dừng sạch.
Trong khi mã candidate còn hiện diện, công cụ hỗ trợ đảo command từng role:

```powershell
& $hostPython -B $configure app --relocate --rollback
& $hostPython -B $configure app --relocate --rollback --apply
```

Thực hiện với cả role đã chuyển, phục hồi command Cloudflared nếu đã đổi. Khôi phục
đúng commit, lựa chọn bundle và cấu hình cũ từ snapshot được bảo vệ; không trộn
commit cũ với bundle mới. Bảo toàn WIP, không dùng reset/clean rộng. Xóa request
probe của lượt chuyển để tránh khởi động nhầm chế độ, sau đó start qua Service
Manager và kiểm tra health/web lại. Nếu cấu hình đã drift, dừng và đối chiếu thay
vì ép ghi đè. Chưa được coi đường quay lui là đã thử trên host khi mới có unit test.

## Nghỉ các phụ thuộc cũ

Các task Query cũ cần đối chiếu XML/action với packet, thời hạn và receipt kết thúc
trước khi disable. Giữ XML xuất ra và hồ sơ gốc; không sửa bằng chứng lịch sử.
Task Math Window13 cần giữ Disabled vì readiness script hiện còn kiểm tra nó.
Không gỡ worktree chỉ vì task bị disable: còn phải kiểm commit riêng, ignored,
untracked, liên kết dữ liệu và tham chiếu hồ sơ. Chưa rõ thì giữ, ghi lý do.

Output mới dùng `.local/runs`, `.local/tmp`, `.local/venvs`. Không tự tạo lịch dọn.
Dependency tái lập phải được dựng thử riêng; không pip install vào `chat_env`.
Không reboot máy trong đợt này; Auto không phải bằng chứng đã thử khởi động sau reboot.
