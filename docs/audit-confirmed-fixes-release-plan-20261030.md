# Kế hoạch kiểm chứng và phát hành bản sửa audit 30/10/2026

## Kết quả lượt triển khai đã được duyệt

Chủ dự án đã duyệt ký/deploy đúng `002a9d3`, giữ dữ liệu và xác nhận không có
người/tác vụ ngoài gửi việc mới trong thời gian bảo trì. Release đã ký hợp lệ,
giữ nguyên grants/versions; `main` local đã fast-forward đến đúng SHA, chưa push.

- Cutover đã đạt health lúc **16:48:58 ngày 30/10/2026 (Asia/Saigon)**. Ba service
  đạt probe dưới đúng Windows/SQL identity, SHA và bundle; local/public HTTP 200,
  SQL OK, worker ready. Bundle SHA256:
  `970f5a8094bc2286a0244160b2de0754d2ca4d669fe5f328bd7855a5461be961`.
- SQL backup mới dùng COPY_ONLY/CHECKSUM và RESTORE VERIFYONLY đạt. V0048 đã áp
  dụng; số hàng nghiệp vụ và hash nội dung cũ của ba bảng liên quan không đổi.
  6.411 file dữ liệu/tài liệu/hồ sơ đã đối chiếu không đổi trước khởi động.
  Môi trường Python, frontend và cấu hình cũ được giữ, không xóa dữ liệu.
- API smoke đạt login/chat có citation/mở nguồn/history chứa câu hỏi/logout;
  frontend local/public khớp gói. Tuy nhiên tiêu chí API history quá yếu: chỉ
  kiểm câu hỏi xuất hiện, chưa kiểm câu trả lời và citation còn đọc được.
- **Browser smoke FAILED**: chat mới `2269` bị ẩn khi nạp lịch sử. SQL không có
  `ChatEvidenceManifest`/`AnswerEvidence` cho lượt này. Đã xác nhận nguyên nhân:
  hai đoạn văn bản gốc chứng nhận được, một `title_block` sinh từ metadata không
  chứng nhận được; adapter chat thường bỏ qua lỗi lưu bằng chứng. Đây là lỗi
  của candidate, không phải bằng chứng quyền nguồn vừa bị thu hồi.
- **Chưa chấp nhận triển khai**. Đã khởi chạy yêu cầu quay lui theo plan; tại lúc
  ghi mục này còn chờ UAC, chưa có receipt quay lui hoàn tất. Không suy ra đã
  quay lui từ receipt `complete_health_verified` của cutover. Mã sửa tiếp theo
  chỉ chuẩn bị local; cần duyệt release mới trước triển khai commit khác.
- Bản sửa nhỏ đã chuẩn bị local ở ba file mã: bỏ riêng `title_block` không chứng
  nhận được trước generation, truyền tập nguồn đã lọc tới cả nhánh từ chối, và
  báo lỗi khi lưu bằng chứng thất bại ở chat thường. Không hạ kiểm quyền, không
  loại bằng chứng sau khi đã sinh đáp án. TDD tái hiện ba lỗi; lượt cuối **7/7
  đạt** theo terminal, review độc lập không còn điểm chặn trong diff. Cấu hình và
  danh sách case nằm trong `HISTORY-TITLE-green2-options.json` và
  `HISTORY-TITLE-green2-collected.json` tại `.local/runs/audit-confirmed-fixes/`.
  Chưa commit/deploy.
  Test chứng minh nội dung lịch sử/ký hiệu nguồn, chưa thay browser smoke của
  bản sửa. Câu hỏi chỉ dựa vào metadata tiêu đề chưa chứng nhận có thể bị từ chối.

Receipt cutover, backup và bảo toàn:
`C:/ProgramData/MechRAGServices/audit-deploy-9052ee9b17cf4eb189d6f3bc53aab8af/`.
Kết quả web và trạng thái cuối của gate:
`.local/runs/audit-deploy-20261030/postdeploy-gate.json`, `website-after.json`,
`browser-failure.png` trong worktree. Bản backup SQL được ghi trong
`sql-backup-migration.json`; không sửa/xóa snapshot hoặc receipt lịch sử.

Các phần bên dưới giữ lại kế hoạch và bằng chứng trước phê duyệt, không thay
trạng thái lượt triển khai vừa nêu.

