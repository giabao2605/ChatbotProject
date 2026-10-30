# Sắp xếp repository — 30/10/2026

> Hồ sơ bên dưới ghi thời điểm chuẩn bị restructure. Sau đó receipt tại
> `C:/ProgramData/MechRAGServices/restructure-deploy-c2859a0f16e140b2a7c1d9c3ee5cce45/`
> xác nhận đã triển khai `98972bf`. Xem [runbook hiện hành](windows-service-operations.md).
> Bản sửa audit tiếp theo trong worktree chưa triển khai; các kết quả kiểm chứng
> lịch sử dưới đây không được dùng làm kết quả cho bản sửa đó.

## Kết quả và giới hạn

Candidate nằm trên nhánh `codex/repo-restructure`, tại
`C:/Users/bao.nguyen/.codex/worktrees/repo-restructure/ChatBotProject`.
Đã sắp xếp mã và công cụ; chưa merge/push candidate, chưa triển khai hoặc khởi động
lại website. Không thay API, schema database, package `mech_chatbot`, dependency
versions hoặc quyền truy cập.

Bản gốc `59d0c98bd2c1ed05c00db3566a6ab0aecb2adff1` đã được push lên GitHub `main`
và tag [`backup/pre-restructure-20261030`](https://github.com/giabao2605/ChatbotProject/tree/backup/pre-restructure-20261030).
Đã xác minh tag giải tham chiếu đúng SHA. Tag chỉ giữ mã nguồn; dữ liệu, credential,
môi trường Python và bộ phát hành local vẫn nằm nguyên tại checkout phục vụ.

## Bảng chuyển và xóa

| Cũ | Candidate |
|---|---|
| `src/mech_chatbot` | `backend/src/mech_chatbot` |
| `tests` | `backend/tests` |
| `mech_chatbot_tests_layered` | `backend/tests_layered` |
| Sáu file `requirements*.txt` ở gốc | `backend/requirements`, giữ nguyên nội dung |
| `web-ui` | `frontend` |
| `doichieukientruc.docx` | `docs/doichieukientruc.docx` |

Xóa đúng bốn file cấu hình không dùng: `Dockerfile`, `Dockerfile.app`,
`docker/docker-compose.yml`, `.streamlit/config.toml`; bỏ hướng dẫn Docker trong
README và demo runbook. Không gỡ package Streamlit khỏi lock hoặc runtime.
Các thư mục local rỗng, worktree cũ, corpus và cache chưa đủ bằng chứng không bị xóa.

Giữ `data`, `src/data`, `eval`, `database`, `reports`, logs và các bản vẽ nguyên vị trí.
Ảnh citation cũ và vision cache `src/data/cache/vision` vẫn được hỗ trợ. Không sửa
JSON/manifest/hồ sơ lịch sử có hash. Công cụ cleanup bảo vệ cả layout cũ và mới.
Hai launcher gốc vẫn dùng được; các công cụ nhận checkout lịch sử chọn source root
bằng sự tồn tại của package. Windows launcher trong candidate trỏ `backend/src`
và `frontend/dist`; không chạy cấu hình service từ worktree này.

Cấu hình `.vscode/settings.json` local ở cả hai checkout chỉ ẩn môi trường,
dependency, cache và output. Đây là file ignored; không xóa hoặc di chuyển dữ liệu.

## Kiểm chứng

Receipt nằm trong `.local/runs/repo-restructure-20261030` của candidate, không commit
log thô. Môi trường Python riêng được cài offline bằng lock có hash và wheelhouse
đã lưu; `pip check` đạt. Không dùng system-site-packages hoặc hardlink tới live venv.

| Kiểm tra | Kết quả |
|---|---|
| Dependency files | 6/6 giữ nguyên nội dung; không nâng/gỡ gói |
| Regression chuyển cấu trúc | 15/15 PASS, gồm source/data/static paths, cleanup và caller ngoài pytest |
| Main test collection | 7.491 ca gốc còn đủ, thêm 15 ca; tổng 7.506 |
| Backend baseline và candidate | Cùng 909 PASS, 3 SKIP, 21 deselected; dừng cùng một lỗi có sẵn |
| Layered unit/security | 396 PASS, 1 SKIP, 16 deselected; collection đủ 413 ca |
| Kiến trúc/L0 | 22 PASS; direct L0 từ thư mục ngoài repo: 4 PASS |
| Frontend | 403 PASS; line 96,53%, branch 90,39%; build đạt |
| Browser fixture E2E | 4 ca đạt: auth restoration/navigation, 2 branch history, history-loading |
| Giữ mã giao diện | 81/82 file frontend/src giữ nguyên; UsersView chỉ thêm dấu quote cho key nhãn UI, build lại đạt |
| Review độc lập | Hai lỗi caller đường dẫn được sửa; không còn blocker được tìm thấy trong phạm vi review |
| Website hiện hành | Health local `ok`; 4 service giữ PID, state và command; serving checkout sạch tại `59d0c98` |

So sánh node ID chuẩn hóa tiền tố thư mục mới và đúng một tham số expiry được sinh
bằng thời gian hiện tại trong test cũ; không loại bỏ test để làm khớp số lượng.

Lỗi backend có sẵn:
`test_chat_history_snapshot.py::test_http_history_revision_links_apply_to_ordinary_and_branch_records[ordinary]`
kỳ vọng page URL nhưng nhận `None`. Không sửa logic history/citation ngoài phạm vi.
Lượt kiểm tra dừng theo `--maxfail=1`; không có báo cáo coverage toàn suite hợp lệ,
gate coverage không đạt. Giữ nguyên ngưỡng 80% dòng và 80% nhánh trong CI;
không dùng coverage của kiểm tra nhỏ để thay cho toàn backend. Không báo CI xanh.

Frontend có một timeout 5 giây ở ca focus trong lượt đầu; chạy lại một worker đạt
403/403, không tăng timeout hoặc sửa component. Ba test cũ được đồng bộ với hợp
đồng đã có trong backup: alias tên test kiến trúc ở layered, thông báo lỗi history
trong E2E, và refusal không tiết lộ lý do nội bộ. Không nới kiểm tra bảo mật.

Hook ECC báo nhầm một biến fixture và nhãn UI là credential sau khi chuyển file.
Đã đổi tên biến fixture (giữ nguyên chuỗi mẫu và assertion, 26 test scanner đạt)
và quote key `password` của nhãn giao diện (giữ nguyên giá trị/hành vi, build đạt).
Không tắt hoặc sửa hook của người dùng.

Các kiểm tra offline dùng môi trường đã bỏ credential production và chặn socket
ngoài loopback cùng các cổng live 8080/8100 ở process kiểm tra. Đây không phải
firewall cho mọi thư viện native hoặc subprocess. Integration/eval live bị tắt;
không cấu hình staging SQL/Qdrant/provider hoặc dùng credential thật.
Browser chỉ phục vụ build candidate trên 127.0.0.1:18080, đối chiếu bytes index.html,
API được fixture chặn; preview đã dừng sau kiểm tra. Không coi đây là bằng chứng
đăng nhập/SQL/chat provider/citation thực hoặc production readiness.

Audit dependency giữ nguyên các advisory đã có: npm 4 package (1 high, 3 moderate),
Python `langgraph-sdk` và `tornado` với tổng 4 advisory. Không tự nâng dependency
trong đợt đổi cấu trúc; audit này không sạch. Scan các dòng mới không phát hiện
mẫu secret; không đọc credential production để kiểm tra.

## Lệnh hiện hành và quay lui

- Python source: đặt `PYTHONPATH=backend/src` khi chạy module; root launchers tự thêm đường dẫn.
- Main tests: `python -m pytest -m "not integration and not eval"` từ repo root.
- Layered: `python -m pytest -c backend/tests_layered/pytest.ini backend/tests_layered -m "unit or security"`.
- Frontend: `npm --prefix frontend run test:coverage` và `npm --prefix frontend run build`.
- E2E cần `E2E_BASE_URL` tường minh; health RAG live cần thêm `E2E_RAG_BASE_URL`.
  Chỉ dùng app/RAG và dữ liệu thử riêng; xem README về proxy `DEV_API_TARGET`.

Production chưa đổi nên không cần thao tác rollback service. Nếu cần đối chiếu
hoặc bỏ candidate, mở checkout riêng từ tag backup đã xác minh; giữ candidate
và receipt cho tới khi quyết định có cần tiếp tục. Không reset/clean hoặc xóa
worktree phục vụ. Khi có đợt triển khai riêng, phải cấp release cho đúng commit,
giữ dữ liệu/cấu hình/asset rollback và thực hiện đầy đủ điều kiện vận hành Windows.

## Bản sửa audit từ `98972bf` (chưa triển khai)

Bản sửa nằm trong worktree `repo-restructure`, nhánh `codex/repo-restructure`.
Các receipt phía trên thuộc đợt chuyển cấu trúc trước đó; không dùng để chứng minh
bản sửa audit. Receipt mới ở `.local/runs/audit-confirmed-fixes` và
`.local/runs/audit-dependencies`. Không commit/push, đổi checkout phục vụ, `.env`,
`chat_env`, service hoặc dữ liệu thật trong đợt này.

### Đối chiếu 16 phát hiện

| Mục | Thay đổi | Bằng chứng trong phạm vi offline |
|---|---|---|
| 1 | Regression nhận actor xác thực, giới hạn phạm vi từng case và kiểm lại quyền khi đọc kết quả | Test HTTP/repository và terminal regression; review độc lập phần quyền |
| 2 | Feedback/golden kiểm toàn bộ nguồn, phiên bản/hash và manifest; từ chối thao tác ngoài quyền trong transaction | Reviewer hợp lệ, nguồn thứ hai bị cấm, thu hồi quyền, nguồn/manifest thay đổi |
| 3 | Endpoint đổi vai trò giữ nguyên `IsActive`; client cũ không thể kích hoạt qua trường này | Test repository với tài khoản bật/tắt và hợp đồng HTTP |
| 4 | Dialog quyền khóa lưu khi đang tải/tải lỗi, bỏ phản hồi dialog cũ | Component tests tải chậm và chuyển A sang B |
| 5 | Thay vector một lần sau kiểm DocID/quyền nhập lại; xóa lỗi thì dừng trước khi ghi | Trang đầu lỗi, xóa lỗi, thiếu DocID, PDF/non-PDF và rollback |
| 6 | Nhận `[AUTH_ERROR]` theo chuỗi literal; giữ quota và giới hạn retry | Test phân loại lỗi auth/tạm thời/quota |
| 7 | Phân biệt terminal thất bại/hủy/thiếu; thiếu tiêu chí thành `ungraded`, mẫu số chỉ gồm `graded` | Regression events/batch và component summary |
| 8 | Bảy test không còn đọc file ngoài Git; tám lỗi retrieval do fixture thiếu timeout đã sửa | 22 test safety/retrieval đạt, giữ assertion hủy/deadline/quyền |
| 9 | Khai báo trực tiếp `langchain-huggingface` và `fastembed`, giữ phiên bản khóa | 278 gói cài offline bằng hash, `pip check` và 15 import đạt |
| 10 | Backup parse trước dựng runtime, chỉ dựng dependency được chọn | `--help`, từng cờ skip và cả hai cờ với stub |
| 11 | Runbook cập nhật deployment `98972bf`, snapshot phục hồi và GitHub chưa đồng bộ | `docs/windows-service-operations.md`; không sửa receipt lịch sử |
| 12 | Khóa hệ điều hành chung trước dựng runtime; chỉ owner quản lý heartbeat, supervisor dừng mã 73 | Hai process khác cwd, crash nhả khóa, loser không dựng runtime/chạm marker |
| 13 | CLI bootstrap `backend/src`, truyền `thu_muc` dạng chuỗi | Subprocess/import và ingestion stub |
| 14 | `{ok:false}` giữ dialog và báo thất bại | Component tests của quản trị tài khoản |
| 15 | Bảng chỉ áp dụng dữ liệu/lỗi/loading của request mới nhất | Component tests phản hồi về sai thứ tự |
| 16 | Vite 8.1.6, Vitest/coverage 4.1.12, langgraph-sdk 0.4.6, Tornado 6.6 | Audit Python 278 gói và npm graph 202 gói: không có advisory được báo tại thời điểm kiểm |

Audit Python gồm dependency runtime và công cụ; audit npm gồm 41 prod và 162 dev
(các nhóm phân loại có thể chồng lấp). Không suy ra không còn lỗ hổng từ kết quả
audit. Lock frontend chỉ đổi closure của bộ công cụ mục tiêu; môi trường Python
kiểm tra nằm riêng tại `.local/venvs/audit-dependencies`.

### Kiểm chứng bản sửa audit

- Frontend trên Vite 8.1.6/Vitest 4.1.12: 86/86 component test đạt; typecheck và
  build đạt. Coverage của ba component sửa: 99,41% dòng, 90,59% nhánh
  (`frontend-focused.xml`, `frontend-coverage/coverage-summary.json`). Đây không
  phải coverage toàn frontend. Cảnh báo stub `Textarea` trong test vẫn còn.
- Ingestion/retry: lượt tập trung 133/133 đạt (`INGEST-focused-final.xml`).
  Receipt coverage lượt này chỉ đo dòng; không dùng làm bằng chứng nhánh.
- Worker/backup/CLI/service launcher: 69/69 đạt (`WORKER-final.xml`). Các subprocess
  khóa và supervisor dùng thư mục tạm, không chạy service hay ghi ProgramData thật.
- Fixture safety/retrieval: 22/22 đạt (`FIXTURE-green3.xml`). Đổi vai trò backend:
  16/16 đạt (`roles-green.xml`). Không cộng các lượt có test trùng thành tổng suite.
- Dependency: `../audit-dependencies/result.json`, `pip-audit.json`,
  `npm-audit.json`, `imports.log` và `pip-check.log` ghi kết quả môi trường riêng.

Lượt bàn giao cuối trên mã đã cố định: **814/814 đạt**, không skip, trong 92,40 giây
(`handoff.xml`, `handoff.log`, `handoff-options.json`). Phạm vi gồm 41 file test
liên quan và 18 kiểm tra kiến trúc; có HTTP/SSE/component giả lập, SQLite và
subprocess tạm. Không chạy toàn suite. Các lượt trước được giữ làm bằng chứng
tái hiện/sửa lỗi, không cộng vào tổng lượt cuối.

Coverage Python của **dòng thực thi và điểm rẽ nhánh được thay đổi** đạt 91,98%
dòng (619/673), 83,33% nhánh (215/258): `final-coverage.json` và
`changed-coverage.json`. Giữ ngưỡng 80% dòng/nhánh; không dùng số này thay cho
coverage toàn backend hoặc kết quả CI. Cảnh báo deprecation Starlette/httpx và
SQLite datetime adapter vẫn còn, không ảnh hưởng kết quả lượt kiểm tra này.

Review độc lập phần quyền và ingestion/worker đã hoàn tất, không còn phát hiện
mức chặn trong phạm vi bản sửa. Review quyền đã đối chiếu nguồn văn bản,
structured/graph, nhóm dòng cùng trang, metadata đổi cùng phiên bản, tài liệu
chia sẻ, actor có nhiều phòng ban và thứ tự citation khác evidence. Deadline/hủy
được giữ cả khi phát sinh trong lúc đọc actor. `git diff --check` đạt; checkout
phục vụ vẫn sạch tại `98972bf`, candidate chưa commit.

Không còn blocker sửa mã đã xác nhận trong 16 mục. Triển khai là công việc riêng:
chưa có kiểm chứng SQL Server/ACL/service thực tế cho bản sửa, chưa áp dụng schema
mới và chưa cấp phát hành cho candidate. Không gọi lượt kiểm tra này là CI xanh
hay production-ready.

Migration `V0048__feedback_regression_access_basis.sql` chỉ được soạn, chưa chạy.
Schema thiếu phải từ chối rõ ràng; null không phải public và không tự backfill
bằng chứng cũ. Khi lên lịch triển khai riêng, cần áp dụng migration trước khi mở
chức năng phụ thuộc các cột mới. Bản sửa này không làm thay đổi hệ thống đang chạy.

Luồng nguồn thường được chứng minh bằng đoạn văn/trích xuất ảnh hiện hành, giữ
pin từ trước sinh đáp án đến khi lưu; metadata chung và thuộc tính đã xác minh
cũng tham gia fingerprint. Payload dẫn xuất (bảng/title block/YCKT/HDCV,
community/calculation) thiếu bằng chứng cấu trúc đầy đủ không được tự chứng nhận:
feedback bị ẩn, regression không đạt điều kiện bằng chứng. Không coi kiểm tra
PDF văn bản là bằng chứng hỗ trợ mọi loại nguồn Advanced RAG.

Chưa kiểm tải dài, SQL Server thật, reboot, backup/restore hoặc rollback thật.
Khóa worker chỉ bảo vệ cùng host với phiên bản đã cập nhật. Rollback ingestion
sau khi đã xóa vector cũ không khôi phục lại vector cũ; vẫn giữ cơ chế dọn vector
mới theo DocID và phục hồi metadata hiện có, không bổ sung hệ thống snapshot vector.

### Cập nhật ở lượt chuẩn bị phát hành tiếp theo

Sau khi được phép commit và thử SQL bằng dữ liệu giả, mã audit đã được chốt local
tại `e8d5e4fc8eb15ced68ac85b772707c3f79192f57`. V0048 đã chạy trên DB test tách
biệt cùng SQL Express instance; 17/17 tình huống HTTP/SQL bổ sung đạt. Các câu
"chưa commit/chưa chạy migration/chưa kiểm SQL Server" ở phần trên là trạng thái
cuối lượt sửa ban đầu; migration production vẫn chưa chạy, bản sửa chưa deploy.
Xem [kết quả mới, điều kiện còn thiếu và kế hoạch quay lui](audit-confirmed-fixes-release-plan-20261030.md).
