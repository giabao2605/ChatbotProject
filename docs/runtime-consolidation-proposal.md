# Đề xuất thống nhất một bản vận hành chính

Ngày khảo sát: 29/10/2026. Trạng thái: đề xuất sau khảo sát; chưa chuyển file,
đổi cấu hình service, đổi scheduled task hoặc triển khai.

## Web thực sự chạy ở đâu

Repo phục vụ: `C:\Users\bao.nguyen\Documents\ChatBotProject`.
Root đang ở commit `d80a1bc2f6beec25f9b28b9c56be7ba8b3cc50cc`, detached HEAD,
Git sạch; local `main` hiện cùng commit. Detached HEAD là cách ghim bản chạy,
không phải bằng chứng có thêm một bản ứng dụng.

```text
Người dùng
  -> Cloudflared
  -> App/API 8080 -> web-ui/dist + src/mech_chatbot
                 -> RAG 8100 -> cùng src/mech_chatbot
                 -> SQL/Qdrant/tài liệu
  Worker xử lý tài liệu -> cùng src/mech_chatbot

App + RAG + Worker dùng root/chat_env và làm việc tại root repo.
Windows Service gọi launcher trong .local/domain-service-20261021.
```

App, RAG và Worker là ba vai trò của cùng một hệ thống, không phải ba bản code.
Frontend source và dist lần lượt là mã để sửa và sản phẩm để phục vụ, không phải
hai bản ứng dụng cạnh tranh nhau.

Service host hiện dùng Python cài ở Windows; process ứng dụng dùng `chat_env`.
Host là lớp nối với Windows Service, không phải một checkout ứng dụng khác.
Không đổi interpreter của host cùng lúc chuyển vị trí launcher.

## Vì sao nhìn như nhiều bản chồng lấp

1. Launcher production nằm trong thư mục `.local` theo ngày, cùng nơi chứa thử
   nghiệm. Source launcher không có trong `git ls-files`, gây thiếu lịch sử review
   và dễ bị hiểu nhầm là file tạm. Docstring runtime nói rõ mục đích ban đầu là
   giữ nguyên source ứng dụng đã ký; đây là dấu vết về thiết kế ban đầu, không phải
   căn cứ khẳng định toàn bộ lý do lịch sử.
2. Có nhiều lối chạy: Windows Service hiện hành, `run_server.py`/`run_worker.py`,
   module Python, supervisor demo và Docker. Chúng có vai trò khác nhau nhưng tài
   liệu chưa chỉ rõ lối production chuẩn. Demo launcher có tự chạy migration.
3. Scheduled task cũ vẫn tham chiếu worktree/venv lịch sử. Có 11 Query Ready nhưng
   NextRunTime rỗng, LastRun tháng 9; một Math Disabled, LastRun tháng 8. Điều này
   không tự chứng minh không ai còn gọi thủ công hoặc task được phép xóa.
4. `requirements.txt` được CI dùng; `requirements.lock.txt` hiện là snapshot,
   chưa phải lock chuẩn. Cài lại theo các chỉ dẫn khác nhau có thể ra bộ gói khác.
5. Hồ sơ thử, bản sao quay lui và dữ liệu đang dùng trộn chung theo tên chiến dịch.
   Di chuyển tất cả để đẹp cây thư mục có thể phá đường dẫn và bằng chứng đã ký.

## Kiến trúc đề xuất chốt

**Một nhánh nguồn chuẩn, một checkout production, một môi trường Python ứng dụng,
một quy trình vận hành.** Giữ khu sửa/test tạm và một bộ quay lui có định danh.

| Thành phần | Nơi chuẩn | Quy tắc |
|---|---|---|
| Code backend | root/src | Nguồn ứng dụng duy nhất |
| Code giao diện và bản build | root/web-ui/src và root/web-ui/dist | Chỉ phát hành dist được build từ cùng bản code đã kiểm chứng |
| Môi trường ứng dụng | root/chat_env | Giữ tên/vị trí hiện tại; không pip thử nghiệm vào đây |
| Source Windows Service | root/scripts/windows_service | Chuyển host/runtime/configure và test cần thiết vào Git |
| Công cụ vận hành | root/scripts/ops | Một lối vào rõ ràng cho status, kiểm tra, cập nhật và quay lui |
| Cloudflared binary | C:\ProgramData\MechRAGServices\bin | Một bản cố định, quyền ghi hạn chế; pin version/hash, không commit binary |
| Secret và state của service | ProgramData hiện tại và .env hiện tại | Giữ vị trí/quyền/độ ưu tiên cấu hình trong đợt đầu |
| Dữ liệu và model cache | Vị trí hiện có | Không nhập chung vào code hoặc di chuyển vì thẩm mỹ |
| Output thử nghiệm mới | .local/runs, .local/tmp, .local/venvs | Có tên nhiệm vụ, trạng thái kết thúc và quy tắc lưu/dọn |
| Worktree cũ | Giữ tới khi hết mọi tham chiếu | Không còn là lựa chọn production; cho nghỉ từng bản có bằng chứng |

Không rải các file service ở ngay cấp cao nhất của root; dùng thư mục ổn định trong
repo để dễ tìm mà không làm root rối thêm. Không thêm framework triển khai mới,
Docker hóa hoặc đổi tên chat_env chỉ để đồng nhất hình thức.

Root production không phải nơi thử code trực tiếp. Sửa/test trong worktree tạm,
tích hợp vào main, rồi cập nhật root production tới commit được duyệt. Không tự
git pull hoặc bật reload trên web đang chạy. Việc có khu thử tạm và một bản quay
lui không tạo nhiều bản vận hành chính.

## Trình tự thực hiện đề nghị

