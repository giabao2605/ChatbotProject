# Kế hoạch kiểm chứng và phát hành bản sửa audit 30/10/2026

Tài liệu chuẩn bị, chưa phải lệnh triển khai hoặc phê duyệt production. Phạm vi
đã cho phép: commit cục bộ bản sửa đã kiểm tra, kiểm SQL Server bằng dữ liệu giả
trong môi trường riêng, kiểm quyền service/worker và chuẩn bị phát hành/quay lui.
Chỉ triển khai production sau một phê duyệt riêng cho đúng commit và kế hoạch này.

## Mốc và hồ sơ cần chốt

| Hạng mục | Giá trị cần ghi trước khi phát hành |
|---|---|
| Candidate mã đã kiểm tra | `e8d5e4fc8eb15ced68ac85b772707c3f79192f57` — commit local, chưa push/deploy |
| Baseline production theo runbook | `98972bfdf68f8cd41e39b3ecad7c06a15f8ef744`; phải đọc lại HEAD/service trước cửa sổ mới |
| Worktree chuẩn bị | `C:/Users/bao.nguyen/.codex/worktrees/repo-restructure/ChatBotProject` |
| Checkout phục vụ | `C:/Users/bao.nguyen/Documents/ChatBotProject` |
| Bằng chứng bản sửa | `.local/runs/audit-confirmed-fixes/`, `.local/runs/audit-dependencies/` trong worktree |
| Kiểm chứng bổ sung | `.local/runs/audit-release-prep-20261030/` trong worktree |
| Hồ sơ còn thiếu | Quyền vận hành nâng quyền, thử lock dưới service identity, bundle/approval mới, snapshot quay lui mới và kết quả smoke |

Giữ SHA mã đã kiểm tra cùng hash frontend, lock Python/npm, wheelhouse và receipt.
Không cộng lượt test trùng, không đổi tên focused checks thành CI xanh. Các con số
814 backend/kiến trúc và 86 component thuộc phạm vi trong
[báo cáo audit](repository-restructure-20261030.md), chưa chứng minh production.
Commit tài liệu sau candidate chỉ ghi nhận bằng chứng; không thay đổi mã thực thi.
Không đánh dấu các bước phát hành dưới là đã đạt trước khi thực hiện.

## Kết quả chuẩn bị đã có ngày 30/10/2026

- Đã commit bản sửa tại SHA trên, qua hook kiểm secret; chưa push. Không sửa
  checkout phục vụ, `.env`, `chat_env`, service, ACL hoặc receipt lịch sử.
- Đã tạo DB `Mech_Chatbot_Test_Audit_20261030_1a18b8d` trên
  `localhost\SQLEXPRESS`. Đây là DB tách biệt **cùng instance**, không phải máy
  hoặc SQL instance staging riêng. Chỉ có schema và dữ liệu giả; không sao hoặc
  truy cập dữ liệu ứng dụng `Mech_Chatbot_DB`. DB test được giữ lại, chưa xóa.
- Schema baseline và V0001–V0047 đã dựng trong DB test. V0048 chạy bằng migration
  runner thật hai lần: ledger chỉ có một entry, ba cột nullable, legacy giữ NULL,
  dữ liệu JSON hỏng bị SQL từ chối, thiếu migration báo lỗi rõ và record legacy
  không được mở. Mười assertion này nằm trong
  `sql-migration-verification.log`; log vẫn giữ lỗi fixture FileName xảy ra sau
  các assertion, không được diễn giải thành toàn bộ script đã pass.
- Sau sửa fixture FileName và tạo site HQ giả hợp lệ, HTTP/SQL đạt **17/17**:
  reviewer đúng phạm vi list/classify/promote/regression; golden cần đủ nguồn;
  thu hồi quyền nguồn thứ hai ẩn cả feedback/golden/run cũ; classify/delete trả
  cùng 404 như ID không tồn tại và không ghi; đổi hash nguồn ẩn feedback/run cũ.
  Receipt: `sql-verification.json`, log `sql-http-final.log`. Các log lỗi fixture
  cũ được giữ. Actor/quyền và transaction dùng SQL thật; session được thay bằng
  dependency actor đã xác thực, stream dùng fake. Chưa kiểm login thật, provider
  hoặc Qdrant. Guard mạng chỉ cho loopback ephemeral dùng bởi test harness.