Tài liệu chuẩn bị, chưa phải lệnh triển khai hoặc phê duyệt production. Phạm vi
đã cho phép: commit cục bộ bản sửa đã kiểm tra, kiểm SQL Server bằng dữ liệu giả
trong môi trường riêng, kiểm quyền service/worker và chuẩn bị phát hành/quay lui.
Chỉ triển khai production sau một phê duyệt riêng cho đúng commit và kế hoạch này.

## Chốt tiếp tục ngày 30/10/2026

Phần sửa 16 lỗi và kiểm tra tập trung đã hoàn tất. Chat tiếp tục chỉ chốt hồ sơ
phát hành; không mở rộng sang audit host, thử token/quyền mới hoặc chạy lại suite.
Chat trước đã dừng, chưa triển khai bản sửa.

| Việc | Trạng thái và bước tiếp theo |
|---|---|
| Mã sửa và kiểm tra | `e8d5e4f`; có receipt 814 backend/kiến trúc, 86 component và 17 HTTP/SQL giả đạt. Tái sử dụng kết quả, không chạy lại khi mã không đổi. |
| Commit dự kiến phát hành | `002a9d37f8fcc62916a0c33742878e494ccad55e`; so với mã đã kiểm tra chỉ khác ba file tài liệu. Bundle phải ký cho SHA này. |
| Gói phát hành/quay lui | `packet-002a9d37` đã có code, frontend, lock của cả hai bản và 280 wheel. Chat tiếp tục đã kiểm lại hash/kích thước đủ 286 file, hash manifest/draft và nội dung hai archive frontend. |
| Tương thích đọc khi quay lui | 10 kiểm tra SQL giả đạt cho baseline với V0048. Chưa chứng minh ghi, restore hoặc service baseline khởi động lại. |
| Quyền Windows | Dùng lại receipt service/token đã có. Không yêu cầu thêm kiểm kê toàn host hoặc thử quyền để chốt hồ sơ. |
| Còn cần chủ dự án duyệt | Đúng SHA, phạm vi giữ nguyên, ký release và cửa sổ bảo trì. Draft chưa ký, chưa có approval production. |
| Làm trong cửa sổ được duyệt | Xác nhận ngừng nhận việc/chờ hết việc, snapshot mới, dừng sạch, migration, thay code/UI/dependency, probe và smoke. Nếu không xác minh được hết việc thì dừng trước khi stop service. |

Gói và receipt nằm trong `.local/runs/audit-release-finalize-20261030/` của
worktree. `release-request.draft.json` và `release-request-validation.json`
khớp manifest; `signing_performed=false`, chữ ký/review chưa hợp lệ là trạng thái
đúng của bản nháp, không phải lỗi cần sửa bằng cách tự duyệt. Giữ Query, Graph,
Grounded Math và các version/phạm vi hiện hành; không mở thêm feature hoặc ghi
nhận nghiệm thu nghiệp vụ. Việc cấp release dùng authority hiện có sau phê duyệt.

Bản cập nhật tài liệu này chưa commit và không thay gói đã ghim `002a9d3`.
Nếu chọn một SHA mới để phát hành, phải cập nhật gói/draft và cấp release cho SHA
mới; không âm thầm ký SHA HEAD khác với gói được duyệt. Tích hợp vào `main` phải
giữ đúng SHA đã chọn; nếu phát sinh thay đổi mã thì kiểm lại đúng phần bị ảnh hưởng.

## Mốc và hồ sơ cần chốt

| Hạng mục | Giá trị cần ghi trước khi phát hành |
|---|---|
| Candidate mã đã kiểm tra | `e8d5e4fc8eb15ced68ac85b772707c3f79192f57` — commit local, chưa push/deploy |
| Commit/gói dự kiến phát hành | `002a9d37f8fcc62916a0c33742878e494ccad55e` — mã thực thi không đổi so với candidate trên |
| Baseline production theo runbook | `98972bfdf68f8cd41e39b3ecad7c06a15f8ef744`; phải đọc lại HEAD/service trước cửa sổ mới |
| Worktree chuẩn bị | `C:/Users/bao.nguyen/.codex/worktrees/repo-restructure/ChatBotProject` |
| Checkout phục vụ | `C:/Users/bao.nguyen/Documents/ChatBotProject` |
| Bằng chứng bản sửa | `.local/runs/audit-confirmed-fixes/`, `.local/runs/audit-dependencies/` trong worktree |
| Kiểm chứng bổ sung | `.local/runs/audit-release-prep-20261030/` trong worktree |
| Gói phát hành và kiểm tương thích baseline | `.local/runs/audit-release-finalize-20261030/` trong worktree |
| Hồ sơ còn thiếu trước deploy | Bundle/approval mới, snapshot quay lui mới, kiểm đường dẫn/khởi động production và kết quả smoke |