### 1. Làm rõ cách vận hành và gom launcher

Đưa source launcher đang được kiểm chứng từ thư mục cũ vào
`scripts/windows_service`; chuyển các test liên quan. Sửa đồng bộ tham chiếu tại
host/configure/register/install/probe/deploy, giữ nguyên stop handling, job-object
containment, thứ tự nạp DLL, service identity, security overlay và các port.
Vị trí mới có độ sâu tương đương vị trí cũ, nhưng phải test việc xác định root,
không dựa vào việc chạy đúng thư mục hiện tại.

README mở đầu bằng bản đồ vận hành Windows hiện hành và một trang hướng dẫn chính.
Đánh dấu rõ dev/demo/Docker là luồng phụ; không dùng demo có migration làm lệnh
restart production. Các entrypoint cũ có caller còn dùng thì giữ wrapper nhỏ hoặc
hướng dẫn thay thế; không duy trì logic khởi động ứng dụng trùng nhau.

### 2. Chuẩn bị bản phát hành và chuyển service

Mã launcher mới tạo commit mới: phát hành qua cơ chế identity/chữ ký hiện có,
đồng bộ commit được ghim và hồ sơ cần thiết. Không bỏ kiểm tra Git sạch hoặc ký lại
hồ sơ cũ theo nội dung mới để hợp thức hóa.

Script configure hiện hành từ chối service có command khác, nên cần đường cập
nhật cấu hình có kiểm tra giá trị cũ và rollback; không chạy lại installer với
hy vọng nó tự thay service. Chuẩn bị cấu hình và binary mới, kiểm tra quyền trước.

Chuyển vào một đợt bảo trì ngắn khi không có request/job đang xử lý; ngừng nhận
công việc mới, chờ hết việc đang chạy, dừng sạch rồi chuyển launcher. Giữ nguyên
SQL/Qdrant, dữ liệu, secret và thư viện. Ghi cấu hình cũ để quay lui nếu không đạt.
Cloudflared được chuyển riêng trong cùng kế hoạch, không trộn với nâng phiên bản.

### 3. Cho các bản cũ nghỉ đúng cách

Lập danh sách 12 scheduled task: nhiệm vụ, lịch, caller, phiên bản, bằng chứng và
đầu ra. Task đã kết thúc thì xuất cấu hình và giữ hồ sơ chốt, sau đó cho nghỉ;
task còn cần thì chuyển cùng hợp đồng/authorization phù hợp, không đổi mỗi path
sang root mới. Không giữ task thử nghiệm làm phụ thuộc vô thời hạn.

Sau khi không còn service/task/caller/evidence cần chạy từ worktree cũ: bảo toàn
commit chưa hợp nhất và ignored/untracked cần thiết rồi gỡ checkout bằng Git.
Hồ sơ lịch sử vẫn giữ phiên bản cũ để giải thích kết quả. Không ép mọi thử nghiệm
quá khứ thành bản mới nhất hoặc sửa ràng buộc quyền để bỏ worktree.

### 4. Chuẩn hóa dependency và cách bàn giao

Giữ requirements.txt làm đầu vào khai báo; tạo lock triển khai Windows từ bộ đã
kiểm chứng và thử dựng mới trước khi coi nó là chuẩn. Không ghi đè lock/snapshot
hiện có khi chưa đối chiếu. Không nâng gói trong đợt chuyển launcher.
Mỗi phát hành ghi commit nguồn, frontend build, dependency lock, service launcher
và bundle đang dùng. Người bảo trì chỉ cần một hướng dẫn để biết bản nào đang chạy.

## Điều kiện chuyển xong

- Cả ba vai trò ứng dụng cùng source commit, chat_env và cấu hình dự kiến; không
  còn service hoặc công cụ production gọi vào thư mục chiến dịch `.local` cũ.
- Cloudflared ở vị trí cố định, quyền/chức năng giữ nguyên.
- Test launcher/root resolution/stop/crash/identity và kiểm tra release đạt;
  kiểm tra quyền dữ liệu, SQL, model, auth không bị nới.
- Local/public health, đăng nhập, chat streaming, citation, history, logout đạt.
  Kiểm tra startup qua Service Manager và rollback trước khi bỏ bản cũ.
- Không còn task hoạt động phụ thuộc bản lịch sử chưa được giải thích; các task
  còn cần được xử lý theo đúng phiên bản, không ép chạy code mới.
- Thực hiện kiểm tra reboot trong cửa sổ riêng trước khi tuyên bố tự khởi động
  sau reboot được xác nhận. Không suy ra điều này từ trạng thái Auto.

## Bằng chứng khảo sát

- `.local/domain-service-20261021/service_runtime.py`: ROOT/PYTHON/PYTHONPATH,
  overlay cấu hình, native DLL order, import code ứng dụng và EXPECTED_COMMIT.
- `.local/domain-service-20261021/service_host.py`: child cwd/root, đường dẫn
  launcher hardcode và shutdown/job object.
- `.local/domain-service-20261021/configure_service.py`: từ chối command lệch,
  dùng identity riêng, yêu cầu service stopped trước cấu hình.
- `src/mech_chatbot/governance/query_activation_contract.py`: Git sạch và vị trí
  evidence worktree; `feature_activation.py`: commit và khóa xác minh trong Git.
- README phần demo/dev, Dockerfiles, docker-compose, worker_supervisor,
  CI tests workflow và tài liệu hợp nhất 29/10 chứng minh nhiều lối vận hành.
- Khảo sát host hiện tại: 4 service Running/Auto cùng bộ launcher cũ; local app
  health ok, SQL ok, worker ready. Chưa thử cutover hoặc chạy Docker trong lượt này.