- Audit mới: `pip-audit.json` không có lỗ hổng trên 278 package trong môi trường
  được khóa (gồm công cụ test/audit),
  `npm-audit.json` không có lỗ hổng trong graph 202 package, gồm development.
  Không cài vào runtime production. Kết quả theo advisory tại thời điểm chạy.
- `candidate-manifest.json` ghi SHA256 hai lock Python, npm lock, migration và
  toàn bộ tám file frontend build đã kiểm tra. Đây là manifest kiểm chứng local,
  không phải approval hoặc deployment receipt. V0048 SHA256:
  `20a5a4797f6703cc494987cd7092ad2318178cc92e8f2aee0a0028c031626a5a`.
- Probe không nâng quyền hiện trả Windows error 5 khi mở quyền start/stop bốn
  service và quyền tạo service thử. Đã gửi người vận hành script chỉ mở handle,
  đọc trạng thái và ghi `service-permissions.json`; chưa nhận receipt nâng quyền.
  Script không start/stop/create service hay sửa ACL. Quyền của token admin không
  tự nâng quyền tiến trình Codex hiện tại.
- ACL đọc được tại `C:/ProgramData/MechRAGServices/worker-heartbeat` cấp Modify
  cho `NT SERVICE\MechRAGWorker`, Full cho SYSTEM/Administrators/người vận hành,
  App chỉ RX. Test cạnh tranh process trong thư mục tạm đã có ở bộ test bản sửa;
  chưa mở/khóa file thử dưới chính service identity hoặc chạm lock/heartbeat thật.

Snapshot lịch sử của lượt đưa `98972bf` vào phục vụ nằm tại
`C:/ProgramData/MechRAGServices/restructure-deploy-c2859a0f16e140b2a7c1d9c3ee5cce45/`.
Đó là bằng chứng của lượt cũ; cần snapshot mới bảo toàn trạng thái đang phục vụ
trước cửa sổ audit. Chưa diễn tập restore hoặc chứng minh baseline chạy lại với
schema V0048 trên SQL test. Các mục này vẫn là điều kiện trước phát hành.

## Kiểm chứng riêng trước khi xin triển khai

1. Chốt working tree và diff thuộc bản sửa; giữ nguyên WIP không liên quan. Commit
   cục bộ rồi ghi candidate SHA. Nếu tích hợp tạo commit khác, cấp release và kiểm
   phần thay đổi theo SHA cuối cùng; không dùng bundle của SHA trước.
2. Dùng SQL Server/DB riêng với dữ liệu giả, xác minh server và `DB_NAME()` trước
   mọi ghi. Không sao DB thật hoặc credential vào Git. Dựng schema tiền V0048,
   ghi nhận các bảng/cột và số hàng giả. Chạy migration runner hiện có với cấu
   hình test riêng; `--dry-run --target V0048` phải liệt kê đúng phần còn thiếu.
   Runner áp mọi migration pending đến target, không chỉ riêng V0048; dừng nếu
   danh sách ngoài phạm vi. Sau áp dụng, kiểm `_SchemaVersions`, ba cột nullable
   và ba CHECK JSON; chạy lại phải không còn pending. JSON hỏng phải bị từ chối,
   hàng cũ vẫn NULL và dữ liệu giả trước đó không bị sửa/xóa.
3. Trên DB giả, chạy repository/reviewer thực tế: người đủ quyền lưu/đọc được;
   nguồn thứ hai ngoài quyền, thu hồi quyền, đổi nguồn/version/hash/manifest và
   legacy NULL phải bị chặn. Kiểm transaction không để sót bản ghi khi từ chối;
   thiếu schema phải lỗi rõ. Không gọi model hoặc tạo nguồn nghiệp vụ thật.
4. Kiểm quyền Service Manager query/change/start/stop bằng chứng quyền thực tế;
   không start/stop production để thử. Đọc identity, command, recovery/start mode
   và ACL cần thiết; không in overlay/token. Không tự sửa ACL khi Access denied.
   Test dưới identity riêng chỉ chứng minh identity đó, không thay bằng chứng
   của ba `NT SERVICE` production.
