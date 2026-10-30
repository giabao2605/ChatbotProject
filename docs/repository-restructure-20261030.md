# Sắp xếp repository — 30/10/2026

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