Giữ SHA mã đã kiểm tra cùng hash frontend, lock Python/npm, wheelhouse và receipt.
Không cộng lượt test trùng, không đổi tên focused checks thành CI xanh. Các con số
814 backend/kiến trúc và 86 component thuộc phạm vi trong
[báo cáo audit](repository-restructure-20261030.md), chưa chứng minh production.
Commit tài liệu sau candidate chỉ ghi nhận bằng chứng; không thay đổi mã thực thi.
Không đánh dấu các bước phát hành dưới là đã đạt trước khi thực hiện.

## Kết quả chuẩn bị đã có ngày 30/10/2026

- Đã commit bản sửa tại SHA trên, qua hook kiểm secret; chưa push. Không sửa
  checkout phục vụ, `.env`, `chat_env`, service, ACL production hoặc receipt lịch sử.
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
- Probe thường ban đầu trả Windows error 5. Receipt `service-permissions.json`
  được người vận hành chạy bằng quyền admin lúc **15:45 ngày 30/10/2026 (Asia/Saigon)**
  xác nhận query/start/stop cả bốn service và quyền tạo service thử đều đạt;
  cả bốn vẫn Running, ba service MechRAG dùng đúng virtual account, Cloudflared
  dùng LocalSystem. Script chỉ mở handle: không start/stop/create service hoặc
  sửa ACL. Chưa kiểm quyền change-config. Token admin không tự nâng quyền tiến
  trình Codex hiện tại.
- ACL đọc được tại `C:/ProgramData/MechRAGServices/worker-heartbeat` cấp Modify
  cho `NT SERVICE\MechRAGWorker`, Full cho SYSTEM/Administrators/người vận hành,
  App chỉ RX. Test cạnh tranh process trong thư mục tạm đã có ở bộ test bản sửa.
  Không tạo/mở/khóa file lock production hoặc sửa heartbeat thật.
- Phép thử token worker chạy bằng admin lúc 15:49 dừng tại `OpenProcessToken`
  với error 5, trước khi tạo thư mục/file thử. `worker-identity-permissions.json`
  giữ kết quả thất bại; không coi quyền quản lý service là quyền đọc token của nó.
  Helper tự kiểm tra bằng token người dùng hiện tại đạt, chỉ đủ kiểm harness.
  Sau đó người vận hành đồng ý và chạy wrapper `run_worker_identity_probe.ps1`
  qua một Scheduled Task SYSTEM tạm lúc **15:55 (Asia/Saigon)**. Task không có
  trigger lặp, hash helper được khóa, có deadline và receipt riêng từng lượt.
- Phép thử SYSTEM đã lấy đúng token của worker đang chạy (PID 10564, SID
  `S-1-5-80-2380487211-1910894509-36140916-3467823900-1131910825`) rồi impersonate
  chỉ trong helper: tạo/đọc/ghi và lấy khóa file mới trên thư mục thử có DACL
  sao chép đúng từ thư mục production; lần lấy khóa bằng handle thứ hai bị chặn;
  thả rồi lấy lại khóa đạt, file khóa được giữ. Token/privilege/handle được trả
  lại; PID và Running của worker thật giữ nguyên trước/sau. Receipt:
  `worker-identity-system-54b395b938264f9db913b3224d62d25d.json`.
- Receipt wrapper cùng lượt `54b395b938264f9db913b3224d62d25d` xác nhận task exit 0,
  `ok=true`, `task_removed=true`; đối chiếu Task Scheduler không còn task có
  tiền tố `MechRAGAuditLockProbe_`. Chỉ ACL/file trong thư mục test được tạo;
  không start/stop production service, không truy cập SQL/Qdrant/provider ở phép
  thử này. Kết quả chứng minh quyền token service trên bản sao DACL, không thay
  kiểm startup worker candidate, đường dẫn production, file lock production có
  ACL riêng hoặc cạnh tranh/crash giữa hai process dưới token service. Kiểm hai
  process/crash dùng token người vận hành đã đạt trong bộ test bản sửa.