5. Kiểm lock bằng subprocess trong vùng test riêng: hai worker khác cwd dùng
   chung lock, chỉ owner dựng runtime/chạm heartbeat; loser thoát 73; process
   kết thúc nhả lock. Kiểm ACL thư mục và file hiện có ở
   `C:/ProgramData/MechRAGServices/worker-heartbeat` bằng đọc quyền; kiểm mở/khóa
   dưới service identity phải dùng file thử riêng trong cửa sổ được phép, tuyệt
   đối không giữ lock thật trong khi worker production chạy. Không xóa lock file.
6. Dựng dependency theo lock candidate vào venv riêng, giữ đủ wheel vá và hash;
   đối chiếu receipt offline/pip check/import đã có theo
   [runbook dependency](runtime-dependencies.md). Dùng frontend build đã kiểm tra
   từ candidate, ghi hash `index.html` và assets. Không cài vào `chat_env` live.

SQL synthetic và test lock không phải diễn tập restore production hoặc bằng
chứng ingestion thật hoàn tất. Không cần chạy lại toàn suite nếu code không đổi.

## Các chốt đang chặn phát hành

- Chưa có approval production mới, bundle ký cho candidate, receipt quyền thực
  tế và smoke live của candidate. Không tái dùng approval/chữ ký lịch sử cho SHA mới.
- Chưa có cơ chế drain production đã xác minh: cần ngừng mọi nguồn nhận việc mới
  và chứng minh request/job đang chạy đã kết thúc. Heartbeat/TCP rỗng không đủ.
  Host dừng sau 110 giây, HTTP graceful 90 giây; không dùng timeout để thay drain.
- `identity_preflight()` cố định DB `Mech_Chatbot_DB` và từng `NT SERVICE` identity;
  không sửa validator để làm staging DB khác tên được báo đạt. Probe kiểm ghi
  thư mục role/data nhưng chưa kiểm riêng `worker-heartbeat`/lock, nên cần bằng
  chứng ACL/lock bổ sung. Service probe thoát sạch chưa đủ: phải đọc `ok`, nonce,
  request hash, SHA và toàn bộ checks trong receipt.
- `build_activation_bundle.py` hiện tạo v1–v7; runtime nhận v8 qua validator riêng.
  Phải tìm và kiểm quy trình cấp v8 thực tế/authority được phép trước cửa sổ;
  không tự sửa JSON hoặc ký thay người duyệt để bù thiếu công cụ/hồ sơ.
- Service runtime cố định `chat_env/Scripts/python.exe`. Venv audit chỉ là môi
  trường kiểm chứng. Cần chốt cách thay dependency và phục hồi nguyên bộ
  `chat_env` khi service đã dừng; không trỏ service tùy tiện sang venv thử.
- Toàn bộ worker trên cùng host phải dùng mã khóa mới và cùng ProgramData.
  Kiểm service, supervisor, scheduled task và CLI khác checkout; worker lịch sử
  hoặc host khác không bị khóa này bảo vệ. Có worker cũ chưa xử lý thì dừng.

## Trình tự triển khai sau phê duyệt riêng

1. Đọc lại baseline/dirty state/identity/command, ghi deployment ID, candidate SHA,
   bundle SHA256, profile, feature grants và versions được duyệt. Giữ nguyên
   phạm vi feature hiện hành. Tạo snapshot được bảo vệ ngoài Git gồm code cũ,
   frontend thực tế đang phục vụ, dependency/wheelhouse cũ, service config/ACL/
   recovery và lựa chọn bundle/config cũ. Không chỉ lưu tên file hoặc lock.
2. Xác minh người vận hành có quyền khôi phục, bản backup DB có receipt và cách
   phục hồi đã chuẩn bị. Không tự diễn tập restore trên DB thật. Chốt phương án
   bảo toàn vector trước khi cho phép nhập lại tài liệu thật; xem giới hạn dưới.
3. Ngừng nhận việc mới, chờ hết request/job rồi dừng sạch App/RAG/Worker qua
   Service Manager. Không đổi Cloudflared trong bản sửa này. Không nâng số worker.
4. Dưới principal migration được phép, kiểm DB đích/pending rồi áp V0048 trước
   khi mở luồng mới. Không cấp `db_owner`/sysadmin cho service để chạy migration.
   Kiểm cột/constraint/version; không suy đoán hoặc backfill bằng chứng legacy.
5. Cập nhật checkout đến đúng candidate; đưa frontend build và dependency đã
   chốt vào vị trí phục vụ khi mọi process sử dụng đã dừng. Giữ native DLL load
   order và host Python. Chọn bundle mới, deployment SHA/hash đồng nhất qua cơ
   chế cấu hình hiện hành. Chỉ thay cấu hình cần cho release, giữ dữ liệu/secret.
   Giữ command `scripts/windows_service/service_host.py` đang có: `--relocate`
   chỉ chuyển vị trí legacy/tracked, không phải thao tác nâng bản audit.
6. Tạo request probe mới có nonce/thời hạn/ACL đúng cho từng role, chạy qua đúng
   service identity từ checkout production. Thu và kiểm receipt theo
   [runbook service](windows-service-operations.md), gồm Windows/SQL identity,
   release/activation, model cache, UI và quyền ghi. Kiểm worker lock riêng như
   trên. Gỡ đúng request của lượt này sau thu receipt; request còn lại khiến host
   vào probe thay vì phục vụ. Không bỏ qua probe bằng biến môi trường tin cậy.
7. Khởi động qua Service Manager; kiểm health local/public `status=ok`, `db=ok`,
   `ingestion_worker=ready`; RAG health xác thực phải đúng SHA, deployment ID,
   bundle hash, profile/feature set và `live_authorized=true`. Dùng
   `production_preflight.py` với expected SHA/profile/deployment/bundle và feature
   set thực tế khi phù hợp; `--health-only` không thay kiểm migration/SQL/quyền.
   Full preflight yêu cầu `default_rollout`, không dùng controlled demo để báo
   production-ready.
8. Một lượt tài khoản thử sẵn có: login, câu hỏi SSE có citation, mở nguồn, history,
   logout; reviewer/role/regression theo quyền đã cấp. Không tạo/reset tài khoản,
   xóa hội thoại hoặc nhập lại tài liệu thật để làm đẹp receipt. Nếu cho phép
   ingest thử riêng, cần nguồn thử/đích/việc giữ dữ liệu đã thống nhất và receipt
   job terminal; heartbeat ready chỉ chứng minh worker sống. Thu kết quả, lỗi và
   giới hạn, chỉ mở lại traffic khi đủ các điều kiện được duyệt.

## Quay lui khi điều kiện hoặc smoke không đạt

Ngừng nhận việc mới, chờ hết việc và dừng sạch trước khi phục hồi. Quay lại đúng
baseline đã chụp của cửa sổ này cùng frontend, môi trường dependency và bundle/
deployment config tương ứng; không trộn code cũ với UI/dependency/bundle mới.
Giữ command tracked hiện hành nếu không đổi trong đợt này; không gọi
`--relocate --rollback` để trỏ về launcher legacy thiếu file. Khôi phục riêng
cấu hình đã thay đổi từ snapshot được bảo vệ, dừng đối chiếu nếu drift.

V0048 là migration cộng thêm: giữ ba cột, constraint, `_SchemaVersions` và mọi
dữ liệu đã ghi. Không DROP, xóa hàng hoặc backfill NULL. Kiểm baseline đọc được
schema cộng thêm trên SQL synthetic trước cửa sổ; nếu chưa kiểm được, ghi chặn
quay lui thay vì giả định tương thích.

Rollback code ingestion sau khi xóa vector cũ **không khôi phục vector cũ**.
Cơ chế hiện có chỉ dọn vector mới theo DocID và phục hồi metadata; không có
snapshot vector mới được bổ sung trong bản sửa. Nếu đã đổi dữ liệu thật, cần
phương án restore dữ liệu đã duyệt riêng; không xóa collection/reindex tự động.
Nếu không có phương án này, chưa cho phép phép thử nhập lại tài liệu thật.

Gỡ request probe của lượt chuyển, start baseline qua Service Manager rồi kiểm
health/identity/release và web như trên. Lưu cả receipt thất bại và phục hồi;
không coi unit test hoặc kế hoạch này là diễn tập rollback thật. Khi quay về
worker cũ, ràng buộc single instance mới không còn: giữ đúng một worker, ngừng
mọi launcher phụ và không dựa vào lock mới để bảo vệ baseline.

Chưa kiểm: service identity/lock thực tế, tải dài, reboot, restore DB/vector thật
và deploy production. SQL test đạt không thay bằng chứng các điều kiện này.