Snapshot lịch sử của lượt đưa `98972bf` vào phục vụ nằm tại
`C:/ProgramData/MechRAGServices/restructure-deploy-c2859a0f16e140b2a7c1d9c3ee5cce45/`.
Đó là bằng chứng của lượt cũ; cần snapshot mới bảo toàn trạng thái đang phục vụ
trong cửa sổ audit trước khi thay đổi production. Receipt
`baseline_sql_compat_result.json` đã xác nhận 10 kiểm tra đọc của baseline trên
DB giả có V0048 đạt và dữ liệu kiểm tra không đổi. Chưa diễn tập restore, kiểm
tương thích ghi hoặc khởi động baseline qua service; không gọi kết quả đọc này
là rollback đã thử thành công.

## Phạm vi kiểm chứng đã dùng trước khi xin triển khai

Các bước dưới giữ phạm vi kiểm chứng ban đầu; trạng thái đạt và giới hạn phải
đọc theo receipt ở trên, không tự yêu cầu chạy lại. Giới hạn còn lại đã tách
trong phần chốt tiếp tục và trình tự triển khai.

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

- Chưa có approval production mới, bundle ký cho candidate và smoke live của
  candidate. Quyền quản lý service bằng token admin và thao tác lock bằng token
  worker trên DACL replica đã có receipt; không tái dùng approval/chữ ký lịch sử
  cho SHA mới. Kiểm đường dẫn/file lock thật trong cửa sổ triển khai vẫn còn.
- Chưa có cơ chế drain production đã xác minh: cần ngừng mọi nguồn nhận việc mới
  và chứng minh request/job đang chạy đã kết thúc. Heartbeat/TCP rỗng không đủ.
  Host dừng sau 110 giây, HTTP graceful 90 giây; không dùng timeout để thay drain.
- `identity_preflight()` cố định DB `Mech_Chatbot_DB` và từng `NT SERVICE` identity;
  không sửa validator để làm staging DB khác tên được báo đạt. Probe kiểm ghi
  thư mục role/data nhưng chưa kiểm riêng `worker-heartbeat`/lock, nên cần bằng
  chứng ACL/lock bổ sung. Service probe thoát sạch chưa đủ: phải đọc `ok`, nonce,
  request hash, SHA và toàn bộ checks trong receipt.
- `build_activation_bundle.py` hiện tạo v1–v7; runtime nhận v8 qua validator riêng.
  Đã tìm thấy luồng v8 của lần triển khai trước tại
  `.local/runs/restructure-deploy-20261030/issue_release.py`, dùng signer hiện có.
  Script lịch sử ghim baseline và chỉ dẫn cũ nên không chạy nguyên trạng cho bản
  audit. Draft mới đã chuẩn bị; sau phê duyệt mới cấp release đúng SHA/chỉ dẫn
  mới và xác minh chữ ký bằng validator hiện có, không dùng lại approval cũ.
- Service runtime cố định `chat_env/Scripts/python.exe`. Venv audit chỉ là môi
  trường kiểm chứng. Packet đã chốt wheel/lock cho cả candidate và baseline:
  sau khi dừng sạch, bảo toàn nguyên `chat_env` cũ rồi dựng môi trường candidate
  ngay tại đường dẫn `chat_env` cuối bằng wheel đã khóa hash. Nếu thất bại, giữ
  môi trường lỗi và phục hồi nguyên môi trường cũ về đường dẫn ban đầu; không
  chạy venv đã chuyển chỗ hoặc trỏ service sang venv audit. Snapshot/cấu hình
  riêng được bảo vệ vẫn phải thu trong cửa sổ, packet không thay bước này.
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
dữ liệu đã ghi. Không DROP, xóa hàng hoặc backfill NULL. Baseline đã đọc được
schema cộng thêm trong 10 kiểm tra SQL giả nêu trên; tương thích ghi và phục hồi
service chưa được chứng minh.

Quay về `98972bf` cũng đưa trở lại các lỗi phân quyền/nguồn đã xác nhận. Kết quả
SQL chỉ chứng minh đọc được schema, không chứng minh an toàn phân quyền. Nếu
phải quay lui, giữ hệ thống trong bảo trì cho đến khi người vận hành chốt phạm
vi sử dụng; không tự mở lại cho reviewer chỉ vì health đã đạt.

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

Chưa kiểm: khởi động candidate qua service, file lock production, tải dài, reboot,
restore DB/vector thật và deploy production. SQL test và token/ACL replica đạt
không thay bằng chứng các điều kiện này.
